"""
Isolated unit tests for PR #3:
  1. Outcome Evaluator Real (R04 — PRD §14.1):
     - Open vs closed evaluation window
     - Real baseline vs observation metrics from core_metric_snapshots
     - Concurrent changes cross-check from core_change_log (HOLD + confounders -> INCONCLUSIVE LOW)
     - Versioned re-evaluation marking LK Sneakers hollow outcome 1968845f-... as SUPERSEDED without deleting history
     - Auditable core_job_runs + concurrency lock + idempotent learning candidate generation
  2. API vs Worker Split & Postgres Advisory Lock (PRD §21.4):
     - pg_try_advisory_lock acquisition, contention rejection (zero duplicate scheduler starts),
       split-brain heartbeat protection, and clean pg_advisory_unlock on shutdown
     - ENABLE_EMBEDDED_SCHEDULER=false isolates HTTP API from APScheduler
     - Admin trigger endpoints enforce SCOPE_ADMIN_ONLY (401/403)
"""

from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.core import outcome_evaluator as oe
from app.core.scheduler_setup import build_scheduler, embedded_scheduler_enabled
from app.core.worker_lock import PostgresAdvisoryLock, lock_name_to_key
from app.main import app
from app.worker import run_worker

ADMIN_KEY = "test-pr3-admin-key"
PLATFORM_KEY = "test-pr3-platform-key"
HERMES_KEY = "test-pr3-hermes-key"

LK_CLIENT_UUID = "3e20e8b9-c1b5-449f-bc5c-eb0a00704387"
LK_ACTION_ID = "09b039e0-f2f4-4ecf-a900-3bc551342628"
LK_HOLLOW_OUTCOME_ID = "1968845f-4273-47f0-9b77-33f39c817bd5"
LK_REC_ID = "90847744-1081-49bc-b9eb-865067dc80af"


# ── In-memory fake Supabase for Outcome Evaluator tests ───────────────────────


class _FakeOutcomeDB:
    def __init__(self) -> None:
        self.tables: dict[str, list[dict]] = {
            "clients": [
                {"id": LK_CLIENT_UUID, "client_id": "lk-sneakers", "pixel_id": "lk-px"},
            ],
            "core_actions": [],
            "core_outcomes": [],
            "core_metric_snapshots": [],
            "core_change_log": [],
            "core_job_runs": [],
            "core_learning_candidates": [],
        }

    def table(self, name: str):
        return _FakeQuery(self, name)


class _NotProxy:
    def __init__(self, q: "_FakeQuery") -> None:
        self._q = q

    def is_(self, col: str, val: str):
        self._q._not_null.append(col)
        return self._q


class _FakeQuery:
    def __init__(self, db: _FakeOutcomeDB, name: str) -> None:
        self.db = db
        self.name = name
        self._op = "select"
        self._eq: list[tuple[str, object]] = []
        self._gte: list[tuple[str, str]] = []
        self._lte: list[tuple[str, str]] = []
        self._not_null: list[str] = []
        self._payload = None
        self.not_ = _NotProxy(self)

    def select(self, *_args, **_kwargs):
        self._op = "select"
        return self

    def insert(self, payload: dict):
        self._op = "insert"
        self._payload = dict(payload)
        return self

    def update(self, payload: dict):
        self._op = "update"
        self._payload = dict(payload)
        return self

    def eq(self, col: str, val):
        self._eq.append((col, val))
        return self

    def gte(self, col: str, val: str):
        self._gte.append((col, str(val)))
        return self

    def lte(self, col: str, val: str):
        self._lte.append((col, str(val)))
        return self

    def order(self, *_args, **_kwargs):
        return self

    def limit(self, _n: int):
        return self

    def _matches(self, r: dict) -> bool:
        for c, v in self._eq:
            if str(r.get(c)) != str(v):
                return False
        for c in self._not_null:
            if r.get(c) is None:
                return False
        for c, v in self._gte:
            rv = r.get(c)
            if rv is None or str(rv) < v:
                return False
        for c, v in self._lte:
            rv = r.get(c)
            if rv is None or str(rv) > v:
                return False
        return True

    def execute(self):
        rows = self.db.tables.setdefault(self.name, [])
        if self._op == "select":
            return SimpleNamespace(data=[dict(r) for r in rows if self._matches(r)])
        if self._op == "insert":
            # Enforce core_job_runs unique run_key constraint
            if self.name == "core_job_runs":
                rk = self._payload.get("run_key")
                if any(r.get("run_key") == rk for r in rows):
                    raise RuntimeError("duplicate key value violates unique constraint core_job_runs_run_key_key (23505)")
            # Enforce partial unique index on active outcomes per (action_id, evaluation_window_days)
            if self.name == "core_outcomes":
                aid = self._payload.get("action_id")
                wd = self._payload.get("evaluation_window_days")
                st = self._payload.get("status")
                if aid and st != "SUPERSEDED":
                    if any(
                        r.get("action_id") == aid
                        and r.get("evaluation_window_days") == wd
                        and r.get("status") != "SUPERSEDED"
                        for r in rows
                    ):
                        raise RuntimeError("duplicate key violates uq_core_outcomes_active_action_window (23505)")
            rec = dict(self._payload)
            rec.setdefault("id", f"{self.name[:6]}-{len(rows) + 1:04d}")
            rows.append(rec)
            return SimpleNamespace(data=[dict(rec)])
        if self._op == "update":
            updated = []
            for r in rows:
                if self._matches(r):
                    r.update(self._payload)
                    updated.append(dict(r))
            return SimpleNamespace(data=updated)
        return SimpleNamespace(data=[])


