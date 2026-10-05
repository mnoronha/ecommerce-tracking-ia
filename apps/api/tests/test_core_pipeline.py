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


# ── MER derived state propagation ────────────────────────────────────────────

class TestMERDerivedState:
    """MER must be OK when both revenue_business and total_spend are OK.

    Root cause of the original bug: _dep_source("total_spend") returned "derived"
    which is never a key in the health dict → health.get("derived") = None →
    _STATE_RANK.get(None, 6) = 6 = MISSING → MER was MISSING even when all
    sources were READY.

    Fix: _dep_source returns metric_key for derived metrics; health[m_def.key]
    is set after computing each derived state.
    """

    @patch("app.core.pipeline.write_snapshot", return_value="snap-mer-test")
    @patch("app.core.pipeline.update_source_reconciliation")
    @patch("app.core.pipeline._upsert_source_state")
    @patch("app.core.pipeline.collect_business",   return_value=_GOOD_BUSINESS_RESULT)
    @patch("app.core.pipeline.collect_ga4",        return_value=_GOOD_GA4_RESULT)
    @patch("app.core.pipeline.collect_google_ads", return_value=_GOOD_GOOGLE_RESULT)
    @patch("app.core.pipeline.collect_meta_ads",   return_value=_GOOD_META_RESULT)
    @patch("app.core.pipeline.get_supabase")
    def test_mer_ok_when_all_sources_ready(
        self, mock_sb, mock_meta, mock_google, mock_ga4, mock_biz,
        mock_upsert, mock_recon_update, mock_write,
    ):
        mock_client = MagicMock()
        mock_client.data = {
            "id": "uuid-lk", "client_id": "lk-sneakers", "name": "LK",
            "timezone": "America/Sao_Paulo", "currency": "BRL", "country": "BR",
            "business_model": "ecommerce",
            "meta_ad_account_id": "1242062509867163", "meta_access_token": "tok",
            "google_ads_customer_id": "162-897-1213", "google_ads_refresh_token": "ref",
            "google_ads_login_customer_id": None, "ga4_property_id": "348553567",
        }
        mock_truth = MagicMock()
        mock_truth.data = [{"client_version": 1, "target_version": 1, "conversion_map_version": 1}]
        mock_sb_i = MagicMock()
        mock_sb_i.table.return_value.select.return_value.eq.return_value.single.return_value.execute.return_value = mock_client
        mock_sb_i.table.return_value.select.return_value.eq.return_value.order.return_value.limit.return_value.execute.return_value = mock_truth
        mock_sb.return_value = mock_sb_i

        from app.core.pipeline import run_pipeline
        result = run_pipeline("lk-sneakers", _P_START, _P_END)

        metric_map = {m["metric_key"]: m for m in result.metrics}

        # total_spend: meta_spend=1000 + google_spend=500 = 1500
        ts = metric_map["total_spend"]
        assert ts["value"] == 1500.0, f"total_spend expected 1500.0 got {ts['value']}"
        assert ts["value_status"] == "OK", f"total_spend status expected OK got {ts['value_status']}"

        # mer: revenue_business=84210.5 / total_spend=1500 = 56.14
        mer = metric_map["mer"]
        assert mer["value"] is not None, "mer value must not be None when all sources READY"
        assert mer["value_status"] == "OK", f"mer status expected OK got {mer['value_status']}"
        assert abs(mer["value"] - round(84210.50 / 1500.0, 4)) < 0.001

    @patch("app.core.pipeline.write_snapshot", return_value="snap-mer-missing")
    @patch("app.core.pipeline.update_source_reconciliation")
    @patch("app.core.pipeline._upsert_source_state")
    @patch("app.core.pipeline.collect_business",   return_value=_GOOD_BUSINESS_RESULT)
    @patch("app.core.pipeline.collect_ga4",        return_value=_GOOD_GA4_RESULT)
    @patch("app.core.pipeline.collect_google_ads", return_value=CollectionResult(
        source_system="google_ads", semantic_domain="ADS",
        account_id="1628971213", client_currency="BRL", client_timezone="America/Sao_Paulo",
        period_start=_P_START, period_end=_P_END,
        rows=[], aggregates={}, collected_at=_NOW,
        error="token error: PERMISSION_DENIED",
    ))
    @patch("app.core.pipeline.collect_meta_ads",  return_value=CollectionResult(
        source_system="meta_ads", semantic_domain="ADS",
        account_id="1242062509867163", client_currency="BRL", client_timezone="America/Sao_Paulo",
        period_start=_P_START, period_end=_P_END,
        rows=[], aggregates={}, collected_at=_NOW,
        error="token invalid: permission denied",
    ))
    @patch("app.core.pipeline.get_supabase")
    def test_mer_missing_when_ad_sources_unavailable(
        self, mock_sb, mock_meta, mock_google, mock_ga4, mock_biz,
        mock_upsert, mock_recon_update, mock_write,
    ):
        mock_client = MagicMock()
        mock_client.data = {
            "id": "uuid-lk", "client_id": "lk-sneakers", "name": "LK",
            "timezone": "America/Sao_Paulo", "currency": "BRL", "country": "BR",
            "business_model": "ecommerce",
            "meta_ad_account_id": "1242062509867163", "meta_access_token": "tok",
            "google_ads_customer_id": "162-897-1213", "google_ads_refresh_token": "ref",
            "google_ads_login_customer_id": None, "ga4_property_id": "348553567",
        }
        mock_truth = MagicMock()
        mock_truth.data = [{"client_version": 1, "target_version": 1, "conversion_map_version": 1}]
        mock_sb_i = MagicMock()
        mock_sb_i.table.return_value.select.return_value.eq.return_value.single.return_value.execute.return_value = mock_client
        mock_sb_i.table.return_value.select.return_value.eq.return_value.order.return_value.limit.return_value.execute.return_value = mock_truth
        mock_sb.return_value = mock_sb_i

        from app.core.pipeline import run_pipeline
        result = run_pipeline("lk-sneakers", _P_START, _P_END)

        metric_map = {m["metric_key"]: m for m in result.metrics}

        # total_spend should be 0 in all_aggs but MISSING state (ad sources unavailable)
        ts = metric_map["total_spend"]
        assert ts["value"] is None, "total_spend value must be None when ad sources unavailable"
        assert ts["value_status"] == "MISSING"

        # mer should also be MISSING (depends on total_spend which is MISSING)
        mer = metric_map["mer"]
        assert mer["value"] is None
        assert mer["value_status"] == "MISSING"


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


