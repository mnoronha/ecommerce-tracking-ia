"""
Telegram notification client for the agency ops channel.

TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID must be set in Railway env vars.
All operational/balance alerts go to one agency-level channel — not per-client.
"""
from __future__ import annotations

import logging

import httpx

from ..config import settings

logger = logging.getLogger(__name__)
_API_BASE = "https://api.telegram.org"


def send_message(text: str) -> bool:
    """Send a message to the agency ops Telegram channel. Returns True on success."""
    bot_token = settings.TELEGRAM_BOT_TOKEN
    chat_id   = settings.TELEGRAM_CHAT_ID
    if not bot_token or not chat_id:
        logger.debug("telegram: TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not configured — skip")
        return False

    url = f"{_API_BASE}/bot{bot_token}/sendMessage"
    try:
        resp = httpx.post(
            url,
            json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
            timeout=10.0,
        )
        if resp.status_code == 200:
            return True
        logger.warning("telegram: HTTP %d — %s", resp.status_code, resp.text[:200])
        return False
    except Exception as exc:
        logger.warning("telegram: send_message failed: %s", exc)
        return False
