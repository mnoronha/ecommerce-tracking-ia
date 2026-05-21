"""
Setup endpoints — automate Shopify onboarding.

  POST /setup/shopify/{pixel_id}/webhooks
    Registers all required Shopify webhooks.

  POST /setup/shopify/{pixel_id}/install   ← NEW
    Full one-shot onboarding: registers all webhooks AND installs the
    tracking pixel via Shopify ScriptTag API (no theme editing needed).
    Requires scopes: read_orders + write_webhooks + read_script_tags + write_script_tags

Docs:
  Webhooks:    https://shopify.dev/docs/api/admin-rest/2024-10/resources/webhook
  ScriptTags:  https://shopify.dev/docs/api/admin-rest/2024-10/resources/scripttag
"""

import logging
from typing import Optional

import httpx
from fastapi import APIRouter, HTTPException

from ..config import settings
from ..database import get_supabase

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/setup", tags=["setup"])

_SHOPIFY_API_VERSION = "2024-10"

# Topics our system processes — match ShopifyAdapter.TOPIC_MAP
_REQUIRED_TOPICS = [
    "orders/create",
    "orders/updated",
    "orders/paid",
    "orders/cancelled",
    "orders/fulfilled",
    "carts/create",
    "carts/update",
    "checkouts/create",      # InitiateCheckout server-side backup when pixel is blocked
    "customers/create",
    "refunds/create",        # for handling refunds (sends negative Purchase to Meta)
]


def _api_base() -> str:
    """Public URL of this API — webhooks need to point here."""
    # Railway provides this; fall back to local for dev
    return getattr(settings, "API_PUBLIC_URL", "") or "https://ecommerce-tracking-ia-production.up.railway.app"


def _shopify_headers(access_token: str) -> dict:
    return {
        "X-Shopify-Access-Token": access_token,
        "Content-Type":           "application/json",
        "Accept":                 "application/json",
    }


def _list_webhooks(shop_domain: str, access_token: str) -> list[dict]:
    """Return all webhooks currently configured on the shop."""
    url = f"https://{shop_domain}/admin/api/{_SHOPIFY_API_VERSION}/webhooks.json"
    try:
        resp = httpx.get(url, headers=_shopify_headers(access_token), timeout=15.0, params={"limit": 250})
        if resp.status_code == 200:
            return resp.json().get("webhooks", []) or []
        logger.warning("Shopify list webhooks HTTP %s: %s", resp.status_code, resp.text[:300])
    except Exception as exc:
        logger.error("_list_webhooks exception: %s", exc)
    return []


def _create_webhook(shop_domain: str, access_token: str, topic: str, address: str) -> Optional[dict]:
    """Create a webhook subscription. Returns the webhook dict or None on failure."""
    url = f"https://{shop_domain}/admin/api/{_SHOPIFY_API_VERSION}/webhooks.json"
    payload = {"webhook": {"topic": topic, "address": address, "format": "json"}}
    try:
        resp = httpx.post(url, headers=_shopify_headers(access_token), json=payload, timeout=15.0)
        if resp.status_code in (201, 200):
            return resp.json().get("webhook")
        # 422 with "address has already been taken" means it exists — not an error
        if resp.status_code == 422 and "already been taken" in resp.text.lower():
            logger.debug("Webhook %s already exists for %s", topic, shop_domain)
            return {"status": "exists", "topic": topic}
        logger.warning("Shopify create webhook %s HTTP %s: %s", topic, resp.status_code, resp.text[:300])
    except Exception as exc:
        logger.error("_create_webhook %s exception: %s", topic, exc)
    return None


# ── ScriptTag helpers ─────────────────────────────────────────────────────────

def _list_script_tags(shop_domain: str, access_token: str) -> list[dict]:
    url = f"https://{shop_domain}/admin/api/{_SHOPIFY_API_VERSION}/script_tags.json"
    try:
        resp = httpx.get(url, headers=_shopify_headers(access_token), timeout=15.0, params={"limit": 250})
        if resp.status_code == 200:
            return resp.json().get("script_tags", []) or []
        logger.warning("Shopify list script_tags HTTP %s: %s", resp.status_code, resp.text[:300])
    except Exception as exc:
        logger.error("_list_script_tags exception: %s", exc)
    return []


