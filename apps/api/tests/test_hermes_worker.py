"""Tests for the Hermes analysis worker and pending endpoint."""
import json
import os
import pytest
from copy import deepcopy
from unittest.mock import MagicMock, patch

from app.core.hermes_worker import _normalise_prompt_response, _run_single, run_hermes_worker_all

# ── Normaliser ─────────────────────────────────────────────────────────────────

class TestNormalisePromptResponse:
    def test_top_level_analyses_nested(self):
        inp = {
            "executive_summary": {"bullets": ["a", "b"]},
            "google_analysis": {"spend_context": "ok"},
            "meta_analysis": {"spend_context": "ok"},
            "diagnosis": [{"observation": "x"}],
            "recommendations": [{"action": "do X specifically"}],
            "priorities_next_week": [{"action": "a"}],
            "analysis_notes": "limited data",
        }
        out = _normalise_prompt_response(inp)
        assert "channel_analysis" in out
        assert out["channel_analysis"]["google"] == {"spend_context": "ok"}
        assert out["channel_analysis"]["meta"] == {"spend_context": "ok"}
        assert "google_analysis" not in out
        assert "meta_analysis" not in out

    def test_priorities_renamed(self):
        inp = {"priorities_next_week": [{"action": "do something concrete"}]}
        out = _normalise_prompt_response(inp)
        assert "priorities" in out
        assert "priorities_next_week" not in out

    def test_analysis_notes_renamed(self):
        inp = {"analysis_notes": "provisional data"}
        out = _normalise_prompt_response(inp)
        assert "limitations" in out
        assert "analysis_notes" not in out

    def test_existing_priorities_not_overwritten(self):
        inp = {"priorities": [{"action": "original"}], "priorities_next_week": [{"action": "new"}]}
        out = _normalise_prompt_response(inp)
        assert out["priorities"] == [{"action": "original"}]

    def test_already_normalised_passthrough(self):
        inp = {
            "channel_analysis": {"google": {"spend_context": "ok"}},
            "priorities": [{"action": "do X"}],
            "limitations": "none",
        }
        out = _normalise_prompt_response(inp)
        assert out["channel_analysis"]["google"]["spend_context"] == "ok"
        assert out["priorities"] == [{"action": "do X"}]
        assert out["limitations"] == "none"


# ── FakeStore for worker tests ─────────────────────────────────────────────────

class FakeStore:
    def __init__(self):
        self.rows: dict[str, dict] = {}

    def get(self, key):
        return self.rows.get(key)

    def cas(self, key, revision, document):
        current = self.rows.get(key)
        if current is None and revision is None:
            self.rows[key] = {"revision": 1, "document": document}
            return True
        if current is not None and current["revision"] == revision:
            self.rows[key] = {"revision": revision + 1, "document": document}
            return True
        return False


def _make_weekly_artifact(client_id="lk-sneakers", period_start="2026-10-19", period_end="2026-10-25", ver=1):
    from datetime import datetime, timezone
    return {
        "client_id": client_id,
        "canonical_engine": "H7",
        "engine_version": "2",
        "version": ver,
        "generated_at": datetime(2026, 10, 26, 8, 0, 0, tzinfo=timezone.utc).isoformat(),
        "period_start": period_start,
        "period_end": period_end,
        "source_state": [{"source": "meta_ads", "status": "OK"}],
        "certification_status": [{"source": "meta_ads", "certified": True}],
        "review_status": "READY",
        "content": {
            "client_id": client_id,
            "analysis_period": {"start": period_start, "end": period_end},
            "status": "READY",
            "facts": [{"metric_key": "revenue_business", "value": 210292.51, "unit": "BRL", "value_status": "OK", "certification_status": "PROVISIONAL"}],
            "channels": {"meta": {"active": True}, "google": {"active": True}},
            "business_truth": "R$210k semana",
            "change_summary": {"meta": {"relevant_changes": 0}},
            "analysis_layer": {
                "status": "WAITING_FOR_HERMES_ANALYSIS",
                "anomalies_detected": [{"type": "HIGH", "code": "GA4_LOW_CVR"}],
                "wow_comparison": {"revenue_business": -20.0},
                "executive_summary": {"bullets": [], "key_win": None, "key_risk": None},
                "google_analysis": None,
                "meta_analysis": None,
                "diagnosis": [],
                "recommendations": [],
                "change_digest_section": None,
                "priorities_next_week": [],
                "analysis_notes": "Awaiting Hermes analysis",
                "engine_version": "2",
            },
        },
        "evidence_refs": [{"ref": "meta_ads_weekly"}],
    }


