"""GA4 server-side purchase: decision, payload, consent parsing, guard allowlist."""
from datetime import datetime, timedelta, timezone

from app.core import ga4_dispatch as g
from app.core import send_guard
from app.core.tracking_reconciliation import analytics_consent_from_attribute

NOW = datetime(2026, 10, 21, 15, 0, tzinfo=timezone.utc)


def _row(**over):
    row = {
        "order_id": "7351180296384", "financial_status": "paid", "excluded_reason": None,
        "checkout_token": "tok", "order_created_at": (NOW - timedelta(hours=2)).isoformat(),
        "total_price": 279.43, "currency": "BRL",
        "shadow_ga4": {"client_id": "1609280332.1792504899", "session_id": "1792515026",
                       "transaction_id": "7351180296384", "value": 279.43, "currency": "BRL",
                       "consent_analytics": True,
                       "order_created_at": (NOW - timedelta(hours=2)).isoformat()},
    }
    row.update(over)
    return row


def test_sends_paid_order_without_thank_you():
    assert g.decide(_row(), thank_you_seen=False, already_sent=False, now=NOW) == (True, "send")


def test_skips_when_thank_you_reached_or_already_sent():
    assert g.decide(_row(), thank_you_seen=True, already_sent=False, now=NOW)[0] is False
    assert g.decide(_row(), thank_you_seen=False, already_sent=True, now=NOW)[1] == "already_sent"


def test_skips_without_consent_client_id_or_payment():
    r = _row(); r["shadow_ga4"]["consent_analytics"] = False
    assert g.decide(r, False, False, NOW)[1] == "analytics_consent_not_granted"
    r = _row(); r["shadow_ga4"]["client_id"] = None
    assert g.decide(r, False, False, NOW)[1] == "missing_ga_client_id"
    assert g.decide(_row(financial_status="pending"), False, False, NOW)[1] == "not_paid"
    assert g.decide(_row(excluded_reason="pos"), False, False, NOW)[0] is False


def test_skips_older_than_72h():
    r = _row(); r["shadow_ga4"]["order_created_at"] = (NOW - timedelta(hours=80)).isoformat()
    assert g.decide(r, False, False, NOW)[1] == "older_than_72h"


def test_payload_backdated_into_original_session():
    p = g.build_payload(_row(), now=NOW)
    ev = p["events"][0]
    assert p["client_id"] == "1609280332.1792504899"
    assert ev["name"] == "purchase"
    assert ev["params"]["session_id"] == 1792515026
    assert ev["params"]["transaction_id"] == "7351180296384"
    assert p["timestamp_micros"] == int((NOW - timedelta(hours=2)).timestamp() * 1_000_000)


def test_analytics_consent_parsing():
    assert analytics_consent_from_attribute("a1m0") is True
    assert analytics_consent_from_attribute("a0m1") is False
    assert analytics_consent_from_attribute(None) is None


def test_guard_allowlist_releases_only_named_channel(monkeypatch):
    monkeypatch.setenv(send_guard.ENV_VAR, "*")
    monkeypatch.setenv(send_guard.ALLOW_ENV_VAR, "enutri-4sph:ga4_noro")
    send_guard.clear_cache()
    assert send_guard.sends_blocked("enutri-4sph", "ga4_noro") is False
    assert send_guard.sends_blocked("enutri-4sph", "purchase") is True
    assert send_guard.sends_blocked("lk-sneakers", "ga4_noro", sb=_NoDb()) is True


class _NoDb:
    def table(self, *_):
        raise RuntimeError("no db in tests")
