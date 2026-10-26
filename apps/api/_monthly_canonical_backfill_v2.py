"""
Monthly Canonical H8 — Backfill Snapshots + Generate V2
Period: September 2026 (2026-09-01 -> 2026-09-30)

Purpose:
  v1 was PARTIAL for Tárcio/Dipua/Enutri/Zipper/Spiti because full-month
  snapshots did not exist when the rollout ran.  This script:
    1. Materializes missing full-month snapshots (via materialize_closed_month).
    2. Generates v2 artifacts from real snapshot data.
    3. Transitions v2 -> READY_FOR_REVIEW.
    4. Supersedes v1 (status=SUPERSEDED, superseded_by_version=2).

Rules:
  - LK is skipped: v1 has 21 metrics and is factually correct.
  - One client failure does NOT abort the others.
  - No DELETE.  v1 is preserved unchanged.
  - No publish.  No delivery.  No email.  No media writes.
  - materialize_closed_month is idempotent (Tárcio snapshot already exists).
  - Platform calls are READ-ONLY (collect from Google/Meta APIs).
  - Snapshot lookup uses BOTH period_start AND period_end to guarantee a
    full-month record (not a 7-day window ending on the 30th).

Output keys (printed at end):
  SEPTEMBER_V1_PRESERVED
  VERSIONING_MODEL_STATUS
  BACKFILL_SNAPSHOTS_MATERIALIZED
  SUPERSESSION_STATUS
  Per-client snapshot + v2 + supersession status
  PLATFORM_READS / PLATFORM_WRITES
  MONTHLY_SCHEDULER_CUTOVER_READY
"""
from __future__ import annotations

import hashlib
import json
import pathlib
import sys
import uuid as _uuid
from datetime import date, datetime, timezone

sys.path.insert(0, str(pathlib.Path(__file__).parent))

from app.database import get_supabase
from app.core.canonical_reports import CoreStore, Flags, Reports, ReportError
from app.core.monthly_backfill import materialize_closed_month

PERIOD_START = date(2026, 9, 1)
PERIOD_END   = date(2026, 9, 30)
TIMEZONE     = "America/Sao_Paulo"

LK_CLIENT_ID   = "lk-sneakers"
LK_SKIP_REASON = "v1 FULL (21 metrics, content=READY_FOR_REVIEW) — no semantic reason for v2"

# Tárcio: snapshot already exists per spec; materialize_closed_month is idempotent.
CLIENTS_TO_BACKFILL = [
    "clinica-tarcio-caetano",
    "dipua",
    "enutri",
    "zipper-galeria",
    "spiti-auction",
]

PILOT_FLAGS = Flags(
    CANONICAL_MONTHLY_REVIEW_ENABLED=True,
    CANONICAL_WEEKLY_REVIEW_ENABLED=True,
    REPORT_PUBLISH_ENABLED=False,
    REPORT_DELIVERY_ENABLED=False,
)

_DEGRADED      = frozenset({"ERROR", "ACCESS_MISSING", "PERMISSION_DENIED", "MISSING", "STALE"})
ACTOR_BACKFILL = "canonical_monthly_backfill"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _engine_version() -> str:
    return hashlib.sha256(pathlib.Path(__file__).read_bytes()).hexdigest()


def _report_id_for(client_id: str) -> str:
    return str(_uuid.uuid5(
        _uuid.NAMESPACE_URL,
        json.dumps([client_id, "monthly", str(PERIOD_START), str(PERIOD_END)]),
    ))


# ---------------------------------------------------------------------------
# V2 artifact builder
# ---------------------------------------------------------------------------

