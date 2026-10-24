"""
Noro Tracking — order ↔ browsing reconciliation (v4.3, shadow mode).

For every Shopify order the webhook pipeline receives, this module writes one
row to `tracking_order_matches` linking the order (Business Truth) to the
browsing evidence collected by the storefront tracker and the Custom Pixel,
and records *how* it was linked (`match_source`).

It also builds the payloads that WOULD be sent to GA4 (Measurement Protocol)
and Google Ads (offline click conversions + enhanced conversions) once those
destinations are enabled for the client. Nothing is sent from here.

Match ladder (first hit wins):
  1. cart_attributes   — order note_attributes carry `_etv` (visitor cookie)
  2. pixel             — Custom Pixel event with the same checkout_token
  3. customer_history  — same email hash already linked to a visitor
  4. shopify_fallback  — landing_site / referrer carried a marketing signal
  5. none              — sale counts, origin unknown

Invariants:
  - ZERO external sends (no GA4, no Google Ads, no Meta).
  - Email stored only as sha256(lower(trim(email))).
  - Click ids are dropped when advertising consent was denied.
  - Never raises into the webhook: every entry point swallows and logs.
"""
from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Optional

logger = logging.getLogger(__name__)

MATCH_SOURCES = ("cart_attributes", "pixel", "customer_history", "shopify_fallback", "none")
CLICK_FIELDS = ("gclid", "gbraid", "wbraid")
OFFLINE_SOURCE_NAMES = frozenset({"pos", "shopify_draft_order"})
ORDER_EVENT_TYPES = frozenset({"order.created", "order.updated", "order.paid"})
STATUS_EVENT_TYPES = frozenset({"order.cancelled", "order.refunded"})
GOOGLE_CLICK_WINDOW_DAYS = 90
GA4_BACKDATE_HOURS = 72


# ── Pure helpers ─────────────────────────────────────────────────────────────


def hash_email(email: Optional[str]) -> Optional[str]:
    if not email or "@" not in str(email):
        return None
    return hashlib.sha256(str(email).strip().lower().encode("utf-8")).hexdigest()


def _clean(value: Any) -> Optional[str]:
    if value is None:
        return None
    s = str(value).strip()
    return s or None


def _parse_dt(value: Any) -> Optional[datetime]:
    if not value:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def _iso(dt: Optional[datetime]) -> Optional[str]:
    return dt.isoformat() if dt else None


def consent_from_attribute(value: Optional[str]) -> Optional[bool]:
    """'a1m1' → True, 'a1m0' → False, absent/invalid → None (unknown, legacy tracker)."""
    if not isinstance(value, str) or len(value) != 4 or not value.startswith("a") or value[2] != "m":
        return None
    if value[3] == "1":
        return True
    if value[3] == "0":
        return False
    return None


def analytics_consent_from_attribute(value: Optional[str]) -> Optional[bool]:
    """'a1m0' → True, 'a0m1' → False, absent/invalid → None."""
    if not isinstance(value, str) or len(value) != 4 or not value.startswith("a") or value[2] != "m":
        return None
    if value[1] == "1":
        return True
    if value[1] == "0":
        return False
    return None