def _make_hermes_payload(analysis_request_id: str, report_version: int = 1) -> dict:
    return {
        "analysis_request_id": analysis_request_id,
        "report_version": report_version,
        "executive_summary": {
            "bullets": ["Meta ROAS 8.56x, 25 conversões na semana", "Google purchase ROAS 5.54x, melhor dos últimos 30 dias"],
            "key_win": "Google ROAS 5.54x +28.5% WoW",
            "key_risk": "MER caiu 17.3% — receita -20% WoW",
        },
        "channel_analysis": {
            "google": {"spend_context": "R$5.312 (-10.7% WoW)", "efficiency_assessment": "ROAS 5.54x"},
            "meta": {"spend_context": "R$8.318 (-2.3% WoW)", "efficiency_assessment": "ROAS 8.56x"},
        },
        "diagnosis": [{"observation": "MER caiu 17.3%", "diagnosis": "receita em queda",
                        "recommended_action": "verificar sazonalidade historica antes de cortar budget",
                        "impact": "high", "confidence": "MEDIUM"}],
        "recommendations": [{"problem_or_opportunity": "Google ROAS em alta",
                              "evidence": "ROAS 5.54x vs 4.3x semana passada",
                              "action": "realocar R$500 de Meta TOPO para Google Smart Bidding esta semana",
                              "priority": "P1",
                              "expected_effect": "incremento de 2-3 purchase conv",
                              "risk": "bidding pode levar 5 dias para re-otimizar",
                              "confidence": "MEDIUM"}],
        "priorities": [{"action": "desativar grupo TOPO Meta por 7 dias",
                        "owner": "gestor midia", "deadline_suggestion": "01/11", "rationale": "isolar impacto"}],
        "limitations": "PROVISIONAL — janela de atribuição 72h",
    }


# ── Worker single-request tests ────────────────────────────────────────────────

