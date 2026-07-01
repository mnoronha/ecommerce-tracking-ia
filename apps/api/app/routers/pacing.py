"""
Pacing — month-to-date progress vs revenue/spend goals.

DTC merchants check this multiple times a day. The widget at the top of the
dashboard answers: am I on track to hit my monthly goal?

Projection uses linear extrapolation from days elapsed / total business days.
"""

import calendar
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException

from ..database import get_supabase

from pydantic import BaseModel
from typing import Optional

logger = logging.getLogger(__name__)
router = APIRouter()


# ── Goals CRUD (service-key — bypasses RLS) ───────────────────────────────────

class GoalPayload(BaseModel):
    month:            str            # YYYY-MM-DD
    revenue_goal:     Optional[float] = None
    roas_goal:        Optional[float] = None
    leads_goal:       Optional[int]   = None
    conversions_goal: Optional[int]   = None
    cpa_target:       Optional[float] = None


@router.post("/goals/{pixel_id}", summary="Salva meta mensal (service key — bypass RLS)")
async def upsert_goal(pixel_id: str, body: GoalPayload):
    sb = get_supabase()

    client = sb.table("clients").select("id, agency_id").eq("pixel_id", pixel_id).limit(1).execute()
    if not (client and client.data):
        raise HTTPException(status_code=404, detail="Cliente não encontrado")

    client_id = client.data[0]["id"]
    agency_id = client.data[0]["agency_id"]

    payload = {
        "agency_id":        agency_id,
        "client_id":        client_id,
        "month":            body.month,
        "revenue_goal":     body.revenue_goal,
        "roas_goal":        body.roas_goal,
        "leads_goal":       body.leads_goal,
        "conversions_goal": body.conversions_goal,
        "cpa_target":       body.cpa_target,
    }

    # Upsert — update if row exists for (client_id, month)
    existing = (
        sb.table("goals")
        .select("id")
        .eq("client_id", client_id)
        .eq("month", body.month)
        .limit(1)
        .execute()
    )

    if existing.data:
        result = sb.table("goals").update(payload).eq("id", existing.data[0]["id"]).execute()
    else:
        result = sb.table("goals").insert(payload).execute()

    if not (result and result.data):
        raise HTTPException(status_code=500, detail="Falha ao salvar meta")

    return result.data[0]


@router.get("/goals/{pixel_id}", summary="Lista metas dos últimos N meses")
async def list_goals(pixel_id: str, months: int = 6):
    sb = get_supabase()
    client = sb.table("clients").select("id").eq("pixel_id", pixel_id).limit(1).execute()
    if not (client and client.data):
        raise HTTPException(status_code=404, detail="Cliente não encontrado")

    from datetime import date, timedelta
    today = date.today()
    month_starts = []
    for i in range(months):
        d = today.replace(day=1)
        # Subtract i months
        month = d.month - i
        year  = d.year
        while month <= 0:
            month += 12
            year  -= 1
        month_starts.append(f"{year}-{month:02d}-01")

    result = (
        sb.table("goals")
        .select("id, month, revenue_goal, roas_goal, leads_goal, conversions_goal, cpa_target")
        .eq("client_id", client.data[0]["id"])
        .in_("month", month_starts)
        .order("month", desc=True)
        .execute()
    )
    return {"goals": result.data or []}


class PersistentGoalsPayload(BaseModel):
    revenue_goal:      Optional[float] = None
    roas_goal:         Optional[float] = None
    cpa_target:        Optional[float] = None
    meta_ads_budget:   Optional[float] = None
    google_ads_budget: Optional[float] = None
    tiktok_ads_budget: Optional[float] = None


@router.get("/clients/{pixel_id}/goals", summary="Metas persistentes do cliente")
async def get_persistent_goals(pixel_id: str):
    sb = get_supabase()
    client = (
        sb.table("clients")
        .select("monthly_revenue_goal, target_roas, cpa_target, "
                "meta_ads_budget, google_ads_budget, tiktok_ads_budget")
        .eq("pixel_id", pixel_id)
        .limit(1)
        .execute()
    )
    if not (client and client.data):
        raise HTTPException(status_code=404, detail="Cliente não encontrado")
    c = client.data[0]
    return {
        "revenue_goal":      c.get("monthly_revenue_goal"),
        "roas_goal":         c.get("target_roas"),
        "cpa_target":        c.get("cpa_target"),
        "meta_ads_budget":   c.get("meta_ads_budget"),
        "google_ads_budget": c.get("google_ads_budget"),
        "tiktok_ads_budget": c.get("tiktok_ads_budget"),
    }


