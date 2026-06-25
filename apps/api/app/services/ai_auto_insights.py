"""
Auto Insights — gerador de insights baseado em regras, sem dependência de Claude.

Roda diariamente como fallback (quando não há créditos Claude) ou em paralelo.
Detecta 5 tipos de anomalia com dados reais do Supabase.
"""

import logging
from datetime import datetime, timedelta, timezone

from ..database import get_supabase

logger = logging.getLogger(__name__)

_OFFLINE_SOURCE_NAMES = {"pos", "in_store", "offline", "draft_order", "draft"}


# ── Helpers ────────────────────────────────────────────────────────────────────

def _was_rule_recently_saved(sb, client_uuid: str, rule_key: str, hours: int = 20) -> bool:
    try:
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
        result = (
            sb.table("ai_insights")
            .select("id", count="exact", head=True)
            .eq("client_id", client_uuid)
            .gte("created_at", cutoff)
            .contains("data", {"rule_key": rule_key})
            .execute()
        )
        return (result.count or 0) > 0
    except Exception:
        return False


def _save_rule_insight(sb, client_uuid: str, ins: dict) -> bool:
    rule_key = ins.get("data", {}).get("rule_key", "")
    if rule_key and _was_rule_recently_saved(sb, client_uuid, rule_key):
        logger.debug("rule %s already saved for %s — skipping", rule_key, client_uuid)
        return False
    try:
        sb.table("ai_insights").insert({
            "client_id": client_uuid,
            "type":      ins["type"],
            "severity":  ins["severity"],
            "title":     ins["title"],
            "content":   ins["content"],
            "data":      ins.get("data", {}),
        }).execute()
        return True
    except Exception as exc:
        logger.error("Failed to save rule insight: %s", exc)
        return False


def _online_orders(sb, client_uuid: str, start: str, end: str | None = None) -> list[dict]:
    q = (
        sb.table("orders")
        .select("total_price, utm_source, source_name, capi_sent, google_match_type, created_at, is_first_purchase")
        .eq("client_id", client_uuid)
        .eq("financial_status", "paid")
        .gt("total_price", 0)
        .gte("created_at", start)
    )
    if end:
        q = q.lt("created_at", end)
    rows = q.execute()
    return [
        r for r in (rows.data or [])
        if str(r.get("source_name") or "").lower() not in _OFFLINE_SOURCE_NAMES
    ]


# ── Rule detectors ─────────────────────────────────────────────────────────────

