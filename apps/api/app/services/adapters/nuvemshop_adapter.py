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


class NuvemshopAdapter(BaseAdapter):
    platform_name = "nuvemshop"

    TOPIC_MAP: dict = {
        "order/created": EventType.ORDER_CREATED,
        "order/updated": EventType.ORDER_UPDATED,
        "order/paid": EventType.ORDER_PAID,
        "order/cancelled": EventType.ORDER_CANCELLED,
        "order/fulfilled": EventType.ORDER_FULFILLED,
        "cart/created": EventType.CART_CREATED,
        "cart/updated": EventType.CART_UPDATED,
    }

    # ------------------------------------------------------------------ #
    # Signature validation                                                 #
    # ------------------------------------------------------------------ #

    def validate_signature(self, payload: bytes, headers: dict, secret: str) -> bool:
        """Validate Nuvemshop HMAC-SHA256 from x-linked-signature header."""
        signature = (
            headers.get("x-linked-signature")
            or headers.get("X-Linked-Signature")
            or headers.get("x-nuvemshop-signature")
            or ""
        )
        if not signature:
            return False

        computed = hmac.new(
            secret.encode("utf-8"),
            payload,
            hashlib.sha256,
        ).hexdigest()
        return hmac.compare_digest(computed, signature)

    # ------------------------------------------------------------------ #
    # Parsing helpers                                                      #
    # ------------------------------------------------------------------ #

    def _parse_address(self, addr: dict) -> Optional[Address]:
        if not addr:
            return None
        return Address(
            street=addr.get("address"),
            city=addr.get("city"),
            state=addr.get("province"),
            country=addr.get("country"),
            zip_code=addr.get("zipcode"),
        )

    def _parse_customer(self, data: dict) -> Optional[CustomerData]:
        customer = data.get("customer") or {}
        if not customer:
            return None
        return CustomerData(
            id=str(customer.get("id", "")),
            email=customer.get("email"),
            name=customer.get("name"),
            phone=customer.get("phone"),
            address=self._parse_address(
                data.get("shipping_address")
                or customer.get("default_address")
                or {}
            ),
        )

    def _parse_items(self, products: list) -> List[OrderItem]:
        items = []
        for item in products or []:
            price = float(item.get("price", 0))
            qty = int(item.get("quantity", 1))
            items.append(
                OrderItem(
                    id=str(item.get("id", "")),
                    product_id=str(item.get("product_id", "")),
                    variant_id=str(item.get("variant_id", "")),
                    name=item.get("name"),
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
        return OrderData(
            id=str(data.get("id")),
            number=str(data.get("number", "")),
            status=data.get("payment_status") or data.get("status"),
            total=float(data.get("total", 0)),
            subtotal=float(data.get("subtotal", 0)),
            tax=float(data.get("total_tax", 0)),
            shipping=float(data.get("shipping_cost", 0)),
            discount=float(data.get("discount", 0)),
            currency=data.get("currency"),
            items=self._parse_items(data.get("products", [])),
            payment_method=(
                data.get("payment_details", {}).get("method")
                if isinstance(data.get("payment_details"), dict)
                else None
            ),
        )

    # ------------------------------------------------------------------ #
    # Normalize                                                            #
    # ------------------------------------------------------------------ #

    def normalize(
        self, payload: dict, client_id: str, headers: Optional[dict] = None
    ) -> NormalizedEvent:
        headers = headers or {}
        topic = payload.get("event") or headers.get("x-nuvemshop-topic", "")
        event_type = self.TOPIC_MAP.get(topic, EventType.CUSTOM)

        # Nuvemshop may wrap the order inside payload["order"]
        order_data = payload.get("order", payload)

        created_at = order_data.get("created_at") or order_data.get("updated_at")
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
            customer=self._parse_customer(order_data),
            order=self._parse_order(order_data),
            raw_payload=payload,
            metadata={
                "store_id": payload.get("store_id"),
                "topic": topic,
            },
        )
