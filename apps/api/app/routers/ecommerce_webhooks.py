import json
import logging
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request

from ..config import settings
from ..database import get_supabase
from ..services import attribution_engine, ga4, google_ads, meta_capi, profitability, writer
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
    secret_column = {
        "shopify":     "shopify_webhook_secret",
        "woocommerce": "woo_webhook_secret",
    }.get(platform)
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


def _attribute_async(order_uuid: str, visitor_uuid: Optional[str]) -> None:  # type: ignore[name-defined]
    """Background helper: load order + visitor, run multi-model attribution."""
    if not order_uuid:
        return
    try:
        sb = get_supabase()
        order_row = (
            sb.table("orders")
            .select("id, client_id, visitor_id, total_price, created_at, financial_status")
            .eq("id", order_uuid)
            .maybe_single()
            .execute()
        )
        if not (order_row and order_row.data):
            return
        order = order_row.data

        visitor = None
        if visitor_uuid:
            v_row = (
                sb.table("visitors")
                .select("id, gclid, utm_history, first_utm_source, first_utm_medium, first_utm_campaign, first_seen_at, last_seen_at")
                .eq("id", visitor_uuid)
                .maybe_single()
                .execute()
            )
            if v_row and v_row.data:
                visitor = v_row.data

        attribution_engine.attribute_order(order, visitor)
    except Exception as exc:
        logger.warning("_attribute_async failed for order %s: %s", order_uuid, exc)


def _dispatch_refund_capi(
    client_pixel_id: str,
    event: object,
) -> None:
    """
    Fire refund event to Meta CAPI (Purchase with negative value) and GA4.
    Runs in a background task — never blocks the webhook response.
    """
    try:
        order = getattr(event, "order", None)
        if not order or not order.id:
            return
        refund_amount = float(getattr(order, "total", 0) or 0)
        if refund_amount <= 0:
            return  # nothing to refund
        currency = getattr(order, "currency", None) or "BRL"
        refund_id = (event.metadata or {}).get("refund_id") if hasattr(event, "metadata") else None  # type: ignore[union-attr]

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

        # Build user_data from email/phone for Meta match
        user_data: dict = {}
        customer = getattr(event, "customer", None)
        if customer:
            import hashlib
            if customer.email:
                user_data["em"] = [hashlib.sha256(customer.email.strip().lower().encode()).hexdigest()]
            if customer.phone:
                phone_clean = "".join(c for c in (customer.phone or "") if c.isdigit())
                if phone_clean:
                    user_data["ph"] = [hashlib.sha256(phone_clean.encode()).hexdigest()]

        # Meta CAPI refund
        if c.get("meta_pixel_id") and c.get("meta_access_token"):
            ok, err = meta_capi.send_refund(
                pixel_id=c["meta_pixel_id"],
                access_token=c["meta_access_token"],
                order_id=str(order.id),
                refund_amount=refund_amount,
                currency=currency,
                refund_id=refund_id,
                user_data=user_data,
                test_event_code=settings.META_TEST_EVENT_CODE or None,
            )
            if ok:
                # Resolve the order_uuid so we can flag the refund row as CAPI-sent.
                try:
                    client_uuid = writer.resolve_client_uuid(client_pixel_id)
                    if client_uuid:
                        ord_row = (
                            get_supabase().table("orders")
                            .select("id")
                            .eq("client_id", client_uuid)
                            .eq("platform_order_id", str(order.id))
                            .limit(1)
                            .execute()
                        )
                        if ord_row and ord_row.data:
                            writer.mark_refund_capi_sent(ord_row.data[0]["id"], refund_id)
                except Exception as exc:
                    logger.debug("mark_refund_capi_sent lookup failed: %s", exc)
            else:
                logger.warning("refund CAPI failed order=%s err=%s", order.id, err)

        # GA4 refund
        if c.get("ga4_measurement_id") and c.get("ga4_api_secret"):
            ga4.send_refund(
                measurement_id=c["ga4_measurement_id"],
                api_secret=c["ga4_api_secret"],
                order_id=str(order.id),
                refund_amount=refund_amount,
                currency=currency,
            )

        logger.info("refund dispatched — client=%s order=%s amount=%.2f",
                    client_pixel_id, order.id, refund_amount)
    except Exception as exc:
        logger.warning("_dispatch_refund_capi error for %s: %s", client_pixel_id, exc)


