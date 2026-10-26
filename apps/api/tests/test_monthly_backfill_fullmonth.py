"""
Tests for monthly_backfill.py full-month idempotency fix.

Coverage:
  - _load_existing_snapshot: requires BOTH period_start AND period_end
  - 7-day snapshot ending on last-of-month NOT accepted as existing monthly snapshot
  - Full-month healthy snapshot IS accepted (idempotent, no pipeline re-run)
  - Full-month degraded snapshot NOT accepted (triggers re-materialization)
  - materialize_closed_month: month must be closed (MONTH_NOT_CLOSED)
  - Pipeline is called only when no healthy full-month snapshot exists
"""
from __future__ import annotations

import copy
from datetime import date, datetime, timezone
from typing import Optional
from unittest.mock import MagicMock, patch

import pytest
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.core.monthly_backfill import (
    _load_existing_snapshot,
    _is_degraded,
    materialize_closed_month,
    BACKFILL_FROM_PROVIDER_READ_ONLY,
)
from app.core.pipeline import PipelineResult


# -- Helpers -------------------------------------------------------------------

PERIOD_START = date(2026, 9, 1)
PERIOD_END   = date(2026, 9, 30)

def _healthy_full_month():
    return {
        "id": "snap-full-month",
        "client_id": "lk-sneakers",
        "period_start": PERIOD_START.isoformat(),
        "period_end":   PERIOD_END.isoformat(),
        "health": {"meta_ads": "HEALTHY", "google_ads": "HEALTHY"},
        "computed_at": "2026-10-14T14:05:24+00:00",
    }


def _degraded_full_month():
    s = _healthy_full_month()
    s["health"] = {"meta_ads": "ERROR", "google_ads": "HEALTHY"}
    return s


def _seven_day_snapshot():
    return {
        "id": "snap-7day",
        "client_id": "dipua",
        "period_start": "2026-09-24",
        "period_end":   PERIOD_END.isoformat(),
        "health": {"meta_ads": "HEALTHY", "google_ads": "HEALTHY"},
        "computed_at": "2026-10-01T07:15:00+00:00",
    }


class _FakeChain:
    """Fake Supabase query chain that filters by eq() calls."""
    def __init__(self, rows):
        self._rows = rows
        self._filters = {}

    def select(self, *_, **__): return self
    def eq(self, k, v):
        self._filters[k] = str(v)
        return self
    def execute(self):
        result = [
            r for r in self._rows
            if all(str(r.get(k)) == v for k, v in self._filters.items())
        ]
        return type("R", (), {"data": result})()


class FakeDB:
    def __init__(self, rows):
        self._rows = rows

    def table(self, name):
        if name == "core_metric_snapshots":
            return _FakeChain(self._rows)
        raise ValueError(name)


# -- _load_existing_snapshot precision -----------------------------------------

def test_full_month_snapshot_is_found(monkeypatch):
    db = FakeDB([_healthy_full_month()])
    monkeypatch.setattr("app.database.get_supabase", lambda: db)

    snap = _load_existing_snapshot("lk-sneakers", PERIOD_START, PERIOD_END)
    assert snap is not None
    assert snap["id"] == "snap-full-month"


def test_seven_day_snapshot_not_found(monkeypatch):
    """A 7-day snapshot ending on period_end must NOT match the full-month query."""
    db = FakeDB([_seven_day_snapshot()])
    monkeypatch.setattr("app.database.get_supabase", lambda: db)

    snap = _load_existing_snapshot("dipua", PERIOD_START, PERIOD_END)
    assert snap is None   # 7-day has period_start=2026-09-24, not 2026-09-01


def test_no_snapshot_returns_none(monkeypatch):
    db = FakeDB([])
    monkeypatch.setattr("app.database.get_supabase", lambda: db)

    snap = _load_existing_snapshot("unknown-client", PERIOD_START, PERIOD_END)
    assert snap is None


def test_db_error_returns_none(monkeypatch):
    class BrokenDB:
        def table(self, _): raise RuntimeError("DB down")

    monkeypatch.setattr("app.database.get_supabase", lambda: BrokenDB())

    snap = _load_existing_snapshot("lk-sneakers", PERIOD_START, PERIOD_END)
    assert snap is None   # exception is swallowed, returns None


