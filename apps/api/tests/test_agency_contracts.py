"""
Agency API v1.1 — contract tests (Etapa 1).

Tests validate:
1. All enums carry the exact values from PRD Section 5.
2. Every PRD example JSON parses cleanly through the Pydantic schema.
3. Invalid payloads raise ValidationError (missing required fields, wrong enums).
4. MetricRef validation logic (missing_refs, validate_refs_in_blocks).
5. Schema round-trip: parse → serialize → re-parse produces identical output.

Run from apps/api/:
    python -m pytest tests/test_agency_contracts.py -v
or:
    pip install pytest && pytest tests/test_agency_contracts.py -v
"""

from __future__ import annotations

import json
from datetime import date, datetime, timezone

import pytest
from pydantic import ValidationError

# ── Imports ───────────────────────────────────────────────────────────────────

from app.agency_api.v1.enums import (
    ActionEventType,
    AlertRuleSuggestionStatus,
    AlertStatus,
    BusinessModel,
    CertificationStatus,
    ChangeConfidence,
    ChangeLogSource,
    ChangeType,
    EvidenceLevel,
    JobStatus,
    LearningCandidateStatus,
    LearningScope,
    Level,
    MatchStatus,
    NarrativeStatus,
    RecommendationStatus,
    ReportType,
    ServiceStatus,
    SourceState,
    TargetStatus,
    ValueStatus,
    VisibilityScope,
)
from app.agency_api.v1.metric_refs import missing_refs, validate_refs_in_blocks
from app.agency_api.v1.schemas import (
    ActionEventCreate,
    ActionProposal,
    AlertFeedbackCreate,
    AlertOut,
    AlertRuleSuggestionDecision,
    ChangeCreate,
    CoreReportContractOut,
    DataHealthEntry,
    DataHealthOut,
    DiagnosisCreate,
    Hypothesis,
    LearningCandidateCreate,
    MetricContract,
    MetricRef,
    MetricValue,
    NarrativeBlock,
    NarrativeTransition,
    Period,
    RecommendationCreate,
    ReportNarrativeCreate,
    TextWithRefs,
    TruthVersions,
)

_NOW = datetime(2026, 10, 3, 12, 0, 0, tzinfo=timezone.utc)


# ═══════════════════════════════════════════════════════════════════════════════
# 1. Enum value tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestSourceStateEnum:
    def test_all_ten_values(self):
        expected = {
            "READY", "PARTIAL", "STALE", "NO_DATA", "MISSING",
            "ACCESS_MISSING", "PERMISSION_DENIED", "NOT_CONTRACTED",
            "NOT_APPLICABLE", "ERROR",
        }
        assert {m.value for m in SourceState} == expected

    def test_not_contracted_distinct_from_missing(self):
        assert SourceState.NOT_CONTRACTED != SourceState.MISSING

    def test_permission_denied_distinct_from_access_missing(self):
        assert SourceState.PERMISSION_DENIED != SourceState.ACCESS_MISSING


class TestValueStatusEnum:
    def test_all_seven_values(self):
        expected = {"OK", "PARTIAL", "STALE", "NO_DATA", "MISSING", "UNKNOWN", "NOT_APPLICABLE"}
        assert {m.value for m in ValueStatus} == expected

    def test_absence_is_not_zero(self):
        # NO_DATA, MISSING, UNKNOWN must all be distinct (never collapse to one)
        statuses = {ValueStatus.NO_DATA, ValueStatus.MISSING, ValueStatus.UNKNOWN}
        assert len(statuses) == 3


class TestBusinessModelEnum:
    def test_four_models(self):
        expected = {"ecommerce", "lead_generation", "local_lead_generation", "auction"}
        assert {m.value for m in BusinessModel} == expected


class TestChangeTypeEnum:
    def test_thirteen_change_types(self):
        expected = {
            "BUDGET", "TARGET_ROAS", "TARGET_CPA", "BID_STRATEGY",
            "CREATIVE_LAUNCH", "CREATIVE_PAUSE", "CAMPAIGN_LAUNCH", "CAMPAIGN_PAUSE",
            "AUDIENCE", "LANDING_PAGE", "OFFER", "TRACKING", "OTHER",
        }
        assert {m.value for m in ChangeType} == expected


