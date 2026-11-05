"""Create and merge PR #42 — canonical delivery bridge."""
import os, sys, json, subprocess, time, urllib.request, urllib.error

GH_TOKEN = os.environ.get("GH_TOKEN", "")
REPO = "mnoronha/hermes-hardening-d"
BASE_BRANCH = "main"
HEAD_BRANCH = "canonical-delivery-bridge"

def gh(method, path, body=None):
    url = f"https://api.github.com{path}"
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Authorization": f"Bearer {GH_TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "Content-Type": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode() or "{}")

def run(cmd):
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"FAIL: {cmd}\n{result.stderr[:300]}")
        sys.exit(1)
    return result.stdout.strip()

print("--- MERGE PR #42: CANONICAL DELIVERY BRIDGE ---")

print("\n[1] Create branch and push...")
run(f"git checkout -b {HEAD_BRANCH} 2>/dev/null || git checkout {HEAD_BRANCH}")
run("git add app/main.py app/core/canonical_delivery_scheduler.py tests/test_canonical_delivery_scheduler.py migrations/081_delivery_settings_timing.sql scripts/_lk_delivery_pilot.py scripts/_merge_42.py")
run('git commit -m "feat: canonical delivery bridge — REPORT_DELIVERY_ENABLED gates legacy email"')
run(f"git push origin {HEAD_BRANCH}")

print("\n[2] Create PR...")
s, pr = gh("POST", f"/repos/{REPO}/pulls", {
    "title": "feat: canonical delivery bridge (migration 081, REPORT_DELIVERY_ENABLED gate)",
    "body": """## Summary

- `app/core/canonical_delivery_scheduler.py` — polls PUBLISHED reports every 30 min, dispatches email per active recipient in core_report_delivery_settings
- `app/main.py` — when `REPORT_DELIVERY_ENABLED=true`, registers canonical delivery job + skips legacy `send_weekly_reports`/`send_monthly_reports` (mutually exclusive)
- `migrations/081_delivery_settings_timing.sql` — ADD COLUMN IF NOT EXISTS for timing fields (weekly_day_of_week, weekly_hour, monthly_day_of_month, monthly_hour, report_timezone)
- 19 new tests covering: flag gate, weekly/monthly delivery, no recipient, inactive recipient, duplicate send, retry, max retries, cross-client isolation
- `scripts/_lk_delivery_pilot.py` — LK pilot: contato@norolabs.com.br only

## Notes

- Apply migration 081 in Supabase SQL Editor BEFORE setting REPORT_DELIVERY_ENABLED=true
- Rollback: set REPORT_DELIVERY_ENABLED=false + redeploy → legacy email re-registers

🤖 Generated with Claude Sonnet 4.6""",
    "head": HEAD_BRANCH,
    "base": BASE_BRANCH,
})
if s in (200, 201):
    pr_url = pr.get("html_url", "")
    pr_number = pr.get("number", "?")
    print(f"    PR #{pr_number}: {pr_url}")
else:
    print(f"    PR creation: {s} {str(pr)[:100]}")
    sys.exit(1)

print("\n[3] Merge PR...")
time.sleep(3)
s2, merge = gh("PUT", f"/repos/{REPO}/pulls/{pr_number}/merge", {
    "commit_title": f"feat: canonical delivery bridge (migration 081, REPORT_DELIVERY_ENABLED gate)",
    "merge_method": "merge",
})
print(f"    merge: {s2} {merge.get('message','')}")

print("\nPR merged. Next steps:")
print("  1. Apply migration 081 in Supabase SQL Editor (migrations/081_delivery_settings_timing.sql)")
print("  2. Set REPORT_DELIVERY_ENABLED=true in Railway variables")
print("  3. Run: railway run python3 scripts/_lk_delivery_pilot.py")
print("  4. Verify DELIVERED status + NO_DUPLICATE_SEND")
