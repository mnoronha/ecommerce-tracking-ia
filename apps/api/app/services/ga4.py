"""
Google Analytics 4 — Measurement Protocol server-side sender.

Sends purchase events server-side for reliable conversion tracking,
bypassing ad blockers and cookie consent restrictions.

Docs: https://developers.google.com/analytics/devguides/collection/protocol/ga4
"""

import logging
import time
from typing import Optional

import httpx

from ..models.events import NormalizedEvent

logger = logging.getLogger(__name__)

_GA4_MP_URL = "https://www.google-analytics.com/mp/collect"
_GA4_DEBUG_URL = "https://www.google-analytics.com/debug/mp/collect"

_GA4_PIXEL_EVENT_MAP: dict[str, str] = {
    "product.viewed":   "view_item",
    "cart.created":     "add_to_cart",
    "cart.updated":     "add_to_cart",
    "checkout.started": "begin_checkout",
}


# ── Sender ────────────────────────────────────────────────────────────────────

def send_purchase(
    measurement_id: str,
    api_secret: str,
    event: NormalizedEvent,
    ga_client_id: Optional[str] = None,
    debug: bool = False,
) -> bool:
    """
    Send a purchase event to GA4 via Measurement Protocol.

    Args:
        measurement_id: GA4 Measurement ID (e.g. "G-XXXXXXXXXX").
        api_secret:     API secret from GA4 Admin > Data Streams > Measurement Protocol.
        event:          Normalized order event (must have event.order set).
        ga_client_id:   GA client_id from the browser cookie (_ga). Falls back to a
                        synthetic ID derived from the order so GA4 doesn't reject it.
        debug:          If True, uses the GA4 debug endpoint (validates but doesn't record).

    Returns True on success, False on any error (never raises).
    """
    if not measurement_id or not api_secret:
        logger.debug("ga4: skipped — no measurement_id or api_secret")
        return False

    order = event.order
    if not order:
        logger.debug("ga4: skipped — no order data in event")
        return False

    # GA4 requires a client_id; prefer the real browser _ga value for session linkage
    effective_client_id = (
        ga_client_id
        or (event.metadata or {}).get("ga_client_id")
        or f"server.{str(order.id).replace('-', '')[:16]}"
    )

    # Build items array from order line items
    items = []
    if order.items:
        for item in order.items:
            items.append({
                "item_id":   str(item.product_id or item.id or ""),
                "item_name": str(item.name or ""),
                "price":     float(item.price or 0),
                "quantity":  int(item.quantity or 1),
            })

    payload = {
        "client_id":       effective_client_id,
        "timestamp_micros": int(time.time() * 1_000_000),
        "events": [
            {
                "name": "purchase",
                "params": {
                    "transaction_id": str(order.id),
                    "value":          float(order.total or 0),
                    "currency":       (order.currency or "BRL").upper(),
                    "items":          items,
                },
            }
        ],
    }

    url = _GA4_DEBUG_URL if debug else _GA4_MP_URL

    params = {"measurement_id": measurement_id, "api_secret": api_secret}
    max_attempts = 1 if debug else 3
    delay = 1.0

    for attempt in range(max_attempts):
        try:
            resp = httpx.post(url, params=params, json=payload, timeout=10.0)
            if resp.status_code in (200, 204):
                if debug:
                    validation = resp.json().get("validationMessages", [])
                    if validation:
                        logger.warning("ga4 debug validation: %s", validation)
                    else:
                        logger.info("ga4 debug: payload valid for order=%s", order.id)
                else:
                    logger.info("ga4 purchase sent — order=%s", order.id)
                return True
            if 400 <= resp.status_code < 500:
                logger.warning("ga4 HTTP %s (no retry): %s", resp.status_code, resp.text[:300])
                return False
            logger.warning("ga4 HTTP %s attempt %d/%d", resp.status_code, attempt + 1, max_attempts)
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            logger.warning("ga4 network error attempt %d/%d: %s", attempt + 1, max_attempts, exc)
        except Exception as exc:
            logger.error("ga4 exception for order=%s: %s", order.id, exc)
            return False
        if attempt < max_attempts - 1:
            time.sleep(delay * (2 ** attempt))

    logger.error("ga4 purchase failed after %d attempts for order=%s", max_attempts, order.id)
    return False


