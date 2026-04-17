"""
Meta Custom Audiences — automatic audience sync.

Syncs visitor segments from the database to Meta Custom Audiences via Marketing API.
Runs on schedule (every 6h) and on-demand via API endpoint.

Audience types:
  high_ltv        — visitors with LTV above threshold (default R$300)
  cart_abandoners — added to cart but never purchased
  recent_buyers   — purchased in last 7 days (suppression list)
  top_customers   — top 10% by LTV (lookalike seed)
  inactive        — purchased before but haven't visited in 90+ days

Docs: https://developers.facebook.com/docs/marketing-api/audiences
"""

import hashlib
import logging
import time
from datetime import datetime, timezone, timedelta
from typing import Optional

import httpx

from ..database import get_supabase

logger = logging.getLogger(__name__)

_GRAPH = "https://graph.facebook.com/v19.0"

# ── Audience definitions ──────────────────────────────────────────────────────

AUDIENCE_CONFIGS: dict[str, dict] = {
    "high_ltv": {
        "name":        "High LTV Customers — Tracking IA",
        "description": "Clientes com LTV acima do threshold — base para lookalike",
        "subtype":     "CUSTOM",
    },
    "cart_abandoners": {
        "name":        "Cart Abandoners — Tracking IA",
        "description": "Visitantes que adicionaram ao carrinho mas nunca compraram",
        "subtype":     "CUSTOM",
    },
    "recent_buyers": {
        "name":        "Recent Buyers Suppression — Tracking IA",
        "description": "Compraram nos últimos 7 dias — suprimir de campanhas de aquisição",
        "subtype":     "CUSTOM",
    },
    "top_customers": {
        "name":        "Top 10% LTV Lookalike Seed — Tracking IA",
        "description": "Top clientes por LTV — usar como seed para Lookalike Audience",
        "subtype":     "CUSTOM",
    },
    "inactive": {
        "name":        "Inactive Customers — Tracking IA",
        "description": "Compraram mas não visitam há 90+ dias — campanha de reativação",
        "subtype":     "CUSTOM",
    },
}


# ── PII hashing (Meta spec: lowercase + SHA256) ───────────────────────────────

