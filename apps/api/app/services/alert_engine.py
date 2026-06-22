"""
Alert engine — evaluates alert_rules, generates/resolves entries in `alerts`.

Schema (migration 018):
  - alert_rules: agency_id, client_id (NULL = all clients), rule_key, severity,
    throttle_minutes, config jsonb, enabled
  - alerts: agency_id, client_id, alert_rule_id, severity, fingerprint,
    title, message, data jsonb, resolved_at

Behaviour:
  1. Load all enabled rules.
  2. For each rule, iterate the eligible clients (scoped or agency-wide).
  3. Run the matching evaluator — returns 0..N findings, each with a stable
     fingerprint. Re-detecting the same condition produces the same fingerprint.
  4. Upsert findings into `alerts`. If an open alert with the same fingerprint
     already exists, leave it alone (so created_at = first detection). If it
     was previously resolved and the condition came back, insert a new row.
  5. Auto-resolve: for that rule, mark any still-open alert whose fingerprint
     wasn't reported this run as resolved (resolved_at = now).

Existing Slack-based alerts (services/alerts.py + anomalies.py) continue
running in parallel — they handle real-time notifications, this engine
owns the persistent state in the `alerts` table.

Cron: every 30 minutes via APScheduler.
"""

from __future__ import annotations

import logging
import statistics
from datetime import datetime, timezone, timedelta
from typing import Optional

import httpx

from ..database import get_supabase
from ..services import crypto

logger = logging.getLogger(__name__)

# Columns we care about across all token_health fields on clients
_INTEGRATION_HEALTH_COLS = {
    "meta_ads":      "meta_token_health",
    "google_ads":    "google_ads_token_health",
    "ga4":           "ga4_health",
    "shopify":       "shopify_health",
    "tiktok_ads":    "tiktok_token_health",
    "pinterest_ads": "pinterest_token_health",
}

_HEALTH_BAD = {"expired", "revoked", "error", "unhealthy"}


# ── helpers ──────────────────────────────────────────────────────────────────

def _now() -> datetime:
    return datetime.now(timezone.utc)


def _month_start(dt: Optional[datetime] = None) -> str:
    """Returns YYYY-MM-01 string for the month of dt (default: today UTC)."""
    d = dt or _now()
    return d.replace(day=1, hour=0, minute=0, second=0, microsecond=0).date().isoformat()


# ── evaluators: one per rule_key ─────────────────────────────────────────────
# Each returns a list of finding dicts: {fingerprint, title, message, severity, data}

def _eval_meta_token_expiring(rule: dict, client: dict) -> list[dict]:
    """Token within `threshold_days` of expiring (or already expired)."""
    expires_at = client.get("meta_token_expires_at")
    if not expires_at:
        return []
    try:
        # supabase returns ISO string
        if isinstance(expires_at, str):
            exp_dt = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
        else:
            exp_dt = expires_at
    except Exception:
        return []

    threshold_days = int((rule.get("config") or {}).get("threshold_days", 7))
    days_left = (exp_dt - _now()).total_seconds() / 86400.0
    if days_left > threshold_days:
        return []

    pixel = client.get("pixel_id") or "unknown"
    if days_left < 0:
        title = f"Token Meta EXPIRADO — {pixel}"
        msg   = f"O token Meta de {pixel} expirou em {exp_dt.date().isoformat()}. CAPI vai parar."
        sev   = "critical"
    else:
        title = f"Token Meta expira em {int(days_left)}d — {pixel}"
        msg   = f"O token Meta de {pixel} expira em {exp_dt.date().isoformat()} ({int(days_left)} dias)."
        sev   = "critical" if days_left <= 2 else "warning"

    return [{
        "fingerprint": f"meta_token_expiring:{client['id']}",
        "title":       title,
        "message":     msg,
        "severity":    sev,
        "data":        {"expires_at": exp_dt.isoformat(), "days_left": days_left},
    }]


def _eval_integration_unhealthy(rule: dict, client: dict) -> list[dict]:
    """Any *_token_health column with a bad state (expired/revoked/error)."""
    findings: list[dict] = []
    pixel = client.get("pixel_id") or "unknown"
    for provider, col in _INTEGRATION_HEALTH_COLS.items():
        status = (client.get(col) or "").lower()
        if status not in _HEALTH_BAD:
            continue
        findings.append({
            "fingerprint": f"integration_unhealthy:{client['id']}:{provider}",
            "title":       f"Integração {provider} sem saúde — {pixel}",
            "message":     f"Status atual: '{status}'. Verifique o token/configuração de {provider} para {pixel}.",
            "severity":    "critical",
            "data":        {"provider": provider, "status": status},
        })
    return findings


def _spend_mtd(client_id: str, channel: str) -> float:
    """Sum spend MTD for the given channel. Currently only meta_ad_attributions exists."""
    if channel not in ("meta_ads", "total"):
        return 0.0  # google_ads / tiktok / pinterest spend tables not yet aggregated
    try:
        start = _month_start()
        rows = (
            get_supabase()
            .table("meta_ad_attributions")
            .select("spend")
            .eq("client_id", client_id)
            .gte("date", start)
            .execute()
        ).data or []
        return sum(float(r.get("spend") or 0) for r in rows)
    except Exception as exc:
        logger.debug("_spend_mtd(%s, %s) failed: %s", client_id, channel, exc)
        return 0.0


def _revenue_mtd(client_id: str) -> float:
    """Sum paid online revenue MTD (excludes POS via utm_source filter)."""
    try:
        start = _month_start()
        rows = (
            get_supabase()
            .table("orders")
            .select("total_price, utm_source")
            .eq("client_id", client_id)
            .eq("financial_status", "paid")
            .gte("created_at", start)
            .execute()
        ).data or []
        return sum(
            float(r.get("total_price") or 0)
            for r in rows
            if (r.get("utm_source") or "").lower() not in ("pos", "offline", "draft_order")
        )
    except Exception as exc:
        logger.debug("_revenue_mtd(%s) failed: %s", client_id, exc)
        return 0.0


def _eval_roas_below_goal(rule: dict, client: dict) -> list[dict]:
    """Current month ROAS vs goals.roas_goal, with tolerance_pct margin."""
    try:
        goal_row = (
            get_supabase()
            .table("goals")
            .select("roas_goal, revenue_goal")
            .eq("client_id", client["id"])
            .eq("month", _month_start())
            .limit(1)
            .execute()
        )
    except Exception as exc:
        logger.debug("_eval_roas_below_goal load goal failed: %s", exc)
        return []

    if not (goal_row and goal_row.data):
        return []
    goal = goal_row.data[0]
    roas_goal = goal.get("roas_goal")
    if not roas_goal or float(roas_goal) <= 0:
        return []

    spend   = _spend_mtd(client["id"], "meta_ads")
    revenue = _revenue_mtd(client["id"])
    if spend <= 0:
        return []

    roas = revenue / spend
    tolerance_pct = float((rule.get("config") or {}).get("tolerance_pct", 10))
    threshold = float(roas_goal) * (1.0 - tolerance_pct / 100.0)

    if roas >= threshold:
        return []

    pixel = client.get("pixel_id") or "unknown"
    return [{
        "fingerprint": f"roas_below_goal:{client['id']}:{_month_start()}",
        "title":       f"ROAS abaixo da meta — {pixel}",
        "message": (
            f"ROAS MTD: {roas:.2f}x vs meta {float(roas_goal):.2f}x "
            f"(tolerância {tolerance_pct:.0f}%). Receita R$ {revenue:,.2f} / Gasto R$ {spend:,.2f}."
        ).replace(",", "_").replace(".", ",").replace("_", "."),
        "severity": "critical" if roas < float(roas_goal) * 0.7 else "warning",
        "data": {
            "roas": roas, "roas_goal": float(roas_goal),
            "revenue": revenue, "spend": spend, "month": _month_start(),
        },
    }]


def _eval_budget_overspent(rule: dict, client: dict) -> list[dict]:
    """For each budget set this month, flag if spend exceeded amount * (1 + threshold)."""
    try:
        budgets = (
            get_supabase()
            .table("budgets")
            .select("channel, amount")
            .eq("client_id", client["id"])
            .eq("month", _month_start())
            .execute()
        ).data or []
    except Exception as exc:
        logger.debug("_eval_budget_overspent load failed: %s", exc)
        return []

    findings: list[dict] = []
    threshold_pct = float((rule.get("config") or {}).get("threshold_pct", 5))
    pixel = client.get("pixel_id") or "unknown"

    for b in budgets:
        spend = _spend_mtd(client["id"], b["channel"])
        amount = float(b["amount"])
        if amount <= 0:
            continue
        ceiling = amount * (1.0 + threshold_pct / 100.0)
        if spend < ceiling:
            continue
        over_pct = (spend / amount - 1.0) * 100.0
        findings.append({
            "fingerprint": f"budget_overspent:{client['id']}:{_month_start()}:{b['channel']}",
            "title":       f"Orçamento {b['channel']} estourado — {pixel}",
            "message": (
                f"Gasto MTD R$ {spend:,.2f} vs orçamento R$ {amount:,.2f} ({over_pct:+.0f}%)."
            ).replace(",", "_").replace(".", ",").replace("_", "."),
            "severity": "critical",
            "data": {
                "channel": b["channel"], "spend": spend, "amount": amount,
                "over_pct": over_pct, "month": _month_start(),
            },
        })
    return findings


