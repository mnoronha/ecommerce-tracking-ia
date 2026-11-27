"""
Portal user management — cria/atualiza usuários Supabase Auth para acesso ao portal do cliente.

POST   /portal/users/{pixel_id}            — cria usuário (email + senha) e concede acesso
PATCH  /portal/users/{pixel_id}/{email}/password — redefine senha
DELETE /portal/users/{pixel_id}/{email}    — revoga acesso (remove client_users, mantém auth)
GET    /portal/users/{pixel_id}            — lista usuários com acesso
"""

import logging
import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..agency_api.v1.auth import AuthContext, SCOPE_ADMIN_ONLY
from ..config import settings
from ..core.pii import email_hash
from ..core.security_audit import record_admin_audit
from ..database import get_supabase

logger = logging.getLogger(__name__)
router = APIRouter(
    prefix="/portal/users",
    tags=["portal"],
    dependencies=[Depends(SCOPE_ADMIN_ONLY)],
)

_AUTH_ADMIN_URL = ""  # preenchido dinamicamente via settings.SUPABASE_URL


def _auth_headers() -> dict:
    return {
        "Authorization": f"Bearer {settings.SUPABASE_SERVICE_KEY}",
        "apikey": settings.SUPABASE_SERVICE_KEY,
        "Content-Type": "application/json",
    }


def _admin_url(path: str) -> str:
    base = (settings.SUPABASE_URL or "").rstrip("/")
    return f"{base}/auth/v1/admin{path}"


def _get_pixel(pixel_id: str) -> dict:
    sb = get_supabase()
    row = sb.table("clients").select("id, name").eq("pixel_id", pixel_id).eq("is_active", True).maybe_single().execute()
    if not (row and row.data):
        raise HTTPException(404, f"Cliente não encontrado: {pixel_id}")
    return row.data


class CreateUserBody(BaseModel):
    email: str
    password: str


class PasswordBody(BaseModel):
    password: str


@router.get("/{pixel_id}")
def list_portal_users(pixel_id: str):
    _get_pixel(pixel_id)
    sb = get_supabase()
    rows = sb.table("client_users").select("id, email, role, created_at").eq("pixel_id", pixel_id).order("created_at").execute()
    return rows.data or []


@router.post("/{pixel_id}", status_code=201)
def create_portal_user(
    pixel_id: str,
    body: CreateUserBody,
    auth: AuthContext = Depends(SCOPE_ADMIN_ONLY),
):
    _get_pixel(pixel_id)
    email = body.email.strip().lower()
    password = body.password

    if len(password) < 6:
        raise HTTPException(400, "Senha deve ter pelo menos 6 caracteres")

    # 1. Criar/atualizar usuário no Supabase Auth
    resp = httpx.post(
        _admin_url("/users"),
        headers=_auth_headers(),
        json={
            "email": email,
            "password": password,
            "email_confirm": True,  # já confirma, sem precisar de link
        },
        timeout=15.0,
    )

    if resp.status_code == 422:
        # Usuário já existe — atualiza a senha somente se não for membro da agência
        data = resp.json()
        existing_msg = (data.get("msg") or data.get("message") or "").lower()
        if "already" in existing_msg or "exists" in existing_msg:
            list_resp = httpx.get(
                _admin_url("/users"),
                headers=_auth_headers(),
                params={"filter": f"email={email}"},
                timeout=15.0,
            )
            users = (list_resp.json().get("users") or []) if list_resp.status_code == 200 else []
            user_id = next((u["id"] for u in users if u.get("email") == email), None)
            if user_id:
                # Guard: never overwrite password of an agency_members account via portal flow
                sb_check = get_supabase()
                agency_row = (
                    sb_check.table("agency_members")
                    .select("id")
                    .eq("user_id", user_id)
                    .limit(1)
                    .execute()
                )
                if agency_row and agency_row.data:
                    raise HTTPException(
                        403,
                        "Não é permitido alterar senha de membro da agência pelo fluxo de portal",
                    )
                patch_resp = httpx.put(
                    _admin_url(f"/users/{user_id}"),
                    headers=_auth_headers(),
                    json={"password": password},
                    timeout=15.0,
                )
                if patch_resp.status_code not in (200, 204):
                    raise HTTPException(502, f"Erro ao atualizar senha: {patch_resp.text[:200]}")
        else:
            raise HTTPException(502, f"Erro Supabase Auth: {resp.text[:200]}")
    elif resp.status_code not in (200, 201):
        raise HTTPException(502, f"Erro Supabase Auth: {resp.text[:200]}")

    # 2. Garantir linha em client_users (upsert)
    sb = get_supabase()
    sb.table("client_users").upsert(
        {"email": email, "pixel_id": pixel_id, "role": "viewer"},
        on_conflict="email,pixel_id",
    ).execute()

    record_admin_audit(
        "PORTAL_USER_CREATE",
        pixel_id,
        auth=auth,
        details={"email_hash": email_hash(email), "role": "viewer"},
    )
    return {"ok": True, "email": email, "pixel_id": pixel_id}


