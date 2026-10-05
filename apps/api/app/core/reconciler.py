"""
Reconciler — post-write verification of core_metric_snapshots.

Validates that aggregated values in the snapshot match what collectors
computed. Detects divergence in spend, conversions, conversion_value.

If delta exceeds TOLERANCE_PCT:
  - reconciliation_state → RECONCILIATION_MISMATCH in core_data_sources
  - RECONCILIATION_OK check → False in DQGReport

Snapshot values are never modified; only core_data_sources metadata is updated.
Business revenue and attributed revenue (ads conversion_value) remain
semantically separate — they are reconciled independently.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from ..database import get_supabase

logger = logging.getLogger(__name__)

TOLERANCE_PCT = 0.01  # 1% tolerance

# Metrics reconciled per source_system
_RECONCILE_METRICS: dict[str, list[str]] = {
    "meta_ads":   ["meta_spend", "meta_conversions", "meta_conversion_value"],
    "google_ads": ["google_spend", "google_conversions", "google_conversion_value"],
    "ga4":        ["ga4_sessions", "ga4_purchases", "ga4_revenue"],
    "shopify":    ["revenue_business", "orders_count"],
}


def reconcile_snapshot(
    snapshot_id: str,
    source_system: str,
    collected_aggregates: dict[str, float],
    written_metrics: list[dict[str, Any]],
    tolerance_pct: float = TOLERANCE_PCT,
) -> dict[str, Any]:
    """
    Compare collected totals against what was written in the snapshot.

    Returns a reconciliation report dict with per-metric results and
    an overall status of "OK" or "RECONCILIATION_MISMATCH".
    """
    keys_to_check = _RECONCILE_METRICS.get(source_system, [])
    written_map   = {m["metric_key"]: m.get("value") for m in written_metrics}

    results: dict[str, Any] = {}
    overall_ok = True

    for key in keys_to_check:
        collected_val = collected_aggregates.get(key)
        written_val   = written_map.get(key)

        if collected_val is None:
            results[key] = {"status": "SKIP", "reason": "not in collected aggregates"}
            continue
        if written_val is None:
            results[key] = {"status": "MISSING_IN_SNAPSHOT"}
            overall_ok = False
            continue

        if collected_val == 0 and written_val == 0:
            results[key] = {"status": "OK", "delta_abs": 0.0, "delta_pct": 0.0}
            continue

        delta_abs = abs(float(written_val) - float(collected_val))
        denom     = max(abs(float(collected_val)), 1e-9)
        delta_pct = delta_abs / denom

        ok = delta_pct <= tolerance_pct
        if not ok:
            overall_ok = False

        results[key] = {
            "status":      "OK" if ok else "RECONCILIATION_MISMATCH",
            "collected":   collected_val,
            "written":     written_val,
            "delta_abs":   round(delta_abs, 4),
            "delta_pct":   round(delta_pct * 100, 3),
            "tolerance":   round(tolerance_pct * 100, 1),
        }

    status = "OK" if overall_ok else "RECONCILIATION_MISMATCH"
    logger.info(
        "reconciler: snapshot=%s source=%s status=%s",
        snapshot_id, source_system, status,
    )
    return {"status": status, "metrics": results}


def update_source_reconciliation(
    client_id: str,
    source_key: str,
    recon_status: str,
    recon_detail: str | None = None,
) -> None:
    """Update core_data_sources with reconciliation outcome."""
    sb = get_supabase()
    now = datetime.now(timezone.utc).isoformat()
    try:
        sb.table("core_data_sources").update({
            "reconciliation_state": recon_status,
            "last_reconciled_at":   now,
            "last_error":           recon_detail if recon_status == "RECONCILIATION_MISMATCH" else None,
            "updated_at":           now,
        }).eq("client_id", client_id).eq("source_key", source_key).execute()
    except Exception as exc:
        logger.warning("reconciler: update failed for %s/%s: %s", client_id, source_key, exc)
