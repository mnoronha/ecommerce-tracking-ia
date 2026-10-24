"""
Unit tests for outcome_evaluator — UUID→slug fix and regression guard.

Covers:
1. UUID resolved to slug before querying core_balance_snapshots
2. EXHAUSTED → OK snapshot → POSITIVE / HIGH confidence
3. Snapshot not found → BLOCKED_DATA_QUALITY
4. Client not found (UUID lookup miss) → BLOCKED_DATA_QUALITY
5. Slug lookup exception → best-effort fallback, no crash
6. Balance still EXHAUSTED after action → NEGATIVE
7. collection_status != OK → BLOCKED_DATA_QUALITY
8. _evaluate_generic_action: roas improvement → POSITIVE (regression guard)
9. _evaluate_generic_action: no before/after → INCONCLUSIVE (regression guard)
10. _evaluate_generic_action: roas regression → NEGATIVE
11. _evaluate_generic_action: delta within ±5% → NEUTRAL
"""
from __future__ import annotations

from unittest.mock import MagicMock
import pytest

from app.core.outcome_evaluator import (
    _evaluate_balance_action,
    _evaluate_generic_action,
)

_CLIENT_UUID = "02a2a276-40ce-4154-bc7f-f3b586e13bc9"
_CLIENT_SLUG = "clinica-tarcio-caetano"

_BALANCE_ACTION = {
    "id":                     "e44f6268-e047-4978-a730-054a69635409",
    "client_id":              _CLIENT_UUID,
    "action_type":            "MANUAL_EXTERNAL_ACTION",
    "platform":               "google",
    "entity_type":            "GOOGLE_BALANCE_RECHARGE",
    "before_state":           {"balance": 0, "balance_status": "EXHAUSTED"},
    "after_state":            {},
    "evaluation_window_days": 7,
}

_SNAP_OK = {
    "balance":                  300.28,
    "balance_status":           "OK",
    "estimated_days_remaining": 10.1,
    "snapshot_at":              "2026-10-24T02:26:00+00:00",
    "collection_status":        "OK",
}

_GENERIC_ACTION = {
    "id":                     "aaaaaaaa-aaaa-aaaa-aaaa-000000000001",
    "client_id":              _CLIENT_UUID,
    "action_type":            "MANUAL_EXTERNAL_ACTION",
    "platform":               "meta",
    "entity_type":            "ad",
    "before_state":           {"roas": 3.5},
    "after_state":            {"roas": 4.2},
    "evaluation_window_days": 7,
}


def _make_sb(clients_data: list, snapshots_data: list):
    """
    Build a mock Supabase client routing table() calls.
    Returns (sb, captured) where captured[table_name] = tbl mock.
    """
    captured: dict = {}
    sb = MagicMock()

    def _table(name: str):
        tbl = MagicMock()
        for method in ("select", "eq", "order", "limit"):
            getattr(tbl, method).return_value = tbl
        if name == "clients":
            tbl.execute.return_value = MagicMock(data=clients_data)
        elif name == "core_balance_snapshots":
            tbl.execute.return_value = MagicMock(data=snapshots_data)
        else:
            tbl.execute.return_value = MagicMock(data=[])
        captured[name] = tbl
        return tbl

    sb.table.side_effect = _table
    return sb, captured


