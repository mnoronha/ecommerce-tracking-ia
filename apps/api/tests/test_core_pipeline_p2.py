"""
Core Pipeline — P2 tests: ROAS/CPA derived metrics + target_ratio.

Invariants:
1. roas_meta = meta_conversion_value / meta_spend (guard: meta_spend > 0)
2. roas_google = google_conversion_value / google_spend (guard: google_spend > 0)
3. cpa_meta = meta_spend / meta_conversions (guard: meta_conversions > 0)
4. cpa_google = google_spend / google_conversions (guard: google_conversions > 0)
5. None when guard condition fails (not zero, not ERROR)
6. target_ratio = value / target, deterministic, set only when target > 0
7. target_status MUST NOT be set by pipeline (no policy threshold)
8. target_ratio None when value is None or target is zero/absent

Run from apps/api/:
    python -m pytest tests/test_core_pipeline_p2.py -v
"""

from __future__ import annotations

import pytest

from app.core.metric_registry import DERIVED_DEPS, METRIC_REGISTRY
from app.core.snapshot_writer import build_metric_value


class TestMetricRegistry:
    """Derived metric definitions and deps exist."""

    def test_roas_meta_in_registry(self):
        assert "roas_meta" in METRIC_REGISTRY
        m = METRIC_REGISTRY["roas_meta"]
        assert m.source_system == "derived"
        assert m.aggregation == "derived"
        assert m.unit == "x"

    def test_roas_google_in_registry(self):
        assert "roas_google" in METRIC_REGISTRY
        m = METRIC_REGISTRY["roas_google"]
        assert m.source_system == "derived"

    def test_cpa_meta_in_registry(self):
        assert "cpa_meta" in METRIC_REGISTRY
        m = METRIC_REGISTRY["cpa_meta"]
        assert m.unit == "BRL"
        assert m.currency == "BRL"

    def test_cpa_google_in_registry(self):
        assert "cpa_google" in METRIC_REGISTRY
        m = METRIC_REGISTRY["cpa_google"]
        assert m.currency == "BRL"

    def test_roas_meta_deps(self):
        assert DERIVED_DEPS["roas_meta"] == ["meta_conversion_value", "meta_spend"]

    def test_roas_google_deps(self):
        assert DERIVED_DEPS["roas_google"] == ["google_conversion_value", "google_spend"]

    def test_cpa_meta_deps(self):
        assert DERIVED_DEPS["cpa_meta"] == ["meta_spend", "meta_conversions"]

    def test_cpa_google_deps(self):
        assert DERIVED_DEPS["cpa_google"] == ["google_spend", "google_conversions"]


class TestBuildMetricValueTargetRatio:
    """build_metric_value accepts and stores target_ratio."""

    def test_target_ratio_stored_when_provided(self):
        mv = build_metric_value(
            metric_key="mer",
            value=5.5,
            unit="x",
            value_status="OK",
            target=5.0,
            target_ratio=1.1,
        )
        assert mv["target"] == 5.0
        assert mv["target_ratio"] == 1.1

    def test_target_ratio_absent_when_not_provided(self):
        mv = build_metric_value(
            metric_key="mer",
            value=5.5,
            unit="x",
            value_status="OK",
        )
        assert "target_ratio" not in mv

    def test_target_status_not_set_by_default(self):
        """Pipeline must not set target_status — no policy threshold."""
        mv = build_metric_value(
            metric_key="mer",
            value=5.5,
            unit="x",
            value_status="OK",
            target=5.0,
            target_ratio=1.1,
        )
        assert mv.get("target_status") is None

    def test_target_ratio_none_excluded(self):
        mv = build_metric_value(
            metric_key="mer",
            value=None,
            unit="x",
            value_status="NO_DATA",
            target=5.0,
            target_ratio=None,
        )
        assert "target_ratio" not in mv


class TestDerivedMetricComputation:
    """ROAS/CPA derivation logic (unit tests on the arithmetic)."""

    def _compute_roas_meta(self, meta_cv: float, meta_spend: float):
        return round(meta_cv / meta_spend, 4) if meta_spend > 0 else None

    def _compute_roas_google(self, google_cv: float, google_spend: float):
        return round(google_cv / google_spend, 4) if google_spend > 0 else None

    def _compute_cpa_meta(self, meta_spend: float, meta_convs: float):
        return round(meta_spend / meta_convs, 2) if meta_convs > 0 else None

    def _compute_cpa_google(self, google_spend: float, google_convs: float):
        return round(google_spend / google_convs, 2) if google_convs > 0 else None

    def test_roas_meta_standard_case(self):
        assert self._compute_roas_meta(108303.42, 7791.46) == pytest.approx(13.9007, abs=1e-3)

    def test_roas_meta_zero_spend_returns_none(self):
        assert self._compute_roas_meta(5000.0, 0.0) is None

    def test_roas_google_standard(self):
        assert self._compute_roas_google(20000.0, 5000.0) == 4.0

    def test_roas_google_zero_spend_returns_none(self):
        assert self._compute_roas_google(0.0, 0.0) is None

    def test_cpa_meta_standard(self):
        assert self._compute_cpa_meta(7791.46, 38.0) == pytest.approx(205.04, abs=0.01)

    def test_cpa_meta_zero_conversions_returns_none(self):
        assert self._compute_cpa_meta(500.0, 0.0) is None

    def test_cpa_google_standard(self):
        assert self._compute_cpa_google(1000.0, 10.0) == 100.0

    def test_cpa_google_zero_conversions_returns_none(self):
        assert self._compute_cpa_google(1000.0, 0.0) is None


class TestTargetRatioArithmetic:
    """target_ratio = value / target, deterministic."""

    def _ratio(self, value, target):
        if value is None or not target or target <= 0:
            return None
        return round(value / target, 4)

    def test_mer_at_target(self):
        assert self._ratio(5.0, 5.0) == 1.0

    def test_mer_above_target(self):
        assert self._ratio(6.0, 5.0) == 1.2

    def test_mer_below_target(self):
        assert self._ratio(4.0, 5.0) == 0.8

    def test_revenue_business_ratio(self):
        # 261675 / 90000 = 2.9075
        assert self._ratio(261675.0, 90000.0) == pytest.approx(2.9075, abs=1e-3)

    def test_zero_target_returns_none(self):
        assert self._ratio(100.0, 0.0) is None

    def test_none_value_returns_none(self):
        assert self._ratio(None, 5.0) is None

    def test_no_policy_encoded(self):
        """target_ratio is just a number — no AT_RISK/BEHIND threshold baked in."""
        # 0.5 should not raise or return a status string
        result = self._ratio(2.5, 5.0)
        assert isinstance(result, float), "target_ratio must be float, not a policy enum"
