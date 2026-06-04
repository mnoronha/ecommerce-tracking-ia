"""
AI Analyst — Claude-powered insights engine.

Coleta métricas dos últimos 7/30 dias, envia para Claude e persiste
insights estruturados na tabela ai_insights.

Tipos de análise:
  - weekly_report   : resumo semanal completo
  - recommendation  : ação específica recomendada
  - anomaly         : variação anormal detectada
  - pattern         : padrão comportamental encontrado
"""

import json
import logging
import re
from datetime import datetime, timedelta
from typing import Optional

import anthropic

from ..config import settings
from ..database import get_supabase

logger = logging.getLogger(__name__)


# ── Data collection ────────────────────────────────────────────────────────────

def _sanitize(value: Optional[str], max_len: int = 80) -> str:
    """Strip control chars and limit length to prevent prompt injection."""
    if not value:
        return ""
    cleaned = re.sub(r"[\x00-\x1f\x7f]", "", str(value))
    return cleaned[:max_len]


def _get_client_name(client_uuid: str) -> str:
    try:
        row = get_supabase().table("clients").select("pixel_id").eq("id", client_uuid).limit(1).execute()
        if row and row.data:
            return row.data[0].get("pixel_id", "loja")
    except Exception:
        pass
    return "loja"


def _collect_metrics(client_uuid: str) -> dict:
    """Coleta todas as métricas relevantes do Supabase para análise."""
    sb = get_supabase()
    now = datetime.utcnow()

    def ago(days: int) -> str:
        return (now - timedelta(days=days)).isoformat()

    # ── Pedidos 7d e 30d ──────────────────────────────────────────────────
    orders_30 = sb.table("orders").select(
        "total_price, financial_status, utm_source, utm_medium, utm_campaign, created_at, email"
    ).eq("client_id", client_uuid).gte("created_at", ago(30)).execute()

    orders_7 = [o for o in (orders_30.data or [])
                if o["created_at"] >= ago(7)]

    orders_prev_7 = sb.table("orders").select("total_price").eq(
        "client_id", client_uuid
    ).gte("created_at", ago(14)).lt("created_at", ago(7)).execute()

    orders_prev_30 = sb.table("orders").select("total_price").eq(
        "client_id", client_uuid
    ).gte("created_at", ago(60)).lt("created_at", ago(30)).execute()

    # ── Eventos pixel 7d ──────────────────────────────────────────────────
    events_7 = sb.table("tracking_events").select(
        "event_type, visitor_id, product_name, utm_source"
    ).eq("client_id", client_uuid).gte("created_at", ago(7)).execute()

    # ── Visitantes ────────────────────────────────────────────────────────
    visitors_total = sb.table("visitors").select(
        "id", count="exact", head=True
    ).eq("client_id", client_uuid).execute()

    visitors_7 = sb.table("visitors").select(
        "id", count="exact", head=True
    ).eq("client_id", client_uuid).gte("created_at", ago(7)).execute()

    # ── Processa métricas ─────────────────────────────────────────────────
    def revenue(orders):
        return round(sum(o.get("total_price", 0) for o in orders), 2)

    def funnel(evts):
        uniq = lambda t: len(set(e["visitor_id"] for e in evts if e["event_type"] == t and e.get("visitor_id")))
        return {
            "pageviews":     uniq("pageview"),
            "product_views": uniq("view_product"),
            "add_to_cart":   uniq("add_to_cart"),
            "checkout":      uniq("begin_checkout"),
        }

    o7  = orders_7
    o30 = orders_30.data or []
    op7 = orders_prev_7.data or []
    op30 = orders_prev_30.data or []
    ev7 = events_7.data or []

    rev7   = revenue(o7)
    rev30  = revenue(o30)
    revp7  = revenue(op7)
    revp30 = revenue(op30)

    # Top campaigns (30d)
    camp_map: dict = {}
    for o in o30:
        src = o.get("utm_source") or "direto"
        camp = o.get("utm_campaign") or "—"
        key = f"{src} / {camp}"
        if key not in camp_map:
            camp_map[key] = {"orders": 0, "revenue": 0}
        camp_map[key]["orders"] += 1
        camp_map[key]["revenue"] += o.get("total_price", 0)

    top_campaigns = sorted(camp_map.items(), key=lambda x: x[1]["revenue"], reverse=True)[:5]

    # ── Cohort retention: % que recomprou em 30d (últimos 3 meses) ───────────
    cohort_data = []
    try:
        for months_ago in range(1, 4):
            c_start = (now - timedelta(days=30 * months_ago)).isoformat()
            c_end   = (now - timedelta(days=30 * (months_ago - 1))).isoformat()
            first_buyers = sb.table("orders").select("email", count="exact", head=False).eq(
                "client_id", client_uuid).eq("is_first_purchase", True).gte(
                "created_at", c_start).lt("created_at", c_end).execute()
            emails = list({o["email"] for o in (first_buyers.data or []) if o.get("email")})
            returned = 0
            if emails:
                for email in emails[:200]:  # cap to avoid huge queries
                    r = sb.table("orders").select("id", count="exact", head=True).eq(
                        "client_id", client_uuid).eq("email", email).eq(
                        "is_first_purchase", False).execute()
                    if (r.count or 0) > 0:
                        returned += 1
            cohort_data.append({
                "mes": f"M-{months_ago}",
                "novos_compradores": len(emails),
                "recompraram_pct": round(returned / len(emails) * 100, 1) if emails else 0,
            })
    except Exception as exc:
        logger.debug("cohort collection failed: %s", exc)

    # ── Budget intelligence: ROAS por canal ──────────────────────────────────
    budget_intel: list[dict] = []
    for key, v in list(camp_map.items())[:5]:
        roas = None
        # Simple efficiency: revenue per order (sem spend, usamos como proxy)
        if v["orders"] > 0:
            roas = round(v["revenue"] / v["orders"], 2)
        budget_intel.append({
            "canal": _sanitize(key),
            "pedidos": v["orders"],
            "receita": round(v["revenue"], 2),
            "ticket_medio": roas,
        })

    # Top products (7d)
    prod_map: dict = {}
    for e in ev7:
        name = e.get("product_name")
        if not name:
            continue
        if name not in prod_map:
            prod_map[name] = {"views": 0, "cart": 0}
        if e["event_type"] == "view_product":
            prod_map[name]["views"] += 1
        elif e["event_type"] == "add_to_cart":
            prod_map[name]["cart"] += 1

    top_products = sorted(prod_map.items(), key=lambda x: x[1]["views"], reverse=True)[:5]

    fn7 = funnel(ev7)
    conv_rate = round((len(o7) / fn7["pageviews"] * 100), 2) if fn7["pageviews"] > 0 else 0

    return {
        "periodo": {
            "7d": {
                "receita": rev7,
                "pedidos": len(o7),
                "ticket_medio": round(rev7 / len(o7), 2) if o7 else 0,
                "variacao_receita_pct": round((rev7 - revp7) / revp7 * 100, 1) if revp7 else None,
                "variacao_pedidos_pct": round((len(o7) - len(op7)) / len(op7) * 100, 1) if op7 else None,
            },
            "30d": {
                "receita": rev30,
                "pedidos": len(o30),
                "ticket_medio": round(rev30 / len(o30), 2) if o30 else 0,
                "variacao_receita_pct": round((rev30 - revp30) / revp30 * 100, 1) if revp30 else None,
            },
        },
        "funil_7d": {
            **fn7,
            "purchases": len(o7),
            "taxa_conversao_pct": conv_rate,
            "abandono_carrinho_pct": round(
                (1 - len(o7) / fn7["add_to_cart"]) * 100, 1
            ) if fn7["add_to_cart"] > 0 else None,
        },
        "visitantes": {
            "total": visitors_total.count or 0,
            "novos_7d": visitors_7.count or 0,
        },
        "top_campanhas_30d": [
            {"canal": _sanitize(k), "pedidos": v["orders"], "receita": round(v["revenue"], 2)}
            for k, v in top_campaigns
        ],
        "top_produtos_7d": [
            {"produto": _sanitize(k), "views": v["views"], "carrinho": v["cart"]}
            for k, v in top_products
        ],
        "cohort_retencao": cohort_data,
        "budget_intel_por_canal": budget_intel,
        "qualidade_dados": {
            "pedidos_com_utm_30d": sum(1 for o in o30 if o.get("utm_source")),
            "pedidos_com_email_30d": sum(1 for o in o30 if o.get("email")),
            "total_pedidos_30d": len(o30),
        },
    }


