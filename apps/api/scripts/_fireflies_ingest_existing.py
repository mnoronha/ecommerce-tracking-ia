"""
Fireflies — Ingest existing transcripts.

Tasks:
  1. Seed domain-based mapping rules (participant_domain) for all 3 clients
  2. Add explicit meeting-id mappings for all 7 known transcripts
  3. POST /webhooks/fireflies for each transcript to trigger ingestion
  4. Poll until all 7 are in core_meetings

Usage:
  cd apps/api
  railway run -- python -u scripts/_fireflies_ingest_existing.py

Requires AGENCY_API_ADMIN_KEY (or PLATFORM_KEY) + FIREFLIES_WEBHOOK_SECRET in env.
"""
import os, sys, json, time, urllib.request, urllib.error, hmac
sys.stdout.reconfigure(encoding="utf-8")

ADMIN_KEY    = os.environ.get("AGENCY_API_ADMIN_KEY", "")
PLATFORM_KEY = os.environ.get("AGENCY_API_PLATFORM_KEY", "")
API_KEY      = ADMIN_KEY or PLATFORM_KEY
BASE         = os.environ.get("API_BASE", "https://tracking.noroia.com.br")
WEBHOOK_SECRET = os.environ.get("FIREFLIES_WEBHOOK_SECRET", "")
ACTOR = "maiconoronha@gmail.com"

if not API_KEY:
    print("ABORT: set AGENCY_API_ADMIN_KEY or AGENCY_API_PLATFORM_KEY")
    sys.exit(1)

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

