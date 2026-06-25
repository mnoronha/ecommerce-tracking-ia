"""
Technical SEO router — Phase 5 AI Presence optimization tools.

  POST /technical/{pixel_id}/schema/audit           — start schema markup audit
  GET  /technical/{pixel_id}/schema/audit/latest    — latest audit + issues
  POST /technical/{pixel_id}/schema/issues/{id}/generate  — generate JSON-LD for issue
  POST /technical/{pixel_id}/schema/issues/{id}/apply     — apply via Shopify API
  GET  /technical/{pixel_id}/llms-txt               — generate preview
  POST /technical/{pixel_id}/llms-txt/apply         — apply to site
  GET  /technical/{pixel_id}/robots                 — validate robots.txt
  GET  /technical/{pixel_id}/core-web-vitals        — PageSpeed check
  GET  /technical/{pixel_id}/pending                — all pending optimizations
  PATCH /technical/{pixel_id}/optimizations/{id}    — update status
  GET  /technical/{pixel_id}/history                — optimization history
  GET  /technical/{pixel_id}/pipeline               — AI Presence pipeline status
  GET  /technical/prompt-templates                  — list vertical templates
  POST /technical/{pixel_id}/prompts/seed           — seed prompts from vertical
  GET  /technical/{pixel_id}/onboarding             — get onboarding state
  PATCH /technical/{pixel_id}/onboarding            — update onboarding state
  GET  /technical/{pixel_id}/time-logs              — list time logs
  POST /technical/{pixel_id}/time-logs              — add time log
"""

import logging
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query
from pydantic import BaseModel

from ..database import get_supabase
from ..services import schema_auditor, llms_txt_generator, robots_validator, core_web_vitals, prompt_templates as pt_svc

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/technical", tags=["technical-seo"])


def _get_client(pixel_id: str) -> dict:
    row = (
        get_supabase()
        .table("clients")
        .select("id,pixel_id,name,shopify_domain")
        .eq("pixel_id", pixel_id)
        .limit(1)
        .execute()
    ).data
    if not row:
        raise HTTPException(status_code=404, detail="client not found")
    return row[0]


# ── Schema Audit ──────────────────────────────────────────────────────────────

@router.post("/{pixel_id}/schema/audit", summary="Inicia auditoria de schema markup")
async def start_audit(pixel_id: str, background_tasks: BackgroundTasks):
    client = _get_client(pixel_id)
    # Check for already running
    sb = get_supabase()
    running = (
        sb.table("schema_markup_audits")
        .select("id")
        .eq("client_id", client["id"])
        .eq("status", "running")
        .limit(1)
        .execute()
    ).data
    if running:
        raise HTTPException(status_code=409, detail="Auditoria já em andamento")
    background_tasks.add_task(schema_auditor.run_audit, client["id"])
    return {"ok": True, "message": "Auditoria iniciada"}


@router.get("/{pixel_id}/schema/audit/latest", summary="Última auditoria + issues")
async def get_latest_audit(
    pixel_id: str,
    status: Optional[str] = Query(None, description="Filtrar issues por status"),
):
    client = _get_client(pixel_id)
    audit  = schema_auditor.get_latest_audit(client["id"])
    if not audit:
        return {"audit": None, "issues": []}
    issues = schema_auditor.get_audit_issues(audit["id"], status=status)
    return {"audit": audit, "issues": issues}


