"""
Action events + outcomes + learning candidates tests.

Covers:
- POST /action-events (real insert)
- GET /recommendations/{recommendation_id}/action-events
- POST /outcomes (real insert + idempotency replay)
- GET /action-events/{action_event_id}/outcomes
- POST /learning-candidates (real insert + idempotency replay)
- Action traceability: action_event_id and task_id propagation
- Legacy rows with null new links remain readable
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
_ACTION_UUID  = "aaaaaaaa-aaaa-aaaa-aaaa-000000000001"
_LC_UUID      = "cccccccc-cccc-cccc-cccc-000000000001"
_TASK_UUID    = "bbbbbbbb-bbbb-bbbb-bbbb-000000000001"
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
    "id":                     _OUTCOME_UUID,
    "action_event_id":        _EVT_UUID,
    "recommendation_id":      _REC_UUID,
    "client_id":              None,
    "evaluation_window_days": 7,
    "measurement_period":     {"start": "2026-01-15", "end": "2026-01-22"},
    "metric_refs":            ["revenue", "roas"],
    "before_state":           {"roas": 3.5},
    "after_state":            {"roas": 4.1},
    "delta":                  {"roas": 0.6},
    "status":                 "CONFIRMED",
    "measured_at":            _TS,
    "created_at":             _TS,
    "created_by":             "human",
    "schema_version":         "1.1",
}

_LC_ROW = {
    "id":                       _LC_UUID,
    "statement":                "Higher ROAS when pausing bottom 10% creatives weekly",
    "scope":                    "CLIENT_ONLY",
    "evidence_level":           "OBSERVATION",
    "evidence_refs":            [],
    "context":                  {},
    "client_id":                None,
    "visibility_scope":         "AGENCY_ONLY",
    "source_action_id":         _ACTION_UUID,
    "source_recommendation_id": _REC_UUID,
    "outcome_id":               _OUTCOME_UUID,
    "status":                   "PENDING",
    "pattern":                  None,
    "observed_outcome":         {},
    "created_at":               _TS,
}

_ACTION_ROW = {
    "id":                      _ACTION_UUID,
    "client_id":               "00000000-0000-0000-0000-000000000001",
    "recommendation_id":       _REC_UUID,
    "diagnosis_id":            None,
    "alert_id":                None,
    "action_type":             "MANUAL_EXTERNAL_ACTION",
    "platform":                "meta",
    "entity_type":             "ad",
    "entity_id":               "ad_001",
    "status":                  "PLANNED",
    "executed_by":             None,
    "executed_at":             None,
    "evaluation_window_days":  14,
    "before_state":            {},
    "after_state":             {},
    "reason":                  None,
    "provenance":              {},
    "requires_external_write": False,
    "change_log_id":           None,
    "action_event_id":         _EVT_UUID,
    "task_id":                 _TASK_UUID,
    "created_at":              _TS,
}

_REC_ROW = {
    "id":           _REC_UUID,
    "client_id":    "00000000-0000-0000-0000-000000000001",
    "diagnosis_id": None,
    "alert_id":     None,
    "status":       "APPROVED",
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


def _fresh_insert_db(row: dict):
    """First execute (idempotency SELECT) returns empty; second (INSERT) returns row."""
    m = MagicMock()
    tbl = MagicMock()
    for method in ("select", "eq", "in_", "order", "limit", "is_",
                    "insert", "update", "neq", "gte", "maybe_single"):
        getattr(tbl, method).return_value = tbl
    tbl.execute.side_effect = [
        MagicMock(data=[]),    # idempotency SELECT → no existing row
        MagicMock(data=[row]), # INSERT → new row
    ]
    m.table.return_value = tbl
    return m


def _multi_table_db(table_map: dict):
    """Return different data per table name. Unregistered tables return empty."""
    m = MagicMock()

    def _make_tbl(data):
        tbl = MagicMock()
        for method in ("select", "eq", "in_", "order", "limit", "is_",
                        "insert", "update", "neq", "gte", "maybe_single"):
            getattr(tbl, method).return_value = tbl
        tbl.execute.return_value = MagicMock(data=data)
        return tbl

    def _table_side(name):
        row = table_map.get(name)
        return _make_tbl([row] if row else [])

    m.table.side_effect = _table_side
    return m


def _app():
    app = FastAPI()
    app.include_router(agency_router)
    app.dependency_overrides[SCOPE_READ_ANY]       = lambda: _AUTH_ADMIN
    app.dependency_overrides[SCOPE_WRITE_ANY]      = lambda: _AUTH_ADMIN
    app.dependency_overrides[SCOPE_PLATFORM_WRITE] = lambda: _AUTH_ADMIN
    app.dependency_overrides[SCOPE_HERMES_WRITE]   = lambda: _AUTH_HERMES
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
        "action_event_id":        _EVT_UUID,
        "evaluation_window_days": 7,
        "measurement_period":     {"start": "2026-01-15", "end": "2026-01-22"},
        "metric_refs":            ["revenue", "roas"],
        "before_state":           {"roas": 3.5},
        "after_state":            {"roas": 4.1},
        "delta":                  {"roas": 0.6},
        "status":                 "CONFIRMED",
    }

    def test_creates_outcome(self):
        with patch("app.agency_api.v1.router._get_db", return_value=_fresh_insert_db(_OUTCOME_ROW)):
            with TestClient(_app()) as c:
                resp = c.post("/agency/v1/outcomes", json=self._BODY)
        assert resp.status_code == 201
        data = resp.json()
        assert data["id"] == _OUTCOME_UUID
        assert data["status"] == "CONFIRMED"
        assert data["delta"] == {"roas": 0.6}
        assert data["reused"] is False

    def test_creates_outcome_without_action_event_id(self):
        # action_event_id is optional — no idempotency check, straight INSERT
        body = {k: v for k, v in self._BODY.items() if k != "action_event_id"}
        # only one execute call (INSERT) when action_event_id is absent
        with patch("app.agency_api.v1.router._get_db", return_value=_single_db(_OUTCOME_ROW)):
            with TestClient(_app()) as c:
                resp = c.post("/agency/v1/outcomes", json=body)
        assert resp.status_code == 201
        assert resp.json()["reused"] is False

    def test_idempotent_replay_returns_existing(self):
        # _single_db: first execute (idempotency SELECT) returns existing row → reused
        with patch("app.agency_api.v1.router._get_db", return_value=_single_db(_OUTCOME_ROW)):
            with TestClient(_app()) as c:
                resp = c.post("/agency/v1/outcomes", json=self._BODY)
        assert resp.status_code == 201
        data = resp.json()
        assert data["id"] == _OUTCOME_UUID
        assert data["reused"] is True

    def test_different_window_is_separate_outcome(self):
        different_body = {**self._BODY, "evaluation_window_days": 30}
        with patch("app.agency_api.v1.router._get_db", return_value=_fresh_insert_db(_OUTCOME_ROW)):
            with TestClient(_app()) as c:
                resp = c.post("/agency/v1/outcomes", json=different_body)
        assert resp.status_code == 201
        assert resp.json()["reused"] is False

    def test_invalid_status_rejected(self):
        body = {**self._BODY, "status": "MAYBE"}
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

    def test_legacy_outcome_without_evaluation_window_readable(self):
        legacy_row = {**_OUTCOME_ROW, "evaluation_window_days": None}
        with patch("app.agency_api.v1.router._get_db", return_value=_single_db(legacy_row)):
            with TestClient(_app()) as c:
                resp = c.get(f"/agency/v1/action-events/{_EVT_UUID}/outcomes")
        assert resp.status_code == 200
        assert resp.json()[0]["evaluation_window_days"] is None


# ── Learning candidates ────────────────────────────────────────────────────────

class TestCreateLearningCandidate:
    _BODY = {
        "statement":        "Higher ROAS when pausing bottom 10% creatives weekly",
        "scope":            "CLIENT_ONLY",
        "evidence_level":   "OBSERVATION",
        "source_action_id": _ACTION_UUID,
        "outcome_id":       _OUTCOME_UUID,
    }

    def test_creates_learning_candidate(self):
        with patch("app.agency_api.v1.router._get_db", return_value=_fresh_insert_db(_LC_ROW)):
            with TestClient(_app()) as c:
                resp = c.post("/agency/v1/learning-candidates", json=self._BODY)
        assert resp.status_code == 201
        data = resp.json()
        assert data["id"] == _LC_UUID
        assert data["source_action_id"] == _ACTION_UUID
        assert data["outcome_id"] == _OUTCOME_UUID
        assert data["reused"] is False

    def test_idempotent_replay_same_source_action_scope(self):
        # _single_db: first execute returns existing → reused=True
        with patch("app.agency_api.v1.router._get_db", return_value=_single_db(_LC_ROW)):
            with TestClient(_app()) as c:
                resp = c.post("/agency/v1/learning-candidates", json=self._BODY)
        assert resp.status_code == 201
        data = resp.json()
        assert data["id"] == _LC_UUID
        assert data["reused"] is True

    def test_idempotent_replay_same_source_rec_scope(self):
        body = {
            "statement":                "Observation from recommendation",
            "scope":                    "AGENCY_WIDE_CANDIDATE",
            "evidence_level":           "OBSERVATION",
            "source_recommendation_id": _REC_UUID,
        }
        lc_row = {
            **_LC_ROW,
            "source_action_id":         None,
            "source_recommendation_id": _REC_UUID,
            "scope":                    "AGENCY_WIDE_CANDIDATE",
        }
        with patch("app.agency_api.v1.router._get_db", return_value=_single_db(lc_row)):
            with TestClient(_app()) as c:
                resp = c.post("/agency/v1/learning-candidates", json=body)
        assert resp.status_code == 201
        assert resp.json()["reused"] is True

    def test_distinct_scopes_stay_distinct(self):
        body = {**self._BODY, "scope": "AGENCY_WIDE_CANDIDATE"}
        with patch("app.agency_api.v1.router._get_db", return_value=_fresh_insert_db(_LC_ROW)):
            with TestClient(_app()) as c:
                resp = c.post("/agency/v1/learning-candidates", json=body)
        assert resp.status_code == 201
        assert resp.json()["reused"] is False

    def test_no_source_key_creates_fresh(self):
        body = {
            "statement":      "Generic observation",
            "scope":          "CLIENT_ONLY",
            "evidence_level": "OBSERVATION",
        }
        # No source_action_id → skip idempotency check → single execute (INSERT)
        with patch("app.agency_api.v1.router._get_db", return_value=_single_db(_LC_ROW)):
            with TestClient(_app()) as c:
                resp = c.post("/agency/v1/learning-candidates", json=body)
        assert resp.status_code == 201
        assert resp.json()["reused"] is False

    def test_legacy_lc_without_outcome_id_readable(self):
        legacy_row = {**_LC_ROW, "outcome_id": None}
        from app.agency_api.v1.router import _learning_candidate_row_to_out
        out = _learning_candidate_row_to_out(legacy_row)
        assert out.outcome_id is None
        assert out.reused is False


# ── Action traceability ────────────────────────────────────────────────────────

class TestActionTraceability:
    _CREATE_BODY = {
        "action_type":            "MANUAL_EXTERNAL_ACTION",
        "platform":               "meta",
        "entity_type":            "ad",
        "entity_id":              "ad_001",
        "evaluation_window_days": 14,
        "action_event_id":        _EVT_UUID,
        "task_id":                _TASK_UUID,
    }

    def test_action_carries_action_event_id_and_task_id(self):
        db = _multi_table_db({
            "core_recommendations": _REC_ROW,
            "core_actions":         _ACTION_ROW,
        })
        with patch("app.agency_api.v1.router._get_db", return_value=db):
            with TestClient(_app()) as c:
                resp = c.post(
                    f"/agency/v1/recommendations/{_REC_UUID}/actions",
                    json=self._CREATE_BODY,
                )
        assert resp.status_code == 201
        data = resp.json()
        assert data["action_event_id"] == _EVT_UUID
        assert data["task_id"] == _TASK_UUID

    def test_action_without_traceability_fields_readable(self):
        legacy_row = {**_ACTION_ROW, "action_event_id": None, "task_id": None}
        db = _multi_table_db({
            "core_recommendations": _REC_ROW,
            "core_actions":         legacy_row,
        })
        with patch("app.agency_api.v1.router._get_db", return_value=db):
            with TestClient(_app()) as c:
                resp = c.post(
                    f"/agency/v1/recommendations/{_REC_UUID}/actions",
                    json=self._CREATE_BODY,
                )
        assert resp.status_code == 201
        data = resp.json()
        assert data["action_event_id"] is None
        assert data["task_id"] is None
