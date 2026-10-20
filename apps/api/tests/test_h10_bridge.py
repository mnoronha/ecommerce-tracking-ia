"""
H10 Core Bridge — recommendation approval → Core Task tests.

Covers:
  §1  Auth                  — hermes approved, platform_web blocked, generic task blocked (3)
  §2  Validation            — invalid rec, missing rec, rejected rec, auto_execute guard (4)
  §3  Task creation         — happy path: fields mapped, description built, source linked (3)
  §4  Priority mapping      — HIGH→URGENT, MEDIUM→HIGH, LOW→MEDIUM (3)
  §5  Idempotency           — duplicate approve returns same task_id, no new insert (3)
  §6  Audit trail           — core_recommendation_approvals row written (2)
  §7  Notion compatibility  — sync_state=none for scheduler pickup (1)
  §8  No side effects       — no Media Change, no platform HTTP calls (2)
  §9  Regression            — /decision still rejects hermes_service (1)

Total: 22 tests

Note: app.agency_api.v1.__init__ does `from .router import router` which shadows the
submodule name for plain `import app.agency_api.v1.router as x`.
All DB patching uses unittest.mock.patch('app.agency_api.v1.router._get_db')
which correctly resolves to the module via importlib.
"""
from __future__ import annotations

import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.agency_api.v1.auth import (
    SCOPE_HERMES_WRITE,
    SCOPE_PLATFORM_WRITE,
    SCOPE_READ_ANY,
    SCOPE_RECOMMENDATION_APPROVE,
    AuthContext,
)
from app.agency_api.v1.router import (
    _build_task_description_from_rec,
    _map_rec_priority_to_task,
    router as agency_router,
)


# ── Shared constants ──────────────────────────────────────────────────────────

_ADMIN    = AuthContext(scope="agency_admin")
_HERMES   = AuthContext(scope="hermes_service")
_PLATFORM = AuthContext(scope="platform_web")

_REC_ID  = str(uuid.uuid4())
_TASK_ID = str(uuid.uuid4())
_CLIENT  = "test-client"
_V1      = "/agency/v1"  # router prefix

_BASE_REC: dict = {
    "id":                      _REC_ID,
    "client_id":               _CLIENT,
    "title":                   "Increase daily budget by 20%",
    "action":                  "Raise Meta campaign daily budget from R$100 to R$120",
    "rationale":               "ROAS above target for 7 consecutive days",
    "priority":                "HIGH",
    "risk":                    "Spend increase of R$20/day",
    "expected_impact":         "+15% conversions",
    "requires_human_approval": True,
    "status":                  "PROPOSED",
    "fingerprint":             None,
    "created_by":              "hermes",
    "created_at":              "2026-10-20T08:00:00+00:00",
}

_INSERTED_TASK: dict = {
    "id":               _TASK_ID,
    "client_id":        _CLIENT,
    "title":            _BASE_REC["title"],
    "description":      "",
    "status":           "OPEN",
    "priority":         "URGENT",
    "source_type":      "recommendation",
    "source_id":        _REC_ID,
    "visibility_scope": "AGENCY_ONLY",
    "sync_state":       "none",
    "external_task_id": None,
    "external_system":  None,
    "last_synced_at":   None,
    "sync_error":       None,
    "owner":            None,
    "due_date":         None,
    "completed_at":     None,
    "created_at":       "2026-10-20T10:00:00+00:00",
    "updated_at":       "2026-10-20T10:00:00+00:00",
}


# ── DB mock factory ───────────────────────────────────────────────────────────

_MISSING = object()  # sentinel: "use _BASE_REC default"


