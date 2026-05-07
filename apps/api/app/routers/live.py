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

    today = (
        sb.table("orders")
        .select("total_price, created_at")
        .eq("client_id", client_uuid)
        .eq("financial_status", "paid")
        .gt("total_price", 0)
        .gte("created_at", start_of_day.isoformat())
        .execute()
    ).data or []

    today_revenue = sum(float(o.get("total_price") or 0) for o in today)
    last_hour_orders = sum(1 for o in today if o.get("created_at") >= last_hour.isoformat())
    last_hour_revenue = sum(
        float(o.get("total_price") or 0) for o in today
        if o.get("created_at") >= last_hour.isoformat()
    )

    return {
        "today_revenue":     round(today_revenue, 2),
        "today_orders":      len(today),
        "today_avg_ticket":  round(today_revenue / len(today), 2) if today else 0,
        "last_hour_orders":  last_hour_orders,
        "last_hour_revenue": round(last_hour_revenue, 2),
        "now":               now.isoformat(),
    }
