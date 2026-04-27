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
    More signals = higher match rate. Target: >80% with fbp + email.
    """
    user_data: dict = {}
    customer = event.customer
    if customer:
        if customer.email:
            user_data["em"] = [_sha256(customer.email)]
        if customer.phone:
            phone_clean = "".join(c for c in (customer.phone or "") if c.isdigit())
            if phone_clean:
                user_data["ph"] = [_sha256(phone_clean)]
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

    # Browser identifiers — not hashed per Meta spec
    meta = event.metadata or {}
    if meta.get("fbp"):
        user_data["fbp"] = meta["fbp"]
    if meta.get("fbc"):
        user_data["fbc"] = meta["fbc"]

    return user_data


def _send(
    pixel_id: str,
    access_token: str,
    capi_events: list[dict],
    test_event_code: Optional[str] = None,
    max_attempts: int = 3,
) -> bool:
    """Low-level sender with exponential backoff retry. Returns True on success."""
    payload: dict = {
        "data":         capi_events,
        "access_token": access_token,
    }
    if test_event_code:
        payload["test_event_code"] = test_event_code

    url = _CAPI_URL.format(pixel_id=pixel_id)
    delay = 1.0

    for attempt in range(max_attempts):
        try:
            resp = httpx.post(url, json=payload, timeout=10.0)
            if resp.status_code == 200:
                result = resp.json()
                logger.info("meta_capi sent %d event(s) — events_received=%s",
                            len(capi_events), result.get("events_received"))
                return True
            # 4xx = client error, don't retry
            if 400 <= resp.status_code < 500:
                logger.warning("meta_capi HTTP %s (no retry): %s", resp.status_code, resp.text[:400])
                return False
            logger.warning("meta_capi HTTP %s attempt %d/%d", resp.status_code, attempt + 1, max_attempts)
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            logger.warning("meta_capi network error attempt %d/%d: %s", attempt + 1, max_attempts, exc)
        except Exception as exc:
            logger.error("meta_capi exception: %s", exc)
            return False

        if attempt < max_attempts - 1:
            time.sleep(delay * (2 ** attempt))

    logger.error("meta_capi failed after %d attempts", max_attempts)
    return False


# ── Public senders ────────────────────────────────────────────────────────────

def send_purchase(
    pixel_id: str,
    access_token: str,
    event: NormalizedEvent,
    test_event_code: Optional[str] = None,
) -> bool:
    """Send Purchase event. Returns True on success."""
    if not pixel_id or not access_token:
        return False
    order = event.order
    if not order:
        return False

    # Deterministic event_id: mesmo order_id sempre gera o mesmo hash.
    # Isso garante deduplicação mesmo em retries de webhook ou reprocessamento.
    dedup_id = _deterministic_purchase_id(event.platform or "webhook", str(order.id))

    capi_event = {
        "event_name":    "Purchase",
        "event_time":    int(time.time()),
        "action_source": "website",
        "event_id":      dedup_id,
        "user_data":     _build_user_data(event),
        "custom_data": {
            "currency": (order.currency or "BRL").upper(),
            "value":    float(order.total or 0),
            "order_id": str(order.id),
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
    return _send(pixel_id, access_token, [capi_event], test_event_code)


def send_pixel_event(
    pixel_id: str,
    access_token: str,
    event: NormalizedEvent,
    test_event_code: Optional[str] = None,
) -> bool:
    """
    Send ViewContent, AddToCart, or InitiateCheckout from pixel events.
    Maps NormalizedEvent.event_type → Meta standard event name.
    Returns True on success.
    """
    if not pixel_id or not access_token:
        return False

    event_map = {
        "product.viewed":    "ViewContent",
        "cart.created":      "AddToCart",
        "cart.updated":      "AddToCart",
        "checkout.started":  "InitiateCheckout",
    }
    meta_event_name = event_map.get(event.event_type.value)
    if not meta_event_name:
        return False

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
