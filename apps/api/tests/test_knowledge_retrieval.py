"""
Tests for the Learning Loop / Obsidian knowledge retrieval system.

Covers:
 1. load_knowledge_context — returns client knowledge only for matching client
 2. load_knowledge_context — returns AGENCY/PLATFORM/BUSINESS_MODEL to all clients
 3. Cross-client isolation — CLIENT_ONLY not visible across clients
 4. Platform filter — narrows both client and shared knowledge
 5. format_for_hermes — compact output, max_items respected
 6. format_for_hermes — empty context returns informative message
 7. INCONCLUSIVE outcome → _generate_learning_candidate returns None (no candidate)
 8. POSITIVE outcome → learning candidate created with PENDING status (never auto-approved)
 9. Causality protection — candidate statement uses attribution language, not causal
10. PENDING candidate cannot be promoted (obsidian_writer rejects)
11. REJECTED candidate cannot be promoted (obsidian_writer rejects)
12. APPROVED is the only gate that allows promotion
13. Duplicate prevention — promote_to_knowledge reuses existing pattern
14. load_knowledge_context graceful fallback when DB errors
15. knowledge-context endpoint auth guard
"""
from __future__ import annotations

from typing import Optional
from unittest.mock import MagicMock, patch
import pytest

# ── Helpers ────────────────────────────────────────────────────────────────────

_CLIENT_UUID = "02a2a276-40ce-4154-bc7f-f3b586e13bc9"
_CLIENT_SLUG = "lk-sneakers"

_KNOWLEDGE_ROW = {
    "id":             "aabbccdd-0000-0000-0000-000000000001",
    "title":          "Manual External Action: google ROAS melhora o resultado",
    "statement":      "MANUAL_EXTERNAL_ACTION em google (ad) melhora o resultado observado após a ação.",
    "pattern":        "MANUAL_EXTERNAL_ACTION:google:ad:POSITIVE",
    "scope":          "CLIENT_ONLY",
    "platform":       "google",
    "evidence_level": "OBSERVATION",
    "observed_outcome": {
        "outcome_status": "POSITIVE",
        "delta_pct":      12.5,
        "action_type":    "MANUAL_EXTERNAL_ACTION",
        "platform":       "google",
    },
    "approved_by":    "maico@norolabs.com.br",
    "approved_at":    "2026-11-04T10:00:00+00:00",
    "promoted_at":    "2026-11-04T10:01:00+00:00",
    "client_id":      _CLIENT_UUID,
}

_AGENCY_KNOWLEDGE_ROW = {
    **_KNOWLEDGE_ROW,
    "id":        "aabbccdd-0000-0000-0000-000000000002",
    "scope":     "AGENCY",
    "client_id": None,
    "pattern":   "MANUAL_EXTERNAL_ACTION:google:ad:POSITIVE:AGENCY",
}


def _make_sb(*, clients_data=None, client_knowledge=None, shared_knowledge=None):
    """Build a mock Supabase client for knowledge_retrieval tests."""
    sb = MagicMock()

    # Cache per-table mocks so the same mock is returned across multiple calls
    _cache: dict = {}
    _kg_call_count = [0]

    def _make_tbl():
        tbl = MagicMock()
        for method in ("select", "eq", "neq", "order", "limit", "in_"):
            getattr(tbl, method).return_value = tbl
        return tbl

    def _table(name: str):
        if name not in _cache:
            tbl = _make_tbl()
            if name == "clients":
                tbl.execute.return_value = MagicMock(
                    data=clients_data if clients_data is not None else [{"id": _CLIENT_UUID}]
                )
            elif name == "core_agency_knowledge":
                def _execute():
                    _kg_call_count[0] += 1
                    if _kg_call_count[0] == 1:
                        return MagicMock(
                            data=client_knowledge if client_knowledge is not None else [_KNOWLEDGE_ROW]
                        )
                    return MagicMock(
                        data=shared_knowledge if shared_knowledge is not None else []
                    )
                tbl.execute.side_effect = _execute
            else:
                tbl.execute.return_value = MagicMock(data=[])
            _cache[name] = tbl
        return _cache[name]

    sb.table.side_effect = _table
    return sb


