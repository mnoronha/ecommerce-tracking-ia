"""
Integration health endpoints.

  GET /integrations/{pixel}/status        — read cached health (fast, no probe)
  POST /integrations/{pixel}/status       — re-probe live and return result
  POST /integrations/{pixel}/test/{name}  — probe a single platform on demand

Used by the dashboard health card and the onboarding wizard's
"Testar agora" buttons.
"""

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException

from ..config import settings
from ..database import get_supabase
from ..services import integrations_health

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/integrations", tags=["integrations"])

_PLATFORM_KEYS = {"meta", "google_ads", "ga4", "tiktok", "pinterest", "shopify"}


@router.get("/{pixel_id}/google/_test_conversion", summary="Diagnóstico — valida upload de conversão (validateOnly)")
async def google_test_conversion(pixel_id: str):
    """
    Temporário. Monta um uploadClickConversions REAL com validateOnly=true:
    o Google valida auth + developer token + conversion action + formato do
    payload (incluindo userIdentifiers/Enhanced Conversions) sem gravar
    conversão fantasma. É o teste de envio de ponta a ponta.
    """
    import hashlib
    import httpx
    from datetime import datetime, timezone
    from ..services.google_ads import _get_access_token, _sha256, _normalize_phone_e164

    sb = get_supabase()
    r = (
        sb.table("clients")
        .select("google_ads_customer_id, google_ads_refresh_token, google_ads_login_customer_id, "
                "google_ads_conversion_action_id")
        .eq("pixel_id", pixel_id).limit(1).execute()
    )
    if not (r and r.data):
        return {"error": "client not found"}
    c = r.data[0]
    action_id = c.get("google_ads_conversion_action_id")
    if not (c.get("google_ads_customer_id") and c.get("google_ads_refresh_token") and action_id):
        return {"error": "missing customer_id, refresh_token or conversion_action_id"}

    token = _get_access_token(
        settings.GOOGLE_ADS_OAUTH_CLIENT_ID,
        settings.GOOGLE_ADS_OAUTH_CLIENT_SECRET,
        c["google_ads_refresh_token"],
    )
    if not token:
        return {"error": "could not obtain access token"}

    clean_cid = c["google_ads_customer_id"].replace("-", "").replace(" ", "")
    mcc = c.get("google_ads_login_customer_id") or settings.GOOGLE_ADS_MANAGER_ID
    headers = {
        "Authorization":   f"Bearer {token}",
        "developer-token": settings.GOOGLE_ADS_DEVELOPER_TOKEN,
        "Content-Type":    "application/json",
    }
    if mcc:
        headers["login-customer-id"] = mcc.replace("-", "")

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S+00:00")
    # Enhanced Conversion test payload — hashed sample identifiers, no gclid
    conversion = {
        "conversionAction":   f"customers/{clean_cid}/conversionActions/{action_id}",
        "conversionDateTime": now,
        "conversionValue":    1.0,
        "currencyCode":       "BRL",
        "orderId":            "healthcheck-" + hashlib.sha256(now.encode()).hexdigest()[:12],
        "userIdentifiers": [
            {"hashedEmail":       _sha256("healthcheck@example.com")},
            {"hashedPhoneNumber": _sha256(_normalize_phone_e164("11999999999"))},
        ],
    }
    payload = {"conversions": [conversion], "partialFailure": True, "validateOnly": True}

    out: dict = {"login_customer_id_effective": mcc, "conversion_action_id": action_id}
    for version in ("v21", "v20"):
        try:
            resp = httpx.post(
                f"https://googleads.googleapis.com/{version}/customers/{clean_cid}:uploadClickConversions",
                headers=headers, json=payload, timeout=15,
            )
            out["version"] = version
            out["status"]  = resp.status_code
            out["body"]    = resp.text[:600]
            if resp.status_code != 404:
                break
        except Exception as exc:
            out["error"] = str(exc)[:300]
            break
    out["interpretation"] = (
        "OK — Google aceitou o payload (Enhanced Conversion válida)" if out.get("status") == 200
        else " erro — ver body"
    )
    return out