def _eval_tracking_stopped(rule: dict, client: dict) -> list[dict]:
    """No tracking_event for the client in the last threshold_hours."""
    if client.get("tracking_enabled") is False:
        return []
    threshold_hours = int((rule.get("config") or {}).get("threshold_hours", 24))
    try:
        rows = (
            get_supabase()
            .table("tracking_events")
            .select("created_at")
            .eq("client_id", client["id"])
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        ).data or []
    except Exception as exc:
        logger.debug("_eval_tracking_stopped(%s): %s", client.get("pixel_id"), exc)
        return []

    if not rows:
        return []  # brand-new client — no baseline to compare against

    last_iso = rows[0]["created_at"]
    if isinstance(last_iso, str):
        last_dt = datetime.fromisoformat(last_iso.replace("Z", "+00:00"))
    else:
        last_dt = last_iso

    hours_since = (_now() - last_dt).total_seconds() / 3600
    if hours_since <= threshold_hours:
        return []

    pixel    = client.get("pixel_id") or "unknown"
    severity = "critical" if hours_since > 48 else "warning"
    return [{
        "fingerprint": f"tracking_stopped:{client['id']}",
        "title":       f"Tracking parado — {pixel}",
        "message":     f"Nenhum evento de tracking nas últimas {int(hours_since)}h. Verifique o snippet/CAPI.",
        "severity":    severity,
        "data":        {"last_event_at": last_iso, "hours_since": round(hours_since, 1)},
    }]


def _eval_cpa_over_target(rule: dict, client: dict) -> list[dict]:
    """CPA MTD vs goals.cpa_target with tolerance_pct margin."""
    try:
        goal_row = (
            get_supabase()
            .table("goals")
            .select("cpa_target")
            .eq("client_id", client["id"])
            .eq("month", _month_start())
            .limit(1)
            .execute()
        )
    except Exception as exc:
        logger.debug("_eval_cpa_over_target load goal(%s): %s", client.get("pixel_id"), exc)
        return []

    if not (goal_row and goal_row.data):
        return []
    cpa_target = goal_row.data[0].get("cpa_target")
    if not cpa_target or float(cpa_target) <= 0:
        return []

    try:
        start = _month_start()
        order_rows = (
            get_supabase()
            .table("orders")
            .select("utm_source")
            .eq("client_id", client["id"])
            .eq("financial_status", "paid")
            .gte("created_at", start)
            .execute()
        ).data or []
    except Exception as exc:
        logger.debug("_eval_cpa_over_target load orders(%s): %s", client.get("pixel_id"), exc)
        return []

    online_orders = [
        r for r in order_rows
        if (r.get("utm_source") or "").lower() not in ("pos", "offline", "draft_order")
    ]
    if not online_orders:
        return []

    spend = _spend_mtd(client["id"], "meta_ads")
    if spend <= 0:
        return []

    cpa           = spend / len(online_orders)
    tolerance_pct = float((rule.get("config") or {}).get("tolerance_pct", 20))
    ceiling       = float(cpa_target) * (1.0 + tolerance_pct / 100.0)
    if cpa <= ceiling:
        return []

    pixel    = client.get("pixel_id") or "unknown"
    over_pct = (cpa / float(cpa_target) - 1.0) * 100.0
    return [{
        "fingerprint": f"cpa_over_target:{client['id']}:{_month_start()}",
        "title":       f"CPA acima da meta — {pixel}",
        "message": (
            f"CPA MTD R$ {cpa:,.2f} vs meta R$ {float(cpa_target):,.2f} ({over_pct:+.0f}%). "
            f"{len(online_orders)} pedido(s) / R$ {spend:,.2f} investido."
        ).replace(",", "_").replace(".", ",").replace("_", "."),
        "severity": "critical" if cpa > float(cpa_target) * 1.5 else "warning",
        "data": {
            "cpa": cpa, "cpa_target": float(cpa_target),
            "orders": len(online_orders), "spend": spend,
            "over_pct": over_pct, "month": _month_start(),
        },
    }]


# ── New evaluators ────────────────────────────────────────────────────────────

def _eval_revenue_drop(rule: dict, client: dict) -> list[dict]:
    """Receita 24h caiu X% vs a MEDIANA dos 7 dias anteriores (geral + por canal).

    Usa mediana (não média) pra não disparar por causa de um único dia atípico,
    e exige que canais filtrados vendam na maioria dos dias — canais esparsos
    (ex.: Google server-side da LK, com vários dias zerados e um pico isolado)
    são voláteis demais pra um alerta de 24h fazer sentido.
    """
    cfg                = rule.get("config") or {}
    threshold_warning  = float(cfg.get("drop_warning_pct",  15))
    threshold_critical = float(cfg.get("drop_critical_pct", 30))
    channel_filter     = cfg.get("channel")  # e.g. "facebook" or None
    # Canal precisa vender em pelo menos N dos 7 dias do baseline pra ser elegível.
    min_active_days    = int(cfg.get("min_active_days", 6))

    sb = get_supabase()
    now = _now()
    t24h    = now - timedelta(hours=24)
    t7d     = now - timedelta(days=8)
    t7d_end = now - timedelta(hours=24)

    try:
        def _q(start, end):
            q = (
                sb.table("orders")
                .select("total_price, utm_source, created_at")
                .eq("client_id", client["id"])
                .eq("financial_status", "paid")
                .gt("total_price", 0)
                .gte("created_at", start.isoformat())
                .lt("created_at", end.isoformat())
            )
            rows = q.execute().data or []
            if channel_filter:
                rows = [r for r in rows if (r.get("utm_source") or "").lower() == channel_filter.lower()]
            return [r for r in rows if (r.get("utm_source") or "").lower() not in ("pos", "draft_order")]

        recent   = _q(t24h, now)
        baseline = _q(t7d, t7d_end)
    except Exception as exc:
        logger.debug("_eval_revenue_drop(%s): %s", client.get("pixel_id"), exc)
        return []

    recent_rev = sum(float(r["total_price"]) for r in recent)

    # Receita por dia no baseline (7 dias), preenchendo dias zerados.
    by_day: dict = {}
    for r in baseline:
        day = str(r.get("created_at") or "")[:10]
        if day:
            by_day[day] = by_day.get(day, 0.0) + float(r["total_price"])
    daily = [by_day.get((t7d_end - timedelta(days=i)).date().isoformat(), 0.0) for i in range(7)]
    active_days = sum(1 for v in daily if v > 0)

    # Canal esparso → volátil demais pra alertar em janela de 24h.
    if channel_filter and active_days < min_active_days:
        return []

    baseline_ref = statistics.median(daily) if daily else 0
    if baseline_ref <= 0:
        return []

    drop = (baseline_ref - recent_rev) / baseline_ref
    if drop < threshold_warning / 100:
        return []

    pixel  = client.get("pixel_id") or "unknown"
    label  = f" [{channel_filter}]" if channel_filter else ""
    sev    = "critical" if drop >= threshold_critical / 100 else "warning"
    return [{
        "fingerprint": f"revenue_drop:{client['id']}:{channel_filter or 'all'}:{now.date().isoformat()}",
        "title":       f"Queda de faturamento{label} — {pixel}",
        "message":     (
            f"Receita 24h{label}: R$ {recent_rev:,.0f} vs mediana 7d R$ {baseline_ref:,.0f}/dia "
            f"(queda de {drop * 100:.0f}%)"
        ).replace(",", "_").replace(".", ",").replace("_", "."),
        "severity": sev,
        "data":     {"recent_rev": recent_rev, "baseline_median": baseline_ref,
                     "active_days": active_days,
                     "drop_pct": round(drop * 100, 1), "channel": channel_filter},
    }]


def _eval_views_drop(rule: dict, client: dict) -> list[dict]:
    """Pageviews das últimas 2h caíram X% vs média das últimas 7 dias no mesmo horário."""
    if client.get("tracking_enabled") is False:
        return []
    threshold = float((rule.get("config") or {}).get("drop_pct", 60))
    window_h  = 2

    sb  = get_supabase()
    now = _now()
    t2h = now - timedelta(hours=window_h)

    try:
        # Últimas 2h
        recent_ct = (
            sb.table("tracking_events")
            .select("id", count="exact", head=True)
            .eq("client_id", client["id"])
            .eq("event_type", "pageview")
            .gte("created_at", t2h.isoformat())
            .execute()
        ).count or 0

        # Mesmo janela de 2h nos últimos 7 dias (média)
        baseline_counts: list[int] = []
        for days_ago in range(1, 8):
            start = now - timedelta(days=days_ago, hours=0)
            end   = start + timedelta(hours=window_h)
            ct = (
                sb.table("tracking_events")
                .select("id", count="exact", head=True)
                .eq("client_id", client["id"])
                .eq("event_type", "pageview")
                .gte("created_at", (start - timedelta(hours=window_h)).isoformat())
                .lt("created_at", end.isoformat())
                .execute()
            ).count or 0
            baseline_counts.append(ct)
    except Exception as exc:
        logger.debug("_eval_views_drop(%s): %s", client.get("pixel_id"), exc)
        return []

    # MEDIANA dos 7 intervalos (imune a um dia atípico) + exige que a maioria
    # dos intervalos no mesmo horário tenha tido tráfego (senão é volátil demais).
    active     = sum(1 for c in baseline_counts if c > 0)
    min_active = int((rule.get("config") or {}).get("min_active_windows", 5))
    if active < min_active:
        return []
    baseline_ref = statistics.median(baseline_counts) if baseline_counts else 0
    if baseline_ref < 10:  # pouco tráfego — não alerta
        return []

    drop = (baseline_ref - recent_ct) / baseline_ref
    if drop < threshold / 100:
        return []

    pixel = client.get("pixel_id") or "unknown"
    return [{
        "fingerprint": f"views_drop:{client['id']}:{now.strftime('%Y%m%d%H')}",
        "title":       f"Queda brusca de views — {pixel}",
        "message":     (
            f"Pageviews nas últimas {window_h}h: {recent_ct} vs mediana 7d "
            f"{baseline_ref:.0f}/intervalo (queda {drop * 100:.0f}%). Verifique o snippet."
        ),
        "severity": "critical" if drop >= 0.80 else "warning",
        "data":     {"recent": recent_ct, "baseline_median": round(baseline_ref, 1),
                     "active_windows": active, "drop_pct": round(drop * 100, 1)},
    }]


