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

    # GA4 requires a client_id; use a synthetic one if not provided
    effective_client_id = ga_client_id or f"server.{str(order.id).replace('-', '')[:16]}"

    # Build items array from line_items metadata if available
    items = []
    if event.metadata and isinstance(event.metadata.get("line_items"), list):
        for item in event.metadata["line_items"]:
            items.append({
                "item_id":   str(item.get("product_id", "")),
                "item_name": str(item.get("name", item.get("title", ""))),
                "price":     float(item.get("price", 0)),
                "quantity":  int(item.get("quantity", 1)),
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

    try:
        resp = httpx.post(
            url,
            params={"measurement_id": measurement_id, "api_secret": api_secret},
            json=payload,
            timeout=10.0,
        )
        # GA4 MP returns 204 on success; debug endpoint returns 200 with validation
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
        else:
            logger.warning(
                "ga4 error %s for order=%s: %s",
                resp.status_code,
                order.id,
                resp.text[:300],
            )
            return False
    except httpx.TimeoutException:
        logger.warning("ga4 timeout for order=%s", order.id)
        return False
    except Exception as exc:
        logger.error("ga4 exception for order=%s: %s", order.id, exc)
        return False
