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
import json

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
    fbp: Optional[str] = None,
    fbc: Optional[str] = None,
    cart_token: Optional[str] = None,
    ga_client_id: Optional[str] = None,
    ttclid: Optional[str] = None,
) -> Optional[str]:
    """
    Upsert visitor keyed on (client_id, visitor_id).
    Also persists gclid/fbclid/ttclid for Google Ads / Meta / TikTok attribution.
    Returns the visitor UUID or None on failure.
    """
    if not visitor_cookie_id:
        return None
    try:
        sb = get_supabase()
        existing = (
            sb.table("visitors")
            .select("id,total_pageviews,gclid,fbclid,fbp,fbc,cart_token,ga_client_id,ttclid")
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
            # First touch wins for attribution identifiers
            if gclid        and not row.get("gclid"):        update["gclid"]        = gclid
            if fbclid       and not row.get("fbclid"):       update["fbclid"]       = fbclid
            if fbp          and not row.get("fbp"):          update["fbp"]          = fbp
            if fbc          and not row.get("fbc"):          update["fbc"]          = fbc
            if cart_token   and not row.get("cart_token"):   update["cart_token"]   = cart_token
            if ga_client_id and not row.get("ga_client_id"): update["ga_client_id"] = ga_client_id
            if ttclid       and not row.get("ttclid"):       update["ttclid"]       = ttclid
            # Append to utm_history for multi-touch attribution
            if utm_source:
                history = row.get("utm_history") or []
                if not isinstance(history, list):
                    history = []
                last_source = history[-1].get("source") if history else None
                last_campaign = history[-1].get("campaign") if history else None
                if utm_source != last_source or utm_campaign != last_campaign:
                    history.append({
                        "ts":       datetime.now(timezone.utc).isoformat(),
                        "source":   utm_source,
                        "medium":   utm_medium,
                        "campaign": utm_campaign,
                    })
                    update["utm_history"] = history[-20:]  # keep last 20 touches
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
        if gclid:        insert_row["gclid"]        = gclid
        if fbclid:       insert_row["fbclid"]       = fbclid
        if fbp:          insert_row["fbp"]          = fbp
        if fbc:          insert_row["fbc"]          = fbc
        if cart_token:   insert_row["cart_token"]   = cart_token
        if ga_client_id: insert_row["ga_client_id"] = ga_client_id
        if ttclid:       insert_row["ttclid"]       = ttclid

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
    cart_token: Optional[str] = None,
    visitor_cookie_id: Optional[str] = None,
    fbp: Optional[str] = None,
    fbc: Optional[str] = None,
    gclid: Optional[str] = None,
    ga_client_id: Optional[str] = None,
) -> Optional[str]:
    """
    Find or create a visitor from an order's customer email/phone.

    Lookup priority (highest match-quality first):
      1. visitor_cookie_id (_etv injected as cart attribute) — direct link to
         the exact browse session, so we inherit fbp/fbc/UTM. This is the
         strongest signal and works even when the customer is redirected to
         an external payment gateway (PIX, Mercado Pago) and never returns
         to the Shopify thank-you page.
      2. Email match — visitor already linked (e.g. previous purchase).
      3. cart_token match — fallback for the legacy flow when checkout_started
         pixel event captured the cart but the cookie wasn't injected as
         note_attribute.
      4. Create new visitor — last resort, no browse signal will be inherited.

    On match, attaches any new identifiers (email/phone/fbp/fbc/...) using
    first-touch-wins semantics — never overwrites an existing non-null value.
    """
    if not (email or platform_customer_id or cart_token or visitor_cookie_id):
        return None
    try:
        sb = get_supabase()

        # ── 1. Lookup by visitor_cookie_id (_etv) ─────────────────────────
        if visitor_cookie_id and client_uuid:
            by_cookie = (
                sb.table("visitors")
                .select("id, email, phone, fbp, fbc, gclid, ga_client_id, platform_customer_id")
                .eq("client_id", client_uuid)
                .eq("visitor_id", visitor_cookie_id)
                .limit(1)
                .execute()
            )
            if by_cookie and by_cookie.data:
                row = by_cookie.data[0]
                update: dict = {"last_seen_at": "now()"}
                if email and not row.get("email"):
                    update["email"] = email.strip().lower()
                if phone and not row.get("phone"):
                    update["phone"] = phone
                if platform_customer_id and not row.get("platform_customer_id"):
                    update["platform_customer_id"] = platform_customer_id
                # Backfill identifiers if the visitor row is missing any
                if fbp and not row.get("fbp"):                   update["fbp"]          = fbp
                if fbc and not row.get("fbc"):                   update["fbc"]          = fbc
                if gclid and not row.get("gclid"):               update["gclid"]        = gclid
                if ga_client_id and not row.get("ga_client_id"): update["ga_client_id"] = ga_client_id
                sb.table("visitors").update(update).eq("id", row["id"]).execute()
                logger.debug("visitor %s matched by _etv cookie", row["id"])
                return row["id"]

        # ── 2. Lookup by email ────────────────────────────────────────────
        if email:
            existing = (
                sb.table("visitors")
                .select("id, fbp, fbc, gclid, ga_client_id, platform_customer_id")
                .eq("client_id", client_uuid)
                .eq("email", email)
                .limit(1)
                .execute()
            )
            if existing and existing.data:
                row = existing.data[0]
                update = {"last_seen_at": "now()"}
                if platform_customer_id and not row.get("platform_customer_id"):
                    update["platform_customer_id"] = platform_customer_id
                # Late-arriving identifiers from cart_attributes can backfill
                if fbp and not row.get("fbp"):                   update["fbp"]          = fbp
                if fbc and not row.get("fbc"):                   update["fbc"]          = fbc
                if gclid and not row.get("gclid"):               update["gclid"]        = gclid
                if ga_client_id and not row.get("ga_client_id"): update["ga_client_id"] = ga_client_id
                sb.table("visitors").update(update).eq("id", row["id"]).execute()
                return row["id"]

        # ── 3. Lookup by cart_token (legacy fallback) ─────────────────────
        if cart_token and client_uuid:
            by_cart = (
                sb.table("visitors")
                .select("id, email, phone, fbp, fbc, gclid, ga_client_id")
                .eq("client_id", client_uuid)
                .eq("cart_token", cart_token)
                .limit(1)
                .execute()
            )
            if by_cart and by_cart.data:
                row = by_cart.data[0]
                update = {"last_seen_at": "now()"}
                if email and not row.get("email"):
                    update["email"] = email.strip().lower()
                if phone and not row.get("phone"):
                    update["phone"] = phone
                if platform_customer_id:
                    update["platform_customer_id"] = platform_customer_id
                if fbp and not row.get("fbp"):                   update["fbp"]          = fbp
                if fbc and not row.get("fbc"):                   update["fbc"]          = fbc
                if gclid and not row.get("gclid"):               update["gclid"]        = gclid
                if ga_client_id and not row.get("ga_client_id"): update["ga_client_id"] = ga_client_id
                sb.table("visitors").update(update).eq("id", row["id"]).execute()
                logger.debug("visitor %s found by cart_token, email linked: %s", row["id"], email)
                return row["id"]

        # ── 4. Create new visitor from order ──────────────────────────────
        insert_row: dict = {
            "client_id": client_uuid,
            "visitor_id": visitor_cookie_id or f"order_{platform_customer_id or email}",
            "email": email,
            "phone": phone,
            "platform_customer_id": platform_customer_id,
            "first_platform": platform,
            "total_orders": 0,
        }
        if fbp:          insert_row["fbp"]          = fbp
        if fbc:          insert_row["fbc"]          = fbc
        if gclid:        insert_row["gclid"]        = gclid
        if ga_client_id: insert_row["ga_client_id"] = ga_client_id
        if cart_token:   insert_row["cart_token"]   = cart_token
        insert_result = sb.table("visitors").insert(insert_row).execute()
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
        # device_type is computed by the pixel router; surface it as a top-level
        # column so dashboards can filter without a JSONB scan.
        if event.metadata.get("device_type"):
            row["device_type"] = event.metadata["device_type"]
    try:
        get_supabase().table("tracking_events").insert(row).execute()
        logger.debug("tracking_events insert OK — %s", event.event_id)
    except Exception as exc:
        logger.error("write_tracking_event failed: %s", exc)