def _delete_script_tag(shop_domain: str, access_token: str, tag_id: int) -> bool:
    url = f"https://{shop_domain}/admin/api/{_SHOPIFY_API_VERSION}/script_tags/{tag_id}.json"
    try:
        resp = httpx.delete(url, headers=_shopify_headers(access_token), timeout=10.0)
        return resp.status_code == 200
    except Exception as exc:
        logger.error("_delete_script_tag %s exception: %s", tag_id, exc)
    return False


def _install_script_tag(shop_domain: str, access_token: str, pixel_id: str) -> dict:
    """
    Install tracker.js via Shopify ScriptTag API — no theme editing needed.
    Removes stale tags from a previous pixel_id before creating the new one.
    Returns {'status': 'created'|'exists'|'failed', 'id': ..., 'src': ...}
    """
    api_base = _api_base()
    tag_src  = f"{api_base}/static/tracker.js?client_id={pixel_id}"

    existing = _list_script_tags(shop_domain, access_token)
    our_tags = [t for t in existing if "/static/tracker.js" in (t.get("src") or "")]

    # Already correctly installed
    for tag in our_tags:
        if tag.get("src") == tag_src:
            return {"status": "exists", "id": tag["id"], "src": tag_src}

    # Remove stale tags (different pixel_id pointing to our endpoint)
    for tag in our_tags:
        _delete_script_tag(shop_domain, access_token, tag["id"])

    url     = f"https://{shop_domain}/admin/api/{_SHOPIFY_API_VERSION}/script_tags.json"
    payload = {"script_tag": {"event": "onload", "src": tag_src, "display_scope": "all"}}
    try:
        resp = httpx.post(url, headers=_shopify_headers(access_token), json=payload, timeout=15.0)
        if resp.status_code in (200, 201):
            tag = resp.json().get("script_tag", {})
            logger.info("ScriptTag installed for %s (id=%s)", pixel_id, tag.get("id"))
            return {"status": "created", "id": tag.get("id"), "src": tag_src}
        # 422 may mean write_script_tags scope not granted
        err = f"HTTP {resp.status_code}: {resp.text[:240]}"
        logger.warning("ScriptTag create failed for %s: %s", pixel_id, err)
        return {"status": "failed", "error": err}
    except Exception as exc:
        err = f"{type(exc).__name__}: {str(exc)[:200]}"
        logger.error("_install_script_tag exception: %s", err)
        return {"status": "failed", "error": err}


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post("/shopify/{pixel_id}/webhooks")
async def register_shopify_webhooks(pixel_id: str):
    """
    Register all required Shopify webhooks for a client. Idempotent — re-running
    is safe (existing webhooks are not duplicated).

    Returns a per-topic success report.
    """
    sb = get_supabase()
    result = (
        sb.table("clients")
        .select("id, shopify_domain, shopify_access_token, ecommerce_platform")
        .eq("pixel_id", pixel_id)
        .eq("is_active", True)
        .maybe_single()
        .execute()
    )
    if not (result and result.data):
        raise HTTPException(404, f"Client not found: {pixel_id}")

    c = result.data
    if c.get("ecommerce_platform") != "shopify":
        raise HTTPException(400, "Client is not a Shopify store")
    if not c.get("shopify_domain") or not c.get("shopify_access_token"):
        raise HTTPException(400, "Missing shopify_domain or shopify_access_token")

    shop_domain  = c["shopify_domain"]
    access_token = c["shopify_access_token"]
    webhook_url  = f"{_api_base()}/webhook/shopify/{pixel_id}"

    # Snapshot existing webhooks to avoid duplicates
    existing = _list_webhooks(shop_domain, access_token)
    existing_keys = {(w.get("topic"), w.get("address")) for w in existing}

    report = {"webhook_url": webhook_url, "results": {}}

    for topic in _REQUIRED_TOPICS:
        if (topic, webhook_url) in existing_keys:
            report["results"][topic] = {"status": "exists"}
            continue
        result = _create_webhook(shop_domain, access_token, topic, webhook_url)
        if result:
            report["results"][topic] = {"status": "created", "id": result.get("id")}
        else:
            report["results"][topic] = {"status": "failed"}

    # Mark client as webhooks_configured
    try:
        sb.table("clients").update({"webhooks_configured": True}).eq("id", c["id"]).execute()
    except Exception as exc:
        logger.debug("webhooks_configured flag update failed: %s", exc)

    succeeded = sum(1 for r in report["results"].values() if r["status"] in ("created", "exists"))
    failed    = sum(1 for r in report["results"].values() if r["status"] == "failed")
    report["summary"] = {"succeeded": succeeded, "failed": failed, "total": len(_REQUIRED_TOPICS)}
    return report


