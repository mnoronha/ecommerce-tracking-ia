"""
Apply migration 085 (fix_v2_artifact_hashes) via Supabase Management API.

Usage:
  cd apps/api
  railway run -- python -u scripts/_apply_migration_085.py
"""
import os, sys, json, urllib.request, urllib.error
sys.stdout.reconfigure(encoding="utf-8")
from pathlib import Path

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SERVICE_KEY  = (
    os.environ.get("SUPABASE_SERVICE_KEY") or
    os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or
    ""
)
ACCESS_TOKEN = os.environ.get("SUPABASE_ACCESS_TOKEN", "")

if not SUPABASE_URL or not SERVICE_KEY:
    print("ABORT: SUPABASE_URL or SUPABASE_SERVICE_KEY not set")
    sys.exit(1)

sql_path = Path(__file__).resolve().parents[1] / "migrations" / "085_fix_v2_artifact_hashes.sql"
sql = sql_path.read_text(encoding="utf-8")

print("Applying migration 085 (fix_v2_artifact_hashes)...")
print(f"  Supabase URL: {SUPABASE_URL[:50]}…")

parts = SUPABASE_URL.replace("https://", "").split(".")
project_ref = parts[0] if parts else ""

if project_ref and ACCESS_TOKEN:
    mgmt_url = f"https://api.supabase.com/v1/projects/{project_ref}/database/query"
    payload = json.dumps({"query": sql}).encode()
    req = urllib.request.Request(
        mgmt_url, data=payload, method="POST",
        headers={"Authorization": f"Bearer {ACCESS_TOKEN}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
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