@router.post("/clients/{pixel_id}/goals", summary="Salva metas persistentes do cliente")
async def save_persistent_goals(pixel_id: str, body: PersistentGoalsPayload):
    sb = get_supabase()
    client = (
        sb.table("clients")
        .select("id, agency_id")
        .eq("pixel_id", pixel_id)
        .limit(1)
        .execute()
    )
    if not (client and client.data):
        raise HTTPException(status_code=404, detail="Cliente não encontrado")
    cid       = client.data[0]["id"]
    agency_id = client.data[0]["agency_id"]

    # Persist to clients table (permanent — survives month change)
    update: dict = {}
    if body.revenue_goal      is not None: update["monthly_revenue_goal"] = body.revenue_goal
    if body.roas_goal          is not None: update["target_roas"]          = body.roas_goal
    if body.cpa_target         is not None: update["cpa_target"]           = body.cpa_target
    if body.meta_ads_budget    is not None: update["meta_ads_budget"]      = body.meta_ads_budget
    if body.google_ads_budget  is not None: update["google_ads_budget"]    = body.google_ads_budget
    if body.tiktok_ads_budget  is not None: update["tiktok_ads_budget"]    = body.tiktok_ads_budget

    # Recalculate total ad spend goal from per-channel budgets
    total_budget = sum(filter(None, [
        body.meta_ads_budget, body.google_ads_budget, body.tiktok_ads_budget,
    ]))
    if total_budget > 0:
        update["monthly_ad_spend_goal"] = round(total_budget, 2)

    if update:
        sb.table("clients").update(update).eq("id", cid).execute()

    # Mirror to goals table for current month so /pacing and history still work
    from datetime import date
    month_key = date.today().replace(day=1).isoformat()
    goal_payload: dict = {"agency_id": agency_id, "client_id": cid, "month": month_key}
    if body.revenue_goal is not None: goal_payload["revenue_goal"] = body.revenue_goal
    if body.roas_goal     is not None: goal_payload["roas_goal"]    = body.roas_goal
    if body.cpa_target    is not None: goal_payload["cpa_target"]   = body.cpa_target

    if len(goal_payload) > 3:  # has at least one metric beyond the 3 required keys
        existing = (
            sb.table("goals").select("id")
            .eq("client_id", cid).eq("month", month_key)
            .limit(1).execute()
        )
        if existing.data:
            sb.table("goals").update(goal_payload).eq("id", existing.data[0]["id"]).execute()
        else:
            sb.table("goals").insert(goal_payload).execute()

    return {"ok": True}


@router.get(
    "/pacing/{pixel_id}",
    summary="Month-to-date pacing vs goals",
    tags=["pacing"],
)
async def get_pacing(pixel_id: str):
    sb = get_supabase()
    client = (
        sb.table("clients")
        .select("id, monthly_revenue_goal, monthly_ad_spend_goal, target_roas")
        .eq("pixel_id", pixel_id)
        .eq("is_active", True)
        .limit(1)
        .execute()
    )
    if not (client and client.data):
        raise HTTPException(status_code=404, detail="Client not found")
    c = client.data[0]

    # Use BRT (UTC-3) — clientes são brasileiros; meia-noite UTC != virada do mês BRT
    from datetime import timedelta
    BRT = timezone(timedelta(hours=-3))
    now = datetime.now(BRT)
    month_start_brt = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    month_key   = month_start_brt.date().isoformat()  # YYYY-MM-01
    month_start = month_start_brt.astimezone(timezone.utc)  # para query no Supabase (stored UTC)

    # Prefer goals table (set via Metas page); fall back to legacy clients column
    try:
        goal_row = (
            sb.table("goals")
            .select("revenue_goal, roas_goal")
            .eq("client_id", c["id"])
            .eq("month", month_key)
            .limit(1)
            .execute()
        ).data
        if goal_row:
            if goal_row[0].get("revenue_goal"):
                c["monthly_revenue_goal"] = goal_row[0]["revenue_goal"]
            if goal_row[0].get("roas_goal"):
                c["target_roas"] = goal_row[0]["roas_goal"]
    except Exception as exc:
        logger.debug("pacing: goals table lookup failed: %s", exc)
    days_in_month = calendar.monthrange(now.year, now.month)[1]
    day_of_month  = now.day
    fraction_done = day_of_month / days_in_month

    # Today vs MTD revenue (BRT boundaries → UTC for Supabase)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)

    mtd_orders = (
        sb.table("orders")
        .select("total_price, gross_profit, created_at")
        .eq("client_id", c["id"])
        .eq("financial_status", "paid")
        .gt("total_price", 0)
        .gte("created_at", month_start.isoformat())
        .execute()
    ).data or []

    mtd_revenue = sum(float(o.get("total_price") or 0) for o in mtd_orders)
    mtd_profit  = sum(float(o.get("gross_profit") or 0) for o in mtd_orders if o.get("gross_profit"))
    mtd_orders_count = len(mtd_orders)

    today_orders = [o for o in mtd_orders if o.get("created_at") >= today_start.isoformat()]
    today_revenue = sum(float(o.get("total_price") or 0) for o in today_orders)

    # Linear projection assuming current run-rate continues
    projected_revenue = round(mtd_revenue / fraction_done, 2) if fraction_done > 0 else 0

    goal       = float(c.get("monthly_revenue_goal") or 0)
    pct_done   = round(mtd_revenue / goal * 100, 1) if goal > 0 else None
    pct_target = round(fraction_done * 100, 1)
    on_track   = (pct_done is not None and pct_done >= pct_target * 0.9) if goal > 0 else None

    needed_per_day_remaining = None
    if goal > 0 and day_of_month < days_in_month:
        remaining = max(goal - mtd_revenue, 0)
        days_left = days_in_month - day_of_month
        needed_per_day_remaining = round(remaining / days_left, 2)

    return {
        "now":                       now.isoformat(),
        "month_start":               month_start.isoformat(),
        "day_of_month":              day_of_month,
        "days_in_month":             days_in_month,
        "fraction_done":             round(fraction_done, 4),
        # Revenue side
        "mtd_revenue":               round(mtd_revenue, 2),
        "mtd_orders":                mtd_orders_count,
        "mtd_profit":                round(mtd_profit, 2) if mtd_profit else None,
        "today_revenue":             round(today_revenue, 2),
        "today_orders":              len(today_orders),
        "projected_revenue":         projected_revenue,
        "monthly_revenue_goal":      goal if goal > 0 else None,
        "pct_done":                  pct_done,
        "pct_target":                pct_target,
        "on_track":                  on_track,
        "needed_per_day_remaining":  needed_per_day_remaining,
        # Spend side (filled in if has Meta Ads creds; left null otherwise)
        "monthly_ad_spend_goal":     float(c.get("monthly_ad_spend_goal") or 0) or None,
        "target_roas":               float(c.get("target_roas") or 0) or None,
    }
