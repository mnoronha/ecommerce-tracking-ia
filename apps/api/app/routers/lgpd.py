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

from ..core.pii import email_hash
from ..database import get_supabase

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/lgpd", tags=["lgpd"])

# Customers are identified by email_hash (core/pii.py). Raw columns are also
# nulled for rows written before the switch.
_ORDER_PII_NULL = {
    "email": None, "phone": None, "first_name": None, "last_name": None,
    "browser_ip": None, "browser_ua": None, "zip_code": None,
    "email_hash": None, "phone_hash": None, "email_masked": None,
}
_VISITOR_PII_NULL = {
    "email": None, "phone": None, "email_hash": None, "phone_hash": None, "email_masked": None,
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


def _subject_hash(email: str) -> str:
    h = email_hash(email)
    if not h:
        raise HTTPException(400, "email inválido")
    return h


@router.get("/{pixel_id}/subject", summary="Prévia: o que existe de um titular (por email)")
def preview_subject(pixel_id: str, email: str):
    cid = _resolve(pixel_id)
    if not cid:
        raise HTTPException(404, "client not found")
    sb = get_supabase()
    h = _subject_hash(email)
    orders = (sb.table("orders").select("id", count="exact", head=True)
              .eq("client_id", cid).eq("email_hash", h).execute()).count or 0
    visitors = (sb.table("visitors").select("id", count="exact", head=True)
                .eq("client_id", cid).eq("email_hash", h).execute()).count or 0
    cookies = (sb.table("attribution_cookies").select("id", count="exact", head=True)
               .eq("client_id", cid).eq("email_hash", h).execute()).count or 0
    matches = (sb.table("tracking_order_matches").select("id", count="exact", head=True)
               .eq("client_id", cid).eq("email_hash", h).execute()).count or 0
    return {"email": email.strip().lower(), "orders": orders, "visitors": visitors,
            "attribution_cookies": cookies, "tracking_order_matches": matches}


@router.post("/{pixel_id}/forget", summary="Apaga/anonimiza os dados pessoais de um titular (LGPD)")
def forget_subject(pixel_id: str, body: ForgetBody):
    cid = _resolve(pixel_id)
    if not cid:
        raise HTTPException(404, "client not found")
    h = _subject_hash(body.email or "")
    sb = get_supabase()

    # Cookies da pessoa (pra anonimizar os eventos/identidades depois)
    cookies: set[str] = set()
    for tbl, col in (("visitors", "visitor_id"),
                     ("attribution_cookies", "visitor_cookie_id"),
                     ("tracking_order_matches", "visitor_cookie_id")):
        rows = (sb.table(tbl).select(col).eq("client_id", cid).eq("email_hash", h).execute()).data or []
        cookies |= {r[col] for r in rows if r.get(col)}

    # 1) Pedidos — anonimiza PII, mantém o registro financeiro
    orders_anon = _count(
        sb.table("orders").update(_ORDER_PII_NULL).eq("client_id", cid).eq("email_hash", h).execute()
    )
    # 2) Visitantes (legado) — anonimiza
    visitors_anon = _count(
        sb.table("visitors").update(_VISITOR_PII_NULL).eq("client_id", cid).eq("email_hash", h).execute()
    )
    # 3) Ligação pedido→visita — remove o hash, mantém a atribuição agregada
    matches_anon = _count(
        sb.table("tracking_order_matches").update({"email_hash": None, "gclid": None, "gbraid": None,
                                                   "wbraid": None, "fbclid": None, "fbc": None, "fbp": None})
        .eq("client_id", cid).eq("email_hash", h).execute()
    )
    # 4) Eventos brutos e identidades dos cookies da pessoa
    events_anon = 0
    identities_anon = 0
    cookie_list = list(cookies)
    for i in range(0, len(cookie_list), 200):
        chunk = cookie_list[i:i + 200]
        events_anon += _count(
            sb.table("tracking_events").update({"ip_hash": None, "user_agent": None})
            .eq("client_id", cid).in_("visitor_cookie_id", chunk).execute()
        )
        identities_anon += _count(
            sb.table("tracking_identities").update({
                "gclid": None, "gbraid": None, "wbraid": None, "fbc": None, "fbp": None,
                "ga_client_id": None, "ga_session_id": None,
                "last_ip_address": None, "last_user_agent": None,
            }).eq("client_id", cid).in_("visitor_cookie_id", chunk).execute()
        )
    # 5) Cookies de atribuição — apaga
    cookies_deleted = _count(
        sb.table("attribution_cookies").delete().eq("client_id", cid).eq("email_hash", h).execute()
    )

    logger.info("lgpd forget %s: orders=%s visitors=%s matches=%s events=%s identities=%s cookies=%s",
                pixel_id, orders_anon, visitors_anon, matches_anon, events_anon, identities_anon, cookies_deleted)
    return {
        "email": (body.email or "").strip().lower(),
        "orders_anonymized": orders_anon,
        "visitors_anonymized": visitors_anon,
        "tracking_order_matches_anonymized": matches_anon,
        "events_anonymized": events_anon,
        "tracking_identities_anonymized": identities_anon,
        "attribution_cookies_deleted": cookies_deleted,
    }
