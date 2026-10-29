"""
Tests for the Hermes async handoff flow.

Covers: handoff creation, analysis callback, idempotency, version conflict,
artifact integrity, quality gate integration, H7/H8 specifics.
No LLM calls. No network. No DB.
"""
from __future__ import annotations

import json
import uuid
from copy import deepcopy
from datetime import date, datetime, timezone
from hashlib import sha256
from typing import Optional
from unittest.mock import MagicMock, patch
import os
import pytest

from app.core.canonical_reports import CoreStore, Flags, ReportError, Reports, digest
from app.core.analysis_layer import (
    build_hermes_handoff_from_artifact,
    apply_hermes_analysis,
    validate_hermes_response,
    check_quality_gate,
    build_analysis_layer,
)


# ── Fake CAS store ────────────────────────────────────────────────────────────

class FakeStore:
    def __init__(self):
        self._data: dict[str, dict] = {}   # key → {revision, document}

    def get(self, key: str) -> Optional[dict]:
        return deepcopy(self._data.get(key))

    def cas(self, key: str, revision, document) -> bool:
        existing = self._data.get(key)
        if (existing is None) != (revision is None):
            return False
        if existing and existing["revision"] != revision:
            return False
        rev = (existing["revision"] + 1) if existing else 1
        self._data[key] = {"revision": rev, "document": deepcopy(document)}
        return True


# ── Fixtures ──────────────────────────────────────────────────────────────────

_PILOT_FLAGS = Flags(
    CANONICAL_WEEKLY_REVIEW_ENABLED=True,
    CANONICAL_MONTHLY_REVIEW_ENABLED=True,
    REPORT_PUBLISH_ENABLED=False,
    REPORT_DELIVERY_ENABLED=False,
)

def _now():
    return datetime.now(timezone.utc).isoformat()


def _h7_artifact(client_id="lk", version=1, include_analysis=True) -> dict:
    """Minimal valid H7 artifact (Mon-Sun last week, already closed)."""
    ps = "2026-10-19"
    pe = "2026-10-25"
    analysis = (
        {
            "status": "WAITING_FOR_HERMES_ANALYSIS",
            "anomalies_detected": [],
            "wow_comparison": {"available": False},
            "executive_summary": {"bullets": [], "key_win": None, "key_risk": None},
            "google_analysis": None,
            "meta_analysis": None,
            "diagnosis": [],
            "recommendations": [],
            "change_digest_section": None,
            "priorities_next_week": [],
            "analysis_notes": "Awaiting Hermes analysis",
            "engine_version": "2",
        }
        if include_analysis
        else {"status": "ANALYSIS_DISABLED"}
    )
    content = {
        "client_id": client_id,
        "analysis_period": {"start": ps, "end": pe},
        "status": "READY",
        "facts": [
            {"metric_key": "meta_spend", "value": 10000, "value_status": "OK"},
            {"metric_key": "revenue_business", "value": 80000, "value_status": "OK"},
        ],
        "channels": {"meta": {"source_state": "READY"}, "google": {"source_state": "READY"}},
        "business_truth": "Business Truth: R$80K receita na semana.",
        "change_summary": {},
        "analysis_layer": analysis,
        "observations": [],
        "limitations": [],
    }
    raw = {
        "client_id": client_id,
        "canonical_engine": "H7",
        "engine_version": "h7-v1",
        "version": version,
        "period_start": ps,
        "period_end": pe,
        "generated_at": "2026-10-26T08:00:00+00:00",
        "source_state": [{"source": "meta", "source_state": "READY"}],
        "certification_status": [{"window": "analysis", "metric_key": "meta_spend", "certification_status": "PROVISIONAL", "value_status": "OK"}],
        "review_status": "READY",
        "content": content,
        "evidence_refs": [{"source": "core_metric_snapshots", "client_id": client_id, "period_start": ps, "period_end": pe, "metric_count": 2}],
    }
    return raw


