"""
Content Production router.

  GET  /content/{pixel_id}/knowledge-base            — lê/cria KB do cliente
  PUT  /content/{pixel_id}/knowledge-base            — atualiza config KB
  GET  /content/{pixel_id}/documents                 — lista documentos
  POST /content/{pixel_id}/documents                 — cria documento (texto ou URL)
  POST /content/{pixel_id}/documents/{doc_id}/index  — (re)indexa documento
  DELETE /content/{pixel_id}/documents/{doc_id}      — desativa documento

  GET  /content/{pixel_id}/briefings                 — lista briefings
  POST /content/{pixel_id}/briefings                 — cria briefing
  GET  /content/{pixel_id}/briefings/{id}            — detalhe do briefing
  PATCH /content/{pixel_id}/briefings/{id}           — atualiza briefing
  POST /content/{pixel_id}/briefings/{id}/generate   — dispara geração

  GET  /content/{pixel_id}/pieces                    — lista peças
  GET  /content/{pixel_id}/pieces/{id}               — detalhe peça + versões
  GET  /content/{pixel_id}/pieces/{id}/versions/{n}  — versão específica
  POST /content/{pixel_id}/pieces/{id}/versions      — salva edição humana
  PATCH /content/{pixel_id}/pieces/{id}              — atualiza status/meta
  GET  /content/{pixel_id}/pieces/{id}/factcheck     — fact-check mais recente

  GET  /content/{pixel_id}/pautas                    — lista pautas mensais
  POST /content/{pixel_id}/pautas                    — cria pauta

  GET  /content/costs                                — resumo de custos de IA
  GET  /content/approve/{token}                      — aprovação pública (sem auth)
  POST /content/approve/{token}                      — responde aprovação
"""

import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query
from pydantic import BaseModel

from ..database import get_supabase
from ..services import content_generator, content_factchecker, rag_indexer

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/content", tags=["content"])


# ── Helpers ───────────────────────────────────────────────────────────────────

def _get_client(pixel_id: str) -> dict:
    row = (
        get_supabase()
        .table("clients")
        .select("id,name,pixel_id")
        .eq("pixel_id", pixel_id)
        .limit(1)
        .execute()
    ).data
    if not row:
        raise HTTPException(status_code=404, detail="client not found")
    return row[0]


def _get_or_create_kb(client_id: str) -> dict:
    sb = get_supabase()
    rows = sb.table("rag_knowledge_bases").select("*").eq("client_id", client_id).limit(1).execute().data
    if rows:
        return rows[0]
    new = sb.table("rag_knowledge_bases").insert({"client_id": client_id}).execute().data[0]
    return new


# ── Knowledge Base ────────────────────────────────────────────────────────────

@router.get("/{pixel_id}/knowledge-base")
async def get_knowledge_base(pixel_id: str):
    c  = _get_client(pixel_id)
    kb = _get_or_create_kb(c["id"])
    doc_count = (
        get_supabase()
        .table("rag_documents")
        .select("id", count="exact")
        .eq("client_id", c["id"])
        .eq("is_active", True)
        .execute()
    ).count or 0
    return {**kb, "doc_count": doc_count}


class KBUpdatePayload(BaseModel):
    brand_voice:                Optional[str]  = None
    brand_dos:                  Optional[list[str]] = None
    brand_donts:                Optional[list[str]] = None
    forbidden_terms:            Optional[list[str]] = None
    preferred_terms:            Optional[dict] = None
    preferred_generation_model: Optional[str]  = None
    preferred_factcheck_model:  Optional[str]  = None
    temperature:                Optional[float] = None


@router.put("/{pixel_id}/knowledge-base")
async def update_knowledge_base(pixel_id: str, body: KBUpdatePayload):
    c  = _get_client(pixel_id)
    kb = _get_or_create_kb(c["id"])
    patch = {k: v for k, v in body.model_dump(exclude_none=True).items()}
    patch["updated_at"] = datetime.now(timezone.utc).isoformat()
    get_supabase().table("rag_knowledge_bases").update(patch).eq("id", kb["id"]).execute()
    return {"ok": True}


# ── Documents ────────────────────────────────────────────────────────────────

