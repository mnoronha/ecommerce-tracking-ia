"""
GA4 server-side purchase from Noro Tracking (Measurement Protocol).

Why: payments by PIX/boleto are confirmed after the browser left the checkout.
Customers who never reach the thank-you page never fire GA4's `purchase`
(Enutri: ~46% of Shopify revenue missing in GA4). When Shopify confirms the
payment (orders/paid webhook or the API sync), this module sends the purchase
to GA4 with the visitor's own client_id and session_id, backdated to the order
time, so GA4 attributes it to the original session (source/medium/campaign).

What it never does:
- send when the thank-you page was reached (our Web Pixel saw `purchase` for the
  same checkout): the store's own GA4 tag (Google & YouTube app) already counted it;
- send without analytics consent, without a GA4 client_id, or twice
  (tracking_order_matches.ga4_sent_at);
- send while CONVERSION_SENDS_DISABLED blocks the client, unless
  CONVERSION_SENDS_ALLOW releases "<client>:ga4_noro".

Runs as a scheduled job (every 10 min) over paid orders reconciled at least
_MIN_AGE ago — so the Web Pixel `purchase` of a card payment (thank-you page)
has time to arrive before we decide, and a missed webhook recovered by the
Shopify API sync is covered too.

GA4_DISPATCH_MODE (Railway): "off" (default) | "debug" (validation endpoint,
nothing recorded in GA4) | "live".
"""
from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx

from . import send_guard

logger = logging.getLogger(__name__)

CHANNEL = "ga4_noro"
MODE_ENV = "GA4_DISPATCH_MODE"
_MP_URL = "https://www.google-analytics.com/mp/collect"
_MP_DEBUG_URL = "https://www.google-analytics.com/debug/mp/collect"
_MAX_BACKDATE = timedelta(hours=71)  # GA4 accepts up to 72 h
_MIN_AGE = timedelta(minutes=15)      # wait for the thank-you pixel event


def _retryable(result: Optional[str]) -> bool:
    """Final outcomes ("skipped:…") are not revisited; debug/http/error are."""
    r = result or ""
    return not r.startswith("skipped:") and r != "sent"


def mode() -> str:
    m = (os.environ.get(MODE_ENV) or "off").strip().lower()
    return m if m in ("off", "debug", "live") else "off"


def _parse(ts: Optional[str]) -> Optional[datetime]:
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


# ── decision (pure) ───────────────────────────────────────────────────────────


def decide(row: dict, thank_you_seen: bool, already_sent: bool, now: Optional[datetime] = None) -> tuple[bool, str]:
    """(send?, reason). `row` is a tracking_order_matches row."""
    if already_sent:
        return False, "already_sent"
    if (row.get("financial_status") or "") != "paid":
        return False, "not_paid"
    if row.get("excluded_reason"):
        return False, f"excluded:{row['excluded_reason']}"
    shadow = row.get("shadow_ga4") or {}
    if shadow.get("consent_analytics") is not True:
        return False, "analytics_consent_not_granted"
    if not shadow.get("client_id"):
        return False, "missing_ga_client_id"
    if thank_you_seen:
        return False, "thank_you_reached_native_tag_counts"
    created = _parse(shadow.get("order_created_at") or row.get("order_created_at"))
    now = now or datetime.now(timezone.utc)
    if created and now - created > _MAX_BACKDATE:
        return False, "older_than_72h"
    return True, "send"


def build_payload(row: dict, now: Optional[datetime] = None) -> dict:
    shadow = row.get("shadow_ga4") or {}
    params: dict = {
        "transaction_id": str(shadow.get("transaction_id") or row.get("order_id")),
        "value":          float(shadow.get("value") if shadow.get("value") is not None else row.get("total_price") or 0),
        "currency":       (shadow.get("currency") or row.get("currency") or "BRL").upper(),
        "engagement_time_msec": 1,
    }
    sid = str(shadow.get("session_id") or "")
    if sid.isdigit():
        params["session_id"] = int(sid)
    payload: dict = {
        "client_id": str(shadow["client_id"]),
        "events":    [{"name": "purchase", "params": params}],
    }
    created = _parse(shadow.get("order_created_at") or row.get("order_created_at"))
    now = now or datetime.now(timezone.utc)
    if created and now - created <= _MAX_BACKDATE:
        payload["timestamp_micros"] = int(created.timestamp() * 1_000_000)
    return payload


# ── IO ────────────────────────────────────────────────────────────────────────


def _thank_you_seen(sb, client_uuid: str, checkout_token: Optional[str]) -> bool:
    if not checkout_token:
        return False
    try:
        res = (
            sb.table("tracking_events").select("id")
            .eq("client_id", client_uuid).eq("checkout_token", checkout_token)
            .eq("event_type", "purchase").limit(1).execute()
        )
        return bool(res.data)
    except Exception as exc:
        logger.warning("ga4_dispatch: thank-you lookup failed: %s", exc)
        return True  # unknown → assume counted, never risk a duplicate


def _already_sent(sb, client_uuid: str, order_id: str) -> bool:
    try:
        res = (
            sb.table("tracking_order_matches").select("ga4_sent_at")
            .eq("client_id", client_uuid).eq("order_id", order_id).limit(1).execute()
        )
        return bool(res.data and res.data[0].get("ga4_sent_at"))
    except Exception as exc:
        logger.warning("ga4_dispatch: sent-state lookup failed (migration 074?): %s", exc)
        return True  # cannot prove it was not sent → do not send


