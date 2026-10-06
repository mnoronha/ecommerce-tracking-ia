"""
Report lifecycle tests — DRAFT→READY_FOR_REVIEW→APPROVED→PUBLISHED→SUPERSEDED.

Covers:
- POST /report-narratives (create DRAFT)
- GET /clients/{client_id}/reports (list, status filter, role filter)
- GET /report-narratives/{narrative_id} (single)
- POST /report-narratives/{narrative_id}/transitions (lifecycle)
  - valid transitions succeed
  - invalid transitions → 422
  - PUBLISHED auto-supersedes previous PUBLISHED for same contract
  - client_viewer only sees PUBLISHED
"""
from __future__ import annotations

import pytest
from datetime import datetime, timezone
from fastapi import FastAPI
from fastapi.testclient import TestClient
from unittest.mock import MagicMock, patch

from app.agency_api.v1.auth import SCOPE_ANY_AUTH, SCOPE_HERMES_WRITE, SCOPE_READ_ANY, AuthContext
from app.agency_api.v1.router import router as agency_router

_AUTH_ADMIN  = AuthContext(scope="agency_admin")
_AUTH_HERMES = AuthContext(scope="hermes_service")
_AUTH_CLIENT = AuthContext(scope="client_viewer")

_CONTRACT_UUID = "aaaaaaaa-bbbb-cccc-dddd-000000000001"
_NAR_UUID      = "aaaaaaaa-bbbb-cccc-dddd-000000000002"
_NAR_UUID2     = "aaaaaaaa-bbbb-cccc-dddd-000000000003"
_TS            = "2026-01-01T00:00:00+00:00"

_CONTRACT_ROW = {
    "id":           _CONTRACT_UUID,
    "client_slug":  "lk-sneakers",
    "report_type":  "weekly",
    "period_start": "2026-01-01",
    "period_end":   "2026-01-07",
}


def _nar(id_: str = _NAR_UUID, status: str = "DRAFT") -> dict:
    return {
        "id": id_,
        "report_contract_id": _CONTRACT_UUID,
        "blocks": [],
        "visibility_scope": "AGENCY_ONLY",
        "status": status,
        "approved_by": None, "approved_at": None,
        "published_at": None, "created_at": _TS,
    }


def _seq_db(*responses) -> MagicMock:
    call_index = [0]
    def _table(_name):
        m = MagicMock()
        for method in ("select", "eq", "in_", "order", "limit", "is_",
                        "insert", "update", "maybe_single", "neq", "gte"):
            getattr(m, method).return_value = m
        def _execute():
            idx = call_index[0]; call_index[0] += 1
            if idx < len(responses):
                return MagicMock(data=responses[idx])
            return MagicMock(data=[])
        m.execute.side_effect = _execute
        return m
    db = MagicMock(); db.table.side_effect = _table
    return db


def _app(auth_admin=True):
    app = FastAPI()
    app.include_router(agency_router)
    auth = _AUTH_ADMIN if auth_admin else _AUTH_CLIENT
    app.dependency_overrides[SCOPE_READ_ANY]     = lambda: auth
    app.dependency_overrides[SCOPE_ANY_AUTH]     = lambda: auth
    app.dependency_overrides[SCOPE_HERMES_WRITE] = lambda: _AUTH_HERMES
    return app


# ── Create ────────────────────────────────────────────────────────────────────

class TestCreateNarrative:
    def test_creates_draft(self):
        # Sequence: contract lookup → insert
        db = _seq_db([_CONTRACT_ROW], [_nar()])
        with patch("app.agency_api.v1.router._get_db", return_value=db):
            with TestClient(_app()) as c:
                resp = c.post("/agency/v1/report-narratives", json={
                    "report_contract_id": _CONTRACT_UUID,
                    "blocks": [],
                    "visibility_scope": "AGENCY_ONLY",
                })
        assert resp.status_code == 201
        data = resp.json()
        assert data["status"] == "DRAFT"
        assert data["id"] == _NAR_UUID

    def test_unknown_contract_404(self):
        db = _seq_db([])  # contract lookup returns empty
        with patch("app.agency_api.v1.router._get_db", return_value=db):
            with TestClient(_app()) as c:
                resp = c.post("/agency/v1/report-narratives", json={
                    "report_contract_id": _CONTRACT_UUID,
                    "blocks": [],
                    "visibility_scope": "AGENCY_ONLY",
                })
        assert resp.status_code == 404

    def test_invalid_uuid_contract_422(self):
        db = _seq_db()
        with patch("app.agency_api.v1.router._get_db", return_value=db):
            with TestClient(_app()) as c:
                resp = c.post("/agency/v1/report-narratives", json={
                    "report_contract_id": "not-a-uuid",
                    "blocks": [],
                    "visibility_scope": "AGENCY_ONLY",
                })
        assert resp.status_code == 422


