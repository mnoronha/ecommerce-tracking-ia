"""
Canonical delivery bridge.

Polls core_canonical_report_records for PUBLISHED reports and dispatches
emails to each active recipient in core_report_delivery_settings.

Idempotency: delivery_id = UUID5(NAMESPACE_URL, f"v2:delivery:{report_id}:v{version}:{recipient_hash}")
  - Same report + version + recipient always produces the same delivery_id.
  - prepare_delivery CAS returns existing receipt without mutation.
  - DELIVERED receipts are skipped immediately by dispatch_delivery.

Gate: runs only when REPORT_DELIVERY_ENABLED=true (same flag as the API endpoints).
Retry: DELIVERY_FAILED receipts with attempt < MAX_RETRIES are retried.
No auto-approve: only reports that are already PUBLISHED are eligible.
No external sends: recipients come from core_report_delivery_settings (agency-managed).
"""
from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime, timezone
from hashlib import sha256

logger = logging.getLogger(__name__)

MAX_RETRIES = 3
_SCHEDULER_ACTOR = "delivery_scheduler_v1"


def _delivery_id_for(report_id: str, version: int, recipient_hash: str) -> str:
    """Deterministic delivery_id; prevents duplicate receipts for the same target."""
    return str(uuid.uuid5(
        uuid.NAMESPACE_URL,
        f"v2:delivery:{report_id}:v{version}:{recipient_hash}",
    ))


def _recipient_hash(email: str) -> str:
    return sha256(email.strip().lower().encode("utf-8")).hexdigest()


def run_pending_deliveries(*, _db=None, _flags=None) -> dict:
    """
    Main entry point called by APScheduler every 30 minutes.

    Steps:
      1. Load all PUBLISHED reports from core_canonical_report_records.
      2. Load all delivery settings.
      3. For each eligible (report, settings, recipient) triple:
         a. prepare_delivery — CAS-safe, returns existing if already created.
         b. Skip if DELIVERED.
         c. Skip if DELIVERY_FAILED and attempt >= MAX_RETRIES.
         d. dispatch_delivery — sends email, records result.
      4. Return summary dict.

    Parameters _db and _flags are for unit tests only (inject MemStore).
    """
    if os.getenv("REPORT_DELIVERY_ENABLED", "").strip().lower() != "true":
        logger.info("canonical_delivery: REPORT_DELIVERY_ENABLED not set, skipping")
        return {"skipped": "REPORT_DELIVERY_ENABLED_NOT_SET"}

    if _db is None:
        from ..database import get_supabase
        _db = get_supabase()

    from .canonical_reports import Reports, CoreStore, Flags, ReportError
    from .report_email_transport import dispatch_delivery

    if _flags is None:
        _flags = Flags(
            CANONICAL_WEEKLY_REVIEW_ENABLED=True,
            CANONICAL_MONTHLY_REVIEW_ENABLED=True,
            REPORT_PUBLISH_ENABLED=True,
            REPORT_DELIVERY_ENABLED=True,
        )

    reports = Reports(CoreStore(_db), flags=_flags)

    summary: dict = {
        "dispatched": 0,
        "already_delivered": 0,
        "skipped_max_retries": 0,
        "skipped_no_settings": 0,
        "failed": 0,
        "errors": [],
    }

    # ── 1. PUBLISHED reports ─────────────────────────────────────────────────
    try:
        rows = (
            _db.table("core_canonical_report_records")
            .select("entity_key,document")
            .like("entity_key", "report:%")
            .contains("document", {"status": "PUBLISHED"})
            .execute()
            .data or []
        )
    except Exception as exc:
        logger.error("canonical_delivery: DB error fetching reports: %s", exc)
        return {"error": str(exc)[:120]}

    published = [r["document"] for r in rows if r.get("document")]
    logger.info("canonical_delivery: %d PUBLISHED report(s) found", len(published))

    if not published:
        return {**summary, "published_count": 0}

    # ── 2. Delivery settings (all clients, one query) ────────────────────────
    try:
        settings_rows = (
            _db.table("core_report_delivery_settings")
            .select("*")
            .execute()
            .data or []
        )
    except Exception as exc:
        logger.error("canonical_delivery: DB error fetching settings: %s", exc)
        return {"error": str(exc)[:120]}

    settings_by_client = {r["client_id"]: r for r in settings_rows}

    # ── 3. Dispatch per (report, recipient) ──────────────────────────────────
    at = datetime.now(timezone.utc).isoformat()

    for doc in published:
        client_id  = doc.get("client_id", "")
        report_id  = doc.get("report_id", "")
        version    = doc.get("version", 1)
        report_type = doc.get("report_type", "")  # "weekly" | "monthly"

        if not client_id or not report_id:
            continue

        settings = settings_by_client.get(client_id)
        if not settings:
            summary["skipped_no_settings"] += 1
            continue

        if report_type == "weekly" and not settings.get("weekly_enabled"):
            continue
        if report_type == "monthly" and not settings.get("monthly_enabled"):
            continue

        active_recipients = [
            r for r in (settings.get("recipients") or [])
            if r.get("active") and r.get("email", "").strip()
        ]
        if not active_recipients:
            summary["skipped_no_settings"] += 1
            continue

        for rec in active_recipients:
            email = rec["email"].strip().lower()
            rh    = _recipient_hash(email)
            did   = _delivery_id_for(report_id, version, rh)

            # a) Prepare receipt (idempotent CAS)
            try:
                receipt = reports.prepare_delivery(
                    did, report_id, version,
                    channel="EMAIL",
                    recipient_hash=rh,
                    actor=_SCHEDULER_ACTOR,
                    at=at,
                )
            except Exception as exc:
                logger.warning(
                    "canonical_delivery: prepare_delivery error report=%s email=%s...: %s",
                    report_id[:8], email[:4], exc,
                )
                summary["errors"].append({"report_id": report_id[:8], "step": "prepare", "error": str(exc)[:80]})
                summary["failed"] += 1
                continue

            # b) Already delivered
            if receipt["status"] == "DELIVERED":
                summary["already_delivered"] += 1
                continue

            # c) Max retries exceeded
            if receipt["status"] == "DELIVERY_FAILED" and receipt.get("attempt", 0) >= MAX_RETRIES:
                logger.warning(
                    "canonical_delivery: max retries (%d) for delivery %s, giving up",
                    MAX_RETRIES, did[:8],
                )
                summary["skipped_max_retries"] += 1
                continue

            # d) Send
            try:
                result = dispatch_delivery(did, email, reports)
            except Exception as exc:
                logger.warning(
                    "canonical_delivery: dispatch error report=%s: %s",
                    report_id[:8], exc,
                )
                summary["errors"].append({"report_id": report_id[:8], "step": "dispatch", "error": str(exc)[:80]})
                summary["failed"] += 1
                continue

            if result["status"] == "DELIVERED":
                logger.info(
                    "canonical_delivery: DELIVERED report=%s v%d to %s...",
                    report_id[:8], version, email[:4],
                )
                summary["dispatched"] += 1
            else:
                logger.warning(
                    "canonical_delivery: DELIVERY_FAILED report=%s v%d: %s",
                    report_id[:8], version, result.get("error"),
                )
                summary["failed"] += 1

    summary["published_count"] = len(published)
    logger.info("canonical_delivery: summary=%s", summary)
    return summary
