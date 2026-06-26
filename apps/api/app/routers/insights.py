"""
Insights router — gera e serve análises de IA para o dashboard.
"""

import logging

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel

from ..database import get_supabase
from ..services import ai_analyst, alerts, reports, spend_sync
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
    include_read: bool = False,
):
    """
    Retorna os insights do cliente. Por padrão só retorna não-lidos (include_read=false).
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
        if not include_read:
            q = q.eq("is_read", False)
        if type:
            q = q.eq("type", type)
        if severity:
            q = q.eq("severity", severity)
        result = q.range(offset, offset + limit - 1).execute()
        return {"insights": result.data or [], "client_id": client_uuid, "offset": offset, "limit": limit}
    except Exception as exc:
        logger.error("Erro ao buscar insights: %s", exc)
        raise HTTPException(status_code=500, detail="Erro ao buscar insights")


@router.get("/{pixel_id}/report-history", summary="Histórico de relatórios enviados (versionamento)")
async def report_history(pixel_id: str, limit: int = 20):
    client_uuid = resolve_client_uuid(pixel_id)
    if not client_uuid:
        raise HTTPException(status_code=404, detail="cliente não encontrado")
    rows = (
        get_supabase().table("reports")
        .select("id, type, period_start, period_end, status, sent_to, sent_at, ai_insight_id")
        .eq("client_id", client_uuid)
        .order("sent_at", desc=True)
        .limit(limit)
        .execute()
    ).data or []
    return {"reports": rows}


@router.get("/{pixel_id}/report-history/{report_id}", summary="Conteúdo de um relatório versionado")
async def report_detail(pixel_id: str, report_id: str):
    client_uuid = resolve_client_uuid(pixel_id)
    if not client_uuid:
        raise HTTPException(status_code=404, detail="cliente não encontrado")
    r = (
        get_supabase().table("reports")
        .select("id, type, period_start, period_end, status, sent_to, sent_at, html_content, ai_summary")
        .eq("id", report_id).eq("client_id", client_uuid).limit(1).execute()
    ).data
    if not r:
        raise HTTPException(status_code=404, detail="relatório não encontrado")
    return r[0]


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


class ReportRequest(BaseModel):
    email: str | None = None          # override alert_email if provided
    report_type: str = "weekly"       # "weekly" | "monthly"
    force: bool = False               # monthly only — bypass the negativity gate


@router.post("/{pixel_id}/report", summary="Gera insights + envia relatório por email")
async def send_report(pixel_id: str, body: ReportRequest | None = None, background_tasks: BackgroundTasks = None):
    """
    Generates fresh Claude insights (if none recent) and sends the requested
    report email. Uses the client's alert_email unless overridden in the body.

    report_type: "weekly" (objective digest) or "monthly" (complete report).
    For monthly, a clearly negative month is held for agency review unless
    force=True; in that case the response reports held=True.
    """
    client_uuid = resolve_client_uuid(pixel_id)
    if not client_uuid:
        raise HTTPException(status_code=404, detail=f"Cliente '{pixel_id}' não encontrado")

    report_type = (body.report_type if body else "weekly") or "weekly"
    if report_type not in ("weekly", "monthly"):
        raise HTTPException(status_code=400, detail="report_type deve ser 'weekly' ou 'monthly'")
    force = bool(body.force) if body else False

    # Resolve target email
    to_email = (body.email if body else None)
    if not to_email:
        row = get_supabase().table("clients").select("alert_email").eq("id", client_uuid).limit(1).execute()
        if row.data:
            to_email = row.data[0].get("alert_email")

    if not to_email:
        raise HTTPException(
            status_code=422,
            detail="Nenhum email configurado. Adicione alert_email no cliente ou passe email no body.",
        )

    email_snapshot = to_email

    def _run():
        try:
            res = reports.send_report_now(
                client_uuid, pixel_id, email_snapshot,
                report_type=report_type, generate_ai=True, force=force,
            )
            logger.info("%s report for %s → %s (held=%s)", report_type, pixel_id, res.get("sent_to"), res.get("held"))
        except Exception as exc:
            logger.error("report failed for %s: %s", pixel_id, exc)

    if background_tasks:
        background_tasks.add_task(_run)
        return {"status": "queued", "email": email_snapshot, "report_type": report_type, "pixel_id": pixel_id}

    res = reports.send_report_now(
        client_uuid, pixel_id, email_snapshot,
        report_type=report_type, generate_ai=True, force=force,
    )
    return {"status": "sent", "report_type": report_type, "pixel_id": pixel_id, **res}


class SpendBackfillRequest(BaseModel):
    start_date: str   # YYYY-MM-DD
    end_date:   str   # YYYY-MM-DD


@router.post("/{pixel_id}/spend-backfill", summary="Importa histórico de investimento Meta + Google")
async def spend_backfill(pixel_id: str, body: SpendBackfillRequest, background_tasks: BackgroundTasks):
    """
    Busca o investimento diário real nas APIs da Meta e Google para cada dia do período
    e upserta na tabela ad_spend. Útil para preencher dados históricos faltantes.

    Executa em background — pode levar alguns minutos para períodos longos.
    """
    from datetime import date as date_type
    client_uuid = resolve_client_uuid(pixel_id)
    if not client_uuid:
        raise HTTPException(status_code=404, detail=f"Cliente '{pixel_id}' não encontrado")

    try:
        start = date_type.fromisoformat(body.start_date)
        end   = date_type.fromisoformat(body.end_date)
    except ValueError:
        raise HTTPException(status_code=400, detail="Datas inválidas — use YYYY-MM-DD")

    if (end - start).days > 365:
        raise HTTPException(status_code=400, detail="Período máximo: 365 dias")
    if end < start:
        raise HTTPException(status_code=400, detail="end_date deve ser >= start_date")

    background_tasks.add_task(spend_sync.backfill_spend_range, client_uuid, pixel_id, start, end)
    return {
        "status":  "queued",
        "pixel_id": pixel_id,
        "start":   body.start_date,
        "end":     body.end_date,
        "days":    (end - start).days + 1,
        "message": "Backfill iniciado em background. Acompanhe nos logs do Railway.",
    }


@router.get("/{pixel_id}/spend-test")
async def spend_api_test(pixel_id: str):
    """
    Diagnóstico síncrono: chama as APIs de TikTok e Pinterest e retorna resposta bruta.
    Útil para debugar falhas de permissão/token no spend sync.
    """
    from datetime import date as date_type, timedelta
    from ..services import crypto, spend_sync as ss
    import httpx

    client_uuid = resolve_client_uuid(pixel_id)
    if not client_uuid:
        raise HTTPException(status_code=404, detail="cliente não encontrado")

    sb = get_supabase()
    row = (
        sb.table("clients")
        .select(
            "id, pixel_id, "
            "tiktok_advertiser_id, tiktok_access_token, "
            "pinterest_ad_account_id, pinterest_access_token"
        )
        .eq("id", client_uuid)
        .limit(1)
        .execute()
    ).data
    if not row:
        raise HTTPException(status_code=404, detail="cliente não encontrado")

    c = crypto.decrypt_client_secrets(row[0])
    yesterday = (date_type.today() - timedelta(days=1)).isoformat()
    result: dict = {}

    if c.get("tiktok_advertiser_id") and c.get("tiktok_access_token"):
        try:
            resp = httpx.get(
                ss._TIKTOK_REPORT,
                params={
                    "advertiser_id": c["tiktok_advertiser_id"],
                    "report_type":   "BASIC",
                    "dimensions":    '["stat_time_day"]',
                    "metrics":       '["spend","impressions","clicks"]',
                    "data_level":    "ACCOUNT",
                    "start_date":    yesterday,
                    "end_date":      yesterday,
                    "page_size":     1,
                },
                headers={"Access-Token": c["tiktok_access_token"]},
                timeout=20.0,
            )
            result["tiktok"] = {"status_code": resp.status_code, "body": resp.json()}
        except Exception as exc:
            result["tiktok"] = {"error": str(exc)}
    else:
        result["tiktok"] = {"error": "credentials not set"}

    if c.get("pinterest_ad_account_id") and c.get("pinterest_access_token"):
        try:
            resp = httpx.get(
                ss._PINTEREST_ANALYTICS.format(ad_account_id=c["pinterest_ad_account_id"]),
                params={
                    "start_date":  yesterday,
                    "end_date":    yesterday,
                    "columns":     "SPEND_IN_DOLLAR,IMPRESSION_1,OUTBOUND_CLICK_1",
                    "granularity": "DAY",
                },
                headers={"Authorization": f"Bearer {c['pinterest_access_token']}"},
                timeout=15.0,
            )
            result["pinterest"] = {"status_code": resp.status_code, "body": resp.json()}
        except Exception as exc:
            result["pinterest"] = {"error": str(exc)}
    else:
        result["pinterest"] = {"error": "credentials not set"}

    return result


@router.get("/{pixel_id}/campaign-products")
async def get_campaign_products(
    pixel_id: str,
    start: str,
    end: str,
):
    """
    O que cada campanha/influencer vendeu — produtos agrupados por utm_campaign.

    Usa order_items (dados server-side, mais confiáveis) quando disponível.
    Fallback para tracking_events purchase events quando não há order_items.

    Parâmetros:
      start — YYYY-MM-DD
      end   — YYYY-MM-DD
    """
    client_uuid = resolve_client_uuid(pixel_id)
    if not client_uuid:
        raise HTTPException(status_code=404, detail=f"Cliente '{pixel_id}' não encontrado")

    sb = get_supabase()
    p_start = start + "T00:00:00+00:00"
    p_end   = end   + "T23:59:59+00:00"

    # ── Tentativa 1: orders → order_items (server-side, mais confiável) ───────
    orders_res = (
        sb.table("orders")
        .select("id, utm_campaign, utm_source, utm_medium, order_items(name, quantity, unit_price, line_total)")
        .eq("client_id", client_uuid)
        .eq("financial_status", "paid")
        .gt("total_price", 0)
        .gte("created_at", p_start)
        .lte("created_at", p_end)
        .execute()
    )
    orders = orders_res.data or []
    has_items = any(o.get("order_items") for o in orders)

    if has_items:
        campaign_map: dict[str, dict] = {}
        for order in orders:
            campaign = order.get("utm_campaign") or "(sem campanha)"
            if campaign not in campaign_map:
                campaign_map[campaign] = {
                    "campaign": campaign,
                    "source":   order.get("utm_source"),
                    "medium":   order.get("utm_medium"),
                    "orders":   0,
                    "revenue":  0.0,
                    "products": {},
                }
            c = campaign_map[campaign]
            c["orders"] += 1
            for item in (order.get("order_items") or []):
                name = item.get("name") or "Produto sem nome"
                qty  = int(item.get("quantity") or 1)
                rev  = float(item.get("line_total") or 0)
                c["revenue"] += rev
                if name not in c["products"]:
                    c["products"][name] = {"name": name, "qty": 0, "revenue": 0.0}
                c["products"][name]["qty"]     += qty
                c["products"][name]["revenue"] += rev

        rows = []
        for c in sorted(campaign_map.values(), key=lambda x: x["revenue"], reverse=True):
            top_products = sorted(c["products"].values(), key=lambda p: p["revenue"], reverse=True)[:15]
            rows.append({
                "campaign":    c["campaign"],
                "source":      c["source"],
                "medium":      c["medium"],
                "orders":      c["orders"],
                "revenue":     round(c["revenue"], 2),
                "products":    [{"name": p["name"], "qty": p["qty"], "revenue": round(p["revenue"], 2)} for p in top_products],
                "data_source": "order_items",
            })
        return {"campaigns": rows, "data_source": "order_items", "total_campaigns": len(rows)}

    # ── Fallback: tracking_events purchase (pixel + CAPI) ────────────────────
    events_res = (
        sb.table("tracking_events")
        .select("utm_campaign, utm_source, utm_medium, product_name, product_quantity, product_price")
        .eq("client_id", client_uuid)
        .eq("event_type", "purchase")
        .not_("product_name", "is", None)
        .gte("created_at", p_start)
        .lte("created_at", p_end)
        .limit(5000)
        .execute()
    )
    events = events_res.data or []

    campaign_map2: dict[str, dict] = {}
    for ev in events:
        campaign = ev.get("utm_campaign") or "(sem campanha)"
        if campaign not in campaign_map2:
            campaign_map2[campaign] = {
                "campaign": campaign,
                "source":   ev.get("utm_source"),
                "medium":   ev.get("utm_medium"),
                "orders":   0,
                "revenue":  0.0,
                "products": {},
            }
        c = campaign_map2[campaign]
        c["orders"] += 1
        name = ev.get("product_name") or "Produto sem nome"
        qty  = int(ev.get("product_quantity") or 1)
        rev  = float(ev.get("product_price") or 0) * qty
        c["revenue"] += rev
        if name not in c["products"]:
            c["products"][name] = {"name": name, "qty": 0, "revenue": 0.0}
        c["products"][name]["qty"]     += qty
        c["products"][name]["revenue"] += rev

    rows2 = []
    for c in sorted(campaign_map2.values(), key=lambda x: x["revenue"], reverse=True):
        top_products = sorted(c["products"].values(), key=lambda p: p["revenue"], reverse=True)[:15]
        rows2.append({
            "campaign":    c["campaign"],
            "source":      c["source"],
            "medium":      c["medium"],
            "orders":      c["orders"],
            "revenue":     round(c["revenue"], 2),
            "products":    [{"name": p["name"], "qty": p["qty"], "revenue": round(p["revenue"], 2)} for p in top_products],
            "data_source": "tracking_events",
        })
    return {"campaigns": rows2, "data_source": "tracking_events", "total_campaigns": len(rows2)}


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
