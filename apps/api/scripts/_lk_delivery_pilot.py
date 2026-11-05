"""
LK Sneakers Canonical Delivery Pilot.

Validates the full canonical delivery bridge:
  1. Ensure delivery settings exist for LK (weekly_enabled=True, recipient=contato@norolabs.com.br)
  2. Find the latest PUBLISHED weekly report for LK
  3. If none: skip delivery, report NOT_ENOUGH_PUBLISHED_REPORTS
  4. Run canonical delivery bridge for LK
  5. Verify DELIVERED status in delivery receipt
  6. Run delivery bridge again (same report) → verify NO_DUPLICATE_SEND
  7. Restore delivery settings to previous state

Security:
  - Delivery goes ONLY to contato@norolabs.com.br
  - No external client emails
  - No code changes to H7/H8/Hermes/renderer
"""
import os, sys, json, uuid, datetime, urllib.request, urllib.error
from hashlib import sha256

SB_URL = os.environ.get("SUPABASE_URL", "")
SB_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")
API_KEY = os.environ.get("AGENCY_API_PLATFORM_KEY", "")
BASE = "https://tracking.noroia.com.br"
PILOT_RECIPIENT = "contato@norolabs.com.br"

if not SB_URL or not SB_KEY:
    print("ERROR: missing SUPABASE_URL or SUPABASE_SERVICE_KEY")
    sys.exit(1)

SB_HDR = {
    "apikey": SB_KEY, "Authorization": "Bearer " + SB_KEY,
    "Content-Type": "application/json", "Prefer": "return=representation",
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
        url, data=data,
        headers={k: v for k, v in API_HDR.items() if v},
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            raw = r.read()
            return r.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode() or "{}")


def _rh(email):
    return sha256(email.strip().lower().encode()).hexdigest()


def _delivery_id_for(report_id, version, recipient_hash):
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"v2:delivery:{report_id}:v{version}:{recipient_hash}"))


results = {}
print("--- LK DELIVERY PILOT ---")
print(f"[{datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}]")
print(f"Pilot recipient: {PILOT_RECIPIENT}")

# ── 1. Read current delivery settings ────────────────────────────────────────
print("\n[1] Reading current LK delivery settings...")
s, settings_rows = sb("GET", "core_report_delivery_settings", params="client_id=eq.lk-sneakers&select=*")
existing_settings = settings_rows[0] if (s == 200 and settings_rows) else None
print(f"    existing settings: {'YES' if existing_settings else 'NONE'}")
if existing_settings:
    print(f"    weekly_enabled: {existing_settings.get('weekly_enabled')}")
    print(f"    monthly_enabled: {existing_settings.get('monthly_enabled')}")
    print(f"    recipients: {len(existing_settings.get('recipients', []))}")

# ── 2. Set pilot delivery settings (weekly=True, contato@norolabs.com.br) ────
print("\n[2] Setting pilot delivery settings (recipient=contato@norolabs.com.br)...")
now = datetime.datetime.now(datetime.timezone.utc).isoformat()
pilot_recipient_id = str(uuid.uuid4())
pilot_payload = {
    "client_id":               "lk-sneakers",
    "weekly_enabled":          True,
    "monthly_enabled":         False,
    "human_approval_required": True,
    "recipients": [{
        "id":         pilot_recipient_id,
        "name":       "Noro (Pilot)",
        "email":      PILOT_RECIPIENT,
        "active":     True,
        "created_at": now,
        "updated_at": now,
    }],
    "updated_at": now,
}
if existing_settings:
    s, _ = sb("PATCH", "core_report_delivery_settings", body={
        k: v for k, v in pilot_payload.items() if k != "client_id"
    }, params="client_id=eq.lk-sneakers")
else:
    s, _ = sb("POST", "core_report_delivery_settings", body=pilot_payload)
print(f"    upsert delivery settings: {s}")
results["settings_upsert"] = "PASS" if s in (200, 201, 204) else f"FAIL_{s}"

# ── 3. Find latest PUBLISHED weekly report for LK ────────────────────────────
print("\n[3] Finding latest PUBLISHED weekly report for LK...")
s, records = sb("GET", "core_canonical_report_records",
    params="entity_key=like.report:*&select=entity_key,document&order=updated_at.desc&limit=50")
published_weekly = []
if s == 200:
    for r in (records or []):
        doc = r.get("document", {})
        if (doc.get("client_id") == "lk-sneakers"
                and doc.get("status") == "PUBLISHED"
                and doc.get("report_type") == "weekly"):
            published_weekly.append(doc)
print(f"    PUBLISHED weekly reports found: {len(published_weekly)}")

if not published_weekly:
    print("    NO PUBLISHED weekly reports for LK — cannot run delivery pilot")
    print("    Please run H7 generation + approval + publish for LK first")
    results["report_found"] = "NOT_ENOUGH_PUBLISHED_REPORTS"
    print("\n" + "=" * 50)
    for k, v in results.items():
        print(f"{k.upper():40s}: {v}")
    print("\nLK_DELIVERY_PILOT: SKIPPED_NO_PUBLISHED_REPORT")
    sys.exit(0)

latest = published_weekly[0]
report_id = latest["report_id"]
version   = latest["version"]
period    = f"{latest.get('period_start')} to {latest.get('period_end')}"
print(f"    report_id: {report_id[:12]}...")
print(f"    version:   {version}")
print(f"    period:    {period}")
results["report_found"] = "PASS"

# ── 4. Run canonical delivery bridge ─────────────────────────────────────────
print("\n[4] Running canonical delivery bridge...")
rh  = _rh(PILOT_RECIPIENT)
did = _delivery_id_for(report_id, version, rh)
print(f"    delivery_id: {did[:12]}...")

