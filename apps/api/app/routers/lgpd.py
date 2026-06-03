"""
LGPD — direito à eliminação (deleção/anonimização de titular).

Recebe um email e remove os dados pessoais daquela pessoa no cliente:
- orders: anonimiza PII (email, telefone, nome, IP, UA, CEP) mas MANTÉM o
  registro financeiro (valor/data) — necessário fiscalmente e para agregados.
- visitors: anonimiza email/telefone.
- tracking_events dos cookies da pessoa: anonimiza ip_hash/user_agent.
- attribution_cookies: apaga.

Registra a solicitação em audit-friendly via os próprios updates (audit_log
captura). Idempotente.
"""

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..database import get_supabase

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/lgpd", tags=["lgpd"])

_ORDER_PII_NULL = {
    "email": None, "phone": None, "first_name": None, "last_name": None,
    "browser_ip": None, "browser_ua": None, "zip_code": None,
}


def _resolve(pixel_id: str) -> Optional[str]:
    r = (
        get_supabase().table("clients").select("id")
        .eq("pixel_id", pixel_id).limit(1).execute()
    )
    return r.data[0]["id"] if (r and r.data) else None


class ForgetBody(BaseModel):
    email: str


def _count(resp) -> int:
    return len(resp.data or []) if resp else 0


@router.get("/{pixel_id}/subject", summary="Prévia: o que existe de um titular (por email)")
async def preview_subject(pixel_id: str, email: str):
    cid = _resolve(pixel_id)
    if not cid:
        raise HTTPException(404, "client not found")
    sb = get_supabase()
    e = email.strip().lower()
    orders = (sb.table("orders").select("id", count="exact", head=True)
              .eq("client_id", cid).ilike("email", e).execute()).count or 0
    visitors = (sb.table("visitors").select("id", count="exact", head=True)
                .eq("client_id", cid).ilike("email", e).execute()).count or 0
    cookies = (sb.table("attribution_cookies").select("id", count="exact", head=True)
               .eq("client_id", cid).ilike("email", e).execute()).count or 0
    return {"email": e, "orders": orders, "visitors": visitors, "attribution_cookies": cookies}


@router.post("/{pixel_id}/forget", summary="Apaga/anonimiza os dados pessoais de um titular (LGPD)")
async def forget_subject(pixel_id: str, body: ForgetBody):
    cid = _resolve(pixel_id)
    if not cid:
        raise HTTPException(404, "client not found")
    e = (body.email or "").strip().lower()
    if not e or "@" not in e:
        raise HTTPException(400, "email inválido")
    sb = get_supabase()

    # Cookies da pessoa (pra anonimizar os eventos brutos depois)
    cookies: set[str] = set()
    for tbl, col in (("visitors", "visitor_id"), ("attribution_cookies", "visitor_cookie_id")):
        rows = (sb.table(tbl).select(col).eq("client_id", cid).ilike("email", e).execute()).data or []
        cookies |= {r[col] for r in rows if r.get(col)}

    # 1) Pedidos — anonimiza PII, mantém o registro
    orders_anon = _count(
        sb.table("orders").update(_ORDER_PII_NULL).eq("client_id", cid).ilike("email", e).execute()
    )
    # 2) Visitantes — anonimiza
    visitors_anon = _count(
        sb.table("visitors").update({"email": None, "phone": None}).eq("client_id", cid).ilike("email", e).execute()
    )
    # 3) Eventos brutos dos cookies — anonimiza ip_hash/user_agent
    events_anon = 0
    cookie_list = list(cookies)
    for i in range(0, len(cookie_list), 200):
        chunk = cookie_list[i:i + 200]
        events_anon += _count(
            sb.table("tracking_events").update({"ip_hash": None, "user_agent": None})
            .eq("client_id", cid).in_("visitor_cookie_id", chunk).execute()
        )
    # 4) Cookies de atribuição — apaga
    cookies_deleted = _count(
        sb.table("attribution_cookies").delete().eq("client_id", cid).ilike("email", e).execute()
    )

    logger.info("lgpd forget %s/%s: orders=%s visitors=%s events=%s cookies=%s",
                pixel_id, e, orders_anon, visitors_anon, events_anon, cookies_deleted)
    return {
        "email": e,
        "orders_anonymized": orders_anon,
        "visitors_anonymized": visitors_anon,
        "events_anonymized": events_anon,
        "attribution_cookies_deleted": cookies_deleted,
    }