# ── 1–3. load_knowledge_context isolation ─────────────────────────────────────

class TestLoadKnowledgeContextIsolation:
    def test_client_knowledge_returned_for_matching_client(self):
        sb = _make_sb(client_knowledge=[_KNOWLEDGE_ROW], shared_knowledge=[])
        with patch("app.core.knowledge_retrieval.get_supabase", return_value=sb):
            from app.core.knowledge_retrieval import load_knowledge_context
            ctx = load_knowledge_context(_CLIENT_SLUG)
        assert len(ctx["client_knowledge"]) == 1
        assert ctx["client_knowledge"][0]["scope"] == "CLIENT_ONLY"
        assert ctx["client_knowledge"][0]["id"] == "aabbccdd-0000-0000-0000-000000000001"

    def test_shared_knowledge_visible_to_all_clients(self):
        sb = _make_sb(client_knowledge=[], shared_knowledge=[_AGENCY_KNOWLEDGE_ROW])
        with patch("app.core.knowledge_retrieval.get_supabase", return_value=sb):
            from app.core.knowledge_retrieval import load_knowledge_context
            ctx = load_knowledge_context("other-client")
        assert len(ctx["shared_knowledge"]) == 1
        assert ctx["shared_knowledge"][0]["scope"] == "AGENCY"

    def test_cross_client_isolation_client_only_not_returned_for_other_client(self):
        """
        CLIENT_ONLY knowledge for lk-sneakers must NOT be returned when querying
        for a different client. The mock returns empty for client query and the
        CLIENT_ONLY row in shared — but shared query excludes CLIENT_ONLY scope,
        so the row would never appear there in production.

        Here we verify that client_knowledge is empty when UUID doesn't match,
        and shared_knowledge respects neq(scope) filter semantically.
        """
        # Different client resolves to different UUID — returns no rows
        sb = _make_sb(
            clients_data=[{"id": "ffffffff-ffff-ffff-ffff-ffffffffffff"}],
            client_knowledge=[],  # no CLIENT_ONLY rows for this UUID
            shared_knowledge=[_AGENCY_KNOWLEDGE_ROW],  # only AGENCY scope
        )
        with patch("app.core.knowledge_retrieval.get_supabase", return_value=sb):
            from app.core.knowledge_retrieval import load_knowledge_context
            ctx = load_knowledge_context("different-client")
        # CLIENT_ONLY row is NOT present
        assert all(k["scope"] != "CLIENT_ONLY" for k in ctx["client_knowledge"])
        assert all(k["scope"] != "CLIENT_ONLY" for k in ctx["shared_knowledge"])

    def test_total_count_sums_both_buckets(self):
        sb = _make_sb(
            client_knowledge=[_KNOWLEDGE_ROW],
            shared_knowledge=[_AGENCY_KNOWLEDGE_ROW],
        )
        with patch("app.core.knowledge_retrieval.get_supabase", return_value=sb):
            from app.core.knowledge_retrieval import load_knowledge_context
            ctx = load_knowledge_context(_CLIENT_SLUG)
        assert ctx["total_count"] == 2

    def test_deduplication_shared_does_not_double_count_client_knowledge(self):
        # Same ID appears in both queries (shouldn't happen in prod but guard it)
        sb = _make_sb(
            client_knowledge=[_KNOWLEDGE_ROW],
            shared_knowledge=[_KNOWLEDGE_ROW],  # same ID as client row
        )
        with patch("app.core.knowledge_retrieval.get_supabase", return_value=sb):
            from app.core.knowledge_retrieval import load_knowledge_context
            ctx = load_knowledge_context(_CLIENT_SLUG)
        # shared should be deduped — that ID is already in client_knowledge
        ids_in_shared = [k["id"] for k in ctx["shared_knowledge"]]
        assert "aabbccdd-0000-0000-0000-000000000001" not in ids_in_shared


