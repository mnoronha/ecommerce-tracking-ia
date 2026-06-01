"""
Dispatcher unificado de notificações — email + WhatsApp.

Centraliza o roteamento:
  • Email  → sempre que há endereço configurado
  • WhatsApp → apenas para severidade ≥ EVOLUTION_MIN_SEVERITY (default: critical)

Uso nos outros serviços:
  from .notify import notify_agency, notify_alert, notify_health_issues

Cada função é best-effort (nunca levanta exceção).
"""

from __future__ import annotations

import html
import logging
import re
from typing import Optional

from ..config import settings
from . import resend as email_svc
from . import whatsapp as wa

logger = logging.getLogger(__name__)


# ── HTML → WhatsApp text ──────────────────────────────────────────────────────

def _html_to_wa(html_body: str, max_len: int = 2000) -> str:
    """
    Converte HTML simplificado em texto formatado para WhatsApp.
    Preserva negrito (<b>/<strong> → *texto*) e listas.
    """
    text = html_body
    # Remove <style>/<script> blocks
    text = re.sub(r"<(style|script)[^>]*>.*?</(style|script)>", "", text, flags=re.S | re.I)
    # Bold
    text = re.sub(r"<(b|strong)[^>]*>(.*?)</(b|strong)>", r"*\2*", text, flags=re.S | re.I)
    # Headings → bold
    text = re.sub(r"<h[1-6][^>]*>(.*?)</h[1-6]>", r"*\1*\n", text, flags=re.S | re.I)
    # Line breaks / paragraphs
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r"<p[^>]*>", "\n", text, flags=re.I)
    text = re.sub(r"</p>", "\n", text, flags=re.I)
    text = re.sub(r"<li[^>]*>", "\n• ", text, flags=re.I)
    text = re.sub(r"<tr[^>]*>", "\n", text, flags=re.I)
    text = re.sub(r"<td[^>]*>", "  ", text, flags=re.I)
    # Strip remaining tags
    text = re.sub(r"<[^>]+>", "", text)
    # Decode HTML entities
    text = html.unescape(text)
    # Collapse whitespace
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = text.strip()
    return text[:max_len]


# ── Core dispatchers ──────────────────────────────────────────────────────────

def notify_agency(
    subject:  str,
    html_body: str,
    wa_text:  Optional[str] = None,
    severity: str = "info",
) -> None:
    """
    Envia notificação para a agência por email e/ou WhatsApp.

    email  → AGENCY_NOTIFY_EMAIL (sempre que configurado)
    WA     → AGENCY_WHATSAPP, apenas se severity ≥ EVOLUTION_MIN_SEVERITY
    wa_text → se None, usa versão convertida do html_body (max 2000 chars)
    """
    email_to = settings.AGENCY_NOTIFY_EMAIL
    if email_to:
        try:
            email_svc.send_email(to=email_to, subject=subject, html_body=html_body)
        except Exception as exc:
            logger.error("notify_agency: email failed: %s", exc)

    if wa.severity_should_notify(severity):
        text = wa_text or _html_to_wa(html_body)
        try:
            wa.send_to_agency(text)
        except Exception as exc:
            logger.error("notify_agency: whatsapp failed: %s", exc)


def notify_alert(
    severity: str,
    title:    str,
    message:  str,
    client_name: str = "",
) -> None:
    """
    Envia um alerta pontual para a agência.
    Email sempre; WhatsApp apenas se severity qualifica.
    """
    sev_emoji = {"critical": "🔴", "warning": "🟡", "info": "🔵"}.get(severity, "⚪")
    sev_label = {"critical": "CRÍTICO", "warning": "ATENÇÃO", "info": "INFO"}.get(severity, severity.upper())

    subject = f"{sev_emoji} [{sev_label}] {title}"
    if client_name:
        subject = f"{sev_emoji} [{sev_label}] {client_name} — {title}"

    html_body = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"></head>
