"""
Noro Tracking v4.3 — ingestion upgrades + order reconciliation (shadow mode).

Covers:
  §1  Consent extraction (storefront v4.3, Custom Pixel v2)
  §2  Identity signals (IP/UA only with ad consent, consent revocation wipes ids)
  §3  Event insert (v4.3 columns) + fallback when migration 067 is missing
  §4  Order facts + match ladder (cart_attributes → pixel → history → fallback → none)
  §5  Consent gate + exclusions + shadow GA4 / Google Ads payloads
  §6  Quality KPIs
  §7  DB orchestration (order.created never downgrades, backfill ordering, never raises)
  §8  Zero external sends

Pure unit tests: no FastAPI, no network, no real DB.
"""
from __future__ import annotations

import inspect
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.core import tracking_ingestion as ti
from app.core import tracking_reconciliation as tr

CLIENT = "aaaaaaaa-0000-0000-0000-000000000001"
NOW = datetime(2026, 10, 16, 18, 0, tzinfo=timezone.utc)


# ── Mock Supabase ────────────────────────────────────────────────────────────


class _Res:
    def __init__(self, data):
        self.data = data


class _Query:
    def __init__(self, sb, table):
        self.sb, self.table_name, self.op, self.payload, self.kwargs = sb, table, "select", None, {}
        self.filters = []

    # builders
    def select(self, *a, **k):
        self.op = "select"
        return self

    def insert(self, row):
        self.op, self.payload = "insert", row
        return self

    def update(self, row):
        self.op, self.payload = "update", row
        return self

    def upsert(self, row, **kwargs):
        self.op, self.payload, self.kwargs = "upsert", row, kwargs
        return self

    def eq(self, c, v):
        self.filters.append(("eq", c, v))
        return self

    def neq(self, c, v):
        self.filters.append(("neq", c, v))
        return self

    def in_(self, c, v):
        self.filters.append(("in", c, v))
        return self

    def gte(self, c, v):
        return self

    def lt(self, c, v):
        return self

    def order(self, *a, **k):
        return self

    def limit(self, *a):
        return self

    def maybe_single(self):
        return self

    @property
    def not_(self):
        return self

    def is_(self, *a):
        return self

    def execute(self):
        call = {"table": self.table_name, "op": self.op, "payload": self.payload,
                "kwargs": self.kwargs, "filters": self.filters}
        self.sb.calls.append(call)
        hook = self.sb.fail_hook
        if hook:
            hook(call)
        return _Res(self.sb.responses.get((self.table_name, self.op)))


class MockSB:
    def __init__(self, responses=None, fail_hook=None):
        self.responses = responses or {}
        self.calls = []
        self.fail_hook = fail_hook

    def table(self, name):
        return _Query(self, name)

    def writes(self, table=None):
        return [c for c in self.calls if c["op"] in ("insert", "update", "upsert")
                and (table is None or c["table"] == table)]


def _ev(**kw):
    base = dict(event_id="e1", event_name="page_view", occurred_at=NOW, session_id="s1",
                visitor_cookie_id="v1", url="/", referrer=None, utm_source=None, utm_medium=None,
                utm_campaign=None, utm_content=None, utm_term=None, fbp=None, fbc=None, fbclid=None,
                gclid=None, gbraid=None, wbraid=None, ga_client_id=None, ga_session_id=None,
                order_id=None, value=None, currency=None, product_id=None, variant_id=None, sku=None,
                product_name=None, product_price=None, product_quantity=None, product_category=None,
                properties={})
    base.update(kw)
    return SimpleNamespace(**base)


def _order_event(event_type="order.paid", status="paid", total=199.9, meta=None, email="Ana@Ex.com", utm=None, test=False):
    return {
        "event_type": event_type,
        "timestamp": "2026-10-16T15:00:00+00:00",
        "customer": {"email": email},
        "order_data": {"id": "5551", "number": "#1001", "status": status, "total": total, "currency": "BRL"},
        "utm": utm or {},
        "metadata": {"source_name": "web", "checkout_token": "ck_1", "processed_at": "2026-10-16T15:05:00+00:00",
                     **(meta or {})},
        "raw_payload": {"test": test},
    }


