"""
Structured writers: persist NormalizedEvent to the v2.0 schema tables.

Flow:
  pixel event  → tracking_events  (+ upsert visitors)
  webhook order → orders           (+ upsert visitors + webhook_deliveries)

All functions are best-effort: they log errors but never raise,
so a DB write failure never breaks the HTTP response.
"""

import logging
from datetime import datetime, timezone
from typing import Optional

from ..database import get_supabase
from ..models.events import NormalizedEvent, EventType

logger = logging.getLogger(__name__)

# Maps our EventType values → tracking_events.event_type CHECK constraint values
_TRACKING_EVENT_TYPE_MAP: dict[str, str] = {
    "page.viewed":          "pageview",
    "product.viewed":       "view_product",
    "cart.created":         "add_to_cart",
    "cart.updated":         "add_to_cart",
    "checkout.started":     "begin_checkout",
    "checkout.completed":   "purchase",
    "order.created":        "purchase",
    "order.paid":           "purchase",
    "order.updated":        "custom",
    "order.cancelled":      "custom",
    "order.fulfilled":      "custom",
    "customer.created":     "custom",
    "custom":               "custom",
}


# ── Client resolution ─────────────────────────────────────────────────────────

def resolve_client_uuid(pixel_id: str) -> Optional[str]:
    """
    Resolve pixel_id (TEXT passed in the URL) → clients.id (UUID).
    Returns None if the client is not found or inactive.
    """
    try:
        result = (
            get_supabase()
            .table("clients")
            .select("id")
            .eq("pixel_id", pixel_id)
            .eq("is_active", True)
            .limit(1)
            .execute()
        )
        if result and result.data:
            return result.data[0]["id"]
    except Exception as exc:
        logger.debug("resolve_client_uuid(%s): %s", pixel_id, exc)
    return None


# ── Visitor upsert ─────────────────────────────────────────────────────────────

def upsert_visitor_by_cookie(
    client_uuid: str,
    visitor_cookie_id: str,
    utm_source: Optional[str] = None,
    utm_medium: Optional[str] = None,
    utm_campaign: Optional[str] = None,
    first_platform: str = "pixel",
    gclid: Optional[str] = None,
    fbclid: Optional[str] = None,
) -> Optional[str]:
    """
    Upsert visitor keyed on (client_id, visitor_id).
    Also persists gclid/fbclid for Google Ads / Meta attribution.
    Returns the visitor UUID or None on failure.
    """
    if not visitor_cookie_id:
        return None
    try:
        sb = get_supabase()
        existing = (
            sb.table("visitors")
            .select("id,total_pageviews,gclid,fbclid")
            .eq("client_id", client_uuid)
            .eq("visitor_id", visitor_cookie_id)
            .limit(1)
            .execute()
        )
        if existing and existing.data:
            row = existing.data[0]
            update: dict = {
                "last_seen_at": "now()",
                "total_pageviews": (row.get("total_pageviews") or 0) + 1,
            }
            # Only set gclid/fbclid if not already stored (first touch wins)
            if gclid and not row.get("gclid"):
                update["gclid"] = gclid
            if fbclid and not row.get("fbclid"):
                update["fbclid"] = fbclid
            sb.table("visitors").update(update).eq("id", row["id"]).execute()
            return row["id"]

        # New visitor
        insert_row: dict = {
            "client_id": client_uuid,
            "visitor_id": visitor_cookie_id,
            "first_utm_source": utm_source,
            "first_utm_medium": utm_medium,
            "first_utm_campaign": utm_campaign,
            "first_platform": first_platform,
            "total_pageviews": 1,
        }
        if gclid:
            insert_row["gclid"] = gclid
        if fbclid:
            insert_row["fbclid"] = fbclid

        insert_result = sb.table("visitors").insert(insert_row).execute()
        if insert_result and insert_result.data:
            return insert_result.data[0]["id"]
    except Exception as exc:
        logger.warning("upsert_visitor_by_cookie: %s", exc)
    return None


