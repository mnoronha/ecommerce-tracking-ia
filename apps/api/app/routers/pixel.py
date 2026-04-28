import logging
import uuid
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Request, Response
from pydantic import BaseModel

from ..config import settings
from ..database import get_supabase
from ..limiter import limiter
from ..services import ga4, google_ads, meta_capi, writer
from ..models.events import EventType, NormalizedEvent, UTMParams

logger = logging.getLogger(__name__)

router = APIRouter()

# ── 1x1 transparent GIF ───────────────────────────────────────────────────────
_TRANSPARENT_GIF = (
    b"GIF89a\x01\x00\x01\x00\x80\x00\x00\xff\xff\xff"
    b"\x00\x00\x00!\xf9\x04\x00\x00\x00\x00\x00,"
    b"\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02D\x01\x00;"
)

# Events that trigger CAPI server-side (complement browser pixel)
_CAPI_PIXEL_EVENTS = {
    EventType.PRODUCT_VIEWED,
    EventType.CART_CREATED,
    EventType.CART_UPDATED,
    EventType.CHECKOUT_STARTED,
}

_PIXEL_EVENT_MAP: dict = {
    "pageview":           EventType.PAGE_VIEWED,
    "page_viewed":        EventType.PAGE_VIEWED,
    "product_viewed":     EventType.PRODUCT_VIEWED,
    "view_product":       EventType.PRODUCT_VIEWED,
    "add_to_cart":        EventType.CART_CREATED,
    "cart_created":       EventType.CART_CREATED,
    "cart_updated":       EventType.CART_UPDATED,
    "checkout_started":   EventType.CHECKOUT_STARTED,
    "begin_checkout":     EventType.CHECKOUT_STARTED,
    "checkout_completed": EventType.CHECKOUT_COMPLETED,
    "purchase":           EventType.CHECKOUT_COMPLETED,
}


# ── Request schema ─────────────────────────────────────────────────────────────

class UTMData(BaseModel):
    source:   Optional[str] = None
    medium:   Optional[str] = None
    campaign: Optional[str] = None
    term:     Optional[str] = None
    content:  Optional[str] = None


class PixelEventRequest(BaseModel):
    client_id:    str
    event_type:   str = "pageview"
    event_id:     Optional[str] = None  # Optional client-provided event_id (used for dedup with CAPI)
    visitor_id:   Optional[str] = None
    session_id:   Optional[str] = None
    page_url:     Optional[str] = None
    referrer:     Optional[str] = None
    utm:          Optional[UTMData] = None
    metadata:     Optional[dict] = None
    timestamp:    Optional[datetime] = None
    # ── Advertising identifiers ──────────────────────────────────────────────
    fbp:          Optional[str] = None  # Meta browser ID (_fbp cookie)
    fbc:          Optional[str] = None  # Meta click ID (_fbc cookie / fbclid)
    ga_client_id: Optional[str] = None  # GA4 client ID (_ga cookie)
    gclid:        Optional[str] = None  # Google click ID (gclid URL param)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _build_normalized(
    data: PixelEventRequest,
    user_agent: Optional[str],
    ip: Optional[str],
) -> NormalizedEvent:
    event_type = _PIXEL_EVENT_MAP.get(data.event_type, EventType.CUSTOM)
    return NormalizedEvent(
        event_id=data.event_id or str(uuid.uuid4()),
        event_type=event_type,
        platform="pixel",
        client_id=data.client_id,
        timestamp=data.timestamp or datetime.utcnow(),
        visitor_id=data.visitor_id,
        session_id=data.session_id,
        page_url=data.page_url,
        referrer=data.referrer,
        utm=UTMParams(**(data.utm.model_dump() if data.utm else {})),
        metadata={
            **(data.metadata or {}),
            "user_agent":   user_agent,
            "ip":           ip,
            "fbp":          data.fbp,
            "fbc":          data.fbc,
            "ga_client_id": data.ga_client_id,
            "gclid":        data.gclid,
        },
    )


def _persist(event: NormalizedEvent) -> None:
    event_dict = event.model_dump(mode="json")
    if "order" in event_dict:
        event_dict["order_data"] = event_dict.pop("order")
    try:
        get_supabase().table("events").insert(event_dict).execute()
    except Exception as exc:
        logger.error("Failed to persist pixel event: %s", exc)


def _dispatch_pixel_ga4(client_pixel_id: str, event: NormalizedEvent) -> None:
    """Send view_item / add_to_cart / begin_checkout to GA4 Measurement Protocol."""
    try:
        creds = (
            get_supabase().table("clients")
            .select("ga4_measurement_id, ga4_api_secret")
            .eq("pixel_id", client_pixel_id)
            .limit(1)
            .execute()
        )
        if not (creds and creds.data):
            return
        c = creds.data[0]
        if c.get("ga4_measurement_id") and c.get("ga4_api_secret"):
            ga4.send_pixel_event(
                measurement_id=c["ga4_measurement_id"],
                api_secret=c["ga4_api_secret"],
                event=event,
            )
    except Exception as exc:
        logger.warning("_dispatch_pixel_ga4 error: %s", exc)


