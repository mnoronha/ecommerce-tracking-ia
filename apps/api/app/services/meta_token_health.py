"""
Meta token health check — periodic verification of all clients' tokens.

Long-lived Meta tokens expire after ~60 days. Without monitoring, CAPI silently
stops working when a token expires. This module:

  1. Runs on schedule (every 6h via APScheduler)
  2. Reads all active clients with meta_access_token
  3. Calls Graph API /debug_token to verify validity
  4. Updates clients.meta_token_health: healthy / expiring_soon / expired / invalid
  5. Notifies via alerts.py when status degrades

Docs: https://developers.facebook.com/docs/graph-api/reference/v19.0/debug_token
"""

import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

import httpx

from ..config import settings
from ..database import get_supabase
from ..services import crypto

logger = logging.getLogger(__name__)

_GRAPH = "https://graph.facebook.com/v19.0"
_EXPIRING_SOON_DAYS = 7  # warn when token expires in <= 7 days


def _check_token(access_token: str, app_id: str, app_secret: str) -> dict:
    """
    Call /debug_token to verify a user access token. Returns:
      {valid: bool, expires_at: int (unix ts) or 0 if never, scopes: [str]}
    Returns {valid: False} on any error.
    """
    try:
        resp = httpx.get(
            f"{_GRAPH}/debug_token",
            params={
                "input_token":  access_token,
                "access_token": f"{app_id}|{app_secret}",
            },
            timeout=10.0,
        )
        if resp.status_code != 200:
            logger.warning("debug_token HTTP %s: %s", resp.status_code, resp.text[:200])
            return {"valid": False}
        data = (resp.json().get("data") or {})
        return {
            "valid":      bool(data.get("is_valid")),
            "expires_at": int(data.get("expires_at") or 0),
            "scopes":     data.get("scopes") or [],
        }
    except Exception as exc:
        logger.warning("_check_token exception: %s", exc)
        return {"valid": False}


def _classify_health(token_info: dict, db_expires_at: Optional[str]) -> str:
    """
    Decide the health status for a token.

    Priority:
      1. Token invalid (Meta says so) → expired
      2. expires_at in the past → expired
      3. expires_at within 7 days → expiring_soon
      4. Otherwise → healthy
    """
    if not token_info.get("valid"):
        return "expired"

    # Prefer Meta's own expires_at if available
    expires_unix = token_info.get("expires_at") or 0

    if expires_unix == 0 and db_expires_at:
        # Fallback to our stored value (long-lived tokens may not report expires_at)
        try:
            expires_unix = int(datetime.fromisoformat(db_expires_at.replace("Z", "+00:00")).timestamp())
        except Exception:
            expires_unix = 0

    if expires_unix == 0:
        # Never expires (system user token) — healthy
        return "healthy"

    now = datetime.now(timezone.utc).timestamp()
    if expires_unix <= now:
        return "expired"
    if expires_unix - now <= _EXPIRING_SOON_DAYS * 24 * 3600:
        return "expiring_soon"
    return "healthy"


def run_token_health_check() -> None:
    """
    Scheduler entry point. Iterates all active clients with meta_access_token
    and updates meta_token_health on each.
    """
    app_id     = settings.META_APP_ID if hasattr(settings, "META_APP_ID") else ""
    app_secret = settings.META_APP_SECRET if hasattr(settings, "META_APP_SECRET") else ""
    if not app_id or not app_secret:
        logger.debug("meta_token_health: skipped (no META_APP_ID / META_APP_SECRET)")
        return

    sb = get_supabase()
    try:
        result = (
            sb.table("clients")
            .select("id, pixel_id, meta_access_token, meta_token_expires_at, meta_token_health, alert_email, slack_webhook_url")
            .eq("is_active", True)
            .not_.is_("meta_access_token", "null")
            .execute()
        )
    except Exception as exc:
        logger.error("meta_token_health: failed to load clients: %s", exc)
        return

    for c in (result.data or []):
        info = _check_token(crypto.decrypt_secret(c["meta_access_token"]), app_id, app_secret)
        new_health = _classify_health(info, c.get("meta_token_expires_at"))
        old_health = c.get("meta_token_health") or "unknown"

        update: dict = {"meta_token_health": new_health}
        if info.get("expires_at"):
            update["meta_token_expires_at"] = datetime.fromtimestamp(
                info["expires_at"], tz=timezone.utc
            ).isoformat()

        try:
            sb.table("clients").update(update).eq("id", c["id"]).execute()
        except Exception as exc:
            logger.warning("meta_token_health: failed to update client %s: %s", c["id"], exc)
            continue

        # Notify on degradation
        if new_health != old_health and new_health in ("expiring_soon", "expired"):
            _notify_degraded(c, new_health)


def _notify_degraded(client: dict, new_health: str) -> None:
    """Send Slack alert when token health degrades. Best-effort, never raises."""
    webhook = client.get("slack_webhook_url")
    if not webhook:
        return
    pixel_id = client.get("pixel_id") or "unknown"
    msg = (
        f":warning: Meta token *{new_health}* para cliente `{pixel_id}`. "
        f"Reconecte em Settings → Meta para evitar interrupção do CAPI."
        if new_health == "expiring_soon"
        else
        f":rotating_light: Meta token *EXPIRED* para cliente `{pixel_id}`. "
        f"CAPI parou de funcionar — reconecte em Settings → Meta."
    )
    try:
        httpx.post(webhook, json={"text": msg}, timeout=5.0)
    except Exception as exc:
        logger.debug("slack notification failed: %s", exc)
