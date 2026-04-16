import logging
import uuid
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel

from ..database import get_supabase
from ..services import writer
from ..models.events import EventType, NormalizedEvent, UTMParams

logger = logging.getLogger(__name__)

router = APIRouter()

# ── 1x1 transparent GIF bytes ─────────────────────────────────────────────────
_TRANSPARENT_GIF = (
    b"GIF89a\x01\x00\x01\x00\x80\x00\x00\xff\xff\xff"
    b"\x00\x00\x00!\xf9\x04\x00\x00\x00\x00\x00,"
    b"\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02D\x01\x00;"
)

_PIXEL_EVENT_MAP: dict = {
    # pageview
    "pageview":             EventType.PAGE_VIEWED,
    "page_viewed":          EventType.PAGE_VIEWED,
    # product
    "product_viewed":       EventType.PRODUCT_VIEWED,
    "view_product":         EventType.PRODUCT_VIEWED,
    # cart — FIX: add_to_cart was missing → was falling through to CUSTOM
    "add_to_cart":          EventType.CART_CREATED,
    "cart_created":         EventType.CART_CREATED,
    "cart_updated":         EventType.CART_UPDATED,
    # checkout
    "checkout_started":     EventType.CHECKOUT_STARTED,
    "begin_checkout":       EventType.CHECKOUT_STARTED,
    # purchase
    "checkout_completed":   EventType.CHECKOUT_COMPLETED,
    "purchase":             EventType.CHECKOUT_COMPLETED,
}


# ── Request schema ─────────────────────────────────────────────────────────────

class UTMData(BaseModel):
    source: Optional[str] = None
    medium: Optional[str] = None
    campaign: Optional[str] = None
    term: Optional[str] = None
    content: Optional[str] = None


class PixelEventRequest(BaseModel):
    client_id: str
    event_type: str = "pageview"
    visitor_id: Optional[str] = None
    session_id: Optional[str] = None
    page_url: Optional[str] = None
    referrer: Optional[str] = None
    utm: Optional[UTMData] = None
    metadata: Optional[dict] = None
    timestamp: Optional[datetime] = None
    # ── Advertising identifiers (critical for CAPI match rate) ──────────────
    fbp: Optional[str] = None           # Meta Pixel browser ID (_fbp cookie)
    fbc: Optional[str] = None           # Meta click ID (_fbc cookie / fbclid param)
    ga_client_id: Optional[str] = None  # GA4 client ID (_ga cookie)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _build_normalized(
    data: PixelEventRequest,
    user_agent: Optional[str],
    ip: Optional[str],
) -> NormalizedEvent:
    event_type = _PIXEL_EVENT_MAP.get(data.event_type, EventType.CUSTOM)
    return NormalizedEvent(
        event_id=str(uuid.uuid4()),
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
            "user_agent": user_agent,
            "ip": ip,
            # Advertising identifiers — stored for CAPI passthrough
            "fbp":          data.fbp,
            "fbc":          data.fbc,
            "ga_client_id": data.ga_client_id,
        },
    )


def _persist(event: NormalizedEvent) -> None:
    """Best-effort persist; never raises."""
    event_dict = event.model_dump(mode="json")
    if "order" in event_dict:
        event_dict["order_data"] = event_dict.pop("order")
    try:
        get_supabase().table("events").insert(event_dict).execute()
    except Exception as exc:
        logger.error("Failed to persist pixel event: %s", exc)


# ── Routes ────────────────────────────────────────────────────────────────────

@router.post(
    "/pixel/events",
    summary="Receive JS pixel events (Beacon API / fetch)",
    tags=["pixel"],
)
async def receive_pixel_event(body: PixelEventRequest, request: Request):
    """
    Receives tracking events sent by the pixel JavaScript snippet via
    the Beacon API (or `fetch` fallback).
    """
    event = _build_normalized(
        data=body,
        user_agent=request.headers.get("user-agent"),
        ip=request.client.host if request.client else None,
    )
    # ── Raw event store (backward-compat) ─────────────────────────────────
    _persist(event)

    # ── Structured v2.0 writes ─────────────────────────────────────────────
    client_uuid = writer.resolve_client_uuid(body.client_id)
    visitor_uuid = writer.upsert_visitor_by_cookie(
        client_uuid=client_uuid,
        visitor_cookie_id=body.visitor_id or "",
        utm_source=body.utm.source if body.utm else None,
        utm_medium=body.utm.medium if body.utm else None,
        utm_campaign=body.utm.campaign if body.utm else None,
    )
    writer.write_tracking_event(client_uuid, visitor_uuid, event)

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
    """
    Fallback for browsers that block the Beacon API.
    Returns a 1×1 transparent GIF while persisting the event.
    """
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
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )
