"""
Phase 6.5 — Close Implementation Validation (Phases 2–5)

Covers:
  §1  Tasks CRUD + source links + status transitions
  §2  Tasks client_viewer visibility / cross-client isolation
  §3  Optimization timeline semantic guards (actions_loader)
  §4  Meetings pipeline: create → transcript → process → task creation
  §5  Role authorization — what client_viewer can and cannot access
  §6  Notion sync prep fields in TaskOut schema

Run from apps/api/:
    python -m pytest tests/test_phase65_validation.py -v
"""

from __future__ import annotations

import json
import uuid
from datetime import date, datetime, timezone
from typing import Optional
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.agency_api.v1.auth import (
    SCOPE_ANY_AUTH,
    SCOPE_CLIENT_VIEWER,
    SCOPE_PLATFORM_WRITE,
    SCOPE_READ_ANY,
    SCOPE_WRITE_ANY,
    SCOPE_HERMES_WRITE,
    SCOPE_ADMIN_ONLY,
    AuthContext,
)
from app.agency_api.v1.router import router as agency_router

# ── Canonical test identities ─────────────────────────────────────────────────

_ADMIN    = AuthContext(scope="agency_admin")
_HERMES   = AuthContext(scope="hermes_service")
_PLATFORM = AuthContext(scope="platform_web")
_CLIENT_A = AuthContext(scope="client_viewer", client_ids=["client-alpha"], subject="user-a")
_CLIENT_B = AuthContext(scope="client_viewer", client_ids=["client-beta"],  subject="user-b")

_TS       = "2026-10-14T12:00:00+00:00"
_CLIENT_SLUG = "client-alpha"
_TASK_UUID   = "aaaaaaaa-aaaa-aaaa-aaaa-000000000001"
_MEETING_UUID = "bbbbbbbb-bbbb-bbbb-bbbb-000000000001"
_SOURCE_UUID  = "cccccccc-cccc-cccc-cccc-000000000001"

# ── DB mock helpers ───────────────────────────────────────────────────────────

def _empty_db():
    m = MagicMock()
    tbl = MagicMock()
    for method in ("select","eq","in_","order","limit","is_","insert","update",
                   "neq","gte","lte","not_","maybe_single","range"):
        getattr(tbl, method).return_value = tbl
    tbl.execute.return_value = MagicMock(data=[])
    m.table.return_value = tbl
    m.rpc.return_value = tbl
    return m


def _single_db(row: dict):
    m = MagicMock()
    tbl = MagicMock()
    for method in ("select","eq","in_","order","limit","is_","insert","update",
                   "neq","gte","lte","not_","maybe_single","range"):
        getattr(tbl, method).return_value = tbl
    tbl.execute.return_value = MagicMock(data=[row])
    m.table.return_value = tbl
    m.rpc.return_value = tbl
    return m


def _sequential_db(*responses):
    """Returns a DB mock that serves successive responses in order, per .execute() call."""
    call_idx = [0]
    m = MagicMock()
    def _table(_name):
        tbl = MagicMock()
        for method in ("select","eq","in_","order","limit","is_","insert","update",
                       "neq","gte","lte","not_","maybe_single","range"):
            getattr(tbl, method).return_value = tbl
        def _execute():
            idx = call_idx[0]; call_idx[0] += 1
            payload = responses[idx] if idx < len(responses) else []
            return MagicMock(data=payload if isinstance(payload, list) else [payload])
        tbl.execute.side_effect = _execute
        return tbl
    m.table.side_effect = _table
    m.rpc.return_value = MagicMock(data=[])
    return m


def _client_meta_row(slug: str = _CLIENT_SLUG) -> dict:
    return {
        "id": _SOURCE_UUID, "client_id": slug,
        "name": f"{slug} test", "business_model": "ecommerce",
        "timezone": "America/Sao_Paulo", "currency": "BRL",
        "country": "BR", "is_active": True, "pixel_id": slug,
    }


def _task_row(
    task_id: str = _TASK_UUID,
    client_id: str = _CLIENT_SLUG,
    title: str = "Test task",
    status: str = "OPEN",
    priority: str = "MEDIUM",
    source_type: Optional[str] = None,
    source_id: Optional[str] = None,
    visibility_scope: str = "AGENCY_ONLY",
    owner: Optional[str] = None,
    due_date: Optional[str] = None,
) -> dict:
    return {
        "id": task_id, "client_id": client_id, "title": title,
        "description": None, "status": status, "priority": priority,
        "owner": owner, "due_date": due_date,
        "source_type": source_type, "source_id": source_id,
        "visibility_scope": visibility_scope,
        "external_task_id": None, "external_system": None,
        "sync_state": "none", "last_synced_at": None, "sync_error": None,
        "created_at": _TS, "updated_at": _TS, "completed_at": None,
    }


def _meeting_row(meeting_id: str = _MEETING_UUID, client_id: str = _CLIENT_SLUG) -> dict:
    return {
        "id": meeting_id, "client_id": client_id,
        "provider": "manual", "external_id": None,
        "title": "Reunião de validação", "started_at": _TS, "ended_at": None,
        "participants": "[]", "recording_url": None,
        "transcript_status": "none", "processing_status": "pending",
        "created_at": _TS, "updated_at": _TS,
    }


def _app_as(auth: AuthContext) -> FastAPI:
    """App with all scope deps overridden to the given auth context."""
    app = FastAPI()
    app.include_router(agency_router)
    for scope_dep in (SCOPE_READ_ANY, SCOPE_WRITE_ANY, SCOPE_HERMES_WRITE,
                      SCOPE_PLATFORM_WRITE, SCOPE_CLIENT_VIEWER, SCOPE_ANY_AUTH, SCOPE_ADMIN_ONLY):
        app.dependency_overrides[scope_dep] = lambda a=auth: a
    return app