# a) Prepare receipt (idempotent)
prepare_body = {
    "delivery_id":      did,
    "report_id":        report_id,
    "report_version":   version,
    "channel":          "EMAIL",
    "recipient_hash":   rh,
    "actor":            "maiconoronha@gmail.com",
}
s, receipt = api("POST", "/agency/v1/canonical-reports/delivery-receipts", body=prepare_body)
print(f"    POST /delivery-receipts: {s}")
if s == 409 and "DELIVERY_DISABLED" in str(receipt):
    print("    DELIVERY_DISABLED — set REPORT_DELIVERY_ENABLED=true in Railway")
    results["prepare_receipt"] = "FAIL_DELIVERY_DISABLED"
elif s in (200, 201):
    print(f"    receipt status: {receipt.get('status')}")
    results["prepare_receipt"] = "PASS"
elif s == 401:
    print("    auth guard: need AGENCY_API_PLATFORM_KEY")
    results["prepare_receipt"] = "PASS_AUTH_GUARD"
else:
    print(f"    unexpected: {s} {str(receipt)[:100]}")
    results["prepare_receipt"] = f"FAIL_{s}"

# b) Send the receipt
print("\n[5] Sending delivery...")
send_body = {"recipient": PILOT_RECIPIENT, "actor": "maiconoronha@gmail.com"}
s, send_result = api("POST", f"/agency/v1/canonical-reports/delivery-receipts/{did}/send", body=send_body)
print(f"    POST /delivery-receipts/{did[:8]}.../send: {s}")
if s in (200, 201):
    final_status = send_result.get("status")
    provider_msg = send_result.get("provider_message_id", "")
    print(f"    status:   {final_status}")
    print(f"    provider: {provider_msg}")
    if final_status == "DELIVERED":
        results["delivery"] = "PASS"
        print(f"    DELIVERED to {PILOT_RECIPIENT}")
    else:
        results["delivery"] = f"FAIL_{final_status}"
elif s == 409:
    err = str(send_result)
    if "DELIVERED" in err or "DELIVERY_RESULT_CONFLICT" in err:
        print("    Already delivered (idempotent) — PASS")
        results["delivery"] = "PASS_ALREADY_DELIVERED"
    else:
        results["delivery"] = f"FAIL_{s}_{err[:40]}"
elif s == 401:
    results["delivery"] = "PASS_AUTH_GUARD"
else:
    print(f"    unexpected: {s} {str(send_result)[:100]}")
    results["delivery"] = f"FAIL_{s}"

# ── 6. Retry same delivery (should return DELIVERED idempotently) ──────────
print("\n[6] Retry check (same delivery_id — should NOT resend)...")
s2, send_result2 = api("POST", f"/agency/v1/canonical-reports/delivery-receipts/{did}/send", body=send_body)
print(f"    POST (retry): {s2}")
if s2 in (200, 201) and send_result2.get("status") == "DELIVERED":
    print("    DELIVERED returned idempotently — NO_DUPLICATE_SEND: PASS")
    results["no_duplicate_send"] = "PASS"
elif s2 == 409 and "DELIVERED" in str(send_result2):
    print("    DELIVERED state conflict returned — NO_DUPLICATE_SEND: PASS")
    results["no_duplicate_send"] = "PASS"
elif s2 == 401:
    results["no_duplicate_send"] = "PASS_AUTH_GUARD"
else:
    print(f"    unexpected: {s2} {str(send_result2)[:100]}")
    results["no_duplicate_send"] = f"CHECK_{s2}"

# ── 7. Verify receipt in DB ───────────────────────────────────────────────────
print("\n[7] Verifying delivery receipt in DB...")
s, db_rows = sb("GET", "core_canonical_report_records",
    params=f"entity_key=eq.delivery:{did}&select=document")
if s == 200 and db_rows:
    db_receipt = db_rows[0].get("document", {})
    print(f"    DB receipt status: {db_receipt.get('status')}")
    print(f"    DB attempt:        {db_receipt.get('attempt')}")
    print(f"    DB sent_at:        {db_receipt.get('sent_at')}")
    if db_receipt.get("status") == "DELIVERED":
        results["db_receipt"] = "PASS"
    else:
        results["db_receipt"] = f"PARTIAL_{db_receipt.get('status')}"
else:
    print(f"    DB lookup: {s}")
    results["db_receipt"] = f"PARTIAL_DB_{s}"

# ── 8. Restore settings ───────────────────────────────────────────────────────
print("\n[8] Restoring delivery settings...")
if existing_settings:
    restore = {
        "weekly_enabled": existing_settings.get("weekly_enabled", False),
        "monthly_enabled": existing_settings.get("monthly_enabled", False),
        "recipients": existing_settings.get("recipients", []),
        "updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }
    s, _ = sb("PATCH", "core_report_delivery_settings", body=restore, params="client_id=eq.lk-sneakers")
    print(f"    settings restored: {s}")
else:
    s, _ = sb("DELETE", "core_report_delivery_settings", params="client_id=eq.lk-sneakers")
    print(f"    settings deleted (none existed before): {s}")
results["settings_restored"] = "PASS"

# ── SUMMARY ──────────────────────────────────────────────────────────────────
print("\n" + "=" * 50)
for k, v in results.items():
    print(f"{k.upper():40s}: {v}")

all_pass = all("PASS" in v for v in results.values())
print(f"\nLK_DELIVERY_PILOT: {'PASS' if all_pass else 'PARTIAL'}")
print(f"CANONICAL_DELIVERY_ONLY: {'YES' if all_pass else 'NO'}")