# ── Claude analysis ────────────────────────────────────────────────────────────

_SYSTEM_PROMPT = """Você é um analista sênior de e-commerce especializado em performance de lojas Shopify,
atribuição de marketing e otimização de funil.

Analise os dados fornecidos e retorne EXATAMENTE um JSON com a seguinte estrutura (sem markdown, sem texto extra):

{
  "insights": [
    {
      "type": "weekly_report|recommendation|anomaly|pattern",
      "severity": "info|warning|critical",
      "title": "Título curto e direto (max 80 chars)",
      "content": "Análise detalhada com contexto e números específicos (2-4 parágrafos)",
      "recommendation": "Ação concreta e específica que o dono da loja deve tomar HOJE",
      "data": {}
    }
  ]
}

Regras:
- Gere entre 3 e 5 insights
- Priorize insights acionáveis — o que a pessoa pode fazer AGORA para melhorar resultados
- Use números reais dos dados para embasar cada insight
- Se houver anomalia (queda ou crescimento > 30%), marque como 'critical' ou 'warning'
- O campo 'recommendation' deve ser 1 frase imperativa e específica
- Responda sempre em português brasileiro
- Foque em: ROI de campanhas, abandono de carrinho, produtos com alto tráfego mas baixa conversão, sazonalidade"""


