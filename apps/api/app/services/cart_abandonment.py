"""
Cart abandonment marker.

Runs hourly. Two passes:
  1. Mark 'add'/'begin_checkout' cart_events older than 60min as abandoned
     (is_abandoned=true, abandoned_at=created_at+60min), unless we already
     have a purchase from the same visitor in the meantime.
  2. Mark abandoned carts as recovered when a purchase from the same visitor
     happened after the cart event but within 24h — sets recovered_at and
     recovery_order_id so we can attribute recovered revenue.

Cheap: bounded to last 48h. Idempotent.
"""

import logging
from datetime import datetime, timedelta, timezone

from ..database import get_supabase

logger = logging.getLogger(__name__)

_ABANDONMENT_WINDOW_MIN = 60      # minutes
_RECOVERY_WINDOW_HOURS  = 24      # hours
_LOOKBACK_HOURS         = 48      # hours


def _mark_abandoned(sb) -> int:
    """
    Find cart events older than 60min that aren't yet flagged as abandoned and
    don't have a same-visitor purchase right after. Flag them.
    """
    now = datetime.now(timezone.utc)
    abandon_cutoff = (now - timedelta(minutes=_ABANDONMENT_WINDOW_MIN)).isoformat()
    lookback       = (now - timedelta(hours=_LOOKBACK_HOURS)).isoformat()

    pending = (
        sb.table("cart_events")
        .select("id, client_id, visitor_id, created_at")
        .in_("action", ["add", "begin_checkout"])
        .eq("is_abandoned", False)
        .lt("created_at", abandon_cutoff)
        .gte("created_at", lookback)
        .limit(500)
        .execute()
    )
    rows = pending.data or []
    if not rows:
        return 0

    # Bulk lookup of purchases by visitor — cheap when we have <500 rows.
    visitor_ids = list({r["visitor_id"] for r in rows if r.get("visitor_id")})
    purchases_by_visitor: dict[str, str] = {}
    if visitor_ids:
        purchases = (
            sb.table("orders")
            .select("visitor_id, created_at")
            .in_("visitor_id", visitor_ids)
            .gte("created_at", lookback)
            .execute()
        )
        for o in (purchases.data or []):
            v = o.get("visitor_id")
            if v and v not in purchases_by_visitor:
                purchases_by_visitor[v] = o["created_at"]

    marked = 0
    for r in rows:
        vis = r.get("visitor_id")
        # If the visitor already converted after the cart event, skip — it'll
        # be picked up as 'recovered' on the second pass.
        if vis and purchases_by_visitor.get(vis, "") > r["created_at"]:
            continue
        try:
            abandoned_at = (
                datetime.fromisoformat(r["created_at"].replace("Z", "+00:00"))
                + timedelta(minutes=_ABANDONMENT_WINDOW_MIN)
            ).isoformat()
            sb.table("cart_events").update({
                "is_abandoned": True,
                "abandoned_at": abandoned_at,
            }).eq("id", r["id"]).execute()
            marked += 1
        except Exception as exc:
            logger.debug("mark_abandoned row %s: %s", r["id"], exc)

    return marked


def _mark_recovered(sb) -> int:
    """
    Find abandoned cart events whose visitor placed a purchase within 24h and
    flag them as recovered, linking the order.
    """
    now = datetime.now(timezone.utc)
    lookback = (now - timedelta(hours=_LOOKBACK_HOURS)).isoformat()

    abandoned = (
        sb.table("cart_events")
        .select("id, client_id, visitor_id, abandoned_at")
        .eq("is_abandoned", True)
        .is_("recovered_at", "null")
        .gte("abandoned_at", lookback)
        .limit(500)
        .execute()
    )
    rows = abandoned.data or []
    if not rows:
        return 0

    visitor_ids = list({r["visitor_id"] for r in rows if r.get("visitor_id")})
    purchases: list[dict] = []
    if visitor_ids:
        purchases = (
            sb.table("orders")
            .select("id, visitor_id, created_at")
            .in_("visitor_id", visitor_ids)
            .gte("created_at", lookback)
            .execute()
        ).data or []

    # First purchase per visitor wins (recovers the earliest abandoned cart).
    first_order_by_visitor: dict[str, dict] = {}
    for o in sorted(purchases, key=lambda x: x["created_at"]):
        v = o.get("visitor_id")
        if v and v not in first_order_by_visitor:
            first_order_by_visitor[v] = o

    marked = 0
    for r in rows:
        vis = r.get("visitor_id")
        order = first_order_by_visitor.get(vis or "")
        if not order or order["created_at"] < r["abandoned_at"]:
            continue
        try:
            sb.table("cart_events").update({
                "recovered_at":      order["created_at"],
                "recovery_order_id": order["id"],
            }).eq("id", r["id"]).execute()
            marked += 1
        except Exception as exc:
            logger.debug("mark_recovered row %s: %s", r["id"], exc)

    return marked


def run_hourly() -> None:
    """Scheduler entry point — runs both passes."""
    try:
        sb = get_supabase()
        abandoned = _mark_abandoned(sb)
        recovered = _mark_recovered(sb)
        if abandoned or recovered:
            logger.info("cart_abandonment: abandoned=%d recovered=%d", abandoned, recovered)
    except Exception as exc:
        logger.error("cart_abandonment.run_hourly failed: %s", exc)