class TestRunSingle:
    def _setup_store(self):
        store = FakeStore()
        from app.core.canonical_reports import Reports, Flags, ReportError
        flags = Flags(CANONICAL_WEEKLY_REVIEW_ENABLED=True, CANONICAL_MONTHLY_REVIEW_ENABLED=True)
        rpts  = Reports(store, flags)
        artifact = _make_weekly_artifact()
        doc = rpts.generate(artifact)
        report_id = doc["report_id"]
        import uuid
        analysis_request_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"analysis-request:{report_id}:v1"))
        handoff = {
            "analysis_request_id": analysis_request_id,
            "report_id":           report_id,
            "report_version":      1,
            "client_id":           "lk-sneakers",
            "report_type":         "weekly",
            "period_start":        "2026-10-19",
            "period_end":          "2026-10-25",
            "requested_at":        "2026-10-26T08:00:00+00:00",
            "status":              "PENDING",
            "hermes_prompt":       "analyse this week",
        }
        rpts.write_analysis_request(analysis_request_id, handoff)
        return store, report_id, analysis_request_id

    def test_missing_fields_skips(self):
        result = _run_single(MagicMock(), {})
        assert result["status"] == "SKIPPED"
        assert result["reason"] == "missing_fields"

    def test_no_anthropic_key_skips(self):
        store, report_id, ar_id = self._setup_store()
        doc = {
            "analysis_request_id": ar_id,
            "report_id":           report_id,
            "report_version":      1,
            "hermes_prompt":       "prompt",
            "client_id":           "lk-sneakers",
            "status":              "PENDING",
        }
        from app.core.canonical_reports import CoreStore, Reports, Flags
        flags = Flags(CANONICAL_WEEKLY_REVIEW_ENABLED=True, CANONICAL_MONTHLY_REVIEW_ENABLED=True)
        rpts  = Reports(store, flags)

        from app.config import settings as _s
        with patch("app.core.canonical_reports.CoreStore", return_value=store), \
             patch("app.core.canonical_reports.Reports", return_value=rpts), \
             patch.object(_s, "ANTHROPIC_API_KEY", ""):
            result = _run_single(MagicMock(), doc)
        assert result["status"] == "SKIPPED"
        assert result["reason"] == "no_anthropic_key"

    def test_already_applied_if_version_has_analysis_complete(self):
        """If the specified report_version is already ANALYSIS_COMPLETE, worker returns ALREADY_APPLIED."""
        store, report_id, ar_id = self._setup_store()
        from app.core.canonical_reports import CoreStore, Reports, Flags
        from datetime import datetime, timezone

        flags = Flags(CANONICAL_WEEKLY_REVIEW_ENABLED=True, CANONICAL_MONTHLY_REVIEW_ENABLED=True)
        rpts  = Reports(store, flags)

        # Apply analysis → creates v2 with ANALYSIS_COMPLETE
        payload = _make_hermes_payload(ar_id, 1)
        at = datetime.now(timezone.utc).isoformat()
        rpts.apply_analysis(report_id, 1, payload, ar_id, actor="canonical_analysis_callback", at=at)

        # Hermes sends a new analysis_request pointing to version 2 (already ANALYSIS_COMPLETE)
        import uuid
        ar_id2 = str(uuid.uuid5(uuid.NAMESPACE_URL, f"analysis-request:{report_id}:v2"))
        doc = {
            "analysis_request_id": ar_id2,
            "report_id":           report_id,
            "report_version":      2,  # already ANALYSIS_COMPLETE
            "hermes_prompt":       "prompt",
            "client_id":           "lk-sneakers",
            "status":              "PENDING",
        }
        with patch("app.core.canonical_reports.CoreStore", return_value=store), \
             patch("app.core.canonical_reports.Reports", return_value=rpts):
            result = _run_single(MagicMock(), doc)
        assert result["status"] == "ALREADY_APPLIED"

    def test_applied_with_mocked_anthropic(self):
        """Full apply flow with mocked Anthropic response."""
        store, report_id, ar_id = self._setup_store()
        from app.core.canonical_reports import CoreStore, Reports, Flags

        flags = Flags(CANONICAL_WEEKLY_REVIEW_ENABLED=True, CANONICAL_MONTHLY_REVIEW_ENABLED=True)
        rpts  = Reports(store, flags)

        # Anthropic response in prompt format (top-level google/meta)
        anthropic_output = {
            "executive_summary": {
                "bullets": ["Meta ROAS 8.56x semana", "Google ROAS 5.54x +28.5%"],
                "key_win": "Google ROAS 5.54x",
                "key_risk": "MER -17.3%",
            },
            "google_analysis": {"spend_context": "R$5.312", "efficiency_assessment": "ROAS 5.54x", "conversion_structure": "ok", "observations": ["obs1"]},
            "meta_analysis": {"spend_context": "R$8.318", "efficiency_assessment": "ROAS 8.56x", "audience_context": "ok", "change_digest_integration": "ok", "observations": ["obs1"]},
            "diagnosis": [{"observation": "MER caiu", "diagnosis": "receita queda",
                           "recommended_action": "verificar sazonalidade antes de cortar budget",
                           "impact": "high", "confidence": "MEDIUM"}],
            "recommendations": [{"problem_or_opportunity": "Google ROAS em alta",
                                  "evidence": "ROAS 5.54x", "action": "realocar R$500 para Google esta semana",
                                  "priority": "P1", "expected_effect": "2-3 conversões", "risk": "bidding lag", "confidence": "MEDIUM"}],
            "priorities_next_week": [{"action": "desativar TOPO Meta", "owner": "gestor", "deadline_suggestion": "01/11", "rationale": "isolar"}],
            "analysis_notes": "PROVISIONAL",
        }

        mock_msg = MagicMock()
        mock_msg.content = [MagicMock(text=json.dumps(anthropic_output))]
        mock_ac = MagicMock()
        mock_ac.messages.create.return_value = mock_msg

        doc = {
            "analysis_request_id": ar_id,
            "report_id":           report_id,
            "report_version":      1,
            "hermes_prompt":       "analyse please",
            "client_id":           "lk-sneakers",
            "status":              "PENDING",
        }

        from app.config import settings as _s
        with patch("app.core.canonical_reports.CoreStore", return_value=store), \
             patch("app.core.canonical_reports.Reports", return_value=rpts), \
             patch.object(_s, "ANTHROPIC_API_KEY", "test-key"), \
             patch.object(_s, "ANTHROPIC_MODEL", "claude-sonnet-4-6"), \
             patch("anthropic.Anthropic", return_value=mock_ac):
            result = _run_single(MagicMock(), doc)

        assert result["status"] == "APPLIED", f"Expected APPLIED, got {result}"
        assert result["new_version"] == 2

        # Verify v2 is in CAS with ANALYSIS_COMPLETE
        v2_key = f"report:{report_id}:v2"
        assert v2_key in store.rows
        v2_doc = store.rows[v2_key]["document"]
        assert v2_doc["content"]["analysis_layer"]["status"] == "ANALYSIS_COMPLETE"
        assert v2_doc["content"]["analysis_layer"]["anomalies_detected"]  # preserved from base

        # Verify handoff contract marked COMPLETED
        req_key = f"analysis-request:{ar_id}"
        assert store.rows[req_key]["document"]["status"] == "COMPLETED"


