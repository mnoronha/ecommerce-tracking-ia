"""
Knowledge Context Provider for Hermes.

Loads approved agency knowledge for a client so it can be injected into
weekly_deep_review, ad-hoc Hermes queries, recommendations, diagnosis,
and meeting prep.

Isolation contract:
  CLIENT_ONLY knowledge is returned only for the matching client.
  AGENCY, PLATFORM, BUSINESS_MODEL knowledge is visible to all clients.

No full vault copy. Returns compact summaries — full markdown stays in DB.
"""
from __future__ import annotations

import logging
from typing import Optional

from ..database import get_supabase

logger = logging.getLogger(__name__)

# Scopes that are agency-wide (not client-restricted)
_SHARED_SCOPES = {"AGENCY", "AGENCY_WIDE_CANDIDATE", "PLATFORM", "BUSINESS_MODEL"}

# Client-restricted scopes
_CLIENT_SCOPES = {"CLIENT_ONLY", "CLIENT"}


def _resolve_client_uuid(db, client_slug: str) -> Optional[str]:
    """Resolve a client slug to its UUID from the clients table."""
    try:
        res = (
            db.table("clients")
            .select("id")
            .eq("client_id", client_slug)
            .limit(1)
            .execute()
        )
        if res and res.data:
            return str(res.data[0]["id"])
    except Exception as exc:
        logger.warning("knowledge_retrieval: UUID lookup failed for %s: %s", client_slug, exc)
    return None


def load_knowledge_context(
    client_id: str,
    platform: Optional[str] = None,
    limit: int = 10,
) -> dict:
    """
    Return relevant approved knowledge for `client_id`.

    Returns:
        {
          "client_knowledge": [compact_knowledge],  # CLIENT_ONLY for this client
          "shared_knowledge": [compact_knowledge],   # AGENCY/PLATFORM/BUSINESS_MODEL
          "total_count": int,
          "client_id": str,
          "platform_filter": str | None,
        }

    Each compact_knowledge entry:
        {id, title, statement, pattern, scope, platform, evidence_level,
         outcome_status, delta_pct, action_type, approved_by, approved_at}
    """
    db = get_supabase()

    client_uuid = _resolve_client_uuid(db, client_id)

    client_knowledge: list[dict] = []
    shared_knowledge: list[dict] = []

    # ── 1. Client-specific knowledge ─────────────────────────────────────────────
    if client_uuid:
        try:
            q = (
                db.table("core_agency_knowledge")
                .select(
                    "id, title, statement, pattern, scope, platform, evidence_level, "
                    "observed_outcome, approved_by, approved_at, promoted_at"
                )
                .eq("client_id", client_uuid)
                .order("promoted_at", desc=True)
                .limit(limit)
            )
            if platform:
                q = q.eq("platform", platform.lower())
            res = q.execute()
            client_knowledge = [_compact(r) for r in (res.data or [])]
        except Exception as exc:
            logger.error(
                "knowledge_retrieval: client knowledge query failed for %s: %s", client_id, exc
            )

    # ── 2. Shared agency/platform/business_model knowledge ────────────────────────
    try:
        q = (
            db.table("core_agency_knowledge")
            .select(
                "id, title, statement, pattern, scope, platform, evidence_level, "
                "observed_outcome, approved_by, approved_at, promoted_at"
            )
            .neq("scope", "CLIENT_ONLY")
            .neq("scope", "CLIENT")
            .order("promoted_at", desc=True)
            .limit(limit)
        )
        if platform:
            q = q.eq("platform", platform.lower())
        res = q.execute()
        shared_knowledge = [_compact(r) for r in (res.data or [])]
    except Exception as exc:
        logger.error("knowledge_retrieval: shared knowledge query failed: %s", exc)

    # Deduplicate: shared query might return rows already in client_knowledge
    client_ids_set = {k["id"] for k in client_knowledge}
    shared_knowledge = [k for k in shared_knowledge if k["id"] not in client_ids_set]

    return {
        "client_knowledge":  client_knowledge,
        "shared_knowledge":  shared_knowledge,
        "total_count":       len(client_knowledge) + len(shared_knowledge),
        "client_id":         client_id,
        "platform_filter":   platform,
    }


def _compact(row: dict) -> dict:
    """Strip to fields useful for Hermes context — no full markdown."""
    obs = row.get("observed_outcome") or {}
    return {
        "id":             str(row["id"]),
        "title":          row.get("title", ""),
        "statement":      row.get("statement", ""),
        "pattern":        row.get("pattern"),
        "scope":          row.get("scope", ""),
        "platform":       row.get("platform"),
        "evidence_level": row.get("evidence_level", "OBSERVATION"),
        "outcome_status": obs.get("outcome_status"),
        "delta_pct":      obs.get("delta_pct"),
        "action_type":    obs.get("action_type"),
        "approved_by":    row.get("approved_by"),
        "approved_at":    str(row.get("approved_at") or ""),
    }


def _empty_context(client_id: str, platform: Optional[str] = None) -> dict:
    return {
        "client_knowledge": [],
        "shared_knowledge": [],
        "total_count":      0,
        "client_id":        client_id,
        "platform_filter":  platform,
    }


def format_for_hermes(ctx: dict, max_items: int = 5) -> str:
    """
    Render knowledge context as a compact text block for Hermes prompt injection.

    Prioritises client-specific knowledge, then shared.
    Max `max_items` total entries to avoid flooding prompts.
    Attribution language only: "resultado observado após ação" — not causal.
    """
    if ctx["total_count"] == 0:
        return "Nenhum aprendizado aprovado disponível para este cliente."

    all_items = ctx["client_knowledge"] + ctx["shared_knowledge"]
    all_items = all_items[:max_items]

    lines = [f"APRENDIZADOS APROVADOS ({ctx['total_count']} no total, mostrando {len(all_items)}):"]

    for k in all_items:
        scope_tag = f"[{k['scope']}]"
        plat_tag  = f" [{k['platform']}]" if k.get("platform") else ""
        lines.append(f"  • {k['title']}{plat_tag} {scope_tag}")
        lines.append(f"    {k['statement']}")
        if k.get("outcome_status"):
            delta_str = ""
            if k.get("delta_pct") is not None:
                sign = "+" if (k["delta_pct"] or 0) > 0 else ""
                delta_str = f" ({sign}{k['delta_pct']:.1f}%)"
            lines.append(f"    Resultado observado: {k['outcome_status']}{delta_str}")
        if k.get("pattern"):
            lines.append(f"    Padrão: {k['pattern']}")
        lines.append("")

    return "\n".join(lines).rstrip()