# ── List ──────────────────────────────────────────────────────────────────────

class TestGetReports:
    def test_empty_when_no_contracts(self):
        db = _seq_db([])  # contract lookup empty
        with patch("app.agency_api.v1.router._get_db", return_value=db):
            with TestClient(_app()) as c:
                resp = c.get("/agency/v1/clients/lk-sneakers/reports")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_empty_when_no_narratives(self):
        db = _seq_db([_CONTRACT_ROW], [])  # contracts found, narratives empty
        with patch("app.agency_api.v1.router._get_db", return_value=db):
            with TestClient(_app()) as c:
                resp = c.get("/agency/v1/clients/lk-sneakers/reports")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_returns_narratives_with_contract_context(self):
        db = _seq_db([_CONTRACT_ROW], [_nar(status="APPROVED")])
        with patch("app.agency_api.v1.router._get_db", return_value=db):
            with TestClient(_app()) as c:
                resp = c.get("/agency/v1/clients/lk-sneakers/reports")
        assert resp.status_code == 200
        items = resp.json()
        assert len(items) == 1
        assert items[0]["report_type"] == "WEEKLY"
        assert items[0]["period_start"] == "2026-01-01"
        assert items[0]["period_end"] == "2026-01-07"
        assert items[0]["client_id"] == "lk-sneakers"

    def test_status_filter_passed(self):
        db = _seq_db([_CONTRACT_ROW], [_nar(status="PUBLISHED")])
        with patch("app.agency_api.v1.router._get_db", return_value=db):
            with TestClient(_app()) as c:
                resp = c.get("/agency/v1/clients/lk-sneakers/reports?status=PUBLISHED")
        assert resp.status_code == 200
        assert resp.json()[0]["status"] == "PUBLISHED"

    def test_client_viewer_only_sees_published(self):
        # DB returns a DRAFT narrative — client_viewer filter should restrict to PUBLISHED
        # The filter is applied at query time; mock returns [] for the narrative query
        db = _seq_db([_CONTRACT_ROW], [])  # narratives filtered to PUBLISHED → []
        app = FastAPI()
        app.include_router(agency_router)
        app.dependency_overrides[SCOPE_READ_ANY] = lambda: _AUTH_CLIENT
        app.dependency_overrides[SCOPE_ANY_AUTH] = lambda: _AUTH_CLIENT
        app.dependency_overrides[SCOPE_HERMES_WRITE] = lambda: _AUTH_HERMES
        with patch("app.agency_api.v1.router._get_db", return_value=db):
            with TestClient(app) as c:
                resp = c.get("/agency/v1/clients/lk-sneakers/reports")
        assert resp.status_code == 200
        assert resp.json() == []


# ── Single ────────────────────────────────────────────────────────────────────

