"""
Meta Conversions API (CAPI) — server-side event sender.

Sends server-side events to Meta, complementing the browser pixel.
Improves attribution reliability and bypasses ad blockers / iOS restrictions.

Events sent:
  Purchase        — on order.paid webhook (highest priority)
  AddToCart       — on pixel add_to_cart event
  ViewContent     — on pixel product_viewed event
  InitiateCheckout — on pixel begin_checkout event

Docs: https://developers.facebook.com/docs/marketing-api/conversions-api
"""

import hashlib
import logging
import time
from typing import Optional


def _deterministic_purchase_id(platform: str, order_id: str) -> str:
    """
    Gera event_id estável para Purchase CAPI.
    Mesmo pedido + plataforma → mesmo ID → Meta deduplica automaticamente
    mesmo que o webhook seja disparado mais de uma vez.
    """
    raw = f"purchase_{platform}_{order_id}"
    return hashlib.sha256(raw.encode()).hexdigest()

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


# ── user_data builder ─────────────────────────────────────────────────────────

def _build_user_data(event: NormalizedEvent) -> dict:
    """
    Build user_data dict with all available PII (hashed) and browser identifiers.

    Each identifier we add raises Event Match Quality. Order of contribution
    (rough): em > ph > external_id > fn/ln > fbp > fbc > zip > city > ip > ua.
    Additions for EMQ improvement: login_id (+8%), db (+6%).
    """
    user_data: dict = {}
    customer = event.customer
    email_for_external = None
    if customer:
        if customer.email:
            email_clean = customer.email.strip().lower()
            user_data["em"] = [_sha256(email_clean)]
            email_for_external = email_clean
        if customer.phone:
            phone_clean = "".join(c for c in (customer.phone or "") if c.isdigit())
            if phone_clean:
                user_data["ph"] = [_sha256(phone_clean)]
        # First/last name — Meta values these even though hashed
        if customer.first_name:
            user_data["fn"] = [_sha256(customer.first_name)]
        if customer.last_name:
            user_data["ln"] = [_sha256(customer.last_name)]
        if customer.date_of_birth:
            # Meta expects YYYYMMDD before hashing
            db_clean = "".join(c for c in customer.date_of_birth if c.isdigit())[:8]
            if len(db_clean) == 8:
                user_data["db"] = [_sha256(db_clean)]
        if customer.gender:
            user_data["ge"] = [_sha256(customer.gender.lower()[:1])]
        # external_id: prefer the platform customer ID (stable across devices),
        # fall back to email_hash so we never go without one (Meta rates this 25%+)
        if customer.id:
            user_data["external_id"] = [_sha256(str(customer.id))]
        elif email_for_external:
            user_data["external_id"] = [_sha256(email_for_external)]
        if customer.address:
            addr = customer.address
            if addr.country:
                user_data["country"] = [_sha256(addr.country.lower())]
            if addr.city:
                user_data["ct"] = [_sha256(addr.city.lower())]
            if addr.state:
                user_data["st"] = [_sha256(addr.state.lower())]
            if addr.zip_code:
                user_data["zp"] = [_sha256(addr.zip_code.replace(" ", ""))]

    # Browser identifiers — Meta requires these UN-hashed
    meta = event.metadata or {}
    if meta.get("fbp"):
        user_data["fbp"] = meta["fbp"]

    # fbc: prefer pre-built from cookies; otherwise reconstruct from a raw fbclid
    # captured on the landing URL (?fbclid=...). Format: fb.1.{epoch_ms}.{fbclid}
    if meta.get("fbc"):
        user_data["fbc"] = meta["fbc"]
    elif meta.get("fbclid"):
        try:
            user_data["fbc"] = f"fb.1.{int(time.time() * 1000)}.{meta['fbclid']}"
        except Exception:
            pass

    if meta.get("ip"):
        user_data["client_ip_address"] = meta["ip"]
    if meta.get("user_agent"):
        user_data["client_user_agent"] = meta["user_agent"]

    # Facebook Login ID — improves EMQ by up to 8%
    if meta.get("facebook_login"):
        user_data["login_id"] = meta["facebook_login"]

    # Date of Birth from pixel (if available) — improves EMQ by up to 6%
    # Only add if not already in customer data (customer.date_of_birth has priority)
    if meta.get("date_of_birth") and "db" not in user_data:
        dob_clean = "".join(c for c in meta["date_of_birth"] if c.isdigit())[:8]
        if len(dob_clean) == 8:
            user_data["db"] = [_sha256(dob_clean)]

    return user_data


