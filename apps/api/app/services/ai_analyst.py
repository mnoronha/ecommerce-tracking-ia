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
from datetime import datetime, timedelta
from typing import Optional

import anthropic

from ..config import settings
from ..database import get_supabase

logger = logging.getLogger(__name__)


# ── Data collection ────────────────────────────────────────────────────────────

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
            {"canal": k, "pedidos": v["orders"], "receita": round(v["revenue"], 2)}
            for k, v in top_campaigns
        ],
        "top_produtos_7d": [
            {"produto": k, "views": v["views"], "carrinho": v["cart"]}
            for k, v in top_products
        ],
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


def _call_claude(metrics: dict) -> list[dict]:
    """Chama Claude com as métricas e retorna lista de insights parseados."""
    client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)

    prompt = f"""Dados da loja LK Sneakers (loja de tênis no Shopify):

{json.dumps(metrics, ensure_ascii=False, indent=2)}

Gere insights estratégicos e recomendações acionáveis baseados nesses dados."""

    message = client.messages.create(
        model=settings.ANTHROPIC_MODEL,
        max_tokens=4096,
        messages=[{"role": "user", "content": prompt}],
        system=_SYSTEM_PROMPT,
    )

    raw = message.content[0].text.strip()

    # Remove markdown code fences if present
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]

    parsed = json.loads(raw)
    return parsed.get("insights", [])


# ── Persist ────────────────────────────────────────────────────────────────────

def _save_insights(client_uuid: str, insights: list[dict]) -> int:
    """Persiste lista de insights na tabela ai_insights. Retorna contagem salva."""
    sb = get_supabase()
    saved = 0
    for ins in insights:
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


# ── Public entry point ─────────────────────────────────────────────────────────

def generate_insights(client_uuid: str) -> dict:
    """
    Coleta métricas, chama Claude, persiste e retorna os insights gerados.
    Raises on fatal errors (no API key, client not found).
    """
    if not settings.ANTHROPIC_API_KEY:
        raise ValueError("ANTHROPIC_API_KEY não configurada")

    logger.info("Coletando métricas para client_uuid=%s", client_uuid)
    metrics = _collect_metrics(client_uuid)

    logger.info("Chamando Claude %s para análise", settings.ANTHROPIC_MODEL)
    insights = _call_claude(metrics)

    saved = _save_insights(client_uuid, insights)
    logger.info("Insights gerados=%d salvos=%d", len(insights), saved)

    return {
        "insights_generated": len(insights),
        "insights_saved":     saved,
        "insights":           insights,
        "metrics_snapshot":   metrics,
    }
