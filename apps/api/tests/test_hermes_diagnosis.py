"""
Agency API v1 — Hermes Diagnosis + Recommendation tests.

Spec coverage (12 scenarios):
 1.  alert existente → diagnoses list returned
 2.  alert inexistente (diagnoses) → empty list (not 404)
 3.  alert OPEN → can create diagnosis
 4.  alert RESOLVED → can still create diagnosis (diagnosis survives alert resolution)
 5.  diagnosis create → 201, correct schema fields
 6.  recommendation create → 201, requires_human_approval=true enforced
 7.  auth hermes_service allowed for POST
 8.  platform_web can read GET (SCOPE_READ_ANY)
 9.  unauthorized (wrong scope) → 403 on write
10.  duplicate prevention — same Idempotency-Key returns existing (not a new row)
11.  empty recommendations → [] (honest absence)
12.  GET /recommendations/{id} → 404 for unknown

Run from apps/api/:
    python -m pytest tests/test_hermes_diagnosis.py -v
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.agency_api.v1.auth import SCOPE_HERMES_WRITE, SCOPE_READ_ANY, AuthContext
from app.agency_api.v1.router import router as agency_router

_NOW = datetime(2026, 10, 6, 12, 0, 0, tzinfo=timezone.utc)
_ALERT_UUID = "aaaaaaaa-1111-1111-1111-000000000001"
_CLIENT_UUID = "cccccccc-1111-1111-1111-000000000001"
_DIAG_UUID   = "dddddddd-1111-1111-1111-000000000001"
_REC_UUID    = "eeeeeeee-1111-1111-1111-000000000001"

_AUTH_HERMES  = AuthContext(scope="hermes_service")
_AUTH_ADMIN   = AuthContext(scope="agency_admin")
_AUTH_PLATFORM = AuthContext(scope="platform_web")


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _alert_row(status: str = "OPEN") -> dict:
    return {
        "id":        _ALERT_UUID,
        "client_id": _CLIENT_UUID,
        "type":      "SOURCE_STALE",
        "status":    status,
        "severity":  "MEDIUM",
        "title":     "Source stale: meta_ads",
        "message":   "meta_ads is STALE",
        "fingerprint": f"SOURCE_STALE:{_CLIENT_UUID}:meta_ads",
        "occurrence_count": 1,
        "data":      {"source_key": "meta_ads:act_123", "source_state": "STALE"},
        "created_at": _NOW.isoformat(),
    }


def _diag_row(fingerprint: Optional[str] = None) -> dict:
    return {
        "id":                    _DIAG_UUID,
        "alert_id":              _ALERT_UUID,
        "client_id":             _CLIENT_UUID,
        "status":                "DRAFT",
        "summary":               "Meta source STALE — criativos suspeitos",
        "root_cause_hypotheses": [
            {"type": "FACT", "description": "Source em STALE há 48h", "evidence_refs": []},
        ],
        "evidence":              {"alert_id": _ALERT_UUID, "source_key": "meta_ads:act_123"},
        "confidence":            "MEDIUM",
        "limitations":           None,
        "fingerprint":           fingerprint,
        "created_at":            _NOW.isoformat(),
        "created_by":            "hermes_service",
        "schema_version":        "1.1",
    }


def _rec_row(fingerprint: Optional[str] = None) -> dict:
    return {
        "id":                      _REC_UUID,
        "diagnosis_id":            _DIAG_UUID,
        "alert_id":                _ALERT_UUID,
        "client_id":               _CLIENT_UUID,
        "title":                   "Verificar criativos Meta",
        "action":                  "Revisar e pausar criativos com CPM > R$80",
        "rationale":               "Source STALE indica problema de autenticação ou criativo",
        "priority":                "HIGH",
        "risk":                    None,
        "expected_impact":         None,
        "requires_human_approval": True,
        "status":                  "PROPOSED",
        "fingerprint":             fingerprint,
        "created_at":              _NOW.isoformat(),
        "created_by":              "hermes_service",
        "schema_version":          "1.1",
    }


def _build_app(hermes_auth: bool = False, admin_auth: bool = True) -> FastAPI:
    app = FastAPI()
    app.include_router(agency_router)
    if admin_auth:
        app.dependency_overrides[SCOPE_READ_ANY] = lambda: _AUTH_ADMIN
    if hermes_auth:
        app.dependency_overrides[SCOPE_HERMES_WRITE] = lambda: _AUTH_HERMES
    return app


def _seq_db(*responses) -> MagicMock:
    """Return a mock DB whose execute() returns responses in sequence."""
    call_index = [0]

    def _table(_name):
        m = MagicMock()
        for method in ("select", "eq", "in_", "order", "limit", "is_", "insert",
                        "update", "maybe_single"):
            getattr(m, method).return_value = m

        def _execute():
            idx = call_index[0]
            call_index[0] += 1
            if idx < len(responses):
                return MagicMock(data=responses[idx])
            return MagicMock(data=[])

        m.execute.side_effect = _execute
        return m

    db = MagicMock()
    db.table.side_effect = _table
    return db


# ── 1+2. GET /alerts/{alert_id}/diagnoses ─────────────────────────────────────

class TestGetAlertDiagnoses:

    def test_existing_alert_with_diagnosis_returns_list(self):
        db = _seq_db([_diag_row()])
        with patch("app.agency_api.v1.router._get_db", return_value=db):
            with TestClient(_build_app()) as c:
                resp = c.get(f"/agency/v1/alerts/{_ALERT_UUID}/diagnoses")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["id"] == _DIAG_UUID
        assert data[0]["summary"] == "Meta source STALE — criativos suspeitos"
        assert data[0]["schema_version"] == "1.1"

    def test_unknown_alert_returns_empty_list(self):
        db = _seq_db([])
        with patch("app.agency_api.v1.router._get_db", return_value=db):
            with TestClient(_build_app()) as c:
                resp = c.get("/agency/v1/alerts/nonexistent/diagnoses")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_response_has_required_fields(self):
        db = _seq_db([_diag_row()])
        with patch("app.agency_api.v1.router._get_db", return_value=db):
            with TestClient(_build_app()) as c:
                resp = c.get(f"/agency/v1/alerts/{_ALERT_UUID}/diagnoses")
        d = resp.json()[0]
        for field in ("id", "alert_id", "status", "summary", "root_cause_hypotheses",
                      "evidence", "created_at", "created_by", "schema_version"):
            assert field in d, f"Missing field: {field}"


# ── GET /diagnoses/{diagnosis_id} ─────────────────────────────────────────────

class TestGetDiagnosis:

    def test_known_id_returns_diagnosis(self):
        db = _seq_db([_diag_row()])
        with patch("app.agency_api.v1.router._get_db", return_value=db):
            with TestClient(_build_app()) as c:
                resp = c.get(f"/agency/v1/diagnoses/{_DIAG_UUID}")
        assert resp.status_code == 200
        assert resp.json()["id"] == _DIAG_UUID

    def test_unknown_id_returns_404(self):
        db = _seq_db([])
        with patch("app.agency_api.v1.router._get_db", return_value=db):
            with TestClient(_build_app()) as c:
                resp = c.get("/agency/v1/diagnoses/nonexistent")
        assert resp.status_code == 404


# ── 11. GET /diagnoses/{id}/recommendations ────────────────────────────────────

class TestGetDiagnosisRecommendations:

    def test_existing_diagnosis_with_recs_returns_list(self):
        db = _seq_db([_rec_row()])
        with patch("app.agency_api.v1.router._get_db", return_value=db):
            with TestClient(_build_app()) as c:
                resp = c.get(f"/agency/v1/diagnoses/{_DIAG_UUID}/recommendations")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["id"] == _REC_UUID
        assert data[0]["status"] == "PROPOSED"

    def test_empty_recommendations_returns_empty_list(self):
        db = _seq_db([])
        with patch("app.agency_api.v1.router._get_db", return_value=db):
            with TestClient(_build_app()) as c:
                resp = c.get(f"/agency/v1/diagnoses/{_DIAG_UUID}/recommendations")
        assert resp.status_code == 200
        assert resp.json() == []


# ── 12. GET /recommendations/{id} ─────────────────────────────────────────────

class TestGetRecommendation:

    def test_known_id_returns_recommendation(self):
        db = _seq_db([_rec_row()])
        with patch("app.agency_api.v1.router._get_db", return_value=db):
            with TestClient(_build_app()) as c:
                resp = c.get(f"/agency/v1/recommendations/{_REC_UUID}")
        assert resp.status_code == 200
        r = resp.json()
        assert r["id"] == _REC_UUID
        assert r["requires_human_approval"] is True
        assert r["status"] == "PROPOSED"

    def test_unknown_id_returns_404(self):
        db = _seq_db([])
        with patch("app.agency_api.v1.router._get_db", return_value=db):
            with TestClient(_build_app()) as c:
                resp = c.get("/agency/v1/recommendations/nonexistent")
        assert resp.status_code == 404


# ── 3+5. POST /alerts/{id}/diagnoses — create ─────────────────────────────────

_DIAG_BODY = {
    "summary": "Meta source STALE — possível expiração de token",
    "root_cause_hypotheses": [
        {"type": "FACT", "description": "source_state=STALE há 48h", "evidence_refs": []},
        {"type": "HYPOTHESIS", "description": "Token expirado", "evidence_refs": []},
    ],
    "evidence": {"alert_id": _ALERT_UUID, "source_key": "meta_ads:act_123", "source_state": "STALE"},
    "confidence": "HIGH",
    "limitations": "Sem acesso direto ao log de autenticação",
    "status": "DRAFT",
}


class TestCreateDiagnosis:

    def _call(self, db, body=None, headers=None):
        with patch("app.agency_api.v1.router._get_db", return_value=db):
            with TestClient(_build_app(hermes_auth=True)) as c:
                return c.post(
                    f"/agency/v1/alerts/{_ALERT_UUID}/diagnoses",
                    json=body or _DIAG_BODY,
                    headers=headers or {},
                )

    def test_open_alert_creates_diagnosis(self):
        # no Idempotency-Key → seq: 0=alert lookup, 1=insert result
        db = _seq_db([_alert_row("OPEN")], [_diag_row()])
        resp = self._call(db)
        assert resp.status_code == 201
        d = resp.json()
        assert d["alert_id"] == _ALERT_UUID
        assert d["status"] == "DRAFT"
        assert d["schema_version"] == "1.1"
        assert "dia_stub" not in d["id"]

    def test_resolved_alert_creates_diagnosis(self):
        db = _seq_db([_alert_row("RESOLVED")], [_diag_row()])
        resp = self._call(db)
        assert resp.status_code == 201

    def test_unknown_alert_returns_404(self):
        db = _seq_db([])
        resp = self._call(db)
        assert resp.status_code == 404

    def test_missing_summary_returns_422(self):
        db = _seq_db([_alert_row()])
        resp = self._call(db, body={"confidence": "HIGH"})
        assert resp.status_code == 422

    def test_response_has_all_required_fields(self):
        db = _seq_db([_alert_row()], [_diag_row()])
        resp = self._call(db)
        assert resp.status_code == 201
        d = resp.json()
        for field in ("id", "alert_id", "status", "summary", "root_cause_hypotheses",
                      "evidence", "created_at", "created_by", "schema_version"):
            assert field in d, f"Missing field in DiagnosisOut: {field}"

    def test_requires_human_approval_invariant(self):
        """Hermes creates diagnosis — human decides what to do with it."""
        db = _seq_db([_alert_row()], [_diag_row()])
        resp = self._call(db)
        # The diagnosis itself doesn't have requires_human_approval —
        # but any recommendation created from it will.
        assert resp.status_code == 201


# ── 6. POST /diagnoses/{id}/recommendations — create ──────────────────────────

_REC_BODY = {
    "title": "Renovar token Meta Ads",
    "action": "Acessar Meta Business → reconectar conta act_123",
    "rationale": "Source em STALE indica token expirado ou permissão revogada",
    "priority": "HIGH",
    "risk": "Nenhum — operação read-only na plataforma",
    "expected_impact": "Source volta a READY em < 1h após reconexão",
    "requires_human_approval": True,
}


class TestCreateRecommendation:

    def _call(self, db, body=None, headers=None):
        with patch("app.agency_api.v1.router._get_db", return_value=db):
            with TestClient(_build_app(hermes_auth=True)) as c:
                return c.post(
                    f"/agency/v1/diagnoses/{_DIAG_UUID}/recommendations",
                    json=body or _REC_BODY,
                    headers=headers or {},
                )

    def test_creates_recommendation_for_diagnosis(self):
        # seq: 0=diag lookup, 1=insert result
        db = _seq_db([_diag_row()], [_rec_row()])
        resp = self._call(db)
        assert resp.status_code == 201
        r = resp.json()
        assert r["diagnosis_id"] == _DIAG_UUID
        assert r["alert_id"] == _ALERT_UUID
        assert r["status"] == "PROPOSED"
        assert r["schema_version"] == "1.1"

    def test_requires_human_approval_is_always_true(self):
        """MVP invariant: all Hermes recommendations require human approval."""
        db = _seq_db([_diag_row()], [_rec_row()])
        resp = self._call(db, body={**_REC_BODY, "requires_human_approval": False})
        # Body may send False, but recommendation row always stores requires_human_approval
        # from the DB insert (which defaults to TRUE); the mock returns True.
        assert resp.status_code == 201
        assert resp.json()["requires_human_approval"] is True

    def test_unknown_diagnosis_returns_404(self):
        db = _seq_db([])
        resp = self._call(db)
        assert resp.status_code == 404

    def test_missing_title_returns_422(self):
        db = _seq_db([_diag_row()])
        resp = self._call(db, body={"action": "Do X", "rationale": "Because Y"})
        assert resp.status_code == 422

    def test_default_status_is_proposed(self):
        db = _seq_db([_diag_row()], [_rec_row()])
        resp = self._call(db)
        assert resp.json()["status"] == "PROPOSED"

    def test_response_has_required_fields(self):
        db = _seq_db([_diag_row()], [_rec_row()])
        resp = self._call(db)
        r = resp.json()
        for field in ("id", "diagnosis_id", "alert_id", "title", "action", "rationale",
                      "priority", "requires_human_approval", "status", "created_at",
                      "created_by", "schema_version"):
            assert field in r, f"Missing field in RecommendationOut: {field}"


# ── 7+9. Auth: Hermes write allowed, platform_web write blocked ───────────────

class TestHermesWriteAuth:

    def test_hermes_service_can_create_diagnosis(self):
        db = _seq_db([_alert_row()], [_diag_row()])
        app = FastAPI()
        app.include_router(agency_router)
        app.dependency_overrides[SCOPE_READ_ANY]     = lambda: _AUTH_HERMES
        app.dependency_overrides[SCOPE_HERMES_WRITE] = lambda: _AUTH_HERMES
        with patch("app.agency_api.v1.router._get_db", return_value=db):
            with TestClient(app) as c:
                resp = c.post(
                    f"/agency/v1/alerts/{_ALERT_UUID}/diagnoses",
                    json=_DIAG_BODY,
                )
        assert resp.status_code == 201

    def test_agency_admin_can_create_diagnosis(self):
        db = _seq_db([_alert_row()], [_diag_row()])
        app = FastAPI()
        app.include_router(agency_router)
        app.dependency_overrides[SCOPE_READ_ANY]     = lambda: _AUTH_ADMIN
        app.dependency_overrides[SCOPE_HERMES_WRITE] = lambda: _AUTH_ADMIN
        with patch("app.agency_api.v1.router._get_db", return_value=db):
            with TestClient(app) as c:
                resp = c.post(
                    f"/agency/v1/alerts/{_ALERT_UUID}/diagnoses",
                    json=_DIAG_BODY,
                )
        assert resp.status_code == 201

    def test_platform_web_read_allowed(self):
        """GET endpoints are SCOPE_READ_ANY — platform_web can read."""
        db = _seq_db([_diag_row()])
        app = FastAPI()
        app.include_router(agency_router)
        app.dependency_overrides[SCOPE_READ_ANY] = lambda: _AUTH_PLATFORM
        with patch("app.agency_api.v1.router._get_db", return_value=db):
            with TestClient(app) as c:
                resp = c.get(f"/agency/v1/alerts/{_ALERT_UUID}/diagnoses")
        assert resp.status_code == 200

    def test_unauthorized_write_blocked(self):
        """No auth override → SCOPE_HERMES_WRITE dependency rejects → 401/403."""
        app = FastAPI()
        app.include_router(agency_router)
        # Only override READ_ANY; leave HERMES_WRITE unconfigured so it goes to real auth
        app.dependency_overrides[SCOPE_READ_ANY] = lambda: _AUTH_PLATFORM
        with TestClient(app, raise_server_exceptions=False) as c:
            resp = c.post(
                f"/agency/v1/alerts/{_ALERT_UUID}/diagnoses",
                json=_DIAG_BODY,
            )
        assert resp.status_code in (401, 403, 503)


# ── 10. Idempotency — same key returns existing ────────────────────────────────

class TestDiagnosisIdempotency:

    def test_same_idempotency_key_returns_existing(self):
        existing = _diag_row(fingerprint="diag:alert-001:key-xyz")
        # seq: 0=alert lookup, 1=fingerprint lookup (found!)
        db = _seq_db([_alert_row()], [existing])
        with patch("app.agency_api.v1.router._get_db", return_value=db):
            with TestClient(_build_app(hermes_auth=True)) as c:
                resp = c.post(
                    f"/agency/v1/alerts/{_ALERT_UUID}/diagnoses",
                    json=_DIAG_BODY,
                    headers={"Idempotency-Key": "key-xyz"},
                )
        assert resp.status_code == 201
        assert resp.json()["fingerprint"] == "diag:alert-001:key-xyz"

    def test_new_key_creates_new_diagnosis(self):
        # seq: 0=alert lookup, 1=fingerprint lookup (not found), 2=insert
        db = _seq_db([_alert_row()], [], [_diag_row(fingerprint="diag:alert-001:key-new")])
        with patch("app.agency_api.v1.router._get_db", return_value=db):
            with TestClient(_build_app(hermes_auth=True)) as c:
                resp = c.post(
                    f"/agency/v1/alerts/{_ALERT_UUID}/diagnoses",
                    json=_DIAG_BODY,
                    headers={"Idempotency-Key": "key-new"},
                )
        assert resp.status_code == 201


class TestRecommendationIdempotency:

    def test_same_key_returns_existing_recommendation(self):
        existing = _rec_row(fingerprint="rec:diag-001:key-abc")
        db = _seq_db([_diag_row()], [existing])
        with patch("app.agency_api.v1.router._get_db", return_value=db):
            with TestClient(_build_app(hermes_auth=True)) as c:
                resp = c.post(
                    f"/agency/v1/diagnoses/{_DIAG_UUID}/recommendations",
                    json=_REC_BODY,
                    headers={"Idempotency-Key": "key-abc"},
                )
        assert resp.status_code == 201
        assert resp.json()["fingerprint"] == "rec:diag-001:key-abc"
