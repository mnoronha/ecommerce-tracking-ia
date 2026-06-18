"""
TikTok Ads dashboard — server-side attribution + CAPI health.

Aggregates paid orders where utm_source ILIKE 'tiktok%' for revenue
attribution, plus CAPI send statistics (all paid online orders). No
ad-spend data yet (TikTok Ads API sync not implemented), so ROAS/CPA
are unavailable; they appear once spend sync is wired up.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Optional

from fastapi import APIRouter, HTTPException

from ..database import get_supabase

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/tiktok-ads", tags=["tiktok_ads"])


# ── Helpers ────────────────────────────────────────────────────────────────────

def _parse_period(days: int, start: Optional[str], end: Optional[str]):
    if start and end:
        d_start = date.fromisoformat(start)
        d_end   = date.fromisoformat(end)
    else:
        d_end   = date.today() - timedelta(days=1)
        d_start = d_end - timedelta(days=days - 1)
    span      = (d_end - d_start).days + 1
    prev_end   = d_start - timedelta(days=1)
    prev_start = prev_end - timedelta(days=span - 1)
    return d_start, d_end, prev_start, prev_end


def _safe_delta(curr: float, prev: float) -> Optional[float]:
    if prev and prev != 0:
        return round((curr - prev) / abs(prev) * 100, 1)
    return None


def _agg(orders: list[dict]) -> dict:
    n   = len(orders)
    rev = sum(float(o.get("total_price") or 0) for o in orders)
    return {
        "orders":     n,
        "revenue":    round(rev, 2),
        "avg_ticket": round(rev / n, 2) if n else 0.0,
    }


def _daily(orders: list[dict]) -> list[dict]:
    by: dict[str, dict] = {}
    for o in orders:
        d = str(o["created_at"])[:10]
        if d not in by:
            by[d] = {"date": d, "orders": 0, "revenue": 0.0}
        by[d]["orders"]  += 1
        by[d]["revenue"] += float(o.get("total_price") or 0)
    for v in by.values():
        v["revenue"] = round(v["revenue"], 2)
    return sorted(by.values(), key=lambda x: x["date"])


def _campaigns(orders: list[dict]) -> list[dict]:
    by: dict[str, dict] = {}
    for o in orders:
        c = o.get("utm_campaign") or "(sem campanha)"
        if c not in by:
            by[c] = {"campaign": c, "orders": 0, "revenue": 0.0}
        by[c]["orders"]  += 1
        by[c]["revenue"] += float(o.get("total_price") or 0)
    result = []
    for v in sorted(by.values(), key=lambda x: -x["revenue"]):
        v["revenue"]    = round(v["revenue"], 2)
        v["avg_ticket"] = round(v["revenue"] / v["orders"], 2) if v["orders"] else 0.0
        result.append(v)
    return result


# ── Endpoint ───────────────────────────────────────────────────────────────────

@router.get("/{pixel_id}/overview", summary="TikTok Ads — atribuição server-side + saúde CAPI")
async def tiktok_overview(
    pixel_id: str,
    days: int = 30,
    start: Optional[str] = None,
    end:   Optional[str] = None,
):
    sb = get_supabase()

    cli = (
        sb.table("clients")
        .select("id, tiktok_pixel_id")
        .eq("pixel_id", pixel_id)
        .limit(1)
        .execute()
    )
    if not (cli and cli.data):
        raise HTTPException(404, "Client not found")
    client_uuid  = cli.data[0]["id"]
    tiktok_pixel = cli.data[0].get("tiktok_pixel_id") or ""

    d_start, d_end, prev_start, prev_end = _parse_period(days, start, end)
    end_excl      = (d_end   + timedelta(days=1)).isoformat()
    prev_end_excl = (prev_end + timedelta(days=1)).isoformat()

    def _q(s: date, e_excl: str) -> list[dict]:
        return (
            sb.table("orders")
            .select("id, created_at, total_price, utm_campaign")
            .eq("client_id", client_uuid)
            .eq("financial_status", "paid")
            .gt("total_price", 0)
            .ilike("utm_source", "tiktok%")
            .gte("created_at", s.isoformat())
            .lt("created_at",  e_excl)
            .order("created_at")
            .limit(2000)
            .execute()
        ).data or []

    curr  = _q(d_start,    end_excl)
    prev  = _q(prev_start, prev_end_excl)

    totals      = _agg(curr)
    prev_totals = _agg(prev)
    deltas = {
        "orders":     _safe_delta(totals["orders"],     prev_totals["orders"]),
        "revenue":    _safe_delta(totals["revenue"],    prev_totals["revenue"]),
        "avg_ticket": _safe_delta(totals["avg_ticket"], prev_totals["avg_ticket"]),
    }

    # ── CAPI health — all paid online orders in period ────────────────────────
    capi_rows = (
        sb.table("orders")
        .select("tiktok_sent, tiktok_last_error")
        .eq("client_id", client_uuid)
        .eq("financial_status", "paid")
        .gt("total_price", 0)
        .or_("utm_source.is.null,utm_source.not.in.(pos,draft)")
        .gte("created_at", d_start.isoformat())
        .lt("created_at",  end_excl)
        .execute()
    ).data or []

    capi_sent   = sum(1 for r in capi_rows if r.get("tiktok_sent") is True)
    capi_failed = sum(
        1 for r in capi_rows
        if r.get("tiktok_sent") is not True
        and r.get("tiktok_last_error")
        and not str(r["tiktok_last_error"]).startswith("skipped:")
    )
    capi_total = len(capi_rows)

    # ── Funnel — all tracking events in period ────────────────────────────────
    funnel_rows = (
        sb.table("tracking_events")
        .select("event_type")
        .eq("client_id", client_uuid)
        .gte("created_at", d_start.isoformat())
        .lt("created_at",  end_excl)
        .in_("event_type", ["pageview", "view_product", "add_to_cart", "begin_checkout", "purchase"])
        .limit(50000)
        .execute()
    ).data or []

    funnel: dict[str, int] = {}
    for r in funnel_rows:
        et = r.get("event_type") or ""
        funnel[et] = funnel.get(et, 0) + 1

    return {
        "has_data":    len(curr) > 0,
        "start":       d_start.isoformat(),
        "end":         d_end.isoformat(),
        "prev_start":  prev_start.isoformat(),
        "prev_end":    prev_end.isoformat(),
        "pixel_id":    tiktok_pixel,
        "totals":      totals,
        "prev_totals": prev_totals,
        "deltas":      deltas,
        "daily":       _daily(curr),
        "campaigns":   _campaigns(curr),
        "capi": {
            "total":    capi_total,
            "sent":     capi_sent,
            "failed":   capi_failed,
            "sent_pct": round(capi_sent / capi_total * 100, 1) if capi_total else 0.0,
        },
        "funnel": funnel,
    }
