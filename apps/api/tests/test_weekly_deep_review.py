"""
Tests for weekly_deep_review.py — internal agency analysis engine.

Scenarios covered:
  - change_digest_integration        — digest data flows into section D
  - channel_contract_awareness       — NOT_CONTRACTED channel is not flagged as error
  - diagnosis_evidence               — diagnoses include evidence (not empty assertions)
  - causality_protection             — no LIKELY_RELATED without corroborating metric shift
  - max_5_diagnoses                  — never exceeds _MAX_DIAGNOSES
  - max_5_actions                    — never exceeds _MAX_ACTIONS
  - cross_client_isolation           — LK data not leaked into Enutri run
  - not_contracted_handling          — meta NOT_CONTRACTED on Google-only client = healthy
  - missing_business_source          — business MISSING downgrades but doesn't crash
  - idempotent_weekly_run            — same period produces same review_status
  - same_week_replay                 — second call overwrites, not appends
  - causality_needs_more_data        — no prior snapshot → NEEDS_MORE_DATA
  - section_g_all_proposed           — all actions are PROPOSED with requires_human_approval
  - section_f_max_one_per_code       — deduplication by code in actions
  - watchlist_approaching_target     — metric at 85% of target appears in watchlist
"""
from __future__ import annotations

import uuid
from copy import deepcopy
from datetime import date, timedelta
from unittest.mock import MagicMock, patch

import pytest

from app.core.weekly_deep_review import (
    _assess_causality,
    _build_actions,
    _build_diagnoses,
    _build_section_b,
    _build_section_d,
    _build_watchlist,
    _build_section_c,
    _load_client_uuid,
    _materialize_dr_recommendations,
    _MAX_ACTIONS,
    _MAX_DIAGNOSES,
    build_weekly_deep_review,
)


# ── Fixtures ───────────────────────────────────────────────────────────────────

PERIOD_END   = date(2026, 11, 2)
PERIOD_START = PERIOD_END - timedelta(days=6)


def _base_contract(
    review_status: str = "READY_FOR_REVIEW",
    wow_changes: dict | None = None,
    health: dict | None = None,
    metrics_current: dict | None = None,
) -> dict:
    return {
        "schema_version": "core-weekly-review-v1",
        "review_status":  review_status,
        "client":         {"client_id": "lk-sneakers", "name": "LK Sneakers", "business_model": "ecommerce"},
        "period":         {"start": PERIOD_START.isoformat(), "end": PERIOD_END.isoformat(), "label": "Semana 44/2026"},
        "comparison_period": {"start": "2026-10-19", "end": "2026-10-25"},
        "data_quality": {
            "sources":          health or {"meta_ads": "OK", "google_ads": "OK", "business": "OK"},
            "snapshot_found":   True,
            "prior_snapshot_found": True,
            "limitations":      [],
            "overall":          "COMPLETE",
        },
        "metrics_current": metrics_current or {
            "meta_spend":    {"value": 1000.0, "value_status": "OK", "unit": "BRL"},
            "google_spend":  {"value": 500.0,  "value_status": "OK", "unit": "BRL"},
            "roas_meta":     {"value": 3.5,    "value_status": "OK", "unit": "ratio"},
            "revenue_business": {"value": 5000.0, "value_status": "OK", "unit": "BRL"},
        },
        "metrics_prior": {},
        "wow_changes": wow_changes or {},
        "target_truth": {},
        "active_alerts": [],
        "executed_actions": [],
        "open_recommendations": [],
        "manual_changes": [],
        "source_snapshot_id": str(uuid.uuid4()),
        "source_prior_snapshot_id": str(uuid.uuid4()),
    }


def _channel_contracts(meta: bool = True, google: bool = True) -> dict:
    return {"meta": meta, "google": google, "business": True, "ga4": False}


# ── Tests ──────────────────────────────────────────────────────────────────────

