"""
Snapshot Writer — append-only writer for core_metric_snapshots.

Rules:
  - Every write is INSERT, never UPDATE of metric values.
  - schema_version is embedded in every snapshot.
  - Older snapshots for the same period are NOT deleted — supersession
    is implicit (GET /metrics returns latest by computed_at DESC).
  - Hermes never calls this directly.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from typing import Any, Literal

from ..database import get_supabase

logger = logging.getLogger(__name__)

_SCHEMA_VERSION = "1.1"


def write_snapshot(
    client_id: str,
    period_start: date,
    period_end: date,
    view: Literal["live", "certified"],
    metrics: list[dict[str, Any]],
    health: dict[str, str],          # semantic_domain → SourceState string
    client_truth_version: int = 1,
    target_truth_version: int = 1,
    conversion_map_version: int = 1,
    source_run_id: str | None = None,
) -> str:
    """
    Insert one row into core_metric_snapshots.

    Args:
        metrics: list of MetricValue-compatible dicts
        health:  domain → SourceState string mapping

    Returns:
        UUID of the newly created snapshot row.

    Raises:
        RuntimeError on DB insert failure.
    """
    sb = get_supabase()

    row = {
        "client_id":              client_id,
        "period_start":           period_start.isoformat(),
        "period_end":             period_end.isoformat(),
        "view":                   view,
        "client_truth_version":   client_truth_version,
        "target_truth_version":   target_truth_version,
        "conversion_map_version": conversion_map_version,
        "metrics":                metrics,
        "health":                 health,
        "computed_at":            datetime.now(timezone.utc).isoformat(),
        "source_run_id":          source_run_id,
    }

    try:
        result = sb.table("core_metric_snapshots").insert(row).execute()
    except Exception as exc:
        logger.error("snapshot_writer: insert failed for %s: %s", client_id, exc)
        raise RuntimeError(f"snapshot insert failed: {exc}") from exc

    if not result.data:
        raise RuntimeError("snapshot insert returned no data")

    snap_id = result.data[0]["id"]
    logger.info(
        "snapshot_writer: created %s view=%s period=%s→%s metrics=%d",
        snap_id, view, period_start, period_end, len(metrics),
    )
    return snap_id


def build_metric_value(
    metric_key: str,
    value: float | None,
    unit: str,
    value_status: str,
    currency: str | None = None,
    domain: str | None = None,
    certification_status: str = "PROVISIONAL",
    reason_code: str | None = None,
    target: float | None = None,
    target_ratio: float | None = None,
    target_status: str | None = None,
) -> dict[str, Any]:
    """
    Build a MetricValue dict for insertion into the metrics JSONB array.
    snapshot_ids is left empty — populated externally if cross-referencing.
    """
    mv: dict[str, Any] = {
        "metric_key":           metric_key,
        "value":                value,
        "unit":                 unit,
        "value_status":         value_status,
        "certification_status": certification_status,
        "snapshot_ids":         [],
    }
    if currency is not None:
        mv["currency"] = currency
    if domain is not None:
        mv["domain"] = domain
    if reason_code is not None:
        mv["reason_code"] = reason_code
    if target is not None:
        mv["target"] = target
    if target_ratio is not None:
        mv["target_ratio"] = target_ratio
    if target_status is not None:
        mv["target_status"] = target_status
    return mv