def upsert_visitor_by_email(
    client_uuid: str,
    email: Optional[str],
    phone: Optional[str] = None,
    platform_customer_id: Optional[str] = None,
    platform: str = "shopify",
) -> Optional[str]:
    """
    Find or create a visitor from an order's customer email/phone.
    Returns the visitor UUID or None.
    """
    if not email and not platform_customer_id:
        return None
    try:
        sb = get_supabase()
        if email:
            existing = (
                sb.table("visitors")
                .select("id,total_orders,total_revenue,ltv")
                .eq("client_id", client_uuid)
                .eq("email", email)
                .limit(1)
                .execute()
            )
            if existing and existing.data:
                row = existing.data[0]
                sb.table("visitors").update({
                    "last_seen_at": "now()",
                    "platform_customer_id": platform_customer_id,
                    # total_orders and total_revenue are updated by write_order
                }).eq("id", row["id"]).execute()
                return row["id"]

        # New visitor from order
        insert_result = sb.table("visitors").insert({
            "client_id": client_uuid,
            "visitor_id": f"order_{platform_customer_id or email}",
            "email": email,
            "phone": phone,
            "platform_customer_id": platform_customer_id,
            "first_platform": platform,
            "total_orders": 0,  # write_order will increment
        }).execute()
        if insert_result and insert_result.data:
            return insert_result.data[0]["id"]
    except Exception as exc:
        logger.warning("upsert_visitor_by_email: %s", exc)
    return None


# ── tracking_events ────────────────────────────────────────────────────────────

def write_tracking_event(
    client_uuid: Optional[str],
    visitor_uuid: Optional[str],
    event: NormalizedEvent,
) -> None:
    """Insert a pixel event into tracking_events."""
    utm = event.utm
    event_type_mapped = _TRACKING_EVENT_TYPE_MAP.get(event.event_type.value, "custom")
    row = {
        "event_type": event_type_mapped,
        "visitor_cookie_id": event.visitor_id,
        "session_id": event.session_id,
        "url": event.page_url,
        "referrer": event.referrer,
        "utm_source":   utm.source   if utm else None,
        "utm_medium":   utm.medium   if utm else None,
        "utm_campaign": utm.campaign if utm else None,
        "utm_content":  utm.content  if utm else None,
        "utm_term":     utm.term     if utm else None,
        "properties": event.metadata or {},
        "processed": False,
    }
    if client_uuid:
        row["client_id"] = client_uuid
    if visitor_uuid:
        row["visitor_id"] = visitor_uuid
    # product fields
    if event.metadata:
        for field in ("product_id", "product_name", "product_sku",
                      "product_price", "product_quantity", "product_category"):
            if field in event.metadata:
                row[field] = event.metadata[field]
    try:
        get_supabase().table("tracking_events").insert(row).execute()
        logger.debug("tracking_events insert OK — %s", event.event_id)
    except Exception as exc:
        logger.error("write_tracking_event failed: %s", exc)


# ── orders ────────────────────────────────────────────────────────────────────

