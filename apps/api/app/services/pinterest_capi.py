"""
Pinterest Conversions API — server-side event sender.

Sends server-side funnel events to Pinterest, complementing the Pinterest
tag (browser pixel). Improves attribution and bypasses iOS / ad-blocker
restrictions, mirroring what we do for Meta and TikTok.

Events sent (Pinterest event_name in parens):
  ViewContent       — on product.viewed   (page_visit)
  AddToCart         — on cart.created     (add_to_cart)
  InitiateCheckout  — on checkouts/create (checkout)
  Purchase          — on order.paid       (checkout with explicit value)

Identity is hashed and sent in `user_data`:
  em (email), ph (phone E.164), fn, ln, ge, db, country, ct, st, zp,
  external_id, client_ip_address, client_user_agent, click_id (epik).

Docs: https://developers.pinterest.com/docs/conversions/conversion-management/
"""

import hashlib
import logging
import time
from typing import Optional

import httpx

from ..models.events import NormalizedEvent

logger = logging.getLogger(__name__)

_PINTEREST_API = "https://api.pinterest.com/v5/ad_accounts/{ad_account_id}/events"

# Maps our internal event types to Pinterest's enum values
_PIN_EVENT_MAP = {
    "product.viewed":    "page_visit",
    "cart.created":      "add_to_cart",
    "cart.updated":      "add_to_cart",
    "checkout.started":  "checkout",
    "order.paid":        "checkout",
}


def _sha256(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    return hashlib.sha256(value.strip().lower().encode("utf-8")).hexdigest()


def _normalize_phone_e164(phone: Optional[str]) -> Optional[str]:
    if not phone:
        return None
    raw = phone.strip()
    digits = "".join(c for c in raw if c.isdigit())
    if not digits:
        return None
    if raw.startswith("+"):
        return "+" + digits
    if len(digits) in (10, 11):
        return "+55" + digits
    return "+" + digits


def _build_user_data(event: NormalizedEvent) -> dict:
    """Build hashed identity payload. Every match key Pinterest accepts."""
    ud: dict = {}
    cust = event.customer
    meta = event.metadata or {}

    if cust:
        if cust.email:
            ud["em"] = [_sha256(cust.email)]
        ph = _normalize_phone_e164(cust.phone)
        if ph:
            ud["ph"] = [_sha256(ph)]
        if cust.first_name:
            ud["fn"] = [_sha256(cust.first_name)]
        if cust.last_name:
            ud["ln"] = [_sha256(cust.last_name)]
        if cust.id:
            ud["external_id"] = [_sha256(str(cust.id))]
        if cust.address:
            addr = cust.address
            if addr.country:
                ud["country"] = [_sha256(addr.country.lower())]
            if addr.city:
                ud["ct"] = [_sha256(addr.city.lower())]
            if addr.state:
                ud["st"] = [_sha256(addr.state.lower())]
            if addr.zip_code:
                ud["zp"] = [_sha256(addr.zip_code.replace(" ", ""))]

    # Browser identifiers (NOT hashed — Pinterest spec)
    if meta.get("ip"):
        ud["client_ip_address"] = meta["ip"]
    if meta.get("user_agent"):
        ud["client_user_agent"] = meta["user_agent"]
    # `epik` is Pinterest's click ID, propagated from URL `?epik=`
    if meta.get("epik"):
        ud["click_id"] = meta["epik"]

    return ud


def _price_float(val) -> float:
    """Convert any price-like value to a clean float rounded to 2 decimal places.
    Pinterest v5 expects numeric types for value and item_price fields."""
    try:
        return round(float(val or 0), 2)
    except (TypeError, ValueError):
        return 0.0


def _build_custom_data(event: NormalizedEvent, pin_event_name: str) -> dict:
    """Currency, value, items — what Pinterest needs for optimization."""
    meta = event.metadata or {}
    custom: dict = {"currency": "BRL"}

    if pin_event_name == "checkout" and event.order:
        # Pinterest v5 API requires value as a number (float) when contents is present.
        # Sending as string causes 400 "not of type 'string'" when contents array is non-empty.
        custom["value"] = _price_float(event.order.total)
        custom["order_id"] = str(event.order.id)
        if event.order.items:
            custom["num_items"] = sum(int(i.quantity or 1) for i in event.order.items)
            custom["content_ids"] = [str(i.product_id) for i in event.order.items if i.product_id]
            custom["contents"] = [
                {
                    "id":         str(i.product_id or ""),
                    "quantity":   int(i.quantity or 1),
                    "item_price": _price_float(i.price),
                }
                for i in event.order.items
            ]
        return custom

    # Mid-funnel (page_visit / add_to_cart) — pull from metadata
    if meta.get("product_id"):
        pid = str(meta["product_id"])
        custom["content_ids"] = [pid]
        custom["contents"] = [{
            "id":         pid,
            "quantity":   int(meta.get("product_quantity") or 1),
            "item_price": _price_float(meta.get("product_price")),
            "item_name":  meta.get("product_name", "")[:255],
        }]
    if meta.get("cart_total") or meta.get("product_price"):
        custom["value"] = _price_float(meta.get("cart_total") or meta.get("product_price"))
    if meta.get("item_count"):
        custom["num_items"] = int(meta["item_count"])

    return custom


def _send(ad_account_id: str, access_token: str, payload: dict, max_attempts: int = 3) -> tuple[bool, Optional[str]]:
    url = _PINTEREST_API.format(ad_account_id=ad_account_id)
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type":  "application/json",
    }
    delay = 1.0
    last_err: Optional[str] = None

    for attempt in range(max_attempts):
        try:
            resp = httpx.post(url, json=payload, headers=headers, timeout=10.0)
            if resp.status_code in (200, 201):
                logger.info("pinterest_capi sent event for account %s", ad_account_id)
                return True, None
            body_text = resp.text[:300]
            if 400 <= resp.status_code < 500:
                err = f"HTTP {resp.status_code}: {body_text}"
                logger.warning("pinterest_capi %s (no retry)", err)
                return False, err
            last_err = f"HTTP {resp.status_code}: {body_text[:160]}"
            logger.warning("pinterest_capi %s attempt %d/%d", last_err, attempt + 1, max_attempts)
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            last_err = f"{type(exc).__name__}: {str(exc)[:160]}"
            logger.warning("pinterest_capi network error %d/%d: %s", attempt + 1, max_attempts, last_err)
        except Exception as exc:
            err = f"{type(exc).__name__}: {str(exc)[:200]}"
            logger.error("pinterest_capi exception: %s", err)
            return False, err
        if attempt < max_attempts - 1:
            time.sleep(delay * (2 ** attempt))

    return False, f"failed after {max_attempts} attempts; last={last_err or 'unknown'}"


