"""
Google Ads Dashboard endpoint.

GET /google-ads/{pixel_id}/overview?days=30

Usa dados server-side (pedidos + match_type do Google) — sem chamada
live à Google Ads API. Foca em:
  - Receita atribuída a tráfego Google (utm_source=google/cpc)
  - Cobertura de conversão: gclid vs enhanced vs sem match
  - Breakdown por campanha UTM
  - Série diária
  - Funil de eventos
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException

from ..database import get_supabase

logger = logging.getLogger(__name__)
router = APIRouter()

_GOOGLE_SOURCES = {"google", "cpc", "paid_search", "ppc", "adwords"}
_GOOGLE_MEDIUMS = {"cpc", "paid_search", "ppc", "google"}


def _is_google(source: Optional[str], medium: Optional[str]) -> bool:
    s = (source or "").lower()
    m = (medium or "").lower()
    return "google" in s or s in _GOOGLE_SOURCES or m in _GOOGLE_MEDIUMS


def _delta(curr: float | None, prev: float | None) -> float | None:
    if curr is None or prev is None or prev == 0:
        return None
    return round((curr - prev) / prev * 100, 1)


@router.get(
    "/google-ads/{pixel_id}/overview",
    summary="Dashboard Google Ads — dados server-side (pedidos + match_type)",
    tags=["google_ads"],
)
async def google_overview(
    pixel_id: str,
    days: int = 30,
    start: str | None = None,
    end:   str | None = None,
):
    """
    Retorna:
    - KPIs: compras, receita, cobertura gclid/enhanced, CPA estimado
    - % Δ vs período anterior
    - Série diária
    - Breakdown por campanha UTM
    - Match type (gclid / gbraid / enhanced_only)
    - Funil de eventos
    - Produtos por campanha (via order_items)
    """
    sb = get_supabase()

    creds = (
        sb.table("clients")
        .select("id, google_ads_customer_id, google_ads_conversion_action_id")
        .eq("pixel_id", pixel_id)
        .eq("is_active", True)
        .limit(1)
        .execute()
    )
    if not (creds and creds.data):
        raise HTTPException(status_code=404, detail="Client not found or inactive")
    c         = creds.data[0]
    client_id = c["id"]
    has_creds = bool(c.get("google_ads_customer_id"))

    if start and end:
        d_start = datetime.fromisoformat(start).date()
        d_end   = datetime.fromisoformat(end).date()
        days    = max(1, (d_end - d_start).days + 1)
    else:
        today   = datetime.now(timezone.utc).date()
        d_end   = today
        d_start = today - timedelta(days=days - 1)
    d_prev_end   = d_start - timedelta(days=1)
    d_prev_start = d_prev_end - timedelta(days=days - 1)

    def _fetch_orders(d_from, d_to):
        return (
            sb.table("orders")
            .select(
                "id, total_price, created_at, utm_source, utm_medium, utm_campaign, "
                "google_sent, google_match_type, google_last_error, platform_source"
            )
            .eq("client_id", client_id)
            .eq("financial_status", "paid")
            .gt("total_price", 0)
            .gte("created_at", f"{d_from}T00:00:00+00:00")
            .lte("created_at", f"{d_to}T23:59:59+00:00")
            .limit(5000)
            .execute()
        ).data or []

    curr_orders = _fetch_orders(d_start, d_end)
    prev_orders = _fetch_orders(d_prev_start, d_prev_end)

    # Partition: Google-attributed vs all conversions sent to Google
    def _partition(orders):
        google_attr = [o for o in orders if _is_google(o.get("utm_source"), o.get("utm_medium"))]
        all_sent    = [o for o in orders if o.get("google_sent")]
        return google_attr, all_sent

    curr_google, curr_sent = _partition(curr_orders)
    prev_google, prev_sent = _partition(prev_orders)

    def _agg(orders):
        revenue = sum(float(o.get("total_price") or 0) for o in orders)
        n       = len(orders)
        return {"orders": n, "revenue": round(revenue, 2)}

    def _match_breakdown(orders):
        sent = [o for o in orders if o.get("google_sent")]
        return {
            "gclid":          sum(1 for o in sent if o.get("google_match_type") == "gclid"),
            "gbraid":         sum(1 for o in sent if o.get("google_match_type") == "gbraid"),
            "enhanced_only":  sum(1 for o in sent if o.get("google_match_type") == "enhanced_only"),
            "not_sent":       sum(1 for o in orders if not o.get("google_sent")
                                  and o.get("platform_source") != "pos"),
            "total_sent":     len(sent),
        }

    curr_agg   = _agg(curr_google)
    prev_agg   = _agg(prev_google)
    curr_match = _match_breakdown(curr_orders)
    prev_match = _match_breakdown(prev_orders)

    sent_coverage_curr = (
        round(curr_match["total_sent"] / len(curr_orders) * 100, 1)
        if curr_orders else None
    )
    gclid_pct_curr = (
        round(curr_match["gclid"] / curr_match["total_sent"] * 100, 1)
        if curr_match["total_sent"] > 0 else None
    )

    # ── Totals with deltas ─────────────────────────────────────────────────
    totals = {
        **curr_agg,
        "total_sent":       curr_match["total_sent"],
        "sent_coverage_pct": sent_coverage_curr,
        "gclid_pct":        gclid_pct_curr,
        "gclid":            curr_match["gclid"],
        "gbraid":           curr_match["gbraid"],
        "enhanced_only":    curr_match["enhanced_only"],
        "not_sent":         curr_match["not_sent"],
        "cpa": round(curr_agg["revenue"] / curr_agg["orders"], 2) if curr_agg["orders"] > 0 else None,
        "avg_ticket": round(curr_agg["revenue"] / curr_agg["orders"], 2) if curr_agg["orders"] > 0 else None,
    }
    prev_totals = {
        **prev_agg,
        "gclid":  prev_match["gclid"],
        "total_sent": prev_match["total_sent"],
    }
    deltas = {
        "orders":  _delta(curr_agg["orders"],  prev_agg["orders"]),
        "revenue": _delta(curr_agg["revenue"], prev_agg["revenue"]),
        "gclid":   _delta(curr_match["gclid"], prev_match["gclid"]),
        "total_sent": _delta(curr_match["total_sent"], prev_match["total_sent"]),
    }

    # ── Daily time series ──────────────────────────────────────────────────
    daily_map: dict = {}
    for o in curr_orders:
        if not _is_google(o.get("utm_source"), o.get("utm_medium")):
            continue
        d = o["created_at"][:10]
        if d not in daily_map:
            daily_map[d] = {"date": d, "orders": 0, "revenue": 0.0, "gclid": 0, "enhanced": 0}
        daily_map[d]["orders"]   += 1
        daily_map[d]["revenue"]  += float(o.get("total_price") or 0)
        mt = o.get("google_match_type") or ""
        if mt == "gclid":    daily_map[d]["gclid"]    += 1
        elif "enhanced" in mt: daily_map[d]["enhanced"] += 1

    daily = []
    for i in range(days):
        d = str(d_start + timedelta(days=i))
        row = daily_map.get(d, {"date": d, "orders": 0, "revenue": 0.0, "gclid": 0, "enhanced": 0})
        row["revenue"] = round(row["revenue"], 2)
        daily.append(row)

    # ── Campaign breakdown ─────────────────────────────────────────────────
    camp_map: dict = {}
    prev_camp: dict = {}
    for o in prev_google:
        cam = o.get("utm_campaign") or "(sem campanha)"
        e   = prev_camp.setdefault(cam, {"orders": 0, "revenue": 0.0})
        e["orders"]  += 1
        e["revenue"] += float(o.get("total_price") or 0)

    for o in curr_google:
        cam = o.get("utm_campaign") or "(sem campanha)"
        e   = camp_map.setdefault(cam, {
            "campaign": cam,
            "orders": 0, "revenue": 0.0,
            "gclid": 0, "enhanced": 0,
        })
        e["orders"]  += 1
        e["revenue"] += float(o.get("total_price") or 0)
        mt = o.get("google_match_type") or ""
        if mt == "gclid":    e["gclid"]    += 1
        elif "enhanced" in mt: e["enhanced"] += 1

    # Fetch order_items for product breakdown
    curr_google_ids = [o["id"] for o in curr_google]
    items_by_order: dict = {}
    if curr_google_ids:
        for i in range(0, len(curr_google_ids), 500):
            chunk = curr_google_ids[i:i+500]
            rows = (
                sb.table("order_items")
                .select("order_id, name, sku, quantity, line_total")
                .in_("order_id", chunk)
                .execute()
            ).data or []
            for it in rows:
                items_by_order.setdefault(it["order_id"], []).append(it)

    prod_by_camp: dict = {}
    for o in curr_google:
        cam   = o.get("utm_campaign") or "(sem campanha)"
        items = items_by_order.get(o["id"], [])
        prod_map = prod_by_camp.setdefault(cam, {})
        for it in items:
            name = it.get("name") or "—"
            p    = prod_map.setdefault(name, {"name": name, "sku": it.get("sku"), "units": 0, "revenue": 0.0})
            p["units"]   += int(it.get("quantity") or 1)
            p["revenue"] += float(it.get("line_total") or 0)

    campaigns_out = []
    for cam, e in camp_map.items():
        prev_c  = prev_camp.get(cam, {"orders": 0, "revenue": 0.0})
        rev     = round(e["revenue"], 2)
        prods   = sorted(prod_by_camp.get(cam, {}).values(), key=lambda p: p["revenue"], reverse=True)[:5]
        campaigns_out.append({
            "campaign":      cam,
            "orders":        e["orders"],
            "revenue":       rev,
            "gclid":         e["gclid"],
            "enhanced":      e["enhanced"],
            "cpa":           round(rev / e["orders"], 2) if e["orders"] > 0 else None,
            "revenue_delta": _delta(e["revenue"], prev_c["revenue"]),
            "top_products":  [{"name": p["name"], "sku": p["sku"], "units": p["units"], "revenue": round(p["revenue"], 2)} for p in prods],
        })
    campaigns_out.sort(key=lambda x: x["revenue"], reverse=True)

    # ── Funnel ─────────────────────────────────────────────────────────────
    funnel_start = f"{d_start}T00:00:00+00:00"
    funnel_end   = f"{d_end}T23:59:59+00:00"
    funnel = {}
    for et in ("pageview", "add_to_cart", "begin_checkout"):
        funnel[et] = (
            sb.table("tracking_events")
            .select("id", count="exact", head=True)
            .eq("client_id", client_id)
            .eq("event_type", et)
            .gte("created_at", funnel_start)
            .lte("created_at", funnel_end)
            .execute()
        ).count or 0
    funnel["purchases"] = curr_agg["orders"]

    return {
        "days":        days,
        "start":       str(d_start),
        "end":         str(d_end),
        "prev_start":  str(d_prev_start),
        "prev_end":    str(d_prev_end),
        "has_creds":   has_creds,
        "customer_id": c.get("google_ads_customer_id"),
        "totals":      totals,
        "prev_totals": prev_totals,
        "deltas":      deltas,
        "campaigns":   campaigns_out,
        "daily":       daily,
        "funnel":      funnel,
    }
