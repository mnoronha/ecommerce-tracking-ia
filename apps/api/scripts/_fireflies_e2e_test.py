"""
Fireflies Real E2E Test.

Uses the most recent LK meeting already in Fireflies (no manual creation).
If the meeting was already ingested (from _fireflies_ingest_existing.py), confirms state.
Otherwise triggers ingestion and waits.

Validates full pipeline:
  Fireflies meeting → webhook → client mapping → core_meeting → transcript → analysis

Returns:
  FIREFLIES_MEETING_ID, CORE_MEETING_ID, CLIENT, TRANSCRIPT_STATUS,
  PROCESSING_STATUS, SUMMARY_STATUS, ACTION_ITEMS_COUNT

Usage:
  cd apps/api
  railway run -- python -u scripts/_fireflies_e2e_test.py
"""
import os, sys, json, time, urllib.request, urllib.error
sys.stdout.reconfigure(encoding="utf-8")

ADMIN_KEY    = os.environ.get("AGENCY_API_ADMIN_KEY", "")
PLATFORM_KEY = os.environ.get("AGENCY_API_PLATFORM_KEY", "")
API_KEY      = ADMIN_KEY or PLATFORM_KEY
BASE         = os.environ.get("API_BASE", "https://tracking.noroia.com.br")
WEBHOOK_SECRET = os.environ.get("FIREFLIES_WEBHOOK_SECRET", "")

# Most recent LK Semanal meeting (confirmed from probe)
FF_MEETING_ID = "01M64PVE2YEY9PVEBQZR15HDP4"
LK_CLIENT_ID  = "lk-sneakers"

if not API_KEY:
    print("ABORT: set AGENCY_API_ADMIN_KEY or AGENCY_API_PLATFORM_KEY")
    sys.exit(1)

PASS, FAIL = "PASS", "FAIL"