# ── cart_events ────────────────────────────────────────────────────────────────

# Maps pixel event_type → cart_events.action CHECK constraint value.
_CART_ACTION_MAP = {
    "cart.created":     "add",
    "cart.updated":     "update_quantity",
    "checkout.started": "begin_checkout",
}


def write_cart_event(
    client_uuid: Optional[str],
    visitor_uuid: Optional[str],
    event: NormalizedEvent,
) -> None:
    """
    Insert a cart_events row when the pixel reports cart activity.
    Powers cart-abandonment analytics and recovery flows downstream.
    """
    action = _CART_ACTION_MAP.get(event.event_type.value)
    if not action:
        return

    meta = event.metadata or {}
    row: dict = {
        "action":         action,
        "session_id":     event.session_id,
        "cart_items":     meta.get("cart_items"),
        "cart_total":     meta.get("cart_total") if meta.get("cart_total") is not None else meta.get("product_price"),
        "cart_currency":  meta.get("currency") or "BRL",
        "item_count":     meta.get("item_count"),
        "product_id":     meta.get("product_id"),
        "product_name":   meta.get("product_name"),
        "product_price":  meta.get("product_price"),
    }
    if client_uuid:
        row["client_id"] = client_uuid
    if visitor_uuid:
        row["visitor_id"] = visitor_uuid
    # Drop keys whose values are None — keeps the insert payload clean.
    row = {k: v for k, v in row.items() if v is not None}
    try:
        get_supabase().table("cart_events").insert(row).execute()
        logger.debug("cart_events insert OK — action=%s visitor=%s", action, visitor_uuid)
    except Exception as exc:
        logger.warning("write_cart_event failed: %s", exc)



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

    # Last-resort: rescue attribution from attribution_cookies. Triggered when
    # the order has no UTM and the visitor record also has none (typical for
    # guest checkouts via PIX gateways that bypass the Shopify thank-you page).
    rescued_cookie_id: Optional[str] = None
    if client_uuid and not effective_utm_source:
        meta_ev = event.metadata or {}
        cookie = lookup_attribution_cookie(
            client_uuid=client_uuid,
            visitor_cookie_id=meta_ev.get("visitor_cookie_id"),
            email=customer.email if customer else None,
        )
        if cookie:
            effective_utm_source   = cookie.get("utm_source")
            effective_utm_medium   = cookie.get("utm_medium")
            effective_utm_campaign = cookie.get("utm_campaign")
            effective_utm_content  = cookie.get("utm_content")
            rescued_cookie_id      = cookie.get("id")

    meta = event.metadata or {}

    # Predicted LTV — computed here so it's available for both DB persistence
    # and downstream CAPI/Google Ads conversion-value override.
    predicted_ltv_value: Optional[float] = None
    if client_uuid and order.total:
        try:
            from . import ltv_predictor
            predicted_ltv_value = ltv_predictor.predict_ltv(
                client_uuid=client_uuid,
                total_price=float(order.total),
                utm_source=effective_utm_source,
                utm_medium=effective_utm_medium,
            )
        except Exception as exc:
            logger.debug("predict_ltv failed: %s", exc)

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
        "predicted_ltv": predicted_ltv_value,
        # Shipping geo — extracted by adapter, used for dashboard filters
        "shipping_country": meta.get("shipping_country"),
        "shipping_state":   meta.get("shipping_state"),
        "shipping_city":    meta.get("shipping_city"),
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

            # Flag the attribution cookie as consumed so it's not reused on a
            # subsequent order from the same visitor.
            if rescued_cookie_id:
                mark_attribution_cookie_used(rescued_cookie_id, order_uuid)

            # ── Update visitor totals ─────────────────────────────────────────
            if visitor_uuid and order.total:
                _update_visitor_totals(visitor_uuid, float(order.total))

            # ── Persist line items + compute gross profit ────────────────────
            # Uses product_costs lookup. Best-effort: never fails the write_order.
            if client_uuid and order.items:
                try:
                    from . import profitability
                    profitability.persist_items_and_margin(client_uuid, order_uuid, event)
                except Exception as exc:
                    logger.debug("profitability calc failed: %s", exc)

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