def _seed_lk_snapshots_and_changes(db: _FakeOutcomeDB, *, num_changes: int = 12) -> None:
    db.tables["core_metric_snapshots"].extend([
        {
            "id": "snap-base-nov12-18",
            "client_id": "lk-sneakers",
            "period_start": "2026-11-12",
            "period_end": "2026-11-18",
            "view": "live",
            "computed_at": "2026-11-19T07:16:00+00:00",
            "metrics": [
                {"metric_key": "roas_meta", "value": 10.4697, "value_status": "OK"},
                {"metric_key": "cpa_meta", "value": 142.63, "value_status": "OK"},
                {"metric_key": "meta_spend", "value": 8557.55, "value_status": "OK"},
                {"metric_key": "revenue_business", "value": 258593.0, "value_status": "OK"},
                {"metric_key": "mer", "value": 18.68, "value_status": "OK"},
            ],
            "health": {"business": "READY", "meta_ads": "READY", "roas_meta": "READY"},
        },
        {
            "id": "snap-obs-nov19-25",
            "client_id": "lk-sneakers",
            "period_start": "2026-11-19",
            "period_end": "2026-11-25",
            "view": "live",
            "computed_at": "2026-11-26T07:16:00+00:00",
            "metrics": [
                {"metric_key": "roas_meta", "value": 7.2917, "value_status": "OK"},
                {"metric_key": "cpa_meta", "value": 198.01, "value_status": "OK"},
                {"metric_key": "meta_spend", "value": 7722.58, "value_status": "OK"},
                {"metric_key": "revenue_business", "value": 233582.79, "value_status": "OK"},
                {"metric_key": "mer", "value": 18.1283, "value_status": "OK"},
            ],
            "health": {"business": "READY", "meta_ads": "READY", "roas_meta": "READY"},
        },
    ])
    for i in range(num_changes):
        db.tables["core_change_log"].append({
            "id": f"chg-{i:03d}",
            "client_id": "lk-sneakers",
            "channel": "META",
            "change_type": "BUDGET_CHANGE",
            "occurred_at": f"2026-11-{20 + (i % 5):02d}T12:00:00+00:00",
            "entity_name_at_time": f"Campanha BF {i}",
        })


# ══════════════════════════════════════════════════════════════════════════════
# 1. Outcome Evaluator Real (R04)
# ══════════════════════════════════════════════════════════════════════════════


