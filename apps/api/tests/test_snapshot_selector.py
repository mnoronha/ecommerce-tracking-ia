"""Unit tests for snapshot_selector.select_best_snapshot."""
import pytest
from app.core.snapshot_selector import select_best_snapshot


def _snap(health: dict, computed_at: str = "2026-01-01T00:00:00+00:00", snap_id: str = "id1") -> dict:
    return {"id": snap_id, "health": health, "computed_at": computed_at}


class TestSelectBestSnapshot:
    def test_empty_list_returns_none(self):
        assert select_best_snapshot([]) is None

    def test_single_snapshot_returned(self):
        s = _snap({"meta_ads": "READY", "google_ads": "READY", "business": "READY"})
        assert select_best_snapshot([s]) is s

    def test_fully_ready_beats_newer_degraded(self):
        good = _snap(
            {"meta_ads": "READY", "google_ads": "READY", "business": "READY"},
            computed_at="2026-09-01T00:00:00+00:00",
            snap_id="good",
        )
        bad = _snap(
            {"meta_ads": "ERROR", "google_ads": "READY", "business": "READY"},
            computed_at="2026-09-02T00:00:00+00:00",
            snap_id="bad",
        )
        result = select_best_snapshot([good, bad])
        assert result["id"] == "good"

    def test_fewer_degraded_wins_over_newer(self):
        older_good = _snap(
            {"meta_ads": "READY", "google_ads": "READY", "business": "READY"},
            computed_at="2026-09-01T00:00:00+00:00",
            snap_id="old_good",
        )
        newer_bad = _snap(
            {"meta_ads": "READY", "google_ads": "ERROR", "business": "READY"},
            computed_at="2026-09-02T00:00:00+00:00",
            snap_id="new_bad",
        )
        result = select_best_snapshot([older_good, newer_bad])
        assert result["id"] == "old_good"

    def test_more_ready_beats_same_degraded_count(self):
        # Both have 1 degraded; a has 2 READY, b has 1 READY
        a = _snap(
            {"meta_ads": "READY", "google_ads": "READY", "business": "STALE"},
            computed_at="2026-09-01T00:00:00+00:00",
            snap_id="a",
        )
        b = _snap(
            {"meta_ads": "READY", "google_ads": "NOT_CONTRACTED", "business": "STALE"},
            computed_at="2026-09-02T00:00:00+00:00",
            snap_id="b",
        )
        result = select_best_snapshot([a, b])
        assert result["id"] == "a"

    def test_newer_timestamp_as_tiebreak(self):
        old = _snap(
            {"meta_ads": "READY", "google_ads": "READY"},
            computed_at="2026-09-01T00:00:00+00:00",
            snap_id="old",
        )
        new = _snap(
            {"meta_ads": "READY", "google_ads": "READY"},
            computed_at="2026-09-02T00:00:00+00:00",
            snap_id="new",
        )
        result = select_best_snapshot([old, new])
        assert result["id"] == "new"

    def test_all_degraded_returns_least_bad(self):
        two_bad = _snap(
            {"meta_ads": "ERROR", "google_ads": "ERROR", "business": "READY"},
            snap_id="two",
        )
        one_bad = _snap(
            {"meta_ads": "ERROR", "google_ads": "READY", "business": "READY"},
            snap_id="one",
        )
        result = select_best_snapshot([two_bad, one_bad])
        assert result["id"] == "one"

    def test_degraded_states_coverage(self):
        # Every member of _DEGRADED must count against quality
        for state in ("ERROR", "ACCESS_MISSING", "PERMISSION_DENIED", "MISSING", "STALE"):
            clean = _snap({"meta_ads": "READY"}, snap_id="clean")
            dirty = _snap({"meta_ads": state}, snap_id="dirty")
            result = select_best_snapshot([dirty, clean])
            assert result["id"] == "clean", f"state={state} should be degraded"

    def test_not_contracted_is_not_degraded(self):
        # NOT_CONTRACTED is expected (client doesn't use that channel); not a degradation
        a = _snap({"meta_ads": "NOT_CONTRACTED", "google_ads": "READY"}, snap_id="no_meta")
        b = _snap({"meta_ads": "READY", "google_ads": "ERROR"}, snap_id="meta_ok_google_bad")
        result = select_best_snapshot([a, b])
        assert result["id"] == "no_meta"

    def test_missing_health_key_treated_as_zero_degraded(self):
        empty_health = {"id": "empty", "health": {}, "computed_at": "2026-01-01T00:00:00+00:00"}
        with_ready = _snap({"meta_ads": "READY"}, snap_id="with_ready")
        # Empty health has 0 degraded, 0 ready; with_ready has 0 degraded, 1 ready
        result = select_best_snapshot([empty_health, with_ready])
        assert result["id"] == "with_ready"

    def test_z_suffix_timestamp_parsed(self):
        z = _snap({"meta_ads": "READY"}, computed_at="2026-09-10T12:00:00Z", snap_id="z")
        plus = _snap({"meta_ads": "READY"}, computed_at="2026-09-09T12:00:00+00:00", snap_id="plus")
        result = select_best_snapshot([z, plus])
        assert result["id"] == "z"
