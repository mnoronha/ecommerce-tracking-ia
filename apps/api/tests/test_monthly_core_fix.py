"""
MONTHLY CORE FIX — runtime validation.

Scenarios:
  A) LK Sept    — no existing snapshot → pipeline runs → snapshot written
  B) Tárcio     — leadgen client, same pipeline path
  C) No data    — pipeline runs when no snapshot exists (NO_DATA is not degraded)
  D) Invalid    — degraded existing snapshot triggers re-run; good one skips
  E) Idempotency — second call returns same snapshot_id without re-running

Policy:
  BACKFILL_FROM_PROVIDER_READ_ONLY must be importable and correct.
  persist_campaign_rows=False must always be passed to run_pipeline.
"""
from __future__ import annotations

from datetime import date
from unittest.mock import call, patch

import pytest

from app.core.monthly_backfill import (
    BACKFILL_FROM_PROVIDER_READ_ONLY,
    materialize_closed_month,
)
from app.core.pipeline import PipelineResult

_SEPT_START = date(2026, 9, 1)
_SEPT_END   = date(2026, 9, 30)
_TODAY      = date(2026, 10, 19)
_TZ         = "America/Sao_Paulo"


def _pipeline_ok(client_id: str, snap_id: str) -> PipelineResult:
    return PipelineResult(
        client_id=client_id,
        period_start=_SEPT_START,
        period_end=_SEPT_END,
        run_id=f"core:{client_id}:2026-09-01:abcd1234",
        snapshot_id=snap_id,
    )


def _good_snap(snap_id: str) -> dict:
    return {
        "id": snap_id,
        "health": {"meta_ads": "READY", "google_ads": "READY", "business": "READY"},
        "computed_at": "2026-09-30T22:00:00+00:00",
    }


def _degraded_snap(snap_id: str) -> dict:
    return {
        "id": snap_id,
        "health": {"meta_ads": "ERROR", "google_ads": "READY", "business": "READY"},
        "computed_at": "2026-09-15T00:00:00+00:00",
    }


# ── Scenario A — LK Sept ──────────────────────────────────────────────────────

class TestScenarioA_LKSept:
    def test_pipeline_runs_when_no_existing_snapshot(self):
        expected = _pipeline_ok("lk-sneakers", "snap-lk-sept")
        with patch("app.core.monthly_backfill._load_existing_snapshot", return_value=None), \
             patch("app.core.monthly_backfill.run_pipeline", return_value=expected) as mock_pl:
            result = materialize_closed_month("lk-sneakers", 2026, 9, timezone=_TZ, today=_TODAY)
        assert result.snapshot_id == "snap-lk-sept"
        mock_pl.assert_called_once_with(
            "lk-sneakers", _SEPT_START, _SEPT_END, view="live", persist_campaign_rows=False,
        )

    def test_returns_pipeline_result_directly(self):
        expected = _pipeline_ok("lk-sneakers", "snap-lk-sept")
        with patch("app.core.monthly_backfill._load_existing_snapshot", return_value=None), \
             patch("app.core.monthly_backfill.run_pipeline", return_value=expected):
            result = materialize_closed_month("lk-sneakers", 2026, 9, timezone=_TZ, today=_TODAY)
        assert result is expected


# ── Scenario B — Tárcio Sept ──────────────────────────────────────────────────

class TestScenarioB_TarcioSept:
    def test_leadgen_client_materializes(self):
        expected = _pipeline_ok("tarcio", "snap-tarcio-sept")
        with patch("app.core.monthly_backfill._load_existing_snapshot", return_value=None), \
             patch("app.core.monthly_backfill.run_pipeline", return_value=expected) as mock_pl:
            result = materialize_closed_month("tarcio", 2026, 9, timezone=_TZ, today=_TODAY)
        assert result.snapshot_id == "snap-tarcio-sept"
        mock_pl.assert_called_once_with(
            "tarcio", _SEPT_START, _SEPT_END, view="live", persist_campaign_rows=False,
        )


# ── Scenario C — No data ──────────────────────────────────────────────────────

class TestScenarioC_NoData:
    def test_pipeline_runs_even_when_no_existing_data(self):
        no_data_result = _pipeline_ok("empty-client", "snap-no-data")
        with patch("app.core.monthly_backfill._load_existing_snapshot", return_value=None), \
             patch("app.core.monthly_backfill.run_pipeline", return_value=no_data_result) as mock_pl:
            result = materialize_closed_month("empty-client", 2026, 9, timezone=_TZ, today=_TODAY)
        assert result.snapshot_id == "snap-no-data"
        mock_pl.assert_called_once()

    def test_no_data_state_in_existing_snap_is_not_degraded(self):
        # NO_DATA is valid (client had zero orders that month); should trigger idempotent skip
        no_data_snap = {
            "id": "snap-no-data-existing",
            "health": {"meta_ads": "NO_DATA", "google_ads": "NO_DATA", "business": "NO_DATA"},
            "computed_at": "2026-09-30T22:00:00+00:00",
        }
        with patch("app.core.monthly_backfill._load_existing_snapshot", return_value=no_data_snap), \
             patch("app.core.monthly_backfill.run_pipeline") as mock_pl:
            result = materialize_closed_month("empty-client", 2026, 9, timezone=_TZ, today=_TODAY)
        mock_pl.assert_not_called()
        assert result.snapshot_id == "snap-no-data-existing"


