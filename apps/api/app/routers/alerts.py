"""
Alerts router.

  POST /alerts/run                    — manually trigger the engine
  GET  /alerts/rules/{pixel_id}       — list alert_rules for a client
  PATCH /alerts/rules/{rule_id}       — update a rule (enabled, config)
  POST /alerts/{alert_id}/resolve     — manually resolve an alert
  GET  /alerts/{pixel_id}             — list open alerts for a client
  GET  /alerts                        — list open alerts for agency
"""

import logging
from typing import Optional
from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from ..database import get_supabase
from ..services import alert_engine

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/alerts", tags=["alerts"])


class RuleUpdate(BaseModel):
    enabled: Optional[bool] = None
    config:  Optional[dict] = None


class RuleToggle(BaseModel):
    rule_key: str
    name:     str
    enabled:  bool


@router.post("/run", summary="Manually run the alert engine")
async def trigger_alert_engine():
    """
    Runs all enabled alert_rules now and returns counters. Useful after editing
    a rule's config or seeding goals/budgets — surfaces new alerts immediately
    instead of waiting for the 30-min cron tick.
    """
    return alert_engine.run_alert_engine()


@router.get("/rules/{pixel_id}", summary="List alert rules for a client")
async def list_rules_for_client(pixel_id: str):
    sb = get_supabase()
    client = (
        sb.table("clients").select("id, agency_id")
        .eq("pixel_id", pixel_id).limit(1).execute()
    )
    if not (client and client.data):
        raise HTTPException(status_code=404, detail="client not found")

    c          = client.data[0]
    agency_id  = c["agency_id"]
    client_id  = c["id"]

    # Regras da agência (globais) + overrides deste cliente, fundidas no estado
    # EFETIVO por regra: o override (se existir) manda no enabled.
    rows = (
        sb.table("alert_rules")
        .select("id, name, rule_key, severity, enabled, channels, throttle_minutes, config, client_id")
        .eq("agency_id", agency_id)
        .or_(f"client_id.is.null,client_id.eq.{client_id}")
        .execute()
    ).data or []

    globals_  = [r for r in rows if r.get("client_id") is None]
    overrides = {(r["rule_key"], r.get("name")): r for r in rows if r.get("client_id")}

    effective = []
    for g in globals_:
        ov = overrides.get((g["rule_key"], g.get("name")))
        enabled = ov["enabled"] if ov else g["enabled"]
        effective.append({
            "rule_key":         g["rule_key"],
            "name":             g.get("name"),
            "severity":         g["severity"],
            "throttle_minutes": g.get("throttle_minutes"),
            "config":           g.get("config"),
            "enabled":          enabled,
            "global_enabled":   g["enabled"],
            "overridden":       bool(ov) and ov["enabled"] != g["enabled"],
        })
    # Ordena por severidade (crítico → atenção → info) e nome
    sev_order = {"critical": 0, "warning": 1, "info": 2}
    effective.sort(key=lambda r: (sev_order.get(r["severity"], 9), r.get("name") or ""))
    return {"rules": effective}


@router.post("/rules/{pixel_id}/toggle", summary="Liga/desliga um alerta SÓ para este cliente")
async def toggle_rule_for_client(pixel_id: str, body: RuleToggle):
    """
    Cria/atualiza um override por cliente para a regra (rule_key, name). Se o
    valor voltar a coincidir com o padrão global, o override é removido (volta
    a herdar o global). Não altera a regra global da agência.
    """
    sb = get_supabase()
    client = (
        sb.table("clients").select("id, agency_id")
        .eq("pixel_id", pixel_id).limit(1).execute()
    )
    if not (client and client.data):
        raise HTTPException(status_code=404, detail="client not found")
    cid       = client.data[0]["id"]
    agency_id = client.data[0]["agency_id"]

    g = (
        sb.table("alert_rules")
        .select("id, rule_key, name, severity, channels, throttle_minutes, config, enabled")
        .eq("agency_id", agency_id)
        .eq("rule_key", body.rule_key)
        .eq("name", body.name)
        .is_("client_id", "null")
        .limit(1)
        .execute()
    ).data
    if not g:
        raise HTTPException(status_code=404, detail="global rule not found")
    g = g[0]

    existing = (
        sb.table("alert_rules")
        .select("id")
        .eq("agency_id", agency_id).eq("client_id", cid)
        .eq("rule_key", body.rule_key).eq("name", body.name)
        .limit(1)
        .execute()
    ).data

    # Voltou ao padrão global → remove override (herda de novo)
    if body.enabled == g["enabled"]:
        if existing:
            sb.table("alert_rules").delete().eq("id", existing[0]["id"]).execute()
        return {"enabled": body.enabled, "overridden": False}

    if existing:
        sb.table("alert_rules").update({"enabled": body.enabled}).eq("id", existing[0]["id"]).execute()
    else:
        sb.table("alert_rules").insert({
            "agency_id":        agency_id,
            "client_id":        cid,
            "rule_key":         g["rule_key"],
            "name":             g["name"],
            "severity":         g["severity"],
            "channels":         g.get("channels"),
            "throttle_minutes": g.get("throttle_minutes"),
            "config":           g.get("config"),
            "enabled":          body.enabled,
        }).execute()
    return {"enabled": body.enabled, "overridden": True}


