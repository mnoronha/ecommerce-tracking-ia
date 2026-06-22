"""
Google Search Console endpoints.

GET /search-console/{pixel_id}/overview       — clicks, CTR, posição, top queries/páginas
GET /search-console/{pixel_id}/opportunities  — queries low-CTR + páginas 4-10
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException

from ..database import get_supabase
from ..services import crypto, search_console as sc_svc

logger = logging.getLogger(__name__)
router = APIRouter()


def _get_sc_client(pixel_id: str) -> dict:
    sb  = get_supabase()
    row = (
        sb.table("clients")
        .select("id, google_ads_refresh_token, search_console_site_url")
        .eq("pixel_id", pixel_id)
        .eq("is_active", True)
        .limit(1)
        .execute()
    )
    if not (row and row.data):
        raise HTTPException(status_code=404, detail="Client not found")
    c = crypto.decrypt_client_secrets(row.data[0])
    if not c.get("google_ads_refresh_token"):
        raise HTTPException(status_code=400, detail="Google OAuth not connected")
    if not c.get("search_console_site_url"):
        raise HTTPException(status_code=400, detail="search_console_site_url not configured")
    return c


def _parse_dates(start: Optional[str], end: Optional[str], days: int):
    from datetime import date as _date
    if start and end:
        try:
            return _date.fromisoformat(start), _date.fromisoformat(end)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid date — use YYYY-MM-DD")
    today    = datetime.now(timezone.utc).date()
    end_dt   = today - timedelta(days=1)
    start_dt = end_dt - timedelta(days=days - 1)
    return start_dt, end_dt


@router.get(
    "/search-console/{pixel_id}/overview",
    summary="Search Console — visão geral: clicks, CTR, posição, top queries/páginas",
    tags=["search_console"],
)
async def sc_overview(
    pixel_id: str,
    start:    Optional[str] = None,
    end:      Optional[str] = None,
    days:     int = 30,
):
    c    = _get_sc_client(pixel_id)
    s, e = _parse_dates(start, end, days)
    result = sc_svc.fetch_overview(
        site_url=c["search_console_site_url"],
        refresh_token=c["google_ads_refresh_token"],
        start_date=s,
        end_date=e,
    )
    if "error" in result:
        if result.get("error") == "scope_missing":
            raise HTTPException(
                status_code=403,
                detail="scope_missing — reconecte o Google OAuth incluindo a permissão Search Console (webmasters.readonly)",
            )
        raise HTTPException(status_code=502, detail=result.get("detail", result["error"]))
    return result


@router.get(
    "/search-console/{pixel_id}/opportunities",
    summary="Search Console — oportunidades: CTR baixo + posições 4-10",
    tags=["search_console"],
)
async def sc_opportunities(
    pixel_id: str,
    start:    Optional[str] = None,
    end:      Optional[str] = None,
    days:     int = 90,
):
    c    = _get_sc_client(pixel_id)
    s, e = _parse_dates(start, end, days)
    result = sc_svc.fetch_opportunities(
        site_url=c["search_console_site_url"],
        refresh_token=c["google_ads_refresh_token"],
        start_date=s,
        end_date=e,
    )
    if "error" in result:
        if result.get("error") == "scope_missing":
            raise HTTPException(status_code=403, detail="scope_missing")
        raise HTTPException(status_code=502, detail=result.get("detail", result["error"]))
    return result