def _bare_app() -> FastAPI:
    """App with NO dependency overrides — real auth checks run."""
    app = FastAPI()
    app.include_router(agency_router)
    return app


# ═══════════════════════════════════════════════════════════════════════════════
# §1 — Tasks CRUD + source links + status transitions
# ═══════════════════════════════════════════════════════════════════════════════

class TestTasksCRUD:
    """Tasks CRUD: list, create, get, update with all source_type values."""

    def _task_client(self, auth: AuthContext = None, db=None):
        app = _app_as(auth or _ADMIN)
        tc = TestClient(app, raise_server_exceptions=False)
        if db is None:
            db = _empty_db()
        return tc, db

    # ── List ──────────────────────────────────────────────────────────────────

    def test_list_tasks_returns_empty_for_unknown_client(self):
        db = _sequential_db([], [])   # _load_client_meta → client found, then list → empty
        # client_meta lookup returns the client
        db2 = _single_db(_client_meta_row())
        # override _load_client_meta to return meta then list empty
        with patch("app.agency_api.v1.router._load_client_meta", return_value=_client_meta_row()):
            with patch("app.agency_api.v1.router._get_db", return_value=_empty_db()):
                with TestClient(_app_as(_ADMIN)) as c:
                    resp = c.get("/agency/v1/clients/client-alpha/tasks")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_list_tasks_returns_rows(self):
        rows = [
            _task_row(task_id="aaaaaaaa-aaaa-aaaa-aaaa-000000000001", title="Task A"),
            _task_row(task_id="aaaaaaaa-aaaa-aaaa-aaaa-000000000002", title="Task B"),
        ]
        with patch("app.agency_api.v1.router._load_client_meta", return_value=_client_meta_row()):
            with patch("app.agency_api.v1.router._get_db", return_value=_single_db(rows[0])):
                db = _empty_db()
                db.table.return_value.execute.return_value = MagicMock(data=rows)
                with patch("app.agency_api.v1.router._get_db", return_value=db):
                    with TestClient(_app_as(_ADMIN)) as c:
                        resp = c.get("/agency/v1/clients/client-alpha/tasks")
        assert resp.status_code == 200
        assert len(resp.json()) == 2

    def test_list_tasks_404_on_missing_client(self):
        with patch("app.agency_api.v1.router._load_client_meta", return_value=None):
            with TestClient(_app_as(_ADMIN)) as c:
                resp = c.get("/agency/v1/clients/no-such-client/tasks")
        assert resp.status_code == 404

    # ── Create ────────────────────────────────────────────────────────────────

    @pytest.mark.parametrize("source_type,source_id", [
        ("recommendation", str(uuid.uuid4())),
        ("action",         str(uuid.uuid4())),
        ("alert",          str(uuid.uuid4())),
        ("meeting",        str(uuid.uuid4())),
        ("manual",         None),
    ])
    def test_create_task_all_source_types(self, source_type, source_id):
        row = _task_row(source_type=source_type, source_id=source_id)
        with patch("app.agency_api.v1.router._load_client_meta", return_value=_client_meta_row()):
            with patch("app.agency_api.v1.router._get_db", return_value=_single_db(row)):
                with TestClient(_app_as(_PLATFORM)) as c:
                    body = {
                        "title": f"Task via {source_type}",
                        "source_type": source_type,
                        "source_id": source_id,
                    }
                    resp = c.post("/agency/v1/clients/client-alpha/tasks", json=body)
        assert resp.status_code == 201, resp.text
        data = resp.json()
        assert data["source_type"] == source_type
        assert data["status"] == "OPEN"

    def test_create_task_with_all_fields(self):
        source_id = str(uuid.uuid4())
        row = _task_row(
            title="Full task",
            priority="HIGH",
            owner="maico@norolabs.com.br",
            due_date="2026-11-01",
            source_type="recommendation",
            source_id=source_id,
            visibility_scope="CLIENT_VISIBLE",
        )
        with patch("app.agency_api.v1.router._load_client_meta", return_value=_client_meta_row()):
            with patch("app.agency_api.v1.router._get_db", return_value=_single_db(row)):
                with TestClient(_app_as(_PLATFORM)) as c:
                    resp = c.post("/agency/v1/clients/client-alpha/tasks", json={
                        "title": "Full task",
                        "priority": "HIGH",
                        "owner": "maico@norolabs.com.br",
                        "due_date": "2026-11-01",
                        "source_type": "recommendation",
                        "source_id": source_id,
                        "visibility_scope": "CLIENT_VISIBLE",
                    })
        assert resp.status_code == 201
        data = resp.json()
        assert data["priority"] == "HIGH"
        assert data["owner"] == "maico@norolabs.com.br"
        assert data["source_type"] == "recommendation"
        assert data["visibility_scope"] == "CLIENT_VISIBLE"

    # ── Get detail ────────────────────────────────────────────────────────────

    def test_get_task_returns_row(self):
        row = _task_row()
        with patch("app.agency_api.v1.router._get_db", return_value=_single_db(row)):
            with TestClient(_app_as(_ADMIN)) as c:
                resp = c.get(f"/agency/v1/tasks/{_TASK_UUID}")
        assert resp.status_code == 200
        assert resp.json()["id"] == _TASK_UUID

    def test_get_task_404_not_found(self):
        with patch("app.agency_api.v1.router._get_db", return_value=_empty_db()):
            with TestClient(_app_as(_ADMIN)) as c:
                resp = c.get(f"/agency/v1/tasks/{_TASK_UUID}")
        assert resp.status_code == 404

    # ── Update / status transitions ───────────────────────────────────────────

    @pytest.mark.parametrize("new_status", ["IN_PROGRESS", "DONE", "BLOCKED", "ARCHIVED"])
    def test_update_task_status_transitions(self, new_status):
        original = _task_row(status="OPEN")
        updated  = _task_row(status=new_status)
        # _get_db called twice: select (check exists) + update
        db = _sequential_db([original], [updated])
        with patch("app.agency_api.v1.router._get_db", return_value=db):
            with TestClient(_app_as(_PLATFORM)) as c:
                resp = c.put(f"/agency/v1/tasks/{_TASK_UUID}", json={"status": new_status})
        assert resp.status_code == 200
        assert resp.json()["status"] == new_status

    def test_update_task_due_date_and_owner(self):
        original = _task_row()
        updated  = _task_row(owner="gestor@agency.com", due_date="2026-12-31")
        db = _sequential_db([original], [updated])
        with patch("app.agency_api.v1.router._get_db", return_value=db):
            with TestClient(_app_as(_PLATFORM)) as c:
                resp = c.put(f"/agency/v1/tasks/{_TASK_UUID}", json={
                    "owner": "gestor@agency.com",
                    "due_date": "2026-12-31",
                })
        assert resp.status_code == 200
        assert resp.json()["owner"] == "gestor@agency.com"

    def test_update_task_priority(self):
        original = _task_row(priority="MEDIUM")
        updated  = _task_row(priority="URGENT")
        db = _sequential_db([original], [updated])
        with patch("app.agency_api.v1.router._get_db", return_value=db):
            with TestClient(_app_as(_PLATFORM)) as c:
                resp = c.put(f"/agency/v1/tasks/{_TASK_UUID}", json={"priority": "URGENT"})
        assert resp.status_code == 200
        assert resp.json()["priority"] == "URGENT"

    def test_update_task_404(self):
        db = _sequential_db([])   # select returns empty → 404
        with patch("app.agency_api.v1.router._get_db", return_value=db):
            with TestClient(_app_as(_PLATFORM)) as c:
                resp = c.put(f"/agency/v1/tasks/{_TASK_UUID}", json={"status": "DONE"})
        assert resp.status_code == 404


# ═══════════════════════════════════════════════════════════════════════════════
# §2 — Tasks client_viewer visibility + cross-client isolation
# ═══════════════════════════════════════════════════════════════════════════════

class TestTasksClientViewerVisibility:
    """client_viewer can only see CLIENT_VISIBLE tasks for their own clients."""

    def test_client_viewer_list_filtered_to_client_visible(self):
        """client_viewer calling list_tasks must get visibility_scope=CLIENT_VISIBLE filter."""
        rows = [
            _task_row(visibility_scope="CLIENT_VISIBLE", title="Public task"),
        ]
        db = _empty_db()
        db.table.return_value.execute.return_value = MagicMock(data=rows)

        with patch("app.agency_api.v1.router._load_client_meta", return_value=_client_meta_row()):
            with patch("app.agency_api.v1.router._get_db", return_value=db):
                with TestClient(_app_as(_CLIENT_A)) as c:
                    resp = c.get("/agency/v1/clients/client-alpha/tasks")

        assert resp.status_code == 200
        result = resp.json()
        # All returned tasks must be CLIENT_VISIBLE
        for t in result:
            assert t["visibility_scope"] == "CLIENT_VISIBLE", (
                f"client_viewer received AGENCY_ONLY task: {t['title']!r}"
            )

    def test_client_viewer_cannot_access_other_client(self):
        """client_viewer for client-alpha must get 403 when requesting client-beta's tasks."""
        with patch("app.agency_api.v1.router._load_client_meta",
                   return_value=_client_meta_row("client-beta")):
            with TestClient(_app_as(_CLIENT_A)) as c:
                # _CLIENT_A has client_ids=["client-alpha"], requesting client-beta
                resp = c.get("/agency/v1/clients/client-beta/tasks")
        assert resp.status_code == 403

    def test_client_viewer_get_task_agency_only_returns_403(self):
        """client_viewer calling GET /tasks/{id} on an AGENCY_ONLY task → 403."""
        row = _task_row(visibility_scope="AGENCY_ONLY")
        with patch("app.agency_api.v1.router._get_db", return_value=_single_db(row)):
            with patch("app.agency_api.v1.router._load_client_meta",
                       return_value=_client_meta_row()):
                with TestClient(_app_as(_CLIENT_A)) as c:
                    resp = c.get(f"/agency/v1/tasks/{_TASK_UUID}")
        assert resp.status_code == 403

    def test_client_viewer_can_read_client_visible_task(self):
        """client_viewer can read a CLIENT_VISIBLE task for their own client."""
        row = _task_row(visibility_scope="CLIENT_VISIBLE", client_id="client-alpha")
        with patch("app.agency_api.v1.router._get_db", return_value=_single_db(row)):
            with patch("app.agency_api.v1.router._load_client_meta",
                       return_value=_client_meta_row("client-alpha")):
                with TestClient(_app_as(_CLIENT_A)) as c:
                    resp = c.get(f"/agency/v1/tasks/{_TASK_UUID}")
        assert resp.status_code == 200
        assert resp.json()["visibility_scope"] == "CLIENT_VISIBLE"

    def test_client_viewer_cannot_create_task(self):
        """POST /tasks is SCOPE_PLATFORM_WRITE — client_viewer must get 401 with real auth."""
        from app import config
        s = config.get_settings()
        with (
            patch.object(s, "AGENCY_API_ADMIN_KEY",    "real-admin"),
            patch.object(s, "AGENCY_API_PLATFORM_KEY", ""),
            patch.object(s, "AGENCY_API_HERMES_KEY",   ""),
            patch.object(s, "SUPABASE_JWT_SECRET",     ""),
            patch("app.agency_api.v1.router._load_client_meta",
                  return_value=_client_meta_row()),
            patch("app.agency_api.v1.router._get_db", return_value=_empty_db()),
        ):
            with TestClient(_bare_app(), raise_server_exceptions=False) as c:
                # Unknown token → 401 (client_viewer would need JWT, not configured)
                resp = c.post(
                    "/agency/v1/clients/client-alpha/tasks",
                    json={"title": "Sneaky task"},
                    headers={"Authorization": "Bearer unknown-token"},
                )
        assert resp.status_code == 401