@router.post("/{pixel_id}/schema/issues/{issue_id}/generate", summary="Gera JSON-LD para issue")
async def generate_markup(pixel_id: str, issue_id: str):
    _get_client(pixel_id)
    try:
        return schema_auditor.generate_schema_markup(issue_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/{pixel_id}/schema/issues/{issue_id}/apply", summary="Aplica schema via Shopify API")
async def apply_markup(pixel_id: str, issue_id: str):
    client = _get_client(pixel_id)
    try:
        return schema_auditor.apply_schema_via_shopify(issue_id, client["id"])
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


# ── llms.txt ──────────────────────────────────────────────────────────────────

@router.get("/{pixel_id}/llms-txt", summary="Gera preview do llms.txt")
async def preview_llms_txt(pixel_id: str):
    client = _get_client(pixel_id)
    try:
        return llms_txt_generator.generate_llms_txt(client["id"])
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


class LlmsTxtApplyBody(BaseModel):
    content: str


@router.post("/{pixel_id}/llms-txt/apply", summary="Aplica llms.txt no site")
async def apply_llms_txt(pixel_id: str, body: LlmsTxtApplyBody):
    client = _get_client(pixel_id)
    try:
        return llms_txt_generator.apply_llms_txt(client["id"], body.content)
    except (ValueError, Exception) as e:
        raise HTTPException(status_code=400, detail=str(e))


# ── robots.txt ────────────────────────────────────────────────────────────────

@router.get("/{pixel_id}/robots", summary="Valida robots.txt para AI crawlers")
async def check_robots(pixel_id: str, background_tasks: BackgroundTasks):
    client = _get_client(pixel_id)
    try:
        result = robots_validator.check_and_save(client["id"])
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ── Core Web Vitals ───────────────────────────────────────────────────────────

@router.get("/{pixel_id}/core-web-vitals", summary="Verifica Core Web Vitals via PageSpeed")
async def check_cwv(pixel_id: str):
    client = _get_client(pixel_id)
    try:
        return core_web_vitals.check_client(client["id"])
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ── Pending optimizations ─────────────────────────────────────────────────────

@router.get("/{pixel_id}/pending", summary="Lista otimizações técnicas pendentes")
async def get_pending_optimizations(
    pixel_id: str,
    severity: Optional[str] = Query(None),
    type:     Optional[str] = Query(None),
):
    client = _get_client(pixel_id)
    sb = get_supabase()
    q  = (
        sb.table("technical_optimizations")
        .select("*")
        .eq("client_id", client["id"])
        .neq("status", "dismissed")
    )
    if severity:
        q = q.eq("severity", severity)
    if type:
        q = q.eq("type", type)
    items = (q.order("severity").order("created_at", desc=True).execute()).data or []

    # Also include schema issues as optimizations
    schema_issues = (
        sb.table("schema_markup_issues")
        .select("id,page_url,page_type,schema_type,severity,status,created_at")
        .eq("client_id", client["id"])
        .eq("status", "pending")
        .limit(10)
        .execute()
    ).data or []

    schema_as_opts = [
        {
            "id":               f"schema_{i['id']}",
            "type":             "schema_markup",
            "title":            f"Schema {i['schema_type']} ausente em {i['page_type']}",
            "description":      f"Página: {i['page_url']}",
            "severity":         i["severity"],
            "estimated_impact": "Médio — schema correto melhora visibilidade em IA e SEO",
            "estimated_time":   "15 min",
            "status":           "pending",
            "action_data":      {"issue_id": i["id"], "page_url": i["page_url"]},
            "created_at":       i["created_at"],
        }
        for i in schema_issues
    ]

    # Merchant Center suggestions
    merchant_sugs = (
        sb.table("merchant_optimization_suggestions")
        .select("id,type,severity,reasoning,estimated_impact,status,created_at")
        .eq("client_id", client["id"])
        .eq("status", "pending")
        .limit(10)
        .execute()
    ).data or []

    merchant_as_opts = [
        {
            "id":               f"merchant_{s['id']}",
            "type":             "merchant_feed",
            "title":            f"Otimização de feed: {s['type'].replace('_',' ').title()}",
            "description":      s.get("reasoning", ""),
            "severity":         "medium" if s.get("severity") == "medium_impact" else ("high" if s.get("severity") == "high_impact" else "low"),
            "estimated_impact": s.get("estimated_impact", ""),
            "estimated_time":   "10 min",
            "status":           "pending",
            "action_data":      {"suggestion_id": s["id"]},
            "created_at":       s["created_at"],
        }
        for s in merchant_sugs
    ]

    all_items = items + schema_as_opts + merchant_as_opts
    # Sort by severity order
    _sev_order = {"high": 0, "medium": 1, "low": 2}
    all_items.sort(key=lambda x: _sev_order.get(x.get("severity", "low"), 2))

    return {"items": all_items, "total": len(all_items)}


class OptimizationUpdate(BaseModel):
    status: str  # 'applied' | 'dismissed' | 'in_progress'


@router.patch("/{pixel_id}/optimizations/{opt_id}", summary="Atualiza status de otimização")
async def update_optimization(pixel_id: str, opt_id: str, body: OptimizationUpdate):
    _get_client(pixel_id)
    sb = get_supabase()
    update: dict = {"status": body.status}
    if body.status == "applied":
        update["applied_at"] = "now()"
    sb.table("technical_optimizations").update(update).eq("id", opt_id).execute()
    return {"ok": True}


# ── History ───────────────────────────────────────────────────────────────────

@router.get("/{pixel_id}/history", summary="Histórico de otimizações aplicadas")
async def get_history(pixel_id: str, limit: int = Query(50, le=200)):
    client = _get_client(pixel_id)
    sb = get_supabase()
    rows = (
        sb.table("optimization_history")
        .select("*")
        .eq("client_id", client["id"])
        .order("applied_at", desc=True)
        .limit(limit)
        .execute()
    ).data or []
    return rows


# ── Pipeline status ───────────────────────────────────────────────────────────

@router.get("/{pixel_id}/pipeline", summary="Status do pipeline AI Presence")
async def get_pipeline(pixel_id: str):
    client = _get_client(pixel_id)
    try:
        return pt_svc.get_pipeline_status(client["id"])
    except Exception as exc:
        logger.warning("pipeline status error: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


# ── Prompt templates ──────────────────────────────────────────────────────────

@router.get("/prompt-templates", summary="Lista templates de prompts por vertical")
async def list_templates(
    vertical: Optional[str] = Query(None),
    intent:   Optional[str] = Query(None),
):
    return pt_svc.get_templates(vertical=vertical, intent=intent)


class SeedPromptsBody(BaseModel):
    vertical: str


@router.post("/{pixel_id}/prompts/seed", summary="Seed de prompts a partir de vertical")
async def seed_prompts(pixel_id: str, body: SeedPromptsBody):
    client = _get_client(pixel_id)
    result = pt_svc.seed_prompts_for_client(client["id"], body.vertical)
    # Update onboarding vertical
    pt_svc.upsert_onboarding(client["id"], vertical=body.vertical)
    return result


# ── Onboarding ────────────────────────────────────────────────────────────────

@router.get("/{pixel_id}/onboarding", summary="Estado do onboarding do cliente")
async def get_onboarding(pixel_id: str):
    client = _get_client(pixel_id)
    ob = pt_svc.get_onboarding(client["id"])
    if not ob:
        ob = pt_svc.upsert_onboarding(client["id"])
    return ob


class OnboardingUpdate(BaseModel):
    vertical:         Optional[str]       = None
    steps_completed:  Optional[list[str]] = None
    current_step:     Optional[int]       = None
    notes:            Optional[str]       = None


@router.patch("/{pixel_id}/onboarding", summary="Atualiza estado do onboarding")
async def update_onboarding(pixel_id: str, body: OnboardingUpdate):
    client = _get_client(pixel_id)
    return pt_svc.upsert_onboarding(
        client["id"],
        vertical        = body.vertical,
        steps_completed = body.steps_completed,
        current_step    = body.current_step,
    )


# ── Time logs ─────────────────────────────────────────────────────────────────

@router.get("/{pixel_id}/time-logs", summary="Lista registros de tempo")
async def get_time_logs(pixel_id: str, limit: int = Query(50, le=200)):
    client = _get_client(pixel_id)
    return pt_svc.get_time_logs(client["id"], limit=limit)


class TimeLogBody(BaseModel):
    activity_type:    str
    duration_minutes: int
    description:      Optional[str] = None
    piece_id:         Optional[str] = None


@router.post("/{pixel_id}/time-logs", summary="Registra tempo gasto")
async def add_time_log(pixel_id: str, body: TimeLogBody):
    client = _get_client(pixel_id)
    return pt_svc.add_time_log(
        client["id"],
        activity_type    = body.activity_type,
        duration_minutes = body.duration_minutes,
        description      = body.description,
        piece_id         = body.piece_id,
    )
