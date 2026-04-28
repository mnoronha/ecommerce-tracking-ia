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
from . import meta_capi, ga4
from ..models.events import NormalizedEvent, EventType, OrderData, CustomerData

logger = logging.getLogger(__name__)

_RETRY_WINDOW_HOURS = 24
_MAX_RETRIES        = 5
_BATCH_LIMIT        = 50


def _build_event(order: dict, visitor: Optional[dict]) -> NormalizedEvent:
    """Reconstruct a minimal NormalizedEvent from persisted order + visitor."""
    customer = CustomerData(
        email=order.get("email"),
        phone=order.get("phone"),
    )
    order_data = OrderData(
        id=str(order["platform_order_id"]),
        number=str(order.get("platform_order_number") or order["platform_order_id"]),
        status=order.get("financial_status"),
        total=float(order.get("total_price") or 0),
        currency=order.get("currency") or "BRL",
    )
    metadata = {}
    if visitor:
        if visitor.get("fbp"): metadata["fbp"] = visitor["fbp"]
        if visitor.get("fbc"): metadata["fbc"] = visitor["fbc"]
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
        result = (
            sb.table("orders")
            .select(
                "id, client_id, platform_order_id, platform_order_number, "
                "platform_source, email, phone, total_price, currency, "
                "financial_status, capi_retry_count, visitor_id"
            )
            .eq("capi_sent", False)
            .gte("created_at", cutoff)
            .lt("capi_retry_count", _MAX_RETRIES)
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
            client_row = (
                sb.table("clients")
                .select("pixel_id, meta_pixel_id, meta_access_token, ga4_measurement_id, ga4_api_secret")
                .eq("id", order["client_id"])
                .maybe_single()
                .execute()
            )
            if not (client_row and client_row.data):
                continue
            c = client_row.data

            # ── Fetch visitor for browser identifiers ─────────────────────────
            visitor = None
            if order.get("visitor_id"):
                v_row = (
                    sb.table("visitors")
                    .select("fbp, fbc, ga_client_id")
                    .eq("id", order["visitor_id"])
                    .maybe_single()
                    .execute()
                )
                if v_row and v_row.data:
                    visitor = v_row.data

            event = _build_event(order, visitor)

            # ── Try Meta CAPI ─────────────────────────────────────────────────
            meta_ok = False
            meta_err = None
            if c.get("meta_pixel_id") and c.get("meta_access_token"):
                try:
                    meta_ok = meta_capi.send_purchase(
                        pixel_id=c["meta_pixel_id"],
                        access_token=c["meta_access_token"],
                        event=event,
                        test_event_code=settings.META_TEST_EVENT_CODE or None,
                    )
                except Exception as exc:
                    meta_err = str(exc)[:200]

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
            update: dict = {
                "capi_retry_count": (order.get("capi_retry_count") or 0) + 1,
            }
            if meta_ok:
                update["capi_sent"]    = True
                update["capi_sent_at"] = datetime.now(timezone.utc).isoformat()
                update["capi_last_error"] = None
            elif meta_err:
                update["capi_last_error"] = meta_err

            sb.table("orders").update(update).eq("id", order["id"]).execute()

            if meta_ok:
                logger.info("capi_retry: order %s recovered (attempt %d)",
                            order["platform_order_id"], update["capi_retry_count"])
        except Exception as exc:
            logger.warning("capi_retry: order %s failed: %s", order.get("id"), exc)
            try:
                sb.table("orders").update({
                    "capi_retry_count": (order.get("capi_retry_count") or 0) + 1,
                    "capi_last_error":  str(exc)[:200],
                }).eq("id", order["id"]).execute()
            except Exception:
                pass