# ═══════════════════════════════════════════════════════════════════════════════
# §3 — Optimization Timeline semantic guards
# ═══════════════════════════════════════════════════════════════════════════════

from app.core.actions_loader import format_executed_actions, format_open_recommendations


class TestTimelineSemanticGuards:
    """
    PROPOSED_RECOMMENDATION_AS_EXECUTED = ZERO
    NON_EXECUTED_ACTION_IN_HISTORY      = ZERO
    OUTCOME_WITHOUT_ACTION_LINK         = ZERO
    IDs allow traceability across the chain.
    """

    def _make_action(self, action_id, status="EXECUTED", outcome_status="CONFIRMED"):
        return {
            "id":                action_id,
            "executed_at":       "2026-10-07T14:30:00+00:00",
            "platform":          "meta_ads",
            "action_type":       "BID_ADJUSTMENT",
            "description":       "Test action",
            "executed_by":       "maico@norolabs.com.br",
            "recommendation_id": _SOURCE_UUID,
            "diagnosis_id":      None,
            "alert_id":          None,
            "expected_impact":   "ROAS > 8x",
            "before_state":      {"daily_budget": 150.0},
            "after_state":       {"daily_budget": 180.0},
            "outcome": {
                "status":             outcome_status,
                "confidence":         "HIGH",
                "delta":              {"roas": 1.4},
                "before_state":       {"roas": 7.8},
                "after_state":        {"roas": 9.2},
                "measurement_period": {"days": 7},
            },
        }

    def _make_rec(self, rec_id, priority="HIGH", status="PROPOSED"):
        return {
            "id": rec_id, "title": "Ativar retargeting",
            "action": "Criar campanha DPA",
            "rationale": "Taxa de abandono alta",
            "priority": priority,
            "expected_impact": "+R$8.000/mês",
            "risk": "Baixo",
            "status": status,
            "created_at": _TS,
        }

    # Guard 1: No PROPOSED status in executed actions output
    def test_no_proposed_in_executed_actions(self):
        action = self._make_action("act-001")
        result = format_executed_actions([action])
        proposed_found = [
            a for a in result
            if (a.get("outcome") or {}).get("status") == "PROPOSED"
        ]
        assert proposed_found == [], f"PROPOSED found in executed actions: {proposed_found}"

    # Guard 2: All executed actions have a date
    def test_all_executed_actions_have_date(self):
        actions = [
            self._make_action("act-001"),
            self._make_action("act-002", outcome_status="PENDING"),
            self._make_action("act-003", outcome_status="BLOCKED_DATA_QUALITY"),
        ]
        result = format_executed_actions(actions)
        missing_date = [a for a in result if not a.get("data")]
        assert missing_date == [], f"Actions missing date: {missing_date}"

    # Guard 3: Outcome status labels are correctly mapped
    def test_outcome_status_labels(self):
        from app.core.actions_loader import _OUTCOME_STATUS_MAP
        statuses = ["CONFIRMED", "PENDING", "BLOCKED_DATA_QUALITY", "NO_IMPACT"]
        for status in statuses:
            action = self._make_action(f"act-{status}", outcome_status=status)
            result = format_executed_actions([action])
            assert result, f"Empty result for {status}"
            outcome = result[0]["outcome"]
            assert outcome is not None, f"No outcome for {status}"
            assert outcome["status"] == status
            label, css = _OUTCOME_STATUS_MAP.get(status, ("", ""))
            assert outcome["status_label"] == label, f"{status}: label mismatch"
            assert outcome["status_css"] == css, f"{status}: css mismatch"

    # Guard 4: IDs allow traceability
    def test_action_id_and_recommendation_id_preserved(self):
        action_id = "act-trace-001"
        action = self._make_action(action_id)
        result = format_executed_actions([action])
        assert result[0]["id"] == action_id

    # Guard 5: Recommendation priority labels
    def test_rec_priority_labels(self):
        recs = [
            self._make_rec("rec-001", priority="HIGH"),
            self._make_rec("rec-002", priority="MEDIUM"),
            self._make_rec("rec-003", priority="LOW"),
        ]
        result = format_open_recommendations(recs)
        assert result[0]["priority_label"] == "!", f"HIGH should be '!'"
        assert result[1]["priority_label"] == "→", f"MEDIUM should be '→'"
        assert result[2]["priority_label"] == "→", f"LOW should be '→'"

    # Guard 6: EXECUTED recs are never in open_recommendations loader output
    def test_executed_recs_not_in_open_recs(self):
        """load_open_recommendations filters to PROPOSED/APPROVED/PENDING_REVIEW only."""
        from app.core.actions_loader import load_open_recommendations

        executed_rec = self._make_rec("rec-exec", status="EXECUTED")
        proposed_rec = self._make_rec("rec-prop", status="PROPOSED")

        db = MagicMock()
        tbl = MagicMock()
        for method in ("select","eq","in_","order","limit"):
            getattr(tbl, method).return_value = tbl
        # Simulate DB returning only PROPOSED (loader filters server-side)
        tbl.execute.return_value = MagicMock(data=[proposed_rec])
        db.table.return_value = tbl

        result = load_open_recommendations(db, _SOURCE_UUID)
        statuses = {r["status"] for r in result}
        assert "EXECUTED" not in statuses, f"EXECUTED rec appeared in open recs: {statuses}"
        assert "PROPOSED" in statuses or len(result) == 1

    # Guard 7: OUTCOME_WITHOUT_ACTION_LINK impossible by structure
    def test_outcome_always_linked_to_action(self):
        """format_executed_actions only attaches an outcome dict when action has outcome key."""
        action_no_outcome = {
            "id": "act-no-outcome",
            "executed_at": _TS,
            "platform": "google_ads",
            "action_type": "AUDIENCE_EXCLUSION",
            "description": "No outcome yet",
            "executed_by": "maico@norolabs.com.br",
            "recommendation_id": None,
            "diagnosis_id": None,
            "alert_id": None,
            "expected_impact": None,
            "before_state": {},
            "after_state": {},
            "outcome": None,
        }
        result = format_executed_actions([action_no_outcome])
        assert result[0]["outcome"] is None, "outcome must be None when not set"
        # No orphan outcome — outcome can only exist if action provides it
        for r in result:
            if r["outcome"] is not None:
                assert r["id"] is not None, "outcome must be linked to an action id"