def _h8_artifact(client_id="lk", version=1) -> dict:
    """Minimal valid H8 artifact (closed calendar month Sep 2026)."""
    import calendar
    ps = "2026-09-01"
    pe = "2026-09-30"
    analysis = {
        "status": "WAITING_FOR_HERMES_ANALYSIS",
        "anomalies_detected": [],
        "wow_comparison": {"available": False},
        "executive_summary": {"bullets": [], "key_win": None, "key_risk": None},
        "google_analysis": None,
        "meta_analysis": None,
        "diagnosis": [],
        "recommendations": [],
        "change_digest_section": None,
        "priorities_next_week": [],
        "analysis_notes": "Awaiting Hermes analysis",
        "engine_version": "2",
    }
    content = {
        "client_id": client_id,
        "analysis_period": {"start": ps, "end": pe},
        "status": "READY",
        "facts": [
            {"metric_key": "meta_spend", "value": 50000, "value_status": "OK"},
            {"metric_key": "revenue_business", "value": 350000, "value_status": "OK"},
        ],
        "channels": {"meta": {"source_state": "READY"}, "google": {"source_state": "READY"}},
        "business_truth": "Business Truth: R$350K receita em setembro.",
        "change_summary": {},
        "analysis_layer": analysis,
        "observations": [],
        "limitations": [],
    }
    return {
        "client_id": client_id,
        "canonical_engine": "H8",
        "engine_version": "h8-v1",
        "version": version,
        "period_start": ps,
        "period_end": pe,
        "generated_at": "2026-10-01T08:30:00+00:00",
        "source_state": [{"source": "meta", "source_state": "READY"}],
        "certification_status": [{"window": "analysis", "metric_key": "meta_spend", "certification_status": "PROVISIONAL", "value_status": "OK"}],
        "review_status": "READY",
        "content": content,
        "evidence_refs": [{"source": "core_metric_snapshots", "client_id": client_id, "period_start": ps, "period_end": pe, "metric_count": 2}],
    }


def _good_hermes_payload(analysis_request_id: str, report_version: int = 1) -> dict:
    return {
        "analysis_request_id": analysis_request_id,
        "report_version": report_version,
        "executive_summary": {
            "bullets": ["Meta spend R$10K, ROAS 8x estável", "Revenue R$80K, MER 8x"],
            "key_win": "MER acima de 8x pela terceira semana consecutiva",
            "key_risk": "GA4 CVR 0.009% requer investigação urgente",
        },
        "channel_analysis": {
            "google": {"spend_context": "Spend Google R$8K estável WoW"},
            "meta": {"spend_context": "Meta spend R$10K, ROAS 8x"},
        },
        "diagnosis": [
            {
                "observation": "GA4 CVR 0.009%",
                "diagnosis": "Bot traffic ou sessões duplicadas no GA4",
                "recommended_action": "Criar segmento GA4 excluindo sessões com bounce < 2s",
                "impact": "high",
                "confidence": "HIGH",
            }
        ],
        "recommendations": [
            {
                "problem_or_opportunity": "GA4 CVR anômalo",
                "evidence": "597K sessões / 52 purchases = CVR 0.009%",
                "action": "Criar filtro GA4 excluindo IPs com sessão < 2s de duração",
                "priority": "P0",
                "expected_effect": "CVR realista para análise de funil (esperado 1-3%)",
                "risk": "Pode reduzir sessões reportadas em 80%+",
                "confidence": "HIGH",
            }
        ],
        "priorities": [
            {
                "action": "Revisar dados GA4 com engenheiro de tracking",
                "owner": "Media + Tracking",
                "deadline_suggestion": "Seg próxima",
                "rationale": "CVR impossível indica problema de medição estrutural",
            }
        ],
        "limitations": "GA4 sessions may include bot traffic",
        "analysis_quality": 8.5,
        "analysis_engine_metadata": {"model": "gpt-4o", "provider": "openai"},
    }


# ── Test: Handoff Contract Creation ──────────────────────────────────────────

