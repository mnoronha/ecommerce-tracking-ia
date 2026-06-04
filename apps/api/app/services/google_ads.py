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
    gbraid:               Optional[str] = None,
    wbraid:               Optional[str] = None,
    email:                Optional[str] = None,
    phone:                Optional[str] = None,
    order_id:             Optional[str] = None,
    occurred_at:          Optional[datetime] = None,
    manager_id:           Optional[str] = None,
    value_override:       Optional[float] = None,
) -> tuple[bool, Optional[str], Optional[str]]:
    """
    Upload a conversion to Google Ads.

    Match strategy (best-available):
      1. gclid present  → Click conversion with GCLID + enhanced identifiers
      2. no gclid       → Enhanced Conversions for Leads (hashed email/phone only)

    Either gclid OR (email/phone) is required.

    The conversion action must be configured in the Google Ads UI to accept
    enhanced conversions. order_id is included whenever provided so the upload
    is idempotent across retries.

    SaaS model: OAuth app credentials (developer token, client id/secret) are
    shared across all clients in Railway env vars; refresh_token is per-client.

    Returns (success, error_message, match_type). match_type is 'gclid' or
    'enhanced_only'. Never raises.
    """
    if not all([customer_id, conversion_action_id, refresh_token,
                settings.GOOGLE_ADS_DEVELOPER_TOKEN,
                settings.GOOGLE_ADS_OAUTH_CLIENT_ID,
                settings.GOOGLE_ADS_OAUTH_CLIENT_SECRET]):
        logger.debug("google_ads: skipped — missing OAuth/customer credentials")
        return False, "missing OAuth/customer credentials", None

    # Drop obviously-truncated click IDs (valid ones are ~70-100 chars) so we
    # never send garbage that Google rejects with "could not be decoded".
    gclid  = gclid  if (gclid  and len(gclid)  >= 20) else None
    gbraid = gbraid if (gbraid and len(gbraid) >= 20) else None
    wbraid = wbraid if (wbraid and len(wbraid) >= 20) else None

    if not (gclid or gbraid or wbraid or email or phone):
        logger.debug("google_ads: skipped — no click id and no email/phone")
        return False, "no click id and no email/phone", None

    access_token = _get_access_token(
        settings.GOOGLE_ADS_OAUTH_CLIENT_ID,
        settings.GOOGLE_ADS_OAUTH_CLIENT_SECRET,
        refresh_token,
    )
    if not access_token:
        return False, "could not obtain access token (refresh_token rejected?)", None

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

    identifiers = []
    if email:
        identifiers.append({"hashedEmail": _sha256(email)})
    phone_e164 = _normalize_phone_e164(phone)
    if phone_e164:
        identifiers.append({"hashedPhoneNumber": _sha256(phone_e164)})

    # Exactly one click id per conversion; precedence gclid > gbraid > wbraid.
    click_field, click_value = None, None
    if gclid:
        click_field, click_value = "gclid", gclid
    elif gbraid:
        click_field, click_value = "gbraid", gbraid
    elif wbraid:
        click_field, click_value = "wbraid", wbraid

    def _build_conv(with_click: bool) -> dict:
        conv: dict = {
            "conversionAction":   f"customers/{clean_cid}/conversionActions/{conversion_action_id}",
            "conversionDateTime": conv_time,
            "conversionValue":    bid_value,
            "currencyCode":       (currency or "BRL").upper(),
        }
        if with_click and click_field:
            conv[click_field] = click_value
        if order_id:
            conv["orderId"] = str(order_id)
        if identifiers:
            conv["userIdentifiers"] = identifiers
        return conv

    def _attempt(with_click: bool) -> tuple[bool, Optional[str]]:
        """One upload with up to 3 retries on 5xx/network. Returns (ok, err)."""
        payload = {"conversions": [_build_conv(with_click)], "partialFailure": True}
        label = (click_field if with_click and click_field else "enhanced_only")
        delay = 1.0
        for attempt in range(3):
            try:
                resp = httpx.post(
                    f"{_ADS_API}/customers/{clean_cid}:uploadClickConversions",
                    headers=headers, json=payload, timeout=15.0,
                )
                if resp.status_code == 200:
                    result = resp.json()
                    if result.get("partialFailureError"):
                        return False, str(result["partialFailureError"])[:300]
                    logger.info("google_ads conversion sent (%s) — order=%s", label, order_id)
                    return True, None
                if 400 <= resp.status_code < 500:
                    return False, f"HTTP {resp.status_code}: {resp.text[:300]}"
                logger.warning("google_ads HTTP %s attempt %d/3 (%s)",
                               resp.status_code, attempt + 1, label)
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                logger.warning("google_ads network error %d/3 (%s): %s", attempt + 1, label, exc)
            except Exception as exc:
                return False, f"{type(exc).__name__}: {str(exc)[:200]}"
            if attempt < 2:
                time.sleep(delay * (2 ** attempt))
        return False, "failed after 3 attempts"

    # Try click id first (direct attribution), fall back to Enhanced Conversions
    # (email/phone) if the click id is invalid/foreign — a bad click id otherwise
    # rejects the whole conversion even when we have valid identifiers.
    if click_field:
        ok, err = _attempt(with_click=True)
        if ok:
            return True, None, click_field
        if identifiers:
            logger.info("google_ads: %s failed (%s) — retrying enhanced-only order=%s",
                        click_field, (err or "")[:80], order_id)
            ok2, err2 = _attempt(with_click=False)
            if ok2:
                # Sent, but lost click attribution. Surface why the click id was
                # rejected so it's persisted (not just logged) for diagnosis.
                return True, f"note: {click_field} rejected → {(err or '')[:400]}", "enhanced_only"
            return False, err2, "enhanced_only"
        return False, err, click_field

    ok, err = _attempt(with_click=False)
    if ok:
        return True, None, "enhanced_only"
    return False, err, "enhanced_only"


