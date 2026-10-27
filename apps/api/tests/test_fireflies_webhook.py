"""
Tests for app/routers/fireflies_webhook.py

Coverage:
  - POST /webhooks/fireflies with valid payload → 200, event logged
  - Duplicate webhook (same meetingId + event_type) → 200, idempotent=True
  - Missing meetingId → 400
  - Invalid JSON → 400
  - Secret mismatch when FIREFLIES_WEBHOOK_SECRET set → 403
  - Secret correct when FIREFLIES_WEBHOOK_SECRET set → 200
  - No secret configured → accept all → 200
  - Background task dispatched (check background_tasks.add_task called)
  - Zero client sends (verify no send_guard.sends_blocked calls)
  - _resolve_client returns (client_id, "explicit") when mapping exists
  - _resolve_client returns (None, "unresolved") when no mapping
"""
from __future__ import annotations

import json
import os
import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.routers.fireflies_webhook import router, _resolve_client, _verify_payload

MEETING_ID = "ff-test-meeting-abc123"
EVENT_ID   = str(uuid.uuid4())
CLIENT_ID  = "test-client-lk"


# ── FakeDB ─────────────────────────────────────────────────────────────────────

class FakeFirefliesDB:
    def __init__(self, event_exists=False, mapping_client_id=None):
        self._event_exists = event_exists
        self._mapping_client_id = mapping_client_id

    def table(self, name):
        t = MagicMock()
        if name == "core_fireflies_events":
            if self._event_exists:
                t.select.return_value.eq.return_value.eq.return_value.limit.return_value.execute.return_value = SimpleNamespace(
                    data=[{"id": EVENT_ID, "status": "received"}]
                )
            else:
                t.select.return_value.eq.return_value.eq.return_value.limit.return_value.execute.return_value = SimpleNamespace(
                    data=[]
                )
            t.insert.return_value.execute.return_value = SimpleNamespace(data=[{"id": EVENT_ID}])
            t.update.return_value.eq.return_value.execute.return_value = SimpleNamespace(data=[])
        elif name == "core_fireflies_client_mapping":
            if self._mapping_client_id:
                t.select.return_value.eq.return_value.limit.return_value.execute.return_value = SimpleNamespace(
                    data=[{"client_id": self._mapping_client_id}]
                )
            else:
                t.select.return_value.eq.return_value.limit.return_value.execute.return_value = SimpleNamespace(
                    data=[]
                )
        return t


def _make_app(db) -> tuple[FastAPI, TestClient]:
    app = FastAPI()
    app.include_router(router)
    with patch("app.routers.fireflies_webhook.get_supabase", return_value=db):
        client = TestClient(app, raise_server_exceptions=False)
    return app, client


PAYLOAD = {"meetingId": MEETING_ID, "webhook_type": "Transcription completed"}


# ── Tests: endpoint behavior ───────────────────────────────────────────────────

def test_valid_payload_returns_200_and_logs_event():
    db = FakeFirefliesDB(event_exists=False)
    with patch("app.routers.fireflies_webhook.get_supabase", return_value=db):
        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)
        resp = client.post("/webhooks/fireflies", json=PAYLOAD)

    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["idempotent"] is False
    assert "event_id" in body


def test_duplicate_webhook_returns_idempotent_true():
    db = FakeFirefliesDB(event_exists=True)
    with patch("app.routers.fireflies_webhook.get_supabase", return_value=db):
        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)
        resp = client.post("/webhooks/fireflies", json=PAYLOAD)

    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["idempotent"] is True
    assert body["event_id"] == EVENT_ID


def test_missing_meeting_id_returns_400():
    db = FakeFirefliesDB()
    with patch("app.routers.fireflies_webhook.get_supabase", return_value=db):
        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)
        resp = client.post("/webhooks/fireflies", json={"webhook_type": "Transcription completed"})

    assert resp.status_code == 400
    assert resp.json()["error"] == "missing_meetingId"


def test_invalid_json_returns_400():
    db = FakeFirefliesDB()
    with patch("app.routers.fireflies_webhook.get_supabase", return_value=db):
        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)
        resp = client.post(
            "/webhooks/fireflies",
            content=b"not-json-at-all",
            headers={"Content-Type": "application/json"},
        )

    assert resp.status_code == 400
    assert resp.json()["error"] == "invalid_json"