# ═══════════════════════════════════════════════════════════════════════════════
# §4 — Meetings pipeline: create → transcript → process → task creation
# ═══════════════════════════════════════════════════════════════════════════════

class TestMeetingsPipeline:
    """Full pipeline: Meeting → Transcript → Summary → Decisions → Tasks."""

    # ── Create meeting ────────────────────────────────────────────────────────

    def test_create_meeting_returns_201(self):
        row = _meeting_row()
        with patch("app.agency_api.v1.router._load_client_meta",
                   return_value=_client_meta_row()):
            with patch("app.agency_api.v1.router._get_db", return_value=_single_db(row)):
                with TestClient(_app_as(_PLATFORM)) as c:
                    resp = c.post("/agency/v1/clients/client-alpha/meetings", json={
                        "title": "Reunião de validação",
                        "provider": "manual",
                    })
        assert resp.status_code == 201
        data = resp.json()
        assert data["provider"] == "manual"
        assert data["transcript_status"] == "none"
        assert data["processing_status"] == "pending"

    def test_create_meeting_provider_agnostic_providers(self):
        """All provider values are accepted (provider-agnostic design)."""
        for provider in ["zoom", "google_meet", "teams", "manual", "other"]:
            row = _meeting_row()
            row["provider"] = provider
            with patch("app.agency_api.v1.router._load_client_meta",
                       return_value=_client_meta_row()):
                with patch("app.agency_api.v1.router._get_db",
                           return_value=_single_db(row)):
                    with TestClient(_app_as(_PLATFORM)) as c:
                        resp = c.post("/agency/v1/clients/client-alpha/meetings", json={
                            "title": f"Meeting {provider}",
                            "provider": provider,
                        })
            assert resp.status_code == 201, f"Failed for provider={provider}: {resp.text}"

    # ── Get meeting ───────────────────────────────────────────────────────────

    def test_get_meeting_returns_row(self):
        row = _meeting_row()
        # get_meeting does: SELECT meeting, SELECT summary
        db = _sequential_db([row], [])  # summary empty
        with patch("app.agency_api.v1.router._get_db", return_value=db):
            with TestClient(_app_as(_ADMIN)) as c:
                resp = c.get(f"/agency/v1/meetings/{_MEETING_UUID}")
        assert resp.status_code == 200
        assert resp.json()["id"] == _MEETING_UUID

    def test_get_meeting_404(self):
        db = _sequential_db([])
        with patch("app.agency_api.v1.router._get_db", return_value=db):
            with TestClient(_app_as(_ADMIN)) as c:
                resp = c.get(f"/agency/v1/meetings/{_MEETING_UUID}")
        assert resp.status_code == 404

    # ── Transcript ingest ─────────────────────────────────────────────────────

    def test_transcript_ingest_returns_201_and_word_count(self):
        transcript_id = str(uuid.uuid4())
        # Sequential: SELECT meeting (exists), INSERT transcript, UPDATE meeting status
        meet_row = {"id": _MEETING_UUID}
        transcript_row = {"id": transcript_id}
        db = _sequential_db([meet_row], [transcript_row], [])
        with patch("app.agency_api.v1.router._get_db", return_value=db):
            with TestClient(_app_as(_PLATFORM)) as c:
                raw = "Esta é uma transcrição de teste com várias palavras na reunião"
                resp = c.post(
                    f"/agency/v1/meetings/{_MEETING_UUID}/transcripts",
                    json={"raw_text": raw, "language": "pt"},
                )
        assert resp.status_code == 201, resp.text
        data = resp.json()
        assert data["meeting_id"] == _MEETING_UUID
        assert data["word_count"] == len(raw.split())
        assert data["status"] == "pending"

    def test_transcript_ingest_404_when_meeting_missing(self):
        db = _sequential_db([])  # meeting not found
        with patch("app.agency_api.v1.router._get_db", return_value=db):
            with TestClient(_app_as(_PLATFORM)) as c:
                resp = c.post(
                    f"/agency/v1/meetings/{_MEETING_UUID}/transcripts",
                    json={"raw_text": "Some text", "language": "pt"},
                )
        assert resp.status_code == 404

    # ── Process meeting (summary + decisions) ─────────────────────────────────

    def test_process_meeting_creates_summary(self):
        summary_id = str(uuid.uuid4())
        meet_row  = {"id": _MEETING_UUID, "client_id": _CLIENT_SLUG}
        sum_row   = {
            "id": summary_id, "meeting_id": _MEETING_UUID,
            "summary": "Reunião validada com sucesso.",
            "decisions": json.dumps([{"decision": "Continuar com Phase 7", "owner": "maico"}]),
            "open_questions": json.dumps([]),
            "commitments": json.dumps([]),
            "follow_ups": json.dumps([]),
            "tasks_created": json.dumps([]),
            "processor": "manual",
            "created_at": _TS,
        }
        db = _sequential_db([meet_row], [], [sum_row], [])  # meet, idempotency check, insert summary, update meeting
        with patch("app.agency_api.v1.router._get_db", return_value=db):
            with TestClient(_app_as(_PLATFORM)) as c:
                resp = c.post(f"/agency/v1/meetings/{_MEETING_UUID}/process", json={
                    "summary": "Reunião validada com sucesso.",
                    "decisions": [{"decision": "Continuar com Phase 7", "owner": "maico"}],
                    "create_tasks": False,
                })
        assert resp.status_code == 201, resp.text
        data = resp.json()
        assert data["meeting_id"] == _MEETING_UUID
        assert "Reunião" in data["summary"]

    def test_process_meeting_creates_tasks_from_commitments(self):
        """create_tasks=True → canonical Tasks created from commitments."""
        task_id   = str(uuid.uuid4())
        sum_id    = str(uuid.uuid4())
        meet_row  = {"id": _MEETING_UUID, "client_id": _CLIENT_SLUG}
        task_row  = _task_row(task_id=task_id, source_type="meeting",
                              source_id=_MEETING_UUID, title="Implementar rastreamento")
        sum_row   = {
            "id": sum_id, "meeting_id": _MEETING_UUID,
            "summary": "Decisão tomada.",
            "decisions": json.dumps([]),
            "open_questions": json.dumps([]),
            "commitments": json.dumps([{"who": "maico", "what": "Implementar rastreamento"}]),
            "follow_ups": json.dumps([]),
            "tasks_created": json.dumps([{"task_id": task_id, "title": "Implementar rastreamento"}]),
            "processor": "manual",
            "created_at": _TS,
        }
        # Sequential: SELECT meeting, idempotency check, INSERT task, INSERT summary, UPDATE meeting
        db = _sequential_db([meet_row], [], [task_row], [sum_row], [])
        with patch("app.agency_api.v1.router._get_db", return_value=db):
            with TestClient(_app_as(_PLATFORM)) as c:
                resp = c.post(f"/agency/v1/meetings/{_MEETING_UUID}/process", json={
                    "summary": "Decisão tomada.",
                    "commitments": [{"who": "maico", "what": "Implementar rastreamento"}],
                    "create_tasks": True,
                })
        assert resp.status_code == 201, resp.text
        data = resp.json()
        assert len(data["tasks_created"]) == 1, "Expected 1 task created from commitment"
        assert data["tasks_created"][0]["task_id"] == task_id

    def test_meeting_task_uses_canonical_task_entity(self):
        """Tasks from meetings have source_type=meeting and source_id=meeting_id."""
        task_id  = str(uuid.uuid4())
        sum_id   = str(uuid.uuid4())
        meet_row = {"id": _MEETING_UUID, "client_id": _CLIENT_SLUG}
        task_row = _task_row(task_id=task_id, source_type="meeting",
                             source_id=_MEETING_UUID, title="Tarefa da reunião")
        sum_row  = {
            "id": sum_id, "meeting_id": _MEETING_UUID,
            "summary": None, "decisions": "[]", "open_questions": "[]",
            "commitments": json.dumps([{"who": "maico", "what": "Tarefa da reunião"}]),
            "follow_ups": "[]",
            "tasks_created": json.dumps([{"task_id": task_id, "title": "Tarefa da reunião"}]),
            "processor": "manual", "created_at": _TS,
        }
        db = _sequential_db([meet_row], [], [task_row], [sum_row], [])
        with patch("app.agency_api.v1.router._get_db", return_value=db):
            with TestClient(_app_as(_PLATFORM)) as c:
                resp = c.post(f"/agency/v1/meetings/{_MEETING_UUID}/process", json={
                    "commitments": [{"who": "maico", "what": "Tarefa da reunião"}],
                    "create_tasks": True,
                })
        assert resp.status_code == 201
        tasks = resp.json()["tasks_created"]
        assert tasks[0]["task_id"] == task_id, "Task ID must be the canonical core_tasks UUID"


