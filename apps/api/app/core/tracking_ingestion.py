"""
Noro Tracking Ingestion — Phase 7.

Stores raw evidence only. Invariants:
- ZERO Meta CAPI sends
- ZERO Google Enhanced Conversion sends
- ZERO Platform writes (no Agency API mutations)
- No raw email, no raw phone, no probabilistic identity matching
- No cross-client data access
- Purchase events are NOT Business Truth (Shopify/Core Business remains canonical)
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

_VALID_EVENT_NAMES = frozenset(
    {"page_view", "view_item", "add_to_cart", "begin_checkout", "purchase"}
)
_CURRENCY_RE = re.compile(r"^[A-Z]{3}$")

# Maps Phase 7 API event names to the legacy tracking_events.event_type CHECK values.
_EVENT_TYPE_MAP = {
    "page_view": "pageview",
    "view_item": "view_product",
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _occurred_iso(event) -> str:
    occ = getattr(event, "occurred_at", None)
    if occ is None:
        return _now_iso()
    return occ.isoformat() if hasattr(occ, "isoformat") else str(occ)


# ── Validation ────────────────────────────────────────────────────────────────


def validate_event(event) -> tuple[bool, Optional[str]]:
    """Return (ok, rejection_reason). Validates schema-level rules not caught by Pydantic."""
    if not getattr(event, "event_id", None) or not str(event.event_id).strip():
        return False, "event_id is required"
    if getattr(event, "event_name", None) not in _VALID_EVENT_NAMES:
        return False, f"unknown event_name '{event.event_name}'"
    if event.event_name == "purchase":
        if not getattr(event, "order_id", None):
            return False, "purchase requires order_id"
        if getattr(event, "value", None) is None:
            return False, "purchase requires value"
        if not getattr(event, "currency", None):
            return False, "purchase requires currency"
    if getattr(event, "currency", None) and not _CURRENCY_RE.match(event.currency):
        return False, "currency must be 3 uppercase ISO 4217 letters (e.g. BRL, USD)"
    if getattr(event, "value", None) is not None and event.value < 0:
        return False, "value must be non-negative"
    if getattr(event, "product_quantity", None) is not None and event.product_quantity < 1:
        return False, "product_quantity must be a positive integer"
    return True, None


# ── Deduplication ─────────────────────────────────────────────────────────────


def _check_duplicate(sb, client_uuid: str, event_id: str) -> bool:
    """Return True if this client already has a tracking_event with this event_id."""
    try:
        r = (
            sb.table("tracking_events")
            .select("id")
            .eq("client_id", client_uuid)
            .eq("event_id", event_id)
            .limit(1)
            .execute()
        )
        return bool(r.data)
    except Exception as exc:
        logger.warning("dedup_check failed client=%s: %s", client_uuid[:8], exc)
        return False


# ── Session resolution ────────────────────────────────────────────────────────


def _resolve_session(sb, client_uuid: str, event) -> bool:
    """
    Ensure a session row exists for this (client_id, session_id).

    First-touch invariant: landing_url and utm_* are set only on creation.
    Subsequent events reuse the existing row without overwriting attribution.

    Returns True if a new session was created, False if reused.
    """
    if not getattr(event, "session_id", None):
        return False

    try:
        check = (
            sb.table("tracking_sessions")
            .select("id")
            .eq("client_id", client_uuid)
            .eq("session_id", event.session_id)
            .maybe_single()
            .execute()
        )
        if check is not None and check.data:
            return False

        sb.table("tracking_sessions").insert({
            "client_id":          client_uuid,
            "session_id":         event.session_id,
            "visitor_cookie_id":  getattr(event, "visitor_cookie_id", None) or "",
            "started_at":         _occurred_iso(event),
            "event_count":        1,
            "utm_source":         getattr(event, "utm_source", None),
            "utm_medium":         getattr(event, "utm_medium", None),
            "utm_campaign":       getattr(event, "utm_campaign", None),
            "landing_url":        getattr(event, "url", None),
        }).execute()
        return True

    except Exception as exc:
        logger.warning("session_resolve failed client=%s session=%s: %s",
                       client_uuid[:8], event.session_id, exc)
        return False


# ── Identity resolution ───────────────────────────────────────────────────────


def _resolve_identity(sb, client_uuid: str, event) -> tuple[bool, bool]:
    """
    Upsert a tracking_identity keyed on (client_id, visitor_cookie_id).

    Deterministic only — no probabilistic matching.
    No raw email or phone stored.
    Identities NEVER cross client boundaries.

    Returns (identity_created: bool, identity_linked: bool).
    """
    vid = getattr(event, "visitor_cookie_id", None)
    if not vid:
        return False, False

    now = _now_iso()
    try:
        check = (
            sb.table("tracking_identities")
            .select("id")
            .eq("client_id", client_uuid)
            .eq("visitor_cookie_id", vid)
            .maybe_single()
            .execute()
        )
        if check is not None and check.data:
            update_data: dict = {"last_seen_at": now}
            for col in ("fbp", "fbc", "gbraid", "wbraid", "ga_client_id"):
                val = getattr(event, col, None)
                if val:
                    update_data[col] = val
            sb.table("tracking_identities").update(update_data).eq(
                "client_id", client_uuid
            ).eq("visitor_cookie_id", vid).execute()
            return False, True

        sb.table("tracking_identities").insert({
            "client_id":        client_uuid,
            "visitor_cookie_id": vid,
            "fbp":              getattr(event, "fbp", None),
            "fbc":              getattr(event, "fbc", None),
            "gbraid":           getattr(event, "gbraid", None),
            "wbraid":           getattr(event, "wbraid", None),
            "ga_client_id":     getattr(event, "ga_client_id", None),
            "first_seen_at":    now,
            "last_seen_at":     now,
        }).execute()
        return True, False

    except Exception as exc:
        logger.warning("identity_resolve failed client=%s vid=%s: %s",
                       client_uuid[:8], vid[:8] if len(vid) > 8 else vid, exc)
        return False, False


# ── Attribution inputs ────────────────────────────────────────────────────────


def _store_attribution_input(sb, client_uuid: str, event) -> None:
    """
    Store raw attribution evidence for purchase events.

    Uses ON CONFLICT DO NOTHING — first-touch wins.
    No-op for non-purchase events.
    """
    if getattr(event, "event_name", None) != "purchase":
        return
    order_id = getattr(event, "order_id", None)
    if not order_id:
        return
    try:
        sb.table("tracking_attribution_inputs").upsert(
            {
                "client_id":        client_uuid,
                "order_id":         order_id,
                "session_id":       getattr(event, "session_id", None),
                "visitor_cookie_id": getattr(event, "visitor_cookie_id", None),
                "utm_source":       getattr(event, "utm_source", None),
                "utm_medium":       getattr(event, "utm_medium", None),
                "utm_campaign":     getattr(event, "utm_campaign", None),
                "fbp":              getattr(event, "fbp", None),
                "fbc":              getattr(event, "fbc", None),
                "fbclid":           getattr(event, "fbclid", None),
                "gclid":            getattr(event, "gclid", None),
                "gbraid":           getattr(event, "gbraid", None),
                "wbraid":           getattr(event, "wbraid", None),
                "ga_client_id":     getattr(event, "ga_client_id", None),
            },
            on_conflict="client_id,order_id",
            ignore_duplicates=True,
        ).execute()
    except Exception as exc:
        logger.warning("attribution_input failed client=%s order=%s: %s",
                       client_uuid[:8], order_id, exc)


# ── Main ingestion orchestrator ───────────────────────────────────────────────


def ingest_events(sb, client_uuid: str, events: list) -> dict:
    """
    Ingest a list of TrackingEventIn objects for one client.

    Returns a dict compatible with TrackingIngestOut schema.

    Zero external writes — no Meta CAPI, no Google Enhanced Conversions,
    no Platform API mutations. Evidence storage only.
    """
    counters = {
        "events_received":     len(events),
        "events_inserted":     0,
        "events_deduplicated": 0,
        "events_rejected":     0,
        "sessions_created":    0,
        "sessions_reused":     0,
        "identities_created":  0,
        "identities_linked":   0,
    }
    results: list[dict] = []

    for event in events:
        eid = getattr(event, "event_id", None) or "?"

        # 1. Validate
        ok, reason = validate_event(event)
        if not ok:
            counters["events_rejected"] += 1
            results.append({"event_id": eid, "status": "rejected", "reason": reason})
            logger.info(
                "tracking_ingest rejected client=%s event_id=%s reason=%s",
                client_uuid[:8], eid, reason,
            )
            continue

        # 2. Dedup
        if _check_duplicate(sb, client_uuid, eid):
            counters["events_deduplicated"] += 1
            results.append({"event_id": eid, "status": "duplicate"})
            logger.debug(
                "tracking_ingest dedup client=%s event_id=%s", client_uuid[:8], eid
            )
            continue

        # 3. Session resolution (first-touch preserved)
        session_created = _resolve_session(sb, client_uuid, event)
        if session_created:
            counters["sessions_created"] += 1
        elif getattr(event, "session_id", None):
            counters["sessions_reused"] += 1

        # 4. Identity resolution (deterministic, no cross-client)
        id_created, id_linked = _resolve_identity(sb, client_uuid, event)
        if id_created:
            counters["identities_created"] += 1
        elif id_linked:
            counters["identities_linked"] += 1

        # 5. Attribution input (purchase only, first-touch wins)
        _store_attribution_input(sb, client_uuid, event)

        # 6. Insert tracking event
        extra_props: dict = dict(getattr(event, "properties", None) or {})
        if getattr(event, "gclid", None):
            extra_props["gclid"] = event.gclid
        if getattr(event, "ga_client_id", None):
            extra_props["ga_client_id"] = event.ga_client_id
        if getattr(event, "fbclid", None):
            extra_props["fbclid"] = event.fbclid

        try:
            sb.table("tracking_events").insert({
                "client_id":        client_uuid,
                "event_id":         eid,
                "event_type":       _EVENT_TYPE_MAP.get(event.event_name, event.event_name),
                "session_id":       getattr(event, "session_id", None),
                "visitor_cookie_id": getattr(event, "visitor_cookie_id", None),
                "url":              getattr(event, "url", None),
                "referrer":         getattr(event, "referrer", None),
                "utm_source":       getattr(event, "utm_source", None),
                "utm_medium":       getattr(event, "utm_medium", None),
                "utm_campaign":     getattr(event, "utm_campaign", None),
                "utm_content":      getattr(event, "utm_content", None),
                "utm_term":         getattr(event, "utm_term", None),
                "fbp":              getattr(event, "fbp", None),
                "fbc":              getattr(event, "fbc", None),
                "gbraid":           getattr(event, "gbraid", None),
                "wbraid":           getattr(event, "wbraid", None),
                "order_id":         getattr(event, "order_id", None),
                "product_id":       getattr(event, "product_id", None),
                "product_name":     getattr(event, "product_name", None),
                "product_price":    (
                    getattr(event, "value", None)
                    if getattr(event, "value", None) is not None
                    else getattr(event, "product_price", None)
                ),
                "product_quantity": getattr(event, "product_quantity", None),
                "product_category": getattr(event, "product_category", None),
                "properties":       extra_props,
                "created_at":       _occurred_iso(event),
            }).execute()
            counters["events_inserted"] += 1
            results.append({"event_id": eid, "status": "accepted"})
            logger.info(
                "tracking_ingest accepted client=%s event_id=%s type=%s",
                client_uuid[:8], eid, event.event_name,
            )
        except Exception as exc:
            logger.error(
                "tracking_insert failed client=%s event_id=%s: %s",
                client_uuid[:8], eid, exc,
            )
            counters["events_rejected"] += 1
            results.append({"event_id": eid, "status": "rejected", "reason": "insert_failed"})

    logger.info(
        "tracking_ingest summary client=%s recv=%d inserted=%d dedup=%d rejected=%d",
        client_uuid[:8],
        counters["events_received"],
        counters["events_inserted"],
        counters["events_deduplicated"],
        counters["events_rejected"],
    )
    return {**counters, "results": results}
