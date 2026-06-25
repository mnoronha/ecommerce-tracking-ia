"""
Orquestrador de aprovação de conteúdo.

  send_for_approval(piece_id, client_id, sent_to_email, deadline_days, auto_approve)
    → cria registro em content_approvals, envia email ao cliente

  handle_approved(approval_id, piece_id, client_id)
    → atualiza status, notifica agência, dispara publicação no Shopify

  handle_changes_requested(approval_id, piece_id, feedback, client_id)
    → volta briefing + peça pra "reviewing", notifica agência com feedback

  run_auto_approve_check()
    → verifica aprovações com deadline expirado e auto_approve_on_deadline=true

  send_deadline_reminders()
    → lembrete 24h antes do deadline
"""

from __future__ import annotations

import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

from ..config import settings
from ..database import get_supabase
from . import notify
from . import shopify_publisher

logger = logging.getLogger(__name__)


def _business_days_from_now(days: int) -> str:
    dt    = datetime.now(timezone.utc)
    added = 0
    while added < days:
        dt += timedelta(days=1)
        if dt.weekday() < 5:
            added += 1
    return dt.isoformat()


def send_for_approval(
    piece_id:      str,
    client_id:     str,
    sent_to_email: str,
    deadline_days: int  = 5,
    auto_approve:  bool = True,
) -> dict:
    """
    Gera token de aprovação, salva no banco e envia email ao cliente.
    Retorna {"ok": True, "approval_id": ..., "token": ..., "approval_url": ...}
    ou {"ok": False, "error": ...}.
    """
    sb = get_supabase()

    # Peça
    piece_rows = sb.table("content_pieces").select(
        "id,final_title,briefing_id"
    ).eq("id", piece_id).limit(1).execute().data
    if not piece_rows:
        return {"ok": False, "error": "peça não encontrada"}
    piece = piece_rows[0]

    # Versão mais recente
    ver_rows = (
        sb.table("content_piece_versions")
        .select("id,title,body_markdown")
        .eq("piece_id", piece_id)
        .order("version_number", desc=True)
        .limit(1)
        .execute()
    ).data
    if not ver_rows:
        return {"ok": False, "error": "nenhuma versão para enviar"}
    version = ver_rows[0]

    # Nome do cliente
    client_rows = sb.table("clients").select("name").eq("id", client_id).limit(1).execute().data
    client_name = client_rows[0]["name"] if client_rows else "Cliente"

    # Cancela aprovações pendentes anteriores
    sb.table("content_approvals").update({"status": "cancelled"}).eq("piece_id", piece_id).eq("status", "pending").execute()

    token    = secrets.token_urlsafe(32)
    deadline = _business_days_from_now(deadline_days)
    now      = datetime.now(timezone.utc).isoformat()

    row = sb.table("content_approvals").insert({
        "piece_id":                 piece_id,
        "version_id":               version["id"],
        "client_id":                client_id,
        "sent_to_email":            sent_to_email,
        "sent_at":                  now,
        "approval_link_token":      token,
        "deadline":                 deadline,
        "auto_approve_on_deadline": auto_approve,
        "status":                   "pending",
    }).execute().data[0]

    sb.table("content_pieces").update({
        "status":     "pending_client",
        "updated_at": now,
    }).eq("id", piece_id).execute()

    # Também atualiza briefing
    if piece.get("briefing_id"):
        sb.table("content_briefings").update({
            "status":     "pending_client",
            "updated_at": now,
        }).eq("id", piece["briefing_id"]).execute()

    # Email ao cliente
    approval_url  = f"{settings.DASHBOARD_URL.rstrip('/')}/approve/{token}"
    title         = version.get("title") or piece.get("final_title") or "Conteúdo para revisão"
    deadline_fmt  = (
        datetime.fromisoformat(deadline.replace("Z", "+00:00"))
        .strftime("%d/%m/%Y")
    )

    _send_client_email(sent_to_email, client_name, title, approval_url, deadline_fmt)

    logger.info("approval sent: piece=%s → %s deadline=%s", piece_id, sent_to_email, deadline_fmt)
    return {
        "ok":           True,
        "approval_id":  row["id"],
        "token":        token,
        "approval_url": approval_url,
    }


