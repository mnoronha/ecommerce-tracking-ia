"""
AI Visibility Analyst — cross-data intelligence after each CSV import.

Crosses AI Visibility metrics with ad spend/revenue, generates Claude insights
stored in ai_insights (type='ai_visibility'), and auto-creates content_briefings
for the highest-priority content opportunities.
"""

import json
import logging
from datetime import date, datetime, timezone, timedelta
from typing import Optional

import anthropic

from ..config import settings
from ..database import get_supabase

logger = logging.getLogger(__name__)


# ── Monthly reminder ───────────────────────────────────────────────────────────

def send_monthly_import_reminder() -> None:
    """
    Runs on the 1st of each month. Notifies the agency to do the monthly
    Ubersuggest AI Visibility CSV import for all active clients.
    """
    try:
        from . import notify
        from ..database import get_supabase

        clients = (
            get_supabase()
            .table("clients")
            .select("id, name")
            .eq("is_active", True)
            .execute()
        ).data or []

        if not clients:
            return

        names = ", ".join(c["name"] for c in clients[:5])
        if len(clients) > 5:
            names += f" e mais {len(clients) - 5}"

        notify.notify_agency(
            subject="[AI Visibility] Import mensal Ubersuggest",
            html_body=(
                f"<p>Olá! É o 1º do mês — hora de exportar o CSV do Ubersuggest AI Search Visibility "
                f"e importar para os clientes ativos ({names}).</p>"
                "<p>Acesse: <a href='/ai-visibility/import'>/ai-visibility/import</a></p>"
            ),
        )
        logger.info("Monthly AI Visibility reminder sent for %d clients", len(clients))
    except Exception as exc:
        logger.warning("Monthly AI Visibility reminder failed: %s", exc)


# ── Data collectors ────────────────────────────────────────────────────────────

def _collect_visibility_data(client_id: str) -> dict:
    sb = get_supabase()
    result: dict = {}

    # Latest monthly summary (current + previous month)
    summaries = (
        sb.table("ai_visibility_monthly_summary")
        .select("month, platform, mention_rate, avg_position, share_of_voice, positive_rate, visibility_index, total_prompts, total_mentioned")
        .eq("client_id", client_id)
        .order("month", desc=True)
        .limit(10)
        .execute()
    )
    result["monthly_summaries"] = summaries.data or []

    # Top performing prompts
    prompts = (
        sb.table("ai_visibility_metrics")
        .select("prompt_id, date, platform, mention_rate, position, sentiment")
        .eq("client_id", client_id)
        .order("date", desc=True)
        .limit(200)
        .execute()
    )
    result["recent_metrics"] = prompts.data or []

    # Prompt texts for context
    prompt_rows = (
        sb.table("ai_visibility_prompts")
        .select("id, prompt_text, category, intent")
        .eq("client_id", client_id)
        .execute()
    )
    prompt_map = {r["id"]: r for r in (prompt_rows.data or [])}
    result["prompts"] = prompt_map

    # Competitor mentions
    competitors = (
        sb.table("ai_visibility_competitor_mentions")
        .select("brand_name, date, platform, mention_count, share_of_voice")
        .eq("client_id", client_id)
        .order("date", desc=True)
        .limit(100)
        .execute()
    )
    result["competitors"] = competitors.data or []

    return result


def _collect_performance_data(client_id: str) -> dict:
    sb = get_supabase()
    result: dict = {}

    # Ad spend + revenue from cache
    cache = (
        sb.table("client_metrics_cache")
        .select("channel, orders, revenue, conversions, conversions_value, refreshed_at")
        .eq("client_id", client_id)
        .execute()
    )
    result["ad_metrics"] = cache.data or []

    # Recent orders count (last 30 days)
    since = (date.today() - timedelta(days=30)).isoformat()
    orders = (
        sb.table("orders")
        .select("id", count="exact", head=True)
        .eq("client_id", client_id)
        .gte("created_at", since)
        .execute()
    )
    result["orders_last_30d"] = orders.count or 0

    return result


def _collect_existing_insights_count(client_id: str) -> int:
    """Avoid re-running if we already have fresh ai_visibility insights today."""
    sb = get_supabase()
    today = date.today().isoformat()
    r = (
        sb.table("ai_insights")
        .select("id", count="exact", head=True)
        .eq("client_id", client_id)
        .eq("type", "ai_visibility")
        .gte("created_at", today)
        .execute()
    )
    return r.count or 0


# ── Prompt builder ─────────────────────────────────────────────────────────────