# ── §1 Consent ───────────────────────────────────────────────────────────────


class TestConsent:
    def test_storefront_consent(self):
        e = _ev(properties={"consent": {"analytics": True, "advertising": False, "source": "shopify"}})
        assert ti.extract_consent(e) == (True, False)

    def test_pixel_consent_combines_storefront_attribute(self):
        e = _ev(properties={"identity": {"consent": {"analytics": None, "marketing": True, "storefront": "a1m0"}}})
        assert ti.extract_consent(e) == (True, False)

    def test_unknown_consent_for_legacy_events(self):
        assert ti.extract_consent(_ev()) == (None, None)

    def test_cart_attribute_parser(self):
        assert tr.consent_from_attribute("a1m1") is True
        assert tr.consent_from_attribute("a0m0") is False
        assert tr.consent_from_attribute(None) is None
        assert tr.consent_from_attribute("junk") is None


# ── §2 Identity signals ──────────────────────────────────────────────────────


class TestIdentitySignals:
    def test_ip_and_ua_only_with_ad_consent(self):
        meta = {"ip": "200.1.2.3", "user_agent": "UA"}
        granted = ti._identity_signals(_ev(properties={"consent": {"advertising": True}}), meta)
        denied = ti._identity_signals(_ev(properties={"consent": {"advertising": False}}), meta)
        unknown = ti._identity_signals(_ev(), meta)
        assert granted["last_ip_address"] == "200.1.2.3" and granted["last_user_agent"] == "UA"
        assert "last_ip_address" not in denied and "last_ip_address" not in unknown

    def test_gclid_and_ga_session_persisted(self):
        s = ti._identity_signals(_ev(gclid="Cj0KCQ_abc", ga_session_id="1760000000"), None)
        assert s["gclid"] == "Cj0KCQ_abc" and s["ga_session_id"] == "1760000000"

    def test_consent_revocation_wipes_ad_ids_on_update(self):
        sb = MockSB({("tracking_identities", "select"): {"id": "x"}})
        e = _ev(properties={"consent": {"advertising": False}})
        created, linked = ti._resolve_identity(sb, CLIENT, e, {"ip": "1.1.1.1"})
        assert (created, linked) == (False, True)
        upd = sb.writes("tracking_identities")[0]["payload"]
        assert upd["gclid"] is None and upd["fbc"] is None and upd["last_ip_address"] is None
        assert upd["consent_advertising"] is False


# ── §3 Event insert ──────────────────────────────────────────────────────────


class TestEventInsert:
    def test_pixel_event_columns(self):
        sb = MockSB()
        e = _ev(event_name="begin_checkout", ga_session_id="1760000000",
                properties={"checkout_token": "ck_9", "source": "shopify_web_pixel",
                            "identity": {"consent": {"analytics": True, "marketing": True, "storefront": "a1m1"}}})
        out = ti.ingest_events(sb, CLIENT, [e], {"ip": "1.2.3.4", "user_agent": "UA"})
        assert out["events_inserted"] == 1
        row = sb.writes("tracking_events")[0]["payload"]
        assert row["checkout_token"] == "ck_9"
        assert row["event_source"] == "shopify_web_pixel"
        assert row["ga_session_id"] == "1760000000"
        assert row["consent_advertising"] is True

    def test_storefront_source_detected(self):
        assert ti.extract_event_source(_ev(properties={"tracker_version": "4.3.1"})) == "storefront"

    def test_fallback_when_migration_067_missing(self):
        def fail_on_new_columns(call):
            if call["table"] == "tracking_events" and call["op"] == "insert" and "checkout_token" in call["payload"]:
                raise Exception('column "checkout_token" of relation "tracking_events" does not exist')

        sb = MockSB(fail_hook=fail_on_new_columns)
        out = ti.ingest_events(sb, CLIENT, [_ev(properties={"checkout_token": "ck"})])
        assert out["events_inserted"] == 1
        inserts = [c for c in sb.calls if c["table"] == "tracking_events" and c["op"] == "insert"]
        assert len(inserts) == 2 and "checkout_token" not in inserts[-1]["payload"]