class TestR04OutcomeEvaluatorReal:
    def test_open_window_is_skipped_and_job_run_audited(self):
        db = _FakeOutcomeDB()
        recent_exec = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
        db.tables["core_actions"].append({
            "id": "act-open-1",
            "client_id": LK_CLIENT_UUID,
            "recommendation_id": LK_REC_ID,
            "action_type": "MANUAL_EXTERNAL_ACTION",
            "platform": "META",
            "status": "EXECUTED",
            "executed_at": recent_exec,
            "evaluation_window_days": 7,
            "before_state": {},
        })

        with patch.object(oe, "get_supabase", return_value=db):
            res = oe.evaluate_pending_outcomes(run_key="outcome_evaluation:test-open-1")

        assert res["evaluated"] == 0
        assert res["skipped"] == 1
        assert res["errors"] == 0
        assert len(db.tables["core_outcomes"]) == 0
        assert len(db.tables["core_job_runs"]) == 1
        assert db.tables["core_job_runs"][0]["job_type"] == "outcome_evaluation"
        assert db.tables["core_job_runs"][0]["status"] == "SUCCEEDED"

    def test_hold_with_concurrent_changes_supersedes_hollow_lk_outcome_without_deleting_history(self):
        db = _FakeOutcomeDB()
        _seed_lk_snapshots_and_changes(db, num_changes=12)

        db.tables["core_actions"].append({
            "id": LK_ACTION_ID,
            "client_id": LK_CLIENT_UUID,
            "recommendation_id": LK_REC_ID,
            "action_type": "NO_ACTION_REQUIRED",
            "platform": "META",
            "status": "EXECUTED",
            "executed_at": "2026-11-19T17:30:00+00:00",
            "evaluation_window_days": 7,
            "before_state": {"roas_meta": 10.4697, "cpa_meta": 142.63},
            "after_state": {"decision": "HOLD"},
        })
        # Existing hollow outcome recorded on 27/11 morning
        db.tables["core_outcomes"].append({
            "id": LK_HOLLOW_OUTCOME_ID,
            "action_id": LK_ACTION_ID,
            "recommendation_id": LK_REC_ID,
            "client_id": LK_CLIENT_UUID,
            "status": "INCONCLUSIVE",
            "confidence": "LOW",
            "evaluation_window_days": 7,
            "metric_refs": [],
            "delta": None,
            "data_health": {"note": "manual_state_comparison", "primary_metric": None},
            "created_by": "outcome_evaluator",
        })

        with patch.object(oe, "get_supabase", return_value=db):
            re_res = oe.reevaluate_action_outcome(
                LK_ACTION_ID,
                reason="replace_hollow_manual_state_with_real_snapshots",
                supersede_outcome_id=LK_HOLLOW_OUTCOME_ID,
            )

        assert "error" not in re_res
        assert re_res["status"] == "INCONCLUSIVE"
        assert re_res["confidence"] == "LOW"
        assert re_res["supersedes_outcome_id"] == LK_HOLLOW_OUTCOME_ID
        assert re_res["learning_candidate_id"] is not None

        # Historical hollow row preserved (never deleted!) and marked SUPERSEDED
        assert len(db.tables["core_outcomes"]) == 2
        hollow = next(r for r in db.tables["core_outcomes"] if r["id"] == LK_HOLLOW_OUTCOME_ID)
        new_out = next(r for r in db.tables["core_outcomes"] if r["id"] != LK_HOLLOW_OUTCOME_ID)
        assert hollow["status"] == "SUPERSEDED"
        assert hollow["superseded_reason"] == "replace_hollow_manual_state_with_real_snapshots"
        assert hollow.get("superseded_at") is not None

        # New outcome carries real snapshots, real delta, and concurrent confounders
        assert new_out["supersedes_outcome_id"] == LK_HOLLOW_OUTCOME_ID
        assert len(new_out["metric_refs"]) > 0
        assert "roas_meta" in new_out["delta"]
        assert new_out["delta"]["roas_meta"]["before"] == pytest.approx(10.4697)
        assert new_out["delta"]["roas_meta"]["after"] == pytest.approx(7.2917)
        assert new_out["data_health"]["concurrent_changes_count"] == 12
        assert "hold_action_no_direct_causality" in new_out["data_health"]["confounders"]
        assert "concurrent_changes_in_window:12" in new_out["data_health"]["confounders"]

        # Methodological PENDING learning candidate created
        assert len(db.tables["core_learning_candidates"]) == 1
        assert db.tables["core_learning_candidates"][0]["status"] == "PENDING"
        assert db.tables["core_learning_candidates"][0]["outcome_id"] == new_out["id"]

        # Second scheduler pass is strictly idempotent (zero duplicate outcomes or candidates)
        with patch.object(oe, "get_supabase", return_value=db):
            res2 = oe.evaluate_pending_outcomes(run_key="outcome_evaluation:test-idemp-2")
        assert res2["evaluated"] == 0
        assert res2["skipped"] == 1
        assert len(db.tables["core_outcomes"]) == 2
        assert len(db.tables["core_learning_candidates"]) == 1

    def test_clean_closed_window_action_produces_positive_outcome_and_job_lock_prevents_duplicate_run(self):
        db = _FakeOutcomeDB()
        db.tables["core_metric_snapshots"].extend([
            {
                "id": "snap-g-base",
                "client_id": "lk-sneakers",
                "period_start": "2026-11-05",
                "period_end": "2026-11-11",
                "view": "live",
                "computed_at": "2026-11-12T07:16:00+00:00",
                "metrics": [
                    {"metric_key": "google_roas_ecommerce", "value": 3.0, "value_status": "OK"},
                    {"metric_key": "google_cpa_ecommerce", "value": 250.0, "value_status": "OK"},
                ],
                "health": {"business": "READY", "google_ads": "READY", "roas_google": "READY"},
            },
            {
                "id": "snap-g-obs",
                "client_id": "lk-sneakers",
                "period_start": "2026-11-12",
                "period_end": "2026-11-18",
                "view": "live",
                "computed_at": "2026-11-19T07:16:00+00:00",
                "metrics": [
                    {"metric_key": "google_roas_ecommerce", "value": 4.2, "value_status": "OK"},
                    {"metric_key": "google_cpa_ecommerce", "value": 180.0, "value_status": "OK"},
                ],
                "health": {"business": "READY", "google_ads": "READY", "roas_google": "READY"},
            },
        ])
        db.tables["core_actions"].append({
            "id": "act-google-clean-1",
            "client_id": LK_CLIENT_UUID,
            "recommendation_id": LK_REC_ID,
            "action_type": "MANUAL_EXTERNAL_ACTION",
            "platform": "GOOGLE",
            "entity_type": "CAMPAIGN",
            "status": "EXECUTED",
            "executed_at": "2026-11-12T10:00:00+00:00",
            "evaluation_window_days": 7,
            "before_state": {},
        })

        with patch.object(oe, "get_supabase", return_value=db):
            r1 = oe.evaluate_pending_outcomes(run_key="outcome_evaluation:2026-11-27T12")
            assert r1["evaluated"] == 1
            assert len(db.tables["core_outcomes"]) == 1
            out = db.tables["core_outcomes"][0]
            assert out["status"] == "POSITIVE"
            assert out["confidence"] == "HIGH"
            assert out["delta"]["google_roas_ecommerce"]["pct"] == pytest.approx(40.0)
            assert len(db.tables["core_learning_candidates"]) == 1

            # Concurrent/duplicate run_key rejected by core_job_runs unique constraint
            r_dup = oe.evaluate_pending_outcomes(run_key="outcome_evaluation:2026-11-27T12")
            assert r_dup["skipped_concurrent"] is True
            assert len(db.tables["core_outcomes"]) == 1


