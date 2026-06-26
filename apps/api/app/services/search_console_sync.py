"""
Google Search Console — coleta diária com persistência no banco.

Coleta 5 dimensões por vez e persiste em search_console_performance.
Agrega snapshots diários e identifica queries de oportunidade.
Rastreia AI Overviews via searchAppearance dimension.
"""

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Optional
from urllib.parse import quote

import httpx

from ..config import settings
from ..database import get_supabase
from . import crypto
from .search_console import _get_token, _GSC_URL

logger = logging.getLogger(__name__)

_MAX_ROWS = 25_000


# ── Core fetch ────────────────────────────────────────────────────────────────

def _fetch_rows(site_url: str, token: str, body: dict) -> list:
    url = _GSC_URL.format(site_url=quote(site_url, safe=""))
    try:
        resp = httpx.post(
            url,
            json=body,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            timeout=30.0,
        )
        if resp.status_code == 403:
            logger.warning("search_console_sync: 403 — scope missing for %s", site_url)
            return []
        if resp.status_code != 200:
            logger.warning("search_console_sync: HTTP %s — %s", resp.status_code, resp.text[:200])
            return []
        return resp.json().get("rows", [])
    except Exception as exc:
        logger.warning("search_console_sync: fetch failed: %s", exc)
        return []


def _collect_dimension_set(
    site_url: str, token: str, start: date, end: date, dimensions: list
) -> list[dict]:
    """Fetch all rows for a given dimension set with pagination."""
    records = []
    start_row = 0
    while True:
        body = {
            "startDate":       start.isoformat(),
            "endDate":         end.isoformat(),
            "dimensions":      dimensions,
            "rowLimit":        _MAX_ROWS,
            "startRow":        start_row,
            "aggregationType": "auto",
        }
        rows = _fetch_rows(site_url, token, body)
        if not rows:
            break
        for row in rows:
            keys = row.get("keys", [])
            rec: dict = {
                "impressions": int(row.get("impressions", 0)),
                "clicks":      int(row.get("clicks", 0)),
                "ctr":         row.get("ctr"),
                "position":    row.get("position"),
                "query":       None,
                "page":        None,
                "country":     None,
                "device":      None,
                "search_appearance": None,
            }
            for i, dim in enumerate(dimensions):
                if i < len(keys):
                    if dim == "query":              rec["query"]   = keys[i]
                    elif dim == "page":             rec["page"]    = keys[i]
                    elif dim == "country":          rec["country"] = keys[i]
                    elif dim == "device":           rec["device"]  = keys[i]
                    elif dim == "searchAppearance": rec["search_appearance"] = keys[i]
            records.append(rec)
        if len(rows) < _MAX_ROWS:
            break
        start_row += _MAX_ROWS
    return records


# ── Persist ───────────────────────────────────────────────────────────────────

def _upsert_performance(client_id: str, target_date: date, records: list[dict]) -> int:
    if not records:
        return 0
    sb = get_supabase()
    rows = []
    for r in records:
        rows.append({
            "client_id":        client_id,
            "date":             target_date.isoformat(),
            "query":            r.get("query"),
            "page":             r.get("page"),
            "country":          r.get("country") or "zzz",
            "device":           r.get("device") or "ALL",
            "search_appearance": r.get("search_appearance") or "WEB",
            "impressions":      r.get("impressions", 0),
            "clicks":           r.get("clicks", 0),
            "ctr":              r.get("ctr"),
            "position":         r.get("position"),
        })
    # Supabase upsert in chunks of 500
    chunk_size = 500
    total = 0
    for i in range(0, len(rows), chunk_size):
        chunk = rows[i : i + chunk_size]
        try:
            sb.table("search_console_performance").upsert(
                chunk,
                on_conflict="client_id,date,query,page,country,device,search_appearance",
            ).execute()
            total += len(chunk)
        except Exception as exc:
            logger.warning("sc_sync: upsert chunk failed: %s", exc)
    return total


# ── Snapshots ─────────────────────────────────────────────────────────────────

