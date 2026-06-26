"""
Google Search Console endpoints.

GET  /search-console/{pixel_id}/overview        — live: clicks, CTR, posição, top queries/páginas
GET  /search-console/{pixel_id}/opportunities   — live: queries low-CTR + posições 4-10
GET  /search-console/{pixel_id}/snapshots       — DB: série diária de snapshots persistidos
GET  /search-console/{pixel_id}/ai-overviews    — DB: aparições em AI Overview por dia + URLs
GET  /search-console/{pixel_id}/opportunities-db — DB: oportunidades identificadas e salvas
PATCH /search-console/{pixel_id}/opportunities-db/{opp_id} — atualiza status de oportunidade
POST /search-console/{pixel_id}/backfill        — dispara backfill histórico (background)
POST /search-console/{pixel_id}/sync            — dispara sync manual de N dias (background)
GET  /search-console/{pixel_id}/piece-performance — performance de peças do AI Presence
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel

from ..database import get_supabase
from ..services import crypto, search_console as sc_svc, search_console_sync as sc_sync

logger = logging.getLogger(__name__)
router = APIRouter()


# ── Helpers ───────────────────────────────────────────────────────────────────

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
        raise HTTPException(
            status_code=400,
            detail="search_console_site_url not configured — add it in Settings",
        )
    return c


def _parse_dates(start: Optional[str], end: Optional[str], days: int):
    from datetime import date as _date
    if start and end:
        try:
            return _date.fromisoformat(start), _date.fromisoformat(end)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid date — use YYYY-MM-DD")
    today    = datetime.now(timezone.utc).date()
    end_dt   = today - timedelta(days=2)   # SC has 2-3 day delay
    start_dt = end_dt - timedelta(days=days - 1)
    return start_dt, end_dt


# ── Live endpoints (existing, unchanged) ─────────────────────────────────────

@router.get(
    "/search-console/{pixel_id}/overview",
    summary="Search Console — visão geral live: clicks, CTR, posição, top queries/páginas",
    tags=["search_console"],
)
async def sc_overview(
    pixel_id: str,
    start:    Optional[str] = None,
    end:      Optional[str] = None,
    days:     int = 28,
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
                detail="scope_missing — reconecte o Google OAuth incluindo webmasters.readonly",
            )
        raise HTTPException(status_code=502, detail=result.get("detail", result["error"]))
    return result


@router.get(
    "/search-console/{pixel_id}/opportunities",
    summary="Search Console — oportunidades live: CTR baixo + posições 4-10",
    tags=["search_console"],
)
async def sc_opportunities_live(
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


# ── DB-backed endpoints ───────────────────────────────────────────────────────

@router.get(
    "/search-console/{pixel_id}/snapshots",
    summary="Search Console — snapshots diários do DB",
    tags=["search_console"],
)
async def sc_snapshots(
    pixel_id: str,
    start:    Optional[str] = None,
    end:      Optional[str] = None,
    days:     int = 28,
):
    c    = _get_sc_client(pixel_id)
    s, e = _parse_dates(start, end, days)
    sb   = get_supabase()

    rows = (
        sb.table("search_console_daily_snapshots")
        .select("*")
        .eq("client_id", c["id"])
        .gte("date", s.isoformat())
        .lte("date", e.isoformat())
        .order("date")
        .execute()
    ).data or []

    if not rows:
        return {"data": [], "period": {"start": s.isoformat(), "end": e.isoformat()},
                "message": "no_data — run backfill first"}

    total_imp = sum(r["total_impressions"] for r in rows)
    total_clk = sum(r["total_clicks"]      for r in rows)
    ai_app    = sum(r.get("ai_overview_appearances", 0) or 0 for r in rows)
    ai_clk    = sum(r.get("ai_overview_clicks", 0) or 0      for r in rows)
    ai_urls   = max((r.get("ai_overview_unique_urls", 0) or 0 for r in rows), default=0)

    return {
        "summary": {
            "total_impressions":    total_imp,
            "total_clicks":         total_clk,
            "avg_ctr":              round(total_clk / total_imp * 100, 2) if total_imp else None,
            "ai_overview_total":    ai_app,
            "ai_overview_clicks":   ai_clk,
            "ai_overview_urls":     ai_urls,
        },
        "daily":  rows,
        "period": {"start": s.isoformat(), "end": e.isoformat()},
    }


@router.get(
    "/search-console/{pixel_id}/ai-overviews",
    summary="Search Console — AI Overview: aparições por URL e query",
    tags=["search_console"],
)
async def sc_ai_overviews(
    pixel_id: str,
    start:    Optional[str] = None,
    end:      Optional[str] = None,
    days:     int = 28,
    limit:    int = 50,
):
    c    = _get_sc_client(pixel_id)
    s, e = _parse_dates(start, end, days)
    sb   = get_supabase()

    rows = (
        sb.table("search_console_performance")
        .select("date,page,query,impressions,clicks,position")
        .eq("client_id", c["id"])
        .eq("search_appearance", "AI_OVERVIEW")
        .gte("date", s.isoformat())
        .lte("date", e.isoformat())
        .order("impressions", desc=True)
        .limit(limit)
        .execute()
    ).data or []

    # Aggregate by URL
    by_url: dict = {}
    for r in rows:
        url = r["page"] or "(unknown)"
        if url not in by_url:
            by_url[url] = {"url": url, "total_impressions": 0, "total_clicks": 0, "queries": []}
        by_url[url]["total_impressions"] += r["impressions"]
        by_url[url]["total_clicks"]      += r["clicks"]
        if r.get("query"):
            by_url[url]["queries"].append({
                "query": r["query"], "impressions": r["impressions"],
                "clicks": r["clicks"], "date": r["date"],
            })

    url_list = sorted(by_url.values(), key=lambda x: x["total_impressions"], reverse=True)

    # Daily series
    daily: dict = {}
    for r in rows:
        d = r["date"]
        if d not in daily:
            daily[d] = {"date": d, "impressions": 0, "clicks": 0, "unique_urls": set()}
        daily[d]["impressions"] += r["impressions"]
        daily[d]["clicks"]      += r["clicks"]
        if r.get("page"):
            daily[d]["unique_urls"].add(r["page"])

    daily_list = [
        {**v, "unique_urls": len(v["unique_urls"])}
        for v in sorted(daily.values(), key=lambda x: x["date"])
    ]

    return {
        "summary": {
            "total_impressions": sum(r["impressions"] for r in rows),
            "total_clicks":      sum(r["clicks"]      for r in rows),
            "unique_urls":       len(by_url),
        },
        "by_url": url_list[:30],
        "daily":  daily_list,
        "period": {"start": s.isoformat(), "end": e.isoformat()},
    }


@router.get(
    "/search-console/{pixel_id}/opportunities-db",
    summary="Search Console — oportunidades identificadas (DB)",
    tags=["search_console"],
)
async def sc_opportunities_db(
    pixel_id:     str,
    status:       Optional[str] = None,
    opp_type:     Optional[str] = None,
    limit:        int = 50,
):
    c  = _get_sc_client(pixel_id)
    sb = get_supabase()

    q = (
        sb.table("search_console_opportunity_queries")
        .select("*")
        .eq("client_id", c["id"])
        .order("avg_impressions_30d", desc=True)
        .limit(limit)
    )
    if status:
        q = q.eq("status", status)
    else:
        q = q.neq("status", "dismissed")
    if opp_type:
        q = q.eq("opportunity_type", opp_type)

    rows = (q.execute()).data or []
    return {"items": rows, "total": len(rows)}


class OppUpdate(BaseModel):
    status:             Optional[str] = None   # 'in_pauta'|'addressed'|'dismissed'
    related_briefing_id: Optional[str] = None


@router.patch(
    "/search-console/{pixel_id}/opportunities-db/{opp_id}",
    summary="Atualiza status de oportunidade",
    tags=["search_console"],
)
async def update_opportunity(pixel_id: str, opp_id: str, body: OppUpdate):
    _get_sc_client(pixel_id)
    sb     = get_supabase()
    update = {k: v for k, v in body.model_dump().items() if v is not None}
    if not update:
        raise HTTPException(status_code=400, detail="Nothing to update")
    sb.table("search_console_opportunity_queries").update(update).eq("id", opp_id).execute()
    return {"ok": True}


@router.get(
    "/search-console/{pixel_id}/piece-performance",
    summary="Search Console — performance das peças do AI Presence",
    tags=["search_console"],
)
async def sc_piece_performance(
    pixel_id: str,
    start:    Optional[str] = None,
    end:      Optional[str] = None,
    days:     int = 30,
    limit:    int = 30,
):
    c    = _get_sc_client(pixel_id)
    s, e = _parse_dates(start, end, days)
    sb   = get_supabase()

    rows = (
        sb.table("search_console_piece_performance")
        .select("*, content_pieces(final_title, slug, published_at, url_published)")
        .eq("client_id", c["id"])
        .gte("date", s.isoformat())
        .lte("date", e.isoformat())
        .order("impressions", desc=True)
        .limit(limit)
        .execute()
    ).data or []

    return {"items": rows, "period": {"start": s.isoformat(), "end": e.isoformat()}}


# ── Sync / backfill ───────────────────────────────────────────────────────────

class BackfillRequest(BaseModel):
    months_back: int = 16


@router.post(
    "/search-console/{pixel_id}/backfill",
    summary="Search Console — backfill histórico (até 16 meses, background)",
    tags=["search_console"],
)
async def sc_backfill(pixel_id: str, body: BackfillRequest, background_tasks: BackgroundTasks):
    c = _get_sc_client(pixel_id)
    if body.months_back < 1 or body.months_back > 16:
        raise HTTPException(status_code=400, detail="months_back deve ser entre 1 e 16")

    def _run():
        result = sc_sync.backfill_client(
            client_id=c["id"],
            pixel_id=pixel_id,
            site_url=c["search_console_site_url"],
            refresh_token=c["google_ads_refresh_token"],
            months_back=body.months_back,
        )
        logger.info("sc_backfill done: %s", result)

    background_tasks.add_task(_run)
    return {
        "status":      "queued",
        "pixel_id":    pixel_id,
        "months_back": body.months_back,
        "message":     "Backfill iniciado. Pode levar vários minutos. Acompanhe nos logs.",
    }


class SyncRequest(BaseModel):
    days: int = 7


@router.post(
    "/search-console/{pixel_id}/sync",
    summary="Search Console — sync manual de N dias (background)",
    tags=["search_console"],
)
async def sc_sync_manual(pixel_id: str, body: SyncRequest, background_tasks: BackgroundTasks):
    c = _get_sc_client(pixel_id)
    from datetime import date as _date
    today = datetime.now(timezone.utc).date()
    end   = today - timedelta(days=2)
    start = end - timedelta(days=body.days - 1)

    def _run():
        result = sc_sync.sync_client(
            client_id=c["id"],
            pixel_id=pixel_id,
            site_url=c["search_console_site_url"],
            refresh_token=c["google_ads_refresh_token"],
            start=start,
            end=end,
        )
        logger.info("sc_sync manual done: %s", result)

    background_tasks.add_task(_run)
    return {
        "status":   "queued",
        "pixel_id": pixel_id,
        "start":    start.isoformat(),
        "end":      end.isoformat(),
    }
