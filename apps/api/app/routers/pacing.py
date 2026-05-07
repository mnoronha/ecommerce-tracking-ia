"""
Pacing — month-to-date progress vs revenue/spend goals.

DTC merchants check this multiple times a day. The widget at the top of the
dashboard answers: am I on track to hit my monthly goal?

Projection uses linear extrapolation from days elapsed / total business days.
"""

import calendar
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException

from ..database import get_supabase

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get(
    "/pacing/{pixel_id}",
    summary="Month-to-date pacing vs goals",
    tags=["pacing"],
)
async def get_pacing(pixel_id: str):
    sb = get_supabase()
    client = (
        sb.table("clients")
        .select("id, monthly_revenue_goal, monthly_ad_spend_goal, target_roas")
        .eq("pixel_id", pixel_id)
        .eq("is_active", True)
        .limit(1)
        .execute()
    )
    if not (client and client.data):
        raise HTTPException(status_code=404, detail="Client not found")
    c = client.data[0]

    now = datetime.now(timezone.utc)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    days_in_month = calendar.monthrange(now.year, now.month)[1]
    day_of_month  = now.day
    fraction_done = day_of_month / days_in_month

    # Today vs MTD revenue
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    mtd_orders = (
        sb.table("orders")
        .select("total_price, gross_profit, created_at")
        .eq("client_id", c["id"])
        .eq("financial_status", "paid")
        .gt("total_price", 0)
        .gte("created_at", month_start.isoformat())
        .execute()
    ).data or []

    mtd_revenue = sum(float(o.get("total_price") or 0) for o in mtd_orders)
    mtd_profit  = sum(float(o.get("gross_profit") or 0) for o in mtd_orders if o.get("gross_profit"))
    mtd_orders_count = len(mtd_orders)

    today_orders = [o for o in mtd_orders if o.get("created_at") >= today_start.isoformat()]
    today_revenue = sum(float(o.get("total_price") or 0) for o in today_orders)

    # Linear projection assuming current run-rate continues
    projected_revenue = round(mtd_revenue / fraction_done, 2) if fraction_done > 0 else 0

    goal       = float(c.get("monthly_revenue_goal") or 0)
    pct_done   = round(mtd_revenue / goal * 100, 1) if goal > 0 else None
    pct_target = round(fraction_done * 100, 1)
    on_track   = (pct_done is not None and pct_done >= pct_target * 0.9) if goal > 0 else None

    needed_per_day_remaining = None
    if goal > 0 and day_of_month < days_in_month:
        remaining = max(goal - mtd_revenue, 0)
        days_left = days_in_month - day_of_month
        needed_per_day_remaining = round(remaining / days_left, 2)

    return {
        "now":                       now.isoformat(),
        "month_start":               month_start.isoformat(),
        "day_of_month":              day_of_month,
        "days_in_month":             days_in_month,
        "fraction_done":             round(fraction_done, 4),
        # Revenue side
        "mtd_revenue":               round(mtd_revenue, 2),
        "mtd_orders":                mtd_orders_count,
        "mtd_profit":                round(mtd_profit, 2) if mtd_profit else None,
        "today_revenue":             round(today_revenue, 2),
        "today_orders":              len(today_orders),
        "projected_revenue":         projected_revenue,
        "monthly_revenue_goal":      goal if goal > 0 else None,
        "pct_done":                  pct_done,
        "pct_target":                pct_target,
        "on_track":                  on_track,
        "needed_per_day_remaining":  needed_per_day_remaining,
        # Spend side (filled in if has Meta Ads creds; left null otherwise)
        "monthly_ad_spend_goal":     float(c.get("monthly_ad_spend_goal") or 0) or None,
        "target_roas":               float(c.get("target_roas") or 0) or None,
    }
