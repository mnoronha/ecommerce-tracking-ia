"""
Deterministic unit tests for the performance alert evaluator.

Tests confirm rule logic with synthetic metric dicts — no DB calls.
Each test constructs the minimal inputs needed to make a rule fire or stay silent.
"""

from __future__ import annotations

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import statistics
import unittest
from unittest.mock import MagicMock, patch, call
from datetime import datetime, timezone

from app.core.performance_alert_evaluator import (
    PERF_ALERT_TYPES,
    _PERF_SEVERITY,
    _metric_value,
    _load_snapshot_history,
    _SPEND_NO_CONV_MIN_BRL,
    _DROP_THRESHOLD,
    _MIN_BASELINE,
    _ROAS_MISS_RATIO,
    _CPA_MISS_RATIO,
    _ANOMALY_SPEND_HIGH,
    _ANOMALY_CONV_LOW,
)


def _mk_metric(key: str, value, status: str = "OK") -> dict:
    return {"metric_key": key, "value": value, "value_status": status}


def _mk_metrics(*pairs) -> dict[str, dict]:
    """Build a metrics dict from (key, value, status) triples."""
    result = {}
    for item in pairs:
        key, val = item[0], item[1]
        status = item[2] if len(item) > 2 else "OK"
        result[key] = _mk_metric(key, val, status)
    return result


# ── Helpers ────────────────────────────────────────────────────────────────────

class TestMetricValueGuard(unittest.TestCase):
    def test_ok_returns_float(self):
        m = {"OK": _mk_metric("x", 100.0, "OK")}
        self.assertEqual(_metric_value({"x": _mk_metric("x", 100.0)}, "x"), 100.0)

    def test_stale_returns_none(self):
        self.assertIsNone(_metric_value({"x": _mk_metric("x", 99.9, "STALE")}, "x"))

    def test_missing_returns_none(self):
        self.assertIsNone(_metric_value({"x": _mk_metric("x", 0.0, "MISSING")}, "x"))

    def test_absent_key_returns_none(self):
        self.assertIsNone(_metric_value({}, "x"))

    def test_partial_returns_float(self):
        self.assertEqual(_metric_value({"x": _mk_metric("x", 42.0, "PARTIAL")}, "x"), 42.0)

    def test_no_data_returns_none(self):
        self.assertIsNone(_metric_value({"x": _mk_metric("x", 0.0, "NO_DATA")}, "x"))


# ── Rule 1: SPEND_NO_CONVERSION ────────────────────────────────────────────────

class TestSpendNoConversion(unittest.TestCase):
    """Rule fires when spend >= threshold AND conv metric is present (OK) AND == 0."""

    def _run_rule(self, metrics, business_model="ecommerce", target_truth=None):
        from app.core.performance_alert_evaluator import (
            _SPEND_KEY, _CONV_KEY, _SPEND_NO_CONV_MIN_BRL,
        )
        fired = []
        conv_map = _CONV_KEY.get(business_model, _CONV_KEY["ecommerce"])
        for platform, spend_key in _SPEND_KEY.items():
            spend = _metric_value(metrics, spend_key)
            if spend is None or spend < _SPEND_NO_CONV_MIN_BRL:
                continue
            conv_key = conv_map.get(platform)
            if not conv_key:
                continue
            convs = _metric_value(metrics, conv_key)
            if convs is None:
                continue
            if convs == 0.0:
                fired.append(platform)
        return fired

    def test_fires_when_spend_high_conv_zero(self):
        m = _mk_metrics(
            ("meta_spend", 1000.0), ("meta_conversions", 0.0),
        )
        self.assertIn("meta", self._run_rule(m))

    def test_no_fire_when_spend_below_threshold(self):
        m = _mk_metrics(
            ("meta_spend", 100.0), ("meta_conversions", 0.0),
        )
        self.assertEqual([], self._run_rule(m))

    def test_no_fire_when_conv_nonzero(self):
        m = _mk_metrics(
            ("meta_spend", 2000.0), ("meta_conversions", 5.0),
        )
        self.assertEqual([], self._run_rule(m))

    def test_no_fire_when_conv_stale(self):
        """Stale conversions → unknown, not zero → must NOT fire."""
        m = _mk_metrics(
            ("meta_spend", 2000.0), ("meta_conversions", 0.0, "STALE"),
        )
        self.assertEqual([], self._run_rule(m))

    def test_no_fire_when_conv_absent(self):
        m = _mk_metrics(("meta_spend", 2000.0))
        self.assertEqual([], self._run_rule(m))

    def test_ecommerce_google_uses_purchase_conv(self):
        """ecommerce: google conv key is google_purchase_conversions."""
        m = _mk_metrics(
            ("google_spend", 1500.0), ("google_purchase_conversions", 0.0),
        )
        self.assertIn("google", self._run_rule(m, business_model="ecommerce"))

    def test_lead_gen_google_uses_google_conversions(self):
        m = _mk_metrics(
            ("google_spend", 1500.0), ("google_conversions", 0.0),
        )
        self.assertIn("google", self._run_rule(m, business_model="lead_generation"))


