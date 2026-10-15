"""
H2 client identity serialization tests.

Verifies that all Agency API read and write endpoints return canonical client
slug (e.g. "enutri") in client_id, never the internal UUID.

Import note: app/agency_api/v1/__init__.py does `from .router import router`
which shadows the submodule name. Use importlib.import_module() to reach the
actual module via sys.modules rather than the package attribute.

Unit tests (no DB required):
  TestSlugMap               — _fetch_slug_map UUID→slug batch resolver
  TestDiagnosisSerializer   — _diagnosis_row_to_out slug resolution
  TestRecommendationSerializer — _recommendation_row_to_out slug resolution
  TestAlertSerializer       — _alert_row_to_out still resolves slugs
  TestDiagnosisEndpoints    — GET /diagnoses/{id} returns slug
  TestRecommendationEndpoints — GET /recommendations/{id} returns slug
  TestCrossClientPrevention — UUID mismatch ↔ slug mismatch

Integration tests (require SUPABASE_URL / SUPABASE_SERVICE_KEY):
  TestExistingDiagnosesIntegration — three H2 diagnosis IDs must return slug
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path
from types import SimpleNamespace
from contextlib import contextmanager
import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))

# app/agency_api/v1/__init__.py does `from .router import router`, which makes
# `import app.agency_api.v1.router` return the APIRouter object instead of the
# module. Use importlib.import_module to bypass the attribute and reach the
# actual submodule from sys.modules.
import app.agency_api.v1  # ensure package is loaded (triggers __init__.py)
_router_mod = importlib.import_module("app.agency_api.v1.router")

# ── Constants ─────────────────────────────────────────────────────────────────

_CLIENT_MAP = {
    "3e20e8b9-c1b5-449f-bc5c-eb0a00704387": "lk-sneakers",
    "84585b15-acdf-4d07-91b8-e5ae51d22ff5": "dipua",
    "f7731f1b-d132-4a81-ab9d-1bd37c52b8e5": "enutri",
    "02a2a276-40ce-4154-bc7f-f3b586e13bc9": "clinica-tarcio-caetano",
    "b2625b82-b5e0-448b-a454-668d723e3715": "zipper-galeria",
    "23f48752-670f-445a-bab8-b5282a43f2f7": "spiti-auction",
}

_H2_DIAGNOSES = [
    ("5a21011a-19fe-445f-b57b-a0491755fddd", "f7731f1b-d132-4a81-ab9d-1bd37c52b8e5", "enutri"),
    ("46715dd5-c077-4300-9080-8285df8436b8", "b2625b82-b5e0-448b-a454-668d723e3715", "zipper-galeria"),
    ("aa7a009c-6f42-47a0-b00a-e1798e7841ff", "23f48752-670f-445a-bab8-b5282a43f2f7", "spiti-auction"),
]

ENUTRI_UUID = "f7731f1b-d132-4a81-ab9d-1bd37c52b8e5"
ENUTRI_SLUG = "enutri"
LK_UUID = "3e20e8b9-c1b5-449f-bc5c-eb0a00704387"
LK_SLUG = "lk-sneakers"


# ── Row factories ─────────────────────────────────────────────────────────────

def _make_diag_row(client_uuid: str, diag_id: str = "aaa00000-0000-0000-0000-000000000001") -> dict:
    return {
        "id": diag_id,
        "alert_id": "bbb00000-0000-0000-0000-000000000001",
        "client_id": client_uuid,
        "status": "FINAL",
        "summary": "test",
        "root_cause_hypotheses": [],
        "evidence": {},
        "confidence": None,
        "limitations": None,
        "fingerprint": None,
        "created_at": "2026-10-01T00:00:00+00:00",
        "created_by": "hermes",
    }


def _make_rec_row(client_uuid: str, rec_id: str = "ccc00000-0000-0000-0000-000000000001") -> dict:
    return {
        "id": rec_id,
        "diagnosis_id": "ddd00000-0000-0000-0000-000000000001",
        "alert_id": "bbb00000-0000-0000-0000-000000000001",
        "client_id": client_uuid,
        "title": "test",
        "action": "test action",
        "rationale": "test rationale",
        "priority": "MEDIUM",
        "risk": None,
        "expected_impact": None,
        "requires_human_approval": True,
        "status": "PROPOSED",
        "fingerprint": None,
        "created_at": "2026-10-01T00:00:00+00:00",
        "created_by": "hermes",
    }


def _make_alert_row(client_uuid: str, alert_id: str = "eee00000-0000-0000-0000-000000000001") -> dict:
    return {
        "id": alert_id,
        "client_id": client_uuid,
        "type": "SOURCE_STALE",
        "severity": "HIGH",
        "status": "OPEN",
        "title": "test",
        "message": "test",
        "fingerprint": "fp",
        "occurrence_count": 1,
        "data": {},
        "created_at": "2026-10-01T00:00:00+00:00",
        "resolved_at": None,
    }


# ── Mock DB ───────────────────────────────────────────────────────────────────

class _MockQuery:
    def __init__(self, name, diagnoses, recs, clients):
        self._name = name
        self._diags = diagnoses
        self._recs = recs
        self._clients = clients
        self._filters: dict = {}

    def select(self, *a): return self
    def eq(self, k, v): self._filters[k] = v; return self
    def in_(self, k, vals): self._filters[k] = vals; return self
    def limit(self, n): return self
    def order(self, *a, **kw): return self
    def not_(self): return self
    def is_(self, k, v): return self

    def execute(self):
        if self._name == "core_diagnoses":
            rows = self._diags
            if "id" in self._filters:
                rows = [r for r in rows if str(r["id"]) == str(self._filters["id"])]
            return SimpleNamespace(data=rows)
        if self._name == "core_recommendations":
            rows = self._recs
            if "id" in self._filters:
                rows = [r for r in rows if str(r["id"]) == str(self._filters["id"])]
            if "diagnosis_id" in self._filters:
                rows = [r for r in rows if str(r.get("diagnosis_id")) == str(self._filters["diagnosis_id"])]
            return SimpleNamespace(data=rows)
        if self._name == "clients":
            rows = self._clients
            if isinstance(self._filters.get("id"), list):
                uuid_set = {str(u) for u in self._filters["id"]}
                rows = [r for r in rows if str(r["id"]) in uuid_set]
            return SimpleNamespace(data=rows)
        return SimpleNamespace(data=[])


class _MockDb:
    def __init__(self, diag_rows=None, rec_rows=None, client_rows=None):
        self._diags = diag_rows or []
        self._recs = rec_rows or []
        self._clients = client_rows or []

    def table(self, name):
        return _MockQuery(name, self._diags, self._recs, self._clients)


def _client_rows_from_uuids(*row_lists) -> list[dict]:
    seen: set = set()
    out = []
    for rows in row_lists:
        for r in rows:
            uuid = r.get("client_id")
            if uuid and uuid not in seen:
                seen.add(uuid)
                out.append({"id": uuid, "client_id": _CLIENT_MAP.get(uuid, "unknown-slug")})
    return out


@contextmanager
def _patched_db(db: _MockDb):
    original = _router_mod._get_db
    _router_mod._get_db = lambda: db
    try:
        yield
    finally:
        _router_mod._get_db = original


def _make_test_client(diag_rows, rec_rows=None, client_rows=None):
    """Return a (TestClient, db) tuple for HTTP-level tests."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.agency_api.v1.auth import (
        SCOPE_ADMIN_ONLY, SCOPE_ANY_AUTH, SCOPE_HERMES_WRITE,
        SCOPE_PLATFORM_WRITE, SCOPE_READ_ANY, SCOPE_WRITE_ANY,
        AuthContext,
    )
    if client_rows is None:
        client_rows = _client_rows_from_uuids(diag_rows, rec_rows or [])
    db = _MockDb(diag_rows, rec_rows or [], client_rows)
    _auth = AuthContext(scope="agency_admin")
    application = FastAPI()
    application.include_router(_router_mod.router)
    for dep in (SCOPE_READ_ANY, SCOPE_WRITE_ANY, SCOPE_ADMIN_ONLY,
                SCOPE_HERMES_WRITE, SCOPE_PLATFORM_WRITE, SCOPE_ANY_AUTH):
        application.dependency_overrides[dep] = lambda: _auth
    return TestClient(application, raise_server_exceptions=False), db