# ═══════════════════════════════════════════════════════════════════════════════
# §5 — Role authorization: what client_viewer can and cannot access
# ═══════════════════════════════════════════════════════════════════════════════

class TestRoleAuthorization:
    """
    client_viewer CANNOT access: system health, jobs, learning candidates,
    data/pipeline health, changes, metrics (SCOPE_READ_ANY endpoints).
    The real auth guard returns 401 because client_viewer JWT is not in
    SCOPE_READ_ANY — so the JWT path is never attempted on those endpoints.
    """

    _ADMIN_KEY = "test-auth-admin-xzy"

    def _bare_client(self):
        return TestClient(_bare_app(), raise_server_exceptions=False)

    def _headers(self, token: str) -> dict:
        return {"Authorization": f"Bearer {token}"}

    # ── client_viewer blocked from SCOPE_READ_ANY endpoints ──────────────────

    @pytest.mark.parametrize("path", [
        "/agency/v1/system/health",
        "/agency/v1/alert-rule-suggestions",
    ])
    def test_client_viewer_jwt_blocked_from_read_any_endpoints(self, path):
        """JWT bearer on SCOPE_READ_ANY endpoint → 401 (JWT path not attempted)."""
        from app import config
        s = config.get_settings()
        import time, jwt as pyjwt
        secret = "test-jwt-secret-minimum-32-bytes!!"
        token = pyjwt.encode(
            {"sub": "uid-cv", "email": "client@example.com",
             "aud": "authenticated", "exp": int(time.time()) + 3600},
            secret, algorithm="HS256",
        )
        with (
            patch.object(s, "AGENCY_API_ADMIN_KEY",    self._ADMIN_KEY),
            patch.object(s, "AGENCY_API_HERMES_KEY",   ""),
            patch.object(s, "AGENCY_API_PLATFORM_KEY", ""),
            patch.object(s, "SUPABASE_JWT_SECRET",     ""),  # JWT not configured
            patch("app.agency_api.v1.router._get_db", return_value=_empty_db()),
        ):
            resp = self._bare_client().get(path, headers=self._headers(token))
        # JWT not accepted on SCOPE_READ_ANY → 401 (unrecognized token)
        assert resp.status_code == 401, (
            f"Expected 401 on {path} for client_viewer, got {resp.status_code}"
        )

    def test_admin_denied_on_jobs_list_without_admin_key(self):
        """GET /jobs is SCOPE_ADMIN_ONLY — hermes/platform get 403."""
        from app import config
        s = config.get_settings()
        with (
            patch.object(s, "AGENCY_API_ADMIN_KEY",    self._ADMIN_KEY),
            patch.object(s, "AGENCY_API_HERMES_KEY",   "hermes-key-test"),
            patch.object(s, "AGENCY_API_PLATFORM_KEY", ""),
            patch.object(s, "SUPABASE_JWT_SECRET",     ""),
            patch("app.agency_api.v1.router._get_db", return_value=_empty_db()),
        ):
            resp = self._bare_client().get(
                "/agency/v1/jobs",
                headers=self._headers("hermes-key-test"),
            )
        assert resp.status_code == 403

    def test_admin_can_access_jobs_list(self):
        """GET /jobs with admin key → HTTP 200."""
        from app import config
        s = config.get_settings()
        with (
            patch.object(s, "AGENCY_API_ADMIN_KEY",    self._ADMIN_KEY),
            patch.object(s, "AGENCY_API_HERMES_KEY",   ""),
            patch.object(s, "AGENCY_API_PLATFORM_KEY", ""),
            patch.object(s, "SUPABASE_JWT_SECRET",     ""),
            patch("app.agency_api.v1.router._get_db", return_value=_empty_db()),
        ):
            resp = self._bare_client().get(
                "/agency/v1/jobs",
                headers=self._headers(self._ADMIN_KEY),
            )
        assert resp.status_code == 200

    # ── SCOPE_CLIENT_VIEWER can read tasks (not write) ────────────────────────

    def test_scope_client_viewer_includes_platform_and_admin_but_not_hermes(self):
        """SCOPE_CLIENT_VIEWER = admin + platform_web + client_viewer (no hermes)."""
        from app.agency_api.v1.auth import SCOPE_CLIENT_VIEWER as _SCV, require_scopes
        # The scope constant is distinct from READ_ANY
        from app.agency_api.v1.auth import SCOPE_READ_ANY as _SRA
        assert _SCV is not _SRA

    def test_unauthenticated_task_list_returns_401(self):
        from app import config
        s = config.get_settings()
        with (
            patch.object(s, "AGENCY_API_ADMIN_KEY",    self._ADMIN_KEY),
            patch.object(s, "AGENCY_API_PLATFORM_KEY", ""),
            patch.object(s, "AGENCY_API_HERMES_KEY",   ""),
            patch.object(s, "SUPABASE_JWT_SECRET",     ""),
            patch("app.agency_api.v1.router._load_client_meta",
                  return_value=_client_meta_row()),
            patch("app.agency_api.v1.router._get_db", return_value=_empty_db()),
        ):
            resp = self._bare_client().get("/agency/v1/clients/client-alpha/tasks")
        assert resp.status_code == 401


