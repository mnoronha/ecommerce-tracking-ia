"""
Obsidian / Agency Knowledge writer.

promote_to_knowledge():
  1. Guard: candidate.status == APPROVED
  2. Duplicate detection: check core_agency_knowledge by pattern
  3. Render markdown document
  4. Write to core_agency_knowledge (primary, always)
  5. Write to GitHub (secondary, only if OBSIDIAN_GITHUB_TOKEN + OBSIDIAN_VAULT_REPO are set)
  6. Create core_knowledge_promotions audit record

GitHub path convention:
  knowledge/{scope_dir}/{platform}/{business_model}/{slug}.md
  e.g. knowledge/client/google/ecommerce/balance-recharge-restores-delivery.md

Hermes contract:
  Hermes reads core_agency_knowledge where scope != CLIENT_ONLY (or matching client_id).
  PROPOSED / PENDING candidates are NEVER promoted here. APPROVED is the gate.

Automatic promotion: ZERO. This module is only called from an explicit human
POST /learning-candidates/{id}/promote endpoint.
"""

from __future__ import annotations

import hashlib
import logging
import re
import textwrap
from datetime import datetime, timezone
from typing import Any, Optional

import httpx

from ..database import get_supabase
from ..config import settings

logger = logging.getLogger(__name__)

# Statuses that block promotion
_BLOCKED_STATUSES = {"PENDING", "PROPOSED", "REJECTED", "SUPERSEDED"}

# obsidian_status values
_OBS_WRITTEN = "WRITTEN"
_OBS_SKIPPED = "SKIPPED"
_OBS_FAILED  = "FAILED"
_OBS_PENDING = "PENDING"


# ── Slug helpers ───────────────────────────────────────────────────────────────

def _slugify(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_]+", "-", text)
    return text[:80]


def _scope_dir(scope: str) -> str:
    mapping = {
        "CLIENT_ONLY":           "client",
        "CLIENT":                "client",
        "BUSINESS_MODEL":        "business-model",
        "PLATFORM":              "platform",
        "AGENCY_WIDE_CANDIDATE": "agency",
        "AGENCY":                "agency",
    }
    return mapping.get(scope, "client")


def _document_path(scope: str, platform: Optional[str], business_model: Optional[str], slug: str) -> str:
    parts = ["knowledge", _scope_dir(scope)]
    if platform:
        parts.append(_slugify(platform))
    if business_model:
        parts.append(_slugify(business_model))
    parts.append(f"{slug}.md")
    return "/".join(parts)


# ── Markdown renderer ──────────────────────────────────────────────────────────

