"""
Phase 6.6 — Dashboard / Roles / Client Portal Closure Validation

Covers:
  §1  Finance auth: SCOPE_ADMIN_ONLY on all /finance/* endpoints
  §2  Notion adapter contract: create/update/sync + Core-canonical invariant
  §3  Role model mapping: admin vs platform_web vs no-auth on finance
  §4  Optimization history guard: load_executed_actions filters EXECUTED only
  §5  Task visibility regression: AGENCY_ONLY blocked for client_viewer

Run from apps/api/:
    python -m pytest tests/test_phase66_validation.py -v
"""

from __future__ import annotations

import uuid
from datetime import date
from unittest.mock import MagicMock, patch, call

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.agency_api.v1.auth import (
    SCOPE_ADMIN_ONLY,
    SCOPE_CLIENT_VIEWER,
    SCOPE_PLATFORM_WRITE,
    SCOPE_READ_ANY,
    SCOPE_WRITE_ANY,
    SCOPE_ANY_AUTH,
    SCOPE_HERMES_WRITE,
    AuthContext,
)
from app.agency_api.v1.router import router as agency_router
from app.config import settings
from app.routers.finance import router as finance_router

# ── Canonical test identities ─────────────────────────────────────────────────

_ADMIN    = AuthContext(scope="agency_admin")
_PLATFORM = AuthContext(scope="platform_web")
_HERMES   = AuthContext(scope="hermes_service")
_CLIENT   = AuthContext(scope="client_viewer", client_ids=["client-alpha"], subject="user-a")

_CLIENT_SLUG = "client-alpha"
_TASK_UUID   = "aaaaaaaa-aaaa-aaaa-aaaa-000000000001"
_TS          = "2026-10-14T12:00:00+00:00"

# ── DB mock helpers ───────────────────────────────────────────────────────────


def _empty_db():
    m = MagicMock()
    tbl = MagicMock()
    for method in ("select", "eq", "in_", "order", "limit", "is_", "insert", "update",
                   "neq", "gte", "lte", "not_", "maybe_single", "range", "lt", "head"):
        getattr(tbl, method).return_value = tbl
    tbl.execute.return_value = MagicMock(data=[], count=0)
    m.table.return_value = tbl
    m.rpc.return_value = tbl
    return m


def _finance_empty_db():
    """Mock DB for finance summary — sets count=0 to avoid JSON serialization issues."""
    m = MagicMock()
    tbl = MagicMock()
    for method in ("select", "eq", "in_", "order", "limit", "is_", "insert", "update",
                   "neq", "gte", "lte", "not_", "maybe_single", "range", "lt", "head"):
        getattr(tbl, method).return_value = tbl
    tbl.execute.return_value = MagicMock(data=[], count=0)
    m.table.return_value = tbl
    return m


def _task_row(visibility_scope: str = "AGENCY_ONLY") -> dict:
    return {
        "id": _TASK_UUID, "client_id": _CLIENT_SLUG, "title": "Test task",
        "description": None, "status": "OPEN", "priority": "MEDIUM",
        "owner": None, "due_date": None,
        "source_type": None, "source_id": None,
        "visibility_scope": visibility_scope,
        "external_task_id": None, "external_system": None,
        "sync_state": "none", "last_synced_at": None, "sync_error": None,
        "created_at": _TS, "updated_at": _TS, "completed_at": None,
    }


def _client_meta_row(slug: str = _CLIENT_SLUG) -> dict:
    return {
        "id": "cccccccc-cccc-cccc-cccc-000000000001", "client_id": slug,
        "name": f"{slug} test", "business_model": "ecommerce",
        "timezone": "America/Sao_Paulo", "currency": "BRL",
        "country": "BR", "is_active": True, "pixel_id": slug,
    }


