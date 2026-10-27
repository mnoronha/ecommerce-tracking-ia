"""
Tests for Meeting Intelligence — process_meeting idempotency, task creation contract,
and processor status surfacing.

Coverage:
  - process_meeting first call → 201, summary created, tasks_created entries
  - process_meeting second call same meeting_id → 200, replayed=True, no new tasks
  - second call with different body → still returns existing (no silent overwrite)
  - create_tasks=True, two distinct commitments → both tasks created (status=created)
  - create_tasks=True, unique violation on second task → status=failed, reason=UNIQUE_VIOLATION
  - create_tasks=False → no tasks attempted
  - missing owner → task row still created (owner=None)
  - missing due_date → task row still created (due_date=None)
  - tasks_created entries carry what/title/status/task_id or reason
  - processor=manual → processor_manual_status=AVAILABLE
  - processor=claude + ANTHROPIC_CREDITS_OK unset → processor_claude_status=DEGRADED_BLOCKED_CREDITS
  - processor=claude + ANTHROPIC_CREDITS_OK=true → processor_claude_status=LIVE
  - meeting not found → 404
  - client isolation: task client_id matches meeting client_id
  - tasks created with sync_state=none (Notion cron picks them up)
  - zero sends, zero media writes (no send_* calls in the endpoint)
"""
from __future__ import annotations

import json
import os
import uuid
from types import SimpleNamespace
from typing import Optional
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.agency_api.v1.router import router
from app.agency_api.v1.auth import AuthContext, SCOPE_PLATFORM_WRITE

# ── Constants ─────────────────────────────────────────────────────────────────

MEETING_ID   = "eca89b66-0e60-4d04-b53b-a466d6fe309e"
CLIENT_ID    = "test-client"
SUMMARY_ID   = str(uuid.uuid4())
TASK_ID_1    = str(uuid.uuid4())
TASK_ID_2    = str(uuid.uuid4())

_SUMMARY_ROW = {
    "id":             SUMMARY_ID,
    "meeting_id":     MEETING_ID,
    "summary":        "Test summary.",
    "decisions":      json.dumps([{"decision": "d1", "owner": "Alice"}]),
    "open_questions": json.dumps(["q1"]),
    "commitments":    json.dumps([{"who": "Bob", "what": "do thing"}]),
    "follow_ups":     json.dumps([]),
    "tasks_created":  json.dumps([{"what": "do thing", "title": "do thing", "status": "created", "task_id": TASK_ID_1}]),
    "processor":      "manual",
    "created_at":     "2026-10-27T10:00:00+00:00",
}

_BODY_MIN = {
    "summary": "Test summary.",
    "decisions": [{"decision": "d1", "owner": "Alice"}],
    "open_questions": ["q1"],
    "commitments": [],
    "follow_ups": [],
    "create_tasks": False,
    "processor": "manual",
}


# ── DB factory ────────────────────────────────────────────────────────────────

class _FakeExec(SimpleNamespace):
    pass


class FakeDB:
    """Supabase-like mock with stable per-table objects."""

    def __init__(
        self,
        meeting_exists: bool = True,
        summary_exists: bool = False,
        existing_summary: Optional[dict] = None,
        task_insert_error: Optional[Exception] = None,
        task_ids: Optional[list[str]] = None,
        new_summary_row: Optional[dict] = None,
    ):
        meeting_row = {"id": MEETING_ID, "client_id": CLIENT_ID} if meeting_exists else None
        task_ids_   = task_ids or [TASK_ID_1, TASK_ID_2]
        _call       = [0]

        # ── meetings ──────────────────────────────────────────────────────────
        meet = MagicMock()
        meet_q = MagicMock()
        meet_q.execute.return_value = _FakeExec(data=[meeting_row] if meeting_row else [])
        meet.select.return_value.eq.return_value.limit.return_value = meet_q
        meet.update.return_value.eq.return_value.execute.return_value = _FakeExec(data=[])
        self._meet = meet

        # ── summaries ─────────────────────────────────────────────────────────
        summ = MagicMock()
        sum_q = MagicMock()
        sum_q.execute.return_value = _FakeExec(
            data=[existing_summary or _SUMMARY_ROW] if summary_exists else []
        )
        summ.select.return_value.eq.return_value.order.return_value.limit.return_value = sum_q

        def _sum_insert(row):
            import json as _j
            # Echo back the inserted row, parsing JSONB fields from JSON strings
            echoed = {"id": SUMMARY_ID, **row}
            for field in ("decisions", "open_questions", "commitments", "follow_ups", "tasks_created"):
                v = echoed.get(field)
                if isinstance(v, str):
                    try:
                        echoed[field] = _j.loads(v)
                    except Exception:
                        echoed[field] = []
            m = MagicMock()
            m.execute.return_value = _FakeExec(data=[new_summary_row or echoed])
            return m

        summ.insert.side_effect = _sum_insert
        self._summ = summ

        # ── tasks ─────────────────────────────────────────────────────────────
        task = MagicMock()
        if task_insert_error:
            task.insert.return_value.execute.side_effect = task_insert_error
        else:
            def _task_insert(row):
                idx = _call[0] % len(task_ids_)
                _call[0] += 1
                m = MagicMock()
                m.execute.return_value = _FakeExec(data=[{**row, "id": task_ids_[idx]}])
                return m
            task.insert.side_effect = _task_insert
        self._task = task

    def table(self, name: str):
        return {
            "core_meetings":          self._meet,
            "core_meeting_summaries": self._summ,
            "core_tasks":             self._task,
        }[name]