def test_secret_mismatch_returns_403():
    db = FakeFirefliesDB()
    payload = {**PAYLOAD, "token": "wrong-token"}
    with patch.dict(os.environ, {"FIREFLIES_WEBHOOK_SECRET": "correct-secret"}):
        with patch("app.routers.fireflies_webhook.get_supabase", return_value=db):
            app = FastAPI()
            app.include_router(router)
            client = TestClient(app)
            resp = client.post("/webhooks/fireflies", json=payload)

    assert resp.status_code == 403
    assert resp.json()["error"] == "forbidden"


def test_secret_correct_returns_200():
    db = FakeFirefliesDB(event_exists=False)
    payload = {**PAYLOAD, "token": "correct-secret"}
    with patch.dict(os.environ, {"FIREFLIES_WEBHOOK_SECRET": "correct-secret"}):
        with patch("app.routers.fireflies_webhook.get_supabase", return_value=db):
            app = FastAPI()
            app.include_router(router)
            client = TestClient(app)
            resp = client.post("/webhooks/fireflies", json=payload)

    assert resp.status_code == 200
    assert resp.json()["ok"] is True


def test_no_secret_configured_accepts_all():
    db = FakeFirefliesDB(event_exists=False)
    env = {k: v for k, v in os.environ.items() if k != "FIREFLIES_WEBHOOK_SECRET"}
    with patch.dict(os.environ, env, clear=True):
        with patch("app.routers.fireflies_webhook.get_supabase", return_value=db):
            app = FastAPI()
            app.include_router(router)
            client = TestClient(app)
            # No token in payload — should still be accepted
            resp = client.post("/webhooks/fireflies", json=PAYLOAD)

    assert resp.status_code == 200
    assert resp.json()["ok"] is True


def test_background_task_dispatched():
    """Verify that background_tasks.add_task is called for new events."""
    db = FakeFirefliesDB(event_exists=False)
    with patch("app.routers.fireflies_webhook.get_supabase", return_value=db):
        app = FastAPI()
        app.include_router(router)

        dispatched = []

        # Intercept BackgroundTasks.add_task
        from fastapi import BackgroundTasks
        original_add_task = BackgroundTasks.add_task

        def capturing_add_task(self, func, *args, **kwargs):
            dispatched.append((func, args, kwargs))
            # Don't actually run it — that requires Fireflies API calls
            pass

        with patch.object(BackgroundTasks, "add_task", capturing_add_task):
            client = TestClient(app)
            resp = client.post("/webhooks/fireflies", json=PAYLOAD)

    assert resp.status_code == 200
    assert len(dispatched) == 1
    func, args, kwargs = dispatched[0]
    assert func.__name__ == "_process_event"
    assert args[1] == MEETING_ID  # second arg is fireflies_meeting_id


def test_zero_client_sends():
    """
    Verify the webhook endpoint never calls send_guard.sends_blocked.
    This module does zero ad-platform sends.
    """
    import app.routers.fireflies_webhook as wh_module
    import inspect

    source = inspect.getsource(wh_module)
    assert "sends_blocked" not in source, (
        "fireflies_webhook must not call send_guard.sends_blocked — it does zero ad sends"
    )


# ── Tests: _resolve_client ────────────────────────────────────────────────────

def test_resolve_client_explicit_mapping():
    db = FakeFirefliesDB(mapping_client_id=CLIENT_ID)
    client_id, method = _resolve_client(MEETING_ID, db)
    assert client_id == CLIENT_ID
    assert method == "explicit"


def test_resolve_client_no_mapping_returns_unresolved():
    db = FakeFirefliesDB(mapping_client_id=None)
    client_id, method = _resolve_client(MEETING_ID, db)
    assert client_id is None
    assert method == "unresolved"


# ── Tests: _verify_payload ────────────────────────────────────────────────────

def test_verify_payload_no_secret_always_true():
    with patch.dict(os.environ, {}, clear=True):
        os.environ.pop("FIREFLIES_WEBHOOK_SECRET", None)
        assert _verify_payload({}) is True
        assert _verify_payload({"token": "anything"}) is True


def test_verify_payload_correct_token():
    with patch.dict(os.environ, {"FIREFLIES_WEBHOOK_SECRET": "my-secret"}):
        assert _verify_payload({"token": "my-secret"}) is True


def test_verify_payload_wrong_token():
    with patch.dict(os.environ, {"FIREFLIES_WEBHOOK_SECRET": "my-secret"}):
        assert _verify_payload({"token": "wrong"}) is False


def test_verify_payload_missing_token_field():
    with patch.dict(os.environ, {"FIREFLIES_WEBHOOK_SECRET": "my-secret"}):
        assert _verify_payload({}) is False