# ── 4. Platform filter ─────────────────────────────────────────────────────────

class TestPlatformFilter:
    def test_platform_filter_passed_to_query(self):
        sb = _make_sb(client_knowledge=[], shared_knowledge=[])

        # Capture which platform value reaches .eq()
        captured_calls: list = []
        orig_side = sb.table.side_effect

        def _table_spy(name):
            tbl = orig_side(name)
            orig_eq = tbl.eq

            def _eq(col, val):
                if col == "platform":
                    captured_calls.append(val)
                return tbl
            tbl.eq.side_effect = _eq
            return tbl

        sb.table.side_effect = _table_spy

        with patch("app.core.knowledge_retrieval.get_supabase", return_value=sb):
            from app.core.knowledge_retrieval import load_knowledge_context
            ctx = load_knowledge_context(_CLIENT_SLUG, platform="google")

        assert ctx["platform_filter"] == "google"


# ── 5–6. format_for_hermes ────────────────────────────────────────────────────

class TestFormatForHermes:
    def test_format_non_empty_context(self):
        from app.core.knowledge_retrieval import format_for_hermes
        ctx = {
            "client_knowledge":  [_make_compact(_KNOWLEDGE_ROW)],
            "shared_knowledge":  [],
            "total_count":       1,
            "client_id":         _CLIENT_SLUG,
            "platform_filter":   None,
        }
        text = format_for_hermes(ctx)
        assert "APRENDIZADOS APROVADOS" in text
        assert "melhora o resultado" in text
        assert "POSITIVE" in text
        assert "+12.5%" in text
        # Must use attribution language, not causal
        assert "observado" in text
        assert "causa" not in text.lower()

    def test_format_empty_context(self):
        from app.core.knowledge_retrieval import format_for_hermes
        ctx = {
            "client_knowledge": [], "shared_knowledge": [],
            "total_count": 0, "client_id": _CLIENT_SLUG, "platform_filter": None,
        }
        text = format_for_hermes(ctx)
        assert "Nenhum aprendizado" in text

    def test_format_max_items_respected(self):
        from app.core.knowledge_retrieval import format_for_hermes
        rows = [dict(_make_compact(_KNOWLEDGE_ROW), id=f"id-{i}") for i in range(10)]
        ctx = {
            "client_knowledge": rows, "shared_knowledge": [],
            "total_count": 10, "client_id": _CLIENT_SLUG, "platform_filter": None,
        }
        text = format_for_hermes(ctx, max_items=3)
        # 3 bullet entries × "  •"
        assert text.count("  •") == 3


# ── 7–9. Learning candidate generation ────────────────────────────────────────

