"""
Google Merchant Center service.

Coleta diária via Content API for Shopping v2.1. Stores products,
approval statuses, issues, price benchmarks, best sellers, and performance.
Calcula feed_health_score e gera alertas pós-coleta.

Scope OAuth: https://www.googleapis.com/auth/content
Credenciais: GOOGLE_ADS_OAUTH_CLIENT_ID/SECRET compartilhados (Railway env)
             merchant_center_refresh_token por cliente (clients table, encrypted)
"""

from __future__ import annotations

import logging
import time
from datetime import date, datetime, timedelta, timezone
from typing import Optional

import httpx

from ..config import settings
from ..database import get_supabase
from ..services import crypto

logger = logging.getLogger(__name__)

_TOKEN_URL = "https://oauth2.googleapis.com/token"
_MC_API    = "https://shoppingcontent.googleapis.com/content/v2.1"
_PAGE_SIZE = 250

# Access token cache (mesmo padrão do google_ads.py)
_token_cache: dict = {}


# ── Auth ──────────────────────────────────────────────────────────────────────

def _get_access_token(refresh_token: str) -> Optional[str]:
    """Retorna access token válido, refrescando se necessário."""
    now       = time.time()
    cache_key = refresh_token[:16]
    cached    = _token_cache.get(cache_key, {})
    if cached.get("token") and cached.get("expires_at", 0) > now + 60:
        return cached["token"]

    try:
        resp = httpx.post(
            _TOKEN_URL,
            data={
                "client_id":     settings.GOOGLE_ADS_OAUTH_CLIENT_ID,
                "client_secret": settings.GOOGLE_ADS_OAUTH_CLIENT_SECRET,
                "refresh_token": refresh_token,
                "grant_type":    "refresh_token",
            },
            timeout=10.0,
        )
        if resp.status_code == 200:
            data = resp.json()
            _token_cache[cache_key] = {
                "token":      data["access_token"],
                "expires_at": now + data.get("expires_in", 3600),
            }
            return _token_cache[cache_key]["token"]
        logger.warning("merchant_center token refresh HTTP %s: %s", resp.status_code, resp.text[:200])
    except Exception as exc:
        logger.error("merchant_center _get_access_token: %s", exc)
    return None


def _headers(access_token: str) -> dict:
    return {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}


# ── Collect products ──────────────────────────────────────────────────────────

_BATCH_SIZE = 500


def _batch_upsert(sb, table: str, rows: list[dict], on_conflict: str) -> int:
    """Upsert em lotes de _BATCH_SIZE para evitar timeout."""
    saved = 0
    for i in range(0, len(rows), _BATCH_SIZE):
        chunk = rows[i : i + _BATCH_SIZE]
        try:
            sb.table(table).upsert(chunk, on_conflict=on_conflict).execute()
            saved += len(chunk)
        except Exception as exc:
            logger.warning("merchant_center batch_upsert %s chunk %d: %s", table, i, exc)
    return saved