# ── §4 Facts + match ladder ──────────────────────────────────────────────────


class TestMatchLadder:
    def test_facts_from_events_row(self):
        f = tr.facts_from_event(_order_event(meta={"visitor_cookie_id": "v1", "noro_consent": "a1m1",
                                                   "ga_session_id": "1760000000"}))
        assert f["order_id"] == "5551" and f["financial_status"] == "paid"
        assert f["email_hash"] == tr.hash_email("ana@ex.com")
        assert f["paid_at"] == datetime(2026, 10, 16, 15, 5, tzinfo=timezone.utc)
        assert f["consent_advertising"] is True and f["ga_session_id"] == "1760000000"

    def test_cart_attributes_first(self):
        f = tr.facts_from_event(_order_event(meta={"visitor_cookie_id": "v1", "noro_session_id": "s1"}))
        m = tr.decide_match(f, {"visitor_cookie_id": "vX"}, {"visitor_cookie_id": "vY"})
        assert m["match_source"] == "cart_attributes" and m["visitor_cookie_id"] == "v1" and m["session_id"] == "s1"

    def test_pixel_second(self):
        f = tr.facts_from_event(_order_event())
        m = tr.decide_match(f, {"visitor_cookie_id": "vP", "session_id": "sP", "utm_source": "google",
                                "gclid": "Cj0_pixel", "consent_advertising": True}, None)
        assert m["match_source"] == "pixel" and m["utm_source"] == "google" and m["gclid"] == "Cj0_pixel"

    def test_customer_history_third(self):
        f = tr.facts_from_event(_order_event())
        m = tr.decide_match(f, None, {"visitor_cookie_id": "vH"})
        assert m["match_source"] == "customer_history" and m["visitor_cookie_id"] == "vH"

    def test_shopify_fallback_from_landing_site(self):
        f = tr.facts_from_event(_order_event(meta={"landing_site": "/?gclid=Cj0abc"}))
        assert tr.decide_match(f)["match_source"] == "shopify_fallback"

    def test_none_when_no_evidence(self):
        f = tr.facts_from_event(_order_event())
        assert tr.decide_match(f)["match_source"] == "none"

    def test_identity_enriches_click_ids(self):
        f = tr.facts_from_event(_order_event(meta={"visitor_cookie_id": "v1", "noro_consent": "a1m1"}))
        m = tr.decide_match(f, identity={"gclid": "Cj0_id", "ga_client_id": "111.222", "consent_advertising": True})
        assert m["gclid"] == "Cj0_id" and m["ga_client_id"] == "111.222" and m["click_source"] == "gclid"


# ── §5 Consent gate, exclusions, shadow payloads ────────────────────────────