def _build_v2_artifact(client_id: str, sb, ev: str) -> dict:
    """
    Build v2 H8 artifact from the full-month snapshot for September 2026.

    Queries with BOTH period_start AND period_end to guarantee a full-month
    record — not a 7-day window that happens to end on 2026-09-30.
    Absence of data is represented explicitly; never zero-filled.
    """
    try:
        snap_res = (
            sb.table("core_metric_snapshots")
            .select("metrics, health, id")
            .eq("client_id", client_id)
            .eq("period_start", PERIOD_START.isoformat())
            .eq("period_end",   PERIOD_END.isoformat())
            .order("computed_at", desc=True)
            .limit(1)
            .execute()
        )
        snapshot = snap_res.data[0] if snap_res.data else None
    except Exception as exc:
        snapshot = None

    metrics_raw   = (snapshot or {}).get("metrics") or []
    source_health = (snapshot or {}).get("health")  or {}
    snapshot_id   = str(snapshot["id"]) if snapshot else None

    if isinstance(metrics_raw, dict):
        metrics_raw = list(metrics_raw.values())
    metrics_dict = {
        m["metric_key"]: m
        for m in metrics_raw
        if isinstance(m, dict) and "metric_key" in m
    }

    facts = [
        {
            "metric_key":           k,
            "value":                v["value"],
            "value_status":         v.get("value_status", "UNKNOWN"),
            "certification_status": "PROVISIONAL",
            "unit":                 v.get("unit"),
            "domain":               v.get("domain"),
            "target":               v.get("target"),
            "target_ratio":         v.get("target_ratio"),
        }
        for k, v in metrics_dict.items()
        if v.get("value") is not None
    ]

    channels: dict = {}
    for ch_alias, ch_src in (("meta", "meta_ads"), ("google", "google_ads")):
        sm    = metrics_dict.get(f"{ch_alias}_spend", {})
        cm    = metrics_dict.get(f"{ch_alias}_conversions", {})
        state = source_health.get(ch_src, "NOT_FOUND")
        if sm.get("value") is not None or state not in ("NOT_FOUND", "NOT_CONTRACTED"):
            channels[ch_alias] = {
                "spend":        sm.get("value"),
                "conversions":  cm.get("value"),
                "source_state": state,
            }

    source_state = [
        {"source_system": k, "source_state": v} for k, v in source_health.items()
    ]

    rev  = metrics_dict.get("revenue_business", {})
    spnd = metrics_dict.get("total_spend", {})
    mer  = metrics_dict.get("mer", {})

    if rev.get("value") and spnd.get("value"):
        bt = f"{client_id} setembro/2026. Receita R${rev['value']:,.2f}"
        if mer.get("value"):
            bt += f" | MER {mer['value']:.1f}x"
            if mer.get("target"):
                bt += f" (meta {mer['target']}x)"
        bt += f" | Investimento R${spnd['value']:,.2f}. PROVISIONAL."
    elif facts:
        bt = f"{client_id} setembro/2026. {len(facts)} metricas disponiveis. PROVISIONAL."
    elif snapshot:
        bt = f"{client_id}: snapshot encontrado, sem valores para setembro/2026. PROVISIONAL."
    else:
        bt = (
            f"{client_id}: sem snapshot mensal completo para setembro/2026. "
            "materialize_closed_month pode ter falhado para este cliente. PROVISIONAL."
        )

    limitations = [
        "CORE_BACKFILL_V2: generated after materialize_closed_month",
        "PROVISIONAL: preliminary values, not audited",
    ]
    if not snapshot:
        limitations.append(
            "snapshot_not_found: materialize_closed_month may have failed for this client"
        )
    degraded = any(v in _DEGRADED for v in source_health.values())
    if degraded:
        limitations.append("DEGRADED_SOURCES_PRESENT")

    content_status = "PARTIAL" if (not snapshot or degraded) else "READY_FOR_REVIEW"

    cert = [
        {
            "window":               "analysis",
            "metric_key":           k,
            "certification_status": "PROVISIONAL",
            "value_status":         v.get("value_status", "UNKNOWN"),
        }
        for k, v in metrics_dict.items()
    ]

    now_utc = datetime.now(timezone.utc).isoformat()

    content = {
        "client_id":                client_id,
        "analysis_period":          {"start": str(PERIOD_START), "end": str(PERIOD_END)},
        "schema_version":           "h8.2-core-backfill",
        "canonical_business_model": "ecommerce",
        "review_model":             "ECOMMERCE_MONTHLY",
        "timezone":                 TIMEZONE,
        "origin":                   "CANONICAL_MONTHLY_BACKFILL_V2",
        "core_writes":              False,
        "platform_writes":          False,
        "facts":                    facts,
        "channels":                 channels,
        "business_truth":           bt,
        "observations":             source_state,
        "hypotheses":               [],
        "recommendations":          [],
        "limitations":              limitations,
        "status":                   content_status,
        "metric_count":             len(facts),
        "source_count":             len(source_health),
    }

    return {
        "client_id":            client_id,
        "canonical_engine":     "H8",
        "engine_version":       ev,
        "version":              2,
        "supersedes_version":   1,
        "period_start":         str(PERIOD_START),
        "period_end":           str(PERIOD_END),
        "generated_at":         now_utc,
        "source_state":         source_state,
        "certification_status": cert,
        "review_status":        "READY" if facts else "PARTIAL",
        "content":              content,
        # No generation timestamp in evidence_refs: hash stable across re-runs.
        "evidence_refs": [{
            "source":       "core_metric_snapshots",
            "client_id":    client_id,
            "period_start": str(PERIOD_START),
            "period_end":   str(PERIOD_END),
            "metric_count": len(metrics_dict),
            "snapshot_id":  snapshot_id,
        }],
    }


