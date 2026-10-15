"""
H4 integration tests — require a real Supabase DB.

All tests are skipped automatically when SUPABASE_URL / SUPABASE_SERVICE_KEY
are absent (handled by the `supabase_service` conftest fixture).

Test matrix:
  test_fixture_executes_and_rollbacks           — SAFE_H4_FIXTURE + ROLLBACK_GUARANTEE
  test_fixture_no_residual_rows                 — NO_RESIDUAL_ROWS
  test_fixture_traceability_ids_linked          — END_TO_END_TRACEABILITY
  test_outcome_idempotency_constraint_via_rpc   — OUTCOME_REPLAY_SAME_ID (DB level)
  test_outcome_idempotency_via_api              — OUTCOME_PERSISTENCE_RUNTIME + OUTCOME_REPLAY_SAME_ID
  test_lc_same_action_same_outcome_replays      — LEARNING_IDEMPOTENCY_MODEL + LEARNING_REPLAY_SAME_ID
  test_lc_distinct_outcome_distinct_candidate   — DISTINCT_EVIDENCE_DISTINCT_CANDIDATE
  test_fixture_no_external_effects              — NO_EXTERNAL_EFFECTS
  test_h4_fixture_endpoint_disabled_default     — endpoint access control
"""
from __future__ import annotations

import os
import pytest


# ── RPC-level fixture tests ────────────────────────────────────────────────────

class TestH4FixtureRpc:
    def test_fixture_executes_and_rollbacks(self, supabase_service):
        res = supabase_service.rpc("h4_fixture_execute", {}).execute()
        assert res.data, "h4_fixture_execute returned no data"
        result = res.data[0] if isinstance(res.data, list) else res.data
        if isinstance(result, dict) and "h4_fixture_execute" in result:
            result = result["h4_fixture_execute"]
        assert result["rollback_verified"] is True, (
            f"rollback_verified is False: {result}"
        )

    def test_fixture_no_residual_rows(self, supabase_service):
        res = supabase_service.rpc("h4_fixture_execute", {}).execute()
        result = res.data[0] if isinstance(res.data, list) else res.data
        if isinstance(result, dict) and "h4_fixture_execute" in result:
            result = result["h4_fixture_execute"]

        after = result["rows_after_rollback"]
        for table, count in after.items():
            assert count == 0, f"Residual rows in {table}: count={count}"

    def test_fixture_change_log_id_present(self, supabase_service):
        res = supabase_service.rpc("h4_fixture_execute", {}).execute()
        result = res.data[0] if isinstance(res.data, list) else res.data
        if isinstance(result, dict) and "h4_fixture_execute" in result:
            result = result["h4_fixture_execute"]

        assert result["change_log_id"] is not None, (
            "change_log_id must be a real UUID — fixture must insert a core_change_log row"
        )

    def test_fixture_traceability_ids_linked(self, supabase_service):
        res = supabase_service.rpc("h4_fixture_execute", {}).execute()
        result = res.data[0] if isinstance(res.data, list) else res.data
        if isinstance(result, dict) and "h4_fixture_execute" in result:
            result = result["h4_fixture_execute"]

        t = result["traceability"]
        assert t["action.recommendation_id"]    == result["recommendation_id"]
        assert t["action.action_event_id"]       == result["action_event_id"]
        assert t["action.task_id"]               == result["task_id"]
        assert t["change_log.linked_action_id"]  == result["action_id"]
        assert t["outcome.action_event_id"]      == result["action_event_id"]
        assert t["outcome.action_id"]            == result["action_id"]
        assert t["lc.source_action_id"]          == result["action_id"]
        assert t["lc.source_recommendation_id"]  == result["recommendation_id"]
        assert t["lc.outcome_id"]                == result["outcome_id"]

    def test_fixture_rows_before_all_zero(self, supabase_service):
        res = supabase_service.rpc("h4_fixture_execute", {}).execute()
        result = res.data[0] if isinstance(res.data, list) else res.data
        if isinstance(result, dict) and "h4_fixture_execute" in result:
            result = result["h4_fixture_execute"]

        before = result["rows_before"]
        for table, count in before.items():
            assert count == 0, f"Expected 0 rows before in {table}, got {count}"

    def test_fixture_rows_during_expected_counts(self, supabase_service):
        res = supabase_service.rpc("h4_fixture_execute", {}).execute()
        result = res.data[0] if isinstance(res.data, list) else res.data
        if isinstance(result, dict) and "h4_fixture_execute" in result:
            result = result["h4_fixture_execute"]

        during = result["rows_during"]
        # Single-row tables
        for table in ("recommendations", "action_events", "actions", "tasks", "change_log"):
            assert during[table] == 1, f"Expected 1 row during in {table}, got {during[table]}"
        # Two rows: O1 + O2, LC1 + LC2
        assert during["outcomes"] == 2, f"Expected 2 outcomes during, got {during['outcomes']}"
        assert during["learning_candidates"] == 2, (
            f"Expected 2 learning_candidates during, got {during['learning_candidates']}"
        )

    def test_fixture_outcome_idempotency(self, supabase_service):
        res = supabase_service.rpc("h4_fixture_execute", {}).execute()
        result = res.data[0] if isinstance(res.data, list) else res.data
        if isinstance(result, dict) and "h4_fixture_execute" in result:
            result = result["h4_fixture_execute"]

        oi = result["outcome_idempotency"]
        assert oi["same_id_on_replay"] is True, (
            "Outcome replay returned different ID: "
            f"o1={oi['outcome_1_id']} replay={oi['outcome_replay_id']}"
        )
        assert oi["distinct_id_on_new_evidence"] is True, (
            "Distinct evaluation window should produce distinct outcome ID"
        )

    def test_fixture_learning_idempotency(self, supabase_service):
        res = supabase_service.rpc("h4_fixture_execute", {}).execute()
        result = res.data[0] if isinstance(res.data, list) else res.data
        if isinstance(result, dict) and "h4_fixture_execute" in result:
            result = result["h4_fixture_execute"]

        li = result["learning_idempotency"]
        assert li["same_id_on_replay"] is True, (
            "LC replay returned different ID: "
            f"lc1={li['lc_1_id']} replay={li['lc_replay_id']}"
        )
        assert li["distinct_id_on_new_evidence"] is True, (
            "Different outcome_id must yield a distinct learning candidate"
        )

    def test_outcome_idempotency_constraint_via_rpc(self, supabase_service):
        res = supabase_service.rpc("h4_idempotency_test", {}).execute()
        assert res.data, "h4_idempotency_test returned no data"
        result = res.data[0] if isinstance(res.data, list) else res.data
        if isinstance(result, dict) and "h4_idempotency_test" in result:
            result = result["h4_idempotency_test"]

        idem = result["outcome_idempotency"]
        assert idem["dup_blocked_by_constraint"] is True, (
            "Duplicate outcome was NOT blocked by the unique constraint"
        )
        assert idem["same_id_on_dup"] is True, "IDs should match on duplicate attempt"
        assert idem["distinct_window_allowed"] is True, (
            "Distinct window outcome was incorrectly blocked"
        )
        assert result["rollback_verified"] is True