def facts_from_event(event: Any) -> dict:
    """
    Normalize a webhook event into the facts this module needs.

    Accepts a NormalizedEvent (pydantic) or a dict shaped like a row of the
    `events` table (order under "order_data") or a NormalizedEvent dump.
    """
    if hasattr(event, "model_dump"):
        d = event.model_dump(mode="json")
    else:
        d = dict(event or {})

    order = d.get("order") or d.get("order_data") or {}
    meta = d.get("metadata") or {}
    utm = d.get("utm") or {}
    customer = d.get("customer") or {}
    raw = d.get("raw_payload") or {}
    event_type = d.get("event_type")
    if hasattr(event_type, "value"):
        event_type = event_type.value

    created_at = _parse_dt(d.get("timestamp"))
    processed_at = _parse_dt(meta.get("processed_at"))
    status = (_clean(order.get("status")) or "").lower() or None
    if event_type == "order.paid" and not status:
        status = "paid"

    return {
        "event_type":        event_type,
        "order_id":          _clean(order.get("id")),
        "order_number":      _clean(order.get("number")),
        "financial_status":  status,
        "total":             float(order["total"]) if order.get("total") is not None else None,
        "currency":          (_clean(order.get("currency")) or "BRL").upper(),
        "created_at":        created_at,
        "paid_at":           (processed_at or created_at) if status == "paid" else None,
        "source_name":       (_clean(meta.get("source_name")) or "").lower() or None,
        "is_test":           bool(raw.get("test")) if isinstance(raw, dict) else False,
        "email_hash":        hash_email(customer.get("email")),
        "checkout_token":    _clean(meta.get("checkout_token")),
        "visitor_cookie_id": _clean(meta.get("visitor_cookie_id")),
        "session_id":        _clean(meta.get("noro_session_id")),
        "ga_client_id":      _clean(meta.get("ga_client_id")),
        "ga_session_id":     _clean(meta.get("ga_session_id")),
        "gclid":             _clean(meta.get("gclid")),
        "gbraid":            _clean(meta.get("gbraid")),
        "wbraid":            _clean(meta.get("wbraid")),
        "fbclid":            _clean(meta.get("fbclid")),
        "fbc":               _clean(meta.get("fbc")),
        "fbp":               _clean(meta.get("fbp")),
        "click_source":      _clean(meta.get("click_source")),
        "consent_advertising": consent_from_attribute(meta.get("noro_consent")),
        "consent_analytics": analytics_consent_from_attribute(meta.get("noro_consent")),
        "ads_consent_denied": bool(meta.get("ads_consent_denied")),
        "tracker_version":   _clean(meta.get("tracker_version")),
        "landing_site":      _clean(meta.get("landing_site")),
        "utm_source":        _clean(utm.get("source")),
        "utm_medium":        _clean(utm.get("medium")),
        "utm_campaign":      _clean(utm.get("campaign")),
        "utm_content":       _clean(utm.get("content")),
        "utm_term":          _clean(utm.get("term")),
    }


def exclusion_reason(facts: dict) -> Optional[str]:
    if facts.get("is_test"):
        return "test"
    if facts.get("source_name") in OFFLINE_SOURCE_NAMES:
        return "offline"
    total = facts.get("total")
    if total is not None and total <= 0:
        return "zero_value"
    return None


def _has_landing_signal(facts: dict) -> bool:
    landing = (facts.get("landing_site") or "").lower()
    if any(k in landing for k in ("utm_source=", "gclid=", "gbraid=", "wbraid=", "fbclid=")):
        return True
    return bool(facts.get("utm_source")) and (facts.get("utm_medium") or "").lower() not in ("", "(none)", "direct")


def decide_match(
    facts: dict,
    pixel_evidence: Optional[dict] = None,
    history_evidence: Optional[dict] = None,
    identity: Optional[dict] = None,
) -> dict:
    """
    Pure decision: given the order facts and the evidence found, return the
    attribution fields and match_source. No I/O.
    """
    out: dict = {
        "match_source":      "none",
        "visitor_cookie_id": None,
        "session_id":        None,
        "consent_advertising": facts.get("consent_advertising"),
    }
    for f in ("utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_term",
              "gclid", "gbraid", "wbraid", "fbclid", "fbc", "fbp",
              "ga_client_id", "ga_session_id", "click_source"):
        out[f] = facts.get(f)

    if facts.get("visitor_cookie_id"):
        out["match_source"] = "cart_attributes"
        out["visitor_cookie_id"] = facts["visitor_cookie_id"]
        out["session_id"] = facts.get("session_id")
    elif pixel_evidence and pixel_evidence.get("visitor_cookie_id"):
        out["match_source"] = "pixel"
        out["visitor_cookie_id"] = pixel_evidence["visitor_cookie_id"]
        out["session_id"] = pixel_evidence.get("session_id")
        for f in ("utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_term",
                  "gclid", "gbraid", "wbraid", "fbc", "fbp", "ga_session_id"):
            if not out.get(f) and pixel_evidence.get(f):
                out[f] = pixel_evidence[f]
        if out["consent_advertising"] is None:
            out["consent_advertising"] = pixel_evidence.get("consent_advertising")
    elif history_evidence and history_evidence.get("visitor_cookie_id"):
        out["match_source"] = "customer_history"
        out["visitor_cookie_id"] = history_evidence["visitor_cookie_id"]
    elif _has_landing_signal(facts):
        out["match_source"] = "shopify_fallback"

    # Enrich missing ad identifiers from the visitor's identity (still within consent).
    if identity and out["visitor_cookie_id"] and identity.get("consent_advertising") is not False:
        for f in ("gclid", "gbraid", "wbraid", "fbc", "fbp", "ga_client_id", "ga_session_id"):
            if not out.get(f) and identity.get(f):
                out[f] = identity[f]
        if out["consent_advertising"] is None:
            out["consent_advertising"] = identity.get("consent_advertising")

    # Consent gate: explicit denial wipes every ad identifier.
    if out["consent_advertising"] is False or facts.get("ads_consent_denied"):
        out["consent_advertising"] = False
        for f in ("gclid", "gbraid", "wbraid", "fbclid", "fbc", "fbp"):
            out[f] = None

    if not out.get("click_source"):
        out["click_source"] = next((f for f in ("gclid", "gbraid", "wbraid", "fbclid") if out.get(f)), None)
    return out


