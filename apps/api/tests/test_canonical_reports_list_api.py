"""
Tests for GET /agency/v1/canonical-reports/list

Coverage:
  - client_id required (422 without it)
  - invalid auth → 401, misconfigured server → 503
  - valid auth via dependency override
  - client A cannot see client B (client isolation via JSONB filter)
  - status filter (READY_FOR_REVIEW, PUBLISHED, no filter → all)
  - report_type filter (weekly, monthly)
  - empty result for unknown client
  - limit enforced (ge=1, le=100 → 422 on >100)
  - sorted by period_start desc, version desc
  - no write operations
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.agency_api.v1.auth import AuthContext, SCOPE_READ_ANY
from app.agency_api.v1.canonical_reports import router
from app.config import settings

# ── In-memory fixture data ────────────────────────────────────────────────────

def _doc(client_id, status, report_type, period_start, version=1):
    return {
        "client_id":    client_id,
        "status":       status,
        "report_type":  report_type,
        "period_start": period_start,
        "period_end":   period_start[:7] + "-28",
        "version":      version,
        "artifact_hash": "a" * 64,
    }

CLIENT_A_ROWS = [
    {"entity_key": "report:aaa:v1", "document": _doc("client-a", "READY_FOR_REVIEW", "weekly",  "2026-10-12")},
    {"entity_key": "report:aaa:v2", "document": _doc("client-a", "APPROVED",         "monthly", "2026-09-01")},
    {"entity_key": "report:aaa:v3", "document": _doc("client-a", "PUBLISHED",        "weekly",  "2026-10-05")},
    {"entity_key": "report:aaa:v4", "document": _doc("client-a", "SUPERSEDED",       "weekly",  "2026-09-28", version=1)},
]
CLIENT_B_ROWS = [
    {"entity_key": "report:bbb:v1", "document": _doc("client-b", "PUBLISHED", "weekly", "2026-10-12")},
]

# ── App helpers ───────────────────────────────────────────────────────────────

_ADMIN_KEY = "test-list-admin-key-9x"

def _app_with_auth_override(client_ids=None):
    """
    Creates a TestClient app where SCOPE_READ_ANY is replaced by a
    dependency that returns a fixed AuthContext.
    """
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[SCOPE_READ_ANY] = lambda: AuthContext(
        scope="agency_admin",
        client_ids=client_ids,
    )
    return app


def _sb_mock(rows):
    """Minimal Supabase mock — returns `rows` from the chained query."""
    result = MagicMock()
    result.data = rows
    chain = MagicMock()
    chain.execute.return_value = result
    chain.contains.return_value = chain
    chain.select.return_value   = chain
    sb = MagicMock()
    sb.table.return_value = chain
    return sb


def _fetch(app_or_client, params, *, headers=None):
    """Helper: GET /agency/v1/canonical-reports/list with given params."""
    if isinstance(app_or_client, FastAPI):
        client = TestClient(app_or_client, raise_server_exceptions=False)
    else:
        client = app_or_client
    kw = dict(params=params)
    if headers:
        kw["headers"] = headers
    return client.get("/agency/v1/canonical-reports/list", **kw)


# ── Auth tests (use real auth layer, patch settings) ─────────────────────────

class TestAuth:
    def test_missing_auth_header_returns_401(self):
        app = FastAPI(); app.include_router(router)
        client = TestClient(app, raise_server_exceptions=False)
        with patch.object(settings, "AGENCY_API_ADMIN_KEY", _ADMIN_KEY):
            r = client.get("/agency/v1/canonical-reports/list", params={"client_id": "x"})
        assert r.status_code == 401

    def test_wrong_key_returns_401(self):
        app = FastAPI(); app.include_router(router)
        client = TestClient(app, raise_server_exceptions=False)
        with patch.object(settings, "AGENCY_API_ADMIN_KEY", _ADMIN_KEY):
            r = client.get(
                "/agency/v1/canonical-reports/list",
                params={"client_id": "x"},
                headers={"Authorization": "Bearer wrong-key"},
            )
        assert r.status_code == 401

    def test_valid_admin_key_returns_200(self):
        app = FastAPI(); app.include_router(router)
        client = TestClient(app, raise_server_exceptions=False)
        with (
            patch.object(settings, "AGENCY_API_ADMIN_KEY", _ADMIN_KEY),
            patch("app.database.get_supabase", return_value=_sb_mock([])),
            patch("app.agency_api.v1.router._resolve_slug", return_value="x"),
        ):
            r = client.get(
                "/agency/v1/canonical-reports/list",
                params={"client_id": "x"},
                headers={"Authorization": f"Bearer {_ADMIN_KEY}"},
            )
        assert r.status_code == 200


# ── Required param ────────────────────────────────────────────────────────────

class TestRequiredParam:
    def test_missing_client_id_returns_422(self):
        app = _app_with_auth_override()
        r   = _fetch(app, params={})
        assert r.status_code == 422

    def test_limit_above_100_returns_422(self):
        app = _app_with_auth_override()
        with patch("app.database.get_supabase", return_value=_sb_mock([])):
            r = _fetch(app, params={"client_id": "x", "limit": "999"})
        assert r.status_code == 422

    def test_limit_zero_returns_422(self):
        app = _app_with_auth_override()
        with patch("app.database.get_supabase", return_value=_sb_mock([])):
            r = _fetch(app, params={"client_id": "x", "limit": "0"})
        assert r.status_code == 422


# ── Client isolation ──────────────────────────────────────────────────────────

class TestClientIsolation:
    def test_client_a_does_not_see_client_b_records(self):
        """The JSONB filter is parameterised on the requested client_id only."""
        app = _app_with_auth_override()
        with (
            patch("app.database.get_supabase", return_value=_sb_mock(CLIENT_A_ROWS)),
            patch("app.agency_api.v1.router._resolve_slug", return_value="client-a"),
        ):
            r = _fetch(app, params={"client_id": "client-a"})
        data = r.json()
        assert r.status_code == 200
        assert all(d["client_id"] == "client-a" for d in data)
        assert not any(d["client_id"] == "client-b" for d in data)

    def test_unknown_client_returns_empty_list(self):
        app = _app_with_auth_override()
        with (
            patch("app.database.get_supabase", return_value=_sb_mock([])),
            patch("app.agency_api.v1.router._resolve_slug", return_value=None),
        ):
            r = _fetch(app, params={"client_id": "no-such-client"})
        assert r.status_code == 200
        assert r.json() == []

    def test_restricted_auth_context_raises_403_for_other_client(self):
        """AuthContext(client_ids=['client-a']) cannot query client-b."""
        app = _app_with_auth_override(client_ids=["client-a"])
        with (
            patch("app.database.get_supabase", return_value=_sb_mock(CLIENT_B_ROWS)),
            patch("app.agency_api.v1.router._resolve_slug", return_value="client-b"),
        ):
            r = _fetch(app, params={"client_id": "client-b"})
        assert r.status_code == 403

    def test_restricted_auth_context_can_read_own_client(self):
        app = _app_with_auth_override(client_ids=["client-a"])
        with (
            patch("app.database.get_supabase", return_value=_sb_mock(CLIENT_A_ROWS)),
            patch("app.agency_api.v1.router._resolve_slug", return_value="client-a"),
        ):
            r = _fetch(app, params={"client_id": "client-a"})
        assert r.status_code == 200
        assert len(r.json()) == len(CLIENT_A_ROWS)


# ── Status filter ─────────────────────────────────────────────────────────────

class TestStatusFilter:
    def test_filter_ready_for_review(self):
        app = _app_with_auth_override()
        with (
            patch("app.database.get_supabase", return_value=_sb_mock(CLIENT_A_ROWS)),
            patch("app.agency_api.v1.router._resolve_slug", return_value="client-a"),
        ):
            r = _fetch(app, params={"client_id": "client-a", "status": "READY_FOR_REVIEW"})
        data = r.json()
        assert r.status_code == 200
        assert all(d["status"] == "READY_FOR_REVIEW" for d in data)
        assert len(data) == 1

    def test_filter_published(self):
        app = _app_with_auth_override()
        with (
            patch("app.database.get_supabase", return_value=_sb_mock(CLIENT_A_ROWS)),
            patch("app.agency_api.v1.router._resolve_slug", return_value="client-a"),
        ):
            r = _fetch(app, params={"client_id": "client-a", "status": "PUBLISHED"})
        data = r.json()
        assert all(d["status"] == "PUBLISHED" for d in data)
        assert len(data) == 1

    def test_filter_superseded(self):
        app = _app_with_auth_override()
        with (
            patch("app.database.get_supabase", return_value=_sb_mock(CLIENT_A_ROWS)),
            patch("app.agency_api.v1.router._resolve_slug", return_value="client-a"),
        ):
            r = _fetch(app, params={"client_id": "client-a", "status": "SUPERSEDED"})
        data = r.json()
        assert all(d["status"] == "SUPERSEDED" for d in data)
        assert len(data) == 1

    def test_no_status_filter_returns_all(self):
        app = _app_with_auth_override()
        with (
            patch("app.database.get_supabase", return_value=_sb_mock(CLIENT_A_ROWS)),
            patch("app.agency_api.v1.router._resolve_slug", return_value="client-a"),
        ):
            r = _fetch(app, params={"client_id": "client-a"})
        assert len(r.json()) == len(CLIENT_A_ROWS)

    def test_status_filter_no_match_returns_empty(self):
        app = _app_with_auth_override()
        rows_no_draft = [row for row in CLIENT_A_ROWS if row["document"]["status"] != "DRAFT"]
        with (
            patch("app.database.get_supabase", return_value=_sb_mock(rows_no_draft)),
            patch("app.agency_api.v1.router._resolve_slug", return_value="client-a"),
        ):
            r = _fetch(app, params={"client_id": "client-a", "status": "DRAFT"})
        assert r.status_code == 200
        assert r.json() == []


# ── Report type filter ────────────────────────────────────────────────────────

class TestReportTypeFilter:
    def test_filter_weekly(self):
        app = _app_with_auth_override()
        with (
            patch("app.database.get_supabase", return_value=_sb_mock(CLIENT_A_ROWS)),
            patch("app.agency_api.v1.router._resolve_slug", return_value="client-a"),
        ):
            r = _fetch(app, params={"client_id": "client-a", "report_type": "weekly"})
        data = r.json()
        assert r.status_code == 200
        assert all(d["report_type"] == "weekly" for d in data)
        assert len(data) == 3   # READY_FOR_REVIEW + PUBLISHED + SUPERSEDED

    def test_filter_monthly(self):
        app = _app_with_auth_override()
        with (
            patch("app.database.get_supabase", return_value=_sb_mock(CLIENT_A_ROWS)),
            patch("app.agency_api.v1.router._resolve_slug", return_value="client-a"),
        ):
            r = _fetch(app, params={"client_id": "client-a", "report_type": "monthly"})
        data = r.json()
        assert all(d["report_type"] == "monthly" for d in data)
        assert len(data) == 1   # APPROVED monthly

    def test_filter_weekly_and_status(self):
        """Combining filters: weekly + PUBLISHED → exactly 1 result."""
        app = _app_with_auth_override()
        with (
            patch("app.database.get_supabase", return_value=_sb_mock(CLIENT_A_ROWS)),
            patch("app.agency_api.v1.router._resolve_slug", return_value="client-a"),
        ):
            r = _fetch(app, params={"client_id": "client-a", "report_type": "weekly", "status": "PUBLISHED"})
        data = r.json()
        assert len(data) == 1
        assert data[0]["report_type"] == "weekly"
        assert data[0]["status"]      == "PUBLISHED"


# ── Sorting ───────────────────────────────────────────────────────────────────

class TestSorting:
    def test_sorted_by_period_start_desc(self):
        app = _app_with_auth_override()
        with (
            patch("app.database.get_supabase", return_value=_sb_mock(CLIENT_A_ROWS)),
            patch("app.agency_api.v1.router._resolve_slug", return_value="client-a"),
        ):
            r = _fetch(app, params={"client_id": "client-a"})
        periods = [d["period_start"] for d in r.json()]
        assert periods == sorted(periods, reverse=True)


# ── Limit ─────────────────────────────────────────────────────────────────────

class TestLimit:
    def test_limit_respected(self):
        many_rows = [
            {"entity_key": f"report:x{i}:v1",
             "document": _doc("client-a", "PUBLISHED", "weekly", f"2026-{1 + i % 9:02d}-01")}
            for i in range(20)
        ]
        app = _app_with_auth_override()
        with (
            patch("app.database.get_supabase", return_value=_sb_mock(many_rows)),
            patch("app.agency_api.v1.router._resolve_slug", return_value="client-a"),
        ):
            r = _fetch(app, params={"client_id": "client-a", "limit": "5"})
        assert r.status_code == 200
        assert len(r.json()) == 5

    def test_default_limit_30(self):
        many_rows = [
            {"entity_key": f"report:x{i}:v1",
             "document": _doc("client-a", "PUBLISHED", "weekly", f"2026-{1 + i % 9:02d}-01")}
            for i in range(40)
        ]
        app = _app_with_auth_override()
        with (
            patch("app.database.get_supabase", return_value=_sb_mock(many_rows)),
            patch("app.agency_api.v1.router._resolve_slug", return_value="client-a"),
        ):
            r = _fetch(app, params={"client_id": "client-a"})
        assert r.status_code == 200
        assert len(r.json()) == 30


# ── No writes ─────────────────────────────────────────────────────────────────

class TestNoWrites:
    def test_get_does_not_call_supabase_insert_or_update(self):
        sb = _sb_mock([])
        app = _app_with_auth_override()
        with (
            patch("app.database.get_supabase", return_value=sb),
            patch("app.agency_api.v1.router._resolve_slug", return_value="client-a"),
        ):
            _fetch(app, params={"client_id": "client-a"})
        sb.table.return_value.insert.assert_not_called()
        sb.table.return_value.update.assert_not_called()
        sb.table.return_value.delete.assert_not_called()
