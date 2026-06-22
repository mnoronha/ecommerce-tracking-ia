"""
Meta Ads endpoints.

GET /meta-ads/{pixel_id}/roas?days=30    — ROAS por campanha (Meta API + pedidos)
GET /meta-ads/{pixel_id}/overview?days=30 — Dashboard completo (meta_ad_attributions)
"""

import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException

from ..database import get_supabase
from ..services import crypto, meta_ads as meta_ads_svc

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get(
    "/meta-ads/{pixel_id}/roas",
    summary="ROAS por campanha (Meta Ads spend + pedidos)",
    tags=["meta_ads"],
)
async def get_roas(pixel_id: str, days: int = 30):
    """
    Retorna por campanha:
      - spend  (Meta Ads API)
      - revenue, orders  (pedidos com utm_campaign)
      - roas = revenue / spend
      - cpa   = spend / orders
      - impressions, clicks (Meta Ads API)

    Se meta_ad_account_id não estiver configurado no cliente, retorna os dados
    de receita apenas (sem gasto/ROAS), sinalizando has_ads_credentials=false.
    """
    sb = get_supabase()

    # ── Buscar credenciais do cliente ─────────────────────────────────────────
    creds_result = (
        sb.table("clients")
        .select("id, meta_pixel_id, meta_access_token, meta_ad_account_id, monthly_ad_spend, monthly_revenue, monthly_roas")
        .eq("pixel_id", pixel_id)
        .eq("is_active", True)
        .limit(1)
        .execute()
    )
    if not (creds_result and creds_result.data):
        raise HTTPException(status_code=404, detail="Client not found or inactive")

    c            = crypto.decrypt_client_secrets(creds_result.data[0])
    client_uuid  = c["id"]
    has_ads_creds = bool(c.get("meta_ad_account_id") and c.get("meta_access_token"))

    # ── Calcular intervalo de datas ───────────────────────────────────────────
    today = datetime.utcnow().date()
    if days <= 1:
        # "Ontem" — só o dia anterior completo
        d_since = today - timedelta(days=1)
        d_until = d_since
    else:
        # Últimos N dias de calendário (incluindo hoje)
        d_since = today - timedelta(days=days - 1)
        d_until = today

    start_date = f"{d_since}T00:00:00"
    end_date   = f"{d_until}T23:59:59.999999"

    # ── Pedidos no período agrupados por utm_campaign ─────────────────────────
    orders_result = (
        sb.table("orders")
        .select("utm_campaign, utm_source, utm_medium, total_price, gross_profit, financial_status")
        .eq("client_id", client_uuid)
        .gte("created_at", start_date)
        .lte("created_at", end_date)
        .execute()
    )
    orders = orders_result.data or []

    campaign_map: dict[str, dict] = {}
    for o in orders:
        key = o.get("utm_campaign") or "(sem campanha)"
        if key not in campaign_map:
            campaign_map[key] = {
                "revenue":      0.0,
                "gross_profit": 0.0,
                "orders":       0,
                "utm_source":   o.get("utm_source"),
                "utm_medium":   o.get("utm_medium"),
                "has_cogs":     False,
            }
        campaign_map[key]["revenue"] += float(o.get("total_price") or 0)
        campaign_map[key]["orders"]  += 1
        if o.get("gross_profit") is not None:
            campaign_map[key]["gross_profit"] += float(o["gross_profit"])
            campaign_map[key]["has_cogs"] = True

    # ── Buscar gasto das campanhas no Meta Ads API ────────────────────────────
    ads_rows: list[dict] = []
    if has_ads_creds:
        ads_rows = meta_ads_svc.fetch_campaign_insights(
            account_id=c["meta_ad_account_id"],
            access_token=c["meta_access_token"],
            since=str(d_since),
            until=str(d_until),
        )

    # Dois mapas para matching tolerante: por nome E por campaign_id numérico.
    # Muitos pedidos chegam com utm_campaign = ID numérico do Meta (ex: "120210118442410224")
    # em vez do nome, então o matching por nome sozinho falha e o ROAS fica zerado.
    spend_map_name: dict[str, dict] = {r["campaign_name"].lower(): r for r in ads_rows}
    spend_map_id:   dict[str, dict] = {r["campaign_id"]: r for r in ads_rows if r.get("campaign_id")}

    def _lookup_ads(utm_key: str) -> dict:
        """Tenta ID primeiro (quando utm_campaign é numérico), depois por nome."""
        if utm_key.isdigit():
            return spend_map_id.get(utm_key) or spend_map_name.get(utm_key.lower(), {})
        return spend_map_name.get(utm_key.lower()) or spend_map_id.get(utm_key, {})

    # ── Merge ─────────────────────────────────────────────────────────────────
    # União de chaves: utm_campaign da nossa base + campaign_name da API Meta
    all_names = set(campaign_map.keys()) | {r["campaign_name"] for r in ads_rows}
    rows = []
    any_cogs = any(v.get("has_cogs") for v in campaign_map.values())
    for name in all_names:
        rev_data       = campaign_map.get(name, {"revenue": 0.0, "gross_profit": 0.0, "orders": 0, "has_cogs": False})
        ads            = _lookup_ads(name)
        spend          = ads.get("spend", 0.0)
        revenue        = rev_data["revenue"]
        gross_profit   = rev_data.get("gross_profit") or 0.0
        has_cogs       = rev_data.get("has_cogs", False)
        n_orders       = rev_data["orders"]
        meta_purchases = ads.get("meta_purchases") or 0
        meta_revenue   = ads.get("meta_revenue") or 0
        meta_cpa       = ads.get("meta_cpa")

        cpa = round(spend / n_orders, 2) if n_orders > 0 and spend > 0 else None

        # diff_pct: positive means Meta is under-reporting CPA (truth is higher)
        cpa_diff_pct = None
        if cpa and meta_cpa and meta_cpa > 0:
            cpa_diff_pct = round((cpa - meta_cpa) / meta_cpa * 100, 1)

        # Meta over-reports purchases when its attribution window catches
        # browser-side hits we never recorded as orders. Negative means we
        # found more orders than Meta did (rare; usually indicates a tag bug).
        purchases_diff = n_orders - meta_purchases

        # Margin ROAS: gross_profit / spend — the metric DTC cares about most
        margin_roas = round(gross_profit / spend, 2) if spend > 0 and has_cogs else None
        margin_pct  = round(gross_profit / revenue * 100, 1) if revenue > 0 and has_cogs else None

        rows.append({
            "campaign_name":  name,
            "utm_source":     rev_data.get("utm_source") or ads.get("utm_source"),
            "spend":          round(spend, 2),
            "revenue":        round(revenue, 2),
            "gross_profit":   round(gross_profit, 2) if has_cogs else None,
            "margin_pct":     margin_pct,
            "margin_roas":    margin_roas,
            "orders":         n_orders,
            "roas":           round(revenue / spend, 2)        if spend > 0    else None,
            "cpa":            cpa,
            "impressions":    ads.get("impressions", 0),
            "clicks":         ads.get("clicks", 0),
            "ctr":            round(ads["clicks"] / ads["impressions"] * 100, 2)
                              if ads.get("impressions", 0) > 0 else None,
            "cpm":            ads.get("cpm"),
            # Meta-reported numbers for side-by-side comparison
            "meta_purchases": meta_purchases,
            "meta_revenue":   round(meta_revenue, 2),
            "meta_cpa":       meta_cpa,
            "meta_roas":      round(meta_revenue / spend, 2) if spend > 0 and meta_revenue > 0 else None,
            "cpa_diff_pct":   cpa_diff_pct,
            "purchases_diff": purchases_diff,
        })

    rows.sort(key=lambda r: r["revenue"], reverse=True)

    total_spend          = round(sum(r["spend"] for r in rows), 2)
    total_revenue        = round(sum(r["revenue"] for r in rows), 2)
    total_orders         = sum(r["orders"] for r in rows)
    total_meta_purchases = sum(r["meta_purchases"] for r in rows)
    total_meta_revenue   = round(sum(r["meta_revenue"] for r in rows), 2)
    total_gross_profit   = round(sum(r["gross_profit"] or 0 for r in rows), 2) if any_cogs else None

    real_cpa = round(total_spend / total_orders, 2) if total_orders > 0 and total_spend > 0 else None
    meta_cpa_total = round(total_spend / total_meta_purchases, 2) \
                     if total_meta_purchases > 0 and total_spend > 0 else None
    cpa_diff_pct_total = None
    if real_cpa and meta_cpa_total and meta_cpa_total > 0:
        cpa_diff_pct_total = round((real_cpa - meta_cpa_total) / meta_cpa_total * 100, 1)

    total_margin_roas = round(total_gross_profit / total_spend, 2) \
                        if any_cogs and total_gross_profit and total_spend > 0 else None
    total_margin_pct  = round(total_gross_profit / total_revenue * 100, 1) \
                        if any_cogs and total_gross_profit and total_revenue > 0 else None

    # Paid-only aggregate — drops campaigns with zero spend (organic / POS /
    # direct / email rows that would otherwise inflate ROAS to fantasy levels).
    # This is the number that actually answers "did my ad money work?".
    paid_rows         = [r for r in rows if r["spend"] > 0]
    paid_revenue      = round(sum(r["revenue"] for r in paid_rows), 2)
    paid_orders       = sum(r["orders"] for r in paid_rows)
    paid_gross_profit = round(sum(r["gross_profit"] or 0 for r in paid_rows), 2) if any_cogs else None
    paid_roas         = round(paid_revenue / total_spend, 2)        if total_spend > 0 else None
    paid_cpa          = round(total_spend / paid_orders, 2)         if paid_orders > 0 and total_spend > 0 else None
    paid_margin_roas  = round(paid_gross_profit / total_spend, 2)   if any_cogs and paid_gross_profit and total_spend > 0 else None

    return {
        "has_ads_credentials": has_ads_creds,
        "has_cogs":   any_cogs,
        "days":       days,
        "campaigns":  rows,
        "totals": {
            "spend":              total_spend,
            "revenue":            total_revenue,
            "gross_profit":       total_gross_profit,
            "margin_pct":         total_margin_pct,
            "margin_roas":        total_margin_roas,
            "orders":             total_orders,
            "roas":               round(total_revenue / total_spend, 2) if total_spend > 0 else None,
            "total_cpa":          real_cpa,
            "meta_purchases":     total_meta_purchases,
            "meta_revenue":       total_meta_revenue,
            "meta_cpa":           meta_cpa_total,
            "meta_roas":          round(total_meta_revenue / total_spend, 2)
                                  if total_spend > 0 and total_meta_revenue > 0 else None,
            "cpa_diff_pct":       cpa_diff_pct_total,
        },
        # Use this block for the headline "did my ad spend pay off?" number.
        # `totals.roas` mixes paid + organic and overstates performance when
        # there's substantial direct / POS / orgânico revenue.
        "paid_only": {
            "revenue":      paid_revenue,
            "orders":       paid_orders,
            "spend":        total_spend,
            "roas":         paid_roas,
            "cpa":          paid_cpa,
            "gross_profit": paid_gross_profit,
            "margin_roas":  paid_margin_roas,
            "campaigns":    len(paid_rows),
        },
    }


