"""
Phase 6.6 Production Smoke Test
Reads AGENCY_API_ADMIN_KEY and AGENCY_API_PLATFORM_KEY from env vars — keys never printed.

Usage (PowerShell):
  $env:AGENCY_API_ADMIN_KEY="<key>"; $env:AGENCY_API_PLATFORM_KEY="<key>"; python scripts/smoke_p66.py
"""
import os, sys
import urllib.request, urllib.error, json

API  = "https://ecommerce-tracking-ia-production.up.railway.app"
ADMIN_KEY    = os.environ.get("AGENCY_API_ADMIN_KEY", "")
PLATFORM_KEY = os.environ.get("AGENCY_API_PLATFORM_KEY", "")

if not ADMIN_KEY or not PLATFORM_KEY:
    print("BLOCKED: AGENCY_API_ADMIN_KEY e/ou AGENCY_API_PLATFORM_KEY não configuradas.")
    sys.exit(1)

results = {}

def get(path, bearer=None):
    req = urllib.request.Request(f"{API}{path}")
    if bearer:
        req.add_header("Authorization", f"Bearer {bearer}")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code
    except Exception:
        return 0

def check(name, got, expect):
    ok = got == expect
    results[name] = "PASS" if ok else f"FAIL (got {got}, expected {expect})"
    print(f"  {'✓' if ok else '✗'} {name}: HTTP {got} (expect {expect})")
    return ok

print("\n── Finance Auth ──────────────────────────────────────────")
check("FINANCE_UNAUTHENTICATED",   get("/finance/summary"),                      401)
check("FINANCE_PLATFORM_WEB",      get("/finance/summary",   PLATFORM_KEY),      403)
check("FINANCE_ADMIN_ACCESS",      get("/finance/summary",   ADMIN_KEY),         200)
check("FINANCE_CONTRACTS_AUTH",    get("/finance/contracts", ADMIN_KEY),         200)
check("FINANCE_PAYABLES_AUTH",     get("/finance/payables",  ADMIN_KEY),         200)

print("\n── Agency API Health ────────────────────────────────────")
check("AGENCY_API_HEALTH",    get("/agency/v1/system/health",    PLATFORM_KEY),  200)
check("CLIENT_LIST_READ",     get("/agency/v1/clients",          PLATFORM_KEY),  200)
check("CHANGES_READ",         get("/agency/v1/clients/lk-sneakers/changes",      PLATFORM_KEY), 200)
check("TASKS_READ",           get("/agency/v1/clients/lk-sneakers/tasks",        PLATFORM_KEY), 200)
check("OPTIMIZATION_HISTORY", get("/agency/v1/clients/lk-sneakers/recommendations", PLATFORM_KEY), 200)

print("\n── Client Isolation ────────────────────────────────────")
# Admin key can read any client; platform_web also
check("ADMIN_CAN_READ_CLIENT", get("/agency/v1/clients/lk-sneakers/tasks", ADMIN_KEY), 200)

print("\n── Task Visibility (API-level filter) ──────────────────")
# Covered by unit tests in test_phase66_validation.py; confirm endpoint responds
check("TASKS_ENDPOINT_LIVE", get("/agency/v1/clients/lk-sneakers/tasks", PLATFORM_KEY), 200)

print("\n── Summary ─────────────────────────────────────────────")
passed = sum(1 for v in results.values() if v == "PASS")
total  = len(results)
all_ok = passed == total
print(f"  {passed}/{total} passed")
print()
if all_ok:
    print("PHASE_6_6_PRODUCTION = CLOSED")
else:
    print("PHASE_6_6_PRODUCTION = BLOCKED")
    for k, v in results.items():
        if v != "PASS":
            print(f"  FAIL: {k} → {v}")

sys.exit(0 if all_ok else 1)
