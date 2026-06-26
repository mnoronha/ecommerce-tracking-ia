"""
AI Search Visibility service.

Consulta e manipula dados de visibilidade da marca nas IAs (ChatGPT, Gemini,
Perplexity, Claude), importados via CSV do Ubersuggest AI Search Visibility.

Tabelas: ai_visibility_brands, ai_visibility_prompts, ai_visibility_imports,
         ai_visibility_metrics, ai_visibility_competitor_mentions,
         ai_visibility_monthly_summary
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from typing import Optional

from ..database import get_supabase

logger = logging.getLogger(__name__)


# ── Summary / KPIs ────────────────────────────────────────────────────────────

def get_summary(
    client_id: str,
    start: str,
    end: str,
    platform: Optional[str] = None,
) -> dict:
    """KPI cards para o dashboard de AI Visibility."""
    sb = get_supabase()
    q = (
        sb.table("ai_visibility_metrics")
        .select("own_brand_mentioned,own_brand_position,own_brand_sentiment,total_brands_mentioned,import_id")
        .eq("client_id", client_id)
        .gte("date", start)
        .lte("date", end)
    )
    if platform:
        q = q.eq("platform", platform)
    rows = q.execute().data or []

    if not rows:
        return {"has_data": False, "prompts_run": 0}

    total     = len(rows)
    mentioned = [r for r in rows if r.get("own_brand_mentioned")]

    mention_rate = len(mentioned) / total

    positions = [r["own_brand_position"] for r in mentioned if r.get("own_brand_position")]
    avg_position = sum(positions) / len(positions) if positions else None

    sentiments = [r["own_brand_sentiment"] for r in mentioned if r.get("own_brand_sentiment")]
    positive_rate = sentiments.count("positive") / len(sentiments) if sentiments else None

    total_mentions = sum(r.get("total_brands_mentioned") or 0 for r in rows)
    share_of_voice = len(mentioned) / total_mentions if total_mentions else None

    import_ids = list({r["import_id"] for r in rows if r.get("import_id")})
    last_import = None
    if import_ids:
        imp = (
            sb.table("ai_visibility_imports")
            .select("imported_at")
            .in_("id", import_ids[:20])
            .order("imported_at", desc=True)
            .limit(1)
            .execute()
        ).data
        if imp:
            last_import = imp[0].get("imported_at")

    return {
        "has_data": True,
        "prompts_run": total,
        "responses_analyzed": total,
        "mention_rate": round(mention_rate, 4),
        "avg_position": round(avg_position, 2) if avg_position is not None else None,
        "positive_sentiment_rate": round(positive_rate, 4) if positive_rate is not None else None,
        "share_of_voice": round(share_of_voice, 4) if share_of_voice is not None else None,
        "last_import_at": last_import,
    }


def get_mention_trend(
    client_id: str,
    start: str,
    end: str,
    platform: Optional[str] = None,
) -> list[dict]:
    """Taxa de menção por dia e plataforma — alimenta o gráfico de linha."""
    sb = get_supabase()
    q = (
        sb.table("ai_visibility_metrics")
        .select("date,platform,own_brand_mentioned")
        .eq("client_id", client_id)
        .gte("date", start)
        .lte("date", end)
        .order("date")
    )
    if platform:
        q = q.eq("platform", platform)
    rows = q.execute().data or []

    # Agrupar por (date, platform)
    buckets: dict[tuple, dict] = {}
    for r in rows:
        key = (r["date"], r["platform"])
        if key not in buckets:
            buckets[key] = {"date": r["date"], "platform": r["platform"], "total": 0, "mentioned": 0}
        buckets[key]["total"] += 1
        if r.get("own_brand_mentioned"):
            buckets[key]["mentioned"] += 1

    result = []
    for v in sorted(buckets.values(), key=lambda x: (x["date"], x["platform"])):
        result.append({
            "date":         v["date"],
            "platform":     v["platform"],
            "mention_rate": round(v["mentioned"] / v["total"], 4) if v["total"] else 0,
            "total":        v["total"],
            "mentioned":    v["mentioned"],
        })
    return result


# ── Prompts ───────────────────────────────────────────────────────────────────

def get_prompt_performance(
    client_id: str,
    start: str,
    end: str,
    platform: Optional[str] = None,
) -> list[dict]:
    """Performance por prompt — alimenta a tabela de prompts."""
    sb = get_supabase()

    prompts = (
        sb.table("ai_visibility_prompts")
        .select("id,prompt_text,category,intent,is_active")
        .eq("client_id", client_id)
        .eq("is_active", True)
        .execute()
    ).data or []

    if not prompts:
        return []

    prompt_ids = [p["id"] for p in prompts]
    q = (
        sb.table("ai_visibility_metrics")
        .select("prompt_id,platform,own_brand_mentioned,own_brand_position,own_brand_sentiment")
        .eq("client_id", client_id)
        .gte("date", start)
        .lte("date", end)
        .in_("prompt_id", prompt_ids)
    )
    if platform:
        q = q.eq("platform", platform)
    metrics = q.execute().data or []

    # Agrupar por prompt_id
    by_prompt: dict[str, dict] = {}
    for m in metrics:
        pid = m["prompt_id"]
        if pid not in by_prompt:
            by_prompt[pid] = {"total": 0, "mentioned": 0, "positions": [], "sentiments": []}
        g = by_prompt[pid]
        g["total"] += 1
        if m.get("own_brand_mentioned"):
            g["mentioned"] += 1
            if m.get("own_brand_position"):
                g["positions"].append(m["own_brand_position"])
            if m.get("own_brand_sentiment"):
                g["sentiments"].append(m["own_brand_sentiment"])

    result = []
    for p in prompts:
        g = by_prompt.get(p["id"], {"total": 0, "mentioned": 0, "positions": [], "sentiments": []})
        total   = g["total"]
        mention = g["mentioned"]
        pos     = g["positions"]
        sents   = g["sentiments"]
        result.append({
            "prompt_id":        p["id"],
            "prompt_text":      p["prompt_text"],
            "category":         p["category"],
            "intent":           p["intent"],
            "total_runs":       total,
            "mention_rate":     round(mention / total, 4) if total else None,
            "avg_position":     round(sum(pos) / len(pos), 2) if pos else None,
            "positive_rate":    round(sents.count("positive") / len(sents), 4) if sents else None,
        })
    return sorted(result, key=lambda x: (-(x["mention_rate"] or 0)))


# ── Competitors ───────────────────────────────────────────────────────────────

def get_competitor_shares(
    client_id: str,
    start: str,
    end: str,
    platform: Optional[str] = None,
) -> list[dict]:
    """Share of voice por competidor + própria marca."""
    sb = get_supabase()
    q = (
        sb.table("ai_visibility_competitor_mentions")
        .select("brand_name,platform")
        .eq("client_id", client_id)
        .gte("date", start)
        .lte("date", end)
    )
    if platform:
        q = q.eq("platform", platform)
    rows = q.execute().data or []

    counts: dict[str, int] = {}
    for r in rows:
        name = r["brand_name"]
        counts[name] = counts.get(name, 0) + 1

    total = sum(counts.values())
    result = [
        {"brand_name": k, "mentions": v, "share": round(v / total, 4) if total else 0}
        for k, v in counts.items()
    ]
    return sorted(result, key=lambda x: -x["mentions"])


# ── Import management ─────────────────────────────────────────────────────────

def create_import_record(
    client_id: str,
    period_start: str,
    period_end: str,
    file_name: Optional[str] = None,
    file_size_bytes: Optional[int] = None,
    source: str = "ubersuggest",
) -> dict:
    sb = get_supabase()
    row = (
        sb.table("ai_visibility_imports")
        .insert({
            "client_id":       client_id,
            "source":          source,
            "source_type":     "csv_upload",
            "period_start":    period_start,
            "period_end":      period_end,
            "file_name":       file_name,
            "file_size_bytes": file_size_bytes,
            "status":          "pending",
        })
        .execute()
    ).data[0]
    return row


def complete_import(
    import_id: str,
    rows_processed: int,
    rows_skipped: int,
    errors: list,
) -> None:
    sb = get_supabase()
    status = "imported" if not errors else "imported"
    sb.table("ai_visibility_imports").update({
        "status":         status,
        "rows_processed": rows_processed,
        "rows_skipped":   rows_skipped,
        "errors":         errors,
        "imported_at":    datetime.now(timezone.utc).isoformat(),
    }).eq("id", import_id).execute()


def fail_import(import_id: str, error: str) -> None:
    sb = get_supabase()
    sb.table("ai_visibility_imports").update({
        "status": "failed",
        "errors": [{"error": error}],
    }).eq("id", import_id).execute()


def revert_import(import_id: str) -> dict:
    """Deleta todas as métricas do import e marca como revertido."""
    sb = get_supabase()
    deleted = (
        sb.table("ai_visibility_metrics")
        .delete()
        .eq("import_id", import_id)
        .execute()
    ).data or []

    sb.table("ai_visibility_imports").update({
        "status": "reverted",
    }).eq("id", import_id).execute()

    return {"deleted_metrics": len(deleted)}


def get_import_history(client_id: str, limit: int = 20) -> list[dict]:
    sb = get_supabase()
    return (
        sb.table("ai_visibility_imports")
        .select("id,client_id,source,period_start,period_end,file_name,rows_processed,rows_skipped,errors,status,created_at,imported_at")
        .eq("client_id", client_id)
        .order("created_at", desc=True)
        .limit(limit)
        .execute()
    ).data or []


# ── Monthly summary ───────────────────────────────────────────────────────────

def recalc_monthly_summary(client_id: str, month: str) -> None:
    """Recalcula ai_visibility_monthly_summary para o mês (e inserido em YYYY-MM-01)."""
    sb = get_supabase()

    month_start = month[:7] + "-01"
    if len(month) > 7:
        month_start = month[:7] + "-01"

    # Limites do mês
    from datetime import date as _date
    import calendar
    d = _date.fromisoformat(month_start)
    last_day = calendar.monthrange(d.year, d.month)[1]
    month_end = f"{d.year:04d}-{d.month:02d}-{last_day:02d}"

    metrics = (
        sb.table("ai_visibility_metrics")
        .select("prompt_id,platform,own_brand_mentioned,own_brand_position,own_brand_sentiment,total_brands_mentioned")
        .eq("client_id", client_id)
        .gte("date", month_start)
        .lte("date", month_end)
        .execute()
    ).data or []

    if not metrics:
        return

    comp_mentions = (
        sb.table("ai_visibility_competitor_mentions")
        .select("brand_name")
        .eq("client_id", client_id)
        .gte("date", month_start)
        .lte("date", month_end)
        .execute()
    ).data or []

    def _summary_for(rows: list[dict]) -> dict:
        total     = len(rows)
        mentioned = [r for r in rows if r.get("own_brand_mentioned")]
        pos       = [r["own_brand_position"] for r in mentioned if r.get("own_brand_position")]
        sents     = [r["own_brand_sentiment"] for r in mentioned if r.get("own_brand_sentiment")]
        total_m   = sum(r.get("total_brands_mentioned") or 0 for r in rows)
        own       = len(mentioned)
        return {
            "total_prompts_run":               len({r["prompt_id"] for r in rows}),
            "total_responses_analyzed":        total,
            "own_brand_mention_rate":          round(own / total, 4) if total else None,
            "own_brand_avg_position":          round(sum(pos) / len(pos), 2) if pos else None,
            "own_brand_positive_sentiment_rate": round(sents.count("positive") / len(sents), 4) if sents else None,
            "share_of_voice":                  round(own / total_m, 4) if total_m else None,
        }

    def _top_competitors(comp_rows: list[dict]) -> dict:
        counts: dict[str, int] = {}
        for r in comp_rows:
            n = r["brand_name"]
            counts[n] = counts.get(n, 0) + 1
        total = sum(counts.values())
        top   = sorted(counts.items(), key=lambda x: -x[1])[:3]
        result = {}
        for i, (name, cnt) in enumerate(top, 1):
            result[f"top_competitor_{i}"]       = name
            result[f"top_competitor_{i}_share"] = round(cnt / total, 4) if total else None
        return result

    def _visibility_index(mention_rate: Optional[float], avg_pos: Optional[float], sov: Optional[float]) -> int:
        if mention_rate is None:
            return 0
        score = mention_rate * 50
        if avg_pos is not None:
            pos_score = max(0, (10 - avg_pos) / 9) * 30
            score += pos_score
        if sov is not None:
            score += sov * 20
        return min(100, int(score))

    platforms = list({r["platform"] for r in metrics})

    for platform in ([None] + platforms):
        if platform:
            subset = [r for r in metrics if r["platform"] == platform]
            comp_subset = [r for r in comp_mentions]  # no platform filter on comp
        else:
            subset = metrics
            comp_subset = comp_mentions

        s  = _summary_for(subset)
        tc = _top_competitors(comp_subset)
        vi = _visibility_index(s.get("own_brand_mention_rate"), s.get("own_brand_avg_position"), s.get("share_of_voice"))

        row = {
            "client_id":  client_id,
            "month":      month_start,
            "platform":   platform,
            "visibility_index": vi,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            **s,
            **tc,
        }
        try:
            sb.table("ai_visibility_monthly_summary").upsert(
                row, on_conflict="client_id,month,platform"
            ).execute()
        except Exception as exc:
            logger.warning("recalc_monthly_summary upsert failed: %s", exc)


# ── Brand management ──────────────────────────────────────────────────────────

def get_brands(client_id: str) -> list[dict]:
    sb = get_supabase()
    return (
        sb.table("ai_visibility_brands")
        .select("*")
        .eq("client_id", client_id)
        .order("is_own_brand", desc=True)
        .order("competitor_priority")
        .execute()
    ).data or []


def upsert_brand(client_id: str, brand_name: str, is_own_brand: bool = False, **kwargs) -> dict:
    sb = get_supabase()
    row = {
        "client_id":    client_id,
        "brand_name":   brand_name,
        "is_own_brand": is_own_brand,
        **kwargs,
    }
    return (
        sb.table("ai_visibility_brands")
        .upsert(row, on_conflict="client_id,brand_name")
        .execute()
    ).data[0]


# ── DataForSEO config ─────────────────────────────────────────────────────────

def get_dataforseo_config(client_id: str) -> Optional[dict]:
    sb = get_supabase()
    rows = (
        sb.table("dataforseo_configs")
        .select("*")
        .eq("client_id", client_id)
        .limit(1)
        .execute()
    ).data
    return rows[0] if rows else None


def upsert_dataforseo_config(client_id: str, **fields) -> dict:
    sb = get_supabase()
    existing = get_dataforseo_config(client_id)
    payload = {
        "client_id":  client_id,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        **fields,
    }
    if existing:
        return (
            sb.table("dataforseo_configs")
            .update(payload)
            .eq("client_id", client_id)
            .execute()
        ).data[0]
    return (
        sb.table("dataforseo_configs")
        .insert(payload)
        .execute()
    ).data[0]


# ── DataForSEO usage ──────────────────────────────────────────────────────────

def get_usage_summary(client_id: Optional[str] = None, days: int = 30) -> list[dict]:
    """Cost summary per client for the last N days. If client_id is None, returns all clients."""
    from datetime import date, timedelta
    sb  = get_supabase()
    since = (date.today() - timedelta(days=days)).isoformat()
    q = (
        sb.table("dataforseo_usage_log")
        .select("client_id,endpoint,request_units,cost_usd,created_at")
        .gte("created_at", since)
        .order("created_at", desc=True)
    )
    if client_id:
        q = q.eq("client_id", client_id)
    rows = q.limit(500).execute().data or []

    buckets: dict[str, dict] = {}
    for r in rows:
        cid = r.get("client_id") or "unknown"
        if cid not in buckets:
            buckets[cid] = {"client_id": cid, "total_cost_usd": 0.0, "request_units": 0, "runs": 0}
        buckets[cid]["total_cost_usd"] += float(r.get("cost_usd") or 0)
        buckets[cid]["request_units"]  += int(r.get("request_units") or 0)

    return sorted(buckets.values(), key=lambda x: -x["total_cost_usd"])
