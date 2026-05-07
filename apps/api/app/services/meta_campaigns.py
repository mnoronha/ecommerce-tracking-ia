"""
Meta Ads campaign-name resolver.

Customers' UTMs from Meta arrive as raw campaign IDs (`120210118442`) when
the ad URL parameters use `{{campaign.id}}` instead of `{{campaign.name}}`.
This module syncs the id → name map via the Meta Marketing API and serves
fast lookups for the dashboard.

The cache lives in `meta_campaign_names`. A single sync call iterates all
campaigns on the ad account; we paginate when needed.
"""

import logging
import re
from typing import Optional

import httpx

from ..database import get_supabase

logger = logging.getLogger(__name__)

_GRAPH = "https://graph.facebook.com/v19.0"
_NUMERIC_ID = re.compile(r"^\d+$")


def is_meta_id(value: str) -> bool:
    """Heuristic: Meta IDs are pure numerics with 12-19 digits."""
    return bool(value) and bool(_NUMERIC_ID.match(value)) and 10 <= len(value) <= 20


def sync_campaign_names(client_uuid: str, ad_account_id: str, access_token: str) -> dict:
    """
    Pull every campaign on the ad account, upsert into meta_campaign_names.
    Returns a small report dict.
    """
    if not (ad_account_id and access_token):
        return {"error": "missing credentials", "synced": 0}

    clean_id = ad_account_id.removeprefix("act_")
    url = f"{_GRAPH}/act_{clean_id}/campaigns"

    rows: list[dict] = []
    next_url: Optional[str] = url
    next_params: Optional[dict] = {
        "fields":       "id,name,status,objective",
        "limit":        500,
        "access_token": access_token,
    }

    while next_url:
        try:
            resp = httpx.get(next_url, params=next_params, timeout=20.0)
            if resp.status_code != 200:
                logger.warning("meta campaigns HTTP %s: %s", resp.status_code, resp.text[:300])
                return {"error": f"HTTP {resp.status_code}", "synced": len(rows)}
            body = resp.json()
            rows.extend(body.get("data", []))
            paging = (body.get("paging") or {}).get("next")
            next_url, next_params = (paging, None) if paging else (None, None)
        except Exception as exc:
            logger.error("meta campaigns sync exception: %s", exc)
            return {"error": str(exc)[:200], "synced": len(rows)}

    if not rows:
        return {"synced": 0, "total_in_account": 0}

    sb = get_supabase()
    upserts = [
        {
            "client_id":   client_uuid,
            "campaign_id": str(r.get("id")),
            "name":        r.get("name") or "(sem nome)",
            "status":      r.get("status"),
            "objective":   r.get("objective"),
            "updated_at":  "now()",
        }
        for r in rows if r.get("id") and r.get("name")
    ]
    if upserts:
        try:
            sb.table("meta_campaign_names").upsert(
                upserts,
                on_conflict="client_id,campaign_id",
            ).execute()
        except Exception as exc:
            logger.warning("meta_campaign_names upsert failed: %s", exc)
            return {"error": str(exc)[:200], "synced": 0}

    return {
        "synced":             len(upserts),
        "total_in_account":   len(rows),
    }


def get_name_map(client_uuid: str, ids: list[str]) -> dict[str, str]:
    """
    Fetch known mappings for a list of campaign IDs. Returns {id: name}.
    Missing IDs are simply absent from the result.
    """
    if not ids:
        return {}
    sb = get_supabase()
    out: dict[str, str] = {}
    for i in range(0, len(ids), 200):
        chunk = ids[i : i + 200]
        try:
            r = (
                sb.table("meta_campaign_names")
                .select("campaign_id, name")
                .eq("client_id", client_uuid)
                .in_("campaign_id", chunk)
                .execute()
            ).data or []
            for row in r:
                out[row["campaign_id"]] = row["name"]
        except Exception as exc:
            logger.debug("get_name_map chunk failed: %s", exc)
    return out
