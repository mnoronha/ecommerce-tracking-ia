"""
Core Pipeline — unit tests with mocked dependencies.

Tests the pipeline orchestration logic without DB or external API calls.
All collectors, DB writes, and DB reads are mocked.

Run from apps/api/:
    python -m pytest tests/test_core_pipeline.py -v
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from app.core.dqg import CollectionResult, run_dqg
from app.core.pipeline import PipelineResult, _source_state_to_value_status, _worst_state
from app.core.reconciler import reconcile_snapshot
from app.core.snapshot_writer import build_metric_value


# ── Helpers ───────────────────────────────────────────────────────────────────

_NOW = datetime.now(timezone.utc)
_P_START = date(2026, 9, 26)
_P_END   = date(2026, 10, 2)

_GOOD_META_RESULT = CollectionResult(
    source_system="meta_ads", semantic_domain="ADS",
    account_id="1242062509867163",
    client_currency="BRL", client_timezone="America/Sao_Paulo",
    period_start=_P_START, period_end=_P_END,
    rows=[{"campaign_id": "c1", "spend": 1000.0}],
    aggregates={"meta_spend": 1000.0, "meta_conversions": 20.0, "meta_conversion_value": 5000.0},
    collected_at=_NOW,
)
_GOOD_GOOGLE_RESULT = CollectionResult(
    source_system="google_ads", semantic_domain="ADS",
    account_id="1628971213",
    client_currency="BRL", client_timezone="America/Sao_Paulo",
    period_start=_P_START, period_end=_P_END,
    rows=[{"date": "range"}],
    aggregates={"google_spend": 500.0, "google_conversions": 10.0, "google_conversion_value": 3000.0},
    collected_at=_NOW,
)
_GOOD_GA4_RESULT = CollectionResult(
    source_system="ga4", semantic_domain="JOURNEY",
    account_id="348553567",
    client_currency="BRL", client_timezone="America/Sao_Paulo",
    period_start=_P_START, period_end=_P_END,
    rows=[{"channel": "Organic Search"}],
    aggregates={"ga4_sessions": 5000, "ga4_purchases": 30.0, "ga4_revenue": 8000.0},
    collected_at=_NOW,
)
_GOOD_BUSINESS_RESULT = CollectionResult(
    source_system="shopify", semantic_domain="BUSINESS",
    account_id="lk-sneakers",
    client_currency="BRL", client_timezone="America/Sao_Paulo",
    period_start=_P_START, period_end=_P_END,
    rows=[{"order_id": "o1", "total_price": 84210.50}],
    aggregates={"revenue_business": 84210.50, "orders_count": 150.0},
    collected_at=_NOW,
)


# ── DQG integration (no mocking needed) ──────────────────────────────────────

class TestDQGIntegration:
    def test_good_result_passes_all_checks(self):
        report = run_dqg(_GOOD_META_RESULT)
        assert report.source_state == "READY"
        assert report.all_passed is True  # pre-reconcile: RECONCILIATION_OK is pending-True

    def test_error_result_fails(self):
        bad = CollectionResult(
            source_system="meta_ads", semantic_domain="ADS",
            account_id="", client_currency="BRL", client_timezone="America/Sao_Paulo",
            period_start=_P_START, period_end=_P_END,
            rows=[], aggregates={}, collected_at=_NOW,
            error="Connection refused",
        )
        report = run_dqg(bad)
        assert report.source_state == "ERROR"
        assert report.value_status == "MISSING"


# ── Reconciler unit tests ─────────────────────────────────────────────────────

class TestReconciler:
    def test_exact_match_is_ok(self):
        written = [
            {"metric_key": "meta_spend", "value": 1000.0},
            {"metric_key": "meta_conversions", "value": 20.0},
            {"metric_key": "meta_conversion_value", "value": 5000.0},
        ]
        report = reconcile_snapshot(
            snapshot_id="snap_test",
            source_system="meta_ads",
            collected_aggregates={
                "meta_spend": 1000.0,
                "meta_conversions": 20.0,
                "meta_conversion_value": 5000.0,
            },
            written_metrics=written,
        )
        assert report["status"] == "OK"
        for m in report["metrics"].values():
            assert m["status"] == "OK"

    def test_within_tolerance_is_ok(self):
        written = [{"metric_key": "meta_spend", "value": 1005.0}]
        report = reconcile_snapshot(
            snapshot_id="snap_test",
            source_system="meta_ads",
            collected_aggregates={"meta_spend": 1000.0},
            written_metrics=written,
        )
        assert report["metrics"]["meta_spend"]["status"] == "OK"

    def test_exceeds_tolerance_is_mismatch(self):
        written = [{"metric_key": "meta_spend", "value": 2000.0}]
        report = reconcile_snapshot(
            snapshot_id="snap_test",
            source_system="meta_ads",
            collected_aggregates={"meta_spend": 1000.0},
            written_metrics=written,
        )
        assert report["status"] == "RECONCILIATION_MISMATCH"
        assert report["metrics"]["meta_spend"]["status"] == "RECONCILIATION_MISMATCH"

    def test_zero_zero_is_ok(self):
        written = [{"metric_key": "meta_spend", "value": 0.0}]
        report = reconcile_snapshot(
            snapshot_id="snap_test",
            source_system="meta_ads",
            collected_aggregates={"meta_spend": 0.0},
            written_metrics=written,
        )
        assert report["metrics"]["meta_spend"]["status"] == "OK"

    def test_missing_in_snapshot(self):
        report = reconcile_snapshot(
            snapshot_id="snap_test",
            source_system="meta_ads",
            collected_aggregates={"meta_spend": 1000.0},
            written_metrics=[],  # nothing written
        )
        assert report["status"] == "RECONCILIATION_MISMATCH"
        assert report["metrics"]["meta_spend"]["status"] == "MISSING_IN_SNAPSHOT"


# ── Snapshot writer unit test ─────────────────────────────────────────────────

class TestSnapshotWriterHelper:
    def test_build_metric_value_ok(self):
        mv = build_metric_value(
            metric_key="revenue_business",
            value=84210.50,
            unit="BRL",
            value_status="OK",
            currency="BRL",
            domain="BUSINESS",
            certification_status="PROVISIONAL",
        )
        assert mv["metric_key"] == "revenue_business"
        assert mv["value"] == 84210.50
        assert mv["value_status"] == "OK"
        assert mv["certification_status"] == "PROVISIONAL"
        assert mv["domain"] == "BUSINESS"
        assert mv["snapshot_ids"] == []

    def test_build_metric_value_missing(self):
        mv = build_metric_value(
            metric_key="meta_spend",
            value=None,
            unit="BRL",
            value_status="MISSING",
            currency="BRL",
        )
        assert mv["value"] is None
        assert mv["value_status"] == "MISSING"


# ── Pipeline helpers ──────────────────────────────────────────────────────────

class TestPipelineHelpers:
    def test_worst_state_error_wins(self):
        assert _worst_state(["READY", "ERROR", "STALE"]) == "ERROR"

    def test_worst_state_partial_beats_stale(self):
        assert _worst_state(["READY", "STALE", "PARTIAL"]) == "PARTIAL"

    def test_worst_state_empty_is_ready(self):
        assert _worst_state([]) == "READY"

    def test_value_status_from_source_state(self):
        assert _source_state_to_value_status("READY",             100.0) == "OK"
        assert _source_state_to_value_status("STALE",             100.0) == "STALE"
        assert _source_state_to_value_status("ERROR",             None)  == "MISSING"
        assert _source_state_to_value_status("NOT_CONTRACTED",    None)  == "NOT_APPLICABLE"
        assert _source_state_to_value_status("READY",             None)  == "NO_DATA"  # absence ≠ zero


# ── Pipeline full run (mocked) ────────────────────────────────────────────────

class TestPipelineFullRun:
    @patch("app.core.pipeline.write_snapshot", return_value="snap-uuid-test-001")
    @patch("app.core.pipeline.update_source_reconciliation")
    @patch("app.core.pipeline._upsert_source_state")
    @patch("app.core.pipeline.collect_business",  return_value=_GOOD_BUSINESS_RESULT)
    @patch("app.core.pipeline.collect_ga4",       return_value=_GOOD_GA4_RESULT)
    @patch("app.core.pipeline.collect_google_ads", return_value=_GOOD_GOOGLE_RESULT)
    @patch("app.core.pipeline.collect_meta_ads",  return_value=_GOOD_META_RESULT)
    @patch("app.core.pipeline.get_supabase")
    def test_full_run_success(
        self,
        mock_sb,
        mock_meta, mock_google, mock_ga4, mock_biz,
        mock_upsert, mock_recon_update, mock_write,
    ):
        mock_client = MagicMock()
        mock_client.data = {
            "id": "3e20e8b9-c1b5-449f-bc5c-eb0a00704387",
            "client_id": "lk-sneakers",
            "name": "LK Sneakers",
            "timezone": "America/Sao_Paulo",
            "currency": "BRL",
            "country": "BR",
            "business_model": "ecommerce",
            "meta_ad_account_id": "1242062509867163",
            "meta_access_token": "fake_token",
            "google_ads_customer_id": "162-897-1213",
            "google_ads_refresh_token": "fake_refresh",
            "google_ads_login_customer_id": None,
            "ga4_property_id": "348553567",
        }
        mock_truth = MagicMock()
        mock_truth.data = [{"client_version": 1, "target_version": 1, "conversion_map_version": 1}]

        mock_sb_instance = MagicMock()
        mock_sb_instance.table.return_value.select.return_value.eq.return_value.single.return_value.execute.return_value = mock_client
        mock_sb_instance.table.return_value.select.return_value.eq.return_value.order.return_value.limit.return_value.execute.return_value = mock_truth
        mock_sb.return_value = mock_sb_instance

        from app.core.pipeline import run_pipeline

        result = run_pipeline("lk-sneakers", _P_START, _P_END)

        assert result.success is True
        assert result.snapshot_id == "snap-uuid-test-001"
        assert len(result.sources) == 4
        assert len(result.metrics) > 0

        # Verify all 4 collectors called
        mock_meta.assert_called_once()
        mock_google.assert_called_once()
        mock_ga4.assert_called_once()
        mock_biz.assert_called_once()

        # Verify snapshot written
        mock_write.assert_called_once()
        write_kwargs = mock_write.call_args[1]
        assert write_kwargs["client_id"] == "lk-sneakers"
        assert write_kwargs["period_start"] == _P_START
        assert write_kwargs["period_end"] == _P_END
        assert write_kwargs["view"] == "live"