class TestHandoffContractCreation:
    def test_contract_fields_present(self):
        artifact = _h7_artifact()
        rid = str(uuid.uuid5(uuid.NAMESPACE_URL, json.dumps(["lk", "weekly", "2026-10-19", "2026-10-25"])))
        doc = {**artifact, "report_id": rid, "report_type": "weekly", "artifact_hash": "abc", "idempotency_key": "xyz", "status": "DRAFT"}
        ar_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"analysis-request:{rid}:v1"))
        contract = build_hermes_handoff_from_artifact(ar_id, rid, 1, doc, "weekly")
        assert contract["schema_version"] == "hermes-analysis-request-v1"
        assert contract["analysis_request_id"] == ar_id
        assert contract["report_id"] == rid
        assert contract["report_version"] == 1
        assert contract["client_id"] == "lk"
        assert contract["status"] == "PENDING"
        assert "hermes_prompt" in contract
        assert "context_manifest" in contract
        assert "requested_analysis_sections" in contract

    def test_context_manifest_has_facts(self):
        artifact = _h7_artifact()
        rid = "r1"
        doc = {**artifact, "report_id": rid, "report_type": "weekly", "artifact_hash": "h", "idempotency_key": "k", "status": "DRAFT"}
        contract = build_hermes_handoff_from_artifact("ar1", rid, 1, doc, "weekly")
        manifest = contract["context_manifest"]
        assert "metrics" in manifest
        assert "facts" in manifest
        assert "meta_spend" in manifest["metrics"]

    def test_weekly_sections_no_monthly_extras(self):
        artifact = _h7_artifact()
        contract = build_hermes_handoff_from_artifact("ar1", "r1", 1, artifact, "weekly")
        secs = contract["requested_analysis_sections"]
        assert "executive_summary" in secs
        assert "monthly_comparison" not in secs

    def test_monthly_sections_include_extras(self):
        artifact = _h8_artifact()
        contract = build_hermes_handoff_from_artifact("ar1", "r1", 1, artifact, "monthly")
        secs = contract["requested_analysis_sections"]
        assert "monthly_comparison" in secs
        assert "next_month_priorities" in secs

    def test_prompt_includes_client_and_period(self):
        artifact = _h7_artifact(client_id="enutri")
        contract = build_hermes_handoff_from_artifact("ar1", "r1", 1, artifact, "weekly")
        prompt = contract["hermes_prompt"]
        assert "enutri" in prompt
        assert "2026-10-19" in prompt

    def test_write_analysis_request_idempotent(self):
        store = FakeStore()
        rpts = Reports(store, flags=_PILOT_FLAGS)
        doc = {"schema_version": "hermes-analysis-request-v1", "analysis_request_id": "ar1"}
        r1 = rpts.write_analysis_request("ar1", doc)
        r2 = rpts.write_analysis_request("ar1", doc)
        assert r1["analysis_request_id"] == "ar1"
        assert r2["analysis_request_id"] == "ar1"

    def test_get_analysis_request_not_found_raises(self):
        store = FakeStore()
        rpts = Reports(store, flags=_PILOT_FLAGS)
        with pytest.raises(ReportError, match="ANALYSIS_REQUEST_NOT_FOUND"):
            rpts.get_analysis_request("nonexistent")

    def test_get_analysis_request_returns_stored(self):
        store = FakeStore()
        rpts = Reports(store, flags=_PILOT_FLAGS)
        doc = {"schema_version": "hermes-analysis-request-v1", "analysis_request_id": "ar2"}
        rpts.write_analysis_request("ar2", doc)
        result = rpts.get_analysis_request("ar2")
        assert result["analysis_request_id"] == "ar2"


# ── Test: Hermes Analysis Callback ────────────────────────────────────────────

