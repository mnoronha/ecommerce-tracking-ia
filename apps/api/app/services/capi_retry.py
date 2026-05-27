"""
CAPI retry — re-tries Meta CAPI Purchase events that failed in the webhook
synchronous path.

When `_dispatch_purchase_capi` fails (network blip, rate limit, transient
Meta API error), `capi_sent` stays false on the order. This job picks those
up and re-sends. Caps at 5 retries to avoid wasting budget on permanently
broken records (revoked tokens, deleted pixels).

Runs every 30 minutes via APScheduler.
"""

import hashlib
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

from ..database import get_supabase
from ..config import settings
from . import meta_capi, ga4, tiktok_capi
from ..models.events import NormalizedEvent, EventType, OrderData, CustomerData, Address

logger = logging.getLogger(__name__)

# Retry window covers Friday-night incidents where the on-call sees the
# breakage only Monday morning. 72h gives a real shot at recovery without
# resending events Meta would dedupe anyway.
_RETRY_WINDOW_HOURS = 72
_MAX_RETRIES        = 5
_BATCH_LIMIT        = 50

# Sentinel prefix written to capi_last_error when a row exhausts retries.
# Lets the health alert filter "really dead" rows (`capi_dead:%`) apart from
# rows still in the retry queue. Keeps the dead state in an existing column
# instead of adding a new boolean.
_DEAD_PREFIX        = "capi_dead:"


def _build_event(order: dict, visitor: Optional[dict]) -> NormalizedEvent:
    """Reconstruct a rich NormalizedEvent from persisted order + visitor data."""
    # Build address from stored order fields (shipped from migration 015+)
    addr: Optional[Address] = None
    if any(order.get(f) for f in ("shipping_country", "shipping_state", "shipping_city", "zip_code")):
        addr = Address(
            country=order.get("shipping_country"),
            state=order.get("shipping_state"),
            city=order.get("shipping_city"),
            zip_code=order.get("zip_code"),
        )

    customer = CustomerData(
        email=order.get("email"),
        phone=order.get("phone"),
        first_name=order.get("first_name"),
        last_name=order.get("last_name"),
        id=order.get("platform_customer_id"),
        address=addr,
    )

    order_data = OrderData(
        id=str(order["platform_order_id"]),
        number=str(order.get("platform_order_number") or order["platform_order_id"]),
        status=order.get("financial_status"),
        total=float(order.get("total_price") or 0),
        currency=order.get("currency") or "BRL",
    )

    metadata: dict = {}
    # Browser identifiers — prefer stored-on-order (from webhook); fall back to visitor
    if order.get("browser_ip"):   metadata["ip"]          = order["browser_ip"]
    if order.get("browser_ua"):   metadata["user_agent"]  = order["browser_ua"]
    if visitor:
        if visitor.get("fbp") and not metadata.get("fbp"): metadata["fbp"] = visitor["fbp"]
        if visitor.get("fbc") and not metadata.get("fbc"): metadata["fbc"] = visitor["fbc"]

    return NormalizedEvent(
        event_id=f"retry_{order['id']}",
        event_type=EventType.ORDER_PAID,
        platform=order.get("platform_source") or "shopify",
        client_id=str(order["client_id"]),
        timestamp=datetime.now(timezone.utc),
        customer=customer,
        order=order_data,
        metadata=metadata,
    )


