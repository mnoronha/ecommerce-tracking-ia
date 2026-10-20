"""
Read-only Google Ads change_event ingest.

Fetches changes made to campaigns, ad groups, ads, keywords, and budgets
within a given time window using the GAQL change_event resource (v23).

IMPORTANT: This module is read-only. No mutating Google Ads API calls.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

import httpx

from ..config import settings
from .google_ads import _get_access_token  # shared OAuth2 token cache

logger = logging.getLogger(__name__)

_ADS_API = "https://googleads.googleapis.com/v23"

_CHANGE_EVENT_QUERY = """\
SELECT
  change_event.resource_name,
  change_event.change_datetime,
  change_event.change_resource_type,
  change_event.resource_change_operation,
  change_event.client_type,
  change_event.new_resource,
  change_event.old_resource,
  change_event.changed_fields,
  change_event.campaign,
  change_event.ad_group,
  change_event.ad,
  change_event.keyword,
  change_event.feed_item
FROM change_event
WHERE change_event.change_datetime >= '{since}'
  AND change_event.change_datetime < '{until}'
ORDER BY change_event.change_datetime DESC
LIMIT 10000
"""


def fetch_change_events(
    customer_id: str,
    refresh_token: str,
    since_dt: datetime,
    until_dt: datetime,
    manager_id: Optional[str] = None,
) -> tuple[str, list[dict]]:
    """
    Fetch change events for a Google Ads customer account in [since_dt, until_dt).

    Returns (status, events):
      READY            — success, events may be empty (healthy-empty = READY + 0 rows)
      PERMISSION_DENIED — credentials valid but change_event resource inaccessible
      ERROR            — network, auth, or unexpected API response

    Never raises.
    """
    if not all([
        customer_id,
        refresh_token,
        settings.GOOGLE_ADS_DEVELOPER_TOKEN,
        settings.GOOGLE_ADS_OAUTH_CLIENT_ID,
        settings.GOOGLE_ADS_OAUTH_CLIENT_SECRET,
    ]):
        logger.warning("google_ads_change_history: missing credentials for %s", customer_id)
        return "ERROR", []

    access_token = _get_access_token(
        settings.GOOGLE_ADS_OAUTH_CLIENT_ID,
        settings.GOOGLE_ADS_OAUTH_CLIENT_SECRET,
        refresh_token,
    )
    if not access_token:
        logger.warning("google_ads_change_history: token refresh failed for %s", customer_id)
        return "ERROR", []

    clean_cid = customer_id.replace("-", "").replace(" ", "")
    mcc = manager_id or settings.GOOGLE_ADS_MANAGER_ID or ""

    headers = {
        "Authorization":   f"Bearer {access_token}",
        "developer-token": settings.GOOGLE_ADS_DEVELOPER_TOKEN,
        "Content-Type":    "application/json",
    }
    if mcc:
        headers["login-customer-id"] = mcc.replace("-", "").replace(" ", "")

    # Google GAQL datetime: YYYY-MM-DD HH:MM:SS in account timezone (UTC here)
    since_str = since_dt.strftime("%Y-%m-%d %H:%M:%S")
    until_str = until_dt.strftime("%Y-%m-%d %H:%M:%S")
    query = _CHANGE_EVENT_QUERY.format(since=since_str, until=until_str)

    all_events: list[dict] = []
    page_token: Optional[str] = None

    try:
        while True:
            body: dict = {"query": query}
            if page_token:
                body["pageToken"] = page_token

            resp = httpx.post(
                f"{_ADS_API}/customers/{clean_cid}/googleAds:search",
                headers=headers,
                json=body,
                timeout=20.0,
            )

            if resp.status_code == 403:
                logger.warning(
                    "google_ads_change_history: PERMISSION_DENIED for %s: %s",
                    clean_cid, resp.text[:200],
                )
                return "PERMISSION_DENIED", []

            if resp.status_code != 200:
                logger.warning(
                    "google_ads_change_history: HTTP %s for %s: %s",
                    resp.status_code, clean_cid, resp.text[:300],
                )
                return "ERROR", []

            data = resp.json()
            results = data.get("results") or []
            all_events.extend(results)

            page_token = data.get("nextPageToken")
            if not page_token:
                break

    except httpx.TimeoutException:
        logger.warning("google_ads_change_history: timeout for %s", clean_cid)
        return "ERROR", []
    except Exception as exc:
        logger.error("google_ads_change_history: fetch failed for %s: %s", clean_cid, exc)
        return "ERROR", []

    logger.info(
        "google_ads_change_history: %d events for %s (%s → %s)",
        len(all_events), clean_cid, since_str, until_str,
    )
    return "READY", all_events