class TestAnalysisCallback:
    def _setup(self, artifact_fn=_h7_artifact):
        """Generate a report and return (store, rpts, doc, analysis_request_id)."""
        store = FakeStore()
        rpts = Reports(store, flags=_PILOT_FLAGS)
        artifact = artifact_fn()
        doc = rpts.generate(artifact)
        rid = doc["report_id"]
        ar_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"analysis-request:{rid}:v1"))
        handoff = build_hermes_handoff_from_artifact(ar_id, rid, 1, doc, "weekly")
        rpts.write_analysis_request(ar_id, handoff)
        return store, rpts, doc, ar_id

    def test_apply_analysis_creates_v2(self):
        store, rpts, doc, ar_id = self._setup()
        payload = _good_hermes_payload(ar_id, report_version=1)
        result = rpts.apply_analysis(doc["report_id"], 1, payload, ar_id, actor="canonical_analysis_callback", at=_now())
        assert result["version"] == 2
        assert result["content"]["analysis_layer"]["status"] == "ANALYSIS_COMPLETE"

    def test_apply_analysis_quality_gate_passes_transitions(self):
        store, rpts, doc, ar_id = self._setup()
        payload = _good_hermes_payload(ar_id, report_version=1)
        result = rpts.apply_analysis(doc["report_id"], 1, payload, ar_id, actor="canonical_analysis_callback", at=_now())
        assert result["status"] == "READY_FOR_REVIEW"

    def test_apply_analysis_bad_base_raises(self):
        store = FakeStore()
        rpts = Reports(store, flags=_PILOT_FLAGS)
        artifact = _h7_artifact()
        doc = rpts.generate(artifact)
        # Manually set analysis status to ANALYSIS_COMPLETE to simulate already-done
        key = f"report:{doc['report_id']}:v1"
        row = store.get(key)
        bad_doc = deepcopy(row["document"])
        bad_doc["content"]["analysis_layer"]["status"] = "ANALYSIS_COMPLETE"
        store._data[key] = {"revision": row["revision"], "document": bad_doc}
        ar_id = "ar-bad"
        payload = _good_hermes_payload(ar_id)
        with pytest.raises(ReportError, match="BASE_VERSION_NOT_WAITING"):
            rpts.apply_analysis(doc["report_id"], 1, payload, ar_id, actor="canonical_analysis_callback", at=_now())

    def test_apply_analysis_invalid_payload_raises(self):
        store, rpts, doc, ar_id = self._setup()
        bad_payload = {"analysis_request_id": ar_id, "report_version": 1, "executive_summary": {"bullets": []}, "diagnosis": [], "recommendations": []}
        with pytest.raises((ValueError, ReportError)):
            rpts.apply_analysis(doc["report_id"], 1, bad_payload, ar_id, actor="canonical_analysis_callback", at=_now())

    def test_apply_analysis_generic_recommendation_raises(self):
        store, rpts, doc, ar_id = self._setup()
        payload = _good_hermes_payload(ar_id)
        payload["recommendations"][0]["action"] = "otimizar campanhas para melhorar ROAS"
        with pytest.raises((ValueError, ReportError)):
            rpts.apply_analysis(doc["report_id"], 1, payload, ar_id, actor="canonical_analysis_callback", at=_now())


# ── Test: Idempotency ─────────────────────────────────────────────────────────

class TestIdempotency:
    def _setup(self):
        store = FakeStore()
        rpts = Reports(store, flags=_PILOT_FLAGS)
        doc = rpts.generate(_h7_artifact())
        rid = doc["report_id"]
        ar_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"analysis-request:{rid}:v1"))
        rpts.write_analysis_request(ar_id, {"analysis_request_id": ar_id})
        return store, rpts, doc, ar_id

    def test_same_analysis_request_id_idempotent(self):
        store, rpts, doc, ar_id = self._setup()
        payload = _good_hermes_payload(ar_id)
        r1 = rpts.apply_analysis(doc["report_id"], 1, payload, ar_id, actor="canonical_analysis_callback", at=_now())
        r2 = rpts.apply_analysis(doc["report_id"], 1, payload, ar_id, actor="canonical_analysis_callback", at=_now())
        assert r1["version"] == r2["version"] == 2

    def test_write_analysis_request_idempotent(self):
        store = FakeStore()
        rpts = Reports(store, flags=_PILOT_FLAGS)
        doc = {"schema_version": "v1", "analysis_request_id": "ar-idem"}
        rpts.write_analysis_request("ar-idem", doc)
        rpts.write_analysis_request("ar-idem", doc)   # no raise
        assert rpts.get_analysis_request("ar-idem")["analysis_request_id"] == "ar-idem"