def collect_products(client_id: str, merchant_id: str, access_token: str, snapshot_date: date) -> int:
    """Lista todos os produtos do feed e salva snapshot diário."""
    sb        = get_supabase()
    url       = f"{_MC_API}/{merchant_id}/products"
    page_token = None
    rows: list[dict] = []

    while True:
        params: dict = {"maxResults": _PAGE_SIZE}
        if page_token:
            params["pageToken"] = page_token

        try:
            resp = httpx.get(url, headers=_headers(access_token), params=params, timeout=20.0)
            if resp.status_code == 401:
                raise PermissionError("merchant_center 401 — refresh token may be invalid or scope missing")
            if resp.status_code == 403:
                body = resp.text[:300]
                raise PermissionError(f"merchant_center 403 — token lacks 'content' scope or no access to account {merchant_id}: {body}")
            resp.raise_for_status()
            data = resp.json()
        except PermissionError:
            raise
        except Exception as exc:
            logger.error("merchant_center collect_products: %s", exc)
            break

        for item in data.get("resources", []):
            price_info = item.get("price") or {}
            sale_info  = item.get("salePrice") or {}
            rows.append({
                "client_id":              client_id,
                "snapshot_date":          snapshot_date.isoformat(),
                "product_id":             item.get("id", ""),
                "offer_id":               item.get("offerId"),
                "channel":                item.get("channel", "online"),
                "language":               item.get("contentLanguage", "pt-BR"),
                "feed_label":             item.get("feedLabel"),
                "title":                  item.get("title"),
                "description":            (item.get("description") or "")[:500] or None,
                "link":                   item.get("link"),
                "image_link":             item.get("imageLink"),
                "brand":                  item.get("brand"),
                "gtin":                   item.get("gtin"),
                "mpn":                    item.get("mpn"),
                "product_type":           item.get("productType"),
                "google_product_category": item.get("googleProductCategory"),
                "price":                  float(price_info.get("value", 0)) if price_info.get("value") else None,
                "sale_price":             float(sale_info.get("value", 0)) if sale_info.get("value") else None,
                "currency":               price_info.get("currency"),
                "availability":           item.get("availability"),
                "custom_label_0":         item.get("customLabel0"),
                "custom_label_1":         item.get("customLabel1"),
                "custom_label_2":         item.get("customLabel2"),
                "custom_label_3":         item.get("customLabel3"),
                "custom_label_4":         item.get("customLabel4"),
                "shipping_country":       "BR",
                "raw_data":               None,
            })

        page_token = data.get("nextPageToken")
        if not page_token:
            break
        time.sleep(0.5)

    saved = _batch_upsert(sb, "merchant_products", rows, "client_id,product_id,snapshot_date")
    logger.info("merchant_center: %d products saved for %s", saved, client_id)
    return saved


# ── Collect statuses + issues ─────────────────────────────────────────────────

def collect_product_statuses(client_id: str, merchant_id: str, access_token: str, snapshot_date: date) -> tuple[int, int]:
    """Coleta status de aprovação e issues por produto."""
    sb         = get_supabase()
    url        = f"{_MC_API}/{merchant_id}/productstatuses"
    page_token = None
    status_rows: list[dict] = []
    issue_rows:  list[dict] = []
    iso = snapshot_date.isoformat()

    while True:
        params: dict = {"maxResults": _PAGE_SIZE}
        if page_token:
            params["pageToken"] = page_token
        try:
            resp = httpx.get(url, headers=_headers(access_token), params=params, timeout=20.0)
            if resp.status_code in (401, 403):
                raise PermissionError(f"merchant_center {resp.status_code} productstatuses: {resp.text[:200]}")
            resp.raise_for_status()
            data = resp.json()
        except PermissionError:
            raise
        except Exception as exc:
            logger.error("merchant_center collect_statuses: %s", exc)
            break

        for item in data.get("resources", []):
            product_id = item.get("productId", "")

            for dest in item.get("destinationStatuses", []):
                dest_name = dest.get("destination", "").lower().replace(" ", "_")
                status_rows.append({
                    "client_id":             client_id,
                    "product_id":            product_id,
                    "snapshot_date":         iso,
                    "destination":           dest_name,
                    "approval_status":       dest.get("status"),
                    "approved_countries":    dest.get("approvedCountries"),
                    "disapproved_countries": dest.get("disapprovedCountries"),
                    "pending_countries":     dest.get("pendingCountries"),
                    "servability":           dest.get("servability"),
                })

            for issue in item.get("itemLevelIssues", []):
                dest_name = (issue.get("destination") or "").lower().replace(" ", "_")
                issue_rows.append({
                    "client_id":          client_id,
                    "product_id":         product_id,
                    "snapshot_date":      iso,
                    "code":               issue.get("code", "unknown"),
                    "severity":           issue.get("resolution") and "warning" or (issue.get("servability") == "disapproved" and "error" or "warning"),
                    "description":        issue.get("description"),
                    "attribute_name":     issue.get("attributeName"),
                    "destination":        dest_name,
                    "documentation_url":  issue.get("documentation"),
                    "affected_countries": issue.get("applicableCountries"),
                    "resolution":         issue.get("resolution"),
                    "is_resolved":        False,
                })

        page_token = data.get("nextPageToken")
        if not page_token:
            break
        time.sleep(0.5)

    saved_status = _batch_upsert(sb, "merchant_product_statuses", status_rows, "client_id,product_id,destination,snapshot_date")
    saved_issues = _batch_upsert(sb, "merchant_product_issues",   issue_rows,  "client_id,product_id,code,destination,snapshot_date")
    logger.info("merchant_center: %d statuses, %d issues for %s", saved_status, saved_issues, client_id)
    return saved_status, saved_issues


