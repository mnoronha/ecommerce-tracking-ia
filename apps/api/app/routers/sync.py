"""
Rotas de sincronização manual via API.

POST /sync/shopify/{pixel_id}           — dispara sync imediato para um cliente
POST /sync/shopify/{pixel_id}/backfill  — sync completo sem filtro de data
GET  /sync/shopify/{pixel_id}/status    — retorna last_sync_at e estado
"""

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from ..database import get_supabase
from ..services import crypto, metrics_cache, reports, report_builder, report_renderer
from ..services import resend as email_service
from ..services import shopify_sync

router = APIRouter(prefix="/sync", tags=["sync"])


def _get_client(pixel_id: str) -> dict:
    sb = get_supabase()
    rows = (
        sb.table("clients")
        .select(
            "id, pixel_id, name, shopify_domain, shopify_access_token, "
            "shopify_sync_enabled, shopify_last_sync_at, is_active, "
            "ga4_measurement_id, ga4_api_secret, "
            "meta_pixel_id, meta_access_token"
        )
        .eq("pixel_id", pixel_id)
        .limit(1)
        .execute()
    ).data or []
    if not rows:
        raise HTTPException(status_code=404, detail="Client not found")
    return rows[0]


@router.post("/shopify/{pixel_id}", summary="Trigger Shopify API sync")
async def trigger_shopify_sync(
    pixel_id: str,
    since: Optional[str] = Query(
        None,
        description="ISO 8601 datetime. If omitted, uses last_sync_at or 7 days ago.",
    ),
):
    """
    Dispara uma sincronização imediata de pedidos via Shopify Admin API.
    Pode ser chamado para qualquer cliente Shopify — não exige shopify_sync_enabled.
    """
    row = _get_client(pixel_id)
    client = crypto.decrypt_client_secrets(row)

    since_dt: Optional[datetime] = None
    if since:
        try:
            since_dt = datetime.fromisoformat(since.replace("Z", "+00:00"))
        except ValueError:
            raise HTTPException(status_code=422, detail="Invalid since date format")

    result = shopify_sync.sync_client(client, since=since_dt)
    return {
        "pixel_id": pixel_id,
        "client_name": row.get("name"),
        **result,
        "synced_at": datetime.now(timezone.utc).isoformat(),
    }


@router.post("/shopify/{pixel_id}/backfill", summary="Full Shopify backfill")
async def trigger_shopify_backfill(pixel_id: str):
    """
    Importa TODOS os pedidos pagos desde sempre.
    Use apenas uma vez para novos clientes ou para reconstruir dados históricos.
    """
    row = _get_client(pixel_id)
    client = crypto.decrypt_client_secrets(row)
    result = shopify_sync.sync_client(client, full_backfill=True)
    return {
        "pixel_id": pixel_id,
        "client_name": row.get("name"),
        **result,
        "synced_at": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/shopify/{pixel_id}/status", summary="Shopify sync status")
async def shopify_sync_status(pixel_id: str):
    row = _get_client(pixel_id)
    return {
        "pixel_id":              pixel_id,
        "client_name":           row.get("name"),
        "shopify_domain":        row.get("shopify_domain"),
        "shopify_sync_enabled":  row.get("shopify_sync_enabled", False),
        "shopify_last_sync_at":  row.get("shopify_last_sync_at"),
        "is_active":             row.get("is_active", True),
    }


@router.post("/metrics-cache", summary="Trigger metrics cache refresh (all clients)")
async def trigger_metrics_cache():
    """Atualiza manualmente o cache de métricas externas (Google Ads conversions) para todos os clientes."""
    import asyncio
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, metrics_cache.run_daily_metrics_cache)
    return {"status": "ok", "synced_at": datetime.now(timezone.utc).isoformat()}


