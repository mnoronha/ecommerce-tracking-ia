"""
Shopify API Sync — importa pedidos pagos via Admin API (polling).

Usado para clientes que querem dados de receita no dashboard mas não
instalam webhooks nem o pixel de tracking. Roda a cada hora via APScheduler
para clientes com shopify_sync_enabled = true.

A tabela orders tem UNIQUE(client_id, platform_order_id), então rodar o sync
múltiplas vezes é seguro — pedidos já importados são atualizados (upsert).
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional
from urllib.parse import parse_qs, urlparse

import httpx

from ..database import get_supabase
from ..services import crypto, writer
from ..services.adapters.shopify_adapter import ShopifyAdapter

logger = logging.getLogger(__name__)

_SHOPIFY_API_VERSION = "2024-10"
_PAGE_SIZE           = 250
_ORDER_FIELDS        = (
    "id,name,email,phone,total_price,currency,financial_status,fulfillment_status,"
    "created_at,updated_at,processed_at,source_name,landing_site,referring_site,"
    "line_items,customer,shipping_address,billing_address,note_attributes,"
    "browser_ip,client_details"
)


def _parse_next_link(link_header: str) -> Optional[str]:
    """Extract the `next` page URL from a Shopify Link header."""
    for part in link_header.split(","):
        url_part, *rel_parts = part.strip().split(";")
        if any("next" in r for r in rel_parts):
            return url_part.strip().strip("<>")
    return None


def sync_client(
    client: dict,
    since: Optional[datetime] = None,
    *,
    full_backfill: bool = False,
) -> dict:
    """
    Pull paid orders from Shopify Admin API and upsert them into orders table.

    Args:
        client:        Decrypted client row from the clients table.
        since:         If provided, only fetch orders updated after this datetime.
                       If None, uses shopify_last_sync_at or 7 days ago.
        full_backfill: If True, ignores since and fetches all time (use only manually).

    Returns:
        {"imported": int, "errors": int, "since": str}
    """
    domain = (client.get("shopify_domain") or "").strip().rstrip("/")
    token  = client.get("shopify_access_token") or ""
    client_uuid = client["id"]

    if not domain or not token:
        logger.warning("shopify_sync: client %s has no domain/token — skipping", client_uuid)
        return {"imported": 0, "errors": 0, "skipped": True}

    # Determine time window
    if full_backfill:
        since_dt = None
    elif since:
        since_dt = since
    else:
        raw = client.get("shopify_last_sync_at")
        if raw:
            try:
                since_dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
                # 5-min overlap to avoid gap on clock skew
                since_dt = since_dt - timedelta(minutes=5)
            except Exception:
                since_dt = datetime.now(timezone.utc) - timedelta(days=7)
        else:
            since_dt = datetime.now(timezone.utc) - timedelta(days=7)

    adapter     = ShopifyAdapter()
    imported    = 0
    errors      = 0
    next_url: Optional[str] = (
        f"https://{domain}/admin/api/{_SHOPIFY_API_VERSION}/orders.json"
    )
    params: Optional[dict] = {
        "financial_status": "paid",
        "status":           "any",
        "limit":            _PAGE_SIZE,
        "fields":           _ORDER_FIELDS,
    }
    if since_dt:
        params["updated_at_min"] = since_dt.isoformat()

    headers = {"X-Shopify-Access-Token": token}

    while next_url:
        try:
            resp = httpx.get(
                next_url,
                headers=headers,
                params=params,
                timeout=30.0,
            )
            params = None  # pagination URL already carries params

            if resp.status_code == 429:
                # Shopify rate limit — back off and retry once
                import time; time.sleep(2)
                resp = httpx.get(next_url, headers=headers, timeout=30.0)

            if resp.status_code != 200:
                logger.error(
                    "shopify_sync: HTTP %s for client %s: %s",
                    resp.status_code, client_uuid, resp.text[:200],
                )
                break

            orders = resp.json().get("orders", [])
            for order in orders:
                try:
                    event = adapter.normalize(
                        payload=order,
                        client_id=client_uuid,
                        headers={"x-shopify-topic": "orders/paid"},
                    )
                    writer.write_order(client_uuid, None, event)
                    imported += 1
                except Exception as exc:
                    logger.warning(
                        "shopify_sync: failed to import order %s: %s",
                        order.get("id"), exc,
                    )
                    errors += 1

            next_url = _parse_next_link(resp.headers.get("Link", ""))

        except Exception as exc:
            logger.error("shopify_sync: page fetch error for client %s: %s", client_uuid, exc)
            break

    # Persist last sync timestamp only on successful run
    if imported > 0 or errors == 0:
        try:
            get_supabase().table("clients").update({
                "shopify_last_sync_at": datetime.now(timezone.utc).isoformat(),
            }).eq("id", client_uuid).execute()
        except Exception as exc:
            logger.warning("shopify_sync: failed to update last_sync_at: %s", exc)

    logger.info(
        "shopify_sync: client %s — imported=%d errors=%d since=%s",
        client_uuid, imported, errors,
        since_dt.isoformat() if since_dt else "all-time",
    )
    return {
        "imported": imported,
        "errors":   errors,
        "since":    since_dt.isoformat() if since_dt else "all-time",
    }


def run_hourly_for_all_clients() -> None:
    """APScheduler entry point: sync all clients with shopify_sync_enabled = true."""
    try:
        sb = get_supabase()
        rows = (
            sb.table("clients")
            .select(
                "id, shopify_domain, shopify_access_token, "
                "shopify_last_sync_at, shopify_sync_enabled"
            )
            .eq("is_active", True)
            .eq("shopify_sync_enabled", True)
            .execute()
        ).data or []

        if not rows:
            return

        for row in rows:
            try:
                client = crypto.decrypt_client_secrets(row)
                sync_client(client)
            except Exception as exc:
                logger.error("shopify_sync: error for client %s: %s", row.get("id"), exc)

    except Exception as exc:
        logger.error("shopify_sync run_hourly: %s", exc)