# ── run_hermes_worker_all tests ────────────────────────────────────────────────

class TestRunHermesWorkerAll:
    def test_flag_off_skips(self):
        with patch.dict(os.environ, {"CANONICAL_HERMES_WORKER_ENABLED": "false"}):
            result = run_hermes_worker_all(sb=MagicMock())
        assert result["skipped"] is True

    def test_flag_on_processes(self):
        store = FakeStore()
        from app.core.canonical_reports import Reports, Flags
        flags = Flags(CANONICAL_WEEKLY_REVIEW_ENABLED=True, CANONICAL_MONTHLY_REVIEW_ENABLED=True)
        rpts  = Reports(store, flags)
        artifact = _make_weekly_artifact()
        doc = rpts.generate(artifact)
        report_id = doc["report_id"]
        import uuid
        ar_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"analysis-request:{report_id}:v1"))

        handoff = {
            "analysis_request_id": ar_id,
            "report_id":           report_id,
            "report_version":      1,
            "client_id":           "lk-sneakers",
            "report_type":         "weekly",
            "period_start":        "2026-10-19",
            "period_end":          "2026-10-25",
            "requested_at":        "2026-10-26T08:00:00+00:00",
            "status":              "PENDING",
            "hermes_prompt":       "analyse this",
        }
        rpts.write_analysis_request(ar_id, handoff)

        # Fake DB returns the pending contract
        fake_rows = [{"entity_key": f"analysis-request:{ar_id}", "document": handoff}]
        sb_mock = MagicMock()
        (sb_mock.table.return_value
         .select.return_value
         .like.return_value
         .contains.return_value
         .order.return_value
         .limit.return_value
         .execute.return_value) = MagicMock(data=fake_rows)

        anthropic_output = {
            "executive_summary": {"bullets": ["meta ok", "google ok"], "key_win": "google roas", "key_risk": "mer caiu"},
            "google_analysis": {"spend_context": "ok", "efficiency_assessment": "ok", "conversion_structure": "ok", "observations": ["obs"]},
            "meta_analysis": {"spend_context": "ok", "efficiency_assessment": "ok", "audience_context": "ok", "change_digest_integration": "ok", "observations": ["obs"]},
            "diagnosis": [{"observation": "mer caiu", "diagnosis": "sazonal",
                           "recommended_action": "verificar historico antes de ajustar budget",
                           "impact": "high", "confidence": "HIGH"}],
            "recommendations": [{"problem_or_opportunity": "google alto",
                                  "evidence": "roas 5.54x", "action": "realocar R$500 para Google esta semana",
                                  "priority": "P1", "expected_effect": "2-3 conv", "risk": "lag", "confidence": "HIGH"}],
            "priorities_next_week": [{"action": "desativar TOPO", "owner": "gestor", "deadline_suggestion": "01/11", "rationale": "isolar"}],
            "analysis_notes": "PROVISIONAL",
        }
        mock_msg = MagicMock()
        mock_msg.content = [MagicMock(text=json.dumps(anthropic_output))]
        mock_ac = MagicMock()
        mock_ac.messages.create.return_value = mock_msg

        from app.config import settings as _s
        with patch("app.core.canonical_reports.CoreStore", return_value=store), \
             patch("app.core.canonical_reports.Reports", return_value=rpts), \
             patch.dict(os.environ, {"CANONICAL_HERMES_WORKER_ENABLED": "true"}), \
             patch.object(_s, "ANTHROPIC_API_KEY", "test-key"), \
             patch.object(_s, "ANTHROPIC_MODEL", "claude-sonnet-4-6"), \
             patch("anthropic.Anthropic", return_value=mock_ac):
            result = run_hermes_worker_all(sb=sb_mock)

        assert result["applied"] == 1, f"Expected 1 applied, got {result}"
        assert result["total"] == 1
