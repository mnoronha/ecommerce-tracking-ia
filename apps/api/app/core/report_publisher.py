"""
Report Publisher.

Triggered when a core_report_narratives record transitions to PUBLISHED.
Fires email + WhatsApp delivery using the existing reports.py send machinery.

Rules:
- Never fires without an explicit PUBLISHED transition (human approval is a prerequisite).
- Uses send_report_now() with generate_ai=False (existing AI insights from ai_insights table).
- force=True bypasses the negativity gate because the human has already approved.
- No narrative is invented here — delivery uses pre-existing report content.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def publish_narrative_report(
    narrative_id: str,
    contract_id: str,
    approved_by: str,
) -> None:
    """
    Fire delivery for a narrative that just transitioned to PUBLISHED.
    Called as a BackgroundTasks task from the transitions endpoint.
    """
    from ..database import get_supabase
    from ..services import reports as reports_svc

    db = get_supabase()

    try:
        cr = (
            db.table("agency_report_contracts")
            .select("client_slug, report_type, period_start, period_end")
            .eq("id", contract_id)
            .limit(1)
            .execute()
        )
        contract_row = cr.data[0] if (cr and cr.data) else None
    except Exception as exc:
        logger.error("report_publisher: contract load failed %s: %s", contract_id, exc)
        return

    if not contract_row:
        logger.error("report_publisher: contract not found %s", contract_id)
        return

    client_slug = contract_row.get("client_slug")
    report_type = (contract_row.get("report_type") or "weekly").lower()

    try:
        cl = (
            db.table("clients")
            .select("id, client_id")
            .eq("client_id", client_slug)
            .limit(1)
            .execute()
        )
        client_row = cl.data[0] if (cl and cl.data) else None
    except Exception as exc:
        logger.error("report_publisher: client load failed %s: %s", client_slug, exc)
        return

    if not client_row:
        logger.error("report_publisher: client not found %s", client_slug)
        return

    client_id = client_row["id"]
    pixel_id  = client_row["client_id"]

    logger.info(
        "report_publisher: delivering %s report for %s "
        "(narrative=%s, period=%s→%s, approved_by=%s)",
        report_type, pixel_id, narrative_id,
        contract_row.get("period_start"), contract_row.get("period_end"), approved_by,
    )

    try:
        reports_svc.send_report_now(
            client_id=client_id,
            pixel_id=pixel_id,
            to_email="",
            report_type=report_type,
            generate_ai=False,
            force=True,
        )
        logger.info(
            "report_publisher: delivered %s for %s (narrative=%s)",
            report_type, pixel_id, narrative_id,
        )
    except Exception as exc:
        logger.error(
            "report_publisher: delivery failed %s %s: %s", pixel_id, report_type, exc
        )