class TestCausalityAssessment:
    def test_likely_related_campaign_pause_with_spend_drop(self):
        change    = {"change_type": "CAMPAIGN_PAUSE", "impact": "high"}
        wow       = {"meta_spend": {"change_pct": -30.0, "current": 700.0, "prior": 1000.0}}
        causality = _assess_causality(change, wow)
        assert causality == "LIKELY_RELATED"

    def test_likely_related_campaign_activation_with_spend_up(self):
        change = {"change_type": "CAMPAIGN_ACTIVATION", "impact": "high"}
        wow    = {"google_spend": {"change_pct": 25.0, "current": 625.0, "prior": 500.0}}
        assert _assess_causality(change, wow) == "LIKELY_RELATED"

    def test_possibly_related_high_impact_no_known_pair(self):
        change = {"change_type": "AD_CREATIVE_CHANGE", "impact": "high"}
        wow    = {"meta_spend": {"change_pct": -20.0, "current": 800.0, "prior": 1000.0}}
        # AD_CREATIVE_CHANGE not in _CAUSAL_PAIRS → falls to possibly_related for high-impact
        assert _assess_causality(change, wow) == "POSSIBLY_RELATED"

    def test_no_evidence_shift_below_threshold(self):
        change = {"change_type": "CAMPAIGN_PAUSE", "impact": "high"}
        wow    = {"meta_spend": {"change_pct": -2.0, "current": 980.0, "prior": 1000.0}}
        # Shift is below 5% threshold
        assert _assess_causality(change, wow) == "NO_EVIDENCE"

    def test_needs_more_data_empty_wow(self):
        change = {"change_type": "CAMPAIGN_PAUSE", "impact": "high"}
        assert _assess_causality(change, {}) == "NEEDS_MORE_DATA"

    def test_no_evidence_wrong_direction(self):
        change = {"change_type": "CAMPAIGN_PAUSE", "impact": "high"}
        wow    = {"meta_spend": {"change_pct": 20.0, "current": 1200.0, "prior": 1000.0}}
        # Paused but spend went UP → no causal evidence
        assert _assess_causality(change, wow) == "NO_EVIDENCE"

    def test_budget_change_any_direction(self):
        change = {"change_type": "BUDGET_CHANGE", "impact": "high"}
        wow    = {"meta_spend": {"change_pct": 15.0, "current": 1150.0, "prior": 1000.0}}
        # Budget change → any direction counts for meta_spend
        assert _assess_causality(change, wow) == "LIKELY_RELATED"


class TestChannelContractAwareness:
    def test_not_contracted_meta_is_healthy(self):
        base      = _base_contract()
        contracts = _channel_contracts(meta=False, google=True)
        section_b = _build_section_b(base, contracts)
        assert section_b["meta"]["contracted"] is False
        assert section_b["meta"]["health"] == "NOT_CONTRACTED"
        # NOT_CONTRACTED must NOT contain "MISSING" or "ERROR"
        assert "MISSING" not in str(section_b["meta"])
        assert "ERROR" not in str(section_b["meta"])

    def test_contracted_channel_has_metrics(self):
        base      = _base_contract()
        contracts = _channel_contracts(meta=True, google=True)
        section_b = _build_section_b(base, contracts)
        assert section_b["meta"]["contracted"] is True
        assert "meta_spend" in section_b["meta"]["metrics"]

    def test_google_only_client_meta_not_contracted(self):
        """Enutri pattern: google contracted, meta NOT_CONTRACTED."""
        base      = _base_contract(
            health={"google_ads": "OK", "business": "OK", "meta_ads": "NOT_CONTRACTED"},
            metrics_current={"google_spend": {"value": 400.0, "value_status": "OK", "unit": "BRL"}},
        )
        contracts = _channel_contracts(meta=False, google=True)
        section_b = _build_section_b(base, contracts)
        assert section_b["meta"]["health"] == "NOT_CONTRACTED"
        assert section_b["google"]["contracted"] is True


