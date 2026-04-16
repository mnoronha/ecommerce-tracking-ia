import base64
import hashlib
import hmac
import uuid
from datetime import datetime
from typing import Optional, List

from .base import BaseAdapter
from ...models.events import (
    NormalizedEvent,
    EventType,
    CustomerData,
    OrderData,
    OrderItem,
    Address,
)


class WooCommerceAdapter(BaseAdapter):
    platform_name = "woocommerce"

    TOPIC_MAP: dict = {
        "order.created": EventType.ORDER_CREATED,
        "order.updated": EventType.ORDER_UPDATED,
        "order.deleted": EventType.ORDER_CANCELLED,
        "customer.created": EventType.CUSTOMER_CREATED,
        "product.created": EventType.CUSTOM,
        "product.updated": EventType.CUSTOM,
    }

    # ------------------------------------------------------------------ #
    # Signature validation                                                 #
    # ------------------------------------------------------------------ #

    def validate_signature(self, payload: bytes, headers: dict, secret: str) -> bool:
        """Validate WooCommerce HMAC-SHA256 from X-WC-Webhook-Signature header."""
        wc_sig = (
            headers.get("x-wc-webhook-signature")
            or headers.get("X-WC-Webhook-Signature")
            or ""
        )
        if not wc_sig:
            return False

        digest = hmac.new(
            secret.encode("utf-8"),
            payload,
            hashlib.sha256,
        ).digest()
        computed = base64.b64encode(digest).decode("utf-8")
        return hmac.compare_digest(computed, wc_sig)

    # ------------------------------------------------------------------ #
    # Parsing helpers                                                      #
    # ------------------------------------------------------------------ #

    def _parse_address(self, addr: dict) -> Optional[Address]:
        if not addr:
            return None
        return Address(
            street=addr.get("address_1"),
            city=addr.get("city"),
            state=addr.get("state"),
            country=addr.get("country"),
            zip_code=addr.get("postcode"),
        )

    def _parse_customer(self, data: dict) -> Optional[CustomerData]:
        billing = data.get("billing") or {}
        if not billing:
            return None
        name = (
            f"{billing.get('first_name', '')} {billing.get('last_name', '')}".strip()
            or None
        )
        return CustomerData(
            id=str(data.get("customer_id", "")),
            email=billing.get("email"),
            name=name,
            phone=billing.get("phone"),
            address=self._parse_address(billing),
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
                    variant_id=str(item.get("variation_id", "")),
                    name=item.get("name"),
                    sku=item.get("sku"),
                    price=price,
                    quantity=qty,
                    total=float(item.get("total", price * qty)),
                )
            )
        return items

    def _parse_order(self, data: dict) -> Optional[OrderData]:
        if not data.get("id"):
            return None
        subtotal = sum(
            float(item.get("subtotal", 0)) for item in data.get("line_items", [])
        )
        return OrderData(
            id=str(data.get("id")),
            number=str(data.get("number", "")),
            status=data.get("status"),
            total=float(data.get("total", 0)),
            subtotal=float(data.get("cart_total") or subtotal),
            tax=float(data.get("total_tax", 0)),
            shipping=float(data.get("shipping_total", 0)),
            discount=float(data.get("discount_total", 0)),
            currency=data.get("currency"),
            items=self._parse_items(data.get("line_items", [])),
            payment_method=(
                data.get("payment_method_title") or data.get("payment_method")
            ),
        )

    # ------------------------------------------------------------------ #
    # Normalize                                                            #
    # ------------------------------------------------------------------ #

    def normalize(
        self, payload: dict, client_id: str, headers: Optional[dict] = None
    ) -> NormalizedEvent:
        headers = headers or {}
        topic = (
            headers.get("x-wc-webhook-topic")
            or headers.get("X-WC-Webhook-Topic", "")
        )
        event_type = self.TOPIC_MAP.get(topic, EventType.CUSTOM)

        created_at = payload.get("date_created") or payload.get("date_modified")
        try:
            timestamp = datetime.fromisoformat(
                str(created_at).replace("Z", "+00:00")
            )
        except Exception:
            timestamp = datetime.utcnow()

        return NormalizedEvent(
            event_id=str(uuid.uuid4()),
            event_type=event_type,
            platform=self.platform_name,
            client_id=client_id,
            timestamp=timestamp,
            customer=self._parse_customer(payload),
            order=self._parse_order(payload),
            raw_payload=payload,
            metadata={
                "wc_webhook_id": headers.get("x-wc-webhook-id"),
                "wc_delivery_id": headers.get("x-wc-delivery-id"),
                "topic": topic,
            },
        )
