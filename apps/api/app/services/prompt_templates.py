"""
Prompt Templates — manages vertical-specific prompt library for AI Visibility.
"""

import logging
from typing import Optional

from ..database import get_supabase

logger = logging.getLogger(__name__)

VERTICALS = ["sneakers", "fashion", "supplements", "electronics", "beauty", "home"]

VERTICAL_LABELS = {
    "sneakers":    "Tênis / Calçados",
    "fashion":     "Moda",
    "supplements": "Suplementos / Nutrição",
    "electronics": "Eletrônicos",
    "beauty":      "Beleza",
    "home":        "Casa / Decoração",
}


def get_templates(vertical: Optional[str] = None, intent: Optional[str] = None) -> list[dict]:
    sb = get_supabase()
    q  = sb.table("ai_presence_prompt_templates").select("*").eq("is_active", True)
    if vertical:
        q = q.eq("vertical", vertical)
    if intent:
        q = q.eq("intent", intent)
    return (q.order("category").execute()).data or []


def get_for_client_vertical(client_id: str) -> list[dict]:
    """Returns templates matching the client's onboarding vertical, or all if not set."""
    sb = get_supabase()
    ob = (
        sb.table("client_onboarding")
        .select("vertical")
        .eq("client_id", client_id)
        .limit(1)
        .execute()
    ).data
    vertical = ob[0]["vertical"] if ob else None
    return get_templates(vertical=vertical)


def seed_prompts_for_client(client_id: str, vertical: str) -> dict:
    """
    Takes templates from the given vertical and seeds ai_visibility_prompts
    for the client if they don't already exist.
    Returns counts of created/existing prompts.
    """
    sb        = get_supabase()
    templates = get_templates(vertical=vertical)
    if not templates:
        return {"created": 0, "existing": 0, "error": f"No templates found for vertical '{vertical}'"}

    # Get existing prompts
    existing_rows = (
        sb.table("ai_visibility_prompts")
        .select("prompt_text")
        .eq("client_id", client_id)
        .execute()
    ).data or []
    existing_texts = {r["prompt_text"].strip().lower() for r in existing_rows}

    to_insert = []
    for t in templates:
        text = t["prompt_text"].strip()
        if text.lower() not in existing_texts:
            to_insert.append({
                "client_id":   client_id,
                "prompt_text": text,
                "category":    t.get("category"),
                "intent":      t.get("intent"),
                "source":      "template",
            })

    if to_insert:
        sb.table("ai_visibility_prompts").insert(to_insert).execute()

    return {"created": len(to_insert), "existing": len(existing_texts), "total": len(templates)}


def get_onboarding(client_id: str) -> Optional[dict]:
    sb = get_supabase()
    rows = (
        sb.table("client_onboarding")
        .select("*")
        .eq("client_id", client_id)
        .limit(1)
        .execute()
    ).data
    return rows[0] if rows else None


def upsert_onboarding(client_id: str, vertical: Optional[str] = None,
                      steps_completed: Optional[list] = None,
                      current_step: Optional[int] = None) -> dict:
    sb   = get_supabase()
    data = {"client_id": client_id, "updated_at": "now()"}
    if vertical is not None:
        data["vertical"] = vertical
    if steps_completed is not None:
        data["steps_completed"] = steps_completed
    if current_step is not None:
        data["current_step"] = current_step

    result = sb.table("client_onboarding").upsert(data, on_conflict="client_id").execute()
    return (result.data or [{}])[0]


def get_time_logs(client_id: str, limit: int = 50) -> list[dict]:
    sb = get_supabase()
    return (
        sb.table("time_logs")
        .select("*")
        .eq("client_id", client_id)
        .order("logged_at", desc=True)
        .limit(limit)
        .execute()
    ).data or []