# ── Collect performance via Reports API ───────────────────────────────────────

def collect_performance(client_id: str, merchant_id: str, access_token: str, report_date: date) -> int:
    """Coleta performance de produtos (impressões, cliques, conversões)."""
    sb  = get_supabase()
    url = f"{_MC_API}/{merchant_id}/reports/search"

    query = f"""
        SELECT
            segments.date,
            segments.offer_id,
            segments.program,
            metrics.impressions,
            metrics.clicks,
            metrics.ctr,
            metrics.conversions,
            metrics.conversion_value_micros
        FROM product_performance_view
        WHERE segments.date = '{report_date.isoformat()}'
        AND metrics.impressions > 0
    """
    try:
        resp = httpx.post(url, headers=_headers(access_token), json={"query": query}, timeout=30.0)
        resp.raise_for_status()
        results = resp.json().get("results", [])
    except Exception as exc:
        logger.warning("merchant_center collect_performance: %s", exc)
        return 0

    saved = 0
    for r in results:
        seg     = r.get("segments", {})
        metrics = r.get("metrics", {})
        program = seg.get("program", "").lower()
        dest    = "free_listings" if "free" in program else "shopping_ads"
        offer_id = seg.get("offerId", "")
        if not offer_id:
            continue
        # Reconstruct product_id from offer_id
        product_id = f"online:pt-BR:BR:{offer_id}"
        try:
            sb.table("merchant_product_performance").upsert({
                "client_id":       client_id,
                "product_id":      product_id,
                "date":            report_date.isoformat(),
                "destination":     dest,
                "impressions":     int(metrics.get("impressions") or 0),
                "clicks":          int(metrics.get("clicks") or 0),
                "ctr":             float(metrics.get("ctr") or 0),
                "click_share":     float(metrics.get("clickShare") or 0) if metrics.get("clickShare") else None,
                "conversions":     float(metrics.get("conversions") or 0),
                "conversion_value": float(metrics.get("conversionValueMicros") or 0) / 1_000_000,
            }, on_conflict="client_id,product_id,destination,date").execute()
            saved += 1
        except Exception as exc:
            logger.warning("merchant_center performance upsert: %s", exc)

    logger.info("merchant_center: %d performance rows for %s on %s", saved, client_id, report_date)
    return saved


# ── Collect price benchmarks ──────────────────────────────────────────────────