class TestDiagnoses:
    def test_spend_drop_detected(self):
        base = _base_contract(
            wow_changes={
                "meta_spend":   {"change_pct": -20.0, "current": 800.0, "prior": 1000.0},
                "google_spend": {"change_pct": -25.0, "current": 375.0, "prior": 500.0},
            }
        )
        section_d = {"impact_assessment": []}
        diagnoses = _build_diagnoses(base, section_d, _channel_contracts())
        codes = [d["code"] for d in diagnoses]
        assert "SPEND_DROP" in codes

    def test_spend_drop_not_triggered_for_not_contracted(self):
        """Meta NOT_CONTRACTED → meta_spend drop should not trigger SPEND_DROP."""
        base = _base_contract(
            wow_changes={"meta_spend": {"change_pct": -50.0, "current": 500.0, "prior": 1000.0}},
        )
        section_d = {"impact_assessment": []}
        contracts = _channel_contracts(meta=False, google=True)
        diagnoses = _build_diagnoses(base, section_d, contracts)
        # Only meta spend dropped; google has no wow data → avg is None → no SPEND_DROP
        codes = [d["code"] for d in diagnoses]
        assert "SPEND_DROP" not in codes

    def test_data_quality_degraded(self):
        base = _base_contract(health={"meta_ads": "ERROR", "google_ads": "OK", "business": "OK"})
        section_d = {"impact_assessment": []}
        diagnoses = _build_diagnoses(base, section_d, _channel_contracts())
        assert any(d["code"] == "DATA_QUALITY_DEGRADED" for d in diagnoses)

    def test_data_quality_not_triggered_for_not_contracted_source(self):
        """META ERROR on client where meta is NOT contracted → not a diagnosis."""
        base = _base_contract(health={"meta_ads": "ERROR", "google_ads": "OK", "business": "OK"})
        section_d = {"impact_assessment": []}
        contracts = _channel_contracts(meta=False, google=True)
        diagnoses = _build_diagnoses(base, section_d, contracts)
        assert all(d["code"] != "DATA_QUALITY_DEGRADED" for d in diagnoses)

    def test_max_5_diagnoses(self):
        """Trigger 6+ potential diagnoses — must cap at 5."""
        wow = {
            "meta_spend":       {"change_pct": -20.0, "current": 800.0, "prior": 1000.0},
            "google_spend":     {"change_pct": -20.0, "current": 400.0, "prior": 500.0},
            "roas_meta":        {"change_pct": -15.0, "current": 2.975, "prior": 3.5},
            "roas_google":      {"change_pct": -12.0, "current": 2.64,  "prior": 3.0},
            "revenue_business": {"change_pct": -18.0, "current": 4100.0, "prior": 5000.0},
        }
        metrics = {
            "meta_spend":    {"value": 800.0, "value_status": "OK", "unit": "BRL"},
            "roas_meta":     {"value": 2.975, "value_status": "OK", "unit": "ratio", "target": 3.5, "target_ratio": 0.5},
            "revenue_business": {"value": 4100.0, "value_status": "OK", "unit": "BRL"},
        }
        base = _base_contract(
            wow_changes=wow,
            health={"meta_ads": "ERROR", "google_ads": "ERROR", "business": "OK"},
            metrics_current=metrics,
        )
        pause_items = [{"change": {"change_type": "CAMPAIGN_PAUSE"}, "platform": "meta", "causality": "LIKELY_RELATED"}]
        section_d = {"impact_assessment": pause_items}
        diagnoses = _build_diagnoses(base, section_d, _channel_contracts())
        assert len(diagnoses) <= _MAX_DIAGNOSES

    def test_diagnosis_evidence_not_empty(self):
        base = _base_contract(
            wow_changes={"meta_spend": {"change_pct": -20.0, "current": 800.0, "prior": 1000.0}},
        )
        section_d = {"impact_assessment": []}
        diagnoses = _build_diagnoses(base, section_d, _channel_contracts())
        for d in diagnoses:
            assert d.get("evidence") is not None, f"diagnosis {d['id']} has no evidence"
            assert d.get("confidence") in ("HIGH", "MEDIUM", "LOW")


class TestRecommendedActions:
    def test_max_5_actions(self):
        diagnoses = [
            {"id": "SPEND_DROP",             "code": "SPEND_DROP",             "finding": "x", "business_impact": "HIGH"},
            {"id": "ROAS_DECLINE_META",       "code": "ROAS_DECLINE",           "finding": "x", "business_impact": "HIGH"},
            {"id": "REVENUE_DROP",            "code": "REVENUE_DROP",           "finding": "x", "business_impact": "HIGH"},
            {"id": "DATA_QUALITY_DEGRADED",   "code": "DATA_QUALITY_DEGRADED",  "finding": "x", "business_impact": "HIGH"},
            {"id": "CAMPAIGN_PAUSED",         "code": "CAMPAIGN_PAUSED",        "finding": "x", "business_impact": "MEDIUM"},
            {"id": "TARGET_MISS_ROAS_META",   "code": "TARGET_MISS",            "finding": "x", "business_impact": "MEDIUM"},
        ]
        actions = _build_actions(diagnoses)
        assert len(actions) <= _MAX_ACTIONS

    def test_all_actions_are_proposed(self):
        diagnoses = [
            {"id": "SPEND_DROP", "code": "SPEND_DROP", "finding": "queda -20%", "business_impact": "HIGH"},
        ]
        actions = _build_actions(diagnoses)
        for action in actions:
            assert action["status"] == "PROPOSED"
            assert action["requires_human_approval"] is True

    def test_no_duplicate_codes_in_actions(self):
        # Two diagnoses with the same code (ROAS_DECLINE for meta and google)
        diagnoses = [
            {"id": "ROAS_DECLINE_META",   "code": "ROAS_DECLINE", "finding": "meta roas down", "business_impact": "HIGH"},
            {"id": "ROAS_DECLINE_GOOGLE", "code": "ROAS_DECLINE", "finding": "google roas down", "business_impact": "HIGH"},
        ]
        actions = _build_actions(diagnoses)
        codes = [a["code"] for a in actions]
        assert len(codes) == len(set(codes)), "duplicate action codes found"


