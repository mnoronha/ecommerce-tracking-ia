"""
Model Router — decide qual modelo de IA usar com fallback em cascata.

Ordem de precedência:
  1. Config específica do client + content_type
  2. Config específica do client (qualquer content_type)
  3. Config global para content_type
  4. Config global para task
  5. Default hardcoded
"""

from __future__ import annotations

import logging
from typing import Optional
from uuid import UUID

from ..database import get_supabase

logger = logging.getLogger(__name__)

_DEFAULTS: dict[str, dict] = {
    "generation": {
        "provider": "anthropic",
        "model_id": "claude-sonnet-4-6",
        "temperature": 0.7,
        "max_tokens": 8000,
    },
    "factcheck": {
        "provider": "anthropic",
        "model_id": "claude-haiku-4-5-20251001",
        "temperature": 0.3,
        "max_tokens": 4000,
    },
    "embedding": {
        "provider": "voyage",
        "model_id": "voyage-3-large",
    },
    "rag_query_expansion": {
        "provider": "anthropic",
        "model_id": "claude-haiku-4-5-20251001",
        "temperature": 0.3,
        "max_tokens": 500,
    },
}


def get_model_for_task(
    task: str,
    client_id: Optional[str] = None,
    content_type: Optional[str] = None,
) -> dict:
    """Resolve config de modelo com fallback em cascata."""
    sb = get_supabase()

    def _query(scope: str, cid=None, ctype=None):
        q = (
            sb.table("ai_model_configs")
            .select("*")
            .eq("task", task)
            .eq("scope", scope)
            .eq("is_active", True)
        )
        if cid:
            q = q.eq("client_id", str(cid))
        if ctype:
            q = q.eq("content_type", ctype)
        rows = q.limit(1).execute().data
        return rows[0] if rows else None

    candidates = []
    if client_id and content_type:
        candidates.append(_query("client", cid=client_id, ctype=content_type))
    if client_id:
        candidates.append(_query("client", cid=client_id))
    if content_type:
        candidates.append(_query("content_type", ctype=content_type))
    candidates.append(_query("global"))

    for c in candidates:
        if c:
            return {
                "provider":    c["provider"],
                "model_id":    c["model_id"],
                "temperature": float(c["temperature"]) if c.get("temperature") else _DEFAULTS.get(task, {}).get("temperature", 0.7),
                "max_tokens":  c.get("max_tokens") or _DEFAULTS.get(task, {}).get("max_tokens", 4000),
            }

    return _DEFAULTS.get(task, {"provider": "anthropic", "model_id": "claude-sonnet-4-6"})