<body style="font-family:Arial,sans-serif;background:#f9fafb;padding:24px">
<div style="max-width:540px;margin:0 auto;background:#fff;border-radius:8px;border:1px solid #e5e7eb;padding:24px">
  <p style="margin:0 0 4px;font-size:11px;color:#9ca3af;text-transform:uppercase;letter-spacing:1px">
    Alerta de Tracking
  </p>
  <h2 style="margin:0 0 12px;font-size:17px;color:#111827">{title}</h2>
  {"<p style='margin:0 0 8px;font-size:12px;color:#6b7280'>Cliente: <strong>" + client_name + "</strong></p>" if client_name else ""}
  <p style="margin:0;color:#374151;font-size:13px;line-height:1.5">{message}</p>
</div>
</body></html>"""

    wa_text = (
        f"{sev_emoji} *{sev_label}*"
        + (f" — {client_name}" if client_name else "")
        + f"\n\n*{title}*\n{message}"
    )

    notify_agency(subject=subject, html_body=html_body, wa_text=wa_text, severity=severity)


def notify_health_issues(results: list[dict]) -> None:
    """
    Gera mensagem WhatsApp compacta para o health_monitor quando há problemas.
    Chamado após o email HTML já ter sido enviado por health_monitor.py.
    Só envia se houver pelo menos um finding crítico/warning.
    """
    issues = [
        (r["name"], f)
        for r in results
        for f in r.get("findings", [])
        if f["severity"] in ("critical", "warning")
    ]
    if not issues:
        return

    if not wa.severity_should_notify("critical"):
        return  # nenhuma severity qualifica

    lines = ["🚨 *Monitor de Tracking — Problemas detectados*\n"]
    for (client_name, finding) in issues[:10]:  # cap em 10 para não estouras o limite
        emoji = "🔴" if finding["severity"] == "critical" else "🟡"
        lines.append(f"{emoji} *{client_name}* — {finding['message']}")

    if len(issues) > 10:
        lines.append(f"\n... e mais {len(issues) - 10} problema(s). Ver email para detalhes.")

    wa.send_to_agency("\n".join(lines))


# ── Test helpers ──────────────────────────────────────────────────────────────

def test_email(to: Optional[str] = None) -> dict:
    """Envia um email de teste. Retorna status."""
    dest = to or settings.AGENCY_NOTIFY_EMAIL
    if not dest:
        return {"ok": False, "error": "Nenhum destinatário configurado (AGENCY_NOTIFY_EMAIL)"}
    ok, err = email_svc.send_email_with_error(
        to=dest,
        subject="✅ Teste de email — Ecommerce Tracking IA",
        html_body="""<!DOCTYPE html>
<html><head><meta charset="utf-8"></head>
<body style="font-family:Arial,sans-serif;padding:32px;background:#f9fafb">
<div style="max-width:480px;margin:0 auto;background:#fff;border-radius:8px;border:1px solid #e5e7eb;padding:28px">
  <h2 style="margin:0 0 12px;color:#111827">Email configurado ✅</h2>
  <p style="color:#6b7280;font-size:13px;margin:0">
    O sistema de notificações por email está funcionando corretamente.<br>
    Você receberá alertas críticos e relatórios automáticos neste endereço.
  </p>
</div>
</body></html>""",
    )
    result = {"ok": ok, "to": dest, "provider": "resend" if settings.RESEND_API_KEY else "smtp"}
    if not ok and err:
        result["error"] = err
    return result


def test_whatsapp(phone: Optional[str] = None) -> dict:
    """Envia uma mensagem de teste via WhatsApp. Retorna status."""
    dest = phone or settings.AGENCY_WHATSAPP
    if not dest:
        return {"ok": False, "error": "Nenhum número configurado (AGENCY_WHATSAPP)"}

    status = wa.check_instance_status()
    if not status.get("ok"):
        return {"ok": False, "error": f"Instância não conectada: {status.get('error') or status.get('state')}"}

    ok = wa.send_text(dest, (
        "✅ *Teste de WhatsApp — Ecommerce Tracking IA*\n\n"
        "Notificações via WhatsApp configuradas com sucesso.\n"
        "Você receberá alertas críticos de tracking aqui."
    ))
    return {"ok": ok, "to": dest, "instance": settings.EVOLUTION_INSTANCE}
