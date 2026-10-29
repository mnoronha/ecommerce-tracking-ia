"""
Hermes Analysis Layer for canonical H7/H8 reports.

Receives artifact data (metrics, change_summary, anomalies, previous week snapshot)
and generates structured analysis via Claude. Includes:
  - Anomaly detection (pure logic, no external deps)
  - WoW comparison builder (pure logic)
  - Claude prompt construction + API call
  - Quality gate (pure logic)

Design rules:
- Never makes media writes, platform API calls, or client sends.
- Claude call is the only external dependency (gated by CANONICAL_ANALYSIS_ENABLED env var).
- On Claude failure: returns structured fallback with limitations; never raises.
- Quality gate is pure logic; no Claude dependency.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

ANALYSIS_ENGINE_VERSION = "1"
FLAG_CANONICAL_ANALYSIS  = "CANONICAL_ANALYSIS_ENABLED"

# Recommendations containing any of these phrases (case-insensitive) are blocked.
_GENERIC_PHRASES = frozenset({
    "otimizar campanhas", "melhorar criativos", "acompanhar resultados",
    "monitorar performance", "continuar acompanhando", "analisar dados",
    "melhorar o desempenho", "otimizar anúncios", "melhorar performance",
})


# ── Anomaly Detection ─────────────────────────────────────────────────────────

def detect_anomalies(metrics_dict: dict, channels: dict) -> list[dict]:
    """Pure logic anomaly detection — no Claude, no network.

    Returns list of dicts with type / severity / description / implication.
    """
    anomalies: list[dict] = []

    def _val(key: str) -> Optional[float]:
        return (metrics_dict.get(key) or {}).get("value")

    ga4_sessions  = _val("ga4_sessions")
    ga4_purchases = _val("ga4_purchases")
    g_total       = _val("google_conversions")
    g_purchase    = _val("google_purchase_conversions")
    rev_biz       = _val("revenue_business")
    ga4_rev       = _val("ga4_revenue")
    cpa_blended   = _val("cpa_google")
    cpa_ecom      = _val("google_cpa_ecommerce")
    orders        = _val("orders_count")

    # GA4 conversion rate anomaly
    if ga4_sessions and ga4_purchases and ga4_sessions > 0:
        cvr = ga4_purchases / ga4_sessions
        if cvr < 0.0005:
            anomalies.append({
                "type": "GA4_LOW_CVR",
                "severity": "HIGH",
                "description": (
                    f"GA4 CVR {cvr * 100:.4f}% "
                    f"({ga4_sessions:,.0f} sessões / {ga4_purchases:,.0f} compras)"
                ),
                "implication": (
                    "CVR muito abaixo de 1–3% esperado para e-commerce. "
                    "Investigar bot traffic, sessões duplicadas ou misconfiguration GA4 "
                    "antes de usar sessões como denominador de qualquer taxa."
                ),
            })

    # Google purchase vs non-purchase gap
    if g_total is not None and g_purchase is not None and g_purchase >= 0:
        non_purchase = g_total - g_purchase
        ratio = non_purchase / g_purchase if g_purchase > 0 else float("inf")
        if ratio > 5:
            anomalies.append({
                "type": "GOOGLE_NON_PURCHASE_MAJORITY",
                "severity": "HIGH",
                "description": (
                    f"Google: {non_purchase:.1f} conversões não-compra vs "
                    f"{g_purchase:.1f} purchase (ratio {ratio:.1f}x)"
                ),
                "implication": (
                    "CPA blended (cpa_google) inflado por micro-conversões de baixo valor. "
                    "Usar CPA ecommerce e purchase conversions como KPIs primários."
                ),
            })
        elif ratio > 2:
            anomalies.append({
                "type": "GOOGLE_HIGH_NON_PURCHASE_RATIO",
                "severity": "MEDIUM",
                "description": (
                    f"Google: {non_purchase:.1f} non-purchase vs {g_purchase:.1f} purchase "
                    f"(ratio {ratio:.1f}x)"
                ),
                "implication": (
                    "CPA blended subestima custo por venda. "
                    "Diferenciar CPA blended de CPA ecommerce nos relatórios."
                ),
            })

    # Business truth vs GA4 revenue gap
    if rev_biz and ga4_rev and rev_biz > 0:
        gap_pct = abs(rev_biz - ga4_rev) / rev_biz * 100
        if gap_pct > 30:
            anomalies.append({
                "type": "GA4_BUSINESS_REVENUE_GAP",
                "severity": "HIGH",
                "description": (
                    f"GA4 R${ga4_rev:,.2f} vs business truth R${rev_biz:,.2f} "
                    f"(gap {gap_pct:.1f}%)"
                ),
                "implication": (
                    "GA4 underreports significativamente. Usar business truth como referência. "
                    "Causas prováveis: pedidos por canais alternativos (WhatsApp, offline), "
                    "janela de atribuição ou tracking gap."
                ),
            })
        elif gap_pct > 15:
            anomalies.append({
                "type": "GA4_BUSINESS_REVENUE_GAP_MEDIUM",
                "severity": "MEDIUM",
                "description": (
                    f"GA4 R${ga4_rev:,.2f} vs business truth R${rev_biz:,.2f} "
                    f"(gap {gap_pct:.1f}%)"
                ),
                "implication": "Divergência acima do típico. Monitorar tendência.",
            })

    # CPA blended vs ecommerce divergence
    if cpa_blended and cpa_ecom and cpa_blended > 0:
        ratio = cpa_ecom / cpa_blended
        if ratio > 4:
            anomalies.append({
                "type": "CPA_BLENDED_VS_ECOMMERCE_DIVERGENCE",
                "severity": "HIGH",
                "description": (
                    f"CPA blended R${cpa_blended:.2f} vs CPA ecommerce R${cpa_ecom:.2f} "
                    f"(ratio {ratio:.1f}x)"
                ),
                "implication": (
                    "CPA blended não representa custo por venda. "
                    "Usar exclusivamente CPA ecommerce para decisões de bid e budget."
                ),
            })

    # Orders vs GA4 purchases gap
    if orders and ga4_purchases and orders > 0:
        gap_pct = abs(orders - ga4_purchases) / orders * 100
        if gap_pct > 30:
            anomalies.append({
                "type": "ORDERS_GA4_PURCHASES_GAP",
                "severity": "MEDIUM",
                "description": (
                    f"Business orders {orders:,.0f} vs GA4 purchases {ga4_purchases:,.0f} "
                    f"(gap {gap_pct:.1f}%)"
                ),
                "implication": (
                    "GA4 não captura todos os pedidos. "
                    "Pedidos por WhatsApp, telefone ou canais offline provavelmente excluídos."
                ),
            })

    return anomalies


# ── WoW Comparison ────────────────────────────────────────────────────────────

_WOW_KEYS = (
    "meta_spend", "meta_conversions", "meta_conversion_value", "roas_meta", "cpa_meta",
    "google_spend", "google_purchase_conversions", "google_purchase_conversion_value",
    "google_roas_ecommerce", "google_cpa_ecommerce",
    "total_spend", "revenue_business", "orders_count", "mer",
)


def build_wow(current_metrics: dict, prev_snapshot: Optional[dict]) -> dict:
    """Build WoW comparison dict. Pure logic, no network."""
    if not prev_snapshot:
        return {"available": False, "note": "Snapshot da semana anterior não encontrado"}

    prev_raw = prev_snapshot.get("metrics") or []
    if isinstance(prev_raw, dict):
        prev_raw = list(prev_raw.values())
    prev_dict = {
        m["metric_key"]: m
        for m in prev_raw
        if isinstance(m, dict) and "metric_key" in m
    }

    deltas: dict = {}
    for k in _WOW_KEYS:
        curr_v = (current_metrics.get(k) or {}).get("value")
        prev_v = (prev_dict.get(k) or {}).get("value")
        if curr_v is not None and prev_v is not None and prev_v != 0:
            delta_pct = (curr_v - prev_v) / abs(prev_v) * 100
            deltas[k] = {
                "current":    curr_v,
                "previous":   prev_v,
                "delta_pct":  round(delta_pct, 1),
                "direction":  "up" if delta_pct > 0 else "down",
            }

    return {"available": True, "deltas": deltas}


# ── Prompt Builder ────────────────────────────────────────────────────────────

def _build_prompt(
    client_id: str,
    period_start: str,
    period_end: str,
    metrics_dict: dict,
    channels: dict,
    business_truth: str,
    change_summary: dict,
    anomalies: list[dict],
    wow: dict,
    context: Optional[dict] = None,
    period_type: str = "weekly",
) -> str:
    """Build the Hermes analysis prompt (weekly or monthly)."""
    is_monthly   = period_type == "monthly"
    period_label = "mês"    if is_monthly else "semana"
    period_adj   = "mensal" if is_monthly else "semanal"
    comp_label   = "mês anterior"    if is_monthly else "semana anterior"
    comp_tag     = "MoM"             if is_monthly else "WoW"

    def _val(key: str) -> Optional[float]:
        return (metrics_dict.get(key) or {}).get("value")

    def _fmt(v: Optional[float], unit: str = "", precision: int = 2) -> str:
        if v is None:
            return "indisponível"
        if unit == "BRL":
            return f"R${v:,.{precision}f}"
        if unit == "x":
            return f"{v:.{precision}f}x"
        return f"{v:,.{precision}f}"

    def _wow_tag(key: str) -> str:
        if not wow.get("available"):
            return ""
        d = wow.get("deltas", {}).get(key)
        if not d:
            return ""
        arrow = "▲" if d["direction"] == "up" else "▼"
        return f" [{arrow}{abs(d['delta_pct']):.1f}% vs {comp_label}]"

    # Change digest section
    change_lines: list[str] = []
    for platform, cs in change_summary.items():
        if not isinstance(cs, dict) or cs.get("error"):
            continue
        for chg in (cs.get("main_changes") or [])[:5]:
            change_lines.append(f"  [{platform.upper()}] {chg}")
        for b in (cs.get("budget_changes") or []):
            entity = b.get("entity") or ""
            before = b.get("before_display") or "—"
            after  = b.get("after_display")  or "—"
            change_lines.append(f"  [{platform.upper()}] Budget '{entity}': {before} → {after}")
    change_text = (
        "\n".join(change_lines)
        if change_lines
        else "  Nenhuma mudança relevante detectada via Change Digest."
    )

    # Anomaly section
    anomaly_lines = [
        f"  [{a['severity']}] {a['type']}: {a['description']} — {a['implication']}"
        for a in anomalies
    ]
    anomaly_text = "\n".join(anomaly_lines) if anomaly_lines else "  Nenhuma anomalia detectada."

    # Operational context section
    ctx_lines: list[str] = []
    if context:
        targets = context.get("targets") or {}
        key_tgts = [(k, v) for k, v in targets.items()
                    if k in ("revenue_business", "mer", "total_spend")]
        if key_tgts:
            ts = [f"{k}: {v.get('target')} {v.get('unit','')}" for k, v in key_tgts]
            ctx_lines.append(f"  Metas: {', '.join(ts)}")

        alerts = context.get("active_alerts") or []
        if alerts:
            high_c = sum(1 for a in alerts if (a.get("severity") or "").lower() == "high")
            ctx_lines.append(f"  Alertas ativos: {len(alerts)} ({high_c} HIGH)")
            for a in alerts[:3]:
                ctx_lines.append(f"  [{(a.get('severity') or '?').upper()}] {a.get('title','')}")

        manual = context.get("manual_changes") or []
        if manual:
            ctx_lines.append(f"  Mudanças manuais no período: {len(manual)}")
            for mc in manual[:3]:
                ctx_lines.append(
                    f"  - {mc.get('change_type','')} em '{mc.get('entity_name_at_time','')}': "
                    f"{(mc.get('reason') or '')[:80]}"
                )

        executed = context.get("executed_actions") or []
        if executed:
            ctx_lines.append(f"  Acoes executadas no periodo: {len(executed)}")
            for ea in executed[:2]:
                ctx_lines.append(f"  - {ea.get('action_type','')}: {(ea.get('description') or '')[:80]}")

        open_recs = context.get("open_recommendations") or []
        if open_recs:
            ctx_lines.append(f"  Recomendacoes abertas nao executadas: {len(open_recs)}")

    context_text = "\n".join(ctx_lines) if ctx_lines else "  Sem contexto operacional adicional disponivel."

    has_google = "google" in channels
    has_meta   = "meta"   in channels

    # Monthly adds a business_result section to the output schema
    monthly_schema_extra = ""
    if is_monthly:
        monthly_schema_extra = """
  "business_result": {
    "vs_previous_period": "performance vs mes anterior com dados especificos",
    "target_achievement": "atingimento de metas mensais (receita, MER, etc.)",
    "key_drivers": ["principal driver de resultado com dado", "segundo driver"]
  },"""

    return f"""Voce e Hermes, motor de analise de midia interna da Noro Labs.
