"""Tests for analysis_layer.py — pure logic paths only (no Hermes/OpenAI network call)."""
import os
import pytest
from unittest.mock import patch

from app.core.analysis_layer import (
    detect_anomalies,
    build_wow,
    check_quality_gate,
    build_analysis_layer,
    _GENERIC_PHRASES,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _metrics(**kw):
    """Build a metrics_dict from keyword pairs of key → value."""
    return {k: {"value": v, "value_status": "OK"} for k, v in kw.items() if v is not None}


def _channels(meta=True, google=True):
    result = {}
    if meta:
        result["meta"] = {"spend": 10000, "conversions": 100, "source_state": "READY"}
    if google:
        result["google"] = {"spend": 8000, "conversions": 90, "source_state": "READY"}
    return result


def _good_analysis(google=True, meta=True, has_changes=False):
    """Minimal analysis_layer dict that passes quality gate."""
    return {
        "status": "ANALYSIS_COMPLETE",
        "executive_summary": {
            "bullets": ["Revenue R$100K, MER 15x", "ROAS Meta 8x"],
            "key_win": "MER acima de 15x",
            "key_risk": "GA4 underreporting",
        },
        "google_analysis": {"spend_context": "Spend estável", "efficiency_assessment": "ROAS 5x", "observations": ["obs"]} if google else None,
        "meta_analysis":   {"spend_context": "Spend cresceu", "efficiency_assessment": "ROAS 8x", "observations": ["obs"]} if meta  else None,
        "diagnosis": [
            {"observation": "ROAS 5x", "diagnosis": "Eficiência ok", "recommended_action": "Aumentar budget 10% na campanha principal", "impact": "high", "confidence": "HIGH"},
            {"observation": "CPA R$70", "diagnosis": "Dentro do alvo", "recommended_action": "Monitorar campanhas de remarketing por 7 dias", "impact": "medium", "confidence": "MEDIUM"},
            {"observation": "GA4 low CVR", "diagnosis": "Bot traffic", "recommended_action": "Criar segmento GA4 excluindo sessões < 2s", "impact": "high", "confidence": "MEDIUM"},
        ],
        "recommendations": [
            {
                "problem_or_opportunity": "Budget CORE abaixo do potencial",
                "evidence": "ROAS 5x com orçamento R$315/dia",
                "action": "Aumentar daily budget da campanha CORE de R$315 para R$400",
                "priority": "P1",
                "expected_effect": "Aumento de 20% em conversões sem perda de ROAS",
                "risk": "Período de aprendizado de 3-7 dias",
                "confidence": "HIGH",
            },
            {
                "problem_or_opportunity": "GA4 sessions anômalas",
                "evidence": "597K sessões com 52 purchases = CVR 0.009%",
                "action": "Criar filtro GA4 excluindo IPs com < 2s de duração de sessão",
                "priority": "P0",
                "expected_effect": "CVR realista para análise de funil",
                "risk": "Pode reduzir sessões reportadas em 80%+",
                "confidence": "HIGH",
            },
        ],
        "change_digest_section": {"main_changes_narrative": "Budget escalou 44%"} if has_changes else None,
        "priorities_next_week": [{"action": "Revisar budget", "owner": "Media", "deadline_suggestion": "Seg", "rationale": "BF prep"}],
    }


# ── detect_anomalies ──────────────────────────────────────────────────────────

class TestDetectAnomalies:
    def test_ga4_low_cvr(self):
        m = _metrics(ga4_sessions=597090, ga4_purchases=52)
        result = detect_anomalies(m, {})
        types = [a["type"] for a in result]
        assert "GA4_LOW_CVR" in types
        ga4 = next(a for a in result if a["type"] == "GA4_LOW_CVR")
        assert ga4["severity"] == "HIGH"

    def test_ga4_normal_cvr_no_anomaly(self):
        m = _metrics(ga4_sessions=10000, ga4_purchases=150)  # CVR 1.5%
        result = detect_anomalies(m, {})
        assert not any(a["type"] == "GA4_LOW_CVR" for a in result)

    def test_google_non_purchase_majority_high(self):
        m = _metrics(google_conversions=128, google_purchase_conversions=13)
        result = detect_anomalies(m, {})
        types = [a["type"] for a in result]
        assert "GOOGLE_NON_PURCHASE_MAJORITY" in types
        a = next(a for a in result if a["type"] == "GOOGLE_NON_PURCHASE_MAJORITY")
        assert a["severity"] == "HIGH"

    def test_google_non_purchase_medium_ratio(self):
        m = _metrics(google_conversions=60, google_purchase_conversions=15)  # ratio 3x
        result = detect_anomalies(m, {})
        types = [a["type"] for a in result]
        assert "GOOGLE_HIGH_NON_PURCHASE_RATIO" in types
        assert "GOOGLE_NON_PURCHASE_MAJORITY" not in types

    def test_google_equal_purchase_no_anomaly(self):
        m = _metrics(google_conversions=30, google_purchase_conversions=25)
        result = detect_anomalies(m, {})
        assert not any("GOOGLE" in a["type"] and "PURCHASE" in a["type"] for a in result)

    def test_business_ga4_revenue_gap_high(self):
        m = _metrics(revenue_business=210000, ga4_revenue=137000)  # 35% gap
        result = detect_anomalies(m, {})
        types = [a["type"] for a in result]
        assert "GA4_BUSINESS_REVENUE_GAP" in types
        a = next(a for a in result if a["type"] == "GA4_BUSINESS_REVENUE_GAP")
        assert a["severity"] == "HIGH"

    def test_business_ga4_revenue_gap_medium(self):
        m = _metrics(revenue_business=100000, ga4_revenue=82000)  # 18% gap
        result = detect_anomalies(m, {})
        types = [a["type"] for a in result]
        assert "GA4_BUSINESS_REVENUE_GAP_MEDIUM" in types
        assert "GA4_BUSINESS_REVENUE_GAP" not in types

    def test_business_ga4_revenue_gap_small_no_anomaly(self):
        m = _metrics(revenue_business=100000, ga4_revenue=93000)  # 7% gap
        result = detect_anomalies(m, {})
        assert not any("GA4_BUSINESS" in a["type"] for a in result)

    def test_cpa_blended_vs_ecommerce_divergence(self):
        m = _metrics(cpa_google=41.51, google_cpa_ecommerce=409.88)  # ratio ~10x
        result = detect_anomalies(m, {})
        types = [a["type"] for a in result]
        assert "CPA_BLENDED_VS_ECOMMERCE_DIVERGENCE" in types

    def test_cpa_close_no_anomaly(self):
        m = _metrics(cpa_google=70, google_cpa_ecommerce=82)  # ratio ~1.2x
        result = detect_anomalies(m, {})
        assert not any("CPA_BLENDED" in a["type"] for a in result)

    def test_orders_ga4_gap(self):
        m = _metrics(orders_count=79, ga4_purchases=52)  # 34% gap
        result = detect_anomalies(m, {})
        assert any(a["type"] == "ORDERS_GA4_PURCHASES_GAP" for a in result)

    def test_no_anomalies_clean_data(self):
        m = _metrics(
            ga4_sessions=10000, ga4_purchases=150,
            google_conversions=25, google_purchase_conversions=20,
            revenue_business=100000, ga4_revenue=95000,
            cpa_google=70, google_cpa_ecommerce=82,
            orders_count=150,
        )
        result = detect_anomalies(m, {})
        assert result == []

    def test_missing_values_no_crash(self):
        result = detect_anomalies({}, {})
        assert result == []


# ── build_wow ─────────────────────────────────────────────────────────────────

class TestBuildWow:
    def test_no_prev_snapshot(self):
        result = build_wow({}, None)
        assert result["available"] is False

    def test_with_prev_snapshot(self):
        current = _metrics(meta_spend=11000, roas_meta=5.2)
        prev_metrics_raw = [
            {"metric_key": "meta_spend", "value": 10000, "value_status": "OK"},
            {"metric_key": "roas_meta",  "value": 4.8,   "value_status": "OK"},
        ]
        prev_snap = {"metrics": prev_metrics_raw}
        result = build_wow(current, prev_snap)
        assert result["available"] is True
        deltas = result["deltas"]
        assert "meta_spend" in deltas
        assert deltas["meta_spend"]["direction"] == "up"
        assert abs(deltas["meta_spend"]["delta_pct"] - 10.0) < 0.1
        assert deltas["roas_meta"]["direction"] == "up"

    def test_with_prev_snapshot_dict_metrics(self):
        current = _metrics(meta_spend=9000)
        prev_snap = {"metrics": {"ms": {"metric_key": "meta_spend", "value": 10000, "value_status": "OK"}}}
        result = build_wow(current, prev_snap)
        assert result["available"] is True
        assert result["deltas"]["meta_spend"]["direction"] == "down"

    def test_prev_zero_not_included(self):
        current = _metrics(meta_spend=5000)
        prev_snap = {"metrics": [{"metric_key": "meta_spend", "value": 0, "value_status": "OK"}]}
        result = build_wow(current, prev_snap)
        assert "meta_spend" not in result["deltas"]


# ── check_quality_gate ────────────────────────────────────────────────────────

class TestCheckQualityGate:
    def _content(self, analysis, channels=None, change_summary=None):
        return {
            "analysis_layer": analysis,
            "channels": channels or _channels(),
            "change_summary": change_summary or {},
            "facts": [{"metric_key": "meta_spend", "value": 10000}],
        }

    def test_pass_with_complete_analysis(self):
        content = self._content(_good_analysis())
        passes, violations = check_quality_gate(content, analysis_enabled=True)
        assert passes, violations

    def test_bypass_when_analysis_disabled(self):
        content = self._content({"status": "ANALYSIS_DISABLED"})
        passes, violations = check_quality_gate(content, analysis_enabled=False)
        assert passes
        assert violations == []

    def test_fail_empty_executive_summary(self):
        a = _good_analysis()
        a["executive_summary"]["bullets"] = []
        passes, violations = check_quality_gate(self._content(a), analysis_enabled=True)
        assert not passes
        assert any("executive_summary" in v for v in violations)

    def test_fail_one_bullet_executive_summary(self):
        a = _good_analysis()
        a["executive_summary"]["bullets"] = ["only one"]
        passes, violations = check_quality_gate(self._content(a), analysis_enabled=True)
        assert not passes

    def test_fail_empty_recommendations(self):
        a = _good_analysis()
        a["recommendations"] = []
        passes, violations = check_quality_gate(self._content(a), analysis_enabled=True)
        assert not passes
        assert any("recommendations" in v for v in violations)

    def test_fail_empty_diagnosis(self):
        a = _good_analysis()
        a["diagnosis"] = []
        passes, violations = check_quality_gate(self._content(a), analysis_enabled=True)
        assert not passes
        assert any("diagnosis" in v for v in violations)

    def test_fail_generic_recommendation(self):
        a = _good_analysis()
        a["recommendations"][0]["action"] = "otimizar campanhas para melhor ROAS"
        passes, violations = check_quality_gate(self._content(a), analysis_enabled=True)
        assert not passes
        assert any("generic" in v for v in violations)

    def test_fail_google_active_but_no_google_analysis(self):
        a = _good_analysis()
        a["google_analysis"] = None
        passes, violations = check_quality_gate(self._content(a), analysis_enabled=True)
        assert not passes
        assert any("google" in v for v in violations)

    def test_fail_meta_active_but_no_meta_analysis(self):
        a = _good_analysis()
        a["meta_analysis"] = None
        passes, violations = check_quality_gate(self._content(a), analysis_enabled=True)
        assert not passes
        assert any("meta" in v for v in violations)

    def test_fail_analysis_unavailable_status(self):
        a = _good_analysis()
        a["status"] = "ANALYSIS_UNAVAILABLE"
        passes, violations = check_quality_gate(self._content(a), analysis_enabled=True)
        assert not passes

    def test_fail_change_digest_present_not_consumed(self):
        a = _good_analysis(has_changes=False)
        a["change_digest_section"] = None
        change_summary = {"meta": {"relevant_changes": 10, "main_changes": ["Budget changed"]}}
        content = self._content(a, change_summary=change_summary)
        passes, violations = check_quality_gate(content, analysis_enabled=True)
        assert not passes
        assert any("change_digest" in v for v in violations)

    def test_pass_no_google_channel(self):
        a = _good_analysis(google=False)
        content = self._content(a, channels=_channels(google=False))
        passes, violations = check_quality_gate(content, analysis_enabled=True)
        assert passes, violations

    def test_multiple_violations_collected(self):
        a = {"status": "ANALYSIS_COMPLETE", "executive_summary": {"bullets": []},
             "recommendations": [], "diagnosis": [], "google_analysis": None, "meta_analysis": None}
        passes, violations = check_quality_gate(self._content(a), analysis_enabled=True)
        assert not passes
        assert len(violations) >= 4


# ── build_analysis_layer ──────────────────────────────────────────────────────

class TestBuildAnalysisLayer:
    _BASE = dict(
        client_id="test-client", period_start="2026-10-19", period_end="2026-10-25",
        metrics_dict={}, channels={}, business_truth="test BT",
        change_summary={}, limitations=[], prev_snapshot=None,
    )

    def test_disabled_flag_returns_fallback(self):
        with patch.dict(os.environ, {"CANONICAL_ANALYSIS_ENABLED": "false"}):
            result = build_analysis_layer(**self._BASE)
        assert result["status"] == "ANALYSIS_UNAVAILABLE"
        assert result["reason"] == "ANALYSIS_DISABLED"
        assert result["executive_summary"]["bullets"] == []

    def test_enabled_no_api_key_returns_fallback(self):
        env = {"CANONICAL_ANALYSIS_ENABLED": "true"}
        with patch.dict(os.environ, env, clear=False):
            if "OPENAI_API_KEY" in os.environ:
                del os.environ["OPENAI_API_KEY"]
            result = build_analysis_layer(**self._BASE)
        assert result["status"] == "ANALYSIS_UNAVAILABLE"
        # Locally openai may not be installed; on Railway it raises the key error
        reason = result.get("reason", "")
        assert "OPENAI_API_KEY" in reason or "openai" in reason.lower()

    def test_enabled_hermes_raises_returns_fallback(self):
        env = {"CANONICAL_ANALYSIS_ENABLED": "true", "OPENAI_API_KEY": "sk-test"}
        with patch.dict(os.environ, env):
            with patch("app.core.analysis_layer._call_hermes", side_effect=RuntimeError("api down")):
                result = build_analysis_layer(**self._BASE)
        assert result["status"] == "ANALYSIS_UNAVAILABLE"
        assert "api down" in result["reason"]

    def test_enabled_hermes_invalid_structure_returns_fallback(self):
        env = {"CANONICAL_ANALYSIS_ENABLED": "true", "OPENAI_API_KEY": "sk-test"}
        with patch.dict(os.environ, env):
            with patch("app.core.analysis_layer._call_hermes", return_value=({"executive_summary": {"bullets": []}}, "gpt-4o")):
                result = build_analysis_layer(**self._BASE)
        assert result["status"] == "ANALYSIS_UNAVAILABLE"
        assert "VALIDATION" in result["reason"] or "bullets" in result["reason"]

    def test_enabled_hermes_success(self):
        good = _good_analysis()
        env = {"CANONICAL_ANALYSIS_ENABLED": "true", "OPENAI_API_KEY": "sk-test"}
        with patch.dict(os.environ, env):
            with patch("app.core.analysis_layer._call_hermes", return_value=(good, "gpt-4o")):
                result = build_analysis_layer(**self._BASE)
        assert result["status"] == "ANALYSIS_COMPLETE"
        assert result["anomalies_detected"] is not None
        assert result["wow_comparison"] is not None
        assert result["analysis_engine"] == "gpt-4o"