def _ads_datetime(dt: Optional[datetime]) -> Optional[str]:
    """Google Ads format: 'yyyy-mm-dd hh:mm:ss+00:00'."""
    if not dt:
        return None
    dt = dt.astimezone(timezone.utc)
    return dt.strftime("%Y-%m-%d %H:%M:%S+00:00")


def build_shadow_payloads(facts: dict, match: dict, now: Optional[datetime] = None) -> tuple[Optional[dict], Optional[dict]]:
    """
    Describe what WOULD be sent to GA4 and Google Ads for a paid order.
    Returns (shadow_ga4, shadow_google_ads); (None, None) when not a paid,
    eligible order. Never contains raw PII.
    """
    if facts.get("financial_status") != "paid" or exclusion_reason(facts):
        return None, None

    now = now or datetime.now(timezone.utc)
    paid_at = facts.get("paid_at") or facts.get("created_at") or now
    consent = match.get("consent_advertising")
    consent_label = "granted" if consent is True else ("denied" if consent is False else "unknown")

    ga4_blockers = []
    consent_analytics = facts.get("consent_analytics")
    if consent_analytics is not True:
        ga4_blockers.append("analytics_consent_not_granted")
    if not match.get("ga_client_id"):
        ga4_blockers.append("missing_ga_client_id")
    if not match.get("ga_session_id"):
        ga4_blockers.append("missing_ga_session_id")  # sends, but lands outside the session
    within_ga4_window = (now - paid_at) <= timedelta(hours=GA4_BACKDATE_HOURS)
    shadow_ga4 = {
        "would_send":     bool(match.get("ga_client_id")) and consent_analytics is True,
        "client_id":      match.get("ga_client_id"),
        "consent_analytics": consent_analytics,
        "order_created_at": _iso(facts.get("created_at")),
        "session_id":     match.get("ga_session_id"),
        "transaction_id": facts.get("order_id"),
        "value":          facts.get("total"),
        "currency":       facts.get("currency"),
        "timestamp":      _iso(paid_at) if within_ga4_window else None,
        "consent":        {"ad_user_data": consent_label, "ad_personalization": consent_label},
        "blockers":       ga4_blockers,
    }

    click_field = next((f for f in CLICK_FIELDS if match.get(f)), None)
    ads_blockers = []
    if consent is False:
        ads_blockers.append("advertising_consent_denied")
    if not click_field and not facts.get("email_hash"):
        ads_blockers.append("no_click_id_and_no_email")
    shadow_ads = {
        "would_send":           not ads_blockers,
        "mode":                 ("click" if click_field else "enhanced_only") if not ads_blockers else None,
        "click_field":          click_field,
        "conversion_date_time": _ads_datetime(paid_at),
        "value":                facts.get("total"),
        "currency":             facts.get("currency"),
        "order_id":             facts.get("order_id"),
        "has_user_identifiers": bool(facts.get("email_hash")),
        "consent":              {"ad_user_data": consent_label, "ad_personalization": consent_label},
        "blockers":             ads_blockers,
    }
    return shadow_ga4, shadow_ads


