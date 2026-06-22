"""
Performance endpoints.

  GET /api/v1/clients/{id}/performance/daily
  GET /api/v1/clients/{id}/performance/campaigns
"""

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request

from ....database import get_supabase
from ..deps import ApiKey, get_request_id, log_request
from ..pagination import paginated_response, single_response

logger = logging.getLogger(__name__)
router = APIRouter(tags=["performance"])

# ── Channel helpers ───────────────────────────────────────────────────────────

_META_SOURCES  = {"facebook", "instagram", "meta", "fb", "ig"}
_META_MEDIUMS  = {"paid_social"}
_GOOGLE_SOURCES = {"google"}
_GOOGLE_MEDIUMS = {"cpc", "paid_search", "ppc"}


def _channel(utm_source: Optional[str], utm_medium: Optional[str]) -> str:
    s = (utm_source or "").lower().strip()
    m = (utm_medium or "").lower().strip()
    if s in _META_SOURCES or m in _META_MEDIUMS:
        return "meta"
    if s in _GOOGLE_SOURCES:
        return "google"
    if m in _GOOGLE_MEDIUMS and s not in _META_SOURCES:
        return "google"
    if s in ("klaviyo", "email") or m == "email":
        return "email"
    if s in ("pos",) or m in ("in_store",):
        return "pos"
    return "other"


def _agg_orders(orders: list) -> dict:
    revenue = sum(float(o.get("total_price") or 0) for o in orders)
    n       = len(orders)
    spend   = 0.0  # filled separately
    return {
        "revenue":    str(round(revenue, 2)),
        "orders":     n,
        "spend":      "0.00",
        "roas":       None,
        "cpa":        None,
        "avg_ticket": str(round(revenue / n, 2)) if n > 0 else None,
    }


def _by_channel(orders: list, spend_by_channel: dict) -> dict:
    channels: dict[str, dict] = {}
    for o in orders:
        ch = _channel(o.get("utm_source"), o.get("utm_medium"))
        if ch not in channels:
            channels[ch] = {"revenue": 0.0, "orders": 0, "impressions": None, "clicks": None}
        channels[ch]["revenue"] += float(o.get("total_price") or 0)
        channels[ch]["orders"]  += 1

    for ch, spend_row in spend_by_channel.items():
        key = "meta" if "meta" in ch else "google"
        if key not in channels:
            channels[key] = {"revenue": 0.0, "orders": 0}
        channels[key]["spend"]       = str(round(float(spend_row.get("spend") or 0), 2))
        channels[key]["impressions"] = int(spend_row.get("impressions") or 0)
        channels[key]["clicks"]      = int(spend_row.get("clicks") or 0)

    result = {}
    for ch, v in channels.items():
        rev   = v["revenue"]
        spend = float(v.get("spend") or 0)
        n     = v["orders"]
        ctr   = None
        if v.get("impressions") and v.get("clicks"):
            ctr = str(round(v["clicks"] / v["impressions"], 4))
        result[ch] = {
            "revenue":     str(round(rev, 2)),
            "orders":      n,
            "spend":       str(round(spend, 2)) if spend else None,
            "roas":        str(round(rev / spend, 2)) if spend > 0 else None,
            "cpa":         str(round(spend / n, 2)) if (spend > 0 and n > 0) else None,
            "impressions": v.get("impressions"),
            "clicks":      v.get("clicks"),
            "ctr":         ctr,
        }
    return result


def _delta(curr_str: Optional[str], prev_str: Optional[str]) -> Optional[dict]:
    if curr_str is None or prev_str is None:
        return None
    try:
        curr = float(curr_str)
        prev = float(prev_str)
    except (ValueError, TypeError):
        return None
    absolute = round(curr - prev, 2)
    pct      = round((curr - prev) / prev, 4) if prev != 0 else None
    return {"absolute": str(absolute), "percentage": str(pct) if pct is not None else None}


# ── GET /api/v1/clients/{id}/performance/daily ───────────────────────────────