def write_order(
    client_uuid: Optional[str],
    visitor_uuid: Optional[str],
    event: NormalizedEvent,
) -> Optional[str]:
    """
    Upsert an order into the orders table.
    Correctly sets is_first_purchase by checking prior orders for this visitor/email.
    Updates visitor totals (total_orders, total_revenue, ltv) after writing.
    Returns the order UUID or None.
    """
    order = event.order
    if not order or not order.id:
        return None

    utm = event.utm
    customer = event.customer
    sb = get_supabase()

    # ── Determine first vs repeat purchase ───────────────────────────────────
    is_first = True
    if client_uuid and customer and customer.email:
        try:
            prior = (
                sb.table("orders")
                .select("id", count="exact", head=True)
                .eq("client_id", client_uuid)
                .eq("email", customer.email)
                .execute()
            )
            is_first = (prior.count or 0) == 0
        except Exception as exc:
            logger.debug("first_purchase check failed: %s", exc)

    # Inherit UTM from visitor pixel session when webhook carries no UTM
    effective_utm_source   = utm.source   if utm else None
    effective_utm_medium   = utm.medium   if utm else None
    effective_utm_campaign = utm.campaign if utm else None
    effective_utm_content  = utm.content  if utm else None

    if visitor_uuid and not effective_utm_source:
        try:
            vis = (
                sb.table("visitors")
                .select("first_utm_source,first_utm_medium,first_utm_campaign,gclid,fbclid")
                .eq("id", visitor_uuid)
                .limit(1)
                .execute()
            )
            if vis and vis.data:
                v = vis.data[0]
                effective_utm_source   = v.get("first_utm_source")
                effective_utm_medium   = v.get("first_utm_medium")
                effective_utm_campaign = v.get("first_utm_campaign")
        except Exception as exc:
            logger.debug("utm inheritance from visitor failed: %s", exc)

    row = {
        "platform_order_id":     order.id,
        "platform_order_number": order.number,
        "platform_source":       event.platform,
        "platform":              event.platform,
        "email":                 customer.email  if customer else None,
        "phone":                 customer.phone  if customer else None,
        "total_price":           order.total,
        "currency":              order.currency or "BRL",
        "financial_status":      order.status,
        "utm_source":   effective_utm_source,
        "utm_medium":   effective_utm_medium,
        "utm_campaign": effective_utm_campaign,
        "utm_content":  effective_utm_content,
        "is_first_purchase":  is_first,
        "is_repeat_purchase": not is_first,
        "capi_sent": False,
    }
    if client_uuid:
        row["client_id"] = client_uuid
    if visitor_uuid:
        row["visitor_id"] = visitor_uuid

    try:
        result = (
            sb.table("orders")
            .upsert(row, on_conflict="client_id,platform_order_id" if client_uuid else None)
            .execute()
        )
        if result.data:
            order_uuid = result.data[0]["id"]
            logger.debug("orders upsert OK — %s (first=%s)", order_uuid, is_first)

            # ── Update visitor totals ─────────────────────────────────────────
            if visitor_uuid and order.total:
                _update_visitor_totals(visitor_uuid, float(order.total))

            return order_uuid
    except Exception as exc:
        logger.error("write_order failed: %s", exc)
    return None


def _update_visitor_totals(visitor_uuid: str, order_total: float) -> None:
    """Increment visitor total_orders, total_revenue and ltv after a new order."""
    try:
        sb = get_supabase()
        existing = (
            sb.table("visitors")
            .select("total_orders,total_revenue,ltv")
            .eq("id", visitor_uuid)
            .limit(1)
            .execute()
        )
        if not (existing and existing.data):
            return
        row = existing.data[0]
        new_orders  = (row.get("total_orders")  or 0) + 1
        new_revenue = float(row.get("total_revenue") or 0) + order_total
        sb.table("visitors").update({
            "total_orders":  new_orders,
            "total_revenue": round(new_revenue, 2),
            "ltv":           round(new_revenue, 2),  # ltv = lifetime revenue
        }).eq("id", visitor_uuid).execute()
        logger.debug("visitor totals updated — %s orders=%d ltv=%.2f",
                     visitor_uuid, new_orders, new_revenue)
    except Exception as exc:
        logger.warning("_update_visitor_totals failed: %s", exc)


# ── Visitor email merge ────────────────────────────────────────────────────────

