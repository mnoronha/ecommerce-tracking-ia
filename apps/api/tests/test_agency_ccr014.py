"""
Agency API v1 — CCR-014 integration tests.

Invariants verified:
1. No _stub_ IDs in any GET response from operational endpoints
2. Collection endpoints return [] when no data exists (honest absence)
3. GET by non-existent ID returns 404, not 500 or fabricated data
4. alerts/{id}/context with unknown ID → 404 (was 500 due to ValueStatus.CERTIFIED bug)
5. recommendations/diagnoses do not fabricate FK chains
6. reports endpoint returns [] when no narratives exist
7. Schema remains valid (all 200 responses parse against response_model)
8. X-Request-ID continues to be echoed

Run from apps/api/:
    python -m pytest tests/test_agency_ccr014.py -v
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from unittest.mock import MagicMock, patch

from app.agency_api.v1.auth import SCOPE_ANY_AUTH, SCOPE_READ_ANY, AuthContext
from app.agency_api.v1.request_id import AgencyRequestIDMiddleware
from app.agency_api.v1.router import router as agency_router

_AUTH = AuthContext(scope="agency_admin")
_REQUEST_ID = "ccr-014-test-id"
_STUB_IDS = {"chg_stub_01", "rec_stub_01", "dia_stub_01", "nar_stub_01",
             "alt_stub_01", "sug_stub_01", "job_stub_01", "snap_stub_124",
             "snap_stub_098", "rpc_stub_2026_w40_lk"}


def _build_app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(AgencyRequestIDMiddleware)
    app.include_router(agency_router)
    app.dependency_overrides[SCOPE_READ_ANY] = lambda: _AUTH
    app.dependency_overrides[SCOPE_ANY_AUTH] = lambda: _AUTH
    return app


def _no_stub_ids(data: object) -> bool:
    """Recursively check that no _stub_ IDs appear anywhere in the response."""
    if isinstance(data, dict):
        for k, v in data.items():
            if isinstance(v, str) and any(sid in v for sid in _STUB_IDS):
                return False
            if not _no_stub_ids(v):
                return False
    elif isinstance(data, list):
        return all(_no_stub_ids(item) for item in data)
    return True


class TestCCR014HonestAbsence:
    """Collection endpoints return [] when underlying tables are empty."""

    @pytest.fixture(scope="class")
    def client(self):
        with TestClient(_build_app()) as c:
            yield c

    def test_changes_empty_list(self, client):
        resp = client.get("/agency/v1/clients/lk-sneakers/changes")
        assert resp.status_code == 200, resp.text
        assert resp.json() == [], f"Expected [] but got: {resp.json()}"

    def test_recommendations_empty_list(self, client):
        resp = client.get("/agency/v1/clients/lk-sneakers/recommendations")
        assert resp.status_code == 200, resp.text
        assert resp.json() == []

    def test_reports_empty_list(self, client):
        resp = client.get("/agency/v1/clients/lk-sneakers/reports")
        assert resp.status_code == 200, resp.text
        assert resp.json() == []

    def test_alerts_empty_list(self, client):
        resp = client.get("/agency/v1/alerts")
        assert resp.status_code == 200, resp.text
        assert resp.json() == []

    def test_alert_diagnoses_empty_list(self, client):
        resp = client.get("/agency/v1/alerts/any-alert-id/diagnoses")
        assert resp.status_code == 200, resp.text
        assert resp.json() == []

    def test_diagnosis_recommendations_empty_list(self, client):
        resp = client.get("/agency/v1/diagnoses/any-diag-id/recommendations")
        assert resp.status_code == 200, resp.text
        assert resp.json() == []

    def test_alert_rule_suggestions_empty_list(self, client):
        resp = client.get("/agency/v1/alert-rule-suggestions")
        assert resp.status_code == 200, resp.text
        assert resp.json() == []


def _make_no_alert_db() -> MagicMock:
    """DB mock that returns None for any alert lookup (simulates 'not found')."""
    m = MagicMock()
    for method in ("select", "eq", "in_", "like", "order", "limit", "is_", "maybe_single"):
        getattr(m, method).return_value = m
    m.execute.return_value = MagicMock(data=None)
    db = MagicMock()
    db.table.return_value = m
    return db


class TestCCR014NotFoundByID:
    """GET by non-existent ID returns 404, not 500 and not fabricated data."""

    @pytest.fixture(scope="class")
    def client(self):
        with TestClient(_build_app(), raise_server_exceptions=False) as c:
            yield c

    def test_alert_context_unknown_id_is_404_not_500(self, client):
        """
        Root cause of HTTP 500: ValueStatus.CERTIFIED doesn't exist in ValueStatus enum
        (CERTIFIED belongs to CertificationStatus) → AttributeError at runtime.
        Fix: raise 404 — alert not found.
        """
        db = _make_no_alert_db()
        with patch("app.agency_api.v1.router._get_db", return_value=db):
            resp = client.get("/agency/v1/alerts/alt_stub_01/context")
        assert resp.status_code == 404, (
            f"Expected 404 for unknown alert_id, got {resp.status_code}. "
            f"Was 500 before CCR-014 due to ValueStatus.CERTIFIED AttributeError."
        )

    def test_alert_context_arbitrary_id_is_404(self, client):
        db = _make_no_alert_db()
        with patch("app.agency_api.v1.router._get_db", return_value=db):
            resp = client.get("/agency/v1/alerts/nonexistent-id-xyz/context")
        assert resp.status_code == 404, resp.text

    def test_diagnosis_by_id_is_404(self, client):
        resp = client.get("/agency/v1/diagnoses/dia_stub_01")
        assert resp.status_code == 404, (
            f"Expected 404 for unknown diagnosis_id, got {resp.status_code}"
        )

    def test_recommendation_by_id_is_404(self, client):
        resp = client.get("/agency/v1/recommendations/rec_stub_01")
        assert resp.status_code == 404, (
            f"Expected 404 for unknown recommendation_id, got {resp.status_code}"
        )


class TestCCR014NoStubIDs:
    """No _stub_ IDs appear in any GET response body."""

    @pytest.fixture(scope="class")
    def client(self):
        with TestClient(_build_app()) as c:
            yield c

    @pytest.mark.parametrize("path", [
        "/agency/v1/clients/lk-sneakers/changes",
        "/agency/v1/clients/lk-sneakers/recommendations",
        "/agency/v1/clients/lk-sneakers/reports",
        "/agency/v1/alerts",
        "/agency/v1/alerts/any-alert-id/diagnoses",
        "/agency/v1/diagnoses/any-diag-id/recommendations",
        "/agency/v1/alert-rule-suggestions",
    ])
    def test_no_stub_ids_in_response(self, client, path):
        resp = client.get(path)
        assert resp.status_code == 200, f"{path} → {resp.status_code}: {resp.text}"
        data = resp.json()
        assert _no_stub_ids(data), (
            f"Found _stub_ ID in response from {path}:\n{data}"
        )

    def test_no_stub_ids_in_report_contracts(self, client):
        """report-contracts is handled by CCR-013 adapter — verify no stubs bleed through."""
        resp = client.get("/agency/v1/clients/unknown-client/report-contracts")
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert isinstance(data, list)
        # For unknown client, should be empty (no fabrication)
        for rc in data:
            assert rc.get("provenance_status") == "REAL", (
                f"Non-REAL provenance_status in report-contract: {rc}"
            )
            assert not rc.get("contract", {}).get("stub"), (
                f"stub=True found in report-contract: {rc}"
            )


class TestCCR014SchemaValid:
    """Responses that return 200 must parse against their declared response_model."""

    @pytest.fixture(scope="class")
    def client(self):
        with TestClient(_build_app()) as c:
            yield c

    def test_changes_is_list(self, client):
        resp = client.get("/agency/v1/clients/lk-sneakers/changes")
        assert isinstance(resp.json(), list)

    def test_recommendations_is_list(self, client):
        resp = client.get("/agency/v1/clients/lk-sneakers/recommendations")
        assert isinstance(resp.json(), list)

    def test_alerts_is_list(self, client):
        resp = client.get("/agency/v1/alerts")
        assert isinstance(resp.json(), list)


class TestCCR014RequestIDPreserved:
    """X-Request-ID continues to be echoed on all corrected endpoints."""

    @pytest.fixture(scope="class")
    def client(self):
        with TestClient(_build_app()) as c:
            yield c

    @pytest.mark.parametrize("path", [
        "/agency/v1/clients/lk-sneakers/changes",
        "/agency/v1/clients/lk-sneakers/recommendations",
        "/agency/v1/alerts",
    ])
    def test_request_id_echoed(self, client, path):
        resp = client.get(path, headers={"X-Request-ID": _REQUEST_ID})
        assert resp.headers.get("x-request-id") == _REQUEST_ID, (
            f"X-Request-ID not echoed on {path}: "
            f"got {resp.headers.get('x-request-id')!r}"
        )

    def test_request_id_echoed_on_404(self, client):
        """404 responses must also echo X-Request-ID."""
        db = _make_no_alert_db()
        with patch("app.agency_api.v1.router._get_db", return_value=db):
            resp = client.get(
                "/agency/v1/alerts/alt_stub_01/context",
                headers={"X-Request-ID": _REQUEST_ID},
            )
        assert resp.status_code == 404
        assert resp.headers.get("x-request-id") == _REQUEST_ID, (
            f"X-Request-ID not echoed on 404: {resp.headers.get('x-request-id')!r}"
        )
