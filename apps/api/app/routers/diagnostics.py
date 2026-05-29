"""
Pixel diagnostics — comprehensive health snapshot for a given pixel_id.

Returns event volumes, identifier coverage, and per-channel CAPI status so
the dashboard can surface exactly where a client's data pipeline stands.
"""

import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException

from ..database import get_supabase

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get(
    "/diagnostics/{pixel_id}",
    summary="Full data-pipeline health snapshot",
    tags=["diagnostics"],
)
async def get_diagnostics(pixel_id: str):
    sb = get_supabase()

    client_row = (
        sb.table("clients")
        .select("id, name, pixel_id, meta_pixel_id, ga4_measurement_id, google_ads_customer_id, tiktok_pixel_id, is_active")
        .eq("pixel_id", pixel_id)
        .limit(1)
        .execute()
    )
    if not (client_row and client_row.data):
        raise HTTPException(status_code=404, detail="Client not found")
    c      = client_row.data[0]
    cid    = c["id"]
    now    = datetime.now(timezone.utc)
    t24h   = (now - timedelta(hours=24)).isoformat()
    t7d    = (now - timedelta(days=7)).isoformat()
    t30d   = (now - timedelta(days=30)).isoformat()

    # ── Tracking events ───────────────────────────────────────────────────────
    ev_last = (
        sb.table("tracking_events")
        .select("created_at")
        .eq("client_id", cid)
        .order("created_at", desc=True)
        .limit(1)
        .execute()
    )
    last_event_at = (ev_last.data[0]["created_at"] if ev_last.data else None)

    ev_24h = (
        sb.table("tracking_events")
        .select("id", count="exact", head=True)
        .eq("client_id", cid)
        .gte("created_at", t24h)
        .execute()
    )
    ev_7d = (
        sb.table("tracking_events")
        .select("id", count="exact", head=True)
        .eq("client_id", cid)
        .gte("created_at", t7d)
        .execute()
    )

    # ── Orders (paid, last 30d) ───────────────────────────────────────────────
    orders_q = (
        sb.table("orders")
        .select(
            "id, created_at, capi_sent, capi_last_error, "
            "google_sent, google_last_error, "
            "tiktok_sent, tiktok_last_error, "
            "visitor_id"
        )
        .eq("client_id", cid)
        .eq("financial_status", "paid")
        .gt("total_price", 0)
        .gte("created_at", t30d)
        .execute()
    )
    orders = orders_q.data or []
    total_orders = len(orders)

    # CAPI coverage
    meta_sent    = sum(1 for o in orders if o.get("capi_sent"))
    meta_errors  = [o["capi_last_error"] for o in orders if o.get("capi_last_error") and not o.get("capi_sent")]

    google_sent   = sum(1 for o in orders if o.get("google_sent"))
    google_errors = [o["google_last_error"] for o in orders if o.get("google_last_error") and not o.get("google_sent")]

    tiktok_sent   = sum(1 for o in orders if o.get("tiktok_sent"))
    tiktok_errors = [o["tiktok_last_error"] for o in orders if o.get("tiktok_last_error") and not o.get("tiktok_sent")]

    # Last error per channel
    last_meta_err   = meta_errors[-1][:300]   if meta_errors   else None
    last_google_err = google_errors[-1][:300] if google_errors else None
    last_tiktok_err = tiktok_errors[-1][:300] if tiktok_errors else None

    # Orders with visitor_id (linkage rate)
    linked_orders = sum(1 for o in orders if o.get("visitor_id"))

    # ── Identifier coverage (visitors, last 30d) ──────────────────────────────
    vis_total_q = (
        sb.table("visitors")
        .select("id", count="exact", head=True)
        .eq("client_id", cid)
        .gte("first_seen_at", t30d)
        .execute()
    )
    vis_total = vis_total_q.count or 0

    fbp_q = (
        sb.table("visitors")
        .select("id", count="exact", head=True)
        .eq("client_id", cid)
        .gte("first_seen_at", t30d)
        .not_.is_("fbp", "null")
        .execute()
    )
    fbc_q = (
        sb.table("visitors")
        .select("id", count="exact", head=True)
        .eq("client_id", cid)
        .gte("first_seen_at", t30d)
        .not_.is_("fbc", "null")
        .execute()
    )
    gclid_q = (
        sb.table("visitors")
        .select("id", count="exact", head=True)
        .eq("client_id", cid)
        .gte("first_seen_at", t30d)
        .not_.is_("gclid", "null")
        .execute()
    )
    ttclid_q = (
        sb.table("visitors")
        .select("id", count="exact", head=True)
        .eq("client_id", cid)
        .gte("first_seen_at", t30d)
        .not_.is_("ttclid", "null")
        .execute()
    )

    ev_total_30d_q = (
        sb.table("tracking_events")
        .select("id", count="exact", head=True)
        .eq("client_id", cid)
        .gte("created_at", t30d)
        .execute()
    )
    ev_total_30d = ev_total_30d_q.count or 0

    fbp_count   = fbp_q.count   or 0
    fbc_count   = fbc_q.count   or 0
    gclid_count = gclid_q.count or 0
    ttclid_count= ttclid_q.count or 0

    def pct(n: int, total: int) -> float | None:
        return round(n / total * 100, 1) if total > 0 else None

    # ── Open alerts ───────────────────────────────────────────────────────────
    alerts_q = (
        sb.table("alerts")
        .select("severity")
        .eq("client_id", cid)
        .is_("resolved_at", "null")
        .execute()
    )
    alert_rows = alerts_q.data or []
    alert_critical = sum(1 for a in alert_rows if a.get("severity") == "critical")
    alert_warning  = sum(1 for a in alert_rows if a.get("severity") == "warning")

    return {
        "pixel_id":       pixel_id,
        "client_name":    c.get("name"),
        "is_active":      c.get("is_active"),
        "now":            now.isoformat(),
        # ── Tracking events ────────────────────────────────────────────────
        "last_event_at":  last_event_at,
        "events_24h":     ev_24h.count  or 0,
        "events_7d":      ev_7d.count   or 0,
        "events_30d":     ev_total_30d,
        # ── Identifier coverage (last 30d events) ─────────────────────────
        "identifiers": {
            "visitors_30d":     vis_total,
            "fbp_count":        fbp_count,
            "fbp_pct":          pct(fbp_count,    vis_total),
            "fbc_count":        fbc_count,
            "fbc_pct":          pct(fbc_count,    vis_total),
            "gclid_visitors":   gclid_count,
            "gclid_pct":        pct(gclid_count,  vis_total),
            "ttclid_visitors":  ttclid_count,
            "ttclid_pct":       pct(ttclid_count, vis_total),
        },
        # ── Orders (paid, last 30d) ────────────────────────────────────────
        "orders_30d":          total_orders,
        "orders_visitor_linked": linked_orders,
        "orders_linked_pct":   pct(linked_orders, total_orders),
        # ── CAPI status ────────────────────────────────────────────────────
        "capi": {
            "meta": {
                "configured":   bool(c.get("meta_pixel_id")),
                "sent":         meta_sent,
                "sent_pct":     pct(meta_sent,   total_orders),
                "errors":       len(meta_errors),
                "last_error":   last_meta_err,
            },
            "google": {
                "configured":   bool(c.get("google_ads_customer_id")),
                "sent":         google_sent,
                "sent_pct":     pct(google_sent,  total_orders),
                "errors":       len(google_errors),
                "last_error":   last_google_err,
            },
            "tiktok": {
                "configured":   bool(c.get("tiktok_pixel_id")),
                "sent":         tiktok_sent,
                "sent_pct":     pct(tiktok_sent,  total_orders),
                "errors":       len(tiktok_errors),
                "last_error":   last_tiktok_err,
            },
        },
        # ── Open alerts ────────────────────────────────────────────────────
        "open_alerts": {
            "critical": alert_critical,
            "warning":  alert_warning,
            "total":    len(alert_rows),
        },
    }