def _credentials(sb, client_uuid: str) -> tuple[Optional[str], Optional[str]]:
    from ..services import crypto
    res = sb.table("clients").select("ga4_measurement_id, ga4_api_secret").eq("id", client_uuid).limit(1).execute()
    row = (res.data or [{}])[0]
    return row.get("ga4_measurement_id"), crypto.decrypt_secret(row.get("ga4_api_secret"))


def _mark(sb, client_uuid: str, order_id: str, result: str, sent: bool) -> None:
    patch = {"ga4_send_result": result[:200]}
    if sent:
        patch["ga4_sent_at"] = datetime.now(timezone.utc).isoformat()
    try:
        sb.table("tracking_order_matches").update(patch).eq("client_id", client_uuid).eq("order_id", order_id).execute()
    except Exception as exc:
        logger.warning("ga4_dispatch: could not record result for %s: %s", order_id, exc)


def maybe_send(sb, client_uuid: str, row: dict) -> Optional[str]:
    """Called after a paid order is reconciled. Never raises. Returns the reason."""
    try:
        m = mode()
        if m == "off" or (row.get("financial_status") or "") != "paid":
            return None
        if send_guard.sends_blocked(client_uuid, CHANNEL, sb):
            return "blocked"
        order_id = str(row.get("order_id"))
        ok, reason = decide(
            row,
            thank_you_seen=_thank_you_seen(sb, client_uuid, row.get("checkout_token")),
            already_sent=_already_sent(sb, client_uuid, order_id),
        )
        if not ok:
            if reason != "already_sent":
                _mark(sb, client_uuid, order_id, f"skipped:{reason}", sent=False)
            return reason
        measurement_id, api_secret = _credentials(sb, client_uuid)
        if not measurement_id or not api_secret:
            _mark(sb, client_uuid, order_id, "skipped:no_ga4_credentials", sent=False)
            return "no_ga4_credentials"

        payload = build_payload(row)
        url = _MP_DEBUG_URL if m == "debug" else _MP_URL
        resp = httpx.post(url, params={"measurement_id": measurement_id, "api_secret": api_secret},
                          json=payload, timeout=10.0)
        if m == "debug":
            msgs = (resp.json() if resp.content else {}).get("validationMessages", [])
            result = f"debug:{resp.status_code}:{'valid' if not msgs else str(msgs)[:150]}"
            _mark(sb, client_uuid, order_id, result, sent=False)  # debug never counts as sent
            logger.info("ga4_dispatch debug order=%s %s", order_id, result)
            return result
        if resp.status_code in (200, 204):
            _mark(sb, client_uuid, order_id, "sent", sent=True)
            logger.info("ga4_dispatch: purchase sent order=%s session=%s",
                        order_id, payload["events"][0]["params"].get("session_id"))
            return "sent"
        _mark(sb, client_uuid, order_id, f"http_{resp.status_code}", sent=False)
        return f"http_{resp.status_code}"
    except Exception as exc:
        logger.warning("ga4_dispatch failed for %s: %s", row.get("order_id"), exc)
        return "error"


def _released_client_uuids(sb) -> Optional[list[str]]:
    """UUIDs of clients this job may send for; None = no restriction (nothing blocked)."""
    if not send_guard.blocked_identifiers():
        return None
    wanted = [c for c, ch in send_guard.allowed_pairs() if ch == CHANNEL]
    uuids: list[str] = []
    for ident in wanted:
        for col in ("pixel_id", "client_id", "id"):
            if col == "id" and len(ident) != 36:
                continue
            try:
                r = sb.table("clients").select("id").eq(col, ident).limit(1).execute()
            except Exception:
                continue
            if r.data:
                uuids.append(str(r.data[0]["id"]))
                break
    return uuids


LAST_STATS: dict = {}


def run_pending(sb=None, now: Optional[datetime] = None) -> dict:
    """Scheduled entry point. Never raises."""
    stats: dict = {"mode": mode(), "checked": 0,
                   "at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    LAST_STATS.clear(); LAST_STATS.update(stats)
    if stats["mode"] == "off":
        return stats
    try:
        if sb is None:
            from ..database import get_supabase
            sb = get_supabase()
        now = now or datetime.now(timezone.utc)
        released = _released_client_uuids(sb)
        stats["clients"] = len(released) if released is not None else "all"
        if released == []:
            logger.info("ga4_dispatch: no client released (%s)", send_guard.ALLOW_ENV_VAR)
            LAST_STATS.clear(); LAST_STATS.update(stats)
            return stats
        q = (
            sb.table("tracking_order_matches")
            .select("client_id, order_id, financial_status, excluded_reason, checkout_token, order_created_at,"
                    " paid_at, total_price, currency, shadow_ga4, ga4_send_result")
            .eq("financial_status", "paid")
            .is_("ga4_sent_at", "null")
            .gte("updated_at", (now - _MAX_BACKDATE).isoformat())
            .lte("updated_at", (now - _MIN_AGE).isoformat())
        )
        if released is not None:
            q = q.in_("client_id", released)
        res = q.order("updated_at", desc=True).limit(500).execute()
        stats["candidates"] = len(res.data or [])
        for row in res.data or []:
            if not _retryable(row.get("ga4_send_result")):
                continue
            stats["checked"] += 1
            reason = maybe_send(sb, row["client_id"], row) or "none"
            stats[reason.split(":")[0]] = stats.get(reason.split(":")[0], 0) + 1
    except Exception as exc:
        logger.warning("ga4_dispatch.run_pending failed: %s", exc)
        stats["error"] = str(exc)[:200]
    logger.info("ga4_dispatch: %s", stats)
    LAST_STATS.clear(); LAST_STATS.update(stats)
    return stats