class TestNarrativeStatusEnum:
    def test_five_states(self):
        expected = {"DRAFT", "READY_FOR_REVIEW", "APPROVED", "PUBLISHED", "SUPERSEDED"}
        assert {m.value for m in NarrativeStatus} == expected


# ═══════════════════════════════════════════════════════════════════════════════
# 2. PRD example JSON → schema round-trips
# ═══════════════════════════════════════════════════════════════════════════════

class TestMetricContractExample:
    """PRD §7 exact example."""

    _example = {
        "schema_version": "1.1",
        "client_id": "lk-sneakers",
        "business_model": "ecommerce",
        "timezone": "America/Sao_Paulo",
        "currency": "BRL",
        "period": {"start": "2026-09-26", "end": "2026-10-02"},
        "view": "live",
        "truth_versions": {"client": 7, "target": 3, "conversion_map": 2},
        "metrics": [
            {
                "metric_key": "revenue_business",
                "value": 84210.50,
                "unit": "BRL",
                "value_status": "OK",
                "certification_status": "PROVISIONAL",
                "snapshot_ids": ["snap_501"],
                "target": 90000,
                "target_status": "OK",
            },
            {
                "metric_key": "mer",
                "value": 5.4,
                "unit": "x",
                "value_status": "OK",
                "certification_status": "PROVISIONAL",
                "snapshot_ids": ["snap_502"],
            },
            {
                "metric_key": "roas_meta",
                "value": None,
                "value_status": "UNKNOWN",
                "reason_code": "SOURCE_PERMISSION_DENIED",
            },
            {
                "metric_key": "ga4_sessions",
                "value": None,
                "value_status": "STALE",
                "reason_code": "LAST_SUCCESS_49H",
            },
        ],
        "health": {
            "business": "READY",
            "google_ads": "READY",
            "meta_ads": "PERMISSION_DENIED",
            "ga4": "STALE",
            "conversion_map": "PARTIAL",
        },
    }

    def test_parses_clean(self):
        mc = MetricContract.model_validate(self._example)
        assert mc.client_id == "lk-sneakers"
        assert mc.business_model == BusinessModel.ECOMMERCE

    def test_null_value_with_unknown_status(self):
        mc = MetricContract.model_validate(self._example)
        roas = next(m for m in mc.metrics if m.metric_key == "roas_meta")
        assert roas.value is None
        assert roas.value_status == ValueStatus.UNKNOWN
        assert roas.reason_code == "SOURCE_PERMISSION_DENIED"

    def test_absence_not_coerced_to_zero(self):
        mc = MetricContract.model_validate(self._example)
        for m in mc.metrics:
            if m.value_status != ValueStatus.OK:
                assert m.value is None, f"{m.metric_key} has value {m.value} despite status {m.value_status}"

    def test_health_uses_source_state(self):
        mc = MetricContract.model_validate(self._example)
        assert mc.health["meta_ads"] == SourceState.PERMISSION_DENIED
        assert mc.health["ga4"] == SourceState.STALE

    def test_round_trip(self):
        mc1 = MetricContract.model_validate(self._example)
        mc2 = MetricContract.model_validate(json.loads(mc1.model_dump_json()))
        assert mc1.model_dump() == mc2.model_dump()


