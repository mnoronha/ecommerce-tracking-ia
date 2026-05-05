import hashlib
import hmac
import base64
import uuid
from datetime import datetime
from typing import Optional, List
from urllib.parse import parse_qs, urlparse

from .base import BaseAdapter
from ...models.events import (
    NormalizedEvent,
    EventType,
    CustomerData,
    OrderData,
    OrderItem,
    Address,
    UTMParams,
)


class ShopifyAdapter(BaseAdapter):
    platform_name = "shopify"

    TOPIC_MAP: dict = {
        "orders/create": EventType.ORDER_CREATED,
        "orders/updated": EventType.ORDER_UPDATED,
        "orders/paid": EventType.ORDER_PAID,
        "orders/cancelled": EventType.ORDER_CANCELLED,
        "orders/fulfilled": EventType.ORDER_FULFILLED,
        "refunds/create": EventType.ORDER_REFUNDED,
        "carts/create": EventType.CART_CREATED,
        "carts/update": EventType.CART_UPDATED,
        "customers/create": EventType.CUSTOMER_CREATED,
    }

    # ------------------------------------------------------------------ #
    # Signature validation                                                 #
    # ------------------------------------------------------------------ #

    def validate_signature(self, payload: bytes, headers: dict, secret: str) -> bool:
        """Validate Shopify HMAC-SHA256 from X-Shopify-Hmac-Sha256 header."""
        shopify_hmac = (
            headers.get("x-shopify-hmac-sha256")
            or headers.get("X-Shopify-Hmac-Sha256")
            or ""
        )
        if not shopify_hmac:
            return False

        digest = hmac.new(
            secret.encode("utf-8"),
            payload,
            hashlib.sha256,
        ).digest()
        computed = base64.b64encode(digest).decode("utf-8")
        return hmac.compare_digest(computed, shopify_hmac)

    # ------------------------------------------------------------------ #
    # Parsing helpers                                                      #
    # ------------------------------------------------------------------ #

    def _parse_utm_from_landing(self, landing_site: str) -> Optional[UTMParams]:
        """Extract UTM params + gclid/fbclid from Shopify's landing_site URL."""
        if not landing_site:
            return None
        try:
            qs = parse_qs(urlparse(landing_site).query)
            p = {k: v[0] for k, v in qs.items() if v}
            if not any(k in p for k in ("utm_source", "utm_medium", "utm_campaign", "gclid", "fbclid")):
                return None
            return UTMParams(
                source=p.get("utm_source"),
                medium=p.get("utm_medium"),
                campaign=p.get("utm_campaign"),
                term=p.get("utm_term"),
                content=p.get("utm_content"),
            )
        except Exception:
            return None

    def _parse_address(self, addr: dict) -> Optional[Address]:
        if not addr:
            return None
        return Address(
            street=addr.get("address1"),
            city=addr.get("city"),
            state=addr.get("province"),
            country=addr.get("country"),
            zip_code=addr.get("zip"),
        )

    def _parse_customer(self, data: dict) -> Optional[CustomerData]:
        customer = data.get("customer") or {}
        if not customer and not data.get("email"):
            return None
        name = (
            f"{customer.get('first_name', '')} {customer.get('last_name', '')}".strip()
            or None
        )
        return CustomerData(
            id=str(customer.get("id", "")),
            email=customer.get("email") or data.get("email"),
            name=name,
            phone=customer.get("phone"),
            address=self._parse_address(
                customer.get("default_address")
                or data.get("shipping_address")
                or {}
            ),
        )

    def _parse_items(self, line_items: list) -> List[OrderItem]:
        items = []
        for item in line_items or []:
            price = float(item.get("price", 0))
            qty = int(item.get("quantity", 1))
            items.append(
                OrderItem(
                    id=str(item.get("id", "")),
                    product_id=str(item.get("product_id", "")),
                    variant_id=str(item.get("variant_id", "")),
                    name=item.get("name") or item.get("title"),
                    sku=item.get("sku"),
                    price=price,
                    quantity=qty,
                    total=price * qty,
                )
            )
        return items

    def _parse_order(self, data: dict) -> Optional[OrderData]:
        if not data.get("id"):
            return None
        shipping_set = data.get("total_shipping_price_set") or {}
        shipping = float(
            (shipping_set.get("shop_money") or {}).get("amount", 0)
        )
        return OrderData(
            id=str(data.get("id")),
            number=str(data.get("order_number", "")),
            status=data.get("financial_status") or data.get("fulfillment_status"),
            total=float(data.get("total_price", 0)),
            subtotal=float(data.get("subtotal_price", 0)),
            tax=float(data.get("total_tax", 0)),
            shipping=shipping,
            discount=float(data.get("total_discounts", 0)),
            currency=data.get("currency"),
            items=self._parse_items(data.get("line_items", [])),
            payment_method=data.get("payment_gateway"),
        )

    # ------------------------------------------------------------------ #
    # Normalize                                                            #
    # ------------------------------------------------------------------ #

    def _parse_refund(self, data: dict) -> Optional[OrderData]:
        """
        Refund webhooks have a different structure than orders. The `id` is
        the refund_id, `order_id` references the original order, and the
        refund total comes from summing the refund transactions.
        """
        order_id = data.get("order_id")
        if not order_id:
            return None
        transactions = data.get("transactions") or []
        refund_total = 0.0
        currency = "BRL"
        for tx in transactions:
            if tx.get("kind") == "refund" and tx.get("status") == "success":
                try:
                    refund_total += float(tx.get("amount", 0))
                except (ValueError, TypeError):
                    pass
                currency = tx.get("currency") or currency
        return OrderData(
            id=str(order_id),
            number=str(order_id),
            status="refunded",
            total=refund_total,
            currency=currency,
        )

    def _parse_note_attributes(self, payload: dict) -> dict:
        """
        Extract our injected cart attributes (visitor cookie, fbp, fbc, etc).

        These are pushed by the JS pixel via POST /cart/update.js with names
        prefixed `_` so Shopify keeps them hidden from the merchant UI but
        forwards them to the order webhook as note_attributes:
            [{"name": "_etv", "value": "..."}, {"name": "_fbp", "value": "..."}]

        This is the bridge that lets us match an order back to the browse
        session even when the customer is sent to an external gateway (PIX)
        and never reaches the Shopify thank-you page.
        """
        attrs = payload.get("note_attributes") or []
        if not isinstance(attrs, list):
            return {}
        out: dict = {}
        for entry in attrs:
            if not isinstance(entry, dict):
                continue
            name = entry.get("name")
            value = entry.get("value")
            if not name or value in (None, ""):
                continue
            out[name] = value
        return out

    def normalize(
        self, payload: dict, client_id: str, headers: Optional[dict] = None
    ) -> NormalizedEvent:
        headers = headers or {}
        topic = headers.get("x-shopify-topic") or headers.get("X-Shopify-Topic", "")
        event_type = self.TOPIC_MAP.get(topic, EventType.CUSTOM)

        created_at = payload.get("created_at") or payload.get("updated_at") or payload.get("processed_at")
        try:
            timestamp = datetime.fromisoformat(
                str(created_at).replace("Z", "+00:00")
            )
        except Exception:
            timestamp = datetime.utcnow()

        landing_site = payload.get("landing_site") or payload.get("landing_site_ref", "")
        qs_raw = parse_qs(urlparse(landing_site).query) if landing_site else {}
        qs = {k: v[0] for k, v in qs_raw.items() if v}

        # Refunds have a different shape — use the refund parser
        if event_type == EventType.ORDER_REFUNDED:
            order = self._parse_refund(payload)
        else:
            order = self._parse_order(payload)

        # note_attributes carry our injected browse-session identifiers.
        # Prefer these over landing_site qs since they're tied to the actual
        # cart, not just the first URL the customer hit.
        nattr = self._parse_note_attributes(payload)

        return NormalizedEvent(
            event_id=str(uuid.uuid4()),
            event_type=event_type,
            platform=self.platform_name,
            client_id=client_id,
            timestamp=timestamp,
            customer=self._parse_customer(payload),
            order=order,
            utm=self._parse_utm_from_landing(landing_site),
            raw_payload=payload,
            metadata={
                "shop_domain":       headers.get("x-shopify-shop-domain"),
                "topic":             topic,
                "source_name":       payload.get("source_name"),
                "landing_site":      landing_site,
                "gclid":             nattr.get("_gclid") or qs.get("gclid"),
                "fbclid":            qs.get("fbclid"),
                "referring_site":    payload.get("referring_site"),
                "cart_token":        payload.get("cart_token"),
                "refund_id":         str(payload.get("id")) if event_type == EventType.ORDER_REFUNDED else None,
                # Browse-session attribution from note_attributes
                "visitor_cookie_id": nattr.get("_etv"),
                "fbp":               nattr.get("_fbp"),
                "fbc":               nattr.get("_fbc"),
                "ga_client_id":      nattr.get("_gcid"),
            },
        )