def _call_claude(metrics: dict, store_name: str = "loja") -> list[dict]:
    """Chama Claude com as métricas e retorna lista de insights parseados."""
    client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)

    prompt = f"""Dados da loja {store_name}:

{json.dumps(metrics, ensure_ascii=False, indent=2)}

Analise os dados acima e gere insights estratégicos incluindo:
1. Performance geral e anomalias
2. Retenção de clientes (use cohort_retencao)
3. Eficiência de canais (use budget_intel_por_canal) com recomendações de budget
4. LTV preditivo: baseado na taxa de recompra, projete o LTV esperado de novos clientes
5. Ações prioritárias para os próximos 7 dias"""

    message = client.messages.create(
        model=settings.ANTHROPIC_MODEL,
        max_tokens=4096,
        messages=[{"role": "user", "content": prompt}],
        system=_SYSTEM_PROMPT,
    )

    raw = message.content[0].text.strip()

    # Remove markdown code fences if present
    for fence in ("```json", "```"):
        if raw.startswith(fence):
            raw = raw[len(fence):]
            break
    if raw.endswith("```"):
        raw = raw[:-3]

    try:
        parsed = json.loads(raw.strip())
        return parsed.get("insights", [])
    except json.JSONDecodeError as exc:
        logger.error("Claude returned invalid JSON: %s — raw[:200]=%s", exc, raw[:200])
        return []


# ── Persist ────────────────────────────────────────────────────────────────────

def _was_recently_generated(client_uuid: str, insight_type: str, hours: int = 6) -> bool:
    """Verifica se um insight do mesmo tipo foi gerado nas últimas N horas."""
    try:
        from datetime import timezone
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
        result = get_supabase().table("ai_insights").select("id", count="exact", head=True).eq(
            "client_id", client_uuid).eq("type", insight_type).gte("created_at", cutoff).execute()
        return (result.count or 0) > 0
    except Exception:
        return False