@router.get(
    "/meta-ads/{pixel_id}/breakdowns",
    summary="Breakdown por idade/gênero/dispositivo/plataforma das campanhas Meta Ads",
    tags=["meta_ads"],
)
async def get_breakdowns(
    pixel_id:  str,
    breakdown: str  = "age",
    days:      int  = 30,
    start:     str | None = None,
    end:       str | None = None,
):
    """
    Segmenta campanhas por breakdown demográfico ou de dispositivo.
    breakdown: age | gender | device | placement
    """
    if breakdown not in ("age", "gender", "device", "placement"):
        raise HTTPException(status_code=400, detail="breakdown must be age, gender, device or placement")

    sb    = get_supabase()
    creds = (
        sb.table("clients")
        .select("id, meta_ad_account_id, meta_access_token")
        .eq("pixel_id", pixel_id)
        .eq("is_active", True)
        .limit(1)
        .execute()
    )
    if not (creds and creds.data):
        raise HTTPException(status_code=404, detail="Client not found or inactive")
    c = crypto.decrypt_client_secrets(creds.data[0])

    if not c.get("meta_ad_account_id") or not c.get("meta_access_token"):
        raise HTTPException(status_code=400, detail="Meta Ads credentials not configured")

    if start and end:
        d_since, d_until = start, end
    else:
        today   = datetime.now(timezone.utc).date()
        d_until = str(today)
        d_since = str(today - timedelta(days=days - 1))

    data = meta_ads_svc.fetch_campaign_breakdowns(
        account_id=c["meta_ad_account_id"],
        access_token=c["meta_access_token"],
        since=d_since,
        until=d_until,
        breakdown=breakdown,
    )

    return {
        "breakdown": breakdown,
        "start":     d_since,
        "end":       d_until,
        "data":      data,
    }


