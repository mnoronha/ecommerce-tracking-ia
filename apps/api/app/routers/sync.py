"""
Rotas de sincronização manual via API.

POST /sync/shopify/{pixel_id}                    — dispara sync imediato para um cliente
POST /sync/shopify/{pixel_id}/backfill           — sync completo sem filtro de data
GET  /sync/shopify/{pixel_id}/status             — retorna last_sync_at e estado
POST /sync/spend/{pixel_id}/backfill             — backfill histórico Meta+Google mensal
POST /sync/spend/{pixel_id}/daily-backfill       — backfill diário ad_spend (por dia, qualquer período)
POST /sync/spend/{pixel_id}/debug               — testa TikTok+Pinterest e retorna resposta bruta
POST /sync/meta-attributions/{pixel_id}/backfill — re-sincroniza meta_ad_attributions desde uma data
"""

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from ..config import settings
from ..database import get_supabase
from ..services import crypto, metrics_cache, reports, report_builder, report_renderer
from ..services import resend as email_service
from ..services import shopify_sync, spend_sync, meta_attribution_sync

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


@router.post("/spend/{pixel_id}/backfill", summary="Backfill histórico de ad spend — Meta + Google Ads mensal")
async def spend_backfill(
    pixel_id: str,
    from_date: str = Query(..., description="Data inicial YYYY-MM-DD"),
    to_date:   Optional[str] = Query(None, description="Data final YYYY-MM-DD (padrão: hoje)"),
):
    """
    Busca gasto histórico do Meta Ads e Google Ads com granularidade mensal
    (2 chamadas API no total), armazena em ad_spend e retorna o relatório mensal.
    """
    from datetime import date as _date

    try:
        start = _date.fromisoformat(from_date)
    except ValueError:
        raise HTTPException(status_code=422, detail="from_date inválido — use YYYY-MM-DD")

    end = _date.today()
    if to_date:
        try:
            end = _date.fromisoformat(to_date)
        except ValueError:
            raise HTTPException(status_code=422, detail="to_date inválido — use YYYY-MM-DD")

    sb = get_supabase()
    rows = (
        sb.table("clients")
        .select(
            "id, pixel_id, name, "
            "meta_ad_account_id, meta_access_token, "
            "google_ads_customer_id, google_ads_refresh_token, google_ads_login_customer_id"
        )
        .eq("pixel_id", pixel_id)
        .limit(1)
        .execute()
    ).data or []
    if not rows:
        raise HTTPException(status_code=404, detail="Cliente não encontrado")

    c         = crypto.decrypt_client_secrets(rows[0])
    client_id = c["id"]
    manager   = c.get("google_ads_login_customer_id") or settings.GOOGLE_ADS_MANAGER_ID or None
    errors: list = []

    # ── Meta ──────────────────────────────────────────────────────────────────
    meta_monthly: dict = {}
    if c.get("meta_ad_account_id") and c.get("meta_access_token"):
        meta_rows = spend_sync.fetch_meta_spend_monthly(
            c["meta_ad_account_id"], c["meta_access_token"], start, end
        )
        for r in meta_rows:
            key = r["date"].isoformat()[:7]
            meta_monthly[key] = r
        if not meta_rows:
            errors.append("meta_ads: sem dados retornados — token pode estar expirado")
    else:
        errors.append("meta_ads: credenciais não configuradas")

    # ── Google Ads ────────────────────────────────────────────────────────────
    google_monthly: dict = {}
    if c.get("google_ads_customer_id") and c.get("google_ads_refresh_token"):
        google_rows = spend_sync.fetch_google_spend_monthly(
            c["google_ads_customer_id"], c["google_ads_refresh_token"], start, end, manager
        )
        for r in google_rows:
            key = r["date"].isoformat()[:7]
            google_monthly[key] = r
        if not google_rows:
            errors.append("google_ads: sem dados retornados — credenciais podem estar inválidas")
    else:
        errors.append("google_ads: credenciais não configuradas")

    # ── Montar relatório ──────────────────────────────────────────────────────
    all_months = sorted(set(list(meta_monthly.keys()) + list(google_monthly.keys())))
    months_report = []
    for month in all_months:
        m = meta_monthly.get(month, {})
        g = google_monthly.get(month, {})
        months_report.append({
            "mes":        month,
            "meta_ads":   m.get("spend", 0.0),
            "google_ads": g.get("spend", 0.0),
            "total":      round((m.get("spend", 0.0) + g.get("spend", 0.0)), 2),
        })

    return {
        "client":           c.get("name"),
        "from":             start.isoformat(),
        "to":               end.isoformat(),
        "months":           months_report,
        "total_meta_ads":   round(sum(r["meta_ads"]   for r in months_report), 2),
        "total_google_ads": round(sum(r["google_ads"] for r in months_report), 2),
        "total_geral":      round(sum(r["total"]      for r in months_report), 2),
        "errors":           errors,
    }


