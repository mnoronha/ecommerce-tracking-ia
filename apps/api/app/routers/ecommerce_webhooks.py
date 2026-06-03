import json
import logging
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request

from ..config import settings
from ..database import get_supabase
from ..services import attribution_engine, crypto, ga4, google_ads, meta_capi, pinterest_capi, profitability, tiktok_capi, writer
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

# Shopify source_name values that represent OFFLINE / manual sales — never sent
# as ad conversions (POS = loja física, draft = pedido manual no admin).
_OFFLINE_SOURCE_NAMES = {"pos", "shopify_draft_order"}


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
            return crypto.decrypt_secret(result.data[secret_column])
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
            .select("id, client_id, visitor_id, total_price, created_at, financial_status, "
                    "utm_source, utm_medium, utm_campaign, utm_content, email")
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
                .select("id, visitor_id, gclid, utm_history, first_utm_source, first_utm_medium, first_utm_campaign, first_seen_at, last_seen_at")
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
        c = crypto.decrypt_client_secrets(creds_result.data[0])

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


def _record_tiktok_result(order_uuid: Optional[str], ok: bool, err: Optional[str]) -> None:
    """Persist TikTok Events API outcome on the order."""
    if not order_uuid:
        return
    update: dict = {"tiktok_sent": bool(ok)}
    if ok:
        from datetime import datetime, timezone
        update["tiktok_sent_at"]   = datetime.now(timezone.utc).isoformat()
        update["tiktok_last_error"] = None
    else:
        update["tiktok_last_error"] = (err or "unknown")[:500]
    try:
        get_supabase().table("orders").update(update).eq("id", order_uuid).execute()
    except Exception as exc:
        logger.debug("failed to persist tiktok result: %s", exc)


def _record_pinterest_result(order_uuid: Optional[str], ok: bool, err: Optional[str]) -> None:
    """Persist Pinterest Conversions API outcome on the order."""
    if not order_uuid:
        return
    update: dict = {"pinterest_sent": bool(ok)}
    if ok:
        from datetime import datetime, timezone
        update["pinterest_sent_at"]   = datetime.now(timezone.utc).isoformat()
        update["pinterest_last_error"] = None
    else:
        update["pinterest_last_error"] = (err or "unknown")[:500]
    try:
        get_supabase().table("orders").update(update).eq("id", order_uuid).execute()
    except Exception as exc:
        logger.debug("failed to persist pinterest result: %s", exc)


