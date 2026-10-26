"""Core-only closed-month materialization using existing collectors and DQG.

Range-level campaign totals must never overwrite daily campaign facts. This
entry point writes canonical snapshots/source provenance through the existing
pipeline, with campaign persistence disabled. It does not publish reports.

Policy: BACKFILL_FROM_PROVIDER_READ_ONLY — no silent legacy fallback.
Idempotent: if a healthy canonical snapshot already exists for the period,
the pipeline is skipped and the existing snapshot_id is returned.
"""
from __future__ import annotations

from calendar import monthrange
from datetime import date, datetime
from typing import Optional
from zoneinfo import ZoneInfo

from .pipeline import PipelineResult, run_pipeline

BACKFILL_FROM_PROVIDER_READ_ONLY = "BACKFILL_FROM_PROVIDER_READ_ONLY"

_DEGRADED: frozenset[str] = frozenset({
    "ERROR", "ACCESS_MISSING", "PERMISSION_DENIED", "MISSING", "STALE",
})


def _load_existing_snapshot(client_id: str, period_start: date, period_end: date) -> Optional[dict]:
    """Load the best existing full-month snapshot for (client, period_start, period_end), or None.

    Requires BOTH period_start and period_end to match so that a 7-day rolling window
    ending on the last day of the month is not mistaken for a complete monthly snapshot.
    """
    from .snapshot_selector import select_best_snapshot
    from ..database import get_supabase
    try:
        sb = get_supabase()
        res = (
            sb.table("core_metric_snapshots")
            .select("id, health, computed_at, period_start, period_end")
            .eq("client_id", client_id)
            .eq("period_start", period_start.isoformat())
            .eq("period_end", period_end.isoformat())
            .execute()
        )
        snapshots = res.data or []
        return select_best_snapshot(snapshots) if snapshots else None
    except Exception:
        return None


def _is_degraded(snapshot: dict) -> bool:
    health = snapshot.get("health") or {}
    return any(s in _DEGRADED for s in health.values())


def materialize_closed_month(
    client_id: str,
    year: int,
    month: int,
    *,
    timezone: str,
    today: date | None = None,
) -> PipelineResult:
    """Materialize a closed calendar month via the Core pipeline.

    Idempotent: returns existing snapshot_id if one is already healthy.
    Policy: BACKFILL_FROM_PROVIDER_READ_ONLY — no silent legacy fallback.
    Raises ValueError("MONTH_NOT_CLOSED") if the month has not ended yet.
    """
    start = date(year, month, 1)
    end = date(year, month, monthrange(year, month)[1])
    local_today = today if today is not None else datetime.now(ZoneInfo(timezone)).date()
    if end >= local_today:
        raise ValueError("MONTH_NOT_CLOSED")

    # Idempotency: skip pipeline if a healthy full-month snapshot already exists.
    # Uses both period_start and period_end to avoid accepting a 7-day rolling
    # window that ends on the last day of the month as a completed monthly snapshot.
    existing = _load_existing_snapshot(client_id, start, end)
    if existing and not _is_degraded(existing):
        run_id = f"backfill:idempotent:{client_id}:{start.isoformat()}"
        return PipelineResult(
            client_id=client_id,
            period_start=start,
            period_end=end,
            run_id=run_id,
            snapshot_id=str(existing["id"]),
        )

    return run_pipeline(
        client_id, start, end, view="live", persist_campaign_rows=False,
    )