def _eval_zero_sales(rule: dict, client: dict) -> list[dict]:
    """Zero vendas pagas em X horas dentro do horário comercial (8h-22h BRT)."""
    threshold_hours = float((rule.get("config") or {}).get("threshold_hours", 8))
    # Só dispara em horário comercial (UTC-3 = 08:00-22:00 BRT → 11:00-01:00 UTC)
    hour_utc = _now().hour
    if not (11 <= hour_utc <= 24 or hour_utc <= 1):
        return []

    sb  = get_supabase()

    # Guard: cliente sem histórico de pedidos nos últimos 30 dias não tem baseline.
    # Evita falsos positivos para clientes dashboard-only (sem integração ecommerce).
    try:
        baseline_check = (
            sb.table("orders")
            .select("id", count="exact", head=True)
            .eq("client_id", client["id"])
            .eq("financial_status", "paid")
            .gt("total_price", 0)
            .gte("created_at", (_now() - timedelta(days=30)).isoformat())
            .execute()
        ).count or 0
        if baseline_check == 0:
            return []
    except Exception as exc:
        logger.debug("_eval_zero_sales baseline(%s): %s", client.get("pixel_id"), exc)
        return []

    cutoff = (_now() - timedelta(hours=threshold_hours)).isoformat()
    try:
        rows = (
            sb.table("orders")
            .select("id")
            .eq("client_id", client["id"])
            .eq("financial_status", "paid")
            .gt("total_price", 0)
            .gte("created_at", cutoff)
            .not_.or_("utm_source.eq.pos,platform_source.eq.pos")
            .limit(1)
            .execute()
        ).data or []
    except Exception as exc:
        logger.debug("_eval_zero_sales(%s): %s", client.get("pixel_id"), exc)
        return []

    if rows:
        return []

    pixel = client.get("pixel_id") or "unknown"
    return [{
        "fingerprint": f"zero_sales:{client['id']}:{_now().date().isoformat()}",
        "title":       f"Sem vendas há {int(threshold_hours)}h — {pixel}",
        "message":     (
            f"Nenhum pedido pago online nas últimas {int(threshold_hours)}h em horário comercial. "
            f"Verifique campanhas, estoque e funcionamento do checkout."
        ),
        "severity": "critical",
        "data":     {"threshold_hours": threshold_hours},
    }]


def _eval_checkout_drop(rule: dict, client: dict) -> list[dict]:
    """begin_checkout das últimas 2h caiu X% vs média 7d no mesmo horário."""
    if client.get("tracking_enabled") is False:
        return []
    threshold = float((rule.get("config") or {}).get("drop_pct", 70))
    window_h  = 2

    sb  = get_supabase()
    now = _now()
    t2h = now - timedelta(hours=window_h)

    try:
        recent_ct = (
            sb.table("tracking_events")
            .select("id", count="exact", head=True)
            .eq("client_id", client["id"])
            .eq("event_type", "begin_checkout")
            .gte("created_at", t2h.isoformat())
            .execute()
        ).count or 0

        baseline_counts: list[int] = []
        for days_ago in range(1, 8):
            s = now - timedelta(days=days_ago, hours=0)
            ct = (
                sb.table("tracking_events")
                .select("id", count="exact", head=True)
                .eq("client_id", client["id"])
                .eq("event_type", "begin_checkout")
                .gte("created_at", (s - timedelta(hours=window_h)).isoformat())
                .lt("created_at", s.isoformat())
                .execute()
            ).count or 0
            baseline_counts.append(ct)
    except Exception as exc:
        logger.debug("_eval_checkout_drop(%s): %s", client.get("pixel_id"), exc)
        return []

    # MEDIANA + guard de janelas ativas. begin_checkout é baixo volume, então o
    # piso na mediana já evita disparar por intervalos naturalmente pequenos.
    active     = sum(1 for c in baseline_counts if c > 0)
    min_active = int((rule.get("config") or {}).get("min_active_windows", 4))
    if active < min_active:
        return []
    baseline_ref = statistics.median(baseline_counts) if baseline_counts else 0
    if baseline_ref < 3:
        return []

    drop = (baseline_ref - recent_ct) / baseline_ref
    if drop < threshold / 100:
        return []

    pixel = client.get("pixel_id") or "unknown"
    return [{
        "fingerprint": f"checkout_drop:{client['id']}:{now.strftime('%Y%m%d%H')}",
        "title":       f"Queda de checkouts iniciados — {pixel}",
        "message":     (
            f"begin_checkout nas últimas {window_h}h: {recent_ct} vs mediana 7d "
            f"{baseline_ref:.0f}/intervalo (queda {drop * 100:.0f}%). Possível problema no checkout."
        ),
        "severity": "warning",
        "data":     {"recent": recent_ct, "baseline_median": round(baseline_ref, 1),
                     "active_windows": active, "drop_pct": round(drop * 100, 1)},
    }]


def _eval_low_balance_meta(rule: dict, client: dict) -> list[dict]:
    """Saldo Meta Ads abaixo do threshold (apenas clientes pre-pagos)."""
    if not client.get("meta_prepaid"):
        return []
    account_id   = client.get("meta_ad_account_id")
    access_token = client.get("meta_access_token")
    if not (account_id and access_token):
        return []

    threshold = float(
        (rule.get("config") or {}).get("threshold_brl")
        or client.get("meta_balance_threshold")
        or 200
    )

    try:
        from . import meta_ads as meta_ads_svc
        result = meta_ads_svc.fetch_account_balance(account_id, access_token)
        if result.get("error"):
            return []
        balance = float(result.get("balance") or 0)
    except Exception as exc:
        logger.debug("_eval_low_balance_meta(%s): %s", client.get("pixel_id"), exc)
        return []

    if balance > threshold:
        return []

    pixel = client.get("pixel_id") or "unknown"
    sev   = "critical" if balance < threshold * 0.5 else "warning"
    return [{
        "fingerprint": f"low_balance_meta:{client['id']}:{_now().date().isoformat()}",
        "title":       f"Saldo Meta Ads baixo — {pixel}",
        "message":     (
            f"Saldo restante: R$ {balance:,.2f} (alerta em R$ {threshold:,.2f}). "
            f"Adicione crédito para evitar interrupção das campanhas."
        ).replace(",", "_").replace(".", ",").replace("_", "."),
        "severity": sev,
        "data":     {"balance": balance, "threshold": threshold, "currency": result.get("currency")},
    }]


def _eval_low_balance_google(rule: dict, client: dict) -> list[dict]:
    """Saldo Google Ads baixo — estima com base no burn rate diário vs orçamento mensal."""
    if not client.get("google_prepaid"):
        return []

    threshold = float(
        (rule.get("config") or {}).get("threshold_days_remaining", 3)
    )
    sb = get_supabase()
    now = _now()

    try:
        # Gasto dos últimos 7 dias em Google
        rows = (
            sb.table("ad_spend")
            .select("spend")
            .eq("client_id", client["id"])
            .eq("channel", "google_ads")
            .gte("date", (now.date() - timedelta(days=7)).isoformat())
            .execute()
        ).data or []
        if not rows:
            return []

        daily_burn = sum(float(r["spend"]) for r in rows) / 7.0

        # Orçamento mensal Google configurado
        budget_row = (
            sb.table("budgets")
            .select("amount")
            .eq("client_id", client["id"])
            .eq("channel", "google_ads")
            .eq("month", _month_start())
            .limit(1)
            .execute()
        ).data or []

        if not budget_row or daily_burn <= 0:
            return []

        monthly_budget = float(budget_row[0]["amount"])
        # Gasto MTD
        mtd_rows = (
            sb.table("ad_spend")
            .select("spend")
            .eq("client_id", client["id"])
            .eq("channel", "google_ads")
            .gte("date", _month_start())
            .execute()
        ).data or []
        mtd_spend     = sum(float(r["spend"]) for r in mtd_rows)
        remaining_brl = monthly_budget - mtd_spend
        days_left     = remaining_brl / daily_burn if daily_burn > 0 else 999
    except Exception as exc:
        logger.debug("_eval_low_balance_google(%s): %s", client.get("pixel_id"), exc)
        return []

    if days_left > threshold:
        return []

    pixel = client.get("pixel_id") or "unknown"
    google_threshold = float(client.get("google_balance_threshold") or 200)
    sev = "critical" if remaining_brl < google_threshold * 0.5 or days_left < 1 else "warning"
    return [{
        "fingerprint": f"low_balance_google:{client['id']}:{_now().date().isoformat()}",
        "title":       f"Saldo Google Ads baixo — {pixel}",
        "message":     (
            f"Saldo estimado: R$ {remaining_brl:,.0f} ({days_left:.1f} dias ao ritmo atual "
            f"de R$ {daily_burn:,.0f}/dia). Revise o orçamento."
        ).replace(",", "_").replace(".", ",").replace("_", "."),
        "severity": sev,
        "data":     {"remaining_brl": round(remaining_brl, 2), "daily_burn": round(daily_burn, 2),
                     "days_remaining": round(days_left, 1)},
    }]