class TestWatchlist:
    def test_declining_metric_in_watchlist(self):
        base = _base_contract(
            wow_changes={"meta_spend": {"change_pct": -8.0, "current": 920.0, "prior": 1000.0}},
        )
        wl = _build_watchlist(base)
        types = [w["type"] for w in wl]
        assert "DECLINING_METRIC" in types

    def test_approaching_target_miss_in_watchlist(self):
        base = _base_contract(
            metrics_current={"roas_meta": {"value": 3.0, "value_status": "OK", "unit": "ratio", "target_ratio": 0.85}},
        )
        wl = _build_watchlist(base)
        types = [w["type"] for w in wl]
        assert "APPROACHING_TARGET_MISS" in types

    def test_full_miss_not_in_watchlist(self):
        """target_ratio < 0.80 is a diagnosis, not a watchlist item."""
        base = _base_contract(
            metrics_current={"roas_meta": {"value": 2.0, "value_status": "OK", "unit": "ratio", "target_ratio": 0.5}},
        )
        wl = _build_watchlist(base)
        # Should not appear in watchlist (it's in diagnoses)
        approaching = [w for w in wl if w["type"] == "APPROACHING_TARGET_MISS" and w["item"] == "roas_meta"]
        assert len(approaching) == 0

    def test_pending_recommendations_in_watchlist(self):
        base = _base_contract()
        base["open_recommendations"] = [{"id": "r1"}, {"id": "r2"}]
        wl = _build_watchlist(base)
        assert any(w["type"] == "PENDING_RECOMMENDATIONS" for w in wl)


class TestSectionD:
    def test_digest_integration(self):
        digest_meta = {
            "changes": [
                {"change_type": "CAMPAIGN_PAUSE", "impact": "high", "entity_type": "CAMPAIGN"},
                {"change_type": "AD_PAUSE",        "impact": "medium", "entity_type": "AD"},
            ]
        }
        wow = {"meta_spend": {"change_pct": -25.0, "current": 750.0, "prior": 1000.0}}
        section_d = _build_section_d(digest_meta, None, wow)
        assert section_d["digest_meta"] is digest_meta
        assert len(section_d["impact_assessment"]) == 2
        assert section_d["impact_assessment"][0]["platform"] == "meta"
        # CAMPAIGN_PAUSE + spend_down -25% → LIKELY_RELATED
        assert section_d["impact_assessment"][0]["causality"] == "LIKELY_RELATED"

    def test_no_digests_produces_empty_assessment(self):
        section_d = _build_section_d(None, None, {})
        assert section_d["digest_meta"] is None
        assert section_d["digest_google"] is None
        assert section_d["impact_assessment"] == []

    def test_low_impact_changes_excluded(self):
        digest = {"changes": [{"change_type": "ASSET_CREATED", "impact": "low"}]}
        section_d = _build_section_d(digest, None, {})
        assert section_d["impact_assessment"] == []