def _save_insights(client_uuid: str, insights: list[dict]) -> int:
    """Persiste lista de insights na tabela ai_insights. Retorna contagem salva."""
    sb = get_supabase()
    saved = 0
    for ins in insights:
        insight_type = ins.get("type", "recommendation")
        # Deduplicate: skip weekly_report if one was generated in the last 6 hours
        if insight_type == "weekly_report" and _was_recently_generated(client_uuid, "weekly_report", hours=6):
            logger.debug("Skipping duplicate weekly_report for %s", client_uuid)
            continue
        try:
            sb.table("ai_insights").insert({
                "client_id": client_uuid,
                "type":      ins.get("type", "recommendation"),
                "severity":  ins.get("severity", "info"),
                "title":     ins.get("title", ""),
                "content":   ins.get("content", ""),
                "data": {
                    "recommendation": ins.get("recommendation", ""),
                    **(ins.get("data") or {}),
                },
            }).execute()
            saved += 1
        except Exception as exc:
            logger.error("Failed to save insight: %s", exc)
    return saved


# ── Daily lightweight analysis ────────────────────────────────────────────────

_DAILY_SYSTEM_PROMPT = """Você é um analista de e-commerce focado em alertas diários de performance.

Analise os dados e retorne EXATAMENTE um JSON (sem markdown, sem texto extra):

{
  "insights": [
    {
      "type": "recommendation|anomaly|pattern",
      "severity": "info|warning|critical",
      "title": "Título curto (max 80 chars)",
      "content": "Análise concisa com 1-2 parágrafos e números específicos",
      "recommendation": "Ação concreta para fazer HOJE",
      "data": {}
    }
  ]
}

Regras:
- Gere 2 a 3 insights curtos e acionáveis
- Foque em anomalias nas últimas 24-48h e oportunidades imediatas
- NÃO gere weekly_report — apenas recommendation, anomaly ou pattern
- Use números reais dos dados
- Responda em português brasileiro"""


def generate_daily_insights(client_uuid: str) -> dict:
    """
    Análise diária leve — gera 2-3 recomendações/anomalias usando Claude Haiku.
    Pula se já foram gerados insights nas últimas 20h (evita duplicatas diárias).
    """
    if not settings.ANTHROPIC_API_KEY:
        raise ValueError("ANTHROPIC_API_KEY não configurada")

    if _was_recently_generated(client_uuid, "recommendation", hours=20):
        logger.debug("daily insights skipped for %s — already generated in last 20h", client_uuid)
        return {"insights_generated": 0, "insights_saved": 0, "skipped": True}

    store_name = _get_client_name(client_uuid)
    metrics = _collect_metrics(client_uuid)

    client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
    prompt = f"""Dados diários da loja {store_name}:

{json.dumps(metrics, ensure_ascii=False, indent=2)}

Analise os dados e gere insights diários focando em:
1. Anomalias nas últimas 24-48h (quedas ou picos)
2. Uma recomendação imediata de alto impacto
3. Padrão relevante para agir hoje"""

    message = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=1500,
        messages=[{"role": "user", "content": prompt}],
        system=_DAILY_SYSTEM_PROMPT,
    )

    raw = message.content[0].text.strip()
    for fence in ("```json", "```"):
        if raw.startswith(fence):
            raw = raw[len(fence):]
            break
    if raw.endswith("```"):
        raw = raw[:-3]

    try:
        parsed = json.loads(raw.strip())
        insights = parsed.get("insights", [])
    except json.JSONDecodeError as exc:
        logger.error("Claude Haiku returned invalid JSON: %s — raw[:200]=%s", exc, raw[:200])
        return {"insights_generated": 0, "insights_saved": 0, "error": str(exc)}

    saved = _save_insights(client_uuid, insights)
    logger.info("Daily insights for %s: generated=%d saved=%d", store_name, len(insights), saved)
    return {"insights_generated": len(insights), "insights_saved": saved}