# ── TestSlugMap ───────────────────────────────────────────────────────────────

class TestSlugMap:
    def test_empty_rows_returns_empty(self):
        # _fetch_slug_map with empty list returns {} before calling _get_db
        result = _router_mod._fetch_slug_map([])
        assert result == {}

    def test_rows_without_client_id_returns_empty(self):
        result = _router_mod._fetch_slug_map([{"id": "x"}, {"something": "else"}])
        assert result == {}

    def test_resolves_uuid_to_slug(self):
        class _FakeQuery:
            def select(self, *a): return self
            def in_(self, *a): return self
            def execute(self):
                return SimpleNamespace(data=[{"id": ENUTRI_UUID, "client_id": ENUTRI_SLUG}])

        class _FakeDB:
            def table(self, name): return _FakeQuery()

        with _patched_db(_FakeDB()):
            result = _router_mod._fetch_slug_map([{"client_id": ENUTRI_UUID}])
        assert result == {ENUTRI_UUID: ENUTRI_SLUG}

    def test_unknown_uuid_not_in_result(self):
        unknown = "00000000-0000-0000-0000-000000000099"

        class _FakeQuery:
            def select(self, *a): return self
            def in_(self, *a): return self
            def execute(self): return SimpleNamespace(data=[])

        class _FakeDB:
            def table(self, name): return _FakeQuery()

        with _patched_db(_FakeDB()):
            result = _router_mod._fetch_slug_map([{"client_id": unknown}])
        assert unknown not in result

    def test_db_error_returns_empty(self):
        class _BadDB:
            def table(self, name): raise RuntimeError("DB down")

        with _patched_db(_BadDB()):
            result = _router_mod._fetch_slug_map([{"client_id": ENUTRI_UUID}])
        assert result == {}