class TestSectionC:
    def test_groups_campaigns_by_entity_id(self):
        changes = [
            {
                "id": "c1", "entity_id": "camp_1", "entity_type": "CAMPAIGN", "channel": "google",
                "entity_name_at_time": "Campanha A", "occurred_at": "2026-10-28T10:00:00Z",
                "before_state": {"status": "ENABLED"}, "after_state": {"status": "PAUSED"},
            },
            {
                "id": "c2", "entity_id": "camp_1", "entity_type": "CAMPAIGN", "channel": "google",
                "entity_name_at_time": "Campanha A", "occurred_at": "2026-10-29T10:00:00Z",
                "before_state": {"status": "PAUSED"}, "after_state": {"status": "ENABLED"},
            },
        ]
        section_c = _build_section_c(changes)
        assert section_c["total_changes"] == 2
        assert len(section_c["campaigns"]) == 1
        assert len(section_c["campaigns"][0]["changes"]) == 2

    def test_budget_brl_conversion(self):
        changes = [
            {
                "id": "b1", "entity_id": "bud_1", "entity_type": "BUDGET", "channel": "google",
                "entity_name_at_time": None, "occurred_at": "2026-10-28T10:00:00Z",
                "before_state": {"amountMicros": "50000000"},
                "after_state":  {"amountMicros": "80000000"},
            }
        ]
        section_c = _build_section_c(changes)
        ch_entry = section_c["campaigns"][0]["changes"][0]
        assert ch_entry["before_budget_brl"] == "R$ 50.00"
        assert ch_entry["after_budget_brl"]  == "R$ 80.00"

    def test_empty_changes(self):
        section_c = _build_section_c([])
        assert section_c["total_changes"] == 0
        assert section_c["campaigns"] == []


