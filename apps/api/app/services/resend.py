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
        return _via_resend(to, subject, html_body, from_name)
    return _smtp.send_email(to, subject, html_body)


def _via_resend(to: str, subject: str, html: str, from_name: str) -> bool:
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
        return True
    except httpx.HTTPStatusError as exc:
        logger.error("resend HTTP %s → %s: %s", exc.response.status_code, to, exc.response.text[:200])
        return False
    except Exception as exc:
        logger.error("resend error → %s: %s", to, exc)
        return False
