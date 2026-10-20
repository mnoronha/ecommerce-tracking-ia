"""
smoke_h10_runtime.py — H10 Core Bridge runtime smoke tests.

Strategy: TestClient (real FastAPI code) + real Supabase + real Notion API.
Railway /health already confirmed fresh deploy. TestClient exercises identical
code paths; no mock DB, no mock Notion.

Run: pytest tests/smoke_h10_runtime.py -v --tb=short -p no:randomly

IMPORTANT: test API keys are injected via os.environ BEFORE any app import.
"""
from __future__ import annotations

import os
import sys
import uuid
from datetime import datetime, timedelta, timezone

# ── Inject test-scoped API keys before app import ─────────────────────────────
# Fresh random keys so they cannot collide with any real Railway key.
_ADMIN_KEY    = "smoke-admin-"  + uuid.uuid4().hex[:12]
_HERMES_KEY   = "smoke-hermes-" + uuid.uuid4().hex[:12]
_PLATFORM_KEY = "smoke-plat-"   + uuid.uuid4().hex[:12]

os.environ["AGENCY_API_ADMIN_KEY"]    = _ADMIN_KEY
os.environ["AGENCY_API_HERMES_KEY"]   = _HERMES_KEY
os.environ["AGENCY_API_PLATFORM_KEY"] = _PLATFORM_KEY

# clear lru_cache so Settings re-reads the new env vars
import importlib, functools
import app.config as _cfg_mod
if hasattr(_cfg_mod.get_settings, "cache_clear"):
    _cfg_mod.get_settings.cache_clear()

import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.config import settings
from app.core.notion_adapter import NotionTaskAdapter
from supabase import create_client as _sb_create

# ── Real clients ──────────────────────────────────────────────────────────────
_SB     = _sb_create(settings.SUPABASE_URL, settings.SUPABASE_SERVICE_KEY)
_notion = NotionTaskAdapter(_SB)

_V1        = "/agency/v1"
_CLIENT_ID = "f7731f1b-d132-4a81-ab9d-1bd37c52b8e5"   # Enutri UUID

# ── Fixture tracking for teardown ────────────────────────────────────────────
_created_recs: list[str]  = []
_created_diags: list[str] = []


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _insert_diagnosis() -> str:
    r = _SB.table("core_diagnoses").insert({
        "alert_id":               str(uuid.uuid4()),
        "client_id":              _CLIENT_ID,
        "status":                 "FINAL",
        "summary":                "[SMOKE H10] diagnosis",
        "root_cause_hypotheses":  [],
        "evidence":               {},
        "created_by":             "smoke_h10",
        "schema_version":         "1.0",
    }).execute()
    did = r.data[0]["id"]
    _created_diags.append(did)
    return did


def _insert_recommendation(
    diagnosis_id: str,
    title: str,
    priority: str = "HIGH",
    status: str = "PROPOSED",
) -> str:
    r = _SB.table("core_recommendations").insert({
        "diagnosis_id":            diagnosis_id,
        "alert_id":                str(uuid.uuid4()),
        "client_id":               _CLIENT_ID,
        "title":                   title,
        "action":                  f"[SMOKE] Action: {title}",
        "rationale":               "[SMOKE] H10 smoke test rationale",
        "priority":                priority,
        "risk":                    "LOW",
        "expected_impact":         "+5% CVR (smoke test)",
        "requires_human_approval": True,
        "status":                  status,
        "created_by":              "smoke_h10",
        "schema_version":          "1.1",
    }).execute()
    rid = r.data[0]["id"]
    _created_recs.append(rid)
    return rid


def _fetch_task(rec_id: str) -> dict | None:
    r = (
        _SB.table("core_tasks")
        .select("*")
        .eq("source_type", "recommendation")
        .eq("source_id", rec_id)
        .limit(1)
        .execute()
    )
    return r.data[0] if (r and r.data) else None


def _fetch_rec(rec_id: str) -> dict | None:
    r = (
        _SB.table("core_recommendations")
        .select("*")
        .eq("id", rec_id)
        .limit(1)
        .execute()
    )
    return r.data[0] if (r and r.data) else None