@router.post("/metrics-cache/{pixel_id}", summary="Trigger metrics cache refresh for one client")
async def trigger_metrics_cache_client(pixel_id: str):
    """Atualiza o cache de métricas externas para um cliente (GA4 se disponível, senão Google Ads)."""
    sb = get_supabase()
    rows = (
        sb.table("clients")
        .select(
            "id, name, ga4_property_id, ga4_reporting_enabled, "
            "google_ads_customer_id, google_ads_refresh_token, google_ads_login_customer_id"
        )
        .eq("pixel_id", pixel_id)
        .eq("is_active", True)
        .limit(1)
        .execute()
    ).data or []
    if not rows:
        raise HTTPException(status_code=404, detail="Client not found")
    client = rows[0]
    source = None
    if client.get("ga4_reporting_enabled") and client.get("ga4_property_id"):
        if metrics_cache.refresh_ga4(client):
            source = "ga4"
    if source is None and client.get("google_ads_customer_id"):
        if metrics_cache.refresh_google_ads(client):
            source = "google_ads"
    return {
        "pixel_id": pixel_id,
        "updated": source is not None,
        "source": source,
        "synced_at": datetime.now(timezone.utc).isoformat(),
    }


@router.post("/reports/{pixel_id}/weekly", summary="Send weekly report preview")
async def trigger_weekly_report(
    pixel_id: str,
    to: str = Query(..., description="Override recipient email address"),
):
    """Dispara o relatório semanal para um cliente imediatamente, enviando para o email informado."""
    sb = get_supabase()
    rows = (
        sb.table("clients")
        .select("id, pixel_id, name, logo_url, alert_email, alert_emails, whatsapp_group_jid, client_type")
        .eq("pixel_id", pixel_id)
        .eq("is_active", True)
        .limit(1)
        .execute()
    ).data or []
    if not rows:
        raise HTTPException(status_code=404, detail="Client not found")
    c = rows[0]

    import asyncio
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(
        None,
        lambda: reports._send_weekly(c["id"], c["pixel_id"], c.get("name") or pixel_id, [to], c),
    )
    return {"sent_to": to, "client": c.get("name"), "type": "weekly"}


_TIKTOK_PINTEREST_HTML = """
  <!-- Recomendação TikTok + Pinterest -->
  <tr>
    <td style="padding:0 0 28px">
      <p style="margin:0 0 12px;font-size:15px;font-weight:600;color:#e2e8f0;
         border-bottom:1px solid #2a2f3e;padding-bottom:8px">
        🚀 Próxima Fronteira — TikTok Ads &amp; Pinterest Ads
      </p>
      <div style="background:#1a1f2e;border:1px solid #2a2f3e;border-radius:8px;
                  border-top:2px solid #6366f1;padding:16px 18px;margin-bottom:12px">
        <p style="margin:0 0 6px;font-size:13px;font-weight:700;color:#e2e8f0">
          Por que retomar agora?
        </p>
        <p style="margin:0;font-size:13px;color:#94a3b8;line-height:1.65">
          A LK já testou TikTok e Pinterest no passado sem conseguir medir resultados com precisão.
          Hoje o cenário mudou: com a <strong style="color:#a5b4fc">Noro Platform</strong>, temos
          tracking first-party real — pixel server-side via CNAME, fbp/fbc capturado, gclid e gbraid
          funcionando. Conseguimos atribuir conversões a qualquer canal com a mesma precisão que
          usamos hoje em Meta e Google. Isso elimina o principal motivo de abandonar esses canais.
        </p>
      </div>
      <table width="100%" cellpadding="0" cellspacing="0" style="margin-bottom:12px">
        <tr>
          <td width="50%" style="padding-right:6px;vertical-align:top">
            <div style="background:#1a1f2e;border:1px solid #2a2f3e;border-radius:8px;padding:14px">
              <p style="margin:0 0 6px;font-size:13px;font-weight:700;color:#e2e8f0">
                🎵 TikTok Ads
              </p>
              <p style="margin:0;font-size:12px;color:#94a3b8;line-height:1.6">
                Audiência jovem (18–34) altamente engajada com moda e sneakers premium.
                Formato de vídeo curto perfeito para mostrar tênis exclusivos em uso.
                CPM historicamente 40–60% menor que Meta para esse segmento.
                Com o pixel server-side da Noro, atribuímos compra a cada anúncio em tempo real.
              </p>
            </div>
          </td>
          <td width="50%" style="padding-left:6px;vertical-align:top">
            <div style="background:#1a1f2e;border:1px solid #2a2f3e;border-radius:8px;padding:14px">
              <p style="margin:0 0 6px;font-size:13px;font-weight:700;color:#e2e8f0">
                📌 Pinterest Ads
              </p>
              <p style="margin:0;font-size:12px;color:#94a3b8;line-height:1.6">
                Plataforma de descoberta de produtos com alta intenção de compra.
                Audiência que pesquisa moda e tem ticket médio maior. Ideal para
                catálogo de sneakers de luxo e colabs como Versace, Loewe e On Running.
                O Shopping Catalog do Pinterest converte como Google Shopping.
              </p>
            </div>
          </td>
        </tr>
      </table>
      <div style="background:#0c0e14;border:1px solid #2a2f3e;border-radius:8px;padding:14px 18px">
        <p style="margin:0 0 8px;font-size:12px;font-weight:600;color:#6366f1;
           text-transform:uppercase;letter-spacing:0.5px">Plano de retomada sugerido</p>
        <p style="margin:0 0 5px;font-size:13px;color:#94a3b8;line-height:1.5">
          <span style="color:#6366f1;font-weight:700">1.</span>
          Instalar o pixel TikTok e Pinterest via Noro Platform (server-side + browser) — 1 dia.
        </p>
        <p style="margin:0 0 5px;font-size:13px;color:#94a3b8;line-height:1.5">
          <span style="color:#6366f1;font-weight:700">2.</span>
          Campanha teste TikTok — R$3.000/mês com vídeos dos modelos destaque (Versace collab, On Loewe).
        </p>
        <p style="margin:0 0 5px;font-size:13px;color:#94a3b8;line-height:1.5">
          <span style="color:#6366f1;font-weight:700">3.</span>
          Campanha teste Pinterest Shopping — R$2.000/mês sincronizando catálogo Shopify.
        </p>
        <p style="margin:0;font-size:13px;color:#94a3b8;line-height:1.5">
          <span style="color:#6366f1;font-weight:700">4.</span>
          Avaliar ROAS após 30 dias com dados server-side. Meta: ROAS ≥ 4x para escalar.
        </p>
      </div>
    </td>
  </tr>
"""


