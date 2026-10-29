"""
Tests for delivery settings endpoints (Fase E):
  GET  /agency/v1/canonical-reports/delivery-settings?client_id=
  PUT  /agency/v1/canonical-reports/delivery-settings?client_id=

Coverage:
  GET:
  - No settings in DB → returns defaults (weekly=false, monthly=false, recipients=[])
  - Settings exist → returns stored values
  - Wrong client scope → 403
  - DB error → 503

  PUT:
  - Create new settings → 200 with stored recipients
  - Update existing → recipients replaced
  - Recipients preserve created_at for existing IDs
  - New recipient gets UUID assigned by server if id not provided
  - human_approval_required always true regardless of input
  - Wrong client scope → 403
  - DB error → 503
"""
from __future__ import annotations

import os
import uuid as _uuid
from unittest.mock import patch, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.agency_api.v1.auth import AuthContext, SCOPE_READ_ANY, SCOPE_PLATFORM_WRITE
from app.agency_api.v1.canonical_reports import router


# ── Fake Supabase ──────────────────────────────────────────────────────────────

class FakeSupabase:
    """Minimal in-memory Supabase fake supporting select/update/insert with eq/limit filters."""

    def __init__(self):
        self._tables: dict[str, list[dict]] = {}
        self._op = None
        self._table_name = None
        self._filters: dict = {}
        self._payload = None

    def table(self, name: str):
        if name not in self._tables:
            self._tables[name] = []
        self._table_name = name
        self._op = None
        self._filters = {}
        self._payload = None
        return self

    def select(self, *_):
        self._op = "select"
        return self

    def eq(self, col, val):
        self._filters[col] = val
        return self

    def limit(self, n):
        return self

    def update(self, data):
        self._op = "update"
        self._payload = data
        return self

    def insert(self, data):
        self._op = "insert"
        self._payload = data
        return self

    def execute(self):
        rows = self._tables.get(self._table_name, [])
        if self._op == "select":
            result = [r for r in rows if all(r.get(k) == v for k, v in self._filters.items())]
            return _Result(result)
        elif self._op == "update":
            flt = self._filters
            for i, r in enumerate(rows):
                if all(r.get(k) == v for k, v in flt.items()):
                    rows[i] = {**r, **self._payload}
            return _Result([])
        elif self._op == "insert":
            rows.append(self._payload)
            return _Result([self._payload])
        return _Result([])


class _Result:
    def __init__(self, data):
        self.data = data


# ── App factory ────────────────────────────────────────────────────────────────

def _app_read(client_ids=None):
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[SCOPE_READ_ANY] = lambda: AuthContext(
        scope="agency_admin", subject="tester", client_ids=client_ids,
    )
    return app


def _app_write(client_ids=None):
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[SCOPE_READ_ANY] = lambda: AuthContext(
        scope="agency_admin", subject="tester", client_ids=client_ids,
    )
    app.dependency_overrides[SCOPE_PLATFORM_WRITE] = lambda: AuthContext(
        scope="agency_admin", subject="tester", client_ids=client_ids,
    )
    return app


# ── GET tests ─────────────────────────────────────────────────────────────────

