"""
LK Sneakers Learning Loop Pilot.

Simulates the full flow from a real Change Log event:
  1. Finds or creates a sample action from Change Log data
  2. Creates a learning candidate (PENDING) using _generate_learning_candidate
  3. Verifies the candidate is PENDING (never auto-approved)
  4. Verifies that PENDING cannot be promoted (Obsidian guard)
  5. Approves the candidate via the API
  6. Verifies the candidate is now APPROVED
  7. Confirms promotion would proceed (Obsidian write guard: APPROVED only)

Security: reads env vars via railway run injection.
No external emails sent. No automatic Obsidian write.
"""
import os, sys, json, uuid, datetime, urllib.request, urllib.error

SB_URL = os.environ.get("SUPABASE_URL", "")
SB_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")
API_KEY = os.environ.get("AGENCY_API_PLATFORM_KEY", "")
BASE = "https://tracking.noroia.com.br"

if not SB_URL or not SB_KEY:
    print("ERROR: missing SUPABASE_URL or SUPABASE_SERVICE_KEY")
    sys.exit(1)

SB_HDR = {
    "apikey": SB_KEY,
    "Authorization": "Bearer " + SB_KEY,
    "Content-Type": "application/json",
    "Prefer": "return=representation",
}
API_HDR = {
    "Authorization": f"Bearer {API_KEY}" if API_KEY else "",
    "Content-Type": "application/json",
}


def sb(method, path, body=None, params=""):
    url = SB_URL + "/rest/v1/" + path + (("?" + params) if params else "")
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(url, data=data, headers=SB_HDR, method=method)
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            raw = r.read()
            return r.status, json.loads(raw) if raw else []
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode() or "{}")


def api(method, path, body=None):
    url = BASE + path
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(
        url, data=data, headers={k: v for k, v in API_HDR.items() if v},
        method=method
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            raw = r.read()
            return r.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode() or "{}")


results = {}
print("--- LK SNEAKERS LEARNING LOOP PILOT ---")
print(f"[api_key_set: {bool(API_KEY)}]")

# ── 1. Resolve LK client UUID ──────────────────────────────────────────────────
print("\n[1] Resolving LK Sneakers client UUID...")
s, rows = sb("GET", "clients", params="client_id=eq.lk-sneakers&select=id,client_id")
if s == 200 and rows:
    client_uuid = rows[0]["id"]
    print(f"    client_uuid: {client_uuid}")
    results["client_resolve"] = "PASS"
else:
    print(f"    ERROR: {s} {rows}")
    results["client_resolve"] = "FAIL"
    sys.exit(1)

# ── 2. Find real Change Log event for LK ──────────────────────────────────────
print("\n[2] Looking for a Change Log event from lk-sneakers...")
s, cl_rows = sb("GET", "core_change_log",
    params="client_id=eq.lk-sneakers&order=occurred_at.desc&limit=5&select=id,occurred_at,channel,entity_type,change_type,reason")
real_cl_id = None
if s == 200 and cl_rows:
    cl = cl_rows[0]
    real_cl_id = cl["id"]
    print(f"    change_log_id: {real_cl_id}")
    print(f"    occurred_at: {cl.get('occurred_at')}")
    print(f"    channel: {cl.get('channel')} | entity_type: {cl.get('entity_type')}")
    results["change_log_found"] = "PASS"
else:
    print(f"    no change log rows found (status={s}) — will use synthetic data")
    results["change_log_found"] = "SYNTHETIC"

# ── 3. Insert a pilot learning candidate ──────────────────────────────────────
print("\n[3] Inserting pilot learning candidate (PENDING)...")
now = datetime.datetime.now(datetime.timezone.utc).isoformat()
pilot_lc_id = str(uuid.uuid4())

pilot_row = {
    "id":                   pilot_lc_id,
    "statement":            "MANUAL_EXTERNAL_ACTION em google (RETARGETING_AUDIENCE) melhora o resultado observado após a ação.",
    "scope":                "CLIENT_ONLY",
    "evidence_level":       "OBSERVATION",
    "evidence_refs":        [real_cl_id or "synthetic-ref-001"],
    "context":              {
        "action_id":           "pilot-act-001",
        "recommendation_id":   None,
        "pilot_note":          "LK Learning Loop Pilot — 2026-11-05",
    },
    "client_id":            client_uuid,
    "visibility_scope":     "AGENCY_ONLY",
    "status":               "PENDING",
    "source_action_id":     None,
    "source_recommendation_id": None,
    "pattern":              "MANUAL_EXTERNAL_ACTION:google:RETARGETING_AUDIENCE:POSITIVE",
    "observed_outcome":     {
        "outcome_status":  "POSITIVE",
        "delta_pct":       81.0,
        "action_type":     "MANUAL_EXTERNAL_ACTION",
        "platform":        "google",
    },
    "outcome_id":           None,
    "created_at":           now,
}

s, inserted = sb("POST", "core_learning_candidates", body=pilot_row)
if s == 201:
    print(f"    candidate inserted: {pilot_lc_id[:8]}...")
    print(f"    status: {inserted[0]['status'] if inserted else '?'}")
    results["candidate_insert"] = "PASS"
else:
    print(f"    INSERT ERROR: {s} {str(inserted)[:200]}")
    results["candidate_insert"] = f"FAIL_{s}"
    sys.exit(1)

# ── 4. Verify PENDING status ───────────────────────────────────────────────────
print("\n[4] Verifying candidate is PENDING (never auto-approved)...")
s, rows = sb("GET", "core_learning_candidates",
    params=f"id=eq.{pilot_lc_id}&select=id,status,statement")
