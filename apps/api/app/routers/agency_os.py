"""
Agency OS integration — ingest endpoint.

POST /agency/ingest/report-contract
  Auth: Authorization: Bearer {AGENCY_OS_INGEST_KEY}
  Body: {
    report_type?:   str   # defaults to "weekly"
    source_run_id?: str
    generated_at?:  str   # ISO 8601
    contract:       dict  # canonical norolabs-report-contract-v1, stored verbatim
  }

Contract canonical shape (validated, not transformed):
  schema_version = "norolabs-report-contract-v1"
  report.client_slug       → upsert key
  report.business_model    → denorm column
  report.period.start/end  → period_start / period_end (upsert key)
  paid_media.google_ads    → inside contract JSONB
  paid_media.meta_ads      → inside contract JSONB
  ... all other fields stored verbatim in contract JSONB
"""

import logging
from typing import Any, Optional

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from ..config import settings
from ..database import get_supabase

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/agency", tags=["agency-os"])


# ── Auth dependency ────────────────────────────────────────────────────────────

def _require_ingest_key(authorization: str = Header(default="")) -> None:
    expected = settings.AGENCY_OS_INGEST_KEY
    if not expected:
        raise HTTPException(503, detail="AGENCY_OS_INGEST_KEY is not configured on this server")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token or token != expected:
        raise HTTPException(401, detail="Invalid or missing Bearer token")


# ── Payload model ──────────────────────────────────────────────────────────────

class IngestPayload(BaseModel):
    report_type:   str            = "weekly"
    source_run_id: Optional[str]  = None
    generated_at:  Optional[str]  = None
    contract:      dict[str, Any]


# ── Route ──────────────────────────────────────────────────────────────────────

@router.post(
    "/ingest/report-contract",
    summary="Ingest a norolabs-report-contract-v1 from Hermes / Agency OS",
    status_code=200,
)
async def ingest_report_contract(
    payload: IngestPayload,
    authorization: str = Header(default=""),
):
    _require_ingest_key(authorization)

    contract = payload.contract

    # ── Validate schema_version ──────────────────────────────────────────────
    if contract.get("schema_version") != "norolabs-report-contract-v1":
        raise HTTPException(
            400,
            detail="contract.schema_version must be 'norolabs-report-contract-v1'",
        )

    # ── Extract metadata from canonical contract fields ──────────────────────
    report        = contract.get("report") or {}
    client_slug   = report.get("client_slug")
    business_model = report.get("business_model")
    period        = report.get("period") or {}
    period_start  = period.get("start")
    period_end    = period.get("end")

    if not client_slug:
        raise HTTPException(400, detail="contract.report.client_slug is required")
    if not business_model:
        raise HTTPException(400, detail="contract.report.business_model is required")
    if not period_start or not period_end:
        raise HTTPException(400, detail="contract.report.period.start and .end are required")

    # ── Upsert — contract stored verbatim ────────────────────────────────────
    try:
        sb = get_supabase()
        sb.table("agency_report_contracts").upsert(
            {
                "client_slug":    client_slug,
                "report_type":    payload.report_type,
                "business_model": business_model,
                "period_start":   period_start,
                "period_end":     period_end,
                "schema_version": "norolabs-report-contract-v1",
                "source_run_id":  payload.source_run_id,
                "generated_at":   payload.generated_at,
                "contract":       contract,
            },
            on_conflict="client_slug,report_type,period_start,period_end",
        ).execute()
    except Exception as exc:
        logger.exception("agency_os ingest upsert failed for %s", client_slug)
        raise HTTPException(500, detail=f"Supabase upsert failed: {exc}") from exc

    logger.info(
        "agency_os ingest accepted: client=%s report_type=%s period=%s_to_%s",
        client_slug, payload.report_type, period_start, period_end,
    )

    return {
        "status": "accepted",
        "client": client_slug,
        "period": f"{period_start}_to_{period_end}",
    }
