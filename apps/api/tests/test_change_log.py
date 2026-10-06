"""
Change log tests — GET list and POST create.

Covers:
- GET /clients/{client_id}/changes (real DB read, honest absence)
- POST /changes (real DB insert, returns row with id)
"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient
from unittest.mock import MagicMock, patch

from app.agency_api.v1.auth import SCOPE_HERMES_WRITE, SCOPE_READ_ANY, AuthContext
from app.agency_api.v1.router import router as agency_router

_AUTH_ADMIN  = AuthContext(scope="agency_admin")
_AUTH_HERMES = AuthContext(scope="hermes_service")

_CHANGE_UUID = "cccccccc-cccc-cccc-cccc-000000000001"
_TS          = "2026-01-15T10:00:00+00:00"

_CHANGE_ROW = {
    "id":                  _CHANGE_UUID,
    "client_id":           "lk-sneakers",
    "occurred_at":         _TS,
    "channel":             "meta",
    "platform_account_id": "act_12345",
    "entity_type":         "CAMPAIGN",
    "entity_name_at_time": "Black Friday Campaign",
    "change_type":         "BUDGET",
    "confidence":          "CONFIRMED",
    "source":              "HUMAN",
    "campaign_id":         None,
    "adset_or_adgroup_id": None,
    "ad_id":               None,
    "before_state":        {"budget": 100},
    "after_state":         {"budget": 200},
    "reason":              "Scaling winner",
    "reported_by":         "maico",
    "linked_action_id":    None,
    "external_change_id":  None,
    "match_status":        None,
    "matched_change_id":   None,
    "created_at":          _TS,
}

_CHANGE_BODY = {
    "client_id":           "lk-sneakers",
    "occurred_at":         _TS,
    "channel":             "meta",
    "platform_account_id": "act_12345",
    "entity_type":         "CAMPAIGN",
    "entity_name_at_time": "Black Friday Campaign",
    "change_type":         "BUDGET",
    "confidence":          "CONFIRMED",
    "source":              "HUMAN",
    "reported_by":         "maico",
    "before":              {"budget": 100},
    "after":               {"budget": 200},
    "reason":              "Scaling winner",
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
    app.dependency_overrides[SCOPE_READ_ANY]     = lambda: _AUTH_ADMIN
    app.dependency_overrides[SCOPE_HERMES_WRITE] = lambda: _AUTH_HERMES
    return app


class TestGetChanges:
    def test_empty_list(self):
        with patch("app.agency_api.v1.router._get_db", return_value=_empty_db()):
            with TestClient(_app()) as c:
                resp = c.get("/agency/v1/clients/lk-sneakers/changes")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_returns_rows(self):
        with patch("app.agency_api.v1.router._get_db", return_value=_single_db(_CHANGE_ROW)):
            with TestClient(_app()) as c:
                resp = c.get("/agency/v1/clients/lk-sneakers/changes")
        assert resp.status_code == 200
        items = resp.json()
        assert len(items) == 1
        assert items[0]["id"] == _CHANGE_UUID
        assert items[0]["change_type"] == "BUDGET"
        assert items[0]["source"] == "HUMAN"

    def test_before_after_fields(self):
        with patch("app.agency_api.v1.router._get_db", return_value=_single_db(_CHANGE_ROW)):
            with TestClient(_app()) as c:
                resp = c.get("/agency/v1/clients/lk-sneakers/changes")
        item = resp.json()[0]
        assert item["before"] == {"budget": 100}
        assert item["after"] == {"budget": 200}


class TestCreateChange:
    def test_creates_and_returns_with_id(self):
        with patch("app.agency_api.v1.router._get_db", return_value=_single_db(_CHANGE_ROW)):
            with TestClient(_app()) as c:
                resp = c.post("/agency/v1/changes", json=_CHANGE_BODY)
        assert resp.status_code == 201
        data = resp.json()
        assert data["id"] == _CHANGE_UUID
        assert "chg_stub" not in data["id"]

    def test_invalid_change_type_rejected(self):
        body = {**_CHANGE_BODY, "change_type": "INVALID_TYPE"}
        with patch("app.agency_api.v1.router._get_db", return_value=_empty_db()):
            with TestClient(_app()) as c:
                resp = c.post("/agency/v1/changes", json=body)
        assert resp.status_code == 422

    def test_invalid_source_rejected(self):
        body = {**_CHANGE_BODY, "source": "MAGIC"}
        with patch("app.agency_api.v1.router._get_db", return_value=_empty_db()):
            with TestClient(_app()) as c:
                resp = c.post("/agency/v1/changes", json=body)
        assert resp.status_code == 422

    def test_requires_hermes_write_scope(self):
        app = FastAPI()
        app.include_router(agency_router)
        app.dependency_overrides[SCOPE_READ_ANY] = lambda: _AUTH_ADMIN
        # intentionally NOT overriding SCOPE_HERMES_WRITE
        with patch("app.agency_api.v1.router._get_db", return_value=_empty_db()):
            with TestClient(app) as c:
                resp = c.post("/agency/v1/changes", json=_CHANGE_BODY)
        assert resp.status_code == 401
