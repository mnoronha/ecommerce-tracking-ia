"""
Core Pipeline — orchestrates a full Deterministic Core run for a client.

Flow per run:
  1. Load client config + truth versions from DB
  2. For each source_system: collect → DQG → compute metrics → update core_data_sources
  3. Compute derived metrics (total_spend, mer)
  4. Write snapshot to core_metric_snapshots
  5. Reconcile: compare collected aggregates vs written snapshot
  6. Update source reconciliation state
  7. Return PipelineResult

Security:
  - Credentials are decrypted in-memory only (Fernet)
  - No credential values are logged

Operational safety:
  - Does NOT modify legacy cron jobs
  - Does NOT send any ad platform events
  - Does NOT modify Media buy campaigns
  - Does NOT touch Hermes or Platform
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any, Optional

from ..database import get_supabase
from ..services import crypto
from .collectors.business import collect_business
from .collectors.ga4 import collect_ga4
from .collectors.google_ads import collect_google_ads
from .collectors.meta_ads import collect_meta_ads
from .dqg import CollectionResult, DQGReport, run_dqg
from .metric_registry import DERIVED_DEPS, METRIC_REGISTRY
from .reconciler import reconcile_snapshot, update_source_reconciliation
from .snapshot_writer import build_metric_value, write_snapshot

logger = logging.getLogger(__name__)


# ── Result types ──────────────────────────────────────────────────────────────

@dataclass
class SourceResult:
    source_system: str
    source_key: str
    collection: CollectionResult
    dqg: DQGReport
    reconciliation: Optional[dict[str, Any]] = None


@dataclass
class PipelineResult:
    client_id: str
    period_start: date
    period_end: date
    run_id: str
    snapshot_id: Optional[str]
    sources: list[SourceResult] = field(default_factory=list)
    metrics: list[dict[str, Any]] = field(default_factory=list)
    health: dict[str, str] = field(default_factory=dict)
    error: Optional[str] = None

    @property
    def success(self) -> bool:
        return self.snapshot_id is not None and self.error is None

    def summary(self) -> dict[str, Any]:
        return {
            "run_id":       self.run_id,
            "client_id":    self.client_id,
            "period":       f"{self.period_start}→{self.period_end}",
            "snapshot_id":  self.snapshot_id,
            "success":      self.success,
            "sources": {
                s.source_system: {
                    "source_state":  s.dqg.source_state,
                    "all_passed":    s.dqg.all_passed,
                    "reconciliation": s.reconciliation.get("status") if s.reconciliation else None,
                }
                for s in self.sources
            },
            "metrics_count": len(self.metrics),
            "error": self.error,
        }


# ── Source-key helpers ────────────────────────────────────────────────────────

def _source_key(source_system: str, account_id: str) -> str:
    clean = account_id.replace("-", "").replace(" ", "")
    return f"{source_system}:{clean}"


def _google_clean(cid: str) -> str:
    return cid.replace("-", "").replace(" ", "")


# ── core_data_sources updater ─────────────────────────────────────────────────

def _upsert_source_state(
    client_id: str,
    source_key: str,
    source_state: str,
    last_error: Optional[str],
    has_data: bool,
) -> None:
    sb  = get_supabase()
    now = datetime.now(timezone.utc).isoformat()
    row: dict[str, Any] = {
        "source_state":        source_state,
        "last_attempt_at":     now,
        "reconciliation_state": None,  # reset at start of each run; set by reconciler if run
        "updated_at":          now,
    }
    if last_error:
        row["last_error"] = last_error
    else:
        row["last_error"] = None
    if has_data:
        row["last_data_at"]       = now
        row["last_validated_at"]  = now
    try:
        sb.table("core_data_sources").update(row).eq(
            "client_id", client_id
        ).eq("source_key", source_key).execute()
    except Exception as exc:
        logger.warning("pipeline: source state update failed %s/%s: %s", client_id, source_key, exc)


# ── Main pipeline ─────────────────────────────────────────────────────────────

def run_pipeline(
    client_id: str,       # canonical slug e.g. "lk-sneakers"
    period_start: date,
    period_end: date,
    view: str = "live",
) -> PipelineResult:
    run_id = f"core:{client_id}:{period_start.isoformat()}:{uuid.uuid4().hex[:8]}"
    logger.info("pipeline: starting run %s", run_id)

    # ── Load client ───────────────────────────────────────────────────────────
    sb = get_supabase()
    try:
        client_row = (
            sb.table("clients")
            .select(
                "id, client_id, name, timezone, currency, country, business_model, "
                "meta_ad_account_id, meta_access_token, "
                "google_ads_customer_id, google_ads_refresh_token, google_ads_login_customer_id, "
                "ga4_property_id"
            )
            .eq("client_id", client_id)
            .single()
            .execute()
        )
    except Exception as exc:
        logger.error("pipeline: client load failed for %s: %s", client_id, exc)
        return PipelineResult(
            client_id=client_id, period_start=period_start, period_end=period_end,
            run_id=run_id, snapshot_id=None, error=f"client load failed: {exc}",
        )

    c = client_row.data
    if not c:
        return PipelineResult(
            client_id=client_id, period_start=period_start, period_end=period_end,
            run_id=run_id, snapshot_id=None, error=f"client not found: {client_id}",
        )

    crypto.decrypt_client_secrets(c)
    client_uuid = c["id"]
    currency    = c.get("currency") or "BRL"
    tz          = c.get("timezone") or "America/Sao_Paulo"

    # ── Load truth versions ───────────────────────────────────────────────────
    try:
        truth_row = (
            sb.table("core_client_truth")
            .select("client_version, target_version, conversion_map_version")
            .eq("client_id", client_id)
            .order("valid_from", desc=True)
            .limit(1)
            .execute()
        )
        tv = truth_row.data[0] if truth_row.data else {}
    except Exception:
        tv = {}

    ctv = int(tv.get("client_version", 1))
    ttv = int(tv.get("target_version", 1))
    cmv = int(tv.get("conversion_map_version", 1))

    # ── Collect + DQG per source ──────────────────────────────────────────────
    sources:     list[SourceResult] = []
    all_aggs:    dict[str, float]   = {}
    health:      dict[str, str]     = {}

    # — Meta Ads ───────────────────────────────────────────────────────────────
    if c.get("meta_ad_account_id") and c.get("meta_access_token"):
        acct_id  = c["meta_ad_account_id"]
        src_key  = _source_key("meta_ads", acct_id)
        coll     = collect_meta_ads(
            account_id=acct_id,
            access_token=c["meta_access_token"],
            period_start=period_start,
            period_end=period_end,
            client_currency=currency,
            client_timezone=tz,
        )
        dqg = run_dqg(coll)
        _upsert_source_state(
            client_id, src_key, dqg.source_state, coll.error, dqg.has_data,
        )
        health["meta_ads"] = dqg.source_state
        if dqg.source_state in ("READY", "PARTIAL"):
            all_aggs.update(coll.aggregates)
        sources.append(SourceResult("meta_ads", src_key, coll, dqg))
    else:
        health["meta_ads"] = "NOT_CONTRACTED"

    # — Google Ads ─────────────────────────────────────────────────────────────
    if c.get("google_ads_customer_id") and c.get("google_ads_refresh_token"):
        cid     = c["google_ads_customer_id"]
        manager = c.get("google_ads_login_customer_id") or None
        src_key = _source_key("google_ads", _google_clean(cid))
        coll    = collect_google_ads(
            customer_id=cid,
            refresh_token=c["google_ads_refresh_token"],
            period_start=period_start,
            period_end=period_end,
            manager_id=manager,
            client_currency=currency,
            client_timezone=tz,
        )
        dqg = run_dqg(coll)
        _upsert_source_state(
            client_id, src_key, dqg.source_state, coll.error, dqg.has_data,
        )
        health["google_ads"] = dqg.source_state
        if dqg.source_state in ("READY", "PARTIAL"):
            all_aggs.update(coll.aggregates)
        sources.append(SourceResult("google_ads", src_key, coll, dqg))
    else:
        health["google_ads"] = "NOT_CONTRACTED"

    # — GA4 ────────────────────────────────────────────────────────────────────
    if c.get("ga4_property_id") and c.get("google_ads_refresh_token"):
        prop_id = c["ga4_property_id"]
        src_key = f"ga4:{prop_id}"
        coll    = collect_ga4(
            property_id=prop_id,
            refresh_token=c["google_ads_refresh_token"],
            period_start=period_start,
            period_end=period_end,
            client_currency=currency,
            client_timezone=tz,
        )
        dqg = run_dqg(coll)
        _upsert_source_state(
            client_id, src_key, dqg.source_state, coll.error, dqg.has_data,
        )
        health["ga4"] = dqg.source_state
        if dqg.source_state in ("READY", "PARTIAL"):
            all_aggs.update(coll.aggregates)
        sources.append(SourceResult("ga4", src_key, coll, dqg))
    else:
        health["ga4"] = "NOT_CONTRACTED"

    # — Business (Shopify) ─────────────────────────────────────────────────────
    src_key = f"shopify:{client_id}"
    coll    = collect_business(
        client_uuid=client_uuid,
        client_id=client_id,
        period_start=period_start,
        period_end=period_end,
        client_currency=currency,
        client_timezone=tz,
    )
    dqg = run_dqg(coll)
    _upsert_source_state(
        client_id, src_key, dqg.source_state, coll.error, dqg.has_data,
    )
    health["business"] = dqg.source_state
    # Business: 0 orders is valid data — include even with source_state=NO_DATA
    all_aggs.update(coll.aggregates)
    sources.append(SourceResult("shopify", src_key, coll, dqg))

    # ── Derived metrics ───────────────────────────────────────────────────────
    meta_spend   = all_aggs.get("meta_spend",   0.0)
    google_spend = all_aggs.get("google_spend", 0.0)
    total_spend  = round(meta_spend + google_spend, 2)
    all_aggs["total_spend"] = total_spend

    rev = all_aggs.get("revenue_business", 0.0)
    if total_spend > 0:
        all_aggs["mer"] = round(rev / total_spend, 4)
    else:
        all_aggs["mer"] = None  # type: ignore[assignment]

    # ── Build metric values ───────────────────────────────────────────────────
    metric_values: list[dict[str, Any]] = []
    # source_system → health domain key (health uses domain names, not source_system)
    _sys_to_domain = {
        "shopify":    "business",
        "meta_ads":   "meta_ads",
        "google_ads": "google_ads",
        "ga4":        "ga4",
    }

    for m_def in METRIC_REGISTRY.values():
        domain_key = _sys_to_domain.get(m_def.source_system, m_def.source_system)
        src_state = health.get(
            domain_key,
            "NOT_CONTRACTED" if m_def.aggregation != "derived" else "READY",
        )
        if m_def.aggregation == "derived":
            # Use worst state of deps regardless of whether they appear in all_aggs.
            # A dep absent from all_aggs means its source was not READY/PARTIAL,
            # so the derived metric is at least as degraded as that dep's source.
            dep_states = [
                health.get(_dep_source(k), "MISSING")
                for k in DERIVED_DEPS.get(m_def.key, [])
            ]
            src_state = _worst_state(dep_states) if dep_states else "READY"
            # Register this derived metric's state so downstream derived deps
            # (e.g. mer depends on total_spend) can find it in health.
            health[m_def.key] = src_state

        raw_value = all_aggs.get(m_def.key)
        v_status  = _source_state_to_value_status(src_state, raw_value)

        mv = build_metric_value(
            metric_key=m_def.key,
            value=raw_value if v_status in ("OK", "PARTIAL") else None,
            unit=m_def.unit,
            value_status=v_status,
            currency=m_def.currency,
            domain=m_def.domain.value,
            certification_status="PROVISIONAL",
        )
        metric_values.append(mv)

    # ── Write snapshot ────────────────────────────────────────────────────────
    try:
        snap_id = write_snapshot(
            client_id=client_id,
            period_start=period_start,
            period_end=period_end,
            view=view,
            metrics=metric_values,
            health=health,
            client_truth_version=ctv,
            target_truth_version=ttv,
            conversion_map_version=cmv,
            source_run_id=run_id,
        )
    except Exception as exc:
        logger.error("pipeline: snapshot write failed for %s: %s", client_id, exc)
        return PipelineResult(
            client_id=client_id, period_start=period_start, period_end=period_end,
            run_id=run_id, snapshot_id=None, sources=sources,
            metrics=metric_values, health=health,
            error=f"snapshot write failed: {exc}",
        )

    # ── Reconcile per source ──────────────────────────────────────────────────
    for sr in sources:
        # Skip reconciliation when source had no data — nothing meaningful to compare
        if sr.dqg.source_state not in ("READY", "PARTIAL"):
            sr.dqg.add_reconciliation_result(
                passed=True,
                detail=f"skipped: source_state={sr.dqg.source_state}",
            )
            continue

        recon = reconcile_snapshot(
            snapshot_id=snap_id,
            source_system=sr.source_system,
            collected_aggregates=sr.collection.aggregates,
            written_metrics=metric_values,
        )
        sr.reconciliation = recon
        recon_ok = recon["status"] == "OK"
        sr.dqg.add_reconciliation_result(
            passed=recon_ok,
            detail=None if recon_ok else str(recon.get("metrics", {})),
        )
        update_source_reconciliation(
            client_id=client_id,
            source_key=sr.source_key,
            recon_status=recon["status"],
            recon_detail=str(recon.get("metrics", {})) if not recon_ok else None,
        )
        if not recon_ok:
            logger.warning(
                "pipeline: RECONCILIATION_MISMATCH %s/%s: %s",
                client_id, sr.source_system, recon.get("metrics"),
            )

    logger.info("pipeline: run %s complete — snapshot %s", run_id, snap_id)
    return PipelineResult(
        client_id=client_id,
        period_start=period_start,
        period_end=period_end,
        run_id=run_id,
        snapshot_id=snap_id,
        sources=sources,
        metrics=metric_values,
        health=health,
    )


# ── Helpers ───────────────────────────────────────────────────────────────────

def _dep_source(metric_key: str) -> str:
    """Map a metric_key to its health-lookup key.

    For derived metrics (source_system="derived"), return the metric_key itself
    so the health dict can be populated as derived states are computed.
    For source metrics, map to the semantic domain key used in the health dict.
    """
    m = METRIC_REGISTRY.get(metric_key)
    if not m:
        return "MISSING"
    if m.source_system == "derived":
        # Derived metrics are registered in health under their own key,
        # not under "derived" (which is never in the health dict).
        return metric_key
    mapping = {
        "shopify":    "business",
        "meta_ads":   "meta_ads",
        "google_ads": "google_ads",
        "ga4":        "ga4",
    }
    return mapping.get(m.source_system, m.source_system)


_STATE_RANK = {
    "READY": 0, "NO_DATA": 1, "STALE": 2, "PARTIAL": 3,
    "ACCESS_MISSING": 4, "PERMISSION_DENIED": 4, "NOT_CONTRACTED": 5,
    "MISSING": 6, "ERROR": 7,
}


def _worst_state(states: list[str]) -> str:
    if not states:
        return "READY"
    return max(states, key=lambda s: _STATE_RANK.get(s, 6))


def _source_state_to_value_status(source_state: str, value: Any) -> str:
    mapping = {
        "READY":             "OK",
        "PARTIAL":           "PARTIAL",
        "STALE":             "STALE",
        "NO_DATA":           "NO_DATA",
        "MISSING":           "MISSING",
        "ACCESS_MISSING":    "MISSING",
        "PERMISSION_DENIED": "MISSING",
        "NOT_CONTRACTED":    "NOT_APPLICABLE",
        "NOT_APPLICABLE":    "NOT_APPLICABLE",
        "ERROR":             "MISSING",
    }
    vs = mapping.get(source_state, "UNKNOWN")
    # Never map None value to OK — absence check
    if vs == "OK" and value is None:
        return "NO_DATA"
    return vs
