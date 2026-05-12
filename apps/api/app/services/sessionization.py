"""
Sessionization — aggregate tracking_events into the sessions table.

Runs daily at 04:00 UTC (01:00 BRT). Looks at the previous full UTC day's events,
groups them by session_id (tracker.js sets one per tab via sessionStorage), and
upserts one row per session into the sessions table.

Why batch instead of streaming: tracker.js already mints session_id client-side
and writes it on every tracking_events row. A nightly roll-up gives us the
fields the dashboard wants (bounced, had_*, duration, counts) without the cost
of recomputing on every event.

Idempotent: re-running the same day overwrites the same session rows.
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from ..database import get_supabase

logger = logging.getLogger(__name__)

# Pixel event types that signal funnel progression. Keep in sync with
# _TRACKING_EVENT_TYPE_MAP in writer.py (these are the post-map values).
_ADD_TO_CART_EVENT = "add_to_cart"
_CHECKOUT_EVENT    = "begin_checkout"
_PURCHASE_EVENT    = "purchase"


def _day_bounds_utc(target: Optional[datetime] = None) -> tuple[str, str]:
    """Return ISO-8601 [start, end) for the UTC day prior to `target`."""
    now = target or datetime.now(timezone.utc)
    end   = now.replace(hour=0, minute=0, second=0, microsecond=0)
    start = end - timedelta(days=1)
    return start.isoformat(), end.isoformat()


def _aggregate_client_day(sb, client_id: str, start: str, end: str) -> int:
    """Aggregate one client's events for [start, end) and upsert sessions. Returns rows upserted."""
    # Pull only what we need. Cap at 50k rows/client/day — anything past that
    # likely indicates a bot run that would skew session stats anyway.
    events = (
        sb.table("tracking_events")
        .select(
            "session_id, visitor_id, event_type, value, created_at, "
            "utm_source, utm_medium, utm_campaign, utm_content, "
            "fbclid, gclid, referrer, url, device_type, browser, os, country, city"
        )
        .eq("client_id", client_id)
        .gte("created_at", start)
        .lt("created_at", end)
        .not_.is_("session_id", "null")
        .order("created_at")
        .limit(50000)
        .execute()
    ).data or []

    if not events:
        return 0

    # Group by session_id
    sessions: dict[str, dict] = {}
    for ev in events:
        sid = ev.get("session_id")
        if not sid:
            continue
        s = sessions.setdefault(sid, {
            "session_id":      sid,
            "client_id":       client_id,
            "visitor_id":      ev.get("visitor_id"),
            "started_at":      ev["created_at"],
            "ended_at":        ev["created_at"],
            "utm_source":      ev.get("utm_source"),
            "utm_medium":      ev.get("utm_medium"),
            "utm_campaign":    ev.get("utm_campaign"),
            "utm_content":     ev.get("utm_content"),
            "fbclid":          ev.get("fbclid"),
            "gclid":           ev.get("gclid"),
            "referrer":        ev.get("referrer"),
            "landing_url":     ev.get("url"),
            "device_type":     ev.get("device_type"),
            "browser":         ev.get("browser"),
            "os":              ev.get("os"),
            "country":         ev.get("country"),
            "city":            ev.get("city"),
            "pageviews_count": 0,
            "events_count":    0,
            "had_add_to_cart": False,
            "had_checkout":    False,
            "had_purchase":    False,
            "purchase_value":  0.0,
        })
        s["events_count"] += 1
        if ev["created_at"] > s["ended_at"]:
            s["ended_at"] = ev["created_at"]
        et = ev.get("event_type")
        if et == "pageview":
            s["pageviews_count"] += 1
        elif et == _ADD_TO_CART_EVENT:
            s["had_add_to_cart"] = True
        elif et == _CHECKOUT_EVENT:
            s["had_checkout"] = True
        elif et == _PURCHASE_EVENT:
            s["had_purchase"] = True
            try:
                s["purchase_value"] += float(ev.get("value") or 0)
            except (TypeError, ValueError):
                pass

    # Finalize derived fields
    rows: list[dict] = []
    for s in sessions.values():
        try:
            start_dt = datetime.fromisoformat(s["started_at"].replace("Z", "+00:00"))
            end_dt   = datetime.fromisoformat(s["ended_at"].replace("Z", "+00:00"))
            duration = max(0, int((end_dt - start_dt).total_seconds()))
        except Exception:
            duration = 0
        s["duration_seconds"] = duration
        # Bounced: 1 event total and no funnel progression.
        s["bounced"] = (s["events_count"] <= 1 and not s["had_add_to_cart"]
                        and not s["had_checkout"] and not s["had_purchase"])
        if s["purchase_value"] == 0:
            s["purchase_value"] = None  # leave null when no purchase
        rows.append(s)

    # Upsert in batches — supabase-py has practical payload limits.
    upserted = 0
    for i in range(0, len(rows), 500):
        chunk = rows[i:i + 500]
        try:
            sb.table("sessions").upsert(chunk, on_conflict="client_id,session_id").execute()
            upserted += len(chunk)
        except Exception as exc:
            logger.warning("sessionization upsert failed (chunk %d, client %s): %s",
                           i, client_id, exc)
    return upserted


def run_daily() -> None:
    """Scheduler entry point — aggregates yesterday's events for every active client."""
    sb = get_supabase()
    start, end = _day_bounds_utc()
    try:
        clients = (
            sb.table("clients")
            .select("id")
            .eq("is_active", True)
            .execute()
        ).data or []
    except Exception as exc:
        logger.error("sessionization: failed to list clients: %s", exc)
        return

    total = 0
    for c in clients:
        try:
            total += _aggregate_client_day(sb, c["id"], start, end)
        except Exception as exc:
            logger.warning("sessionization: client %s failed: %s", c.get("id"), exc)

    logger.info("sessionization: window=[%s, %s) sessions_upserted=%d clients=%d",
                start, end, total, len(clients))


def backfill_day(client_id: str, target_day_utc: datetime) -> int:
    """Manual backfill for a single client+day. Returns sessions upserted."""
    end   = target_day_utc.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
    start = end - timedelta(days=1)
    return _aggregate_client_day(get_supabase(), client_id, start.isoformat(), end.isoformat())