def collect_price_benchmarks(client_id: str, merchant_id: str, access_token: str, report_date: date) -> int:
    """Coleta price competitiveness report."""
    sb  = get_supabase()
    url = f"{_MC_API}/{merchant_id}/reports/search"

    query = f"""
        SELECT
            price_competitiveness_product_view.offer_id,
            price_competitiveness_product_view.price_micros,
            price_competitiveness_product_view.price_currency_code,
            price_competitiveness_product_view.benchmark_price_micros,
            price_competitiveness_product_view.country_code,
            price_competitiveness_product_view.report_date
        FROM price_competitiveness_product_view
        WHERE price_competitiveness_product_view.report_date = '{report_date.isoformat()}'
        AND price_competitiveness_product_view.country_code = 'BR'
    """
    try:
        resp = httpx.post(url, headers=_headers(access_token), json={"query": query}, timeout=30.0)
        resp.raise_for_status()
        results = resp.json().get("results", [])
    except Exception as exc:
        logger.warning("merchant_center collect_price_benchmarks: %s", exc)
        return 0

    saved = 0
    for r in results:
        row = r.get("priceCompetitivenessProductView", {})
        offer_id        = row.get("offerId", "")
        price_micros    = row.get("priceMicros")
        benchmark_micros = row.get("benchmarkPriceMicros")
        if not (offer_id and price_micros):
            continue

        product_price    = float(price_micros) / 1_000_000
        benchmark_price  = float(benchmark_micros) / 1_000_000 if benchmark_micros else None
        diff_pct = None
        status   = None
        if benchmark_price and benchmark_price > 0:
            diff_pct = round((product_price - benchmark_price) / benchmark_price * 100, 2)
            if diff_pct > 10:
                status = "above_market"
            elif diff_pct < -10:
                status = "below_market"
            else:
                status = "competitive"

        product_id = f"online:pt-BR:BR:{offer_id}"
        try:
            sb.table("merchant_price_benchmarks").upsert({
                "client_id":           client_id,
                "product_id":          product_id,
                "snapshot_date":       report_date.isoformat(),
                "product_price":       product_price,
                "benchmark_price":     benchmark_price,
                "price_difference_pct": diff_pct,
                "competitive_status":  status,
                "country":             row.get("countryCode", "BR"),
            }, on_conflict="client_id,product_id,country,snapshot_date").execute()
            saved += 1
        except Exception as exc:
            logger.warning("merchant_center price_benchmarks upsert: %s", exc)

    logger.info("merchant_center: %d price benchmarks for %s", saved, client_id)
    return saved


# ── Feed health snapshot ──────────────────────────────────────────────────────