@router.get(
    "/meta-ads/{pixel_id}/overview",
    summary="Dashboard Meta Ads completo — usa meta_ad_attributions (sync diário)",
    tags=["meta_ads"],
)
async def get_overview(
    pixel_id: str,
    days: int = 30,
    start: str | None = None,
    end:   str | None = None,
):
    """
    Retorna KPIs, séries diárias, breakdown campanha→adset→anúncio e funil.
    Fonte principal: meta_ad_attributions (sem chamada live à Meta API).
    Período anterior (mesma duração) incluído para calcular % Δ.
    start/end (YYYY-MM-DD) sobrepõem `days` quando informados.
    """
    sb = get_supabase()

    creds = (
        sb.table("clients")
        .select("id, meta_pixel_id, meta_ad_account_id, meta_access_token")
        .eq("pixel_id", pixel_id)
        .eq("is_active", True)
        .limit(1)
        .execute()
    )
    if not (creds and creds.data):
        raise HTTPException(status_code=404, detail="Client not found or inactive")
    c         = crypto.decrypt_client_secrets(creds.data[0])
    client_id = c["id"]

    if start and end:
        d_start = datetime.fromisoformat(start).date()
        d_end   = datetime.fromisoformat(end).date()
        days    = max(1, (d_end - d_start).days + 1)
    else:
        today   = datetime.now(timezone.utc).date()
        d_end   = today
        d_start = today - timedelta(days=days - 1)
    d_prev_end   = d_start - timedelta(days=1)
    d_prev_start = d_prev_end - timedelta(days=days - 1)

    def _agg_rows(rows: list) -> dict:
        spend  = sum(float(r.get("spend") or 0)        for r in rows)
        impr   = sum(int(r.get("impressions") or 0)    for r in rows)
        clicks = sum(int(r.get("clicks") or 0)         for r in rows)
        purch  = sum(int(r.get("purchases") or 0)      for r in rows)
        rev    = sum(float(r.get("purchase_value") or 0) for r in rows)
        ctr    = round(clicks / impr * 100, 2)  if impr  > 0 else None
        cpm    = round(spend  / impr * 1000, 2) if impr  > 0 else None
        cpc    = round(spend  / clicks, 2)      if clicks > 0 else None
        roas   = round(rev   / spend, 2)        if spend > 0 else None
        cpa    = round(spend / purch, 2)        if purch > 0 else None
        ticket = round(rev / purch, 2)          if purch > 0 else None
        return {
            "spend": round(spend, 2), "impressions": impr, "clicks": clicks,
            "purchases": purch, "revenue": round(rev, 2),
            "ctr": ctr, "cpm": cpm, "cpc": cpc,
            "roas": roas, "cpa": cpa, "avg_ticket": ticket,
        }

    def _delta(curr: float | None, prev: float | None) -> float | None:
        if curr is None or prev is None or prev == 0:
            return None
        return round((curr - prev) / prev * 100, 1)

    # ── Fetch attribution rows ─────────────────────────────────────────────
    def _fetch(d_from, d_to):
        return (
            sb.table("meta_ad_attributions")
            .select("campaign_id,campaign_name,adset_id,adset_name,ad_id,ad_name,"
                    "date,spend,impressions,clicks,purchases,purchase_value")
            .eq("client_id", client_id)
            .gte("date", str(d_from))
            .lte("date", str(d_to))
            .limit(10000)
            .execute()
        ).data or []

    curr_rows = _fetch(d_start, d_end)
    prev_rows = _fetch(d_prev_start, d_prev_end)

    curr_totals = _agg_rows(curr_rows)
    prev_totals = _agg_rows(prev_rows)

    # % Δ for each KPI
    deltas = {k: _delta(curr_totals.get(k), prev_totals.get(k)) for k in curr_totals}

    # ── Daily time series ──────────────────────────────────────────────────
    daily_map: dict = {}
    for r in curr_rows:
        d = str(r["date"])
        if d not in daily_map:
            daily_map[d] = {"date": d, "spend": 0.0, "purchases": 0, "revenue": 0.0, "impressions": 0, "clicks": 0}
        daily_map[d]["spend"]       += float(r.get("spend") or 0)
        daily_map[d]["purchases"]   += int(r.get("purchases") or 0)
        daily_map[d]["revenue"]     += float(r.get("purchase_value") or 0)
        daily_map[d]["impressions"] += int(r.get("impressions") or 0)
        daily_map[d]["clicks"]      += int(r.get("clicks") or 0)

    daily = []
    for i in range(days):
        d = str(d_start + timedelta(days=i))
        day = daily_map.get(d, {"date": d, "spend": 0.0, "purchases": 0, "revenue": 0.0, "impressions": 0, "clicks": 0})
        day["roas"] = round(day["revenue"] / day["spend"], 2) if day["spend"] > 0 else None
        day["cpc"]  = round(day["spend"] / day["clicks"], 2)  if day["clicks"] > 0 else None
        day["spend"]   = round(day["spend"], 2)
        day["revenue"] = round(day["revenue"], 2)
        daily.append(day)

    # ── Campaign hierarchy ─────────────────────────────────────────────────
    camp_map: dict = {}
    prev_camp_rev: dict = {}
    for r in prev_rows:
        cid_  = r.get("campaign_id") or ""
        prev_camp_rev.setdefault(cid_, {"spend": 0.0, "purchases": 0, "revenue": 0.0})
        prev_camp_rev[cid_]["spend"]     += float(r.get("spend") or 0)
        prev_camp_rev[cid_]["purchases"] += int(r.get("purchases") or 0)
        prev_camp_rev[cid_]["revenue"]   += float(r.get("purchase_value") or 0)

    for r in curr_rows:
        cid_  = r.get("campaign_id") or ""
        asid  = r.get("adset_id") or ""
        ad_id = r.get("ad_id") or ""

        # Campaign level
        c_ = camp_map.setdefault(cid_, {
            "campaign_id": cid_, "campaign_name": r.get("campaign_name") or cid_,
            "_totals": {"spend": 0.0, "impressions": 0, "clicks": 0, "purchases": 0, "revenue": 0.0},
            "_adsets": {},
        })
        for k in ("spend", "impressions", "clicks", "purchases"):
            c_["_totals"][k] += float(r.get(k) or 0) if k == "spend" else int(r.get(k) or 0)
        c_["_totals"]["revenue"] += float(r.get("purchase_value") or 0)

        # Adset level
        a_ = c_["_adsets"].setdefault(asid, {
            "adset_id": asid, "adset_name": r.get("adset_name") or asid,
            "_totals": {"spend": 0.0, "impressions": 0, "clicks": 0, "purchases": 0, "revenue": 0.0},
            "_ads": {},
        })
        for k in ("spend", "impressions", "clicks", "purchases"):
            a_["_totals"][k] += float(r.get(k) or 0) if k == "spend" else int(r.get(k) or 0)
        a_["_totals"]["revenue"] += float(r.get("purchase_value") or 0)

        # Ad level
        ad_ = a_["_ads"].setdefault(ad_id, {
            "ad_id": ad_id, "ad_name": r.get("ad_name") or ad_id,
            "_totals": {"spend": 0.0, "impressions": 0, "clicks": 0, "purchases": 0, "revenue": 0.0},
        })
        for k in ("spend", "impressions", "clicks", "purchases"):
            ad_["_totals"][k] += float(r.get(k) or 0) if k == "spend" else int(r.get(k) or 0)
        ad_["_totals"]["revenue"] += float(r.get("purchase_value") or 0)

    def _finalize(totals: dict, prev_spend: float | None = None, prev_rev: float | None = None) -> dict:
        t = totals
        sp = t["spend"]
        rv = t["revenue"]
        pu = t["purchases"]
        im = t["impressions"]
        cl = t["clicks"]
        roas = round(rv / sp, 2) if sp > 0 else None
        prev_roas = round(prev_rev / prev_spend, 2) if (prev_spend and prev_rev is not None and prev_spend > 0) else None
        return {
            "spend":       round(sp, 2),
            "revenue":     round(rv, 2),
            "purchases":   pu,
            "impressions": im,
            "clicks":      cl,
            "roas":        roas,
            "cpa":         round(sp / pu, 2) if pu > 0 else None,
            "ctr":         round(cl / im * 100, 2) if im > 0 else None,
            "cpc":         round(sp / cl, 2) if cl > 0 else None,
            "roas_delta":  _delta(roas, prev_roas),
            "spend_delta": _delta(sp, prev_spend),
        }

    # Fetch ad_creatives for image_url lookup
    creative_rows = (
        sb.table("ad_creatives")
        .select("ad_id, image_url, effective_status")
        .eq("client_id", client_id)
        .execute()
    ).data or []
    creative_map = {r["ad_id"]: r for r in creative_rows if r.get("ad_id")}

    # Fetch server-side orders for reconciliation
    order_rows = (
        sb.table("orders")
        .select("utm_campaign, total_price")
        .eq("client_id", client_id)
        .eq("financial_status", "paid")
        .gt("total_price", 0)
        .gte("created_at", f"{d_start}T00:00:00+00:00")
        .lte("created_at", f"{d_end}T23:59:59+00:00")
        .limit(5000)
        .execute()
    ).data or []

    # Build server lookup by ALL utm_campaign values (ID numérico, nome exato, nome lower)
    server_by_key: dict = {}
    for o in order_rows:
        cam = (o.get("utm_campaign") or "").strip()
        if not cam:
            continue
        e = server_by_key.setdefault(cam, {"orders": 0, "revenue": 0.0})
        e["orders"]  += 1
        e["revenue"] += float(o.get("total_price") or 0)

    def _srv_lookup(campaign_id: str, campaign_name: str) -> dict:
        """Tenta match por ID numérico, ID parcial, nome exato e nome lowercase."""
        empty = {"orders": 0, "revenue": 0.0}
        # 1. ID exato
        hit = server_by_key.get(campaign_id)
        if hit: return hit
        # 2. Nome exato
        hit = server_by_key.get(campaign_name)
        if hit: return hit
        # 3. Nome lowercase
        name_lower = campaign_name.lower()
        for k, v in server_by_key.items():
            if k.lower() == name_lower:
                return v
        # 4. ID parcial — alguns pedidos têm IDs truncados (12 dígitos vs 18)
        if campaign_id.isdigit() and len(campaign_id) >= 12:
            for k, v in server_by_key.items():
                if k.isdigit() and (k.startswith(campaign_id[:12]) or campaign_id.startswith(k[:12])):
                    return v
        return empty

    campaigns_out = []
    for cid_, c_ in camp_map.items():
        prev_c = prev_camp_rev.get(cid_, {})
        t = _finalize(c_["_totals"], prev_c.get("spend"), prev_c.get("revenue"))

        # Server-side reconciliation — multi-strategy lookup
        srv = _srv_lookup(cid_, c_["campaign_name"])
        t["server_orders"]  = srv["orders"]
        t["server_revenue"] = round(srv["revenue"], 2)

        adsets_out = []
        for asid_, a_ in c_["_adsets"].items():
            at = _finalize(a_["_totals"])
            ads_out = []
            for ad_id_, ad_ in a_["_ads"].items():
                adt = _finalize(ad_["_totals"])
                cr = creative_map.get(ad_id_, {})
                ads_out.append({
                    "ad_id":    ad_id_,
                    "ad_name":  ad_["ad_name"],
                    "image_url": cr.get("image_url"),
                    "status":    cr.get("effective_status"),
                    **adt,
                })
            ads_out.sort(key=lambda x: x["spend"], reverse=True)
            adsets_out.append({
                "adset_id":   asid_,
                "adset_name": a_["adset_name"],
                "ads":        ads_out,
                **at,
            })
        adsets_out.sort(key=lambda x: x["spend"], reverse=True)
        campaigns_out.append({
            "campaign_id":   cid_,
            "campaign_name": c_["campaign_name"],
            "adsets":        adsets_out,
            **t,
        })
    campaigns_out.sort(key=lambda x: x["spend"], reverse=True)

    # ── Funnel from tracking_events (single query per period) ─────────────
    funnel_start = f"{d_start}T00:00:00+00:00"
    funnel_end   = f"{d_end}T23:59:59+00:00"

    def _funnel_counts(start_ts: str, end_ts: str) -> dict:
        rows = (
            sb.table("tracking_events")
            .select("event_type")
            .eq("client_id", client_id)
            .in_("event_type", ["pageview", "view_product", "add_to_cart", "begin_checkout"])
            .gte("created_at", start_ts)
            .lte("created_at", end_ts)
            .limit(100000)
            .execute()
        ).data or []
        counts: dict[str, int] = {}
        for r in rows:
            et = r.get("event_type") or ""
            counts[et] = counts.get(et, 0) + 1
        return counts

    try:
        funnel_raw = _funnel_counts(funnel_start, funnel_end)
    except Exception:
        funnel_raw = {}

    funnel_curr = {et: funnel_raw.get(et, 0)
                   for et in ("pageview", "view_product", "add_to_cart", "begin_checkout")}
    funnel_curr["purchases"] = curr_totals["purchases"]

    prev_funnel_start = f"{d_prev_start}T00:00:00+00:00"
    prev_funnel_end   = f"{d_prev_end}T23:59:59+00:00"
    try:
        prev_funnel_raw = _funnel_counts(prev_funnel_start, prev_funnel_end)
    except Exception:
        prev_funnel_raw = {}

    funnel_prev = {et: prev_funnel_raw.get(et, 0)
                   for et in ("pageview", "add_to_cart", "begin_checkout")}
    funnel_prev["purchases"] = prev_totals["purchases"]

    return {
        "days":       days,
        "start":      str(d_start),
        "end":        str(d_end),
        "prev_start": str(d_prev_start),
        "prev_end":   str(d_prev_end),
        "has_data":   bool(curr_rows),
        "totals":     curr_totals,
        "prev_totals": prev_totals,
        "deltas":     deltas,
        "campaigns":  campaigns_out,
        "daily":      daily,
        "funnel":     funnel_curr,
        "funnel_prev": funnel_prev,
    }
