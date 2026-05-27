"""
Ad spend sync — pulls yesterday's spend from Google Ads and TikTok Marketing APIs
and upserts into the `ad_spend` table.

Google Ads: uses the same OAuth credentials already stored per-client
(google_ads_customer_id + google_ads_refresh_token + shared agency tokens).

TikTok: requires tiktok_advertiser_id + tiktok_access_token on the client row.
The Events API access_token doubles as the Marketing API token when the app has
the right permissions; no separate token needed.

Scheduler: runs daily at 06:00 UTC (after midnight UTC closes the previous day).
"""

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Optional

import httpx

from ..config import settings
from ..database import get_supabase

logger = logging.getLogger(__name__)

_GOOGLE_ADS_API = "https://googleads.googleapis.com/v21"
_TOKEN_URL      = "https://oauth2.googleapis.com/token"
_TIKTOK_REPORT  = "https://business-api.tiktok.com/open_api/v1.3/report/integrated/get/"
_META_INSIGHTS  = "https://graph.facebook.com/v19.0/act_{account_id}/insights"

_token_cache: dict = {}


# ── OAuth helper (shared with google_ads.py pattern) ─────────────────────────

def _get_google_token(refresh_token: str) -> Optional[str]:
    import time
    now = time.time()
    key = refresh_token[:16]
    cached = _token_cache.get(key, {})
    if cached.get("token") and cached.get("expires_at", 0) > now + 60:
        return cached["token"]
    try:
        resp = httpx.post(
            _TOKEN_URL,
            data={
                "client_id":     settings.GOOGLE_ADS_OAUTH_CLIENT_ID,
                "client_secret": settings.GOOGLE_ADS_OAUTH_CLIENT_SECRET,
                "refresh_token": refresh_token,
                "grant_type":    "refresh_token",
            },
            timeout=10.0,
        )
        if resp.status_code == 200:
            data = resp.json()
            _token_cache[key] = {"token": data["access_token"], "expires_at": now + data.get("expires_in", 3600)}
            return _token_cache[key]["token"]
    except Exception as exc:
        logger.warning("spend_sync: google token refresh failed: %s", exc)
    return None


# ── Google Ads spend ──────────────────────────────────────────────────────────

def _fetch_google_spend(customer_id: str, refresh_token: str, target_date: date, manager_id: Optional[str] = None) -> Optional[dict]:
    """
    Query Google Ads Reporting API for spend on target_date.
    Returns dict with spend/impressions/clicks/conversions or None on failure.
    """
    if not all([settings.GOOGLE_ADS_DEVELOPER_TOKEN, settings.GOOGLE_ADS_OAUTH_CLIENT_ID, settings.GOOGLE_ADS_OAUTH_CLIENT_SECRET]):
        return None

    token = _get_google_token(refresh_token)
    if not token:
        return None

    clean_cid = customer_id.replace("-", "").replace(" ", "")
    date_str  = target_date.isoformat()

    query = (
        f"SELECT metrics.cost_micros, metrics.impressions, metrics.clicks, "
        f"metrics.conversions "
        f"FROM customer "
        f"WHERE segments.date = '{date_str}'"
    )

    headers = {
        "Authorization":           f"Bearer {token}",
        "developer-token":         settings.GOOGLE_ADS_DEVELOPER_TOKEN,
        "Content-Type":            "application/json",
    }
    if manager_id:
        headers["login-customer-id"] = manager_id.replace("-", "").replace(" ", "")

    url = f"{_GOOGLE_ADS_API}/customers/{clean_cid}/googleAds:search"
    try:
        resp = httpx.post(url, json={"query": query}, headers=headers, timeout=30.0)
        if resp.status_code != 200:
            logger.warning("google_spend: HTTP %s for customer %s: %s", resp.status_code, customer_id, resp.text[:200])
            return None
        body = resp.json()
        results = body.get("results") or []
        # Sum across all rows (one row per day at customer level)
        total_spend_micros = 0
        total_impressions  = 0
        total_clicks       = 0
        total_conversions  = 0.0
        for row in results:
            m = row.get("metrics") or {}
            total_spend_micros += int(m.get("costMicros", 0))
            total_impressions  += int(m.get("impressions", 0))
            total_clicks       += int(m.get("clicks", 0))
            total_conversions  += float(m.get("conversions", 0))
        return {
            "spend":       round(total_spend_micros / 1_000_000, 2),
            "impressions": total_impressions,
            "clicks":      total_clicks,
            "conversions": round(total_conversions, 2),
        }
    except Exception as exc:
        logger.warning("google_spend: fetch failed for %s: %s", customer_id, exc)
        return None


# ── TikTok spend ──────────────────────────────────────────────────────────────

def _fetch_tiktok_spend(advertiser_id: str, access_token: str, target_date: date) -> Optional[dict]:
    """
    Query TikTok Marketing API Reporting for spend on target_date.
    Returns dict with spend/impressions/clicks/conversions or None on failure.
    """
    date_str = target_date.isoformat()
    params = {
        "advertiser_id": advertiser_id,
        "report_type":   "BASIC",
        "dimensions":    '["stat_time_day"]',
        "metrics":       '["spend","impressions","clicks","total_complete_payment_event_count"]',
        "data_level":    "ACCOUNT",
        "start_date":    date_str,
        "end_date":      date_str,
        "page_size":     1,
    }
    headers = {"Access-Token": access_token}
    try:
        resp = httpx.get(_TIKTOK_REPORT, params=params, headers=headers, timeout=30.0)
        body = resp.json()
        if body.get("code") != 0:
            logger.warning("tiktok_spend: API error for advertiser %s: %s", advertiser_id, body.get("message", ""))
            return None
        rows = (body.get("data") or {}).get("list") or []
        if not rows:
            return {"spend": 0.0, "impressions": 0, "clicks": 0, "conversions": 0.0}
        m = rows[0].get("metrics") or {}
        return {
            "spend":       round(float(m.get("spend", 0)), 2),
            "impressions": int(m.get("impressions", 0)),
            "clicks":      int(m.get("clicks", 0)),
            "conversions": round(float(m.get("total_complete_payment_event_count", 0)), 2),
        }
    except Exception as exc:
        logger.warning("tiktok_spend: fetch failed for %s: %s", advertiser_id, exc)
        return None