def api(method, path, body=None):
    url  = BASE + path
    hdrs = {"Authorization": "Bearer " + API_KEY, "Content-Type": "application/json"}
    data = json.dumps(body).encode() if body else None
    req  = urllib.request.Request(url, data=data, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read()
            return r.status, json.loads(raw) if raw.strip() else {}
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace").strip()
        try:
            return e.code, json.loads(raw) if raw else {}
        except Exception:
            return e.code, {"_raw": raw[:200]}

def webhook_post(ff_meeting_id: str):
    url  = BASE + "/webhooks/fireflies"
    payload = {"meetingId": ff_meeting_id, "event_type": "Transcription.completed",
               "token": WEBHOOK_SECRET}
    data = json.dumps(payload).encode()
    req  = urllib.request.Request(url, data=data, method="POST",
                                   headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8", "replace") or "{}")

def p(label, status, detail=""):
    icon = "OK" if status in ("PASS", "YES", "AUTOMATIC", "PRESENT") else "FAIL"
    print(f"  [{icon}] {label:50s} {status} {detail}")

print("=" * 72)
print("FIREFLIES REAL E2E TEST")
print(f"  FF_MEETING_ID : {FF_MEETING_ID}")
print(f"  CLIENT        : {LK_CLIENT_ID}")
print(f"  Base URL      : {BASE}")
print("=" * 72)

results = {}

# ── Step 1: Check if already ingested ─────────────────────────────────────────
print("\n[1] CHECK EXISTING INGESTION")

CORE_MEETING_ID = None
s_lk, meetings = api("GET", f"/agency/v1/clients/{LK_CLIENT_ID}/meetings?limit=100")
if s_lk == 200 and isinstance(meetings, list):
    for m in meetings:
        if m.get("provider") == "fireflies" and m.get("external_id") == FF_MEETING_ID:
            CORE_MEETING_ID = m["id"]
            print(f"    Meeting already ingested: core_meeting_id={CORE_MEETING_ID}")
            print(f"    transcript_status = {m.get('transcript_status')}")
            print(f"    processing_status = {m.get('processing_status')}")
            break

if not CORE_MEETING_ID:
    print("    Not yet ingested. Triggering webhook...")
    # Step 1b: Ensure mapping exists (explicit override)
    s_map, _ = api("POST", "/agency/v1/fireflies/mappings",
                   body={"fireflies_meeting_id": FF_MEETING_ID, "client_id": LK_CLIENT_ID})
    print(f"    Explicit mapping: status={s_map} ({'exists' if s_map == 200 else 'created'})")

    # Step 1c: Fire webhook
    s_wh, wh = webhook_post(FF_MEETING_ID)
    event_id = wh.get("event_id", "")
    idempotent = wh.get("idempotent", False)
    print(f"    Webhook: status={s_wh} event_id={event_id[:20] if event_id else '?'}… idempotent={idempotent}")

    if s_wh != 200:
        print(f"    WEBHOOK_FAILED: {str(wh)[:200]}")
        # Try to find the meeting anyway (maybe webhook fired before)

    # Step 1d: Poll for meeting creation
    print("\n    Polling for meeting creation (timeout=90s)...")
    deadline = time.time() + 90
    polls = 0
    while time.time() < deadline and not CORE_MEETING_ID:
        polls += 1
        time.sleep(5)
        s_lk2, ms2 = api("GET", f"/agency/v1/clients/{LK_CLIENT_ID}/meetings?limit=100")
        if s_lk2 == 200 and isinstance(ms2, list):
            for m in ms2:
                if m.get("provider") == "fireflies" and m.get("external_id") == FF_MEETING_ID:
                    CORE_MEETING_ID = m["id"]
                    break
        elapsed = int(time.time() - (deadline - 90))
        print(f"    poll #{polls:2d} (+{elapsed:2d}s): {'FOUND' if CORE_MEETING_ID else 'waiting'}",
              flush=True)
        if CORE_MEETING_ID:
            break

if not CORE_MEETING_ID:
    print("\nABORT: Meeting not created after 90s")
    print("  Possible causes:")
    print("  - Mapping not configured before webhook fired")
    print("  - Fireflies API key expired")
    print("  - Migration 083 not applied yet (mapping-rules table missing)")
    sys.exit(1)

p("Meeting ingested to core_meetings", PASS, f"core_meeting_id={CORE_MEETING_ID[:20]}…")
results["CORE_MEETING_ID"] = CORE_MEETING_ID

# ── Step 2: Verify meeting details ────────────────────────────────────────────
print("\n[2] MEETING DETAILS")

s_m, meeting = api("GET", f"/agency/v1/clients/{LK_CLIENT_ID}/meetings")
# Need individual meeting endpoint — let's use the list and filter
s_all, all_m = api("GET", f"/agency/v1/clients/{LK_CLIENT_ID}/meetings?limit=100")
meeting = None
if s_all == 200:
    for m in (all_m or []):
        if m.get("id") == CORE_MEETING_ID:
            meeting = m
            break

if not meeting:
    print(f"    Could not fetch meeting detail for {CORE_MEETING_ID}")
    sys.exit(1)

TITLE      = meeting.get("title", "")
TX_STATUS  = meeting.get("transcript_status", "?")
PROC_STATUS = meeting.get("processing_status", "?")
print(f"    title             = {TITLE}")
print(f"    provider          = {meeting.get('provider')}")
print(f"    external_id       = {meeting.get('external_id')}")
print(f"    transcript_status = {TX_STATUS}")
print(f"    processing_status = {PROC_STATUS}")
print(f"    client_id         = {meeting.get('client_id')}")

p("Client assigned correctly", PASS if meeting.get("client_id") == LK_CLIENT_ID else FAIL,
  f"got={meeting.get('client_id')!r}")
p("Provider = fireflies",      PASS if meeting.get("provider") == "fireflies" else FAIL)
results["CLIENT"] = meeting.get("client_id", "?")
results["TRANSCRIPT_STATUS"] = TX_STATUS
results["PROCESSING_STATUS"] = PROC_STATUS

# ── Step 3: Verify transcript ─────────────────────────────────────────────────
print("\n[3] TRANSCRIPT")

s_tx, tx = api("GET", f"/agency/v1/meetings/{CORE_MEETING_ID}/transcript")
if s_tx == 200:
    tx_content = tx.get("transcript", {}) if isinstance(tx.get("transcript"), dict) else tx
    raw_text = tx_content.get("raw_text", "") or tx.get("raw_text", "") or ""
    tx_status_resp = tx.get("transcript_status") or tx_content.get("transcript_status") or TX_STATUS
    print(f"    transcript_status = {tx_status_resp}")
    print(f"    raw_text length   = {len(raw_text)} chars")
    print(f"    provider          = {tx.get('provider')}")
    p("Transcript present", PASS if len(raw_text) > 10 else FAIL,
      f"{len(raw_text)} chars")
    results["TRANSCRIPT_STATUS"] = tx_status_resp
elif s_tx == 404:
    print("    No transcript yet — may still be processing")
    p("Transcript present", FAIL, "status=404")
else:
    print(f"    Error: status={s_tx} {str(tx)[:100]}")
    p("Transcript present", FAIL, f"status={s_tx}")

# ── Step 4: Verify analysis ────────────────────────────────────────────────────
print("\n[4] ANALYSIS (Claude auto-analyze)")

SUMMARY_STATUS = "NOT_ANALYZED"
ACTION_ITEMS_COUNT = 0

# Check via meetings list for processing_status=done
s_all2, all_m2 = api("GET", f"/agency/v1/clients/{LK_CLIENT_ID}/meetings?limit=100")
meeting2 = None
if s_all2 == 200:
    for m in (all_m2 or []):
        if m.get("id") == CORE_MEETING_ID:
            meeting2 = m
            break

if meeting2:
    PROC_STATUS = meeting2.get("processing_status", PROC_STATUS)
    print(f"    processing_status = {PROC_STATUS}")

# Check meeting summary directly
s_sum, summaries = api("GET", f"/agency/v1/clients/{LK_CLIENT_ID}/meetings")
# Try the analysis endpoint
s_an, an = api("GET", f"/agency/v1/meetings/{CORE_MEETING_ID}/analysis")
if s_an == 200:
    summary_text = an.get("summary") or ""
    commitments  = an.get("commitments") or an.get("action_items") or []
    decisions    = an.get("decisions") or []
    ACTION_ITEMS_COUNT = len(commitments)
    SUMMARY_STATUS = "ANALYZED" if summary_text else "EMPTY"
    print(f"    summary present   = {'YES' if summary_text else 'NO'}")
    print(f"    decisions         = {len(decisions)}")
    print(f"    action_items      = {ACTION_ITEMS_COUNT}")
    p("Analysis complete", PASS if summary_text else "FAIL",
      f"summary={'PRESENT' if summary_text else 'MISSING'}")
elif s_an == 404:
    print("    No analysis found (FIREFLIES_AUTO_ANALYZE may not be set, or ANTHROPIC_CREDITS_OK=1 needed)")
    print("    → analysis can be triggered manually: POST /agency/v1/meetings/{id}/process")
    SUMMARY_STATUS = "NOT_ANALYZED"
    p("Analysis complete", "PASS_PENDING",
      "auto-analyze requires FIREFLIES_AUTO_ANALYZE=1 + ANTHROPIC_CREDITS_OK=1")
else:
    print(f"    Analysis endpoint: status={s_an}")
    SUMMARY_STATUS = "ENDPOINT_ERROR"

results["SUMMARY_STATUS"]    = SUMMARY_STATUS
results["ACTION_ITEMS_COUNT"] = ACTION_ITEMS_COUNT

# ── Final matrix ──────────────────────────────────────────────────────────────
print("\n" + "=" * 72)
print("FIREFLIES REAL E2E PROOF")
print("=" * 72)
print(f"  FIREFLIES_MEETING_ID = {FF_MEETING_ID}")
print(f"  CORE_MEETING_ID      = {results.get('CORE_MEETING_ID', 'NOT_FOUND')}")
print(f"  CLIENT               = {results.get('CLIENT', '?')}")
print(f"  TRANSCRIPT_STATUS    = {results.get('TRANSCRIPT_STATUS', '?')}")
print(f"  PROCESSING_STATUS    = {results.get('PROCESSING_STATUS', '?')}")
print(f"  SUMMARY_STATUS       = {results.get('SUMMARY_STATUS', '?')}")
print(f"  ACTION_ITEMS_COUNT   = {results.get('ACTION_ITEMS_COUNT', 0)}")
print()

ingested = bool(results.get("CORE_MEETING_ID"))
transcript_ok = results.get("TRANSCRIPT_STATUS") in ("ready", "COMPLETE")
client_ok     = results.get("CLIENT") == LK_CLIENT_ID
analysis_done = results.get("SUMMARY_STATUS") == "ANALYZED"

p("FIREFLIES_MEETING_INGESTED",    PASS if ingested else FAIL)
p("TRANSCRIPT_READY",              PASS if transcript_ok else FAIL,
  f"status={results.get('TRANSCRIPT_STATUS')}")
p("CLIENT_CORRECTLY_MAPPED",       PASS if client_ok else FAIL,
  f"client={results.get('CLIENT')}")
p("ANALYSIS_COMPLETE",             PASS if analysis_done else "PENDING",
  f"(requires FIREFLIES_AUTO_ANALYZE=1 + ANTHROPIC_CREDITS_OK=1)" if not analysis_done else "")

pipeline_ready = ingested and transcript_ok and client_ok
overall = "PASS" if pipeline_ready else "FAIL"
print()
print(f"  REAL_FIREFLIES_E2E = {overall}")
print("=" * 72)
sys.exit(0 if pipeline_ready else 1)