# ── Scenario D — Degraded existing snapshot ───────────────────────────────────

class TestScenarioD_InvalidSourceState:
    def test_degraded_existing_triggers_rerun(self):
        new_result = _pipeline_ok("lk-sneakers", "snap-lk-new")
        with patch("app.core.monthly_backfill._load_existing_snapshot",
                   return_value=_degraded_snap("old-snap")), \
             patch("app.core.monthly_backfill.run_pipeline", return_value=new_result) as mock_pl:
            result = materialize_closed_month("lk-sneakers", 2026, 9, timezone=_TZ, today=_TODAY)
        assert result.snapshot_id == "snap-lk-new"
        mock_pl.assert_called_once()

    def test_good_existing_skips_pipeline(self):
        with patch("app.core.monthly_backfill._load_existing_snapshot",
                   return_value=_good_snap("canonical-snap")), \
             patch("app.core.monthly_backfill.run_pipeline") as mock_pl:
            result = materialize_closed_month("lk-sneakers", 2026, 9, timezone=_TZ, today=_TODAY)
        mock_pl.assert_not_called()
        assert result.snapshot_id == "canonical-snap"

    @pytest.mark.parametrize("state", [
        "ERROR", "ACCESS_MISSING", "PERMISSION_DENIED", "MISSING", "STALE",
    ])
    def test_each_degraded_state_triggers_rerun(self, state):
        snap = {
            "id": "snap-degraded",
            "health": {"meta_ads": state, "google_ads": "READY", "business": "READY"},
            "computed_at": "2026-09-15T00:00:00+00:00",
        }
        new_result = _pipeline_ok("lk-sneakers", "snap-new")
        with patch("app.core.monthly_backfill._load_existing_snapshot", return_value=snap), \
             patch("app.core.monthly_backfill.run_pipeline", return_value=new_result) as mock_pl:
            materialize_closed_month("lk-sneakers", 2026, 9, timezone=_TZ, today=_TODAY)
        mock_pl.assert_called_once()


# ── Scenario E — Idempotency ──────────────────────────────────────────────────

class TestScenarioE_Idempotency:
    def test_second_call_returns_same_snapshot_without_rerun(self):
        with patch("app.core.monthly_backfill._load_existing_snapshot",
                   return_value=_good_snap("canonical-snap")), \
             patch("app.core.monthly_backfill.run_pipeline") as mock_pl:
            result1 = materialize_closed_month("lk-sneakers", 2026, 9, timezone=_TZ, today=_TODAY)
            result2 = materialize_closed_month("lk-sneakers", 2026, 9, timezone=_TZ, today=_TODAY)
        mock_pl.assert_not_called()
        assert result1.snapshot_id == result2.snapshot_id == "canonical-snap"

    def test_idempotent_result_has_correct_period(self):
        with patch("app.core.monthly_backfill._load_existing_snapshot",
                   return_value=_good_snap("canonical-snap")), \
             patch("app.core.monthly_backfill.run_pipeline"):
            result = materialize_closed_month("lk-sneakers", 2026, 9, timezone=_TZ, today=_TODAY)
        assert result.period_start == _SEPT_START
        assert result.period_end   == _SEPT_END
        assert result.client_id    == "lk-sneakers"

    def test_open_month_always_raises(self):
        with pytest.raises(ValueError, match="MONTH_NOT_CLOSED"):
            materialize_closed_month("lk-sneakers", 2026, 10, timezone=_TZ, today=date(2026, 10, 15))

    def test_month_end_equals_today_raises(self):
        # end = Sept 30; today = Sept 30 → not closed yet
        with pytest.raises(ValueError, match="MONTH_NOT_CLOSED"):
            materialize_closed_month("lk-sneakers", 2026, 9, timezone=_TZ, today=date(2026, 9, 30))


# ── Policy invariants ─────────────────────────────────────────────────────────

class TestPolicyInvariants:
    def test_policy_constant_value(self):
        assert BACKFILL_FROM_PROVIDER_READ_ONLY == "BACKFILL_FROM_PROVIDER_READ_ONLY"

    def test_persist_campaign_rows_always_false(self):
        expected = _pipeline_ok("lk-sneakers", "snap-x")
        with patch("app.core.monthly_backfill._load_existing_snapshot", return_value=None), \
             patch("app.core.monthly_backfill.run_pipeline", return_value=expected) as mock_pl:
            materialize_closed_month("lk-sneakers", 2026, 9, timezone=_TZ, today=_TODAY)
        _, kwargs = mock_pl.call_args
        assert kwargs.get("persist_campaign_rows") is False

    def test_view_is_live(self):
        expected = _pipeline_ok("lk-sneakers", "snap-x")
        with patch("app.core.monthly_backfill._load_existing_snapshot", return_value=None), \
             patch("app.core.monthly_backfill.run_pipeline", return_value=expected) as mock_pl:
            materialize_closed_month("lk-sneakers", 2026, 9, timezone=_TZ, today=_TODAY)
        _, kwargs = mock_pl.call_args
        assert kwargs.get("view") == "live"