@router.post(
    "/shopify/{pixel_id}/install",
    summary="Full one-shot Shopify onboarding: webhooks + pixel ScriptTag",
    tags=["setup"],
)
async def install_shopify(pixel_id: str):
    """
    Single endpoint that handles the entire Shopify technical setup:
      1. Registers all required webhooks (orders, carts, customers, refunds)
      2. Installs tracker.js via ScriptTag API (no theme editing needed)

    Requires Admin API token scopes:
      read_orders, write_webhooks, read_script_tags, write_script_tags

    ScriptTag falls back gracefully — if write_script_tags scope is missing
    the response indicates the manual install snippet should be shown to the user.
    """
    sb = get_supabase()
    result = (
        sb.table("clients")
        .select("id, shopify_domain, shopify_access_token, ecommerce_platform")
        .eq("pixel_id", pixel_id)
        .eq("is_active", True)
        .maybe_single()
        .execute()
    )
    if not (result and result.data):
        raise HTTPException(404, f"Client not found: {pixel_id}")

    c = result.data
    if c.get("ecommerce_platform") != "shopify":
        raise HTTPException(400, "Client is not a Shopify store")
    if not c.get("shopify_domain") or not c.get("shopify_access_token"):
        raise HTTPException(400, "Missing shopify_domain or shopify_access_token")

    shop_domain  = c["shopify_domain"]
    access_token = c["shopify_access_token"]
    webhook_url  = f"{_api_base()}/webhook/shopify/{pixel_id}"

    # ── 1. Webhooks ───────────────────────────────────────────────────────────
    existing      = _list_webhooks(shop_domain, access_token)
    existing_keys = {(w.get("topic"), w.get("address")) for w in existing}
    webhook_results: dict = {}

    for topic in _REQUIRED_TOPICS:
        if (topic, webhook_url) in existing_keys:
            webhook_results[topic] = {"status": "exists"}
            continue
        wh = _create_webhook(shop_domain, access_token, topic, webhook_url)
        webhook_results[topic] = {"status": "created", "id": wh.get("id")} if wh else {"status": "failed"}

    webhooks_ok   = sum(1 for r in webhook_results.values() if r["status"] in ("created", "exists"))
    webhooks_fail = sum(1 for r in webhook_results.values() if r["status"] == "failed")

    try:
        sb.table("clients").update({"webhooks_configured": True}).eq("id", c["id"]).execute()
    except Exception as exc:
        logger.debug("webhooks_configured flag update failed: %s", exc)

    # ── 2. ScriptTag (pixel auto-install) ─────────────────────────────────────
    script_tag = _install_script_tag(shop_domain, access_token, pixel_id)

    return {
        "pixel_id":    pixel_id,
        "webhook_url": webhook_url,
        "tracker_src": f"{_api_base()}/static/tracker.js?client_id={pixel_id}",
        "webhooks": {
            "succeeded": webhooks_ok,
            "failed":    webhooks_fail,
            "total":     len(_REQUIRED_TOPICS),
        },
        "script_tag": script_tag,  # status: created | exists | failed
    }
