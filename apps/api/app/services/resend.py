"""
Email delivery via Resend API (preferred) with SMTP fallback.

Priority:
  1. RESEND_API_KEY set → use Resend
  2. SMTP_HOST set      → fall back to SMTP
  3. Neither            → log and no-op

Configure via env vars:
  RESEND_API_KEY, RESEND_FROM (sender address, e.g. relatorios@noroia.com)
"""

import logging

import httpx

from ..config import settings
from . import smtp as _smtp

logger = logging.getLogger(__name__)

_RESEND_URL  = "https://api.resend.com/emails"
_FROM_NAME   = "Ecommerce Tracking IA"
_FROM_DOMAIN = "relatorios@noroia.com"


def send_email(to: str, subject: str, html_body: str, from_name: str = _FROM_NAME) -> bool:
    """Send an HTML email. Returns True on success."""
    if settings.RESEND_API_KEY:
        ok, _ = _via_resend(to, subject, html_body, from_name)
        return ok
    return _smtp.send_email(to, subject, html_body)


def send_email_with_error(to: str, subject: str, html_body: str, from_name: str = _FROM_NAME) -> tuple[bool, str]:
    """Send an HTML email. Returns (success, error_message)."""
    if settings.RESEND_API_KEY:
        return _via_resend(to, subject, html_body, from_name)
    ok = _smtp.send_email(to, subject, html_body)
    return ok, "" if ok else "SMTP send failed — check logs"


def send_email_with_attachment(
    to: str,
    subject: str,
    html_body: str,
    attachment_content: bytes,
    attachment_filename: str,
    attachment_type: str = "application/pdf",
    from_name: str = _FROM_NAME,
) -> bool:
    """Send an HTML email with a binary attachment (e.g. PDF). Resend only."""
    if not settings.RESEND_API_KEY:
        logger.warning("resend: attachment send requires RESEND_API_KEY — falling back to plain email")
        return send_email(to, subject, html_body, from_name)

    import base64
    from_addr = settings.RESEND_FROM or _FROM_DOMAIN
    payload = {
        "from":    f"{from_name} <{from_addr}>",
        "to":      [to],
        "subject": subject,
        "html":    html_body,
        "attachments": [{
            "filename":    attachment_filename,
            "content":     base64.b64encode(attachment_content).decode(),
            "content_type": attachment_type,
        }],
    }
    try:
        resp = httpx.post(
            _RESEND_URL,
            headers={"Authorization": f"Bearer {settings.RESEND_API_KEY}", "Content-Type": "application/json"},
            json=payload,
            timeout=30.0,
        )
        resp.raise_for_status()
        logger.info("resend: sent with attachment → %s | %s", to, subject)
        return True
    except httpx.HTTPStatusError as exc:
        logger.error("resend attachment HTTP %s → %s: %s", exc.response.status_code, to, exc.response.text[:200])
        return False
    except Exception as exc:
        logger.error("resend attachment error → %s: %s", to, exc)
        return False


def _via_resend(to: str, subject: str, html: str, from_name: str) -> tuple[bool, str]:
    from_addr = settings.RESEND_FROM or _FROM_DOMAIN
    payload = {
        "from":    f"{from_name} <{from_addr}>",
        "to":      [to],
        "subject": subject,
        "html":    html,
    }
    try:
        resp = httpx.post(
            _RESEND_URL,
            headers={
                "Authorization": f"Bearer {settings.RESEND_API_KEY}",
                "Content-Type":  "application/json",
            },
            json=payload,
            timeout=15.0,
        )
        resp.raise_for_status()
        logger.info("resend: sent → %s | %s", to, subject)
        return True, ""
    except httpx.HTTPStatusError as exc:
        err = exc.response.text[:300]
        logger.error("resend HTTP %s → %s: %s", exc.response.status_code, to, err)
        return False, f"HTTP {exc.response.status_code}: {err}"
    except Exception as exc:
        logger.error("resend error → %s: %s", to, exc)
        return False, str(exc)[:200]
