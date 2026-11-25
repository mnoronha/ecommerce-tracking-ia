"""
Repair artifact_hash for all v2+ (post-Hermes) canonical report records.

Background: apply_analysis() used an incorrect field-exclusion set when computing
artifact_hash for v2 records. The CAS RPC enforces IMMUTABLE_REPORT_VERSION and
blocks artifact_hash changes. This script generates a SQL migration that the
superuser can apply directly (bypassing the RPC guard).

Usage:
  cd apps/api
  railway run -- python -u scripts/_fix_v2_artifact_hashes.py

The script prints the SQL to stdout AND writes migrations/085_fix_v2_artifact_hashes.sql.
Apply the migration via the Supabase SQL editor or Management API.
"""
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.core.canonical_reports import digest

_HASH_EXCLUDE = frozenset({
    'report_id', 'report_type', 'artifact_hash', 'idempotency_key',
    'status', 'approved_at', 'approved_by', 'published_at',
    'approval_hash', 'generated_at',
    'superseded_at', 'superseded_by_actor', 'superseded_by_version',
})


def _correct_hash(doc: dict) -> str:
    return digest({k: v for k, v in doc.items() if k not in _HASH_EXCLUDE})


def run():
    from app.database import get_supabase
    sb = get_supabase()

    rows = (
        sb.table('core_canonical_report_records')
        .select('entity_key,revision,document')
        .like('entity_key', 'report:%')
        .execute()
        .data or []
    )

    targets = [
        r for r in rows
        if (r.get('document') or {}).get('analysis_request_id')
    ]

    print(f"Found {len(targets)} v2+ report records to check.")

    repairs = []
    already_ok = 0

    for row in targets:
        key = row['entity_key']
        doc = row['document']
        stored_hash = doc.get('artifact_hash', '')
        correct = _correct_hash(doc)

        if stored_hash == correct:
            already_ok += 1
            print(f"  OK        {key}")
            continue

        print(f"  NEEDS FIX {key}  {stored_hash[:16]}... -> {correct[:16]}...")
        repairs.append((key, correct))

    print()
    print(f"Records to fix: {len(repairs)}  already correct: {already_ok}")

    if not repairs:
        print("Nothing to do.")
        return True

    # Build migration SQL — superuser UPDATE bypasses CAS immutability guard.
    lines = ["BEGIN;", ""]
    for key, new_hash in repairs:
        escaped_key = key.replace("'", "''")
        escaped_hash = new_hash.replace("'", "''")
        lines.append(
            f"UPDATE core_canonical_report_records"
            f"  SET document = jsonb_set(document, '{{artifact_hash}}', '\"{escaped_hash}\"'::jsonb),"
            f"      revision = revision + 1,"
            f"      updated_at = now()"
            f"  WHERE entity_key = '{escaped_key}';"
        )
    lines += ["", "COMMIT;"]
    sql = "\n".join(lines)

    out_path = ROOT / "migrations" / "085_fix_v2_artifact_hashes.sql"
    out_path.write_text(sql, encoding="utf-8")
    print(f"\nMigration written to: {out_path}")
    print("\n--- SQL ---")
    print(sql)
    print("--- END ---")
    print("\nApply via Supabase SQL editor or Management API.")
    return True


if __name__ == '__main__':
    ok = run()
    sys.exit(0 if ok else 1)