@router.get("/{pixel_id}/google/_introspect", summary="Diagnóstico Google Ads — token + URL + resposta")
async def google_introspect(pixel_id: str):
    """
    Temporário. Mostra: se conseguimos um access_token a partir do refresh,
    a URL exata que tentamos, e o status+corpo (truncado) de cada versão.
    Isola "refresh inválido" de "URL/endpoint errado" de "developer token".
    """
    import httpx
    from ..services.google_ads import _get_access_token
    sb = get_supabase()
    r = (
        sb.table("clients")
        .select("google_ads_customer_id, google_ads_refresh_token, google_ads_login_customer_id")
        .eq("pixel_id", pixel_id).limit(1).execute()
    )
    if not (r and r.data):
        return {"error": "client not found"}
    c = r.data[0]
    effective_mcc = c.get("google_ads_login_customer_id") or settings.GOOGLE_ADS_MANAGER_ID
    out: dict = {
        "customer_id_raw": c.get("google_ads_customer_id"),
        "dev_token_set":   bool(settings.GOOGLE_ADS_DEVELOPER_TOKEN),
        "oauth_id_set":    bool(settings.GOOGLE_ADS_OAUTH_CLIENT_ID),
        "oauth_secret_set": bool(settings.GOOGLE_ADS_OAUTH_CLIENT_SECRET),
        "login_customer_id_from_db": c.get("google_ads_login_customer_id"),
        "manager_id_effective":      effective_mcc or None,
    }
    token = _get_access_token(
        settings.GOOGLE_ADS_OAUTH_CLIENT_ID,
        settings.GOOGLE_ADS_OAUTH_CLIENT_SECRET,
        c.get("google_ads_refresh_token") or "",
    )
    out["access_token_obtained"] = bool(token)
    if not token:
        return out

    clean_cid = (c.get("google_ads_customer_id") or "").replace("-", "").replace(" ", "")
    headers = {
        "Authorization":   f"Bearer {token}",
        "developer-token": settings.GOOGLE_ADS_DEVELOPER_TOKEN,
        "Content-Type":    "application/json",
    }
    if effective_mcc:
        headers["login-customer-id"] = effective_mcc.replace("-", "")
    attempts = []
    for version in ("v21", "v20", "v19", "v18"):
        url = f"https://googleads.googleapis.com/{version}/customers/{clean_cid}/googleAds:search"
        try:
            resp = httpx.post(url, headers=headers,
                              json={"query": "SELECT customer.id FROM customer LIMIT 1"}, timeout=10)
            attempts.append({"version": version, "status": resp.status_code,
                             "requested_url": str(resp.request.url),
                             "body": resp.text[:600]})
            if resp.status_code != 404:
                break
        except Exception as exc:
            attempts.append({"version": version, "error": str(exc)[:200]})

    # Also probe the token-only endpoint to confirm the access token is live
    try:
        ti = httpx.get("https://oauth2.googleapis.com/tokeninfo",
                       params={"access_token": token}, timeout=10)
        out["tokeninfo"] = {"status": ti.status_code, "body": ti.text[:250]}
    except Exception as exc:
        out["tokeninfo_error"] = str(exc)[:200]

    out["attempts"] = attempts
    return out


@router.get("/{pixel_id}/meta/_introspect", summary="Diagnóstico Meta — quem é o dono do token")
async def meta_introspect(pixel_id: str):
    """
    Temporário. Pega o meta_access_token do DB do cliente e chama dois
    endpoints Meta sem usar APP_SECRET:

      - GET /me?access_token=...      → confirma se o token é válido por si só
      - GET /me/permissions?access_token=...  → lista permissions concedidas

    Isto separa "token quebrado" de "app desativado". O check normal
    /test/meta usa APP_ID|APP_SECRET, que falha se a *conta de developer*
    do app não está ativa — independente do token.
    """
    import httpx
    sb = get_supabase()
    r = (
        sb.table("clients")
        .select("meta_access_token, meta_pixel_id, meta_ad_account_id")
        .eq("pixel_id", pixel_id)
        .limit(1)
        .execute()
    )
    if not (r and r.data and r.data[0].get("meta_access_token")):
        return {"error": "no token in DB"}
    token = r.data[0]["meta_access_token"]
    out: dict = {}
    try:
        me = httpx.get("https://graph.facebook.com/v19.0/me",
                       params={"access_token": token, "fields": "id,name"}, timeout=10).json()
        out["me"] = me
    except Exception as exc:
        out["me_error"] = str(exc)
    try:
        perms = httpx.get("https://graph.facebook.com/v19.0/me/permissions",
                          params={"access_token": token}, timeout=10).json()
        out["permissions"] = perms
    except Exception as exc:
        out["permissions_error"] = str(exc)
    return out


