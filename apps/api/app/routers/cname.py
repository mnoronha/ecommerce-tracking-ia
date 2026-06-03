"""
CNAME first-party tracking — endpoints for setup and verification.

Concept: instead of having merchants point their pixel to
`https://ecommerce-tracking-ia-production.up.railway.app`, they use a
first-party subdomain like `https://track.lojadocliente.com.br` that CNAME-s
to our infrastructure.

Benefits:
  - Cookies are first-party from the merchant's domain → not blocked by
    Safari ITP, Firefox ETP, iOS 14+ App Tracking Transparency.
  - Match rate for Meta CAPI / Google Ads jumps from ~60% to 90%+.
  - Anti-adblock — third-party tracker domains are easily blocked.

Required infrastructure (set up by Pareto Plus, not the client):
  Option A — Cloudflare for SaaS (recommended):
    - Add a fallback origin pointing to our Railway service
    - Each client subdomain auto-issues SSL and routes to origin
    - Customers add CNAME `track.cliente.com → tracking.pareto.plus`
  Option B — Railway custom domains (manual, doesn't scale):
    - Add each customer subdomain in Railway dashboard
    - Railway auto-issues SSL via Let's Encrypt
    - Limit ~50 domains per service

Verification: HTTPS GET https://{cname}/_verify/{secret} and expect that
exact secret back. The endpoint /_verify/{secret} below returns whatever
secret was passed — that proves the host header routes to us.
"""

import logging
import secrets
import re
from typing import Optional

import httpx
from fastapi import APIRouter, HTTPException, Request

from ..database import get_supabase
from ..services import crypto

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/setup", tags=["setup"])

_HOSTNAME_RE = re.compile(r"^[a-zA-Z0-9]([a-zA-Z0-9\-\.]{0,253}[a-zA-Z0-9])?$")


@router.post("/cname/{pixel_id}/init")
async def cname_init(pixel_id: str, body: dict):
    """
    Step 1: store the desired CNAME and generate a verification secret.

    Returns the secret + instructions for the merchant to add the DNS record.
    """
    cname = (body.get("cname") or "").strip().lower()
    if not cname or not _HOSTNAME_RE.match(cname):
        raise HTTPException(400, "Invalid CNAME hostname")

    sb = get_supabase()
    found = (
        sb.table("clients")
        .select("id")
        .eq("pixel_id", pixel_id)
        .eq("is_active", True)
        .maybe_single()
        .execute()
    )
    if not (found and found.data):
        raise HTTPException(404, "Client not found")

    secret = secrets.token_urlsafe(24)
    sb.table("clients").update({
        "tracking_cname":          cname,
        "tracking_cname_secret":   crypto.encrypt_secret(secret),
        "tracking_cname_verified": False,
    }).eq("id", found.data["id"]).execute()

    return {
        "cname":  cname,
        "secret": secret,
        "instructions": {
            "step_1_dns": (
                f"No painel de DNS do domínio do cliente, criar registro CNAME: "
                f"{cname} → tracking.pareto.plus"
            ),
            "step_2_ssl": (
                "Aguardar SSL provisionar (~5-15min após DNS propagar)."
            ),
            "step_3_verify": (
                f"Clicar em 'Verificar' — o sistema testa "
                f"https://{cname}/_verify/{secret[:8]}…"
            ),
        },
    }


@router.post("/cname/{pixel_id}/verify")
async def cname_verify(pixel_id: str):
    """
    Step 2: probe https://{cname}/_verify/{secret} and confirm the response
    matches the stored secret. Marks tracking_cname_verified=true on success.
    """
    sb = get_supabase()
    row = (
        sb.table("clients")
        .select("id, tracking_cname, tracking_cname_secret")
        .eq("pixel_id", pixel_id)
        .maybe_single()
        .execute()
    )
    if not (row and row.data):
        raise HTTPException(404, "Client not found")

    cname  = row.data.get("tracking_cname")
    secret = row.data.get("tracking_cname_secret")
    if not cname or not secret:
        raise HTTPException(400, "CNAME not initialized — call /init first")

    url = f"https://{cname}/_verify/{secret}"
    try:
        resp = httpx.get(url, timeout=10.0, follow_redirects=False)
    except httpx.ConnectError:
        return {"verified": False, "error": "DNS_OR_SSL_NOT_READY",
                "hint": "DNS pode não ter propagado ainda, ou SSL ainda não foi provisionado."}
    except httpx.TimeoutException:
        return {"verified": False, "error": "TIMEOUT",
                "hint": "O hostname não respondeu — verifique se o CNAME aponta para nossa infraestrutura."}
    except Exception as exc:
        logger.warning("cname verify exception: %s", exc)
        return {"verified": False, "error": "UNKNOWN", "hint": str(exc)[:200]}

    if resp.status_code != 200:
        return {"verified": False, "error": f"HTTP_{resp.status_code}",
                "hint": "O endpoint respondeu mas com status incorreto — verifique se a rota é nossa."}

    body = resp.text.strip()
    if body != secret:
        return {"verified": False, "error": "SECRET_MISMATCH",
                "hint": "O endpoint respondeu mas com conteúdo diferente — DNS aponta para outra origem."}

    sb.table("clients").update({
        "tracking_cname_verified": True,
    }).eq("id", row.data["id"]).execute()

    return {"verified": True, "cname": cname}


# Echo endpoint registered at app root (not under /setup) — see main.py
# When called via the customer's CNAME, it proves the routing works:
#   GET https://track.lojadocliente.com.br/_verify/{secret}
#   → returns {secret} as plain text, confirming DNS routes to us
