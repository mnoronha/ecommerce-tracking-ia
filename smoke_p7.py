"""
Phase 7 Runtime Smoke Test — Noro Tracking Ingestion.

Sends fixture events to the production endpoint.
Reads AGENCY_API_PLATFORM_KEY from env; no keys ever printed.

Usage (PowerShell):
  $env:AGENCY_API_PLATFORM_KEY="<key>"; python smoke_p7.py

Zero external writes:
  - NO Meta CAPI sends
  - NO Google Enhanced Conversion sends
  - NO Platform mutations
"""
import json
import os
import sys
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone

API = "https://ecommerce-tracking-ia-production.up.railway.app"
PLATFORM_KEY = os.environ.get("AGENCY_API_PLATFORM_KEY", "")
TEST_CLIENT = "lk-sneakers"

if not PLATFORM_KEY:
    print("BLOCKED: AGENCY_API_PLATFORM_KEY not set.")
    sys.exit(1)

results: dict[str, str] = {}


def _call(method: str, path: str, body=None, bearer=None) -> tuple[int, dict]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        f"{API}{path}",
        data=data,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    if bearer:
        req.add_header("Authorization", f"Bearer {bearer}")
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        try:
            detail = json.loads(e.read())
        except Exception:
            detail = {}
        return e.code, detail
    except Exception as exc:
        return 0, {"error": str(exc)}


def check(name: str, got: int, expect: int, detail: dict | None = None):
    ok = got == expect
    results[name] = "PASS" if ok else f"FAIL (got {got}, expected {expect})"
    marker = "✓" if ok else "✗"
    extra = ""
    if not ok and detail:
        extra = f"  → {detail}"
    print(f"  {marker} {name}: HTTP {got} (expect {expect}){extra}")
    return ok


# ── 1. Unauthenticated → 401 ─────────────────────────────────────────────────
print("\n── Auth Gates ───────────────────────────────────────────")
code, body = _call("POST", f"/agency/v1/clients/{TEST_CLIENT}/tracking/events",
                   body={"events": []})
check("TRACKING_UNAUTHENTICATED_401", code, 401)

# ── 2. Unknown client → 404 ──────────────────────────────────────────────────
code, body = _call("POST", "/agency/v1/clients/nonexistent-client-xyz/tracking/events",
                   bearer=PLATFORM_KEY,
                   body={"events": [{"event_id": "x", "event_name": "page_view",
                                     "occurred_at": datetime.now(timezone.utc).isoformat()}]})
check("TRACKING_UNKNOWN_CLIENT_404", code, 404, body)

# ── 3. Valid page_view ────────────────────────────────────────────────────────
print("\n── Single Event Ingestion ───────────────────────────────")
eid_pv = f"smoke-pv-{uuid.uuid4()}"
code, body = _call("POST", f"/agency/v1/clients/{TEST_CLIENT}/tracking/events",
                   bearer=PLATFORM_KEY,
                   body={"events": [{
                       "event_id": eid_pv,
                       "event_name": "page_view",
                       "occurred_at": datetime.now(timezone.utc).isoformat(),
                       "session_id": f"smoke-sess-{uuid.uuid4()}",
                       "visitor_cookie_id": f"smoke-vc-{uuid.uuid4()}",
                       "url": "https://example.com/smoke-test",
                       "utm_source": "smoke_test",
                       "utm_medium": "test",
                   }]})
check("PAGE_VIEW_INSERTED", code, 200, body)
if code == 200:
    assert body.get("events_inserted") == 1, f"Expected 1 inserted, got: {body}"
    print(f"    counters: {body}")
    results["PAGE_VIEW_COUNTERS_OK"] = "PASS"
else:
    results["PAGE_VIEW_COUNTERS_OK"] = f"SKIP (HTTP {code})"

# ── 4. Duplicate detection ────────────────────────────────────────────────────
print("\n── Deduplication ────────────────────────────────────────")
code, body = _call("POST", f"/agency/v1/clients/{TEST_CLIENT}/tracking/events",
                   bearer=PLATFORM_KEY,
                   body={"events": [{
                       "event_id": eid_pv,  # same event_id as above
                       "event_name": "page_view",
                       "occurred_at": datetime.now(timezone.utc).isoformat(),
                   }]})