def webhook(ff_meeting_id: str, event_type: str = "Transcription.completed"):
    """POST webhook payload using the shared secret."""
    url  = BASE + "/webhooks/fireflies"
    payload = {
        "meetingId":    ff_meeting_id,
        "event_type":   event_type,
        "token":        WEBHOOK_SECRET,
    }
    data = json.dumps(payload).encode()
    req  = urllib.request.Request(url, data=data, method="POST",
                                   headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8", "replace") or "{}")

def p(label, ok, detail=""):
    print(f"  [{'OK' if ok else 'FAIL'}] {label:55s} {detail}")

print("=" * 70)
print("FIREFLIES — INGEST EXISTING TRANSCRIPTS")
print(f"  Base URL : {BASE}")
print("=" * 70)

# ── [1] Seed domain mapping rules ─────────────────────────────────────────────
print("\n[1] SEED DOMAIN MAPPING RULES")

DOMAIN_RULES = [
    {"client_id": "lk-sneakers", "match_type": "participant_domain",
     "match_value": "lksneakers.com.br", "priority": 5,
     "notes": "LK Sneakers participant domain"},
    {"client_id": "dipua",       "match_type": "participant_domain",
     "match_value": "dipua.com.br",      "priority": 5,
     "notes": "Dipua participant domain"},
    {"client_id": "enutri",      "match_type": "participant_domain",
     "match_value": "enutri.com.br",     "priority": 5,
     "notes": "Enutri participant domain"},
]

for rule in DOMAIN_RULES:
    s, r = api("POST", "/agency/v1/fireflies/mapping-rules", body=rule)
    ok = s in (200, 201)
    p(f"rule: {rule['match_value']} → {rule['client_id']}", ok,
      f"status={s} {'(exists)' if s == 200 else '(created)' if s == 201 else ''}")

# Verify rules
s, rules = api("GET", "/agency/v1/fireflies/mapping-rules")
print(f"    Total rules configured: {len(rules) if isinstance(rules, list) else '?'}")

# ── [2] Add explicit meeting-id mappings ──────────────────────────────────────
print("\n[2] ADD EXPLICIT MEETING-ID MAPPINGS (all 7 known transcripts)")

KNOWN_MAPPINGS = [
    # (fireflies_meeting_id, client_id, title)
    ("01M64PVE2YEY9PVEBQZR15HDP4", "lk-sneakers", "Reunião Semanal LK 2026-11-05"),
    ("01M64PVDQ7EEF128AC0G0SRJRN", "dipua",       "Reunião Dipua Black Friday 2026-11-04"),
    ("01M6HD3METCZ0E7P32QHH5D9WJ", "enutri",      "Reunião Enutri 2026-11-04"),
    ("01M5Z8GV0MHXSD13FGSW9VE97G", "dipua",       "Reunião Dipua Black Friday 2026-10-29"),
    ("01M5YW9R6JQAASY40P0Z4DR77S", "lk-sneakers", "Reunião Semanal LK 2026-10-29"),
    ("01M5YW9R7HFR7EJRPAWHEA2C05", "dipua",       "Reunião Dipua Black Friday 2026-10-27"),
    ("01M4Y0YHCFCD5HR9C3B1S0H78D", "dipua",       "Dipua Midia e BI GA4 2026-10-15"),
]

for ff_id, cid, title in KNOWN_MAPPINGS:
    s, r = api("POST", "/agency/v1/fireflies/mappings",
               body={"fireflies_meeting_id": ff_id, "client_id": cid})
    ok = s in (200, 201)
    p(f"{ff_id[:20]}… → {cid}", ok,
      f"status={s} {title[:35]}")

# ── [3] POST webhooks to trigger ingestion ────────────────────────────────────
print("\n[3] POST WEBHOOKS TO TRIGGER INGESTION")

if not WEBHOOK_SECRET:
    print("    WARN: FIREFLIES_WEBHOOK_SECRET not set — webhooks may fail auth check")

event_ids = {}
for ff_id, cid, title in KNOWN_MAPPINGS:
    s, r = webhook(ff_id)
    event_id = r.get("event_id", "")
    idempotent = r.get("idempotent", False)
    ok = s == 200
    p(f"webhook {ff_id[:20]}…",
      ok, f"event_id={event_id[:20] if event_id else '?'}… {'(dup)' if idempotent else '(new)'}")
    if ok and event_id:
        event_ids[ff_id] = event_id

# ── [4] Poll until meetings created ──────────────────────────────────────────
print("\n[4] POLL FOR MEETING CREATION (timeout=120s)")

deadline  = time.time() + 120
expected  = {ff_id: cid for ff_id, cid, _ in KNOWN_MAPPINGS}
confirmed = {}
polls     = 0

while time.time() < deadline and len(confirmed) < len(expected):
    polls += 1
    time.sleep(5)
    # Check all 3 clients
    for cid_check in ("lk-sneakers", "dipua", "enutri"):
        s, ms = api("GET", f"/agency/v1/clients/{cid_check}/meetings?limit=50")
        if s == 200 and isinstance(ms, list):
            for m in ms:
                if m.get("provider") == "fireflies":
                    ext_id = m.get("external_id", "")
                    if ext_id in expected and ext_id not in confirmed:
                        confirmed[ext_id] = {
                            "meeting_id":        m["id"],
                            "client_id":         cid_check,
                            "transcript_status": m.get("transcript_status"),
                            "processing_status": m.get("processing_status"),
                        }
    elapsed = int(time.time() - (deadline - 120))
    print(f"    poll #{polls:2d} (+{elapsed:3d}s): {len(confirmed)}/{len(expected)} confirmed", flush=True)
    if len(confirmed) == len(expected):
        break

print()
print("[5] RESULTS")
all_ok = True
for ff_id, cid, title in KNOWN_MAPPINGS:
    if ff_id in confirmed:
        c = confirmed[ff_id]
        p(f"{ff_id[:20]}… ({title[:30]})", True,
          f"meeting_id={c['meeting_id'][:20]}… tx={c['transcript_status']} proc={c['processing_status']}")
    else:
        p(f"{ff_id[:20]}… ({title[:30]})", False, "NOT_CONFIRMED")
        all_ok = False

print()
print("=" * 70)
print(f"  TOTAL TRANSCRIPTS : {len(KNOWN_MAPPINGS)}")
print(f"  INGESTED          : {len(confirmed)}")
print(f"  DOMAIN_RULES      : {len(DOMAIN_RULES)}")
print(f"  MEETING_MAPPING_RESULT = {'PASS' if all_ok else 'PARTIAL'}")
print("=" * 70)
sys.exit(0 if all_ok else 1)