def _render_markdown(
    *,
    title: str,
    statement: str,
    pattern: Optional[str],
    context: dict,
    observed_outcome: dict,
    evidence_refs: list,
    evidence_level: str,
    confidence: Optional[str],
    scope: str,
    client_id: Optional[str],
    business_model: Optional[str],
    platform: Optional[str],
    source_alert_id: Optional[str],
    source_diagnosis_id: Optional[str],
    source_recommendation_id: Optional[str],
    source_action_id: Optional[str],
    source_outcome_id: Optional[str],
    source_learning_candidate_id: str,
    approved_by: str,
    approved_at: str,
    promoted_by: str,
    promoted_at: str,
    document_version: int,
) -> str:
    outcome_status = observed_outcome.get("outcome_status", "UNKNOWN")
    delta_pct = observed_outcome.get("delta_pct")
    action_type = observed_outcome.get("action_type", "")

    # YAML frontmatter
    fm_lines = [
        "---",
        f"title: \"{title}\"",
        f"pattern: \"{pattern or ''}\"",
        f"scope: {scope}",
        f"platform: {platform or 'N/A'}",
        f"business_model: {business_model or 'N/A'}",
        f"evidence_level: {evidence_level}",
        f"confidence: {confidence or 'UNKNOWN'}",
        f"outcome_status: {outcome_status}",
        f"approved_by: {approved_by}",
        f"approved_at: {approved_at}",
        f"promoted_by: {promoted_by}",
        f"promoted_at: {promoted_at}",
        f"version: {document_version}",
        f"source_learning_candidate: {source_learning_candidate_id}",
        "---",
        "",
    ]

    body_parts = [
        *fm_lines,
        f"# {title}",
        "",
        "## Aprendizado",
        "",
        statement,
        "",
    ]

    if pattern:
        body_parts += ["## Padrão", "", f"`{pattern}`", ""]

    # Observed outcome
    body_parts += ["## Outcome Observado", ""]
    body_parts.append(f"- **Status**: {outcome_status}")
    if delta_pct is not None:
        sign = "+" if delta_pct > 0 else ""
        body_parts.append(f"- **Delta**: {sign}{delta_pct:.2f}%")
    if action_type:
        body_parts.append(f"- **Tipo de ação**: {action_type}")
    body_parts.append("")

    # Context
    if context:
        body_parts += ["## Contexto", ""]
        for k, v in context.items():
            body_parts.append(f"- **{k}**: {v}")
        body_parts.append("")

    # Evidence
    if evidence_refs:
        body_parts += ["## Evidências", ""]
        for ref in evidence_refs:
            body_parts.append(f"- {ref}")
        body_parts.append("")

    # When to apply / not apply / limitations (populated from context if available)
    when_apply = context.get("when_to_apply") or ""
    when_not   = context.get("when_not_to_apply") or ""
    limits     = context.get("limitations") or ""

    if when_apply:
        body_parts += ["## Quando Aplicar", "", str(when_apply), ""]
    if when_not:
        body_parts += ["## Quando NÃO Aplicar", "", str(when_not), ""]
    if limits:
        body_parts += ["## Limitações", "", str(limits), ""]

    # Traceability
    body_parts += ["## Rastreabilidade", ""]
    trace_fields = [
        ("Learning Candidate", source_learning_candidate_id),
        ("Outcome",            source_outcome_id),
        ("Ação",               source_action_id),
        ("Recomendação",       source_recommendation_id),
        ("Diagnóstico",        source_diagnosis_id),
        ("Alerta",             source_alert_id),
        ("Cliente",            client_id),
    ]
    for label, val in trace_fields:
        if val:
            body_parts.append(f"- **{label}**: `{val}`")
    body_parts.append("")

    # Governance footer
    body_parts += [
        "---",
        "",
        "> **APPROVED ≠ PROMOTED AUTOMATICALLY**",
        "> Este documento foi promovido manualmente por um humano.",
        "> Candidatos PROPOSED/PENDING nunca chegam aqui.",
        "",
    ]

    return "\n".join(body_parts)


# ── GitHub writer ──────────────────────────────────────────────────────────────

def _write_to_github(
    *,
    path: str,
    content: str,
    message: str,
    existing_sha: Optional[str] = None,
) -> dict:
    """
    Write or update a file in the configured GitHub vault repo.
    Returns {"status": "written"|"skipped", "url": ...}.
    Requires: OBSIDIAN_GITHUB_TOKEN, OBSIDIAN_VAULT_REPO (owner/repo).
    """
    token: str = getattr(settings, "OBSIDIAN_GITHUB_TOKEN", "") or ""
    repo:  str = getattr(settings, "OBSIDIAN_VAULT_REPO", "") or ""
    branch: str = getattr(settings, "OBSIDIAN_VAULT_BRANCH", "") or "main"

    if not token or not repo:
        return {"status": "skipped", "reason": "OBSIDIAN_GITHUB_TOKEN or OBSIDIAN_VAULT_REPO not configured"}

    import base64
    encoded = base64.b64encode(content.encode("utf-8")).decode("ascii")

    url = f"https://api.github.com/repos/{repo}/contents/{path}"
    payload: dict[str, Any] = {
        "message": message,
        "content": encoded,
        "branch":  branch,
    }
    if existing_sha:
        payload["sha"] = existing_sha

    try:
        resp = httpx.put(
            url,
            json=payload,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            timeout=15.0,
        )
        resp.raise_for_status()
        data = resp.json()
        github_url = data.get("content", {}).get("html_url") or url
        return {"status": "written", "url": github_url, "sha": data.get("content", {}).get("sha")}
    except httpx.HTTPStatusError as exc:
        logger.warning("obsidian_writer: GitHub write failed (%s): %s", exc.response.status_code, exc.response.text[:200])
        return {"status": "failed", "reason": f"HTTP {exc.response.status_code}"}
    except Exception as exc:
        logger.warning("obsidian_writer: GitHub write error: %s", exc)
        return {"status": "failed", "reason": str(exc)}