def _dispatch_pixel_google_ads(
    client_pixel_id: str,
    event: NormalizedEvent,
    gclid: str,
    action_column: str,
) -> None:
    """Upload AddToCart or Checkout conversion to Google Ads when visitor has a gclid."""
    try:
        creds = (
            get_supabase().table("clients")
            .select(f"google_ads_customer_id, google_ads_refresh_token, {action_column}")
            .eq("pixel_id", client_pixel_id)
            .limit(1)
            .execute()
        )
        if not (creds and creds.data):
            return
        c = creds.data[0]
        action_id = c.get(action_column)
        if not (c.get("google_ads_customer_id") and action_id and c.get("google_ads_refresh_token")):
            return
        google_ads.send_conversion(
            customer_id=c["google_ads_customer_id"],
            conversion_action_id=action_id,
            gclid=gclid,
            value=float((event.metadata or {}).get("product_price") or 0),
            currency="BRL",
            refresh_token=c["google_ads_refresh_token"],
            order_id=event.event_id,
            manager_id=settings.GOOGLE_ADS_MANAGER_ID or None,
        )
    except Exception as exc:
        logger.warning("_dispatch_pixel_google_ads error for %s: %s", client_pixel_id, exc)


def _dispatch_pixel_capi(client_pixel_id: str, event: NormalizedEvent) -> None:
    """Send ViewContent / AddToCart / InitiateCheckout to Meta CAPI (background)."""
    try:
        creds = (
            get_supabase().table("clients")
            .select("meta_pixel_id, meta_access_token")
            .eq("pixel_id", client_pixel_id)
            .limit(1)
            .execute()
        )
        if not (creds and creds.data):
            return
        c = creds.data[0]
        if c.get("meta_pixel_id") and c.get("meta_access_token"):
            meta_capi.send_pixel_event(
                pixel_id=c["meta_pixel_id"],
                access_token=c["meta_access_token"],
                event=event,
                test_event_code=settings.META_TEST_EVENT_CODE or None,
            )
    except Exception as exc:
        logger.warning("_dispatch_pixel_capi error: %s", exc)


# ── Routes ────────────────────────────────────────────────────────────────────

@router.post(
    "/pixel/events",
    summary="Receive JS pixel events (Beacon API / fetch)",
    tags=["pixel"],
)
@limiter.limit("100/minute")
async def receive_pixel_event(
    request: Request,
    body: PixelEventRequest,
    background_tasks: BackgroundTasks,
):
    event = _build_normalized(
        data=body,
        user_agent=request.headers.get("user-agent"),
        ip=request.client.host if request.client else None,
    )

    _persist(event)

    client_uuid  = writer.resolve_client_uuid(body.client_id)
    visitor_uuid = writer.upsert_visitor_by_cookie(
        client_uuid=client_uuid,
        visitor_cookie_id=body.visitor_id or "",
        utm_source=body.utm.source     if body.utm else None,
        utm_medium=body.utm.medium     if body.utm else None,
        utm_campaign=body.utm.campaign if body.utm else None,
        gclid=body.gclid,
        fbclid=body.fbc,
        fbp=body.fbp,
        fbc=body.fbc,
        cart_token=(body.metadata or {}).get("cart_token"),
        ga_client_id=body.ga_client_id,
    )
    writer.write_tracking_event(client_uuid, visitor_uuid, event)

    if visitor_uuid:
        background_tasks.add_task(writer.update_lead_score, visitor_uuid, event.event_type)

    # Link cookie visitor to email when checkout_completed carries customer_email
    # This ensures the webhook (order.paid) finds the same visitor record
    if event.event_type == EventType.CHECKOUT_COMPLETED and visitor_uuid:
        email_from_pixel = (body.metadata or {}).get("customer_email")
        if email_from_pixel:
            writer.set_visitor_email(visitor_uuid, email_from_pixel)

    # Fire CAPI + GA4 for mid-funnel events (ViewContent, AddToCart, InitiateCheckout)
    if event.event_type in _CAPI_PIXEL_EVENTS:
        background_tasks.add_task(_dispatch_pixel_capi, body.client_id, event)
        background_tasks.add_task(_dispatch_pixel_ga4, body.client_id, event)

    # Google Ads mid-funnel conversions (AddToCart, Checkout) — only when gclid present
    if body.gclid:
        if event.event_type == EventType.CART_CREATED:
            background_tasks.add_task(
                _dispatch_pixel_google_ads,
                body.client_id, event, body.gclid,
                "google_ads_add_to_cart_action_id",
            )
        elif event.event_type == EventType.CHECKOUT_STARTED:
            background_tasks.add_task(
                _dispatch_pixel_google_ads,
                body.client_id, event, body.gclid,
                "google_ads_checkout_action_id",
            )

    return {"status": "ok", "event_id": event.event_id}


@router.get(
    "/pixel/events",
    summary="Image-pixel fallback (1×1 GIF)",
    tags=["pixel"],
    response_class=Response,
)
async def pixel_image_fallback(
    request: Request,
    cid: str = "",
    et: str = "pageview",
    vid: Optional[str] = None,
    url: Optional[str] = None,
    ref: Optional[str] = None,
):
    if cid:
        body = PixelEventRequest(
            client_id=cid,
            event_type=et,
            visitor_id=vid,
            page_url=url,
            referrer=ref,
        )
        event = _build_normalized(
            data=body,
            user_agent=request.headers.get("user-agent"),
            ip=request.client.host if request.client else None,
        )
        _persist(event)

    return Response(
        content=_TRANSPARENT_GIF,
        media_type="image/gif",
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma":        "no-cache",
            "Expires":       "0",
        },
    )
