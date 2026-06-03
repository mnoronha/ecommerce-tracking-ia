"""
Creative metadata sync — pulls ad creative assets from Meta Marketing API.

Stores per-ad creative info (thumbnail/image URL, body copy, headline, CTA) in
`ad_creatives`. Joined with `meta_ad_attributions` (per-day performance) this
gives us "image of the ad + its 7d performance" for Sprint 5.2's vision-based
analysis.

Runs daily as part of the meta_attribution_sync flow, but cheap enough that
it can also be triggered ad-hoc from the dashboard.

Docs:
  https://developers.facebook.com/docs/marketing-api/reference/adgroup
  https://developers.facebook.com/docs/marketing-api/reference/ad-creative
"""

import logging
from datetime import datetime, timezone
from typing import Optional

import httpx

from ..database import get_supabase
from ..services import crypto

logger = logging.getLogger(__name__)

_GRAPH = "https://graph.facebook.com/v19.0"


def _fetch_ads_with_creatives(account_id: str, access_token: str) -> list[dict]:
    """
    Fetch active ads + their nested creative metadata in a single Graph call.
    Limits to ACTIVE / PAUSED (excludes deleted) and last 365d to keep payload
    bounded. Paginates.
    """
    clean = account_id.removeprefix("act_")
    url = f"{_GRAPH}/act_{clean}/ads"
    params: Optional[dict] = {
        "limit":  100,
        "fields": (
            "id,name,adset_id,campaign_id,effective_status,"
            "creative{id,thumbnail_url,image_url,video_id,body,title,call_to_action_type}"
        ),
        "filtering": '[{"field":"effective_status","operator":"IN","value":["ACTIVE","PAUSED"]}]',
        "access_token": access_token,
    }

    rows: list[dict] = []
    while url:
        try:
            resp = httpx.get(url, params=params, timeout=45.0)
            if resp.status_code != 200:
                logger.warning("creative_sync HTTP %s: %s", resp.status_code, resp.text[:300])
                return rows
            body = resp.json()
            rows.extend(body.get("data", []))
            paging = (body.get("paging") or {}).get("next")
            if paging:
                url, params = paging, None
            else:
                url = None
        except Exception as exc:
            logger.error("creative_sync exception: %s", exc)
            return rows
    return rows


def sync_for_client(client_uuid: str, account_id: str, access_token: str) -> dict:
    """
    Pull ad-level creatives for one client and upsert them. Idempotent.
    Returns {ads_seen, upserted, errors}.
    """
    ads = _fetch_ads_with_creatives(account_id, access_token)
    if not ads:
        return {"ads_seen": 0, "upserted": 0, "errors": 0}

    sb = get_supabase()
    rows = []
    for ad in ads:
        creative = ad.get("creative") or {}
        rows.append({
            "client_id":        client_uuid,
            "ad_id":            str(ad.get("id") or ""),
            "ad_name":          ad.get("name"),
            "adset_id":         ad.get("adset_id"),
            "campaign_id":      ad.get("campaign_id"),
            "creative_id":      str(creative.get("id")) if creative.get("id") else None,
            "thumbnail_url":    creative.get("thumbnail_url"),
            "image_url":        creative.get("image_url"),
            "video_id":         creative.get("video_id"),
            "body":             (creative.get("body") or "")[:2000] or None,
            "headline":         creative.get("title"),
            "call_to_action":   creative.get("call_to_action_type"),
            "effective_status": ad.get("effective_status"),
            "last_synced_at":   datetime.now(timezone.utc).isoformat(),
        })

    upserted = 0
    errors   = 0
    # Filter out rows without ad_id (shouldn't happen but defensive)
    rows = [r for r in rows if r["ad_id"]]
    for i in range(0, len(rows), 200):
        chunk = rows[i:i + 200]
        try:
            sb.table("ad_creatives").upsert(chunk, on_conflict="client_id,ad_id").execute()
            upserted += len(chunk)
        except Exception as exc:
            errors += len(chunk)
            logger.warning("creative_sync upsert failed (chunk %d, client %s): %s",
                           i, client_uuid, exc)
    logger.info("creative_sync: client=%s ads=%d upserted=%d errors=%d",
                client_uuid, len(rows), upserted, errors)
    return {"ads_seen": len(ads), "upserted": upserted, "errors": errors}


def run_daily_for_all_clients() -> None:
    """Scheduler entry — sync creatives for every active client with Meta creds."""
    sb = get_supabase()
    try:
        clients = (
            sb.table("clients")
            .select("id, meta_ad_account_id, meta_access_token")
            .eq("is_active", True)
            .execute()
        ).data or []
    except Exception as exc:
        logger.error("creative_sync: failed to list clients: %s", exc)
        return

    for c in clients:
        if not (c.get("meta_ad_account_id") and c.get("meta_access_token")):
            continue
        try:
            sync_for_client(c["id"], c["meta_ad_account_id"], crypto.decrypt_secret(c["meta_access_token"]))
        except Exception as exc:
            logger.warning("creative_sync: client %s failed: %s", c.get("id"), exc)
