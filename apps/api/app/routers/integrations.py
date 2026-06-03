"""
Integration health endpoints.

  GET /integrations/{pixel}/status        — read cached health (fast, no probe)
  POST /integrations/{pixel}/status       — re-probe live and return result
  POST /integrations/{pixel}/test/{name}  — probe a single platform on demand

Used by the dashboard health card and the onboarding wizard's
"Testar agora" buttons.
"""

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException

from ..database import get_supabase
from ..services import crypto, integrations_health

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/integrations", tags=["integrations"])

_PLATFORM_KEYS = {"meta", "google_ads", "ga4", "tiktok", "pinterest", "shopify"}


@router.post("/{pixel_id}/google/backfill", summary="Reenviar conversões pagas recentes ao Google Ads")
async def google_backfill(pixel_id: str, hours: int = 48, limit: int = 100):
    """
    Re-sends paid orders to Google Ads that were never delivered (e.g. while
    the integration was misconfigured). Real send — recovers lost conversions
    and populates orders.google_sent. orderId dedup prevents double counting.
    Targets paid orders in the last `hours` where google_sent is not true.
    """
    from datetime import datetime, timezone, timedelta
    from ..config import settings
    from ..services import google_ads

    sb = get_supabase()
    cli = (
        sb.table("clients")
        .select("id, google_ads_customer_id, google_ads_refresh_token, "
                "google_ads_login_customer_id, google_ads_conversion_action_id")
        .eq("pixel_id", pixel_id).limit(1).execute()
    )
    if not (cli and cli.data):
        raise HTTPException(404, "Client not found")
    c = crypto.decrypt_client_secrets(cli.data[0])
    if not (c.get("google_ads_customer_id") and c.get("google_ads_refresh_token")
            and c.get("google_ads_conversion_action_id")):
        raise HTTPException(400, "Google Ads not fully configured for this client")

    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    orders = (
        sb.table("orders")
        .select("id, platform_order_id, email, phone, total_price, currency, created_at, visitor_id, utm_source")
        .eq("client_id", c["id"])
        .eq("financial_status", "paid")
        .gt("total_price", 0)
        .neq("google_sent", True)
        # Offline sales (POS / manual draft) are never ad conversions.
        # or_() is required because .not_.in_() excludes NULL rows in SQL.
        .or_("utm_source.is.null,utm_source.not.in.(pos,draft)")
        .gte("created_at", cutoff)
        .order("created_at", desc=True)
        .limit(min(limit, 500))
        .execute()
    ).data or []

    mcc = c.get("google_ads_login_customer_id") or settings.GOOGLE_ADS_MANAGER_ID or None
    sent = failed = skipped = 0
    errors: list[str] = []
    for o in orders:
        email = o.get("email")
        phone = o.get("phone")
        gclid = None
        if o.get("visitor_id"):
            v = (sb.table("visitors").select("gclid").eq("id", o["visitor_id"]).limit(1).execute()).data
            gclid = v[0].get("gclid") if v else None
        if not (gclid or email or phone):
            skipped += 1
            continue
        occurred = None
        try:
            occurred = datetime.fromisoformat(str(o["created_at"]).replace("Z", "+00:00"))
        except Exception:
            pass
        ok, err, match = google_ads.send_conversion(
            customer_id=c["google_ads_customer_id"],
            conversion_action_id=c["google_ads_conversion_action_id"],
            value=float(o.get("total_price") or 0),
            currency=o.get("currency") or "BRL",
            refresh_token=c["google_ads_refresh_token"],
            gclid=gclid, email=email, phone=phone,
            order_id=str(o["platform_order_id"]),
            occurred_at=occurred,
            manager_id=mcc,
        )
        upd = {"google_sent": ok, "google_match_type": match,
               # `err` may be a diagnostic note even when ok (click id rejected → enhanced).
               "google_last_error": (err[:500] if err else None)}
        if ok:
            upd["google_sent_at"] = datetime.now(timezone.utc).isoformat()
            sent += 1
        else:
            failed += 1
            if err and len(errors) < 3:
                errors.append(err[:200])
        try:
            sb.table("orders").update(upd).eq("id", o["id"]).execute()
        except Exception:
            pass

    return {"scanned": len(orders), "sent": sent, "failed": failed,
            "skipped_no_identifiers": skipped, "sample_errors": errors}