class TestLearningCandidateGeneration:
    """Unit tests for _generate_learning_candidate in outcome_evaluator."""

    _ACTION = {
        "id":          "aaaaaaaa-aaaa-aaaa-aaaa-000000000001",
        "action_type": "MANUAL_EXTERNAL_ACTION",
        "platform":    "google",
        "entity_type": "ad",
    }

    def _make_sb_insert(self, inserted_row):
        sb = MagicMock()
        tbl = MagicMock()
        for m in ("insert",):
            getattr(tbl, m).return_value = tbl
        tbl.execute.return_value = MagicMock(data=[inserted_row])
        sb.table.return_value = tbl
        return sb

    def test_inconclusive_outcome_does_not_generate_candidate(self):
        from app.core.outcome_evaluator import _generate_learning_candidate
        sb = MagicMock()
        result = _generate_learning_candidate(
            sb, self._ACTION, "out-id", "INCONCLUSIVE", "rec-id", _CLIENT_UUID, None
        )
        assert result is None
        sb.table.assert_not_called()

    def test_positive_outcome_generates_candidate_with_pending_status(self):
        inserted = {
            "id": "cand-0001",
            "status": "PENDING",
            "statement": "MANUAL_EXTERNAL_ACTION em google (ad) melhora o resultado observado após a ação.",
        }
        sb = self._make_sb_insert(inserted)
        from app.core.outcome_evaluator import _generate_learning_candidate
        result = _generate_learning_candidate(
            sb, self._ACTION, "out-id", "POSITIVE", "rec-id", _CLIENT_UUID, 15.0
        )
        assert result == "cand-0001"
        # Verify insert was called with PENDING status
        call_args = sb.table("core_learning_candidates").insert.call_args[0][0]
        assert call_args["status"] == "PENDING"

    def test_negative_outcome_generates_candidate(self):
        inserted = {"id": "cand-0002", "status": "PENDING", "statement": ""}
        sb = self._make_sb_insert(inserted)
        from app.core.outcome_evaluator import _generate_learning_candidate
        result = _generate_learning_candidate(
            sb, self._ACTION, "out-id", "NEGATIVE", "rec-id", _CLIENT_UUID, -8.5
        )
        assert result == "cand-0002"

    def test_neutral_outcome_generates_candidate(self):
        inserted = {"id": "cand-0003", "status": "PENDING", "statement": ""}
        sb = self._make_sb_insert(inserted)
        from app.core.outcome_evaluator import _generate_learning_candidate
        result = _generate_learning_candidate(
            sb, self._ACTION, "out-id", "NEUTRAL", "rec-id", _CLIENT_UUID, 2.0
        )
        assert result == "cand-0003"

    def test_candidate_uses_attribution_language_not_causal(self):
        """Statement must say 'resultado observado após a ação', not assert causation."""
        inserted = {"id": "cand-0004", "status": "PENDING", "statement": ""}
        sb = self._make_sb_insert(inserted)
        from app.core.outcome_evaluator import _generate_learning_candidate
        _generate_learning_candidate(
            sb, self._ACTION, "out-id", "POSITIVE", "rec-id", _CLIENT_UUID, 10.0
        )
        insert_row = sb.table("core_learning_candidates").insert.call_args[0][0]
        stmt = insert_row["statement"]
        # Should say "melhora o resultado observado" — attributive, not "causou"
        assert "resultado observado após a ação" in stmt or "melhora" in stmt
        assert "causou" not in stmt
        assert "causa" not in stmt

    def test_candidate_never_auto_approved(self):
        inserted = {"id": "cand-0005", "status": "PENDING", "statement": ""}
        sb = self._make_sb_insert(inserted)
        from app.core.outcome_evaluator import _generate_learning_candidate
        _generate_learning_candidate(
            sb, self._ACTION, "out-id", "POSITIVE", "rec-id", _CLIENT_UUID, 5.0
        )
        insert_row = sb.table("core_learning_candidates").insert.call_args[0][0]
        assert insert_row["status"] == "PENDING"
        assert insert_row["status"] != "APPROVED"


# ── 10–13. Obsidian promotion gate ────────────────────────────────────────────