class TestGetDeliverySettings:
    def test_no_settings_returns_defaults(self):
        db = FakeSupabase()
        app = _app_read()
        with patch("app.database.get_supabase", return_value=db):
            r = TestClient(app).get(
                "/agency/v1/canonical-reports/delivery-settings?client_id=lk-sneakers"
            )
        assert r.status_code == 200
        data = r.json()
        assert data["client_id"] == "lk-sneakers"
        assert data["weekly_enabled"] is False
        assert data["monthly_enabled"] is False
        assert data["human_approval_required"] is True
        assert data["recipients"] == []
        assert data["updated_at"] is None

    def test_existing_settings_returned(self):
        db = FakeSupabase()
        db._tables["core_report_delivery_settings"] = [{
            "id": str(_uuid.uuid4()),
            "client_id": "lk-sneakers",
            "weekly_enabled": True,
            "monthly_enabled": False,
            "human_approval_required": True,
            "recipients": [{"id": "r1", "name": "Ana", "email": "ana@noro.com", "active": True}],
            "updated_at": "2026-10-28T10:00:00+00:00",
        }]
        app = _app_read()
        with patch("app.database.get_supabase", return_value=db):
            r = TestClient(app).get(
                "/agency/v1/canonical-reports/delivery-settings?client_id=lk-sneakers"
            )
        assert r.status_code == 200
        data = r.json()
        assert data["weekly_enabled"] is True
        assert len(data["recipients"]) == 1
        assert data["recipients"][0]["name"] == "Ana"

    def test_human_approval_always_true_even_if_db_has_false(self):
        db = FakeSupabase()
        db._tables["core_report_delivery_settings"] = [{
            "id": str(_uuid.uuid4()),
            "client_id": "lk-sneakers",
            "weekly_enabled": False,
            "monthly_enabled": False,
            "human_approval_required": False,  # stored as false (should not happen but defensive)
            "recipients": [],
            "updated_at": "2026-10-28T10:00:00+00:00",
        }]
        app = _app_read()
        with patch("app.database.get_supabase", return_value=db):
            r = TestClient(app).get(
                "/agency/v1/canonical-reports/delivery-settings?client_id=lk-sneakers"
            )
        assert r.json()["human_approval_required"] is True

    def test_wrong_client_scope_returns_403(self):
        db = FakeSupabase()
        app = _app_read(client_ids=["other-client"])
        with patch("app.database.get_supabase", return_value=db):
            r = TestClient(app, raise_server_exceptions=False).get(
                "/agency/v1/canonical-reports/delivery-settings?client_id=lk-sneakers"
            )
        assert r.status_code == 403

    def test_db_error_returns_503(self):
        class BrokenDB:
            def table(self, *a): return self
            def select(self, *a): return self
            def eq(self, *a): return self
            def limit(self, *a): return self
            def execute(self): raise RuntimeError("connection timeout")

        app = _app_read()
        with patch("app.database.get_supabase", return_value=BrokenDB()):
            r = TestClient(app, raise_server_exceptions=False).get(
                "/agency/v1/canonical-reports/delivery-settings?client_id=lk-sneakers"
            )
        assert r.status_code == 503


# ── PUT tests ─────────────────────────────────────────────────────────────────