# ── Rule 2: ROAS_BELOW_TARGET ──────────────────────────────────────────────────

class TestRoasBelowTarget(unittest.TestCase):
    def _run_rule(self, metrics, target_truth, business_model="ecommerce"):
        from app.core.performance_alert_evaluator import _ROAS_KEY, _ROAS_MISS_RATIO, _safe_float
        fired = []
        roas_map = _ROAS_KEY.get(business_model, _ROAS_KEY["ecommerce"])
        for platform, roas_key in roas_map.items():
            tt = target_truth.get(roas_key)
            if not isinstance(tt, dict) or "target" not in tt:
                continue
            target = _safe_float(tt["target"])
            if not target or target <= 0:
                continue
            actual = _metric_value(metrics, roas_key)
            if actual is None:
                continue
            if actual < target * _ROAS_MISS_RATIO:
                fired.append((platform, roas_key))
        return fired

    def test_fires_when_roas_below_80pct_target(self):
        m = _mk_metrics(("roas_meta", 3.0))
        tt = {"roas_meta": {"target": 5.0}}
        # 3.0 < 5.0 * 0.80 = 4.0 → should fire
        self.assertTrue(len(self._run_rule(m, tt)) > 0)

    def test_no_fire_above_80pct(self):
        m = _mk_metrics(("roas_meta", 4.5))
        tt = {"roas_meta": {"target": 5.0}}
        # 4.5 >= 4.0 → should NOT fire
        self.assertEqual([], self._run_rule(m, tt))

    def test_not_applicable_when_no_target(self):
        m = _mk_metrics(("roas_meta", 2.0))
        self.assertEqual([], self._run_rule(m, {}))

    def test_no_fire_when_roas_stale(self):
        m = _mk_metrics(("roas_meta", 2.0, "STALE"))
        tt = {"roas_meta": {"target": 5.0}}
        self.assertEqual([], self._run_rule(m, tt))


# ── Rule 3: CPA_ABOVE_TARGET ───────────────────────────────────────────────────

class TestCpaAboveTarget(unittest.TestCase):
    def _run_rule(self, metrics, target_truth, business_model="ecommerce"):
        from app.core.performance_alert_evaluator import _CPA_KEY, _CPA_MISS_RATIO, _safe_float
        fired = []
        cpa_map = _CPA_KEY.get(business_model, _CPA_KEY["ecommerce"])
        for platform, cpa_key in cpa_map.items():
            tt = target_truth.get(cpa_key)
            if not isinstance(tt, dict) or "target" not in tt:
                continue
            target = _safe_float(tt["target"])
            if not target or target <= 0:
                continue
            actual = _metric_value(metrics, cpa_key)
            if actual is None:
                continue
            if actual > target * _CPA_MISS_RATIO:
                fired.append((platform, cpa_key))
        return fired

    def test_fires_when_cpa_above_130pct(self):
        m = _mk_metrics(("cpa_meta", 200.0))
        tt = {"cpa_meta": {"target": 100.0}}
        # 200 > 100 * 1.30 = 130 → fire
        self.assertTrue(len(self._run_rule(m, tt)) > 0)

    def test_no_fire_below_130pct(self):
        m = _mk_metrics(("cpa_meta", 120.0))
        tt = {"cpa_meta": {"target": 100.0}}
        # 120 <= 130 → no fire
        self.assertEqual([], self._run_rule(m, tt))

    def test_not_applicable_when_no_target(self):
        m = _mk_metrics(("cpa_meta", 500.0))
        self.assertEqual([], self._run_rule(m, {}))


# ── Rules 4-6: Drop detection ─────────────────────────────────────────────────

class TestDropDetection(unittest.TestCase):
    def _run_drop(self, metric_key, current_val, prev_vals):
        """Return True if the drop rule would fire."""
        if current_val is None or len(prev_vals) < _MIN_BASELINE:
            return False
        med = statistics.median(prev_vals)
        if med <= 0:
            return False
        return current_val < med * (1.0 - _DROP_THRESHOLD)

    def test_fires_at_50pct_drop(self):
        # current=50, median=100 → drop=50% > 40% threshold
        self.assertTrue(self._run_drop("x", 50.0, [100.0, 100.0, 100.0]))

    def test_no_fire_at_30pct_drop(self):
        self.assertFalse(self._run_drop("x", 70.0, [100.0, 100.0, 100.0]))

    def test_no_fire_when_insufficient_baseline(self):
        # Only 2 prior periods — below _MIN_BASELINE=3
        self.assertFalse(self._run_drop("x", 10.0, [100.0, 100.0]))

    def test_no_fire_at_exact_threshold(self):
        # current = median * 0.60 → exactly at threshold, not below
        self.assertFalse(self._run_drop("x", 60.0, [100.0, 100.0, 100.0]))

    def test_fires_just_below_threshold(self):
        self.assertTrue(self._run_drop("x", 59.9, [100.0, 100.0, 100.0]))

    def test_no_fire_zero_median(self):
        self.assertFalse(self._run_drop("x", 0.0, [0.0, 0.0, 0.0]))

    def test_no_fire_when_current_none(self):
        self.assertFalse(self._run_drop("x", None, [100.0, 100.0, 100.0]))