def _check_revenue_anomaly(sb, client_uuid: str, now: datetime) -> dict | None:
    """Receita de ontem < 50% da mediana dos 7 dias anteriores."""
    yesterday_start = (now - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    yesterday_end   = yesterday_start + timedelta(days=1)
    week_start      = yesterday_start - timedelta(days=7)

    yday = _online_orders(sb, client_uuid, yesterday_start.isoformat(), yesterday_end.isoformat())
    week = _online_orders(sb, client_uuid, week_start.isoformat(), yesterday_start.isoformat())

    yday_rev = sum(o.get("total_price", 0) or 0 for o in yday)

    daily_rev: dict[str, float] = {}
    for o in week:
        day = (o.get("created_at") or "")[:10]
        daily_rev[day] = daily_rev.get(day, 0) + (o.get("total_price", 0) or 0)

    if len(daily_rev) < 3:
        return None

    vals = sorted(daily_rev.values())
    median = vals[len(vals) // 2]
    if median <= 0:
        return None

    ratio = yday_rev / median
    if ratio >= 0.55:
        return None

    severity  = "critical" if ratio < 0.30 else "warning"
    pct_drop  = round((1 - ratio) * 100)
    date_str  = yesterday_start.strftime("%d/%m")

    return {
        "type": "anomaly",
        "severity": severity,
        "title": f"Receita {pct_drop}% abaixo da média em {date_str}",
        "content": (
            f"Em {date_str}, a receita online foi R$ {yday_rev:,.0f} — {pct_drop}% abaixo da "
            f"mediana dos 7 dias anteriores (R$ {median:,.0f}). Pode indicar campanhas pausadas, "
            f"falha no checkout ou queda de tráfego pago."
        ),
        "data": {
            "rule_key": "revenue_anomaly",
            "recommendation": "Verifique se as campanhas estão ativas e se o pixel está disparando no checkout.",
            "yesterday_revenue": yday_rev,
            "median_7d": round(median, 2),
            "ratio": round(ratio, 2),
        },
    }


def _check_capi_coverage(sb, client_uuid: str, now: datetime) -> dict | None:
    """Menos de 85% dos pedidos com capi_sent=true nos últimos 7 dias."""
    start  = (now - timedelta(days=7)).isoformat()
    orders = _online_orders(sb, client_uuid, start)
    total  = len(orders)
    if total < 5:
        return None

    sent     = sum(1 for o in orders if o.get("capi_sent") is True)
    coverage = sent / total
    if coverage >= 0.85:
        return None

    severity = "critical" if coverage < 0.70 else "warning"
    pct      = round(coverage * 100)
    missed   = total - sent

    return {
        "type": "anomaly",
        "severity": severity,
        "title": f"Cobertura CAPI em {pct}% — {missed} pedido{'s' if missed != 1 else ''} sem envio",
        "content": (
            f"Nos últimos 7 dias, apenas {pct}% dos pedidos foram enviados via CAPI ao Meta "
            f"({missed} de {total} sem confirmação de envio). Isso reduz a qualidade de atribuição "
            f"e prejudica o aprendizado das campanhas."
        ),
        "data": {
            "rule_key": "capi_coverage",
            "recommendation": "Acesse Configurações → Integrações e verifique se o Meta CAPI está ativo.",
            "coverage_pct": pct,
            "total_orders": total,
            "sent_orders": sent,
        },
    }


def _check_gclid_coverage(sb, client_uuid: str, now: datetime) -> dict | None:
    """Menos de 40% dos pedidos Google com gclid capturado nos últimos 7 dias."""
    start  = (now - timedelta(days=7)).isoformat()
    orders = _online_orders(sb, client_uuid, start)

    google = [o for o in orders if str(o.get("utm_source") or "").lower() in ("google", "cpc")]
    total  = len(google)
    if total < 5:
        return None

    with_gclid = sum(1 for o in google if o.get("google_match_type") == "gclid")
    coverage   = with_gclid / total
    if coverage >= 0.40:
        return None

    pct = round(coverage * 100)

    return {
        "type": "pattern",
        "severity": "warning",
        "title": f"gclid em apenas {pct}% dos pedidos Google",
        "content": (
            f"De {total} pedidos vindos do Google nos últimos 7 dias, apenas {with_gclid} ({pct}%) "
            f"tiveram gclid capturado. Sem gclid, conversões são enviadas apenas via enhanced conversions "
            f"(email/telefone hashed), reduzindo a eficiência dos lances automáticos."
        ),
        "data": {
            "rule_key": "gclid_coverage",
            "recommendation": "Confirme que o auto-tagging do Google Ads está ativo e que o snippet está em todas as páginas.",
            "google_orders": total,
            "with_gclid": with_gclid,
            "coverage_pct": pct,
        },
    }


def _check_retention(sb, client_uuid: str, now: datetime) -> dict | None:
    """Taxa de recompra abaixo de 8% nos últimos 30 dias."""
    start  = (now - timedelta(days=30)).isoformat()
    orders = _online_orders(sb, client_uuid, start)
    total  = len(orders)
    if total < 10:
        return None

    returning = sum(1 for o in orders if o.get("is_first_purchase") is False)
    pct       = round(returning / total * 100, 1)
    if pct >= 8:
        return None

    severity = "critical" if pct < 2 else "warning"

    return {
        "type": "pattern",
        "severity": severity,
        "title": f"Recompra em apenas {pct}% nos últimos 30 dias",
        "content": (
            f"Nos últimos 30 dias, apenas {returning} de {total} pedidos ({pct}%) foram de clientes "
            f"recorrentes. Uma taxa saudável é entre 15-25%. Baixa recompra eleva o CAC efetivo "
            f"e reduz o LTV — o crescimento fica 100% dependente de aquisição."
        ),
        "data": {
            "rule_key": "retention_low",
            "recommendation": "Crie fluxo de email pós-compra e campanha de remarketing para quem comprou há 30-90 dias.",
            "total_orders_30d": total,
            "returning_orders": returning,
            "retention_pct": pct,
        },
    }


def _check_campaign_waste(sb, client_uuid: str, now: datetime) -> dict | None:
    """Campanha com gasto >= R$300 e zero conversões nos últimos 7 dias."""
    start = (now - timedelta(days=7)).date().isoformat()
    try:
        rows = (
            sb.table("ad_spend")
            .select("campaign_name, channel, spend, conversions")
            .eq("client_id", client_uuid)
            .gte("date", start)
            .execute()
        )
    except Exception:
        return None

    by_campaign: dict[str, dict] = {}
    for row in (rows.data or []):
        name    = row.get("campaign_name") or "Sem nome"
        channel = row.get("channel") or ""
        key     = f"{channel}:{name}"
        if key not in by_campaign:
            by_campaign[key] = {"name": name, "channel": channel, "spend": 0.0, "conversions": 0}
        by_campaign[key]["spend"]       += float(row.get("spend") or 0)
        by_campaign[key]["conversions"] += int(row.get("conversions") or 0)

    waste = [c for c in by_campaign.values() if c["spend"] >= 300 and c["conversions"] == 0]
    if not waste:
        return None

    total_waste = sum(c["spend"] for c in waste)
    worst       = max(waste, key=lambda c: c["spend"])

    return {
        "type": "anomaly",
        "severity": "critical",
        "title": f"{len(waste)} campanha{'s' if len(waste) > 1 else ''} com R$ {total_waste:,.0f} sem conversão",
        "content": (
            f"Nos últimos 7 dias, {len(waste)} campanha{'s gastaram' if len(waste) > 1 else ' gastou'} "
            f"R$ {total_waste:,.0f} sem nenhuma conversão registrada. Maior gasto: "
            f"'{worst['name']}' ({worst['channel']}) — R$ {worst['spend']:,.0f}."
        ),
        "data": {
            "rule_key": "campaign_waste",
            "recommendation": f"Pause ou revise '{worst['name']}': analise o criativo, a segmentação e a landing page.",
            "total_waste": round(total_waste, 2),
            "campaigns": waste[:5],
        },
    }


# ── Public API ─────────────────────────────────────────────────────────────────

_CHECKS = [
    _check_revenue_anomaly,
    _check_capi_coverage,
    _check_gclid_coverage,
    _check_retention,
    _check_campaign_waste,
]


def generate_rule_based_insights(client_uuid: str) -> dict:
    """
    Gera insights baseados em regras para um cliente.
    Não depende de Claude — sempre funciona independente de créditos.
    """
    sb   = get_supabase()
    now  = datetime.now(timezone.utc)
    found: list[dict] = []

    for check in _CHECKS:
        try:
            result = check(sb, client_uuid, now)
            if result:
                found.append(result)
        except Exception as exc:
            logger.error("Rule check %s failed for %s: %s", check.__name__, client_uuid, exc)

    saved = sum(1 for ins in found if _save_rule_insight(sb, client_uuid, ins))
    logger.info("Rule insights for %s: found=%d saved=%d", client_uuid, len(found), saved)
    return {"insights_found": len(found), "insights_saved": saved}


def run_rule_based_insights_all_clients() -> None:
    """Cron entry point — chamado junto com (ou no lugar de) daily insights Claude."""
    try:
        sb      = get_supabase()
        clients = sb.table("clients").select("id, pixel_id").execute()
        for row in (clients.data or []):
            try:
                generate_rule_based_insights(row["id"])
            except Exception as exc:
                logger.error("rule insights failed for %s: %s", row.get("pixel_id"), exc)
    except Exception as exc:
        logger.error("run_rule_based_insights_all_clients: %s", exc)
