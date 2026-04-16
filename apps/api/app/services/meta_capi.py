"""
Meta Conversions API (CAPI) — server-side purchase event sender.

Sends server-side Purchase events to Meta, complementing the browser pixel.
This improves attribution reliability and bypasses ad blockers / iOS restrictions.

Docs: https://developers.facebook.com/docs/marketing-api/conversions-api
"""

import hashlib
import logging
import time
from typing import Optional

import httpx

from ..models.events import NormalizedEvent

logger = logging.getLogger(__name__)

_CAPI_URL = "https://graph.facebook.com/v19.0/{pixel_id}/events"


# ── PII hashing ───────────────────────────────────────────────────────────────

def _sha256(value: Optional[str]) -> Optional[str]:
    """Normalize and SHA-256 hash a PII value (required by Meta CAPI spec)."""
    if not value:
        return None
    return hashlib.sha256(value.strip().lower().encode("utf-8")).hexdigest()


# ── Sender ────────────────────────────────────────────────────────────────────

def send_purchase(
    pixel_id: str,
    access_token: str,
    event: NormalizedEvent,
    test_event_code: Optional[str] = None,
) -> bool:
    """
    Send a Purchase event to Meta CAPI.

    Args:
        pixel_id:        Meta Pixel ID (from Business Manager).
        access_token:    System User access token with ads_management permission.
        event:           Normalized order event (must have event.order set).
        test_event_code: Optional test code to verify in Events Manager.

    Returns True on success, False on any error (never raises).
    """
    if not pixel_id or not access_token:
        logger.debug("meta_capi: skipped — no pixel_id or access_token")
        return False

    order = event.order
    if not order:
        logger.debug("meta_capi: skipped — no order data in event")
        return False

    customer = event.customer

    # Build user_data with hashed PII
    user_data: dict = {}
    if customer:
        if customer.email:
            user_data["em"] = [_sha256(customer.email)]
        if customer.phone:
            # Strip non-digits, then hash
            phone_clean = "".join(c for c in (customer.phone or "") if c.isdigit())
            if phone_clean:
                user_data["ph"] = [_sha256(phone_clean)]

    # Build the event payload
    capi_event: dict = {
        "event_name":    "Purchase",
        "event_time":    int(time.time()),
        "action_source": "website",
        "event_id":      event.event_id,   # deduplication key vs. browser pixel
        "user_data":     user_data,
        "custom_data": {
            "currency": (order.currency or "BRL").upper(),
            "value":    float(order.total or 0),
            "order_id": str(order.id),
        },
    }

    payload: dict = {
        "data":         [capi_event],
        "access_token": access_token,
    }
    if test_event_code:
        payload["test_event_code"] = test_event_code

    try:
        resp = httpx.post(
            _CAPI_URL.format(pixel_id=pixel_id),
            json=payload,
            timeout=10.0,
        )
        if resp.status_code == 200:
            result = resp.json()
            logger.info(
                "meta_capi Purchase sent — order=%s events_received=%s",
                order.id,
                result.get("events_received"),
            )
            return True
        else:
            logger.warning(
                "meta_capi error %s for order=%s: %s",
                resp.status_code,
                order.id,
                resp.text[:300],
            )
            return False
    except httpx.TimeoutException:
        logger.warning("meta_capi timeout for order=%s", order.id)
        return False
    except Exception as exc:
        logger.error("meta_capi exception for order=%s: %s", order.id, exc)
        return False