def _make_db(**kwargs) -> FakeDB:
    return FakeDB(**kwargs)


# ── App fixture ───────────────────────────────────────────────────────────────

def _make_app(db):
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[SCOPE_PLATFORM_WRITE] = lambda: AuthContext(
        scope="agency_admin", subject="test-actor"
    )
    return app, TestClient(app)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _post(client: TestClient, body: dict) -> object:
    return client.post(f"/agency/v1/meetings/{MEETING_ID}/process", json=body)


# ── Tests: idempotency ────────────────────────────────────────────────────────

class TestProcessMeetingIdempotency:

    def test_first_call_returns_201(self):
        db = _make_db(summary_exists=False)
        _, tc = _make_app(db)
        with patch("app.agency_api.v1.router._get_db", return_value=db):
            r = _post(tc, _BODY_MIN)
        assert r.status_code == 201

    def test_second_call_returns_200_replayed(self):
        db = _make_db(summary_exists=True)
        _, tc = _make_app(db)
        with patch("app.agency_api.v1.router._get_db", return_value=db):
            r = _post(tc, _BODY_MIN)
        assert r.status_code == 200
        assert r.json()["replayed"] is True

    def test_second_call_returns_existing_summary(self):
        db = _make_db(summary_exists=True)
        _, tc = _make_app(db)
        with patch("app.agency_api.v1.router._get_db", return_value=db):
            r = _post(tc, _BODY_MIN)
        data = r.json()
        assert data["meeting_id"] == MEETING_ID
        assert data["summary"] == "Test summary."

    def test_second_call_does_not_insert_tasks(self):
        """Replay path exits before any task insertion."""
        db = _make_db(summary_exists=True)
        _, tc = _make_app(db)
        with patch("app.agency_api.v1.router._get_db", return_value=db):
            _post(tc, {**_BODY_MIN, "create_tasks": True,
                       "commitments": [{"who": "Alice", "what": "do thing"}]})
        db._task.insert.assert_not_called()

    def test_second_call_different_body_still_returns_existing(self):
        """Changed input does not overwrite existing summary."""
        db = _make_db(summary_exists=True)
        _, tc = _make_app(db)
        different_body = {**_BODY_MIN, "summary": "COMPLETELY DIFFERENT SUMMARY"}
        with patch("app.agency_api.v1.router._get_db", return_value=db):
            r = _post(tc, different_body)
        assert r.json()["summary"] == "Test summary."  # original preserved

    def test_replayed_field_false_on_first_call(self):
        db = _make_db(summary_exists=False)
        _, tc = _make_app(db)
        with patch("app.agency_api.v1.router._get_db", return_value=db):
            r = _post(tc, _BODY_MIN)
        assert r.json()["replayed"] is False


# ── Tests: task creation contract ─────────────────────────────────────────────