def _get_existing_github_sha(path: str) -> Optional[str]:
    """Get SHA of existing file in GitHub repo (for update rather than create)."""
    token: str = getattr(settings, "OBSIDIAN_GITHUB_TOKEN", "") or ""
    repo:  str = getattr(settings, "OBSIDIAN_VAULT_REPO", "") or ""
    branch: str = getattr(settings, "OBSIDIAN_VAULT_BRANCH", "") or "main"
    if not token or not repo:
        return None
    try:
        resp = httpx.get(
            f"https://api.github.com/repos/{repo}/contents/{path}",
            params={"ref": branch},
            headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
            timeout=10.0,
        )
        if resp.status_code == 200:
            return resp.json().get("sha")
    except Exception:
        pass
    return None


# ── Duplicate detection ────────────────────────────────────────────────────────

def _find_existing_knowledge(sb, pattern: Optional[str], scope: str) -> Optional[dict]:
    """Return existing knowledge document with the same pattern, or None."""
    if not pattern:
        return None
    try:
        res = (
            sb.table("core_agency_knowledge")
            .select("id, title, pattern, document_version, document_path, scope")
            .eq("pattern", pattern)
            .limit(1)
            .execute()
        )
        return res.data[0] if (res and res.data) else None
    except Exception as exc:
        logger.warning("obsidian_writer: duplicate check failed: %s", exc)
        return None


# ── Main promotion function ────────────────────────────────────────────────────