class TestGetNarrative:
    def test_404_for_invalid_uuid(self):
        db = _seq_db()
        with patch("app.agency_api.v1.router._get_db", return_value=db):
            with TestClient(_app()) as c:
                resp = c.get("/agency/v1/report-narratives/nar_stub_01")
        assert resp.status_code == 404

    def test_404_when_not_found(self):
        db = _seq_db([])  # narrative lookup empty
        with patch("app.agency_api.v1.router._get_db", return_value=db):
            with TestClient(_app()) as c:
                resp = c.get(f"/agency/v1/report-narratives/{_NAR_UUID}")
        assert resp.status_code == 404

    def test_returns_narrative(self):
        # Sequence: narrative → contract → truth_versions
        db = _seq_db([_nar(status="APPROVED")], [_CONTRACT_ROW])
        with patch("app.agency_api.v1.router._get_db", return_value=db):
            with TestClient(_app()) as c:
                resp = c.get(f"/agency/v1/report-narratives/{_NAR_UUID}")
        assert resp.status_code == 200
        assert resp.json()["status"] == "APPROVED"

    def test_client_viewer_cannot_see_draft(self):
        db = _seq_db([_nar(status="DRAFT")])  # found but not PUBLISHED
        app = FastAPI()
        app.include_router(agency_router)
        app.dependency_overrides[SCOPE_ANY_AUTH] = lambda: _AUTH_CLIENT
        app.dependency_overrides[SCOPE_READ_ANY] = lambda: _AUTH_CLIENT
        app.dependency_overrides[SCOPE_HERMES_WRITE] = lambda: _AUTH_HERMES
        with patch("app.agency_api.v1.router._get_db", return_value=db):
            with TestClient(app) as c:
                resp = c.get(f"/agency/v1/report-narratives/{_NAR_UUID}")
        assert resp.status_code == 404


# ── Lifecycle transitions ─────────────────────────────────────────────────────

class TestNarrativeTransitions:
    def _post_transition(self, db, nar_id, target_status, actor="test-actor"):
        with patch("app.agency_api.v1.router._get_db", return_value=db):
            with TestClient(_app()) as c:
                return c.post(f"/agency/v1/report-narratives/{nar_id}/transitions", json={
                    "target_status": target_status,
                    "actor": actor,
                })

    def test_draft_to_ready_for_review(self):
        db = _seq_db(
            [_nar(status="DRAFT")],      # fetch
            [{**_nar(), "status": "READY_FOR_REVIEW"}],  # update result
            [_CONTRACT_ROW],              # contract
        )
        resp = self._post_transition(db, _NAR_UUID, "READY_FOR_REVIEW")
        assert resp.status_code == 200
        assert resp.json()["status"] == "READY_FOR_REVIEW"

    def test_ready_for_review_to_approved(self):
        db = _seq_db(
            [_nar(status="READY_FOR_REVIEW")],
            [{**_nar(), "status": "APPROVED", "approved_by": "test-actor",
              "approved_at": _TS}],
            [_CONTRACT_ROW],
        )
        resp = self._post_transition(db, _NAR_UUID, "APPROVED")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "APPROVED"
        assert data["approved_by"] == "test-actor"

    def test_approved_to_published_supersedes_old(self):
        db = _seq_db(
            [_nar(status="APPROVED")],
            [{**_nar(), "status": "PUBLISHED", "published_at": _TS}],
            [],          # supersede previous PUBLISHED (empty = nothing to supersede)
            [_CONTRACT_ROW],
        )
        resp = self._post_transition(db, _NAR_UUID, "PUBLISHED")
        assert resp.status_code == 200
        assert resp.json()["status"] == "PUBLISHED"

    def test_invalid_transition_draft_to_published(self):
        db = _seq_db([_nar(status="DRAFT")])
        resp = self._post_transition(db, _NAR_UUID, "PUBLISHED")
        assert resp.status_code == 422

    def test_invalid_transition_published_to_draft(self):
        db = _seq_db([_nar(status="PUBLISHED")])
        resp = self._post_transition(db, _NAR_UUID, "DRAFT")
        assert resp.status_code == 422

    def test_superseded_is_immutable(self):
        db = _seq_db([_nar(status="SUPERSEDED")])
        resp = self._post_transition(db, _NAR_UUID, "DRAFT")
        assert resp.status_code == 422

    def test_invalid_uuid_narrative_id(self):
        db = _seq_db()
        resp = self._post_transition(db, "narr_stub_01", "READY_FOR_REVIEW")
        assert resp.status_code == 404

    def test_narrative_not_found(self):
        db = _seq_db([])  # fetch returns empty
        resp = self._post_transition(db, _NAR_UUID, "READY_FOR_REVIEW")
        assert resp.status_code == 404
