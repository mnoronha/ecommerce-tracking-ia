"""
Snapshot quality selector for monthly review.

When multiple snapshots exist for the same (client, period_end), selects the
best one based on health state rather than recency alone. A newer degraded
snapshot must not supersede an older healthy one.

Ranking (lower tuple = better, used with min()):
  1. Fewer degraded sources (ERROR/ACCESS_MISSING/PERMISSION_DENIED/MISSING/STALE)
  2. More READY sources (prefer complete over partial)
  3. Newer computed_at as tiebreak (prefer latest when quality is equal)
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

_DEGRADED: frozenset[str] = frozenset({
    "ERROR", "ACCESS_MISSING", "PERMISSION_DENIED", "MISSING", "STALE",
})


def _count_degraded(snapshot: dict) -> int:
    health = snapshot.get("health") or {}
    return sum(1 for s in health.values() if s in _DEGRADED)


def _count_ready(snapshot: dict) -> int:
    health = snapshot.get("health") or {}
    return sum(1 for s in health.values() if s == "READY")


def _parse_ts(snapshot: dict) -> float:
    ts_str = snapshot.get("computed_at") or ""
    if not ts_str:
        return 0.0
    try:
        ts_str = ts_str.replace("Z", "+00:00")
        return datetime.fromisoformat(ts_str).timestamp()
    except Exception:
        return 0.0


def select_best_snapshot(snapshots: list[dict]) -> Optional[dict]:
    """Return the highest-quality snapshot from a list.

    Never deletes snapshots. Only selects.
    Returns None if the list is empty.
    """
    if not snapshots:
        return None
    return min(
        snapshots,
        key=lambda s: (
            _count_degraded(s),
            -_count_ready(s),
            -_parse_ts(s),
        ),
    )