def set_visitor_email(
    visitor_uuid: str,
    email: str,
    phone: Optional[str] = None,
) -> None:
    """
    Write email (and optionally phone) onto a cookie-based visitor after checkout.

    Called when the browser pixel fires checkout_completed with customer_email.
    This links the anonymous browsing session to the real customer, so that
    upsert_visitor_by_email (called later by the webhook) finds the same record
    instead of creating a duplicate.
    """
    if not visitor_uuid or not email:
        return
    try:
        update: dict = {"email": email.strip().lower()}
        if phone:
            update["phone"] = phone
        get_supabase().table("visitors").update(update).eq("id", visitor_uuid).execute()
        logger.debug("visitor %s → email linked: %s", visitor_uuid, email)
    except Exception as exc:
        logger.warning("set_visitor_email failed: %s", exc)


# ── fulfillment_status update ─────────────────────────────────────────────────

def update_order_fulfillment(
    client_uuid: str,
    platform_order_id: str,
    fulfillment_status: str,
) -> None:
    """Update fulfillment_status on an existing order after orders/fulfilled webhook."""
    if not client_uuid or not platform_order_id:
        return
    try:
        get_supabase().table("orders").update(
            {"fulfillment_status": fulfillment_status}
        ).eq("client_id", client_uuid).eq("platform_order_id", platform_order_id).execute()
        logger.debug("fulfillment_status=%s for order %s", fulfillment_status, platform_order_id)
    except Exception as exc:
        logger.warning("update_order_fulfillment failed: %s", exc)


# ── Lead quality score ────────────────────────────────────────────────────────

_LEAD_SCORE_MAP: dict[str, int] = {
    "product.viewed":     1,
    "cart.created":       3,
    "cart.updated":       3,
    "checkout.started":   5,
    "checkout.completed": 10,
    "order.paid":         10,
}


def update_lead_score(visitor_uuid: str, event_type: EventType) -> None:
    """Increment visitor lead_score based on engagement event."""
    points = _LEAD_SCORE_MAP.get(event_type.value, 0)
    if not points or not visitor_uuid:
        return
    try:
        sb = get_supabase()
        existing = (
            sb.table("visitors")
            .select("lead_score")
            .eq("id", visitor_uuid)
            .limit(1)
            .execute()
        )
        if not (existing and existing.data):
            return
        current = existing.data[0].get("lead_score") or 0
        sb.table("visitors").update(
            {"lead_score": current + points}
        ).eq("id", visitor_uuid).execute()
        logger.debug("lead_score +%d → %d for visitor %s", points, current + points, visitor_uuid)
    except Exception as exc:
        logger.warning("update_lead_score failed: %s", exc)


# ── Mark CAPI sent ─────────────────────────────────────────────────────────────

def mark_capi_sent(order_uuid: str) -> None:
    """Mark an order as successfully sent to Meta CAPI."""
    try:
        get_supabase().table("orders").update({
            "capi_sent":    True,
            "capi_sent_at": datetime.now(timezone.utc).isoformat(),
        }).eq("id", order_uuid).execute()
        logger.debug("capi_sent=true for order %s", order_uuid)
    except Exception as exc:
        logger.warning("mark_capi_sent failed: %s", exc)


# ── webhook_deliveries ────────────────────────────────────────────────────────

def write_webhook_delivery(
    client_uuid: Optional[str],
    event: NormalizedEvent,
    raw_headers: dict,
    order_uuid: Optional[str],
    visitor_uuid: Optional[str],
) -> None:
    """Record every webhook delivery for audit and replay."""
    row = {
        "platform": event.platform,
        "platform_event_id": event.event_id,
        "event_topic": (event.metadata or {}).get("topic", event.event_type.value),
        "payload": event.raw_payload or {},
        "headers": {k: v for k, v in raw_headers.items()
                    if k.lower().startswith(("x-shopify", "x-wc", "x-nuvemshop"))},
        "signature_valid": True,
        "status": "processed",
        "result_order_id": order_uuid,
        "result_visitor_id": visitor_uuid,
    }
    if client_uuid:
        row["client_id"] = client_uuid
    try:
        get_supabase().table("webhook_deliveries").insert(row).execute()
    except Exception as exc:
        logger.error("write_webhook_delivery failed: %s", exc)