@router.get("/{pixel_id}/documents")
async def list_documents(
    pixel_id: str,
    category: Optional[str] = Query(None),
    status:   Optional[str] = Query(None),
    page:     int = Query(1, ge=1),
    per_page: int = Query(30, ge=10, le=100),
):
    c  = _get_client(pixel_id)
    sb = get_supabase()
    q  = (
        sb.table("rag_documents")
        .select("id,title,description,category,source_type,word_count,processing_status,is_active,priority,tags,uploaded_at,processed_at")
        .eq("client_id", c["id"])
        .eq("is_active", True)
    )
    if category:
        q = q.eq("category", category)
    if status:
        q = q.eq("processing_status", status)
    offset = (page - 1) * per_page
    rows = q.order("uploaded_at", desc=True).offset(offset).limit(per_page).execute().data or []
    return {"documents": rows, "page": page, "per_page": per_page}


class DocumentPayload(BaseModel):
    title:       str
    description: Optional[str] = None
    category:    str = "other"
    source_type: str = "manual_entry"
    source_url:  Optional[str] = None
    raw_text:    Optional[str] = None
    priority:    int = 5
    tags:        Optional[list[str]] = None


@router.post("/{pixel_id}/documents")
async def create_document(pixel_id: str, body: DocumentPayload, bg: BackgroundTasks):
    c  = _get_client(pixel_id)
    kb = _get_or_create_kb(c["id"])
    sb = get_supabase()

    word_count = len(body.raw_text.split()) if body.raw_text else 0
    row = (
        sb.table("rag_documents").insert({
            "knowledge_base_id": kb["id"],
            "client_id":         c["id"],
            "title":             body.title,
            "description":       body.description,
            "category":          body.category,
            "source_type":       body.source_type,
            "source_url":        body.source_url,
            "raw_text":          body.raw_text,
            "word_count":        word_count,
            "priority":          body.priority,
            "tags":              body.tags,
            "processing_status": "pending",
        }).execute()
    ).data[0]

    if body.raw_text:
        bg.add_task(rag_indexer.index_document, row["id"])

    return {"document_id": row["id"], "indexing": bool(body.raw_text)}


@router.post("/{pixel_id}/documents/{doc_id}/index")
async def reindex_document(pixel_id: str, doc_id: str, bg: BackgroundTasks):
    _get_client(pixel_id)
    bg.add_task(rag_indexer.index_document, doc_id)
    return {"ok": True, "message": "indexação iniciada em background"}


@router.delete("/{pixel_id}/documents/{doc_id}")
async def deactivate_document(pixel_id: str, doc_id: str):
    _get_client(pixel_id)
    get_supabase().table("rag_documents").update({
        "is_active":   False,
        "updated_at":  datetime.now(timezone.utc).isoformat(),
    }).eq("id", doc_id).execute()
    return {"ok": True}


# ── Briefings ─────────────────────────────────────────────────────────────────

@router.get("/{pixel_id}/briefings")
async def list_briefings(
    pixel_id: str,
    status:       Optional[str] = Query(None),
    content_type: Optional[str] = Query(None),
    pauta_id:     Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    per_page: int = Query(30, ge=5, le=100),
):
    c  = _get_client(pixel_id)
    sb = get_supabase()
    q  = sb.table("content_briefings").select("*").eq("client_id", c["id"])
    if status:       q = q.eq("status", status)
    if content_type: q = q.eq("content_type", content_type)
    if pauta_id:     q = q.eq("pauta_id", pauta_id)
    offset = (page - 1) * per_page
    rows = q.order("created_at", desc=True).offset(offset).limit(per_page).execute().data or []
    return {"briefings": rows, "page": page, "per_page": per_page}


class BriefingPayload(BaseModel):
    working_title:          str
    content_type:           str
    target_query:           Optional[str] = None
    target_keywords:        Optional[list[str]] = None
    target_audience:        Optional[str] = None
    products_to_mention:    Optional[list[str]] = None
    competitors_to_cite:    Optional[list[str]] = None
    required_length:        str = "medium"
    required_structure:     Optional[str] = None
    tone_override:          Optional[str] = None
    additional_instructions: Optional[str] = None
    source:                 str = "manual"
    source_data:            Optional[dict] = None
    priority:               str = "medium"
    due_date:               Optional[str] = None
    pauta_id:               Optional[str] = None


@router.post("/{pixel_id}/briefings")
async def create_briefing(pixel_id: str, body: BriefingPayload):
    c  = _get_client(pixel_id)
    row = (
        get_supabase().table("content_briefings").insert({
            "client_id": c["id"],
            **body.model_dump(exclude_none=True),
        }).execute()
    ).data[0]
    return {"briefing_id": row["id"]}


@router.get("/{pixel_id}/briefings/{briefing_id}")
async def get_briefing(pixel_id: str, briefing_id: str):
    _get_client(pixel_id)
    rows = get_supabase().table("content_briefings").select("*").eq("id", briefing_id).limit(1).execute().data
    if not rows:
        raise HTTPException(404, "briefing not found")
    return rows[0]