# ── attribution_cookies ───────────────────────────────────────────────────────
#
# Persisted record of (visitor_cookie_id → UTMs) captured by the pixel. Acts as
# a server-side backup of the _eta cookie so we can rescue attribution when the
# order webhook arrives without UTMs and the visitor row was created fresh from
# the webhook (no cart-token bridge, no _etv attribute).

def write_attribution_cookie(
    client_uuid: Optional[str],
    visitor_cookie_id: Optional[str],
    event: NormalizedEvent,
) -> None:
    """
    Persist the visitor's attribution context when the pixel sees UTMs or click
    IDs. Idempotent-ish: writes a new row per pageview so we keep history; the
    lookup function below always picks the freshest. Skips when there's no
    actual attribution to record.
    """
    utm = event.utm
    meta = event.metadata or {}
    has_utm = utm and (utm.source or utm.medium or utm.campaign or utm.content or utm.term)
    has_clickid = meta.get("fbclid") or meta.get("gclid")
    if not (has_utm or has_clickid):
        return
    if not (client_uuid and visitor_cookie_id):
        return

    row = {
        "client_id":         client_uuid,
        "visitor_cookie_id": visitor_cookie_id,
        "utm_source":        utm.source   if utm else None,
        "utm_medium":        utm.medium   if utm else None,
        "utm_campaign":      utm.campaign if utm else None,
        "utm_content":       utm.content  if utm else None,
        "utm_term":          utm.term     if utm else None,
        "fbclid":            meta.get("fbclid"),
        "gclid":             meta.get("gclid"),
        "referrer":          event.referrer,
        "landing_url":       event.page_url,
    }
    row = {k: v for k, v in row.items() if v is not None}
    try:
        get_supabase().table("attribution_cookies").insert(row).execute()
    except Exception as exc:
        logger.debug("write_attribution_cookie failed: %s", exc)