def _eval_google_conversion_drop(rule: dict, client: dict) -> list[dict]:
    """Queda no número de conversões enviadas ao Google vs MEDIANA dos 7 dias.

    Usa mediana + exige envio em quase todos os dias (conversões Google são
    esparsas/voláteis) pra não disparar por variação normal nem por um dia
    atípico no baseline.
    """
    cfg             = rule.get("config") or {}
    threshold       = float(cfg.get("drop_pct", 50))
    min_active_days = int(cfg.get("min_active_days", 6))

    sb  = get_supabase()
    now = _now()
    t24h    = now - timedelta(hours=24)
    t7d     = now - timedelta(days=8)
    t7d_end = now - timedelta(hours=24)

    try:
        recent = (
            sb.table("orders")
            .select("id", count="exact", head=True)
            .eq("client_id", client["id"])
            .eq("financial_status", "paid")
            .gt("total_price", 0)
            .eq("google_sent", True)
            .gte("created_at", t24h.isoformat())
            .execute()
        ).count or 0

        baseline_rows = (
            sb.table("orders")
            .select("created_at")
            .eq("client_id", client["id"])
            .eq("financial_status", "paid")
            .gt("total_price", 0)
            .eq("google_sent", True)
            .gte("created_at", t7d.isoformat())
            .lt("created_at", t7d_end.isoformat())
            .limit(5000)
            .execute()
        ).data or []
    except Exception as exc:
        logger.debug("_eval_google_conversion_drop(%s): %s", client.get("pixel_id"), exc)
        return []

    by_day: dict = {}
    for r in baseline_rows:
        day = str(r.get("created_at") or "")[:10]
        if day:
            by_day[day] = by_day.get(day, 0) + 1
    daily = [by_day.get((t7d_end - timedelta(days=i)).date().isoformat(), 0) for i in range(7)]
    active_days = sum(1 for v in daily if v > 0)
    if active_days < min_active_days:
        return []

    baseline_ref = statistics.median(daily)
    if baseline_ref < 1:
        return []

    drop = (baseline_ref - recent) / baseline_ref
    if drop < threshold / 100:
        return []

    pixel = client.get("pixel_id") or "unknown"
    return [{
        "fingerprint": f"google_conv_drop:{client['id']}:{now.date().isoformat()}",
        "title":       f"Queda de conversões Google — {pixel}",
        "message":     (
            f"Conversões enviadas ao Google nas últimas 24h: {recent} vs mediana 7d "
            f"{baseline_ref:.0f}/dia (queda {drop * 100:.0f}%). Verifique o webhook."
        ),
        "severity": "warning",
        "data":     {"recent": recent, "baseline_median": baseline_ref,
                     "active_days": active_days, "drop_pct": round(drop * 100, 1)},
    }]


def _eval_high_ticket_anomaly(rule: dict, client: dict) -> list[dict]:
    """Pedido com valor > X vezes o ticket médio dos últimos 30d."""
    multiple = float((rule.get("config") or {}).get("multiple", 5))
    min_value = float((rule.get("config") or {}).get("min_value", 1000))

    sb  = get_supabase()
    now = _now()
    t30d = now - timedelta(days=30)
    t1h  = now - timedelta(hours=1)

    try:
        baseline_rows = (
            sb.table("orders")
            .select("total_price")
            .eq("client_id", client["id"])
            .eq("financial_status", "paid")
            .gt("total_price", 0)
            .gte("created_at", t30d.isoformat())
            .lt("created_at", t1h.isoformat())
            .execute()
        ).data or []

        if len(baseline_rows) < 5:
            return []

        avg_ticket = sum(float(r["total_price"]) for r in baseline_rows) / len(baseline_rows)
        threshold_value = max(avg_ticket * multiple, min_value)

        recent_big = (
            sb.table("orders")
            .select("id, platform_order_id, total_price, email")
            .eq("client_id", client["id"])
            .eq("financial_status", "paid")
            .gt("total_price", threshold_value)
            .gte("created_at", t1h.isoformat())
            .execute()
        ).data or []
    except Exception as exc:
        logger.debug("_eval_high_ticket_anomaly(%s): %s", client.get("pixel_id"), exc)
        return []

    findings = []
    pixel = client.get("pixel_id") or "unknown"
    for o in recent_big:
        val = float(o["total_price"])
        findings.append({
            "fingerprint": f"high_ticket:{o['id']}",
            "title":       f"Pedido de alto valor — {pixel}",
            "message":     (
                f"Pedido #{o.get('platform_order_id', '?')} de R$ {val:,.0f} "
                f"({val / avg_ticket:.1f}× ticket médio R$ {avg_ticket:,.0f})."
            ).replace(",", "_").replace(".", ",").replace("_", "."),
            "severity": "info",
            "data":     {"order_id": o["id"], "value": val, "avg_ticket": round(avg_ticket, 2),
                         "multiple": round(val / avg_ticket, 1)},
        })
    return findings


def _eval_roas_drop_channel(rule: dict, client: dict) -> list[dict]:
    """
    ROAS das últimas 24h por canal caiu X% vs média dos 7 dias anteriores.
    Detecta campanhas que pararam de converter mesmo com spend ativo.
    config: channel ('meta_ads'|'google_ads'), drop_pct (default 40)
    """
    channel    = (rule.get("config") or {}).get("channel", "meta_ads")
    drop_pct   = float((rule.get("config") or {}).get("drop_pct", 40))
    min_spend  = float((rule.get("config") or {}).get("min_spend_24h", 100))

    sb  = get_supabase()
    now = _now()
    t24h     = now - timedelta(hours=24)
    t7d_end  = now - timedelta(hours=24)
    t7d_start = now - timedelta(days=8)

    # Map channel key to utm_source values
    _UTM = {
        "meta_ads":   ("facebook", "fb", "instagram", "ig", "meta"),
        "google_ads": ("google", "google_ads"),
    }
    utm_values = _UTM.get(channel, ())

    def _period_roas(start, end) -> tuple[float, float]:
        rows = (
            sb.table("orders")
            .select("total_price, utm_source")
            .eq("client_id", client["id"])
            .eq("financial_status", "paid")
            .gt("total_price", 0)
            .gte("created_at", start.isoformat())
            .lt("created_at", end.isoformat())
            .execute()
        ).data or []
        rev = sum(float(r["total_price"]) for r in rows
                  if (r.get("utm_source") or "").lower() in utm_values)
        spd_rows = (
            sb.table("ad_spend")
            .select("spend")
            .eq("client_id", client["id"])
            .eq("channel", channel)
            .gte("date", start.date().isoformat())
            .lte("date", end.date().isoformat())
            .execute()
        ).data or []
        spd = sum(float(r["spend"]) for r in spd_rows)
        return rev, spd

    try:
        rev_recent, spd_recent = _period_roas(t24h, now)
        rev_base, spd_base = _period_roas(t7d_start, t7d_end)
    except Exception as exc:
        logger.debug("_eval_roas_drop_channel(%s/%s): %s", client.get("pixel_id"), channel, exc)
        return []

    if spd_recent < min_spend:
        return []  # spend muito baixo — não é significativo

    roas_recent = rev_recent / spd_recent if spd_recent > 0 else 0
    roas_base   = (rev_base / spd_base / 7) if spd_base > 0 else 0  # média diária

    if roas_base < 0.5:
        return []  # sem baseline confiável

    drop = (roas_base - roas_recent) / roas_base
    if drop < drop_pct / 100:
        return []

    channel_label = {"meta_ads": "Meta Ads", "google_ads": "Google Ads"}.get(channel, channel)
    pixel = client.get("pixel_id") or "unknown"
    return [{
        "fingerprint": f"roas_drop_channel:{client['id']}:{channel}:{now.date().isoformat()}",
        "title":       f"ROAS {channel_label} caiu — {pixel}",
        "message":     (
            f"ROAS 24h ({channel_label}): {roas_recent:.2f}x vs média 7d {roas_base:.2f}x "
            f"(queda {drop * 100:.0f}%). Receita: R$ {rev_recent:,.0f} / Gasto: R$ {spd_recent:,.0f}."
        ).replace(",", "_").replace(".", ",").replace("_", "."),
        "severity": "critical" if drop >= 0.60 else "warning",
        "data":     {"channel": channel, "roas_24h": round(roas_recent, 2),
                     "roas_7d_avg": round(roas_base, 2), "drop_pct": round(drop * 100, 1)},
    }]


