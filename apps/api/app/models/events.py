from pydantic import BaseModel, Field
from typing import Optional, Dict, Any, List
from datetime import datetime
from enum import Enum


class EventType(str, Enum):
    ORDER_CREATED = "order.created"
    ORDER_UPDATED = "order.updated"
    ORDER_PAID = "order.paid"
    ORDER_CANCELLED = "order.cancelled"
    ORDER_FULFILLED = "order.fulfilled"
    ORDER_REFUNDED = "order.refunded"
    CART_CREATED = "cart.created"
    CART_UPDATED = "cart.updated"
    PRODUCT_VIEWED = "product.viewed"
    PAGE_VIEWED = "page.viewed"
    CHECKOUT_STARTED = "checkout.started"
    CHECKOUT_COMPLETED = "checkout.completed"
    CUSTOMER_CREATED = "customer.created"
    CUSTOM = "custom"


class Address(BaseModel):
    street: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    country: Optional[str] = None
    zip_code: Optional[str] = None


class CustomerData(BaseModel):
    id: Optional[str] = None
    email: Optional[str] = None
    name: Optional[str] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    phone: Optional[str] = None
    date_of_birth: Optional[str] = None
    gender: Optional[str] = None
    address: Optional[Address] = None


class OrderItem(BaseModel):
    id: Optional[str] = None
    product_id: Optional[str] = None
    variant_id: Optional[str] = None
    name: Optional[str] = None
    sku: Optional[str] = None
    price: Optional[float] = None
    quantity: Optional[int] = None
    total: Optional[float] = None


class OrderData(BaseModel):
    id: Optional[str] = None
    number: Optional[str] = None
    status: Optional[str] = None
    total: Optional[float] = None
    subtotal: Optional[float] = None
    tax: Optional[float] = None
    shipping: Optional[float] = None
    discount: Optional[float] = None
    currency: Optional[str] = None
    items: Optional[List[OrderItem]] = Field(default_factory=list)
    payment_method: Optional[str] = None


class UTMParams(BaseModel):
    source: Optional[str] = None
    medium: Optional[str] = None
    campaign: Optional[str] = None
    term: Optional[str] = None
    content: Optional[str] = None


class NormalizedEvent(BaseModel):
    event_id: str
    event_type: EventType
    platform: str
    client_id: str
    timestamp: datetime
    visitor_id: Optional[str] = None
    session_id: Optional[str] = None
    customer: Optional[CustomerData] = None
    order: Optional[OrderData] = None
    page_url: Optional[str] = None
    referrer: Optional[str] = None
    utm: Optional[UTMParams] = None
    raw_payload: Optional[Dict[str, Any]] = None
    metadata: Optional[Dict[str, Any]] = None