@router.get("/_envcheck", summary="Diagnóstico — quais env vars o runtime enxerga (sem valores)")
async def env_check():
    """
    Temporário. Devolve {var_name: 'set'|'missing', length: N} pra cada env var
    de integração que o backend exige. Nunca expõe valores. Apagar depois
    de resolver o gap do Google Ads.

    Também lista os NOMES (não valores) de toda env var contendo 'GOOGLE',
    'META', 'TIKTOK' ou 'PINTEREST', pra detectar typo no Railway.
    """
    import os
    def state(name: str) -> dict:
        val = getattr(settings, name, "") or ""
        return {
            "status":  "set" if val else "missing",
            "length":  len(val),
            "preview": (val[:4] + "…" + val[-2:]) if len(val) > 8 else ("***" if val else ""),
        }
    keywords = ("GOOGLE", "META", "TIKTOK", "PINTEREST", "GA4_")
    all_matching = sorted([k for k in os.environ.keys() if any(w in k.upper() for w in keywords)])
    return {
        "expected": {
            "GOOGLE_ADS_DEVELOPER_TOKEN":     state("GOOGLE_ADS_DEVELOPER_TOKEN"),
            "GOOGLE_ADS_OAUTH_CLIENT_ID":     state("GOOGLE_ADS_OAUTH_CLIENT_ID"),
            "GOOGLE_ADS_OAUTH_CLIENT_SECRET": state("GOOGLE_ADS_OAUTH_CLIENT_SECRET"),
            "GOOGLE_ADS_REFRESH_TOKEN":       state("GOOGLE_ADS_REFRESH_TOKEN"),
            "GOOGLE_ADS_MANAGER_ID":          state("GOOGLE_ADS_MANAGER_ID"),
            "META_APP_ID":                    state("META_APP_ID"),
            "META_APP_SECRET":                state("META_APP_SECRET"),
        },
        # Names only — never values. Lets us catch typos / wrong service.
        "all_env_var_names_matching": all_matching,
    }


def _load_client(pixel_id: str) -> dict:
    sb = get_supabase()
    r = (
        sb.table("clients")
        .select(
            "id, pixel_id, meta_access_token, meta_pixel_id, meta_token_health, meta_token_expires_at, "
            "google_ads_customer_id, google_ads_refresh_token, google_ads_login_customer_id, "
            "google_ads_token_health, google_ads_token_checked_at, google_ads_token_error, "
            "ga4_measurement_id, ga4_api_secret, ga4_health, ga4_checked_at, ga4_error, "
            "tiktok_access_token, tiktok_token_health, tiktok_token_checked_at, tiktok_token_error, "
            "pinterest_ad_account_id, pinterest_access_token, pinterest_tag_id, "
            "pinterest_token_health, pinterest_token_checked_at, pinterest_token_error, "
            "shopify_store_domain, shopify_domain, shopify_access_token, "
            "shopify_health, shopify_checked_at, shopify_error"
        )
        .eq("pixel_id", pixel_id)
        .eq("is_active", True)
        .limit(1)
        .execute()
    )
    if not (r and r.data):
        raise HTTPException(status_code=404, detail="Client not found")
    return r.data[0]


