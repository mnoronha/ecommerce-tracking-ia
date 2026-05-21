"""
Google Ads Conversion API — server-side conversion upload.

Sends purchase events to Google Ads. Prefers GCLID for direct click
attribution, but falls back to Enhanced Conversions for Leads
(hashed email/phone) when no GCLID is available — covering organic,
direct, social and email-driven sales that previously went unreported.

Docs:
- GCLID upload:        https://developers.google.com/google-ads/api/docs/conversions/upload-clicks
- Enhanced for leads:  https://developers.google.com/google-ads/api/docs/conversions/upload-enhanced-conversions-for-leads
"""

import hashlib
import logging
import time
from datetime import datetime, timezone
from typing import Optional

import httpx

from ..config import settings

logger = logging.getLogger(__name__)

_TOKEN_URL = "https://oauth2.googleapis.com/token"
# Google Ads aposenta versões da API a cada ~ano. v17-v19 já retornam 404.
# Manter nesta constante pra atualizar num lugar só quando a v21 expirar.
_ADS_API   = "https://googleads.googleapis.com/v21"

# Per-refresh-token cache: {refresh_token_prefix -> {token, expires_at}}
# Keyed by first 16 chars of refresh_token to avoid storing full secrets in memory keys.
_token_cache: dict = {}


def _get_access_token(client_id: str, client_secret: str, refresh_token: str) -> Optional[str]:
    """Return a valid OAuth2 access token, refreshing if within 60s of expiry.
    Cache is per refresh_token so multiple clients don't share the same token.
    """
    now = time.time()
    cache_key = refresh_token[:16]
    cached = _token_cache.get(cache_key, {})
    if cached.get("token") and cached.get("expires_at", 0) > now + 60:
        return cached["token"]

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
            _token_cache[cache_key] = {
                "token":      data["access_token"],
                "expires_at": now + data.get("expires_in", 3600),
            }
            logger.debug("google_ads: OAuth2 token refreshed for key=%s…", cache_key)
            return _token_cache[cache_key]["token"]
        logger.warning("google_ads token refresh HTTP %s: %s", resp.status_code, resp.text[:200])
    except Exception as exc:
        logger.error("google_ads _get_access_token: %s", exc)
    return None


def _sha256(value: Optional[str]) -> Optional[str]:
    """SHA-256 the lowercased+trimmed value, as Google requires for user_identifiers."""
    if not value:
        return None
    return hashlib.sha256(value.strip().lower().encode("utf-8")).hexdigest()


def _normalize_phone_e164(phone: Optional[str]) -> Optional[str]:
    """Coerce a phone string to E.164 (+5511999999999). Assumes BR when no country code."""
    if not phone:
        return None
    raw = phone.strip()
    digits = "".join(c for c in raw if c.isdigit())
    if not digits:
        return None
    if raw.startswith("+"):
        return "+" + digits
    # 10/11-digit BR mobile/landline → prepend country code
    if len(digits) in (10, 11):
        return "+55" + digits
    # Already has a country code embedded
    return "+" + digits


def send_conversion(
    customer_id:          str,
    conversion_action_id: str,
    value:                float,
    currency:             str,
    refresh_token:        str,
    gclid:                Optional[str] = None,
    email:                Optional[str] = None,
    phone:                Optional[str] = None,
    order_id:             Optional[str] = None,
    occurred_at:          Optional[datetime] = None,
    manager_id:           Optional[str] = None,
    value_override:       Optional[float] = None,
) -> bool:
    """
    Upload a conversion to Google Ads.

    Match strategy (best-available):
      1. gclid present  → Click conversion with GCLID + enhanced identifiers
      2. no gclid       → Enhanced Conversions for Leads (hashed email/phone only)

    Either gclid OR (email/phone) is required — silently returns False when
    neither is present, so the caller can keep this side-effect optional.

    The conversion action must be configured in the Google Ads UI to accept
    enhanced conversions. order_id is included whenever provided so the upload
    is idempotent across retries.

    SaaS model: OAuth app credentials (developer token, client id/secret) are
    shared across all clients in Railway env vars; refresh_token is per-client.

    Returns True on success, False on any error (never raises).
    """
    if not all([customer_id, conversion_action_id, refresh_token,
                settings.GOOGLE_ADS_DEVELOPER_TOKEN,
                settings.GOOGLE_ADS_OAUTH_CLIENT_ID,
                settings.GOOGLE_ADS_OAUTH_CLIENT_SECRET]):
        logger.debug("google_ads: skipped — missing OAuth/customer credentials")
        return False

    if not (gclid or email or phone):
        logger.debug("google_ads: skipped — no gclid and no email/phone")
        return False

    access_token = _get_access_token(
        settings.GOOGLE_ADS_OAUTH_CLIENT_ID,
        settings.GOOGLE_ADS_OAUTH_CLIENT_SECRET,
        refresh_token,
    )
    if not access_token:
        return False

    clean_cid = customer_id.replace("-", "").replace(" ", "")
    conv_time = (occurred_at or datetime.now(timezone.utc)).strftime("%Y-%m-%d %H:%M:%S+00:00")
    bid_value = float(value_override) if value_override is not None else float(value)

    headers = {
        "Authorization":   f"Bearer {access_token}",
        "developer-token": settings.GOOGLE_ADS_DEVELOPER_TOKEN,
        "Content-Type":    "application/json",
    }
    if manager_id:
        headers["login-customer-id"] = manager_id.replace("-", "")

    conv: dict = {
        "conversionAction":   f"customers/{clean_cid}/conversionActions/{conversion_action_id}",
        "conversionDateTime": conv_time,
        "conversionValue":    bid_value,
        "currencyCode":       (currency or "BRL").upper(),
    }
    if gclid:
        conv["gclid"] = gclid
    if order_id:
        conv["orderId"] = str(order_id)

    identifiers = []
    if email:
        identifiers.append({"hashedEmail": _sha256(email)})
    phone_e164 = _normalize_phone_e164(phone)
    if phone_e164:
        identifiers.append({"hashedPhoneNumber": _sha256(phone_e164)})
    if identifiers:
        conv["userIdentifiers"] = identifiers

    payload = {
        "conversions":   [conv],
        "partialFailure": True,
    }

    match_label = "gclid" if gclid else "enhanced_only"

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
                    logger.warning("google_ads partial failure (%s) order=%s: %s",
                                   match_label, order_id, result["partialFailureError"])
                    return False
                logger.info("google_ads conversion sent (%s) — order=%s",
                            match_label, order_id)
                return True
            if 400 <= resp.status_code < 500:
                logger.warning("google_ads HTTP %s (no retry, %s) order=%s: %s",
                               resp.status_code, match_label, order_id, resp.text[:300])
                return False
            logger.warning("google_ads HTTP %s attempt %d/3 (%s)",
                           resp.status_code, attempt + 1, match_label)
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            logger.warning("google_ads network error attempt %d/3 (%s): %s",
                           attempt + 1, match_label, exc)
        except Exception as exc:
            logger.error("google_ads exception (%s) for order=%s: %s",
                         match_label, order_id, exc)
            return False
        if attempt < 2:
            time.sleep(delay * (2 ** attempt))

    logger.error("google_ads failed after 3 attempts (%s) for order=%s",
                 match_label, order_id)
    return False