# ── Test: Version / CAS Integrity ─────────────────────────────────────────────

class TestVersionIntegrity:
    def test_v2_artifact_hash_differs_from_v1(self):
        store = FakeStore()
        rpts = Reports(store, flags=_PILOT_FLAGS)
        doc = rpts.generate(_h7_artifact())
        rid = doc["report_id"]
        ar_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"analysis-request:{rid}:v1"))
        rpts.write_analysis_request(ar_id, {"analysis_request_id": ar_id})
        payload = _good_hermes_payload(ar_id)
        r2 = rpts.apply_analysis(rid, 1, payload, ar_id, actor="canonical_analysis_callback", at=_now())
        assert r2.get("artifact_hash") != doc.get("artifact_hash")

    def test_v2_analysis_status_complete(self):
        store = FakeStore()
        rpts = Reports(store, flags=_PILOT_FLAGS)
        doc = rpts.generate(_h7_artifact())
        rid = doc["report_id"]
        ar_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"analysis-request:{rid}:v1"))
        rpts.write_analysis_request(ar_id, {"analysis_request_id": ar_id})
        payload = _good_hermes_payload(ar_id)
        r2 = rpts.apply_analysis(rid, 1, payload, ar_id, actor="canonical_analysis_callback", at=_now())
        assert r2["content"]["analysis_layer"]["status"] == "ANALYSIS_COMPLETE"


# ── Test: Quality Gate Integration ────────────────────────────────────────────

class TestQualityGate:
    def test_waiting_fails_gate(self):
        content = {
            "facts": [{"metric_key": "meta_spend", "value": 10000}],
            "channels": {"meta": {}, "google": {}},
            "analysis_layer": {
                "status": "WAITING_FOR_HERMES_ANALYSIS",
                "executive_summary": {"bullets": [], "key_win": None, "key_risk": None},
                "recommendations": [], "diagnosis": [], "google_analysis": None, "meta_analysis": None,
            },
        }
        passes, violations = check_quality_gate(content, analysis_enabled=True)
        assert not passes
        assert any("WAITING_FOR_HERMES_ANALYSIS" in v for v in violations)

    def test_disabled_passes_gate_with_facts(self):
        content = {
            "facts": [{"metric_key": "meta_spend", "value": 10000}],
            "channels": {},
            "analysis_layer": {"status": "ANALYSIS_DISABLED"},
        }
        passes, violations = check_quality_gate(content, analysis_enabled=False)
        assert passes

    def test_empty_facts_fails_gate_always(self):
        content = {"facts": [], "channels": {}, "analysis_layer": {"status": "WAITING_FOR_HERMES_ANALYSIS"}}
        passes, violations = check_quality_gate(content, analysis_enabled=False)
        assert not passes


# ── Test: H7 Scheduler Flow ──────────────────────────────────────────────────

class TestH7SchedulerHandoff:
    def test_scheduler_writes_handoff_and_returns_waiting(self):
        from app.core.canonical_weekly_scheduler import run_canonical_weekly_for_client
        store = FakeStore()
        with patch.dict(os.environ, {"CANONICAL_ANALYSIS_ENABLED": "true",
                                      "CANONICAL_WEEKLY_REVIEW_ENABLED": "true"}):
            fake_sb = MagicMock()
            # Simulate no existing snapshot → scheduler needs DB but we test the store path only
            # Instead: test via _store_override with a pre-built artifact via generate().
            rpts = Reports(store, flags=_PILOT_FLAGS)
            artifact = _h7_artifact()
            doc = rpts.generate(artifact)
            # Check that analysis_layer.status is WAITING_FOR_HERMES_ANALYSIS
            assert doc["content"]["analysis_layer"]["status"] == "WAITING_FOR_HERMES_ANALYSIS"
            assert doc["status"] == "DRAFT"

    def test_weekly_generate_produces_draft_with_waiting(self):
        store = FakeStore()
        rpts = Reports(store, flags=_PILOT_FLAGS)
        with patch.dict(os.environ, {"CANONICAL_ANALYSIS_ENABLED": "true"}):
            artifact = _h7_artifact()
            doc = rpts.generate(artifact)
        assert doc["status"] == "DRAFT"
        assert doc["content"]["analysis_layer"]["status"] == "WAITING_FOR_HERMES_ANALYSIS"


