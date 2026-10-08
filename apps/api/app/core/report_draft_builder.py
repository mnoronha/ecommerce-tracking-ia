"""
Report Draft Builder.

Creates a core_report_narratives DRAFT record after a contract is written.
Does NOT generate narrative content — blocks are a minimal placeholder pending Hermes.
Idempotent: returns existing DRAFT/READY_FOR_REVIEW id if one already exists for this contract.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)


def create_narrative_draft(
    sb,
    contract_id: str,
    report_type: str,
    review_status: str,
) -> Optional[str]:
    """
    Create or return the existing active DRAFT for this contract.
    Idempotent: returns the existing id when a DRAFT or READY_FOR_REVIEW already exists.
    Returns narrative_id or None on error.
    """
    try:
        res = (
            sb.table("core_report_narratives")
            .select("id, status")
            .eq("report_contract_id", contract_id)
            .neq("status", "SUPERSEDED")
            .execute()
        )
        existing = res.data or []
    except Exception as exc:
        logger.warning("draft_builder: idempotency check failed contract=%s: %s", contract_id, exc)
        existing = []

    for row in existing:
        if row.get("status") in ("DRAFT", "READY_FOR_REVIEW"):
            logger.info(
                "draft_builder: reusing %s narrative %s (contract=%s)",
                row["status"], row["id"], contract_id,
            )
            return str(row["id"])

    narrative_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    initial_blocks = [
        {
            "section":     "data_quality",
            "statement":   (
                f"Review status: {review_status}. "
                f"Report type: {report_type}. "
                "Narrative pending Hermes analysis — no content generated yet."
            ),
            "metric_refs": {},
        }
    ]

    try:
        sb.table("core_report_narratives").insert({
            "id":                 narrative_id,
            "report_contract_id": contract_id,
            "blocks":             initial_blocks,
            "visibility_scope":   "AGENCY_ONLY",
            "status":             "DRAFT",
            "created_at":         now,
        }).execute()
        logger.info(
            "draft_builder: DRAFT %s created (contract=%s, %s, review_status=%s)",
            narrative_id, contract_id, report_type, review_status,
        )
        return narrative_id
    except Exception as exc:
        logger.error(
            "draft_builder: insert failed contract=%s: %s", contract_id, exc
        )
        return None