class TestShadow:
    def test_consent_denied_wipes_ids_and_blocks_ads(self):
        f = tr.facts_from_event(_order_event(meta={"visitor_cookie_id": "v1", "noro_consent": "a1m0",
                                                   "gclid": "Cj0_leak", "ga_client_id": "111.222"}))
        m = tr.decide_match(f)
        assert m["gclid"] is None and m["consent_advertising"] is False
        ga4, ads = tr.build_shadow_payloads(f, m, NOW)
        assert ads["would_send"] is False and "advertising_consent_denied" in ads["blockers"]
        assert ga4["consent"]["ad_user_data"] == "denied"
        row = tr.build_match_row(CLIENT, f, m, now=NOW)
        assert "gclid" in row and row["gclid"] is None  # explicit NULL overwrites stale ids

    def test_ga4_ready_and_in_session(self):
        f = tr.facts_from_event(_order_event(meta={"visitor_cookie_id": "v1", "noro_consent": "a1m1",
                                                   "ga_client_id": "111.222", "ga_session_id": "1760000000"}))
        ga4, _ = tr.build_shadow_payloads(f, tr.decide_match(f), NOW)
        assert ga4["would_send"] and ga4["session_id"] == "1760000000" and ga4["blockers"] == []
        assert ga4["transaction_id"] == "5551" and ga4["timestamp"]

    def test_ga4_timestamp_dropped_outside_72h(self):
        f = tr.facts_from_event(_order_event(meta={"ga_client_id": "111.222"}))
        ga4, _ = tr.build_shadow_payloads(f, tr.decide_match(f), NOW + timedelta(days=4))
        assert ga4["timestamp"] is None

    def test_ads_click_mode_and_datetime_format(self):
        f = tr.facts_from_event(_order_event(meta={"visitor_cookie_id": "v1", "noro_consent": "a1m1",
                                                   "gbraid": "GB_123456"}))
        _, ads = tr.build_shadow_payloads(f, tr.decide_match(f), NOW)
        assert ads["mode"] == "click" and ads["click_field"] == "gbraid"
        assert ads["conversion_date_time"] == "2026-10-16 15:05:00+00:00"

    def test_ads_enhanced_only_without_click(self):
        f = tr.facts_from_event(_order_event())
        _, ads = tr.build_shadow_payloads(f, tr.decide_match(f), NOW)
        assert ads["would_send"] and ads["mode"] == "enhanced_only" and ads["has_user_identifiers"]

    def test_exclusions_have_no_shadow(self):
        for kwargs, reason in (({"meta": {"source_name": "pos"}}, "offline"),
                               ({"total": 0}, "zero_value"),
                               ({"test": True}, "test")):
            f = tr.facts_from_event(_order_event(**kwargs))
            assert tr.exclusion_reason(f) == reason
            assert tr.build_shadow_payloads(f, tr.decide_match(f), NOW) == (None, None)

    def test_pending_pix_has_no_shadow(self):
        f = tr.facts_from_event(_order_event(event_type="order.created", status="pending"))
        assert f["paid_at"] is None
        assert tr.build_shadow_payloads(f, tr.decide_match(f), NOW) == (None, None)


# ── §6 Quality KPIs ──────────────────────────────────────────────────────────


class TestQuality:
    def test_kpis(self):
        rows = [
            {"financial_status": "paid", "total_price": 100, "match_source": "cart_attributes",
             "gclid": "x", "consent_advertising": True,
             "shadow_ga4": {"would_send": True, "session_id": "1"}, "shadow_google_ads": {"would_send": True, "mode": "click"}},
            {"financial_status": "paid", "total_price": 50, "match_source": "pixel", "consent_advertising": None,
             "shadow_ga4": {"would_send": True, "session_id": None}, "shadow_google_ads": {"would_send": True, "mode": "enhanced_only"}},
            {"financial_status": "paid", "total_price": 50, "match_source": "none", "consent_advertising": False,
             "shadow_ga4": {"would_send": False}, "shadow_google_ads": {"would_send": False}},
            {"financial_status": "paid", "total_price": 80, "match_source": "none", "excluded_reason": "offline"},
            {"financial_status": "pending", "total_price": 70, "match_source": "none"},
        ]
        k = tr.compute_quality(rows, shopify_paid_orders=4, shopify_paid_revenue=200.0)
        assert k["paid_orders"] == 3 and k["paid_revenue"] == 200.0
        assert k["coverage_orders_pct"] == 75.0 and k["coverage_revenue_pct"] == 100.0
        assert k["match_rate_pct"] == 66.7
        assert k["match_sources"]["cart_attributes"] == 1 and k["match_sources"]["none"] == 1
        assert k["google_click_rate_pct"] == 33.3
        assert k["consent_advertising"] == {"granted": 1, "denied": 1, "unknown": 1}
        assert k["ga4_ready_pct"] == 66.7 and k["ga4_in_session_pct"] == 33.3
        assert k["google_ads_click_pct"] == 33.3
        assert k["excluded_orders"] == 1 and k["pending_orders"] == 1

    def test_empty_period(self):
        k = tr.compute_quality([], None, None)
        assert k["paid_orders"] == 0 and k["match_rate_pct"] is None and k["coverage_orders_pct"] is None