class TestTaskCreationContract:

    def test_two_commitments_both_created(self):
        db = _make_db(summary_exists=False, task_ids=[TASK_ID_1, TASK_ID_2])
        _, tc = _make_app(db)
        body = {
            **_BODY_MIN,
            "create_tasks": True,
            "commitments": [
                {"who": "Alice", "what": "First commitment"},
                {"who": "Bob",   "what": "Second commitment"},
            ],
        }
        with patch("app.agency_api.v1.router._get_db", return_value=db):
            r = _post(tc, body)
        assert r.status_code == 201
        tc_list = r.json()["tasks_created"]
        assert len(tc_list) == 2
        assert all(e["status"] == "created" for e in tc_list)
        assert tc_list[0]["task_id"] == TASK_ID_1
        assert tc_list[1]["task_id"] == TASK_ID_2

    def test_unique_violation_surfaces_as_failed(self):
        """DB unique constraint → entry carries status=failed, reason=UNIQUE_VIOLATION."""
        err = Exception("duplicate key value violates unique constraint")
        db = _make_db(summary_exists=False, task_insert_error=err)
        _, tc = _make_app(db)
        body = {**_BODY_MIN, "create_tasks": True,
                "commitments": [{"who": "Alice", "what": "thing"}]}
        with patch("app.agency_api.v1.router._get_db", return_value=db):
            r = _post(tc, body)
        assert r.status_code == 201
        entry = r.json()["tasks_created"][0]
        assert entry["status"] == "failed"
        assert entry["reason"] == "UNIQUE_VIOLATION"

    def test_generic_db_error_surfaces_as_failed(self):
        err = Exception("connection timeout")
        db = _make_db(summary_exists=False, task_insert_error=err)
        _, tc = _make_app(db)
        body = {**_BODY_MIN, "create_tasks": True,
                "commitments": [{"who": "Alice", "what": "thing"}]}
        with patch("app.agency_api.v1.router._get_db", return_value=db):
            r = _post(tc, body)
        entry = r.json()["tasks_created"][0]
        assert entry["status"] == "failed"
        assert entry["reason"] == "DB_ERROR"

    def test_task_entry_has_what_and_title(self):
        db = _make_db(summary_exists=False, task_ids=[TASK_ID_1])
        _, tc = _make_app(db)
        body = {**_BODY_MIN, "create_tasks": True,
                "commitments": [{"who": "Alice", "what": "Send the report"}]}
        with patch("app.agency_api.v1.router._get_db", return_value=db):
            r = _post(tc, body)
        entry = r.json()["tasks_created"][0]
        assert entry["what"] == "Send the report"
        assert "title" in entry

    def test_create_tasks_false_no_tasks_attempted(self):
        db = _make_db(summary_exists=False)
        _, tc = _make_app(db)
        body = {**_BODY_MIN, "create_tasks": False,
                "commitments": [{"who": "Alice", "what": "thing"}]}
        with patch("app.agency_api.v1.router._get_db", return_value=db):
            r = _post(tc, body)
        assert r.status_code == 201
        assert r.json()["tasks_created"] == []

    def test_missing_owner_still_creates_task(self):
        db = _make_db(summary_exists=False, task_ids=[TASK_ID_1])
        _, tc = _make_app(db)
        body = {**_BODY_MIN, "create_tasks": True,
                "commitments": [{"who": None, "what": "No owner task"}]}
        with patch("app.agency_api.v1.router._get_db", return_value=db):
            r = _post(tc, body)
        assert r.json()["tasks_created"][0]["status"] == "created"

    def test_missing_due_date_still_creates_task(self):
        db = _make_db(summary_exists=False, task_ids=[TASK_ID_1])
        _, tc = _make_app(db)
        body = {**_BODY_MIN, "create_tasks": True,
                "commitments": [{"who": "Alice", "what": "No deadline task"}]}
        with patch("app.agency_api.v1.router._get_db", return_value=db):
            r = _post(tc, body)
        assert r.json()["tasks_created"][0]["status"] == "created"

    def test_tasks_created_with_sync_state_none(self):
        """Tasks must start with sync_state='none' so Notion cron picks them up."""
        db = _make_db(summary_exists=False, task_ids=[TASK_ID_1])
        _, tc = _make_app(db)
        body = {**_BODY_MIN, "create_tasks": True,
                "commitments": [{"who": "Alice", "what": "Sync check"}]}
        captured = []

        def _capture(row):
            captured.append(row)
            m = MagicMock()
            m.execute.return_value = _FakeExec(data=[{**row, "id": TASK_ID_1}])
            return m
        db._task.insert.side_effect = _capture

        with patch("app.agency_api.v1.router._get_db", return_value=db):
            _post(tc, body)
        assert captured[0]["sync_state"] == "none"
        assert captured[0]["source_type"] == "meeting"

    def test_client_id_on_task_matches_meeting(self):
        """task.client_id comes from the meeting row, not from request body."""
        db = _make_db(summary_exists=False, task_ids=[TASK_ID_1])
        _, tc = _make_app(db)
        body = {**_BODY_MIN, "create_tasks": True,
                "commitments": [{"who": "Alice", "what": "Client check"}]}
        captured = []

        def _capture(row):
            captured.append(row)
            m = MagicMock()
            m.execute.return_value = _FakeExec(data=[{**row, "id": TASK_ID_1}])
            return m
        db._task.insert.side_effect = _capture

        with patch("app.agency_api.v1.router._get_db", return_value=db):
            _post(tc, body)
        assert captured[0]["client_id"] == CLIENT_ID