Gere analise {period_adj} de performance de midia digital para a equipe da agencia.
Isto e analise interna — nao e relatorio para cliente.

DADOS DO PERIODO ({period_label}):
  Cliente: {client_id}
  Periodo: {period_start} a {period_end} ({period_label} fechado)
  Business Truth: {business_truth}

METRICAS META ADS:{"" if has_meta else " [Meta nao contratado]"}
  Spend:             {_fmt(_val("meta_spend"), "BRL")}{_wow_tag("meta_spend")}
  Conversoes:        {_fmt(_val("meta_conversions"))}{_wow_tag("meta_conversions")}
  Valor convertido:  {_fmt(_val("meta_conversion_value"), "BRL")}{_wow_tag("meta_conversion_value")}
  ROAS:              {_fmt(_val("roas_meta"), "x")}{_wow_tag("roas_meta")}
  CPA:               {_fmt(_val("cpa_meta"), "BRL")}{_wow_tag("cpa_meta")}

METRICAS GOOGLE ADS:{"" if has_google else " [Google nao contratado]"}
  Spend:              {_fmt(_val("google_spend"), "BRL")}{_wow_tag("google_spend")}
  Conversoes totais:  {_fmt(_val("google_conversions"))}
  Purchase conv.:     {_fmt(_val("google_purchase_conversions"))}{_wow_tag("google_purchase_conversions")}
  Purchase value:     {_fmt(_val("google_purchase_conversion_value"), "BRL")}{_wow_tag("google_purchase_conversion_value")}
  ROAS ecommerce:     {_fmt(_val("google_roas_ecommerce"), "x")}{_wow_tag("google_roas_ecommerce")}
  CPA ecommerce:      {_fmt(_val("google_cpa_ecommerce"), "BRL")}{_wow_tag("google_cpa_ecommerce")}