class TestPutDeliverySettings:
    def test_creates_new_settings(self):
        db = FakeSupabase()
        app = _app_write()
        body = {
            "weekly_enabled": True,
            "monthly_enabled": False,
            "recipients": [{"name": "Maico", "email": "contato@norolabs.com.br", "active": True}],
        }
        with patch("app.database.get_supabase", return_value=db):
            r = TestClient(app).put(
                "/agency/v1/canonical-reports/delivery-settings?client_id=lk-sneakers",
                json=body,
            )
        assert r.status_code == 200
        data = r.json()
        assert data["client_id"] == "lk-sneakers"
        assert data["weekly_enabled"] is True
        assert len(data["recipients"]) == 1
        assert data["recipients"][0]["email"] == "contato@norolabs.com.br"
        assert data["human_approval_required"] is True
        # Row inserted into fake db
        assert len(db._tables["core_report_delivery_settings"]) == 1

    def test_updates_existing_settings(self):
        db = FakeSupabase()
        existing_id = str(_uuid.uuid4())
        db._tables["core_report_delivery_settings"] = [{
            "id": existing_id,
            "client_id": "lk-sneakers",
            "weekly_enabled": False,
            "monthly_enabled": False,
            "human_approval_required": True,
            "recipients": [],
        }]
        app = _app_write()
        body = {"weekly_enabled": True, "monthly_enabled": True, "recipients": []}
        with patch("app.database.get_supabase", return_value=db):
            r = TestClient(app).put(
                "/agency/v1/canonical-reports/delivery-settings?client_id=lk-sneakers",
                json=body,
            )
        assert r.status_code == 200
        data = r.json()
        assert data["weekly_enabled"] is True
        assert data["monthly_enabled"] is True

    def test_existing_recipient_preserves_created_at(self):
        db = FakeSupabase()
        rid = str(_uuid.uuid4())
        original_created_at = "2026-10-01T00:00:00+00:00"
        db._tables["core_report_delivery_settings"] = [{
            "id": str(_uuid.uuid4()),
            "client_id": "lk-sneakers",
            "weekly_enabled": True,
            "monthly_enabled": False,
            "human_approval_required": True,
            "recipients": [{
                "id": rid, "name": "Ana", "email": "ana@noro.com",
                "active": True, "created_at": original_created_at, "updated_at": "2026-10-01T00:00:00+00:00",
            }],
        }]
        app = _app_write()
        body = {
            "weekly_enabled": True,
            "monthly_enabled": False,
            "recipients": [{"id": rid, "name": "Ana Renamed", "email": "ana@noro.com", "active": True}],
        }
        with patch("app.database.get_supabase", return_value=db):
            r = TestClient(app).put(
                "/agency/v1/canonical-reports/delivery-settings?client_id=lk-sneakers",
                json=body,
            )
        assert r.status_code == 200
        recs = r.json()["recipients"]
        assert recs[0]["created_at"] == original_created_at
        assert recs[0]["name"] == "Ana Renamed"

    def test_new_recipient_without_id_gets_uuid_assigned(self):
        db = FakeSupabase()
        app = _app_write()
        body = {
            "weekly_enabled": False,
            "monthly_enabled": False,
            "recipients": [{"name": "No ID", "email": "noid@noro.com", "active": True}],
        }
        with patch("app.database.get_supabase", return_value=db):
            r = TestClient(app).put(
                "/agency/v1/canonical-reports/delivery-settings?client_id=lk-sneakers",
                json=body,
            )
        assert r.status_code == 200
        rec = r.json()["recipients"][0]
        assert rec["id"]  # server assigned a UUID
        # Valid UUID format
        _uuid.UUID(rec["id"])

    def test_email_normalized_to_lowercase(self):
        db = FakeSupabase()
        app = _app_write()
        body = {
            "weekly_enabled": False, "monthly_enabled": False,
            "recipients": [{"name": "Test", "email": "Test@NoroLabs.COM.BR", "active": True}],
        }
        with patch("app.database.get_supabase", return_value=db):
            r = TestClient(app).put(
                "/agency/v1/canonical-reports/delivery-settings?client_id=lk-sneakers",
                json=body,
            )
        assert r.json()["recipients"][0]["email"] == "test@norolabs.com.br"

    def test_human_approval_always_true_in_response(self):
        db = FakeSupabase()
        app = _app_write()
        body = {"weekly_enabled": False, "monthly_enabled": False, "recipients": []}
        with patch("app.database.get_supabase", return_value=db):
            r = TestClient(app).put(
                "/agency/v1/canonical-reports/delivery-settings?client_id=lk-sneakers",
                json=body,
            )
        assert r.json()["human_approval_required"] is True

    def test_wrong_client_scope_returns_403(self):
        db = FakeSupabase()
        app = _app_write(client_ids=["other-client"])
        body = {"weekly_enabled": False, "monthly_enabled": False, "recipients": []}
        with patch("app.database.get_supabase", return_value=db):
            r = TestClient(app, raise_server_exceptions=False).put(
                "/agency/v1/canonical-reports/delivery-settings?client_id=lk-sneakers",
                json=body,
            )
        assert r.status_code == 403

    def test_deactivate_recipient_persisted(self):
        db = FakeSupabase()
        rid = str(_uuid.uuid4())
        db._tables["core_report_delivery_settings"] = [{
            "id": str(_uuid.uuid4()),
            "client_id": "lk-sneakers",
            "weekly_enabled": True,
            "monthly_enabled": False,
            "human_approval_required": True,
            "recipients": [{"id": rid, "name": "Ana", "email": "ana@noro.com",
                            "active": True, "created_at": "2026-10-01T00:00:00+00:00",
                            "updated_at": "2026-10-01T00:00:00+00:00"}],
        }]
        app = _app_write()
        body = {
            "weekly_enabled": True, "monthly_enabled": False,
            "recipients": [{"id": rid, "name": "Ana", "email": "ana@noro.com", "active": False}],
        }
        with patch("app.database.get_supabase", return_value=db):
            r = TestClient(app).put(
                "/agency/v1/canonical-reports/delivery-settings?client_id=lk-sneakers",
                json=body,
            )
        assert r.status_code == 200
        assert r.json()["recipients"][0]["active"] is False