class TestDiagnosisExample:
    """PRD §6 diagnosis example."""

    _example = {
        "schema_version": "1.1",
        "alert_id": "alt_01J_stub",
        "facts": [
            {
                "statement": "Compras Meta caíram {{m1}} vs. baseline de 14 dias",
                "metric_refs": {
                    "m1": {
                        "snapshot_ids": ["snap_124"],
                        "baseline_snapshot_ids": ["snap_098"],
                        "presentation": "delta_pct",
                    }
                },
            }
        ],
        "localization": "Deterioração concentrada entre carrinho e checkout",
        "related_changes": ["chg_88"],
        "hypotheses": [
            {
                "statement": "Mudança de frete",
                "supporting_refs": [],
                "how_to_verify": "Confirmar com cliente",
            }
        ],
        "confidence": "MEDIUM",
        "do_not_conclude": ["Que o problema é o checkout sem dado da plataforma de e-commerce"],
        "data_limitations": ["GA4 STALE há 2 dias"],
        "visibility_scope": "AGENCY_ONLY",
    }

    def test_parses_clean(self):
        d = DiagnosisCreate.model_validate(self._example)
        assert d.confidence == Level.MEDIUM
        assert d.visibility_scope == VisibilityScope.AGENCY_ONLY

    def test_metric_ref_fields(self):
        d = DiagnosisCreate.model_validate(self._example)
        ref = d.facts[0].metric_refs["m1"]
        assert ref.snapshot_ids == ["snap_124"]
        assert ref.baseline_snapshot_ids == ["snap_098"]
        assert ref.presentation == "delta_pct"

    def test_default_visibility_agency_only(self):
        payload = dict(self._example)
        del payload["visibility_scope"]
        d = DiagnosisCreate.model_validate(payload)
        assert d.visibility_scope == VisibilityScope.AGENCY_ONLY


class TestRecommendationExample:
    """PRD §6 recommendation example."""

    _example = {
        "schema_version": "1.1",
        "diagnosis_id": "dia_01J_stub",
        "recommendation": "Reduzir budget do ADV+ Geral em 15% e reavaliar em 72h",
        "action_proposal": {
            "platform": "meta_ads",
            "platform_account_id": "act_123",
            "entity_type": "campaign",
            "entity_id": "120210000000",
            "entity_name_at_time": "ADV+ Geral",
            "change_type": "BUDGET",
            "before": 500,
            "after": 425,
            "unit": "BRL/dia",
        },
        "priority": "HIGH",
        "confidence": "MEDIUM",
        "risk": "LOW",
        "reversible": True,
        "expected_effect": "Conter CPA enquanto criativos são renovados",
        "review_window_days": 3,
        "requires_approval": True,
        "visibility_scope": "AGENCY_ONLY",
    }

    def test_parses_clean(self):
        r = RecommendationCreate.model_validate(self._example)
        assert r.priority == Level.HIGH
        assert r.risk == Level.LOW

    def test_action_proposal_uses_platform_ids(self):
        r = RecommendationCreate.model_validate(self._example)
        assert r.action_proposal is not None
        assert r.action_proposal.entity_id == "120210000000"
        assert r.action_proposal.change_type == ChangeType.BUDGET


class TestChangeLogExample:
    """PRD §6 change log example."""

    _example = {
        "schema_version": "1.1",
        "client_id": "lk-sneakers",
        "occurred_at": "2026-10-03T14:30:00-03:00",
        "channel": "google_ads",
        "platform_account_id": "123-456-7890",
        "entity_type": "campaign",
        "campaign_id": "20123456789",
        "adset_or_adgroup_id": None,
        "ad_id": None,
        "entity_name_at_time": "PMax Geral",
        "change_type": "TARGET_ROAS",
        "before": 400,
        "after": 500,
        "reason": "Campanha gastando acima do ritmo",
        "source": "HUMAN",
        "reported_by": "maicon",
        "confidence": "CONFIRMED",
        "linked_action_id": None,
    }

    def test_parses_clean(self):
        c = ChangeCreate.model_validate(self._example)
        assert c.change_type == ChangeType.TARGET_ROAS
        assert c.source == ChangeLogSource.HUMAN
        assert c.confidence == ChangeConfidence.CONFIRMED

    def test_before_after_preserved(self):
        c = ChangeCreate.model_validate(self._example)
        assert c.before == 400
        assert c.after == 500