NEGOCIO:
  Receita business:  {_fmt(_val("revenue_business"), "BRL")}{_wow_tag("revenue_business")}
  Pedidos:           {_fmt(_val("orders_count"))}{_wow_tag("orders_count")}
  MER:               {_fmt(_val("mer"), "x")}{_wow_tag("mer")}
  Spend total:       {_fmt(_val("total_spend"), "BRL")}{_wow_tag("total_spend")}
  GA4 sessoes:       {_fmt(_val("ga4_sessions"))}{_wow_tag("ga4_sessions")}
  GA4 compras:       {_fmt(_val("ga4_purchases"))}{_wow_tag("ga4_purchases")}
  GA4 receita:       {_fmt(_val("ga4_revenue"), "BRL")}{_wow_tag("ga4_revenue")}

COMPARACAO {comp_tag} (vs {comp_label}):
  {"Disponivel — tags [A/V X%] indicadas nas metricas acima" if wow.get("available") else "Indisponivel — snapshot do periodo anterior nao encontrado"}

MUDANCAS DO PERIODO (Change Digest):
{change_text}

ANOMALIAS DETECTADAS AUTOMATICAMENTE:
{anomaly_text}

CONTEXTO OPERACIONAL:
{context_text}

REGRAS OBRIGATORIAS:
1. Business Truth > platform attribution. Revenue business e a fonte de verdade.
2. Nunca use conversoes blended Google como KPI principal se purchase separado disponivel.
3. Inclua comparacao {comp_tag} quando tags [A/V X%] estiverem presentes.
4. Nunca afirme causalidade de mudancas sem evidencia. Use "associacao temporal observada".
5. Recomendacoes PROIBIDAS (causam rejeicao): "otimizar campanhas", "melhorar criativos",
   "acompanhar resultados", "monitorar performance". Cada acao deve ser concreta e executavel.