def _build_snapshot(client_id: str, target_date: date) -> None:
    sb  = get_supabase()
    iso = target_date.isoformat()

    # Pull the day's rows from DB (just inserted)
    rows = (
        sb.table("search_console_performance")
        .select("impressions,clicks,ctr,position,query,page,search_appearance")
        .eq("client_id", client_id)
        .eq("date", iso)
        .execute()
    ).data or []

    if not rows:
        return

    total_imp   = sum(r["impressions"] for r in rows)
    total_clk   = sum(r["clicks"]      for r in rows)
    avg_ctr     = total_clk / total_imp if total_imp else None
    positions   = [r["position"] for r in rows if r.get("position")]
    avg_pos     = sum(positions) / len(positions) if positions else None

    unique_queries = len({r["query"] for r in rows if r.get("query")})
    unique_pages   = len({r["page"]  for r in rows if r.get("page")})

    ai_rows = [r for r in rows if r.get("search_appearance") == "AI_OVERVIEW"]
    ai_imp  = sum(r["impressions"] for r in ai_rows)
    ai_clk  = sum(r["clicks"]      for r in ai_rows)
    ai_urls = len({r["page"] for r in ai_rows if r.get("page")})

    fs_imp = sum(r["impressions"] for r in rows if r.get("search_appearance") == "FEATURED_SNIPPET")

    # 7-day comparison
    d7 = (target_date - timedelta(days=7)).isoformat()
    prev_rows = (
        sb.table("search_console_performance")
        .select("impressions,clicks,position")
        .eq("client_id", client_id)
        .eq("date", d7)
        .execute()
    ).data or []

    prev_imp = sum(r["impressions"] for r in prev_rows)
    prev_clk = sum(r["clicks"]      for r in prev_rows)
    prev_pos = [r["position"] for r in prev_rows if r.get("position")]
    prev_avg_pos = sum(prev_pos) / len(prev_pos) if prev_pos else None

    def pct_change(curr, prev):
        if not prev:
            return None
        return round((curr - prev) / prev * 100, 2)

    snapshot = {
        "client_id":                   client_id,
        "date":                        iso,
        "total_impressions":           total_imp,
        "total_clicks":                total_clk,
        "avg_ctr":                     avg_ctr,
        "avg_position":                avg_pos,
        "unique_queries":              unique_queries,
        "unique_pages":                unique_pages,
        "ai_overview_appearances":     ai_imp,
        "ai_overview_clicks":          ai_clk,
        "ai_overview_unique_urls":     ai_urls,
        "featured_snippet_impressions": fs_imp,
        "impressions_change_vs_7d":    pct_change(total_imp, prev_imp),
        "clicks_change_vs_7d":         pct_change(total_clk, prev_clk),
        "position_change_vs_7d":       pct_change(avg_pos, prev_avg_pos) if avg_pos and prev_avg_pos else None,
    }
    try:
        sb.table("search_console_daily_snapshots").upsert(
            snapshot, on_conflict="client_id,date"
        ).execute()
    except Exception as exc:
        logger.warning("sc_sync: snapshot upsert failed: %s", exc)


# ── Opportunity queries ───────────────────────────────────────────────────────