@router.get("/clients/{client_id}/performance/daily")
async def performance_daily(
    request: Request,
    client_id: str,
    key: ApiKey,
    date_param: Optional[str] = Query(None, alias="date"),
    period: Optional[str] = Query(None),
    channels: str = Query("all"),
):
    req_id = get_request_id(request)
    t0     = datetime.now(timezone.utc)
    key.assert_client_scope(client_id)

    sb = get_supabase()
    # Validate client
    client_row = (
        sb.table("clients")
        .select("id, name, monthly_revenue_goal, monthly_ad_spend_goal, target_roas, cpa_target, meta_ads_budget, google_ads_budget")
        .eq("id", client_id)
        .eq("is_active", True)
        .limit(1)
        .execute()
    ).data or []
    if not client_row:
        raise HTTPException(404, "Client not found")
    client = client_row[0]

    # Resolve dates
    today     = datetime.now(timezone.utc).date()
    yesterday = today - timedelta(days=1)

    if date_param:
        try:
            main_date = date.fromisoformat(date_param)
        except ValueError:
            raise HTTPException(400, "Invalid date format — use YYYY-MM-DD")
    elif period in ("today",):
        main_date = today
    else:
        main_date = yesterday

    baseline_end   = main_date - timedelta(days=1)
    baseline_start = baseline_end - timedelta(days=6)  # 7-day average

    # Fetch orders helper
    def fetch_paid_orders(d_from: date, d_to: date) -> list:
        return (
            sb.table("orders")
            .select("id, total_price, utm_source, utm_medium, utm_campaign, "
                    "capi_sent, google_sent, financial_status, platform_source")
            .eq("client_id", client_id)
            .eq("financial_status", "paid")
            .gt("total_price", 0)
            .gte("created_at", f"{d_from}T00:00:00+00:00")
            .lte("created_at", f"{d_to}T23:59:59+00:00")
            .neq("platform_source", "pos")          # exclude POS
            .limit(5000)
            .execute()
        ).data or []

    # Fetch spend
    def fetch_spend(d_from: date, d_to: date) -> dict:
        rows = (
            sb.table("ad_spend")
            .select("channel, date, spend, impressions, clicks")
            .eq("client_id", client_id)
            .gte("date", str(d_from))
            .lte("date", str(d_to))
            .execute()
        ).data or []
        # Aggregate by channel
        by_ch: dict = {}
        for r in rows:
            ch = r["channel"]
            if ch not in by_ch:
                by_ch[ch] = {"spend": 0.0, "impressions": 0, "clicks": 0}
            by_ch[ch]["spend"]       += float(r.get("spend") or 0)
            by_ch[ch]["impressions"] += int(r.get("impressions") or 0)
            by_ch[ch]["clicks"]      += int(r.get("clicks") or 0)
        return by_ch

    # Pull data
    main_orders    = fetch_paid_orders(main_date, main_date)
    base_orders    = fetch_paid_orders(baseline_start, baseline_end)
    main_spend_map = fetch_spend(main_date, main_date)
    base_spend_map = fetch_spend(baseline_start, baseline_end)

    # Total spend
    main_spend = round(sum(v["spend"] for v in main_spend_map.values()), 2)
    base_spend = round(sum(v["spend"] for v in base_spend_map.values()), 2)

    # Main period aggregates
    main_agg    = _agg_orders(main_orders)
    main_rev    = float(main_agg["revenue"])
    main_orders_n = main_agg["orders"]
    main_agg["spend"] = str(main_spend)
    main_agg["roas"]  = str(round(main_rev / main_spend, 2)) if main_spend > 0 else None
    main_agg["cpa"]   = str(round(main_spend / main_orders_n, 2)) if (main_spend > 0 and main_orders_n > 0) else None
    main_agg["by_channel"] = _by_channel(main_orders, main_spend_map)

    # Baseline (7-day daily average)
    base_agg    = _agg_orders(base_orders)
    base_rev    = float(base_agg["revenue"])
    base_n      = base_agg["orders"]
    base_agg["spend"] = str(round(base_spend / 7, 2))
    base_roas = round(base_rev / base_spend * 7, 2) if base_spend > 0 else None  # per day
    base_agg["roas"] = str(base_roas) if base_roas else None
    base_cpa  = round(base_spend / 7 / max(base_n / 7, 0.001), 2) if base_spend > 0 else None
    base_agg["cpa"]  = str(base_cpa) if base_cpa else None
    # Average over 7 days
    for k in ("revenue", "orders"):
        try:
            val = float(base_agg[k]) / 7
            base_agg[k] = str(round(val, 2)) if isinstance(base_agg[k], str) else round(val)
        except (ValueError, TypeError):
            pass

    # Deltas
    deltas = {
        "revenue":    _delta(main_agg["revenue"], base_agg["revenue"]),
        "spend":      _delta(main_agg["spend"],   base_agg["spend"]),
        "roas":       _delta(main_agg["roas"],     base_agg["roas"]),
        "cpa":        _delta(main_agg["cpa"],      base_agg["cpa"]),
    }

    # Month progress
    month_str = main_date.strftime("%Y-%m")
    month_start = date(main_date.year, main_date.month, 1)
    month_orders = fetch_paid_orders(month_start, main_date)
    month_revenue = sum(float(o.get("total_price") or 0) for o in month_orders)

    import calendar
    days_in_month = calendar.monthrange(main_date.year, main_date.month)[1]
    days_elapsed  = main_date.day
    days_remaining = days_in_month - days_elapsed
    revenue_target = float(client.get("monthly_revenue_goal") or 0)
    projected = round(month_revenue / days_elapsed * days_in_month, 2) if days_elapsed > 0 else 0

    if revenue_target > 0:
        completion_pct = round(month_revenue / revenue_target, 4)
        pace_ratio     = completion_pct / (days_elapsed / days_in_month) if days_elapsed > 0 else 0
        if pace_ratio >= 0.95:
            pacing_status = "on_track"
        elif pace_ratio >= 0.80:
            pacing_status = "slightly_behind"
        else:
            pacing_status = "behind"
    else:
        completion_pct = None
        pacing_status  = "no_target"

    month_progress = {
        "month":               month_str,
        "revenue_target":      str(revenue_target) if revenue_target else None,
        "revenue_actual":      str(round(month_revenue, 2)),
        "completion_pct":      str(completion_pct) if completion_pct is not None else None,
        "days_elapsed":        days_elapsed,
        "days_remaining":      days_remaining,
        "projected_close":     str(projected),
        "pacing_status":       pacing_status,
    }

    # Tracking health
    total_main = len(main_orders)
    capi_sent  = sum(1 for o in main_orders if o.get("capi_sent"))
    google_sent = sum(1 for o in main_orders if o.get("google_sent"))

    # Last pixel event
    try:
        last_event = (
            sb.table("tracking_events")
            .select("created_at")
            .eq("client_id", client_id)
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        ).data or []
        pixel_last = last_event[0]["created_at"] if last_event else None
        pixel_status = "healthy" if pixel_last else "unknown"
    except Exception:
        pixel_last   = None
        pixel_status = "unknown"

    tracking_health = {
        "pixel_status":       pixel_status,
        "pixel_last_event_at": pixel_last,
        "capi_meta": {
            "events_sent_24h":     capi_sent,
            "total_orders_24h":    total_main,
            "match_rate":          str(round(capi_sent / total_main, 4)) if total_main > 0 else None,
            "status":              "healthy" if capi_sent == total_main else ("warning" if capi_sent > 0 else "unknown"),
        },
        "capi_google": {
            "events_sent_24h":  google_sent,
            "total_orders_24h": total_main,
            "match_rate":       str(round(google_sent / total_main, 4)) if total_main > 0 else None,
            "status":           "healthy" if google_sent >= total_main * 0.8 else "warning",
        },
    }

    result = {
        "client_id":       client_id,
        "client_name":     client["name"],
        "period": {
            "type":           "daily",
            "main_date":      str(main_date),
            "baseline_start": str(baseline_start),
            "baseline_end":   str(baseline_end),
        },
        "main_period":     main_agg,
        "baseline":        base_agg,
        "deltas":          deltas,
        "month_progress":  month_progress,
        "tracking_health": tracking_health,
        "generated_at":    datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }

    ms = int((datetime.now(timezone.utc) - t0).total_seconds() * 1000)
    log_request(request, 200, ms, client_id=client_id)
    return single_response(result, req_id)