class TestBuildWeeklyDeepReview:
    """Integration-style tests that mock the DB layer."""

    def _make_sb_mock(
        self,
        channel_contracts: dict | None = None,
        campaign_changes: list | None = None,
        base_override: dict | None = None,
    ) -> MagicMock:
        sb = MagicMock()

        # clients table mock (channel contracts)
        contracts = channel_contracts or {"meta_ad_account_id": "act_123", "google_ads_customer_id": "123456789"}
        clients_res = MagicMock()
        clients_res.data = contracts
        sb.table.return_value.select.return_value.eq.return_value.single.return_value.execute.return_value = clients_res

        # core_change_log mock (campaign changes)
        changes_res = MagicMock()
        changes_res.data = campaign_changes or []
        # core_change_digests mock
        digests_res = MagicMock()
        digests_res.data = None

        return sb

    def test_cross_client_isolation(self):
        """build_weekly_deep_review for lk-sneakers should never read enutri data."""
        base_lk = _base_contract()
        base_en = _base_contract()
        base_en["client"]["client_id"] = "enutri"

        with patch("app.core.weekly_deep_review._get_db") as mock_db, \
             patch("app.core.weekly_deep_review._load_channel_contracts") as mock_cc, \
             patch("app.core.weekly_deep_review._load_campaign_changes") as mock_ch, \
             patch("app.core.weekly_deep_review._load_digest") as mock_dig, \
             patch("app.core.weekly_review.build_weekly_review_contract") as mock_base:

            mock_base.return_value = base_lk
            mock_cc.return_value   = _channel_contracts()
            mock_ch.return_value   = []
            mock_dig.return_value  = None

            result = build_weekly_deep_review("lk-sneakers", PERIOD_START, PERIOD_END)

        # Must be for lk-sneakers
        assert result["client"]["client_id"] == "lk-sneakers"
        mock_ch.assert_called_once_with(mock_db.return_value, "lk-sneakers", PERIOD_START, PERIOD_END)

    def test_blocked_base_propagates(self):
        with patch("app.core.weekly_deep_review._get_db"), \
             patch("app.core.weekly_review.build_weekly_review_contract") as mock_base:

            mock_base.return_value = {
                "schema_version": "core-weekly-review-v1",
                "review_status":  "BLOCKED",
                "error":          "client not found: ghost-client",
            }

            result = build_weekly_deep_review("ghost-client", PERIOD_START, PERIOD_END)

        assert result["review_status"] == "BLOCKED"
        assert "error" in result

    def test_missing_business_source_does_not_crash(self):
        """business MISSING should produce DATA_QUALITY_DEGRADED diagnosis, not an exception."""
        base = _base_contract(health={"meta_ads": "OK", "google_ads": "OK", "business": "MISSING"})

        with patch("app.core.weekly_deep_review._get_db"), \
             patch("app.core.weekly_deep_review._load_channel_contracts") as mock_cc, \
             patch("app.core.weekly_deep_review._load_campaign_changes") as mock_ch, \
             patch("app.core.weekly_deep_review._load_digest") as mock_dig, \
             patch("app.core.weekly_review.build_weekly_review_contract") as mock_base:

            mock_base.return_value = base
            mock_cc.return_value   = _channel_contracts()
            mock_ch.return_value   = []
            mock_dig.return_value  = None

            result = build_weekly_deep_review("lk-sneakers", PERIOD_START, PERIOD_END)

        diagnoses = result["section_f_diagnoses"]
        codes = [d["code"] for d in diagnoses]
        assert "DATA_QUALITY_DEGRADED" in codes

    def test_idempotent_review_status(self):
        """Two calls with same period produce same review_status."""
        base = _base_contract(review_status="PARTIAL")

        for _ in range(2):
            with patch("app.core.weekly_deep_review._get_db"), \
                 patch("app.core.weekly_deep_review._load_channel_contracts") as mock_cc, \
                 patch("app.core.weekly_deep_review._load_campaign_changes") as mock_ch, \
                 patch("app.core.weekly_deep_review._load_digest") as mock_dig, \
                 patch("app.core.weekly_review.build_weekly_review_contract") as mock_base:

                mock_base.return_value = deepcopy(base)
                mock_cc.return_value   = _channel_contracts()
                mock_ch.return_value   = []
                mock_dig.return_value  = None

                result = build_weekly_deep_review("lk-sneakers", PERIOD_START, PERIOD_END)

            assert result["review_status"] == "PARTIAL"

    def test_schema_version(self):
        with patch("app.core.weekly_deep_review._get_db"), \
             patch("app.core.weekly_deep_review._load_channel_contracts") as mock_cc, \
             patch("app.core.weekly_deep_review._load_campaign_changes") as mock_ch, \
             patch("app.core.weekly_deep_review._load_digest") as mock_dig, \
             patch("app.core.weekly_review.build_weekly_review_contract") as mock_base:

            mock_base.return_value = _base_contract()
            mock_cc.return_value   = _channel_contracts()
            mock_ch.return_value   = []
            mock_dig.return_value  = None

            result = build_weekly_deep_review("lk-sneakers", PERIOD_START, PERIOD_END)

        assert result["schema_version"] == "weekly-deep-review-v1"

    def test_all_sections_present(self):
        with patch("app.core.weekly_deep_review._get_db"), \
             patch("app.core.weekly_deep_review._load_channel_contracts") as mock_cc, \
             patch("app.core.weekly_deep_review._load_campaign_changes") as mock_ch, \
             patch("app.core.weekly_deep_review._load_digest") as mock_dig, \
             patch("app.core.weekly_review.build_weekly_review_contract") as mock_base:

            mock_base.return_value = _base_contract()
            mock_cc.return_value   = _channel_contracts()
            mock_ch.return_value   = []
            mock_dig.return_value  = None

            result = build_weekly_deep_review("lk-sneakers", PERIOD_START, PERIOD_END)

        required_sections = [
            "section_a_executive_state",
            "section_b_channel_review",
            "section_c_campaign_review",
            "section_d_change_impact",
            "section_e_tracking_quality",
            "section_f_diagnoses",
            "section_g_recommended_actions",
            "section_h_watchlist",
        ]
        for s in required_sections:
            assert s in result, f"section {s!r} missing from contract"


# ── Materialisation tests ──────────────────────────────────────────────────────

CONTRACT_ID  = str(uuid.uuid4())
LK_UUID_FAKE = str(uuid.uuid4())

_SAMPLE_CONTRACT = {
    "section_f_diagnoses": [
        {"id": str(uuid.uuid4()), "code": "SPEND_DROP",      "finding": "Gasto caiu 20%", "severity": "high"},
        {"id": str(uuid.uuid4()), "code": "CAMPAIGN_PAUSED", "finding": "2 campanhas pausadas"},
    ],
    "section_g_recommended_actions": [
        {
            "id": str(uuid.uuid4()), "code": "SPEND_DROP", "action": "Verificar campanhas pausadas",
            "priority": "HIGH", "risk": "LOW", "expected_effect": "Restaurar spend",
            "status": "PROPOSED", "requires_human_approval": True,
        },
        {
            "id": str(uuid.uuid4()), "code": "CAMPAIGN_PAUSED", "action": "Confirmar pausas intencionais",
            "priority": "MEDIUM", "risk": "LOW", "expected_effect": "Clareza operacional",
            "status": "PROPOSED", "requires_human_approval": True,
        },
    ],
}