def build_match_row(
    client_uuid: str,
    facts: dict,
    match: dict,
    order_uuid: Optional[str] = None,
    is_first_purchase: Optional[bool] = None,
    now: Optional[datetime] = None,
) -> dict:
    now = now or datetime.now(timezone.utc)
    shadow_ga4, shadow_ads = build_shadow_payloads(facts, match, now)
    row = {
        "client_id":         client_uuid,
        "order_id":          facts["order_id"],
        "order_uuid":        order_uuid,
        "order_number":      facts.get("order_number"),
        "financial_status":  facts.get("financial_status"),
        "total_price":       facts.get("total"),
        "currency":          facts.get("currency"),
        "order_created_at":  _iso(facts.get("created_at")),
        "paid_at":           _iso(facts.get("paid_at")),
        "source_name":       facts.get("source_name"),
        "excluded_reason":   exclusion_reason(facts),
        "is_first_purchase": is_first_purchase,
        "email_hash":        facts.get("email_hash"),
        "checkout_token":    facts.get("checkout_token"),
        "tracker_version":   facts.get("tracker_version"),
        "shadow_ga4":        shadow_ga4,
        "shadow_google_ads": shadow_ads,
        "updated_at":        _iso(now),
        **{k: match.get(k) for k in (
            "match_source", "visitor_cookie_id", "session_id",
            "utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_term",
            "click_source", "gclid", "gbraid", "wbraid", "fbclid", "fbc", "fbp",
            "ga_client_id", "ga_session_id", "consent_advertising",
        )},
    }
    # Never overwrite known values with NULL on a later, poorer event (e.g. order.updated),
    # except where NULL is the point: exclusions and ad ids wiped by a consent denial.
    keep_null = {"excluded_reason"}
    if match.get("consent_advertising") is False:
        keep_null |= {"gclid", "gbraid", "wbraid", "fbclid", "fbc", "fbp", "consent_advertising"}
    return {k: v for k, v in row.items() if v is not None or k in keep_null}


# ── Quality KPIs (pure) ──────────────────────────────────────────────────────


def _pct(num: int, den: int) -> Optional[float]:
    return round(100.0 * num / den, 1) if den else None


def compute_quality(
    matches: Iterable[dict],
    shopify_paid_orders: Optional[int] = None,
    shopify_paid_revenue: Optional[float] = None,
) -> dict:
    """
    KPIs over tracking_order_matches rows for a period.

    shopify_paid_* come from public.orders (the webhook-fed order store) and
    measure coverage of this layer against Business Truth.
    """
    rows = list(matches)
    paid = [r for r in rows if r.get("financial_status") == "paid" and not r.get("excluded_reason")]
    n = len(paid)
    revenue = round(sum(float(r.get("total_price") or 0) for r in paid), 2)

    by_source = {s: 0 for s in MATCH_SOURCES}
    for r in paid:
        by_source[r.get("match_source") or "none"] = by_source.get(r.get("match_source") or "none", 0) + 1
    matched = n - by_source.get("none", 0)

    with_google_click = sum(1 for r in paid if any(r.get(f) for f in CLICK_FIELDS))
    consent = {
        "granted": sum(1 for r in paid if r.get("consent_advertising") is True),
        "denied":  sum(1 for r in paid if r.get("consent_advertising") is False),
        "unknown": sum(1 for r in paid if r.get("consent_advertising") is None),
    }
    ga4_ready = sum(1 for r in paid if (r.get("shadow_ga4") or {}).get("would_send"))
    ga4_in_session = sum(1 for r in paid if (r.get("shadow_ga4") or {}).get("would_send")
                         and (r.get("shadow_ga4") or {}).get("session_id"))
    ads_ready = sum(1 for r in paid if (r.get("shadow_google_ads") or {}).get("would_send"))
    ads_click = sum(1 for r in paid if (r.get("shadow_google_ads") or {}).get("mode") == "click")

    return {
        "paid_orders":            n,
        "paid_revenue":           revenue,
        "excluded_orders":        sum(1 for r in rows if r.get("excluded_reason")),
        "pending_orders":         sum(1 for r in rows if r.get("financial_status") not in ("paid", "refunded", "voided", "partially_refunded")),
        "coverage_orders_pct":    _pct(n, shopify_paid_orders) if shopify_paid_orders else None,
        "coverage_revenue_pct":   (round(100.0 * revenue / shopify_paid_revenue, 1) if shopify_paid_revenue else None),
        "shopify_paid_orders":    shopify_paid_orders,
        "shopify_paid_revenue":   shopify_paid_revenue,
        "match_rate_pct":         _pct(matched, n),
        "match_sources":          by_source,
        "google_click_rate_pct":  _pct(with_google_click, n),
        "consent_advertising":    consent,
        "ga4_ready_pct":          _pct(ga4_ready, n),
        "ga4_in_session_pct":     _pct(ga4_in_session, n),
        "google_ads_ready_pct":   _pct(ads_ready, n),
        "google_ads_click_pct":   _pct(ads_click, n),
    }


