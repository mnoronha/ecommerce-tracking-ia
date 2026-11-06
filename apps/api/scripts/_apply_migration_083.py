"""
Apply migration 083 (core_fireflies_mapping_rules) via Supabase REST API.
Uses service-role key for DDL execution via the SQL endpoint.

Usage:
  cd apps/api
  railway run -- python -u scripts/_apply_migration_083.py
"""
import os, sys, json, urllib.request, urllib.error
sys.stdout.reconfigure(encoding="utf-8")

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SERVICE_KEY  = (
    os.environ.get("SUPABASE_SERVICE_KEY") or
    os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or
    ""
)

if not SUPABASE_URL or not SERVICE_KEY:
    print("ABORT: SUPABASE_URL or SUPABASE_SERVICE_KEY not set")
    sys.exit(1)

# Read migration SQL
sql_path = os.path.join(os.path.dirname(__file__), "..", "migrations", "083_fireflies_mapping_rules.sql")
with open(sql_path) as f:
    sql = f.read()

# Supabase exposes a /rest/v1/rpc/exec_sql endpoint (only on Pro/Enterprise)
# For free/starter: use the pg connection string instead
# Try the query endpoint
print("Applying migration 083...")
print(f"  Supabase URL: {SUPABASE_URL[:40]}…")

# Check if the table already exists first
check_url = f"{SUPABASE_URL}/rest/v1/core_fireflies_mapping_rules?select=id&limit=1"
hdrs = {
    "Authorization": f"Bearer {SERVICE_KEY}",
    "apikey": SERVICE_KEY,
}
req = urllib.request.Request(check_url, headers=hdrs, method="GET")
try:
    with urllib.request.urlopen(req, timeout=10) as r:
        print("TABLE_ALREADY_EXISTS: core_fireflies_mapping_rules")
        sys.exit(0)
except urllib.error.HTTPError as e:
    body = e.read().decode()[:300]
    if '"code":"42P01"' in body or "does not exist" in body:
        print("TABLE_MISSING — need to apply migration")
    else:
        print(f"CHECK_ERROR: status={e.code} body={body[:200]}")

# Apply via Supabase SQL API (available on all plans via service role)
sql_url = f"{SUPABASE_URL}/rest/v1/rpc/exec"
# This endpoint doesn't exist on standard Supabase — we need pg direct
# Try the alternative: POST to /pg/query (Supabase Studio API, not public)
# Best option: use the Supabase Management API /v1/projects/{ref}/database/query
# But we don't have the project ref from env here.

# Parse project ref from SUPABASE_URL: https://<ref>.supabase.co
parts = SUPABASE_URL.replace("https://", "").split(".")
if len(parts) >= 1:
    project_ref = parts[0]
    mgmt_url = f"https://api.supabase.com/v1/projects/{project_ref}/database/query"
    # Management API requires SUPABASE_ACCESS_TOKEN (personal access token)
    access_token = os.environ.get("SUPABASE_ACCESS_TOKEN", "")
    if access_token:
        payload = json.dumps({"query": sql}).encode()
        req2 = urllib.request.Request(
            mgmt_url,
            data=payload,
            method="POST",
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
            }
        )
        try:
            with urllib.request.urlopen(req2, timeout=30) as r:
                result = json.loads(r.read())
                print(f"MIGRATION_APPLIED: {result}")
                sys.exit(0)
        except urllib.error.HTTPError as e:
            body = e.read().decode()[:300]
            print(f"MGMT_API_ERROR: status={e.code} body={body}")
    else:
        print("SUPABASE_ACCESS_TOKEN not set — cannot use Management API")

print()
print("MANUAL APPLY REQUIRED.")
print("Paste the following SQL in your Supabase SQL Editor:")
print("=" * 60)
print(sql)
print("=" * 60)
sys.exit(1)