# ── TestDiagnosisSerializer ───────────────────────────────────────────────────

class TestDiagnosisSerializer:
    def test_resolves_uuid_to_slug_when_map_provided(self):
        row = _make_diag_row(ENUTRI_UUID)
        out = _router_mod._diagnosis_row_to_out(row, {ENUTRI_UUID: ENUTRI_SLUG})
        assert out.client_id == ENUTRI_SLUG

    def test_all_six_clients_resolve(self):
        for uuid, slug in _CLIENT_MAP.items():
            row = _make_diag_row(uuid)
            out = _router_mod._diagnosis_row_to_out(row, _CLIENT_MAP)
            assert out.client_id == slug

    def test_no_map_falls_back_to_uuid(self):
        row = _make_diag_row(ENUTRI_UUID)
        out = _router_mod._diagnosis_row_to_out(row, None)
        assert out.client_id == ENUTRI_UUID  # None slug_map → UUID fallback

    def test_empty_map_falls_back_to_uuid(self):
        row = _make_diag_row(ENUTRI_UUID)
        out = _router_mod._diagnosis_row_to_out(row, {})
        assert out.client_id == ENUTRI_UUID  # {} is falsy → UUID fallback

    def test_wrong_map_key_returns_none(self):
        """When slug_map is non-empty but doesn't contain this UUID, client_id is None."""
        row = _make_diag_row(ENUTRI_UUID)
        wrong_map = {LK_UUID: LK_SLUG}
        out = _router_mod._diagnosis_row_to_out(row, wrong_map)
        # slug_map is truthy, UUID not in it → client_id = None (not UUID, not wrong slug)
        assert out.client_id != LK_SLUG, "must not return wrong client's slug"
        assert out.client_id is None


# ── TestRecommendationSerializer ──────────────────────────────────────────────

