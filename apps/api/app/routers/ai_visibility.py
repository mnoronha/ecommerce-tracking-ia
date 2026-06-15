"""
AI Visibility router.

  POST /ai-visibility/{pixel_id}/import/preview  — valida CSV e retorna preview
  POST /ai-visibility/{pixel_id}/import/confirm  — importa CSV validado
  GET  /ai-visibility/{pixel_id}/summary         — KPIs do dashboard
  GET  /ai-visibility/{pixel_id}/trend           — série temporal por plataforma
  GET  /ai-visibility/{pixel_id}/prompts         — performance por prompt
  GET  /ai-visibility/{pixel_id}/competitors     — share of voice por competidor
  GET  /ai-visibility/{pixel_id}/imports         — histórico de imports
  POST /ai-visibility/imports/{import_id}/revert — reverte um import
  GET  /ai-visibility/{pixel_id}/brands          — marcas cadastradas
  POST /ai-visibility/{pixel_id}/brands          — cadastra/atualiza marca
"""

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, UploadFile, File
from pydantic import BaseModel

from ..database import get_supabase
from ..services import ai_visibility as svc
from ..services.ai_visibility_parser import UbersuggestCSVParser

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/ai-visibility", tags=["ai-visibility"])


# ── helpers ───────────────────────────────────────────────────────────────────

def _get_client(pixel_id: str) -> dict:
    row = (
        get_supabase()
        .table("clients")
        .select("id,pixel_id,name")
        .eq("pixel_id", pixel_id)
        .limit(1)
        .execute()
    ).data
    if not row:
        raise HTTPException(status_code=404, detail="client not found")
    return row[0]


# ── Import ────────────────────────────────────────────────────────────────────

@router.post("/{pixel_id}/import/preview", summary="Valida CSV e retorna preview")
async def import_preview(pixel_id: str, file: UploadFile = File(...)):
    client     = _get_client(pixel_id)
    file_bytes = await file.read()

    parser = UbersuggestCSVParser(client["id"], file_bytes)
    result = parser.validate()

    return {
        "client_id":   client["id"],
        "file_name":   file.filename,
        "file_size":   len(file_bytes),
        "valid":       result.valid,
        "csv_type":    result.csv_type,
        "total_rows":  result.total_rows,
        "period_start": result.period_start,
        "period_end":   result.period_end,
        "platforms":    result.platforms,
        "errors":       result.errors,
        "warnings":     result.warnings,
        "sample_rows":  result.sample_rows,
    }


@router.post("/{pixel_id}/import/confirm", summary="Importa CSV após preview")
async def import_confirm(pixel_id: str, file: UploadFile = File(...)):
    client     = _get_client(pixel_id)
    file_bytes = await file.read()

    parser     = UbersuggestCSVParser(client["id"], file_bytes)
    validation = parser.validate()

    if not validation.valid:
        raise HTTPException(status_code=422, detail={"errors": validation.errors})

    import_record = svc.create_import_record(
        client_id    = client["id"],
        period_start = validation.period_start,
        period_end   = validation.period_end,
        file_name    = file.filename,
        file_size_bytes = len(file_bytes),
    )
    import_id = import_record["id"]

    try:
        result = parser.import_to_db(import_id)
        svc.complete_import(
            import_id      = import_id,
            rows_processed = result.rows_processed,
            rows_skipped   = result.rows_skipped,
            errors         = result.errors,
        )
        # Recalcular resumo mensal para os meses afetados
        if validation.period_start:
            svc.recalc_monthly_summary(client["id"], validation.period_start)
        if validation.period_end and validation.period_end[:7] != (validation.period_start or "")[:7]:
            svc.recalc_monthly_summary(client["id"], validation.period_end)

    except Exception as exc:
        svc.fail_import(import_id, str(exc))
        logger.error("ai_visibility import failed for %s: %s", pixel_id, exc)
        raise HTTPException(status_code=500, detail=str(exc))

    return {
        "import_id":     import_id,
        "rows_processed": result.rows_processed,
        "rows_skipped":  result.rows_skipped,
        "errors_count":  len(result.errors),
        "errors":        result.errors[:10],
        "status":        "imported",
    }


# ── Dashboard ─────────────────────────────────────────────────────────────────

@router.get("/{pixel_id}/summary", summary="KPIs do dashboard de AI Visibility")
async def get_summary(
    pixel_id: str,
    start: str = Query(..., description="YYYY-MM-DD"),
    end:   str = Query(..., description="YYYY-MM-DD"),
    platform: Optional[str] = Query(None),
):
    client = _get_client(pixel_id)
    return svc.get_summary(client["id"], start, end, platform)


@router.get("/{pixel_id}/trend", summary="Série temporal de menção por plataforma")
async def get_trend(
    pixel_id: str,
    start: str = Query(...),
    end:   str = Query(...),
    platform: Optional[str] = Query(None),
):
    client = _get_client(pixel_id)
    return svc.get_mention_trend(client["id"], start, end, platform)


@router.get("/{pixel_id}/prompts", summary="Performance por prompt")
async def get_prompts(
    pixel_id: str,
    start: str = Query(...),
    end:   str = Query(...),
    platform: Optional[str] = Query(None),
):
    client = _get_client(pixel_id)
    return svc.get_prompt_performance(client["id"], start, end, platform)


@router.get("/{pixel_id}/competitors", summary="Share of voice por competidor")
async def get_competitors(
    pixel_id: str,
    start: str = Query(...),
    end:   str = Query(...),
    platform: Optional[str] = Query(None),
):
    client = _get_client(pixel_id)
    return svc.get_competitor_shares(client["id"], start, end, platform)


# ── Import history ────────────────────────────────────────────────────────────

@router.get("/{pixel_id}/imports", summary="Histórico de imports")
async def get_imports(pixel_id: str):
    client = _get_client(pixel_id)
    return svc.get_import_history(client["id"])


@router.post("/imports/{import_id}/revert", summary="Reverte um import")
async def revert_import(import_id: str):
    sb  = get_supabase()
    imp = (
        sb.table("ai_visibility_imports")
        .select("id,status,client_id")
        .eq("id", import_id)
        .limit(1)
        .execute()
    ).data
    if not imp:
        raise HTTPException(status_code=404, detail="import not found")
    if imp[0]["status"] == "reverted":
        raise HTTPException(status_code=409, detail="already reverted")

    result = svc.revert_import(import_id)
    return {"reverted": True, **result}


# ── Brands ────────────────────────────────────────────────────────────────────

@router.get("/{pixel_id}/brands", summary="Marcas cadastradas")
async def get_brands(pixel_id: str):
    client = _get_client(pixel_id)
    return svc.get_brands(client["id"])


class BrandPayload(BaseModel):
    brand_name:          str
    is_own_brand:        bool = False
    competitor_priority: Optional[int] = None
    website_url:         Optional[str] = None
    brand_aliases:       Optional[list[str]] = None


@router.post("/{pixel_id}/brands", summary="Cadastra/atualiza marca")
async def upsert_brand(pixel_id: str, body: BrandPayload):
    client = _get_client(pixel_id)
    return svc.upsert_brand(
        client_id            = client["id"],
        brand_name           = body.brand_name,
        is_own_brand         = body.is_own_brand,
        competitor_priority  = body.competitor_priority,
        website_url          = body.website_url,
        brand_aliases        = body.brand_aliases,
    )
