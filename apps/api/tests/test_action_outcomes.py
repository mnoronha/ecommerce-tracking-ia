"""
Action events + outcomes tests.

Covers:
- POST /action-events (real insert)
- GET /recommendations/{recommendation_id}/action-events
- POST /outcomes (real insert)
- GET /action-events/{action_event_id}/outcomes
"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient
from unittest.mock import MagicMock, patch

from app.agency_api.v1.auth import (
    SCOPE_HERMES_WRITE, SCOPE_PLATFORM_WRITE, SCOPE_READ_ANY, SCOPE_WRITE_ANY, AuthContext,
)
from app.agency_api.v1.router import router as agency_router

_AUTH_ADMIN  = AuthContext(scope="agency_admin")
_AUTH_HERMES = AuthContext(scope="hermes_service")

_REC_UUID     = "dddddddd-dddd-dddd-dddd-000000000001"
_EVT_UUID     = "eeeeeeee-eeee-eeee-eeee-000000000001"
_OUTCOME_UUID = "ffffffff-ffff-ffff-ffff-000000000001"
_TS           = "2026-01-15T10:00:00+00:00"

_EVENT_ROW = {
    "id":                _EVT_UUID,
    "recommendation_id": _REC_UUID,
    "event_type":        "APPROVED",
    "actor":             "maico",
    "occurred_at":       _TS,
    "note":              "Approved in weekly review",
    "change_id":         None,
    "created_at":        _TS,
}

_OUTCOME_ROW = {
    "id":                 _OUTCOME_UUID,
    "action_event_id":    _EVT_UUID,
    "recommendation_id":  _REC_UUID,
    "client_id":          None,
    "measurement_period": {"start": "2026-01-15", "end": "2026-01-22"},
    "metric_refs":        ["revenue", "roas"],
    "before_state":       {"roas": 3.5},
    "after_state":        {"roas": 4.1},
    "delta":              {"roas": 0.6},
    "status":             "CONFIRMED",
    "measured_at":        _TS,
    "created_at":         _TS,
    "created_by":         "human",
    "schema_version":     "1.1",
}


def _empty_db():
    m = MagicMock()
    tbl = MagicMock()
    for method in ("select", "eq", "in_", "order", "limit", "is_",
                    "insert", "update", "neq", "gte", "maybe_single"):
        getattr(tbl, method).return_value = tbl
    tbl.execute.return_value = MagicMock(data=[])
    m.table.return_value = tbl
    return m


def _single_db(row: dict):
    m = MagicMock()
    tbl = MagicMock()
    for method in ("select", "eq", "in_", "order", "limit", "is_",
                    "insert", "update", "neq", "gte", "maybe_single"):
        getattr(tbl, method).return_value = tbl
    tbl.execute.return_value = MagicMock(data=[row])
    m.table.return_value = tbl
    return m


def _app():
    app = FastAPI()
    app.include_router(agency_router)
    app.dependency_overrides[SCOPE_READ_ANY]      = lambda: _AUTH_ADMIN
    app.dependency_overrides[SCOPE_WRITE_ANY]     = lambda: _AUTH_ADMIN
    app.dependency_overrides[SCOPE_PLATFORM_WRITE] = lambda: _AUTH_ADMIN
    app.dependency_overrides[SCOPE_HERMES_WRITE]  = lambda: _AUTH_HERMES
    return app


# ── Action events ─────────────────────────────────────────────────────────────

class TestCreateActionEvent:
    _BODY = {
        "recommendation_id": _REC_UUID,
        "event_type":        "APPROVED",
        "actor":             "maico",
        "note":              "Approved in weekly review",
    }

    def test_creates_event(self):
        with patch("app.agency_api.v1.router._get_db", return_value=_single_db(_EVENT_ROW)):
            with TestClient(_app()) as c:
                resp = c.post("/agency/v1/action-events", json=self._BODY)
        assert resp.status_code == 201
        data = resp.json()
        assert data["id"] == _EVT_UUID
        assert "evt_stub" not in data["id"]
        assert data["event_type"] == "APPROVED"

    def test_invalid_event_type_rejected(self):
        body = {**self._BODY, "event_type": "INVENTED"}
        with patch("app.agency_api.v1.router._get_db", return_value=_empty_db()):
            with TestClient(_app()) as c:
                resp = c.post("/agency/v1/action-events", json=body)
        assert resp.status_code == 422

    def test_missing_recommendation_id_rejected(self):
        body = {k: v for k, v in self._BODY.items() if k != "recommendation_id"}
        with patch("app.agency_api.v1.router._get_db", return_value=_empty_db()):
            with TestClient(_app()) as c:
                resp = c.post("/agency/v1/action-events", json=body)
        assert resp.status_code == 422


class TestGetRecommendationActionEvents:
    def test_empty_for_invalid_uuid(self):
        with patch("app.agency_api.v1.router._get_db", return_value=_empty_db()):
            with TestClient(_app()) as c:
                resp = c.get("/agency/v1/recommendations/rec_stub_01/action-events")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_empty_when_no_events(self):
        with patch("app.agency_api.v1.router._get_db", return_value=_empty_db()):
            with TestClient(_app()) as c:
                resp = c.get(f"/agency/v1/recommendations/{_REC_UUID}/action-events")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_returns_events(self):
        with patch("app.agency_api.v1.router._get_db", return_value=_single_db(_EVENT_ROW)):
            with TestClient(_app()) as c:
                resp = c.get(f"/agency/v1/recommendations/{_REC_UUID}/action-events")
        assert resp.status_code == 200
        items = resp.json()
        assert len(items) == 1
        assert items[0]["id"] == _EVT_UUID
        assert items[0]["actor"] == "maico"


# ── Outcomes ──────────────────────────────────────────────────────────────────

class TestCreateOutcome:
    _BODY = {
        "action_event_id":    _EVT_UUID,
        "measurement_period": {"start": "2026-01-15", "end": "2026-01-22"},
        "metric_refs":        ["revenue", "roas"],
        "before_state":       {"roas": 3.5},
        "after_state":        {"roas": 4.1},
        "delta":              {"roas": 0.6},
        "status":             "CONFIRMED",
    }

    def test_creates_outcome(self):
        with patch("app.agency_api.v1.router._get_db", return_value=_single_db(_OUTCOME_ROW)):
            with TestClient(_app()) as c:
                resp = c.post("/agency/v1/outcomes", json=self._BODY)
        assert resp.status_code == 201
        data = resp.json()
        assert data["id"] == _OUTCOME_UUID
        assert data["status"] == "CONFIRMED"
        assert data["delta"] == {"roas": 0.6}

    def test_invalid_status_rejected(self):
        body = {**self._BODY, "status": "MAYBE"}
        with patch("app.agency_api.v1.router._get_db", return_value=_empty_db()):
            with TestClient(_app()) as c:
                resp = c.post("/agency/v1/outcomes", json=body)
        assert resp.status_code == 422

    def test_missing_action_event_id_rejected(self):
        body = {k: v for k, v in self._BODY.items() if k != "action_event_id"}
        with patch("app.agency_api.v1.router._get_db", return_value=_empty_db()):
            with TestClient(_app()) as c:
                resp = c.post("/agency/v1/outcomes", json=body)
        assert resp.status_code == 422


class TestGetActionEventOutcomes:
    def test_empty_for_invalid_uuid(self):
        with patch("app.agency_api.v1.router._get_db", return_value=_empty_db()):
            with TestClient(_app()) as c:
                resp = c.get("/agency/v1/action-events/evt_stub_01/outcomes")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_empty_when_no_outcomes(self):
        with patch("app.agency_api.v1.router._get_db", return_value=_empty_db()):
            with TestClient(_app()) as c:
                resp = c.get(f"/agency/v1/action-events/{_EVT_UUID}/outcomes")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_returns_outcomes(self):
        with patch("app.agency_api.v1.router._get_db", return_value=_single_db(_OUTCOME_ROW)):
            with TestClient(_app()) as c:
                resp = c.get(f"/agency/v1/action-events/{_EVT_UUID}/outcomes")
        assert resp.status_code == 200
        items = resp.json()
        assert len(items) == 1
        assert items[0]["id"] == _OUTCOME_UUID
        assert items[0]["metric_refs"] == ["revenue", "roas"]
