"""
AI Visibility router.

CSV import (legacy — kept for backward compat):
  POST /ai-visibility/{pixel_id}/import/preview  — valida CSV e retorna preview
  POST /ai-visibility/{pixel_id}/import/confirm  — importa CSV validado

DataForSEO automatic collection:
  GET  /ai-visibility/{pixel_id}/config          — get DataForSEO config
  PUT  /ai-visibility/{pixel_id}/config          — create/update DataForSEO config
  POST /ai-visibility/{pixel_id}/collect         — trigger manual collection
  GET  /ai-visibility/costs                      — agency-wide cost dashboard

Dashboard:
  GET  /ai-visibility/{pixel_id}/summary         — KPIs
  GET  /ai-visibility/{pixel_id}/trend           — série temporal
  GET  /ai-visibility/{pixel_id}/prompts         — performance por prompt
  GET  /ai-visibility/{pixel_id}/competitors     — share of voice
  GET  /ai-visibility/{pixel_id}/imports         — histórico (CSV + DataForSEO)
  POST /ai-visibility/imports/{import_id}/revert — reverte um import
  GET  /ai-visibility/{pixel_id}/brands          — marcas cadastradas
  POST /ai-visibility/{pixel_id}/brands          — cadastra/atualiza marca
"""

import logging
from typing import List, Optional

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, UploadFile, File
from pydantic import BaseModel

from ..database import get_supabase
from ..services import ai_visibility as svc, ai_visibility_analyst as analyst_svc, ai_visibility_collector as collector_svc
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
async def import_confirm(pixel_id: str, background_tasks: BackgroundTasks, file: UploadFile = File(...)):
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

        # Trigger cross-data analysis in background
        background_tasks.add_task(
            analyst_svc.run_visibility_analysis,
            client["id"],
            import_id,
        )

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


# ── AI Insights ───────────────────────────────────────────────────────────────

@router.get("/{pixel_id}/insights", summary="Insights IA de AI Visibility")
async def get_insights(pixel_id: str, limit: int = Query(10, le=50)):
    client = _get_client(pixel_id)
    rows = (
        get_supabase()
        .table("ai_insights")
        .select("id, title, content, severity, data, created_at")
        .eq("client_id", client["id"])
        .eq("type", "ai_visibility")
        .order("created_at", desc=True)
        .limit(limit)
        .execute()
    )
    return rows.data or []


@router.post("/{pixel_id}/insights/trigger", summary="Dispara análise manual de AI Visibility")
async def trigger_analysis(pixel_id: str, background_tasks: BackgroundTasks):
    client = _get_client(pixel_id)
    background_tasks.add_task(analyst_svc.run_visibility_analysis, client["id"])
    return {"ok": True, "message": "Análise iniciada em background"}


# ── DataForSEO config ─────────────────────────────────────────────────────────

@router.get("/{pixel_id}/config", summary="Configuração DataForSEO do cliente")
async def get_config(pixel_id: str):
    client = _get_client(pixel_id)
    config = svc.get_dataforseo_config(client["id"])
    return config or {"client_id": client["id"], "is_enabled": False, "configured": False}


class DataForSEOConfigPayload(BaseModel):
    is_enabled:          Optional[bool]       = None
    llms_to_monitor:     Optional[List[str]]  = None
    collection_frequency: Optional[str]       = None  # 'weekly' | 'biweekly' | 'monthly'
    location_code:       Optional[int]        = None
    language_code:       Optional[str]        = None
    budget_monthly_usd:  Optional[float]      = None
    notes:               Optional[str]        = None


@router.put("/{pixel_id}/config", summary="Cria/atualiza configuração DataForSEO")
async def upsert_config(pixel_id: str, body: DataForSEOConfigPayload):
    client = _get_client(pixel_id)
    fields = {k: v for k, v in body.model_dump().items() if v is not None}
    result = svc.upsert_dataforseo_config(client["id"], **fields)
    return result


# ── Manual collection trigger ─────────────────────────────────────────────────

@router.post("/{pixel_id}/collect", summary="Dispara coleta DataForSEO agora")
async def trigger_collection(
    pixel_id: str,
    background_tasks: BackgroundTasks,
    force: bool = Query(False, description="Ignora verificação de budget"),
):
    client = _get_client(pixel_id)
    background_tasks.add_task(collector_svc.collect_for_client, client["id"], force=force)
    return {"ok": True, "message": "Coleta iniciada em background — aguarde ~60s"}


# ── Agency cost dashboard ─────────────────────────────────────────────────────

@router.get("/costs", summary="Resumo de custos DataForSEO (agência)")
async def get_costs(days: int = Query(30, le=90)):
    return svc.get_usage_summary(client_id=None, days=days)
