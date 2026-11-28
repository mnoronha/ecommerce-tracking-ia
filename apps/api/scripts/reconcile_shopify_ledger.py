"""
Read-only CLI for the Shopify ↔ Core Reconciliation Ledger (PRD §6 / R06).

Usage:
  cd apps/api
  python scripts/reconcile_shopify_ledger.py --client lk-sneakers --start 2026-10-01 --end 2026-10-31

NEVER writes to Shopify or Supabase. Emits structured JSON reconciliation ledger to stdout.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.shopify_reconciliation import build_shopify_reconciliation_ledger


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Read-only Shopify Reconciliation Ledger (PRD §6 / R06)"
    )
    parser.add_argument("--client", required=True, help="Client slug, pixel_id, or UUID")
    parser.add_argument("--start", required=True, help="Period start YYYY-MM-DD (store timezone)")
    parser.add_argument("--end", required=True, help="Period end YYYY-MM-DD (store timezone)")
    args = parser.parse_args()

    ledger = build_shopify_reconciliation_ledger(
        args.client,
        date.fromisoformat(args.start),
        date.fromisoformat(args.end),
    )
    print(json.dumps(ledger, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