def retry_failed_capi() -> None:
    """
    Scheduler entry point. Picks up orders with capi_sent=false in the last
    24h, re-sends Purchase to Meta CAPI + GA4, marks success. Increments
    capi_retry_count to cap attempts.
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=_RETRY_WINDOW_HOURS)).isoformat()
    sb = get_supabase()

    try:
        # Skip zero-value orders (drafts / abandoned) — Meta rejects them anyway
        # Only retry orders that are actually paid — pending/expired/refunded
        # must NEVER be sent to conversion APIs (caused phantom conversions).
        result = (
            sb.table("orders")
            .select(
                "id, client_id, platform_order_id, platform_order_number, "
                "platform_source, email, phone, first_name, last_name, "
                "total_price, currency, financial_status, capi_retry_count, "
                "visitor_id, predicted_ltv, "
                "shipping_country, shipping_state, shipping_city, zip_code, "
                "browser_ip, browser_ua"
            )
            .eq("capi_sent", False)
            .eq("financial_status", "paid")
            .gt("total_price", 0)
            .gte("created_at", cutoff)
            .lt("capi_retry_count", _MAX_RETRIES)
            # Never retry deliberately-skipped orders (offline POS / draft /
            # zero-value). Their capi_last_error starts with "skipped:" and the
            # reason is permanent — retrying would re-attempt forever or, worse,
            # send a balcony sale to Meta as an ad conversion.
            .not_.ilike("capi_last_error", "skipped:%")
            .not_.ilike("capi_last_error", "capi_dead:%")
            .order("created_at")
            .limit(_BATCH_LIMIT)
            .execute()
        )
    except Exception as exc:
        logger.error("capi_retry: query failed: %s", exc)
        return

    failed = result.data or []
    if not failed:
        return

    logger.info("capi_retry: processing %d failed orders", len(failed))

    for order in failed:
        try:
            # ── Fetch credentials for this client ─────────────────────────────
            # NOTE: use .limit(1) instead of .maybe_single() — the latter raises
            # "Missing response / code 204" when the row is not found, which
            # gets persisted to capi_last_error and pollutes diagnostics.
            client_row = (
                sb.table("clients")
                .select("pixel_id, meta_pixel_id, meta_access_token, ga4_measurement_id, ga4_api_secret, value_based_bidding")
                .eq("id", order["client_id"])
                .limit(1)
                .execute()
            )
            if not (client_row and client_row.data):
                continue
            c = client_row.data[0]

            # ── Fetch visitor for browser identifiers ─────────────────────────
            visitor = None
            if order.get("visitor_id"):
                v_row = (
                    sb.table("visitors")
                    .select("fbp, fbc, ga_client_id, ttclid")
                    .eq("id", order["visitor_id"])
                    .limit(1)
                    .execute()
                )
                if v_row and v_row.data:
                    visitor = v_row.data[0]

            event = _build_event(order, visitor)

            # ── Try Meta CAPI ─────────────────────────────────────────────────
            meta_ok = False
            meta_err = None
            # Value-based bidding: pass predicted_ltv as conversion value when set.
            bid_override: Optional[float] = None
            if c.get("value_based_bidding"):
                val = order.get("predicted_ltv")
                if val is not None:
                    bid_override = float(val)
            if c.get("meta_pixel_id") and c.get("meta_access_token"):
                try:
                    meta_ok, meta_err = meta_capi.send_purchase(
                        pixel_id=c["meta_pixel_id"],
                        access_token=c["meta_access_token"],
                        event=event,
                        test_event_code=settings.META_TEST_EVENT_CODE or None,
                        value_override=bid_override,
                    )
                except Exception as exc:
                    meta_err = f"{type(exc).__name__}: {str(exc)[:200]}"
            else:
                meta_err = "client missing meta_pixel_id or meta_access_token"

            # ── Try GA4 (best-effort, doesn't gate success) ──────────────────
            if c.get("ga4_measurement_id") and c.get("ga4_api_secret"):
                try:
                    ga4.send_purchase(
                        measurement_id=c["ga4_measurement_id"],
                        api_secret=c["ga4_api_secret"],
                        event=event,
                        ga_client_id=(visitor or {}).get("ga_client_id"),
                    )
                except Exception:
                    pass

            # ── Update order based on Meta result ─────────────────────────────
            # Clear stale capi_last_error on success — keeping it visible
            # after a successful retry was confusing (looked like the order
            # had failed when it actually recovered). The successful retry
            # is recorded via capi_sent + capi_retry_count.
            next_count = (order.get("capi_retry_count") or 0) + 1
            update: dict = {"capi_retry_count": next_count}
            if meta_ok:
                update["capi_sent"]       = True
                update["capi_sent_at"]    = datetime.now(timezone.utc).isoformat()
                update["capi_last_error"] = None
            elif meta_err:
                # Last attempt and still failing — mark as dead so the health
                # alert and ops dashboards can distinguish it from rows still
                # in the retry queue.
                if next_count >= _MAX_RETRIES:
                    update["capi_last_error"] = f"{_DEAD_PREFIX} {meta_err}"[:500]
                    logger.warning(
                        "capi_retry: order %s exhausted retries — marked capi_dead: %s",
                        order["platform_order_id"], meta_err,
                    )
                else:
                    update["capi_last_error"] = meta_err

            sb.table("orders").update(update).eq("id", order["id"]).execute()

            if meta_ok:
                logger.info("capi_retry: order %s recovered (attempt %d)",
                            order["platform_order_id"], next_count)
        except Exception as exc:
            logger.warning("capi_retry: order %s failed: %s", order.get("id"), exc)
            try:
                sb.table("orders").update({
                    "capi_retry_count": (order.get("capi_retry_count") or 0) + 1,
                    "capi_last_error":  str(exc)[:200],
                }).eq("id", order["id"]).execute()
            except Exception:
                pass


def retry_failed_tiktok() -> None:
    """
    Retry TikTok Events API Purchase events that failed at webhook time.
    Mirrors retry_failed_capi but for the tiktok_sent / tiktok_last_error columns.
    Runs every 30 minutes via APScheduler.
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=_RETRY_WINDOW_HOURS)).isoformat()
    sb = get_supabase()

    try:
        result = (
            sb.table("orders")
            .select(
                "id, client_id, platform_order_id, platform_order_number, "
                "platform_source, email, phone, first_name, last_name, "
                "total_price, currency, financial_status, tiktok_retry_count, "
                "visitor_id, predicted_ltv, browser_ip, browser_ua"
            )
            .eq("tiktok_sent", False)
            .eq("financial_status", "paid")
            .gt("total_price", 0)
            .gte("created_at", cutoff)
            .lt("tiktok_retry_count", _MAX_RETRIES)
            .not_.ilike("tiktok_last_error", "skipped:%")
            .not_.ilike("tiktok_last_error", "capi_dead:%")
            .order("created_at")
            .limit(_BATCH_LIMIT)
            .execute()
        )
    except Exception as exc:
        logger.error("tiktok_retry: query failed: %s", exc)
        return

    failed = result.data or []
    if not failed:
        return

    logger.info("tiktok_retry: processing %d failed orders", len(failed))

    for order in failed:
        try:
            client_row = (
                sb.table("clients")
                .select("pixel_id, tiktok_pixel_id, tiktok_access_token, value_based_bidding")
                .eq("id", order["client_id"])
                .limit(1)
                .execute()
            )
            if not (client_row and client_row.data):
                continue
            c = client_row.data[0]
            if not (c.get("tiktok_pixel_id") and c.get("tiktok_access_token")):
                continue

            visitor = None
            if order.get("visitor_id"):
                v_row = (
                    sb.table("visitors")
                    .select("ttclid")
                    .eq("id", order["visitor_id"])
                    .limit(1)
                    .execute()
                )
                if v_row and v_row.data:
                    visitor = v_row.data[0]

            bid_override: Optional[float] = None
            if c.get("value_based_bidding"):
                val = order.get("predicted_ltv")
                if val is not None:
                    bid_override = float(val)

            event = _build_event(order, visitor)

            ok, err = tiktok_capi.send_purchase(
                pixel_code=c["tiktok_pixel_id"],
                access_token=c["tiktok_access_token"],
                event=event,
                ttclid=(visitor or {}).get("ttclid"),
                value_override=bid_override,
            )

            next_count = (order.get("tiktok_retry_count") or 0) + 1
            update: dict = {"tiktok_retry_count": next_count}
            if ok:
                update["tiktok_sent"]       = True
                update["tiktok_sent_at"]    = datetime.now(timezone.utc).isoformat()
                update["tiktok_last_error"] = None
                logger.info("tiktok_retry: order %s recovered (attempt %d)",
                            order["platform_order_id"], next_count)
            else:
                if next_count >= _MAX_RETRIES:
                    update["tiktok_last_error"] = f"{_DEAD_PREFIX} {err}"[:500]
                else:
                    update["tiktok_last_error"] = (err or "unknown")[:500]

            sb.table("orders").update(update).eq("id", order["id"]).execute()

        except Exception as exc:
            logger.warning("tiktok_retry: order %s failed: %s", order.get("id"), exc)
            try:
                sb.table("orders").update({
                    "tiktok_retry_count": (order.get("tiktok_retry_count") or 0) + 1,
                    "tiktok_last_error":  str(exc)[:200],
                }).eq("id", order["id"]).execute()
            except Exception:
                pass
