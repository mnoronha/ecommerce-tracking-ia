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
from datetime import datetime, timezone, timedelta
from typing import Optional

import httpx

from ..database import get_supabase

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


_EVALUATORS = {
    "meta_token_expiring":   _eval_meta_token_expiring,
    "integration_unhealthy": _eval_integration_unhealthy,
    "roas_below_goal":       _eval_roas_below_goal,
    "budget_overspent":      _eval_budget_overspent,
    "tracking_stopped":      _eval_tracking_stopped,
    "cpa_over_target":       _eval_cpa_over_target,
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
        "id, pixel_id, agency_id, slack_webhook_url, "
        "meta_token_expires_at, "
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
    return rows


def run_alert_engine() -> dict:
    """Scheduler entry point. Returns counters for observability."""
    sb = get_supabase()
    try:
        rules = (
            sb.table("alert_rules")
            .select("id, agency_id, client_id, rule_key, severity, config, throttle_minutes")
            .eq("enabled", True)
            .execute()
        ).data or []
    except Exception as exc:
        logger.error("alert_engine: load rules failed: %s", exc)
        return {"rules": 0, "new": 0, "resolved": 0}

    total_new = 0
    total_resolved = 0
    rules_evaluated = 0

    for rule in rules:
        evaluator = _EVALUATORS.get(rule["rule_key"])
        if not evaluator:
            logger.debug("alert_engine: no evaluator for rule_key=%s", rule["rule_key"])
            continue

        clients        = _clients_for_rule(rule)
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
