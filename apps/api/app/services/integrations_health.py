"""
Unified health checks across all integrations.

For each client, probes the live API of each connected platform and updates
the matching `<platform>_health` / `<platform>_checked_at` / `<platform>_error`
columns on clients. Powers both:

  - GET /integrations/{pixel}/status — the dashboard health card
  - the wizard's "Testar agora" buttons during onboarding

Each check is best-effort and never raises — a failure on Google Ads doesn't
break Meta's probe.

Health states (kept uniform per CHECK constraint in migration 017):
  healthy        — token works, expiry is comfortable
  expiring_soon  — token works but expires < 7 days (where applicable)
  expired        — token rejected for expiry reasons
  invalid        — credentials wrong / scopes missing / account banned
  unknown        — never probed
"""

import logging
import time
from datetime import datetime, timezone
from typing import Optional

import httpx

from ..config import settings
from ..database import get_supabase
from .google_ads import _get_access_token as _google_ads_token

logger = logging.getLogger(__name__)

_TIMEOUT = 8.0


# ── individual probes ─────────────────────────────────────────────────────────

def check_meta(access_token: Optional[str], pixel_id: Optional[str] = None) -> dict:
    """
    Validates the Meta access token using `/me` — works without APP_SECRET
    and without requiring the developer account that owns the app to have
    completed Developer Registration. Falls through to `debug_token` only
    when the app credentials are present and we want expiry data.
    """
    if not access_token:
        return {"status": "unknown", "error": "no token configured"}
    try:
        me = httpx.get(
            "https://graph.facebook.com/v19.0/me",
            params={"access_token": access_token, "fields": "id"},
            timeout=_TIMEOUT,
        )
        body = me.json()
        if me.status_code != 200 or not body.get("id"):
            err = (body.get("error") or {}).get("message") or me.text[:200]
            # Code 190 means token itself is rejected (expired/revoked/bad signature)
            code = (body.get("error") or {}).get("code")
            return {"status": "expired" if code == 190 else "invalid", "error": err}
    except Exception as exc:
        return {"status": "invalid", "error": f"{type(exc).__name__}: {exc}"}

    # Token works. Try debug_token for expiry info — only if APP credentials
    # are configured AND the developer account is active. Failure here is
    # informational only and doesn't downgrade the status.
    if settings.META_APP_ID and settings.META_APP_SECRET:
        try:
            r = httpx.get(
                "https://graph.facebook.com/v19.0/debug_token",
                params={
                    "input_token":  access_token,
                    "access_token": f"{settings.META_APP_ID}|{settings.META_APP_SECRET}",
                },
                timeout=_TIMEOUT,
            )
            data = (r.json().get("data") or {})
            if r.status_code == 200 and data.get("is_valid"):
                expires = int(data.get("expires_at") or 0)
                now = time.time()
                if expires and expires - now < 7 * 86400 and expires > now:
                    return {"status": "expiring_soon", "expires_at": expires}
                if expires and expires <= now:
                    return {"status": "expired"}
                return {"status": "healthy", "expires_at": expires or None}
            # debug_token failed — that's fine if /me passed. Mark healthy
            # but signal we can't read expiry. Keeps the dashboard green
            # when the only failure is the agency's dev-registration step.
            return {"status": "healthy", "expires_at": None}
        except Exception:
            return {"status": "healthy", "expires_at": None}
    return {"status": "healthy", "expires_at": None}


