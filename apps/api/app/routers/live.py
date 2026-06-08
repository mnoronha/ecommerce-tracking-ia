"""
Real-time order feed for the live ticker page.

GET /live/{pixel_id}/orders?since=<iso>&limit=20
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException

from ..database import get_supabase

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get(
    "/live/{pixel_id}/orders",
    summary="Recent paid orders for the live ticker",
    tags=["live"],
)
async def live_orders(
    pixel_id: str,
    since: Optional[str] = None,
    limit: int = 20,
):
    """
    Returns the most recent paid orders. The frontend polls every 10s with
    `since=<last_seen_iso>` to receive only new orders. Without `since` we
    return the last `limit` orders so the ticker has initial state.
    """
    sb = get_supabase()
    client_row = (
        sb.table("clients")
        .select("id")
        .eq("pixel_id", pixel_id)
        .eq("is_active", True)
        .limit(1)
        .execute()
    )
    if not (client_row and client_row.data):
        raise HTTPException(status_code=404, detail="Client not found")

    client_uuid = client_row.data[0]["id"]

    cutoff = since or (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()

    q = (
        sb.table("orders")
        .select("id, platform_order_number, email, total_price, currency, "
                "utm_source, utm_medium, utm_campaign, platform_source, "
                "is_first_purchase, financial_status, created_at")
        .eq("client_id", client_uuid)
        .eq("financial_status", "paid")
        .gt("total_price", 0)
        .gte("created_at", cutoff)
        .order("created_at", desc=True)
        .limit(min(limit, 100))
    )
    rows = q.execute().data or []

    return {
        "now":    datetime.now(timezone.utc).isoformat(),
        "orders": rows,
        "count":  len(rows),
    }


@router.get(
    "/live/{pixel_id}/stats",
    summary="Live stats for the today-so-far header",
    tags=["live"],
)
async def live_stats(pixel_id: str):
    """
    Snapshot of today's revenue + orders + last-hour velocity.
    Refresh on the same poll interval as the ticker.
    """
    sb = get_supabase()
    client_row = (
        sb.table("clients")
        .select("id")
        .eq("pixel_id", pixel_id)
        .eq("is_active", True)
        .limit(1)
        .execute()
    )
    if not (client_row and client_row.data):
        raise HTTPException(status_code=404, detail="Client not found")

    client_uuid = client_row.data[0]["id"]
    now = datetime.now(timezone.utc)
    start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    last_hour = now - timedelta(hours=1)

    # Single aggregate query instead of fetching all rows and summing in Python.
    row = (
        sb.rpc("live_today_stats", {
            "p_client_id": client_uuid,
            "p_day_start": start_of_day.isoformat(),
            "p_last_hour": last_hour.isoformat(),
        }).execute()
    ).data
    stats = (row or [{}])[0]

    today_revenue    = float(stats.get("today_revenue")    or 0)
    today_orders     = int(stats.get("today_orders")       or 0)
    last_hour_rev    = float(stats.get("last_hour_revenue") or 0)
    last_hour_cnt    = int(stats.get("last_hour_orders")   or 0)

    return {
        "today_revenue":     round(today_revenue, 2),
        "today_orders":      today_orders,
        "today_avg_ticket":  round(today_revenue / today_orders, 2) if today_orders else 0,
        "last_hour_orders":  last_hour_cnt,
        "last_hour_revenue": round(last_hour_rev, 2),
        "now":               now.isoformat(),
    }
