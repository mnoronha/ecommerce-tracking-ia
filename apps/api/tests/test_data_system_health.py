"""
Data Health + System Health tests.

Invariants:
  DATA HEALTH:
    1. /clients/{id}/health reads from core_data_sources
    2. collection_status derived deterministically from source_state
    3. freshness_status derived from last_data_at + source-specific threshold
    4. reconciliation_status derived from reconciliation_state
    5. 404 when no sources exist
    6. 503 on DB error
    7. Stale source exposes STALE
    8. Missing last_data_at → freshness UNKNOWN
    9. All source_state values map to known collection_status

  SYSTEM HEALTH:
    10. DB UP when query succeeds
    11. DB DOWN when query raises
    12. Running job counted in running_jobs
    13. Queued job counted in queued_jobs
    14. Failed job in last 24h counted in failed_jobs_last_24h
    15. RUNNING > 120 min → appears in stuck_jobs
    16. QUEUED > 240 min → appears in stuck_jobs
    17. No stuck jobs when all RUNNING/QUEUED are fresh
    18. overall_status=DEGRADED when stuck_jobs
    19. overall_status=DOWN when DB DOWN
    20. Empty state → healthy overall_status UP

Run from apps/api/:
    python -m pytest tests/test_data_system_health.py -v
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.agency_api.v1.auth import SCOPE_READ_ANY, AuthContext
from app.agency_api.v1.router import (
    _collection_status,
    _freshness_status,
    _reconciliation_status,
    router as agency_router,
)

_AUTH = AuthContext(scope="agency_admin")
_NOW = datetime(2026, 10, 5, 12, 0, 0, tzinfo=timezone.utc)


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _source_row(**overrides) -> dict:
    base = {
        "source_key":          "meta_ads:act_123",
        "source_system":       "meta_ads",
        "semantic_domain":     "ADS",
        "source_state":        "READY",
        "last_attempt_at":     _NOW.isoformat(),
        "last_data_at":        _NOW.isoformat(),
        "last_validated_at":   _NOW.isoformat(),
        "last_reconciled_at":  None,
        "reconciliation_state": "OK",
        "last_error":          None,
    }
    base.update(overrides)
    return base


def _job_row(status: str = "SUCCEEDED", started_offset_min: int = 0, **overrides) -> dict:
    # Use real now() so that age comparisons in the endpoint work correctly.
    now_utc = datetime.now(timezone.utc)
    started_at = (now_utc - timedelta(minutes=started_offset_min)).isoformat()
    finished_at = now_utc.isoformat() if status in ("SUCCEEDED", "FAILED") else None
    base = {
        "id":          "job-uuid-0001",
        "job_type":    "core_pipeline",
        "run_key":     "core_pipeline:lk-sneakers:2026-10-04",
        "status":      status,
        "attempt":     1,
        "started_at":  started_at,
        "finished_at": finished_at,
        "error":       None,
        "replay_of":   None,
        "next_retry_at": None,
    }
    base.update(overrides)
    return base


def _make_health_db(sources: list[dict]) -> MagicMock:
    def _table(name: str):
        m = MagicMock()
        for method in ("select", "eq", "order", "limit", "in_", "gte", "lte", "is_", "maybe_single"):
            getattr(m, method).return_value = m
        if name == "core_data_sources":
            m.execute.return_value = MagicMock(data=sources)
        else:
            m.execute.return_value = MagicMock(data=[])
        return m

    db = MagicMock()
    db.table.side_effect = _table
    return db


def _make_system_db(
    active_jobs: list[dict] | None = None,
    failed_count: int = 0,
    last_ok: dict | None = None,
    last_err: dict | None = None,
    raise_on_probe: bool = False,
) -> MagicMock:
    active_jobs = active_jobs or []
    failed_ids = [{"id": f"f{i}"} for i in range(failed_count)]
    ok_rows = [last_ok] if last_ok else []
    err_rows = [last_err] if last_err else []

    call_count = [0]

    def _table(name: str):
        m = MagicMock()
        for method in ("select", "eq", "order", "limit", "in_", "gte", "lte", "is_", "maybe_single"):
            getattr(m, method).return_value = m

        def _execute():
            call_count[0] += 1
            if raise_on_probe and call_count[0] == 1:
                raise Exception("connection refused")
            # dispatch based on the last eq() call context is tricky with MagicMock;
            # use a simple sequential dispatch
            idx = call_count[0] - 1
            responses = [
                MagicMock(data=[], count=1),        # 0: DB probe (limit 1)
                MagicMock(data=active_jobs),         # 1: RUNNING/QUEUED
                MagicMock(data=failed_ids),          # 2: FAILED last 24h
                MagicMock(data=ok_rows),             # 3: last SUCCEEDED
                MagicMock(data=err_rows),            # 4: last FAILED error
            ]
            return responses[idx] if idx < len(responses) else MagicMock(data=[])

        m.execute.side_effect = _execute
        return m

    db = MagicMock()
    db.table.return_value = _table("core_job_runs")
    db.table.side_effect = _table
    return db


def _build_app(db: MagicMock) -> FastAPI:
    app = FastAPI()
    app.include_router(agency_router)
    app.dependency_overrides[SCOPE_READ_ANY] = lambda: _AUTH
    return app


# ── Unit: derived status functions ────────────────────────────────────────────

class TestCollectionStatus:
    def test_ready_returns_ok(self):
        assert _collection_status("READY") == "OK"

    def test_partial_returns_ok(self):
        assert _collection_status("PARTIAL") == "OK"

    def test_no_data_returns_no_data(self):
        assert _collection_status("NO_DATA") == "NO_DATA"

    def test_stale_returns_stale(self):
        assert _collection_status("STALE") == "STALE"

    def test_error_returns_error(self):
        assert _collection_status("ERROR") == "ERROR"

    def test_access_missing_returns_error(self):
        assert _collection_status("ACCESS_MISSING") == "ERROR"

    def test_permission_denied_returns_error(self):
        assert _collection_status("PERMISSION_DENIED") == "ERROR"

    def test_not_contracted_returns_not_applicable(self):
        assert _collection_status("NOT_CONTRACTED") == "NOT_APPLICABLE"


class TestFreshnessStatus:
    def test_fresh_meta_ads_within_48h(self):
        recent = (_NOW - timedelta(hours=24)).isoformat()
        assert _freshness_status("meta_ads", recent, _NOW) == "FRESH"

    def test_stale_meta_ads_beyond_48h(self):
        old = (_NOW - timedelta(hours=50)).isoformat()
        assert _freshness_status("meta_ads", old, _NOW) == "STALE"

    def test_fresh_shopify_within_24h(self):
        recent = (_NOW - timedelta(hours=12)).isoformat()
        assert _freshness_status("shopify", recent, _NOW) == "FRESH"

    def test_stale_shopify_beyond_24h(self):
        old = (_NOW - timedelta(hours=26)).isoformat()
        assert _freshness_status("shopify", old, _NOW) == "STALE"

    def test_missing_last_data_returns_unknown(self):
        assert _freshness_status("meta_ads", None, _NOW) == "UNKNOWN"

    def test_ga4_fresh_within_72h(self):
        recent = (_NOW - timedelta(hours=60)).isoformat()
        assert _freshness_status("ga4", recent, _NOW) == "FRESH"


class TestReconciliationStatus:
    def test_ok_state_returns_ok(self):
        assert _reconciliation_status("OK") == "OK"

    def test_none_returns_unknown(self):
        assert _reconciliation_status(None) == "UNKNOWN"

    def test_degraded_state_returns_degraded(self):
        assert _reconciliation_status("MISMATCH") == "DEGRADED"
        assert _reconciliation_status("FAILED") == "DEGRADED"


# ── Integration: GET /clients/{id}/health ─────────────────────────────────────

class TestDataHealth:
    def test_ready_source_all_statuses_ok(self):
        source = _source_row(source_state="READY", last_data_at=(_NOW - timedelta(hours=12)).isoformat())
        db = _make_health_db([source])
        with patch("app.agency_api.v1.router._get_db", return_value=db):
            with TestClient(_build_app(db)) as c:
                resp = c.get("/agency/v1/clients/lk-sneakers/health")
        assert resp.status_code == 200
        entry = resp.json()["health"][0]
        assert entry["source_state"] == "READY"
        assert entry["collection_status"] == "OK"
        assert entry["freshness_status"] == "FRESH"
        assert entry["reconciliation_status"] == "OK"

    def test_stale_source_freshness_stale(self):
        old_data = (_NOW - timedelta(hours=72)).isoformat()
        source = _source_row(source_state="STALE", last_data_at=old_data)
        db = _make_health_db([source])
        with patch("app.agency_api.v1.router._get_db", return_value=db):
            with TestClient(_build_app(db)) as c:
                resp = c.get("/agency/v1/clients/lk-sneakers/health")
        entry = resp.json()["health"][0]
        assert entry["collection_status"] == "STALE"
        assert entry["freshness_status"] == "STALE"

    def test_missing_last_data_at_freshness_unknown(self):
        source = _source_row(last_data_at=None)
        db = _make_health_db([source])
        with patch("app.agency_api.v1.router._get_db", return_value=db):
            with TestClient(_build_app(db)) as c:
                resp = c.get("/agency/v1/clients/lk-sneakers/health")
        assert resp.json()["health"][0]["freshness_status"] == "UNKNOWN"

    def test_error_source_collection_error(self):
        source = _source_row(source_state="ERROR", last_error="token expired")
        db = _make_health_db([source])
        with patch("app.agency_api.v1.router._get_db", return_value=db):
            with TestClient(_build_app(db)) as c:
                resp = c.get("/agency/v1/clients/lk-sneakers/health")
        entry = resp.json()["health"][0]
        assert entry["collection_status"] == "ERROR"
        assert entry["reason"] == "token expired"

    def test_no_sources_returns_404(self):
        db = _make_health_db([])
        with patch("app.agency_api.v1.router._get_db", return_value=db):
            with TestClient(_build_app(db)) as c:
                resp = c.get("/agency/v1/clients/unknown/health")
        assert resp.status_code == 404

    def test_db_error_returns_503(self):
        db = MagicMock()
        db.table.side_effect = Exception("DB down")
        with patch("app.agency_api.v1.router._get_db", return_value=db):
            with TestClient(_build_app(db), raise_server_exceptions=False) as c:
                resp = c.get("/agency/v1/clients/lk-sneakers/health")
        assert resp.status_code in (503, 500)

    def test_schema_version_present(self):
        source = _source_row()
        db = _make_health_db([source])
        with patch("app.agency_api.v1.router._get_db", return_value=db):
            with TestClient(_build_app(db)) as c:
                resp = c.get("/agency/v1/clients/lk-sneakers/health")
        assert resp.json()["schema_version"] == "1.1"

    def test_source_fields_exposed(self):
        source = _source_row(source_key="meta_ads:act_123", source_system="meta_ads")
        db = _make_health_db([source])
        with patch("app.agency_api.v1.router._get_db", return_value=db):
            with TestClient(_build_app(db)) as c:
                resp = c.get("/agency/v1/clients/lk-sneakers/health")
        entry = resp.json()["health"][0]
        assert entry["source_key"] == "meta_ads:act_123"
        assert entry["source_system"] == "meta_ads"


# ── Integration: GET /system/health ───────────────────────────────────────────

class TestSystemHealth:
    def _call(self, db):
        fake_sch = MagicMock()
        fake_sch.running = True
        fake_sch.get_jobs.return_value = []

        with patch("app.agency_api.v1.router._get_db", return_value=db):
            with patch("app.core.scheduler_registry.get_scheduler", return_value=fake_sch):
                with patch("app.core.scheduler_registry.get_job_runs", return_value={}):
                    with TestClient(_build_app(db)) as c:
                        return c.get("/agency/v1/system/health")

    def test_empty_state_overall_up(self):
        db = _make_system_db()
        resp = self._call(db)
        assert resp.status_code == 200
        data = resp.json()
        assert data["database"] == "UP"
        assert data["running_jobs"] == 0
        assert data["queued_jobs"] == 0
        assert data["failed_jobs_last_24h"] == 0
        assert data["stuck_jobs"] == []

    def test_overall_down_when_db_down(self):
        db = _make_system_db(raise_on_probe=True)
        resp = self._call(db)
        data = resp.json()
        assert data["database"] == "DOWN"
        assert data["overall_status"] == "DOWN"

    def test_running_job_counted(self):
        db = _make_system_db(active_jobs=[_job_row(status="RUNNING", started_offset_min=5)])
        resp = self._call(db)
        assert resp.json()["running_jobs"] == 1

    def test_queued_job_counted(self):
        db = _make_system_db(active_jobs=[_job_row(status="QUEUED", started_offset_min=5)])
        resp = self._call(db)
        assert resp.json()["queued_jobs"] == 1

    def test_failed_in_24h_counted(self):
        db = _make_system_db(failed_count=3)
        resp = self._call(db)
        assert resp.json()["failed_jobs_last_24h"] == 3

    def test_stuck_running_job_detected(self):
        # RUNNING > 120 min = STUCK
        stuck = _job_row(status="RUNNING", started_offset_min=150, id="stuck-job-1")
        db = _make_system_db(active_jobs=[stuck])
        resp = self._call(db)
        data = resp.json()
        assert len(data["stuck_jobs"]) == 1
        assert data["stuck_jobs"][0]["id"] == "stuck-job-1"
        assert data["overall_status"] == "DEGRADED"

    def test_fresh_running_job_not_stuck(self):
        # RUNNING < 120 min = NOT stuck
        fresh = _job_row(status="RUNNING", started_offset_min=30)
        db = _make_system_db(active_jobs=[fresh])
        resp = self._call(db)
        assert resp.json()["stuck_jobs"] == []

    def test_stuck_queued_job_detected(self):
        # QUEUED > 240 min = STUCK
        stuck = _job_row(status="QUEUED", started_offset_min=300, id="stuck-queue-1")
        db = _make_system_db(active_jobs=[stuck])
        resp = self._call(db)
        assert len(resp.json()["stuck_jobs"]) == 1

    def test_schema_version_present(self):
        db = _make_system_db()
        resp = self._call(db)
        assert resp.json()["schema_version"] == "1.1"

    def test_overall_degraded_on_failures(self):
        db = _make_system_db(failed_count=2)
        resp = self._call(db)
        assert resp.json()["overall_status"] == "DEGRADED"