@router.get("/{pixel_id}/google/conversion-actions",
            summary="Listar conversion actions do Google Ads (diagnóstico)")
async def google_conversion_actions(pixel_id: str):
    """
    Read-only: lists every conversion action in the client's Google Ads account
    (id, name, type, category, status). Use this to pick the correct
    `conversion_action_id` — the value to store is `id`, which equals the UI
    URL's `ctId`, NOT the account-level `ocid`/`ascid`.

    `configured_id` echoes what's saved today and `configured_match` flags
    whether it actually corresponds to a real conversion action (if false, the
    saved id is wrong — likely the account ocid).
    """
    from ..config import settings
    from ..services import google_ads

    sb = get_supabase()
    cli = (
        sb.table("clients")
        .select("google_ads_customer_id, google_ads_refresh_token, "
                "google_ads_login_customer_id, google_ads_conversion_action_id")
        .eq("pixel_id", pixel_id).limit(1).execute()
    )
    if not (cli and cli.data):
        raise HTTPException(404, "Client not found")
    c = crypto.decrypt_client_secrets(cli.data[0])
    if not (c.get("google_ads_customer_id") and c.get("google_ads_refresh_token")):
        raise HTTPException(400, "Google Ads not connected for this client")

    mcc = c.get("google_ads_login_customer_id") or settings.GOOGLE_ADS_MANAGER_ID or None
    ok, err, actions = google_ads.list_conversion_actions(
        customer_id=c["google_ads_customer_id"],
        refresh_token=c["google_ads_refresh_token"],
        manager_id=mcc,
    )
    if not ok:
        raise HTTPException(502, f"Google Ads query failed: {err}")

    configured = str(c.get("google_ads_conversion_action_id") or "")
    return {
        "customer_id":      c["google_ads_customer_id"],
        "configured_id":    configured or None,
        "configured_match": any(str(a.get("id")) == configured for a in actions) if configured else None,
        "count":            len(actions),
        "conversion_actions": actions,
    }


def _load_client(pixel_id: str) -> dict:
    sb = get_supabase()
    r = (
        sb.table("clients")
        .select(
            "id, pixel_id, meta_access_token, meta_pixel_id, meta_token_health, meta_token_expires_at, "
            "google_ads_customer_id, google_ads_refresh_token, google_ads_login_customer_id, "
            "google_ads_token_health, google_ads_token_checked_at, google_ads_token_error, "
            "ga4_measurement_id, ga4_api_secret, ga4_health, ga4_checked_at, ga4_error, "
            "tiktok_access_token, tiktok_token_health, tiktok_token_checked_at, tiktok_token_error, "
            "pinterest_ad_account_id, pinterest_access_token, pinterest_tag_id, "
            "pinterest_token_health, pinterest_token_checked_at, pinterest_token_error, "
            "shopify_store_domain, shopify_domain, shopify_access_token, "
            "shopify_health, shopify_checked_at, shopify_error"
        )
        .eq("pixel_id", pixel_id)
        .eq("is_active", True)
        .limit(1)
        .execute()
    )
    if not (r and r.data):
        raise HTTPException(status_code=404, detail="Client not found")
    return crypto.decrypt_client_secrets(r.data[0])


def _cached_view(client: dict) -> dict:
    """Return the persisted health snapshot — no live API calls."""
    def block(status, checked_at, error, configured):
        return {
            "status":       status or "unknown",
            "last_checked": checked_at,
            "error":        error,
            "configured":   bool(configured),
        }
    return {
        "pixel_id": client.get("pixel_id"),
        "meta": block(
            client.get("meta_token_health"),
            client.get("meta_token_expires_at"),
            None,
            client.get("meta_access_token"),
        ),
        "google_ads": block(
            client.get("google_ads_token_health"),
            client.get("google_ads_token_checked_at"),
            client.get("google_ads_token_error"),
            client.get("google_ads_customer_id") and client.get("google_ads_refresh_token"),
        ),
        "ga4": block(
            client.get("ga4_health"),
            client.get("ga4_checked_at"),
            client.get("ga4_error"),
            client.get("ga4_measurement_id") and client.get("ga4_api_secret"),
        ),
        "tiktok": block(
            client.get("tiktok_token_health"),
            client.get("tiktok_token_checked_at"),
            client.get("tiktok_token_error"),
            client.get("tiktok_access_token"),
        ),
        "pinterest": block(
            client.get("pinterest_token_health"),
            client.get("pinterest_token_checked_at"),
            client.get("pinterest_token_error"),
            client.get("pinterest_access_token") and client.get("pinterest_ad_account_id"),
        ),
        "shopify": block(
            client.get("shopify_health"),
            client.get("shopify_checked_at"),
            client.get("shopify_error"),
            client.get("shopify_access_token") and (client.get("shopify_store_domain") or client.get("shopify_domain")),
        ),
    }


