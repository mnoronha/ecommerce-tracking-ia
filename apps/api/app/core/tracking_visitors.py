"""
Noro Tracking — Visitors and per-visitor journey (read-only).

Built on the v4.3 tables (tracking_identities, tracking_sessions,
tracking_events, tracking_order_matches). Replaces the legacy Visitors screen,
which read the `visitors` table fed by the pixel disabled in 2026-06.

Never returns click ids, IP, User-Agent or email hashes — only booleans.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)

_EVENT_LABEL = {
    "pageview":       "Página vista",
    "page_view":      "Página vista",
    "view_product":   "Produto visto",
    "view_item":      "Produto visto",
    "add_to_cart":    "Adicionou ao carrinho",
    "begin_checkout": "Iniciou checkout",
    "purchase":       "Finalizou pedido",
}


def _data(res) -> list:
    d = getattr(res, "data", None)
    return d if isinstance(d, list) else ([] if d is None else [d])


def _first_touch(sessions: list[dict]) -> dict:
    """Earliest session with a marketing signal, else the earliest session."""
    ordered = sorted(sessions, key=lambda s: str(s.get("started_at") or ""))
    for s in ordered:
        if s.get("utm_source"):
            return s
    return ordered[0] if ordered else {}


def summarize_visitors(identities: list[dict], sessions: list[dict], orders: list[dict]) -> list[dict]:
    """Pure: join identities with their sessions and matched orders."""
    by_vid_sessions: dict[str, list[dict]] = {}
    for s in sessions:
        by_vid_sessions.setdefault(s.get("visitor_cookie_id") or "", []).append(s)
    by_vid_orders: dict[str, list[dict]] = {}
    for o in orders:
        by_vid_orders.setdefault(o.get("visitor_cookie_id") or "", []).append(o)

    out = []
    for ident in identities:
        vid = ident.get("visitor_cookie_id") or ""
        sess = by_vid_sessions.get(vid, [])
        ords = [o for o in by_vid_orders.get(vid, []) if not o.get("excluded_reason")]
        paid = [o for o in ords if o.get("financial_status") == "paid"]
        ft = _first_touch(sess)
        out.append({
            "visitor_id":        vid,
            "first_seen_at":     ident.get("first_seen_at"),
            "last_seen_at":      ident.get("last_seen_at"),
            "sessions":          len(sess),
            "first_utm_source":  ft.get("utm_source"),
            "first_utm_medium":  ft.get("utm_medium"),
            "first_utm_campaign": ft.get("utm_campaign"),
            "landing_url":       ft.get("landing_url"),
            "orders":            len(paid),
            "pending_orders":    len(ords) - len(paid),
            "revenue":           round(sum(float(o.get("total_price") or 0) for o in paid), 2),
            "has_google_click":  bool(ident.get("gclid") or ident.get("gbraid") or ident.get("wbraid")),
            "has_ga4":           bool(ident.get("ga_client_id")),
            "consent_advertising": ident.get("consent_advertising"),
        })
    return out


def build_timeline(sessions: list[dict], events: list[dict], orders: list[dict]) -> list[dict]:
    """Pure: one chronological list of sessions, events and orders."""
    items: list[dict] = []
    for s in sessions:
        items.append({
            "kind": "session", "at": s.get("started_at"),
            "label": "Nova sessão",
            "utm_source": s.get("utm_source"), "utm_medium": s.get("utm_medium"),
            "utm_campaign": s.get("utm_campaign"), "url": s.get("landing_url"),
        })
    for e in events:
        et = e.get("event_type") or ""
        items.append({
            "kind": "event", "at": e.get("created_at"),
            "label": _EVENT_LABEL.get(et, et),
            "event_type": et, "url": e.get("url"),
            "product_name": e.get("product_name"),
            "source": e.get("event_source"),
        })
    for o in orders:
        items.append({
            "kind": "order", "at": o.get("paid_at") or o.get("order_created_at"),
            "label": "Pedido pago" if o.get("financial_status") == "paid" else "Pedido criado",
            "order_number": o.get("order_number"), "value": o.get("total_price"),
            "financial_status": o.get("financial_status"), "match_source": o.get("match_source"),
        })
    return sorted(items, key=lambda i: str(i.get("at") or ""))


# ── DB access ────────────────────────────────────────────────────────────────


def list_visitors(sb, client_uuid: str, start_iso: str, end_iso: str, limit: int, offset: int) -> dict:
    res = (
        sb.table("tracking_identities")
        .select("visitor_cookie_id, first_seen_at, last_seen_at, gclid, gbraid, wbraid, ga_client_id, consent_advertising",
                count="exact")
        .eq("client_id", client_uuid)
        .gte("last_seen_at", start_iso)
        .lt("last_seen_at", end_iso)
        .order("last_seen_at", desc=True)
        .range(offset, offset + limit - 1)
        .execute()
    )
    identities = _data(res)
    total = getattr(res, "count", None) or len(identities)
    vids = [i["visitor_cookie_id"] for i in identities if i.get("visitor_cookie_id")]

    sessions: list[dict] = []
    orders: list[dict] = []
    if vids:
        try:
            sessions = _data(
                sb.table("tracking_sessions")
                .select("visitor_cookie_id, started_at, utm_source, utm_medium, utm_campaign, landing_url")
                .eq("client_id", client_uuid)
                .in_("visitor_cookie_id", vids)
                .limit(5000)
                .execute()
            )
        except Exception as exc:
            logger.warning("visitors: sessions read failed: %s", exc)
        try:
            orders = _data(
                sb.table("tracking_order_matches")
                .select("visitor_cookie_id, financial_status, total_price, excluded_reason")
                .eq("client_id", client_uuid)
                .in_("visitor_cookie_id", vids)
                .limit(5000)
                .execute()
            )
        except Exception as exc:
            logger.warning("visitors: orders read failed: %s", exc)

    # Is the tracker installed at all for this client (any identity ever)?
    tracking_installed = bool(identities)
    if not tracking_installed:
        try:
            any_row = _data(sb.table("tracking_identities").select("id").eq("client_id", client_uuid).limit(1).execute())
            tracking_installed = bool(any_row)
        except Exception:
            pass

    return {
        "total":              total,
        "tracking_installed": tracking_installed,
        "items":              summarize_visitors(identities, sessions, orders),
    }


def visitor_journey(sb, client_uuid: str, visitor_id: str) -> Optional[dict]:
    ident = _data(
        sb.table("tracking_identities")
        .select("visitor_cookie_id, first_seen_at, last_seen_at, gclid, gbraid, wbraid, ga_client_id, consent_advertising")
        .eq("client_id", client_uuid)
        .eq("visitor_cookie_id", visitor_id)
        .limit(1)
        .execute()
    )
    if not ident:
        return None

    def safe(q, what) -> list:
        try:
            return _data(q.execute())
        except Exception as exc:
            logger.warning("journey: %s read failed: %s", what, exc)
            return []

    sessions = safe(
        sb.table("tracking_sessions")
        .select("visitor_cookie_id, started_at, utm_source, utm_medium, utm_campaign, landing_url")
        .eq("client_id", client_uuid).eq("visitor_cookie_id", visitor_id)
        .order("started_at").limit(200), "sessions")
    events = safe(
        sb.table("tracking_events")
        .select("created_at, event_type, url, product_name, event_source")
        .eq("client_id", client_uuid).eq("visitor_cookie_id", visitor_id)
        .order("created_at", desc=True).limit(300), "events")
    orders = safe(
        sb.table("tracking_order_matches")
        .select("visitor_cookie_id, order_number, order_created_at, paid_at, total_price, financial_status,"
                " match_source, excluded_reason")
        .eq("client_id", client_uuid).eq("visitor_cookie_id", visitor_id)
        .limit(100), "orders")

    summary = summarize_visitors(ident, sessions, orders)[0]
    return {"visitor": summary, "timeline": build_timeline(sessions, events, orders)}