@router.post("/reports/{pixel_id}/monthly", summary="Send monthly report preview")
async def trigger_monthly_report(
    pixel_id: str,
    to: str = Query(..., description="Override recipient email address"),
    force: bool = Query(False, description="Send even if health check would hold it"),
    year: Optional[int] = Query(None, description="Ano do relatório (padrão: mês anterior)"),
    month: Optional[int] = Query(None, description="Mês do relatório 1-12 (padrão: mês anterior)"),
    online_only: bool = Query(False, description="True = exclui pedidos POS/loja física (só e-commerce)"),
):
    """Dispara o relatório mensal para um cliente imediatamente, enviando para o email informado.
    Quando year+month são fornecidos, gera o relatório para aquele período específico (útil para
    relatórios de mês corrente para reuniões). online_only=true exclui vendas de loja física."""
    sb = get_supabase()
    rows = (
        sb.table("clients")
        .select("id, pixel_id, name, logo_url, alert_email, alert_emails, whatsapp_group_jid, client_type")
        .eq("pixel_id", pixel_id)
        .eq("is_active", True)
        .limit(1)
        .execute()
    ).data or []
    if not rows:
        raise HTTPException(status_code=404, detail="Client not found")
    c = rows[0]

    import asyncio
    loop = asyncio.get_event_loop()

    # Período customizado: gera o relatório direto via build_monthly_context
    if year and month:
        if not (1 <= month <= 12):
            raise HTTPException(status_code=400, detail="month deve estar entre 1 e 12")
        _MONTH_PT = ["", "Janeiro", "Fevereiro", "Março", "Abril", "Maio", "Junho",
                     "Julho", "Agosto", "Setembro", "Outubro", "Novembro", "Dezembro"]

        def _build_custom():
            from ..services import crypto as _crypto
            _full = (sb.table("clients").select("*").eq("id", c["id"]).limit(1).execute().data or [{}])[0]
            client_full = _crypto.decrypt_client_secrets({**c, **_full})
            ctx = report_builder.build_monthly_context(
                client_id=c["id"], client=client_full, year=year, month=month,
                online_only=online_only,
            )
            html = report_renderer.render_monthly_email_html(ctx)
            # Injetar seção TikTok + Pinterest antes do footer
            html = html.replace(
                "<!-- Footer -->",
                _TIKTOK_PINTEREST_HTML + "\n      <!-- Footer -->",
            )
            periodo = f"{_MONTH_PT[month]}/{year}"
            canal_tag = " · Somente E-commerce" if online_only else ""
            subject = (
                f"📈 Relatório {periodo} (1–{datetime.now(timezone.utc).day:02d}/{month:02d})"
                f"{canal_tag} · {c.get('name') or pixel_id}"
            )
            email_service.send_email(to=to, subject=subject, html_body=html)
            return {"sent_to": to, "held": False, "period": f"{year}-{month:02d}", "online_only": online_only}

        result = await loop.run_in_executor(None, _build_custom)
        return {"client": c.get("name"), "type": "monthly", **result}

    result = await loop.run_in_executor(
        None,
        lambda: reports._send_monthly(
            c["id"], c["pixel_id"], c.get("name") or pixel_id, [to], force=force, client=c
        ),
    )
    return {"client": c.get("name"), "type": "monthly", **result}


