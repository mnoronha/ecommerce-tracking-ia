"""
Finance module — internal financial management for Norolabs agency.

Contracts, receivables, payables, and due-date notifications.
All data is agency-scoped (not client-facing).
"""

from datetime import date, timedelta
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from ..database import get_supabase

router = APIRouter(prefix="/finance", tags=["finance"])


# ── Pydantic models ────────────────────────────────────────────────────────────

class ContractIn(BaseModel):
    client_id:     Optional[str] = None
    client_name:   str
    description:   Optional[str] = None
    monthly_value: float
    due_day:       int
    start_date:    str   # YYYY-MM-DD
    end_date:      Optional[str] = None
    status:        str = "active"
    notes:         Optional[str] = None

class ContractPatch(BaseModel):
    client_name:   Optional[str] = None
    description:   Optional[str] = None
    monthly_value: Optional[float] = None
    due_day:       Optional[int] = None
    start_date:    Optional[str] = None
    end_date:      Optional[str] = None
    status:        Optional[str] = None
    notes:         Optional[str] = None

class ReceivableIn(BaseModel):
    contract_id:  Optional[str] = None
    client_name:  str
    description:  str
    amount:       float
    due_date:     str   # YYYY-MM-DD
    status:       str = "pending"
    notes:        Optional[str] = None

class ReceivablePatch(BaseModel):
    description:  Optional[str] = None
    amount:       Optional[float] = None
    due_date:     Optional[str] = None
    status:       Optional[str] = None
    paid_at:      Optional[str] = None
    notes:        Optional[str] = None

class PayableIn(BaseModel):
    description:  str
    supplier:     Optional[str] = None
    amount:       float
    due_date:     str   # YYYY-MM-DD
    category:     str = "other"
    recurrent:    bool = False
    notes:        Optional[str] = None

class PayablePatch(BaseModel):
    description:  Optional[str] = None
    supplier:     Optional[str] = None
    amount:       Optional[float] = None
    due_date:     Optional[str] = None
    category:     Optional[str] = None
    status:       Optional[str] = None
    paid_at:      Optional[str] = None
    recurrent:    Optional[bool] = None
    notes:        Optional[str] = None


# ── Summary ────────────────────────────────────────────────────────────────────

@router.get("/summary")
def get_summary():
    """MRR, total a receber no mês, total a pagar no mês, inadimplência."""
    sb    = get_supabase()
    today = date.today()
    month_start = today.replace(day=1).isoformat()
    month_end   = (today.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)

    # MRR: soma dos contratos ativos
    contracts = sb.table("contracts").select("monthly_value").eq("status", "active").execute().data or []
    mrr = round(sum(float(c["monthly_value"]) for c in contracts), 2)

    # Recebíveis do mês
    recv = sb.table("receivables").select("amount,status").gte("due_date", month_start).lte("due_date", month_end.isoformat()).execute().data or []
    recv_total   = round(sum(float(r["amount"]) for r in recv), 2)
    recv_paid    = round(sum(float(r["amount"]) for r in recv if r["status"] == "paid"), 2)
    recv_pending = round(sum(float(r["amount"]) for r in recv if r["status"] == "pending"), 2)
    recv_overdue = round(sum(float(r["amount"]) for r in sb.table("receivables").select("amount").eq("status", "pending").lt("due_date", today.isoformat()).execute().data or []), 2)

    # Pagamentos do mês
    pay = sb.table("payables").select("amount,status").gte("due_date", month_start).lte("due_date", month_end.isoformat()).execute().data or []
    pay_total   = round(sum(float(p["amount"]) for p in pay), 2)
    pay_paid    = round(sum(float(p["amount"]) for p in pay if p["status"] == "paid"), 2)
    pay_pending = round(sum(float(p["amount"]) for p in pay if p["status"] == "pending"), 2)

    # Notificações não lidas
    unread = sb.table("finance_notifications").select("id", count="exact", head=True).is_("read_at", "null").execute().count or 0

    return {
        "mrr":              mrr,
        "active_contracts": len(contracts),
        "receivables": {
            "month_total":   recv_total,
            "paid":          recv_paid,
            "pending":       recv_pending,
            "overdue":       recv_overdue,
        },
        "payables": {
            "month_total":   pay_total,
            "paid":          pay_paid,
            "pending":       pay_pending,
        },
        "unread_notifications": unread,
    }


# ── Contracts ─────────────────────────────────────────────────────────────────

@router.get("/contracts")
def list_contracts(status: Optional[str] = Query(None)):
    sb = get_supabase()
    q  = sb.table("contracts").select("*").order("client_name")
    if status:
        q = q.eq("status", status)
    return q.execute().data or []


@router.post("/contracts", status_code=201)
def create_contract(body: ContractIn):
    sb  = get_supabase()
    row = body.model_dump(exclude_none=True)
    res = sb.table("contracts").insert(row).execute()
    return res.data[0]


@router.patch("/contracts/{contract_id}")
def update_contract(contract_id: str, body: ContractPatch):
    sb      = get_supabase()
    updates = {k: v for k, v in body.model_dump(exclude_none=True).items()}
    if not updates:
        raise HTTPException(400, "Nenhum campo para atualizar")
    from datetime import datetime, timezone
    updates["updated_at"] = datetime.now(timezone.utc).isoformat()
    res = sb.table("contracts").update(updates).eq("id", contract_id).execute()
    if not res.data:
        raise HTTPException(404, "Contrato não encontrado")
    return res.data[0]


