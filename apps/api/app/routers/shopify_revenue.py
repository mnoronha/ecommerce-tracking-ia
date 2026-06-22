"""
Shopify Revenue — faturamento real do Shopify via tabela orders.

Lê da tabela `orders` (sincronizada a cada hora via shopify_sync) para
fornecer métricas completas de receita sem depender do pixel de tracking.
Seguro para clientes que usam tracking nativo do Shopify.
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException

from ..database import get_supabase

logger = logging.getLogger(__name__)
router = APIRouter()


def _classify_channel(source: Optional[str], medium: Optional[str]) -> str:
    s = (source or "").lower()
    m = (medium or "").lower()
    if not s:
        return "Direto"
    if s in ("facebook", "instagram", "meta", "fb") or "facebook" in s or "instagram" in s:
        return "Meta Ads"
    if s == "google" and m == "organic":
        return "Google Orgânico"
    if s in ("google", "cpc", "adwords", "paid_search", "ppc"):
        return "Google Ads"
    if "tiktok" in s or s == "tt":
        return "TikTok Ads"
    if s in ("klaviyo", "email", "newsletter", "crm") or m in ("email", "newsletter", "crm"):
        return "Email / CRM"
    if m == "organic":
        return "Orgânico"
    return "Outros"


def _delta(curr: float | None, prev: float | None) -> float | None:
    if curr is None or prev is None or prev == 0:
        return None
    return round((curr - prev) / prev * 100, 1)


def _aggregate(orders: list[dict]) -> dict:
    paid    = [o for o in orders if (o.get("financial_status") or "").lower() == "paid" and float(o.get("total_price") or 0) > 0]
    refund  = [o for o in orders if (o.get("financial_status") or "").lower() in ("refunded", "partially_refunded")]
    pending = [o for o in orders if (o.get("financial_status") or "").lower() in ("pending", "authorized")]
    voided  = [o for o in orders if (o.get("financial_status") or "").lower() in ("voided", "cancelled")]
    gmv     = sum(float(o.get("total_price") or 0) for o in paid)
    ref_val = sum(float(o.get("total_price") or 0) for o in refund)
    return {
        "gmv":                round(gmv, 2),
        "net_revenue":        round(gmv - ref_val, 2),
        "refunds":            round(ref_val, 2),
        "paid_orders":        len(paid),
        "pending_orders":     len(pending),
        "refund_orders":      len(refund),
        "voided_orders":      len(voided),
        "avg_ticket":         round(gmv / len(paid), 2) if paid else 0,
        "new_customers":      sum(1 for o in paid if o.get("is_first_purchase") is True),
        "returning_customers": sum(1 for o in paid if o.get("is_first_purchase") is False),
    }


@router.get(
    "/shopify/{pixel_id}/revenue",
    summary="Faturamento real do Shopify — KPIs, série diária, canais, produtos",
    tags=["shopify"],
)
async def shopify_revenue(
    pixel_id: str,
    start: Optional[str] = None,
    end:   Optional[str] = None,
    days:  int = 30,
):
    """
    Retorna métricas completas de faturamento baseadas nos pedidos do Shopify
    (sincronizados a cada hora via shopify_sync). Não depende do pixel de tracking.

    Inclui:
    - GMV, receita líquida, reembolsos, pedidos pagos/pendentes
    - Ticket médio, novos vs recorrentes
    - Série diária de receita
    - Breakdown por canal de aquisição (UTM)
    - Top produtos (de order_items)
    - Distribuição geográfica (por país)
    - Status financeiro dos pedidos
    - Delta vs período anterior
    """
    sb = get_supabase()

    row = (
        sb.table("clients")
        .select("id, name")
        .eq("pixel_id", pixel_id)
        .eq("is_active", True)
        .limit(1)
        .execute()
    )
    if not (row and row.data):
        raise HTTPException(status_code=404, detail="Client not found")
    client_id   = row.data[0]["id"]
    client_name = row.data[0].get("name", "")

    if start and end:
        try:
            d_start = datetime.fromisoformat(start).date()
            d_end   = datetime.fromisoformat(end).date()
            days    = max(1, (d_end - d_start).days + 1)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid date format — use YYYY-MM-DD")
    else:
        today   = datetime.now(timezone.utc).date()
        d_end   = today
        d_start = today - timedelta(days=days - 1)

    d_prev_end   = d_start - timedelta(days=1)
    d_prev_start = d_prev_end - timedelta(days=days - 1)

    def _fetch(d_from, d_to) -> list[dict]:
        return (
            sb.table("orders")
            .select(
                "id, total_price, financial_status, created_at, "
                "utm_source, utm_medium, utm_campaign, "
                "is_first_purchase, platform_source, shipping_country"
            )
            .eq("client_id", client_id)
            .gte("created_at", f"{d_from}T00:00:00+00:00")
            .lte("created_at", f"{d_to}T23:59:59+00:00")
            .limit(5000)
            .execute()
        ).data or []

    curr_all = _fetch(d_start, d_end)
    prev_all = _fetch(d_prev_start, d_prev_end)

    curr_agg = _aggregate(curr_all)
    prev_agg = _aggregate(prev_all)

    deltas = {
        "gmv":         _delta(curr_agg["gmv"],         prev_agg["gmv"]),
        "paid_orders": _delta(curr_agg["paid_orders"], prev_agg["paid_orders"]),
        "avg_ticket":  _delta(curr_agg["avg_ticket"],  prev_agg["avg_ticket"]),
        "net_revenue": _delta(curr_agg["net_revenue"], prev_agg["net_revenue"]),
    }

    # ── Daily series (paid orders only) ───────────────────────────────────────
    daily_map: dict = {}
    paid_curr = [
        o for o in curr_all
        if (o.get("financial_status") or "").lower() == "paid"
        and float(o.get("total_price") or 0) > 0
    ]
    for o in paid_curr:
        d = o["created_at"][:10]
        if d not in daily_map:
            daily_map[d] = {"date": d, "revenue": 0.0, "orders": 0}
        daily_map[d]["revenue"] += float(o.get("total_price") or 0)
        daily_map[d]["orders"]  += 1

    daily = []
    for i in range(days):
        from datetime import date as _date
        d   = str(d_start + timedelta(days=i))
        row = daily_map.get(d, {"date": d, "revenue": 0.0, "orders": 0})
        row["revenue"] = round(row["revenue"], 2)
        daily.append(row)

    # ── Channel breakdown (paid orders) ───────────────────────────────────────
    channel_map: dict = {}
    for o in paid_curr:
        ch = _classify_channel(o.get("utm_source"), o.get("utm_medium"))
        if ch not in channel_map:
            channel_map[ch] = {"channel": ch, "revenue": 0.0, "orders": 0}
        channel_map[ch]["revenue"] += float(o.get("total_price") or 0)
        channel_map[ch]["orders"]  += 1

    total_rev = curr_agg["gmv"] or 1
    channels = sorted(
        [
            {
                "channel": k,
                "revenue": round(v["revenue"], 2),
                "orders":  v["orders"],
                "pct":     round(v["revenue"] / total_rev * 100, 1),
            }
            for k, v in channel_map.items()
        ],
        key=lambda x: x["revenue"],
        reverse=True,
    )

    # ── Top products (from order_items) ────────────────────────────────────────
    paid_ids = [o["id"] for o in paid_curr]
    products_map: dict = {}
    if paid_ids:
        for i in range(0, len(paid_ids), 500):
            chunk = paid_ids[i : i + 500]
            try:
                items = (
                    sb.table("order_items")
                    .select("name, sku, quantity, line_total")
                    .in_("order_id", chunk)
                    .execute()
                ).data or []
                for it in items:
                    name = (it.get("name") or "Produto desconhecido").strip()
                    if name not in products_map:
                        products_map[name] = {
                            "name":    name,
                            "sku":     it.get("sku"),
                            "units":   0,
                            "revenue": 0.0,
                        }
                    products_map[name]["units"]   += int(it.get("quantity") or 1)
                    products_map[name]["revenue"] += float(it.get("line_total") or 0)
            except Exception as exc:
                logger.warning("shopify_revenue: order_items query failed: %s", exc)

    top_products = sorted(
        [
            {
                "name":    v["name"],
                "sku":     v["sku"],
                "units":   v["units"],
                "revenue": round(v["revenue"], 2),
            }
            for v in products_map.values()
        ],
        key=lambda x: x["revenue"],
        reverse=True,
    )[:25]

    # ── Geographic breakdown (by country) ─────────────────────────────────────
    geo_map: dict = {}
    for o in paid_curr:
        country = (o.get("shipping_country") or "Desconhecido").strip()
        if country not in geo_map:
            geo_map[country] = {"country": country, "orders": 0, "revenue": 0.0}
        geo_map[country]["orders"]  += 1
        geo_map[country]["revenue"] += float(o.get("total_price") or 0)

    geo_breakdown = sorted(
        [
            {
                "country": k,
                "orders":  v["orders"],
                "revenue": round(v["revenue"], 2),
                "pct":     round(v["orders"] / max(len(paid_curr), 1) * 100, 1),
            }
            for k, v in geo_map.items()
        ],
        key=lambda x: x["revenue"],
        reverse=True,
    )[:15]

    # ── UTM campaign breakdown (paid orders) ──────────────────────────────────
    utm_map: dict = {}
    for o in paid_curr:
        src = o.get("utm_source") or "direto"
        med = o.get("utm_medium") or "—"
        cam = o.get("utm_campaign") or "—"
        key = f"{src}|||{med}|||{cam}"
        if key not in utm_map:
            utm_map[key] = {"source": src, "medium": med, "campaign": cam, "orders": 0, "revenue": 0.0}
        utm_map[key]["orders"]  += 1
        utm_map[key]["revenue"] += float(o.get("total_price") or 0)

    utm_breakdown = sorted(
        [
            {
                "source":   v["source"],
                "medium":   v["medium"],
                "campaign": v["campaign"],
                "orders":   v["orders"],
                "revenue":  round(v["revenue"], 2),
                "pct":      round(v["revenue"] / total_rev * 100, 1),
            }
            for v in utm_map.values()
        ],
        key=lambda x: x["revenue"],
        reverse=True,
    )[:25]

    return {
        "period": {
            "start": str(d_start),
            "end":   str(d_end),
            "days":  days,
        },
        "client_name":     client_name,
        "summary":         curr_agg,
        "prev":            prev_agg,
        "deltas":          deltas,
        "daily":           daily,
        "channels":        channels,
        "top_products":    top_products,
        "geo_breakdown":   geo_breakdown,
        "utm_breakdown":   utm_breakdown,
        "status_dist": {
            "paid":     curr_agg["paid_orders"],
            "pending":  curr_agg["pending_orders"],
            "refunded": curr_agg["refund_orders"],
            "voided":   curr_agg["voided_orders"],
        },
        "total_all_orders": len(curr_all),
    }