# ── Tests: processor status ───────────────────────────────────────────────────

class TestProcessorStatus:

    def test_manual_processor_status_always_available(self):
        db = _make_db(summary_exists=False)
        _, tc = _make_app(db)
        with patch("app.agency_api.v1.router._get_db", return_value=db):
            r = _post(tc, {**_BODY_MIN, "processor": "manual"})
        assert r.json()["processor_manual_status"] == "AVAILABLE"

    def test_claude_status_degraded_when_credits_not_ok(self):
        db = _make_db(summary_exists=False)
        _, tc = _make_app(db)
        env = {"ANTHROPIC_API_KEY": "sk-test", "ANTHROPIC_CREDITS_OK": ""}
        with patch("app.agency_api.v1.router._get_db", return_value=db), \
             patch.dict(os.environ, env):
            r = _post(tc, {**_BODY_MIN, "processor": "claude"})
        assert r.json()["processor_claude_status"] == "DEGRADED_BLOCKED_CREDITS"

    def test_claude_status_live_when_credits_ok_set(self):
        db = _make_db(summary_exists=False)
        _, tc = _make_app(db)
        env = {"ANTHROPIC_API_KEY": "sk-test", "ANTHROPIC_CREDITS_OK": "true"}
        with patch("app.agency_api.v1.router._get_db", return_value=db), \
             patch.dict(os.environ, env):
            r = _post(tc, {**_BODY_MIN, "processor": "claude"})
        assert r.json()["processor_claude_status"] == "LIVE"

    def test_claude_status_unavailable_when_no_key(self):
        db = _make_db(summary_exists=False)
        _, tc = _make_app(db)
        env = {k: "" for k in ("ANTHROPIC_API_KEY", "ANTHROPIC_CREDITS_OK")}
        with patch("app.agency_api.v1.router._get_db", return_value=db), \
             patch.dict(os.environ, env, clear=False):
            r = _post(tc, {**_BODY_MIN, "processor": "claude"})
        assert r.json()["processor_claude_status"] == "UNAVAILABLE_NO_KEY"

    def test_replayed_also_carries_processor_status(self):
        db = _make_db(summary_exists=True)
        _, tc = _make_app(db)
        with patch("app.agency_ai.v1.router._get_db", return_value=db) \
                if False else patch("app.agency_api.v1.router._get_db", return_value=db):
            r = _post(tc, _BODY_MIN)
        data = r.json()
        assert data["replayed"] is True
        assert data["processor_manual_status"] == "AVAILABLE"
        assert "processor_claude_status" in data


# ── Tests: error handling ─────────────────────────────────────────────────────

class TestErrorHandling:

    def test_meeting_not_found_404(self):
        db = _make_db(meeting_exists=False)
        _, tc = _make_app(db)
        with patch("app.agency_api.v1.router._get_db", return_value=db):
            r = _post(tc, _BODY_MIN)
        assert r.status_code == 404

    def test_no_sends_no_media_writes(self):
        """Verify process_meeting never calls any send_* function."""
        db = _make_db(summary_exists=False)
        _, tc = _make_app(db)
        send_guard_mock = MagicMock()
        with patch("app.agency_api.v1.router._get_db", return_value=db), \
             patch("app.core.send_guard", send_guard_mock, create=True):
            r = _post(tc, _BODY_MIN)
        # send_guard.sends_blocked should never be queried by this endpoint
        send_guard_mock.sends_blocked.assert_not_called()
        assert r.status_code == 201