def lookup_attribution_cookie(
    client_uuid: str,
    visitor_cookie_id: Optional[str] = None,
    email: Optional[str] = None,
) -> Optional[dict]:
    """
    Find the freshest unused attribution cookie for a buyer. Used by write_order
    as a last-resort fallback when neither the webhook payload nor the visitor
    record carry UTMs. Returns the matching row or None.
    """
    if not client_uuid or not (visitor_cookie_id or email):
        return None
    sb = get_supabase()
    try:
        q = (
            sb.table("attribution_cookies")
            .select("id, utm_source, utm_medium, utm_campaign, utm_content, fbclid, gclid")
            .eq("client_id", client_uuid)
            .is_("used_in_order_id", "null")
            .gte("expires_at", datetime.now(timezone.utc).isoformat())
            .order("created_at", desc=True)
            .limit(1)
        )
        if visitor_cookie_id:
            q = q.eq("visitor_cookie_id", visitor_cookie_id)
        elif email:
            q = q.eq("email", email.strip().lower())
        rows = q.execute().data or []
    except Exception as exc:
        logger.debug("lookup_attribution_cookie failed: %s", exc)
        return None
    return rows[0] if rows else None


def mark_attribution_cookie_used(cookie_id: str, order_uuid: str) -> None:
    """Flag an attribution_cookies row as consumed by an order so it's not re-used."""
    if not (cookie_id and order_uuid):
        return
    try:
        get_supabase().table("attribution_cookies").update({
            "used_in_order_id": order_uuid,
            "used_at":          datetime.now(timezone.utc).isoformat(),
        }).eq("id", cookie_id).execute()
    except Exception as exc:
        logger.debug("mark_attribution_cookie_used failed: %s", exc)


# ── refunds ───────────────────────────────────────────────────────────────────

