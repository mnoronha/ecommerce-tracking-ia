"""Change Digest API — generate and retrieve change digests per client/platform/period."""
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from .auth import SCOPE_HERMES_WRITE, SCOPE_READ_ANY, AuthContext

router = APIRouter(prefix="/agency/v1/change-digest", tags=["change-digest"])


def _db():
    from ...database import get_supabase
    return get_supabase()


def _scope(auth: AuthContext, client_id: str):
    if auth.client_ids is not None and client_id not in auth.client_ids:
        raise HTTPException(403, "CLIENT_SCOPE_DENIED")


class DigestGenerate(BaseModel):
    client_id: str = Field(min_length=1)
    platform: str = Field(pattern="^(meta|google)$")
    period_start: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    period_end: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")


@router.post("/generate")
def generate_digest(body: DigestGenerate, auth: AuthContext = Depends(SCOPE_HERMES_WRITE)):
    """
    Build a change digest from core_change_log and persist it.
    Idempotent: same (client_id, platform, period_start, period_end) overwrites.
    """
    _scope(auth, body.client_id)
    from ...core.change_digest import build_digest, save_digest
    db = _db()
    try:
        digest = build_digest(db, body.client_id, body.platform, body.period_start, body.period_end)
        saved = save_digest(db, digest)
        return {**digest, "id": saved.get("id")}
    except Exception as exc:
        raise HTTPException(500, f"DIGEST_BUILD_FAILED: {str(exc)[:200]}")


@router.get("/{client_id}")
def get_digest(
    client_id: str,
    platform: str = Query(..., pattern="^(meta|google)$"),
    period_start: str = Query(..., pattern=r"^\d{4}-\d{2}-\d{2}$"),
    period_end: str = Query(..., pattern=r"^\d{4}-\d{2}-\d{2}$"),
    auth: AuthContext = Depends(SCOPE_READ_ANY),
):
    """Return stored digest. 404 if not yet generated for this period."""
    _scope(auth, client_id)
    from ...core.change_digest import get_digest as _get
    db = _db()
    digest = _get(db, client_id, platform, period_start, period_end)
    if digest is None:
        raise HTTPException(404, "DIGEST_NOT_FOUND")
    return digest


@router.get("/{client_id}/list")
def list_digests(
    client_id: str,
    limit: int = Query(20, ge=1, le=100),
    auth: AuthContext = Depends(SCOPE_READ_ANY),
):
    """List stored digests for a client, most recent first (summary only, no changes array)."""
    _scope(auth, client_id)
    from ...core.change_digest import list_digests as _list
    return _list(_db(), client_id, limit=limit)


@router.get("/{client_id}/report-section")
def report_section(
    client_id: str,
    platform: str = Query(..., pattern="^(meta|google)$"),
    period_start: str = Query(..., pattern=r"^\d{4}-\d{2}-\d{2}$"),
    period_end: str = Query(..., pattern=r"^\d{4}-\d{2}-\d{2}$"),
    auth: AuthContext = Depends(SCOPE_READ_ANY),
):
    """
    Returns the 'Principais otimizações realizadas no período' section.
    Used by Hermes when building weekly/monthly report content.
    Falls back to on-demand build if digest not pre-generated (not saved).
    """
    _scope(auth, client_id)
    from ...core.change_digest import build_report_change_section
    db = _db()
    try:
        return build_report_change_section(db, client_id, platform, period_start, period_end)
    except Exception as exc:
        raise HTTPException(500, f"REPORT_SECTION_FAILED: {str(exc)[:200]}")
