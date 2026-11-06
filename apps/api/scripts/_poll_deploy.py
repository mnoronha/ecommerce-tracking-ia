"""Poll for new endpoint to become available (deploy check)."""
import sys, os, time, urllib.request, urllib.error
sys.stdout.reconfigure(encoding="utf-8")

API_KEY = os.environ.get("AGENCY_API_ADMIN_KEY", "") or os.environ.get("AGENCY_API_PLATFORM_KEY", "")
BASE = "https://tracking.noroia.com.br"

deadline = time.time() + 300  # 5 min
attempt  = 0

while time.time() < deadline:
    attempt += 1
    try:
        req = urllib.request.Request(
            BASE + "/agency/v1/fireflies/mapping-rules",
            headers={"Authorization": "Bearer " + API_KEY}
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            print(f"attempt {attempt}: DEPLOYED status={r.status}")
            sys.exit(0)
    except urllib.error.HTTPError as e:
        body = e.read().decode()[:100]
        if e.code == 404 and "Not Found" in body:
            # Endpoint not yet deployed
            print(f"attempt {attempt}: NOT_YET status=404 (waiting...)", flush=True)
        elif e.code in (200, 503):
            print(f"attempt {attempt}: DEPLOYED status={e.code}", flush=True)
            sys.exit(0)
        else:
            print(f"attempt {attempt}: status={e.code} body={body}", flush=True)
            if e.code not in (502, 503, 504):
                # Likely deployed but table missing
                print("DEPLOY DETECTED (endpoint exists, table may be pending)")
                sys.exit(0)
    except Exception as exc:
        print(f"attempt {attempt}: ERROR {exc}", flush=True)
    time.sleep(20)

print("TIMEOUT: deploy not detected within 5 minutes")
sys.exit(1)