# ── GET /api/v1/clients/{id}/performance/campaigns ───────────────────────────

@router.get("/clients/{client_id}/performance/campaigns")
async def performance_campaigns(
    request: Request,
    client_id: str,
    key: ApiKey,
    period: str = Query("last_7d", enum=["today", "yesterday", "last_7d", "last_30d", "custom"]),
    start_date: Optional[str] = Query(None),
    end_date:   Optional[str] = Query(None),
    platform:   str  = Query("all"),
    sort:       str  = Query("revenue", enum=["spend", "roas", "cpa", "conversions", "revenue"]),
    order:      str  = Query("desc", enum=["asc", "desc"]),
    cursor:     Optional[str] = Query(None),
    limit:      int  = Query(50, ge=1, le=100),
):
    req_id = get_request_id(request)
    t0     = datetime.now(timezone.utc)
    key.assert_client_scope(client_id)

    sb = get_supabase()
    today     = datetime.now(timezone.utc).date()
    yesterday = today - timedelta(days=1)

    if period == "today":
        d_from, d_to = today, today
    elif period == "yesterday":
        d_from, d_to = yesterday, yesterday
    elif period == "last_7d":
        d_from, d_to = today - timedelta(days=7), yesterday
    elif period == "last_30d":
        d_from, d_to = today - timedelta(days=30), yesterday
    else:
        try:
            d_from = date.fromisoformat(start_date or str(yesterday))
            d_to   = date.fromisoformat(end_date   or str(yesterday))
        except ValueError:
            raise HTTPException(400, "Invalid date format")

    orders = (
        sb.table("orders")
        .select("id, total_price, utm_source, utm_medium, utm_campaign, "
                "capi_sent, google_sent, financial_status")
        .eq("client_id", client_id)
        .eq("financial_status", "paid")
        .gt("total_price", 0)
        .gte("created_at", f"{d_from}T00:00:00+00:00")
        .lte("created_at", f"{d_to}T23:59:59+00:00")
        .limit(5000)
        .execute()
    ).data or []

    # Group by campaign + platform
    camp_map: dict[tuple, dict] = {}
    for o in orders:
        ch  = _channel(o.get("utm_source"), o.get("utm_medium"))
        cam = o.get("utm_campaign") or "(sem campanha)"
        key_ = (cam, ch)
        if key_ not in camp_map:
            camp_map[key_] = {
                "campaign_name": cam,
                "platform": ch,
                "orders": 0,
                "revenue": 0.0,
                "spend": None,
                "roas": None,
                "cpa": None,
            }
        camp_map[key_]["orders"]  += 1
        camp_map[key_]["revenue"] += float(o.get("total_price") or 0)

    campaigns = []
    for e in camp_map.values():
        rev = round(e["revenue"], 2)
        n   = e["orders"]
        campaigns.append({
            **e,
            "revenue": str(rev),
            "cpa":     str(round(rev / n, 2)) if n > 0 else None,
        })

    # Sort
    reverse = order == "desc"
    def sort_key(x):
        v = x.get(sort)
        try: return float(v) if v else 0
        except (TypeError, ValueError): return 0

    campaigns.sort(key=sort_key, reverse=reverse)

    if platform != "all":
        campaigns = [c for c in campaigns if c.get("platform") == platform]

    total = len(campaigns)
    page  = campaigns[:limit]

    ms = int((datetime.now(timezone.utc) - t0).total_seconds() * 1000)
    log_request(request, 200, ms, client_id=client_id)
    return paginated_response(page, total, limit, request_id=req_id)