def check_google_ads(
    customer_id: Optional[str],
    refresh_token: Optional[str],
    login_customer_id: Optional[str] = None,
) -> dict:
    """
    Refreshes the access token and runs a 1-row query against the customer.
    `login_customer_id` is the per-client MCC; falls back to the agency-wide
    GOOGLE_ADS_MANAGER_ID env var.
    """
    if not (customer_id and refresh_token):
        return {"status": "unknown", "error": "no customer_id or refresh_token"}
    if not (settings.GOOGLE_ADS_OAUTH_CLIENT_ID and settings.GOOGLE_ADS_OAUTH_CLIENT_SECRET and settings.GOOGLE_ADS_DEVELOPER_TOKEN):
        return {"status": "unknown", "error": "Google Ads OAuth env vars missing"}
    try:
        token = _google_ads_token(
            settings.GOOGLE_ADS_OAUTH_CLIENT_ID,
            settings.GOOGLE_ADS_OAUTH_CLIENT_SECRET,
            refresh_token,
        )
        if not token:
            return {"status": "expired", "error": "refresh_token rejected"}
        clean_cid = customer_id.replace("-", "").replace(" ", "")
        # Probe a customer-scoped endpoint that actually exists across
        # Google Ads API versions. googleAds:search with a one-row query
        # validates: OAuth token → developer token → customer access.
        # We try the current versions in order; first non-404 wins.
        headers = {
            "Authorization":   f"Bearer {token}",
            "developer-token": settings.GOOGLE_ADS_DEVELOPER_TOKEN,
            "Content-Type":    "application/json",
        }
        mcc = login_customer_id or settings.GOOGLE_ADS_MANAGER_ID
        if mcc:
            headers["login-customer-id"] = mcc.replace("-", "")

        r = None
        for version in ("v21", "v20", "v19"):
            r = httpx.post(
                f"https://googleads.googleapis.com/{version}/customers/{clean_cid}/googleAds:search",
                headers=headers,
                json={"query": "SELECT customer.id FROM customer LIMIT 1", "pageSize": 1},
                timeout=_TIMEOUT,
            )
            if r.status_code != 404:
                break
        if r is None:
            return {"status": "invalid", "error": "no version returned a response"}
        if r.status_code == 200:
            return {"status": "healthy"}
        if r.status_code == 401:
            return {"status": "expired", "error": "OAuth token rejected"}
        if r.status_code == 403:
            txt = r.text
            if "USER_PERMISSION_DENIED" in txt:
                hint = ("conta-cliente sem acesso. Defina GOOGLE_ADS_MANAGER_ID "
                        "(MCC) no Railway ou conceda acesso ao usuário OAuth.")
                return {"status": "invalid", "error": hint}
            if "DEVELOPER_TOKEN" in txt:
                return {"status": "invalid", "error": "developer token não aprovado/ inválido"}
            return {"status": "invalid", "error": "acesso negado: " + txt[:200]}
        return {"status": "invalid", "error": f"HTTP {r.status_code}: {r.text[:200]}"}
    except Exception as exc:
        return {"status": "invalid", "error": f"{type(exc).__name__}: {exc}"}


def check_ga4(measurement_id: Optional[str], api_secret: Optional[str]) -> dict:
    """Sends a debug event to GA4. Returns the validation_messages payload."""
    if not (measurement_id and api_secret):
        return {"status": "unknown", "error": "no measurement_id or api_secret"}
    try:
        r = httpx.post(
            "https://www.google-analytics.com/debug/mp/collect",
            params={"measurement_id": measurement_id, "api_secret": api_secret},
            json={
                "client_id": "health.check",
                "events": [{"name": "page_view", "params": {}}],
            },
            timeout=_TIMEOUT,
        )
        if r.status_code != 200:
            return {"status": "invalid", "error": f"HTTP {r.status_code}"}
        body = r.json()
        msgs = body.get("validationMessages") or []
        if msgs:
            return {"status": "invalid", "error": str(msgs[0])[:200]}
        return {"status": "healthy"}
    except Exception as exc:
        return {"status": "invalid", "error": f"{type(exc).__name__}: {exc}"}


def check_tiktok(access_token: Optional[str]) -> dict:
    """Calls /oauth2/access_token_info/ — TikTok's debug endpoint."""
    if not access_token:
        return {"status": "unknown", "error": "no token configured"}
    try:
        r = httpx.post(
            "https://business-api.tiktok.com/open_api/v1.3/oauth2/access_token_info/",
            json={"access_token": access_token},
            timeout=_TIMEOUT,
        )
        body = r.json()
        if r.status_code != 200 or body.get("code") not in (0, "0"):
            return {"status": "invalid", "error": body.get("message") or r.text[:200]}
        return {"status": "healthy"}
    except Exception as exc:
        return {"status": "invalid", "error": f"{type(exc).__name__}: {exc}"}


def check_pinterest(ad_account_id: Optional[str], access_token: Optional[str]) -> dict:
    """Hits /v5/user_account — cheapest authenticated endpoint."""
    if not (ad_account_id and access_token):
        return {"status": "unknown", "error": "no ad_account_id or token"}
    try:
        r = httpx.get(
            "https://api.pinterest.com/v5/user_account",
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=_TIMEOUT,
        )
        if r.status_code == 200:
            return {"status": "healthy"}
        if r.status_code in (401, 403):
            return {"status": "expired", "error": r.text[:200]}
        return {"status": "invalid", "error": f"HTTP {r.status_code}: {r.text[:200]}"}
    except Exception as exc:
        return {"status": "invalid", "error": f"{type(exc).__name__}: {exc}"}