def promote_to_knowledge(
    *,
    learning_candidate_id: str,
    actor: str,
    reason: Optional[str] = None,
    idempotency_key: Optional[str] = None,
    force_new_version: bool = False,
) -> dict:
    """
    Promote an APPROVED learning candidate to Agency Knowledge.

    Returns dict with keys:
      status: COMPLETED | EXISTING_KNOWLEDGE | NEW_VERSION | FAILED
      knowledge_id: str | None
      promotion_id: str | None
      document_path: str | None
      document_version: int
      obsidian_status: WRITTEN | SKIPPED | FAILED | PENDING
      obsidian_url: str | None
      error: str | None
    """
    sb = get_supabase()
    now = datetime.now(timezone.utc)

    # ── 1. Idempotency check ───────────────────────────────────────────────────
    if idempotency_key:
        try:
            dup = (
                sb.table("core_knowledge_promotions")
                .select("*")
                .eq("idempotency_key", idempotency_key)
                .limit(1)
                .execute()
            )
            if dup and dup.data:
                row = dup.data[0]
                return {
                    "status": row["status"],
                    "knowledge_id": str(row["knowledge_id"]) if row.get("knowledge_id") else None,
                    "promotion_id": str(row["id"]),
                    "document_path": row.get("document_path"),
                    "document_version": row.get("document_version", 1),
                    "obsidian_status": _OBS_SKIPPED,
                    "obsidian_url": None,
                    "error": None,
                }
        except Exception as exc:
            logger.warning("obsidian_writer: idempotency check failed: %s", exc)

    # ── 2. Load candidate ──────────────────────────────────────────────────────
    try:
        cand_res = (
            sb.table("core_learning_candidates")
            .select("*")
            .eq("id", learning_candidate_id)
            .limit(1)
            .execute()
        )
        candidate = cand_res.data[0] if (cand_res and cand_res.data) else None
    except Exception as exc:
        return _promotion_failed(sb, learning_candidate_id, actor, reason, idempotency_key, f"candidate lookup failed: {exc}")

    if not candidate:
        return _promotion_failed(sb, learning_candidate_id, actor, reason, idempotency_key, "candidate not found")

    # ── 3. APPROVED guard ──────────────────────────────────────────────────────
    cand_status = candidate.get("status", "PENDING")
    if cand_status in _BLOCKED_STATUSES:
        return _promotion_failed(
            sb, learning_candidate_id, actor, reason, idempotency_key,
            f"candidate status {cand_status!r} is not APPROVED — promotion blocked"
        )
    if cand_status != "APPROVED":
        return _promotion_failed(
            sb, learning_candidate_id, actor, reason, idempotency_key,
            f"candidate status {cand_status!r} is not APPROVED"
        )

    # ── 4. Duplicate detection ─────────────────────────────────────────────────
    pattern  = candidate.get("pattern")
    scope    = candidate.get("scope", "CLIENT_ONLY")
    existing = _find_existing_knowledge(sb, pattern, scope)

    if existing and not force_new_version:
        # Return existing without creating duplicate
        promo_row = _create_promotion_record(
            sb,
            candidate_id=learning_candidate_id,
            knowledge_id=str(existing["id"]),
            actor=actor,
            reason=reason,
            idempotency_key=idempotency_key,
            status="EXISTING_KNOWLEDGE",
            document_path=existing.get("document_path"),
            document_version=int(existing.get("document_version", 1)),
            existing_knowledge_id=str(existing["id"]),
        )
        return {
            "status": "EXISTING_KNOWLEDGE",
            "knowledge_id": str(existing["id"]),
            "promotion_id": str(promo_row["id"]) if promo_row else None,
            "document_path": existing.get("document_path"),
            "document_version": int(existing.get("document_version", 1)),
            "obsidian_status": _OBS_SKIPPED,
            "obsidian_url": existing.get("obsidian_url"),
            "error": None,
        }

    # ── 5. Determine document metadata ────────────────────────────────────────
    observed = candidate.get("observed_outcome") or {}
    context  = candidate.get("context") or {}
    platform = (context.get("platform") or observed.get("platform") or "").lower()
    client_uuid = str(candidate["client_id"]) if candidate.get("client_id") else None

    # Resolve business_model from clients table
    business_model: Optional[str] = None
    if client_uuid:
        try:
            bm_res = (
                sb.table("clients").select("business_model").eq("id", client_uuid).limit(1).execute()
            )
            business_model = (bm_res.data[0].get("business_model") or "").lower() if bm_res.data else None
        except Exception:
            pass

    statement = candidate.get("statement", "aprendizado sem título")
    title = f"{(observed.get('action_type') or 'ação').replace('_', ' ').title()}: {statement[:80]}"
    slug  = _slugify(f"{platform or 'geral'}-{(pattern or statement)[:50]}")
    document_version = 1

    if existing and force_new_version:
        document_version = int(existing.get("document_version", 1)) + 1
        slug = f"{slug}-v{document_version}"

    doc_path = _document_path(scope, platform or None, business_model, slug)

    # Approved_by: load from learning candidate context or use actor
    approved_by = str(context.get("approved_by") or actor)
    approved_at = candidate.get("updated_at") or now.isoformat()

    # Source IDs
    source_rec_id = str(candidate["source_recommendation_id"]) if candidate.get("source_recommendation_id") else None
    source_act_id = str(candidate["source_action_id"]) if candidate.get("source_action_id") else None
    evidence_refs = candidate.get("evidence_refs") or []
    source_outcome_id = evidence_refs[0] if evidence_refs else None

    # Fetch alert/diagnosis from recommendation chain
    source_alert_id = source_diag_id = None
    if source_rec_id:
        try:
            rec_res = (
                sb.table("core_recommendations")
                .select("alert_id, diagnosis_id")
                .eq("id", source_rec_id)
                .limit(1)
                .execute()
            )
            if rec_res and rec_res.data:
                source_alert_id = str(rec_res.data[0].get("alert_id") or "") or None
                source_diag_id  = str(rec_res.data[0].get("diagnosis_id") or "") or None
        except Exception:
            pass

    # ── 6. Render markdown ────────────────────────────────────────────────────
    markdown = _render_markdown(
        title=title,
        statement=statement,
        pattern=pattern,
        context=context,
        observed_outcome=observed,
        evidence_refs=evidence_refs,
        evidence_level=candidate.get("evidence_level", "OBSERVATION"),
        confidence=None,
        scope=scope,
        client_id=client_uuid,
        business_model=business_model,
        platform=platform or None,
        source_alert_id=source_alert_id,
        source_diagnosis_id=source_diag_id,
        source_recommendation_id=source_rec_id,
        source_action_id=source_act_id,
        source_outcome_id=source_outcome_id,
        source_learning_candidate_id=learning_candidate_id,
        approved_by=approved_by,
        approved_at=str(approved_at),
        promoted_by=actor,
        promoted_at=now.isoformat(),
        document_version=document_version,
    )

    # ── 7. Write to GitHub (best-effort) ──────────────────────────────────────
    existing_sha = _get_existing_github_sha(doc_path) if (existing and force_new_version) else None
    gh_result = _write_to_github(
        path=doc_path,
        content=markdown,
        message=f"knowledge: promote {slug} (v{document_version}) from learning candidate {learning_candidate_id[:8]}",
        existing_sha=existing_sha,
    )
    obsidian_status = {
        "written": _OBS_WRITTEN,
        "skipped": _OBS_SKIPPED,
        "failed":  _OBS_FAILED,
    }.get(gh_result["status"], _OBS_PENDING)
    obsidian_url = gh_result.get("url")

    # ── 8. Write to core_agency_knowledge ─────────────────────────────────────
    knowledge_row: dict = {
        "learning_candidate_id":    learning_candidate_id,
        "source_alert_id":          source_alert_id,
        "source_diagnosis_id":      source_diag_id,
        "source_recommendation_id": source_rec_id,
        "source_action_id":         source_act_id,
        "source_outcome_id":        source_outcome_id,
        "title":                    title,
        "statement":                statement,
        "pattern":                  pattern,
        "context":                  context,
        "observed_outcome":         observed,
        "evidence_refs":            evidence_refs,
        "evidence_level":           candidate.get("evidence_level", "OBSERVATION"),
        "scope":                    scope,
        "client_id":                client_uuid,
        "business_model":           business_model,
        "platform":                 platform or None,
        "approved_by":              approved_by,
        "approved_at":              str(approved_at),
        "promoted_by":              actor,
        "promoted_at":              now.isoformat(),
        "document_path":            doc_path,
        "document_slug":            slug,
        "document_version":         document_version,
        "obsidian_status":          obsidian_status,
        "obsidian_url":             obsidian_url,
        "markdown_content":         markdown,
        "schema_version":           "1.1",
    }
    if existing and force_new_version:
        knowledge_row["supersedes_id"] = str(existing["id"])

    try:
        ins = sb.table("core_agency_knowledge").insert(knowledge_row).execute()
        knowledge_id = str(ins.data[0]["id"]) if (ins and ins.data) else None
    except Exception as exc:
        logger.error("obsidian_writer: core_agency_knowledge insert failed: %s", exc)
        return _promotion_failed(sb, learning_candidate_id, actor, reason, idempotency_key, f"knowledge insert failed: {exc}")

    # ── 9. Create promotion audit record ──────────────────────────────────────
    final_status = "NEW_VERSION" if (existing and force_new_version) else "COMPLETED"
    promo_row = _create_promotion_record(
        sb,
        candidate_id=learning_candidate_id,
        knowledge_id=knowledge_id,
        actor=actor,
        reason=reason,
        idempotency_key=idempotency_key,
        status=final_status,
        document_path=doc_path,
        document_version=document_version,
    )

    logger.info(
        "obsidian_writer: promoted candidate %s → knowledge %s (path=%s, obsidian=%s)",
        learning_candidate_id, knowledge_id, doc_path, obsidian_status,
    )

    return {
        "status": final_status,
        "knowledge_id": knowledge_id,
        "promotion_id": str(promo_row["id"]) if promo_row else None,
        "document_path": doc_path,
        "document_version": document_version,
        "obsidian_status": obsidian_status,
        "obsidian_url": obsidian_url,
        "error": None,
    }


