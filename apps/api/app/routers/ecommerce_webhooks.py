import json
import logging

from fastapi import APIRouter, HTTPException, Request

from ..config import settings
from ..database import get_supabase
from ..services import writer
from ..services.adapters import (
    ShopifyAdapter,
    NuvemshopAdapter,
    WooCommerceAdapter,
    SignatureError,
)

logger = logging.getLogger(__name__)

router = APIRouter()

# Registry of supported adapters (keyed by URL path segment)
ADAPTERS = {
    "shopify": ShopifyAdapter(),
    "nuvemshop": NuvemshopAdapter(),
    "woocommerce": WooCommerceAdapter(),
}


async def _get_client_secret(client_id: str, platform: str) -> str:
    """
    Look up the per-client webhook secret from the clients table.
    Matches by pixel_id (client_id sent in the URL) and platform.
    Falls back to DEFAULT_WEBHOOK_SECRET when not found.
    """
    # Only WooCommerce has a dedicated secret column in the current schema
    secret_column = {
        "woocommerce": "woo_webhook_secret",
    }.get(platform)

    if not secret_column:
        return settings.DEFAULT_WEBHOOK_SECRET

    try:
        supabase = get_supabase()
        result = (
            supabase.table("clients")
            .select(secret_column)
            .eq("pixel_id", client_id)
            .eq("is_active", True)
            .maybe_single()
            .execute()
        )
        if result.data and result.data.get(secret_column):
            return result.data[secret_column]
    except Exception as exc:
        logger.warning("Could not fetch client secret for %s/%s: %s", platform, client_id, exc)

    return settings.DEFAULT_WEBHOOK_SECRET


def _store_event(event_dict: dict) -> None:
    """Persist a normalised event to Supabase (best-effort, never raises)."""
    # 'order' is a reserved keyword in PostgreSQL — rename for the column
    if "order" in event_dict:
        event_dict["order_data"] = event_dict.pop("order")
    try:
        get_supabase().table("events").insert(event_dict).execute()
    except Exception as exc:
        logger.error("Failed to persist event: %s", exc)


@router.post(
    "/webhook/{platform}/{client_id}",
    summary="Unified webhook receiver",
    tags=["webhooks"],
)
async def receive_webhook(platform: str, client_id: str, request: Request):
    """
    Single entry-point for all e-commerce platform webhooks.

    Path params:
    - **platform**: `shopify` | `nuvemshop` | `woocommerce`
    - **client_id**: your internal client / store identifier
    """
    if platform not in ADAPTERS:
        raise HTTPException(
            status_code=404,
            detail=f"Platform '{platform}' is not supported. "
                   f"Supported: {', '.join(ADAPTERS)}",
        )

    adapter = ADAPTERS[platform]

    # Read raw body *before* any parsing so the HMAC digest stays valid
    raw_body: bytes = await request.body()
    headers: dict = dict(request.headers)

    try:
        payload_dict: dict = json.loads(raw_body)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Request body is not valid JSON")

    secret = await _get_client_secret(client_id, platform)

    try:
        event = adapter.process(
            payload=raw_body,
            payload_dict=payload_dict,
            headers=headers,
            client_id=client_id,
            secret=secret,
        )
    except SignatureError as exc:
        logger.warning("Signature validation failed for %s/%s: %s", platform, client_id, exc)
        raise HTTPException(status_code=401, detail=str(exc))

    # ── Raw event store (backward-compat) ─────────────────────────────────
    _store_event(event.model_dump(mode="json"))

    # ── Structured v2.0 writes ─────────────────────────────────────────────
    client_uuid = writer.resolve_client_uuid(client_id)
    visitor_uuid = writer.upsert_visitor_by_email(
        client_uuid=client_uuid,
        email=event.customer.email if event.customer else None,
        phone=event.customer.phone if event.customer else None,
        platform_customer_id=event.customer.id if event.customer else None,
        platform=platform,
    )
    order_uuid = writer.write_order(client_uuid, visitor_uuid, event)
    writer.write_webhook_delivery(client_uuid, event, headers, order_uuid, visitor_uuid)

    return {"status": "ok", "event_id": event.event_id, "event_type": event.event_type}