def _recompute_order_profit_after_refund(order_uuid: str) -> None:
    """Background helper: refresh gross_profit / margin_pct after a refund hits."""
    try:
        profitability.recompute_after_refund(order_uuid)
    except Exception as exc:
        logger.warning("_recompute_order_profit_after_refund failed for %s: %s", order_uuid, exc)


def _record_capi_error(order_uuid: Optional[str], err: str) -> None:
    """Persist capi_last_error on the order so we can diagnose sync-path failures."""
    if not order_uuid or not err:
        return
    try:
        get_supabase().table("orders").update({
            "capi_last_error": err[:500],
        }).eq("id", order_uuid).execute()
    except Exception as exc:
        logger.debug("failed to persist capi_last_error: %s", exc)


def _dispatch_purchase_capi(
    client_pixel_id: str,
    event: object,
    order_uuid: str,
) -> None:
    """
    Fire Purchase event to Meta CAPI + GA4.
    Marks capi_sent=true on the order after successful Meta send.
    Runs in a background task — never blocks the webhook response.
    Skips silently if capi_sent is already True (idempotent on retries).
    """
    try:
        # Guard: skip if already sent (webhook retry / duplicate delivery)
        if order_uuid:
            check = (
                get_supabase().table("orders")
                .select("capi_sent")
                .eq("id", order_uuid)
                .limit(1)
                .execute()
            )
            if check.data and check.data[0].get("capi_sent"):
                logger.info("capi already sent for order %s — skipping", order_uuid)
                return

        # Skip orders with no value — Meta rejects Purchase value=0 with HTTP 400
        # subcode 2804050. These are typically drafts / abandoned carts that
        # Shopify forwards via orders/create webhook before payment is confirmed.
        order = getattr(event, "order", None)
        if order and (order.total or 0) <= 0:
            _record_capi_error(order_uuid, "skipped: order total is 0 or null")
            logger.info("skipping CAPI for zero-value order %s", order_uuid)
            return

        creds_result = (
            get_supabase().table("clients")
            .select(
                "meta_pixel_id, meta_access_token, "
                "ga4_measurement_id, ga4_api_secret, "
                "google_ads_customer_id, google_ads_conversion_action_id, "
                "google_ads_refresh_token"
            )
            .eq("pixel_id", client_pixel_id)
            .limit(1)
            .execute()
        )
        if not (creds_result and creds_result.data):
            _record_capi_error(order_uuid, "client credentials row not found")
            return
        c = creds_result.data[0]

        # ── Fetch visitor attribution data (gclid, fbp, fbc, ip, ua) ─────
        gclid:        str | None = None
        fbp:          str | None = None
        fbc:          str | None = None
        ga_client_id: str | None = None
        client_ip:    str | None = None
        client_ua:    str | None = None
        visitor_uuid: str | None = None
        if order_uuid:
            try:
                ord_row = (
                    get_supabase().table("orders")
                    .select("visitor_id")
                    .eq("id", order_uuid)
                    .limit(1)
                    .execute()
                )
                visitor_uuid = (ord_row.data or [{}])[0].get("visitor_id")
                if visitor_uuid:
                    vis_row = (
                        get_supabase().table("visitors")
                        .select("gclid, fbp, fbc, ga_client_id")
                        .eq("id", visitor_uuid)
                        .limit(1)
                        .execute()
                    )
                    if vis_row.data:
                        gclid = vis_row.data[0].get("gclid")
                        fbp   = vis_row.data[0].get("fbp")
                        fbc   = vis_row.data[0].get("fbc")
                        ga_client_id = vis_row.data[0].get("ga_client_id")
            except Exception as exc:
                logger.debug("visitor attribution lookup failed: %s", exc)

        # ── Last-known IP / UA from the visitor's most recent pixel event ─
        # Meta uses these as fallback identifiers — adds ~1-2 points to EMQ.
        if visitor_uuid:
            try:
                te_row = (
                    get_supabase().table("tracking_events")
                    .select("properties")
                    .eq("visitor_id", visitor_uuid)
                    .order("created_at", desc=True)
                    .limit(1)
                    .execute()
                )
                if te_row.data:
                    props = te_row.data[0].get("properties") or {}
                    client_ip = props.get("ip")
                    client_ua = props.get("user_agent")
            except Exception as exc:
                logger.debug("visitor IP/UA lookup failed: %s", exc)

        # ── Retroactive fallback by email ──────────────────────────────────
        # When the visitor on the order was created fresh from the webhook
        # (no _etv cart attribute), the visitor row has no fbp/fbc/IP/UA.
        # We look up other visitor rows with the same email (any past pixel
        # session for this customer) and harvest their identifiers. This is
        # the common case for stores that don't have our Liquid snippet
        # installed, or guest checkouts that came in via gateway.
        cust_email = (event.customer.email if hasattr(event, "customer") and event.customer else None)
        if cust_email and (not fbp or not fbc or not client_ip):
            try:
                vis_email_q = (
                    get_supabase().table("visitors")
                    .select("id, fbp, fbc, gclid, ga_client_id, last_seen_at")
                    .eq("email", cust_email.strip().lower())
                    .order("last_seen_at", desc=True)
                    .limit(5)
                    .execute()
                )
                candidates = vis_email_q.data or []
                for v in candidates:
                    if v.get("id") == visitor_uuid:
                        continue
                    if not fbp           and v.get("fbp"):           fbp = v["fbp"]
                    if not fbc           and v.get("fbc"):           fbc = v["fbc"]
                    if not gclid         and v.get("gclid"):         gclid = v["gclid"]
                    if not ga_client_id  and v.get("ga_client_id"):  ga_client_id = v["ga_client_id"]
                    if fbp and fbc:
                        break

                # If still no IP/UA, pull from the most recent tracking_event
                # belonging to any of those candidate visitors.
                if not client_ip and candidates:
                    candidate_ids = [v["id"] for v in candidates if v.get("id")]
                    if candidate_ids:
                        te_email_q = (
                            get_supabase().table("tracking_events")
                            .select("properties")
                            .in_("visitor_id", candidate_ids)
                            .order("created_at", desc=True)
                            .limit(1)
                            .execute()
                        )
                        if te_email_q.data:
                            props = te_email_q.data[0].get("properties") or {}
                            client_ip = client_ip or props.get("ip")
                            client_ua = client_ua or props.get("user_agent")
            except Exception as exc:
                logger.debug("retroactive email-based identifier lookup failed: %s", exc)

        # ── Meta CAPI ────────────────────────────────────────────────────
        if c.get("meta_pixel_id") and c.get("meta_access_token"):
            # Enrich event with visitor's fbp/fbc/ip/ua if available
            if (fbp or fbc or client_ip or client_ua) and hasattr(event, "metadata"):
                meta = dict(event.metadata or {})
                if fbp       and not meta.get("fbp"):        meta["fbp"]        = fbp
                if fbc       and not meta.get("fbc"):        meta["fbc"]        = fbc
                if client_ip and not meta.get("ip"):         meta["ip"]         = client_ip
                if client_ua and not meta.get("user_agent"): meta["user_agent"] = client_ua
                object.__setattr__(event, "metadata", meta)
            # Note: meta_capi._build_user_data falls back to fbclid → fbc
            # automatically if event.metadata.fbclid was set by the adapter.

            success, err = meta_capi.send_purchase(
                pixel_id=c["meta_pixel_id"],
                access_token=c["meta_access_token"],
                event=event,  # type: ignore[arg-type]
                test_event_code=settings.META_TEST_EVENT_CODE or None,
            )
            if success and order_uuid:
                writer.mark_capi_sent(order_uuid)
            elif not success:
                _record_capi_error(order_uuid, err or "send_purchase returned False without error")
        else:
            _record_capi_error(order_uuid, "client missing meta_pixel_id or meta_access_token")

        # ── GA4 Measurement Protocol ──────────────────────────────────────
        if c.get("ga4_measurement_id") and c.get("ga4_api_secret"):
            ga4.send_purchase(
                measurement_id=c["ga4_measurement_id"],
                api_secret=c["ga4_api_secret"],
                event=event,  # type: ignore[arg-type]
                ga_client_id=ga_client_id,
            )

        # ── Google Ads Conversion API ─────────────────────────────────────
        if (
            gclid
            and c.get("google_ads_customer_id")
            and c.get("google_ads_conversion_action_id")
            and c.get("google_ads_refresh_token")
            and event.order  # type: ignore[union-attr]
        ):
            google_ads.send_conversion(
                customer_id=c["google_ads_customer_id"],
                conversion_action_id=c["google_ads_conversion_action_id"],
                gclid=gclid,
                value=float(event.order.total or 0),  # type: ignore[union-attr]
                currency=event.order.currency or "BRL",  # type: ignore[union-attr]
                order_id=str(event.order.id),  # type: ignore[union-attr]
                refresh_token=c["google_ads_refresh_token"],
                manager_id=settings.GOOGLE_ADS_MANAGER_ID or None,
            )
    except Exception as exc:
        err = f"{type(exc).__name__}: {str(exc)[:200]}"
        logger.warning("_dispatch_purchase_capi error for %s: %s", client_pixel_id, err)
        _record_capi_error(order_uuid, err)


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

    client_uuid = writer.resolve_client_uuid(client_id)
    meta = event.metadata or {}
    visitor_uuid = writer.upsert_visitor_by_email(
        client_uuid=client_uuid,
        email=event.customer.email if event.customer else None,
        phone=event.customer.phone if event.customer else None,
        platform_customer_id=event.customer.id if event.customer else None,
        platform=platform,
        cart_token=meta.get("cart_token"),
        # Browse-session identifiers injected as cart note_attributes by the JS pixel
        visitor_cookie_id=meta.get("visitor_cookie_id"),
        fbp=meta.get("fbp"),
        fbc=meta.get("fbc"),
        gclid=meta.get("gclid"),
        ga_client_id=meta.get("ga_client_id"),
    )
    order_uuid = writer.write_order(client_uuid, visitor_uuid, event)
    writer.write_webhook_delivery(client_uuid, event, headers, order_uuid, visitor_uuid)

    # Fire Purchase CAPI + GA4 for paid orders (non-blocking, marks capi_sent)
    if event.event_type.value in ("order.paid", "checkout.completed") and order_uuid:
        background_tasks.add_task(_dispatch_purchase_capi, client_id, event, order_uuid)
        # Visitor converted — reset retargeting score so they leave retargeting audiences
        if visitor_uuid:
            background_tasks.add_task(writer.reset_retargeting_score, visitor_uuid)
        # Compute unified attribution across all models (last_click, first_click, linear, time_decay, position_based)
        background_tasks.add_task(_attribute_async, order_uuid, visitor_uuid)

    # Update fulfillment_status when order is fulfilled
    if event.event_type.value == "order.fulfilled" and client_uuid and event.order:
        fulfillment_status = (event.raw_payload or {}).get("fulfillment_status") or "fulfilled"
        writer.update_order_fulfillment(client_uuid, event.order.id, fulfillment_status)

    # Refunds — persist to refunds table, then dispatch negative-value Purchase
    # to Meta CAPI + refund event to GA4 in the background.
    if event.event_type.value == "order.refunded":
        refund_order_uuid = writer.write_refund(client_uuid, event)
        background_tasks.add_task(_dispatch_refund_capi, client_id, event)
        if refund_order_uuid:
            background_tasks.add_task(_recompute_order_profit_after_refund, refund_order_uuid)

    return {"status": "ok", "event_id": event.event_id, "event_type": event.event_type}
