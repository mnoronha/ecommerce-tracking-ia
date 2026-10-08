"""
Telegram notification client for the agency ops channel.

TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID must be set in Railway env vars.
All operational/balance alerts go to one agency-level channel — not per-client.
"""
from __future__ import annotations

import logging
from typing import Any

import httpx

from ..config import settings

logger = logging.getLogger(__name__)
_API_BASE = "https://api.telegram.org"

# Telegram error descriptions for classification
_TELEGRAM_ERROR_MAP = {
    401: "invalid_token",
    403: "bot_blocked_or_kicked",
    400: "bad_request",       # refined below from description
}


def send_message(text: str) -> tuple[bool, dict[str, Any]]:
    """
    Send a message to the agency ops Telegram channel.

    Returns (success: bool, detail: dict).
    detail always contains 'reason' explaining the outcome.
    Never leaks tokens — only http_status, telegram error codes and descriptions.
    """
    bot_token = settings.TELEGRAM_BOT_TOKEN
    chat_id   = settings.TELEGRAM_CHAT_ID

    if not bot_token or not chat_id:
        detail = {
            "reason":      "no_credentials",
            "description": "TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not set in env",
        }
        logger.warning("telegram: %s", detail["description"])
        return False, detail

    url = f"{_API_BASE}/bot{bot_token}/sendMessage"
    try:
        resp = httpx.post(
            url,
            json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
            timeout=10.0,
        )
    except Exception as exc:
        detail = {"reason": "network_error", "description": str(exc)[:200]}
        logger.warning("telegram: network error: %s", exc)
        return False, detail

    # Parse Telegram response body
    try:
        body: dict[str, Any] = resp.json()
    except Exception:
        body = {}

    if resp.status_code == 200 and body.get("ok"):
        result = body.get("result", {})
        detail = {
            "reason":     "sent",
            "http_status": 200,
            "message_id":  result.get("message_id"),
            "chat_id":     result.get("chat", {}).get("id"),
        }
        return True, detail

    # Failure — classify the error
    tg_description: str = body.get("description", "")
    tg_error_code:  int  = body.get("error_code", resp.status_code)

    reason = _classify_telegram_error(resp.status_code, tg_description)
    detail = {
        "reason":          reason,
        "http_status":     resp.status_code,
        "tg_error_code":   tg_error_code,
        "tg_description":  tg_description[:300],
    }
    logger.warning(
        "telegram: HTTP %d reason=%s — %s",
        resp.status_code, reason, tg_description[:200],
    )
    return False, detail


def _classify_telegram_error(http_status: int, description: str) -> str:
    """Return a short error classification string from the Telegram API response."""
    desc_lower = description.lower()

    if http_status == 401:
        return "invalid_token"

    if http_status == 403:
        if "bot was blocked" in desc_lower:
            return "bot_blocked"
        if "bot is not a member" in desc_lower or "not a member" in desc_lower:
            return "bot_not_member"
        if "kicked" in desc_lower:
            return "bot_kicked"
        return "forbidden"

    if http_status == 400:
        if "chat not found" in desc_lower:
            return "chat_not_found"
        if "group chat was upgraded" in desc_lower:
            return "group_upgraded_to_supergroup"
        if "parse mode" in desc_lower:
            return "invalid_parse_mode"
        return "bad_request"

    if http_status == 429:
        return "rate_limited"

    return f"http_{http_status}"