def handle_approved(approval_id: str, piece_id: str, client_id: str) -> None:
    """Dispara publicação no Shopify e notifica a agência."""
    try:
        result = shopify_publisher.publish_piece(piece_id, client_id)
        if result["ok"]:
            url = result.get("url", "")
            notify.notify_agency(
                subject=f"✅ Conteúdo aprovado e publicado",
                html_body=_agency_email_approved(url),
            )
        else:
            notify.notify_agency(
                subject="✅ Aprovado — publicação Shopify falhou",
                html_body=_agency_email_publish_failed(result.get("error", "")),
            )
    except Exception as exc:
        logger.error("handle_approved: %s", exc)


def handle_changes_requested(
    approval_id: str,
    piece_id:    str,
    feedback:    str,
    client_id:   str,
) -> None:
    """Volta peça + briefing para 'reviewing' e notifica a agência com o feedback."""
    sb  = get_supabase()
    now = datetime.now(timezone.utc).isoformat()

    sb.table("content_pieces").update({
        "status":     "reviewing",
        "updated_at": now,
    }).eq("id", piece_id).execute()

    piece_rows = sb.table("content_pieces").select("briefing_id").eq("id", piece_id).limit(1).execute().data
    if piece_rows and piece_rows[0].get("briefing_id"):
        sb.table("content_briefings").update({
            "status":     "reviewing",
            "updated_at": now,
        }).eq("id", piece_rows[0]["briefing_id"]).execute()

    try:
        notify.notify_agency(
            subject="🔄 Cliente solicitou revisão de conteúdo",
            html_body=_agency_email_changes(feedback),
        )
    except Exception as exc:
        logger.error("handle_changes_requested notify: %s", exc)


def run_auto_approve_check() -> int:
    """
    Aprova automaticamente peças cujo deadline expirou.
    Retorna número de peças auto-aprovadas.
    """
    sb  = get_supabase()
    now = datetime.now(timezone.utc).isoformat()
    rows = (
        sb.table("content_approvals")
        .select("id,piece_id,client_id")
        .eq("status", "pending")
        .eq("auto_approve_on_deadline", True)
        .lte("deadline", now)
        .execute()
    ).data or []

    count = 0
    for row in rows:
        sb.table("content_approvals").update({
            "status":       "approved",
            "responded_at": now,
            "feedback":     "Auto-aprovado por expiração de prazo",
        }).eq("id", row["id"]).execute()
        sb.table("content_pieces").update({
            "status":     "approved",
            "updated_at": now,
        }).eq("id", row["piece_id"]).execute()
        handle_approved(row["id"], row["piece_id"], row["client_id"])
        count += 1

    if count:
        logger.info("auto_approve: %d peça(s) auto-aprovada(s)", count)
    return count


def send_deadline_reminders() -> None:
    """Envia lembrete 24h antes do deadline."""
    sb  = get_supabase()
    now = datetime.now(timezone.utc)
    rows = (
        sb.table("content_approvals")
        .select("id,sent_to_email,deadline")
        .eq("status", "pending")
        .lte("deadline", (now + timedelta(hours=25)).isoformat())
        .gte("deadline", now.isoformat())
        .execute()
    ).data or []

    for row in rows:
        email = row.get("sent_to_email")
        if not email:
            continue
        try:
            deadline_dt  = datetime.fromisoformat((row["deadline"] or "").replace("Z", "+00:00"))
            deadline_fmt = deadline_dt.strftime("%d/%m/%Y às %H:%M")
            from . import resend as _email
            _email.send_email(
                to=email,
                subject=f"[{settings.AGENCY_NAME}] Lembrete: conteúdo aguarda sua aprovação",
                html_body=_reminder_email(deadline_fmt),
                from_name=settings.AGENCY_NAME,
            )
        except Exception as exc:
            logger.error("deadline_reminder: %s → %s", email, exc)


# ── Email templates ───────────────────────────────────────────────────────────

def _send_client_email(to: str, client_name: str, title: str, url: str, deadline: str) -> None:
    html_body = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"></head>