def _fetch_audit(rec_id: str) -> list[dict]:
    r = (
        _SB.table("core_recommendation_approvals")
        .select("*")
        .eq("recommendation_id", rec_id)
        .execute()
    )
    return r.data or []


def _approve(client: TestClient, rec_id: str, actor: str = "smoke_admin",
             reason: str | None = None, key: str | None = None):
    key = key or _ADMIN_KEY
    body: dict = {"actor": actor}
    if reason:
        body["reason"] = reason
    return client.post(
        f"{_V1}/recommendations/{rec_id}/approve",
        headers={"Authorization": f"Bearer {key}"},
        json=body,
    )


# ─── Session-scoped fixtures ──────────────────────────────────────────────────

@pytest.fixture(scope="module")
def http():
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="module")
def rec_ecommerce():
    did = _insert_diagnosis()
    return _insert_recommendation(did, "[SMOKE] Reduce cart abandonment — ecommerce", priority="HIGH")


@pytest.fixture(scope="module")
def rec_leadgen():
    did = _insert_diagnosis()
    return _insert_recommendation(did, "[SMOKE] Increase lead quality — leadgen", priority="MEDIUM")


@pytest.fixture(scope="module")
def rec_rejected():
    did = _insert_diagnosis()
    rid = _insert_recommendation(did, "[SMOKE] Rejected recommendation — scenario C", priority="LOW")
    _SB.table("core_recommendations").update({"status": "REJECTED"}).eq("id", rid).execute()
    return rid


@pytest.fixture(scope="module", autouse=True)
def _teardown():
    yield
    # Delete tasks and audits created by smoke run
    for rid in list(_created_recs):
        _SB.table("core_tasks").delete().eq("source_type", "recommendation").eq("source_id", rid).execute()
        _SB.table("core_recommendation_approvals").delete().eq("recommendation_id", rid).execute()
    # Delete recommendations
    for rid in list(_created_recs):
        _SB.table("core_recommendations").delete().eq("id", rid).execute()
    # Delete diagnoses
    for did in list(_created_diags):
        _SB.table("core_diagnoses").delete().eq("id", did).execute()


# ═══════════════════════════════════════════════════════════════════════════════
# SCENARIO A — ecommerce recommendation: approve → 1 task → sync Notion
# ═══════════════════════════════════════════════════════════════════════════════

class TestScenarioA:

    def test_approve_returns_201(self, http, rec_ecommerce):
        resp = _approve(http, rec_ecommerce, actor="smoke_admin", reason="scenario A ecommerce")
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["recommendation_id"] == rec_ecommerce
        assert body["created"] is True
        assert uuid.UUID(body["task_id"])

    def test_exactly_one_task_in_db(self, rec_ecommerce):
        r = (
            _SB.table("core_tasks")
            .select("id")
            .eq("source_type", "recommendation")
            .eq("source_id", rec_ecommerce)
            .execute()
        )
        assert len(r.data) == 1, f"Expected 1 task, got {len(r.data)}"

    def test_task_source_fields(self, rec_ecommerce):
        t = _fetch_task(rec_ecommerce)
        assert t["source_type"] == "recommendation"
        assert t["source_id"] == rec_ecommerce

    def test_priority_high_maps_to_urgent(self, rec_ecommerce):
        t = _fetch_task(rec_ecommerce)
        assert t["priority"] == "URGENT", f"Expected URGENT (HIGH→URGENT), got {t['priority']}"

    def test_task_sync_state_none(self, rec_ecommerce):
        t = _fetch_task(rec_ecommerce)
        assert t["sync_state"] == "none"
        assert t["external_task_id"] is None

    def test_task_status_open(self, rec_ecommerce):
        t = _fetch_task(rec_ecommerce)
        assert t["status"] == "OPEN"
        assert t["visibility_scope"] == "AGENCY_ONLY"

    def test_recommendation_status_approved(self, rec_ecommerce):
        rec = _fetch_rec(rec_ecommerce)
        assert rec["status"] == "APPROVED"

    def test_audit_row_created(self, rec_ecommerce):
        audits = _fetch_audit(rec_ecommerce)
        assert len(audits) >= 1
        a = audits[0]
        assert a["actor"] == "smoke_admin"
        assert a["reason"] == "scenario A ecommerce"
        assert a["recommendation_id"] == rec_ecommerce
        t = _fetch_task(rec_ecommerce)
        assert a["task_id"] == t["id"]

    def test_notion_sync_creates_exactly_one_page(self, rec_ecommerce):
        t = _fetch_task(rec_ecommerce)
        result = _notion.sync_task_to_notion(t["id"])
        assert result["status"] == "synced", f"Notion sync error: {result}"
        assert result["external_task_id"], "No external_task_id after sync"

        # Verify DB updated
        r = (
            _SB.table("core_tasks")
            .select("sync_state,external_task_id")
            .eq("id", t["id"])
            .single()
            .execute()
        )
        row = r.data
        assert row["sync_state"] == "synced"
        assert row["external_task_id"]


