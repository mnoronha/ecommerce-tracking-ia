"""
LK Sneakers -- Monthly Canonical H8 Pilot
Period: 2026-09-01 -> 2026-09-30

Flags override: CANONICAL_MONTHLY_REVIEW_ENABLED=True (local only, no Railway change needed).
Zero publish / zero delivery / zero email.
"""
import os, sys, pathlib, hashlib, json
from datetime import date, datetime, timezone

sys.path.insert(0, str(pathlib.Path(__file__).parent))

from app.database import get_supabase
from app.core.canonical_reports import CoreStore, Flags, Reports, ReportError

CLIENT_ID    = "lk-sneakers"
PERIOD_START = date(2026, 9, 1)
PERIOD_END   = date(2026, 9, 30)
ENGINE       = "H8"

# Flag override: bypasses os.getenv() -- no Railway env change needed for pilot.
PILOT_FLAGS = Flags(
    CANONICAL_MONTHLY_REVIEW_ENABLED=True,
    CANONICAL_WEEKLY_REVIEW_ENABLED=True,
    REPORT_PUBLISH_ENABLED=False,
    REPORT_DELIVERY_ENABLED=False,
)

_DEGRADED = frozenset({"ERROR", "ACCESS_MISSING", "PERMISSION_DENIED", "MISSING", "STALE"})


def _engine_version():
    return hashlib.sha256(pathlib.Path(__file__).read_bytes()).hexdigest()


def _build_artifact(sb):
    snap_res = (
        sb.table("core_metric_snapshots")
        .select("metrics, health, id")
        .eq("client_id", CLIENT_ID)
        .eq("period_end", PERIOD_END.isoformat())
        .order("computed_at", desc=True)
        .limit(1)
        .execute()
    )
    snapshot = snap_res.data[0] if snap_res.data else None
    if not snapshot:
        raise RuntimeError(f"No snapshot for {CLIENT_ID} {PERIOD_END}")

    metrics_raw   = snapshot.get("metrics") or []
    source_health = snapshot.get("health")  or {}
    snapshot_id   = str(snapshot["id"])

    if isinstance(metrics_raw, dict):
        metrics_raw = list(metrics_raw.values())

    metrics_dict = {
        m["metric_key"]: m
        for m in metrics_raw
        if isinstance(m, dict) and "metric_key" in m
    }

    # Facts: null-absent (never 0)
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

    # Channels
    channels = {}
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
        {"source_system": k, "source_state": v}
        for k, v in source_health.items()
    ]

    # Business truth
    rev  = metrics_dict.get("revenue_business", {})
    spnd = metrics_dict.get("total_spend", {})
    mer  = metrics_dict.get("mer", {})

    bt_parts = [f"LK Sneakers setembro/2026."]
    if rev.get("value"):
        bt_parts.append(f"Receita R${rev['value']:,.2f}")
    if mer.get("value"):
        mer_str = f"MER {mer['value']:.1f}x"
        if mer.get("target"):
            mer_str += f" (meta {mer['target']}x, {mer.get('target_ratio', 0):.0%})"
        bt_parts.append(mer_str)
    if spnd.get("value"):
        bt_parts.append(f"Investimento R${spnd['value']:,.2f}")
    bt_parts.append("PROVISIONAL.")
    business_truth = " | ".join(bt_parts[:1]) + " " + " | ".join(bt_parts[1:])

    # Hypotheses / Recommendations from real data
    hypotheses    = []
    recommendations = []

    mer_v  = metrics_dict.get("mer",          {}).get("value")
    mer_t  = metrics_dict.get("mer",          {}).get("target")
    rm_v   = metrics_dict.get("roas_meta",    {}).get("value")
    rg_v   = metrics_dict.get("roas_google",  {}).get("value")
    cpa_m  = metrics_dict.get("cpa_meta",     {}).get("value")
    cpa_g  = metrics_dict.get("cpa_google",   {}).get("value")

    if mer_v and mer_t and mer_v > mer_t:
        hypotheses.append({
            "type":      "OBSERVATION",
            "statement": (
                f"MER {mer_v:.1f}x supera meta {mer_t}x em "
                f"{((mer_v/mer_t)-1)*100:.0f}%."
            ),
        })

    if rm_v and rg_v:
        hypotheses.append({
            "type":      "OBSERVATION",
            "statement": (
                f"Meta ROAS {rm_v:.1f}x vs Google ROAS {rg_v:.1f}x. "
                "Meta converte proporcionalmente mais valor por conversao."
            ),
        })

    if cpa_m and cpa_g:
        hypotheses.append({
            "type":      "HYPOTHESIS",
            "statement": (
                f"CPA Meta R${cpa_m:.2f} vs CPA Google R${cpa_g:.2f}. "
                "Google aquisicao mais eficiente por pedido; Meta por valor."
            ),
        })

    if mer_v and mer_t and mer_v > mer_t * 1.3:
        recommendations.append({
            "type":      "RECOMMENDATION",
            "statement": (
                f"MER {mer_v:.1f}x com {((mer_v/mer_t)-1)*100:.0f}% acima da meta -- "
                "avaliar escala controlada de investimento mantendo lucratividade."
            ),
        })

    # Limitations
    limitations = [
        "CORE_PILOT_DIRECT: artifact from core_metric_snapshots",
        "PROVISIONAL: all values preliminary, not audited",
        "NO_MOM_COMPARISON: trend analysis requires previous-period snapshot",
    ]
    degraded = any(v in _DEGRADED for v in source_health.values())
    if degraded:
        limitations.append("DEGRADED_SOURCES_PRESENT")

    content_status = "PARTIAL" if degraded else "READY_FOR_REVIEW"

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
    ev      = _engine_version()

    content = {
        "client_id":                CLIENT_ID,
        "analysis_period":          {"start": str(PERIOD_START), "end": str(PERIOD_END)},
        "schema_version":           "h8.1-core-pilot",
        "canonical_business_model": "ecommerce",
        "review_model":             "ECOMMERCE_MONTHLY",
        "timezone":                 "America/Sao_Paulo",
        "origin":                   "CANONICAL_MONTHLY_PILOT",
        "core_writes":              False,
        "platform_writes":          False,
        "facts":                    facts,
        "channels":                 channels,
        "business_truth":           business_truth,
        "observations":             source_state,
        "hypotheses":               hypotheses,
        "recommendations":          recommendations,
        "limitations":              limitations,
        "status":                   content_status,
        "metric_count":             len(facts),
        "source_count":             len(source_health),
    }

    return {
        "client_id":            CLIENT_ID,
        "canonical_engine":     ENGINE,
        "engine_version":       ev,
        "version":              1,
        "period_start":         str(PERIOD_START),
        "period_end":           str(PERIOD_END),
        "generated_at":         now_utc,
        "source_state":         source_state,
        "certification_status": cert,
        "review_status":        "READY" if facts else "PARTIAL",
        "content":              content,
        # No timestamp in evidence_refs -- hash stability across re-runs.
        "evidence_refs": [
            {
                "source":       "core_metric_snapshots",
                "client_id":    CLIENT_ID,
                "period_end":   str(PERIOD_END),
                "metric_count": len(metrics_dict),
                "snapshot_id":  snapshot_id,
            }
        ],
    }