class TestRecommendationSerializer:
    def test_resolves_uuid_to_slug(self):
        row = _make_rec_row(ENUTRI_UUID)
        out = _router_mod._recommendation_row_to_out(row, {ENUTRI_UUID: ENUTRI_SLUG})
        assert out.client_id == ENUTRI_SLUG

    def test_all_six_clients_resolve(self):
        for uuid, slug in _CLIENT_MAP.items():
            row = _make_rec_row(uuid)
            out = _router_mod._recommendation_row_to_out(row, _CLIENT_MAP)
            assert out.client_id == slug

    def test_no_map_falls_back_to_uuid(self):
        row = _make_rec_row(ENUTRI_UUID)
        out = _router_mod._recommendation_row_to_out(row, None)
        assert out.client_id == ENUTRI_UUID

    def test_cross_client_map_does_not_resolve_to_wrong_slug(self):
        row = _make_rec_row(ENUTRI_UUID)
        out = _router_mod._recommendation_row_to_out(row, {LK_UUID: LK_SLUG})
        assert out.client_id != LK_SLUG


# ── TestAlertSerializer ───────────────────────────────────────────────────────

class TestAlertSerializer:
    def test_resolves_slug_with_map(self):
        row = _make_alert_row(ENUTRI_UUID)
        out = _router_mod._alert_row_to_out(row, {ENUTRI_UUID: ENUTRI_SLUG})
        assert out.client_id == ENUTRI_SLUG

    def test_all_six_clients_resolve(self):
        for uuid, slug in _CLIENT_MAP.items():
            row = _make_alert_row(uuid)
            out = _router_mod._alert_row_to_out(row, _CLIENT_MAP)
            assert out.client_id == slug


# ── TestDiagnosisEndpoints ────────────────────────────────────────────────────

class TestDiagnosisEndpoints:
    def test_get_diagnosis_returns_slug_not_uuid(self):
        diag_id = "5a21011a-19fe-445f-b57b-a0491755fddd"
        rows = [_make_diag_row(ENUTRI_UUID, diag_id)]
        tc, db = _make_test_client(rows)
        with _patched_db(db):
            resp = tc.get(f"/agency/v1/diagnoses/{diag_id}")
        assert resp.status_code == 200, resp.text
        assert resp.json()["client_id"] == ENUTRI_SLUG

    def test_get_alert_diagnoses_returns_slug_not_uuid(self):
        alert_id = "bbb00000-0000-0000-0000-000000000001"
        rows = [_make_diag_row(ENUTRI_UUID)]
        tc, db = _make_test_client(rows)
        with _patched_db(db):
            resp = tc.get(f"/agency/v1/alerts/{alert_id}/diagnoses")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["client_id"] == ENUTRI_SLUG

    def test_all_six_clients_return_slug(self):
        diag_id = "5a21011a-19fe-445f-b57b-a0491755fddd"
        for uuid, slug in _CLIENT_MAP.items():
            rows = [_make_diag_row(uuid, diag_id)]
            client_rows = [{"id": uuid, "client_id": slug}]
            tc, db = _make_test_client(rows, client_rows=client_rows)
            with _patched_db(db):
                resp = tc.get(f"/agency/v1/diagnoses/{diag_id}")
            assert resp.status_code == 200
            assert resp.json()["client_id"] == slug

    def test_enutri_existing_diagnosis_preserved(self):
        diag_id = "5a21011a-19fe-445f-b57b-a0491755fddd"
        rows = [_make_diag_row(ENUTRI_UUID, diag_id)]
        tc, db = _make_test_client(rows)
        with _patched_db(db):
            resp = tc.get(f"/agency/v1/diagnoses/{diag_id}")
        assert resp.status_code == 200
        assert resp.json()["client_id"] == "enutri"

    def test_zipper_existing_diagnosis_preserved(self):
        diag_id = "46715dd5-c077-4300-9080-8285df8436b8"
        zipper_uuid = "b2625b82-b5e0-448b-a454-668d723e3715"
        rows = [_make_diag_row(zipper_uuid, diag_id)]
        tc, db = _make_test_client(rows)
        with _patched_db(db):
            resp = tc.get(f"/agency/v1/diagnoses/{diag_id}")
        assert resp.status_code == 200
        assert resp.json()["client_id"] == "zipper-galeria"

    def test_spiti_existing_diagnosis_preserved(self):
        diag_id = "aa7a009c-6f42-47a0-b00a-e1798e7841ff"
        spiti_uuid = "23f48752-670f-445a-bab8-b5282a43f2f7"
        rows = [_make_diag_row(spiti_uuid, diag_id)]
        tc, db = _make_test_client(rows)
        with _patched_db(db):
            resp = tc.get(f"/agency/v1/diagnoses/{diag_id}")
        assert resp.status_code == 200
        assert resp.json()["client_id"] == "spiti-auction"