def run_daily_insights_all_clients() -> None:
    """Cron entry point — roda às 07:30 UTC (04:30 BRT) para todos os clientes ativos."""
    try:
        sb = get_supabase()
        clients = sb.table("clients").select("id, pixel_id").execute()
        for row in (clients.data or []):
            try:
                generate_daily_insights(row["id"])
            except Exception as exc:
                logger.error("daily insights failed for %s: %s", row.get("pixel_id"), exc)
    except Exception as exc:
        logger.error("run_daily_insights_all_clients: %s", exc)


# ── Monthly deep analysis ──────────────────────────────────────────────────────

_MONTHLY_SYSTEM_PROMPT = """Você é um estrategista sênior de tráfego pago e e-commerce escrevendo o
relatório MENSAL que será lido pelo dono da loja (cliente da agência). Sua
análise é o principal diferencial da agência — precisa ser específica,
acionável e valiosa, nunca genérica.

Você recebe os números REAIS do mês (faturamento, ROAS por canal, campanhas,
funil, retenção, metas). Use-os literalmente — cite valores, percentuais e
nomes de campanha. Nada de conselhos vagos do tipo "invista mais em quem
performa"; diga QUAL campanha/canal, com QUE número, e O QUE fazer.

Retorne EXATAMENTE este JSON (sem markdown, sem texto fora do JSON):

{
  "resumo_executivo": "2-4 frases que um dono de loja ocupado leria em 15s: como foi o mês, o número que mais importa, e o foco do próximo mês.",
  "destaques": [
    {"tipo": "positivo", "titulo": "curto", "texto": "1-2 frases com número real"},
    {"tipo": "atencao",  "titulo": "curto", "texto": "1-2 frases com número real e o porquê"}
  ],
  "analise_canais": "1-2 parágrafos comparando Meta vs Google (e outros) com ROAS/CPA reais; aponte a melhor e a pior campanha pelo nome e o que fazer com cada uma.",
  "plano": {
    "meta_faturamento": <número sugerido p/ próximo mês, baseado na tendência e na meta atual>,
    "meta_roas": <número>,
    "meta_cpa": <número ou 0 se não der p/ estimar>,
    "budget_total": <investimento total sugerido p/ próximo mês>,
    "acoes": [
      "Ação 1 priorizada, específica e mensurável (ex.: 'Escalar a campanha X em 20% — ROAS 4,1x, melhor do mês')",
      "Ação 2",
      "Ação 3",
      "(3 a 5 ações no total)"
    ]
  }
}

Regras:
- SEMPRE comece pelos pontos positivos, mesmo num mês fraco — encontre o que funcionou (canal eficiente, recompra, produto, melhora de ticket).
- Seja honesto sobre problemas, com tom construtivo e propositivo (solução, não culpa).
- 1 a 3 destaques positivos e 1 a 2 de atenção.
- 3 a 5 ações no plano, ordenadas por impacto, cada uma com um número/critério concreto.
- Se faltar dado para uma meta numérica, estime com base na tendência (não deixe 0 sem motivo).
- Português brasileiro. Valores monetários em reais."""


