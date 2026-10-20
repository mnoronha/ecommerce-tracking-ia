"""
Read-only Meta Ads activity history ingest.

Fetches account activity log via the Marketing API /act_{id}/activities endpoint.
The `extra_data` field is an undocumented JSON string whose keys vary by event_type.

IMPORTANT: Read-only. No mutating Meta API calls.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

_GRAPH_BASE = "https://graph.facebook.com/v22.0"
_FIELDS = (
    "object_id,object_name,object_type,event_type,"
    "extra_data,translated_text,timestamp,actor_id,actor_name"
)
_PAGE_LIMIT = 100  # Meta default is 25; 100 keeps pagination overhead low


def fetch_activities(
    account_id: str,
    access_token: str,
    since_dt: datetime,
    until_dt: datetime,
) -> tuple[str, list[dict]]:
    """
    Fetch Meta Ads account activity in [since_dt, until_dt).

    Returns (status, activities):
      READY            — success, activities may be empty (healthy-empty = READY + 0 rows)
      PERMISSION_DENIED — token lacks ads_management scope for the activities endpoint
      ERROR            — network, auth, or API error

    Never raises.
    """
    if not account_id or not access_token:
        return "ERROR", []

    clean_id = account_id.removeprefix("act_")
    url: Optional[str] = f"{_GRAPH_BASE}/act_{clean_id}/activities"
    params: dict = {
        "fields":       _FIELDS,
        "since":        int(since_dt.timestamp()),
        "until":        int(until_dt.timestamp()),
        "limit":        _PAGE_LIMIT,
        "access_token": access_token,
    }

    all_activities: list[dict] = []

    try:
        while url:
            resp = httpx.get(url, params=params, timeout=20.0)

            if resp.status_code == 403:
                logger.warning(
                    "meta_ads_change_history: PERMISSION_DENIED for act_%s: %s",
                    clean_id, resp.text[:200],
                )
                return "PERMISSION_DENIED", []

            if resp.status_code != 200:
                body: dict = {}
                ct = resp.headers.get("content-type", "")
                if "json" in ct:
                    try:
                        body = resp.json()
                    except Exception:
                        pass
                err_code = (body.get("error") or {}).get("code", 0)
                # Meta returns permission errors as HTTP 400 with specific error codes
                if err_code in (100, 190, 200, 10, 803, 368):
                    logger.warning(
                        "meta_ads_change_history: PERMISSION_DENIED (code %s) for act_%s",
                        err_code, clean_id,
                    )
                    return "PERMISSION_DENIED", []
                logger.warning(
                    "meta_ads_change_history: HTTP %s for act_%s: %s",
                    resp.status_code, clean_id, resp.text[:300],
                )
                return "ERROR", []

            data = resp.json()
            all_activities.extend(data.get("data") or [])

            # Cursor pagination: follow 'next' until exhausted
            paging = data.get("paging") or {}
            url = paging.get("next") or None
            params = {}  # 'next' URL embeds all params

    except httpx.TimeoutException:
        logger.warning("meta_ads_change_history: timeout for act_%s", clean_id)
        return "ERROR", []
    except Exception as exc:
        logger.error("meta_ads_change_history: fetch failed for act_%s: %s", clean_id, exc)
        return "ERROR", []

    logger.info(
        "meta_ads_change_history: %d activities for act_%s",
        len(all_activities), clean_id,
    )
    return "READY", all_activities