class TestEvaluateBalanceActionUUIDToSlugFix:

    def test_slug_resolved_before_snapshot_query(self):
        """core_balance_snapshots must be queried with slug, not UUID."""
        sb, captured = _make_sb(
            clients_data=[{"client_id": _CLIENT_SLUG}],
            snapshots_data=[_SNAP_OK],
        )
        _evaluate_balance_action(_BALANCE_ACTION, sb)

        snap_tbl = captured.get("core_balance_snapshots")
        assert snap_tbl is not None, "core_balance_snapshots was never queried"
        eq_args = [c.args for c in snap_tbl.eq.call_args_list]
        assert ("client_id", _CLIENT_SLUG) in eq_args, "snapshot query must use slug"
        assert ("client_id", _CLIENT_UUID) not in eq_args, "snapshot query must NOT use UUID"

    def test_exhausted_to_ok_returns_positive_high(self):
        """EXHAUSTED before + OK after → POSITIVE, HIGH confidence."""
        sb, _ = _make_sb(
            clients_data=[{"client_id": _CLIENT_SLUG}],
            snapshots_data=[_SNAP_OK],
        )
        result = _evaluate_balance_action(_BALANCE_ACTION, sb)

        assert result["status"] == "POSITIVE"
        assert result["confidence"] == "HIGH"
        assert result["after_state"]["balance"] == pytest.approx(300.28)
        assert result["after_state"]["balance_status"] == "OK"

    def test_snapshot_not_found_returns_blocked_data_quality(self):
        """Slug resolved but no snapshot exists → BLOCKED_DATA_QUALITY."""
        sb, _ = _make_sb(
            clients_data=[{"client_id": _CLIENT_SLUG}],
            snapshots_data=[],
        )
        result = _evaluate_balance_action(_BALANCE_ACTION, sb)

        assert result["status"] == "BLOCKED_DATA_QUALITY"
        assert result["confidence"] == "LOW"

    def test_client_not_found_returns_blocked_data_quality(self):
        """UUID lookup misses (no client row) → fallback UUID → 0 snapshots → BLOCKED."""
        sb, _ = _make_sb(
            clients_data=[],
            snapshots_data=[],
        )
        result = _evaluate_balance_action(_BALANCE_ACTION, sb)

        assert result["status"] == "BLOCKED_DATA_QUALITY"
        assert result["confidence"] == "LOW"

    def test_slug_lookup_exception_no_crash(self):
        """Exception in clients lookup → safe fallback, no crash, BLOCKED_DATA_QUALITY."""
        sb = MagicMock()

        clients_tbl = MagicMock()
        for method in ("select", "eq", "limit"):
            getattr(clients_tbl, method).return_value = clients_tbl
        clients_tbl.execute.side_effect = RuntimeError("connection refused")

        snap_tbl = MagicMock()
        for method in ("select", "eq", "order", "limit"):
            getattr(snap_tbl, method).return_value = snap_tbl
        snap_tbl.execute.return_value = MagicMock(data=[])

        def _table(name):
            return clients_tbl if name == "clients" else snap_tbl

        sb.table.side_effect = _table

        result = _evaluate_balance_action(_BALANCE_ACTION, sb)
        assert result["status"] == "BLOCKED_DATA_QUALITY"
        assert result["confidence"] == "LOW"

    def test_still_exhausted_returns_negative(self):
        """Balance still 0 / EXHAUSTED after action → NEGATIVE."""
        snap_exhausted = {
            "balance":                  0,
            "balance_status":           "EXHAUSTED",
            "estimated_days_remaining": 0,
            "snapshot_at":              "2026-10-24T02:26:00+00:00",
            "collection_status":        "OK",
        }
        sb, _ = _make_sb(
            clients_data=[{"client_id": _CLIENT_SLUG}],
            snapshots_data=[snap_exhausted],
        )
        result = _evaluate_balance_action(_BALANCE_ACTION, sb)

        assert result["status"] == "NEGATIVE"
        assert result["confidence"] == "HIGH"

    def test_collection_not_ok_returns_blocked(self):
        """Snapshot exists but collection_status != OK → BLOCKED_DATA_QUALITY."""
        snap_perm = {**_SNAP_OK, "collection_status": "PERMISSION_DENIED"}
        sb, _ = _make_sb(
            clients_data=[{"client_id": _CLIENT_SLUG}],
            snapshots_data=[snap_perm],
        )
        result = _evaluate_balance_action(_BALANCE_ACTION, sb)

        assert result["status"] == "BLOCKED_DATA_QUALITY"

    def test_clients_table_is_queried(self):
        """clients table must be called — confirms slug resolution path runs."""
        sb, captured = _make_sb(
            clients_data=[{"client_id": _CLIENT_SLUG}],
            snapshots_data=[_SNAP_OK],
        )
        _evaluate_balance_action(_BALANCE_ACTION, sb)

        assert "clients" in captured, "clients table must be queried for slug resolution"


class TestEvaluateGenericActionRegression:

    def test_roas_improvement_positive(self):
        """roas 3.5 → 4.2 (+20%) → POSITIVE."""
        result = _evaluate_generic_action(_GENERIC_ACTION)

        assert result["status"] == "POSITIVE"
        assert result["delta"]["roas"] == pytest.approx(0.7, abs=1e-3)

    def test_no_before_after_inconclusive(self):
        """Empty before/after → INCONCLUSIVE."""
        action = {**_GENERIC_ACTION, "before_state": {}, "after_state": {}}
        result = _evaluate_generic_action(action)

        assert result["status"] == "INCONCLUSIVE"
        assert result["confidence"] == "LOW"

    def test_roas_regression_negative(self):
        """roas 4.2 → 3.5 (~-17%) → NEGATIVE."""
        action = {**_GENERIC_ACTION, "before_state": {"roas": 4.2}, "after_state": {"roas": 3.5}}
        result = _evaluate_generic_action(action)

        assert result["status"] == "NEGATIVE"

    def test_delta_within_neutral_band_neutral(self):
        """roas 4.0 → 4.1 (+2.5%) is within ±5% band → NEUTRAL."""
        action = {**_GENERIC_ACTION, "before_state": {"roas": 4.0}, "after_state": {"roas": 4.1}}
        result = _evaluate_generic_action(action)

        assert result["status"] == "NEUTRAL"