def _cached_view(client: dict) -> dict:
    """Return the persisted health snapshot — no live API calls."""
    def block(status, checked_at, error, configured):
        return {
            "status":       status or "unknown",
            "last_checked": checked_at,
            "error":        error,
            "configured":   bool(configured),
        }
    return {
        "pixel_id": client.get("pixel_id"),
        "meta": block(
            client.get("meta_token_health"),
            client.get("meta_token_expires_at"),
            None,
            client.get("meta_access_token"),
        ),
        "google_ads": block(
            client.get("google_ads_token_health"),
            client.get("google_ads_token_checked_at"),
            client.get("google_ads_token_error"),
            client.get("google_ads_customer_id") and client.get("google_ads_refresh_token"),
        ),
        "ga4": block(
            client.get("ga4_health"),
            client.get("ga4_checked_at"),
            client.get("ga4_error"),
            client.get("ga4_measurement_id") and client.get("ga4_api_secret"),
        ),
        "tiktok": block(
            client.get("tiktok_token_health"),
            client.get("tiktok_token_checked_at"),
            client.get("tiktok_token_error"),
            client.get("tiktok_access_token"),
        ),
        "pinterest": block(
            client.get("pinterest_token_health"),
            client.get("pinterest_token_checked_at"),
            client.get("pinterest_token_error"),
            client.get("pinterest_access_token") and client.get("pinterest_ad_account_id"),
        ),
        "shopify": block(
            client.get("shopify_health"),
            client.get("shopify_checked_at"),
            client.get("shopify_error"),
            client.get("shopify_access_token") and (client.get("shopify_store_domain") or client.get("shopify_domain")),
        ),
    }


@router.get("/{pixel_id}/status", summary="Cached integration health")
async def get_status(pixel_id: str):
    """Fast read of the last-known health, persisted by the hourly cron."""
    client = _load_client(pixel_id)
    return _cached_view(client)


@router.post("/{pixel_id}/status", summary="Re-probe all integrations now")
async def probe_status(pixel_id: str):
    """Live probe of every connected integration. ~5-10s response time."""
    client = _load_client(pixel_id)
    results = integrations_health.check_all(client, persist=True)
    return {"pixel_id": pixel_id, **results}


@router.post("/{pixel_id}/test/{platform}", summary="Re-probe a single integration")
async def probe_one(pixel_id: str, platform: str):
    """Wizard "Testar agora" button — single-platform live probe."""
    if platform not in _PLATFORM_KEYS:
        raise HTTPException(status_code=400, detail=f"Unknown platform '{platform}'")
    client = _load_client(pixel_id)

    if platform == "meta":
        result = integrations_health.check_meta(client.get("meta_access_token"))
    elif platform == "google_ads":
        result = integrations_health.check_google_ads(
            client.get("google_ads_customer_id"),
            client.get("google_ads_refresh_token"),
            client.get("google_ads_login_customer_id"),
        )
    elif platform == "ga4":
        result = integrations_health.check_ga4(
            client.get("ga4_measurement_id"),
            client.get("ga4_api_secret"),
        )
    elif platform == "tiktok":
        result = integrations_health.check_tiktok(client.get("tiktok_access_token"))
    elif platform == "pinterest":
        result = integrations_health.check_pinterest(
            client.get("pinterest_ad_account_id"),
            client.get("pinterest_access_token"),
        )
    elif platform == "shopify":
        result = integrations_health.check_shopify(
            client.get("shopify_store_domain") or client.get("shopify_domain"),
            client.get("shopify_access_token"),
        )
    else:
        raise HTTPException(status_code=400, detail=f"Unsupported platform '{platform}'")

    # Persist single-platform result. Reuse check_all paths by selectively
    # writing only the matching health/error/checked_at columns.
    sb = get_supabase()
    from datetime import datetime, timezone
    now_iso = datetime.now(timezone.utc).isoformat()
    col_map = {
        "google_ads": ("google_ads_token_health", "google_ads_token_checked_at", "google_ads_token_error"),
        "ga4":        ("ga4_health",              "ga4_checked_at",              "ga4_error"),
        "tiktok":     ("tiktok_token_health",     "tiktok_token_checked_at",     "tiktok_token_error"),
        "pinterest":  ("pinterest_token_health",  "pinterest_token_checked_at",  "pinterest_token_error"),
        "shopify":    ("shopify_health",          "shopify_checked_at",          "shopify_error"),
    }
    if platform in col_map:
        h, c, e = col_map[platform]
        try:
            sb.table("clients").update({
                h: result["status"],
                c: now_iso,
                e: result.get("error") or None,
            }).eq("id", client["id"]).execute()
        except Exception as exc:
            logger.warning("probe_one persist failed: %s", exc)
    elif platform == "meta":
        try:
            sb.table("clients").update({
                "meta_token_health": result["status"],
            }).eq("id", client["id"]).execute()
        except Exception as exc:
            logger.warning("probe_one persist failed: %s", exc)

    return {"platform": platform, **result, "last_checked": now_iso}