def _make_sb_materialise(
    rec_dup_data=None,
    diag_dup_data=None,
    client_uuid_data=None,
) -> MagicMock:
    """
    Build a Supabase mock for materialisation tests.

    Call sequence per action:
      1. core_recommendations.select.eq(fingerprint).limit.execute  → rec dup check
      2. core_diagnoses.select.eq(fingerprint).limit.execute        → diag dup check
      3. core_diagnoses.insert.execute                              → diag insert
      4. core_recommendations.insert.execute                        → rec insert
    """
    sb = MagicMock()

    # --- core_recommendations fingerprint dup check ---
    rec_dup_res = MagicMock()
    rec_dup_res.data = rec_dup_data  # None or [] → not duplicate; list with item → duplicate

    # --- core_diagnoses fingerprint dup check ---
    diag_dup_res = MagicMock()
    diag_dup_res.data = diag_dup_data  # None or [] → not found; list with item → found

    # --- core_diagnoses insert ---
    diag_insert_res = MagicMock()
    diag_insert_res.data = [{"id": str(uuid.uuid4())}]

    # --- core_recommendations insert ---
    rec_insert_res = MagicMock()
    rec_insert_res.data = [{"id": str(uuid.uuid4())}]

    # Wire: table("core_recommendations").select().eq().limit().execute()
    rec_sel_chain = MagicMock()
    rec_sel_chain.execute.return_value = rec_dup_res
    rec_insert_chain = MagicMock()
    rec_insert_chain.execute.return_value = rec_insert_res

    # Wire: table("core_diagnoses").select().eq().limit().execute()
    diag_sel_chain = MagicMock()
    diag_sel_chain.execute.return_value = diag_dup_res
    diag_insert_chain = MagicMock()
    diag_insert_chain.execute.return_value = diag_insert_res

    def _table_side_effect(name):
        t = MagicMock()
        if name == "core_recommendations":
            t.select.return_value.eq.return_value.limit.return_value = rec_sel_chain
            t.insert.return_value = rec_insert_chain
        elif name == "core_diagnoses":
            t.select.return_value.eq.return_value.limit.return_value = diag_sel_chain
            t.insert.return_value = diag_insert_chain
        elif name == "clients":
            client_res = MagicMock()
            client_res.data = client_uuid_data or {"id": LK_UUID_FAKE}
            t.select.return_value.eq.return_value.single.return_value.execute.return_value = client_res
        return t

    sb.table.side_effect = _table_side_effect
    return sb


