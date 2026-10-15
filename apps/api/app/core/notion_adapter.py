"""
Notion task adapter — stub implementation.

Core remains canonical. This adapter pushes task state to Notion (write-only output).
Replace method bodies with real Notion API calls when credentials are available.

Invariants:
- create_external_task: writes sync metadata to Core; task content stays in Core
- update_external_task: does NOT touch Core DB (only pushes to Notion)
- sync_task_status: reads from Core only, never writes task content
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from ..database import get_supabase


class NotionTaskAdapter:

    def __init__(self, supabase=None):
        self._sb = supabase or get_supabase()

    def create_external_task(self, task: dict) -> dict:
        """Push task to Notion (stub). Updates Core sync metadata only."""
        task_id     = task["id"]
        external_id = f"notion-stub-{task_id}"
        self._sb.table("core_tasks").update({
            "external_task_id": external_id,
            "external_system":  "notion",
            "sync_state":       "synced",
            "last_synced_at":   datetime.now(timezone.utc).isoformat(),
            "sync_error":       None,
        }).eq("id", task_id).execute()
        return {"external_task_id": external_id, "system": "notion", "sync_state": "synced"}

    def update_external_task(self, external_id: str, patch: dict) -> dict:
        """Update Notion page fields (stub). Core task content is unchanged."""
        return {
            "external_task_id": external_id,
            "system":           "notion",
            "patched_fields":   list(patch.keys()),
        }

    def sync_task_status(self, task_id: str) -> dict:
        """Reconcile Core task state to Notion (stub). Reads from Core only."""
        row = (
            self._sb.table("core_tasks")
            .select("id,sync_state,external_task_id")
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
        }