# ── TestRecommendationEndpoints ───────────────────────────────────────────────

class TestRecommendationEndpoints:
    def test_get_recommendation_returns_slug(self):
        rec_id = "ccc00000-0000-0000-0000-000000000001"
        rows = [_make_rec_row(ENUTRI_UUID, rec_id)]
        tc, db = _make_test_client([], rows)
        with _patched_db(db):
            resp = tc.get(f"/agency/v1/recommendations/{rec_id}")
        assert resp.status_code == 200, resp.text
        assert resp.json()["client_id"] == ENUTRI_SLUG

    def test_get_diagnosis_recommendations_returns_slug(self):
        diag_id = "ddd00000-0000-0000-0000-000000000001"
        rec_id = "ccc00000-0000-0000-0000-000000000001"
        rows = [_make_rec_row(ENUTRI_UUID, rec_id)]
        tc, db = _make_test_client([], rows)
        with _patched_db(db):
            resp = tc.get(f"/agency/v1/diagnoses/{diag_id}/recommendations")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["client_id"] == ENUTRI_SLUG

    def test_all_six_clients_return_slug(self):
        rec_id = "ccc00000-0000-0000-0000-000000000001"
        for uuid, slug in _CLIENT_MAP.items():
            rows = [_make_rec_row(uuid, rec_id)]
            client_rows = [{"id": uuid, "client_id": slug}]
            tc, db = _make_test_client([], rows, client_rows=client_rows)
            with _patched_db(db):
                resp = tc.get(f"/agency/v1/recommendations/{rec_id}")
            assert resp.status_code == 200
            assert resp.json()["client_id"] == slug


# ── TestCrossClientPrevention ─────────────────────────────────────────────────

class TestCrossClientPrevention:
    def test_enutri_uuid_does_not_resolve_to_lk_slug(self):
        row = _make_diag_row(ENUTRI_UUID)
        out = _router_mod._diagnosis_row_to_out(row, {LK_UUID: LK_SLUG})
        assert out.client_id != LK_SLUG

    def test_lk_uuid_does_not_resolve_to_enutri_slug(self):
        row = _make_rec_row(LK_UUID)
        out = _router_mod._recommendation_row_to_out(row, {ENUTRI_UUID: ENUTRI_SLUG})
        assert out.client_id != ENUTRI_SLUG

    def test_slug_map_keys_are_uuids_values_are_slugs(self):
        class _FakeQuery:
            def select(self, *a): return self
            def in_(self, *a): return self
            def execute(self):
                return SimpleNamespace(data=[
                    {"id": LK_UUID, "client_id": LK_SLUG},
                    {"id": ENUTRI_UUID, "client_id": ENUTRI_SLUG},
                ])

        class _FakeDB:
            def table(self, name): return _FakeQuery()

        with _patched_db(_FakeDB()):
            result = _router_mod._fetch_slug_map([
                {"client_id": LK_UUID}, {"client_id": ENUTRI_UUID},
            ])
        assert result[LK_UUID] == LK_SLUG
        assert result[ENUTRI_UUID] == ENUTRI_SLUG
        assert LK_SLUG not in result
        assert ENUTRI_SLUG not in result


# ── Integration tests (real DB) ───────────────────────────────────────────────

class TestExistingDiagnosesIntegration:
    """
    H2 production validation — skipped without SUPABASE_URL / SUPABASE_SERVICE_KEY.
    """

    @pytest.mark.parametrize("diag_id,expected_uuid,expected_slug", _H2_DIAGNOSES)
    def test_existing_diagnosis_returns_slug(self, diag_id, expected_uuid, expected_slug, app_real_db):
        resp = app_real_db.get(f"/agency/v1/diagnoses/{diag_id}")
        assert resp.status_code == 200, f"HTTP {resp.status_code}: {resp.text}"
        data = resp.json()
        assert data["id"] == diag_id
        assert data["client_id"] == expected_slug, (
            f"Diagnosis {diag_id}: expected {expected_slug!r}, got {data['client_id']!r}"
        )
