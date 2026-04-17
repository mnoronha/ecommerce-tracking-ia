"""
Google Ads Conversion API — server-side conversion upload.

Sends purchase events via GCLID to Google Ads for accurate attribution,
bypassing browser-side tag requirements (replaces gtag conversion tracking).

Docs: https://developers.google.com/google-ads/api/docs/conversions/upload-clicks
"""

import logging
import time
from datetime import datetime, timezone
from typing import Optional

import httpx

from ..config import settings

logger = logging.getLogger(__name__)

_TOKEN_URL = "https://oauth2.googleapis.com/token"
_ADS_API   = "https://googleads.googleapis.com/v17"

# In-process token cache — avoids refreshing on every conversion
_token_cache: dict = {"token": None, "expires_at": 0.0}


def _get_access_token(client_id: str, client_secret: str, refresh_token: str) -> Optional[str]:
    """Return a valid OAuth2 access token, refreshing if within 60s of expiry."""
    now = time.time()
    if _token_cache["token"] and _token_cache["expires_at"] > now + 60:
        return _token_cache["token"]

    try:
        resp = httpx.post(
            _TOKEN_URL,
            data={
                "client_id":     client_id,
                "client_secret": client_secret,
                "refresh_token": refresh_token,
                "grant_type":    "refresh_token",
            },
            timeout=10.0,
        )
        if resp.status_code == 200:
            data = resp.json()
            _token_cache["token"]      = data["access_token"]
            _token_cache["expires_at"] = now + data.get("expires_in", 3600)
            logger.debug("google_ads: OAuth2 token refreshed")
            return _token_cache["token"]
        logger.warning("google_ads token refresh HTTP %s: %s", resp.status_code, resp.text[:200])
    except Exception as exc:
        logger.error("google_ads _get_access_token: %s", exc)
    return None


def send_conversion(
    customer_id:          str,
    conversion_action_id: str,
    gclid:                str,
    value:                float,
    currency:             str,
    order_id:             str,
    occurred_at:          Optional[datetime] = None,
    manager_id:           Optional[str] = None,
) -> bool:
    """
    Upload a click conversion to Google Ads API.

    Requires global env vars:
      GOOGLE_ADS_DEVELOPER_TOKEN, GOOGLE_ADS_OAUTH_CLIENT_ID,
      GOOGLE_ADS_OAUTH_CLIENT_SECRET, GOOGLE_ADS_REFRESH_TOKEN

    Per-client config (clients table):
      google_ads_customer_id, google_ads_conversion_action_id
      google_ads_refresh_token (optional — falls back to global)

    Returns True on success, False on any error (never raises).
    """
    if not all([customer_id, conversion_action_id, gclid,
                settings.GOOGLE_ADS_DEVELOPER_TOKEN,
                settings.GOOGLE_ADS_OAUTH_CLIENT_ID,
                settings.GOOGLE_ADS_OAUTH_CLIENT_SECRET]):
        logger.debug("google_ads: skipped — missing credentials or gclid")
        return False

    refresh = settings.GOOGLE_ADS_REFRESH_TOKEN
    if not refresh:
        logger.debug("google_ads: skipped — no refresh token")
        return False

    access_token = _get_access_token(
        settings.GOOGLE_ADS_OAUTH_CLIENT_ID,
        settings.GOOGLE_ADS_OAUTH_CLIENT_SECRET,
        refresh,
    )
    if not access_token:
        return False

    clean_cid    = customer_id.replace("-", "").replace(" ", "")
    conv_time    = (occurred_at or datetime.now(timezone.utc)).strftime("%Y-%m-%d %H:%M:%S+00:00")

    headers = {
        "Authorization":   f"Bearer {access_token}",
        "developer-token": settings.GOOGLE_ADS_DEVELOPER_TOKEN,
        "Content-Type":    "application/json",
    }
    if manager_id:
        headers["login-customer-id"] = manager_id.replace("-", "")

    payload = {
        "conversions": [{
            "gclid":              gclid,
            "conversionAction":   f"customers/{clean_cid}/conversionActions/{conversion_action_id}",
            "conversionDateTime": conv_time,
            "conversionValue":    float(value),
            "currencyCode":       (currency or "BRL").upper(),
            "orderId":            str(order_id),
        }],
        "partialFailure": True,
    }

    delay = 1.0
    for attempt in range(3):
        try:
            resp = httpx.post(
                f"{_ADS_API}/customers/{clean_cid}:uploadClickConversions",
                headers=headers,
                json=payload,
                timeout=15.0,
            )
            if resp.status_code == 200:
                result = resp.json()
                if result.get("partialFailureError"):
                    logger.warning("google_ads partial failure for order=%s: %s",
                                   order_id, result["partialFailureError"])
                    return False
                logger.info("google_ads conversion sent — order=%s gclid=%s…",
                            order_id, gclid[:20])
                return True
            if 400 <= resp.status_code < 500:
                logger.warning("google_ads HTTP %s (no retry) order=%s: %s",
                               resp.status_code, order_id, resp.text[:300])
                return False
            logger.warning("google_ads HTTP %s attempt %d/3", resp.status_code, attempt + 1)
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            logger.warning("google_ads network error attempt %d/3: %s", attempt + 1, exc)
        except Exception as exc:
            logger.error("google_ads exception for order=%s: %s", order_id, exc)
            return False
        if attempt < 2:
            time.sleep(delay * (2 ** attempt))

    logger.error("google_ads failed after 3 attempts for order=%s", order_id)
    return False