def _identify_opportunities(client_id: str) -> int:
    sb  = get_supabase()
    cutoff = (date.today() - timedelta(days=30)).isoformat()

    rows = (
        sb.table("search_console_performance")
        .select("query,page,impressions,clicks,ctr,position")
        .eq("client_id", client_id)
        .gte("date", cutoff)
        .not_.is_("query", "null")
        .execute()
    ).data or []

    # Aggregate by query
    agg: dict[str, dict] = {}
    for r in rows:
        q = r["query"]
        if not q:
            continue
        if q not in agg:
            agg[q] = {"impressions": [], "clicks": [], "positions": [], "ctrs": [], "pages": set()}
        agg[q]["impressions"].append(r["impressions"])
        agg[q]["clicks"].append(r["clicks"])
        if r.get("position"):
            agg[q]["positions"].append(r["position"])
        if r.get("ctr"):
            agg[q]["ctrs"].append(r["ctr"])
        if r.get("page"):
            agg[q]["pages"].add(r["page"])

    opportunities = []
    for query, data in agg.items():
        avg_imp = sum(data["impressions"]) / len(data["impressions"])
        avg_pos = sum(data["positions"])  / len(data["positions"])  if data["positions"] else 99
        avg_ctr = sum(data["ctrs"])       / len(data["ctrs"])       if data["ctrs"]      else 0

        if avg_imp < 50:
            continue

        opp_type = None
        est_potential = None

        if 11 <= avg_pos <= 30 and avg_imp >= 100:
            opp_type = "high_impression_low_position"
            est_potential = max(0, int(avg_imp * 0.06 - (sum(data["clicks"]) / len(data["clicks"]))))
        elif avg_imp >= 100 and avg_ctr < 0.03:
            opp_type = "high_impression_low_ctr"
            est_potential = int(avg_imp * 0.05 - (sum(data["clicks"]) / len(data["clicks"])))
        elif 4 <= avg_pos <= 10 and avg_imp >= 50:
            opp_type = "emerging"
            est_potential = int(avg_imp * 0.15 - (sum(data["clicks"]) / len(data["clicks"])))

        if not opp_type:
            continue

        opportunities.append({
            "client_id":                 client_id,
            "query":                     query,
            "related_pages":             list(data["pages"])[:5],
            "opportunity_type":          opp_type,
            "avg_impressions_30d":       int(avg_imp),
            "avg_position_30d":          round(avg_pos, 2),
            "avg_ctr_30d":               round(avg_ctr, 6),
            "estimated_potential_clicks": max(0, est_potential or 0),
            "last_seen_at":              datetime.now(timezone.utc).isoformat(),
        })

    if not opportunities:
        return 0

    try:
        sb.table("search_console_opportunity_queries").upsert(
            opportunities,
            on_conflict="client_id,query",
        ).execute()
    except Exception as exc:
        logger.warning("sc_sync: opportunities upsert failed: %s", exc)
        return 0

    return len(opportunities)


# ── Piece performance ─────────────────────────────────────────────────────────

def _update_piece_performance(client_id: str, target_date: date) -> None:
    sb  = get_supabase()
    iso = target_date.isoformat()

    pieces = (
        sb.table("content_pieces")
        .select("id,url_published,published_at")
        .eq("client_id", client_id)
        .eq("status", "published")
        .not_.is_("url_published", "null")
        .execute()
    ).data or []

    for piece in pieces:
        url = piece["url_published"]
        if not url:
            continue

        # Get all rows for this URL on target_date
        perf_rows = (
            sb.table("search_console_performance")
            .select("query,impressions,clicks,ctr,position,search_appearance")
            .eq("client_id", client_id)
            .eq("date", iso)
            .eq("page", url)
            .execute()
        ).data or []

        if not perf_rows:
            continue

        total_imp   = sum(r["impressions"] for r in perf_rows)
        total_clk   = sum(r["clicks"]      for r in perf_rows)
        positions   = [r["position"] for r in perf_rows if r.get("position")]
        avg_pos     = sum(positions) / len(positions) if positions else None
        avg_ctr     = total_clk / total_imp if total_imp else None

        ai_rows     = [r for r in perf_rows if r.get("search_appearance") == "AI_OVERVIEW"]
        ai_imp      = sum(r["impressions"] for r in ai_rows)

        top_queries = sorted(
            [
                {"query": r["query"], "impressions": r["impressions"],
                 "clicks": r["clicks"], "position": r.get("position")}
                for r in perf_rows if r.get("query")
            ],
            key=lambda x: x["impressions"], reverse=True,
        )[:10]

        pub_at = piece.get("published_at")
        days_since = None
        if pub_at:
            try:
                pub_date = datetime.fromisoformat(pub_at.replace("Z", "+00:00")).date()
                days_since = (target_date - pub_date).days
            except Exception:
                pass

        row = {
            "piece_id":               piece["id"],
            "client_id":              client_id,
            "date":                   iso,
            "url":                    url,
            "impressions":            total_imp,
            "clicks":                 total_clk,
            "ctr":                    avg_ctr,
            "avg_position":           avg_pos,
            "appeared_in_ai_overview": ai_imp > 0,
            "ai_overview_impressions": ai_imp,
            "top_queries":            top_queries,
            "days_since_publication": days_since,
        }
        try:
            sb.table("search_console_piece_performance").upsert(
                row, on_conflict="piece_id,date"
            ).execute()
        except Exception as exc:
            logger.warning("sc_sync: piece_perf upsert failed piece=%s: %s", piece["id"], exc)