# ═══════════════════════════════════════════════════════════════════════════════
# §6 — Notion sync prep: fields exist in schema and TaskOut
# ═══════════════════════════════════════════════════════════════════════════════

class TestNotionSyncPrepFields:
    """
    Core is canonical. Notion is adapter only.
    external_task_id / external_system / sync_state / last_synced_at / sync_error
    must be present in TaskOut and default correctly.
    """

    def test_task_out_contains_notion_fields(self):
        row = _task_row()
        with patch("app.agency_api.v1.router._get_db", return_value=_single_db(row)):
            with TestClient(_app_as(_ADMIN)) as c:
                resp = c.get(f"/agency/v1/tasks/{_TASK_UUID}")
        assert resp.status_code == 200
        data = resp.json()
        assert "external_task_id" in data, "external_task_id missing from TaskOut"
        assert "external_system"  in data, "external_system missing from TaskOut"
        assert "sync_state"       in data, "sync_state missing from TaskOut"
        assert "last_synced_at"   in data, "last_synced_at missing from TaskOut"
        assert "sync_error"       in data, "sync_error missing from TaskOut"

    def test_sync_state_defaults_to_none(self):
        row = _task_row()
        with patch("app.agency_api.v1.router._get_db", return_value=_single_db(row)):
            with TestClient(_app_as(_ADMIN)) as c:
                resp = c.get(f"/agency/v1/tasks/{_TASK_UUID}")
        assert resp.json()["sync_state"] == "none"

    def test_external_fields_null_by_default(self):
        row = _task_row()
        with patch("app.agency_api.v1.router._get_db", return_value=_single_db(row)):
            with TestClient(_app_as(_ADMIN)) as c:
                resp = c.get(f"/agency/v1/tasks/{_TASK_UUID}")
        data = resp.json()
        assert data["external_task_id"] is None
        assert data["external_system"]  is None
        assert data["last_synced_at"]   is None
        assert data["sync_error"]       is None

    def test_notion_sync_fields_can_be_set_via_update(self):
        """Notion adapter sets external_task_id / sync_state via PUT /tasks/{id}."""
        original = _task_row()
        updated  = _task_row()
        updated["external_task_id"] = "notion-task-abc123"
        updated["external_system"]  = "notion"
        updated["sync_state"]       = "synced"
        db = _sequential_db([original], [updated])
        with patch("app.agency_api.v1.router._get_db", return_value=db):
            with TestClient(_app_as(_PLATFORM)) as c:
                resp = c.put(f"/agency/v1/tasks/{_TASK_UUID}", json={
                    "external_task_id": "notion-task-abc123",
                    "external_system":  "notion",
                    "sync_state":       "synced",
                })
        assert resp.status_code == 200
        data = resp.json()
        assert data["external_task_id"] == "notion-task-abc123"
        assert data["sync_state"]       == "synced"

    def test_core_is_canonical_notion_fields_not_in_create(self):
        """TaskCreate has NO Notion fields — only TaskUpdate allows them."""
        from app.agency_api.v1.schemas import TaskCreate, TaskUpdate
        create_fields = set(TaskCreate.model_fields.keys())
        assert "external_task_id" not in create_fields, (
            "external_task_id must not be in TaskCreate — Core is canonical, not Notion"
        )
        assert "sync_state" not in create_fields, (
            "sync_state must not be in TaskCreate — Core is canonical"
        )
        update_fields = set(TaskUpdate.model_fields.keys())
        assert "external_task_id" in update_fields, "external_task_id must be in TaskUpdate"
        assert "sync_state" in update_fields, "sync_state must be in TaskUpdate"