# ── Database access (thin, all failures swallowed) ───────────────────────────


def _first(res) -> Optional[dict]:
    data = getattr(res, "data", None)
    if isinstance(data, list):
        return data[0] if data else None
    return data or None


def lookup_pixel_evidence(sb, client_uuid: str, checkout_token: Optional[str]) -> Optional[dict]:
    if not checkout_token:
        return None
    try:
        res = (
            sb.table("tracking_events")
            .select("visitor_cookie_id, session_id, utm_source, utm_medium, utm_campaign, utm_content, utm_term,"
                    " gclid, gbraid, wbraid, fbc, fbp, ga_session_id, consent_advertising")
            .eq("client_id", client_uuid)
            .eq("checkout_token", checkout_token)
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )
        return _first(res)
    except Exception as exc:  # column missing before migration 067, network, ...
        logger.debug("pixel evidence lookup failed: %s", exc)
        return None


def lookup_customer_history(sb, client_uuid: str, email_hash: Optional[str], order_id: str) -> Optional[dict]:
    if not email_hash:
        return None
    try:
        res = (
            sb.table("tracking_order_matches")
            .select("visitor_cookie_id")
            .eq("client_id", client_uuid)
            .eq("email_hash", email_hash)
            .neq("order_id", order_id)
            .not_.is_("visitor_cookie_id", "null")
            .order("matched_at", desc=True)
            .limit(1)
            .execute()
        )
        return _first(res)
    except Exception as exc:
        logger.debug("customer history lookup failed: %s", exc)
        return None


def lookup_identity(sb, client_uuid: str, visitor_cookie_id: Optional[str]) -> Optional[dict]:
    if not visitor_cookie_id:
        return None
    try:
        res = (
            sb.table("tracking_identities")
            .select("*")
            .eq("client_id", client_uuid)
            .eq("visitor_cookie_id", visitor_cookie_id)
            .limit(1)
            .execute()
        )
        row = _first(res)
        if not row:
            return None
        # Click ids older than the Google Ads import window are useless.
        last_seen = _parse_dt(row.get("last_seen_at"))
        if last_seen and datetime.now(timezone.utc) - last_seen > timedelta(days=GOOGLE_CLICK_WINDOW_DAYS):
            for f in ("gclid", "gbraid", "wbraid"):
                row[f] = None
        return row
    except Exception as exc:
        logger.debug("identity lookup failed: %s", exc)
        return None


def lookup_is_first_purchase(sb, order_uuid: Optional[str]) -> Optional[bool]:
    if not order_uuid:
        return None
    try:
        row = _first(sb.table("orders").select("is_first_purchase").eq("id", order_uuid).limit(1).execute())
        return row.get("is_first_purchase") if row else None
    except Exception:
        return None


def upsert_match(sb, row: dict, keep_existing: bool = False) -> bool:
    """keep_existing=True: insert only (order.created must never downgrade a paid row)."""
    try:
        sb.table("tracking_order_matches").upsert(
            row, on_conflict="client_id,order_id", ignore_duplicates=keep_existing,
        ).execute()
        return True
    except Exception as exc:
        logger.warning("tracking_order_matches upsert failed client=%s order=%s: %s",
                       str(row.get("client_id"))[:8], row.get("order_id"), exc)
        return False