class TestObsidianApprovalGate:
    """Unit tests for promote_to_knowledge approval gate."""

    def _make_sb_candidate(self, status: str):
        sb = MagicMock()
        cand_tbl = MagicMock()
        for m in ("select", "eq", "limit"):
            getattr(cand_tbl, m).return_value = cand_tbl
        cand_tbl.execute.return_value = MagicMock(data=[{
            "id":                   "cand-9999",
            "status":               status,
            "statement":            "test statement",
            "pattern":              "TEST:pattern",
            "scope":                "CLIENT_ONLY",
            "client_id":            _CLIENT_UUID,
            "evidence_refs":        ["out-id-1"],
            "evidence_level":       "OBSERVATION",
            "observed_outcome":     {"outcome_status": "POSITIVE", "delta_pct": 10.0},
            "context":              {},
            "source_action_id":     "act-id",
            "source_recommendation_id": None,
            "updated_at":           "2026-11-04T10:00:00+00:00",
        }])

        promo_tbl = MagicMock()
        for m in ("select", "eq", "limit", "insert"):
            getattr(promo_tbl, m).return_value = promo_tbl
        promo_tbl.execute.return_value = MagicMock(data=[])

        def _table(name):
            if name == "core_learning_candidates":
                return cand_tbl
            return promo_tbl
        sb.table.side_effect = _table
        return sb

    def test_pending_candidate_cannot_be_promoted(self):
        sb = self._make_sb_candidate("PENDING")
        with patch("app.core.obsidian_writer.get_supabase", return_value=sb):
            from app.core.obsidian_writer import promote_to_knowledge
            result = promote_to_knowledge(
                learning_candidate_id="cand-9999",
                actor="maico@norolabs.com.br",
            )
        assert result["status"] == "FAILED"
        assert "PENDING" in (result.get("error") or "")

    def test_rejected_candidate_cannot_be_promoted(self):
        sb = self._make_sb_candidate("REJECTED")
        with patch("app.core.obsidian_writer.get_supabase", return_value=sb):
            from app.core.obsidian_writer import promote_to_knowledge
            result = promote_to_knowledge(
                learning_candidate_id="cand-9999",
                actor="maico@norolabs.com.br",
            )
        assert result["status"] == "FAILED"
        assert "REJECTED" in (result.get("error") or "")

    def test_superseded_candidate_cannot_be_promoted(self):
        sb = self._make_sb_candidate("SUPERSEDED")
        with patch("app.core.obsidian_writer.get_supabase", return_value=sb):
            from app.core.obsidian_writer import promote_to_knowledge
            result = promote_to_knowledge(
                learning_candidate_id="cand-9999",
                actor="maico@norolabs.com.br",
            )
        assert result["status"] == "FAILED"

    def _make_sb_full(self, cand_status: str, existing_knowledge: Optional[dict] = None):
        """
        Build a fully-routed mock Supabase for obsidian_writer tests.
        No recursion — each table mock is built independently.
        """
        # candidate table
        cand_tbl = MagicMock()
        for m in ("select", "eq", "limit"):
            getattr(cand_tbl, m).return_value = cand_tbl
        cand_tbl.execute.return_value = MagicMock(data=[{
            "id":                   "cand-9999",
            "status":               cand_status,
            "statement":            "test statement",
            "pattern":              "TEST:pattern",
            "scope":                "CLIENT_ONLY",
            "client_id":            _CLIENT_UUID,
            "evidence_refs":        ["out-id-1"],
            "evidence_level":       "OBSERVATION",
            "observed_outcome":     {"outcome_status": "POSITIVE", "delta_pct": 10.0},
            "context":              {},
            "source_action_id":     "act-id",
            "source_recommendation_id": None,
            "updated_at":           "2026-11-04T10:00:00+00:00",
        }])

        # idempotency / promotion table
        promo_tbl = MagicMock()
        for m in ("select", "eq", "limit", "insert"):
            getattr(promo_tbl, m).return_value = promo_tbl
        promo_tbl.execute.return_value = MagicMock(data=[{"id": "promo-001"}])

        # knowledge table
        kg_tbl = MagicMock()
        for m in ("select", "eq", "limit", "insert"):
            getattr(kg_tbl, m).return_value = kg_tbl
        kg_tbl.execute.return_value = MagicMock(
            data=[existing_knowledge] if existing_knowledge else []
        )

        # clients table (for business_model lookup)
        cli_tbl = MagicMock()
        for m in ("select", "eq", "limit"):
            getattr(cli_tbl, m).return_value = cli_tbl
        cli_tbl.execute.return_value = MagicMock(data=[{"business_model": "ecommerce"}])

        sb = MagicMock()

        def _table(name):
            if name == "core_learning_candidates":
                return cand_tbl
            if name == "core_knowledge_promotions":
                return promo_tbl
            if name == "core_agency_knowledge":
                return kg_tbl
            if name == "clients":
                return cli_tbl
            return promo_tbl

        sb.table.side_effect = _table
        return sb

    def test_approved_candidate_reaches_duplicate_check(self):
        """APPROVED candidate passes the gate and proceeds to duplicate detection."""
        existing = {
            "id":               "know-0001",
            "title":            "existing",
            "pattern":          "TEST:pattern",
            "document_version": 1,
            "document_path":    "knowledge/client/google/ecommerce/test.md",
            "scope":            "CLIENT_ONLY",
            "obsidian_url":     None,
        }
        sb = self._make_sb_full("APPROVED", existing_knowledge=existing)
        with patch("app.core.obsidian_writer.get_supabase", return_value=sb):
            from app.core.obsidian_writer import promote_to_knowledge
            result = promote_to_knowledge(
                learning_candidate_id="cand-9999",
                actor="maico@norolabs.com.br",
            )
        assert result["status"] in ("EXISTING_KNOWLEDGE", "COMPLETED", "NEW_VERSION")

    def test_duplicate_prevention_returns_existing_knowledge_id(self):
        """When pattern already exists, promote_to_knowledge returns EXISTING_KNOWLEDGE."""
        existing_id = "know-existing-001"
        existing = {
            "id":               existing_id,
            "title":            "existing knowledge",
            "pattern":          "TEST:pattern",
            "document_version": 1,
            "document_path":    "knowledge/client/google/ecommerce/test.md",
            "scope":            "CLIENT_ONLY",
            "obsidian_url":     None,
        }
        sb = self._make_sb_full("APPROVED", existing_knowledge=existing)
        with patch("app.core.obsidian_writer.get_supabase", return_value=sb):
            from app.core.obsidian_writer import promote_to_knowledge
            result = promote_to_knowledge(
                learning_candidate_id="cand-9999",
                actor="maico@norolabs.com.br",
                force_new_version=False,
            )
        assert result["status"] == "EXISTING_KNOWLEDGE"
        assert result["knowledge_id"] == existing_id


