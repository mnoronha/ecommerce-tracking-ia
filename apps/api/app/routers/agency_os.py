"""
Agency OS integration — ingest endpoint.

POST /agency/ingest/report-contract
  Auth: Authorization: Bearer {AGENCY_OS_INGEST_KEY}
  Body: {
    report_type?:   str   # optional compatibility field; must match contract.report.type
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
import hmac
import json
from datetime import date
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel
from jsonschema import Draft202012Validator, FormatChecker

from ..config import settings
from ..database import get_supabase

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/agency", tags=["agency-os"])
CANONICAL_CLIENTS = frozenset({"lk-sneakers", "dipua", "enutri", "clinica-tarcio-caetano", "spiti-auction", "zipper-galeria"})
CONTRACT_SCHEMA = json.loads((Path(__file__).parents[1] / "schemas/report-contract-v1.schema.json").read_text())
CONTRACT_VALIDATOR = Draft202012Validator(CONTRACT_SCHEMA, format_checker=FormatChecker())


# ── Auth dependency ────────────────────────────────────────────────────────────

def _require_ingest_key(authorization: str = Header(default="")) -> None:
    expected = settings.AGENCY_OS_INGEST_KEY
    if not expected:
        raise HTTPException(503, detail="AGENCY_OS_INGEST_KEY is not configured on this server")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token or not hmac.compare_digest(token, expected):
        raise HTTPException(401, detail="Invalid or missing Bearer token")


# ── Payload model ──────────────────────────────────────────────────────────────

class IngestPayload(BaseModel):
    report_type:   Optional[str]  = None
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

    errors = sorted(CONTRACT_VALIDATOR.iter_errors(contract), key=lambda err: str(list(err.path)))
    if errors:
        raise HTTPException(400, detail={"error": "invalid_report_contract", "paths": [".".join(map(str, err.path)) for err in errors[:20]]})

    # ── Validate schema_version ──────────────────────────────────────────────
    if contract.get("schema_version") != "norolabs-report-contract-v1":
        raise HTTPException(
            400,
            detail="contract.schema_version must be 'norolabs-report-contract-v1'",
        )

    # ── Extract metadata from canonical contract fields ──────────────────────
    report        = contract.get("report") or {}
    report_type   = report["type"]
    client_slug   = report.get("client_slug")
    business_model = report.get("business_model")
    period        = report.get("period") or {}
    period_start  = period.get("start")
    period_end    = period.get("end")
    provenance    = contract["provenance"]
    comparison    = report.get("comparison_period") or {}

    if client_slug not in CANONICAL_CLIENTS:
        raise HTTPException(400, detail="Unknown canonical client")
    if contract["client"]["slug"] != client_slug:
        raise HTTPException(400, detail="Client identity must match the canonical report")
    if date.fromisoformat(period_start) > date.fromisoformat(period_end):
        raise HTTPException(400, detail="Report period start must not exceed end")
    for name, canonical in (("report_type", report_type), ("source_run_id", provenance.get("source_run_id")), ("generated_at", provenance.get("generated_at"))):
        provided = getattr(payload, name)
        if provided is not None and provided != canonical:
            raise HTTPException(400, detail=f"{name} must match the canonical contract")

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
                "report_type":    report_type,
                "business_model": business_model,
                "period_start":   period_start,
                "period_end":     period_end,
                "schema_version": "norolabs-report-contract-v1",
                "comparison_period_start": comparison.get("start"),
                "comparison_period_end": comparison.get("end"),
                "source_run_id":  provenance.get("source_run_id"),
                "generated_at":   provenance.get("generated_at"),
                "contract":       contract,
            },
            on_conflict="client_slug,report_type,period_start,period_end",
        ).execute()
    except Exception as exc:
        logger.exception("agency_os ingest upsert failed for %s", client_slug)
        raise HTTPException(500, detail="Report contract persistence failed") from exc

    logger.info(
        "agency_os ingest accepted: client=%s report_type=%s period=%s_to_%s",
        client_slug, report_type, period_start, period_end,
    )

    return {
        "status": "accepted",
        "client": client_slug,
        "report_type": report_type,
        "source_run_id": provenance.get("source_run_id"),
        "period": f"{period_start}_to_{period_end}",
    }