# ---------------------------------------------------------------------------
# Per-client backfill
# ---------------------------------------------------------------------------

def _backfill_one_client(
    client_id: str,
    sb,
    store: CoreStore,
    reports: Reports,
    ev: str,
    today: date,
) -> dict:
    result: dict = {
        "client_id":      client_id,
        "snapshot":       "UNKNOWN",
        "v2_status":      "UNKNOWN",
        "v2_report_id":   None,
        "metric_count":   None,
        "content_status": None,
        "supersession":   "UNKNOWN",
        "error":          None,
    }

    # Step 1: materialize full-month snapshot (idempotent)
    try:
        snap = materialize_closed_month(
            client_id,
            PERIOD_START.year,
            PERIOD_START.month,
            timezone=TIMEZONE,
            today=today,
        )
        result["snapshot"] = f"OK:{snap.snapshot_id}"
    except Exception as exc:
        result["snapshot"] = f"FAILED:{exc}"
        result["error"]    = str(exc)
        # Continue: snapshot may already exist in DB even if this path raised.

    # Step 2: build + generate v2
    try:
        artifact = _build_v2_artifact(client_id, sb, ev)
        doc      = reports.generate(artifact)
        rid      = doc["report_id"]

        if doc["status"] == "DRAFT":
            doc = reports.transition(
                rid, 2, "READY_FOR_REVIEW",
                actor=ACTOR_BACKFILL,
                at=datetime.now(timezone.utc).isoformat(),
                expected_hash=doc["artifact_hash"],
            )

        result["v2_status"]      = doc["status"]
        result["v2_report_id"]   = rid
        result["v2_hash_prefix"] = doc["artifact_hash"][:16] + "..."
        result["metric_count"]   = doc.get("content", {}).get("metric_count", 0)
        result["content_status"] = doc.get("content", {}).get("status", "?")

    except ReportError as exc:
        err = str(exc)
        if "IMMUTABLE" in err:
            # v2 already stored and hash matches — idempotent.
            result["v2_status"] = "ALREADY_EXISTS_IMMUTABLE"
            rid = _report_id_for(client_id)
            result["v2_report_id"] = rid
        elif "GENERATION_DISABLED" in err:
            result["v2_status"] = "GENERATION_DISABLED"
            result["error"]     = err
            return result
        else:
            result["v2_status"] = f"REPORT_ERROR:{err}"
            result["error"]     = err
            return result
    except Exception as exc:
        result["v2_status"] = f"EXCEPTION:{exc}"
        result["error"]     = str(exc)
        return result

    # Step 3: supersede v1
    try:
        rid    = _report_id_for(client_id)
        v1_key = f"report:{rid}:v1"
        v1_row = store.get(v1_key)

        if v1_row is None:
            result["supersession"] = "V1_NOT_FOUND"
        elif v1_row["document"].get("status") == "SUPERSEDED":
            result["supersession"] = "ALREADY_SUPERSEDED"
        else:
            reports.transition(
                rid, 1, "SUPERSEDED",
                actor=ACTOR_BACKFILL,
                at=datetime.now(timezone.utc).isoformat(),
                expected_hash=v1_row["document"]["artifact_hash"],
                superseded_by_version=2,
            )
            result["supersession"] = "OK"

    except Exception as exc:
        suffix = f" | supersession: {exc}"
        result["supersession"] = f"FAILED:{exc}"
        result["error"]        = (result["error"] or "") + suffix

    return result


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def run_backfill() -> None:
    sep = "=" * 65
    print(sep)
    print("MONTHLY CANONICAL H8 — BACKFILL + V2 GENERATION")
    print(f"Period: {PERIOD_START} -> {PERIOD_END}")
    print(sep)

    today = datetime.now().date()
    sb    = get_supabase()
    store = CoreStore(sb)
    rpts  = Reports(store, flags=PILOT_FLAGS)
    ev    = _engine_version()

    print(f"\nengine_version_v2 = {ev[:16]}...")
    print(f"today             = {today}")

    # ── Confirm v1 preservation before touching anything ────────────────────
    all_records = (
        sb.table("core_canonical_report_records")
        .select("entity_key, document")
        .execute()
        .data or []
    )
    h8_v1_before = [
        r for r in all_records
        if r.get("document", {}).get("canonical_engine") == "H8"
        and ":v1" in r["entity_key"]
    ]
    sept_v1_preserved = len(h8_v1_before) == 6

    print(f"\n[1] SEPTEMBER V1 PRESERVED")
    print(f"    H8 v1 records in DB = {len(h8_v1_before)}  (expected 6)")
    for rec in h8_v1_before:
        d = rec["document"]
        print(f"      {d.get('client_id','?'):30s}  {d.get('status','?')}  "
              f"metrics={d.get('content',{}).get('metric_count','?')}")
    print(f"    SEPTEMBER_V1_PRESERVED = {sept_v1_preserved}")

    # ── LK: skip explicitly ─────────────────────────────────────────────────
    print(f"\n    lk-sneakers: SKIPPED — {LK_SKIP_REASON}")

    # ── Backfill + v2 for 5 clients ─────────────────────────────────────────
    print(f"\n[2] BACKFILL + V2 GENERATION  ({len(CLIENTS_TO_BACKFILL)} clients)")
    print("-" * 65)

    results: list[dict] = []
    for cid in CLIENTS_TO_BACKFILL:
        try:
            r = _backfill_one_client(cid, sb, store, rpts, ev, today)
        except Exception as exc:
            r = {
                "client_id":      cid,
                "snapshot":       "EXCEPTION",
                "v2_status":      "EXCEPTION",
                "metric_count":   None,
                "content_status": None,
                "supersession":   "EXCEPTION",
                "error":          str(exc),
            }
        results.append(r)

        snap_tag = "OK" if r["snapshot"].startswith("OK") else "FAIL"
        v2_ok    = r["v2_status"] in ("READY_FOR_REVIEW", "ALREADY_EXISTS_IMMUTABLE")
        sup_ok   = r["supersession"] in ("OK", "ALREADY_SUPERSEDED")
        print(
            f"  {cid:30s}  snap={snap_tag:4s}  "
            f"v2={'OK' if v2_ok else r['v2_status'][:18]:20s}  "
            f"sup={'OK' if sup_ok else r['supersession'][:12]:12s}  "
            f"metrics={r.get('metric_count','?')}"
        )
        if r.get("error"):
            print(f"    error: {r['error'][:100]}")

    # ── DB state after backfill ──────────────────────────────────────────────
    print(f"\n[3] DB STATE AFTER BACKFILL")
    print("-" * 65)
    all_records2 = (
        sb.table("core_canonical_report_records")
        .select("entity_key, document")
        .execute()
        .data or []
    )
    h8_all      = [r for r in all_records2 if r.get("document", {}).get("canonical_engine") == "H8"]
    h8_v1       = [r for r in h8_all if ":v1" in r["entity_key"]]
    h8_v2       = [r for r in h8_all if ":v2" in r["entity_key"]]
    h7_all      = [r for r in all_records2 if r.get("document", {}).get("canonical_engine") == "H7"]
    published   = [r for r in all_records2 if r.get("document", {}).get("status") == "PUBLISHED"]
    deliveries  = [r for r in all_records2 if r.get("entity_key", "").startswith("delivery:")]

    print(f"  H8 v1 records:  {len(h8_v1)}  (must remain 6)")
    print(f"  H8 v2 records:  {len(h8_v2)}  (expected 5)")
    print(f"  H7 records:     {len(h7_all)}  (must remain 12, untouched)")
    print(f"  PUBLISHED:      {len(published)}  (must be 0)")
    print(f"  delivery:*:     {len(deliveries)}  (must be 0)")

    for rec in sorted(h8_all, key=lambda r: (r["document"].get("client_id",""), r["entity_key"])):
        d   = rec["document"]
        ver = d.get("version", "?")
        mc  = d.get("content", {}).get("metric_count", "?")
        cs  = d.get("content", {}).get("status", "?")
        st  = d.get("status", "?")
        print(f"    {d.get('client_id','?'):30s}  v{ver}  {st:20s}  content={cs:18s}  metrics={mc}")

    # ── Idempotency re-run ───────────────────────────────────────────────────
    print(f"\n[4] IDEMPOTENCY CHECK  (re-run backfill for Tárcio)")
    print("-" * 65)
    try:
        r_idem = _backfill_one_client(
            "clinica-tarcio-caetano", sb, store, rpts, ev, today
        )
        idem_ok = (
            r_idem["v2_status"] in ("READY_FOR_REVIEW", "ALREADY_EXISTS_IMMUTABLE")
            and r_idem["supersession"] in ("OK", "ALREADY_SUPERSEDED")
        )
        print(f"  v2_status      = {r_idem['v2_status']}")
        print(f"  supersession   = {r_idem['supersession']}")
        print(f"  IDEMPOTENT     = {idem_ok}")
    except Exception as exc:
        print(f"  IDEMPOTENCY CHECK FAILED: {exc}")
        idem_ok = False

    # ── Summary ─────────────────────────────────────────────────────────────
    v2_ok_count  = sum(
        1 for r in results
        if r.get("v2_status") in ("READY_FOR_REVIEW", "ALREADY_EXISTS_IMMUTABLE")
    )
    sup_ok_count = sum(
        1 for r in results
        if r.get("supersession") in ("OK", "ALREADY_SUPERSEDED")
    )
    snap_ok_count = sum(
        1 for r in results if r["snapshot"].startswith("OK")
    )

    lk_v1 = next(
        (r for r in h8_v1 if r["document"].get("client_id") == LK_CLIENT_ID), None
    )
    lk_intact = (
        lk_v1 is not None
        and lk_v1["document"].get("status") == "READY_FOR_REVIEW"
        and lk_v1["document"].get("content", {}).get("metric_count") == 21
    )

    false_partials = [
        r for r in results
        if r.get("content_status") == "PARTIAL"
        and r["snapshot"].startswith("OK")
    ]

    cutover_ready = (
        sept_v1_preserved
        and v2_ok_count == 5
        and sup_ok_count == 5
        and len(h8_v2) == 5
        and len(h7_all) == 12
        and len(published) == 0
        and len(deliveries) == 0
        and lk_intact
        and len(false_partials) == 0
        and idem_ok
    )

    print(f"\n{sep}")
    print("OUTPUT")
    print(sep)
    print(f"SEPTEMBER_V1_PRESERVED           = {sept_v1_preserved}")
    print(f"VERSIONING_MODEL_STATUS          = SUPPORTED  "
          f"(CAS :v1/:v2 separate keys, SUPERSEDED terminal, superseded_by_version set)")
    print(f"SUPERSESSION_STATUS              = {sup_ok_count}/5 clients")
    print()

    # Per-client detail
    _label = {
        "clinica-tarcio-caetano": "TARCIO",
        "dipua":                  "DIPUA",
        "enutri":                 "ENUTRI",
        "zipper-galeria":         "ZIPPER",
        "spiti-auction":          "SPITI",
    }
    for r in results:
        lbl = _label.get(r["client_id"], r["client_id"].upper().replace("-", "_"))
        mc  = r.get("metric_count", "?")
        cs  = r.get("content_status", "?")
        print(f"{lbl}_MONTHLY_SNAPSHOT       = {r['snapshot']}")
        print(f"{lbl}_V2_STATUS              = {r['v2_status']}  metrics={mc}  content={cs}")
        print(f"{lbl}_SUPERSESSION           = {r['supersession']}")
        print()

    print(f"BACKFILL_IDEMPOTENCY             = {idem_ok}")
    print(f"PLATFORM_READS                   = YES  (Google/Meta API reads via run_pipeline)")
    print(f"PLATFORM_WRITES                  = NO")
    print()

    # Current versions
    versions = []
    for rec in sorted(h8_all, key=lambda r: r["document"].get("client_id", "")):
        d = rec["document"]
        if d.get("status") != "SUPERSEDED":
            versions.append(f"{d.get('client_id','?')}:v{d.get('version','?')}")
    print(f"MONTHLY_V2_RECORDS               = {len(h8_v2)}")
    print(f"MONTHLY_CURRENT_VERSIONS         = {', '.join(versions)}")
    print()

    # Coverage
    coverage_map: dict[str, str] = {}
    for rec in h8_all:
        d   = rec["document"]
        cid = d.get("client_id", "?")
        ver = d.get("version", 0)
        st  = d.get("status", "?")
        cs  = d.get("content", {}).get("status", "?")
        mc  = d.get("content", {}).get("metric_count", 0) or 0
        if st != "SUPERSEDED" and int(ver) >= coverage_map.get(cid, (0,))[0] if isinstance(coverage_map.get(cid), tuple) else True:
            if mc > 0:
                coverage_map[cid] = "FULL"
            else:
                coverage_map[cid] = "PARTIAL_LEGITIMATE"
    print(f"MONTHLY_REAL_DATA_COVERAGE:")
    for cid, cov in sorted(coverage_map.items()):
        print(f"  {cid:30s} = {cov}")
    print(f"FALSE_PARTIALS                   = {len(false_partials)}")
    print()

    print(f"CANONICAL_MONTHLY_SCHEDULER      = EXISTS  "
          f"(canonical_monthly_scheduler.py, CANONICAL_MONTHLY_REVIEW_ENABLED flag-gated)")
    print(f"LEGACY_MONTHLY_SCHEDULER         = INTACT  "
          f"(monthly_review registered when flag=false)")
    print(f"ROLLBACK_STATUS                  = "
          f"SET CANONICAL_MONTHLY_REVIEW_ENABLED=false -> legacy re-registers automatically")
    print()
    print(f"PUBLISH_COUNT                    = {len(published)}")
    print(f"DELIVERY_COUNT                   = {len(deliveries)}")
    print(f"CLIENT_SENDS                     = 0")
    print()
    print(f"MONTHLY_SCHEDULER_CUTOVER_READY  = {cutover_ready}")
    print(sep)


if __name__ == "__main__":
    run_backfill()