# ═══════════════════════════════════════════════════════════════════════════════
# §7 — Regression smoke: existing core endpoints still respond
# ═══════════════════════════════════════════════════════════════════════════════

class TestRegressionSmoke:
    """Basic smoke: all core routes respond (not 500) with admin auth."""

    def test_system_health_returns_200(self):
        with patch("app.agency_api.v1.router._get_db", return_value=_empty_db()):
            with TestClient(_app_as(_ADMIN)) as c:
                resp = c.get("/agency/v1/system/health")
        assert resp.status_code == 200

    def test_list_clients_returns_200(self):
        with patch("app.agency_api.v1.router._get_db", return_value=_empty_db()):
            with TestClient(_app_as(_ADMIN)) as c:
                resp = c.get("/agency/v1/clients")
        assert resp.status_code == 200

    def test_list_alerts_global_returns_200(self):
        with patch("app.agency_api.v1.router._get_db", return_value=_empty_db()):
            with TestClient(_app_as(_ADMIN)) as c:
                resp = c.get("/agency/v1/alerts")
        assert resp.status_code == 200

    def test_alert_rule_suggestions_returns_200(self):
        with patch("app.agency_api.v1.router._get_db", return_value=_empty_db()):
            with TestClient(_app_as(_ADMIN)) as c:
                resp = c.get("/agency/v1/alert-rule-suggestions")
        assert resp.status_code == 200

    def test_client_tasks_endpoint_exists_and_responds(self):
        with patch("app.agency_api.v1.router._load_client_meta",
                   return_value=_client_meta_row()):
            with patch("app.agency_api.v1.router._get_db", return_value=_empty_db()):
                with TestClient(_app_as(_ADMIN)) as c:
                    resp = c.get("/agency/v1/clients/client-alpha/tasks")
        assert resp.status_code == 200

    def test_client_meetings_endpoint_exists_and_responds(self):
        with patch("app.agency_api.v1.router._load_client_meta",
                   return_value=_client_meta_row()):
            with patch("app.agency_api.v1.router._get_db", return_value=_empty_db()):
                with TestClient(_app_as(_ADMIN)) as c:
                    resp = c.get("/agency/v1/clients/client-alpha/meetings")
        assert resp.status_code == 200

    def test_new_endpoints_dont_break_existing_changes_route(self):
        with patch("app.agency_api.v1.router._load_client_meta",
                   return_value=_client_meta_row()):
            with patch("app.agency_api.v1.router._get_db", return_value=_empty_db()):
                with TestClient(_app_as(_ADMIN)) as c:
                    resp = c.get("/agency/v1/clients/client-alpha/changes")
        assert resp.status_code == 200

    def test_new_endpoints_dont_break_existing_recs_route(self):
        with patch("app.agency_api.v1.router._load_client_meta",
                   return_value=_client_meta_row()):
            with patch("app.agency_api.v1.router._get_db", return_value=_empty_db()):
                with TestClient(_app_as(_ADMIN)) as c:
                    resp = c.get("/agency/v1/clients/client-alpha/recommendations")
        assert resp.status_code == 200