def _send(
    pixel_id: str,
    access_token: str,
    capi_events: list[dict],
    test_event_code: Optional[str] = None,
    max_attempts: int = 3,
) -> tuple[bool, Optional[str]]:
    """
    Low-level sender with exponential backoff retry.
    Returns (success, error_message). error_message is None on success,
    otherwise a short string suitable for storing in orders.capi_last_error.
    """
    payload: dict = {
        "data":         capi_events,
        "access_token": access_token,
    }
    if test_event_code:
        payload["test_event_code"] = test_event_code

    url = _CAPI_URL.format(pixel_id=pixel_id)
    delay = 1.0
    last_err: Optional[str] = None

    for attempt in range(max_attempts):
        try:
            resp = httpx.post(url, json=payload, timeout=10.0)
            if resp.status_code == 200:
                result = resp.json()
                logger.info("meta_capi sent %d event(s) — events_received=%s",
                            len(capi_events), result.get("events_received"))
                return True, None
            # 4xx = client error, don't retry
            if 400 <= resp.status_code < 500:
                err = f"HTTP {resp.status_code}: {resp.text[:240]}"
                logger.warning("meta_capi %s (no retry)", err)
                return False, err
            last_err = f"HTTP {resp.status_code}: {resp.text[:160]}"
            logger.warning("meta_capi %s attempt %d/%d", last_err, attempt + 1, max_attempts)
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            last_err = f"{type(exc).__name__}: {str(exc)[:160]}"
            logger.warning("meta_capi network error attempt %d/%d: %s", attempt + 1, max_attempts, last_err)
        except Exception as exc:
            err = f"{type(exc).__name__}: {str(exc)[:200]}"
            logger.error("meta_capi exception: %s", err)
            return False, err

        if attempt < max_attempts - 1:
            time.sleep(delay * (2 ** attempt))

    final = f"failed after {max_attempts} attempts; last={last_err or 'unknown'}"
    logger.error("meta_capi %s", final)
    return False, final


# ── Public senders ────────────────────────────────────────────────────────────

def send_purchase(
    pixel_id: str,
    access_token: str,
    event: NormalizedEvent,
    test_event_code: Optional[str] = None,
    value_override: Optional[float] = None,
) -> tuple[bool, Optional[str]]:
    """
    Send Purchase event. Returns (success, error_message).

    `value_override` — when set, sent to Meta as the conversion value instead of
    the order total. Used for value-based bidding: pass the customer's predicted
    LTV so Meta optimizes for high-LTV cohorts rather than one-time purchases.
    The raw order total is always preserved in custom_data.order_total for audit.
    """
    if not pixel_id or not access_token:
        return False, "missing pixel_id or access_token"
    order = event.order
    if not order:
        return False, "event has no order data"

    # Deterministic event_id: mesmo order_id sempre gera o mesmo hash.
    # Isso garante deduplicação mesmo em retries de webhook ou reprocessamento.
    dedup_id = _deterministic_purchase_id(event.platform or "webhook", str(order.id))

    order_total = float(order.total or 0)
    bid_value = float(value_override) if value_override is not None else order_total

    meta = event.metadata or {}
    capi_event = {
        "event_name":    "Purchase",
        "event_time":    int(time.time()),
        "action_source": "website",
        "event_id":      dedup_id,
        "user_data":     _build_user_data(event),
        "custom_data": {
            "currency": (order.currency or "BRL").upper(),
            "value":    bid_value,
            "order_id": str(order.id),
            "order_total": order_total,
            # content_ids and contents improve ML signal for Meta Advantage+
            "content_ids":  [str(item.product_id) for item in (order.items or []) if item.product_id],
            "contents": [
                {
                    "id":         str(item.product_id),
                    "quantity":   item.quantity or 1,
                    "item_price": float(item.price or 0),
                    "title":      item.name or "",
                }
                for item in (order.items or []) if item.product_id
            ],
            "num_items": sum(item.quantity or 1 for item in (order.items or [])),
        },
    }
    # event_source_url: prefer the order confirmation page, fall back to landing page
    source_url = (
        event.page_url
        or meta.get("order_status_url")
    )
    if source_url:
        capi_event["event_source_url"] = source_url
    return _send(pixel_id, access_token, [capi_event], test_event_code)


