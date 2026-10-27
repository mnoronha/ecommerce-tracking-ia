"""
Tests for Fireflies admin endpoints in agency_api/v1/router.py

Coverage:
  - GET /fireflies/status: adapter success → api_auth=PASS
  - GET /fireflies/status: adapter raises → api_auth=FAIL, no stack trace
  - GET /fireflies/status: authenticated=False → api_auth=FAIL
  - GET /fireflies/mappings: returns list, optional client_id filter
  - POST /fireflies/mappings: client not found → 404
  - POST /fireflies/mappings: valid → 201
  - POST /fireflies/mappings: duplicate → 200 (idempotent)
  - DELETE /fireflies/mappings/{id}: exists → 204
  - DELETE /fireflies/mappings/{id}: missing → 404
  - GET /fireflies/events: returns list, optional status filter
"""
from __future__ import annotations

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

from app.agency_api.v1.router import router
from app.agency_api.v1.auth import AuthContext, SCOPE_READ_ANY, SCOPE_PLATFORM_WRITE

# ── Constants ──────────────────────────────────────────────────────────────────

CLIENT_ID    = "lk-sneakers"
FF_MEETING   = "ff-test-meeting-abc"
MAPPING_ID   = str(uuid.uuid4())

_MAPPING_ROW = {
    "id":                   MAPPING_ID,
    "fireflies_meeting_id": FF_MEETING,
    "client_id":            CLIENT_ID,
    "created_by":           "agency_admin",
    "created_at":           "2026-10-27T10:00:00+00:00",
}

_EVENT_ROW = {
    "id":                   str(uuid.uuid4()),
    "fireflies_meeting_id": FF_MEETING,
    "event_type":           "Transcription completed",
    "client_id":            CLIENT_ID,
    "resolution_method":    "explicit",
    "meeting_id":           None,
    "status":               "received",
    "error":                None,
    "received_at":          "2026-10-27T10:00:00+00:00",
    "processed_at":         None,
}


# ── FakeDB ─────────────────────────────────────────────────────────────────────

class FakeAdminDB:
    def __init__(
        self,
        client_exists: bool = True,
        mapping_exists: bool = False,
        mappings: list | None = None,
        events: list | None = None,
    ):
        self._client_exists = client_exists
        self._mapping_exists = mapping_exists
        self._mappings = mappings or []
        self._events = events or []

        # Stable mocks per table
        self._clients  = MagicMock()
        self._mapping  = MagicMock()
        self._events_t = MagicMock()

        # clients lookup
        if self._client_exists:
            self._clients.select.return_value.eq.return_value.limit.return_value.execute.return_value = SimpleNamespace(data=[{"id": CLIENT_ID}])
        else:
            self._clients.select.return_value.eq.return_value.limit.return_value.execute.return_value = SimpleNamespace(data=[])

        # mapping queries
        self._mapping.select.return_value.eq.return_value.limit.return_value.execute.return_value = (
            SimpleNamespace(data=[_MAPPING_ROW]) if self._mapping_exists else SimpleNamespace(data=[])
        )
        self._mapping.select.return_value.order.return_value.limit.return_value.execute.return_value = SimpleNamespace(data=self._mappings)
        self._mapping.select.return_value.eq.return_value.order.return_value.limit.return_value.execute.return_value = SimpleNamespace(data=self._mappings)
        self._mapping.insert.return_value.execute.return_value = SimpleNamespace(data=[_MAPPING_ROW])
        self._mapping.delete.return_value.eq.return_value.execute.return_value = SimpleNamespace(data=[])

        # events queries
        self._events_t.select.return_value.order.return_value.limit.return_value.execute.return_value = SimpleNamespace(data=self._events)
        self._events_t.select.return_value.eq.return_value.order.return_value.limit.return_value.execute.return_value = SimpleNamespace(data=self._events)

    def table(self, name: str):
        if name == "clients":
            return self._clients
        if name == "core_fireflies_client_mapping":
            return self._mapping
        if name == "core_fireflies_events":
            return self._events_t
        return MagicMock()


def _make_app(db: FakeAdminDB) -> tuple[FastAPI, TestClient]:
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[SCOPE_READ_ANY] = lambda: AuthContext(scope="agency_admin", subject="test")
    app.dependency_overrides[SCOPE_PLATFORM_WRITE] = lambda: AuthContext(scope="agency_admin", subject="test")
    with patch("app.agency_api.v1.router._get_db", return_value=db):
        client = TestClient(app, raise_server_exceptions=False)
    return app, client


# ── Tests: GET /fireflies/status ──────────────────────────────────────────────

def test_status_pass_when_adapter_succeeds():
    db = FakeAdminDB()
    _, tc = _make_app(db)
    auth_info = {
        "authenticated": True,
        "user_id": "u123",
        "name": "Alice",
        "num_transcripts": 10,
        "plan_access": "AVAILABLE",
        "transcript_read": "AVAILABLE",
    }
    with patch("app.agency_api.v1.router._get_db", return_value=db):
        with patch("app.core.fireflies_adapter.validate_auth", return_value=auth_info):
            resp = tc.get("/agency/v1/fireflies/status")

    assert resp.status_code == 200
    body = resp.json()
    assert body["api_auth"] == "PASS"
    assert body["plan_access"] == "AVAILABLE"
    assert body["transcript_read"] == "AVAILABLE"
    assert body["user_id"] == "u123"
    assert body["num_transcripts"] == 10