# ══════════════════════════════════════════════════════════════════════════════
# 2. Worker Split & Postgres Advisory Lock (PRD §21.4)
# ══════════════════════════════════════════════════════════════════════════════


class _FakePgCursor:
    def __init__(self, conn: "_FakePgConn") -> None:
        self.conn = conn
        self._result = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, sql: str, params=None):
        self.conn.queries.append((sql, params))
        if "pg_try_advisory_lock" in sql:
            self._result = (self.conn.lock_response,)
        elif "pg_advisory_unlock" in sql:
            self.conn.unlocked = True
            self._result = (True,)
        elif "SELECT 1" in sql:
            if self.conn.drop_on_heartbeat:
                raise RuntimeError("connection closed")
            self._result = (1,)

    def fetchone(self):
        return self._result


class _FakePgConn:
    def __init__(self, *, lock_response: bool = True, drop_on_heartbeat: bool = False) -> None:
        self.lock_response = lock_response
        self.drop_on_heartbeat = drop_on_heartbeat
        self.queries: list[tuple[str, object]] = []
        self.closed = False
        self.unlocked = False

    def cursor(self):
        return _FakePgCursor(self)

    def close(self):
        self.closed = True


class TestWorkerSplitAndAdvisoryLock:
    def test_advisory_lock_acquire_and_release(self):
        conn = _FakePgConn(lock_response=True)
        lock = PostgresAdvisoryLock(
            lock_name="noro:test:lock",
            dsn="postgresql://user:pass@localhost/db",
            connection_factory=lambda _dsn: conn,
        )
        assert lock.acquire() is True
        assert lock.held is True
        assert any("pg_try_advisory_lock" in sql and params == (lock_name_to_key("noro:test:lock"),) for sql, params in conn.queries)

        assert lock.verify_alive() is True
        assert lock.release() is True
        assert lock.held is False
        assert conn.unlocked is True
        assert conn.closed is True

    def test_worker_skips_scheduler_when_advisory_lock_held_by_another_replica(self):
        conn = _FakePgConn(lock_response=False)
        lock = PostgresAdvisoryLock(
            dsn="postgresql://user:pass@localhost/db",
            connection_factory=lambda _dsn: conn,
        )
        sched_factory = MagicMock()

        code = run_worker(lock=lock, scheduler_factory=sched_factory)
        assert code == 0
        sched_factory.assert_not_called()
        assert conn.closed is True

    def test_worker_starts_scheduler_under_lock_and_releases_on_stop(self):
        conn = _FakePgConn(lock_response=True)
        lock = PostgresAdvisoryLock(
            dsn="postgresql://user:pass@localhost/db",
            connection_factory=lambda _dsn: conn,
        )
        fake_sched = MagicMock()
        fake_sched.get_jobs.return_value = [SimpleNamespace(id="outcome_evaluation")]
        stop_evt = threading.Event()
        stop_evt.set()  # immediately stop after start

        code = run_worker(
            lock=lock,
            scheduler_factory=lambda: (fake_sched, {}),
            heartbeat_seconds=0.05,
            stop_event=stop_evt,
        )
        assert code == 0
        fake_sched.start.assert_called_once()
        fake_sched.shutdown.assert_called_once_with(wait=True)
        assert conn.unlocked is True
        assert conn.closed is True

    def test_worker_exits_and_stops_scheduler_if_lock_connection_drops(self):
        conn = _FakePgConn(lock_response=True, drop_on_heartbeat=True)
        lock = PostgresAdvisoryLock(
            dsn="postgresql://user:pass@localhost/db",
            connection_factory=lambda _dsn: conn,
        )
        fake_sched = MagicMock()
        fake_sched.get_jobs.return_value = []
        stop_evt = threading.Event()

        code = run_worker(
            lock=lock,
            scheduler_factory=lambda: (fake_sched, {}),
            heartbeat_seconds=0.01,
            stop_event=stop_evt,
        )
        assert code == 1
        fake_sched.start.assert_called_once()
        fake_sched.shutdown.assert_called_once_with(wait=True)
        assert conn.closed is True

    def test_embedded_scheduler_flag_and_shared_builder_registers_outcome_evaluation(self, monkeypatch):
        monkeypatch.setenv("ENABLE_EMBEDDED_SCHEDULER", "false")
        assert embedded_scheduler_enabled() is False

        monkeypatch.setenv("ENABLE_EMBEDDED_SCHEDULER", "true")
        assert embedded_scheduler_enabled() is True

        sched, _ = build_scheduler()
        job_ids = {j.id for j in sched.get_jobs()}
        assert "outcome_evaluation" in job_ids
        assert "core_pipeline_daily" in job_ids

    def test_admin_outcome_endpoints_require_agency_admin(self, monkeypatch):
        monkeypatch.setattr(settings, "AGENCY_API_ADMIN_KEY", ADMIN_KEY)
        monkeypatch.setattr(settings, "AGENCY_API_PLATFORM_KEY", PLATFORM_KEY)
        monkeypatch.setattr(settings, "AGENCY_API_HERMES_KEY", HERMES_KEY)

        tc = TestClient(app, raise_server_exceptions=False)
        for url in (
            "/agency/v1/admin/outcomes/evaluate",
            f"/agency/v1/admin/actions/{LK_ACTION_ID}/reevaluate-outcome",
        ):
            assert tc.post(url).status_code == 401
            assert tc.post(url, headers={"Authorization": f"Bearer {PLATFORM_KEY}"}).status_code == 403
            assert tc.post(url, headers={"Authorization": f"Bearer {HERMES_KEY}"}).status_code == 403