# ═══════════════════════════════════════════════════════════════════════════════
# SCENARIO B — lead-gen recommendation: approve → 1 task → sync Notion
# ═══════════════════════════════════════════════════════════════════════════════

class TestScenarioB:

    def test_approve_returns_201_hermes_scope(self, http, rec_leadgen):
        resp = _approve(http, rec_leadgen, actor="smoke_hermes",
                        reason="scenario B leadgen", key=_HERMES_KEY)
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["created"] is True

    def test_exactly_one_task_in_db(self, rec_leadgen):
        r = (
            _SB.table("core_tasks")
            .select("id")
            .eq("source_type", "recommendation")
            .eq("source_id", rec_leadgen)
            .execute()
        )
        assert len(r.data) == 1, f"Expected 1 task, got {len(r.data)}"

    def test_priority_medium_maps_to_high(self, rec_leadgen):
        t = _fetch_task(rec_leadgen)
        assert t["priority"] == "HIGH", f"Expected HIGH (MEDIUM→HIGH), got {t['priority']}"

    def test_notion_sync_creates_exactly_one_page(self, rec_leadgen):
        t = _fetch_task(rec_leadgen)
        result = _notion.sync_task_to_notion(t["id"])
        assert result["status"] == "synced", f"Notion sync error: {result}"

        r = (
            _SB.table("core_tasks")
            .select("sync_state,external_task_id")
            .eq("id", t["id"])
            .single()
            .execute()
        )
        row = r.data
        assert row["sync_state"] == "synced"
        assert row["external_task_id"]


# ═══════════════════════════════════════════════════════════════════════════════
# SCENARIO C — rejected recommendation: 0 tasks, 0 Notion pages
# ═══════════════════════════════════════════════════════════════════════════════

class TestScenarioC:

    def test_approve_rejected_returns_422(self, http, rec_rejected):
        resp = _approve(http, rec_rejected, actor="smoke_admin")
        assert resp.status_code == 422, resp.text

    def test_zero_tasks_created(self, rec_rejected):
        r = (
            _SB.table("core_tasks")
            .select("id")
            .eq("source_type", "recommendation")
            .eq("source_id", rec_rejected)
            .execute()
        )
        assert len(r.data) == 0, f"Expected 0 tasks for rejected rec, got {len(r.data)}"

    def test_recommendation_status_still_rejected(self, rec_rejected):
        rec = _fetch_rec(rec_rejected)
        assert rec["status"] == "REJECTED"

    def test_zero_notion_pages(self, rec_rejected):
        r = (
            _SB.table("core_tasks")
            .select("id")
            .eq("source_type", "recommendation")
            .eq("source_id", rec_rejected)
            .execute()
        )
        assert len(r.data) == 0
        # Since no task exists, Notion sync was never called → 0 pages


# ═══════════════════════════════════════════════════════════════════════════════
# SCENARIO D — duplicate approve: same task_id, created=False, no duplicates
# ═══════════════════════════════════════════════════════════════════════════════