def write_refund(
    client_uuid: Optional[str],
    event: NormalizedEvent,
) -> Optional[str]:
    """
    Persist a refund into the refunds table.

    The adapter packs the refund this way:
      event.order.id     = original platform order_id (NOT the refund_id)
      event.order.total  = refund amount (positive number)
      event.metadata.refund_id = the platform's refund_id

    Returns the order_uuid we resolved (so the caller can pass it to CAPI senders).
    """
    if not client_uuid or not event.order or not event.order.id:
        return None
    platform_order_id = str(event.order.id)
    refund_amount     = float(event.order.total or 0)
    if refund_amount <= 0:
        logger.debug("write_refund: skipping zero/negative refund for order %s", platform_order_id)
        return None

    refund_id = (event.metadata or {}).get("refund_id")

    try:
        ord_row = (
            get_supabase().table("orders")
            .select("id, total_price")
            .eq("client_id", client_uuid)
            .eq("platform_order_id", platform_order_id)
            .limit(1)
            .execute()
        )
    except Exception as exc:
        logger.warning("write_refund: order lookup failed for %s: %s", platform_order_id, exc)
        return None

    if not (ord_row and ord_row.data):
        logger.info("write_refund: original order %s not found — refund dropped", platform_order_id)
        return None
    order = ord_row.data[0]
    order_uuid    = order["id"]
    original_total = float(order.get("total_price") or 0)
    is_partial    = original_total > 0 and refund_amount < original_total

    row = {
        "client_id":          client_uuid,
        "order_id":           order_uuid,
        "platform_refund_id": refund_id,
        "amount":             refund_amount,
        "currency":           event.order.currency or "BRL",
        "is_partial":         is_partial,
    }
    try:
        get_supabase().table("refunds").insert(row).execute()
        logger.info("refund persisted — order=%s amount=%.2f partial=%s",
                    platform_order_id, refund_amount, is_partial)
    except Exception as exc:
        logger.warning("write_refund insert failed for %s: %s", platform_order_id, exc)

    return order_uuid


def mark_refund_capi_sent(order_uuid: str, refund_id: Optional[str]) -> None:
    """Mark a refund row as CAPI-sent once Meta acknowledges the negative-value Purchase."""
    if not order_uuid:
        return
    try:
        q = (
            get_supabase().table("refunds")
            .update({
                "capi_sent":    True,
                "capi_sent_at": datetime.now(timezone.utc).isoformat(),
            })
            .eq("order_id", order_uuid)
        )
        if refund_id:
            q = q.eq("platform_refund_id", refund_id)
        q.execute()
    except Exception as exc:
        logger.debug("mark_refund_capi_sent failed: %s", exc)


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

# Retargeting score: intent to buy without completing purchase
# High score = prime candidate for retargeting campaign
_RETARGETING_SCORE_MAP: dict[str, int] = {
    "product.viewed":   5,
    "cart.created":     20,
    "cart.updated":     20,
    "checkout.started": 35,
}


def update_lead_score(visitor_uuid: str, event_type: EventType) -> None:
    """Increment visitor lead_score and retargeting_score based on engagement event."""
    lead_pts = _LEAD_SCORE_MAP.get(event_type.value, 0)
    retarg_pts = _RETARGETING_SCORE_MAP.get(event_type.value, 0)
    if not (lead_pts or retarg_pts) or not visitor_uuid:
        return
    try:
        sb = get_supabase()
        existing = (
            sb.table("visitors")
            .select("lead_score, retargeting_score")
            .eq("id", visitor_uuid)
            .limit(1)
            .execute()
        )
        if not (existing and existing.data):
            return
        row = existing.data[0]
        update: dict = {}
        if lead_pts:
            update["lead_score"] = (row.get("lead_score") or 0) + lead_pts
        if retarg_pts:
            update["retargeting_score"] = min((row.get("retargeting_score") or 0) + retarg_pts, 100)
        # Track last cart interaction for audience segmentation
        if event_type.value in ("cart.created", "cart.updated"):
            update["last_cart_at"] = datetime.now(timezone.utc).isoformat()
        sb.table("visitors").update(update).eq("id", visitor_uuid).execute()
        logger.debug("scores updated for visitor %s: lead+%d retarg+%d",
                     visitor_uuid, lead_pts, retarg_pts)
    except Exception as exc:
        logger.warning("update_lead_score failed: %s", exc)


def reset_retargeting_score(visitor_uuid: str) -> None:
    """Reset retargeting_score to 0 after purchase — visitor converted."""
    if not visitor_uuid:
        return
    try:
        get_supabase().table("visitors").update({
            "retargeting_score": 0,
            "last_purchase_at": datetime.now(timezone.utc).isoformat(),
        }).eq("id", visitor_uuid).execute()
    except Exception as exc:
        logger.warning("reset_retargeting_score failed: %s", exc)


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