def _h(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    return hashlib.sha256(value.strip().lower().encode("utf-8")).hexdigest()


def _hash_phone(phone: Optional[str]) -> Optional[str]:
    if not phone:
        return None
    digits = "".join(c for c in phone if c.isdigit())
    if not digits:
        return None
    return hashlib.sha256(digits.encode("utf-8")).hexdigest()


# ── Data fetchers ─────────────────────────────────────────────────────────────

def _fetch_high_ltv(client_uuid: str, ltv_threshold: float = 300.0) -> list[dict]:
    """Visitors with LTV above threshold."""
    try:
        result = (
            get_supabase().table("visitors")
            .select("email, phone")
            .eq("client_id", client_uuid)
            .gte("ltv", ltv_threshold)
            .not_.is_("email", "null")
            .limit(10000)
            .execute()
        )
        return result.data or []
    except Exception as exc:
        logger.warning("_fetch_high_ltv: %s", exc)
        return []


def _fetch_cart_abandoners(client_uuid: str) -> list[dict]:
    """Visitors who added to cart but never purchased."""
    try:
        result = (
            get_supabase().table("visitors")
            .select("email, phone")
            .eq("client_id", client_uuid)
            .eq("total_orders", 0)
            .gt("retargeting_score", 10)
            .not_.is_("email", "null")
            .limit(10000)
            .execute()
        )
        return result.data or []
    except Exception as exc:
        logger.warning("_fetch_cart_abandoners: %s", exc)
        return []


def _fetch_recent_buyers(client_uuid: str, days: int = 7) -> list[dict]:
    """Customers who purchased in the last N days."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    try:
        result = (
            get_supabase().table("visitors")
            .select("email, phone")
            .eq("client_id", client_uuid)
            .gte("last_purchase_at", cutoff)
            .not_.is_("email", "null")
            .limit(10000)
            .execute()
        )
        return result.data or []
    except Exception as exc:
        logger.warning("_fetch_recent_buyers: %s", exc)
        return []


def _fetch_top_customers(client_uuid: str) -> list[dict]:
    """Top 10% visitors by LTV."""
    try:
        # Get P90 LTV threshold
        all_ltv = (
            get_supabase().table("visitors")
            .select("ltv")
            .eq("client_id", client_uuid)
            .gt("ltv", 0)
            .limit(10000)
            .execute()
        )
        values = sorted([r["ltv"] for r in (all_ltv.data or []) if r.get("ltv")])
        if not values:
            return []
        p90_idx = max(0, int(len(values) * 0.9))
        p90 = values[p90_idx]

        result = (
            get_supabase().table("visitors")
            .select("email, phone")
            .eq("client_id", client_uuid)
            .gte("ltv", p90)
            .not_.is_("email", "null")
            .limit(5000)
            .execute()
        )
        return result.data or []
    except Exception as exc:
        logger.warning("_fetch_top_customers: %s", exc)
        return []


def _fetch_inactive(client_uuid: str, days: int = 90) -> list[dict]:
    """Customers who haven't visited in 90+ days but purchased before."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    try:
        result = (
            get_supabase().table("visitors")
            .select("email, phone")
            .eq("client_id", client_uuid)
            .gt("total_orders", 0)
            .lt("last_seen_at", cutoff)
            .not_.is_("email", "null")
            .limit(10000)
            .execute()
        )
        return result.data or []
    except Exception as exc:
        logger.warning("_fetch_inactive: %s", exc)
        return []


# ── Meta API helpers ──────────────────────────────────────────────────────────

def _get_or_create_audience(
    ad_account_id: str,
    access_token: str,
    audience_type: str,
) -> Optional[str]:
    """Return existing audience ID or create a new one. Returns audience_id or None."""
    config = AUDIENCE_CONFIGS[audience_type]
    clean_id = ad_account_id.removeprefix("act_")

    # Check audience_syncs table for existing platform ID
    try:
        existing = (
            get_supabase().table("audience_syncs")
            .select("platform_audience_id")
            .eq("audience_type", audience_type)
            .eq("platform", "meta")
            .not_.is_("platform_audience_id", "null")
            .limit(1)
            .execute()
        )
        if existing and existing.data and existing.data[0].get("platform_audience_id"):
            return existing.data[0]["platform_audience_id"]
    except Exception as exc:
        logger.debug("audience_syncs lookup failed: %s", exc)

    # Create new custom audience
    try:
        resp = httpx.post(
            f"{_GRAPH}/act_{clean_id}/customaudiences",
            params={"access_token": access_token},
            json={
                "name":                   config["name"],
                "description":            config["description"],
                "subtype":                config["subtype"],
                "customer_file_source":   "USER_PROVIDED_ONLY",
            },
            timeout=15.0,
        )
        if resp.status_code == 200:
            audience_id = resp.json().get("id")
            logger.info("Created Meta audience '%s' → id=%s", config["name"], audience_id)
            return audience_id
        logger.warning("Failed to create audience %s: HTTP %s %s",
                       audience_type, resp.status_code, resp.text[:300])
        return None
    except Exception as exc:
        logger.error("_get_or_create_audience exception: %s", exc)
        return None


def _push_users_to_audience(
    audience_id: str,
    access_token: str,
    users: list[dict],
) -> int:
    """Hash user data and push to Meta Custom Audience. Returns users uploaded count."""
    if not users:
        return 0

    # Build schema and hashed data
    data_rows = []
    for u in users:
        email_hash = _h(u.get("email"))
        phone_hash = _hash_phone(u.get("phone"))
        if email_hash:
            row = [email_hash]
            if phone_hash:
                row.append(phone_hash)
            else:
                row.append("")
            data_rows.append(row)

    if not data_rows:
        return 0

    # Meta accepts max 10k per batch
    batch_size = 5000
    total_uploaded = 0

    for i in range(0, len(data_rows), batch_size):
        batch = data_rows[i:i + batch_size]
        try:
            resp = httpx.post(
                f"{_GRAPH}/{audience_id}/users",
                params={"access_token": access_token},
                json={
                    "payload": {
                        "schema": ["EMAIL", "PHONE"],
                        "data":   batch,
                    }
                },
                timeout=30.0,
            )
            if resp.status_code == 200:
                result = resp.json()
                total_uploaded += result.get("num_received", len(batch))
                logger.info("Audience %s batch %d: %d/%d uploaded",
                            audience_id, i // batch_size + 1,
                            result.get("num_received", 0), len(batch))
            else:
                logger.warning("Audience push HTTP %s: %s",
                               resp.status_code, resp.text[:300])
        except Exception as exc:
            logger.error("_push_users_to_audience batch error: %s", exc)

        if i + batch_size < len(data_rows):
            time.sleep(0.5)  # be gentle with the API

    return total_uploaded


# ── Sync status tracking ──────────────────────────────────────────────────────

def _record_sync(
    client_id: str,
    audience_type: str,
    platform_audience_id: Optional[str],
    users_count: int,
    status: str,
    error: Optional[str] = None,
) -> None:
    try:
        sb = get_supabase()
        existing = (
            sb.table("audience_syncs")
            .select("id")
            .eq("client_id", client_id)
            .eq("audience_type", audience_type)
            .eq("platform", "meta")
            .limit(1)
            .execute()
        )
        row = {
            "client_id":            client_id,
            "audience_type":        audience_type,
            "platform":             "meta",
            "platform_audience_id": platform_audience_id,
            "audience_name":        AUDIENCE_CONFIGS[audience_type]["name"],
            "users_count":          users_count,
            "last_synced_at":       datetime.now(timezone.utc).isoformat(),
            "status":               status,
            "error_message":        error,
        }
        if existing and existing.data:
            sb.table("audience_syncs").update(row).eq("id", existing.data[0]["id"]).execute()
        else:
            sb.table("audience_syncs").insert(row).execute()
    except Exception as exc:
        logger.warning("_record_sync failed: %s", exc)


# ── Public API ────────────────────────────────────────────────────────────────

def sync_audience(
    client_uuid: str,
    ad_account_id: str,
    access_token: str,
    audience_type: str,
    ltv_threshold: float = 300.0,
) -> dict:
    """
    Sync one audience type to Meta. Returns result dict.
    audience_type must be one of AUDIENCE_CONFIGS keys.
    """
    if audience_type not in AUDIENCE_CONFIGS:
        return {"error": f"Unknown audience type: {audience_type}"}

    logger.info("Syncing audience '%s' for client %s", audience_type, client_uuid)

    # Fetch users from DB
    fetchers = {
        "high_ltv":        lambda: _fetch_high_ltv(client_uuid, ltv_threshold),
        "cart_abandoners": lambda: _fetch_cart_abandoners(client_uuid),
        "recent_buyers":   lambda: _fetch_recent_buyers(client_uuid),
        "top_customers":   lambda: _fetch_top_customers(client_uuid),
        "inactive":        lambda: _fetch_inactive(client_uuid),
    }
    users = fetchers[audience_type]()

    if not users:
        _record_sync(client_uuid, audience_type, None, 0, "synced")
        return {"audience_type": audience_type, "users_found": 0, "users_uploaded": 0}

    # Get or create Meta audience
    audience_id = _get_or_create_audience(ad_account_id, access_token, audience_type)
    if not audience_id:
        _record_sync(client_uuid, audience_type, None, len(users), "error",
                     "Failed to create/find Meta audience")
        return {"audience_type": audience_type, "error": "Failed to create Meta audience"}

    # Push users
    uploaded = _push_users_to_audience(audience_id, access_token, users)

    _record_sync(client_uuid, audience_type, audience_id, uploaded, "synced")
    logger.info("Audience '%s' synced: %d users → Meta audience %s",
                audience_type, uploaded, audience_id)

    return {
        "audience_type":   audience_type,
        "audience_id":     audience_id,
        "users_found":     len(users),
        "users_uploaded":  uploaded,
        "status":          "synced",
    }


def sync_all_audiences(client_uuid: str, ad_account_id: str, access_token: str) -> list[dict]:
    """Sync all audience types for a client. Called by scheduler."""
    results = []
    for aud_type in AUDIENCE_CONFIGS:
        result = sync_audience(client_uuid, ad_account_id, access_token, aud_type)
        results.append(result)
        time.sleep(1.0)  # rate limit between audience syncs
    return results


def run_audience_sync_all_clients() -> None:
    """Entry point for the scheduler — syncs audiences for all active clients."""
    try:
        clients = (
            get_supabase().table("clients")
            .select("id, meta_ad_account_id, meta_access_token")
            .eq("is_active", True)
            .not_.is_("meta_ad_account_id", "null")
            .not_.is_("meta_access_token", "null")
            .execute()
        )
        if not (clients and clients.data):
            return
        for c in clients.data:
            logger.info("Audience sync for client %s", c["id"])
            sync_all_audiences(c["id"], c["meta_ad_account_id"], c["meta_access_token"])
    except Exception as exc:
        logger.error("run_audience_sync_all_clients: %s", exc)


def get_sync_status(client_uuid: str) -> list[dict]:
    """Return current sync status for all audience types."""
    try:
        result = (
            get_supabase().table("audience_syncs")
            .select("audience_type, audience_name, users_count, last_synced_at, status, error_message, platform_audience_id")
            .eq("client_id", client_uuid)
            .eq("platform", "meta")
            .order("audience_type")
            .execute()
        )
        synced = {r["audience_type"]: r for r in (result.data or [])}
        # Return all types, even ones never synced
        output = []
        for aud_type, config in AUDIENCE_CONFIGS.items():
            if aud_type in synced:
                output.append(synced[aud_type])
            else:
                output.append({
                    "audience_type":       aud_type,
                    "audience_name":       config["name"],
                    "users_count":         0,
                    "last_synced_at":      None,
                    "status":              "never_synced",
                    "error_message":       None,
                    "platform_audience_id": None,
                })
        return output
    except Exception as exc:
        logger.warning("get_sync_status: %s", exc)
        return []