def generate_monthly_analysis(
    client_uuid: str,
    report_metrics: Optional[dict] = None,
    store_name: Optional[str] = None,
    force: bool = False,
) -> dict:
    """
    Análise mensal estratégica (Claude Sonnet) a partir dos números REAIS do
    relatório. Retorna o objeto rico {resumo_executivo, destaques, analise_canais,
    plano} e o persiste em ai_insights (type=monthly_report, data=<objeto>).

    `report_metrics`: dict compacto montado pelo report_builder com os números do
    mês (canais, campanhas, metas, funil, retenção). Se ausente, cai no coletor
    genérico de 30d. Reutiliza um insight rico gerado nas últimas 3h (a menos de
    force=True) para não repetir a chamada ao Claude em re-renders.
    """
    if not settings.ANTHROPIC_API_KEY:
        logger.warning("generate_monthly_analysis: ANTHROPIC_API_KEY ausente")
        return {}

    sb = get_supabase()
    if not force:
        try:
            cutoff = (datetime.utcnow() - timedelta(hours=3)).isoformat()
            recent = (
                sb.table("ai_insights").select("data, created_at")
                .eq("client_id", client_uuid).eq("type", "monthly_report")
                .gte("created_at", cutoff).order("created_at", desc=True).limit(1).execute()
            )
            if recent.data:
                data = recent.data[0].get("data") or {}
                if data.get("plano"):           # already the rich structure
                    logger.info("monthly analysis: reusing cached insight for %s", client_uuid)
                    return data
        except Exception as exc:
            logger.debug("monthly analysis cache check failed: %s", exc)

    store_name = store_name or _get_client_name(client_uuid)
    metrics = report_metrics if report_metrics is not None else _collect_metrics(client_uuid)

    client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
    prompt = (
        f"Números reais do mês para a loja {store_name}:\n\n"
        f"{json.dumps(metrics, ensure_ascii=False, indent=2)}\n\n"
        "Escreva a análise mensal seguindo EXATAMENTE o formato JSON do sistema. "
        "Cite campanhas pelo nome e use os números acima."
    )

    try:
        message = client.messages.create(
            model=settings.ANTHROPIC_MODEL,
            max_tokens=4096,
            messages=[{"role": "user", "content": prompt}],
            system=_MONTHLY_SYSTEM_PROMPT,
        )
        raw = message.content[0].text.strip()
        for fence in ("```json", "```"):
            if raw.startswith(fence):
                raw = raw[len(fence):]
                break
        if raw.endswith("```"):
            raw = raw[:-3]
        parsed = json.loads(raw.strip())
    except Exception as exc:
        logger.error("monthly analysis Claude/JSON failed for %s: %s", store_name, exc)
        return {}

    # Persist as a monthly_report insight (content = resumo, data = full object)
    try:
        sb.table("ai_insights").insert({
            "client_id": client_uuid,
            "type":      "monthly_report",
            "severity":  "info",
            "title":     f"Relatório mensal — {store_name}",
            "content":   parsed.get("resumo_executivo", ""),
            "data":      parsed,
        }).execute()
    except Exception as exc:
        logger.warning("monthly analysis save failed for %s: %s", store_name, exc)

    logger.info("monthly analysis generated for %s (acoes=%d)", store_name,
                len((parsed.get("plano") or {}).get("acoes") or []))
    return parsed


def generate_monthly_insights(client_uuid: str) -> dict:
    """Back-compat wrapper — o relatório agora chama generate_monthly_analysis
    com os dados reais. Mantido para callers/cron legados."""
    parsed = generate_monthly_analysis(client_uuid)
    return {"insights_generated": 1 if parsed else 0, "insights_saved": 1 if parsed else 0}


# ── Public entry point ─────────────────────────────────────────────────────────

def generate_insights(client_uuid: str) -> dict:
    """
    Coleta métricas, chama Claude, persiste e retorna os insights gerados.
    Raises on fatal errors (no API key, client not found).
    """
    if not settings.ANTHROPIC_API_KEY:
        raise ValueError("ANTHROPIC_API_KEY não configurada")

    store_name = _get_client_name(client_uuid)
    logger.info("Coletando métricas para %s (%s)", store_name, client_uuid)
    metrics = _collect_metrics(client_uuid)

    logger.info("Chamando Claude %s para análise de %s", settings.ANTHROPIC_MODEL, store_name)
    insights = _call_claude(metrics, store_name)

    saved = _save_insights(client_uuid, insights)
    logger.info("Insights gerados=%d salvos=%d", len(insights), saved)

    return {
        "insights_generated": len(insights),
        "insights_saved":     saved,
        "insights":           insights,
        "metrics_snapshot":   metrics,
    }