@router.get("/shopify/{pixel_id}/note-attributes", summary="Inspect raw note_attributes from Shopify (GTM tag validation)")
async def inspect_note_attributes(
    pixel_id: str,
    limit: int = Query(5, ge=1, le=20, description="Número de pedidos recentes a inspecionar"),
):
    """
    Busca os pedidos mais recentes diretamente da Shopify Admin API e retorna
    os note_attributes crus. Usado para validar se a tag GTM está escrevendo
    _utm_*, _fbp, _fbc, _gclid nos atributos do carrinho.
    """
    import httpx
    row = _get_client(pixel_id)
    client = crypto.decrypt_client_secrets(row)

    domain = (client.get("shopify_domain") or "").strip().rstrip("/")
    token  = client.get("shopify_access_token") or ""
    if not domain or not token:
        raise HTTPException(status_code=400, detail="Client has no Shopify domain/token")

    url = f"https://{domain}/admin/api/2024-10/orders.json"
    params = {
        "financial_status": "any",
        "status": "any",
        "limit": limit,
        "fields": "id,name,created_at,note_attributes,source_name,landing_site",
        "order": "created_at DESC",
    }
    try:
        resp = httpx.get(url, headers={"X-Shopify-Access-Token": token}, params=params, timeout=15.0)
        resp.raise_for_status()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Shopify API error: {exc}")

    orders = resp.json().get("orders", [])
    result = []
    for o in orders:
        attrs = {a["name"]: a["value"] for a in (o.get("note_attributes") or [])}
        noro_keys = {k: v for k, v in attrs.items() if k.startswith("_utm_") or k in ("_gclid", "_gbraid", "_wbraid", "_fbclid", "_fbp", "_fbc", "_etv")}
        result.append({
            "order_id":       o.get("id"),
            "name":           o.get("name"),
            "created_at":     o.get("created_at"),
            "source_name":    o.get("source_name"),
            "landing_site":   o.get("landing_site"),
            "noro_attrs":     noro_keys,
            "all_attr_keys":  list(attrs.keys()),
        })

    return {
        "pixel_id": pixel_id,
        "orders_inspected": len(result),
        "orders": result,
    }


@router.patch("/shopify/{pixel_id}/enable", summary="Enable/disable Shopify API sync")
async def toggle_shopify_sync(pixel_id: str, enabled: bool = Query(...)):
    """Ativa ou desativa o polling horário para um cliente."""
    row = _get_client(pixel_id)
    get_supabase().table("clients").update(
        {"shopify_sync_enabled": enabled}
    ).eq("id", row["id"]).execute()
    return {"pixel_id": pixel_id, "shopify_sync_enabled": enabled}