def _record_google_result(
    order_uuid: Optional[str],
    ok: bool,
    err: Optional[str],
    match_type: Optional[str],
) -> None:
    """Persist the Google Ads conversion outcome (mirrors capi_sent for Meta)."""
    if not order_uuid:
        return
    update: dict = {
        "google_sent":       bool(ok),
        "google_match_type": match_type,
        # On success `err` may carry a diagnostic note (e.g. click id rejected,
        # fell back to enhanced); persist it so the reason isn't only in logs.
        "google_last_error": (err[:500] if err else None) if ok else (err or "unknown")[:500],
    }
    if ok:
        from datetime import datetime, timezone
        update["google_sent_at"] = datetime.now(timezone.utc).isoformat()
    try:
        get_supabase().table("orders").update(update).eq("id", order_uuid).execute()
    except Exception as exc:
        logger.debug("failed to persist google conversion result: %s", exc)


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

        # Skip orders that are not actually paid — even if we received order.paid webhook,
        # the order's financial_status might be "expired", "pending", "refunded", etc.
        # Only send conversions for orders with financial_status="paid"
        if order and order.status and order.status.lower() != "paid":
            _record_capi_error(order_uuid, f"skipped: order financial_status is '{order.status}', not 'paid'")
            logger.info("skipping CAPI for non-paid order %s (status=%s)", order_uuid, order.status)
            return

        # Skip OFFLINE sales — POS (loja física) and manual draft orders are not
        # digital conversions. Sending them to Meta/Google would let those
        # platforms match the customer's email/phone to an ad view and falsely
        # credit a balcony sale to a campaign — inflating ROAS and teaching the
        # bidding algorithm to chase the wrong audience. They still count in the
        # dashboard (store revenue), just never as ad conversions.
        source_name = (event.metadata or {}).get("source_name") if hasattr(event, "metadata") else None
        if (source_name or "").lower() in _OFFLINE_SOURCE_NAMES:
            _record_capi_error(order_uuid, f"skipped: offline sale (source_name={source_name})")
            logger.info("skipping ad-conversion for offline order %s (source=%s)", order_uuid, source_name)
            return

        creds_result = (
            get_supabase().table("clients")
            .select(
                "meta_pixel_id, meta_access_token, "
                "ga4_measurement_id, ga4_api_secret, "
                "google_ads_customer_id, google_ads_conversion_action_id, "
                "google_ads_refresh_token, google_ads_login_customer_id, "
                "tiktok_pixel_id, tiktok_access_token, "
                "pinterest_ad_account_id, pinterest_access_token, pinterest_tag_id, "
                "value_based_bidding"
            )
            .eq("pixel_id", client_pixel_id)
            .limit(1)
            .execute()
        )
        if not (creds_result and creds_result.data):
            _record_capi_error(order_uuid, "client credentials row not found")
            return
        c = crypto.decrypt_client_secrets(creds_result.data[0])

        # Resolve the bid value: when value-based bidding is enabled and the
        # order has a predicted_ltv, send the LTV instead of the order total.
        # This is what teaches Meta/Google to optimize for high-LTV buyers.
        bid_value_override: Optional[float] = None
        if c.get("value_based_bidding") and order_uuid:
            try:
                ltv_row = (
                    get_supabase().table("orders")
                    .select("predicted_ltv")
                    .eq("id", order_uuid)
                    .limit(1)
                    .execute()
                )
                if ltv_row and ltv_row.data:
                    val = ltv_row.data[0].get("predicted_ltv")
                    if val is not None:
                        bid_value_override = float(val)
            except Exception as exc:
                logger.debug("predicted_ltv lookup failed for %s: %s", order_uuid, exc)

        # ── Fetch visitor attribution data (gclid, fbp, fbc, ip, ua) ─────
        gclid:        str | None = None
        fbp:          str | None = None
        fbc:          str | None = None
        ga_client_id: str | None = None
        ttclid:       str | None = None
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
                        .select("gclid, fbp, fbc, ga_client_id, ttclid")
                        .eq("id", visitor_uuid)
                        .limit(1)
                        .execute()
                    )
                    if vis_row.data:
                        gclid        = vis_row.data[0].get("gclid")
                        fbp          = vis_row.data[0].get("fbp")
                        fbc          = vis_row.data[0].get("fbc")
                        ga_client_id = vis_row.data[0].get("ga_client_id")
                        ttclid       = vis_row.data[0].get("ttclid")
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
                    if not ttclid        and v.get("ttclid"):        ttclid = v["ttclid"]
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
                value_override=bid_value_override,
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
        # Fire whenever credentials + an order are available. send_conversion
        # internally prefers gclid for click-attributed conversions and falls
        # back to Enhanced Conversions for Leads (hashed email/phone) when no
        # gclid is present — covers organic/direct/social/email-driven sales.
        if (
            c.get("google_ads_customer_id")
            and c.get("google_ads_conversion_action_id")
            and c.get("google_ads_refresh_token")
            and event.order  # type: ignore[union-attr]
        ):
            cust = getattr(event, "customer", None)
            email = cust.email if cust else None
            phone = cust.phone if cust else None
            ev_meta = event.metadata or {}
            # Fall back to the gclid relayed on the order itself (Shopify cart
            # note_attribute `_gclid`) when the visitor record has none. The
            # order-carried value survives visitor-cookie linkage failures at
            # webhook time, which is why visitor-only capture was stuck at ~32%
            # of paid Google clicks.
            gclid  = gclid or ev_meta.get("gclid")
            gbraid = ev_meta.get("gbraid")
            wbraid = ev_meta.get("wbraid")
            if gclid or gbraid or wbraid or email or phone:
                g_ok, g_err, g_match = google_ads.send_conversion(
                    customer_id=c["google_ads_customer_id"],
                    conversion_action_id=c["google_ads_conversion_action_id"],
                    value=float(event.order.total or 0),  # type: ignore[union-attr]
                    currency=event.order.currency or "BRL",  # type: ignore[union-attr]
                    refresh_token=c["google_ads_refresh_token"],
                    gclid=gclid,
                    gbraid=gbraid,
                    wbraid=wbraid,
                    email=email,
                    phone=phone,
                    order_id=str(event.order.id),  # type: ignore[union-attr]
                    # per-client MCC first, agency-wide fallback
                    manager_id=c.get("google_ads_login_customer_id") or settings.GOOGLE_ADS_MANAGER_ID or None,
                    value_override=bid_value_override,
                )
                _record_google_result(order_uuid, g_ok, g_err, g_match)

        # ── TikTok Events API ────────────────────────────────────────────
        if c.get("tiktok_pixel_id") and c.get("tiktok_access_token"):
            tt_ok, tt_err = tiktok_capi.send_purchase(
                pixel_code=c["tiktok_pixel_id"],
                access_token=c["tiktok_access_token"],
                event=event,  # type: ignore[arg-type]
                ttclid=ttclid,
                value_override=bid_value_override,
            )
            _record_tiktok_result(order_uuid, tt_ok, tt_err)

        # ── Pinterest Conversions API ─────────────────────────────────────
        if (
            c.get("pinterest_ad_account_id")
            and c.get("pinterest_access_token")
            and c.get("pinterest_tag_id")
        ):
            pin_ok, pin_err = pinterest_capi.send_purchase(
                ad_account_id=c["pinterest_ad_account_id"],
                access_token=c["pinterest_access_token"],
                tag_id=c["pinterest_tag_id"],
                event=event,  # type: ignore[arg-type]
                value_override=bid_value_override,
            )
            _record_pinterest_result(order_uuid, pin_ok, pin_err)

    except Exception as exc:
        err = f"{type(exc).__name__}: {str(exc)[:200]}"
        logger.warning("_dispatch_purchase_capi error for %s: %s", client_pixel_id, err)
        _record_capi_error(order_uuid, err)