def _build_analysis_prompt(client_id: str, vis: dict, perf: dict) -> str:
    summaries = vis["monthly_summaries"]
    prompts   = vis["prompts"]
    metrics   = vis["recent_metrics"]
    comps     = vis["competitors"]
    ad_data   = perf["ad_metrics"]
    orders    = perf["orders_last_30d"]

    # Aggregate mention rate per prompt
    prompt_rates: dict[str, list[float]] = {}
    for m in metrics:
        pid = m["prompt_id"]
        if pid and m.get("mention_rate") is not None:
            prompt_rates.setdefault(pid, []).append(float(m["mention_rate"]))

    prompt_summary = []
    for pid, rates in prompt_rates.items():
        p = prompts.get(pid, {})
        avg = sum(rates) / len(rates)
        prompt_summary.append({
            "text":     p.get("prompt_text", pid)[:120],
            "category": p.get("category"),
            "intent":   p.get("intent"),
            "mention_rate": round(avg, 3),
            "runs": len(rates),
        })
    prompt_summary.sort(key=lambda x: x["mention_rate"])  # worst first

    # Summarize competitor data
    comp_totals: dict[str, float] = {}
    for c in comps:
        comp_totals[c["brand_name"]] = comp_totals.get(c["brand_name"], 0) + c.get("mention_count", 0)
    comp_list = sorted(comp_totals.items(), key=lambda x: x[1], reverse=True)[:5]

    # Monthly trend
    monthly_by_platform: dict[str, list] = {}
    for s in summaries:
        plat = s.get("platform", "all")
        monthly_by_platform.setdefault(plat, []).append(s)

    data_block = json.dumps({
        "monthly_summaries_last_5": summaries[:5],
        "prompt_performance_worst_to_best": prompt_summary[:15],
        "top_competitors": [{"brand": b, "total_mentions": n} for b, n in comp_list],
        "ad_channel_metrics": ad_data,
        "orders_last_30d": orders,
    }, ensure_ascii=False, indent=2)

    return f"""Você é um analista especialista em AI Search Visibility e marketing digital.

Analise os dados abaixo de visibilidade da marca em IA (ChatGPT, Gemini, Perplexity, Claude) cruzados com performance de anúncios e vendas.

<dados_cliente>
{data_block}
</dados_cliente>

Retorne SOMENTE um JSON válido com esta estrutura:
{{
  "insights": [
    {{
      "title": "Título curto e direto (max 80 chars)",
      "content": "Análise detalhada com contexto, causa provável e impacto estimado. Seja específico com números dos dados.",
      "severity": "info|warning|critical",
      "category": "trend|competitor|opportunity|alert",
      "suggested_action": "Ação concreta recomendada"
    }}
  ],
  "content_suggestions": [
    {{
      "working_title": "Título da pauta de conteúdo sugerida",
      "content_type": "guide|comparison|faq|use_case|pillar|glossary",
      "target_query": "Prompt/pergunta de AI que este conteúdo deve responder",
      "rationale": "Por que este conteúdo aumentaria a visibilidade (max 150 chars)",
      "priority": "high|medium|low",
      "intent": "high_intent|mid_intent|low_intent"
    }}
  ],
  "summary": "Resumo executivo em 2-3 frases do estado atual de AI Visibility."
}}

Regras:
- Gere entre 2-5 insights. Priorize os mais impactantes.
- Gere entre 1-3 sugestões de conteúdo apenas se houver oportunidade clara de melhoria.
- Se mention_rate < 0.3 em prompts de alta intenção, isso é crítico.
- Se um competidor tem share_of_voice > 2x o da marca, mencione isso como alert.
- Correlacione visibilidade em AI com vendas quando possível (ex: prompts de alta intenção com baixa menção = oportunidade perdida de receita).
- Seja direto, quantitativo e acionável. Não use jargão vazio."""


# ── Persistence ────────────────────────────────────────────────────────────────

def _save_insights(client_id: str, insights: list[dict]) -> int:
    if not insights:
        return 0
    sb = get_supabase()
    rows = []
    for ins in insights:
        rows.append({
            "client_id": client_id,
            "type":      "ai_visibility",
            "title":     ins.get("title", "AI Visibility Insight")[:200],
            "content":   ins.get("content", ""),
            "severity":  ins.get("severity", "info"),
            "data": {
                "category":         ins.get("category"),
                "suggested_action": ins.get("suggested_action"),
            },
        })
    sb.table("ai_insights").insert(rows).execute()
    return len(rows)