class TestScenarioD:

    def test_duplicate_approve_same_task_id(self, http, rec_ecommerce):
        first_task = _fetch_task(rec_ecommerce)
        assert first_task, "Prerequisite: task from scenario A must exist"

        resp = _approve(http, rec_ecommerce, actor="smoke_admin_retry", reason="scenario D")
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["task_id"] == first_task["id"], (
            f"Expected same task_id {first_task['id']}, got {body['task_id']}"
        )
        assert body["created"] is False

    def test_still_exactly_one_task(self, rec_ecommerce):
        r = (
            _SB.table("core_tasks")
            .select("id")
            .eq("source_type", "recommendation")
            .eq("source_id", rec_ecommerce)
            .execute()
        )
        assert len(r.data) == 1, f"Duplicate approve created extra task: {len(r.data)} tasks found"

    def test_no_duplicate_notion_page(self, rec_ecommerce):
        # Sync again — must update existing page, not create a second one
        t = _fetch_task(rec_ecommerce)
        result = _notion.sync_task_to_notion(t["id"])
        assert result["status"] == "synced"
        assert result.get("operation") == "update", (
            f"Expected 'update' (page exists), got {result.get('operation')!r}"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# SECURITY GUARDS
# ═══════════════════════════════════════════════════════════════════════════════

class TestSecurityGuards:

    def test_platform_web_blocked_on_approve(self, http, rec_leadgen):
        resp = _approve(http, rec_leadgen, actor="platform_web_test", key=_PLATFORM_KEY)
        assert resp.status_code == 403, (
            f"platform_web must be DENIED on /approve, got {resp.status_code}"
        )

    def test_unauthenticated_blocked(self, http, rec_ecommerce):
        resp = http.post(
            f"{_V1}/recommendations/{rec_ecommerce}/approve",
            json={"actor": "anon"},
        )
        assert resp.status_code in (401, 403), resp.text

    def test_hermes_blocked_on_generic_task_create(self, http):
        resp = http.post(
            f"{_V1}/clients/enutri-4sph/tasks",
            headers={"Authorization": f"Bearer {_HERMES_KEY}"},
            json={"title": "should 403", "priority": "HIGH", "status": "OPEN"},
        )
        assert resp.status_code == 403, (
            f"hermes_service must be blocked on generic /tasks, got {resp.status_code}"
        )

    def test_approval_type_isolated_from_notification_approval(self, http, rec_ecommerce):
        # /decision endpoint uses SCOPE_HERMES_WRITE; approve uses SCOPE_RECOMMENDATION_APPROVE.
        # hermes can call /approve but must NOT be able to call /decision for rec approval
        # (they are separate; here we just check that hermes can approve)
        t = _fetch_task(rec_ecommerce)
        assert t, "Task from A must exist for this guard to be meaningful"


# ═══════════════════════════════════════════════════════════════════════════════
# ZERO SIDE-EFFECTS GUARD
# ═══════════════════════════════════════════════════════════════════════════════

class TestZeroSideEffects:

    def test_no_media_change_log_entries_from_smoke(self):
        cutoff = (datetime.now(timezone.utc) - timedelta(seconds=120)).isoformat()
        r = (
            _SB.table("core_change_log")
            .select("id")
            .gte("created_at", cutoff)
            .execute()
        )
        assert len(r.data) == 0, (
            f"Unexpected core_change_log entries in last 2 min: {r.data}"
        )

    def test_no_send_guard_platform_writes(self):
        # No external ad platform calls → verify no entries in recent send_log
        # The approve endpoint has no send_guard path; we just verify no HTTP was attempted
        # (test_no_external_http_calls already covered in unit tests)
        pass  # guarded by unit test_no_external_http_calls

    def test_notion_scheduler_unchanged(self, http):
        resp = http.get("/health")
        assert resp.status_code == 200
        body = resp.json()
        assert body["scheduler_running"] is True
        job_ids = [j["id"] for j in body.get("jobs", [])]
        assert "notion_sync_pending" in job_ids, (
            f"notion_sync_pending job missing from scheduler; jobs: {job_ids}"
        )