@router.post("/spend/{pixel_id}/daily-backfill", summary="Backfill diário de ad_spend — Meta+Google+TikTok+Pinterest por dia")
async def spend_daily_backfill(
    pixel_id:  str,
    from_date: str = Query(..., description="Data inicial YYYY-MM-DD"),
    to_date:   Optional[str] = Query(None, description="Data final YYYY-MM-DD (padrão: hoje)"),
):
    """
    Re-sincroniza ad_spend dia a dia para o período informado, chamando cada API
    individualmente por dia. Corrige dias faltando ou com dados desatualizados.
    Para backfill de meses inteiros prefira /spend/{pixel_id}/backfill (uma chamada mensal).
    """
    from datetime import date as _date

    try:
        start = _date.fromisoformat(from_date)
    except ValueError:
        raise HTTPException(status_code=422, detail="from_date inválido — use YYYY-MM-DD")

    end = _date.today()
    if to_date:
        try:
            end = _date.fromisoformat(to_date)
        except ValueError:
            raise HTTPException(status_code=422, detail="to_date inválido — use YYYY-MM-DD")

    sb = get_supabase()
    rows = (
        sb.table("clients")
        .select("id, pixel_id")
        .eq("pixel_id", pixel_id)
        .eq("is_active", True)
        .limit(1)
        .execute()
    ).data or []
    if not rows:
        raise HTTPException(status_code=404, detail="Cliente não encontrado")

    client_id = rows[0]["id"]

    import asyncio
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(
        None,
        lambda: spend_sync.backfill_spend_range(client_id, pixel_id, start, end),
    )
    return result


@router.post("/meta-attributions/{pixel_id}/backfill", summary="Re-sincroniza meta_ad_attributions desde uma data")
async def meta_attributions_backfill(
    pixel_id:  str,
    from_date: str = Query(..., description="Data inicial YYYY-MM-DD — todos os dias até hoje são re-sincronizados"),
):
    """
    Re-busca dados por anúncio/dia da Meta Ads API para o período desde from_date até hoje
    e faz upsert em meta_ad_attributions. Corrige dados retroativos de atribuição
    (janela de 28 dias do Meta) e dias que nunca foram atualizados após o primeiro sync.
    """
    from datetime import date as _date

    try:
        start = _date.fromisoformat(from_date)
    except ValueError:
        raise HTTPException(status_code=422, detail="from_date inválido — use YYYY-MM-DD")

    today = _date.today()
    days = (today - start).days + 1
    if days < 1:
        raise HTTPException(status_code=422, detail="from_date não pode ser no futuro")
    if days > 180:
        raise HTTPException(status_code=422, detail="Período máximo de 180 dias")

    sb = get_supabase()
    rows = (
        sb.table("clients")
        .select("id, meta_ad_account_id, meta_access_token")
        .eq("pixel_id", pixel_id)
        .eq("is_active", True)
        .limit(1)
        .execute()
    ).data or []
    if not rows:
        raise HTTPException(status_code=404, detail="Cliente não encontrado")

    c = crypto.decrypt_client_secrets(rows[0])
    if not c.get("meta_ad_account_id") or not c.get("meta_access_token"):
        raise HTTPException(status_code=400, detail="Credenciais Meta Ads não configuradas")

    import asyncio
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(
        None,
        lambda: meta_attribution_sync.sync_for_client(
            client_uuid=c["id"],
            account_id=c["meta_ad_account_id"],
            access_token=c["meta_access_token"],
            days=days,
        ),
    )
    return {
        "pixel_id":  pixel_id,
        "from_date": from_date,
        "to_date":   today.isoformat(),
        "days":      days,
        **result,
    }


