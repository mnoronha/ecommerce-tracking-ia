"""
Klaviyo webhook receiver.

Klaviyo can POST events (Placed Order, Clicked Email, Opened Email, etc.) to
a custom endpoint. This router receives them, enriches order data with email
attribution context, and logs mid-funnel events as tracking_events so they
appear in the per-client analytics.

Authentication: Klaviyo sends a shared secret in the Authorization header or
as a query param. We validate against the per-client `webhook_secret`.

Setup in Klaviyo:
  Flow → Send Webhook → URL: https://api.noroia.com/webhook/klaviyo/{pixel_id}
  Headers: Authorization: Bearer <webhook_secret>
"""

import hashlib
import hmac
import logging
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from ..database import get_supabase

logger = logging.getLogger(__name__)
router = APIRouter()

# Klaviyo event types we care about
_HANDLED_EVENTS = {
    "Placed Order",
    "Ordered Product",
    "Clicked Email",
    "Opened Email",
    "Unsubscribed",
    "Active on Site",
}


def _resolve_client(pixel_id: str) -> Optional[dict]:
    row = (
        get_supabase()
        .table("clients")
        .select("id, pixel_id, webhook_secret")
        .eq("pixel_id", pixel_id)
        .eq("is_active", True)
        .limit(1)
        .execute()
    )
    return row.data[0] if (row and row.data) else None


def _verify_secret(request: Request, client: dict) -> bool:
    """
    Accept the request if:
    - client has no webhook_secret (open endpoint, Klaviyo test flows), OR
    - Authorization: Bearer <secret> header matches, OR
    - ?secret=<secret> query param matches.
    """
    expected = client.get("webhook_secret")
    if not expected:
        return True
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        return hmac.compare_digest(auth_header[7:], expected)
    query_secret = request.query_params.get("secret", "")
    return hmac.compare_digest(query_secret, expected)


def _event_id_from_klaviyo(event_data: dict) -> str:
    """Derive a stable event_id from Klaviyo's event properties."""
    eid = event_data.get("id") or event_data.get("event_id") or ""
    if eid:
        return f"kl_{eid}"
    # Fallback: hash profile + event type + timestamp
    profile = event_data.get("customer_properties") or {}
    raw = f"kl_{profile.get('email','')}{event_data.get('event','')}{event_data.get('datetime','')}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


@router.post(
    "/webhook/klaviyo/{pixel_id}",
    summary="Receive Klaviyo flow events",
    tags=["webhooks"],
    include_in_schema=True,
)
async def klaviyo_webhook(pixel_id: str, request: Request):
    client = _resolve_client(pixel_id)
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")

    if not _verify_secret(request, client):
        raise HTTPException(status_code=401, detail="Invalid webhook secret")

    try:
        body: Any = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    # Klaviyo can batch events in an array or send a single object
    events = body if isinstance(body, list) else [body]
    processed = 0
    sb = get_supabase()

    for ev in events:
        event_name = ev.get("event") or ev.get("type") or ""
        if event_name not in _HANDLED_EVENTS:
            logger.debug("klaviyo: ignored event type '%s' for %s", event_name, pixel_id)
            continue

        profile   = ev.get("customer_properties") or ev.get("profile") or {}
        email     = profile.get("email") or profile.get("$email") or ""
        props     = ev.get("event_properties") or ev.get("properties") or {}
        event_at  = ev.get("datetime") or ev.get("timestamp") or datetime.now(timezone.utc).isoformat()
        event_id  = _event_id_from_klaviyo(ev)

        try:
            # Find or look up visitor by email
            visitor_id = None
            if email:
                vis_q = (
                    sb.table("visitors")
                    .select("id")
                    .eq("client_id", client["id"])
                    .eq("email", email.lower().strip())
                    .order("last_seen_at", desc=True)
                    .limit(1)
                    .execute()
                )
                if vis_q.data:
                    visitor_id = vis_q.data[0]["id"]

            # Log as tracking event so attribution and funnel analytics pick it up
            tracking_row = {
                "client_id":  client["id"],
                "event_type": f"klaviyo.{event_name.lower().replace(' ', '_')}",
                "event_id":   event_id,
                "visitor_id": visitor_id,
                "properties": {
                    "email":      email,
                    "source":     "klaviyo",
                    "event_name": event_name,
                    **{k: v for k, v in props.items() if k not in ("email",)},
                },
                "created_at": event_at,
            }
            sb.table("tracking_events").upsert(tracking_row, on_conflict="event_id").execute()

            # For Placed Order events, enrich the matching order with Klaviyo source
            if event_name in ("Placed Order", "Ordered Product"):
                order_id_str = str(props.get("OrderId") or props.get("order_id") or "")
                if order_id_str:
                    update = {
                        "klaviyo_attributed": True,
                        "klaviyo_flow":       ev.get("flow_name") or ev.get("campaign_name") or None,
                        "klaviyo_message_id": str(ev.get("message") or ev.get("message_id") or "")[:200] or None,
                    }
                    # Try to update by platform_order_id
                    try:
                        sb.table("orders").update(update).eq("client_id", client["id"]).eq("platform_order_id", order_id_str).execute()
                    except Exception as exc:
                        logger.debug("klaviyo: order enrich failed: %s", exc)

            processed += 1
            logger.info("klaviyo: %s processed for %s (email=%s)", event_name, pixel_id, email[:20] if email else "?")

        except Exception as exc:
            logger.warning("klaviyo: error processing event %s for %s: %s", event_name, pixel_id, exc)

    return JSONResponse({"ok": True, "processed": processed})