def add_time_log(client_id: str, activity_type: str, duration_minutes: int,
                 description: Optional[str] = None, piece_id: Optional[str] = None) -> dict:
    sb = get_supabase()
    row = {
        "client_id":       client_id,
        "activity_type":   activity_type,
        "duration_minutes": duration_minutes,
    }
    if description:
        row["description"] = description
    if piece_id:
        row["piece_id"] = piece_id
    result = sb.table("time_logs").insert(row).execute()
    return (result.data or [{}])[0]


def get_pipeline_status(client_id: str) -> dict:
    """
    Returns the AI Presence pipeline status for a client:
    RAG, prompts, schema, merchant, content, reports.
    """
    sb = get_supabase()

    def count(table: str, filters: dict) -> int:
        q = sb.table(table).select("id", count="exact")
        for k, v in filters.items():
            q = q.eq(k, v)
        r = q.execute()
        return r.count or 0

    # RAG
    kb = (
        sb.table("rag_knowledge_bases")
        .select("id")
        .eq("client_id", client_id)
        .limit(1)
        .execute()
    ).data
    rag_kb_id   = kb[0]["id"] if kb else None
    rag_docs    = count("rag_documents", {"knowledge_base_id": rag_kb_id, "processing_status": "indexed"}) if rag_kb_id else 0

    # AI Visibility prompts
    prompts = count("ai_visibility_prompts", {"client_id": client_id})
    imports = count("ai_visibility_imports",  {"client_id": client_id, "status": "imported"})

    # Schema audit
    audit = (
        sb.table("schema_markup_audits")
        .select("schema_health_score,issues_found")
        .eq("client_id", client_id)
        .order("created_at", desc=True)
        .limit(1)
        .execute()
    ).data
    schema_score = audit[0]["schema_health_score"] if audit else None
    schema_issues = audit[0]["issues_found"] if audit else None

    # Merchant Center
    merchant_snap = (
        sb.table("merchant_feed_health_snapshots")
        .select("feed_health_score")
        .eq("client_id", client_id)
        .order("snapshot_date", desc=True)
        .limit(1)
        .execute()
    ).data
    merchant_score = merchant_snap[0]["feed_health_score"] if merchant_snap else None

    # Content
    pieces_published = count("content_pieces", {"client_id": client_id, "status": "published"})
    pieces_in_progress = count("content_pieces", {"client_id": client_id, "status": "generated"})

    # Reports (check ai_insights for weekly_report type as proxy)
    last_report = (
        sb.table("ai_insights")
        .select("created_at")
        .eq("client_id", client_id)
        .eq("type", "weekly_report")
        .order("created_at", desc=True)
        .limit(1)
        .execute()
    ).data or []

    # Onboarding
    onboarding = get_onboarding(client_id)

    return {
        "rag": {
            "configured": rag_kb_id is not None,
            "documents":  rag_docs,
            "status":     "ok" if rag_docs > 0 else ("pending" if rag_kb_id else "missing"),
        },
        "prompts": {
            "configured": prompts > 0,
            "count":      prompts,
            "imports":    imports,
            "status":     "ok" if (prompts > 0 and imports > 0) else ("partial" if prompts > 0 else "pending"),
        },
        "schema": {
            "audited":    schema_score is not None,
            "score":      schema_score,
            "issues":     schema_issues,
            "status":     "ok" if (schema_score or 0) >= 80 else ("warning" if schema_score is not None else "pending"),
        },
        "merchant": {
            "configured": merchant_score is not None,
            "score":      merchant_score,
            "status":     "ok" if (merchant_score or 0) >= 70 else ("warning" if merchant_score is not None else "pending"),
        },
        "content": {
            "published":    pieces_published,
            "in_progress":  pieces_in_progress,
            "status":       "ok" if pieces_published > 0 else "pending",
        },
        "onboarding": {
            "completed": onboarding.get("completed_at") is not None if onboarding else False,
            "step":      onboarding.get("current_step", 1) if onboarding else 1,
            "vertical":  onboarding.get("vertical") if onboarding else None,
        },
    }