<body style="font-family:Arial,sans-serif;background:#f9fafb;padding:24px">
<div style="max-width:560px;margin:0 auto;background:#fff;border-radius:12px;border:1px solid #e5e7eb;padding:32px">
  <p style="margin:0 0 6px;font-size:12px;color:#9ca3af;text-transform:uppercase;letter-spacing:1px">{settings.AGENCY_NAME}</p>
  <h1 style="margin:0 0 20px;font-size:22px;color:#111827">Conteúdo aguardando sua aprovação</h1>
  <p style="color:#374151;font-size:15px;margin:0 0 8px">Olá, {client_name}!</p>
  <p style="color:#374151;font-size:14px;margin:0 0 24px">
    Preparamos um novo conteúdo para você revisar e aprovar:
  </p>
  <div style="background:#f3f4f6;border-radius:8px;padding:16px;margin:0 0 24px">
    <p style="margin:0;font-size:15px;font-weight:600;color:#111827">{title}</p>
  </div>
  <a href="{url}"
     style="display:inline-block;background:#4f46e5;color:#fff;text-decoration:none;padding:14px 28px;border-radius:8px;font-size:15px;font-weight:600;margin:0 0 24px">
    Revisar e aprovar →
  </a>
  <p style="margin:0;font-size:12px;color:#9ca3af">
    Prazo: <strong>{deadline}</strong>. Após este prazo, o conteúdo é aprovado automaticamente.
  </p>
</div>
</body></html>"""
    try:
        from . import resend as _email
        _email.send_email(
            to=to,
            subject=f"[{settings.AGENCY_NAME}] Conteúdo aguardando aprovação: {title}",
            html_body=html_body,
            from_name=settings.AGENCY_NAME,
        )
    except Exception as exc:
        logger.error("client email falhou para %s: %s", to, exc)


def _agency_email_approved(url: str) -> str:
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"></head>
<body style="font-family:Arial,sans-serif;background:#f9fafb;padding:24px">
<div style="max-width:540px;margin:0 auto;background:#fff;border-radius:8px;border:1px solid #e5e7eb;padding:28px">
  <h2 style="margin:0 0 12px;color:#111827">✅ Conteúdo aprovado e publicado</h2>
  <p style="color:#374151;font-size:14px;margin:0 0 8px">Publicado em:</p>
  <a href="{url}" style="color:#4f46e5;font-size:14px">{url}</a>
</div></body></html>"""


def _agency_email_publish_failed(error: str) -> str:
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"></head>
<body style="font-family:Arial,sans-serif;background:#f9fafb;padding:24px">
<div style="max-width:540px;margin:0 auto;background:#fff;border-radius:8px;border:1px solid #e5e7eb;padding:28px">
  <h2 style="margin:0 0 12px;color:#111827">✅ Aprovado — publicação Shopify falhou</h2>
  <p style="background:#fef2f2;border-radius:6px;padding:12px;color:#b91c1c;font-size:13px;margin:0 0 12px">
    {error or "erro desconhecido"}
  </p>
  <a href="{settings.DASHBOARD_URL}" style="color:#4f46e5">Abrir dashboard para publicar manualmente</a>
</div></body></html>"""


def _agency_email_changes(feedback: str) -> str:
    fb = feedback or "Nenhum comentário."
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"></head>
<body style="font-family:Arial,sans-serif;background:#f9fafb;padding:24px">
<div style="max-width:540px;margin:0 auto;background:#fff;border-radius:8px;border:1px solid #e5e7eb;padding:28px">
  <h2 style="margin:0 0 12px;color:#111827">🔄 Cliente solicitou revisão</h2>
  <p style="color:#374151;font-size:14px;margin:0 0 12px">Feedback:</p>
  <div style="background:#fffbeb;border-radius:6px;padding:14px;border-left:3px solid #f59e0b;margin:0 0 16px">
    <p style="margin:0;font-size:14px;color:#92400e;white-space:pre-wrap">{fb}</p>
  </div>
  <a href="{settings.DASHBOARD_URL}" style="color:#4f46e5">Abrir dashboard para ajustar</a>
</div></body></html>"""


def _reminder_email(deadline_fmt: str) -> str:
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"></head>
<body style="font-family:Arial,sans-serif;background:#f9fafb;padding:24px">
<div style="max-width:540px;margin:0 auto;background:#fff;border-radius:8px;border:1px solid #e5e7eb;padding:28px">
  <h2 style="margin:0 0 12px;color:#111827">Lembrete de aprovação</h2>
  <p style="color:#374151;font-size:14px;margin:0 0 16px">
    O prazo para aprovação do conteúdo enviado por <strong>{settings.AGENCY_NAME}</strong>
    se encerra em <strong>{deadline_fmt}</strong>.
  </p>
  <p style="margin:0;font-size:13px;color:#6b7280">
    Após este prazo o conteúdo será aprovado automaticamente.
  </p>
</div></body></html>"""