@router.patch("/{pixel_id}/briefings/{briefing_id}")
async def update_briefing(pixel_id: str, briefing_id: str, body: dict):
    _get_client(pixel_id)
    body["updated_at"] = datetime.now(timezone.utc).isoformat()
    get_supabase().table("content_briefings").update(body).eq("id", briefing_id).execute()
    return {"ok": True}


@router.post("/{pixel_id}/briefings/{briefing_id}/generate")
async def generate_from_briefing(pixel_id: str, briefing_id: str, bg: BackgroundTasks):
    _get_client(pixel_id)
    bg.add_task(content_generator.generate_piece, briefing_id)
    return {"ok": True, "message": "geração iniciada em background"}


# ── Pieces ────────────────────────────────────────────────────────────────────

@router.get("/{pixel_id}/pieces")
async def list_pieces(
    pixel_id: str,
    status:   Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    per_page: int = Query(30, ge=5, le=100),
):
    c  = _get_client(pixel_id)
    sb = get_supabase()
    q  = (
        sb.table("content_pieces")
        .select("id,final_title,status,current_version,published_at,created_at,briefing_id")
        .eq("client_id", c["id"])
    )
    if status:
        q = q.eq("status", status)
    offset = (page - 1) * per_page
    rows = q.order("created_at", desc=True).offset(offset).limit(per_page).execute().data or []
    return {"pieces": rows, "page": page, "per_page": per_page}


@router.get("/{pixel_id}/pieces/{piece_id}")
async def get_piece(pixel_id: str, piece_id: str):
    _get_client(pixel_id)
    sb = get_supabase()
    piece_rows = sb.table("content_pieces").select("*").eq("id", piece_id).limit(1).execute().data
    if not piece_rows:
        raise HTTPException(404, "piece not found")
    piece = piece_rows[0]
    # Latest version
    version_rows = (
        sb.table("content_piece_versions")
        .select("id,version_number,version_type,title,body_markdown,word_count,generation_model,tokens_input,tokens_output,generation_cost_usd,created_at,rag_chunks_used")
        .eq("piece_id", piece_id)
        .order("version_number", desc=True)
        .limit(1)
        .execute()
    ).data
    # Factcheck
    fc_rows = (
        sb.table("content_factchecks")
        .select("overall_confidence,facts_to_verify,issues_found,recommendation,created_at")
        .eq("piece_id", piece_id)
        .order("created_at", desc=True)
        .limit(1)
        .execute()
    ).data
    return {
        **piece,
        "latest_version": version_rows[0] if version_rows else None,
        "factcheck":      fc_rows[0] if fc_rows else None,
    }


class HumanVersionPayload(BaseModel):
    body_markdown: str
    edit_notes:    Optional[str] = None


@router.post("/{pixel_id}/pieces/{piece_id}/versions")
async def save_human_edit(pixel_id: str, piece_id: str, body: HumanVersionPayload):
    _get_client(pixel_id)
    sb = get_supabase()
    piece_rows = sb.table("content_pieces").select("client_id,current_version").eq("id", piece_id).limit(1).execute().data
    if not piece_rows:
        raise HTTPException(404, "piece not found")
    piece = piece_rows[0]
    new_version = (piece["current_version"] or 1) + 1
    word_count = len(body.body_markdown.split())
    lines = body.body_markdown.strip().splitlines()
    title = lines[0].lstrip("# ").strip() if lines else None

    version_row = (
        sb.table("content_piece_versions").insert({
            "piece_id":       piece_id,
            "client_id":      piece["client_id"],
            "version_number": new_version,
            "version_type":   "human_edit",
            "title":          title,
            "body_markdown":  body.body_markdown,
            "word_count":     word_count,
            "edit_notes":     body.edit_notes,
        }).execute()
    ).data[0]
    now = datetime.now(timezone.utc).isoformat()
    sb.table("content_pieces").update({
        "current_version": new_version,
        "final_title":     title,
        "status":          "reviewed",
        "updated_at":      now,
    }).eq("id", piece_id).execute()
    return {"version_id": version_row["id"], "version_number": new_version}


@router.patch("/{pixel_id}/pieces/{piece_id}")
async def update_piece(pixel_id: str, piece_id: str, body: dict):
    _get_client(pixel_id)
    body["updated_at"] = datetime.now(timezone.utc).isoformat()
    get_supabase().table("content_pieces").update(body).eq("id", piece_id).execute()
    return {"ok": True}