# -- _is_degraded --------------------------------------------------------------

def test_is_degraded_healthy():
    assert _is_degraded({"health": {"meta_ads": "HEALTHY", "google_ads": "HEALTHY"}}) is False


def test_is_degraded_error():
    assert _is_degraded({"health": {"meta_ads": "ERROR"}}) is True


def test_is_degraded_empty_health():
    assert _is_degraded({"health": {}}) is False


# -- materialize_closed_month --------------------------------------------------

def test_month_not_closed_raises(monkeypatch):
    """Future or current month must raise MONTH_NOT_CLOSED."""
    today = date(2026, 9, 15)  # inside September
    with pytest.raises(ValueError, match="MONTH_NOT_CLOSED"):
        materialize_closed_month("lk-sneakers", 2026, 9, timezone="America/Sao_Paulo", today=today)


def test_idempotent_when_healthy_full_month_exists(monkeypatch):
    """When a healthy full-month snapshot exists, pipeline is NOT called."""
    monkeypatch.setattr(
        "app.core.monthly_backfill._load_existing_snapshot",
        lambda *a, **kw: _healthy_full_month(),
    )
    pipeline_called = {"n": 0}
    def fake_run_pipeline(*args, **kwargs):
        pipeline_called["n"] += 1
        raise AssertionError("pipeline must not be called when idempotent")

    monkeypatch.setattr("app.core.monthly_backfill.run_pipeline", fake_run_pipeline)

    result = materialize_closed_month(
        "lk-sneakers", 2026, 9, timezone="America/Sao_Paulo",
        today=date(2026, 10, 1),
    )
    assert pipeline_called["n"] == 0
    assert result.snapshot_id == "snap-full-month"


def test_seven_day_snapshot_triggers_pipeline(monkeypatch):
    """A 7-day snapshot ending on period_end must NOT prevent re-materialization."""
    db = FakeDB([_seven_day_snapshot()])
    monkeypatch.setattr("app.database.get_supabase", lambda: db)

    pipeline_called = {"n": 0}
    fake_result = PipelineResult(
        client_id="dipua",
        period_start=PERIOD_START,
        period_end=PERIOD_END,
        run_id="run-001",
        snapshot_id="snap-new-full-month",
        health={"meta_ads": "HEALTHY"},
    )
    def fake_run_pipeline(*args, **kwargs):
        pipeline_called["n"] += 1
        return fake_result

    monkeypatch.setattr("app.core.monthly_backfill.run_pipeline", fake_run_pipeline)

    result = materialize_closed_month(
        "dipua", 2026, 9, timezone="America/Sao_Paulo",
        today=date(2026, 10, 1),
    )
    assert pipeline_called["n"] == 1
    assert result.snapshot_id == "snap-new-full-month"


def test_degraded_full_month_triggers_pipeline(monkeypatch):
    """A degraded full-month snapshot triggers re-run to get a fresh one."""
    monkeypatch.setattr(
        "app.core.monthly_backfill._load_existing_snapshot",
        lambda *a, **kw: _degraded_full_month(),
    )
    pipeline_called = {"n": 0}
    fake_result = PipelineResult(
        client_id="lk-sneakers",
        period_start=PERIOD_START,
        period_end=PERIOD_END,
        run_id="run-002",
        snapshot_id="snap-refreshed",
        health={"meta_ads": "HEALTHY"},
    )
    def fake_run_pipeline(*args, **kwargs):
        pipeline_called["n"] += 1
        return fake_result

    monkeypatch.setattr("app.core.monthly_backfill.run_pipeline", fake_run_pipeline)

    result = materialize_closed_month(
        "lk-sneakers", 2026, 9, timezone="America/Sao_Paulo",
        today=date(2026, 10, 1),
    )
    assert pipeline_called["n"] == 1
    assert result.snapshot_id == "snap-refreshed"


def test_backfill_policy_constant():
    assert BACKFILL_FROM_PROVIDER_READ_ONLY == "BACKFILL_FROM_PROVIDER_READ_ONLY"