class TestReportNarrativeExample:
    """PRD §6 narrative example — metric_refs via contract_path."""

    _example = {
        "schema_version": "1.1",
        "report_contract_id": "rpc_2026_09_lk",
        "blocks": [
            {
                "section": "resultado",
                "statement": "Receita do mês fechou em {{m1}}, {{m2}} da meta",
                "metric_refs": {
                    "m1": {
                        "contract_path": "metrics.revenue_business",
                        "presentation": "currency",
                    },
                    "m2": {
                        "contract_path": "metrics.revenue_business.target_attainment",
                        "presentation": "pct",
                    },
                },
            }
        ],
        "visibility_scope": "AGENCY_ONLY",
    }

    def test_parses_clean(self):
        n = ReportNarrativeCreate.model_validate(self._example)
        assert n.report_contract_id == "rpc_2026_09_lk"
        assert len(n.blocks) == 1

    def test_contract_path_refs(self):
        n = ReportNarrativeCreate.model_validate(self._example)
        ref_m1 = n.blocks[0].metric_refs["m1"]
        assert ref_m1.contract_path == "metrics.revenue_business"
        assert ref_m1.presentation == "currency"


# ═══════════════════════════════════════════════════════════════════════════════
# 3. Validation errors — invalid payloads must fail
# ═══════════════════════════════════════════════════════════════════════════════

class TestValidationErrors:
    def test_diagnosis_missing_confidence_fails(self):
        with pytest.raises(ValidationError):
            DiagnosisCreate.model_validate({
                "schema_version": "1.1",
                "alert_id": "alt_01",
                "facts": [],
                # confidence is required — omitted
            })

    def test_unknown_value_status_fails(self):
        with pytest.raises(ValidationError):
            MetricValue.model_validate({
                "metric_key": "revenue",
                "value": 100.0,
                "value_status": "AVAILABLE",  # wrong — legacy enum value
            })

    def test_unknown_source_state_fails(self):
        with pytest.raises(ValidationError):
            DataHealthEntry.model_validate({
                "domain": "meta_ads",
                "source_state": "healthy",  # wrong — legacy integrations_health value
                "checked_at": "2026-10-03T12:00:00Z",
            })

    def test_unknown_business_model_fails(self):
        with pytest.raises(ValidationError):
            MetricContract.model_validate({
                "client_id": "x",
                "business_model": "e_commerce",  # wrong spelling
                "timezone": "UTC",
                "currency": "BRL",
                "period": {"start": "2026-10-01", "end": "2026-10-07"},
                "view": "live",
                "truth_versions": {"client": 1, "target": 1, "conversion_map": 1},
                "metrics": [],
                "health": {},
            })

    def test_recommendation_missing_priority_fails(self):
        with pytest.raises(ValidationError):
            RecommendationCreate.model_validate({
                "schema_version": "1.1",
                "diagnosis_id": "dia_01",
                "recommendation": "Reduce budget",
                # priority, confidence, risk, reversible required — omitted
            })

    def test_change_missing_source_fails(self):
        with pytest.raises(ValidationError):
            ChangeCreate.model_validate({
                "schema_version": "1.1",
                "client_id": "lk-sneakers",
                "occurred_at": "2026-10-03T14:00:00Z",
                "channel": "google_ads",
                "platform_account_id": "123",
                "entity_type": "campaign",
                "entity_name_at_time": "PMax",
                "change_type": "BUDGET",
                # source is required — omitted
            })


# ═══════════════════════════════════════════════════════════════════════════════
# 4. metric_refs utility
# ═══════════════════════════════════════════════════════════════════════════════

class TestMetricRefs:
    def test_no_missing_refs(self):
        errors = missing_refs(
            "CPA subiu {{m1}} de {{m2}} para {{m3}}",
            {"m1": {"presentation": "delta_pct"}, "m2": {"presentation": "currency"}, "m3": {"presentation": "currency"}},
        )
        assert errors == []

    def test_detects_missing_ref(self):
        errors = missing_refs(
            "CPA subiu {{m1}}",
            {},
        )
        assert "m1" in errors

    def test_no_placeholders_no_errors(self):
        errors = missing_refs("Texto sem placeholder", {"m1": {}})
        assert errors == []

    def test_validate_refs_in_blocks_valid(self):
        blocks = [
            {
                "statement": "Receita {{m1}}",
                "metric_refs": {"m1": {"presentation": "currency"}},
            }
        ]
        assert validate_refs_in_blocks(blocks) == []

    def test_validate_refs_in_blocks_invalid(self):
        blocks = [
            {
                "statement": "Receita {{m1}} e ROAS {{m2}}",
                "metric_refs": {"m1": {"presentation": "currency"}},  # m2 missing
            }
        ]
        errors = validate_refs_in_blocks(blocks)
        assert len(errors) == 1
        assert "m2" in errors[0]