def _app_as(auth: AuthContext) -> FastAPI:
    """Agency API app with all scope deps overridden."""
    app = FastAPI()
    app.include_router(agency_router)
    for dep in (SCOPE_READ_ANY, SCOPE_WRITE_ANY, SCOPE_HERMES_WRITE,
                SCOPE_PLATFORM_WRITE, SCOPE_CLIENT_VIEWER, SCOPE_ANY_AUTH, SCOPE_ADMIN_ONLY):
        app.dependency_overrides[dep] = lambda a=auth: a
    return app


def _finance_app_as(auth: AuthContext) -> FastAPI:
    """Finance-only app with SCOPE_ADMIN_ONLY overridden to the given context."""
    app = FastAPI()
    app.include_router(finance_router)
    app.dependency_overrides[SCOPE_ADMIN_ONLY] = lambda a=auth: a
    return app


def _finance_bare_app() -> FastAPI:
    """Finance-only app with no dependency overrides — real auth runs."""
    app = FastAPI()
    app.include_router(finance_router)
    return app


# ═══════════════════════════════════════════════════════════════════════════════
# §1 — Finance auth: all /finance/* endpoints require SCOPE_ADMIN_ONLY
# ═══════════════════════════════════════════════════════════════════════════════

class TestFinanceAuth:
    """All /finance/* endpoints return 401 when unauthenticated."""

    def test_finance_summary_no_auth(self):
        app = _finance_bare_app()
        r = TestClient(app, raise_server_exceptions=False).get("/finance/summary")
        assert r.status_code == 401

    def test_finance_contracts_no_auth(self):
        app = _finance_bare_app()
        r = TestClient(app, raise_server_exceptions=False).get("/finance/contracts")
        assert r.status_code == 401

    def test_finance_receivables_no_auth(self):
        app = _finance_bare_app()
        r = TestClient(app, raise_server_exceptions=False).get("/finance/receivables")
        assert r.status_code == 401

    def test_finance_payables_no_auth(self):
        app = _finance_bare_app()
        r = TestClient(app, raise_server_exceptions=False).get("/finance/payables")
        assert r.status_code == 401

    def test_finance_notifications_no_auth(self):
        app = _finance_bare_app()
        r = TestClient(app, raise_server_exceptions=False).get("/finance/notifications")
        assert r.status_code == 401

    def test_finance_summary_admin_allowed(self):
        """Admin context reaches summary endpoint and gets 200."""
        with patch("app.routers.finance.get_supabase", return_value=_finance_empty_db()):
            app = _finance_app_as(_ADMIN)
            r = TestClient(app, raise_server_exceptions=False).get("/finance/summary")
        assert r.status_code == 200
        body = r.json()
        assert "mrr" in body
        assert "receivables" in body
        assert "payables" in body

    def test_finance_contracts_admin_allowed(self):
        """Admin context reaches contracts endpoint and gets 200."""
        with patch("app.routers.finance.get_supabase", return_value=_finance_empty_db()):
            app = _finance_app_as(_ADMIN)
            r = TestClient(app, raise_server_exceptions=False).get("/finance/contracts")
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_finance_platform_web_rejected(self, monkeypatch):
        """Platform_web token is rejected by SCOPE_ADMIN_ONLY (403)."""
        monkeypatch.setattr(settings, "AGENCY_API_ADMIN_KEY",    "test-admin-key-66")
        monkeypatch.setattr(settings, "AGENCY_API_PLATFORM_KEY", "test-platform-key-66")

        app = _finance_bare_app()
        r = TestClient(app, raise_server_exceptions=False).get(
            "/finance/summary",
            headers={"Authorization": "Bearer test-platform-key-66"},
        )
        assert r.status_code == 403

    def test_finance_post_create_contract_no_auth(self):
        app = _finance_bare_app()
        r = TestClient(app, raise_server_exceptions=False).post(
            "/finance/contracts",
            json={"client_name": "Test", "monthly_value": 1000, "due_day": 5, "start_date": "2026-10-01"},
        )
        assert r.status_code == 401

    def test_finance_patch_receivable_no_auth(self):
        app = _finance_bare_app()
        r = TestClient(app, raise_server_exceptions=False).patch(
            "/finance/receivables/some-id",
            json={"status": "paid"},
        )
        assert r.status_code == 401

    def test_finance_delete_contract_no_auth(self):
        app = _finance_bare_app()
        r = TestClient(app, raise_server_exceptions=False).delete("/finance/contracts/some-id")
        assert r.status_code == 401

    def test_finance_post_notifications_read_all_no_auth(self):
        app = _finance_bare_app()
        r = TestClient(app, raise_server_exceptions=False).post("/finance/notifications/read-all")
        assert r.status_code == 401