def calculate_feed_health_snapshot(client_id: str, snapshot_date: date) -> int:
    """Agrega dados do dia e calcula Feed Health Score (0-100)."""
    sb  = get_supabase()
    iso = snapshot_date.isoformat()

    # Contagens de produtos
    products = (
        sb.table("merchant_products")
        .select("product_id,availability")
        .eq("client_id", client_id)
        .eq("snapshot_date", iso)
        .execute()
    ).data or []

    product_ids = [p["product_id"] for p in products]
    total       = len(products)
    out_of_stock = sum(1 for p in products if (p.get("availability") or "") in ("out_of_stock", "preorder"))

    # Status por produto
    statuses = (
        sb.table("merchant_product_statuses")
        .select("product_id,approval_status,destination")
        .eq("client_id", client_id)
        .eq("snapshot_date", iso)
        .execute()
    ).data or []

    approved     = len({s["product_id"] for s in statuses if s.get("approval_status") == "approved"})
    disapproved  = len({s["product_id"] for s in statuses if s.get("approval_status") == "disapproved"})
    pending      = len({s["product_id"] for s in statuses if s.get("approval_status") == "pending"})

    # Issues
    issues = (
        sb.table("merchant_product_issues")
        .select("product_id,code,severity")
        .eq("client_id", client_id)
        .eq("snapshot_date", iso)
        .eq("is_resolved", False)
        .execute()
    ).data or []

    errors         = [i for i in issues if i.get("severity") == "error"]
    warnings       = [i for i in issues if i.get("severity") == "warning"]
    prods_w_errors = len({i["product_id"] for i in errors})
    prods_w_warns  = len({i["product_id"] for i in warnings})

    code_counts: dict[str, int] = {}
    for i in issues:
        c = i.get("code") or "unknown"
        code_counts[c] = code_counts.get(c, 0) + 1
    top_codes = sorted(code_counts.items(), key=lambda x: -x[1])[:5]
    top_codes_json = [{"code": c, "count": n} for c, n in top_codes]

    # Price benchmarks
    benchmarks = (
        sb.table("merchant_price_benchmarks")
        .select("competitive_status")
        .eq("client_id", client_id)
        .eq("snapshot_date", iso)
        .execute()
    ).data or []

    above_market = sum(1 for b in benchmarks if b.get("competitive_status") == "above_market")
    below_market = sum(1 for b in benchmarks if b.get("competitive_status") == "below_market")

    # Feed Health Score (0-100)
    score = 0
    if total > 0:
        # 40% aprovação
        approval_rate = approved / total
        score += int(approval_rate * 40)
        # 25% ausência de errors
        error_rate = prods_w_errors / total
        score += int(max(0, 1 - error_rate * 2) * 25)
        # 20% preço competitivo
        if benchmarks:
            competitive = sum(1 for b in benchmarks if b.get("competitive_status") == "competitive")
            score += int((competitive / len(benchmarks)) * 20)
        else:
            score += 15   # sem benchmark = não penaliza
        # 15% estoque (itens em estoque)
        stock_rate = (total - out_of_stock) / total
        score += int(stock_rate * 15)

    score = min(100, max(0, score))

    try:
        sb.table("merchant_feed_health_snapshots").upsert({
            "client_id":                 client_id,
            "snapshot_date":             iso,
            "total_products":            total,
            "approved_products":         approved,
            "pending_products":          pending,
            "disapproved_products":      disapproved,
            "out_of_stock_products":     out_of_stock,
            "products_with_warnings":    prods_w_warns,
            "products_with_errors":      prods_w_errors,
            "total_warnings":            len(warnings),
            "total_errors":              len(errors),
            "unique_issue_codes":        len(code_counts),
            "top_issue_codes":           top_codes_json,
            "products_above_market_price": above_market,
            "products_below_market_price": below_market,
            "feed_health_score":         score,
        }, on_conflict="client_id,snapshot_date").execute()
    except Exception as exc:
        logger.error("merchant_center feed_health upsert: %s", exc)

    logger.info("merchant_center: feed health score=%d for %s on %s", score, client_id, iso)
    return score


def detect_resolved_issues(client_id: str, snapshot_date: date) -> None:
    """Marca issues do dia anterior que não apareceram hoje como resolvidas."""
    sb           = get_supabase()
    today_iso    = snapshot_date.isoformat()
    previous_iso = (snapshot_date - timedelta(days=1)).isoformat()

    yesterday = (
        sb.table("merchant_product_issues")
        .select("id,product_id,code,destination")
        .eq("client_id", client_id)
        .eq("snapshot_date", previous_iso)
        .eq("is_resolved", False)
        .execute()
    ).data or []

    today_keys = {
        (r["product_id"], r["code"], r.get("destination") or "")
        for r in (
            sb.table("merchant_product_issues")
            .select("product_id,code,destination")
            .eq("client_id", client_id)
            .eq("snapshot_date", today_iso)
            .execute()
        ).data or []
    }

    resolved_now = datetime.now(timezone.utc).isoformat()
    for row in yesterday:
        key = (row["product_id"], row["code"], row.get("destination") or "")
        if key not in today_keys:
            try:
                sb.table("merchant_product_issues").update({
                    "is_resolved": True,
                    "resolved_at": resolved_now,
                }).eq("id", row["id"]).execute()
            except Exception as exc:
                logger.debug("merchant_center resolve issue %s: %s", row["id"], exc)


# ── Full daily sync ───────────────────────────────────────────────────────────

