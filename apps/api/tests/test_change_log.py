"""
Change log tests — GET list, GET single, POST create.

Covers:
- GET /clients/{client_id}/changes (list, filters, honest absence)
- GET /clients/{client_id}/media-changes/{change_id} (single record)
- POST /changes (insert, v2 fields, validation)
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
_TASK_UUID   = "dddddddd-dddd-dddd-dddd-000000000002"
_REC_UUID    = "eeeeeeee-eeee-eeee-eeee-000000000003"
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
    # v2 fields
    "entity_id":           "23456789",
    "description":         "Budget increased from 100 to 200",
    "requested_by":        "maico",
    "approved_by":         None,
    "executed_by":         "maico",
    "recommendation_id":   _REC_UUID,
    "task_id":             _TASK_UUID,
    "report_id":           None,
    "detected_at":         None,
    "expected_impact":     {"roas_delta": "+0.3"},
    "risk_level":          "LOW",
    "outcome_status":      "PENDING",
    "outcome_summary":     None,
    "updated_at":          None,
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


class TestGetChangesFilters:
    def test_filter_by_platform(self):
        with patch("app.agency_api.v1.router._get_db", return_value=_single_db(_CHANGE_ROW)):
            with TestClient(_app()) as c:
                resp = c.get("/agency/v1/clients/lk-sneakers/changes?platform=meta")
        assert resp.status_code == 200

    def test_filter_by_entity_type(self):
        with patch("app.agency_api.v1.router._get_db", return_value=_single_db(_CHANGE_ROW)):
            with TestClient(_app()) as c:
                resp = c.get("/agency/v1/clients/lk-sneakers/changes?entity_type=CAMPAIGN")
        assert resp.status_code == 200

    def test_filter_by_change_type(self):
        with patch("app.agency_api.v1.router._get_db", return_value=_single_db(_CHANGE_ROW)):
            with TestClient(_app()) as c:
                resp = c.get("/agency/v1/clients/lk-sneakers/changes?change_type=BUDGET")
        assert resp.status_code == 200

    def test_filter_by_source(self):
        with patch("app.agency_api.v1.router._get_db", return_value=_single_db(_CHANGE_ROW)):
            with TestClient(_app()) as c:
                resp = c.get("/agency/v1/clients/lk-sneakers/changes?source=HUMAN")
        assert resp.status_code == 200

    def test_filter_by_task_id(self):
        with patch("app.agency_api.v1.router._get_db", return_value=_single_db(_CHANGE_ROW)):
            with TestClient(_app()) as c:
                resp = c.get(f"/agency/v1/clients/lk-sneakers/changes?task_id={_TASK_UUID}")
        assert resp.status_code == 200

    def test_filter_by_recommendation_id(self):
        with patch("app.agency_api.v1.router._get_db", return_value=_single_db(_CHANGE_ROW)):
            with TestClient(_app()) as c:
                resp = c.get(f"/agency/v1/clients/lk-sneakers/changes?recommendation_id={_REC_UUID}")
        assert resp.status_code == 200

    def test_date_range_since_until(self):
        with patch("app.agency_api.v1.router._get_db", return_value=_empty_db()):
            with TestClient(_app()) as c:
                resp = c.get(
                    "/agency/v1/clients/lk-sneakers/changes"
                    "?since=2026-01-01T00:00:00Z&until=2026-01-31T23:59:59Z"
                )
        assert resp.status_code == 200

    def test_custom_limit(self):
        with patch("app.agency_api.v1.router._get_db", return_value=_empty_db()):
            with TestClient(_app()) as c:
                resp = c.get("/agency/v1/clients/lk-sneakers/changes?limit=50")
        assert resp.status_code == 200

    def test_limit_exceeds_max_rejected(self):
        with patch("app.agency_api.v1.router._get_db", return_value=_empty_db()):
            with TestClient(_app()) as c:
                resp = c.get("/agency/v1/clients/lk-sneakers/changes?limit=9999")
        assert resp.status_code == 422


class TestGetSingleChange:
    def test_returns_single_record(self):
        with patch("app.agency_api.v1.router._get_db", return_value=_single_db(_CHANGE_ROW)):
            with TestClient(_app()) as c:
                resp = c.get(f"/agency/v1/clients/lk-sneakers/media-changes/{_CHANGE_UUID}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == _CHANGE_UUID

    def test_not_found_on_missing(self):
        db = _empty_db()
        db.table.return_value.maybe_single.return_value = db.table.return_value
        db.table.return_value.execute.return_value = MagicMock(data=None)
        with patch("app.agency_api.v1.router._get_db", return_value=db):
            with TestClient(_app()) as c:
                resp = c.get(f"/agency/v1/clients/lk-sneakers/media-changes/{_CHANGE_UUID}")
        assert resp.status_code == 404

    def test_invalid_uuid_returns_404(self):
        with patch("app.agency_api.v1.router._get_db", return_value=_empty_db()):
            with TestClient(_app()) as c:
                resp = c.get("/agency/v1/clients/lk-sneakers/media-changes/not-a-uuid")
        assert resp.status_code == 404


class TestChangeLogV2Fields:
    def test_v2_fields_returned_in_list(self):
        with patch("app.agency_api.v1.router._get_db", return_value=_single_db(_CHANGE_ROW)):
            with TestClient(_app()) as c:
                resp = c.get("/agency/v1/clients/lk-sneakers/changes")
        item = resp.json()[0]
        assert item["entity_id"] == "23456789"
        assert item["description"] == "Budget increased from 100 to 200"
        assert item["recommendation_id"] == _REC_UUID
        assert item["task_id"] == _TASK_UUID
        assert item["risk_level"] == "LOW"
        assert item["outcome_status"] == "PENDING"
        assert item["expected_impact"] == {"roas_delta": "+0.3"}

    def test_new_change_type_accepted(self):
        body = {**_CHANGE_BODY, "change_type": "ADSET_STATUS_CHANGE"}
        with patch("app.agency_api.v1.router._get_db", return_value=_single_db({
            **_CHANGE_ROW, "change_type": "ADSET_STATUS_CHANGE"
        })):
            with TestClient(_app()) as c:
                resp = c.post("/agency/v1/changes", json=body)
        assert resp.status_code == 201

    def test_new_source_accepted(self):
        body = {**_CHANGE_BODY, "source": "HERMES_RECOMMENDATION"}
        with patch("app.agency_api.v1.router._get_db", return_value=_single_db({
            **_CHANGE_ROW, "source": "HERMES_RECOMMENDATION"
        })):
            with TestClient(_app()) as c:
                resp = c.post("/agency/v1/changes", json=body)
        assert resp.status_code == 201

    def test_platform_detected_source_accepted(self):
        body = {**_CHANGE_BODY, "source": "PLATFORM_DETECTED"}
        with patch("app.agency_api.v1.router._get_db", return_value=_single_db({
            **_CHANGE_ROW, "source": "PLATFORM_DETECTED"
        })):
            with TestClient(_app()) as c:
                resp = c.post("/agency/v1/changes", json=body)
        assert resp.status_code == 201

    def test_v2_body_fields_written(self):
        body = {
            **_CHANGE_BODY,
            "entity_id": "23456789",
            "description": "Budget bump",
            "risk_level": "LOW",
            "outcome_status": "PENDING",
            "task_id": _TASK_UUID,
            "recommendation_id": _REC_UUID,
        }
        with patch("app.agency_api.v1.router._get_db", return_value=_single_db(_CHANGE_ROW)):
            with TestClient(_app()) as c:
                resp = c.post("/agency/v1/changes", json=body)
        assert resp.status_code == 201
        data = resp.json()
        assert data["id"] == _CHANGE_UUID