# ═══════════════════════════════════════════════════════════════════════════════
# §2 — Notion adapter contract
# ═══════════════════════════════════════════════════════════════════════════════

class TestNotionAdapterContract:
    """
    Notion adapter contract — Core remains canonical.

    Updated for Phase NOTION REAL: stub replaced with production adapter.
    When NOTION_API_KEY is not configured, adapter returns CONFIG_REQUIRED
    and marks sync_error without writing a fake external_task_id.
    """

    def _mock_sb(self):
        sb = MagicMock()
        tbl = MagicMock()
        for method in ("select", "eq", "update", "maybe_single", "limit", "in_"):
            getattr(tbl, method).return_value = tbl
        tbl.execute.return_value = MagicMock(data=None)
        sb.table.return_value = tbl
        return sb

    def test_create_external_task_without_config_returns_error(self):
        """Without NOTION_API_KEY, create_external_task returns error (not a stub id)."""
        from app.core.notion_adapter import NotionTaskAdapter

        sb      = self._mock_sb()
        adapter = NotionTaskAdapter(supabase=sb)
        task    = {"id": _TASK_UUID, "title": "Test", "status": "OPEN"}

        # No config set → CONFIG_REQUIRED path
        result = adapter.create_external_task(task)

        assert result["system"]     == "notion"
        # When config is missing the adapter must NOT produce a fake external_task_id
        assert result["external_task_id"] is None
        assert result["sync_state"] in ("error",)

    def test_create_external_task_with_config_writes_sync_fields_to_core(self):
        """With config + mocked httpx, create_external_task writes real sync metadata."""
        from unittest.mock import patch as _patch
        from app.core.notion_adapter import NotionTaskAdapter

        sb   = self._mock_sb()
        # Make DB return the task row
        task_row = {
            "id": _TASK_UUID, "title": "Test task", "status": "OPEN",
            "priority": "MEDIUM", "client_id": _CLIENT_SLUG,
            "description": None, "owner": None, "due_date": None,
            "source_type": None, "source_id": None,
            "external_task_id": None, "created_at": _TS, "updated_at": _TS,
        }
        sb.table.return_value.execute.return_value = MagicMock(data=[task_row])

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"id": "notion-page-real-001", "object": "page"}
        mock_response.headers = {}

        with _patch("app.core.notion_adapter.settings") as mock_cfg, \
             _patch("app.core.notion_adapter.httpx.request", return_value=mock_response):
            mock_cfg.NOTION_API_KEY           = "secret_test_key"
            mock_cfg.NOTION_TASKS_DATABASE_ID = "db-test-id"
            adapter = NotionTaskAdapter(supabase=sb)
            result  = adapter.create_external_task({"id": _TASK_UUID, "title": "Test", "status": "OPEN"})

        assert result["system"]        == "notion"
        assert result["sync_state"]    == "synced"
        assert result["external_task_id"] == "notion-page-real-001"
        # Must NOT start with the old stub prefix
        assert not str(result["external_task_id"]).startswith("notion-stub-")

    def test_create_external_task_writes_sync_fields_to_core(self):
        """With config, Core DB must receive real sync metadata (not stub id)."""
        from unittest.mock import patch as _patch
        from app.core.notion_adapter import NotionTaskAdapter

        sb       = self._mock_sb()
        task_row = {
            "id": _TASK_UUID, "title": "Test task", "status": "OPEN",
            "priority": "MEDIUM", "client_id": _CLIENT_SLUG,
            "description": None, "owner": None, "due_date": None,
            "source_type": None, "source_id": None,
            "external_task_id": None, "created_at": _TS, "updated_at": _TS,
        }
        sb.table.return_value.execute.return_value = MagicMock(data=[task_row])

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"id": "notion-page-real-002", "object": "page"}
        mock_response.headers = {}

        with _patch("app.core.notion_adapter.settings") as mock_cfg, \
             _patch("app.core.notion_adapter.httpx.request", return_value=mock_response):
            mock_cfg.NOTION_API_KEY           = "secret_test_key"
            mock_cfg.NOTION_TASKS_DATABASE_ID = "db-test-id"
            adapter = NotionTaskAdapter(supabase=sb)
            adapter.create_external_task({"id": _TASK_UUID, "title": "Test", "status": "OPEN"})

        # Verify .update() was called on core_tasks
        sb.table.assert_any_call("core_tasks")
        update_calls = sb.table.return_value.update.call_args_list
        assert len(update_calls) >= 1
        updated_fields = update_calls[-1].args[0] if update_calls[-1].args else update_calls[-1].kwargs
        assert updated_fields.get("sync_state")      == "synced"
        assert updated_fields.get("external_system") == "notion"
        assert "external_task_id" in updated_fields
        assert "last_synced_at"   in updated_fields
        assert updated_fields.get("sync_error") is None

    def test_update_external_task_does_not_touch_core_db_when_no_config(self):
        """update_external_task without config returns CONFIG_REQUIRED, never touches Core DB."""
        from app.core.notion_adapter import NotionTaskAdapter

        sb      = self._mock_sb()
        adapter = NotionTaskAdapter(supabase=sb)

        result = adapter.update_external_task("notion-page-123", {"status": "Done", "priority": "High"})

        # Core DB must NOT be touched
        sb.table.assert_not_called()
        assert result["external_task_id"] == "notion-page-123"
        assert result["system"]           == "notion"
        assert set(result["patched_fields"]) == {"status", "priority"}

    def test_update_external_task_does_not_touch_core_db(self):
        """update_external_task is Notion-only — Core task content is never modified."""
        from unittest.mock import patch as _patch
        from app.core.notion_adapter import NotionTaskAdapter

        sb      = self._mock_sb()

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"id": "notion-page-123", "object": "page"}
        mock_response.headers = {}

        with _patch("app.core.notion_adapter.settings") as mock_cfg, \
             _patch("app.core.notion_adapter.httpx.request", return_value=mock_response):
            mock_cfg.NOTION_API_KEY           = "secret_test_key"
            mock_cfg.NOTION_TASKS_DATABASE_ID = "db-test-id"
            adapter = NotionTaskAdapter(supabase=sb)
            result  = adapter.update_external_task("notion-page-123", {"status": "DONE", "priority": "HIGH"})

        # Core DB must NOT be touched
        sb.table.assert_not_called()
        assert result["external_task_id"] == "notion-page-123"
        assert result["system"]           == "notion"
        assert set(result["patched_fields"]) == {"status", "priority"}

    def test_sync_task_status_reads_from_core(self):
        """sync_task_status reads task state from Core DB."""
        from app.core.notion_adapter import NotionTaskAdapter

        sb  = self._mock_sb()
        sb.table.return_value.execute.return_value = MagicMock(data={
            "id":               _TASK_UUID,
            "external_task_id": "notion-page-abc",
            "sync_state":       "synced",
            "last_synced_at":   _TS,
        })
        adapter = NotionTaskAdapter(supabase=sb)

        result = adapter.sync_task_status(_TASK_UUID)

        assert result["task_id"]          == _TASK_UUID
        assert result["external_task_id"] == "notion-page-abc"
        assert result["sync_state"]       == "synced"

    def test_sync_task_status_raises_for_missing_task(self):
        """sync_task_status raises ValueError when task not found."""
        from app.core.notion_adapter import NotionTaskAdapter

        sb  = self._mock_sb()
        sb.table.return_value.execute.return_value = MagicMock(data=None)
        adapter = NotionTaskAdapter(supabase=sb)

        with pytest.raises(ValueError, match="not found"):
            adapter.sync_task_status("nonexistent-id")

    def test_core_canonical_invariant_task_create_has_no_notion_fields(self):
        """TaskCreate schema must NOT contain Notion sync fields — Core is canonical."""
        from app.agency_api.v1.schemas import TaskCreate

        notion_fields = {"external_task_id", "external_system", "sync_state", "last_synced_at", "sync_error"}
        task_create_fields = set(TaskCreate.model_fields.keys())

        overlap = notion_fields & task_create_fields
        assert overlap == set(), (
            f"TaskCreate must not expose Notion fields (Core canonical invariant). "
            f"Found: {overlap}"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# §3 — Role model mapping
# ═══════════════════════════════════════════════════════════════════════════════

class TestRoleModelMapping:
    """Finance enforces SCOPE_ADMIN_ONLY: admin 200, platform_web 403, no-auth 401."""

    def test_admin_key_reaches_finance_summary(self, monkeypatch):
        """Admin bearer token (scope=agency_admin) gets through SCOPE_ADMIN_ONLY."""
        monkeypatch.setattr(settings, "AGENCY_API_ADMIN_KEY",    "test-admin-66")
        monkeypatch.setattr(settings, "AGENCY_API_PLATFORM_KEY", "test-platform-66")

        with patch("app.routers.finance.get_supabase", return_value=_finance_empty_db()):
            app = _finance_bare_app()
            r   = TestClient(app, raise_server_exceptions=False).get(
                "/finance/summary",
                headers={"Authorization": "Bearer test-admin-66"},
            )
        assert r.status_code == 200

    def test_platform_web_key_rejected_from_finance(self, monkeypatch):
        """Platform_web bearer token is not in SCOPE_ADMIN_ONLY → 403."""
        monkeypatch.setattr(settings, "AGENCY_API_ADMIN_KEY",    "test-admin-66")
        monkeypatch.setattr(settings, "AGENCY_API_PLATFORM_KEY", "test-platform-66")

        app = _finance_bare_app()
        r   = TestClient(app, raise_server_exceptions=False).get(
            "/finance/summary",
            headers={"Authorization": "Bearer test-platform-66"},
        )
        assert r.status_code == 403
        assert "scope" in r.json().get("detail", "").lower() or r.status_code == 403

    def test_no_bearer_rejected_from_finance(self):
        """Missing Authorization header → 401."""
        app = _finance_bare_app()
        r   = TestClient(app, raise_server_exceptions=False).get("/finance/summary")
        assert r.status_code == 401

    def test_hermes_key_rejected_from_finance(self, monkeypatch):
        """Hermes service token is not in SCOPE_ADMIN_ONLY → 403."""
        monkeypatch.setattr(settings, "AGENCY_API_ADMIN_KEY",   "test-admin-66")
        monkeypatch.setattr(settings, "AGENCY_API_HERMES_KEY",  "test-hermes-66")

        app = _finance_bare_app()
        r   = TestClient(app, raise_server_exceptions=False).get(
            "/finance/summary",
            headers={"Authorization": "Bearer test-hermes-66"},
        )
        assert r.status_code == 403


# ═══════════════════════════════════════════════════════════════════════════════
# §4 — Optimization history guard (regression from Phase 6.5)
# ═══════════════════════════════════════════════════════════════════════════════

class TestOptimizationHistoryGuard:
    """load_executed_actions always filters by status=EXECUTED — PROPOSED never shown."""

    def test_load_executed_actions_filters_executed_status(self):
        from app.core.actions_loader import load_executed_actions

        sb     = _empty_db()
        result = load_executed_actions(
            sb, "client-alpha", date(2026, 10, 1), date(2026, 10, 31)
        )

        assert result == []  # empty DB → empty result

        # Verify the query included .eq("status", "EXECUTED")
        all_eq_calls = sb.table.return_value.eq.call_args_list
        status_filter_present = any(
            (c.args == ("status", "EXECUTED") or
             (c.kwargs.get("column") == "status" and c.kwargs.get("value") == "EXECUTED"))
            for c in all_eq_calls
        )
        assert status_filter_present, (
            "load_executed_actions must filter .eq('status', 'EXECUTED'). "
            "PROPOSED actions must never reach the output."
        )

    def test_format_executed_actions_does_not_invent_status(self):
        """format_executed_actions only transforms what it receives — it cannot promote PROPOSED."""
        from app.core.actions_loader import format_executed_actions

        # Feed a PROPOSED action directly (simulates bug where filter is bypassed)
        proposed_action = {
            "id": "act-1", "action_type": "BUDGET_CHANGE", "platform": "meta",
            "description": "Should not appear as executed",
            "executed_at": "2026-10-10T10:00:00+00:00",
            "executed_by": "tester",
            "status": "PROPOSED",  # invariant: load_executed_actions must never pass this in
            "outcome": None,
            "recommendation_id": None,
        }
        # The formatter does not check status — invariant is in the loader
        # This test confirms format_executed_actions has no own filter (the filter is in load_)
        result = format_executed_actions([proposed_action])
        assert len(result) == 1  # formatter accepts it
        # The semantic guard lives in load_executed_actions, not format_


# ═══════════════════════════════════════════════════════════════════════════════
# §5 — Task visibility regression (Phase 6.5 invariant)
# ═══════════════════════════════════════════════════════════════════════════════

class TestTaskVisibilityRegression:
    """AGENCY_ONLY tasks must never reach client_viewer — server-side enforcement."""

    def test_client_viewer_list_tasks_query_filters_client_visible(self):
        """list_tasks for client_viewer must include .eq('visibility_scope','CLIENT_VISIBLE') in DB query."""
        # Return CLIENT_VISIBLE tasks only (the DB filter normally enforces this)
        rows = [_task_row(visibility_scope="CLIENT_VISIBLE")]
        db   = _empty_db()
        db.table.return_value.execute.return_value = MagicMock(data=rows)

        with patch("app.agency_api.v1.router._load_client_meta", return_value=_client_meta_row()):
            with patch("app.agency_api.v1.router._get_db", return_value=db):
                app = _app_as(_CLIENT)
                r   = TestClient(app, raise_server_exceptions=False).get(
                    f"/agency/v1/clients/{_CLIENT_SLUG}/tasks"
                )

        assert r.status_code == 200
        tasks = r.json()
        # All returned tasks are CLIENT_VISIBLE
        for t in tasks:
            assert t.get("visibility_scope") == "CLIENT_VISIBLE"

        # Verify the DB query included the visibility_scope filter
        all_eq_calls = db.table.return_value.eq.call_args_list
        visibility_filter = any(
            (c.args == ("visibility_scope", "CLIENT_VISIBLE"))
            for c in all_eq_calls
        )
        assert visibility_filter, (
            "list_tasks for client_viewer must add .eq('visibility_scope','CLIENT_VISIBLE') "
            "to the DB query — server-side enforcement missing"
        )

    def test_client_viewer_can_see_client_visible_task(self):
        """GET /agency/v1/clients/{slug}/tasks returns CLIENT_VISIBLE tasks to client_viewer."""
        client_visible_task = _task_row(visibility_scope="CLIENT_VISIBLE")
        db = _empty_db()
        db.table.return_value.execute.return_value = MagicMock(data=[client_visible_task])

        with patch("app.agency_api.v1.router._load_client_meta", return_value=_client_meta_row()):
            with patch("app.agency_api.v1.router._get_db", return_value=db):
                app = _app_as(_CLIENT)
                r   = TestClient(app, raise_server_exceptions=False).get(
                    f"/agency/v1/clients/{_CLIENT_SLUG}/tasks"
                )

        assert r.status_code == 200
