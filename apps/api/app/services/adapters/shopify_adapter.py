import hashlib
import hmac
import base64
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


class ShopifyAdapter(BaseAdapter):
    platform_name = "shopify"

    TOPIC_MAP: dict = {
        "orders/create": EventType.ORDER_CREATED,
        "orders/updated": EventType.ORDER_UPDATED,
        "orders/paid": EventType.ORDER_PAID,
        "orders/cancelled": EventType.ORDER_CANCELLED,
        "orders/fulfilled": EventType.ORDER_FULFILLED,
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

    def normalize(
        self, payload: dict, client_id: str, headers: Optional[dict] = None
    ) -> NormalizedEvent:
        headers = headers or {}
        topic = headers.get("x-shopify-topic") or headers.get("X-Shopify-Topic", "")
        event_type = self.TOPIC_MAP.get(topic, EventType.CUSTOM)

        created_at = payload.get("created_at") or payload.get("updated_at")
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
                "shop_domain": headers.get("x-shopify-shop-domain"),
                "topic": topic,
            },
        )
