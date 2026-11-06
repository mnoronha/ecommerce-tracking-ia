"""Probe Fireflies GraphQL schema for available transcript fields."""
import sys, json
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, ".")
from app.core import fireflies_adapter as ff

# Try extended fields
q_extended = """
query {
  transcripts(limit: 10) {
    id
    title
    date
    duration
    organizer_email
    participants
    meeting_link
    calendar_id
  }
}
"""

q_basic = """
query {
  transcripts(limit: 10) {
    id
    title
    date
    duration
    participants
  }
}
"""

print("=== Testing extended fields ===")
try:
    r = ff._gql(q_extended)
    errors = r.get("errors")
    if errors:
        msgs = [e.get("message", "") for e in errors]
        print("FIELD_ERRORS:", msgs)
        print("→ Falling back to basic query")
        r2 = ff._gql(q_basic)
        txs = (r2.get("data") or {}).get("transcripts") or []
    else:
        txs = (r.get("data") or {}).get("transcripts") or []
except Exception as exc:
    print("EXCEPTION:", exc)
    txs = []

print(f"transcript count = {len(txs)}")
print()
for t in txs:
    ps = t.get("participants") or []
    # Extract domains from emails, don't expose full emails
    domains = set()
    names = []
    for p in ps:
        email = p if isinstance(p, str) else (p.get("email") or "")
        if "@" in email:
            domains.add(email.split("@")[1].lower())
        name = p if isinstance(p, str) else (p.get("name") or "")
        names.append(name[:30])

    date_ms = t.get("date")
    date_str = ""
    if date_ms:
        from datetime import datetime, timezone
        try:
            date_str = datetime.fromtimestamp(int(date_ms)/1000, tz=timezone.utc).strftime("%Y-%m-%d")
        except Exception:
            pass

    print(f"  FIREFLIES_MEETING_ID = {t.get('id')}")
    print(f"  TITLE                = {t.get('title')}")
    print(f"  DATE                 = {date_str}")
    print(f"  DURATION_S           = {t.get('duration')}")
    print(f"  ORGANIZER_EMAIL      = {t.get('organizer_email', 'NOT_IN_SCHEMA')}")
    print(f"  MEETING_LINK         = {t.get('meeting_link', 'NOT_IN_SCHEMA')}")
    print(f"  PARTICIPANT_COUNT    = {len(ps)}")
    print(f"  PARTICIPANT_DOMAINS  = {sorted(domains)}")
    print(f"  PARTICIPANT_NAMES    = {names[:5]}")
    print()