def list_conversion_actions(
    customer_id:  str,
    refresh_token: str,
    manager_id:   Optional[str] = None,
) -> tuple[bool, Optional[str], list[dict]]:
    """List every conversion action in the account (read-only diagnostic).

    Use this to pick the correct conversion_action_id for server-side uploads.
    The number to store is conversion_action.id — the same value the Google Ads
    UI shows as `ctId` in a conversion action's detail URL, NOT the account-level
    `ocid`/`ascid` (which is identical across all actions).

    Returns (ok, error, actions) where each action is
    {id, name, type, category, status, primary_for_goal}. Never raises.
    """
    if not all([customer_id, refresh_token,
                settings.GOOGLE_ADS_DEVELOPER_TOKEN,
                settings.GOOGLE_ADS_OAUTH_CLIENT_ID,
                settings.GOOGLE_ADS_OAUTH_CLIENT_SECRET]):
        return False, "missing OAuth/customer credentials", []

    access_token = _get_access_token(
        settings.GOOGLE_ADS_OAUTH_CLIENT_ID,
        settings.GOOGLE_ADS_OAUTH_CLIENT_SECRET,
        refresh_token,
    )
    if not access_token:
        return False, "could not obtain access token (refresh_token rejected?)", []

    clean_cid = customer_id.replace("-", "").replace(" ", "")
    headers = {
        "Authorization":   f"Bearer {access_token}",
        "developer-token": settings.GOOGLE_ADS_DEVELOPER_TOKEN,
        "Content-Type":    "application/json",
    }
    if manager_id:
        headers["login-customer-id"] = manager_id.replace("-", "")

    # No LIMIT/pageSize together (Google rejects mixing them — see notes).
    query = (
        "SELECT conversion_action.id, conversion_action.name, "
        "conversion_action.type, conversion_action.category, "
        "conversion_action.status, conversion_action.primary_for_goal "
        "FROM conversion_action ORDER BY conversion_action.name"
    )
    try:
        resp = httpx.post(
            f"{_ADS_API}/customers/{clean_cid}/googleAds:search",
            headers=headers, json={"query": query}, timeout=15.0,
        )
    except Exception as exc:
        return False, f"{type(exc).__name__}: {str(exc)[:200]}", []

    if resp.status_code != 200:
        return False, f"HTTP {resp.status_code}: {resp.text[:300]}", []

    actions: list[dict] = []
    for row in resp.json().get("results", []):
        ca = row.get("conversionAction", {})
        actions.append({
            "id":               ca.get("id"),
            "name":             ca.get("name"),
            "type":             ca.get("type"),
            "category":         ca.get("category"),
            "status":           ca.get("status"),
            "primary_for_goal": ca.get("primaryForGoal"),
        })
    return True, None, actions