def send_refund(
    measurement_id: str,
    api_secret:     str,
    order_id:       str,
    refund_amount:  float,
    currency:       str,
    ga_client_id:   Optional[str] = None,
) -> bool:
    """
    Send a `refund` event to GA4 via Measurement Protocol.

    transaction_id must match the original purchase transaction_id for GA4 to
    correctly subtract the refunded amount from the purchase reports.
    """
    if not measurement_id or not api_secret or not order_id:
        return False

    effective_client_id = ga_client_id or f"server.{str(order_id).replace('-', '')[:16]}"

    payload = {
        "client_id":        effective_client_id,
        "timestamp_micros": int(time.time() * 1_000_000),
        "events": [{
            "name": "refund",
            "params": {
                "transaction_id": str(order_id),
                "value":          float(refund_amount),
                "currency":       (currency or "BRL").upper(),
            },
        }],
    }

    try:
        resp = httpx.post(
            _GA4_MP_URL,
            params={"measurement_id": measurement_id, "api_secret": api_secret},
            json=payload,
            timeout=10.0,
        )
        if resp.status_code in (200, 204):
            logger.info("ga4 refund sent — order=%s value=%.2f", order_id, refund_amount)
            return True
        logger.warning("ga4 refund HTTP %s: %s", resp.status_code, resp.text[:200])
    except Exception as exc:
        logger.warning("ga4 send_refund exception: %s", exc)
    return False


def send_pixel_event(
    measurement_id: str,
    api_secret: str,
    event: NormalizedEvent,
    ga_client_id: Optional[str] = None,
) -> bool:
    """
    Send mid-funnel pixel events (view_item, add_to_cart, begin_checkout) to GA4
    via Measurement Protocol. Complements browser-side gtag for server reliability.
    """
    if not measurement_id or not api_secret:
        return False

    ga4_event_name = _GA4_PIXEL_EVENT_MAP.get(event.event_type.value)
    if not ga4_event_name:
        return False

    effective_client_id = (
        ga_client_id
        or (event.metadata or {}).get("ga_client_id")
        or f"server.{event.visitor_id or event.event_id[:16]}"
    )

    params: dict = {}
    meta = event.metadata or {}

    if ga4_event_name == "view_item":
        item = {
            "item_id":       str(meta.get("product_id", "")),
            "item_name":     str(meta.get("product_name", "")),
            "price":         float(meta.get("product_price", 0)),
            "item_category": str(meta.get("product_category", "")),
        }
        params = {"items": [item], "value": float(meta.get("product_price", 0)), "currency": "BRL"}

    elif ga4_event_name == "add_to_cart":
        item = {
            "item_id":   str(meta.get("product_id", "")),
            "item_name": str(meta.get("product_name", "")),
            "price":     float(meta.get("product_price", 0)),
            "quantity":  int(meta.get("product_quantity", 1)),
        }
        params = {"items": [item], "value": float(meta.get("product_price", 0)), "currency": "BRL"}

    elif ga4_event_name == "begin_checkout":
        params = {"currency": "BRL"}

    payload = {
        "client_id":        effective_client_id,
        "timestamp_micros": int(time.time() * 1_000_000),
        "events":           [{"name": ga4_event_name, "params": params}],
    }

    delay = 1.0
    for attempt in range(3):
        try:
            resp = httpx.post(
                _GA4_MP_URL,
                params={"measurement_id": measurement_id, "api_secret": api_secret},
                json=payload,
                timeout=10.0,
            )
            if resp.status_code in (200, 204):
                logger.debug("ga4 %s sent — visitor=%s", ga4_event_name, event.visitor_id)
                return True
            if 400 <= resp.status_code < 500:
                return False
            logger.warning("ga4 pixel %s HTTP %s attempt %d/3", ga4_event_name, resp.status_code, attempt + 1)
        except (httpx.TimeoutException, httpx.NetworkError):
            logger.warning("ga4 pixel %s network error attempt %d/3", ga4_event_name, attempt + 1)
        except Exception as exc:
            logger.warning("ga4 send_pixel_event exception: %s", exc)
            return False
        if attempt < 2:
            time.sleep(delay * (2 ** attempt))
    return False