def _make_db(
    rec=_MISSING,          # _MISSING → _BASE_REC; None → "not found"
    existing_task: dict | None = None,
) -> MagicMock:
    """Build a mock Supabase client.

    rec=_MISSING (default) → uses _BASE_REC.
    rec=None               → simulates recommendation not found (empty select).
    rec={...}              → uses that dict.

    Captures inserts into db._inserted[table_name] for assertion.
    """
    if rec is _MISSING:
        rec = _BASE_REC

    db = MagicMock()
    db._inserted: dict[str, list] = {}

    rec_fetch_res  = MagicMock(data=[rec] if rec is not None else [])
    task_check_res = MagicMock(data=[{"id": existing_task["id"]}] if existing_task else [])
    noop_res       = MagicMock(data=[])

    def _table(name: str):
        t = MagicMock()

        if name == "core_recommendations":
            t.select.return_value.eq.return_value.limit.return_value.execute.return_value = rec_fetch_res
            t.update.return_value.eq.return_value.execute.return_value = noop_res

        elif name == "core_tasks":
            # Existence check: select("id").eq(...).eq(...).limit(1).execute()
            t.select.return_value.eq.return_value.eq.return_value.limit.return_value.execute.return_value = task_check_res

            # Insert: .insert(payload).execute() → data=[row]
            def _insert_task(payload):
                db._inserted.setdefault("core_tasks", []).append(payload)
                exec_result = MagicMock(data=[{**_INSERTED_TASK, **payload}])
                m = MagicMock()
                m.execute.return_value = exec_result
                return m

            t.insert.side_effect = _insert_task

        elif name == "core_recommendation_approvals":
            def _insert_audit(payload):
                db._inserted.setdefault("core_recommendation_approvals", []).append(payload)
                m = MagicMock()
                m.execute.return_value = noop_res
                return m

            t.insert.side_effect = _insert_audit

        elif name == "core_changes":
            def _insert_change(payload):
                db._inserted.setdefault("core_changes", []).append(payload)
                m = MagicMock()
                m.execute.return_value = noop_res
                return m

            t.insert.side_effect = _insert_change

        else:
            t.insert.return_value.execute.return_value = noop_res

        return t

    db.table.side_effect = _table
    return db


# ── App builder ───────────────────────────────────────────────────────────────

def _build_app(auth: AuthContext) -> FastAPI:
    app = FastAPI()
    app.include_router(agency_router)
    app.dependency_overrides[SCOPE_RECOMMENDATION_APPROVE] = lambda: auth
    app.dependency_overrides[SCOPE_PLATFORM_WRITE]         = lambda: auth
    app.dependency_overrides[SCOPE_HERMES_WRITE]           = lambda: auth
    app.dependency_overrides[SCOPE_READ_ANY]               = lambda: auth
    return app


@contextmanager
def _client(auth: AuthContext, db: MagicMock):
    """TestClient with _get_db patched to the given mock."""
    app = _build_app(auth)
    with patch("app.agency_api.v1.router._get_db", return_value=db):
        yield TestClient(app)


# ── §1 Auth ───────────────────────────────────────────────────────────────────

def test_hermes_can_approve():
    """hermes_service is allowed by SCOPE_RECOMMENDATION_APPROVE."""
    db = _make_db()
    with _client(_HERMES, db) as c:
        resp = c.post(f"{_V1}/recommendations/{_REC_ID}/approve", json={"actor": "hermes-h10"})
    assert resp.status_code == 201
    assert resp.json()["task_id"] == _TASK_ID


def test_platform_web_cannot_approve():
    """platform_web is excluded from SCOPE_RECOMMENDATION_APPROVE — use /decision instead."""
    app = FastAPI()
    app.include_router(agency_router)
    # Override PLATFORM_WRITE but NOT SCOPE_RECOMMENDATION_APPROVE
    app.dependency_overrides[SCOPE_PLATFORM_WRITE] = lambda: _PLATFORM

    db = _make_db()
    with patch("app.agency_api.v1.router._get_db", return_value=db):
        with patch("app.agency_api.v1.auth._resolve_service_token",
                   return_value=AuthContext(scope="platform_web")):
            c = TestClient(app, raise_server_exceptions=False)
            resp = c.post(
                f"{_V1}/recommendations/{_REC_ID}/approve",
                headers={"Authorization": "Bearer fake-platform-key"},
                json={"actor": "human-user"},
            )
    assert resp.status_code == 403


def test_hermes_cannot_create_generic_task():
    """Generic POST /tasks remains blocked to hermes_service (SCOPE_PLATFORM_WRITE)."""
    app = FastAPI()
    app.include_router(agency_router)
    # Only override SCOPE_RECOMMENDATION_APPROVE, leave SCOPE_PLATFORM_WRITE as-is
    app.dependency_overrides[SCOPE_RECOMMENDATION_APPROVE] = lambda: _HERMES

    db = _make_db()
    with patch("app.agency_api.v1.router._get_db", return_value=db):
        with patch("app.agency_api.v1.auth._resolve_service_token",
                   return_value=AuthContext(scope="hermes_service")):
            c = TestClient(app, raise_server_exceptions=False)
            resp = c.post(
                f"{_V1}/clients/test-client/tasks",
                headers={"Authorization": "Bearer fake-hermes-key"},
                json={"title": "Should be rejected"},
            )
    assert resp.status_code == 403