def _eval_spend_below_expected(rule: dict, client: dict) -> list[dict]:
    """
    Gasto das últimas 24h caiu X% vs média 7d — indica campanhas pausadas ou sem entrega.
    config: channel ('meta_ads'|'google_ads'|'all'), drop_pct (default 50), min_daily_spend
    """
    channel   = (rule.get("config") or {}).get("channel", "meta_ads")
    drop_pct  = float((rule.get("config") or {}).get("drop_pct", 50))
    min_spend = float((rule.get("config") or {}).get("min_daily_spend", 200))

    sb  = get_supabase()
    now = _now()
    yesterday     = (now - timedelta(days=1)).date()
    week_ago      = (now - timedelta(days=8)).date()

    def _daily_spend_rows(start_date, end_date):
        q = sb.table("ad_spend").select("spend, channel, date").eq("client_id", client["id"])
        if channel != "all":
            q = q.eq("channel", channel)
        return q.gte("date", start_date.isoformat()).lte("date", end_date.isoformat()).execute().data or []

    try:
        yesterday_rows = _daily_spend_rows(yesterday, yesterday)
        week_rows      = _daily_spend_rows(week_ago, (now - timedelta(days=2)).date())
    except Exception as exc:
        logger.debug("_eval_spend_below_expected(%s): %s", client.get("pixel_id"), exc)
        return []

    # Sem linha de ad_spend para ontem = o sync diário ainda não rodou (roda às
    # 06:00 UTC, enquanto o alert_engine roda a cada 30min desde 00:00 UTC). Não
    # confundir "dado ainda não sincronizado" com "campanha pausada" — senão o
    # alerta dispara todo dia logo após a meia-noite UTC e se auto-resolve de
    # manhã (falso positivo). _upsert_spend grava linha mesmo com spend=0, então
    # uma pausa real continua produzindo linha e o alerta dispara corretamente.
    if not yesterday_rows:
        return []

    spend_yesterday = sum(float(r["spend"]) for r in yesterday_rows)
    spend_7d_total  = sum(float(r["spend"]) for r in week_rows)

    spend_7d_avg = spend_7d_total / 7.0
    if spend_7d_avg < min_spend:
        return []  # cliente não tem spend relevante

    drop = (spend_7d_avg - spend_yesterday) / spend_7d_avg
    if drop < drop_pct / 100:
        return []

    channel_label = {"meta_ads": "Meta Ads", "google_ads": "Google Ads", "all": "Ads"}.get(channel, channel)
    pixel = client.get("pixel_id") or "unknown"
    sev   = "critical" if drop >= 0.80 else "warning"

    return [{
        "fingerprint": f"spend_below_expected:{client['id']}:{channel}:{yesterday.isoformat()}",
        "title":       f"Investimento {channel_label} abaixo do esperado — {pixel}",
        "message":     (
            f"Gasto ontem ({channel_label}): R$ {spend_yesterday:,.0f} vs média 7d "
            f"R$ {spend_7d_avg:,.0f}/dia (queda {drop * 100:.0f}%). "
            f"Verifique se as campanhas estão ativas e com orçamento suficiente."
        ).replace(",", "_").replace(".", ",").replace("_", "."),
        "severity": sev,
        "data":     {"channel": channel, "spend_yesterday": round(spend_yesterday, 2),
                     "spend_7d_avg": round(spend_7d_avg, 2), "drop_pct": round(drop * 100, 1)},
    }]


def _eval_utm_null_ratio(rule: dict, client: dict) -> list[dict]:
    """
    % de pedidos sem utm_source nas últimas 24h acima do threshold.
    Detecta falha no relay de UTM — quando snippet não passa UTMs para os pedidos,
    o Meta e Google ficam cegos para as conversões.
    config: threshold_pct (default 60), min_orders (default 5)
    """
    threshold  = float((rule.get("config") or {}).get("threshold_pct", 60))
    min_orders = int((rule.get("config") or {}).get("min_orders", 5))

    sb   = get_supabase()
    now  = _now()
    t24h = (now - timedelta(hours=24)).isoformat()

    try:
        rows = (
            sb.table("orders")
            .select("utm_source, financial_status")
            .eq("client_id", client["id"])
            .eq("financial_status", "paid")
            .gt("total_price", 0)
            .gte("created_at", t24h)
            .execute()
        ).data or []
    except Exception as exc:
        logger.debug("_eval_utm_null_ratio(%s): %s", client.get("pixel_id"), exc)
        return []

    # Exclude POS/offline
    online = [r for r in rows if (r.get("utm_source") or "").lower() not in ("pos", "in_store", "offline", "draft_order")]
    if len(online) < min_orders:
        return []

    null_count = sum(1 for r in online if not r.get("utm_source"))
    null_pct   = null_count / len(online) * 100

    if null_pct < threshold:
        return []

    pixel = client.get("pixel_id") or "unknown"
    return [{
        "fingerprint": f"utm_null_ratio:{client['id']}:{now.date().isoformat()}",
        "title":       f"UTM sem atribuição ({null_pct:.0f}%) — {pixel}",
        "message":     (
            f"{null_count} de {len(online)} pedidos online nas últimas 24h sem utm_source "
            f"({null_pct:.0f}%). O snippet pode não estar passando UTMs para os pedidos. "
            f"Meta e Google ficam cegos para essas conversões."
        ),
        "severity": "critical" if null_pct >= 80 else "warning",
        "data":     {"null_count": null_count, "total_online": len(online),
                     "null_pct": round(null_pct, 1)},
    }]