check("DUPLICATE_RETURNS_200", code, 200, body)
if code == 200:
    dedup = body.get("events_deduplicated", -1)
    ok = dedup == 1
    results["DUPLICATE_DEDUPLICATED"] = "PASS" if ok else f"FAIL (dedup={dedup})"
    print(f"  {'✓' if ok else '✗'} DUPLICATE_DEDUPLICATED: dedup={dedup}")

# ── 5. Purchase event ─────────────────────────────────────────────────────────
print("\n── Purchase Event ───────────────────────────────────────")
eid_pur = f"smoke-pur-{uuid.uuid4()}"
code, body = _call("POST", f"/agency/v1/clients/{TEST_CLIENT}/tracking/events",
                   bearer=PLATFORM_KEY,
                   body={"events": [{
                       "event_id": eid_pur,
                       "event_name": "purchase",
                       "occurred_at": datetime.now(timezone.utc).isoformat(),
                       "order_id": f"SMOKE-{uuid.uuid4()}",
                       "value": 199.90,
                       "currency": "BRL",
                       "visitor_cookie_id": f"smoke-vc-pur-{uuid.uuid4()}",
                       "session_id": f"smoke-sess-pur-{uuid.uuid4()}",
                       "utm_source": "meta",
                       "utm_medium": "paid",
                       "fbp": "fb.1.1234.5678",
                   }]})
check("PURCHASE_INSERTED", code, 200, body)
if code == 200:
    assert body.get("events_inserted") == 1, f"Expected 1 inserted, got: {body}"
    print(f"    counters: {body}")
    results["PURCHASE_COUNTERS_OK"] = "PASS"
else:
    results["PURCHASE_COUNTERS_OK"] = f"SKIP (HTTP {code})"

# ── 6. Validation reject ──────────────────────────────────────────────────────
print("\n── Validation ───────────────────────────────────────────")
code, body = _call("POST", f"/agency/v1/clients/{TEST_CLIENT}/tracking/events",
                   bearer=PLATFORM_KEY,
                   body={"events": [{
                       "event_id": f"smoke-rej-{uuid.uuid4()}",
                       "event_name": "purchase",  # missing order_id/value/currency
                       "occurred_at": datetime.now(timezone.utc).isoformat(),
                   }]})
check("PURCHASE_MISSING_FIELDS_200", code, 200, body)
if code == 200:
    rej = body.get("events_rejected", -1)
    ok = rej == 1
    results["PURCHASE_REJECTED_REASON"] = "PASS" if ok else f"FAIL (rejected={rej})"
    print(f"  {'✓' if ok else '✗'} PURCHASE_REJECTED_REASON: rejected={rej}")

# ── 7. Batch ingestion ────────────────────────────────────────────────────────
print("\n── Batch Ingestion ──────────────────────────────────────")
batch_events = [
    {"event_id": f"smoke-b{i}-{uuid.uuid4()}", "event_name": "page_view",
     "occurred_at": datetime.now(timezone.utc).isoformat()}
    for i in range(5)
]
code, body = _call("POST", f"/agency/v1/clients/{TEST_CLIENT}/tracking/events",
                   bearer=PLATFORM_KEY,
                   body={"events": batch_events})
check("BATCH_5_INSERTED", code, 200, body)
if code == 200:
    ins = body.get("events_inserted", -1)
    ok = ins == 5
    results["BATCH_COUNTERS_OK"] = "PASS" if ok else f"FAIL (inserted={ins})"
    print(f"  {'✓' if ok else '✗'} BATCH_COUNTERS_OK: inserted={ins}")

# ── Summary ───────────────────────────────────────────────────────────────────
print("\n── Summary ─────────────────────────────────────────────")
passed = sum(1 for v in results.values() if v == "PASS")
total = len(results)
all_ok = passed == total
print(f"  {passed}/{total} passed")
print()
if all_ok:
    print("PHASE_7_RUNTIME_SMOKE = PASS")
else:
    print("PHASE_7_RUNTIME_SMOKE = BLOCKED")
    for k, v in results.items():
        if v != "PASS":
            print(f"  FAIL: {k} → {v}")

sys.exit(0 if all_ok else 1)