def fetch_campaign_insights(
    customer_id:   str,
    refresh_token: str,
    start_date:    str,
    end_date:      str,
    manager_id:    Optional[str] = None,
    limit:         int = 8,
) -> list[dict]:
    """
    Campaign-level performance for the period, for the monthly report.

    Returns top `limit` campaigns by cost, each:
      {campaign_id, campaign_name, spend, impressions, clicks, conversions,
       conversions_value, roas, cpa, ctr, status}

    Best-effort: returns [] on any failure (missing creds, API error) so the
    report never breaks. start_date/end_date are inclusive YYYY-MM-DD.
    Revenue here is Google's reported conversions_value — differs from our
    server-side attribution; the report footnotes this.
    """
    if not all([customer_id, refresh_token,
                settings.GOOGLE_ADS_DEVELOPER_TOKEN,
                settings.GOOGLE_ADS_OAUTH_CLIENT_ID,
                settings.GOOGLE_ADS_OAUTH_CLIENT_SECRET]):
        return []

    access_token = _get_access_token(
        settings.GOOGLE_ADS_OAUTH_CLIENT_ID,
        settings.GOOGLE_ADS_OAUTH_CLIENT_SECRET,
        refresh_token,
    )
    if not access_token:
        return []

    clean_cid = customer_id.replace("-", "").replace(" ", "")
    headers = {
        "Authorization":   f"Bearer {access_token}",
        "developer-token": settings.GOOGLE_ADS_DEVELOPER_TOKEN,
        "Content-Type":    "application/json",
    }
    mcc = manager_id or settings.GOOGLE_ADS_MANAGER_ID
    if mcc:
        headers["login-customer-id"] = mcc.replace("-", "")

    # No LIMIT in GAQL alongside an ORDER BY pageSize quirk — slice client-side.
    query = (
        "SELECT campaign.name, campaign.id, campaign.status, "
        "metrics.cost_micros, metrics.impressions, metrics.clicks, "
        "metrics.conversions, metrics.conversions_value "
        "FROM campaign "
        f"WHERE segments.date BETWEEN '{start_date}' AND '{end_date}' "
        "AND metrics.cost_micros > 0 "
        "ORDER BY metrics.cost_micros DESC"
    )
    try:
        resp = httpx.post(
            f"{_ADS_API}/customers/{clean_cid}/googleAds:search",
            headers=headers, json={"query": query}, timeout=20.0,
        )
    except Exception as exc:
        logger.warning("google_ads fetch_campaign_insights: %s", exc)
        return []

    if resp.status_code != 200:
        logger.warning("google_ads campaigns HTTP %s: %s", resp.status_code, resp.text[:300])
        return []

    rows: list[dict] = []
    for row in resp.json().get("results", []):
        camp = row.get("campaign", {})
        m    = row.get("metrics", {})
        spend = round(int(m.get("costMicros") or 0) / 1_000_000, 2)
        if spend <= 0:
            continue
        impressions = int(m.get("impressions") or 0)
        clicks      = int(m.get("clicks") or 0)
        conversions = float(m.get("conversions") or 0)
        conv_value  = round(float(m.get("conversionsValue") or 0), 2)
        rows.append({
            "campaign_id":       camp.get("id"),
            "campaign_name":     camp.get("name") or "—",
            "status":            camp.get("status") or "",
            "spend":             spend,
            "impressions":       impressions,
            "clicks":            clicks,
            "conversions":       conversions,
            "conversions_value": conv_value,
            "roas":              round(conv_value / spend, 2) if spend > 0 else None,
            "cpa":               round(spend / conversions, 2) if conversions > 0 else None,
            "ctr":               round(clicks / impressions * 100, 2) if impressions > 0 else 0.0,
        })

    rows.sort(key=lambda x: x["spend"], reverse=True)
    logger.info("google_ads: %d campaigns fetched for %s", len(rows), clean_cid)
    return rows[:limit]