@router.patch("/rules/{rule_id}", summary="Update a rule (enabled, config)")
async def update_rule(rule_id: str, body: RuleUpdate):
    sb = get_supabase()
    patch: dict = {}
    if body.enabled is not None:
        patch["enabled"] = body.enabled
    if body.config is not None:
        patch["config"] = body.config
    if not patch:
        raise HTTPException(status_code=400, detail="nothing to update")
    result = (
        sb.table("alert_rules")
        .update(patch)
        .eq("id", rule_id)
        .execute()
    )
    if not (result and result.data):
        raise HTTPException(status_code=404, detail="rule not found")
    return result.data[0]


@router.post("/{alert_id}/silence", summary="Silence an alert for 24 hours")
async def silence_alert(alert_id: str):
    sb    = get_supabase()
    until = (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat()
    result = (
        sb.table("alerts")
        .update({"silenced_until": until})
        .eq("id", alert_id)
        .is_("resolved_at", "null")
        .execute()
    )
    if not (result and result.data):
        raise HTTPException(status_code=404, detail="alert not found or already resolved")
    return {"silenced": True, "silenced_until": until}


@router.post("/{alert_id}/resolve", summary="Manually resolve an alert")
async def resolve_alert(alert_id: str):
    sb  = get_supabase()
    now = datetime.now(timezone.utc).isoformat()
    result = (
        sb.table("alerts")
        .update({"resolved_at": now, "is_resolved": True})
        .eq("id", alert_id)
        .is_("resolved_at", "null")
        .execute()
    )
    if not (result and result.data):
        raise HTTPException(status_code=404, detail="alert not found or already resolved")
    return {"resolved": True, "resolved_at": now}


@router.get("/{pixel_id}", summary="List open alerts for a client")
async def list_alerts_for_client(
    pixel_id: str,
    include_resolved: bool = Query(False),
    limit: int = Query(50, ge=1, le=200),
):
    sb = get_supabase()
    client = (
        sb.table("clients").select("id, agency_id")
        .eq("pixel_id", pixel_id).limit(1).execute()
    )
    if not (client and client.data):
        raise HTTPException(status_code=404, detail="client not found")

    q = (
        sb.table("alerts")
        .select("id, severity, fingerprint, title, message, data, created_at, resolved_at, alert_rule_id")
        .eq("client_id", client.data[0]["id"])
        .order("created_at", desc=True)
        .limit(limit)
    )
    if not include_resolved:
        q = q.is_("resolved_at", "null")
    rows = q.execute().data or []
    return {"alerts": rows, "count": len(rows)}


@router.get("", summary="List open alerts across all clients of an agency")
async def list_alerts_for_agency(
    agency_slug: str = Query(..., description="Agency slug (e.g. 'pareto-plus')"),
    include_resolved: bool = Query(False),
    limit: int = Query(100, ge=1, le=500),
):
    sb = get_supabase()
    agency = (
        sb.table("agencies").select("id, slug")
        .eq("slug", agency_slug).limit(1).execute()
    )
    if not (agency and agency.data):
        raise HTTPException(status_code=404, detail="agency not found")

    q = (
        sb.table("alerts")
        .select("id, client_id, severity, fingerprint, title, message, data, created_at, resolved_at, alert_rule_id")
        .eq("agency_id", agency.data[0]["id"])
        .order("created_at", desc=True)
        .limit(limit)
    )
    if not include_resolved:
        q = q.is_("resolved_at", "null")
    rows = q.execute().data or []
    return {"alerts": rows, "count": len(rows)}