# ── Rule 7: CAMPAIGN_NOT_SPENDING ─────────────────────────────────────────────

class TestCampaignNotSpending(unittest.TestCase):
    def _run_rule(self, platform_date_spend, platform, zero_days=2):
        """Return True if CAMPAIGN_NOT_SPENDING would fire for this platform."""
        dates = sorted(
            {k[1] for k in platform_date_spend if k[0] == platform},
            reverse=True,
        )
        if len(dates) < zero_days:
            return False
        recent_zero = all(
            platform_date_spend.get((platform, d), 0) == 0.0
            for d in dates[:zero_days]
        )
        if not recent_zero:
            return False
        older_had_spend = any(
            platform_date_spend.get((platform, d), 0) > 0
            for d in dates[zero_days:]
        )
        return older_had_spend

    def test_fires_when_2_consecutive_zero_after_active(self):
        spend = {
            ("google", "2026-10-05"): 500.0,  # was active
            ("google", "2026-10-06"): 0.0,    # zero
            ("google", "2026-10-07"): 0.0,    # zero
        }
        self.assertTrue(self._run_rule(spend, "google"))

    def test_no_fire_when_only_1_zero_day(self):
        spend = {
            ("google", "2026-10-05"): 500.0,
            ("google", "2026-10-06"): 500.0,
            ("google", "2026-10-07"): 0.0,    # only 1 zero
        }
        self.assertFalse(self._run_rule(spend, "google"))

    def test_no_fire_when_never_had_spend(self):
        """All dates zero and no older spend — could be new/always paused."""
        spend = {
            ("google", "2026-10-06"): 0.0,
            ("google", "2026-10-07"): 0.0,
        }
        self.assertFalse(self._run_rule(spend, "google"))

    def test_no_fire_when_active_last_day(self):
        spend = {
            ("google", "2026-10-05"): 500.0,
            ("google", "2026-10-06"): 0.0,
            ("google", "2026-10-07"): 400.0,  # active on most recent
        }
        self.assertFalse(self._run_rule(spend, "google"))

    def test_no_fire_insufficient_dates(self):
        spend = {("google", "2026-10-07"): 0.0}
        self.assertFalse(self._run_rule(spend, "google"))


# ── Rule 8: SOURCE_PERFORMANCE_ANOMALY ────────────────────────────────────────

class TestSourcePerformanceAnomaly(unittest.TestCase):
    def _run_rule(self, cur_spend, cur_conv, prev_spends, prev_convs):
        if len(prev_spends) < _MIN_BASELINE or len(prev_convs) < _MIN_BASELINE:
            return False
        med_spend = statistics.median(prev_spends)
        med_conv  = statistics.median(prev_convs)
        if med_spend <= 0 or med_conv <= 0:
            return False
        return (
            (cur_spend / med_spend) > _ANOMALY_SPEND_HIGH
            and (cur_conv  / med_conv)  < _ANOMALY_CONV_LOW
        )

    def test_fires_on_efficiency_collapse(self):
        # spend 120% of median, conversions 20% of median
        self.assertTrue(self._run_rule(
            cur_spend=120.0, cur_conv=20.0,
            prev_spends=[100.0, 100.0, 100.0],
            prev_convs =[100.0, 100.0, 100.0],
        ))

    def test_no_fire_when_both_normal(self):
        self.assertFalse(self._run_rule(
            cur_spend=100.0, cur_conv=95.0,
            prev_spends=[100.0, 100.0, 100.0],
            prev_convs =[100.0, 100.0, 100.0],
        ))

    def test_no_fire_when_only_spend_high(self):
        # Spend up but conversions also up — not an anomaly
        self.assertFalse(self._run_rule(
            cur_spend=120.0, cur_conv=110.0,
            prev_spends=[100.0, 100.0, 100.0],
            prev_convs =[100.0, 100.0, 100.0],
        ))

    def test_no_fire_insufficient_baseline(self):
        self.assertFalse(self._run_rule(
            cur_spend=200.0, cur_conv=5.0,
            prev_spends=[100.0, 100.0],  # only 2
            prev_convs =[100.0, 100.0],
        ))


# ── Alert type completeness ────────────────────────────────────────────────────

class TestAlertTypeCatalogue(unittest.TestCase):
    def test_all_types_have_severity(self):
        for t in PERF_ALERT_TYPES:
            self.assertIn(t, _PERF_SEVERITY, f"missing severity for {t}")

    def test_all_severity_values_valid(self):
        valid = {"HIGH", "MEDIUM", "LOW"}
        for t, sev in _PERF_SEVERITY.items():
            self.assertIn(sev, valid, f"{t} has invalid severity {sev!r}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
