"""
Notion task adapter — production implementation (Phase NOTION REAL).

Architecture invariants:
  Core/Supabase = canonical truth (never overwritten by Notion).
  Notion = human-facing sync layer (write-out, restricted write-back).

Data flow:
  Core → Notion : full task push via create_page / update_page
  Notion → Core : restricted writeback (status, owner, due_date, priority only)

Idempotency:
  Before creating a Notion page, searches the database for an existing page
  with property "Core Task ID" == task.id. If found, updates instead of creates.

Sync loop guard:
  Notion→Core updates are skipped when the Notion page was last edited within
  SYNC_LOOP_GUARD_S seconds of the most recent Core→Notion push (last_synced_at).
  This prevents a push from immediately triggering a redundant writeback.

Error handling:
  401/403 → no retry; mark sync_error = "auth_error:{status}"
  404     → no retry; mark sync_error = "external_missing"
  429/5xx → exponential backoff up to MAX_RETRIES
  network → mark sync_error = "network_error:{msg}"
  Any failure → Core task is never deleted or corrupted.

Config required (backend env vars only — never frontend):
  NOTION_API_KEY           — Notion internal integration secret
  NOTION_TASKS_DATABASE_ID — Notion database ID for task pages

When either variable is empty, all sync operations return
{"status": "CONFIG_REQUIRED", ...} and mark sync_error without touching
the Core task beyond that sync_error field.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone, timedelta
from typing import Any, Optional

import httpx

from ..config import settings
from ..database import get_supabase

logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

_NOTION_API_BASE    = "https://api.notion.com/v1"
_NOTION_VERSION     = "2022-06-28"
_MAX_RETRIES        = 3
_BACKOFF_BASE_S     = 1.0      # 1s → 2s → 4s
_SYNC_LOOP_GUARD_S  = 30       # seconds: skip Notion→Core if push was <30s ago
_REQUEST_TIMEOUT    = 15.0     # seconds per HTTP call
_BATCH_LIMIT        = 50       # max tasks per sync_pending run
_POLL_MINUTES       = 10       # default poll window in minutes

# ── Status / Priority mappings ────────────────────────────────────────────────

_CORE_TO_NOTION_STATUS: dict[str, str] = {
    "OPEN":        "Open",
    "IN_PROGRESS": "In Progress",
    "DONE":        "Done",
    "BLOCKED":     "Blocked",
    "ARCHIVED":    "Archived",
}
_NOTION_TO_CORE_STATUS: dict[str, str] = {v: k for k, v in _CORE_TO_NOTION_STATUS.items()}

_CORE_TO_NOTION_PRIORITY: dict[str, str] = {
    "LOW":    "Low",
    "MEDIUM": "Medium",
    "HIGH":   "High",
    "URGENT": "Urgent",
}
_NOTION_TO_CORE_PRIORITY: dict[str, str] = {v: k for k, v in _CORE_TO_NOTION_PRIORITY.items()}

# Fields Notion is permitted to write back to Core
_ALLOWED_WRITEBACK: frozenset[str] = frozenset({"status", "owner", "due_date", "priority"})

# Core fields that Notion must never overwrite
_PROTECTED_FIELDS: frozenset[str] = frozenset({
    "id", "client_id", "source_type", "source_id", "created_at",
    "external_task_id", "external_system", "visibility_scope",
})


# ── Notion property builders ──────────────────────────────────────────────────

def _prop_title(text: str) -> dict:
    return {"title": [{"text": {"content": text or ""}}]}


def _prop_rich_text(text: str | None) -> dict:
    return {"rich_text": [{"text": {"content": str(text) if text else ""}}]}


def _prop_select(name: str | None) -> dict:
    if not name:
        return {"select": None}
    return {"select": {"name": name}}


def _prop_date(value: str | None) -> dict:
    """value: ISO date string (YYYY-MM-DD) or None."""
    if not value:
        return {"date": None}
    # Trim to date portion if datetime was passed
    date_str = str(value)[:10]
    return {"date": {"start": date_str}}


# ── Notion property readers ───────────────────────────────────────────────────

def _read_rich_text(prop: dict | None) -> str | None:
    if not prop:
        return None
    parts = prop.get("rich_text") or []
    if not parts:
        return None
    return parts[0].get("text", {}).get("content") or None


def _read_select(prop: dict | None) -> str | None:
    if not prop:
        return None
    sel = prop.get("select") or {}
    return sel.get("name") or None


def _read_date(prop: dict | None) -> str | None:
    if not prop:
        return None
    d = prop.get("date") or {}
    return d.get("start") or None


def _read_title(prop: dict | None) -> str | None:
    if not prop:
        return None
    parts = prop.get("title") or []
    if not parts:
        return None
    return parts[0].get("text", {}).get("content") or None


# ── Core → Notion property map ────────────────────────────────────────────────

def _task_to_notion_properties(task: dict) -> dict:
    """Build the Notion properties dict from a Core task row."""
    notion_status   = _CORE_TO_NOTION_STATUS.get(task.get("status", "OPEN"), "Open")
    notion_priority = _CORE_TO_NOTION_PRIORITY.get(task.get("priority", "MEDIUM"), "Medium")
    source_type     = task.get("source_type") or ""

    props: dict[str, Any] = {
        "Task":                  _prop_title(task.get("title") or ""),
        "Core Task ID":          _prop_rich_text(str(task["id"])),
        "Client":                _prop_rich_text(task.get("client_id") or ""),
        "Status":                _prop_select(notion_status),
        "Priority":              _prop_select(notion_priority),
        "Owner":                 _prop_rich_text(task.get("owner")),
        "Due Date":              _prop_date(task.get("due_date")),
        "Source":                _prop_select(source_type.capitalize() if source_type else None),
        "Source ID":             _prop_rich_text(task.get("source_id")),
        "Description / Action":  _prop_rich_text(task.get("description")),
        "Created At":            _prop_rich_text(str(task.get("created_at") or "")),
        "Updated At":            _prop_rich_text(str(task.get("updated_at") or "")),
    }
    return props


# ── HTTP helper with retry ────────────────────────────────────────────────────

def _notion_request(
    method: str,
    path: str,
    api_key: str,
    json_body: dict | None = None,
) -> dict:
    """
    Execute one Notion API call with retry/backoff.

    Returns:
      {"ok": True, "data": <response dict>}              — success
      {"ok": False, "status": <http_status>, "error": …} — API error
      {"ok": False, "status": 0, "error": "network_error:…"} — network error
    """
    url     = f"{_NOTION_API_BASE}/{path.lstrip('/')}"
    headers = {
        "Authorization":  f"Bearer {api_key}",
        "Notion-Version": _NOTION_VERSION,
        "Content-Type":   "application/json",
    }
    attempt = 0
    while True:
        attempt += 1
        try:
            resp = httpx.request(
                method,
                url,
                headers=headers,
                json=json_body,
                timeout=_REQUEST_TIMEOUT,
            )
        except Exception as exc:
            err_msg = f"network_error:{type(exc).__name__}:{exc}"
            logger.warning("notion_adapter: network error %s %s: %s", method, path, exc)
            return {"ok": False, "status": 0, "error": err_msg}

        status = resp.status_code

        # Success
        if 200 <= status < 300:
            try:
                return {"ok": True, "data": resp.json()}
            except Exception:
                return {"ok": True, "data": {}}

        # Non-retryable client errors
        if status in (401, 403):
            logger.error("notion_adapter: auth error %s %s → %s", method, path, status)
            return {"ok": False, "status": status, "error": f"auth_error:{status}"}

        if status == 404:
            return {"ok": False, "status": 404, "error": "external_missing"}

        if status == 409:
            return {"ok": False, "status": 409, "error": f"conflict:{status}"}

        # Rate limit or server error — retry with backoff
        retryable = (status == 429 or status >= 500)
        if retryable and attempt < _MAX_RETRIES:
            delay = _BACKOFF_BASE_S * (2 ** (attempt - 1))
            retry_after = resp.headers.get("Retry-After")
            if retry_after:
                try:
                    delay = max(delay, float(retry_after))
                except ValueError:
                    pass
            logger.warning(
                "notion_adapter: %s %s → %s (attempt %d/%d, retry in %.1fs)",
                method, path, status, attempt, _MAX_RETRIES, delay,
            )
            time.sleep(delay)
            continue

        # Exhausted retries or non-retryable 4xx
        logger.error("notion_adapter: %s %s → %s after %d attempt(s)", method, path, status, attempt)
        try:
            body = resp.json()
            err_detail = body.get("message") or body.get("code") or str(status)
        except Exception:
            err_detail = str(status)
        return {"ok": False, "status": status, "error": f"http_{status}:{err_detail}"}


# ── Core DB helpers ───────────────────────────────────────────────────────────

def _write_sync_metadata(
    sb,
    task_id: str,
    external_task_id: str,
    sync_state: str,
    sync_error: str | None,
) -> None:
    """Write sync metadata to core_tasks. Never touches task content fields."""
    now = datetime.now(timezone.utc).isoformat()
    patch: dict[str, Any] = {
        "external_system": "notion",
        "external_task_id": external_task_id,
        "sync_state":       sync_state,
        "sync_error":       sync_error,
    }
    if sync_state == "synced":
        patch["last_synced_at"] = now
    try:
        sb.table("core_tasks").update(patch).eq("id", task_id).execute()
    except Exception as exc:
        logger.error("notion_adapter: failed to write sync metadata for %s: %s", task_id, exc)


def _write_sync_error(sb, task_id: str, error: str) -> None:
    """Mark a task as sync error. Preserves external_task_id if already set."""
    try:
        sb.table("core_tasks").update({
            "sync_state": "error",
            "sync_error": error[:500],  # guard column length
        }).eq("id", task_id).execute()
    except Exception as exc:
        logger.error("notion_adapter: failed to write sync error for %s: %s", task_id, exc)


def _fetch_task(sb, task_id: str) -> dict | None:
    try:
        res = (
            sb.table("core_tasks")
            .select("*")
            .eq("id", task_id)
            .limit(1)
            .execute()
        )
        rows = res.data or []
        return rows[0] if rows else None
    except Exception as exc:
        logger.error("notion_adapter: fetch task %s failed: %s", task_id, exc)
        return None


# ── Main adapter class ────────────────────────────────────────────────────────

class NotionTaskAdapter:
    """
    Production Notion task adapter.

    All methods that interact with Notion require NOTION_API_KEY and
    NOTION_TASKS_DATABASE_ID to be configured. If either is missing,
    the operation fails with CONFIG_REQUIRED and marks sync_error on
    the Core task without destroying it.
    """

    def __init__(self, supabase=None):
        self._sb = supabase or get_supabase()

    # ── Config guard ──────────────────────────────────────────────────────────

    def _check_config(self) -> tuple[str, str] | None:
        """
        Returns (api_key, db_id) if configured, else None.
        Side-effect: logs CONFIG_REQUIRED.
        """
        api_key = settings.NOTION_API_KEY
        db_id   = settings.NOTION_TASKS_DATABASE_ID
        if not api_key or not db_id:
            missing = []
            if not api_key:
                missing.append("NOTION_API_KEY")
            if not db_id:
                missing.append("NOTION_TASKS_DATABASE_ID")
            logger.warning("notion_adapter: CONFIG_REQUIRED — %s", ", ".join(missing))
            return None
        return api_key, db_id

    # ── Idempotency: find existing Notion page by Core Task ID ────────────────

    def _find_page_by_task_id(self, api_key: str, db_id: str, task_id: str) -> str | None:
        """
        Search Notion database for a page with Core Task ID == task_id.
        Returns the Notion page ID if found, else None.
        """
        result = _notion_request(
            "POST",
            f"databases/{db_id}/query",
            api_key,
            json_body={
                "filter": {
                    "property": "Core Task ID",
                    "rich_text": {"equals": str(task_id)},
                },
                "page_size": 1,
            },
        )
        if not result["ok"]:
            return None
        results = result["data"].get("results") or []
        if results:
            return results[0].get("id")
        return None

    # ── Core → Notion ─────────────────────────────────────────────────────────

    def sync_task_to_notion(self, task_id: str) -> dict:
        """
        Push one Core task to Notion. Creates or updates the Notion page.

        Returns:
          {"status": "synced", "external_task_id": "...", "operation": "create|update"}
          {"status": "error",  "error": "...", "task_id": "..."}
          {"status": "CONFIG_REQUIRED", "missing": [...]}
        """
        cfg = self._check_config()
        if cfg is None:
            missing = []
            if not settings.NOTION_API_KEY:
                missing.append("NOTION_API_KEY")
            if not settings.NOTION_TASKS_DATABASE_ID:
                missing.append("NOTION_TASKS_DATABASE_ID")
            _write_sync_error(self._sb, task_id, f"CONFIG_REQUIRED:{','.join(missing)}")
            return {"status": "CONFIG_REQUIRED", "missing": missing, "task_id": task_id}

        api_key, db_id = cfg

        task = _fetch_task(self._sb, task_id)
        if not task:
            return {"status": "error", "error": "task_not_found", "task_id": task_id}

        properties = _task_to_notion_properties(task)

        # Idempotency: check for existing page before creating
        existing_id = task.get("external_task_id") or self._find_page_by_task_id(api_key, db_id, task_id)

        if existing_id:
            # Update existing page
            result = _notion_request(
                "PATCH",
                f"pages/{existing_id}",
                api_key,
                json_body={"properties": properties},
            )
            operation = "update"
            page_id   = existing_id
        else:
            # Create new page
            result = _notion_request(
                "POST",
                "pages",
                api_key,
                json_body={
                    "parent":     {"database_id": db_id},
                    "properties": properties,
                },
            )
            operation = "create"
            page_id   = result["data"].get("id") if result["ok"] else None

        if not result["ok"]:
            err = result.get("error", "unknown_error")
            _write_sync_error(self._sb, task_id, err)
            logger.error(
                "notion_adapter: sync_task_to_notion %s op=%s → error: %s",
                task_id, operation, err,
            )
            return {"status": "error", "error": err, "task_id": task_id, "operation": operation}

        # Success — persist sync metadata to Core
        _write_sync_metadata(self._sb, task_id, page_id, "synced", None)
        logger.info(
            "notion_adapter: sync_task_to_notion %s op=%s page_id=%s",
            task_id, operation, page_id,
        )
        return {
            "status":           "synced",
            "task_id":          task_id,
            "external_task_id": page_id,
            "operation":        operation,
        }

    # ── Notion → Core (restricted writeback) ─────────────────────────────────

    def sync_task_from_notion(self, external_task_id: str) -> dict:
        """
        Pull allowed fields from a Notion page back to Core.
        Allowed: status, owner, due_date, priority.
        Protected fields are silently ignored.

        Returns:
          {"status": "applied", "task_id": "...", "fields_applied": [...]}
          {"status": "skipped", "reason": "sync_loop_guard|no_change|task_not_found"}
          {"status": "error", "error": "..."}
          {"status": "CONFIG_REQUIRED", ...}
        """
        cfg = self._check_config()
        if cfg is None:
            missing = []
            if not settings.NOTION_API_KEY:
                missing.append("NOTION_API_KEY")
            if not settings.NOTION_TASKS_DATABASE_ID:
                missing.append("NOTION_TASKS_DATABASE_ID")
            return {"status": "CONFIG_REQUIRED", "missing": missing}

        api_key, _ = cfg

        # Fetch Notion page
        result = _notion_request("GET", f"pages/{external_task_id}", api_key)
        if not result["ok"]:
            err = result.get("error", "unknown_error")
            logger.warning("notion_adapter: sync_from_notion page %s → %s", external_task_id, err)
            return {"status": "error", "error": err, "external_task_id": external_task_id}

        page       = result["data"]
        props      = page.get("properties") or {}
        notion_edited_raw = page.get("last_edited_time")  # ISO 8601 string

        # Resolve Core task_id from page
        core_task_id = _read_rich_text(props.get("Core Task ID"))
        if not core_task_id:
            return {"status": "error", "error": "missing_core_task_id", "external_task_id": external_task_id}

        # Fetch Core task
        task = _fetch_task(self._sb, core_task_id)
        if not task:
            return {"status": "skipped", "reason": "task_not_found", "task_id": core_task_id}

        # Sync loop guard: skip if Notion edit is within guard window of our last push
        if notion_edited_raw and task.get("last_synced_at"):
            try:
                notion_edited = datetime.fromisoformat(notion_edited_raw.replace("Z", "+00:00"))
                last_push     = datetime.fromisoformat(str(task["last_synced_at"]))
                if last_push.tzinfo is None:
                    last_push = last_push.replace(tzinfo=timezone.utc)
                guard_cutoff = last_push + timedelta(seconds=_SYNC_LOOP_GUARD_S)
                if notion_edited <= guard_cutoff:
                    logger.debug(
                        "notion_adapter: sync_loop_guard skip task %s (notion_edit=%s last_push=%s)",
                        core_task_id, notion_edited_raw, task["last_synced_at"],
                    )
                    return {"status": "skipped", "reason": "sync_loop_guard", "task_id": core_task_id}
            except Exception as exc:
                logger.debug("notion_adapter: sync_loop_guard parse error: %s", exc)

        # Extract allowed writeback fields
        patch: dict[str, Any] = {}

        notion_status = _read_select(props.get("Status"))
        if notion_status:
            core_status = _NOTION_TO_CORE_STATUS.get(notion_status)
            if core_status and core_status != task.get("status"):
                patch["status"] = core_status
            elif not core_status:
                logger.info(
                    "notion_adapter: unknown Notion status %r for task %s — ignoring",
                    notion_status, core_task_id,
                )

        notion_owner = _read_rich_text(props.get("Owner"))
        if notion_owner is not None and notion_owner != task.get("owner"):
            patch["owner"] = notion_owner or None

        notion_due = _read_date(props.get("Due Date"))
        core_due_raw = task.get("due_date")
        core_due = str(core_due_raw)[:10] if core_due_raw else None
        if notion_due != core_due:
            patch["due_date"] = notion_due

        notion_priority = _read_select(props.get("Priority"))
        if notion_priority:
            core_priority = _NOTION_TO_CORE_PRIORITY.get(notion_priority)
            if core_priority and core_priority != task.get("priority"):
                patch["priority"] = core_priority

        if not patch:
            return {"status": "skipped", "reason": "no_change", "task_id": core_task_id}

        # Safety: strip any protected fields that slipped through
        for pf in _PROTECTED_FIELDS:
            patch.pop(pf, None)

        patch["updated_at"] = datetime.now(timezone.utc).isoformat()

        # Apply completion timestamp when status is terminal
        if patch.get("status") in ("DONE", "ARCHIVED"):
            patch["completed_at"] = patch["updated_at"]

        try:
            self._sb.table("core_tasks").update(patch).eq("id", core_task_id).execute()
        except Exception as exc:
            logger.error("notion_adapter: writeback failed for %s: %s", core_task_id, exc)
            return {"status": "error", "error": f"db_write_failed:{exc}", "task_id": core_task_id}

        fields_applied = [k for k in patch if k not in ("updated_at", "completed_at")]
        logger.info(
            "notion_adapter: writeback task %s fields=%s (source=notion)",
            core_task_id, fields_applied,
        )
        return {
            "status":         "applied",
            "task_id":        core_task_id,
            "fields_applied": fields_applied,
        }

    # ── Batch sync: Core → Notion ─────────────────────────────────────────────

    def sync_pending_tasks_to_notion(self, limit: int = _BATCH_LIMIT) -> dict:
        """
        Sync all Core tasks with sync_state in (none, pending, error) to Notion.

        Never processes more than `limit` tasks per call. Never raises — failures
        are recorded per-task in sync_error without stopping the batch.

        Returns summary dict suitable for scheduler logging.
        """
        cfg = self._check_config()
        if cfg is None:
            missing = []
            if not settings.NOTION_API_KEY:
                missing.append("NOTION_API_KEY")
            if not settings.NOTION_TASKS_DATABASE_ID:
                missing.append("NOTION_TASKS_DATABASE_ID")
            return {"status": "CONFIG_REQUIRED", "missing": missing, "synced": 0, "errors": 0}

        try:
            res = (
                self._sb.table("core_tasks")
                .select("id")
                .in_("sync_state", ["none", "pending", "error"])
                .limit(limit)
                .execute()
            )
            pending = res.data or []
        except Exception as exc:
            logger.error("notion_adapter: sync_pending — db fetch failed: %s", exc)
            return {"status": "error", "error": str(exc), "synced": 0, "errors": 0}

        synced = 0
        errors = 0
        skipped = 0

        for row in pending:
            task_id = str(row["id"])
            try:
                result = self.sync_task_to_notion(task_id)
                if result.get("status") == "synced":
                    synced += 1
                elif result.get("status") == "CONFIG_REQUIRED":
                    # Stop batch if config disappeared mid-run
                    break
                else:
                    errors += 1
            except Exception as exc:
                errors += 1
                logger.error("notion_adapter: sync_pending task %s unhandled: %s", task_id, exc)
                _write_sync_error(self._sb, task_id, f"unhandled:{type(exc).__name__}")

        logger.info(
            "notion_adapter: sync_pending done — synced=%d errors=%d skipped=%d total=%d",
            synced, errors, skipped, len(pending),
        )
        return {
            "status":  "ok",
            "total":   len(pending),
            "synced":  synced,
            "errors":  errors,
            "skipped": skipped,
        }

    # ── Polling: Notion → Core ────────────────────────────────────────────────

    def poll_notion_task_updates(
        self, since_minutes: int = _POLL_MINUTES, limit: int = _BATCH_LIMIT
    ) -> dict:
        """
        Poll the Notion database for pages edited within the last `since_minutes` minutes
        and sync allowed field changes back to Core.

        Uses last_edited_time filter + per-task sync_loop_guard.
        Never raises — per-task errors are logged without stopping the poll.
        """
        cfg = self._check_config()
        if cfg is None:
            missing = []
            if not settings.NOTION_API_KEY:
                missing.append("NOTION_API_KEY")
            if not settings.NOTION_TASKS_DATABASE_ID:
                missing.append("NOTION_TASKS_DATABASE_ID")
            return {"status": "CONFIG_REQUIRED", "missing": missing, "applied": 0, "errors": 0}

        api_key, db_id = cfg

        since_dt  = datetime.now(timezone.utc) - timedelta(minutes=since_minutes)
        since_iso = since_dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")

        result = _notion_request(
            "POST",
            f"databases/{db_id}/query",
            api_key,
            json_body={
                "filter": {
                    "timestamp": "last_edited_time",
                    "last_edited_time": {"on_or_after": since_iso},
                },
                "sorts": [{"timestamp": "last_edited_time", "direction": "descending"}],
                "page_size": min(limit, 100),
            },
        )

        if not result["ok"]:
            err = result.get("error", "unknown_error")
            logger.error("notion_adapter: poll_notion_task_updates query failed: %s", err)
            return {"status": "error", "error": err, "applied": 0, "errors": 1}

        pages   = result["data"].get("results") or []
        applied = 0
        skipped = 0
        errors  = 0

        for page in pages:
            page_id = page.get("id")
            if not page_id:
                continue
            try:
                r = self.sync_task_from_notion(page_id)
                s = r.get("status")
                if s == "applied":
                    applied += 1
                elif s == "skipped":
                    skipped += 1
                elif s == "CONFIG_REQUIRED":
                    break
                else:
                    errors += 1
            except Exception as exc:
                errors += 1
                logger.error("notion_adapter: poll — page %s unhandled: %s", page_id, exc)

        logger.info(
            "notion_adapter: poll done — pages=%d applied=%d skipped=%d errors=%d",
            len(pages), applied, skipped, errors,
        )
        return {
            "status":  "ok",
            "pages":   len(pages),
            "applied": applied,
            "skipped": skipped,
            "errors":  errors,
        }

    # ── Legacy compat methods (preserve existing test contract) ──────────────

    def create_external_task(self, task: dict) -> dict:
        """
        Push task to Notion. Delegates to sync_task_to_notion.
        Returns dict with external_task_id, system, sync_state for backward compat.
        """
        result = self.sync_task_to_notion(task["id"])
        status = result.get("status")
        if status == "CONFIG_REQUIRED":
            # Mark error in DB without fake external_id
            _write_sync_error(self._sb, task["id"], f"CONFIG_REQUIRED:{','.join(result.get('missing', []))}")
            return {
                "external_task_id": None,
                "system":           "notion",
                "sync_state":       "error",
                "error":            "CONFIG_REQUIRED",
            }
        if status == "synced":
            return {
                "external_task_id": result["external_task_id"],
                "system":           "notion",
                "sync_state":       "synced",
            }
        return {
            "external_task_id": None,
            "system":           "notion",
            "sync_state":       "error",
            "error":            result.get("error"),
        }

    def update_external_task(self, external_id: str, patch: dict) -> dict:
        """
        Update Notion page fields. Core task content is unchanged.
        This is a targeted property patch (does not fetch Core task).
        """
        cfg = self._check_config()
        if cfg is None:
            return {
                "external_task_id": external_id,
                "system":           "notion",
                "patched_fields":   list(patch.keys()),
                "error":            "CONFIG_REQUIRED",
            }
        api_key, _ = cfg

        # Build Notion properties from allowed patch fields only
        notion_props: dict[str, Any] = {}
        if "status" in patch:
            notion_status = _CORE_TO_NOTION_STATUS.get(patch["status"]) or patch["status"]
            notion_props["Status"] = _prop_select(notion_status)
        if "priority" in patch:
            notion_priority = _CORE_TO_NOTION_PRIORITY.get(patch["priority"]) or patch["priority"]
            notion_props["Priority"] = _prop_select(notion_priority)
        if "owner" in patch:
            notion_props["Owner"] = _prop_rich_text(patch["owner"])
        if "due_date" in patch:
            notion_props["Due Date"] = _prop_date(patch["due_date"])
        if "title" in patch:
            notion_props["Task"] = _prop_title(patch["title"])

        if notion_props:
            _notion_request(
                "PATCH",
                f"pages/{external_id}",
                api_key,
                json_body={"properties": notion_props},
            )

        return {
            "external_task_id": external_id,
            "system":           "notion",
            "patched_fields":   list(patch.keys()),
        }

    def sync_task_status(self, task_id: str) -> dict:
        """Reconcile Core task state. Returns current sync metadata from Core DB."""
        row = (
            self._sb.table("core_tasks")
            .select("id,sync_state,external_task_id,last_synced_at")
            .eq("id", task_id)
            .maybe_single()
            .execute()
            .data
        )
        if not row:
            raise ValueError(f"Task {task_id} not found")
        return {
            "task_id":          task_id,
            "external_task_id": row.get("external_task_id"),
            "sync_state":       row.get("sync_state", "none"),
            "last_synced_at":   row.get("last_synced_at"),
        }