# ═══════════════════════════════════════════════════════════════════════════════
# 5. Data health — semantic separation tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestDataHealthSemantics:
    """Validate that source_state and value_status carry distinct meanings."""

    def test_not_contracted_differs_from_no_data(self):
        assert SourceState.NOT_CONTRACTED != SourceState.NO_DATA

    def test_permission_denied_differs_from_access_missing(self):
        assert SourceState.PERMISSION_DENIED != SourceState.ACCESS_MISSING

    def test_value_status_stale_vs_source_state_stale(self):
        # Both enums have STALE but they are different types
        assert ValueStatus.STALE == "STALE"
        assert SourceState.STALE == "STALE"
        # They share the same string value by design (PRD alignment)
        # but they are distinct Python types
        assert type(ValueStatus.STALE) is not type(SourceState.STALE)

    def test_data_health_entry_accepts_all_source_states(self):
        for state in SourceState:
            entry = DataHealthEntry.model_validate({
                "domain": "google_ads",
                "source_state": state.value,
                "checked_at": "2026-10-03T12:00:00Z",
            })
            assert entry.source_state == state


# ═══════════════════════════════════════════════════════════════════════════════
# 6. schema_version present in all contracts
# ═══════════════════════════════════════════════════════════════════════════════

class TestSchemaVersion:
    def test_metric_contract_has_version(self):
        mc = MetricContract.model_validate({
            "client_id": "lk-sneakers",
            "business_model": "ecommerce",
            "timezone": "America/Sao_Paulo",
            "currency": "BRL",
            "period": {"start": "2026-10-01", "end": "2026-10-07"},
            "view": "live",
            "truth_versions": {"client": 1, "target": 1, "conversion_map": 1},
            "metrics": [],
            "health": {},
        })
        assert mc.schema_version == "1.1"

    def test_diagnosis_has_version(self):
        d = DiagnosisCreate.model_validate({
            "alert_id": "alt_01",
            "facts": [],
            "confidence": "MEDIUM",
        })
        assert d.schema_version == "1.1"

    def test_narrative_has_version(self):
        n = ReportNarrativeCreate.model_validate({
            "report_contract_id": "rpc_01",
            "blocks": [],
        })
        assert n.schema_version == "1.1"

    def test_change_has_version(self):
        c = ChangeCreate.model_validate({
            "client_id": "lk-sneakers",
            "occurred_at": "2026-10-03T12:00:00Z",
            "channel": "google_ads",
            "platform_account_id": "123",
            "entity_type": "campaign",
            "entity_name_at_time": "PMax",
            "change_type": "BUDGET",
            "source": "HUMAN",
        })
        assert c.schema_version == "1.1"


# ═══════════════════════════════════════════════════════════════════════════════
# 7. Legacy MetricStatus → v1.1 mapping validation
# ═══════════════════════════════════════════════════════════════════════════════

class TestLegacyMappingContract:
    """
    Ensures the v1.1 enums can represent all states the legacy MetricStatus
    carried (from apps/web/lib/agency-os.ts).
    """

    _LEGACY_TO_V1 = {
        "available":        (ValueStatus.OK, None),
        "access_missing":   (ValueStatus.UNKNOWN, "SOURCE_ACCESS_MISSING"),   # source_state promotes to value_status
        "permission_denied":(ValueStatus.UNKNOWN, "SOURCE_PERMISSION_DENIED"),
        "not_contracted":   (ValueStatus.NOT_APPLICABLE, "SOURCE_NOT_CONTRACTED"),
        "no_data":          (ValueStatus.NO_DATA, None),
        "unknown":          (ValueStatus.UNKNOWN, None),
    }

    def test_all_legacy_states_have_v1_mapping(self):
        for legacy, (v_status, reason) in self._LEGACY_TO_V1.items():
            assert v_status in ValueStatus, f"ValueStatus missing {v_status} for legacy '{legacy}'"