@router.get("/{pixel_id}/status", summary="Cached integration health")
async def get_status(pixel_id: str):
    """Fast read of the last-known health, persisted by the hourly cron."""
    client = _load_client(pixel_id)
    return _cached_view(client)


@router.post("/{pixel_id}/status", summary="Re-probe all integrations now")
async def probe_status(pixel_id: str):
    """Live probe of every connected integration. ~5-10s response time."""
    client = _load_client(pixel_id)
    results = integrations_health.check_all(client, persist=True)
    return {"pixel_id": pixel_id, **results}


@router.post("/{pixel_id}/test/{platform}", summary="Re-probe a single integration")
async def probe_one(pixel_id: str, platform: str):
    """Wizard "Testar agora" button — single-platform live probe."""
    if platform not in _PLATFORM_KEYS:
        raise HTTPException(status_code=400, detail=f"Unknown platform '{platform}'")
    client = _load_client(pixel_id)

    if platform == "meta":
        result = integrations_health.check_meta(client.get("meta_access_token"))
    elif platform == "google_ads":
        result = integrations_health.check_google_ads(
            client.get("google_ads_customer_id"),
            client.get("google_ads_refresh_token"),
            client.get("google_ads_login_customer_id"),
        )
    elif platform == "ga4":
        result = integrations_health.check_ga4(
            client.get("ga4_measurement_id"),
            client.get("ga4_api_secret"),
        )
    elif platform == "tiktok":
        result = integrations_health.check_tiktok(client.get("tiktok_access_token"))
    elif platform == "pinterest":
        result = integrations_health.check_pinterest(
            client.get("pinterest_ad_account_id"),
            client.get("pinterest_access_token"),
        )
    elif platform == "shopify":
        result = integrations_health.check_shopify(
            client.get("shopify_store_domain") or client.get("shopify_domain"),
            client.get("shopify_access_token"),
        )
    else:
        raise HTTPException(status_code=400, detail=f"Unsupported platform '{platform}'")

    # Persist single-platform result. Reuse check_all paths by selectively
    # writing only the matching health/error/checked_at columns.
    sb = get_supabase()
    from datetime import datetime, timezone
    now_iso = datetime.now(timezone.utc).isoformat()
    col_map = {
        "google_ads": ("google_ads_token_health", "google_ads_token_checked_at", "google_ads_token_error"),
        "ga4":        ("ga4_health",              "ga4_checked_at",              "ga4_error"),
        "tiktok":     ("tiktok_token_health",     "tiktok_token_checked_at",     "tiktok_token_error"),
        "pinterest":  ("pinterest_token_health",  "pinterest_token_checked_at",  "pinterest_token_error"),
        "shopify":    ("shopify_health",          "shopify_checked_at",          "shopify_error"),
    }
    if platform in col_map:
        h, c, e = col_map[platform]
        try:
            sb.table("clients").update({
                h: result["status"],
                c: now_iso,
                e: result.get("error") or None,
            }).eq("id", client["id"]).execute()
        except Exception as exc:
            logger.warning("probe_one persist failed: %s", exc)
    elif platform == "meta":
        try:
            sb.table("clients").update({
                "meta_token_health": result["status"],
            }).eq("id", client["id"]).execute()
        except Exception as exc:
            logger.warning("probe_one persist failed: %s", exc)

    return {"platform": platform, **result, "last_checked": now_iso}