@router.delete("/contracts/{contract_id}", status_code=204)
def cancel_contract(contract_id: str):
    sb = get_supabase()
    from datetime import datetime, timezone
    sb.table("contracts").update({"status": "cancelled", "updated_at": datetime.now(timezone.utc).isoformat()}).eq("id", contract_id).execute()


# ── Receivables ───────────────────────────────────────────────────────────────

@router.get("/receivables")
def list_receivables(
    status:     Optional[str] = Query(None),
    month:      Optional[str] = Query(None, description="YYYY-MM — filtra pelo mês de vencimento"),
):
    sb = get_supabase()
    q  = sb.table("receivables").select("*, contracts(client_name, monthly_value)").order("due_date")
    if status:
        q = q.eq("status", status)
    if month:
        q = q.gte("due_date", f"{month}-01").lte("due_date", f"{month}-31")
    return q.execute().data or []


@router.post("/receivables", status_code=201)
def create_receivable(body: ReceivableIn):
    sb  = get_supabase()
    row = body.model_dump(exclude_none=True)
    res = sb.table("receivables").insert(row).execute()
    return res.data[0]


@router.patch("/receivables/{receivable_id}")
def update_receivable(receivable_id: str, body: ReceivablePatch):
    sb      = get_supabase()
    updates = {k: v for k, v in body.model_dump(exclude_none=True).items()}
    if not updates:
        raise HTTPException(400, "Nenhum campo para atualizar")
    from datetime import datetime, timezone
    updates["updated_at"] = datetime.now(timezone.utc).isoformat()
    # Se marcando como pago e paid_at não informado, usa hoje
    if updates.get("status") == "paid" and "paid_at" not in updates:
        updates["paid_at"] = date.today().isoformat()
    res = sb.table("receivables").update(updates).eq("id", receivable_id).execute()
    if not res.data:
        raise HTTPException(404, "Recebível não encontrado")
    return res.data[0]


@router.post("/receivables/generate")
def generate_receivables_from_contracts(month: str = Query(..., description="YYYY-MM")):
    """Gera recebíveis para o mês a partir dos contratos ativos (ignora se já existir)."""
    sb        = get_supabase()
    contracts = sb.table("contracts").select("*").eq("status", "active").execute().data or []
    created   = []
    skipped   = []

    for c in contracts:
        due_day  = int(c["due_day"])
        due_date = f"{month}-{due_day:02d}"

        # Verifica se já existe recebível para este contrato neste mês
        existing = sb.table("receivables").select("id").eq("contract_id", c["id"]).gte("due_date", f"{month}-01").lte("due_date", f"{month}-31").execute().data or []
        if existing:
            skipped.append(c["client_name"])
            continue

        row = {
            "contract_id":  c["id"],
            "client_name":  c["client_name"],
            "description":  c.get("description") or f"Mensalidade {month}",
            "amount":       c["monthly_value"],
            "due_date":     due_date,
            "status":       "pending",
        }
        res = sb.table("receivables").insert(row).execute()
        created.append(res.data[0]["client_name"])

    return {"created": created, "skipped": skipped, "month": month}


# ── Payables ──────────────────────────────────────────────────────────────────

@router.get("/payables")
def list_payables(
    status:   Optional[str] = Query(None),
    month:    Optional[str] = Query(None, description="YYYY-MM"),
    category: Optional[str] = Query(None),
):
    sb = get_supabase()
    q  = sb.table("payables").select("*").order("due_date")
    if status:
        q = q.eq("status", status)
    if month:
        q = q.gte("due_date", f"{month}-01").lte("due_date", f"{month}-31")
    if category:
        q = q.eq("category", category)
    return q.execute().data or []


@router.post("/payables", status_code=201)
def create_payable(body: PayableIn):
    sb  = get_supabase()
    row = body.model_dump(exclude_none=True)
    res = sb.table("payables").insert(row).execute()
    return res.data[0]


@router.patch("/payables/{payable_id}")
def update_payable(payable_id: str, body: PayablePatch):
    sb      = get_supabase()
    updates = {k: v for k, v in body.model_dump(exclude_none=True).items()}
    if not updates:
        raise HTTPException(400, "Nenhum campo para atualizar")
    from datetime import datetime, timezone
    updates["updated_at"] = datetime.now(timezone.utc).isoformat()
    if updates.get("status") == "paid" and "paid_at" not in updates:
        updates["paid_at"] = date.today().isoformat()
    res = sb.table("payables").update(updates).eq("id", payable_id).execute()
    if not res.data:
        raise HTTPException(404, "Conta a pagar não encontrada")
    return res.data[0]


@router.delete("/payables/{payable_id}", status_code=204)
def delete_payable(payable_id: str):
    sb = get_supabase()
    sb.table("payables").update({"status": "cancelled"}).eq("id", payable_id).execute()


# ── Notifications ─────────────────────────────────────────────────────────────

@router.get("/notifications")
def list_notifications(unread_only: bool = Query(True)):
    sb = get_supabase()
    q  = sb.table("finance_notifications").select("*").order("created_at", desc=True).limit(50)
    if unread_only:
        q = q.is_("read_at", "null")
    return q.execute().data or []


@router.post("/notifications/{notification_id}/read", status_code=204)
def mark_read(notification_id: str):
    from datetime import datetime, timezone
    sb = get_supabase()
    sb.table("finance_notifications").update({"read_at": datetime.now(timezone.utc).isoformat()}).eq("id", notification_id).execute()


@router.post("/notifications/read-all", status_code=204)
def mark_all_read():
    from datetime import datetime, timezone
    sb = get_supabase()
    sb.table("finance_notifications").update({"read_at": datetime.now(timezone.utc).isoformat()}).is_("read_at", "null").execute()