# ── §2 Validation ─────────────────────────────────────────────────────────────

def test_invalid_uuid_returns_404():
    db = _make_db()
    with _client(_ADMIN, db) as c:
        resp = c.post(f"{_V1}/recommendations/not-a-uuid/approve", json={"actor": "admin"})
    assert resp.status_code == 404


def test_missing_recommendation_returns_404():
    db = _make_db(rec=None)
    with _client(_ADMIN, db) as c:
        resp = c.post(f"{_V1}/recommendations/{_REC_ID}/approve", json={"actor": "admin"})
    assert resp.status_code == 404


def test_rejected_recommendation_returns_422():
    db = _make_db(rec={**_BASE_REC, "status": "REJECTED"})
    with _client(_ADMIN, db) as c:
        resp = c.post(f"{_V1}/recommendations/{_REC_ID}/approve", json={"actor": "admin"})
    assert resp.status_code == 422
    assert "rejected" in resp.json()["detail"].lower()


def test_auto_execute_guard_returns_422():
    """requires_human_approval=False recommendations must not be bridged here."""
    db = _make_db(rec={**_BASE_REC, "requires_human_approval": False})
    with _client(_ADMIN, db) as c:
        resp = c.post(f"{_V1}/recommendations/{_REC_ID}/approve", json={"actor": "admin"})
    assert resp.status_code == 422
    assert "requires_human_approval" in resp.json()["detail"]


# ── §3 Task creation ──────────────────────────────────────────────────────────

def test_happy_path_returns_201_with_task_id():
    db = _make_db()
    with _client(_ADMIN, db) as c:
        resp = c.post(
            f"{_V1}/recommendations/{_REC_ID}/approve",
            json={"actor": "admin", "reason": "Approved after weekly review"},
        )
    assert resp.status_code == 201
    body = resp.json()
    assert body["task_id"] == _TASK_ID
    assert body["recommendation_id"] == _REC_ID
    assert body["created"] is True
    assert body["client_id"] == _CLIENT


def test_task_source_fields_set_correctly():
    db = _make_db()
    with _client(_ADMIN, db) as c:
        c.post(f"{_V1}/recommendations/{_REC_ID}/approve", json={"actor": "admin"})

    inserted = db._inserted.get("core_tasks", [])
    assert inserted, "core_tasks.insert was never called"
    row = inserted[0]
    assert row["source_type"] == "recommendation"
    assert row["source_id"] == _REC_ID
    assert row["status"] == "OPEN"
    assert row["visibility_scope"] == "AGENCY_ONLY"


def test_task_description_contains_recommendation_fields():
    desc = _build_task_description_from_rec(_BASE_REC)
    assert "Raise Meta campaign" in desc         # action
    assert "ROAS above target" in desc           # rationale
    assert "Spend increase" in desc              # risk
    assert "+15% conversions" in desc            # expected_impact
    assert f"recommendation/{_REC_ID}" in desc   # source link


# ── §4 Priority mapping ───────────────────────────────────────────────────────

def test_priority_high_maps_to_urgent():
    assert _map_rec_priority_to_task("HIGH") == "URGENT"


def test_priority_medium_maps_to_high():
    assert _map_rec_priority_to_task("MEDIUM") == "HIGH"


def test_priority_low_maps_to_medium():
    assert _map_rec_priority_to_task("LOW") == "MEDIUM"


# ── §5 Idempotency ────────────────────────────────────────────────────────────

def test_duplicate_approve_returns_same_task_id():
    db = _make_db(existing_task={"id": _TASK_ID})
    with _client(_ADMIN, db) as c:
        resp = c.post(f"{_V1}/recommendations/{_REC_ID}/approve", json={"actor": "admin"})
    assert resp.status_code == 201
    assert resp.json()["task_id"] == _TASK_ID
    assert resp.json()["created"] is False


