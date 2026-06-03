"""
Creative Intelligence endpoints — gallery of ads + Claude Vision insights.

GET  /creatives/{pixel_id}                — gallery with per-ad performance
GET  /creatives/{pixel_id}/latest-analysis — latest Claude Vision insight
POST /creatives/{pixel_id}/sync           — pull creatives from Meta now
POST /creatives/{pixel_id}/analyze        — run Vision analysis now (cap 1/h)
"""

import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException

from ..database import get_supabase
from ..services import creative_intelligence, creative_sync, crypto

logger = logging.getLogger(__name__)
router = APIRouter()


def _resolve(pixel_id: str) -> tuple[str, dict]:
    """Look up the client by pixel_id and return (client_uuid, client_row)."""
    r = (
        get_supabase().table("clients")
        .select("id, meta_ad_account_id, meta_access_token")
        .eq("pixel_id", pixel_id)
        .eq("is_active", True)
        .limit(1)
        .execute()
    )
    if not (r and r.data):
        raise HTTPException(404, "Client not found")
    row = crypto.decrypt_client_secrets(r.data[0])
    return row["id"], row


@router.get("/creatives/{pixel_id}", tags=["creatives"])
async def list_creatives(
    pixel_id: str,
    days: int = 30,
    start: str | None = None,
    end:   str | None = None,
):
    """
    Returns one row per ad with creative metadata + aggregated performance over
    the trailing `days`. Sorted by spend desc.
    start/end (YYYY-MM-DD) sobrepõem `days` quando informados.
    """
    client_uuid, _ = _resolve(pixel_id)
    sb = get_supabase()
    if start and end:
        since, until = start, end
    else:
        since = (datetime.now(timezone.utc).date() - timedelta(days=days)).isoformat()
        until = None

    creatives = (
        sb.table("ad_creatives")
        .select("ad_id, ad_name, campaign_id, image_url, thumbnail_url, "
                "body, headline, call_to_action, effective_status, last_synced_at")
        .eq("client_id", client_uuid)
        .execute()
    ).data or []
    if not creatives:
        return {"days": days, "creatives": [], "total_spend": 0, "total_revenue": 0}

    by_ad = {c["ad_id"]: c for c in creatives}
    ad_ids = list(by_ad.keys())

    perf_rows: list[dict] = []
    for i in range(0, len(ad_ids), 200):
        chunk = ad_ids[i:i + 200]
        q = (
            sb.table("meta_ad_attributions")
            .select("ad_id, spend, clicks, impressions, purchases, purchase_value")
            .eq("client_id", client_uuid)
            .gte("date", since)
        )
        if until:
            q = q.lte("date", until)
        r = q.in_("ad_id", chunk).execute()
        perf_rows.extend(r.data or [])

    agg: dict[str, dict] = {}
    for r in perf_rows:
        ad_id = r["ad_id"]
        b = agg.setdefault(ad_id, {"spend": 0.0, "clicks": 0, "impressions": 0,
                                   "purchases": 0, "revenue": 0.0})
        b["spend"]       += float(r.get("spend") or 0)
        b["clicks"]      += int(r.get("clicks") or 0)
        b["impressions"] += int(r.get("impressions") or 0)
        b["purchases"]   += int(r.get("purchases") or 0)
        b["revenue"]     += float(r.get("purchase_value") or 0)

    rows = []
    total_spend = total_revenue = 0.0
    for ad_id, c in by_ad.items():
        perf = agg.get(ad_id, {"spend": 0.0, "clicks": 0, "impressions": 0,
                               "purchases": 0, "revenue": 0.0})
        total_spend   += perf["spend"]
        total_revenue += perf["revenue"]
        roas = (perf["revenue"] / perf["spend"]) if perf["spend"] > 0 else None
        cpa  = (perf["spend"] / perf["purchases"]) if perf["purchases"] > 0 else None
        ctr  = (perf["clicks"] / perf["impressions"] * 100) if perf["impressions"] > 0 else None
        rows.append({
            "ad_id":           ad_id,
            "ad_name":         c.get("ad_name"),
            "campaign_id":     c.get("campaign_id"),
            "image_url":       c.get("image_url") or c.get("thumbnail_url"),
            "headline":        c.get("headline"),
            "body":            c.get("body"),
            "call_to_action":  c.get("call_to_action"),
            "effective_status": c.get("effective_status"),
            "spend":           round(perf["spend"], 2),
            "clicks":          perf["clicks"],
            "impressions":     perf["impressions"],
            "purchases":       perf["purchases"],
            "revenue":         round(perf["revenue"], 2),
            "roas":            round(roas, 2) if roas is not None else None,
            "cpa":             round(cpa, 2)  if cpa  is not None else None,
            "ctr":             round(ctr, 2)  if ctr  is not None else None,
        })
    rows.sort(key=lambda r: r["spend"], reverse=True)

    return {
        "days":          days,
        "creatives":     rows,
        "total_spend":   round(total_spend, 2),
        "total_revenue": round(total_revenue, 2),
        "total_ads":     len(rows),
    }


@router.get("/creatives/{pixel_id}/latest-analysis", tags=["creatives"])
async def latest_analysis(pixel_id: str):
    """Return the most recent creative_analysis insight for the client, if any."""
    client_uuid, _ = _resolve(pixel_id)
    r = (
        get_supabase().table("ai_insights")
        .select("id, title, content, data, created_at")
        .eq("client_id", client_uuid)
        .eq("type", "creative_analysis")
        .order("created_at", desc=True)
        .limit(1)
        .execute()
    )
    if not (r and r.data):
        return {"analysis": None}
    return {"analysis": r.data[0]}


@router.post("/creatives/{pixel_id}/sync", tags=["creatives"])
async def sync_now(pixel_id: str):
    """Manual trigger for the creative_sync flow. Returns counts."""
    client_uuid, client = _resolve(pixel_id)
    if not (client.get("meta_ad_account_id") and client.get("meta_access_token")):
        raise HTTPException(400, "Client missing meta_ad_account_id or meta_access_token")
    return creative_sync.sync_for_client(
        client_uuid=client_uuid,
        account_id=client["meta_ad_account_id"],
        access_token=client["meta_access_token"],
    )


@router.post("/creatives/{pixel_id}/analyze", tags=["creatives"])
async def analyze_now(pixel_id: str):
    """
    Manual trigger for Claude Vision analysis. Rate-limited at one run per hour
    per client to keep Anthropic spend bounded.
    """
    client_uuid, _ = _resolve(pixel_id)
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    recent = (
        get_supabase().table("ai_insights")
        .select("id", count="exact", head=True)
        .eq("client_id", client_uuid)
        .eq("type", "creative_analysis")
        .gte("created_at", cutoff)
        .execute()
    )
    if (recent.count or 0) > 0:
        raise HTTPException(429, "Rate-limited: only one analysis per hour per client")

    result = creative_intelligence.analyze_client(client_uuid)
    if not result:
        raise HTTPException(400, "Not enough data — need at least 8 ads with measurable ROAS")
    return {"status": "ok", "title": result.get("title")}
