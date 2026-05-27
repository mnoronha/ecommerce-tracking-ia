"""
Insights router — gera e serve análises de IA para o dashboard.
"""

import logging

from fastapi import APIRouter, BackgroundTasks, HTTPException

from ..database import get_supabase
from ..services import ai_analyst, alerts
from ..services.writer import resolve_client_uuid

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/insights", tags=["insights"])


@router.post("/{pixel_id}/generate")
async def generate_insights(pixel_id: str, background_tasks: BackgroundTasks):
    """
    Dispara geração de insights via Claude para o cliente.
    Retorna imediatamente com status 'queued'; processamento roda em background.
    """
    client_uuid = resolve_client_uuid(pixel_id)
    if not client_uuid:
        raise HTTPException(status_code=404, detail=f"Cliente '{pixel_id}' não encontrado")

    background_tasks.add_task(_run_analysis, pixel_id, client_uuid)
    return {"status": "queued", "pixel_id": pixel_id, "client_id": client_uuid}


def _run_analysis(pixel_id: str, client_uuid: str) -> None:
    try:
        result = ai_analyst.generate_insights(client_uuid)
        logger.info("Insights gerados para %s: %d insights", pixel_id, result["insights_generated"])
    except Exception as exc:
        logger.error("Falha ao gerar insights para %s: %s", pixel_id, exc)


@router.get("/{pixel_id}")
async def get_insights(
    pixel_id: str,
    limit: int = 10,
    offset: int = 0,
    type: str | None = None,
    severity: str | None = None,
):
    """
    Retorna os insights do cliente com suporte a paginação e filtros.
    """
    client_uuid = resolve_client_uuid(pixel_id)
    if not client_uuid:
        raise HTTPException(status_code=404, detail=f"Cliente '{pixel_id}' não encontrado")

    try:
        q = (
            get_supabase()
            .table("ai_insights")
            .select("id, type, severity, title, content, data, is_read, created_at")
            .eq("client_id", client_uuid)
            .order("created_at", desc=True)
        )
        if type:
            q = q.eq("type", type)
        if severity:
            q = q.eq("severity", severity)
        result = q.range(offset, offset + limit - 1).execute()
        return {"insights": result.data or [], "client_id": client_uuid, "offset": offset, "limit": limit}
    except Exception as exc:
        logger.error("Erro ao buscar insights: %s", exc)
        raise HTTPException(status_code=500, detail="Erro ao buscar insights")


@router.get("/{pixel_id}/funnel")
async def get_funnel(
    pixel_id: str,
    days: int = 30,
    start: str | None = None,
    end:   str | None = None,
    device: str | None = None,
):
    """
    Funil de conversão com COUNT DISTINCT visitor_id por etapa.
    Evita o limite de linhas do PostgREST carregando tudo no cliente.

    Parâmetros:
      days   — período relativo (ignorado se start+end forem fornecidos)
      start  — ISO date YYYY-MM-DD (início inclusivo)
      end    — ISO date YYYY-MM-DD (fim exclusivo)
      device — 'mobile' | 'desktop' | 'tablet' (omitir = todos)
    """
    from datetime import datetime, timedelta, timezone

    client_uuid = resolve_client_uuid(pixel_id)
    if not client_uuid:
        raise HTTPException(status_code=404, detail=f"Cliente '{pixel_id}' não encontrado")

    now = datetime.now(timezone.utc)
    if start and end:
        p_start = start + "T00:00:00+00:00"
        p_end   = end   + "T23:59:59+00:00"
    else:
        p_start = (now - timedelta(days=days)).isoformat()
        p_end   = now.isoformat()

    try:
        sb = get_supabase()
        funnel_res = sb.rpc("funnel_stats", {
            "p_client_id": client_uuid,
            "p_start":     p_start,
            "p_end":       p_end,
            "p_device":    device,
        }).execute()
        funnel = funnel_res.data or {}

        # Purchase count from orders (not tracking_events)
        order_q = (
            sb.table("orders")
            .select("id", count="exact", head=True)
            .eq("client_id", client_uuid)
            .eq("financial_status", "paid")
            .gt("total_price", 0)
            .gte("created_at", p_start)
            .lte("created_at", p_end)
            .execute()
        )
        purchases = order_q.count or 0

        return {
            "pixel_id":        pixel_id,
            "period_start":    p_start,
            "period_end":      p_end,
            "unique_visitors": funnel.get("unique_visitors", 0),
            "funnel": {
                "pageview":       funnel.get("pageview", 0),
                "view_product":   funnel.get("view_product", 0),
                "add_to_cart":    funnel.get("add_to_cart", 0),
                "begin_checkout": funnel.get("begin_checkout", 0),
                "purchase":       purchases,
            },
        }
    except Exception as exc:
        logger.error("funnel error for %s: %s", pixel_id, exc)
        raise HTTPException(status_code=500, detail="Erro ao calcular funil")


@router.post("/{pixel_id}/test-alert")
async def test_alert(pixel_id: str):
    """Envia alerta de teste para o Slack webhook configurado no cliente."""
    client_uuid = resolve_client_uuid(pixel_id)
    if not client_uuid:
        raise HTTPException(status_code=404, detail=f"Cliente '{pixel_id}' não encontrado")
    try:
        row = (
            get_supabase()
            .table("clients")
            .select("slack_webhook_url")
            .eq("pixel_id", pixel_id)
            .limit(1)
            .execute()
        )
        webhook_url = (row.data or [{}])[0].get("slack_webhook_url")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    if not webhook_url:
        raise HTTPException(status_code=400, detail="slack_webhook_url não configurado para este cliente")

    ok = alerts.send_test_alert(pixel_id, webhook_url)
    return {"status": "ok" if ok else "error", "pixel_id": pixel_id}


@router.patch("/{pixel_id}/{insight_id}/read")
async def mark_as_read(pixel_id: str, insight_id: str):
    """Marca um insight como lido."""
    client_uuid = resolve_client_uuid(pixel_id)
    if not client_uuid:
        raise HTTPException(status_code=404, detail=f"Cliente '{pixel_id}' não encontrado")
    try:
        get_supabase().table("ai_insights").update({"is_read": True}).eq(
            "id", insight_id
        ).eq("client_id", client_uuid).execute()
        return {"status": "ok"}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
