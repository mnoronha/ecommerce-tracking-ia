"""
Security Admin Audit helper (PRD §21.2 — S1/S2/S3).

Records sensitive administrative and destructive operations both as structured
application logs and (best-effort) into the `audit_log` table.
NEVER logs raw credentials, passwords, tokens, or plaintext PII.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from ..database import get_supabase

logger = logging.getLogger("app.security.audit")


def _as_uuid_or_none(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return str(uuid.UUID(str(value)))
    except (ValueError, TypeError):
        return None


def record_admin_audit(
    action: str,
    resource: str,
    *,
    auth: Any = None,
    details: dict[str, Any] | None = None,
) -> None:
    """
    Emit a structured audit log and persist a best-effort row in `audit_log`.

    Must never raise on DB failure so audit persistence does not turn a
    completed operation into an unhandled 500, while still logging prominently.
    """
    scope = getattr(auth, "scope", None) or "unknown"
    subject = getattr(auth, "subject", None)
    safe_details = dict(details or {})
    now_iso = datetime.now(timezone.utc).isoformat()

    logger.info(
        "SECURITY_AUDIT action=%s resource=%s scope=%s subject=%s details=%s",
        action,
        resource,
        scope,
        subject or "-",
        safe_details,
    )

    try:
        sb = get_supabase()
        sb.table("audit_log").insert(
            {
                "table_name": "security_admin_audit",
                "op": action,
                "row_id": str(resource)[:255],
                "actor": _as_uuid_or_none(subject),
                "new_data": {
                    "scope": scope,
                    "subject": subject,
                    "details": safe_details,
                    "recorded_at": now_iso,
                },
                "changed_at": now_iso,
            }
        ).execute()
    except Exception as exc:
        logger.warning(
            "SECURITY_AUDIT db insert failed action=%s resource=%s err=%s",
            action,
            resource,
            type(exc).__name__,
        )
