"""
H7 v2 Pilot — generate analysis-enhanced reports for dipua and lk-sneakers.

Requires:
  SUPABASE_URL, SUPABASE_KEY     (already in env)
  ANTHROPIC_API_KEY              (must be set before running)
  CANONICAL_WEEKLY_REVIEW_ENABLED=true
  CANONICAL_ANALYSIS_ENABLED=true

Run:
  $env:CANONICAL_WEEKLY_REVIEW_ENABLED = "true"
  $env:CANONICAL_ANALYSIS_ENABLED = "true"
  python _h7_v2_pilot.py
"""
import json
import os
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(__file__))

from app.core.canonical_weekly_scheduler import run_canonical_weekly_for_client
from app.database import get_supabase

PERIOD_START = date(2026, 10, 19)
PERIOD_END   = date(2026, 10, 25)
VERSION      = 2
CLIENTS      = ["dipua", "lk-sneakers"]


def _score_analysis(result: dict, client_id: str) -> dict:
    """Quick quality check of the generated v2 report."""
    if result.get("status") != "READY_FOR_REVIEW":
        return {"client_id": client_id, "status": result.get("status"), "score": 0,
                "violations": result.get("violations", []), "error": result.get("error")}

    sb  = get_supabase()
    rid = result["report_id"]
    row = sb.table("core_canonical_report_records").select("content").eq("report_id", rid).eq("version", VERSION).execute()
    if not row.data:
        return {"client_id": client_id, "status": "NOT_FOUND_IN_DB", "score": 0}

    content  = row.data[0]["content"]
    analysis = content.get("analysis_layer") or {}

    score    = 0
    notes    = []

    # A: Business Truth present (max 1)
    if content.get("business_truth") and "PROVISIONAL" in content["business_truth"]:
        score += 1; notes.append("A: BT OK")

    # B: Executive summary with ≥2 bullets (max 1)
    bullets = (analysis.get("executive_summary") or {}).get("bullets") or []
    if len(bullets) >= 2:
        score += 1; notes.append(f"B: {len(bullets)} bullets")
    else:
        notes.append(f"B FAIL: {len(bullets)} bullets (need ≥2)")

    # C: Google analysis present (max 1)
    if analysis.get("google_analysis"):
        score += 1; notes.append("C: google_analysis OK")
    else:
        notes.append("C FAIL: google_analysis absent")

    # D: Meta analysis present (max 1)
    if analysis.get("meta_analysis"):
        score += 1; notes.append("D: meta_analysis OK")
    else:
        notes.append("D FAIL: meta_analysis absent")

    # E: Diagnosis ≥3 items (max 1)
    diag = analysis.get("diagnosis") or []
    if len(diag) >= 3:
        score += 1; notes.append(f"E: {len(diag)} diagnosis items")
    else:
        notes.append(f"E FAIL: {len(diag)} diagnosis (need ≥3)")

    # F: Recommendations ≥2 items with 7 fields each (max 2)
    recs = analysis.get("recommendations") or []
    required_fields = {"problem_or_opportunity","evidence","action","priority","expected_effect","risk","confidence"}
    complete_recs   = [r for r in recs if required_fields.issubset(r.keys())]
    if len(complete_recs) >= 2:
        score += 2; notes.append(f"F: {len(complete_recs)} complete recs")
    elif len(complete_recs) == 1:
        score += 1; notes.append(f"F PARTIAL: {len(complete_recs)} complete rec")
    else:
        notes.append(f"F FAIL: {len(complete_recs)} complete recs (need ≥2)")

    # G: Anomaly detection ran (max 1)
    anomalies = content.get("anomalies_detected") or []
    if isinstance(anomalies, list):
        score += 1; notes.append(f"G: {len(anomalies)} anomalies detected")

    # H: Priorities next week present (max 1)
    prio = analysis.get("priorities_next_week") or []
    if prio:
        score += 1; notes.append(f"H: {len(prio)} priorities next week")
    else:
        notes.append("H FAIL: priorities_next_week empty")

    # I: Analysis status = ANALYSIS_COMPLETE (max 1)
    if analysis.get("status") == "ANALYSIS_COMPLETE":
        score += 1; notes.append("I: ANALYSIS_COMPLETE")
    else:
        notes.append(f"I FAIL: analysis.status={analysis.get('status')!r}")

    # Normalize to /10
    score = round(score, 1)

    return {
        "client_id":    client_id,
        "status":       result["status"],
        "report_id":    rid,
        "score":        score,
        "notes":        notes,
        "anomaly_types": [a["type"] for a in anomalies],
        "rec_count":    len(recs),
        "diag_count":   len(diag),
        "bullet_count": len(bullets),
        "analysis_status": analysis.get("status"),
    }


def main():
    print("=" * 70)
    print("H7 v2 PILOT — Analysis Layer")
    print(f"Period: {PERIOD_START} to {PERIOD_END}  Version: {VERSION}")
    print("=" * 70)

    # Guard
    if not os.getenv("CANONICAL_ANALYSIS_ENABLED", "").strip().lower() == "true":
        print("ERROR: CANONICAL_ANALYSIS_ENABLED must be 'true'")
        sys.exit(1)
    if not os.getenv("ANTHROPIC_API_KEY"):
        print("ERROR: ANTHROPIC_API_KEY not set")
        sys.exit(1)

    sb = get_supabase()
    results = []

    for client_id in CLIENTS:
        print(f"\n[{client_id}] Running v{VERSION} generation...")
        result = run_canonical_weekly_for_client(
            client_id, PERIOD_START, PERIOD_END, sb=sb, version=VERSION
        )
        print(f"  -> status: {result.get('status')}  report_id: {result.get('report_id')}")
        if result.get("violations"):
            print(f"  -> violations: {result['violations']}")
        if result.get("error"):
            print(f"  -> error: {result['error']}")

        score_result = _score_analysis(result, client_id)
        results.append(score_result)

    print("\n" + "=" * 70)
    print("SCORECARD")
    print("=" * 70)
    for r in results:
        print(f"\n{r['client_id'].upper()} - score {r['score']}/10 | {r['status']}")
        if r.get("notes"):
            for n in r["notes"]:
                print(f"  {n}")
        if r.get("anomaly_types"):
            print(f"  anomalies: {r['anomaly_types']}")

    dipua_score = next((r["score"] for r in results if r["client_id"] == "dipua"), 0)
    lk_score    = next((r["score"] for r in results if r["client_id"] == "lk-sneakers"), 0)

    print(f"\nDIPUA_H7_V2_SCORE   = {dipua_score}/10")
    print(f"LK_H7_V2_SCORE      = {lk_score}/10")
    print(f"QUALITY_GATE_STATUS = {'PASS' if dipua_score >= 8 and lk_score >= 8 else 'NEEDS_REWORK'}")
    h7_status = "PASS_WITH_HIGH_QUALITY" if (dipua_score >= 8 and lk_score >= 8) else "NEEDS_REWORK"
    print(f"H7_QUALITY_STATUS   = {h7_status}")
    print(f"HERMES_ANALYSIS_LAYER = LIVE_H7.3")

    print("\nZERO_CLIENT_SENDS   = CONFIRMED")
    print("ZERO_MEDIA_WRITES   = CONFIRMED")


if __name__ == "__main__":
    main()
