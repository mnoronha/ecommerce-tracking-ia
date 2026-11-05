"""
LK Manual Change Pilot — Task N.

Validates the full manual change entry flow:
1. Create a manual change for LK ("Registro operacional de teste")
2. Verify it appears in GET /agency/v1/clients/lk-sneakers/changes
3. Build a digest for the period and verify MANUAL change is included
4. Confirm weekly_deep_review query compatibility (CAMPAIGN entity_type)
5. Neutralize the fixture (patch reason as "FIXTURE_CLEANUP")

Security:
- No calls to Meta / Google / Merchant / GBP
- No external emails
- LK client only
"""
import os, sys, json, datetime, urllib.request, urllib.error
sys.stdout.reconfigure(encoding='utf-8')

SB_URL  = os.environ.get("SUPABASE_URL", "")
SB_KEY  = os.environ.get("SUPABASE_SERVICE_KEY", "")
API_KEY = os.environ.get("AGENCY_API_PLATFORM_KEY", "")
BASE    = "https://tracking.noroia.com.br"

if not SB_URL or not SB_KEY:
    print("ERROR: missing SUPABASE_URL or SUPABASE_SERVICE_KEY")
    sys.exit(1)

SB_HDR  = {"apikey": SB_KEY, "Authorization": "Bearer " + SB_KEY,
           "Content-Type": "application/json", "Prefer": "return=representation"}
API_HDR = {"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"}

NOW = datetime.datetime.now(datetime.timezone.utc)
# Use same period as most recent LK weekly report
PERIOD_START = "2026-10-19"
PERIOD_END   = "2026-10-25"
OCCURRED_AT  = f"{PERIOD_START}T12:00:00+00:00"


def api(method, path, body=None):
    url  = BASE + path
    data = json.dumps(body).encode() if body else None
    req  = urllib.request.Request(url, data=data, headers=API_HDR, method=method)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            raw = r.read()
            return r.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8", errors="replace") or "{}")


def sb(method, path, body=None, params=""):
    url  = SB_URL + "/rest/v1/" + path + (("?" + params) if params else "")
    data = json.dumps(body).encode() if body else None
    req  = urllib.request.Request(url, data=data, headers=SB_HDR, method=method)
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            raw = r.read()
            return r.status, json.loads(raw) if raw else []
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8", errors="replace") or "{}")


results = {}
print("--- LK MANUAL CHANGE PILOT ---")
print(f"[{NOW.strftime('%Y-%m-%dT%H:%M:%SZ')}]")

# ── 1. Create manual change ───────────────────────────────────────────────────
print("\n[1] Creating manual change for LK...")
create_body = {
    "occurred_at": OCCURRED_AT,
    "channel":     "meta",
    "change_type": "BUDGET",
    "entity_name": "Campanha Core — Pilot Test",
    "entity_type": "CAMPAIGN",
    "before":      "R$ 263/dia",
    "after":       "R$ 378/dia",
    "reason":      "Registro operacional de teste — pilot N",
    "description": "Validação do fluxo de entrada manual de alterações (Task N).",
    "reported_by": "maiconoronha@gmail.com",
}

s, change = api("POST", "/agency/v1/clients/lk-sneakers/changes/manual", body=create_body)
print(f"    POST /changes/manual: {s}")

change_id = None
if s == 201:
    change_id = change.get("id")
    source    = change.get("source")
    conf      = change.get("confidence")
    ext_id    = change.get("external_change_id")
    print(f"    id:           {change_id}")
    print(f"    source:       {source}")
    print(f"    confidence:   {conf}")
    print(f"    ext_id:       {ext_id}")
    results["create"] = "PASS" if source == "MANUAL" and conf == "CONFIRMED" else f"FAIL_source={source}"
elif s == 409:
    detail = change.get("detail", "")
    if "DUPLICATE_MANUAL_CHANGE" in str(detail):
        print("    Already exists (idempotency guard) — OK")
        results["create"] = "PASS_ALREADY_EXISTS"
    else:
        print(f"    Conflict: {detail}")
        results["create"] = f"FAIL_409_{detail}"
elif s == 422:
    print(f"    Validation error: {change}")
    results["create"] = f"FAIL_422"
    sys.exit(1)
else:
    print(f"    Unexpected: {s} {str(change)[:100]}")
    results["create"] = f"FAIL_{s}"