def send_refund(
    pixel_id:        str,
    access_token:    str,
    order_id:        str,
    refund_amount:   float,
    currency:        str,
    refund_id:       Optional[str] = None,
    user_data:       Optional[dict] = None,
    test_event_code: Optional[str] = None,
) -> tuple[bool, Optional[str]]:
    """
    Send a Purchase event with NEGATIVE value to Meta CAPI to record a refund.

    Per Meta best practices, refunds are reported as a Purchase event with a
    negative `value`. The event_id is derived from the refund_id (or order_id)
    so it doesn't collide with the original Purchase event.

    Returns (success, error_message).
    """
    if not pixel_id or not access_token or not order_id:
        return False, "missing pixel_id, access_token or order_id"

    raw = f"refund_{order_id}_{refund_id or 'full'}"
    dedup_id = hashlib.sha256(raw.encode()).hexdigest()

    capi_event = {
        "event_name":    "Purchase",
        "event_time":    int(time.time()),
        "action_source": "website",
        "event_id":      dedup_id,
        "user_data":     user_data or {},
        "custom_data": {
            "currency": (currency or "BRL").upper(),
            "value":    -abs(float(refund_amount)),
            "order_id": str(order_id),
            "refund":   True,
        },
    }
    return _send(pixel_id, access_token, [capi_event], test_event_code)


def send_pixel_event(
    pixel_id: str,
    access_token: str,
    event: NormalizedEvent,
    test_event_code: Optional[str] = None,
) -> tuple[bool, Optional[str]]:
    """
    Send ViewContent, AddToCart, or InitiateCheckout from pixel events.
    Maps NormalizedEvent.event_type → Meta standard event name.
    Returns (success, error_message).
    """
    if not pixel_id or not access_token:
        return False, "missing pixel_id or access_token"

    event_map = {
        "product.viewed":    "ViewContent",
        "cart.created":      "AddToCart",
        "cart.updated":      "AddToCart",
        "checkout.started":  "InitiateCheckout",
    }
    meta_event_name = event_map.get(event.event_type.value)
    if not meta_event_name:
        return False, f"unmapped event_type: {event.event_type.value}"

    meta = event.metadata or {}
    custom_data: dict = {}

    if meta_event_name == "ViewContent":
        custom_data = {
            "content_type": "product",
            "content_ids":  [str(meta["product_id"])] if meta.get("product_id") else [],
            "contents": [{
                "id":         str(meta.get("product_id", "")),
                "quantity":   1,
                "item_price": float(meta.get("product_price", 0)),
                "title":      meta.get("product_name", ""),
            }] if meta.get("product_id") else [],
            "value":    float(meta.get("product_price", 0)),
            "currency": "BRL",
        }
    elif meta_event_name == "AddToCart":
        custom_data = {
            "content_type": "product",
            "content_ids":  [str(meta["product_id"])] if meta.get("product_id") else [],
            "contents": [{
                "id":         str(meta.get("product_id", "")),
                "quantity":   1,
                "item_price": float(meta.get("product_price", 0)),
                "title":      meta.get("product_name", ""),
            }] if meta.get("product_id") else [],
            "value":    float(meta.get("product_price", 0)),
            "currency": "BRL",
        }
    elif meta_event_name == "InitiateCheckout":
        custom_data = {
            "value":      float(meta.get("cart_total", 0)),
            "currency":   "BRL",
            "num_items":  int(meta.get("item_count", 0)),
        }

    capi_event = {
        "event_name":    meta_event_name,
        "event_time":    int(time.time()),
        "action_source": "website",
        "event_id":      event.event_id,
        "user_data":     _build_user_data(event),
        "custom_data":   custom_data,
    }
    if event.page_url:
        capi_event["event_source_url"] = event.page_url

    return _send(pixel_id, access_token, [capi_event], test_event_code)
