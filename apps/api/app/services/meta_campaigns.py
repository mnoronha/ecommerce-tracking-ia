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
_EMBEDDED_ID = re.compile(r"(\d{10,20})")


def is_meta_id(value: str) -> bool:
    """Heuristic: Meta IDs are pure numerics with 12-19 digits."""
    return bool(value) and bool(_NUMERIC_ID.match(value)) and 10 <= len(value) <= 20


def extract_meta_id(value: str) -> Optional[str]:
    """
    Extract a Meta numeric ID from a UTM string that may have a human prefix.
    Handles pure IDs ("120210118442") and embedded IDs ("meta paid|120210118442").
    Returns None when no qualifying numeric sequence is found.
    """
    if not value:
        return None
    if is_meta_id(value):
        return value
    m = _EMBEDDED_ID.search(value)
    return m.group(1) if m else None


def _paginate(url: str, params: dict) -> list[dict]:
    """Iterate Graph API cursor pagination, returning the flat list of nodes."""
    rows: list[dict] = []
    while url:
        try:
            resp = httpx.get(url, params=params, timeout=30.0)
            if resp.status_code != 200:
                logger.warning("meta paginate HTTP %s: %s", resp.status_code, resp.text[:300])
                return rows
            body = resp.json()
            rows.extend(body.get("data", []))
            paging = (body.get("paging") or {}).get("next")
            if paging:
                url, params = paging, None
            else:
                url = None
        except Exception as exc:
            logger.error("meta paginate exception: %s", exc)
            return rows
    return rows


def sync_campaign_names(client_uuid: str, ad_account_id: str, access_token: str) -> dict:
    """
    Pull every campaign, adset and ad on the ad account, upsert into
    meta_campaign_names. Customers' UTM params can use any of:
      - {{campaign.id}} → 12-digit numeric
      - {{adset.id}}    → 17-18 digit numeric
      - {{ad.id}}       → 17-18 digit numeric
    so we cache all three and let the lookup match by id at any level.
    """
    if not (ad_account_id and access_token):
        return {"error": "missing credentials", "synced": 0}

    clean_id = ad_account_id.removeprefix("act_")

    # Campaigns
    campaigns = _paginate(
        f"{_GRAPH}/act_{clean_id}/campaigns",
        {"fields": "id,name,status,objective", "limit": 500, "access_token": access_token},
    )
    # Adsets — bring campaign_id so we can show "(na campanha X)"
    adsets = _paginate(
        f"{_GRAPH}/act_{clean_id}/adsets",
        {"fields": "id,name,status,campaign_id,campaign{name}", "limit": 500, "access_token": access_token},
    )
    # Ads — bring adset_id and campaign_id for parent context
    ads = _paginate(
        f"{_GRAPH}/act_{clean_id}/ads",
        {"fields": "id,name,status,adset_id,adset{name},campaign{id,name}", "limit": 500, "access_token": access_token},
    )

    sb = get_supabase()
    upserts: list[dict] = []

    for c in campaigns:
        if c.get("id") and c.get("name"):
            upserts.append({
                "client_id":   client_uuid,
                "campaign_id": str(c["id"]),
                "name":        c["name"],
                "level":       "campaign",
                "parent_id":   None,
                "parent_name": None,
                "status":      c.get("status"),
                "objective":   c.get("objective"),
                "updated_at":  "now()",
            })

    for s in adsets:
        if s.get("id") and s.get("name"):
            camp = s.get("campaign") or {}
            upserts.append({
                "client_id":   client_uuid,
                "campaign_id": str(s["id"]),
                "name":        s["name"],
                "level":       "adset",
                "parent_id":   str(s.get("campaign_id") or camp.get("id") or ""),
                "parent_name": camp.get("name"),
                "status":      s.get("status"),
                "objective":   None,
                "updated_at":  "now()",
            })

    for a in ads:
        if a.get("id") and a.get("name"):
            adset = a.get("adset") or {}
            camp  = a.get("campaign") or {}
            # For ads we surface "Campanha · Adset · Ad" via parent_name as
            # the campaign name (most useful). Adset name is in the ad name
            # path so we don't lose it.
            upserts.append({
                "client_id":   client_uuid,
                "campaign_id": str(a["id"]),
                "name":        a["name"],
                "level":       "ad",
                "parent_id":   str(a.get("adset_id") or adset.get("id") or ""),
                "parent_name": camp.get("name"),
                "status":      a.get("status"),
                "objective":   None,
                "updated_at":  "now()",
            })

    if upserts:
        # Chunk to keep Supabase REST happy on large accounts
        for i in range(0, len(upserts), 500):
            chunk = upserts[i : i + 500]
            try:
                sb.table("meta_campaign_names").upsert(
                    chunk,
                    on_conflict="client_id,campaign_id",
                ).execute()
            except Exception as exc:
                logger.warning("meta_campaign_names upsert failed: %s", exc)

    return {
        "synced_campaigns": len(campaigns),
        "synced_adsets":    len(adsets),
        "synced_ads":       len(ads),
        "synced":           len(upserts),
    }


def get_name_map(client_uuid: str, ids: list[str]) -> dict[str, str]:
    """
    Fetch known mappings for a list of Meta IDs (any level). Returns
    {id: display_name}. For ads/adsets the display includes the parent
    campaign name so the journey screen reads naturally:
      - campaign:  "Pareto.Vendas [Masculino]"
      - adset:     "Pareto.Vendas [Masculino] · Ads — Lookalike 1%"
      - ad:        "Pareto.Vendas [Masculino] · Criativo Helena #3"
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
                .select("campaign_id, name, level, parent_name")
                .eq("client_id", client_uuid)
                .in_("campaign_id", chunk)
                .execute()
            ).data or []
            for row in r:
                level   = row.get("level") or "campaign"
                name    = row["name"]
                parent  = row.get("parent_name")
                if level == "campaign" or not parent:
                    display = name
                else:
                    # "Parent · Self" so analyst sees the campaign first
                    display = f"{parent} · {name}"
                out[row["campaign_id"]] = display
        except Exception as exc:
            logger.debug("get_name_map chunk failed: %s", exc)
    return out