def reconcile_order(sb, client_uuid: str, event: Any, order_uuid: Optional[str] = None) -> Optional[dict]:
    """Compute and store the match row for one order event. Returns the row or None."""
    facts = facts_from_event(event)
    if not facts.get("order_id"):
        return None

    pixel = None if facts.get("visitor_cookie_id") else lookup_pixel_evidence(sb, client_uuid, facts.get("checkout_token"))
    history = None
    if not facts.get("visitor_cookie_id") and not (pixel and pixel.get("visitor_cookie_id")):
        history = lookup_customer_history(sb, client_uuid, facts.get("email_hash"), facts["order_id"])

    match = decide_match(facts, pixel, history)
    identity = lookup_identity(sb, client_uuid, match.get("visitor_cookie_id"))
    if identity:
        match = decide_match(facts, pixel, history, identity)

    row = build_match_row(
        client_uuid, facts, match,
        order_uuid=order_uuid,
        is_first_purchase=lookup_is_first_purchase(sb, order_uuid),
    )
    keep_existing = facts.get("event_type") == "order.created"
    return row if upsert_match(sb, row, keep_existing=keep_existing) else None


def update_order_status(sb, client_uuid: str, event: Any) -> None:
    """Cancellations / refunds: only move the status, keep the attribution."""
    facts = facts_from_event(event)
    if not facts.get("order_id"):
        return
    status = facts.get("financial_status")
    if facts.get("event_type") == "order.cancelled":
        status = "voided"
    elif facts.get("event_type") == "order.refunded" and status not in ("refunded", "partially_refunded"):
        status = "refunded"
    try:
        sb.table("tracking_order_matches").update({
            "financial_status": status,
            "updated_at":       datetime.now(timezone.utc).isoformat(),
        }).eq("client_id", client_uuid).eq("order_id", facts["order_id"]).execute()
    except Exception as exc:
        logger.debug("tracking_order_matches status update failed: %s", exc)


def record_from_webhook(client_uuid: Optional[str], event: Any, order_uuid: Optional[str] = None, sb=None) -> None:
    """
    Background-task entry point from the Shopify webhook. Never raises.
    """
    if not client_uuid:
        return
    try:
        if sb is None:
            from ..database import get_supabase
            sb = get_supabase()
        event_type = facts_from_event(event).get("event_type")
        if event_type in ORDER_EVENT_TYPES:
            reconcile_order(sb, client_uuid, event, order_uuid)
        elif event_type in STATUS_EVENT_TYPES:
            update_order_status(sb, client_uuid, event)
    except Exception as exc:
        logger.warning("tracking reconciliation failed client=%s: %s", str(client_uuid)[:8], exc)


def backfill_from_events(sb, client_uuid: str, pixel_id: str, since: datetime, limit: int = 2000) -> dict:
    """
    Rebuild match rows from the stored webhook events (public.events) for a
    period. Used to recover from missed background tasks and to seed history.
    """
    counters = {"events_read": 0, "orders_reconciled": 0, "failed": 0}
    try:
        res = (
            sb.table("events")
            .select("event_type, timestamp, customer, order_data, utm, metadata")
            .eq("client_id", pixel_id)
            .in_("event_type", ["order.created", "order.paid"])
            .gte("timestamp", since.isoformat())
            .order("timestamp")
            .limit(limit)
            .execute()
        )
        rows = getattr(res, "data", None) or []
    except Exception as exc:
        logger.warning("backfill events read failed client=%s: %s", client_uuid[:8], exc)
        return {**counters, "error": "events_read_failed"}

    counters["events_read"] = len(rows)
    # order.created and order.paid share the order's created_at timestamp:
    # process every order.created first so order.paid always has the last word.
    rows.sort(key=lambda r: (r.get("event_type") == "order.paid", str(r.get("timestamp") or "")))
    for row in rows:
        if reconcile_order(sb, client_uuid, row):
            counters["orders_reconciled"] += 1
        else:
            counters["failed"] += 1
    return counters