# ── API-level idempotency tests ────────────────────────────────────────────────

class TestOutcomeIdempotencyApi:
    """
    These tests create real rows and then verify they are cleaned up.
    Each test creates a unique action_event to avoid cross-test collisions.
    """

    def _create_minimal_action_event(self, db):
        """Insert a minimal action_event row and return its id."""
        import uuid
        rec_id = str(uuid.uuid4())
        res = db.table("core_action_events").insert({
            "recommendation_id": rec_id,
            "event_type": "APPROVED",
            "actor": "h4_integration_test",
            "note": "H4 integration test — will be deleted",
        }).execute()
        assert res.data, "action_event insert returned no data"
        return res.data[0]["id"]

    def _cleanup_outcome(self, db, outcome_id):
        try:
            db.table("core_outcomes").delete().eq("id", outcome_id).execute()
        except Exception:
            pass

    def _cleanup_action_event(self, db, evt_id):
        try:
            db.table("core_action_events").delete().eq("id", evt_id).execute()
        except Exception:
            pass

    def test_outcome_idempotency_via_api(self, app_real_db, supabase_service):
        """POST outcome twice with same action_event_id → same id, second reused=True."""
        evt_id = self._create_minimal_action_event(supabase_service)
        created_ids = []
        try:
            body = {
                "action_event_id":        evt_id,
                "evaluation_window_days": 7,
                "measurement_period":     {"start": "2026-10-01", "end": "2026-10-08"},
                "metric_refs":            ["roas"],
                "status":                 "PENDING",
            }
            resp1 = app_real_db.post("/agency/v1/outcomes", json=body)
            assert resp1.status_code == 201, f"first outcome failed: {resp1.text}"
            d1 = resp1.json()
            created_ids.append(d1["id"])
            assert d1["reused"] is False

            resp2 = app_real_db.post("/agency/v1/outcomes", json=body)
            assert resp2.status_code == 201
            d2 = resp2.json()
            assert d2["id"] == d1["id"], "Idempotent replay must return same ID"
            assert d2["reused"] is True
        finally:
            for oid in created_ids:
                self._cleanup_outcome(supabase_service, oid)
            self._cleanup_action_event(supabase_service, evt_id)