def _dispatch_funnel_capi(client_pixel_id: str, event: object) -> None:
    """
    Server-side mid-funnel events (AddToCart + InitiateCheckout) via Shopify
    `carts/create` and `checkouts/create` / `checkouts/update` webhooks.

    Runs in addition to the pixel — when the browser pixel is blocked by an
    adblocker or never fires (mobile in-app browser, redirect to PIX gateway
    before JS runs), the webhook still arrives and Meta / GA4 / Google Ads
    get the funnel event anyway.

    event_id is derived deterministically from the cart/checkout token so
    duplicate webhook deliveries collapse in each platform's dedup window.
    The browser snippet still uses random UUIDs, so rare double-fires can
    happen — accepted in exchange for never missing the signal when JS is
    blocked.

    Why we only fire on `carts/create` (not `carts/update`): updates fire
    on every quantity change and would flood Meta with duplicate AddToCart
    events. The create webhook captures the moment that matters — first
    item dropped into the cart.
    """
    import hashlib

    try:
        topic = (getattr(event, "metadata", {}) or {}).get("topic", "")

        # Decide event class from the Shopify topic
        if topic == "carts/create":
            kind            = "atc"
            event_id_prefix = "atc_shopify_"
        elif topic in ("checkouts/create", "checkouts/update"):
            kind            = "ic"
            event_id_prefix = "ic_shopify_"
        else:
            return

        raw = event.raw_payload or {}
        token = raw.get("token") or raw.get("cart_token") or raw.get("id") or ""
        if not token:
            return

        # Defensive: never relay offline (POS / draft) funnel events as ad signals.
        src = (raw.get("source_name") or (event.metadata or {}).get("source_name") or "").lower()
        if src in _OFFLINE_SOURCE_NAMES:
            return

        creds_result = (
            get_supabase().table("clients")
            .select(
                "meta_pixel_id, meta_access_token, "
                "ga4_measurement_id, ga4_api_secret, "
                "google_ads_customer_id, google_ads_refresh_token, google_ads_login_customer_id, "
                "google_ads_add_to_cart_action_id, google_ads_checkout_action_id, "
                "tiktok_pixel_id, tiktok_access_token, "
                "pinterest_ad_account_id, pinterest_access_token, pinterest_tag_id"
            )
            .eq("pixel_id", client_pixel_id)
            .limit(1)
            .execute()
        )
        if not (creds_result and creds_result.data):
            return
        c = crypto.decrypt_client_secrets(creds_result.data[0])

        # Enrich event with cart totals / item count so each platform carries
        # a meaningful `value` instead of zero.
        try:
            line_items = raw.get("line_items") or []
            total = float(raw.get("total_price") or raw.get("total_line_items_price") or 0)
            if not total and line_items:
                total = sum(float(li.get("price") or 0) * int(li.get("quantity") or 1) for li in line_items)
            num_items = sum(int(li.get("quantity") or 1) for li in line_items)
            meta_dict = dict(event.metadata or {})
            meta_dict.setdefault("cart_total", total)
            meta_dict.setdefault("item_count", num_items)
            if line_items:
                first = line_items[0]
                meta_dict.setdefault("product_id",    first.get("product_id") or first.get("id"))
                meta_dict.setdefault("product_name",  first.get("title") or first.get("name"))
                meta_dict.setdefault("product_price", float(first.get("price") or 0))
            object.__setattr__(event, "metadata", meta_dict)
        except Exception as exc:
            logger.debug("funnel enrich failed: %s", exc)

        # Deterministic event_id — re-deliveries collapse in dedup windows
        det_id = hashlib.sha256(f"{event_id_prefix}{token}".encode()).hexdigest()
        object.__setattr__(event, "event_id", det_id)

        # ── Meta CAPI (AddToCart or InitiateCheckout) ─────────────────────
        if c.get("meta_pixel_id") and c.get("meta_access_token"):
            try:
                ok, err = meta_capi.send_pixel_event(
                    pixel_id=c["meta_pixel_id"],
                    access_token=c["meta_access_token"],
                    event=event,  # type: ignore[arg-type]
                    test_event_code=settings.META_TEST_EVENT_CODE or None,
                )
                if not ok:
                    logger.debug("funnel CAPI failed (%s): %s", kind, err)
            except Exception as exc:
                logger.debug("funnel meta send failed (%s): %s", kind, exc)

        # ── GA4 add_to_cart / begin_checkout ──────────────────────────────
        if c.get("ga4_measurement_id") and c.get("ga4_api_secret"):
            try:
                ga4.send_pixel_event(
                    measurement_id=c["ga4_measurement_id"],
                    api_secret=c["ga4_api_secret"],
                    event=event,  # type: ignore[arg-type]
                )
            except Exception as exc:
                logger.debug("funnel ga4 send failed (%s): %s", kind, exc)

        # ── TikTok AddToCart / InitiateCheckout ───────────────────────────
        if c.get("tiktok_pixel_id") and c.get("tiktok_access_token"):
            try:
                tiktok_capi.send_pixel_event(
                    pixel_code=c["tiktok_pixel_id"],
                    access_token=c["tiktok_access_token"],
                    event=event,  # type: ignore[arg-type]
                )
            except Exception as exc:
                logger.debug("funnel tiktok send failed (%s): %s", kind, exc)

        # ── Pinterest AddToCart / Checkout ────────────────────────────────
        if (
            c.get("pinterest_ad_account_id")
            and c.get("pinterest_access_token")
            and c.get("pinterest_tag_id")
        ):
            try:
                from ..services import pinterest_capi
                pinterest_capi.send_pixel_event(
                    ad_account_id=c["pinterest_ad_account_id"],
                    access_token=c["pinterest_access_token"],
                    tag_id=c["pinterest_tag_id"],
                    event=event,  # type: ignore[arg-type]
                )
            except Exception as exc:
                logger.debug("funnel pinterest send failed (%s): %s", kind, exc)

        # ── Google Ads Enhanced Conversion (cart_total + hashed email/phone)
        # Carts have no customer attached, so Google Ads only fires on the
        # checkout step (where Shopify includes email + phone in the payload).
        if kind == "ic":
            action_col = (
                "google_ads_checkout_action_id" if kind == "ic"
                else "google_ads_add_to_cart_action_id"
            )
            action_id = c.get(action_col)
            if (
                action_id
                and c.get("google_ads_customer_id")
                and c.get("google_ads_refresh_token")
            ):
                cust = getattr(event, "customer", None)
                cust_email = cust.email if cust else None
                cust_phone = cust.phone if cust else None
                # Also accept email/phone directly on the Shopify payload
                cust_email = cust_email or raw.get("email")
                cust_phone = cust_phone or raw.get("phone") or raw.get("shipping_address", {}).get("phone")
                meta_local = getattr(event, "metadata", {}) or {}
                gclid = meta_local.get("gclid")
                value = float(meta_local.get("cart_total") or 0)
                if value > 0 and (gclid or cust_email or cust_phone):
                    try:
                        google_ads.send_conversion(
                            customer_id=c["google_ads_customer_id"],
                            conversion_action_id=action_id,
                            value=value,
                            currency="BRL",
                            refresh_token=c["google_ads_refresh_token"],
                            gclid=gclid,
                            email=cust_email,
                            phone=cust_phone,
                            order_id=det_id,  # dedup key — same as Meta event_id
                            manager_id=c.get("google_ads_login_customer_id") or settings.GOOGLE_ADS_MANAGER_ID or None,
                        )
                    except Exception as exc:
                        logger.debug("funnel google_ads send failed (%s): %s", kind, exc)

    except Exception as exc:
        logger.warning("_dispatch_funnel_capi error for %s: %s", client_pixel_id, exc)


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

    # Server-side mid-funnel: AddToCart on `carts/create` + InitiateCheckout
    # on `checkouts/create|update`. Backup for sessions where the browser
    # pixel never runs (adblock, in-app browsers, fast PIX redirects).
    if event.event_type.value in ("cart.created", "checkout.started"):
        background_tasks.add_task(_dispatch_funnel_capi, client_id, event)

    # Fire Purchase CAPI + GA4 for paid orders (non-blocking, marks capi_sent)
    # Only on order.paid (payment confirmed), NOT checkout.completed (which fires before payment)
    if event.event_type.value == "order.paid" and order_uuid:
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

    # Order cancelled — update financial_status so dashboards exclude these
    # and CAPI retry job stops trying to send them as conversions.
    if event.event_type.value == "order.cancelled" and client_uuid and event.order:
        try:
            writer.update_order_financial_status(client_uuid, event.order.id, "voided")
        except Exception as exc:
            logger.warning("update cancelled status failed: %s", exc)
        # If the order had already been sent to Meta as a purchase, send a
        # compensating refund event so Meta removes the conversion credit.
        background_tasks.add_task(_dispatch_refund_capi, client_id, event)

    # Refunds — persist to refunds table, then dispatch negative-value Purchase
    # to Meta CAPI + refund event to GA4 in the background.
    if event.event_type.value == "order.refunded":
        refund_order_uuid = writer.write_refund(client_uuid, event)
        background_tasks.add_task(_dispatch_refund_capi, client_id, event)
        if refund_order_uuid:
            background_tasks.add_task(_recompute_order_profit_after_refund, refund_order_uuid)

    return {"status": "ok", "event_id": event.event_id, "event_type": event.event_type}
