"""
Portal user management — cria/atualiza usuários Supabase Auth para acesso ao portal do cliente.

POST   /portal/users/{pixel_id}            — cria usuário (email + senha) e concede acesso
PATCH  /portal/users/{pixel_id}/{email}/password — redefine senha
DELETE /portal/users/{pixel_id}/{email}    — revoga acesso (remove client_users, mantém auth)
GET    /portal/users/{pixel_id}            — lista usuários com acesso
"""

import logging
import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..config import settings
from ..database import get_supabase

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/portal/users", tags=["portal"])

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
async def list_portal_users(pixel_id: str):
    _get_pixel(pixel_id)
    sb = get_supabase()
    rows = sb.table("client_users").select("id, email, role, created_at").eq("pixel_id", pixel_id).order("created_at").execute()
    return rows.data or []


@router.post("/{pixel_id}", status_code=201)
async def create_portal_user(pixel_id: str, body: CreateUserBody):
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
        # Usuário já existe — atualiza a senha
        data = resp.json()
        existing_msg = (data.get("msg") or data.get("message") or "").lower()
        if "already" in existing_msg or "exists" in existing_msg:
            # Busca o user_id pelo email
            list_resp = httpx.get(
                _admin_url("/users"),
                headers=_auth_headers(),
                params={"filter": f"email={email}"},
                timeout=15.0,
            )
            users = (list_resp.json().get("users") or []) if list_resp.status_code == 200 else []
            user_id = next((u["id"] for u in users if u.get("email") == email), None)
            if user_id:
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

    return {"ok": True, "email": email, "pixel_id": pixel_id}


@router.patch("/{pixel_id}/{email}/password")
async def reset_portal_password(pixel_id: str, email: str, body: PasswordBody):
    _get_pixel(pixel_id)
    email = email.strip().lower()

    if len(body.password) < 6:
        raise HTTPException(400, "Senha deve ter pelo menos 6 caracteres")

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

    patch_resp = httpx.put(
        _admin_url(f"/users/{user_id}"),
        headers=_auth_headers(),
        json={"password": body.password},
        timeout=15.0,
    )
    if patch_resp.status_code not in (200, 204):
        raise HTTPException(502, f"Erro ao atualizar senha: {patch_resp.text[:200]}")

    return {"ok": True, "email": email}


@router.delete("/{pixel_id}/{email}")
async def remove_portal_user(pixel_id: str, email: str):
    _get_pixel(pixel_id)
    email = email.strip().lower()
    sb = get_supabase()
    sb.table("client_users").delete().eq("pixel_id", pixel_id).eq("email", email).execute()
    return {"ok": True, "email": email}
