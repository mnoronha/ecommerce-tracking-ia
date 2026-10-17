"""Noro Tracking — Visitors / journey pure functions."""
from __future__ import annotations

from app.core import tracking_visitors as tv

IDENTS = [
    {"visitor_cookie_id": "v1", "first_seen_at": "2026-10-01T10:00:00Z", "last_seen_at": "2026-10-05T10:00:00Z",
     "gclid": "Cj0_x", "ga_client_id": "1.2", "consent_advertising": True},
    {"visitor_cookie_id": "v2", "first_seen_at": "2026-10-02T10:00:00Z", "last_seen_at": "2026-10-02T11:00:00Z"},
]
SESSIONS = [
    {"visitor_cookie_id": "v1", "started_at": "2026-10-03T10:00:00Z", "utm_source": None},
    {"visitor_cookie_id": "v1", "started_at": "2026-10-01T10:00:00Z", "utm_source": "google", "utm_medium": "cpc",
     "landing_url": "/?utm_source=google"},
    {"visitor_cookie_id": "v2", "started_at": "2026-10-02T10:00:00Z", "utm_source": None, "landing_url": "/"},
]
ORDERS = [
    {"visitor_cookie_id": "v1", "financial_status": "paid", "total_price": 100},
    {"visitor_cookie_id": "v1", "financial_status": "pending", "total_price": 50},
    {"visitor_cookie_id": "v1", "financial_status": "paid", "total_price": 999, "excluded_reason": "test"},
]


class TestSummaries:
    def test_join(self):
        out = {v["visitor_id"]: v for v in tv.summarize_visitors(IDENTS, SESSIONS, ORDERS)}
        v1, v2 = out["v1"], out["v2"]
        assert v1["sessions"] == 2 and v1["first_utm_source"] == "google"
        assert v1["orders"] == 1 and v1["pending_orders"] == 1 and v1["revenue"] == 100.0
        assert v1["has_google_click"] and v1["has_ga4"]
        assert v2["orders"] == 0 and v2["first_utm_source"] is None and v2["landing_url"] == "/"

    def test_no_identifiers_leak(self):
        row = tv.summarize_visitors(IDENTS, SESSIONS, ORDERS)[0]
        assert "gclid" not in row and "ga_client_id" not in row


class TestTimeline:
    def test_chronological(self):
        t = tv.build_timeline(
            [{"started_at": "2026-10-01T10:00:00Z", "utm_source": "google"}],
            [{"created_at": "2026-10-01T10:05:00Z", "event_type": "add_to_cart"},
             {"created_at": "2026-10-01T10:01:00Z", "event_type": "pageview"}],
            [{"paid_at": "2026-10-01T11:00:00Z", "financial_status": "paid", "order_number": "#1"}],
        )
        assert [i["kind"] for i in t] == ["session", "event", "event", "order"]
        assert t[1]["label"] == "Página vista" and t[2]["label"] == "Adicionou ao carrinho"
        assert t[3]["label"] == "Pedido pago"