def sync_client(client_id: str) -> dict:
    """Sync completo de um cliente. Retorna resumo."""
    sb = get_supabase()
    row = (
        sb.table("clients")
        .select("id,pixel_id,name,merchant_center_id,merchant_center_refresh_token")
        .eq("id", client_id)
        .limit(1)
        .execute()
    ).data
    if not row:
        return {"error": "client not found"}
    c = crypto.decrypt_client_secrets(row[0])

    merchant_id   = c.get("merchant_center_id")
    refresh_token = c.get("merchant_center_refresh_token")
    pixel         = c.get("pixel_id") or client_id

    if not (merchant_id and refresh_token):
        logger.debug("merchant_center: %s not configured", pixel)
        return {"skipped": True, "reason": "not configured"}

    if not (settings.GOOGLE_ADS_OAUTH_CLIENT_ID and settings.GOOGLE_ADS_OAUTH_CLIENT_SECRET):
        return {"error": "missing GOOGLE_ADS_OAUTH_CLIENT_ID/SECRET in env"}

    access_token = _get_access_token(refresh_token)
    if not access_token:
        logger.error("merchant_center: could not get access token for %s", pixel)
        return {"error": "token_refresh_failed"}

    # Collect yesterday (data completa do dia fechado)
    yesterday = date.today() - timedelta(days=1)

    try:
        products       = collect_products(client_id, merchant_id, access_token, yesterday)
        statuses, issues = collect_product_statuses(client_id, merchant_id, access_token, yesterday)
        performance    = collect_performance(client_id, merchant_id, access_token, yesterday)
        benchmarks     = collect_price_benchmarks(client_id, merchant_id, access_token, yesterday)
        detect_resolved_issues(client_id, yesterday)
        score          = calculate_feed_health_snapshot(client_id, yesterday)

        sb.table("clients").update({
            "merchant_center_last_sync_at": datetime.now(timezone.utc).isoformat(),
        }).eq("id", client_id).execute()

    except PermissionError as exc:
        logger.error("merchant_center: 401 for %s — %s", pixel, exc)
        return {"error": "unauthorized", "detail": str(exc)}
    except Exception as exc:
        logger.error("merchant_center: sync failed for %s: %s", pixel, exc)
        return {"error": str(exc)}

    return {
        "client_id": client_id,
        "date":      yesterday.isoformat(),
        "products":  products,
        "statuses":  statuses,
        "issues":    issues,
        "performance": performance,
        "benchmarks": benchmarks,
        "feed_health_score": score,
    }


def run_daily_sync_all_clients() -> None:
    """Cron entry-point: sync de todos os clientes com Merchant Center configurado."""
    sb = get_supabase()
    clients = (
        sb.table("clients")
        .select("id,pixel_id")
        .not_.is_("merchant_center_id", "null")
        .not_.is_("merchant_center_refresh_token", "null")
        .eq("is_active", True)
        .execute()
    ).data or []

    logger.info("merchant_center: starting daily sync for %d clients", len(clients))
    for c in clients:
        try:
            result = sync_client(c["id"])
            logger.info("merchant_center: %s → %s", c.get("pixel_id"), result)
        except Exception as exc:
            logger.error("merchant_center: sync failed for %s: %s", c.get("pixel_id"), exc)
        time.sleep(5)   # espaça clientes para não bater rate limit global


# ── Dashboard queries ─────────────────────────────────────────────────────────

def get_feed_health(client_id: str, days: int = 30) -> list[dict]:
    """Histórico do Feed Health Score para o gráfico."""
    sb     = get_supabase()
    since  = (date.today() - timedelta(days=days)).isoformat()
    return (
        sb.table("merchant_feed_health_snapshots")
        .select("snapshot_date,feed_health_score,total_products,approved_products,disapproved_products,total_errors,total_warnings,products_above_market_price,top_issue_codes")
        .eq("client_id", client_id)
        .gte("snapshot_date", since)
        .order("snapshot_date")
        .execute()
    ).data or []


