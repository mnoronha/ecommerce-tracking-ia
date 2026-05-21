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
from ..services import integrations_health

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/integrations", tags=["integrations"])

_PLATFORM_KEYS = {"meta", "google_ads", "ga4", "tiktok", "pinterest", "shopify"}


def _load_client(pixel_id: str) -> dict:
    sb = get_supabase()
    r = (
        sb.table("clients")
        .select(
            "id, pixel_id, meta_access_token, meta_pixel_id, meta_token_health, meta_token_expires_at, "
            "google_ads_customer_id, google_ads_refresh_token, google_ads_token_health, "
            "google_ads_token_checked_at, google_ads_token_error, "
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
    return r.data[0]


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