# ── Main sync entry point ─────────────────────────────────────────────────────

def sync_client(client_id: str, pixel_id: str, site_url: str, refresh_token: str,
                start: date, end: date) -> dict:
    """
    Full sync for one client over [start, end].
    Collects query+page+searchAppearance, persists, builds snapshot, updates pieces.
    """
    token = _get_token(refresh_token)
    if not token:
        return {"error": "token_refresh_failed"}

    total_rows = 0
    current = start
    while current <= end:
        # Collect query + page (main performance data)
        qp_rows = _collect_dimension_set(site_url, token, current, current,
                                          ["query", "page"])
        total_rows += _upsert_performance(client_id, current, qp_rows)

        # Collect searchAppearance separately (AI Overviews)
        sa_rows = _collect_dimension_set(site_url, token, current, current,
                                          ["page", "searchAppearance"])
        # Mark search_appearance properly (query will be null for this set)
        for r in sa_rows:
            r["query"] = None
        total_rows += _upsert_performance(client_id, current, sa_rows)

        # Build snapshot from persisted data
        _build_snapshot(client_id, current)

        # Update AI Presence piece performance
        _update_piece_performance(client_id, current)

        current += timedelta(days=1)

    # Identify opportunities from last 30 days
    opps = _identify_opportunities(client_id)

    logger.info(
        "sc_sync: %s [%s→%s] rows=%d opps=%d",
        pixel_id, start, end, total_rows, opps,
    )
    return {
        "client":     pixel_id,
        "start":      start.isoformat(),
        "end":        end.isoformat(),
        "rows_saved": total_rows,
        "opportunities_identified": opps,
    }


# ── Cron entry point ──────────────────────────────────────────────────────────

def run_daily_sync_all_clients() -> None:
    """
    Daily cron: sync last 3 days for all clients with SC configured.
    SC data has 2-3 day delay, so we always collect d-3 to d-1.
    """
    sb = get_supabase()
    try:
        clients = (
            sb.table("clients")
            .select("id,pixel_id,search_console_site_url,google_ads_refresh_token")
            .eq("is_active", True)
            .not_.is_("search_console_site_url", "null")
            .execute()
        ).data or []
    except Exception as exc:
        logger.error("sc_sync cron: clients query failed: %s", exc)
        return

    today  = datetime.now(timezone.utc).date()
    end    = today - timedelta(days=2)
    start  = end - timedelta(days=2)

    for c in clients:
        crypto.decrypt_client_secrets(c)
        refresh_token = c.get("google_ads_refresh_token")
        site_url      = c.get("search_console_site_url")
        if not refresh_token or not site_url:
            continue
        try:
            result = sync_client(
                client_id=c["id"],
                pixel_id=c.get("pixel_id", c["id"]),
                site_url=site_url,
                refresh_token=refresh_token,
                start=start,
                end=end,
            )
            logger.info("sc_sync cron: %s", result)
        except Exception as exc:
            logger.error("sc_sync cron: failed for %s: %s", c.get("pixel_id"), exc)


# ── Historical backfill ───────────────────────────────────────────────────────

def backfill_client(client_id: str, pixel_id: str, site_url: str,
                    refresh_token: str, months_back: int = 16) -> dict:
    """Import up to 16 months of historical SC data in monthly chunks."""
    today   = date.today()
    end     = today - timedelta(days=2)
    start   = end - timedelta(days=months_back * 30)

    total_rows = 0
    total_opps = 0
    current = start

    while current <= end:
        chunk_end = min(current + timedelta(days=29), end)
        result = sync_client(
            client_id=client_id,
            pixel_id=pixel_id,
            site_url=site_url,
            refresh_token=refresh_token,
            start=current,
            end=chunk_end,
        )
        if "error" in result:
            return result
        total_rows += result.get("rows_saved", 0)
        total_opps  = result.get("opportunities_identified", 0)  # last run is fine
        current     = chunk_end + timedelta(days=1)

    return {
        "client":     pixel_id,
        "months":     months_back,
        "rows_saved": total_rows,
        "opportunities": total_opps,
    }