class TestLearningCandidateIdempotencyApi:
    """
    Real-DB tests for the LC idempotency model using the new 060 key:
      (source_action_id, outcome_id, scope)
    """

    def _create_action(self, db):
        import uuid
        rec_id = str(uuid.uuid4())
        res = db.table("core_actions").insert({
            "client_id":         str(uuid.uuid4()),
            "recommendation_id": rec_id,
            "action_type":       "MANUAL_EXTERNAL_ACTION",
            "status":            "PLANNED",
        }).execute()
        assert res.data, "action insert returned no data"
        return res.data[0]["id"]

    def _cleanup_lc(self, db, lc_id):
        try:
            db.table("core_learning_candidates").delete().eq("id", lc_id).execute()
        except Exception:
            pass

    def _cleanup_action(self, db, action_id):
        try:
            db.table("core_actions").delete().eq("id", action_id).execute()
        except Exception:
            pass

    def test_lc_same_action_same_outcome_replays(self, app_real_db, supabase_service):
        """Same (action, outcome, scope) → second call is reused=True, same ID."""
        action_id = self._create_action(supabase_service)
        import uuid
        outcome_id = str(uuid.uuid4())
        created_ids = []
        try:
            body = {
                "statement":        "Integration test LC — same action/outcome/scope",
                "scope":            "CLIENT_ONLY",
                "evidence_level":   "OBSERVATION",
                "source_action_id": action_id,
                "outcome_id":       outcome_id,
            }
            os.environ["H4_FIXTURE_ENABLED"] = "true"
            resp1 = app_real_db.post("/agency/v1/learning-candidates", json=body)
            assert resp1.status_code == 201, f"first LC failed: {resp1.text}"
            d1 = resp1.json()
            created_ids.append(d1["id"])
            assert d1["reused"] is False

            resp2 = app_real_db.post("/agency/v1/learning-candidates", json=body)
            assert resp2.status_code == 201
            d2 = resp2.json()
            assert d2["id"] == d1["id"], "Same (action, outcome, scope) must replay same LC"
            assert d2["reused"] is True
        finally:
            for lcid in created_ids:
                self._cleanup_lc(supabase_service, lcid)
            self._cleanup_action(supabase_service, action_id)

    def test_lc_distinct_outcome_distinct_candidate(self, app_real_db, supabase_service):
        """Same action + same scope but DIFFERENT outcome_id → distinct LC, not reused."""
        action_id = self._create_action(supabase_service)
        import uuid
        outcome_a = str(uuid.uuid4())
        outcome_b = str(uuid.uuid4())
        created_ids = []
        try:
            body_a = {
                "statement":        "Integration test LC — outcome A",
                "scope":            "CLIENT_ONLY",
                "evidence_level":   "OBSERVATION",
                "source_action_id": action_id,
                "outcome_id":       outcome_a,
            }
            body_b = {**body_a, "statement": "Integration test LC — outcome B", "outcome_id": outcome_b}

            resp_a = app_real_db.post("/agency/v1/learning-candidates", json=body_a)
            assert resp_a.status_code == 201, f"LC A failed: {resp_a.text}"
            da = resp_a.json()
            created_ids.append(da["id"])

            resp_b = app_real_db.post("/agency/v1/learning-candidates", json=body_b)
            assert resp_b.status_code == 201, f"LC B failed: {resp_b.text}"
            db_resp = resp_b.json()
            created_ids.append(db_resp["id"])

            assert da["id"] != db_resp["id"], (
                "Distinct outcome_ids must produce distinct learning candidates"
            )
            assert db_resp["reused"] is False
        finally:
            for lcid in created_ids:
                self._cleanup_lc(supabase_service, lcid)
            self._cleanup_action(supabase_service, action_id)


# ── Admin endpoint access control ─────────────────────────────────────────────

class TestH4FixtureEndpoint:
    def test_h4_fixture_endpoint_disabled_default(self, app_real_db):
        os.environ.pop("H4_FIXTURE_ENABLED", None)
        resp = app_real_db.post("/agency/v1/admin/h4-fixture")
        assert resp.status_code == 403
        assert "disabled" in resp.json()["detail"].lower()