def _save_briefings(client_id: str, suggestions: list[dict]) -> int:
    if not suggestions:
        return 0
    sb = get_supabase()
    rows = []
    for s in suggestions:
        rows.append({
            "client_id":     client_id,
            "working_title": s.get("working_title", "Pauta sugerida por AI Visibility")[:300],
            "content_type":  s.get("content_type", "guide"),
            "target_query":  s.get("target_query", ""),
            "priority":      s.get("priority", "medium"),
            "source":        "ai_visibility",
            "source_data": {
                "rationale": s.get("rationale"),
                "intent":    s.get("intent"),
            },
            "status": "briefed",
        })
    sb.table("content_briefings").insert(rows).execute()
    return len(rows)


def _check_drop_alert(client_id: str, summaries: list[dict]) -> Optional[dict]:
    """Returns a critical insight dict if mention_rate dropped >20% vs prior month."""
    if len(summaries) < 2:
        return None
    # summaries are ordered desc by month; take 'all' platform aggregates
    agg = [s for s in summaries if s.get("platform") == "all" or s.get("platform") is None]
    if len(agg) < 2:
        # fall back to any platform
        agg = summaries
    if len(agg) < 2:
        return None
    cur  = float(agg[0].get("mention_rate") or 0)
    prev = float(agg[1].get("mention_rate") or 0)
    if prev == 0:
        return None
    drop = (prev - cur) / prev
    if drop >= 0.20:
        return {
            "title":   f"Queda de {drop*100:.0f}% na taxa de menção vs mês anterior",
            "content": (
                f"A taxa de menção caiu de {prev*100:.1f}% para {cur*100:.1f}% "
                f"({drop*100:.0f}% de redução). Isso pode indicar mudança no algoritmo "
                "dos LLMs, conteúdo desatualizado ou avanço de competidores. "
                "Revise os prompts com menor performance e atualize o conteúdo do site."
            ),
            "severity": "critical",
            "category": "alert",
            "suggested_action": "Auditar prompts com mention_rate < 30% e revisar conteúdo existente.",
        }
    return None


# ── Main entry point ───────────────────────────────────────────────────────────

def run_visibility_analysis(client_id: str, import_id: Optional[str] = None) -> dict:
    """
    Triggered after each CSV import. Collects data, runs Claude analysis,
    persists insights + suggested briefings.
    Returns summary dict with counts.
    """
    logger.info("AI Visibility analysis start client=%s import=%s", client_id, import_id)

    if not settings.ANTHROPIC_API_KEY:
        logger.warning("ANTHROPIC_API_KEY not set — skipping AI Visibility analysis")
        return {"skipped": True, "reason": "no_api_key"}

    try:
        vis  = _collect_visibility_data(client_id)
        perf = _collect_performance_data(client_id)
    except Exception as exc:
        logger.exception("Data collection failed: %s", exc)
        return {"error": str(exc)}

    # Rule-based drop alert (always, even without Claude)
    drop_alert = _check_drop_alert(client_id, vis["monthly_summaries"])

    # Claude analysis
    claude_insights:    list[dict] = []
    claude_suggestions: list[dict] = []
    summary_text = ""

    try:
        client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
        prompt = _build_analysis_prompt(client_id, vis, perf)
        msg = client.messages.create(
            model=settings.ANTHROPIC_MODEL,
            max_tokens=1500,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = msg.content[0].text.strip()
        # Strip markdown fences if present
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        parsed = json.loads(raw)
        claude_insights    = parsed.get("insights", [])
        claude_suggestions = parsed.get("content_suggestions", [])
        summary_text       = parsed.get("summary", "")
    except Exception as exc:
        logger.warning("Claude analysis failed: %s", exc)

    # Merge rule-based alert with Claude insights
    all_insights = []
    if drop_alert:
        all_insights.append(drop_alert)
    all_insights.extend(claude_insights)

    n_insights   = _save_insights(client_id, all_insights)
    n_briefings  = _save_briefings(client_id, claude_suggestions)

    # Persist summary as a single top-level insight if Claude returned one
    if summary_text:
        try:
            get_supabase().table("ai_insights").insert({
                "client_id": client_id,
                "type":      "ai_visibility",
                "title":     "Resumo AI Visibility",
                "content":   summary_text,
                "severity":  "info",
                "data":      {"category": "summary", "import_id": import_id},
            }).execute()
            n_insights += 1
        except Exception as exc:
            logger.warning("Failed to save summary insight: %s", exc)

    logger.info(
        "AI Visibility analysis done client=%s insights=%d briefings=%d",
        client_id, n_insights, n_briefings,
    )
    return {
        "insights_created":   n_insights,
        "briefings_created":  n_briefings,
        "drop_alert":         drop_alert is not None,
    }