# ── Admin endpoint integration (TestClient, same path as production) ──────────

class TestAdminPipelineEndpoint:
    """
    Exercises POST /agency/v1/admin/pipeline-run via TestClient.
    Uses real CollectionResult values matching the production case:
      meta_spend=7791.46, google_spend=6104.08, revenue_business=261675.15
      → total_spend=13895.54, mer≈18.8316

    Mocked: collectors, write_snapshot, _upsert_source_state,
            update_source_reconciliation, pipeline+admin DB reads.
    NOT mocked: pipeline orchestration, DQG, metric registry, _dep_source.
    """

    _SNAP_ID = "snap-endpoint-test-001"
    _CLIENT_ROW = {
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

    @staticmethod
    def _make_pipeline_db() -> MagicMock:
        """Mock for app.core.pipeline.get_supabase: handles client + truth loads."""
        db = MagicMock()
        client_r = MagicMock()
        client_r.data = TestAdminPipelineEndpoint._CLIENT_ROW
        db.table.return_value.select.return_value.eq.return_value.single.return_value.execute.return_value = client_r
        truth_r = MagicMock()
        truth_r.data = [{"client_version": 1, "target_version": 1, "conversion_map_version": 1}]
        db.table.return_value.select.return_value.eq.return_value.order.return_value.limit.return_value.execute.return_value = truth_r
        return db

    @staticmethod
    def _make_admin_db(snap_id: str) -> MagicMock:
        """Mock for app.database.get_supabase: handles DS selects + snapshot reads."""
        db = MagicMock()
        snap_row = {
            "id": snap_id,
            "period_start": "2026-09-28",
            "period_end": "2026-10-04",
            "view": "live",
            "computed_at": "2026-10-05T10:00:00+00:00",
            "client_truth_version": 1,
            "target_truth_version": 1,
            "metrics": [
                {"metric_key": "mer", "value": 18.8316, "value_status": "OK",
                 "certification_status": "PROVISIONAL", "unit": "ratio", "currency": None, "domain": "ADS"},
                {"metric_key": "total_spend", "value": 13895.54, "value_status": "OK",
                 "certification_status": "PROVISIONAL", "unit": "BRL", "currency": "BRL", "domain": "ADS"},
                {"metric_key": "revenue_business", "value": 261675.15, "value_status": "OK",
                 "certification_status": "PROVISIONAL", "unit": "BRL", "currency": "BRL", "domain": "BUSINESS"},
            ],
        }

        def table_side(name):
            t = MagicMock()
            if name == "core_data_sources":
                ds_r = MagicMock()
                ds_r.data = []
                t.select.return_value.eq.return_value.execute.return_value = ds_r
            elif name == "core_metric_snapshots":
                # snapshot by id: .select(cols).eq(id).limit(1).execute()
                snap_r = MagicMock()
                snap_r.data = [snap_row]
                t.select.return_value.eq.return_value.limit.return_value.execute.return_value = snap_r
                # count: .select(id, count=exact).eq(client_id).eq(period_start).eq(period_end).execute()
                count_r = MagicMock()
                count_r.count = 3
                count_r.data = []
                t.select.return_value.eq.return_value.eq.return_value.eq.return_value.execute.return_value = count_r
            return t

        db.table.side_effect = table_side
        return db

    @patch("app.database.get_supabase")
    @patch("app.core.pipeline.write_snapshot", return_value=_SNAP_ID)
    @patch("app.core.pipeline.update_source_reconciliation")
    @patch("app.core.pipeline._upsert_source_state")
    @patch("app.core.pipeline.collect_business", return_value=CollectionResult(
        source_system="shopify", semantic_domain="BUSINESS",
        account_id="lk-sneakers", client_currency="BRL", client_timezone="America/Sao_Paulo",
        period_start=date(2026, 9, 28), period_end=date(2026, 10, 4),
        rows=[{"order_id": "o1", "total_price": 261675.15}],
        aggregates={"revenue_business": 261675.15, "orders_count": 89.0},
        collected_at=_NOW,
    ))
    @patch("app.core.pipeline.collect_ga4", return_value=CollectionResult(
        source_system="ga4", semantic_domain="JOURNEY",
        account_id="348553567", client_currency="BRL", client_timezone="America/Sao_Paulo",
        period_start=date(2026, 9, 28), period_end=date(2026, 10, 4),
        rows=[{"channel": "Organic Search"}],
        aggregates={"ga4_sessions": 296909, "ga4_purchases": 54.0, "ga4_revenue": 137577.37},
        collected_at=_NOW,
    ))
    @patch("app.core.pipeline.collect_google_ads", return_value=CollectionResult(
        source_system="google_ads", semantic_domain="ADS",
        account_id="1628971213", client_currency="BRL", client_timezone="America/Sao_Paulo",
        period_start=date(2026, 9, 28), period_end=date(2026, 10, 4),
        rows=[{"date": "range"}],
        aggregates={"google_spend": 6104.08, "google_conversions": 42.0, "google_conversion_value": 71302.10},
        collected_at=_NOW,
    ))
    @patch("app.core.pipeline.collect_meta_ads", return_value=CollectionResult(
        source_system="meta_ads", semantic_domain="ADS",
        account_id="1242062509867163", client_currency="BRL", client_timezone="America/Sao_Paulo",
        period_start=date(2026, 9, 28), period_end=date(2026, 10, 4),
        rows=[{"campaign_id": "c1", "spend": 7791.46}],
        aggregates={"meta_spend": 7791.46, "meta_conversions": 55.0, "meta_conversion_value": 139850.22},
        collected_at=_NOW,
    ))
    @patch("app.core.pipeline.get_supabase")
    def test_endpoint_mer_ok_and_snapshot_audit_populated(
        self,
        mock_pipeline_db,
        mock_meta, mock_google, mock_ga4, mock_biz,
        mock_upsert, mock_recon_update,
        mock_write,
        mock_admin_db,
    ):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from app.agency_api.v1.admin_router import admin_router, _PIPELINE_TAG
        from app.agency_api.v1.auth import SCOPE_ADMIN_ONLY, AuthContext

        mock_pipeline_db.return_value = self._make_pipeline_db()
        mock_admin_db.return_value = self._make_admin_db(self._SNAP_ID)

        test_app = FastAPI()
        test_app.include_router(admin_router)
        test_app.dependency_overrides[SCOPE_ADMIN_ONLY] = lambda: AuthContext(scope="agency_admin")

        with TestClient(test_app) as client:
            resp = client.post("/agency/v1/admin/pipeline-run", json={
                "client_id": "lk-sneakers",
                "period_start": "2026-09-28",
                "period_end": "2026-10-04",
                "view": "live",
            })

        assert resp.status_code == 200, f"non-200: {resp.text}"
        body = resp.json()

        # ── 1. pipeline_tag proves new code is deployed ──────────────────────
        assert body["pipeline_tag"] == _PIPELINE_TAG, (
            f"pipeline_tag mismatch: got {body.get('pipeline_tag')!r}, "
            f"expected {_PIPELINE_TAG!r}. "
            "Railway may still be running old code."
        )

        # ── 2. MER must be computed (fix: _dep_source for derived metrics) ───
        metric_map = {m["metric_key"]: m for m in body["metrics"]}

        total_spend_m = metric_map["total_spend"]
        assert total_spend_m["value_status"] == "OK"
        assert abs(total_spend_m["value"] - 13895.54) < 0.01, (
            f"total_spend expected ≈13895.54 got {total_spend_m['value']}"
        )

        mer_m = metric_map["mer"]
        assert mer_m["value_status"] == "OK", (
            f"mer.value_status expected OK got {mer_m['value_status']}. "
            "Check _dep_source: derived metrics must register health[m_def.key]."
        )
        assert mer_m["value"] is not None, "mer.value must not be None when all sources READY"
        expected_mer = round(261675.15 / 13895.54, 4)
        assert abs(mer_m["value"] - expected_mer) < 0.001, (
            f"mer expected ≈{expected_mer} got {mer_m['value']}"
        )

        # ── 3. snapshot_audit must be populated (fix: remove schema_version from SELECT) ──
        assert body.get("snapshot_audit_error") is None, (
            f"snapshot_audit_error: {body.get('snapshot_audit_error')}"
        )
        sa = body.get("snapshot_audit")
        assert sa is not None, (
            "snapshot_audit is null. If snapshot_audit_error has a DB column error, "
            "the schema_version fix in SELECT may not be deployed."
        )
        assert sa["snapshot_id"] == self._SNAP_ID
        assert sa["schema_version"] == "1.1"
        assert sa["metric_count"] == 3
        assert sa["total_snapshots_for_period"] == 3

        # ── 4. success + health ───────────────────────────────────────────────
        assert body["success"] is True
        assert body["health"]["meta_ads"] == "READY"
        assert body["health"]["google_ads"] == "READY"
        assert body["health"]["ga4"] == "READY"
        assert body["health"]["business"] == "READY"
