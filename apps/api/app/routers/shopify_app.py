"""
Shopify app per client (Dev Dashboard, custom distribution) — OAuth install.

Since 2026-01-01 Shopify no longer lets stores create custom apps in the admin,
so there is no "reveal token once" button anymore. Each client store gets a Noro
app in the Dev Dashboard (custom distribution to that store). The merchant opens
the install link, Shopify sends them to the App URL below, we run the
authorization code grant and keep the offline Admin API token (non-expiring by
default) encrypted in `clients.shopify_access_token` — the column every Shopify
reader (sync, health, schema audit…) already uses.

Configure in the Dev Dashboard app version:
  App URL:        {PUBLIC_API_URL}/shopify/app/{pixel_id}/install
  Redirect URL:   {PUBLIC_API_URL}/shopify/app/{pixel_id}/callback
  Scopes:         read_orders, read_customers, read_products, read_inventory

The app's Client ID / Client secret are saved per client from the Platform
settings (PUT /setup/{pixel_id}/credentials → shopify_app_client_id /
shopify_app_client_secret; the secret is Fernet-encrypted).

Read-only by design: never request write scopes here.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import re
import secrets
import time
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse

from ..config import settings
from ..database import get_supabase
from ..services import crypto

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/shopify/app", tags=["shopify-app"])

SCOPES = "read_orders,read_customers,read_products,read_inventory"
_STATE_TTL_S = 15 * 60
_SHOP_RE = re.compile(r"^[a-z0-9][a-z0-9\-]*\.myshopify\.com$")


# ── helpers (pure) ────────────────────────────────────────────────────────────


def normalize_shop(value: Optional[str]) -> str:
    v = (value or "").strip().lower()
    v = re.sub(r"^https?://", "", v).split("/")[0]
    return v


def valid_shop(shop: str) -> bool:
    return bool(_SHOP_RE.match(shop or ""))


def query_hmac_ok(params: list[tuple[str, str]], secret: str) -> bool:
    """Shopify OAuth redirect HMAC: drop `hmac` (and legacy `signature`), sort the
    rest as `key=value`, join with `&`, HMAC-SHA256 hex with the client secret."""
    given = next((v for k, v in params if k == "hmac"), "")
    if not given or not secret:
        return False
    msg = "&".join(sorted(f"{k}={v}" for k, v in params if k not in ("hmac", "signature")))
    digest = hmac.new(secret.encode(), msg.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(digest, given)


def make_state(pixel_id: str, secret: str, now: Optional[float] = None) -> str:
    ts = str(int(now if now is not None else time.time()))
    nonce = secrets.token_urlsafe(12)
    sig = hmac.new(secret.encode(), f"{pixel_id}.{ts}.{nonce}".encode(), hashlib.sha256).hexdigest()
    return f"{ts}.{nonce}.{sig}"


def state_ok(state: str, pixel_id: str, secret: str, now: Optional[float] = None) -> bool:
    try:
        ts, nonce, sig = (state or "").split(".", 2)
        age = (now if now is not None else time.time()) - int(ts)
    except ValueError:
        return False
    if age < 0 or age > _STATE_TTL_S:
        return False
    expected = hmac.new(secret.encode(), f"{pixel_id}.{ts}.{nonce}".encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, sig)


def _api_base() -> str:
    return (settings.PUBLIC_API_URL or "").rstrip("/")


def redirect_uri(pixel_id: str) -> str:
    return f"{_api_base()}/shopify/app/{pixel_id}/callback"


def _platform_redirect(pixel_id: str, status: str) -> RedirectResponse:
    base = (settings.PLATFORM_URL or "").rstrip("/")
    return RedirectResponse(f"{base}/clients/{pixel_id}/settings?{urlencode({'shopify': status})}", status_code=302)


# ── DB ────────────────────────────────────────────────────────────────────────


def _load_client(pixel_id: str) -> dict:
    res = (
        get_supabase().table("clients")
        .select("id, pixel_id, shopify_domain, shopify_app_client_id, shopify_app_client_secret")
        .eq("pixel_id", pixel_id)
        .limit(1)
        .execute()
    )
    rows = res.data or []
    if not rows:
        raise HTTPException(404, "cliente não encontrado")
    row = rows[0]
    row["shopify_app_client_secret"] = crypto.decrypt_secret(row.get("shopify_app_client_secret"))
    if not row.get("shopify_app_client_id") or not row.get("shopify_app_client_secret"):
        raise HTTPException(409, "App Shopify não configurado para este cliente (Client ID / Client secret)")
    return row


def _check_shop(row: dict, shop: str, signed: bool) -> None:
    """`shop` must be a myshopify domain. When the request is signed by Shopify
    (valid HMAC with this client's own app secret), Shopify is authoritative:
    the app is custom-distributed to one store, so a different value in
    clients.shopify_domain is just stale/handle data — logged, not fatal."""
    if not valid_shop(shop):
        raise HTTPException(400, "parâmetro shop inválido")
    expected = normalize_shop(row.get("shopify_domain"))
    if expected and expected.endswith(".myshopify.com") and expected != shop:
        if signed:
            logger.warning("shopify app: shop %s differs from stored shopify_domain %s for %s — using Shopify's",
                           shop, expected, row.get("pixel_id"))
            return
        raise HTTPException(403, f"loja não corresponde ao cliente (Shopify: {shop}; cadastro: {expected})")


# ── routes ────────────────────────────────────────────────────────────────────


@router.get("/{pixel_id}/install", summary="App URL — inicia a instalação (OAuth) do app Shopify do cliente")
def install(pixel_id: str, request: Request):
    params = list(request.query_params.multi_items())
    shop = normalize_shop(request.query_params.get("shop"))
    row = _load_client(pixel_id)
    secret = row["shopify_app_client_secret"]
    # Shopify signs the launch request; a manual visit without hmac is allowed
    # (the callback is always verified), a wrong hmac is not.
    signed = bool(request.query_params.get("hmac"))
    if signed and not query_hmac_ok(params, secret):
        raise HTTPException(401, "hmac inválido")
    _check_shop(row, shop, signed)
    q = urlencode({
        "client_id":    row["shopify_app_client_id"],
        "scope":        SCOPES,
        "redirect_uri": redirect_uri(pixel_id),
        "state":        make_state(pixel_id, secret),
    })
    return RedirectResponse(f"https://{shop}/admin/oauth/authorize?{q}", status_code=302)


@router.get("/{pixel_id}/callback", summary="Redirect URL — troca o code pelo token offline e grava cifrado")
def callback(pixel_id: str, request: Request):
    params = list(request.query_params.multi_items())
    qp = request.query_params
    shop = normalize_shop(qp.get("shop"))
    row = _load_client(pixel_id)
    secret = row["shopify_app_client_secret"]

    if not query_hmac_ok(params, secret):
        raise HTTPException(401, "hmac inválido")
    _check_shop(row, shop, signed=True)
    if not state_ok(qp.get("state") or "", pixel_id, secret):
        raise HTTPException(401, "state inválido ou expirado — reinicie a instalação")
    code = qp.get("code")
    if not code:
        raise HTTPException(400, "code ausente")

    try:
        r = httpx.post(
            f"https://{shop}/admin/oauth/access_token",
            json={"client_id": row["shopify_app_client_id"], "client_secret": secret, "code": code},
            timeout=15,
        )
        r.raise_for_status()
        body = r.json()
    except Exception as exc:
        logger.warning("shopify oauth: token exchange failed for %s: %s", pixel_id, exc)
        return _platform_redirect(pixel_id, "error")

    token = body.get("access_token")
    if not token:
        logger.warning("shopify oauth: no access_token for %s (keys=%s)", pixel_id, list(body))
        return _platform_redirect(pixel_id, "error")
    if body.get("expires_in"):
        # Custom-distribution apps get non-expiring offline tokens by default
        # (expiring=0). If that ever changes we must add refresh — make it loud.
        logger.error("shopify oauth: token for %s expires in %ss — refresh not implemented",
                     pixel_id, body.get("expires_in"))

    patch = {
        "shopify_access_token":     crypto.encrypt_secret(token),
        "shopify_app_scopes":       body.get("scope") or "",
        "shopify_app_installed_at": datetime.now(timezone.utc).isoformat(),
    }
    if normalize_shop(row.get("shopify_domain")) != shop:
        # The installing store's permanent myshopify domain is authoritative
        # (sync/health use shopify_domain for Admin API calls).
        patch["shopify_domain"] = shop
    get_supabase().table("clients").update(patch).eq("id", row["id"]).execute()
    logger.info("shopify oauth: %s connected (%s) scopes=%s", pixel_id, shop, patch["shopify_app_scopes"])
    return _platform_redirect(pixel_id, "connected")


@router.get("/{pixel_id}/urls", summary="URLs para configurar no Dev Dashboard (sem segredos)")
def app_urls(pixel_id: str):
    return {
        "app_url":      f"{_api_base()}/shopify/app/{pixel_id}/install",
        "redirect_url": redirect_uri(pixel_id),
        "scopes":       SCOPES,
    }
