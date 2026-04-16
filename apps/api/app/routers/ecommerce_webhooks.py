import json
import logging

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request

from ..config import settings
from ..database import get_supabase
from ..services import ga4, meta_capi, writer
from ..services.adapters import (
    NuvemshopAdapter,
    ShopifyAdapter,
    SignatureError,
    WooCommerceAdapter,
)

logger = logging.getLogger(__name__)

router = APIRouter()

ADAPTERS = {
    "shopify":     ShopifyAdapter(),
    "nuvemshop":   NuvemshopAdapter(),
    "woocommerce": WooCommerceAdapter(),
}


async def _get_client_secret(client_id: str, platform: str) -> str:
    secret_column = {"woocommerce": "woo_webhook_secret"}.get(platform)
    if not secret_column:
        return settings.DEFAULT_WEBHOOK_SECRET
    try:
        result = (
            get_supabase().table("clients")
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
    if "order" in event_dict:
        event_dict["order_data"] = event_dict.pop("order")
    try:
        get_supabase().table("events").insert(event_dict).execute()
    except Exception as exc:
        logger.error("Failed to persist event: %s", exc)


def _dispatch_purchase_capi(
    client_pixel_id: str,
    event: object,
    order_uuid: str,
) -> None:
    """
    Fire Purchase event to Meta CAPI + GA4.
    Marks capi_sent=true on the order after successful Meta send.
    Runs in a background task — never blocks the webhook response.
    """
    try:
        creds_result = (
            get_supabase().table("clients")
            .select("meta_pixel_id, meta_access_token, ga4_measurement_id, ga4_api_secret")
            .eq("pixel_id", client_pixel_id)
            .limit(1)
            .execute()
        )
        if not (creds_result and creds_result.data):
            return
        c = creds_result.data[0]

        if c.get("meta_pixel_id") and c.get("meta_access_token"):
            success = meta_capi.send_purchase(
                pixel_id=c["meta_pixel_id"],
                access_token=c["meta_access_token"],
                event=event,  # type: ignore[arg-type]
            )
            if success and order_uuid:
                writer.mark_capi_sent(order_uuid)

        if c.get("ga4_measurement_id") and c.get("ga4_api_secret"):
            ga4.send_purchase(
                measurement_id=c["ga4_measurement_id"],
                api_secret=c["ga4_api_secret"],
                event=event,  # type: ignore[arg-type]
            )
    except Exception as exc:
        logger.warning("_dispatch_purchase_capi error for %s: %s", client_pixel_id, exc)


@router.post(
    "/webhook/{platform}/{client_id}",
    summary="Unified webhook receiver",
    tags=["webhooks"],
)
async def receive_webhook(
    platform: str,
    client_id: str,
    request: Request,
    background_tasks: BackgroundTasks,
):
    if platform not in ADAPTERS:
        raise HTTPException(
            status_code=404,
            detail=f"Platform '{platform}' not supported. Supported: {', '.join(ADAPTERS)}",
        )

    raw_body: bytes = await request.body()
    headers: dict = dict(request.headers)

    try:
        payload_dict: dict = json.loads(raw_body)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Request body is not valid JSON")

    secret = await _get_client_secret(client_id, platform)

    try:
        event = ADAPTERS[platform].process(
            payload=raw_body,
            payload_dict=payload_dict,
            headers=headers,
            client_id=client_id,
            secret=secret,
        )
    except SignatureError as exc:
        logger.warning("Signature validation failed for %s/%s: %s", platform, client_id, exc)
        raise HTTPException(status_code=401, detail=str(exc))

    _store_event(event.model_dump(mode="json"))

    client_uuid  = writer.resolve_client_uuid(client_id)
    visitor_uuid = writer.upsert_visitor_by_email(
        client_uuid=client_uuid,
        email=event.customer.email if event.customer else None,
        phone=event.customer.phone if event.customer else None,
        platform_customer_id=event.customer.id if event.customer else None,
        platform=platform,
    )
    order_uuid = writer.write_order(client_uuid, visitor_uuid, event)
    writer.write_webhook_delivery(client_uuid, event, headers, order_uuid, visitor_uuid)

    # Fire Purchase CAPI + GA4 for paid orders (non-blocking, marks capi_sent)
    if event.event_type.value in ("order.paid", "checkout.completed") and order_uuid:
        background_tasks.add_task(_dispatch_purchase_capi, client_id, event, order_uuid)

    return {"status": "ok", "event_id": event.event_id, "event_type": event.event_type}