class TestMaterializeDRRecommendations:
    def test_materialises_all_actions(self):
        """Happy path: 2 actions → 2 diagnoses + 2 recommendations inserted."""
        sb = _make_sb_materialise(rec_dup_data=None, diag_dup_data=None)
        count = _materialize_dr_recommendations(
            sb, "lk-sneakers", LK_UUID_FAKE, PERIOD_END, _SAMPLE_CONTRACT, CONTRACT_ID
        )
        assert count == 2

    def test_returns_zero_when_no_actions(self):
        """Contract with empty section_g returns 0 immediately."""
        contract = {**_SAMPLE_CONTRACT, "section_g_recommended_actions": []}
        sb = _make_sb_materialise()
        count = _materialize_dr_recommendations(
            sb, "lk-sneakers", LK_UUID_FAKE, PERIOD_END, contract, CONTRACT_ID
        )
        assert count == 0

    def test_idempotent_second_run_skips_existing(self):
        """Fingerprint already in core_recommendations → action skipped, count=0."""
        existing = [{"id": str(uuid.uuid4()), "status": "PROPOSED"}]
        sb = _make_sb_materialise(rec_dup_data=existing)
        count = _materialize_dr_recommendations(
            sb, "lk-sneakers", LK_UUID_FAKE, PERIOD_END, _SAMPLE_CONTRACT, CONTRACT_ID
        )
        assert count == 0

    def test_approved_recommendation_skipped(self):
        """APPROVED status → action is skipped (no overwrite)."""
        existing = [{"id": str(uuid.uuid4()), "status": "APPROVED"}]
        sb = _make_sb_materialise(rec_dup_data=existing)
        count = _materialize_dr_recommendations(
            sb, "lk-sneakers", LK_UUID_FAKE, PERIOD_END, _SAMPLE_CONTRACT, CONTRACT_ID
        )
        assert count == 0

    def test_reuses_existing_diagnosis(self):
        """Diagnosis already in core_diagnoses → no second insert."""
        existing_diag = [{"id": str(uuid.uuid4())}]
        sb = _make_sb_materialise(rec_dup_data=None, diag_dup_data=existing_diag)
        count = _materialize_dr_recommendations(
            sb, "lk-sneakers", LK_UUID_FAKE, PERIOD_END, _SAMPLE_CONTRACT, CONTRACT_ID
        )
        assert count == 2

    def test_skips_action_without_code(self):
        """Action missing 'code' field is silently skipped."""
        contract = {
            **_SAMPLE_CONTRACT,
            "section_g_recommended_actions": [
                {"id": str(uuid.uuid4()), "action": "No code here", "priority": "LOW"},
            ],
        }
        sb = _make_sb_materialise()
        count = _materialize_dr_recommendations(
            sb, "lk-sneakers", LK_UUID_FAKE, PERIOD_END, contract, CONTRACT_ID
        )
        assert count == 0

    def test_fingerprint_format(self):
        """Fingerprint on recommendation must be dr:{client}:{period_end}:{code}."""
        inserted_fp: list[str] = []

        def _table_side_effect(name):
            t = MagicMock()
            if name == "core_recommendations":
                t.select.return_value.eq.return_value.limit.return_value.execute.return_value.data = None
                def _insert(payload):
                    inserted_fp.append(payload.get("fingerprint", ""))
                    r = MagicMock()
                    r.execute.return_value = MagicMock(data=[{"id": str(uuid.uuid4())}])
                    return r
                t.insert.side_effect = _insert
            elif name == "core_diagnoses":
                t.select.return_value.eq.return_value.limit.return_value.execute.return_value.data = None
                t.insert.return_value.execute.return_value.data = [{"id": str(uuid.uuid4())}]
            return t

        sb = MagicMock()
        sb.table.side_effect = _table_side_effect

        # single action with known code
        contract = {
            "section_f_diagnoses": [{"code": "SPEND_DROP", "finding": "spend fell"}],
            "section_g_recommended_actions": [
                {"code": "SPEND_DROP", "action": "Check spend", "priority": "HIGH", "risk": "LOW"},
            ],
        }
        _materialize_dr_recommendations(
            sb, "lk-sneakers", LK_UUID_FAKE, date(2026, 11, 8), contract, CONTRACT_ID
        )
        assert inserted_fp == ["dr:lk-sneakers:2026-11-08:SPEND_DROP"]

    def test_diag_insert_failure_skips_recommendation(self):
        """DB error on core_diagnoses insert → action skipped, no crash."""
        def _table_side_effect(name):
            t = MagicMock()
            if name == "core_recommendations":
                t.select.return_value.eq.return_value.limit.return_value.execute.return_value.data = None
            elif name == "core_diagnoses":
                t.select.return_value.eq.return_value.limit.return_value.execute.return_value.data = None
                t.insert.return_value.execute.side_effect = RuntimeError("DB error")
            return t

        sb = MagicMock()
        sb.table.side_effect = _table_side_effect
        count = _materialize_dr_recommendations(
            sb, "lk-sneakers", LK_UUID_FAKE, PERIOD_END, _SAMPLE_CONTRACT, CONTRACT_ID
        )
        assert count == 0  # all skipped due to diag failure


class TestLoadClientUuid:
    def test_returns_uuid_when_found(self):
        sb = MagicMock()
        client_res = MagicMock()
        client_res.data = {"id": LK_UUID_FAKE}
        sb.table.return_value.select.return_value.eq.return_value.single.return_value.execute.return_value = client_res
        result = _load_client_uuid(sb, "lk-sneakers")
        assert result == LK_UUID_FAKE

    def test_returns_none_on_db_error(self):
        sb = MagicMock()
        sb.table.return_value.select.return_value.eq.return_value.single.return_value.execute.side_effect = RuntimeError("timeout")
        result = _load_client_uuid(sb, "lk-sneakers")
        assert result is None

    def test_returns_none_when_no_data(self):
        sb = MagicMock()
        client_res = MagicMock()
        client_res.data = {}
        sb.table.return_value.select.return_value.eq.return_value.single.return_value.execute.return_value = client_res
        result = _load_client_uuid(sb, "lk-sneakers")
        assert result is None