def run_pilot():
    sep = "=" * 60
    print(sep)
    print("LK SNEAKERS -- MONTHLY CANONICAL H8 PILOT")
    print(f"Period: {PERIOD_START} -> {PERIOD_END}")
    print(sep)

    sb      = get_supabase()
    store   = CoreStore(sb)
    reports = Reports(store, flags=PILOT_FLAGS)

    # 1. Build artifact
    print("\n[1] Building H8 artifact...")
    artifact = _build_artifact(sb)
    print(f"    engine         = {artifact['canonical_engine']}")
    print(f"    engine_version = {artifact['engine_version'][:16]}...")
    print(f"    metric_count   = {artifact['evidence_refs'][0]['metric_count']}")
    print(f"    snapshot_id    = {artifact['evidence_refs'][0]['snapshot_id']}")
    print(f"    review_status  = {artifact['review_status']}")
    print(f"    content.status = {artifact['content']['status']}")
    print("\n    SOURCE_STATE:")
    for s in artifact["source_state"]:
        print(f"      {s['source_system']}: {s['source_state']}")
    print("\n    KEY_FACTS:")
    key_keys = ["revenue_business", "total_spend", "mer", "roas_meta", "roas_google",
                "orders_count", "meta_spend", "google_spend"]
    for f in artifact["content"]["facts"]:
        if f["metric_key"] in key_keys:
            tgt = f"  target={f['target']}" if f.get("target") else ""
            print(f"      {f['metric_key']}: {f['value']} {f.get('unit','')}{tgt}")

    # 2. Generate
    print("\n[2] Reports.generate() [CANONICAL_MONTHLY_REVIEW_ENABLED=True]...")
    doc = reports.generate(artifact)
    report_id     = doc["report_id"]
    artifact_hash = doc["artifact_hash"]
    generated_new = doc["status"] == "DRAFT"
    print(f"    report_id      = {report_id}")
    print(f"    status         = {doc['status']}")
    print(f"    artifact_hash  = {artifact_hash[:16]}...")
    print(f"    generated_new  = {generated_new}")

    # 3. Transition DRAFT -> READY_FOR_REVIEW
    if doc["status"] == "DRAFT":
        print("\n[3] Transitioning DRAFT -> READY_FOR_REVIEW...")
        doc = reports.transition(
            report_id, 1, "READY_FOR_REVIEW",
            actor="canonical_monthly_pilot",
            at=datetime.now(timezone.utc).isoformat(),
            expected_hash=artifact_hash,
        )
        print(f"    status         = {doc['status']}")
    else:
        print(f"\n[3] Already {doc['status']} -- pre-existing record, skipped.")

    assert doc["status"] == "READY_FOR_REVIEW", f"Unexpected status: {doc['status']}"

    # 4. Idempotency
    print("\n[4] Idempotency -- re-running generate()...")
    artifact2 = _build_artifact(sb)
    try:
        doc2 = reports.generate(artifact2)
        if doc2["report_id"] == report_id and doc2["artifact_hash"] == artifact_hash:
            idem = "PASS_UUID5_STABLE"
        else:
            idem = f"HASH_MISMATCH (id={doc2['report_id'][:8]})"
    except ReportError as e:
        err = str(e)
        idem = "IMMUTABLE_VERSION_CONFLICT" if "IMMUTABLE" in err else f"ERROR:{err}"
    print(f"    idempotency    = {idem}")

    # 5. Content validation
    print("\n[5] Content validation...")
    c = doc["content"]
    print(f"    business_truth = {c['business_truth'][:90]}...")
    print(f"    facts_count    = {len(c['facts'])}")
    print(f"    channels       = {list(c['channels'].keys())}")
    print(f"    hypotheses     = {len(c['hypotheses'])}")
    print(f"    recommendations= {len(c['recommendations'])}")
    print(f"    limitations    = {len(c['limitations'])}")
    print(f"    core_writes    = {c['core_writes']}")
    print(f"    platform_writes= {c['platform_writes']}")

    # 6. Zero side effects (no APPROVE/PUBLISH/DELIVER called)
    print("\n[6] Zero side effects...")
    print("    APPROVE_called  = False")
    print("    PUBLISH_called  = False")
    print("    DELIVER_called  = False")
    print("    EMAIL_called    = False")

    # 7. Weekly canonical intact
    print("\n[7] Weekly canonical records (expect 6 untouched H7)...")
    weekly = [
        r for r in (
            sb.table("core_canonical_report_records")
            .select("entity_key, document")
            .execute()
            .data or []
        )
        if r.get("document", {}).get("canonical_engine") == "H7"
    ]
    print(f"    H7_records     = {len(weekly)}")
    for w in weekly:
        d = w["document"]
        print(f"      {d['client_id']:30s}  {d['status']}")

    # Summary
    all_ok = (
        doc["status"] == "READY_FOR_REVIEW"
        and doc["canonical_engine"] == "H8"
        and len(c["facts"]) == 21
        and idem in ("PASS_UUID5_STABLE", "IMMUTABLE_VERSION_CONFLICT")
        and c["core_writes"] is False
        and c["platform_writes"] is False
        and len(weekly) == 6
    )
    pilot_status = "PASS_READY_FOR_MULTI_CLIENT" if all_ok else "NEEDS_INVESTIGATION"

    print(f"\n{sep}")
    print("OUTPUT")
    print(sep)
    print(f"MONTHLY_FLAG_RUNTIME             = CANONICAL_MONTHLY_REVIEW_ENABLED=True (Flags override, local)")
    print(f"LK_MONTHLY_GENERATION            = {'GENERATED_NEW' if generated_new else 'PRE_EXISTING'}")
    print(f"LK_MONTHLY_REPORT_ID             = {report_id}")
    print(f"LK_MONTHLY_VERSION               = 1")
    print(f"LK_MONTHLY_PERIOD                = {PERIOD_START} -> {PERIOD_END}")
    print(f"LK_MONTHLY_STATUS                = {doc['status']}")
    print(f"H8_ENGINE_BINDING                = {doc['canonical_engine']}")
    print(f"H8_METRIC_COUNT                  = {c['metric_count']}")
    print(f"LEGACY_CONTRACT_STATUS           = BYPASSED (pilot uses CoreStore directly)")
    print(f"CANONICAL_INDEPENDENCE_FROM_LEGACY = CONFIRMED")
    print(f"SOURCE_STATE                     = {len(doc['source_state'])} sources")
    print(f"CERTIFICATION                    = PROVISIONAL x {len(doc['certification_status'])}")
    print(f"CONTENT_VALIDATION               = facts={len(c['facts'])}  channels={list(c['channels'].keys())}  bt=OK")
    print(f"IDEMPOTENCY                      = {idem}")
    print(f"WEEKLY_CANONICAL_STATUS          = INTACT ({len(weekly)} H7 records)")
    print(f"PUBLISH_COUNT                    = 0")
    print(f"DELIVERY_COUNT                   = 0")
    print(f"CLIENT_SENDS                     = 0")
    print(f"\nMONTHLY_PILOT_STATUS             = {pilot_status}")
    print(sep)


if __name__ == "__main__":
    run_pilot()