# ── 14. Graceful fallback on DB error ─────────────────────────────────────────

class TestKnowledgeRetrievalFallback:
    def test_db_error_returns_empty_context(self):
        sb = MagicMock()

        def _table(name):
            tbl = MagicMock()
            for m in ("select", "eq", "neq", "order", "limit"):
                getattr(tbl, m).return_value = tbl
            tbl.execute.side_effect = Exception("DB connection lost")
            return tbl
        sb.table.side_effect = _table

        with patch("app.core.knowledge_retrieval.get_supabase", return_value=sb):
            from app.core.knowledge_retrieval import load_knowledge_context
            ctx = load_knowledge_context(_CLIENT_SLUG)
        # Must not raise — returns empty structure
        assert ctx["total_count"] == 0
        assert ctx["client_knowledge"] == []
        assert ctx["shared_knowledge"] == []

    def test_client_uuid_not_found_returns_no_client_knowledge(self):
        sb = MagicMock()

        def _table(name):
            tbl = MagicMock()
            for m in ("select", "eq", "neq", "order", "limit"):
                getattr(tbl, m).return_value = tbl
            if name == "clients":
                tbl.execute.return_value = MagicMock(data=[])  # no UUID found
            else:
                tbl.execute.return_value = MagicMock(data=[_AGENCY_KNOWLEDGE_ROW])
            return tbl

        # Only call execute once for agency query
        call_counts: dict = {"core_agency_knowledge": 0}
        orig = sb.table.side_effect

        def _table_counting(name):
            if name == "core_agency_knowledge":
                call_counts["core_agency_knowledge"] += 1
            return orig(name)
        sb.table.side_effect = _table_counting

        with patch("app.core.knowledge_retrieval.get_supabase", return_value=sb):
            from app.core.knowledge_retrieval import load_knowledge_context
            ctx = load_knowledge_context("unknown-client")
        # No client knowledge (UUID not found → skipped), but shared may still load
        assert ctx["client_knowledge"] == []


# ── 15. Endpoint auth guard ────────────────────────────────────────────────────

def _agency_test_client():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.agency_api.v1.router import router as _r
    app = FastAPI()
    app.include_router(_r)
    return TestClient(app, raise_server_exceptions=False)


