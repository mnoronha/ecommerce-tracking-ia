"""
Phase NOTION REAL — Production sync test suite.

Tests the production NotionTaskAdapter with mocked httpx and Supabase.

Coverage:
  §1  Core → Notion create (new page)
  §2  Core → Notion update (existing page — idempotency)
  §3  Notion → Core writeback (status, owner, due_date, priority)
  §4  Duplicate prevention (idempotency via Core Task ID search)
  §5  Retry: 429 with backoff
  §6  Auth errors: 401/403 no retry
  §7  External missing: 404 marks external_missing, preserves task
  §8  Sync loop guard
  §9  Invalid/unknown Notion status ignored
  §10 Protected fields blocked from writeback
  §11 CONFIG_REQUIRED — no secret exposure, no fake external_task_id
  §12 Batch sync_pending_tasks_to_notion
  §13 poll_notion_task_updates
  §14 Client isolation (tasks are per-client)

Run:
    cd apps/api && python -m pytest tests/test_notion_sync.py -v
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone, timedelta
from typing import Any
from unittest.mock import MagicMock, patch, call

import pytest

# ── Test identities ───────────────────────────────────────────────────────────

_TASK_UUID  = "aaaaaaaa-aaaa-aaaa-aaaa-000000000001"
_TASK_UUID2 = "bbbbbbbb-bbbb-bbbb-bbbb-000000000002"
_CLIENT     = "client-alpha"
_TS         = "2026-10-16T10:00:00+00:00"
_PAGE_ID    = "notion-page-prod-001"
_DB_ID      = "db-test-id-notion"
_API_KEY    = "secret_test_key_notion"


# ── Mock builders ─────────────────────────────────────────────────────────────

def _make_sb(task_rows: list[dict] | None = None):
    """Build a Supabase mock. task_rows feeds select().execute().data."""
    sb  = MagicMock()
    tbl = MagicMock()
    for method in ("select", "eq", "update", "maybe_single", "limit", "in_", "insert", "neq"):
        getattr(tbl, method).return_value = tbl
    tbl.execute.return_value = MagicMock(data=task_rows or [])
    sb.table.return_value = tbl
    return sb


def _make_http_resp(status: int, body: dict | None = None, headers: dict | None = None):
    resp = MagicMock()
    resp.status_code = status
    resp.json.return_value = body or {}
    resp.headers = headers or {}
    return resp


def _task_row(
    task_id: str = _TASK_UUID,
    status: str = "OPEN",
    external_task_id: str | None = None,
    last_synced_at: str | None = None,
) -> dict:
    return {
        "id":               task_id,
        "client_id":        _CLIENT,
        "title":            "Test task",
        "description":      None,
        "status":           status,
        "priority":         "MEDIUM",
        "owner":            None,
        "due_date":         None,
        "source_type":      None,
        "source_id":        None,
        "external_task_id": external_task_id,
        "last_synced_at":   last_synced_at,
        "sync_state":       "synced" if external_task_id else "none",
        "sync_error":       None,
        "created_at":       _TS,
        "updated_at":       _TS,
    }


def _notion_page(
    page_id: str = _PAGE_ID,
    core_task_id: str = _TASK_UUID,
    status: str = "Open",
    owner: str | None = None,
    due_date: str | None = None,
    priority: str = "Medium",
    last_edited: str = "2026-10-16T12:00:00.000Z",
) -> dict:
    """Simulate a Notion page response."""
    return {
        "object": "page",
        "id": page_id,
        "last_edited_time": last_edited,
        "properties": {
            "Core Task ID": {
                "rich_text": [{"text": {"content": core_task_id}}]
            },
            "Status":   {"select": {"name": status}},
            "Priority": {"select": {"name": priority}},
            "Owner":    {"rich_text": [{"text": {"content": owner or ""}}]},
            "Due Date": {"date": {"start": due_date} if due_date else None},
            "Task":     {"title": [{"text": {"content": "Test task"}}]},
        },
    }


def _patch_settings(key: str = _API_KEY, db_id: str = _DB_ID):
    m = MagicMock()
    m.NOTION_API_KEY           = key
    m.NOTION_TASKS_DATABASE_ID = db_id
    return m


# ═══════════════════════════════════════════════════════════════════════════════
# §1 — Core → Notion create (new page)
# ═══════════════════════════════════════════════════════════════════════════════

class TestCoreToNotionCreate:

    def test_create_new_page_success(self):
        """sync_task_to_notion creates a Notion page and writes sync metadata to Core."""
        from app.core.notion_adapter import NotionTaskAdapter

        task  = _task_row()
        sb    = _make_sb([task])
        # First httpx call = search (empty result), second = create page
        search_resp = _make_http_resp(200, {"results": []})
        create_resp = _make_http_resp(200, {"id": _PAGE_ID, "object": "page"})

        with patch("app.core.notion_adapter.settings", _patch_settings()), \
             patch("app.core.notion_adapter.httpx.request", side_effect=[search_resp, create_resp]):
            adapter = NotionTaskAdapter(supabase=sb)
            result  = adapter.sync_task_to_notion(_TASK_UUID)

        assert result["status"]           == "synced"
        assert result["external_task_id"] == _PAGE_ID
        assert result["operation"]        == "create"
        assert result["task_id"]          == _TASK_UUID

    def test_create_writes_sync_metadata_to_core(self):
        """After create, Core task gets external_task_id, sync_state=synced, sync_error=None."""
        from app.core.notion_adapter import NotionTaskAdapter

        task  = _task_row()
        sb    = _make_sb([task])
        search_resp = _make_http_resp(200, {"results": []})
        create_resp = _make_http_resp(200, {"id": _PAGE_ID, "object": "page"})

        with patch("app.core.notion_adapter.settings", _patch_settings()), \
             patch("app.core.notion_adapter.httpx.request", side_effect=[search_resp, create_resp]):
            NotionTaskAdapter(supabase=sb).sync_task_to_notion(_TASK_UUID)

        update_calls = sb.table.return_value.update.call_args_list
        assert len(update_calls) >= 1
        fields = update_calls[-1].args[0] if update_calls[-1].args else update_calls[-1].kwargs
        assert fields["external_task_id"] == _PAGE_ID
        assert fields["external_system"]  == "notion"
        assert fields["sync_state"]       == "synced"
        assert fields["sync_error"]       is None
        assert "last_synced_at" in fields

    def test_create_task_not_found(self):
        """sync_task_to_notion returns error if task not in DB."""
        from app.core.notion_adapter import NotionTaskAdapter

        sb = _make_sb([])  # empty DB

        with patch("app.core.notion_adapter.settings", _patch_settings()):
            result = NotionTaskAdapter(supabase=sb).sync_task_to_notion(_TASK_UUID)

        assert result["status"] == "error"
        assert result["error"]  == "task_not_found"


# ═══════════════════════════════════════════════════════════════════════════════
# §2 — Core → Notion update (existing page)
# ═══════════════════════════════════════════════════════════════════════════════

class TestCoreToNotionUpdate:

    def test_update_existing_page_by_external_task_id(self):
        """If external_task_id already set, PATCH the existing page (skip search)."""
        from app.core.notion_adapter import NotionTaskAdapter

        task   = _task_row(external_task_id=_PAGE_ID)
        sb     = _make_sb([task])
        update_resp = _make_http_resp(200, {"id": _PAGE_ID, "object": "page"})

        calls_made = []
        def mock_http(method, url, **kwargs):
            calls_made.append((method, url))
            return update_resp

        with patch("app.core.notion_adapter.settings", _patch_settings()), \
             patch("app.core.notion_adapter.httpx.request", side_effect=mock_http):
            result = NotionTaskAdapter(supabase=sb).sync_task_to_notion(_TASK_UUID)

        assert result["status"]    == "synced"
        assert result["operation"] == "update"
        # Should have made exactly 1 HTTP call (PATCH, no search)
        assert len(calls_made) == 1
        assert calls_made[0][0] == "PATCH"

    def test_update_status_mapping_sent_to_notion(self):
        """Core status DONE maps to Notion status 'Done' in the PATCH payload."""
        from app.core.notion_adapter import NotionTaskAdapter

        task  = _task_row(status="DONE", external_task_id=_PAGE_ID)
        sb    = _make_sb([task])
        payloads = []

        def mock_http(method, url, *, json=None, **kwargs):
            payloads.append(json)
            return _make_http_resp(200, {"id": _PAGE_ID, "object": "page"})

        with patch("app.core.notion_adapter.settings", _patch_settings()), \
             patch("app.core.notion_adapter.httpx.request", side_effect=mock_http):
            NotionTaskAdapter(supabase=sb).sync_task_to_notion(_TASK_UUID)

        notion_status = payloads[0]["properties"]["Status"]["select"]["name"]
        assert notion_status == "Done"


# ═══════════════════════════════════════════════════════════════════════════════
# §3 — Notion → Core writeback
# ═══════════════════════════════════════════════════════════════════════════════

class TestNotionToCoreWriteback:

    def _make_sb_with_task(self, task: dict):
        sb  = MagicMock()
        tbl = MagicMock()
        for method in ("select", "eq", "update", "maybe_single", "limit", "in_"):
            getattr(tbl, method).return_value = tbl
        tbl.execute.return_value = MagicMock(data=[task])
        sb.table.return_value = tbl
        return sb

    def test_status_writeback_open_to_in_progress(self):
        """Notion status 'In Progress' maps to Core status IN_PROGRESS."""
        from app.core.notion_adapter import NotionTaskAdapter

        # last_synced_at is old (60 min ago) — guard does not block
        synced_at = (datetime.now(timezone.utc) - timedelta(minutes=60)).isoformat()
        task = _task_row(external_task_id=_PAGE_ID, last_synced_at=synced_at)
        sb   = self._make_sb_with_task(task)

        edited_at = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
        page = _notion_page(status="In Progress", last_edited=edited_at.replace("+00:00", "Z"))

        with patch("app.core.notion_adapter.settings", _patch_settings()), \
             patch("app.core.notion_adapter.httpx.request", return_value=_make_http_resp(200, page)):
            result = NotionTaskAdapter(supabase=sb).sync_task_from_notion(_PAGE_ID)

        assert result["status"]         == "applied"
        assert "status" in result["fields_applied"]
        # Verify DB update
        update_calls = sb.table.return_value.update.call_args_list
        patch_data = update_calls[-1].args[0] if update_calls[-1].args else update_calls[-1].kwargs
        assert patch_data["status"] == "IN_PROGRESS"

    def test_owner_writeback(self):
        """Notion Owner field writes back to Core owner."""
        from app.core.notion_adapter import NotionTaskAdapter

        synced_at = (datetime.now(timezone.utc) - timedelta(minutes=60)).isoformat()
        task = _task_row(external_task_id=_PAGE_ID, last_synced_at=synced_at)
        sb   = self._make_sb_with_task(task)

        edited_at = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
        page = _notion_page(owner="maico", last_edited=edited_at.replace("+00:00", "Z"))

        with patch("app.core.notion_adapter.settings", _patch_settings()), \
             patch("app.core.notion_adapter.httpx.request", return_value=_make_http_resp(200, page)):
            result = NotionTaskAdapter(supabase=sb).sync_task_from_notion(_PAGE_ID)

        assert result["status"]        == "applied"
        assert "owner" in result["fields_applied"]
        update_calls = sb.table.return_value.update.call_args_list
        patch_data = update_calls[-1].args[0] if update_calls[-1].args else update_calls[-1].kwargs
        assert patch_data["owner"] == "maico"

    def test_due_date_writeback(self):
        """Notion Due Date writes back to Core due_date."""
        from app.core.notion_adapter import NotionTaskAdapter

        synced_at = (datetime.now(timezone.utc) - timedelta(minutes=60)).isoformat()
        task = _task_row(external_task_id=_PAGE_ID, last_synced_at=synced_at)
        sb   = self._make_sb_with_task(task)

        edited_at = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
        page = _notion_page(due_date="2026-11-01", last_edited=edited_at.replace("+00:00", "Z"))

        with patch("app.core.notion_adapter.settings", _patch_settings()), \
             patch("app.core.notion_adapter.httpx.request", return_value=_make_http_resp(200, page)):
            result = NotionTaskAdapter(supabase=sb).sync_task_from_notion(_PAGE_ID)

        assert result["status"]          == "applied"
        assert "due_date" in result["fields_applied"]
        update_calls = sb.table.return_value.update.call_args_list
        patch_data = update_calls[-1].args[0] if update_calls[-1].args else update_calls[-1].kwargs
        assert patch_data["due_date"] == "2026-11-01"

    def test_no_change_returns_skipped(self):
        """If Notion fields match Core exactly, writeback is skipped."""
        from app.core.notion_adapter import NotionTaskAdapter

        synced_at = (datetime.now(timezone.utc) - timedelta(minutes=60)).isoformat()
        task = {**_task_row(external_task_id=_PAGE_ID, last_synced_at=synced_at), "status": "OPEN"}
        sb   = self._make_sb_with_task(task)

        edited_at = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
        page = _notion_page(status="Open", last_edited=edited_at.replace("+00:00", "Z"))

        with patch("app.core.notion_adapter.settings", _patch_settings()), \
             patch("app.core.notion_adapter.httpx.request", return_value=_make_http_resp(200, page)):
            result = NotionTaskAdapter(supabase=sb).sync_task_from_notion(_PAGE_ID)

        assert result["status"] == "skipped"
        assert result["reason"] == "no_change"


# ═══════════════════════════════════════════════════════════════════════════════
# §4 — Duplicate prevention (idempotency)
# ═══════════════════════════════════════════════════════════════════════════════

class TestDuplicatePrevention:

    def test_finds_existing_page_by_core_task_id_before_creating(self):
        """If search returns existing page, PATCH not POST."""
        from app.core.notion_adapter import NotionTaskAdapter

        task = _task_row()  # no external_task_id yet
        sb   = _make_sb([task])

        search_resp = _make_http_resp(200, {"results": [{"id": _PAGE_ID}]})
        update_resp = _make_http_resp(200, {"id": _PAGE_ID, "object": "page"})

        calls = []
        def mock_http(method, url, **kwargs):
            calls.append(method)
            if "query" in url:
                return search_resp
            return update_resp

        with patch("app.core.notion_adapter.settings", _patch_settings()), \
             patch("app.core.notion_adapter.httpx.request", side_effect=mock_http):
            result = NotionTaskAdapter(supabase=sb).sync_task_to_notion(_TASK_UUID)

        assert result["status"]    == "synced"
        assert result["operation"] == "update"
        assert "POST" not in calls[1:]   # second call should be PATCH, not POST

    def test_does_not_create_duplicate_when_called_twice(self):
        """Calling sync twice on the same task updates rather than creates twice."""
        from app.core.notion_adapter import NotionTaskAdapter

        # Second call: task already has external_task_id
        task_with_id = _task_row(external_task_id=_PAGE_ID)
        sb           = _make_sb([task_with_id])
        update_resp  = _make_http_resp(200, {"id": _PAGE_ID, "object": "page"})

        calls = []
        def mock_http(method, url, **kwargs):
            calls.append(method)
            return update_resp

        with patch("app.core.notion_adapter.settings", _patch_settings()), \
             patch("app.core.notion_adapter.httpx.request", side_effect=mock_http):
            result = NotionTaskAdapter(supabase=sb).sync_task_to_notion(_TASK_UUID)

        # Must be update, not create (exactly 1 PATCH, 0 POST)
        assert result["operation"] == "update"
        assert calls == ["PATCH"]


# ═══════════════════════════════════════════════════════════════════════════════
# §5 — Retry: 429 with backoff
# ═══════════════════════════════════════════════════════════════════════════════

class TestRetry429:

    def test_retries_on_429_then_succeeds(self):
        """429 → retry → 200 succeeds after backoff."""
        from app.core.notion_adapter import NotionTaskAdapter

        task = _task_row(external_task_id=_PAGE_ID)
        sb   = _make_sb([task])

        retry_resp  = _make_http_resp(429, {}, headers={"Retry-After": "0.01"})
        ok_resp     = _make_http_resp(200, {"id": _PAGE_ID, "object": "page"})

        with patch("app.core.notion_adapter.settings", _patch_settings()), \
             patch("app.core.notion_adapter.time.sleep"), \
             patch("app.core.notion_adapter.httpx.request", side_effect=[retry_resp, ok_resp]):
            result = NotionTaskAdapter(supabase=sb).sync_task_to_notion(_TASK_UUID)

        assert result["status"] == "synced"

    def test_exhausted_retries_marks_error(self):
        """All retries exhausted → sync_state=error on Core task."""
        from app.core.notion_adapter import NotionTaskAdapter

        task = _task_row(external_task_id=_PAGE_ID)
        sb   = _make_sb([task])
        err_resp = _make_http_resp(500, {"message": "internal server error"})

        with patch("app.core.notion_adapter.settings", _patch_settings()), \
             patch("app.core.notion_adapter.time.sleep"), \
             patch("app.core.notion_adapter.httpx.request", return_value=err_resp):
            result = NotionTaskAdapter(supabase=sb).sync_task_to_notion(_TASK_UUID)

        assert result["status"] == "error"
        # DB must have been updated with sync_state=error
        update_calls = sb.table.return_value.update.call_args_list
        assert any(
            (c.args[0] if c.args else c.kwargs).get("sync_state") == "error"
            for c in update_calls
        )


# ═══════════════════════════════════════════════════════════════════════════════
# §6 — Auth errors: 401/403 no retry
# ═══════════════════════════════════════════════════════════════════════════════

class TestAuthErrors:

    @pytest.mark.parametrize("status_code", [401, 403])
    def test_auth_error_no_retry(self, status_code):
        """401/403 → single attempt → mark sync_error; no retry."""
        from app.core.notion_adapter import NotionTaskAdapter

        task  = _task_row(external_task_id=_PAGE_ID)
        sb    = _make_sb([task])
        calls = []

        def mock_http(method, url, **kwargs):
            calls.append(method)
            return _make_http_resp(status_code, {"message": "Unauthorized"})

        with patch("app.core.notion_adapter.settings", _patch_settings()), \
             patch("app.core.notion_adapter.httpx.request", side_effect=mock_http):
            result = NotionTaskAdapter(supabase=sb).sync_task_to_notion(_TASK_UUID)

        assert result["status"] == "error"
        assert str(status_code) in result["error"]
        # Must have made exactly 1 HTTP call (no retry)
        assert len(calls) == 1

    @pytest.mark.parametrize("status_code", [401, 403])
    def test_auth_error_never_leaks_api_key(self, status_code):
        """Auth errors must never include the API key value in the returned error."""
        from app.core.notion_adapter import NotionTaskAdapter

        task = _task_row(external_task_id=_PAGE_ID)
        sb   = _make_sb([task])

        with patch("app.core.notion_adapter.settings", _patch_settings(key="secret_SHOULD_NOT_APPEAR")), \
             patch("app.core.notion_adapter.httpx.request",
                   return_value=_make_http_resp(status_code, {})):
            result = NotionTaskAdapter(supabase=sb).sync_task_to_notion(_TASK_UUID)

        error_str = str(result)
        assert "secret_SHOULD_NOT_APPEAR" not in error_str


# ═══════════════════════════════════════════════════════════════════════════════
# §7 — External missing: 404 marks external_missing, preserves task
# ═══════════════════════════════════════════════════════════════════════════════

class TestExternalMissing:

    def test_404_marks_external_missing_preserves_task(self):
        """404 from Notion → marks sync_error=external_missing, does NOT delete Core task."""
        from app.core.notion_adapter import NotionTaskAdapter

        task = _task_row(external_task_id=_PAGE_ID)
        sb   = _make_sb([task])

        with patch("app.core.notion_adapter.settings", _patch_settings()), \
             patch("app.core.notion_adapter.httpx.request",
                   return_value=_make_http_resp(404, {})):
            result = NotionTaskAdapter(supabase=sb).sync_task_to_notion(_TASK_UUID)

        assert result["status"] == "error"
        assert "missing" in result.get("error", "").lower() or "missing" in str(result)
        # Core task must NOT have been deleted
        assert not sb.table.return_value.delete.called

    def test_404_on_writeback_returns_error_not_delete(self):
        """404 when pulling from Notion does not delete the Core task."""
        from app.core.notion_adapter import NotionTaskAdapter

        sb = _make_sb([])  # empty — task lookup won't be reached

        with patch("app.core.notion_adapter.settings", _patch_settings()), \
             patch("app.core.notion_adapter.httpx.request",
                   return_value=_make_http_resp(404, {})):
            result = NotionTaskAdapter(supabase=sb).sync_task_from_notion(_PAGE_ID)

        assert result["status"] == "error"
        assert not sb.table.return_value.delete.called


# ═══════════════════════════════════════════════════════════════════════════════
# §8 — Sync loop guard
# ═══════════════════════════════════════════════════════════════════════════════

class TestSyncLoopGuard:

    def test_skips_writeback_when_notion_edit_is_within_guard_window(self):
        """If Notion page was last edited within SYNC_LOOP_GUARD_S of our push, skip."""
        from app.core.notion_adapter import NotionTaskAdapter

        # last_synced_at = 10 seconds ago
        synced_at = (datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat()
        task = _task_row(external_task_id=_PAGE_ID, last_synced_at=synced_at)
        sb   = MagicMock()
        tbl  = MagicMock()
        for m in ("select", "eq", "limit", "in_", "update", "maybe_single"):
            getattr(tbl, m).return_value = tbl
        tbl.execute.return_value = MagicMock(data=[task])
        sb.table.return_value = tbl

        # Notion last_edited_time = 5 seconds ago (within 30s guard of our 10s push)
        edited_at = (datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat()
        page = _notion_page(status="In Progress", last_edited=edited_at.replace("+00:00", "Z"))

        with patch("app.core.notion_adapter.settings", _patch_settings()), \
             patch("app.core.notion_adapter.httpx.request", return_value=_make_http_resp(200, page)):
            result = NotionTaskAdapter(supabase=sb).sync_task_from_notion(_PAGE_ID)

        assert result["status"] == "skipped"
        assert result["reason"] == "sync_loop_guard"
        # Core task must NOT have been updated
        tbl.update.assert_not_called()

    def test_allows_writeback_when_notion_edit_is_outside_guard_window(self):
        """Edit older than SYNC_LOOP_GUARD_S relative to last push should be processed."""
        from app.core.notion_adapter import NotionTaskAdapter

        # last_synced_at = 5 minutes ago
        synced_at = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
        task = _task_row(status="OPEN", external_task_id=_PAGE_ID, last_synced_at=synced_at)
        sb   = MagicMock()
        tbl  = MagicMock()
        for m in ("select", "eq", "limit", "in_", "update", "maybe_single"):
            getattr(tbl, m).return_value = tbl
        tbl.execute.return_value = MagicMock(data=[task])
        sb.table.return_value = tbl

        # Notion last_edited = 2 minutes ago (well after the 30s guard window)
        edited_at = (datetime.now(timezone.utc) - timedelta(minutes=2)).isoformat()
        page = _notion_page(status="Done", last_edited=edited_at.replace("+00:00", "Z"))

        with patch("app.core.notion_adapter.settings", _patch_settings()), \
             patch("app.core.notion_adapter.httpx.request", return_value=_make_http_resp(200, page)):
            result = NotionTaskAdapter(supabase=sb).sync_task_from_notion(_PAGE_ID)

        assert result["status"] == "applied"
        assert "status" in result["fields_applied"]


# ═══════════════════════════════════════════════════════════════════════════════
# §9 — Invalid/unknown Notion status ignored
# ═══════════════════════════════════════════════════════════════════════════════

class TestInvalidNotionStatus:

    def test_unknown_status_does_not_break_sync(self):
        """An unrecognised Notion status value is ignored; task is not corrupted."""
        from app.core.notion_adapter import NotionTaskAdapter

        synced_at = (datetime.now(timezone.utc) - timedelta(minutes=60)).isoformat()
        task = _task_row(external_task_id=_PAGE_ID, last_synced_at=synced_at)
        sb   = MagicMock()
        tbl  = MagicMock()
        for m in ("select", "eq", "limit", "in_", "update", "maybe_single"):
            getattr(tbl, m).return_value = tbl
        tbl.execute.return_value = MagicMock(data=[task])
        sb.table.return_value = tbl

        edited_at = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
        page = _notion_page(status="FancyCustomStatus", last_edited=edited_at.replace("+00:00", "Z"))

        with patch("app.core.notion_adapter.settings", _patch_settings()), \
             patch("app.core.notion_adapter.httpx.request", return_value=_make_http_resp(200, page)):
            result = NotionTaskAdapter(supabase=sb).sync_task_from_notion(_PAGE_ID)

        # Invalid status is silently ignored; result is skipped (no other change)
        assert result["status"] in ("skipped", "applied")
        if result["status"] == "applied":
            assert "status" not in result["fields_applied"]

    def test_notion_missing_core_task_id_returns_error(self):
        """Notion page without Core Task ID property → error, not exception."""
        from app.core.notion_adapter import NotionTaskAdapter

        sb   = _make_sb([])
        page = {
            "object": "page",
            "id":     _PAGE_ID,
            "last_edited_time": "2026-10-16T10:00:00.000Z",
            "properties": {"Status": {"select": {"name": "Open"}}},  # no Core Task ID
        }

        with patch("app.core.notion_adapter.settings", _patch_settings()), \
             patch("app.core.notion_adapter.httpx.request", return_value=_make_http_resp(200, page)):
            result = NotionTaskAdapter(supabase=sb).sync_task_from_notion(_PAGE_ID)

        assert result["status"] == "error"
        assert "core_task_id" in result.get("error", "")


# ═══════════════════════════════════════════════════════════════════════════════
# §10 — Protected fields blocked from writeback
# ═══════════════════════════════════════════════════════════════════════════════

class TestProtectedFields:

    def test_protected_fields_never_written_back_to_core(self):
        """client_id, source_type, source_id, id are never overwritten from Notion."""
        from app.core.notion_adapter import NotionTaskAdapter, _PROTECTED_FIELDS

        synced_at = (datetime.now(timezone.utc) - timedelta(minutes=60)).isoformat()
        task = {
            **_task_row(external_task_id=_PAGE_ID, last_synced_at=synced_at),
            "status": "OPEN",
        }
        sb  = MagicMock()
        tbl = MagicMock()
        for m in ("select", "eq", "limit", "in_", "update", "maybe_single"):
            getattr(tbl, m).return_value = tbl
        tbl.execute.return_value = MagicMock(data=[task])
        sb.table.return_value = tbl

        edited_at = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
        page = _notion_page(status="Done", last_edited=edited_at.replace("+00:00", "Z"))

        with patch("app.core.notion_adapter.settings", _patch_settings()), \
             patch("app.core.notion_adapter.httpx.request", return_value=_make_http_resp(200, page)):
            result = NotionTaskAdapter(supabase=sb).sync_task_from_notion(_PAGE_ID)

        if result["status"] == "applied":
            update_calls = tbl.update.call_args_list
            for call_ in update_calls:
                patch_data = call_.args[0] if call_.args else call_.kwargs
                for protected in _PROTECTED_FIELDS:
                    assert protected not in patch_data, (
                        f"Protected field '{protected}' was written to Core from Notion"
                    )


# ═══════════════════════════════════════════════════════════════════════════════
# §11 — CONFIG_REQUIRED — no secret exposure, no fake external_task_id
# ═══════════════════════════════════════════════════════════════════════════════

class TestConfigRequired:

    def test_config_required_when_api_key_missing(self):
        """Without NOTION_API_KEY, sync returns CONFIG_REQUIRED."""
        from app.core.notion_adapter import NotionTaskAdapter

        task = _task_row()
        sb   = _make_sb([task])
        cfg  = _patch_settings(key="", db_id=_DB_ID)

        with patch("app.core.notion_adapter.settings", cfg):
            result = NotionTaskAdapter(supabase=sb).sync_task_to_notion(_TASK_UUID)

        assert result["status"] == "CONFIG_REQUIRED"
        assert "NOTION_API_KEY" in result.get("missing", [])

    def test_config_required_when_db_id_missing(self):
        """Without NOTION_TASKS_DATABASE_ID, sync returns CONFIG_REQUIRED."""
        from app.core.notion_adapter import NotionTaskAdapter

        task = _task_row()
        sb   = _make_sb([task])
        cfg  = _patch_settings(key=_API_KEY, db_id="")

        with patch("app.core.notion_adapter.settings", cfg):
            result = NotionTaskAdapter(supabase=sb).sync_task_to_notion(_TASK_UUID)

        assert result["status"] == "CONFIG_REQUIRED"
        assert "NOTION_TASKS_DATABASE_ID" in result.get("missing", [])

    def test_config_required_does_not_expose_partial_key(self):
        """CONFIG_REQUIRED response must never include any secret value."""
        from app.core.notion_adapter import NotionTaskAdapter

        task = _task_row()
        sb   = _make_sb([task])
        cfg  = _patch_settings(key="", db_id="")

        with patch("app.core.notion_adapter.settings", cfg):
            result = NotionTaskAdapter(supabase=sb).sync_task_to_notion(_TASK_UUID)

        result_str = str(result)
        assert "secret" not in result_str.lower()
        assert "Bearer" not in result_str

    def test_config_required_does_not_write_fake_external_task_id(self):
        """CONFIG_REQUIRED must not persist any fake external_task_id to Core."""
        from app.core.notion_adapter import NotionTaskAdapter

        task = _task_row()
        sb   = _make_sb([task])
        cfg  = _patch_settings(key="", db_id="")

        with patch("app.core.notion_adapter.settings", cfg):
            NotionTaskAdapter(supabase=sb).sync_task_to_notion(_TASK_UUID)

        update_calls = sb.table.return_value.update.call_args_list
        for call_ in update_calls:
            patch_data = call_.args[0] if call_.args else call_.kwargs
            ext_id = patch_data.get("external_task_id")
            if ext_id is not None:
                assert not str(ext_id).startswith("notion-stub-"), (
                    "Stub external_task_id must never be written to Core DB"
                )

    def test_create_external_task_compat_without_config(self):
        """Legacy create_external_task returns error dict (not stub) when not configured."""
        from app.core.notion_adapter import NotionTaskAdapter

        sb  = _make_sb([])
        cfg = _patch_settings(key="", db_id="")

        with patch("app.core.notion_adapter.settings", cfg):
            result = NotionTaskAdapter(supabase=sb).create_external_task({"id": _TASK_UUID, "title": "X", "status": "OPEN"})

        assert result["system"]     == "notion"
        assert result["sync_state"] == "error"
        assert result["external_task_id"] is None
        # Must not start with old stub prefix
        assert result["external_task_id"] != f"notion-stub-{_TASK_UUID}"


# ═══════════════════════════════════════════════════════════════════════════════
# §12 — Batch sync_pending_tasks_to_notion
# ═══════════════════════════════════════════════════════════════════════════════

class TestBatchSync:

    def test_sync_pending_returns_summary(self):
        """sync_pending_tasks_to_notion returns {status, total, synced, errors, skipped}."""
        from app.core.notion_adapter import NotionTaskAdapter

        tasks = [_task_row(_TASK_UUID), _task_row(_TASK_UUID2, "OPEN")]
        sb    = _make_sb(tasks)

        search_resp = _make_http_resp(200, {"results": []})
        create_resp = _make_http_resp(200, {"id": _PAGE_ID, "object": "page"})

        with patch("app.core.notion_adapter.settings", _patch_settings()), \
             patch("app.core.notion_adapter.httpx.request",
                   side_effect=[search_resp, create_resp, search_resp, create_resp]):
            result = NotionTaskAdapter(supabase=sb).sync_pending_tasks_to_notion(limit=50)

        assert result["status"] == "ok"
        assert "total"   in result
        assert "synced"  in result
        assert "errors"  in result
        assert "skipped" in result

    def test_sync_pending_config_required(self):
        """sync_pending returns CONFIG_REQUIRED without crashing when key is missing."""
        from app.core.notion_adapter import NotionTaskAdapter

        sb  = _make_sb([])
        cfg = _patch_settings(key="", db_id="")

        with patch("app.core.notion_adapter.settings", cfg):
            result = NotionTaskAdapter(supabase=sb).sync_pending_tasks_to_notion()

        assert result["status"] == "CONFIG_REQUIRED"

    def test_sync_pending_single_task_error_does_not_stop_batch(self):
        """If one task fails, others still process (no all-or-nothing batch failure)."""
        from app.core.notion_adapter import NotionTaskAdapter

        tasks = [_task_row(_TASK_UUID), _task_row(_TASK_UUID2)]
        sb    = _make_sb(tasks)

        # First task: 500 error. Second task: success.
        search_resp = _make_http_resp(200, {"results": []})
        fail_resp   = _make_http_resp(500, {"message": "error"})
        ok_resp     = _make_http_resp(200, {"id": _PAGE_ID, "object": "page"})

        call_count = [0]
        def mock_http(*a, **kw):
            call_count[0] += 1
            # search calls return empty, create calls alternate fail/ok
            url = a[1] if len(a) > 1 else kw.get("url", "")
            if "query" in str(url):
                return search_resp
            if call_count[0] <= 2:
                return fail_resp
            return ok_resp

        with patch("app.core.notion_adapter.settings", _patch_settings()), \
             patch("app.core.notion_adapter.time.sleep"), \
             patch("app.core.notion_adapter.httpx.request", side_effect=mock_http):
            result = NotionTaskAdapter(supabase=sb).sync_pending_tasks_to_notion(limit=50)

        assert result["status"] == "ok"
        assert result["total"] == 2


# ═══════════════════════════════════════════════════════════════════════════════
# §13 — poll_notion_task_updates
# ═══════════════════════════════════════════════════════════════════════════════

class TestPollNotionTaskUpdates:

    def test_poll_returns_summary(self):
        """poll_notion_task_updates returns {status, pages, applied, skipped, errors}."""
        from app.core.notion_adapter import NotionTaskAdapter

        synced_at = (datetime.now(timezone.utc) - timedelta(minutes=60)).isoformat()
        task  = _task_row(external_task_id=_PAGE_ID, last_synced_at=synced_at)
        sb    = _make_sb([task])

        edited_at   = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
        pages_resp  = _make_http_resp(200, {"results": [
            _notion_page(status="In Progress", last_edited=edited_at.replace("+00:00", "Z"))
        ]})
        page_resp   = _make_http_resp(200, _notion_page(
            status="In Progress", last_edited=edited_at.replace("+00:00", "Z")
        ))

        responses = [pages_resp, page_resp]
        with patch("app.core.notion_adapter.settings", _patch_settings()), \
             patch("app.core.notion_adapter.httpx.request", side_effect=responses):
            result = NotionTaskAdapter(supabase=sb).poll_notion_task_updates(since_minutes=10)

        assert result["status"] == "ok"
        assert "pages"   in result
        assert "applied" in result
        assert "skipped" in result
        assert "errors"  in result

    def test_poll_config_required(self):
        """poll returns CONFIG_REQUIRED when key is missing."""
        from app.core.notion_adapter import NotionTaskAdapter

        sb  = _make_sb([])
        cfg = _patch_settings(key="", db_id="")

        with patch("app.core.notion_adapter.settings", cfg):
            result = NotionTaskAdapter(supabase=sb).poll_notion_task_updates()

        assert result["status"] == "CONFIG_REQUIRED"

    def test_poll_query_error_does_not_raise(self):
        """If Notion query fails, poll returns error dict without raising."""
        from app.core.notion_adapter import NotionTaskAdapter

        sb = _make_sb([])

        with patch("app.core.notion_adapter.settings", _patch_settings()), \
             patch("app.core.notion_adapter.httpx.request",
                   return_value=_make_http_resp(503, {})):
            result = NotionTaskAdapter(supabase=sb).poll_notion_task_updates()

        assert result["status"] == "error"
        # Must not have raised
        assert "applied" in result


# ═══════════════════════════════════════════════════════════════════════════════
# §14 — Client isolation
# ═══════════════════════════════════════════════════════════════════════════════

class TestClientIsolation:

    def test_core_task_id_in_notion_page_must_match_client(self):
        """
        sync_task_from_notion must use the Core Task ID from the Notion page
        to look up the task, so cross-client writes are impossible by construction.
        The Core task is fetched by its UUID (immutable canonical ID), not by
        client_id from the Notion page.
        """
        from app.core.notion_adapter import NotionTaskAdapter

        # Task belongs to client-alpha
        synced_at = (datetime.now(timezone.utc) - timedelta(minutes=60)).isoformat()
        task = {**_task_row(external_task_id=_PAGE_ID, last_synced_at=synced_at), "client_id": "client-alpha"}
        sb  = MagicMock()
        tbl = MagicMock()
        for m in ("select", "eq", "limit", "in_", "update", "maybe_single"):
            getattr(tbl, m).return_value = tbl
        tbl.execute.return_value = MagicMock(data=[task])
        sb.table.return_value = tbl

        edited_at = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
        # Notion page has Core Task ID pointing to _TASK_UUID (client-alpha's task)
        page = _notion_page(
            core_task_id=_TASK_UUID,
            status="Done",
            last_edited=edited_at.replace("+00:00", "Z"),
        )

        with patch("app.core.notion_adapter.settings", _patch_settings()), \
             patch("app.core.notion_adapter.httpx.request", return_value=_make_http_resp(200, page)):
            result = NotionTaskAdapter(supabase=sb).sync_task_from_notion(_PAGE_ID)

        # Must have used Core Task ID to look up the task (not any Notion-side client field)
        # Verify the select was called with the correct task UUID
        select_chain_eq_calls = [
            c for c in tbl.eq.call_args_list
            if c.args and c.args[0] == "id" and c.args[1] == _TASK_UUID
        ]
        assert len(select_chain_eq_calls) >= 1, (
            "Core task lookup must use the Core Task ID from the Notion page"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# §15 — Status mapping correctness
# ═══════════════════════════════════════════════════════════════════════════════

class TestStatusMapping:

    @pytest.mark.parametrize("core_status,expected_notion", [
        ("OPEN",        "Open"),
        ("IN_PROGRESS", "In Progress"),
        ("DONE",        "Done"),
        ("BLOCKED",     "Blocked"),
        ("ARCHIVED",    "Archived"),
    ])
    def test_all_core_statuses_map_to_notion(self, core_status, expected_notion):
        """Every Core status has a corresponding Notion status name."""
        from app.core.notion_adapter import _CORE_TO_NOTION_STATUS
        assert _CORE_TO_NOTION_STATUS[core_status] == expected_notion

    @pytest.mark.parametrize("notion_status,expected_core", [
        ("Open",        "OPEN"),
        ("In Progress", "IN_PROGRESS"),
        ("Done",        "DONE"),
        ("Blocked",     "BLOCKED"),
        ("Archived",    "ARCHIVED"),
    ])
    def test_all_notion_statuses_map_to_core(self, notion_status, expected_core):
        """Every Notion status name maps back to a valid Core status."""
        from app.core.notion_adapter import _NOTION_TO_CORE_STATUS
        assert _NOTION_TO_CORE_STATUS[notion_status] == expected_core

    def test_status_mapping_is_bijective(self):
        """Core→Notion and Notion→Core mappings are exact inverses."""
        from app.core.notion_adapter import _CORE_TO_NOTION_STATUS, _NOTION_TO_CORE_STATUS
        for core_val, notion_val in _CORE_TO_NOTION_STATUS.items():
            assert _NOTION_TO_CORE_STATUS[notion_val] == core_val