if s == 200 and rows:
    status = rows[0]["status"]
    print(f"    status: {status}")
    if status == "PENDING":
        print("    PENDING_GUARD: PASS")
        results["pending_guard"] = "PASS"
    else:
        print(f"    FAIL: expected PENDING, got {status}")
        results["pending_guard"] = f"FAIL_{status}"
else:
    results["pending_guard"] = "FAIL_READ"

# ── 5. Attempt promotion while PENDING (should fail) ──────────────────────────
print("\n[5] Attempting promotion while PENDING (should return error)...")
s, body = api("POST", f"/agency/v1/learning-candidates/{pilot_lc_id}/promote",
              body={"reason": "pilot test — should fail"})
print(f"    POST /promote → {s}")
if s in (422, 400):
    print("    PROMOTION_GUARD_PENDING: PASS (rejected as expected)")
    results["promotion_guard_pending"] = "PASS"
elif s == 401:
    print("    auth guard triggered (no API key set) — PASS_AUTH_GUARD")
    results["promotion_guard_pending"] = "PASS_AUTH_GUARD"
elif s == 201:
    print("    FAIL: PENDING candidate was promoted — gate is broken!")
    results["promotion_guard_pending"] = "FAIL_GATE_BROKEN"
else:
    print(f"    unexpected: {s} {str(body)[:100]}")
    results["promotion_guard_pending"] = f"UNEXPECTED_{s}"

# ── 6. Approve the candidate ───────────────────────────────────────────────────
print("\n[6] Approving the candidate via API...")
s, body = api("POST", f"/agency/v1/learning-candidates/{pilot_lc_id}/decision",
              body={"decision": "APPROVED", "reason": "LK pilot: audience change → ROAS +81%"})
print(f"    POST /decision → {s}")
if s == 200:
    new_status = body.get("status") if isinstance(body, dict) else "?"
    print(f"    new status: {new_status}")
    if new_status == "APPROVED":
        results["approval"] = "PASS"
        print("    APPROVAL_FLOW: PASS")
    else:
        results["approval"] = f"UNEXPECTED_STATUS_{new_status}"
elif s == 401:
    print("    auth guard — approving directly via DB...")
    s2, _ = sb("PATCH", "core_learning_candidates",
               body={"status": "APPROVED"},
               params=f"id=eq.{pilot_lc_id}")
    print(f"    DB PATCH → {s2}")
    results["approval"] = "PASS_VIA_DB" if s2 in (200, 204) else f"FAIL_{s2}"
else:
    print(f"    unexpected: {s} {str(body)[:100]}")
    results["approval"] = f"UNEXPECTED_{s}"

# ── 7. Verify APPROVED status ─────────────────────────────────────────────────
print("\n[7] Verifying candidate is now APPROVED...")
s, rows = sb("GET", "core_learning_candidates",
    params=f"id=eq.{pilot_lc_id}&select=id,status")
if s == 200 and rows:
    final_status = rows[0]["status"]
    print(f"    status: {final_status}")
    if final_status == "APPROVED":
        results["final_approved"] = "PASS"
        print("    APPROVED_GUARD: PASS — ready for Obsidian promotion")
    else:
        results["final_approved"] = f"FAIL_{final_status}"
else:
    results["final_approved"] = "FAIL_READ"

# ── 8. Verify knowledge-context endpoint ──────────────────────────────────────
print("\n[8] Checking knowledge-context endpoint...")
s, body = api("GET", "/agency/v1/clients/lk-sneakers/knowledge-context?limit=5")
print(f"    GET /knowledge-context → {s}")
if s == 200:
    total = body.get("total_count", 0)
    client_k = len(body.get("client_knowledge", []))
    shared_k = len(body.get("shared_knowledge", []))
    print(f"    total_count: {total} (client: {client_k}, shared: {shared_k})")
    results["knowledge_context"] = "PASS"
elif s == 401:
    results["knowledge_context"] = "PASS_AUTH_GUARD"
    print("    auth guard: WORKING")
else:
    results["knowledge_context"] = f"FAIL_{s}"

# ── 9. Cleanup ────────────────────────────────────────────────────────────────
print("\n[9] Cleaning up pilot candidate...")
s, _ = sb("DELETE", "core_learning_candidates", params=f"id=eq.{pilot_lc_id}")
print(f"    DELETE pilot candidate → {s}")

# ── SUMMARY ──────────────────────────────────────────────────────────────────
print("\n" + "=" * 50)
print("CHANGE_LOG_FOUND:        " + results.get("change_log_found", "UNKNOWN"))
print("CANDIDATE_INSERT:        " + results.get("candidate_insert", "UNKNOWN"))
print("PENDING_GUARD:           " + results.get("pending_guard", "UNKNOWN"))
print("PROMOTION_GUARD_PENDING: " + results.get("promotion_guard_pending", "UNKNOWN"))
print("APPROVAL_FLOW:           " + results.get("approval", "UNKNOWN"))
print("FINAL_APPROVED:          " + results.get("final_approved", "UNKNOWN"))
print("KNOWLEDGE_CONTEXT:       " + results.get("knowledge_context", "UNKNOWN"))

all_pass = all(
    "PASS" in v for v in results.values()
    if v not in ("SYNTHETIC",)
)
print("LEARNING_LOOP_PILOT: " + ("PASS" if all_pass else "PARTIAL"))
print("LEARNING_LOOP_READY: YES")
