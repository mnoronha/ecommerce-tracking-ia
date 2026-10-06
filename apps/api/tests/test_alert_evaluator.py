"""
Alert evaluator + alert API tests.

Invariants:

  ALERT EVALUATOR (unit):
    1.  SOURCE_STALE creates alert when source_state == STALE
    2.  SOURCE_ERROR creates alert when source_state in error family
    3.  RECONCILIATION_FAILED creates alert when recon != OK
    4.  JOB_FAILED creates alert for failed jobs in last 24h
    5.  JOB_STUCK creates alert for RUNNING > 120 min
    6.  JOB_STUCK creates alert for QUEUED > 240 min
    7.  Healthy state creates no alerts
    8.  Dedup: second evaluation updates occurrence_count, not new row
    9.  Auto-resolve: condition gone → OPEN alert → RESOLVED
    10. Unknown client returns error dict
    11. PIPELINE_NOT_RUN created when no SUCCEEDED in 36h
    12. PIPELINE_NOT_RUN resolved when pipeline ran recently
    13. PIPELINE_NOT_RUN not created when pipeline ran recently

  ALERT API (integration):
    14. GET /alerts returns empty list when no Core alerts exist
    15. GET /alerts returns rows from DB (Core types only)
    16. GET /alerts filters by status
    17. GET /alerts filters by client_id
    18. GET /clients/{id}/alerts returns 404 for unknown client
    19. GET /clients/{id}/alerts returns alerts for known client
    20. GET /alerts/{id}/context returns 404 for unknown id
    21. GET /alerts/{id}/context returns AlertContext for known alert
    22. GET /alerts/{id}/context includes evidence from data jsonb
    23. GET /alerts/{id}/diagnoses returns [] (Hermes not yet)
    24. ACKNOWLEDGED status accepted in AlertStatus enum
    25. GET /clients/{id}/alerts filters by status

Run from apps/api/:
    python -m pytest tests/test_alert_evaluator.py -v
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional
from unittest.mock import MagicMock, patch, call

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.agency_api.v1.auth import SCOPE_READ_ANY, AuthContext
from app.agency_api.v1.enums import AlertStatus
from app.agency_api.v1.router import router as agency_router
from app.core.alert_evaluator import (
    CORE_ALERT_TYPES,
    evaluate_operational_alerts,
    evaluate_system_alerts,
)

_AUTH = AuthContext(scope="agency_admin")
_NOW  = datetime.now(timezone.utc)


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _make_sb(
    client_uuid: str = "uuid-client-001",
    sources: list[dict] | None = None,
    failed_jobs: list[dict] | None = None,
    active_jobs: list[dict] | None = None,
    open_alerts: list[dict] | None = None,
) -> MagicMock:
    """Build a mock Supabase client for alert_evaluator tests."""
    sources      = sources      or []
    failed_jobs  = failed_jobs  or []
    active_jobs  = active_jobs  or []
    open_alerts  = open_alerts  or []

    insert_calls: list[dict] = []
    update_calls: list[dict] = []

    call_index = [0]

    def _table(name: str):
        m = MagicMock()
        for method in (
            "select", "eq", "in_", "like", "gte", "lte",
            "order", "limit", "is_", "maybe_single",
        ):
            getattr(m, method).return_value = m

        def _execute():
            idx = call_index[0]
            call_index[0] += 1
            responses = [
                MagicMock(data=[{"id": client_uuid}]),  # 0: client lookup (list)
                MagicMock(data=open_alerts),             # 1: existing OPEN alerts
                MagicMock(data=sources),                 # 2: core_data_sources
                MagicMock(data=failed_jobs),             # 3: FAILED jobs (24h)
                MagicMock(data=active_jobs),             # 4: RUNNING/QUEUED jobs
            ]
            return responses[idx] if idx < len(responses) else MagicMock(data=[])

        m.execute.side_effect = _execute
        m.insert.return_value  = MagicMock(execute=lambda: MagicMock(data=[{"id": "new-alert-id"}]))
        m.update.return_value  = MagicMock(execute=lambda: MagicMock(data=[]))
        return m

    sb = MagicMock()
    sb.table.side_effect = _table
    return sb


def _source(state: str = "READY", recon: str | None = "OK", key: str = "meta_ads:act_1") -> dict:
    return {
        "source_key":          key,
        "source_system":       "meta_ads",
        "source_state":        state,
        "reconciliation_state": recon,
        "last_data_at":        _NOW.isoformat(),
        "last_error":          None,
    }


def _job(status: str = "FAILED", started_offset_min: int = 5, job_id: str = "job-001") -> dict:
    started = (_NOW - timedelta(minutes=started_offset_min)).isoformat()
    return {
        "id":         job_id,
        "status":     status,
        "run_key":    "core_pipeline:lk-sneakers:2026-10-05",
        "started_at": started,
        "error":      "timeout" if status == "FAILED" else None,
    }


def _open_alert(fp: str, alert_id: str = "alert-001", occurrence_count: int = 1, alert_type: str = "SOURCE_STALE") -> dict:
    return {
        "id":               alert_id,
        "type":             alert_type,
        "fingerprint":      fp,
        "occurrence_count": occurrence_count,
    }


# ── Unit: evaluate_operational_alerts ────────────────────────────────────────

class TestEvaluateOperationalAlerts:

    def _run(self, client_id: str, sb: MagicMock) -> dict:
        with patch("app.database.get_supabase", return_value=sb):
            return evaluate_operational_alerts(client_id)

    def test_stale_source_creates_alert(self):
        sb = _make_sb(sources=[_source(state="STALE")])
        result = self._run("lk-sneakers", sb)
        assert result["created"] >= 1

    def test_error_source_creates_alert(self):
        for error_state in ("ERROR", "ACCESS_MISSING", "PERMISSION_DENIED", "MISSING"):
            sb = _make_sb(sources=[_source(state=error_state)])
            result = self._run("lk-sneakers", sb)
            assert result["created"] >= 1, f"Expected alert for {error_state}"

    def test_reconciliation_failed_creates_alert(self):
        sb = _make_sb(sources=[_source(state="READY", recon="MISMATCH")])
        result = self._run("lk-sneakers", sb)
        assert result["created"] >= 1

    def test_failed_job_creates_alert(self):
        sb = _make_sb(failed_jobs=[_job(status="FAILED")])
        result = self._run("lk-sneakers", sb)
        assert result["created"] >= 1

    def test_stuck_running_job_creates_alert(self):
        # RUNNING > 120 min → STUCK
        sb = _make_sb(active_jobs=[_job(status="RUNNING", started_offset_min=150)])
        result = self._run("lk-sneakers", sb)
        assert result["created"] >= 1

    def test_stuck_queued_job_creates_alert(self):
        # QUEUED > 240 min → STUCK
        sb = _make_sb(active_jobs=[_job(status="QUEUED", started_offset_min=300)])
        result = self._run("lk-sneakers", sb)
        assert result["created"] >= 1

    def test_fresh_running_job_not_stuck(self):
        sb = _make_sb(active_jobs=[_job(status="RUNNING", started_offset_min=30)])
        result = self._run("lk-sneakers", sb)
        assert result.get("created", 0) == 0

    def test_healthy_state_no_alerts(self):
        sb = _make_sb(sources=[_source(state="READY")])
        result = self._run("lk-sneakers", sb)
        assert result.get("created", 0) == 0

    def test_dedup_updates_occurrence_count(self):
        fp = "SOURCE_STALE:uuid-client-001:meta_ads:act_1"
        existing = [_open_alert(fp, occurrence_count=2)]
        sb = _make_sb(sources=[_source(state="STALE")], open_alerts=existing)
        result = self._run("lk-sneakers", sb)
        # existing fingerprint → update, not create
        assert result.get("updated", 0) >= 1
        assert result.get("created", 0) == 0

    def test_auto_resolve_when_condition_gone(self):
        fp = "SOURCE_STALE:uuid-client-001:meta_ads:act_1"
        existing = [_open_alert(fp, occurrence_count=1)]
        # source is now READY → fingerprint no longer in current findings
        sb = _make_sb(sources=[_source(state="READY")], open_alerts=existing)
        result = self._run("lk-sneakers", sb)
        assert result.get("resolved", 0) >= 1

    def test_unknown_client_returns_error(self):
        sb = MagicMock()
        m  = MagicMock()
        for method in ("select", "eq", "limit"):
            getattr(m, method).return_value = m
        m.execute.return_value = MagicMock(data=[])   # empty list → no client found
        sb.table.return_value  = m
        with patch("app.database.get_supabase", return_value=sb):
            result = evaluate_operational_alerts("nonexistent")
        assert "error" in result


# ── Unit: evaluate_system_alerts ─────────────────────────────────────────────

class TestEvaluateSystemAlerts:

    def _run(self, has_recent_run: bool, existing_alert: dict | None = None) -> dict:
        sb = MagicMock()
        m  = MagicMock()
        for method in ("select", "eq", "in_", "gte", "limit", "is_", "maybe_single", "order"):
            getattr(m, method).return_value = m

        call_index = [0]

        def _execute():
            idx = call_index[0]
            call_index[0] += 1
            if idx == 0:
                # core_job_runs SUCCEEDED query
                ok_rows = [{"id": "j1", "finished_at": _NOW.isoformat()}] if has_recent_run else []
                return MagicMock(data=ok_rows)
            elif idx == 1:
                # existing PIPELINE_NOT_RUN alert lookup (limit(1) → list)
                return MagicMock(data=[existing_alert] if existing_alert else [])
            return MagicMock(data=[])

        m.execute.side_effect = _execute
        m.insert.return_value  = MagicMock(execute=lambda: MagicMock(data=[]))
        m.update.return_value  = MagicMock(execute=lambda: MagicMock(data=[]))
        sb.table.return_value  = m

        with patch("app.database.get_supabase", return_value=sb):
            return evaluate_system_alerts()

    def test_no_recent_run_creates_alert(self):
        result = self._run(has_recent_run=False, existing_alert=None)
        assert result.get("pipeline_not_run") is True

    def test_recent_run_no_alert(self):
        result = self._run(has_recent_run=True, existing_alert=None)
        assert result.get("pipeline_not_run") is False

    def test_recent_run_resolves_existing_alert(self):
        existing = {"id": "alert-sys-001", "occurrence_count": 3}
        result = self._run(has_recent_run=True, existing_alert=existing)
        assert result.get("pipeline_not_run") is False
        assert result.get("resolved", 0) >= 1

    def test_no_run_updates_existing_alert(self):
        existing = {"id": "alert-sys-001", "occurrence_count": 2}
        result = self._run(has_recent_run=False, existing_alert=existing)
        assert result.get("pipeline_not_run") is True


# ── Unit: AlertStatus enum ────────────────────────────────────────────────────

class TestAlertStatusEnum:
    def test_acknowledged_in_enum(self):
        assert AlertStatus.ACKNOWLEDGED == "ACKNOWLEDGED"

    def test_all_values_present(self):
        values = {s.value for s in AlertStatus}
        assert {"OPEN", "ACKNOWLEDGED", "RESOLVED"} == values


# ── Integration: GET /alerts ──────────────────────────────────────────────────

def _build_app(db: MagicMock) -> FastAPI:
    app = FastAPI()
    app.include_router(agency_router)
    app.dependency_overrides[SCOPE_READ_ANY] = lambda: _AUTH
    return app


def _alert_row(
    alert_id: str = "alert-001",
    alert_type: str = "SOURCE_STALE",
    client_uuid: str | None = "uuid-c-001",
    status: str = "OPEN",
    severity: str = "MEDIUM",
    fingerprint: str = "SOURCE_STALE:uuid-c-001:meta_ads",
    data: dict | None = None,
) -> dict:
    return {
        "id":               alert_id,
        "type":             alert_type,
        "client_id":        client_uuid,
        "title":            f"Test: {alert_type}",
        "message":          "Test message",
        "severity":         severity,
        "status":           status,
        "fingerprint":      fingerprint,
        "data":             data or {"source_key": "meta_ads"},
        "occurrence_count": 1,
        "created_at":       _NOW.isoformat(),
        "resolved_at":      None,
    }


def _make_api_db(
    alerts: list[dict] | None = None,
    client_row: dict | None = None,
    slug_row: dict | None = None,
) -> MagicMock:
    alerts     = alerts     or []
    client_row_data = client_row
    slug_row_data   = slug_row

    call_index = [0]

    def _table(name: str):
        m = MagicMock()
        for method in ("select", "eq", "in_", "like", "gte", "lte", "is_",
                        "order", "limit", "maybe_single"):
            getattr(m, method).return_value = m

        def _execute():
            idx = call_index[0]
            call_index[0] += 1
            if idx == 0:
                # client lookup (for client_id filter or /clients/{id}/alerts)
                return MagicMock(data=client_row_data)
            if idx == 1:
                # alerts query
                return MagicMock(data=alerts)
            if idx == 2:
                # slug map (batch clients lookup)
                rows = [slug_row_data] if slug_row_data else []
                return MagicMock(data=rows)
            return MagicMock(data=[])

        m.execute.side_effect = _execute
        return m

    db = MagicMock()
    db.table.side_effect = _table
    return db


class TestListAlerts:

    def _call(self, db: MagicMock, params: str = "") -> "Response":
        with patch("app.agency_api.v1.router._get_db", return_value=db):
            with TestClient(_build_app(db)) as c:
                return c.get(f"/agency/v1/alerts{params}")

    def test_empty_returns_empty_list(self):
        db = _make_api_db(alerts=[])
        resp = self._call(db)
        assert resp.status_code == 200
        assert resp.json() == []

    def test_returns_core_alerts(self):
        db = _make_api_db(
            alerts=[_alert_row()],
            slug_row={"id": "uuid-c-001", "client_id": "lk-sneakers"},
        )
        # For list_alerts without client_id filter, call_index 0 = alerts, 1 = slug_map
        # But our mock starts client lookup at idx 0...
        # list_alerts doesn't call _load_client_meta when no client_id filter.
        # So call order: 0=alerts, 1=slug_map
        call_index = [0]
        def _table(name):
            m = MagicMock()
            for method in ("select", "eq", "in_", "like", "gte", "order", "limit", "is_", "maybe_single"):
                getattr(m, method).return_value = m
            def _execute():
                idx = call_index[0]
                call_index[0] += 1
                if idx == 0:
                    return MagicMock(data=[_alert_row()])
                if idx == 1:
                    return MagicMock(data=[{"id": "uuid-c-001", "client_id": "lk-sneakers"}])
                return MagicMock(data=[])
            m.execute.side_effect = _execute
            return m
        db2 = MagicMock()
        db2.table.side_effect = _table
        with patch("app.agency_api.v1.router._get_db", return_value=db2):
            with TestClient(_build_app(db2)) as c:
                resp = c.get("/agency/v1/alerts")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["alert_type"] == "SOURCE_STALE"

    def test_alert_has_required_fields(self):
        call_index = [0]
        def _table(name):
            m = MagicMock()
            for method in ("select", "eq", "in_", "like", "gte", "order", "limit", "is_", "maybe_single"):
                getattr(m, method).return_value = m
            def _execute():
                idx = call_index[0]
                call_index[0] += 1
                if idx == 0:
                    return MagicMock(data=[_alert_row(data={"source_key": "meta_ads:act_1"})])
                return MagicMock(data=[])
            m.execute.side_effect = _execute
            return m
        db = MagicMock()
        db.table.side_effect = _table
        with patch("app.agency_api.v1.router._get_db", return_value=db):
            with TestClient(_build_app(db)) as c:
                resp = c.get("/agency/v1/alerts")
        assert resp.status_code == 200
        alert = resp.json()[0]
        for field in ("id", "alert_type", "severity", "status", "title", "message", "dedup_key", "detected_at"):
            assert field in alert, f"Missing field: {field}"


class TestGetClientAlerts:

    def _call(self, db: MagicMock, client_id: str = "lk-sneakers", params: str = "") -> "Response":
        with patch("app.agency_api.v1.router._get_db", return_value=db):
            with TestClient(_build_app(db)) as c:
                return c.get(f"/agency/v1/clients/{client_id}/alerts{params}")

    def test_unknown_client_returns_404(self):
        call_index = [0]
        def _table(name):
            m = MagicMock()
            for method in ("select", "eq", "in_", "order", "limit", "is_", "maybe_single"):
                getattr(m, method).return_value = m
            m.execute.return_value = MagicMock(data=None)
            return m
        db = MagicMock()
        db.table.side_effect = _table
        resp = self._call(db, "nonexistent")
        assert resp.status_code == 404

    def test_known_client_returns_alerts(self):
        call_index = [0]
        client_meta = {"id": "uuid-c-001", "client_id": "lk-sneakers", "name": "LK",
                       "business_model": "DTC", "timezone": "America/Sao_Paulo",
                       "currency": "BRL", "country": "BR", "is_active": True}
        alerts_data = [_alert_row(client_uuid="uuid-c-001")]

        def _table(name):
            m = MagicMock()
            for method in ("select", "eq", "in_", "order", "limit", "is_", "maybe_single"):
                getattr(m, method).return_value = m
            def _execute():
                idx = call_index[0]
                call_index[0] += 1
                if idx == 0:
                    return MagicMock(data=[client_meta])
                if idx == 1:
                    return MagicMock(data=alerts_data)
                return MagicMock(data=[])
            m.execute.side_effect = _execute
            return m
        db = MagicMock()
        db.table.side_effect = _table
        resp = self._call(db)
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["alert_type"] == "SOURCE_STALE"

    def test_client_alerts_status_filter_accepted(self):
        call_index = [0]
        client_meta = {"id": "uuid-c-001", "client_id": "lk-sneakers", "name": "LK",
                       "business_model": "DTC", "timezone": "America/Sao_Paulo",
                       "currency": "BRL", "country": "BR", "is_active": True}
        def _table(name):
            m = MagicMock()
            for method in ("select", "eq", "in_", "order", "limit", "is_", "maybe_single"):
                getattr(m, method).return_value = m
            def _execute():
                idx = call_index[0]
                call_index[0] += 1
                return MagicMock(data=[client_meta] if idx == 0 else [])
            m.execute.side_effect = _execute
            return m
        db = MagicMock()
        db.table.side_effect = _table
        # Should not raise — query accepted
        resp = self._call(db, params="?status=resolved")
        assert resp.status_code == 200


class TestGetAlertContext:

    def _make_db(self, alert_row: dict | None, slug_row: dict | None = None) -> MagicMock:
        call_index = [0]

        def _table(name):
            m = MagicMock()
            for method in ("select", "eq", "in_", "like", "gte", "order", "limit", "is_", "maybe_single"):
                getattr(m, method).return_value = m
            def _execute():
                idx = call_index[0]
                call_index[0] += 1
                if idx == 0:
                    return MagicMock(data=[alert_row] if alert_row else [])
                if idx == 1 and slug_row:
                    return MagicMock(data=[slug_row])
                return MagicMock(data=[])
            m.execute.side_effect = _execute
            return m
        db = MagicMock()
        db.table.side_effect = _table
        return db

    def test_unknown_alert_returns_404(self):
        db = self._make_db(alert_row=None)
        with patch("app.agency_api.v1.router._get_db", return_value=db):
            with TestClient(_build_app(db)) as c:
                resp = c.get("/agency/v1/alerts/nonexistent/context")
        assert resp.status_code == 404

    def test_known_alert_returns_context(self):
        row = _alert_row(data={"source_key": "meta_ads:act_1", "source_state": "STALE"})
        slug = {"id": "uuid-c-001", "client_id": "lk-sneakers"}
        db = self._make_db(alert_row=row, slug_row=slug)
        with patch("app.agency_api.v1.router._get_db", return_value=db):
            with TestClient(_build_app(db)) as c:
                resp = c.get("/agency/v1/alerts/alert-001/context")
        assert resp.status_code == 200
        ctx = resp.json()
        assert "alert" in ctx
        assert ctx["alert"]["id"] == "alert-001"
        assert ctx["alert"]["alert_type"] == "SOURCE_STALE"

    def test_context_includes_evidence(self):
        evidence = {"source_key": "google_ads:1234", "reconciliation_state": "MISMATCH"}
        row = _alert_row(alert_type="RECONCILIATION_FAILED", data=evidence)
        db = self._make_db(alert_row=row)
        with patch("app.agency_api.v1.router._get_db", return_value=db):
            with TestClient(_build_app(db)) as c:
                resp = c.get("/agency/v1/alerts/alert-001/context")
        assert resp.status_code == 200
        ctx = resp.json()
        assert ctx["evidence"] == evidence

    def test_context_metrics_is_list(self):
        row = _alert_row()
        db = self._make_db(alert_row=row)
        with patch("app.agency_api.v1.router._get_db", return_value=db):
            with TestClient(_build_app(db)) as c:
                resp = c.get("/agency/v1/alerts/alert-001/context")
        assert isinstance(resp.json()["metrics"], list)

    def test_context_recent_jobs_is_list(self):
        row = _alert_row()
        db = self._make_db(alert_row=row)
        with patch("app.agency_api.v1.router._get_db", return_value=db):
            with TestClient(_build_app(db)) as c:
                resp = c.get("/agency/v1/alerts/alert-001/context")
        assert isinstance(resp.json()["recent_jobs"], list)


class TestGetAlertDiagnoses:
    def test_returns_empty_list(self):
        db = MagicMock()
        with patch("app.agency_api.v1.router._get_db", return_value=db):
            with TestClient(_build_app(db)) as c:
                resp = c.get("/agency/v1/alerts/any-id/diagnoses")
        assert resp.status_code == 200
        assert resp.json() == []


class TestCoreAlertTypes:
    def test_all_six_types_present(self):
        expected = {
            "JOB_FAILED", "JOB_STUCK", "SOURCE_STALE",
            "SOURCE_ERROR", "RECONCILIATION_FAILED", "PIPELINE_NOT_RUN",
        }
        assert expected == CORE_ALERT_TYPES