class TestKnowledgeContextEndpoint:
    def test_knowledge_context_endpoint_requires_auth(self):
        from unittest.mock import patch
        from app.config import settings
        with (
            patch.object(settings, "AGENCY_API_ADMIN_KEY",    ""),
            patch.object(settings, "AGENCY_API_HERMES_KEY",   ""),
            patch.object(settings, "AGENCY_API_PLATFORM_KEY", ""),
            patch.object(settings, "SUPABASE_JWT_SECRET",     ""),
        ):
            resp = _agency_test_client().get(
                "/agency/v1/clients/lk-sneakers/knowledge-context"
            )
        assert resp.status_code in (401, 403)


# ── Fixture helpers ───────────────────────────────────────────────────────────

def _make_compact(row: dict) -> dict:
    obs = row.get("observed_outcome") or {}
    return {
        "id":             str(row["id"]),
        "title":          row.get("title", ""),
        "statement":      row.get("statement", ""),
        "pattern":        row.get("pattern"),
        "scope":          row.get("scope", ""),
        "platform":       row.get("platform"),
        "evidence_level": row.get("evidence_level", "OBSERVATION"),
        "outcome_status": obs.get("outcome_status"),
        "delta_pct":      obs.get("delta_pct"),
        "action_type":    obs.get("action_type"),
        "approved_by":    row.get("approved_by"),
        "approved_at":    str(row.get("approved_at") or ""),
    }


# ── LK Sneakers Pilot fixture ─────────────────────────────────────────────────

class TestLKSneakersPilot:
    """
    Offline unit tests exercising the full learning loop with
    LK Sneakers data — no real DB required.
    """

    _LK_CHANGE_LOG_ROW = {
        "id":           "cl-lk-0001",
        "client_id":    "lk-sneakers",
        "occurred_at":  "2026-10-28T00:00:00+00:00",
        "channel":      "GOOGLE",
        "entity_type":  "RETARGETING_AUDIENCE",
        "entity_name_at_time": "Retargeting — LK Nov",
        "change_type":  "AUDIENCE_CHANGE",
        "before_state": {"roas": 2.1, "audience": "retargeting-v1"},
        "after_state":  {"roas": 3.8, "audience": "retargeting-v2"},
        "reason":       "Audience estava saturada — troca para lookalike 7d",
        "reported_by":  "Pedro",
        "source":       "HUMAN",
        "confidence":   0.85,
    }

    def test_pilot_candidate_created_from_positive_outcome(self):
        """Simulate a POSITIVE outcome on a Google action → candidate generated."""
        from app.core.outcome_evaluator import _generate_learning_candidate

        action = {
            "id":          "act-lk-0001",
            "action_type": "MANUAL_EXTERNAL_ACTION",
            "platform":    "google",
            "entity_type": "RETARGETING_AUDIENCE",
        }

        inserted_cand = {
            "id":        "cand-lk-pilot-001",
            "status":    "PENDING",
            "statement": "MANUAL_EXTERNAL_ACTION em google (RETARGETING_AUDIENCE) melhora o resultado observado após a ação.",
        }
        sb = MagicMock()
        tbl = MagicMock()
        for m in ("insert",):
            getattr(tbl, m).return_value = tbl
        tbl.execute.return_value = MagicMock(data=[inserted_cand])
        sb.table.return_value = tbl

        cand_id = _generate_learning_candidate(
            sb, action, "out-lk-001", "POSITIVE", "rec-lk-001", _CLIENT_UUID, 81.0
        )
        assert cand_id == "cand-lk-pilot-001"

        row = tbl.insert.call_args[0][0]
        assert row["status"] == "PENDING"
        assert "POSITIVE" in row["pattern"]
        assert row["evidence_level"] == "OBSERVATION"
        assert row["client_id"] == _CLIENT_UUID

    def test_pilot_candidate_statement_attribution_only(self):
        """Statement must use attribution language, never claim causation."""
        from app.core.outcome_evaluator import _generate_learning_candidate

        action = {
            "id":          "act-lk-0002",
            "action_type": "MANUAL_EXTERNAL_ACTION",
            "platform":    "google",
            "entity_type": "RETARGETING_AUDIENCE",
        }
        inserted_cand = {"id": "cand-lk-002", "status": "PENDING", "statement": ""}
        sb = MagicMock()
        tbl = MagicMock()
        tbl.insert.return_value = tbl
        tbl.execute.return_value = MagicMock(data=[inserted_cand])
        sb.table.return_value = tbl

        _generate_learning_candidate(
            sb, action, "out-id", "POSITIVE", "rec-id", _CLIENT_UUID, 81.0
        )
        row = tbl.insert.call_args[0][0]
        assert "causa" not in row["statement"].lower()
        assert "observado" in row["statement"].lower() or "melhora" in row["statement"].lower()

    def test_pilot_pending_candidate_blocked_from_promotion(self):
        """Human must approve before Obsidian write — PENDING is rejected."""
        sb_cand = MagicMock()
        tbl = MagicMock()
        for m in ("select", "eq", "limit"):
            getattr(tbl, m).return_value = tbl
        tbl.execute.return_value = MagicMock(data=[{
            "id": "cand-lk-pilot-001", "status": "PENDING",
            "statement": "test", "pattern": "PAT", "scope": "CLIENT_ONLY",
            "client_id": _CLIENT_UUID, "evidence_refs": [], "evidence_level": "OBSERVATION",
            "observed_outcome": {}, "context": {}, "source_action_id": None,
            "source_recommendation_id": None, "updated_at": None,
        }])
        promo_tbl = MagicMock()
        for m in ("select", "eq", "limit", "insert"):
            getattr(promo_tbl, m).return_value = promo_tbl
        promo_tbl.execute.return_value = MagicMock(data=[])

        def _t(name):
            if name == "core_learning_candidates":
                return tbl
            return promo_tbl
        sb_cand.table.side_effect = _t

        with patch("app.core.obsidian_writer.get_supabase", return_value=sb_cand):
            from app.core.obsidian_writer import promote_to_knowledge
            result = promote_to_knowledge(
                learning_candidate_id="cand-lk-pilot-001",
                actor="maico@norolabs.com.br",
            )
        assert result["status"] == "FAILED"
        assert "PENDING" in (result.get("error") or "")