def _eval_nonpaid_spike(rule: dict, client: dict) -> list[dict]:
    """Pico de receita SEM aumento proporcional de investimento (viral/orgânico/
    influencer/imprensa). Compara ONTEM (dia completo, já sincronizado) com a
    MEDIANA dos 7 dias anteriores — para receita E investimento. Só dispara se a
    receita subiu forte e o gasto NÃO acompanhou. Alerta informativo (oportunidade
    de capitalizar: subir remarketing/orçamento enquanto a demanda está quente).
    """
    cfg             = rule.get("config") or {}
    spike_pct       = float(cfg.get("spike_pct", 50))        # receita +50% vs mediana
    min_active_days = int(cfg.get("min_active_days", 5))     # baseline precisa ser sólido
    min_revenue     = float(cfg.get("min_daily_revenue", 300))  # ignora lojas sem volume
    # gasto pode subir no máximo metade do ritmo da receita pra ainda ser "não-pago"
    spend_ratio_max = float(cfg.get("spend_ratio_max", 0.5))

    sb  = get_supabase()
    now = _now()
    y_start = (now - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    y_end   = now.replace(hour=0, minute=0, second=0, microsecond=0)
    base_start = y_start - timedelta(days=7)
    ydate = y_start.date().isoformat()

    def _day(i):  # data ISO de i dias antes de ontem (i=0 → ontem)
        return (y_start - timedelta(days=i)).date().isoformat()

    # ── Receita por dia (online; exclui POS/offline) ──────────────────────────
    try:
        rows = (sb.table("orders")
                .select("total_price, utm_source, created_at")
                .eq("client_id", client["id"]).eq("financial_status", "paid")
                .gt("total_price", 0)
                .gte("created_at", base_start.isoformat()).lt("created_at", y_end.isoformat())
                .limit(10000).execute().data or [])
    except Exception as exc:
        logger.debug("_eval_nonpaid_spike(%s): %s", client.get("pixel_id"), exc)
        return []

    rev_by_day: dict = {}
    for r in rows:
        if (r.get("utm_source") or "").lower() in ("pos", "draft_order", "in_store", "offline"):
            continue
        d = str(r.get("created_at") or "")[:10]
        if d:
            rev_by_day[d] = rev_by_day.get(d, 0.0) + float(r.get("total_price") or 0)

    y_rev = rev_by_day.get(ydate, 0.0)
    daily = [rev_by_day.get(_day(i), 0.0) for i in range(1, 8)]
    if sum(1 for v in daily if v > 0) < min_active_days:
        return []
    base_rev = statistics.median(daily)
    if base_rev < min_revenue:
        return []

    rev_lift = (y_rev - base_rev) / base_rev
    if rev_lift < spike_pct / 100:
        return []

    # ── Investimento por dia (todos os canais) ────────────────────────────────
    try:
        srows = (sb.table("ad_spend").select("date, spend")
                 .eq("client_id", client["id"])
                 .gte("date", base_start.date().isoformat()).lte("date", ydate)
                 .execute().data or [])
    except Exception:
        srows = []
    spend_by_day: dict = {}
    for r in srows:
        d = str(r.get("date") or "")[:10]
        if d:
            spend_by_day[d] = spend_by_day.get(d, 0.0) + float(r.get("spend") or 0)

    y_spend    = spend_by_day.get(ydate, 0.0)
    sdaily     = [spend_by_day.get(_day(i), 0.0) for i in range(1, 8)]
    base_spend = statistics.median(sdaily)

    # Se havia investimento e ele acompanhou a receita, o pico é PAGO → não alerta.
    if base_spend > 0:
        # Sem linha de ad_spend para ontem = o sync diário (06:00 UTC) ainda não
        # rodou. Com y_spend=0 artificial não dá pra classificar pago vs não-pago
        # → adiar, senão vira falso "pico não-pago" toda madrugada UTC (o
        # alert_engine roda a cada 30min desde 00:00). _upsert_spend grava linha
        # mesmo com spend=0, então um dia realmente sem gasto continua avaliável.
        if ydate not in spend_by_day:
            return []
        spend_lift = (y_spend - base_spend) / base_spend
        if spend_lift >= rev_lift * spend_ratio_max:
            return []
    else:
        spend_lift = 0.0  # sem histórico de gasto → qualquer pico é não-pago

    pixel    = client.get("pixel_id") or "unknown"
    roas_txt = f" — ROAS do dia {y_rev / y_spend:.1f}x" if y_spend > 0 else " — sem investimento no dia"
    return [{
        "fingerprint": f"nonpaid_spike:{client['id']}:{ydate}",
        "title":       f"Pico de tráfego não-pago — {pixel}",
        "message":     (
            f"Receita de ontem R$ {y_rev:,.0f} vs mediana 7d R$ {base_rev:,.0f}/dia "
            f"(+{rev_lift * 100:.0f}%), sem aumento proporcional de investimento "
            f"(gasto {y_spend:,.0f} vs mediana {base_spend:,.0f}){roas_txt}. "
            f"Possível viral/orgânico/influencer — considere capitalizar (subir remarketing/orçamento)."
        ).replace(",", "_").replace(".", ",").replace("_", "."),
        "severity": "info",
        "data":     {"yesterday_revenue": round(y_rev, 2), "baseline_revenue_median": round(base_rev, 2),
                     "rev_lift_pct": round(rev_lift * 100, 1), "yesterday_spend": round(y_spend, 2),
                     "baseline_spend_median": round(base_spend, 2), "spend_lift_pct": round(spend_lift * 100, 1)},
    }]


# ── GA4-based evaluators (clientes sem tracking próprio) ─────────────────────

def _ga4_daily_metrics(property_id: str, refresh_token: str, days: int = 8) -> list[dict]:
    """Busca métricas diárias GA4 dos últimos `days` dias até ontem inclusive."""
    from .ga4_reporting import _get_token, _GA4_API_URL
    token = _get_token(refresh_token)
    if not token:
        return []
    url     = _GA4_API_URL.format(property_id=property_id)
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    start   = (_now().date() - timedelta(days=days)).isoformat()
    end     = (_now().date() - timedelta(days=1)).isoformat()
    body = {
        "dateRanges": [{"startDate": start, "endDate": end}],
        "dimensions": [{"name": "date"}],
        "metrics":    [{"name": "sessions"}, {"name": "conversions"}, {"name": "purchaseRevenue"}],
        "orderBys":   [{"dimension": {"dimensionName": "date"}}],
    }
    try:
        resp = httpx.post(url, json=body, headers=headers, timeout=15.0)
        if resp.status_code != 200:
            logger.debug("_ga4_daily_metrics HTTP %s — %s", resp.status_code, resp.text[:120])
            return []
        result = []
        for row in resp.json().get("rows", []):
            d    = row["dimensionValues"][0]["value"]  # YYYYMMDD
            vals = row.get("metricValues", [])
            result.append({
                "date":        f"{d[:4]}-{d[4:6]}-{d[6:]}",
                "sessions":    int(float(vals[0]["value"]))   if len(vals) > 0 else 0,
                "conversions": float(vals[1]["value"]) if len(vals) > 1 else 0.0,
                "revenue":     float(vals[2]["value"]) if len(vals) > 2 else 0.0,
            })
        return result
    except Exception as exc:
        logger.debug("_ga4_daily_metrics(%s): %s", property_id, exc)
        return []


def _eval_ga4_sessions_drop(rule: dict, client: dict) -> list[dict]:
    """Sessões GA4 de ontem caíram X% vs mediana 7d. Para clientes GA4-only."""
    if not client.get("ga4_reporting_enabled") or not client.get("ga4_property_id"):
        return []
    refresh_token = client.get("google_ads_refresh_token")
    if not refresh_token:
        return []

    cfg             = rule.get("config") or {}
    drop_warning    = float(cfg.get("drop_warning_pct",  20))
    drop_critical   = float(cfg.get("drop_critical_pct", 40))
    min_active_days = int(cfg.get("min_active_days", 5))
    min_sessions    = int(cfg.get("min_daily_sessions", 50))

    rows = _ga4_daily_metrics(client["ga4_property_id"], refresh_token, days=8)
    if not rows:
        return []

    yesterday     = (_now().date() - timedelta(days=1)).isoformat()
    yesterday_row = next((r for r in rows if r["date"] == yesterday), None)
    baseline_rows = [r for r in rows if r["date"] != yesterday]
    if not yesterday_row or not baseline_rows:
        return []

    daily = [r["sessions"] for r in baseline_rows]
    active_days = sum(1 for v in daily if v > 0)
    if active_days < min_active_days:
        return []
    baseline_ref = statistics.median(daily)
    if baseline_ref < min_sessions:
        return []

    drop = (baseline_ref - yesterday_row["sessions"]) / baseline_ref
    if drop < drop_warning / 100:
        return []

    pixel = client.get("pixel_id") or "unknown"
    sev   = "critical" if drop >= drop_critical / 100 else "warning"
    return [{
        "fingerprint": f"ga4_sessions_drop:{client['id']}:{yesterday}",
        "title":       f"Queda de sessões GA4 — {pixel}",
        "message":     (
            f"Sessões ontem: {yesterday_row['sessions']:,} vs mediana 7d {baseline_ref:,.0f}/dia "
            f"(queda {drop * 100:.0f}%). Verifique o site e campanhas."
        ).replace(",", "_").replace(".", ",").replace("_", "."),
        "severity": sev,
        "data":     {"sessions_yesterday": yesterday_row["sessions"],
                     "baseline_median": round(baseline_ref, 1),
                     "drop_pct": round(drop * 100, 1)},
    }]


def _eval_ga4_conversions_drop(rule: dict, client: dict) -> list[dict]:
    """Conversões GA4 de ontem caíram X% vs mediana 7d. Para clientes GA4-only."""
    if not client.get("ga4_reporting_enabled") or not client.get("ga4_property_id"):
        return []
    refresh_token = client.get("google_ads_refresh_token")
    if not refresh_token:
        return []

    cfg             = rule.get("config") or {}
    drop_warning    = float(cfg.get("drop_warning_pct",  30))
    drop_critical   = float(cfg.get("drop_critical_pct", 55))
    min_active_days = int(cfg.get("min_active_days", 5))

    rows = _ga4_daily_metrics(client["ga4_property_id"], refresh_token, days=8)
    if not rows:
        return []

    yesterday     = (_now().date() - timedelta(days=1)).isoformat()
    yesterday_row = next((r for r in rows if r["date"] == yesterday), None)
    baseline_rows = [r for r in rows if r["date"] != yesterday]
    if not yesterday_row or not baseline_rows:
        return []

    daily_conv  = [r["conversions"] for r in baseline_rows]
    active_days = sum(1 for v in daily_conv if v > 0)
    if active_days < min_active_days:
        return []
    baseline_ref = statistics.median(daily_conv)
    if baseline_ref < 1:
        return []

    drop = (baseline_ref - yesterday_row["conversions"]) / baseline_ref
    if drop < drop_warning / 100:
        return []

    pixel = client.get("pixel_id") or "unknown"
    sev   = "critical" if drop >= drop_critical / 100 else "warning"
    return [{
        "fingerprint": f"ga4_conversions_drop:{client['id']}:{yesterday}",
        "title":       f"Queda de conversões GA4 — {pixel}",
        "message":     (
            f"Conversões GA4 ontem: {yesterday_row['conversions']:.0f} vs mediana 7d "
            f"{baseline_ref:.0f}/dia (queda {drop * 100:.0f}%). Verifique campanhas e checkout."
        ),
        "severity": sev,
        "data":     {"conversions_yesterday": yesterday_row["conversions"],
                     "baseline_median": round(baseline_ref, 1),
                     "drop_pct": round(drop * 100, 1)},
    }]


def _eval_ga4_zero_conversions(rule: dict, client: dict) -> list[dict]:
    """Zero conversões GA4 ontem vs baseline 7d. Para clientes GA4-only."""
    if not client.get("ga4_reporting_enabled") or not client.get("ga4_property_id"):
        return []
    refresh_token = client.get("google_ads_refresh_token")
    if not refresh_token:
        return []

    cfg             = rule.get("config") or {}
    min_active_days = int(cfg.get("min_active_days", 5))

    rows = _ga4_daily_metrics(client["ga4_property_id"], refresh_token, days=8)
    if not rows:
        return []

    yesterday     = (_now().date() - timedelta(days=1)).isoformat()
    yesterday_row = next((r for r in rows if r["date"] == yesterday), None)
    baseline_rows = [r for r in rows if r["date"] != yesterday]
    if not yesterday_row:
        return []

    daily_conv  = [r["conversions"] for r in baseline_rows]
    active_days = sum(1 for v in daily_conv if v > 0)
    if active_days < min_active_days:
        return []
    baseline_ref = statistics.median(daily_conv)
    if baseline_ref < 1:
        return []

    if yesterday_row["conversions"] > 0:
        return []

    pixel = client.get("pixel_id") or "unknown"
    return [{
        "fingerprint": f"ga4_zero_conversions:{client['id']}:{yesterday}",
        "title":       f"Zero conversões GA4 ontem — {pixel}",
        "message":     (
            f"Nenhuma conversão GA4 ontem ({yesterday}) vs mediana 7d de "
            f"{baseline_ref:.0f} conversões/dia. Verifique campanhas e checkout."
        ),
        "severity": "critical",
        "data":     {"conversions_yesterday": yesterday_row["conversions"],
                     "baseline_median": round(baseline_ref, 1), "date": yesterday},
    }]


def _eval_visibility_drop(rule: dict, client: dict) -> list[dict]:
    """Taxa de menção em IA caiu X% vs o mês anterior."""
    cfg          = rule.get("config") or {}
    warn_pct     = float(cfg.get("drop_warning_pct",  20))
    crit_pct     = float(cfg.get("drop_critical_pct", 40))

    sb   = get_supabase()
    now  = _now()
    # Mês atual e mês anterior
    this_month = _month_start(now)
    prev_month = _month_start(now.replace(day=1) - timedelta(days=1))

    try:
        def _rate(month: str) -> Optional[float]:
            rows = (
                sb.table("ai_visibility_metrics")
                .select("own_brand_mentioned")
                .eq("client_id", client["id"])
                .gte("date", month)
                .lt("date", _month_start(
                    datetime.fromisoformat(month).replace(day=28) + timedelta(days=4)
                ))
                .execute()
            ).data or []
            if not rows:
                return None
            mentioned = sum(1 for r in rows if r.get("own_brand_mentioned"))
            return mentioned / len(rows)

        rate_now  = _rate(this_month)
        rate_prev = _rate(prev_month)
    except Exception as exc:
        logger.debug("_eval_visibility_drop(%s): %s", client.get("pixel_id"), exc)
        return []

    if rate_now is None or rate_prev is None or rate_prev == 0:
        return []

    drop = (rate_prev - rate_now) / rate_prev
    if drop < warn_pct / 100:
        return []

    pixel = client.get("pixel_id") or "unknown"
    sev   = "critical" if drop >= crit_pct / 100 else "warning"
    return [{
        "fingerprint": f"visibility_drop:{client['id']}:{this_month}",
        "title":       f"Queda de visibilidade em IA — {pixel}",
        "message":     (
            f"Taxa de menção em IA: {rate_now * 100:.0f}% vs {rate_prev * 100:.0f}% mês anterior "
            f"(queda de {drop * 100:.0f}%). Revise conteúdo e estratégia de SEO para IA."
        ),
        "severity": sev,
        "data":     {"mention_rate": round(rate_now, 4), "prev_mention_rate": round(rate_prev, 4),
                     "drop_pct": round(drop * 100, 1), "month": this_month},
    }]


def _eval_visibility_sentiment_spike(rule: dict, client: dict) -> list[dict]:
    """Menções negativas passaram de 20% em um mês."""
    cfg       = rule.get("config") or {}
    threshold = float(cfg.get("negative_pct", 20))

    sb  = get_supabase()
    now = _now()
    month = _month_start(now)

    try:
        rows = (
            sb.table("ai_visibility_metrics")
            .select("own_brand_mentioned,own_brand_sentiment")
            .eq("client_id", client["id"])
            .gte("date", month)
            .execute()
        ).data or []
    except Exception as exc:
        logger.debug("_eval_visibility_sentiment_spike(%s): %s", client.get("pixel_id"), exc)
        return []

    mentioned = [r for r in rows if r.get("own_brand_mentioned")]
    if len(mentioned) < 5:
        return []  # sem baseline suficiente

    negative_pct = (
        sum(1 for r in mentioned if r.get("own_brand_sentiment") == "negative")
        / len(mentioned) * 100
    )
    if negative_pct < threshold:
        return []

    pixel = client.get("pixel_id") or "unknown"
    return [{
        "fingerprint": f"visibility_negative_spike:{client['id']}:{month}",
        "title":       f"Sentimento negativo em IA — {pixel}",
        "message":     (
            f"{negative_pct:.0f}% das menções da marca em IAs são negativas este mês "
            f"(alerta em {threshold:.0f}%). Verifique reputação online."
        ),
        "severity": "critical",
        "data":     {"negative_pct": round(negative_pct, 1), "threshold": threshold,
                     "mentions": len(mentioned), "month": month},
    }]


def _eval_visibility_stale(rule: dict, client: dict) -> list[dict]:
    """Dados de AI Visibility não atualizados há mais de 14 dias (alerta interno)."""
    cfg        = rule.get("config") or {}
    max_days   = int(cfg.get("max_days", 14))

    sb  = get_supabase()
    now = _now()
    cutoff = (now - timedelta(days=max_days)).isoformat()

    try:
        last = (
            sb.table("ai_visibility_imports")
            .select("imported_at")
            .eq("client_id", client["id"])
            .eq("status", "imported")
            .order("imported_at", desc=True)
            .limit(1)
            .execute()
        ).data
    except Exception as exc:
        logger.debug("_eval_visibility_stale(%s): %s", client.get("pixel_id"), exc)
        return []

    if not last:
        return []  # nunca importou — sem baseline pra alertar

    last_iso = last[0].get("imported_at") or ""
    if last_iso >= cutoff:
        return []

    days_ago = (now - datetime.fromisoformat(last_iso.replace("Z", "+00:00"))).days
    pixel    = client.get("pixel_id") or "unknown"
    return [{
        "fingerprint": f"visibility_stale:{client['id']}:{now.date().isoformat()}",
        "title":       f"AI Visibility desatualizado há {days_ago}d — {pixel}",
        "message":     (
            f"Último import de AI Visibility foi há {days_ago} dias. "
            f"Exporte o CSV do Ubersuggest e faça o upload no Noro."
        ),
        "severity": "warning",
        "data":     {"last_import_at": last_iso, "days_ago": days_ago, "max_days": max_days},
    }]


def _eval_merchant_disapproval_spike(rule: dict, client: dict) -> list[dict]:
    """Produtos reprovados passaram de X% do catálogo."""
    cfg       = rule.get("config") or {}
    threshold = float(cfg.get("disapproval_pct", 10))
    sb        = get_supabase()
    try:
        snap = (
            sb.table("merchant_feed_health_snapshots")
            .select("snapshot_date,total_products,disapproved_products,feed_health_score")
            .eq("client_id", client["id"])
            .order("snapshot_date", desc=True)
            .limit(1)
            .execute()
        ).data
    except Exception as exc:
        logger.debug("_eval_merchant_disapproval_spike(%s): %s", client.get("pixel_id"), exc)
        return []
    if not snap or not snap[0].get("total_products"):
        return []
    row      = snap[0]
    total    = row["total_products"]
    disapp   = row.get("disapproved_products") or 0
    pct      = disapp / total * 100
    if pct < threshold:
        return []
    pixel = client.get("pixel_id") or "unknown"
    return [{
        "fingerprint": f"merchant_disapproval_spike:{client['id']}:{row['snapshot_date']}",
        "title":       f"Reprovações no Merchant Center — {pixel}",
        "message":     (
            f"{disapp} de {total} produtos estão reprovados ({pct:.0f}%) no Merchant Center "
            f"em {row['snapshot_date']}. Feed health score: {row.get('feed_health_score', '?')}/100."
        ),
        "severity":    "critical" if pct >= threshold * 2 else "warning",
        "data":        {"total_products": total, "disapproved_products": disapp,
                        "disapproval_pct": round(pct, 1), "snapshot_date": row["snapshot_date"]},
    }]


def _eval_merchant_feed_health_drop(rule: dict, client: dict) -> list[dict]:
    """Feed health score caiu X pontos em relação à semana passada."""
    cfg        = rule.get("config") or {}
    min_drop   = int(cfg.get("min_drop_points", 15))
    sb         = get_supabase()
    try:
        snaps = (
            sb.table("merchant_feed_health_snapshots")
            .select("snapshot_date,feed_health_score")
            .eq("client_id", client["id"])
            .order("snapshot_date", desc=True)
            .limit(8)
            .execute()
        ).data
    except Exception as exc:
        logger.debug("_eval_merchant_feed_health_drop(%s): %s", client.get("pixel_id"), exc)
        return []
    if len(snaps) < 2:
        return []
    score_now  = snaps[0].get("feed_health_score")
    score_prev = snaps[-1].get("feed_health_score")
    if score_now is None or score_prev is None or score_prev == 0:
        return []
    drop = score_prev - score_now
    if drop < min_drop:
        return []
    pixel = client.get("pixel_id") or "unknown"
    return [{
        "fingerprint": f"merchant_feed_health_drop:{client['id']}:{snaps[0]['snapshot_date']}",
        "title":       f"Feed health caiu {drop} pontos — {pixel}",
        "message":     (
            f"Feed health score caiu de {score_prev} para {score_now} pontos "
            f"(queda de {drop}p) em {snaps[0]['snapshot_date']}."
        ),
        "severity":    "critical" if drop >= min_drop * 2 else "warning",
        "data":        {"score_now": score_now, "score_prev": score_prev, "drop": drop,
                        "snapshot_date": snaps[0]["snapshot_date"]},
    }]


def _eval_merchant_token_expired(rule: dict, client: dict) -> list[dict]:
    """refresh_token do Merchant Center está expirado (sync falhou)."""
    if not client.get("merchant_center_id"):
        return []
    health = (client.get("merchant_center_health") or "").lower()
    if health not in {"expired", "error", "unhealthy"}:
        return []
    pixel = client.get("pixel_id") or "unknown"
    return [{
        "fingerprint": f"merchant_token_expired:{client['id']}",
        "title":       f"Token Merchant Center inválido — {pixel}",
        "message":     f"A integração com o Google Merchant Center de {pixel} está com erro ({health}). Reconecte o acesso nas configurações.",
        "severity":    "critical",
        "data":        {"health": health, "merchant_center_id": client.get("merchant_center_id")},
    }]


def _eval_merchant_feed_not_updated(rule: dict, client: dict) -> list[dict]:
    """Feed não atualizado há mais de N dias."""
    if not client.get("merchant_center_id"):
        return []
    cfg      = rule.get("config") or {}
    max_days = int(cfg.get("max_days", 3))
    sb       = get_supabase()
    try:
        snap = (
            sb.table("merchant_feed_health_snapshots")
            .select("snapshot_date")
            .eq("client_id", client["id"])
            .order("snapshot_date", desc=True)
            .limit(1)
            .execute()
        ).data
    except Exception as exc:
        logger.debug("_eval_merchant_feed_not_updated(%s): %s", client.get("pixel_id"), exc)
        return []
    now = _now()
    if snap:
        last_date = snap[0]["snapshot_date"]
        days_ago  = (now.date() - datetime.fromisoformat(last_date).date()).days
    else:
        days_ago = 9999
        last_date = None
    if days_ago < max_days:
        return []
    pixel = client.get("pixel_id") or "unknown"
    return [{
        "fingerprint": f"merchant_feed_not_updated:{client['id']}:{now.date().isoformat()}",
        "title":       f"Feed Merchant Center parado há {days_ago}d — {pixel}",
        "message":     (
            f"Nenhum dado novo do Merchant Center em {days_ago} dias "
            f"(último snapshot: {last_date or 'nunca'}). Verifique a conexão."
        ),
        "severity":    "warning",
        "data":        {"last_snapshot_date": last_date, "days_ago": days_ago, "max_days": max_days},
    }]


_EVALUATORS = {
    "meta_token_expiring":      _eval_meta_token_expiring,
    "integration_unhealthy":    _eval_integration_unhealthy,
    "roas_below_goal":          _eval_roas_below_goal,
    "budget_overspent":         _eval_budget_overspent,
    "tracking_stopped":         _eval_tracking_stopped,
    "cpa_over_target":          _eval_cpa_over_target,
    "revenue_drop":             _eval_revenue_drop,
    "views_drop":               _eval_views_drop,
    "zero_sales":               _eval_zero_sales,
    "checkout_drop":            _eval_checkout_drop,
    "low_balance_meta":         _eval_low_balance_meta,
    "low_balance_google":       _eval_low_balance_google,
    "google_conversion_drop":   _eval_google_conversion_drop,
    "high_ticket_anomaly":      _eval_high_ticket_anomaly,
    # 4 novos checks
    "roas_drop_channel":        _eval_roas_drop_channel,
    "spend_below_expected":     _eval_spend_below_expected,
    "utm_null_ratio":           _eval_utm_null_ratio,
    "nonpaid_spike":            _eval_nonpaid_spike,
    # GA4-only clients
    "ga4_sessions_drop":        _eval_ga4_sessions_drop,
    "ga4_conversions_drop":     _eval_ga4_conversions_drop,
    "ga4_zero_conversions":     _eval_ga4_zero_conversions,
    # AI Visibility
    "visibility_drop":          _eval_visibility_drop,
    "visibility_negative_spike": _eval_visibility_sentiment_spike,
    "visibility_stale":         _eval_visibility_stale,
    # Merchant Center
    "merchant_disapproval_spike":  _eval_merchant_disapproval_spike,
    "merchant_feed_health_drop":   _eval_merchant_feed_health_drop,
    "merchant_token_expired":      _eval_merchant_token_expired,
    "merchant_feed_not_updated":   _eval_merchant_feed_not_updated,
}


# ── upsert + auto-resolve ────────────────────────────────────────────────────

def _upsert_findings(rule: dict, agency_id: str, findings: list[dict]) -> list[dict]:
    """
    Insert new alerts for findings without an open row. Returns the list of
    newly inserted findings (for downstream Slack notifications).
    """
    if not findings:
        return []

    sb  = get_supabase()
    fps = [f["fingerprint"] for f in findings]

    try:
        existing = (
            sb.table("alerts")
            .select("fingerprint")
            .in_("fingerprint", fps)
            .is_("resolved_at", "null")
            .execute()
        ).data or []
    except Exception as exc:
        logger.warning("alert_engine: failed to load existing alerts: %s", exc)
        return []

    open_fps    = {r["fingerprint"] for r in existing}
    new_findings: list[dict] = []
    for f in findings:
        if f["fingerprint"] in open_fps:
            continue  # already open — keep the original created_at
        try:
            sb.table("alerts").insert({
                "agency_id":     agency_id,
                "client_id":     f.get("client_id"),
                "alert_rule_id": rule["id"],
                "fingerprint":   f["fingerprint"],
                "severity":      f.get("severity") or rule.get("severity") or "warning",
                "title":         f["title"],
                "message":       f["message"],
                "type":          rule["rule_key"],  # legacy column
                "data":          f.get("data") or {},
                "is_resolved":   False,
            }).execute()
            new_findings.append(f)
        except Exception as exc:
            logger.warning("alert_engine insert failed (%s): %s", f["fingerprint"], exc)
    return new_findings


def _notify_slack(new_findings: list[dict], clients_by_id: dict) -> None:
    """Send Slack message for each newly created alert if the client has a webhook."""
    for f in new_findings:
        client  = clients_by_id.get(f.get("client_id") or "")
        webhook = (client or {}).get("slack_webhook_url")
        if not webhook:
            continue
        severity = f.get("severity", "warning")
        emoji    = ":red_circle:" if severity == "critical" else ":large_yellow_circle:"
        text     = f"{emoji} *{f['title']}*\n{f['message']}"
        try:
            httpx.post(webhook, json={"text": text}, timeout=5.0)
        except Exception as exc:
            logger.debug("alert_engine slack notify failed: %s", exc)


def _auto_resolve(rule: dict, current_fps: set[str]) -> int:
    """Mark open alerts of this rule as resolved if their fingerprint isn't in current findings."""
    sb = get_supabase()
    try:
        open_rows = (
            sb.table("alerts")
            .select("id, fingerprint")
            .eq("alert_rule_id", rule["id"])
            .is_("resolved_at", "null")
            .execute()
        ).data or []
    except Exception as exc:
        logger.warning("alert_engine load open failed: %s", exc)
        return 0

    resolved = 0
    now_iso = _now().isoformat()
    for row in open_rows:
        if row["fingerprint"] in current_fps:
            continue
        try:
            sb.table("alerts").update({
                "resolved_at": now_iso,
                "is_resolved": True,
            }).eq("id", row["id"]).execute()
            resolved += 1
        except Exception as exc:
            logger.warning("alert_engine resolve failed (%s): %s", row["id"], exc)
    return resolved


# ── main entry point ─────────────────────────────────────────────────────────

def _clients_for_rule(rule: dict) -> list[dict]:
    """
    Eligible clients for a rule. If rule.client_id is set → that client only;
    else all active clients in the rule's agency.
    """
    sb = get_supabase()
    cols = (
        "id, pixel_id, agency_id, slack_webhook_url, tracking_enabled, "
        "meta_token_expires_at, meta_ad_account_id, meta_access_token, "
        "meta_prepaid, google_prepaid, meta_balance_threshold, google_balance_threshold, "
        "ga4_property_id, ga4_reporting_enabled, google_ads_refresh_token, "
        + ", ".join(_INTEGRATION_HEALTH_COLS.values())
    )
    if rule.get("client_id"):
        rows = (
            sb.table("clients").select(cols)
            .eq("id", rule["client_id"]).eq("is_active", True)
            .execute()
        ).data or []
    else:
        rows = (
            sb.table("clients").select(cols)
            .eq("agency_id", rule["agency_id"]).eq("is_active", True)
            .execute()
        ).data or []
    for _r in rows:
        crypto.decrypt_client_secrets(_r)
    return rows


def run_alert_engine() -> dict:
    """Scheduler entry point. Returns counters for observability."""
    sb = get_supabase()
    try:
        rules = (
            sb.table("alert_rules")
            .select("id, agency_id, client_id, rule_key, name, severity, config, throttle_minutes")
            .eq("enabled", True)
            .execute()
        ).data or []
    except Exception as exc:
        logger.error("alert_engine: load rules failed: %s", exc)
        return {"rules": 0, "new": 0, "resolved": 0}

    # Overrides por cliente: a existência de uma regra client-specific (ligada OU
    # desligada) de mesmo (rule_key, name) anula a regra global para aquele
    # cliente — a versão do cliente é quem manda (liga/desliga por cliente).
    try:
        specific = (
            sb.table("alert_rules")
            .select("client_id, rule_key, name")
            .not_.is_("client_id", "null")
            .execute()
        ).data or []
    except Exception:
        specific = []
    overridden: dict = {}
    for s in specific:
        overridden.setdefault((s["rule_key"], s.get("name")), set()).add(s["client_id"])

    total_new = 0
    total_resolved = 0
    rules_evaluated = 0

    for rule in rules:
        evaluator = _EVALUATORS.get(rule["rule_key"])
        if not evaluator:
            logger.debug("alert_engine: no evaluator for rule_key=%s", rule["rule_key"])
            continue

        clients = _clients_for_rule(rule)
        # Regra global pula clientes que têm override próprio dessa (rule_key, name).
        if not rule.get("client_id"):
            skip = overridden.get((rule["rule_key"], rule.get("name")), set())
            if skip:
                clients = [c for c in clients if c["id"] not in skip]
        clients_by_id  = {c["id"]: c for c in clients}
        all_findings: list[dict] = []
        for client in clients:
            try:
                findings = evaluator(rule, client) or []
                for f in findings:
                    f["client_id"] = client["id"]
                all_findings.extend(findings)
            except Exception as exc:
                logger.warning(
                    "alert_engine: evaluator %s failed for client %s: %s",
                    rule["rule_key"], client.get("pixel_id"), exc,
                )

        new_findings = _upsert_findings(rule, rule["agency_id"], all_findings)
        resolved     = _auto_resolve(rule, {f["fingerprint"] for f in all_findings})
        _notify_slack(new_findings, clients_by_id)
        total_new      += len(new_findings)
        total_resolved += resolved
        rules_evaluated += 1
        if new_findings or resolved:
            logger.info(
                "alert_engine: rule=%s clients=%d findings=%d new=%d resolved=%d",
                rule["rule_key"], len(clients), len(all_findings), len(new_findings), resolved,
            )

    return {"rules": rules_evaluated, "new": total_new, "resolved": total_resolved}