def test_status_fail_when_adapter_raises():
    db = FakeAdminDB()
    _, tc = _make_app(db)
    with patch("app.agency_api.v1.router._get_db", return_value=db):
        with patch("app.core.fireflies_adapter.validate_auth", side_effect=RuntimeError("Fireflies HTTP 401: ...")):
            resp = tc.get("/agency/v1/fireflies/status")

    assert resp.status_code == 200
    body = resp.json()
    assert body["api_auth"] == "FAIL"
    assert body["plan_access"] == "UNKNOWN"
    assert "401" not in str(body)  # stack trace must not leak into response


def test_status_fail_when_not_authenticated():
    db = FakeAdminDB()
    _, tc = _make_app(db)
    auth_info = {"authenticated": False, "plan_access": "UNAVAILABLE", "transcript_read": "UNAVAILABLE"}
    with patch("app.agency_api.v1.router._get_db", return_value=db):
        with patch("app.core.fireflies_adapter.validate_auth", return_value=auth_info):
            resp = tc.get("/agency/v1/fireflies/status")

    assert resp.status_code == 200
    assert resp.json()["api_auth"] == "FAIL"


# ── Tests: GET /fireflies/mappings ────────────────────────────────────────────

def test_list_mappings_returns_list():
    db = FakeAdminDB(mappings=[_MAPPING_ROW])
    _, tc = _make_app(db)
    with patch("app.agency_api.v1.router._get_db", return_value=db):
        resp = tc.get("/agency/v1/fireflies/mappings")

    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    assert data[0]["fireflies_meeting_id"] == FF_MEETING
    assert data[0]["client_id"] == CLIENT_ID


def test_list_mappings_empty():
    db = FakeAdminDB(mappings=[])
    _, tc = _make_app(db)
    with patch("app.agency_api.v1.router._get_db", return_value=db):
        resp = tc.get("/agency/v1/fireflies/mappings")

    assert resp.status_code == 200
    assert resp.json() == []


# ── Tests: POST /fireflies/mappings ───────────────────────────────────────────

def test_create_mapping_client_not_found_returns_404():
    db = FakeAdminDB(client_exists=False)
    _, tc = _make_app(db)
    with patch("app.agency_api.v1.router._get_db", return_value=db):
        resp = tc.post("/agency/v1/fireflies/mappings", json={
            "fireflies_meeting_id": FF_MEETING,
            "client_id": "nonexistent",
        })

    assert resp.status_code == 404


def test_create_mapping_valid_returns_201():
    db = FakeAdminDB(client_exists=True, mapping_exists=False)
    _, tc = _make_app(db)
    with patch("app.agency_api.v1.router._get_db", return_value=db):
        resp = tc.post("/agency/v1/fireflies/mappings", json={
            "fireflies_meeting_id": FF_MEETING,
            "client_id": CLIENT_ID,
        })

    assert resp.status_code == 201
    body = resp.json()
    assert body["fireflies_meeting_id"] == FF_MEETING
    assert body["client_id"] == CLIENT_ID


def test_create_mapping_duplicate_returns_200():
    db = FakeAdminDB(client_exists=True, mapping_exists=True)
    _, tc = _make_app(db)
    with patch("app.agency_api.v1.router._get_db", return_value=db):
        resp = tc.post("/agency/v1/fireflies/mappings", json={
            "fireflies_meeting_id": FF_MEETING,
            "client_id": CLIENT_ID,
        })

    assert resp.status_code == 200


# ── Tests: DELETE /fireflies/mappings/{id} ────────────────────────────────────

def test_delete_mapping_existing_returns_204():
    db = FakeAdminDB(mapping_exists=True)
    _, tc = _make_app(db)
    with patch("app.agency_api.v1.router._get_db", return_value=db):
        resp = tc.delete(f"/agency/v1/fireflies/mappings/{FF_MEETING}")

    assert resp.status_code == 204


def test_delete_mapping_missing_returns_404():
    db = FakeAdminDB(mapping_exists=False)
    _, tc = _make_app(db)
    with patch("app.agency_api.v1.router._get_db", return_value=db):
        resp = tc.delete(f"/agency/v1/fireflies/mappings/{FF_MEETING}")

    assert resp.status_code == 404


# ── Tests: GET /fireflies/events ──────────────────────────────────────────────

def test_list_events_returns_list():
    db = FakeAdminDB(events=[_EVENT_ROW])
    _, tc = _make_app(db)
    with patch("app.agency_api.v1.router._get_db", return_value=db):
        resp = tc.get("/agency/v1/fireflies/events")

    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    assert data[0]["fireflies_meeting_id"] == FF_MEETING
    assert data[0]["status"] == "received"


def test_list_events_empty():
    db = FakeAdminDB(events=[])
    _, tc = _make_app(db)
    with patch("app.agency_api.v1.router._get_db", return_value=db):
        resp = tc.get("/agency/v1/fireflies/events")

    assert resp.status_code == 200
    assert resp.json() == []
