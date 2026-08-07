"""
Finance alerts — daily cron that checks upcoming due dates and:
  1. Creates dashboard notifications (finance_notifications)
  2. Sends email to FINANCE_ALERT_EMAIL (contato@norolabs.com.br)

Runs daily at 09:00 UTC (06:00 BRT).
Alert windows: 3 days before and 1 day before due date.
"""

import logging
from datetime import date, timedelta, datetime, timezone

from ..config import settings
from ..database import get_supabase
from . import resend as email_service

logger = logging.getLogger(__name__)

FINANCE_ALERT_EMAIL = "contato@norolabs.com.br"
ALERT_DAYS = [3, 1]   # avisa 3 dias e 1 dia antes

_CATEGORY_LABEL = {
    "tool": "Ferramenta", "service": "Serviço", "freelancer": "Freelancer",
    "tax": "Imposto", "salary": "Salário", "other": "Outro",
}


def _already_notified(sb, ref_id: str, ref_type: str, days_before: int) -> bool:
    """Evita duplicar notificação para o mesmo item+janela no mesmo dia."""
    window_start = (datetime.now(timezone.utc) - timedelta(hours=20)).isoformat()
    rows = sb.table("finance_notifications").select("id").eq("reference_id", ref_id).eq("reference_type", ref_type).gte("created_at", window_start).execute().data or []
    return len(rows) > 0


def _create_notification(sb, ntype: str, ref_id: str, ref_type: str, message: str) -> None:
    try:
        sb.table("finance_notifications").insert({
            "type":           ntype,
            "reference_id":   ref_id,
            "reference_type": ref_type,
            "message":        message,
        }).execute()
    except Exception as exc:
        logger.warning("finance_alerts: failed to create notification: %s", exc)


def run_daily_finance_alerts() -> None:
    """Entry point para o cron diário."""
    sb    = get_supabase()
    today = date.today()

    receivable_alerts: list[dict] = []
    payable_alerts:    list[dict] = []
    overdue_alerts:    list[dict] = []

    # ── Recebíveis vencendo em 1 ou 3 dias ────────────────────────────────────
    for days in ALERT_DAYS:
        target = (today + timedelta(days=days)).isoformat()
        rows = sb.table("receivables").select("id, client_name, description, amount, due_date").eq("due_date", target).eq("status", "pending").execute().data or []
        for r in rows:
            if _already_notified(sb, r["id"], "receivable", days):
                continue
            msg = f"Recebível de R$ {float(r['amount']):,.2f} ({r['client_name']}) vence em {days} dia{'s' if days > 1 else ''} ({r['due_date']})"
            _create_notification(sb, "receivable_due", r["id"], "receivable", msg)
            receivable_alerts.append({**r, "days_before": days})

    # ── Contas a pagar vencendo em 1 ou 3 dias ────────────────────────────────
    for days in ALERT_DAYS:
        target = (today + timedelta(days=days)).isoformat()
        rows = sb.table("payables").select("id, description, supplier, amount, due_date, category").eq("due_date", target).eq("status", "pending").execute().data or []
        for p in rows:
            if _already_notified(sb, p["id"], "payable", days):
                continue
            cat = _CATEGORY_LABEL.get(p.get("category", "other"), "Outro")
            msg = f"Conta a pagar '{p['description']}' ({cat}) de R$ {float(p['amount']):,.2f} vence em {days} dia{'s' if days > 1 else ''} ({p['due_date']})"
            _create_notification(sb, "payable_due", p["id"], "payable", msg)
            payable_alerts.append({**p, "days_before": days})

    # ── Recebíveis em atraso (pendentes + vencidos) ───────────────────────────
    overdue_rows = sb.table("receivables").select("id, client_name, description, amount, due_date").eq("status", "pending").lt("due_date", today.isoformat()).execute().data or []
    for r in overdue_rows:
        days_late = (today - date.fromisoformat(r["due_date"])).days
        if days_late == 1 or days_late % 7 == 0:  # avisa no 1º dia e semanalmente
            if not _already_notified(sb, r["id"], "receivable", 0):
                msg = f"ATRASO {days_late}d: Recebível de R$ {float(r['amount']):,.2f} ({r['client_name']}) — venceu em {r['due_date']}"
                _create_notification(sb, "overdue", r["id"], "receivable", msg)
                # Atualiza status para overdue
                sb.table("receivables").update({"status": "overdue"}).eq("id", r["id"]).execute()
                overdue_alerts.append({**r, "days_late": days_late})

    # ── Contratos expirando em 30 dias ────────────────────────────────────────
    expiry_target = (today + timedelta(days=30)).isoformat()
    expiring = sb.table("contracts").select("id, client_name, monthly_value, end_date").eq("status", "active").lte("end_date", expiry_target).gte("end_date", today.isoformat()).execute().data or []
    for c in expiring:
        if not _already_notified(sb, c["id"], "contract", 30):
            days_left = (date.fromisoformat(c["end_date"]) - today).days
            msg = f"Contrato de {c['client_name']} (R$ {float(c['monthly_value']):,.2f}/mês) vence em {days_left} dias ({c['end_date']})"
            _create_notification(sb, "contract_expiring", c["id"], "contract", msg)

    # ── Envia email consolidado se houver alertas ──────────────────────────────
    total = len(receivable_alerts) + len(payable_alerts) + len(overdue_alerts)
    if total > 0:
        _send_alert_email(today, receivable_alerts, payable_alerts, overdue_alerts)

    logger.info(
        "finance_alerts: %d recebíveis, %d pagamentos, %d atrasos",
        len(receivable_alerts), len(payable_alerts), len(overdue_alerts),
    )


