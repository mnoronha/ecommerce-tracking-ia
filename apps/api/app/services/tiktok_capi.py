"""
TikTok Events API (CAPI) — server-side event sender.

Sends server-side funnel events to TikTok, complementing the browser pixel.
Improves attribution reliability and bypasses iOS / ad-blocker restrictions.

Events sent (TikTok event names in parens):
  ViewContent       — on product.viewed   pixel event
  AddToCart         — on cart.created/updated webhook or pixel event
  InitiateCheckout  — on checkouts/create webhook or pixel begin_checkout
  PlaceAnOrder      — on order.paid webhook

Advanced matching: every event carries hashed email + phone + external_id +
ip + user_agent in `context.user` so TikTok can identify the visitor even
without a fresh ttclid.

Docs: https://business-api.tiktok.com/portal/docs?id=1771101027431425
"""

import hashlib
import logging
import time
from typing import Optional

import httpx

from ..models.events import NormalizedEvent

logger = logging.getLogger(__name__)

_TIKTOK_EVENTS_URL = "https://business-api.tiktok.com/open_api/v1.3/event/track/"


# ── PII hashing ───────────────────────────────────────────────────────────────

def _sha256(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    return hashlib.sha256(value.strip().lower().encode("utf-8")).hexdigest()


# ── Event builder ─────────────────────────────────────────────────────────────

def _build_user(event: NormalizedEvent, ttclid: Optional[str] = None) -> dict:
    user: dict = {}
    customer = event.customer
    if customer:
        if customer.email:
            user["email"] = [_sha256(customer.email.strip().lower())]
        if customer.phone:
            phone_clean = "".join(c for c in customer.phone if c.isdigit())
            if phone_clean:
                user["phone_number"] = [_sha256(phone_clean)]
        if customer.id:
            user["external_id"] = [_sha256(str(customer.id))]

    meta = event.metadata or {}
    if ttclid or meta.get("ttclid"):
        user["ttclid"] = ttclid or meta["ttclid"]

    ip = meta.get("ip")
    ua = meta.get("user_agent")
    if ip:
        user["ip"] = ip
    if ua:
        user["user_agent"] = ua

    return user


def _build_contents(event: NormalizedEvent) -> list[dict]:
    order = event.order
    if not order or not order.items:
        return []
    contents = []
    for item in order.items:
        c: dict = {}
        if item.product_id:
            c["content_id"] = str(item.product_id)
        if item.name:
            c["content_name"] = item.name[:255]
        c["quantity"]   = int(item.quantity or 1)
        c["price"]      = float(item.price or 0)
        contents.append(c)
    return contents


# ── Sender ────────────────────────────────────────────────────────────────────

def _send(pixel_code: str, access_token: str, event_dict: dict, max_attempts: int = 3) -> tuple[bool, Optional[str]]:
    """Wraps a single event dict into the v1.3 data-array envelope and POSTs it."""
    headers = {
        "Access-Token": access_token,
        "Content-Type": "application/json",
    }
    # TikTok Events API v1.3 uses a `data` array wrapper, not a flat payload.
    body_out = {"pixel_code": pixel_code, "data": [event_dict]}
    delay = 1.0
    last_err: Optional[str] = None

    for attempt in range(max_attempts):
        try:
            resp = httpx.post(_TIKTOK_EVENTS_URL, json=body_out, headers=headers, timeout=10.0)
            body = resp.json()
            code = body.get("code", -1)
            if resp.status_code == 200 and code == 0:
                logger.info("tiktok_capi sent event for pixel %s — code=%s", pixel_code, code)
                return True, None
            if 400 <= resp.status_code < 500:
                err = f"HTTP {resp.status_code} code={code}: {body.get('message', '')[:200]}"
                logger.warning("tiktok_capi %s (no retry)", err)
                return False, err
            last_err = f"HTTP {resp.status_code} code={code}: {body.get('message', '')[:160]}"
            logger.warning("tiktok_capi %s attempt %d/%d", last_err, attempt + 1, max_attempts)
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            last_err = f"{type(exc).__name__}: {str(exc)[:160]}"
            logger.warning("tiktok_capi network error attempt %d/%d: %s", attempt + 1, max_attempts, last_err)
        except Exception as exc:
            err = f"{type(exc).__name__}: {str(exc)[:200]}"
            logger.error("tiktok_capi exception: %s", err)
            return False, err

        if attempt < max_attempts - 1:
            time.sleep(delay * (2 ** attempt))

    final = f"failed after {max_attempts} attempts; last={last_err or 'unknown'}"
    logger.error("tiktok_capi %s", final)
    return False, final


# ── Public sender ─────────────────────────────────────────────────────────────

def send_purchase(
    pixel_code: str,
    access_token: str,
    event: NormalizedEvent,
    ttclid: Optional[str] = None,
    value_override: Optional[float] = None,
) -> tuple[bool, Optional[str]]:
    """
    Send PlaceAnOrder event to TikTok Events API.
    Returns (success, error_message).

    `value_override` — when set, sent as the conversion value (used for
    value-based bidding with predicted LTV, same logic as Meta CAPI).
    """
    if not pixel_code or not access_token:
        return False, "missing pixel_code or access_token"
    order = event.order
    if not order:
        return False, "event has no order data"
    if (order.total or 0) <= 0:
        return False, "skipped: order total is 0 or null"

    meta = event.metadata or {}
    event_time = int(time.time())

    # Stable event_id for deduplication
    raw_id = f"purchase_tiktok_{event.client_id}_{order.id}"
    event_id = hashlib.sha256(raw_id.encode()).hexdigest()[:32]

    value = value_override if value_override is not None else float(order.total)
    currency = order.currency or "BRL"

    event_dict = {
        "event":      "Purchase",
        "event_id":   event_id,
        "timestamp":  event_time,
        "context": {
            "user": _build_user(event, ttclid),
            "page": {
                "url": meta.get("page_url") or "",
            },
        },
        "properties": {
            "currency":  currency,
            "value":     value,
            "order_id":  str(order.id),
            "contents":  _build_contents(event),
            "num_items": sum(int(i.quantity or 1) for i in (order.items or [])) or 1,
        },
    }

    return _send(pixel_code, access_token, event_dict)


# ── Funnel events ─────────────────────────────────────────────────────────────

_TIKTOK_EVENT_MAP = {
    "product.viewed":    "ViewContent",
    "cart.created":      "AddToCart",
    "cart.updated":      "AddToCart",
    "checkout.started":  "InitiateCheckout",
}


def send_pixel_event(
    pixel_code: str,
    access_token: str,
    event: NormalizedEvent,
    ttclid: Optional[str] = None,
) -> tuple[bool, Optional[str]]:
    """
    Send ViewContent / AddToCart / InitiateCheckout to TikTok Events API.
    Mirrors meta_capi.send_pixel_event so the webhook dispatchers can call
    every CAPI service through the same signature.
    """
    if not pixel_code or not access_token:
        return False, "missing pixel_code or access_token"

    tiktok_event_name = _TIKTOK_EVENT_MAP.get(event.event_type.value)
    if not tiktok_event_name:
        return False, f"unmapped event_type: {event.event_type.value}"

    meta = event.metadata or {}

    # event_id must match what the snippet sends so TikTok dedupes browser ↔ server
    event_id = event.event_id or hashlib.sha256(
        f"{tiktok_event_name}_{event.client_id}_{int(time.time())}".encode()
    ).hexdigest()[:32]

    properties: dict = {"currency": "BRL"}
    if tiktok_event_name in ("ViewContent", "AddToCart"):
        if meta.get("product_id"):
            properties["contents"] = [{
                "content_id":   str(meta.get("product_id") or ""),
                "content_name": meta.get("product_name", ""),
                "price":        float(meta.get("product_price") or 0),
                "quantity":     int(meta.get("product_quantity") or 1),
            }]
        properties["value"] = float(meta.get("product_price") or 0)
    elif tiktok_event_name == "InitiateCheckout":
        properties["value"]    = float(meta.get("cart_total") or 0)
        properties["num_items"] = int(meta.get("item_count") or 0)

    event_dict = {
        "event":      tiktok_event_name,
        "event_id":   event_id,
        "timestamp":  int(time.time()),
        "context": {
            "user": _build_user(event, ttclid),
            "page": {"url": (event.page_url or meta.get("page_url") or "")},
        },
        "properties": properties,
    }
    return _send(pixel_code, access_token, event_dict)