@router.patch("/{pixel_id}/{email}/password")
def reset_portal_password(
    pixel_id: str,
    email: str,
    body: PasswordBody,
    auth: AuthContext = Depends(SCOPE_ADMIN_ONLY),
):
    _get_pixel(pixel_id)
    email = email.strip().lower()

    if len(body.password) < 6:
        raise HTTPException(400, "Senha deve ter pelo menos 6 caracteres")

    # Guard: email must belong to client_users for this pixel_id
    sb = get_supabase()
    membership = (
        sb.table("client_users")
        .select("id")
        .eq("pixel_id", pixel_id)
        .eq("email", email)
        .limit(1)
        .execute()
    )
    if not (membership and membership.data):
        raise HTTPException(404, "Usuário não vinculado a este portal")

    # Busca user_id
    list_resp = httpx.get(
        _admin_url("/users"),
        headers=_auth_headers(),
        params={"filter": f"email={email}"},
        timeout=15.0,
    )
    if list_resp.status_code != 200:
        raise HTTPException(502, "Erro ao buscar usuário")

    users = list_resp.json().get("users") or []
    user_id = next((u["id"] for u in users if u.get("email") == email), None)
    if not user_id:
        raise HTTPException(404, "Usuário não encontrado no Supabase Auth")

    # Guard: never overwrite password of an agency_members account via portal flow
    agency_row = (
        sb.table("agency_members")
        .select("id")
        .eq("user_id", user_id)
        .limit(1)
        .execute()
    )
    if agency_row and agency_row.data:
        raise HTTPException(
            403,
            "Não é permitido redefinir senha de membro da agência pelo fluxo de portal",
        )

    patch_resp = httpx.put(
        _admin_url(f"/users/{user_id}"),
        headers=_auth_headers(),
        json={"password": body.password},
        timeout=15.0,
    )
    if patch_resp.status_code not in (200, 204):
        raise HTTPException(502, f"Erro ao atualizar senha: {patch_resp.text[:200]}")

    record_admin_audit(
        "PORTAL_USER_PASSWORD_RESET",
        pixel_id,
        auth=auth,
        details={"email_hash": email_hash(email)},
    )
    return {"ok": True, "email": email}


@router.delete("/{pixel_id}/{email}")
def remove_portal_user(
    pixel_id: str,
    email: str,
    auth: AuthContext = Depends(SCOPE_ADMIN_ONLY),
):
    _get_pixel(pixel_id)
    email = email.strip().lower()
    sb = get_supabase()
    sb.table("client_users").delete().eq("pixel_id", pixel_id).eq("email", email).execute()
    record_admin_audit(
        "PORTAL_USER_REMOVE",
        pixel_id,
        auth=auth,
        details={"email_hash": email_hash(email)},
    )
    return {"ok": True, "email": email}