def test_duplicate_approve_does_not_insert_new_task():
    db = _make_db(existing_task={"id": _TASK_ID})
    with _client(_ADMIN, db) as c:
        c.post(f"{_V1}/recommendations/{_REC_ID}/approve", json={"actor": "admin"})
    assert "core_tasks" not in db._inserted, (
        "core_tasks.insert must not be called when a task already exists"
    )


def test_already_approved_status_returns_idempotent_result():
    db = _make_db(
        rec={**_BASE_REC, "status": "APPROVED"},
        existing_task={"id": _TASK_ID},
    )
    with _client(_ADMIN, db) as c:
        resp = c.post(f"{_V1}/recommendations/{_REC_ID}/approve", json={"actor": "admin"})
    assert resp.status_code == 201
    assert resp.json()["created"] is False
    assert resp.json()["task_id"] == _TASK_ID


# ── §6 Audit trail ────────────────────────────────────────────────────────────

def test_audit_record_written_on_new_approval():
    db = _make_db()
    with _client(_ADMIN, db) as c:
        c.post(
            f"{_V1}/recommendations/{_REC_ID}/approve",
            json={"actor": "admin", "reason": "Weekly review sign-off"},
        )
    audits = db._inserted.get("core_recommendation_approvals", [])
    assert audits, "core_recommendation_approvals.insert was never called"


def test_audit_record_contains_actor_and_ids():
    db = _make_db()
    with _client(_ADMIN, db) as c:
        c.post(f"{_V1}/recommendations/{_REC_ID}/approve", json={"actor": "admin-user"})
    audits = db._inserted.get("core_recommendation_approvals", [])
    assert audits
    row = audits[0]
    assert row["actor"] == "admin-user"
    assert row["recommendation_id"] == _REC_ID
    assert "task_id" in row


# ── §7 Notion compatibility ───────────────────────────────────────────────────

def test_created_task_has_sync_state_none():
    """sync_state=none ensures the Notion scheduler picks up the task within 5 minutes."""
    db = _make_db()
    with _client(_ADMIN, db) as c:
        c.post(f"{_V1}/recommendations/{_REC_ID}/approve", json={"actor": "admin"})
    inserted = db._inserted.get("core_tasks", [])
    assert inserted
    assert inserted[0]["sync_state"] == "none", (
        "sync_state must be 'none' so Notion scheduler includes the task in next batch"
    )


# ── §8 No side effects ────────────────────────────────────────────────────────

def test_no_media_change_created():
    """Approval creates a Task. Media Change only appears on execution evidence."""
    db = _make_db()
    with _client(_ADMIN, db) as c:
        c.post(f"{_V1}/recommendations/{_REC_ID}/approve", json={"actor": "admin"})
    assert "core_changes" not in db._inserted, (
        "approve_recommendation must NOT write to core_changes"
    )


def test_no_external_http_calls():
    """Approval must not call any ad-platform API."""
    db = _make_db()
    with _client(_ADMIN, db) as c:
        with patch("httpx.AsyncClient.post") as mock_post, \
             patch("httpx.AsyncClient.get") as mock_get:
            c.post(f"{_V1}/recommendations/{_REC_ID}/approve", json={"actor": "admin"})
            assert not mock_post.called
            assert not mock_get.called


# ── §9 Regression — existing /decision endpoint ───────────────────────────────

def test_decision_endpoint_still_rejects_hermes_service():
    """POST /recommendations/{id}/decision must keep denying hermes_service."""
    app = FastAPI()
    app.include_router(agency_router)
    # Override ONLY the approve scope — leave /decision scope real
    app.dependency_overrides[SCOPE_RECOMMENDATION_APPROVE] = lambda: _HERMES

    db = _make_db()
    with patch("app.agency_api.v1.router._get_db", return_value=db):
        with patch("app.agency_api.v1.auth._resolve_service_token",
                   return_value=AuthContext(scope="hermes_service")):
            c = TestClient(app, raise_server_exceptions=False)
            resp = c.post(
                f"{_V1}/recommendations/{_REC_ID}/decision",
                headers={"Authorization": "Bearer fake-hermes-key"},
                json={"schema_version": "1.1", "decision": "ACCEPTED", "actor": "hermes"},
            )
    assert resp.status_code == 403, (
        f"hermes_service must still be denied at /decision endpoint (got {resp.status_code})"
    )