def _promotion_failed(sb, candidate_id: str, actor: str, reason: Optional[str], idempotency_key: Optional[str], error: str) -> dict:
    logger.warning("obsidian_writer: promotion failed for %s: %s", candidate_id, error)
    promo_row = _create_promotion_record(
        sb, candidate_id=candidate_id, knowledge_id=None, actor=actor,
        reason=reason, idempotency_key=idempotency_key, status="FAILED",
        document_path=None, document_version=None, error=error,
    )
    return {
        "status": "FAILED",
        "knowledge_id": None,
        "promotion_id": str(promo_row["id"]) if promo_row else None,
        "document_path": None,
        "document_version": 1,
        "obsidian_status": _OBS_FAILED,
        "obsidian_url": None,
        "error": error,
    }


def check_obsidian_readiness() -> dict:
    """
    Read-only runtime check of the Obsidian/GitHub configuration.

    Does NOT write any file. Does NOT expose token values.
    Performs: GET /repos/{owner/repo} + GET /repos/{owner/repo}/branches/{branch}
    to verify auth, repo existence, branch existence, and write permission.

    Returns:
      token_present:            bool
      repo_present:             bool
      branch_present:           bool
      repo_name:                str | None   (not a secret)
      branch_name:              str
      github_api_reachable:     bool
      github_repo_accessible:   bool
      github_branch_accessible: bool
      github_write_permission:  bool
      ready:                    bool         (all checks pass)
      error:                    str | None
    """
    token: str = getattr(settings, "OBSIDIAN_GITHUB_TOKEN", "") or ""
    repo:  str = getattr(settings, "OBSIDIAN_VAULT_REPO", "") or ""
    branch: str = getattr(settings, "OBSIDIAN_VAULT_BRANCH", "") or "main"

    result: dict = {
        "token_present":            len(token) > 0,
        "repo_present":             len(repo) > 0,
        "branch_present":           len(branch) > 0,
        "repo_name":                repo if repo else None,
        "branch_name":              branch,
        "github_api_reachable":     False,
        "github_repo_accessible":   False,
        "github_branch_accessible": False,
        "github_write_permission":  False,
        "ready":                    False,
        "error":                    None,
    }

    if not token or not repo:
        result["error"] = "OBSIDIAN_GITHUB_TOKEN or OBSIDIAN_VAULT_REPO not set"
        return result

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    # ── Check 1: GitHub API reachable + repo exists + auth valid ──────────────
    try:
        r = httpx.get(
            f"https://api.github.com/repos/{repo}",
            headers=headers,
            timeout=10.0,
        )
        result["github_api_reachable"] = True
        if r.status_code == 200:
            result["github_repo_accessible"] = True
            perms = r.json().get("permissions", {})
            result["github_write_permission"] = bool(perms.get("push") or perms.get("admin"))
        elif r.status_code == 401:
            result["error"] = "GitHub auth rejected (token invalid or expired)"
            return result
        elif r.status_code == 403:
            result["error"] = "GitHub token lacks repo read permission"
            return result
        elif r.status_code == 404:
            result["error"] = f"GitHub repo not found: {repo!r}"
            return result
        else:
            result["error"] = f"GitHub repo check returned HTTP {r.status_code}"
            return result
    except httpx.ConnectError:
        result["error"] = "GitHub API unreachable (network/DNS)"
        return result
    except Exception as exc:
        result["error"] = f"GitHub repo check error: {exc}"
        return result

    # ── Check 2: Branch exists ────────────────────────────────────────────────
    try:
        r2 = httpx.get(
            f"https://api.github.com/repos/{repo}/branches/{branch}",
            headers=headers,
            timeout=10.0,
        )
        if r2.status_code == 200:
            result["github_branch_accessible"] = True
        elif r2.status_code == 404:
            result["error"] = f"Branch {branch!r} not found in repo {repo!r}"
        else:
            result["error"] = f"Branch check returned HTTP {r2.status_code}"
    except Exception as exc:
        result["error"] = f"Branch check error: {exc}"

    result["ready"] = (
        result["token_present"]
        and result["repo_present"]
        and result["github_repo_accessible"]
        and result["github_branch_accessible"]
        and result["github_write_permission"]
    )
    return result


def _create_promotion_record(
    sb,
    *,
    candidate_id: str,
    knowledge_id: Optional[str],
    actor: str,
    reason: Optional[str],
    idempotency_key: Optional[str],
    status: str,
    document_path: Optional[str],
    document_version: Optional[int],
    error: Optional[str] = None,
    existing_knowledge_id: Optional[str] = None,
) -> Optional[dict]:
    row = {
        "learning_candidate_id": candidate_id,
        "knowledge_id":          knowledge_id,
        "actor":                 actor,
        "reason":                reason,
        "status":                status,
        "destination":           "agency_knowledge",
        "document_path":         document_path,
        "document_version":      document_version,
        "error":                 error,
        "idempotency_key":       idempotency_key,
        "promoted_at":           datetime.now(timezone.utc).isoformat() if status in ("COMPLETED","NEW_VERSION") else None,
        "existing_knowledge_id": existing_knowledge_id,
        "schema_version":        "1.1",
    }
    try:
        res = sb.table("core_knowledge_promotions").insert(row).execute()
        return res.data[0] if (res and res.data) else None
    except Exception as exc:
        logger.error("obsidian_writer: promotion record insert failed: %s", exc)
        return None