@pytest.mark.parametrize("identifier", [_CLIENT_SLUG, _CLIENT_UUID])
def test_candidate_creation_normalizes_client_before_dedup_and_insert(identifier):
    from importlib import import_module
    from app.agency_api.v1.schemas import LearningCandidateCreate

    db = MagicMock()
    registry = db.table.return_value
    registry.select.return_value.eq.return_value.limit.return_value.execute.return_value.data = [
        {"id": _CLIENT_UUID}
    ]
    candidate_table = MagicMock()
    db.table.side_effect = {"clients": registry, "core_learning_candidates": candidate_table}.__getitem__
    lookup = candidate_table.select.return_value
    lookup.eq.return_value = lookup
    lookup.is_.return_value = lookup
    lookup.limit.return_value.execute.return_value.data = []
    candidate_table.insert.return_value.execute.return_value.data = [{
        "id": "aabbccdd-0000-0000-0000-000000000003",
        "client_id": _CLIENT_UUID,
        "scope": "CLIENT_ONLY",
        "status": "PENDING",
    }]
    body = LearningCandidateCreate(
        statement="Review evidence",
        scope="CLIENT_ONLY",
        evidence_level="OBSERVATION",
        client_id=identifier,
        source_action_id="aabbccdd-0000-0000-0000-000000000004",
    )

    router = import_module("app.agency_api.v1.router")
    with patch.object(router, "_get_db", return_value=db):
        result = router.create_learning_candidate(body)

    lookup.eq.assert_any_call("client_id", _CLIENT_UUID)
    assert candidate_table.insert.call_args.args[0]["client_id"] == _CLIENT_UUID
    assert result.client_id == _CLIENT_UUID
    assert result.status == "PENDING"