def _build_event_obj(
    event: NormalizedEvent,
    pin_event_name: str,
    event_id: str,
    tag_id: str,
) -> dict:
    return {
        "event_name":    pin_event_name,
        "action_source": "web",
        "event_time":    int(time.time()),
        "event_id":      event_id,
        "event_source_url": event.page_url or "",
        "partner_name":  "ecommerce-tracking-ia",
        "user_data":     _build_user_data(event),
        "custom_data":   _build_custom_data(event, pin_event_name),
    }


# ── Public senders ───────────────────────────────────────────────────────────

def send_purchase(
    ad_account_id: str,
    access_token: str,
    tag_id: str,
    event: NormalizedEvent,
    value_override: Optional[float] = None,
) -> tuple[bool, Optional[str]]:
    """Send checkout (with order value) — for order.paid webhook."""
    if not (ad_account_id and access_token and tag_id):
        return False, "missing pinterest credentials"
    order = event.order
    if not order or (order.total or 0) <= 0:
        return False, "skipped: no order or zero value"

    event_id = hashlib.sha256(
        f"purchase_pinterest_{event.client_id}_{order.id}".encode()
    ).hexdigest()

    obj = _build_event_obj(event, "checkout", event_id, tag_id)
    if value_override is not None:
        obj["custom_data"]["value"] = _price_float(value_override)

    return _send(ad_account_id, access_token, {"data": [obj]})


def send_pixel_event(
    ad_account_id: str,
    access_token: str,
    tag_id: str,
    event: NormalizedEvent,
) -> tuple[bool, Optional[str]]:
    """
    Mid-funnel: page_visit / add_to_cart / checkout. Mirrors meta_capi and
    tiktok_capi signatures so dispatchers can call all three uniformly.
    """
    if not (ad_account_id and access_token and tag_id):
        return False, "missing pinterest credentials"
    pin_event_name = _PIN_EVENT_MAP.get(event.event_type.value)
    if not pin_event_name:
        return False, f"unmapped event_type: {event.event_type.value}"

    event_id = event.event_id or hashlib.sha256(
        f"{pin_event_name}_{event.client_id}_{int(time.time())}".encode()
    ).hexdigest()

    obj = _build_event_obj(event, pin_event_name, event_id, tag_id)
    return _send(ad_account_id, access_token, {"data": [obj]})
