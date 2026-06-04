"""
Pixel diagnostics — comprehensive health snapshot for a given pixel_id.

Returns event volumes, identifier coverage, and per-channel CAPI status so
the dashboard can surface exactly where a client's data pipeline stands.
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from ..database import get_supabase
from ..services import health_monitor, notify as notify_svc, whatsapp as wa_svc

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get(
    "/diagnostics/{pixel_id}",
    summary="Full data-pipeline health snapshot",
    tags=["diagnostics"],
)
async def get_diagnostics(pixel_id: str):
    sb = get_supabase()

    client_row = (
        sb.table("clients")
        .select("id, name, pixel_id, meta_pixel_id, ga4_measurement_id, google_ads_customer_id, tiktok_pixel_id, is_active")
        .eq("pixel_id", pixel_id)
        .limit(1)
        .execute()
    )
    if not (client_row and client_row.data):
        raise HTTPException(status_code=404, detail="Client not found")
    c      = client_row.data[0]
    cid    = c["id"]
    now    = datetime.now(timezone.utc)
    t24h   = (now - timedelta(hours=24)).isoformat()
    t7d    = (now - timedelta(days=7)).isoformat()
    t30d   = (now - timedelta(days=30)).isoformat()

    # ── Tracking events ───────────────────────────────────────────────────────
    ev_last = (
        sb.table("tracking_events")
        .select("created_at")
        .eq("client_id", cid)
        .order("created_at", desc=True)
        .limit(1)
        .execute()
    )
    last_event_at = (ev_last.data[0]["created_at"] if ev_last.data else None)

    ev_24h = (
        sb.table("tracking_events")
        .select("id", count="exact", head=True)
        .eq("client_id", cid)
        .gte("created_at", t24h)
        .execute()
    )
    ev_7d = (
        sb.table("tracking_events")
        .select("id", count="exact", head=True)
        .eq("client_id", cid)
        .gte("created_at", t7d)
        .execute()
    )

    # ── Orders (paid, last 30d) ───────────────────────────────────────────────
    orders_q = (
        sb.table("orders")
        .select(
            "id, created_at, capi_sent, capi_last_error, "
            "google_sent, google_last_error, "
            "tiktok_sent, tiktok_last_error, "
            "visitor_id"
        )
        .eq("client_id", cid)
        .eq("financial_status", "paid")
        .gt("total_price", 0)
        .gte("created_at", t30d)
        .execute()
    )
    orders = orders_q.data or []
    total_orders = len(orders)

    # CAPI coverage
    meta_sent    = sum(1 for o in orders if o.get("capi_sent"))
    meta_errors  = [o["capi_last_error"] for o in orders if o.get("capi_last_error") and not o.get("capi_sent")]

    google_sent   = sum(1 for o in orders if o.get("google_sent"))
    google_errors = [o["google_last_error"] for o in orders if o.get("google_last_error") and not o.get("google_sent")]

    tiktok_sent   = sum(1 for o in orders if o.get("tiktok_sent"))
    tiktok_errors = [o["tiktok_last_error"] for o in orders if o.get("tiktok_last_error") and not o.get("tiktok_sent")]

    # Last error per channel
    last_meta_err   = meta_errors[-1][:300]   if meta_errors   else None
    last_google_err = google_errors[-1][:300] if google_errors else None
    last_tiktok_err = tiktok_errors[-1][:300] if tiktok_errors else None

    # Orders with visitor_id (linkage rate)
    linked_orders = sum(1 for o in orders if o.get("visitor_id"))

    # ── Identifier coverage (visitors, last 30d) ──────────────────────────────
    vis_total_q = (
        sb.table("visitors")
        .select("id", count="exact", head=True)
        .eq("client_id", cid)
        .gte("first_seen_at", t30d)
        .execute()
    )
    vis_total = vis_total_q.count or 0

    fbp_q = (
        sb.table("visitors")
        .select("id", count="exact", head=True)
        .eq("client_id", cid)
        .gte("first_seen_at", t30d)
        .not_.is_("fbp", "null")
        .execute()
    )
    fbc_q = (
        sb.table("visitors")
        .select("id", count="exact", head=True)
        .eq("client_id", cid)
        .gte("first_seen_at", t30d)
        .not_.is_("fbc", "null")
        .execute()
    )
    gclid_q = (
        sb.table("visitors")
        .select("id", count="exact", head=True)
        .eq("client_id", cid)
        .gte("first_seen_at", t30d)
        .not_.is_("gclid", "null")
        .execute()
    )
    ttclid_q = (
        sb.table("visitors")
        .select("id", count="exact", head=True)
        .eq("client_id", cid)
        .gte("first_seen_at", t30d)
        .not_.is_("ttclid", "null")
        .execute()
    )

    ev_total_30d_q = (
        sb.table("tracking_events")
        .select("id", count="exact", head=True)
        .eq("client_id", cid)
        .gte("created_at", t30d)
        .execute()
    )
    ev_total_30d = ev_total_30d_q.count or 0

    fbp_count   = fbp_q.count   or 0
    fbc_count   = fbc_q.count   or 0
    gclid_count = gclid_q.count or 0
    ttclid_count= ttclid_q.count or 0

    def pct(n: int, total: int) -> float | None:
        return round(n / total * 100, 1) if total > 0 else None

    # ── Open alerts ───────────────────────────────────────────────────────────
    alerts_q = (
        sb.table("alerts")
        .select("severity")
        .eq("client_id", cid)
        .is_("resolved_at", "null")
        .execute()
    )
    alert_rows = alerts_q.data or []
    alert_critical = sum(1 for a in alert_rows if a.get("severity") == "critical")
    alert_warning  = sum(1 for a in alert_rows if a.get("severity") == "warning")

    return {
        "pixel_id":       pixel_id,
        "client_name":    c.get("name"),
        "is_active":      c.get("is_active"),
        "now":            now.isoformat(),
        # ── Tracking events ────────────────────────────────────────────────
        "last_event_at":  last_event_at,
        "events_24h":     ev_24h.count  or 0,
        "events_7d":      ev_7d.count   or 0,
        "events_30d":     ev_total_30d,
        # ── Identifier coverage (last 30d events) ─────────────────────────
        "identifiers": {
            "visitors_30d":     vis_total,
            "fbp_count":        fbp_count,
            "fbp_pct":          pct(fbp_count,    vis_total),
            "fbc_count":        fbc_count,
            "fbc_pct":          pct(fbc_count,    vis_total),
            "gclid_visitors":   gclid_count,
            "gclid_pct":        pct(gclid_count,  vis_total),
            "ttclid_visitors":  ttclid_count,
            "ttclid_pct":       pct(ttclid_count, vis_total),
        },
        # ── Orders (paid, last 30d) ────────────────────────────────────────
        "orders_30d":          total_orders,
        "orders_visitor_linked": linked_orders,
        "orders_linked_pct":   pct(linked_orders, total_orders),
        # ── CAPI status ────────────────────────────────────────────────────
        "capi": {
            "meta": {
                "configured":   bool(c.get("meta_pixel_id")),
                "sent":         meta_sent,
                "sent_pct":     pct(meta_sent,   total_orders),
                "errors":       len(meta_errors),
                "last_error":   last_meta_err,
            },
            "google": {
                "configured":   bool(c.get("google_ads_customer_id")),
                "sent":         google_sent,
                "sent_pct":     pct(google_sent,  total_orders),
                "errors":       len(google_errors),
                "last_error":   last_google_err,
            },
            "tiktok": {
                "configured":   bool(c.get("tiktok_pixel_id")),
                "sent":         tiktok_sent,
                "sent_pct":     pct(tiktok_sent,  total_orders),
                "errors":       len(tiktok_errors),
                "last_error":   last_tiktok_err,
            },
        },
        # ── Open alerts ────────────────────────────────────────────────────
        "open_alerts": {
            "critical": alert_critical,
            "warning":  alert_warning,
            "total":    len(alert_rows),
        },
    }


@router.post(
    "/health-monitor/run",
    summary="Dispara o monitor de saúde manualmente",
    tags=["diagnostics"],
)
async def run_health_monitor(
    date: Optional[str] = Query(
        default=None,
        description="Data alvo YYYY-MM-DD (default: ontem UTC)",
    )
):
    """
    Executa o health_monitor para todos os clientes ativos e retorna o resultado.
    Envia o email de relatório para AGENCY_NOTIFY_EMAIL.
    Aceita ?date=2026-05-31 para re-verificar um dia específico.
    """
    try:
        results = health_monitor.run_daily_health_check(target_date=date)
    except Exception as exc:
        logger.error("health-monitor/run: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))

    summary = [
        {
            "pixel_id":  r["pixel_id"],
            "name":      r["name"],
            "healthy":   r["healthy"],
            "findings":  r["findings"],
            "snippet":   {
                "total":     r["snippet"]["total"],
                "daily_avg": r["snippet"]["daily_avg"],
                "drop_pct":  r["snippet"]["drop_pct"],
            },
            "visitors": {
                "total":     r["visitors"]["total"],
                "pct_fbp":   r["visitors"]["pct_fbp"],
                "pct_gclid": r["visitors"]["pct_gclid"],
            },
            "orders": {
                "paid_online":       r["orders"]["paid_online"],
                "paid_pos":          r["orders"]["paid_pos"],
                "meta_dispatched":   r["orders"]["meta_dispatched"],
                "google_dispatched": r["orders"]["google_dispatched"],
                "meta_missing":      r["orders"]["meta_missing"],
                "google_missing":    r["orders"]["google_missing"],
                "pct_enriched":      r["orders"]["pct_enriched"],
                "gm_counts":         r["orders"]["gm_counts"],
            },
        }
        for r in results
    ]

    all_healthy = all(r["healthy"] for r in results)
    return {
        "status":    "ok" if all_healthy else "issues_found",
        "clients":   len(results),
        "n_issues":  sum(len(r["findings"]) for r in results),
        "results":   summary,
    }


# ── Notifications ─────────────────────────────────────────────────────────────

@router.get(
    "/notifications/status",
    summary="Status dos canais de notificação (email + WhatsApp)",
    tags=["diagnostics"],
)
async def notifications_status():
    """
    Verifica quais canais de notificação estão configurados e operacionais.
    """
    from ..config import settings

    email_configured = bool(settings.RESEND_API_KEY or settings.SMTP_HOST)
    wa_configured    = bool(settings.EVOLUTION_API_URL and settings.EVOLUTION_API_KEY and settings.EVOLUTION_INSTANCE)

    wa_status = wa_svc.check_instance_status() if wa_configured else {"ok": False, "error": "não configurado"}

    return {
        "email": {
            "configured": email_configured,
            "provider":   "resend" if settings.RESEND_API_KEY else ("smtp" if settings.SMTP_HOST else "none"),
            "from":       settings.RESEND_FROM or settings.SMTP_FROM or settings.SMTP_USER or None,
            "agency_email": settings.AGENCY_NOTIFY_EMAIL or None,
        },
        "whatsapp": {
            "configured":   wa_configured,
            "instance":     settings.EVOLUTION_INSTANCE or None,
            "agency_phone": settings.AGENCY_WHATSAPP or None,
            "min_severity": settings.EVOLUTION_MIN_SEVERITY or "critical",
            "connected":    wa_status.get("ok", False),
            "state":        wa_status.get("state"),
            "error":        wa_status.get("error"),
        },
    }


@router.post(
    "/notifications/whatsapp/resolve-invite",
    summary="Resolve link de convite WhatsApp para JID do grupo",
    tags=["diagnostics"],
)
async def resolve_whatsapp_invite(invite: str = Query(..., description="Link ou código do convite")):
    result = wa_svc.resolve_group_invite(invite)
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error", "Não foi possível resolver o convite"))
    return result


@router.get(
    "/notifications/whatsapp/instances",
    summary="Lista todas as instâncias Evolution API (debug)",
    tags=["diagnostics"],
)
async def list_whatsapp_instances():
    """Mostra todas as instâncias disponíveis na Evolution API para debug."""
    return wa_svc.list_instances()


@router.get(
    "/notifications/test/pdf",
    summary="Testa geração de PDF com WeasyPrint",
    tags=["diagnostics"],
)
async def test_pdf_generation():
    """Gera um PDF simples e retorna info sobre o resultado."""
    try:
        from weasyprint import HTML as WP_HTML
        html = "<html><body><h1>Teste PDF</h1><p>WeasyPrint funcionando.</p></body></html>"
        pdf_bytes = WP_HTML(string=html).write_pdf()
        return {
            "weasyprint": "ok",
            "pdf_size_bytes": len(pdf_bytes),
            "pdf_size_kb": round(len(pdf_bytes) / 1024, 1),
        }
    except ImportError:
        return {"weasyprint": "not_installed", "error": "WeasyPrint não encontrado"}
    except Exception as exc:
        return {"weasyprint": "error", "error": str(exc)[:300]}


@router.get(
    "/notifications/test/monthly-pdf/{pixel_id}",
    summary="Renderiza o relatório mensal real → PDF (debug do PDF em branco)",
    tags=["diagnostics"],
)
async def test_monthly_pdf(pixel_id: str, download: bool = Query(default=False)):
    """Builds the real monthly context for a client and renders it through the
    actual template→PDF pipeline. With ?download=1 returns the PDF bytes so we
    can open it; otherwise returns sizes + an HTML-length sanity check."""
    from fastapi.responses import Response
    from ..services import report_builder, report_renderer

    sb = get_supabase()
    row = (
        sb.table("clients").select("*").eq("pixel_id", pixel_id).limit(1).execute()
    )
    if not (row and row.data):
        raise HTTPException(status_code=404, detail="Client not found")
    client = row.data[0]

    now = datetime.now(timezone.utc)
    year  = now.year if now.month > 1 else now.year - 1
    month = now.month - 1 if now.month > 1 else 12
    try:
        ctx = report_builder.build_monthly_context(
            client_id=client["id"], client=client, year=year, month=month,
        )
    except Exception as exc:
        logger.exception("test_monthly_pdf: build_monthly_context failed")
        return {"stage": "build_context", "error": f"{type(exc).__name__}: {exc}"}

    try:
        html = report_renderer.render_monthly_html(ctx)
    except Exception as exc:
        logger.exception("test_monthly_pdf: render_monthly_html failed")
        return {"stage": "render_html", "error": f"{type(exc).__name__}: {exc}"}

    try:
        from weasyprint import HTML as WP_HTML
        pdf = WP_HTML(string=html).write_pdf()
    except Exception as exc:
        logger.exception("test_monthly_pdf: weasyprint failed")
        return {"stage": "weasyprint", "html_len": len(html),
                "error": f"{type(exc).__name__}: {exc}"}

    if download and pdf:
        return Response(content=pdf, media_type="application/pdf")
    return {
        "stage": "ok",
        "html_len": len(html),
        "html_has_body_text": "Resumo Executivo" in html,
        "context_keys": sorted(ctx.keys()),
        "pdf_size_bytes": len(pdf) if pdf else 0,
        "pdf_size_kb": round(len(pdf) / 1024, 1) if pdf else 0,
    }


@router.post(
    "/notifications/test/email",
    summary="Envia email de teste para AGENCY_NOTIFY_EMAIL",
    tags=["diagnostics"],
)
async def test_email_notification(to: Optional[str] = Query(default=None)):
    result = notify_svc.test_email(to=to)
    if not result["ok"]:
        raise HTTPException(status_code=400, detail=result.get("error", "falha ao enviar"))
    return result


@router.post(
    "/notifications/test/whatsapp",
    summary="Envia mensagem de teste via WhatsApp (Evolution API)",
    tags=["diagnostics"],
)
async def test_whatsapp_notification(phone: Optional[str] = Query(default=None)):
    result = notify_svc.test_whatsapp(phone=phone)
    if not result["ok"]:
        raise HTTPException(status_code=400, detail=result.get("error", "falha ao enviar"))
    return result