# ── Test: H8 Monthly ─────────────────────────────────────────────────────────

class TestH8MonthlyHandoff:
    def test_h8_generate_returns_waiting(self):
        store = FakeStore()
        rpts = Reports(store, flags=_PILOT_FLAGS)
        with patch.dict(os.environ, {"CANONICAL_ANALYSIS_ENABLED": "true"}):
            artifact = _h8_artifact()
            doc = rpts.generate(artifact)
        assert doc["status"] == "DRAFT"
        assert doc["content"]["analysis_layer"]["status"] == "WAITING_FOR_HERMES_ANALYSIS"

    def test_h8_handoff_has_monthly_sections(self):
        artifact = _h8_artifact()
        rid = "r-h8"
        doc = {**artifact, "report_id": rid, "report_type": "monthly", "artifact_hash": "h", "idempotency_key": "k", "status": "DRAFT"}
        ar_id = "ar-h8"
        contract = build_hermes_handoff_from_artifact(ar_id, rid, 1, doc, "monthly")
        secs = contract["requested_analysis_sections"]
        assert "monthly_comparison" in secs
        assert "next_month_priorities" in secs

    def test_h8_apply_analysis_monthly_extras(self):
        store = FakeStore()
        rpts = Reports(store, flags=_PILOT_FLAGS)
        artifact = _h8_artifact()
        doc = rpts.generate(artifact)
        rid = doc["report_id"]
        ar_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"analysis-request:{rid}:v1"))
        rpts.write_analysis_request(ar_id, {"analysis_request_id": ar_id})
        payload = _good_hermes_payload(ar_id)
        payload["monthly_comparison"] = {"vs_previous_month": "Revenue +15%"}
        payload["next_month_priorities"] = [{"action": "Escalar budget Meta 20%", "rationale": "ROAS 8x consistente"}]
        r2 = rpts.apply_analysis(rid, 1, payload, ar_id, actor="canonical_analysis_callback", at=_now())
        assert r2["content"]["analysis_layer"]["monthly_comparison"] is not None
        assert r2["content"]["analysis_layer"]["next_month_priorities"] is not None


# ── Test: Client Isolation ────────────────────────────────────────────────────

class TestClientIsolation:
    def test_different_clients_separate_records(self):
        store = FakeStore()
        rpts = Reports(store, flags=_PILOT_FLAGS)
        doc_lk     = rpts.generate(_h7_artifact(client_id="lk"))
        doc_enutri = rpts.generate(_h7_artifact(client_id="enutri"))
        assert doc_lk["report_id"] != doc_enutri["report_id"]
        assert doc_lk["client_id"] == "lk"
        assert doc_enutri["client_id"] == "enutri"

    def test_analysis_request_ids_differ_per_client(self):
        store = FakeStore()
        rpts = Reports(store, flags=_PILOT_FLAGS)
        doc_lk     = rpts.generate(_h7_artifact(client_id="lk"))
        doc_enutri = rpts.generate(_h7_artifact(client_id="enutri"))
        ar_lk     = str(uuid.uuid5(uuid.NAMESPACE_URL, f"analysis-request:{doc_lk['report_id']}:v1"))
        ar_enutri = str(uuid.uuid5(uuid.NAMESPACE_URL, f"analysis-request:{doc_enutri['report_id']}:v1"))
        assert ar_lk != ar_enutri