def check_shopify(shop_domain: Optional[str], access_token: Optional[str]) -> dict:
    """Calls /admin/api/{ver}/shop.json — the cheapest authenticated probe."""
    if not (shop_domain and access_token):
        return {"status": "unknown", "error": "no domain or token"}
    try:
        r = httpx.get(
            f"https://{shop_domain}/admin/api/2024-10/shop.json",
            headers={
                "X-Shopify-Access-Token": access_token,
                "Accept":                 "application/json",
            },
            timeout=_TIMEOUT,
        )
        if r.status_code == 200:
            return {"status": "healthy"}
        if r.status_code in (401, 403):
            return {"status": "invalid", "error": "token rejected by Shopify"}
        return {"status": "invalid", "error": f"HTTP {r.status_code}"}
    except Exception as exc:
        return {"status": "invalid", "error": f"{type(exc).__name__}: {exc}"}


# ── aggregate ────────────────────────────────────────────────────────────────

_PLATFORM_COLS = {
    "meta":       ("meta_token_health",       "meta_token_expires_at",         "meta_last_error"),
    "google_ads": ("google_ads_token_health", "google_ads_token_checked_at",   "google_ads_token_error"),
    "ga4":        ("ga4_health",              "ga4_checked_at",                "ga4_error"),
    "tiktok":     ("tiktok_token_health",     "tiktok_token_checked_at",       "tiktok_token_error"),
    "pinterest":  ("pinterest_token_health",  "pinterest_token_checked_at",    "pinterest_token_error"),
    "shopify":    ("shopify_health",          "shopify_checked_at",            "shopify_error"),
}


def check_all(client: dict, persist: bool = True) -> dict:
    """
    Run every probe for one client row. Returns a dict keyed by platform,
    each value `{status, last_checked, expires_at?, error?, configured: bool}`.

    `client` must include the credential columns selected from `clients`.
    When `persist=True`, also writes the result back to the matching
    `<platform>_health` / `<platform>_checked_at` columns.
    """
    now_iso = datetime.now(timezone.utc).isoformat()
    results: dict[str, dict] = {}

    results["meta"]       = check_meta(client.get("meta_access_token"), client.get("meta_pixel_id"))
    results["google_ads"] = check_google_ads(
        client.get("google_ads_customer_id"),
        client.get("google_ads_refresh_token"),
        client.get("google_ads_login_customer_id"),
    )
    results["ga4"]        = check_ga4(client.get("ga4_measurement_id"), client.get("ga4_api_secret"))
    results["tiktok"]     = check_tiktok(client.get("tiktok_access_token"))
    results["pinterest"]  = check_pinterest(
        client.get("pinterest_ad_account_id"), client.get("pinterest_access_token"),
    )
    results["shopify"]    = check_shopify(
        client.get("shopify_store_domain") or client.get("shopify_domain"),
        client.get("shopify_access_token"),
    )

    for platform, info in results.items():
        info["configured"] = info.get("status") != "unknown" or info.get("error") not in (
            "no token configured", "no customer_id or refresh_token",
            "no measurement_id or api_secret", "no domain or token",
        )
        info["last_checked"] = now_iso

    if not persist:
        return results

    sb = get_supabase()
    update: dict = {}
    for platform, info in results.items():
        cols = _PLATFORM_COLS.get(platform)
        if not cols:
            continue
        health_col, checked_col, error_col = cols
        # Meta uses its own (existing) columns for health + expires_at and lacks
        # a dedicated error column, so we skip the error part to stay schema-safe.
        if platform == "meta":
            update[health_col]  = info["status"]
            if info.get("expires_at"):
                update[checked_col] = datetime.fromtimestamp(info["expires_at"], tz=timezone.utc).isoformat()
            continue
        update[health_col]  = info["status"]
        update[checked_col] = now_iso
        if info.get("error"):
            update[error_col] = info["error"][:500]
        else:
            update[error_col] = None

    try:
        sb.table("clients").update(update).eq("id", client["id"]).execute()
    except Exception as exc:
        logger.warning("integrations_health persist failed for %s: %s", client.get("pixel_id"), exc)

    return results


def run_hourly_for_all_clients() -> None:
    """Cron entry point — probe every active client once an hour."""
    sb = get_supabase()
    try:
        clients = (
            sb.table("clients")
            .select(
                "id, pixel_id, meta_access_token, meta_pixel_id, "
                "google_ads_customer_id, google_ads_refresh_token, google_ads_login_customer_id, "
                "ga4_measurement_id, ga4_api_secret, "
                "tiktok_access_token, "
                "pinterest_ad_account_id, pinterest_access_token, "
                "shopify_store_domain, shopify_domain, shopify_access_token"
            )
            .eq("is_active", True)
            .execute()
        ).data or []
    except Exception as exc:
        logger.error("integrations_health: client load failed: %s", exc)
        return

    for c in clients:
        try:
            check_all(c, persist=True)
        except Exception as exc:
            logger.warning("integrations_health: %s failed: %s", c.get("pixel_id"), exc)
    logger.info("integrations_health: checked %d clients", len(clients))
