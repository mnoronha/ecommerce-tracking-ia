"""
Core DQG — unit tests.

Tests all 10 checks across different source_systems and failure scenarios.
No DB or external API calls — pure logic.

Run from apps/api/:
    python -m pytest tests/test_core_dqg.py -v
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from app.core.dqg import CollectionResult, DQGReport, run_dqg


# ── Helpers ───────────────────────────────────────────────────────────────────

def _meta_result(**overrides) -> CollectionResult:
    base = CollectionResult(
        source_system="meta_ads",
        semantic_domain="ADS",
        account_id="1242062509867163",
        client_currency="BRL",
        client_timezone="America/Sao_Paulo",
        period_start=date(2026, 9, 26),
        period_end=date(2026, 10, 2),
        rows=[{"campaign_id": "c1", "spend": 100.0, "meta_purchases": 5, "meta_revenue": 500.0}],
        aggregates={
            "meta_spend": 100.0,
            "meta_conversions": 5.0,
            "meta_conversion_value": 500.0,
        },
        collected_at=datetime.now(timezone.utc),
    )
    for k, v in overrides.items():
        object.__setattr__(base, k, v)
    return base


def _google_result(**overrides) -> CollectionResult:
    base = CollectionResult(
        source_system="google_ads",
        semantic_domain="ADS",
        account_id="1628971213",
        client_currency="BRL",
        client_timezone="America/Sao_Paulo",
        period_start=date(2026, 9, 26),
        period_end=date(2026, 10, 2),
        rows=[{"date": "2026-09-26:2026-10-02", "spend": 200.0}],
        aggregates={
            "google_spend": 200.0,
            "google_conversions": 10.0,
            "google_conversion_value": 1000.0,
        },
        collected_at=datetime.now(timezone.utc),
    )
    for k, v in overrides.items():
        object.__setattr__(base, k, v)
    return base


def _business_result(**overrides) -> CollectionResult:
    base = CollectionResult(
        source_system="shopify",
        semantic_domain="BUSINESS",
        account_id="lk-sneakers",
        client_currency="BRL",
        client_timezone="America/Sao_Paulo",
        period_start=date(2026, 9, 26),
        period_end=date(2026, 10, 2),
        rows=[{"order_id": "ord_1", "total_price": 300.0}],
        aggregates={
            "revenue_business": 300.0,
            "orders_count": 1.0,
        },
        collected_at=datetime.now(timezone.utc),
    )
    for k, v in overrides.items():
        object.__setattr__(base, k, v)
    return base


# ── Test: happy path ──────────────────────────────────────────────────────────

class TestDQGHappyPath:
    def test_meta_all_pass(self):
        result = _meta_result()
        report = run_dqg(result)

        check_names = [c.name for c in report.checks]
        assert "COLLECTION_OK"      in check_names
        assert "SCHEMA_OK"          in check_names
        assert "ACCOUNT_ID_OK"      in check_names
        assert "PERIOD_OK"          in check_names
        assert "CURRENCY_OK"        in check_names
        assert "TIMEZONE_OK"        in check_names
        assert "FRESHNESS_OK"       in check_names
        assert "DUPLICATE_CHECK_OK" in check_names
        assert "ROW_COUNT_SANITY_OK" in check_names
        assert "RECONCILIATION_OK"  in check_names

        # Pre-reconcile, RECONCILIATION_OK is pending-True
        assert report.get_check("RECONCILIATION_OK").passed is True
        assert report.source_state == "READY"
        assert report.value_status == "OK"

    def test_google_all_pass(self):
        report = run_dqg(_google_result())
        assert report.source_state == "READY"

    def test_business_all_pass(self):
        report = run_dqg(_business_result())
        assert report.source_state == "READY"


# ── Test: COLLECTION_OK failure ───────────────────────────────────────────────

class TestCollectionOKFailure:
    def test_error_causes_error_state(self):
        result = _meta_result(error="API timeout", rows=[], aggregates={})
        report = run_dqg(result)

        coll_check = report.get_check("COLLECTION_OK")
        assert coll_check.passed is False
        assert "timeout" in coll_check.detail

        # All subsequent checks skipped
        for name in ["SCHEMA_OK", "ACCOUNT_ID_OK", "FRESHNESS_OK"]:
            c = report.get_check(name)
            assert c is not None
            assert c.passed is False

        assert report.source_state == "ERROR"
        assert report.value_status == "MISSING"

    def test_permission_error_maps_to_permission_denied(self):
        result = _meta_result(error="403 permission denied for token", rows=[], aggregates={})
        report = run_dqg(result)
        assert report.source_state == "PERMISSION_DENIED"


# ── Test: SCHEMA_OK failure ───────────────────────────────────────────────────

class TestSchemaOKFailure:
    def test_missing_aggregate_key(self):
        result = _meta_result(aggregates={"meta_spend": 100.0})  # missing conversions
        report = run_dqg(result)

        schema = report.get_check("SCHEMA_OK")
        assert schema.passed is False
        assert report.source_state == "ERROR"


# ── Test: FRESHNESS_OK failure ────────────────────────────────────────────────

class TestFreshnessOKFailure:
    def test_stale_meta(self):
        old_time = datetime.now(timezone.utc) - timedelta(hours=50)
        result = _meta_result(collected_at=old_time)
        report = run_dqg(result)

        fresh = report.get_check("FRESHNESS_OK")
        assert fresh.passed is False
        assert report.source_state == "STALE"
        assert report.value_status == "STALE"


# ── Test: ROW_COUNT_SANITY_OK — zero rows ────────────────────────────────────

class TestZeroRows:
    def test_zero_rows_is_no_data(self):
        result = _meta_result(rows=[], aggregates={
            "meta_spend": 0.0,
            "meta_conversions": 0.0,
            "meta_conversion_value": 0.0,
        })
        report = run_dqg(result)

        rc = report.get_check("ROW_COUNT_SANITY_OK")
        assert rc.passed is True  # passes (under MAX_ROWS)
        assert "0 rows" in (rc.detail or "")
        assert report.has_data is False
        assert report.source_state == "NO_DATA"
        assert report.value_status == "NO_DATA"


# ── Test: PERIOD_OK ───────────────────────────────────────────────────────────

class TestPeriodOKFailure:
    def test_inverted_period_fails(self):
        result = _meta_result(
            period_start=date(2026, 10, 2),
            period_end=date(2026, 9, 26),
        )
        report = run_dqg(result)
        period = report.get_check("PERIOD_OK")
        assert period.passed is False
        assert report.source_state == "PARTIAL"


# ── Test: DUPLICATE_CHECK_OK ─────────────────────────────────────────────────

class TestDuplicateCheck:
    def test_duplicate_campaign_ids_fail(self):
        rows = [
            {"campaign_id": "c1", "spend": 50.0},
            {"campaign_id": "c1", "spend": 50.0},  # duplicate
        ]
        result = _meta_result(rows=rows)
        report = run_dqg(result)
        dup = report.get_check("DUPLICATE_CHECK_OK")
        assert dup.passed is False
        assert report.source_state == "PARTIAL"


# ── Test: RECONCILIATION_OK post-write ───────────────────────────────────────

class TestReconciliationCheck:
    def test_add_reconciliation_ok(self):
        result = _meta_result()
        report = run_dqg(result)
        assert report.get_check("RECONCILIATION_OK").passed is True

        report.add_reconciliation_result(passed=True)
        assert report.get_check("RECONCILIATION_OK").passed is True
        assert report.source_state == "READY"

    def test_add_reconciliation_fail_gives_partial(self):
        result = _meta_result()
        report = run_dqg(result)
        report.add_reconciliation_result(passed=False, detail="spend delta 5%")

        rc = report.get_check("RECONCILIATION_OK")
        assert rc.passed is False
        assert report.source_state == "PARTIAL"

    def test_audit_dict_contains_all_10_checks(self):
        result = _meta_result()
        report = run_dqg(result)
        report.add_reconciliation_result(passed=True)

        audit = report.to_audit_dict()
        check_names = {c["name"] for c in audit["checks"]}
        expected = {
            "COLLECTION_OK", "SCHEMA_OK", "ACCOUNT_ID_OK", "PERIOD_OK",
            "CURRENCY_OK", "TIMEZONE_OK", "FRESHNESS_OK", "DUPLICATE_CHECK_OK",
            "ROW_COUNT_SANITY_OK", "RECONCILIATION_OK",
        }
        assert check_names == expected
        assert len(audit["checks"]) == 10
