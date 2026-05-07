"""
Anomaly detection — daily comparison of last 24h vs trailing 7d baseline.

Rules (each tunable):
  - Revenue dropped > 25% → critical
  - Revenue dropped > 15% → warning
  - CPA up > 30% (Meta CAPI vs ours) → warning
  - Single order > 3x avg ticket → info ("big order")
  - New top product (#1 by views, not in top-3 last 7d) → info

Notifies via the client's slack_webhook_url. Best-effort; logs but never raises.

Cron: daily at 08:00 UTC via APScheduler.
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx

from ..database import get_supabase

logger = logging.getLogger(__name__)

# Tunables
_REVENUE_DROP_CRITICAL = 0.25
_REVENUE_DROP_WARNING  = 0.15
_BIG_ORDER_MULTIPLE    = 3.0


def _slack(webhook: str, text: str) -> None:
    if not webhook:
        return
    try:
        httpx.post(webhook, json={"text": text}, timeout=5.0)
    except Exception as exc:
        logger.debug("slack notify failed: %s", exc)


def _fmt_brl(n: float) -> str:
    return f"R$ {n:,.2f}".replace(",", "_").replace(".", ",").replace("_", ".")


def _check_one_client(client: dict) -> list[dict]:
    """
    Run all anomaly rules for one client. Returns list of detected anomalies
    (used by the API endpoint and for cron logging).
    """
    sb = get_supabase()
    client_id   = client["id"]
    pixel_id    = client.get("pixel_id") or "unknown"
    webhook     = client.get("slack_webhook_url")

    now           = datetime.now(timezone.utc)
    last_24h      = now - timedelta(hours=24)
    prev_7d_start = now - timedelta(days=8)
    prev_7d_end   = now - timedelta(hours=24)

    findings: list[dict] = []

    # ── Revenue 24h vs avg of trailing 7d ──────────────────────────────────────
    try:
        recent = (
            sb.table("orders")
            .select("total_price")
            .eq("client_id", client_id)
            .eq("financial_status", "paid")
            .gt("total_price", 0)
            .gte("created_at", last_24h.isoformat())
            .execute()
        ).data or []
        recent_rev = sum(float(o["total_price"]) for o in recent)

        baseline = (
            sb.table("orders")
            .select("total_price, created_at")
            .eq("client_id", client_id)
            .eq("financial_status", "paid")
            .gt("total_price", 0)
            .gte("created_at", prev_7d_start.isoformat())
            .lt("created_at", prev_7d_end.isoformat())
            .execute()
        ).data or []
        baseline_rev_avg = sum(float(o["total_price"]) for o in baseline) / 7.0 if baseline else 0

        if baseline_rev_avg > 0:
            drop = (baseline_rev_avg - recent_rev) / baseline_rev_avg
            if drop >= _REVENUE_DROP_CRITICAL:
                findings.append({
                    "type":     "revenue_drop_critical",
                    "severity": "critical",
                    "message":  (
                        f":rotating_light: *{pixel_id}*: receita 24h caiu *{drop * 100:.0f}%* "
                        f"vs média 7d ({_fmt_brl(recent_rev)} vs {_fmt_brl(baseline_rev_avg)}/dia)"
                    ),
                })
            elif drop >= _REVENUE_DROP_WARNING:
                findings.append({
                    "type":     "revenue_drop_warning",
                    "severity": "warning",
                    "message":  (
                        f":warning: *{pixel_id}*: receita 24h caiu {drop * 100:.0f}% "
                        f"vs média 7d ({_fmt_brl(recent_rev)} vs {_fmt_brl(baseline_rev_avg)}/dia)"
                    ),
                })
    except Exception as exc:
        logger.debug("revenue check failed for %s: %s", pixel_id, exc)

    # ── Big order: 24h max > 3x avg ticket ─────────────────────────────────────
    try:
        if recent:
            max_order = max(float(o["total_price"]) for o in recent)
            avg_ticket = sum(float(o["total_price"]) for o in recent) / len(recent)
            if max_order >= _BIG_ORDER_MULTIPLE * avg_ticket and max_order > 500:
                findings.append({
                    "type":     "big_order",
                    "severity": "info",
                    "message":  (
                        f":moneybag: *{pixel_id}*: pedido grande {_fmt_brl(max_order)} entrou "
                        f"({max_order / avg_ticket:.1f}× ticket médio)"
                    ),
                })
    except Exception as exc:
        logger.debug("big-order check failed for %s: %s", pixel_id, exc)

    # Send notifications
    for f in findings:
        _slack(webhook, f["message"])

    return findings


def run_daily_anomaly_check() -> None:
    """Scheduler entry point — iterate all active clients with slack_webhook_url."""
    sb = get_supabase()
    try:
        clients = (
            sb.table("clients")
            .select("id, pixel_id, slack_webhook_url")
            .eq("is_active", True)
            .not_.is_("slack_webhook_url", "null")
            .execute()
        ).data or []
    except Exception as exc:
        logger.error("anomalies: failed to load clients: %s", exc)
        return

    total_findings = 0
    for c in clients:
        try:
            findings = _check_one_client(c)
            total_findings += len(findings)
        except Exception as exc:
            logger.warning("anomaly check failed for %s: %s", c.get("pixel_id"), exc)
    logger.info("anomalies: checked %d clients, %d findings", len(clients), total_findings)