def _fmt_brl(val: float) -> str:
    return f"R$ {val:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _send_alert_email(today: date, receivables: list, payables: list, overdue: list) -> None:
    def _rows(items: list, key_label: str) -> str:
        if not items:
            return ""
        rows = ""
        for it in items:
            days = it.get("days_before")
            label = f"<b>{it.get('client_name') or it.get('description', '')}</b>"
            amount = _fmt_brl(float(it["amount"]))
            due    = it["due_date"]
            badge  = (
                f'<span style="background:#fef3c7;color:#92400e;padding:1px 6px;border-radius:4px;font-size:11px">vence em {days}d</span>'
                if days else
                f'<span style="background:#fee2e2;color:#991b1b;padding:1px 6px;border-radius:4px;font-size:11px">{it.get("days_late",0)}d em atraso</span>'
            )
            rows += f"<tr><td style='padding:8px 0;border-bottom:1px solid #f3f4f6'>{label}</td><td style='padding:8px 0;text-align:right;border-bottom:1px solid #f3f4f6'>{amount}</td><td style='padding:8px 0;text-align:right;border-bottom:1px solid #f3f4f6;font-size:12px;color:#6b7280'>{due}</td><td style='padding:8px;border-bottom:1px solid #f3f4f6;text-align:right'>{badge}</td></tr>"
        return f"""
        <p style="margin:18px 0 6px;font-weight:700;color:#374151;font-size:13px">{key_label}</p>
        <table style="width:100%;border-collapse:collapse">
          <tr><th style="text-align:left;font-size:11px;color:#9ca3af;padding:4px 0">Item</th><th style="text-align:right;font-size:11px;color:#9ca3af">Valor</th><th style="text-align:right;font-size:11px;color:#9ca3af">Vencimento</th><th style="text-align:right;font-size:11px;color:#9ca3af">Status</th></tr>
          {rows}
        </table>"""

    recv_html  = _rows(receivables, "💰 A Receber")
    pay_html   = _rows(payables,    "💸 A Pagar")
    over_html  = _rows(overdue,     "🚨 Em Atraso")

    total_recv = sum(float(r["amount"]) for r in receivables)
    total_pay  = sum(float(p["amount"]) for p in payables)
    total_over = sum(float(o["amount"]) for o in overdue)

    dashboard_url = (settings.DASHBOARD_URL or "") + "/financeiro"

    html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"></head>
<body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;background:#f9fafb;margin:0;padding:0">
<div style="max-width:560px;margin:0 auto;padding:24px 16px">

  <div style="background:#1e1b4b;border-radius:8px 8px 0 0;padding:20px 24px">
    <h1 style="margin:0;color:#fff;font-size:20px;font-weight:700">Norolabs · Financeiro</h1>
    <p style="margin:4px 0 0;color:#8b9cf4;font-size:12px">Alertas de vencimento · {today.strftime('%d/%m/%Y')}</p>
  </div>

  <div style="background:#fff;border:1px solid #e5e7eb;border-top:none;border-radius:0 0 8px 8px;padding:24px">

    <div style="display:flex;gap:10px;margin-bottom:4px">
      {"" if not total_recv else f'<div style="flex:1;background:#f0fdf4;border:1px solid #bbf7d0;border-radius:6px;padding:12px;text-align:center"><p style="margin:0;font-size:11px;color:#6b7280">A Receber</p><p style="margin:4px 0 0;font-size:18px;font-weight:700;color:#111827">{_fmt_brl(total_recv)}</p></div>'}
      {"" if not total_pay  else f'<div style="flex:1;background:#fff7ed;border:1px solid #fed7aa;border-radius:6px;padding:12px;text-align:center"><p style="margin:0;font-size:11px;color:#6b7280">A Pagar</p><p style="margin:4px 0 0;font-size:18px;font-weight:700;color:#111827">{_fmt_brl(total_pay)}</p></div>'}
      {"" if not total_over else f'<div style="flex:1;background:#fef2f2;border:1px solid #fecaca;border-radius:6px;padding:12px;text-align:center"><p style="margin:0;font-size:11px;color:#6b7280">Em Atraso</p><p style="margin:4px 0 0;font-size:18px;font-weight:700;color:#dc2626">{_fmt_brl(total_over)}</p></div>'}
    </div>

    {recv_html}
    {pay_html}
    {over_html}

    <div style="margin-top:20px;text-align:center">
      <a href="{dashboard_url}" style="background:#6366f1;color:#fff;padding:10px 24px;border-radius:6px;text-decoration:none;font-size:13px;font-weight:600">Ver painel financeiro</a>
    </div>
  </div>

  <p style="color:#9ca3af;font-size:11px;text-align:center;margin-top:16px">Norolabs · alertas automáticos do sistema financeiro</p>
</div>
</body></html>"""

    try:
        total_alerts = len(receivables) + len(payables) + len(overdue)
        email_service.send_email(
            to=FINANCE_ALERT_EMAIL,
            subject=f"💼 Financeiro Norolabs · {total_alerts} alerta{'s' if total_alerts > 1 else ''} — {today.strftime('%d/%m')}",
            html_body=html,
        )
        logger.info("finance_alerts: email enviado para %s", FINANCE_ALERT_EMAIL)
    except Exception as exc:
        logger.error("finance_alerts: falha ao enviar email: %s", exc)
