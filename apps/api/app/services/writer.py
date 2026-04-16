"""
Structured writers: persist NormalizedEvent to the v2.0 schema tables.

Flow:
  pixel event  → tracking_events  (+ upsert visitors)
  webhook order → orders           (+ upsert visitors + webhook_deliveries)

All functions are best-effort: they log errors but never raise,
so a DB write failure never breaks the HTTP response.
"""

import logging
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
) -> Optional[str]:
    """
    Upsert visitor keyed on (client_id, visitor_id).
    Returns the visitor UUID or None on failure.
    """
    if not visitor_cookie_id:
        return None
    try:
        sb = get_supabase()
        existing = (
            sb.table("visitors")
            .select("id,total_pageviews")
            .eq("client_id", client_uuid)
            .eq("visitor_id", visitor_cookie_id)
            .limit(1)
            .execute()
        )
        if existing and existing.data:
            row = existing.data[0]
            sb.table("visitors").update({
                "last_seen_at": "now()",
                "total_pageviews": (row.get("total_pageviews") or 0) + 1,
            }).eq("id", row["id"]).execute()
            return row["id"]

        # New visitor
        insert_result = sb.table("visitors").insert({
            "client_id": client_uuid,
            "visitor_id": visitor_cookie_id,
            "first_utm_source": utm_source,
            "first_utm_medium": utm_medium,
            "first_utm_campaign": utm_campaign,
            "first_platform": first_platform,
            "total_pageviews": 1,
        }).execute()
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
        # Try to find by email first
        if email:
            existing = (
                sb.table("visitors")
                .select("id,total_orders")
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
                    "total_orders": (row.get("total_orders") or 0) + 1,
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
            "total_orders": 1,
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
    Returns the order UUID or None.
    """
    order = event.order
    if not order or not order.id:
        return None

    utm = event.utm
    customer = event.customer

    row = {
        "platform_order_id": order.id,
        "platform_order_number": order.number,
        "platform_source": event.platform,
        "platform": event.platform,
        "email": customer.email if customer else None,
        "phone": customer.phone if customer else None,
        "total_price": order.total,
        "currency": order.currency or "BRL",
        "financial_status": order.status,
        "utm_source":   utm.source   if utm else None,
        "utm_medium":   utm.medium   if utm else None,
        "utm_campaign": utm.campaign if utm else None,
        "utm_content":  utm.content  if utm else None,
        "is_first_purchase": False,
        "is_repeat_purchase": False,
        "capi_sent": False,
    }
    if client_uuid:
        row["client_id"] = client_uuid
    if visitor_uuid:
        row["visitor_id"] = visitor_uuid

    try:
        sb = get_supabase()
        # Upsert on (client_id, platform_order_id)
        result = (
            sb.table("orders")
            .upsert(row, on_conflict="client_id,platform_order_id" if client_uuid else None)
            .execute()
        )
        if result.data:
            order_uuid = result.data[0]["id"]
            logger.debug("orders upsert OK — %s", order_uuid)
            return order_uuid
    except Exception as exc:
        logger.error("write_order failed: %s", exc)
    return None


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