# ── §7 Orchestration ─────────────────────────────────────────────────────────


class TestOrchestration:
    def test_order_created_never_overwrites(self):
        sb = MockSB()
        tr.reconcile_order(sb, CLIENT, _order_event(event_type="order.created", status="pending"))
        up = sb.writes("tracking_order_matches")[0]
        assert up["op"] == "upsert" and up["kwargs"]["ignore_duplicates"] is True

    def test_order_paid_overwrites(self):
        sb = MockSB()
        tr.reconcile_order(sb, CLIENT, _order_event(meta={"visitor_cookie_id": "v1"}))
        up = sb.writes("tracking_order_matches")[0]
        assert up["kwargs"]["ignore_duplicates"] is False
        assert up["payload"]["match_source"] == "cart_attributes"
        assert "email" not in up["payload"] and up["payload"]["email_hash"]

    def test_pixel_lookup_used_without_cart_attributes(self):
        sb = MockSB({("tracking_events", "select"): [{"visitor_cookie_id": "vP", "session_id": "sP"}]})
        tr.reconcile_order(sb, CLIENT, _order_event())
        assert sb.writes("tracking_order_matches")[0]["payload"]["match_source"] == "pixel"

    def test_cancel_moves_status_only(self):
        sb = MockSB()
        tr.record_from_webhook(CLIENT, _order_event(event_type="order.cancelled", status="voided"), sb=sb)
        w = sb.writes("tracking_order_matches")[0]
        assert w["op"] == "update" and w["payload"]["financial_status"] == "voided"
        assert set(w["payload"]) == {"financial_status", "updated_at"}

    def test_backfill_processes_paid_after_created(self):
        rows = [_order_event(event_type="order.paid"), _order_event(event_type="order.created", status="pending")]
        sb = MockSB({("events", "select"): rows})
        out = tr.backfill_from_events(sb, CLIENT, "enutri-px", NOW - timedelta(days=7))
        assert out["events_read"] == 2 and out["orders_reconciled"] == 2
        ups = sb.writes("tracking_order_matches")
        assert ups[0]["payload"]["financial_status"] == "pending" and ups[-1]["payload"]["financial_status"] == "paid"

    def test_never_raises_into_webhook(self):
        def boom(call):
            raise RuntimeError("db down")
        tr.record_from_webhook(CLIENT, _order_event(), sb=MockSB(fail_hook=boom))  # must not raise
        tr.record_from_webhook(None, _order_event(), sb=MockSB())


# ── §8 Zero external sends ───────────────────────────────────────────────────


class TestZeroExternalSends:
    def test_no_http_clients_in_tracking_modules(self):
        for mod in (ti, tr):
            src = inspect.getsource(mod)
            for forbidden in ("import httpx", "import requests", "urllib.request", "google_ads.", "meta_capi.", "ga4.send"):
                assert forbidden not in src, f"{mod.__name__} must not reference {forbidden}"


# ── §9 Hourly Shopify sync also reconciles ───────────────────────────────────


class TestShopifySyncReconciles:
    def test_sync_calls_reconciliation_and_requests_join_fields(self):
        import pathlib
        src = (pathlib.Path(__file__).resolve().parents[1] / "app" / "services" / "shopify_sync.py").read_text(encoding="utf-8")
        assert "tracking_reconciliation.record_from_webhook(client_uuid, event, order_uuid)" in src
        assert "checkout_token" in src.split("_ORDER_FIELDS", 1)[1].split(")", 1)[0]
