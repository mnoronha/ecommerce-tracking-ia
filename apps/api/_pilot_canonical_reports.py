"""
Pilot: generate H7 (weekly) + H8 (monthly) canonical reports for LK Sneakers.
Version 2 — full analysis layer (CANONICAL_ANALYSIS_ENABLED=true).

Run from apps/api/:
    $env:PYTHONUTF8='1'; python _pilot_canonical_reports.py

Requires SUPABASE_URL + SUPABASE_SERVICE_KEY + ANTHROPIC_API_KEY in .env (or set in shell env).
AUTO_SEND=0, CLIENT_SENDS=0, MEDIA_WRITES=0. Does NOT publish or send.
"""
import os, sys

# Load .env before anything else so Railway secrets are available locally.
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# Enable full analysis for this pilot (production-equivalent).
# Set CANONICAL_ANALYSIS_ENABLED=false to run data-only dry-run.
os.environ["CANONICAL_WEEKLY_REVIEW_ENABLED"]  = "true"
os.environ["CANONICAL_MONTHLY_REVIEW_ENABLED"] = "true"
os.environ.setdefault("CANONICAL_ANALYSIS_ENABLED", "true")

from datetime import date
from app.core.canonical_weekly_scheduler  import run_canonical_weekly_for_client
from app.core.canonical_monthly_scheduler import run_canonical_monthly_for_client

CLIENT = "lk-sneakers"

# H7 — last complete week: Mon 2026-10-19 to Sun 2026-10-25
# v2 to avoid IMMUTABLE_VERSION_CONFLICT with v1 (PUBLISHED on 2026-10-26)
WEEKLY_START = date(2026, 10, 19)
WEEKLY_END   = date(2026, 10, 25)

# H8 — September 2026 (last complete month)
# v2 to avoid conflict with v1 (READY_FOR_REVIEW since 2026-10-24)
MONTHLY_START = date(2026,  9,  1)
MONTHLY_END   = date(2026,  9, 30)

analysis_on = os.environ.get("CANONICAL_ANALYSIS_ENABLED", "true").lower() == "true"
print(f"CANONICAL_ANALYSIS_ENABLED = {analysis_on}")
print()

print(f"=== H7 WEEKLY v2 | {WEEKLY_START} to {WEEKLY_END} ===")
r_w = run_canonical_weekly_for_client(CLIENT, WEEKLY_START, WEEKLY_END, version=2)
print(r_w)
print()

print(f"=== H8 MONTHLY v2 | {MONTHLY_START} to {MONTHLY_END} ===")
r_m = run_canonical_monthly_for_client(CLIENT, MONTHLY_START, MONTHLY_END, version=2)
print(r_m)