def get_latest_snapshot(client_id: str) -> Optional[dict]:
    rows = (
        get_supabase()
        .table("merchant_feed_health_snapshots")
        .select("*")
        .eq("client_id", client_id)
        .order("snapshot_date", desc=True)
        .limit(1)
        .execute()
    ).data
    return rows[0] if rows else None


def get_products(
    client_id: str,
    snapshot_date: str,
    status_filter: Optional[str] = None,
    availability_filter: Optional[str] = None,
    page: int = 1,
    per_page: int = 50,
) -> dict:
    sb  = get_supabase()
    offset = (page - 1) * per_page

    q = (
        sb.table("merchant_products")
        .select("product_id,title,brand,price,availability,image_link,google_product_category")
        .eq("client_id", client_id)
        .eq("snapshot_date", snapshot_date)
    )
    if availability_filter:
        q = q.eq("availability", availability_filter)

    products = q.offset(offset).limit(per_page).execute().data or []

    if status_filter and products:
        product_ids = [p["product_id"] for p in products]
        statuses = (
            sb.table("merchant_product_statuses")
            .select("product_id,approval_status")
            .eq("client_id", client_id)
            .eq("snapshot_date", snapshot_date)
            .in_("product_id", product_ids)
            .execute()
        ).data or []
        status_map = {s["product_id"]: s["approval_status"] for s in statuses}
        if status_filter != "all":
            products = [p for p in products if status_map.get(p["product_id"]) == status_filter]
        for p in products:
            p["approval_status"] = status_map.get(p["product_id"])

    return {"products": products, "page": page, "per_page": per_page}


def get_top_issues(client_id: str, snapshot_date: str, limit: int = 20) -> list[dict]:
    sb = get_supabase()
    issues = (
        sb.table("merchant_product_issues")
        .select("code,severity,description,product_id,documentation_url,resolution")
        .eq("client_id", client_id)
        .eq("snapshot_date", snapshot_date)
        .eq("is_resolved", False)
        .execute()
    ).data or []

    grouped: dict[str, dict] = {}
    for i in issues:
        k = (i["code"], i.get("severity", "warning"))
        if k not in grouped:
            grouped[k] = {
                "code":             i["code"],
                "severity":         i.get("severity"),
                "description":      i.get("description"),
                "documentation_url": i.get("documentation_url"),
                "resolution":       i.get("resolution"),
                "affected_products": [],
            }
        grouped[k]["affected_products"].append(i["product_id"])

    result = []
    for v in sorted(grouped.values(), key=lambda x: (x["severity"] != "error", -len(x["affected_products"]))):
        result.append({**v, "count": len(v["affected_products"])})
    return result[:limit]


def get_price_summary(client_id: str, snapshot_date: str) -> dict:
    sb = get_supabase()
    rows = (
        sb.table("merchant_price_benchmarks")
        .select("product_id,product_price,benchmark_price,price_difference_pct,competitive_status")
        .eq("client_id", client_id)
        .eq("snapshot_date", snapshot_date)
        .execute()
    ).data or []

    total        = len(rows)
    competitive  = sum(1 for r in rows if r.get("competitive_status") == "competitive")
    above_market = sum(1 for r in rows if r.get("competitive_status") == "above_market")
    below_market = sum(1 for r in rows if r.get("competitive_status") == "below_market")
    diffs        = [r["price_difference_pct"] for r in rows if r.get("price_difference_pct") is not None]
    avg_diff     = round(sum(diffs) / len(diffs), 2) if diffs else None

    top_above = sorted(
        [r for r in rows if r.get("competitive_status") == "above_market"],
        key=lambda x: -(x.get("price_difference_pct") or 0)
    )[:10]

    return {
        "total_with_benchmark": total,
        "competitive": competitive,
        "above_market": above_market,
        "below_market": below_market,
        "competitive_pct": round(competitive / total * 100, 1) if total else None,
        "avg_price_difference_pct": avg_diff,
        "top_above_market": top_above,
    }