# ── Meta Ads spend ────────────────────────────────────────────────────────────

def _fetch_meta_spend(ad_account_id: str, access_token: str, target_date: date) -> Optional[dict]:
    """
    Query Meta Marketing API for account-level spend on target_date.
    Uses `level=account` so we get a single aggregated row.
    """
    clean_id  = ad_account_id.removeprefix("act_")
    date_str  = target_date.isoformat()
    url       = _META_INSIGHTS.format(account_id=clean_id)
    try:
        resp = httpx.get(
            url,
            params={
                "fields":     "spend,impressions,clicks,actions",
                "time_range": f'{{"since":"{date_str}","until":"{date_str}"}}',
                "level":      "account",
                "access_token": access_token,
            },
            timeout=15.0,
        )
        if resp.status_code != 200:
            logger.warning("meta_spend: HTTP %s for act_%s: %s", resp.status_code, clean_id, resp.text[:200])
            return None
        rows = resp.json().get("data") or []
        if not rows:
            return {"spend": 0.0, "impressions": 0, "clicks": 0, "conversions": 0.0}
        row = rows[0]
        # Count purchases from actions array
        purchases = 0.0
        for action in (row.get("actions") or []):
            if action.get("action_type") in ("purchase", "omni_purchase", "offsite_conversion.fb_pixel_purchase"):
                try:
                    purchases = float(action.get("value") or 0)
                except (TypeError, ValueError):
                    pass
                break
        return {
            "spend":       round(float(row.get("spend") or 0), 2),
            "impressions": int(row.get("impressions") or 0),
            "clicks":      int(row.get("clicks") or 0),
            "conversions": round(purchases, 2),
        }
    except Exception as exc:
        logger.warning("meta_spend: fetch failed for act_%s: %s", clean_id, exc)
        return None


# ── Upsert helper ─────────────────────────────────────────────────────────────

def _upsert_spend(client_id: str, channel: str, target_date: date, metrics: dict) -> None:
    sb = get_supabase()
    row = {
        "client_id":   client_id,
        "channel":     channel,
        "date":        target_date.isoformat(),
        "spend":       metrics["spend"],
        "impressions": metrics["impressions"],
        "clicks":      metrics["clicks"],
        "conversions": metrics["conversions"],
        "updated_at":  datetime.now(timezone.utc).isoformat(),
    }
    try:
        sb.table("ad_spend").upsert(row, on_conflict="client_id,channel,date").execute()
    except Exception as exc:
        logger.warning("spend_sync: upsert failed for %s/%s/%s: %s", client_id, channel, target_date, exc)


# ── Scheduler entry point ─────────────────────────────────────────────────────

def run_daily_spend_sync() -> None:
    """
    Pull yesterday's spend for every client that has Google Ads or TikTok credentials.
    Called daily at 06:00 UTC.
    """
    yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).date()
    sb        = get_supabase()

    try:
        clients = (
            sb.table("clients")
            .select(
                "id, pixel_id, "
                "meta_ad_account_id, meta_access_token, "
                "google_ads_customer_id, google_ads_refresh_token, google_ads_login_customer_id, "
                "tiktok_advertiser_id, tiktok_access_token"
            )
            .eq("is_active", True)
            .execute()
        )
    except Exception as exc:
        logger.error("spend_sync: clients query failed: %s", exc)
        return

    for c in (clients.data or []):
        pixel = c.get("pixel_id", c["id"])

        # ── Meta Ads ──────────────────────────────────────────────────────
        if c.get("meta_ad_account_id") and c.get("meta_access_token"):
            metrics = _fetch_meta_spend(
                ad_account_id = c["meta_ad_account_id"],
                access_token  = c["meta_access_token"],
                target_date   = yesterday,
            )
            if metrics is not None:
                _upsert_spend(c["id"], "meta_ads", yesterday, metrics)
                logger.info("spend_sync: meta_ads %s → R$%.2f", pixel, metrics["spend"])

        # ── Google Ads ────────────────────────────────────────────────────
        if c.get("google_ads_customer_id") and c.get("google_ads_refresh_token"):
            manager = c.get("google_ads_login_customer_id") or settings.GOOGLE_ADS_MANAGER_ID or None
            metrics = _fetch_google_spend(
                customer_id   = c["google_ads_customer_id"],
                refresh_token = c["google_ads_refresh_token"],
                target_date   = yesterday,
                manager_id    = manager,
            )
            if metrics is not None:
                _upsert_spend(c["id"], "google_ads", yesterday, metrics)
                logger.info("spend_sync: google_ads %s → R$%.2f", pixel, metrics["spend"])

        # ── TikTok ────────────────────────────────────────────────────────
        if c.get("tiktok_advertiser_id") and c.get("tiktok_access_token"):
            metrics = _fetch_tiktok_spend(
                advertiser_id = c["tiktok_advertiser_id"],
                access_token  = c["tiktok_access_token"],
                target_date   = yesterday,
            )
            if metrics is not None:
                _upsert_spend(c["id"], "tiktok_ads", yesterday, metrics)
                logger.info("spend_sync: tiktok_ads %s → R$%.2f", pixel, metrics["spend"])