6. Diagnosis: MINIMO 3 sinais. Formato: OBSERVATION -> DIAGNOSIS -> RECOMMENDED ACTION.
7. Recommendations: MINIMO 2, MAXIMO 5. Cada uma com evidencia + acao especifica.
8. Se campo for indisponivel, mencione na limitacao. Nao invente dados.
9. Se alertas ativos: enderece obrigatoriamente em diagnosis ou recommendations.
10. Se mudancas manuais registradas: relacione com variacoes de performance observadas.

FORMATO DE SAIDA — JSON ESTRITO (sem texto antes ou depois do JSON):
{{{monthly_schema_extra}
  "executive_summary": {{
    "bullets": ["bullet1 com numero especifico", "bullet2", "bullet3", "bullet4"],
    "key_win": "principal resultado positivo com dado",
    "key_risk": "principal risco ou ponto de atencao com dado"
  }},
  "google_analysis": {{
    "spend_context": "analise do spend com {comp_tag} quando disponivel",
    "efficiency_assessment": "avaliacao de ROAS ecommerce e CPA ecommerce com interpretacao",
    "conversion_structure": "breakdown purchase vs non-purchase com implicacao para decisao",
    "observations": ["obs especifica 1 com dado", "obs 2", "obs 3"]
  }},
  "meta_analysis": {{
    "spend_context": "analise do spend Meta com {comp_tag}",
    "efficiency_assessment": "avaliacao de ROAS e CPA com interpretacao",
    "audience_context": "prospeccao vs remarketing quando aplicavel a estrutura do cliente",
    "change_digest_integration": "mudancas relevantes do periodo e impacto potencial observado",
    "observations": ["obs especifica 1 com dado", "obs 2", "obs 3"]
  }},
  "diagnosis": [
    {{
      "observation": "dado especifico observado",
      "diagnosis": "interpretacao e causa provavel",
      "recommended_action": "acao concreta e executavel",
      "impact": "high|medium|low",
      "confidence": "HIGH|MEDIUM|LOW"
    }}
  ],
  "recommendations": [
    {{
      "problem_or_opportunity": "problema ou oportunidade com dado especifico",
      "evidence": "metrica ou evento que suporta",
      "action": "acao concreta com detalhe suficiente para execucao imediata",
      "priority": "P0|P1|P2",
      "expected_effect": "efeito esperado com estimativa quando possivel",
      "risk": "risco ou contra-indicacao",
      "confidence": "HIGH|MEDIUM|LOW"
    }}
  ],
  "change_digest_section": {{
    "main_changes_narrative": "narrativa PT-BR das principais mudancas com datas",
    "temporal_associations": ["mudanca X em DD/MM: metrica Y mudou Z (associacao temporal; causalidade nao comprovada)"],
    "pending_monitoring": ["o que observar nos proximos {period_label}s"]
  }},
  "priorities_next_week": [
    {{
      "action": "acao prioritaria especifica",
      "owner": "quem executa",
      "deadline_suggestion": "sugestao de prazo",
      "rationale": "por que este {period_label}"
    }}
  ],
  "analysis_notes": "limitacoes ou ressalvas sobre os dados usados nesta analise"
}}"""


# ── Claude Call ───────────────────────────────────────────────────────────────

def _call_claude(prompt: str) -> dict:
    """Make the Anthropic API call. Returns parsed JSON dict or raises."""
    import anthropic  # lazy import — only needed when analysis enabled

    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY not set")

    client   = anthropic.Anthropic(api_key=api_key)
    response = client.messages.create(
        model      = "claude-sonnet-4-6",
        max_tokens = 4096,
        messages   = [{"role": "user", "content": prompt}],
    )
    raw = response.content[0].text.strip()

    # Strip markdown fences if present
    if raw.startswith("```"):
        raw = raw.split("```", 2)[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.rsplit("```", 1)[0]

    return json.loads(raw)


def _fallback(reason: str, limitations: list[str]) -> dict:
    return {
        "status":    "ANALYSIS_UNAVAILABLE",
        "reason":    reason,
        "limitations": limitations,
        "executive_summary": {"bullets": [], "key_win": None, "key_risk": None},
        "google_analysis":   None,
        "meta_analysis":     None,
        "diagnosis":         [],
        "recommendations":   [],
        "change_digest_section": None,
        "priorities_next_week":  [],
        "analysis_notes":    reason,
    }


def build_analysis_layer(
    client_id: str,
    period_start: str,
    period_end: str,
    metrics_dict: dict,
    channels: dict,
    business_truth: str,
    change_summary: dict,
    limitations: list[str],
    prev_snapshot: Optional[dict],
    context: Optional[dict] = None,
    period_type: str = "weekly",
) -> dict:
    """
    Build analysis layer. Returns structured dict — never raises.

    When CANONICAL_ANALYSIS_ENABLED=false: returns fallback with status=ANALYSIS_DISABLED.
    Default is true — set CANONICAL_ANALYSIS_ENABLED=false explicitly for dry-runs/tests.

    When CANONICAL_ANALYSIS_ENABLED=true: calls Claude and validates output.
    On any failure: returns fallback with status=ANALYSIS_UNAVAILABLE.
    """
    enabled = os.getenv(FLAG_CANONICAL_ANALYSIS, "true").strip().lower() == "true"
    if not enabled:
        return _fallback("ANALYSIS_DISABLED", ["CANONICAL_ANALYSIS_ENABLED=false"])

    anomalies = detect_anomalies(metrics_dict, channels)
    wow       = build_wow(metrics_dict, prev_snapshot)

    try:
        prompt = _build_prompt(
            client_id, period_start, period_end,
            metrics_dict, channels, business_truth,
            change_summary, anomalies, wow,
            context=context, period_type=period_type,
        )
        raw = _call_claude(prompt)
    except Exception as exc:
        logger.warning("analysis_layer: Claude call failed for %s: %s", client_id, exc)
        return _fallback(
            f"CLAUDE_CALL_FAILED: {str(exc)[:200]}",
            [f"analysis: {str(exc)[:120]}"],
        )

    # Validate minimum required fields
    try:
        _validate_analysis(raw)
    except ValueError as exc:
        logger.warning("analysis_layer: validation failed for %s: %s", client_id, exc)
        return _fallback(
            f"ANALYSIS_VALIDATION_FAILED: {str(exc)[:200]}",
            [f"analysis validation: {str(exc)[:120]}"],
        )

    raw["anomalies_detected"] = anomalies
    raw["wow_comparison"]     = wow
    raw["analysis_engine"]    = "claude-sonnet-4-6"
    raw["engine_version"]     = ANALYSIS_ENGINE_VERSION
    raw["status"]             = "ANALYSIS_COMPLETE"
    return raw


# ── Validation ────────────────────────────────────────────────────────────────

def _validate_analysis(raw: dict) -> None:
    """Raise ValueError if analysis is structurally invalid."""
    if not isinstance(raw, dict):
        raise ValueError("response is not a dict")

    es = raw.get("executive_summary") or {}
    bullets = es.get("bullets") or []
    if not isinstance(bullets, list) or len(bullets) < 2:
        raise ValueError(f"executive_summary.bullets has {len(bullets)} items (min 2)")

    recs = raw.get("recommendations") or []
    if not isinstance(recs, list) or len(recs) < 1:
        raise ValueError("recommendations list is empty")

    diag = raw.get("diagnosis") or []
    if not isinstance(diag, list) or len(diag) < 1:
        raise ValueError("diagnosis list is empty")

    # Block generic recommendations
    for r in recs:
        action_text = (r.get("action") or "").lower()
        for phrase in _GENERIC_PHRASES:
            if phrase in action_text:
                raise ValueError(
                    f"generic recommendation blocked: '{phrase}' in '{action_text[:80]}'"
                )


# ── Quality Scores ────────────────────────────────────────────────────────────

def compute_quality_scores(content: dict) -> dict:
    """
    Compute DATA_QUALITY (0-100) and ANALYSIS_QUALITY (0-10) as independent scores.

    DATA_QUALITY: presence and health of factual metrics data.
    ANALYSIS_QUALITY: depth, structure, and specificity of the analytical layer.
    A report with 100% data but no analysis = data_quality=95, analysis_quality=0.
    """
    # DATA QUALITY
    facts       = content.get("facts") or []
    source_obs  = content.get("observations") or []
    ready_count = sum(1 for s in source_obs if s.get("source_state") == "READY")
    total_src   = len(source_obs)

    dq = 0
    if facts:
        dq += 50
        if len(facts) >= 15:
            dq += 10
    if total_src > 0:
        dq += round(ready_count / total_src * 25)
    bt = (content.get("business_truth") or "").lower()
    if bt and "no snapshot" not in bt and "sem snapshot" not in bt:
        dq += 10
    if content.get("change_summary"):
        dq += 3
    if content.get("context_manifest"):
        dq += 2
    dq = min(dq, 100)
    dq_label = (
        "COMPLETE" if dq >= 85
        else "GOOD"    if dq >= 70
        else "PARTIAL" if dq >= 50
        else "BLOCKED"
    )

    # ANALYSIS QUALITY
    analysis = content.get("analysis_layer") or {}
    status   = analysis.get("status", "")
    if status != "ANALYSIS_COMPLETE":
        return {
            "data_quality":     dq,
            "data_label":       dq_label,
            "analysis_quality": 0.0,
            "analysis_label":   "NO_ANALYSIS",
            "analysis_status":  status or "ABSENT",
        }

    aq = 0.0
    es      = analysis.get("executive_summary") or {}
    bullets = es.get("bullets") or []
    aq += min(len(bullets), 4) * 0.4
    if es.get("key_win"):  aq += 0.2
    if es.get("key_risk"): aq += 0.2

    if analysis.get("google_analysis"): aq += 1.5
    if analysis.get("meta_analysis"):   aq += 1.5
    if analysis.get("business_result"): aq += 1.0  # monthly only

    diag = analysis.get("diagnosis") or []
    aq += min(len(diag), 3) * 0.5

    recs = analysis.get("recommendations") or []
    aq += min(len(recs), 3) * 0.5

    if analysis.get("change_digest_section"): aq += 0.5

    prios = analysis.get("priorities_next_week") or []
    aq += min(len(prios), 2) * 0.25

    aq = min(round(aq, 1), 10.0)
    aq_label = (
        "EXCELLENT" if aq >= 9.0
        else "GOOD"    if aq >= 7.0
        else "PARTIAL" if aq >= 4.0
        else "MINIMAL"
    )
    return {
        "data_quality":     dq,
        "data_label":       dq_label,
        "analysis_quality": aq,
        "analysis_label":   aq_label,
        "analysis_status":  status,
    }


def build_monthly_analysis_layer(
    client_id: str,
    period_start: str,
    period_end: str,
    metrics_dict: dict,
    channels: dict,
    business_truth: str,
    change_summary: dict,
    limitations: list[str],
    prev_month_snapshot: Optional[dict],
    context: Optional[dict] = None,
) -> dict:
    """H8 monthly analysis — same engine as H7, monthly period type with MoM comparison."""
    return build_analysis_layer(
        client_id      = client_id,
        period_start   = period_start,
        period_end     = period_end,
        metrics_dict   = metrics_dict,
        channels       = channels,
        business_truth = business_truth,
        change_summary = change_summary,
        limitations    = limitations,
        prev_snapshot  = prev_month_snapshot,
        context        = context,
        period_type    = "monthly",
    )


# ── Quality Gate ──────────────────────────────────────────────────────────────

def check_quality_gate(content: dict, analysis_enabled: bool = True) -> tuple[bool, list[str]]:
    """
    Pure logic quality gate. Returns (passes, violations).

    Always checks data layer (facts must be present).
    When analysis_enabled=False: passes after data check — no analysis required.
    When analysis_enabled=True: full gate including analysis completeness.
    """
    violations: list[str] = []

    # DATA LAYER — always checked
    facts = content.get("facts") or []
    if not facts:
        violations.append("facts is empty — no metrics data to report")
        return False, violations  # Short-circuit: can't gate-pass without data

    if not analysis_enabled:
        return True, []

    analysis = content.get("analysis_layer") or {}
    channels = content.get("channels") or {}

    # Executive summary
    es      = analysis.get("executive_summary") or {}
    bullets = es.get("bullets") or []
    if len(bullets) < 2:
        violations.append(
            f"executive_summary.bullets has {len(bullets)} items (min 2)"
        )

    # Recommendations
    recs = analysis.get("recommendations") or []
    if len(recs) < 1:
        violations.append("recommendations is empty")
    else:
        for r in recs:
            action_text = (r.get("action") or "").lower()
            for phrase in _GENERIC_PHRASES:
                if phrase in action_text:
                    violations.append(f"generic recommendation: '{phrase}'")
                    break

    # Diagnosis
    diag = analysis.get("diagnosis") or []
    if len(diag) < 1:
        violations.append("diagnosis is empty")

    # Channel coverage
    if channels.get("google") and not analysis.get("google_analysis"):
        violations.append("google channel active but google_analysis absent")
    if channels.get("meta") and not analysis.get("meta_analysis"):
        violations.append("meta channel active but meta_analysis absent")

    # Change digest consumed
    change_summary = content.get("change_summary") or {}
    has_relevant_changes = any(
        isinstance(cs, dict) and cs.get("relevant_changes", 0) > 0
        for cs in change_summary.values()
    )
    if has_relevant_changes and not analysis.get("change_digest_section"):
        violations.append("change_digest applicable but change_digest_section absent")

    # Analysis status must not be UNAVAILABLE or DISABLED
    status = analysis.get("status", "")
    if status in ("ANALYSIS_UNAVAILABLE", "ANALYSIS_DISABLED", ""):
        violations.append(f"analysis_layer.status={status!r} — analysis did not complete")

    return len(violations) == 0, violations