@router.get("/{pixel_id}/pieces/{piece_id}/factcheck")
async def get_factcheck(pixel_id: str, piece_id: str):
    _get_client(pixel_id)
    rows = (
        get_supabase()
        .table("content_factchecks")
        .select("*")
        .eq("piece_id", piece_id)
        .order("created_at", desc=True)
        .limit(1)
        .execute()
    ).data
    if not rows:
        raise HTTPException(404, "no factcheck found")
    return rows[0]


# ── Pautas ───────────────────────────────────────────────────────────────────

@router.get("/{pixel_id}/pautas")
async def list_pautas(pixel_id: str, status: Optional[str] = Query(None)):
    c  = _get_client(pixel_id)
    q  = get_supabase().table("content_pautas").select("*").eq("client_id", c["id"])
    if status:
        q = q.eq("status", status)
    return q.order("month", desc=True).limit(24).execute().data or []


class PautaPayload(BaseModel):
    month:                str  # "2026-07-01"
    total_pieces_planned: Optional[int] = None


@router.post("/{pixel_id}/pautas")
async def create_pauta(pixel_id: str, body: PautaPayload):
    c   = _get_client(pixel_id)
    row = (
        get_supabase().table("content_pautas").insert({
            "client_id":            c["id"],
            "month":                body.month,
            "total_pieces_planned": body.total_pieces_planned,
        }).execute()
    ).data[0]
    return {"pauta_id": row["id"]}


# ── AI Cost Summary ───────────────────────────────────────────────────────────

@router.get("/costs")
async def get_ai_costs(days: int = Query(30, ge=7, le=90)):
    sb   = get_supabase()
    from datetime import date, timedelta
    since = (date.today() - timedelta(days=days)).isoformat()
    rows  = (
        sb.table("ai_usage_log")
        .select("client_id,task,provider,model_id,tokens_input,tokens_output,cost_usd,created_at")
        .gte("created_at", since)
        .order("created_at", desc=True)
        .limit(10000)
        .execute()
    ).data or []

    total_cost = sum(r.get("cost_usd") or 0 for r in rows)
    by_task: dict[str, float] = {}
    by_model: dict[str, float] = {}
    for r in rows:
        t = r.get("task", "unknown")
        m = r.get("model_id", "unknown")
        by_task[t]  = by_task.get(t, 0)  + (r.get("cost_usd") or 0)
        by_model[m] = by_model.get(m, 0) + (r.get("cost_usd") or 0)

    return {
        "total_cost_usd": round(total_cost, 4),
        "total_calls":    len(rows),
        "by_task":        by_task,
        "by_model":       by_model,
        "period_days":    days,
    }


# ── Public Approval ───────────────────────────────────────────────────────────

@router.get("/approve/{token}")
async def get_approval_page(token: str):
    sb   = get_supabase()
    rows = sb.table("content_approvals").select("*").eq("approval_link_token", token).limit(1).execute().data
    if not rows:
        raise HTTPException(404, "link de aprovação inválido ou expirado")
    approval = rows[0]
    if approval["status"] != "pending":
        return {"status": approval["status"], "responded_at": approval["responded_at"]}
    # Busca peça e versão
    version_rows = (
        sb.table("content_piece_versions")
        .select("title,body_markdown,body_html")
        .eq("id", approval["version_id"])
        .limit(1)
        .execute()
    ).data
    version = version_rows[0] if version_rows else {}
    return {
        "status":     "pending",
        "deadline":   approval.get("deadline"),
        "title":      version.get("title"),
        "body_html":  version.get("body_html"),
        "body_markdown": version.get("body_markdown"),
    }


class ApprovalResponse(BaseModel):
    decision: str  # "approved" | "requested_changes"
    feedback: Optional[str] = None


@router.post("/approve/{token}")
async def respond_approval(token: str, body: ApprovalResponse):
    sb   = get_supabase()
    rows = sb.table("content_approvals").select("id,piece_id").eq("approval_link_token", token).eq("status", "pending").limit(1).execute().data
    if not rows:
        raise HTTPException(400, "link inválido, expirado ou já respondido")
    approval = rows[0]
    now = datetime.now(timezone.utc).isoformat()
    sb.table("content_approvals").update({
        "status":       body.decision,
        "responded_at": now,
        "feedback":     body.feedback,
    }).eq("id", approval["id"]).execute()
    if body.decision == "approved":
        sb.table("content_pieces").update({"status": "approved", "updated_at": now}).eq("id", approval["piece_id"]).execute()
    return {"ok": True, "status": body.decision}