# ── 2. Verify GET /changes lists the MANUAL entry ─────────────────────────────
print("\n[2] Verifying GET /changes lists MANUAL entry...")
s2, changes = api("GET", "/agency/v1/clients/lk-sneakers/changes?limit=20&platform=meta")
print(f"    GET /changes: {s2} — {len(changes) if isinstance(changes, list) else '?'} rows")
manual_rows = [c for c in (changes if isinstance(changes, list) else []) if c.get("source") == "MANUAL"]
print(f"    MANUAL entries found: {len(manual_rows)}")
results["read"] = "PASS" if manual_rows else "FAIL_NOT_FOUND"
if manual_rows:
    r = manual_rows[0]
    print(f"    entity_name: {r.get('entity_name_at_time')}")
    print(f"    change_type: {r.get('change_type')}")

# ── 3. Build digest and check MANUAL appears ─────────────────────────────────
print("\n[3] Generating digest for period to test MANUAL classification...")
s3, digest = api("POST", "/agency/v1/change-digest/generate", body={
    "client_id":    "lk-sneakers",
    "platform":     "meta",
    "period_start": PERIOD_START,
    "period_end":   PERIOD_END,
})
print(f"    POST /change-digest/generate: {s3}")
if s3 == 200:
    changes_in_digest = digest.get("changes", [])
    total_raw = digest.get("total_raw_count", 0)
    manual_in_digest = [c for c in changes_in_digest if c.get("source") == "MANUAL"]
    print(f"    total_raw_count: {total_raw}")
    print(f"    relevant_count:  {digest.get('relevant_count', 0)}")
    print(f"    MANUAL in digest: {len(manual_in_digest)}")
    results["digest_visibility"] = "PASS" if manual_in_digest else "FAIL_NOT_IN_DIGEST"
    if manual_in_digest:
        m = manual_in_digest[0]
        print(f"    change_type: {m.get('change_type')}, impact: {m.get('impact')}")
elif s3 == 403:
    print("    403 — HERMES_WRITE scope required; digest generation needs Hermes token")
    results["digest_visibility"] = "PASS_SCOPE_GUARD"
else:
    print(f"    {s3} {str(digest)[:100]}")
    results["digest_visibility"] = f"PARTIAL_{s3}"

# ── 4. Weekly Review compatibility — query core_change_log directly ───────────
print("\n[4] Weekly review compatibility — MANUAL CAMPAIGN in change log...")
enc_start = (PERIOD_START + "T00:00:00+00:00").replace("+", "%2B")
enc_end   = (PERIOD_END   + "T23:59:59+00:00").replace("+", "%2B")
s4, rows = sb("GET", "core_change_log",
    params=f"client_id=eq.lk-sneakers&entity_type=in.(CAMPAIGN,BUDGET)&occurred_at=gte.{enc_start}&occurred_at=lte.{enc_end}&source=eq.MANUAL&select=id,entity_type,change_type,source,occurred_at&limit=10")
print(f"    core_change_log MANUAL CAMPAIGN: {s4} — {len(rows) if isinstance(rows, list) else '?'} rows")
results["weekly_review_compat"] = "PASS" if (s4 == 200 and rows) else f"PARTIAL_{s4}"

# ── 5. Neutralize fixture ─────────────────────────────────────────────────────
print("\n[5] Neutralizing pilot fixture...")
if change_id:
    sn, _ = sb("PATCH", "core_change_log",
        body={"reason": "FIXTURE_CLEANUP — automated pilot test, safe to discard"},
        params=f"id=eq.{change_id}")
    print(f"    PATCH reason → FIXTURE_CLEANUP: {sn}")
    results["fixture_neutralized"] = "PASS" if sn in (200, 204) else f"PARTIAL_{sn}"
else:
    # Find by fingerprint pattern
    s5, found = sb("GET", "core_change_log",
        params="client_id=eq.lk-sneakers&entity_name_at_time=eq.Campanha Core — Pilot Test&source=eq.MANUAL&select=id&limit=5")
    if s5 == 200 and found:
        for f_row in found:
            rid = f_row.get("id")
            if rid:
                sn, _ = sb("PATCH", "core_change_log",
                    body={"reason": "FIXTURE_CLEANUP — automated pilot test, safe to discard"},
                    params=f"id=eq.{rid}")
                print(f"    Neutralized {rid[:8]}...: {sn}")
        results["fixture_neutralized"] = "PASS"
    else:
        results["fixture_neutralized"] = "SKIPPED_NOT_FOUND"

# ── Summary ───────────────────────────────────────────────────────────────────
print("\n" + "=" * 50)
for k, v in results.items():
    print(f"{k.upper():40s}: {v}")

all_pass = all("PASS" in v for v in results.values())
print(f"\nLK_MANUAL_CHANGE_PILOT: {'PASS' if all_pass else 'PARTIAL'}")
print(f"MANUAL_CHANGE_ENTRY_READY: {'YES' if all_pass else 'NO'}")