@router.post("/spend/{pixel_id}/debug", summary="Testa spend sync TikTok+Pinterest e retorna resposta bruta das APIs")
async def debug_spend_sync(
    pixel_id: str,
    target_date: Optional[str] = Query(None, description="Data alvo YYYY-MM-DD (padrão: ontem)"),
):
    """
    Dispara uma chamada de teste para as APIs de spend de TikTok e Pinterest
    para um único dia e retorna o resultado detalhado — incluindo erros.
    Útil para diagnosticar tokens expirados ou problemas de credencial.
    """
    from datetime import date as _date
    import httpx as _httpx
    from ..services.spend_sync import (
        _TIKTOK_REPORT, _PINTEREST_ANALYTICS,
    )

    sb = get_supabase()
    rows = (
        sb.table("clients")
        .select(
            "id, pixel_id, name, "
            "tiktok_advertiser_id, tiktok_access_token, "
            "pinterest_ad_account_id, pinterest_access_token"
        )
        .eq("pixel_id", pixel_id)
        .eq("is_active", True)
        .limit(1)
        .execute()
    ).data or []
    if not rows:
        raise HTTPException(status_code=404, detail="Client not found")

    c = crypto.decrypt_client_secrets(rows[0])

    if target_date:
        try:
            d = _date.fromisoformat(target_date)
        except ValueError:
            raise HTTPException(status_code=422, detail="Invalid date format — use YYYY-MM-DD")
    else:
        d = (_date.today() - __import__("datetime").timedelta(days=1))

    result: dict = {
        "client":      c.get("name") or pixel_id,
        "target_date": d.isoformat(),
        "tiktok":      None,
        "pinterest":   None,
    }

    # ── TikTok ──────────────────────────────────────────────────────────────────
    if c.get("tiktok_advertiser_id") and c.get("tiktok_access_token"):
        date_str = d.isoformat()
        params = {
            "advertiser_id": c["tiktok_advertiser_id"],
            "report_type":   "BASIC",
            "dimensions":    '["stat_time_day"]',
            "metrics":       '["spend","impressions","clicks","total_complete_payment_event_count"]',
            "data_level":    "AUCTION_ADVERTISER",
            "start_date":    date_str,
            "end_date":      date_str,
            "page_size":     1,
        }
        try:
            resp = _httpx.get(
                _TIKTOK_REPORT,
                params=params,
                headers={"Access-Token": c["tiktok_access_token"]},
                timeout=15.0,
            )
            body = resp.json()
            result["tiktok"] = {
                "http_status": resp.status_code,
                "api_code":    body.get("code"),
                "api_message": body.get("message"),
                "raw":         body,
            }
        except Exception as exc:
            result["tiktok"] = {"error": str(exc)}
    else:
        result["tiktok"] = {"error": "credenciais não configuradas"}

    # ── Pinterest ────────────────────────────────────────────────────────────────
    if c.get("pinterest_ad_account_id") and c.get("pinterest_access_token"):
        url = _PINTEREST_ANALYTICS.format(ad_account_id=c["pinterest_ad_account_id"])
        try:
            resp = _httpx.get(
                url,
                params={
                    "start_date":  d.isoformat(),
                    "end_date":    d.isoformat(),
                    "columns":     "SPEND_IN_DOLLAR,IMPRESSION_1,OUTBOUND_CLICK_1,TOTAL_CHECKOUT",
                    "granularity": "DAY",
                },
                headers={"Authorization": f"Bearer {c['pinterest_access_token']}"},
                timeout=15.0,
            )
            body = resp.json() if resp.status_code == 200 else resp.text
            result["pinterest"] = {
                "http_status": resp.status_code,
                "raw":         body,
            }
        except Exception as exc:
            result["pinterest"] = {"error": str(exc)}
    else:
        result["pinterest"] = {"error": "credenciais não configuradas"}

    return result


@router.patch("/shopify/{pixel_id}/enable", summary="Enable/disable Shopify API sync")
async def toggle_shopify_sync(pixel_id: str, enabled: bool = Query(...)):
    """Ativa ou desativa o polling horário para um cliente."""
    row = _get_client(pixel_id)
    get_supabase().table("clients").update(
        {"shopify_sync_enabled": enabled}
    ).eq("id", row["id"]).execute()
    return {"pixel_id": pixel_id, "shopify_sync_enabled": enabled}
