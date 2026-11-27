import logging
import os
from pathlib import Path

from datetime import datetime, timezone

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from .agency_api.v1 import router as agency_v1_router
from .agency_api.v1.admin_router import admin_router as agency_v1_admin_router
from .agency_api.v1.auth import SCOPE_INTERNAL_OPERATOR
from .agency_api.v1.openapi_patch import patch_agency_v1_openapi
from .agency_api.v1.request_id import AgencyRequestIDMiddleware
from .api.v1 import router as public_api_router
from .api.v1.errors import NoroPlatformError, http_exception_handler, noro_error_handler
from .config import settings
from .core.scheduler_setup import build_scheduler, embedded_scheduler_enabled
from .core.worker_lock import PostgresAdvisoryLock
from .limiter import limiter
from .routers import agency_os, ai_visibility as ai_visibility_router, alerts as alerts_router, annotations, attribution, audiences, cname, cogs, content as content_router, creatives, diagnostics, ecommerce_webhooks, finance as finance_router, fireflies_webhook, google_ads_dashboard, insights, integrations, journey, klaviyo_webhook, lgpd, live, merchant_center as merchant_center_router, meta_ads, pacing, pinterest_ads, pixel, portal_users, search_console as search_console_router, setup, shopify_app as shopify_app_router, shopify_revenue as shopify_revenue_router, sync as sync_router, technical_seo as technical_seo_router, tiktok_ads

logging.basicConfig(
    level=logging.DEBUG if settings.DEBUG else logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
)
logger = logging.getLogger(__name__)

app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    debug=settings.DEBUG,
    docs_url="/docs",
    redoc_url="/redoc",
)

# ── Custom OpenAPI: inject Agency v1.1 metadata (CCR-001/003/004) ─────────────
# Source of truth: agency_api/v1/openapi_patch.py
# Regenerate: cd apps/api && python -c "from app.main import app; import json; \
#   print(json.dumps(app.openapi(), indent=2))" > ../../docs/agency-api-v1.openapi.json
_orig_openapi = app.openapi


def _custom_openapi() -> dict:
    if app.openapi_schema:
        return app.openapi_schema
    schema = _orig_openapi()
    patch_agency_v1_openapi(schema)
    app.openapi_schema = schema
    return schema


app.openapi = _custom_openapi  # type: ignore[method-assign]

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_exception_handler(NoroPlatformError, noro_error_handler)
from fastapi import HTTPException as _HTTPException
app.add_exception_handler(_HTTPException, http_exception_handler)

_STARTED_AT = datetime.now(timezone.utc)
_JOB_RUNS: dict = {}   # job_id → {last_run, last_status}
_scheduler, _JOB_RUNS = build_scheduler(_JOB_RUNS)
_embedded_lock: PostgresAdvisoryLock | None = None


@app.on_event("startup")
def _start_scheduler() -> None:
    global _embedded_lock
    if not embedded_scheduler_enabled():
        logger.info("APScheduler disabled in HTTP API (ENABLE_EMBEDDED_SCHEDULER=false — running dedicated app.worker)")
        return
    dsn = os.getenv("DATABASE_URL") or os.getenv("SUPABASE_DB_URL") or ""
    if dsn:
        lock = PostgresAdvisoryLock(dsn=dsn)
        if not lock.acquire():
            logger.warning("APScheduler skipped in HTTP API — advisory lock already held by another worker")
            return
        _embedded_lock = lock
    _scheduler.start()
    logger.info("APScheduler started in embedded mode (%d jobs)", len(_scheduler.get_jobs()))


@app.on_event("shutdown")
def _stop_scheduler() -> None:
    global _embedded_lock
    if getattr(_scheduler, "running", False):
        _scheduler.shutdown(wait=False)
    if _embedded_lock is not None:
        _embedded_lock.release()
        _embedded_lock = None

# ── CORS (PRD §21.2 S5) ───────────────────────────────────────────────────────
# Explicit allowlist ONLY — no wildcard origin regex with credentials.
# Public tracking ingestion uses Bearer ingest tokens (no cookies) and gets a
# dedicated, path-restricted CORS middleware below.
_DEFAULT_CORS_ORIGINS = [
    "https://track.noroia.com.br",
    "https://ecommerce-tracking-ia.vercel.app",
    "http://localhost:3000",
    "http://127.0.0.1:3000",
]


def _parse_cors_origins(raw: str) -> list[str]:
    origins = [
        o.strip().rstrip("/")
        for o in (raw or "").split(",")
        if o.strip() and o.strip() != "*"
    ]
    return origins or list(_DEFAULT_CORS_ORIGINS)


_ALLOWED_CORS_ORIGINS = _parse_cors_origins(settings.CORS_ORIGINS)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_ALLOWED_CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "Idempotency-Key", "X-Request-ID"],
)

# ── CORS for Public Tracking Ingestion (Storefront + Shopify Custom Web Pixel) ─
# Tracking ingestion authenticates via per-client Bearer ingest tokens (never
# cookies). Both storefront pages (`https://store.example`) and Shopify Custom
# Web Pixels (`Origin: null`) POST to `/agency/v1/clients/{id}/tracking/events`.
# Scoped strictly to that path, POST/OPTIONS only, and NEVER with credentials.
import re as _re

_TRACKING_INGEST_PATH = _re.compile(r"^/agency/v1/clients/[^/]+/tracking/events/?$")


@app.middleware("http")
async def _pixel_sandbox_cors(request, call_next):
    origin = request.headers.get("origin")
    if not origin or not _TRACKING_INGEST_PATH.match(request.url.path):
        return await call_next(request)

    from starlette.responses import Response as _R

    cors = {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "POST, OPTIONS",
        "Access-Control-Allow-Headers": "Authorization, Content-Type",
        "Access-Control-Max-Age": "86400",
    }
    if request.method == "OPTIONS":
        return _R(status_code=204, headers=cors)
    response = await call_next(request)
    response.headers["Access-Control-Allow-Origin"] = "*"
    if "access-control-allow-credentials" in response.headers:
        del response.headers["access-control-allow-credentials"]
    return response


# ── Request body size limit ────────────────────────────────────────────────────
# Uvicorn has no default body size limit. Railway's proxy is not documented.
# This guard rejects oversized requests before JSON parsing runs.
# Tracking ingest max: 50 events × ~500 B ≈ 25 KB. Global cap at 1 MB is safe.
#
# Two paths:
#   Fast — Content-Length present: reject on header value before reading body.
#   Slow — Content-Length absent (chunked transfer or missing header on POST/PUT/PATCH):
#          buffer body chunks up to the limit, then re-inject via request._body
#          so downstream Pydantic parsing can still read it.

_MAX_REQUEST_BODY_BYTES = 1_048_576  # 1 MB


@app.middleware("http")
async def _body_size_limit(request, call_next):
    from starlette.responses import Response as _R

    cl = request.headers.get("content-length")
    if cl is not None:
        try:
            if int(cl) > _MAX_REQUEST_BODY_BYTES:
                return _R("Request body too large", status_code=413)
        except ValueError:
            pass  # malformed header — pass through; Pydantic will reject bad JSON
        return await call_next(request)

    # No Content-Length: buffer actual body for write methods only.
    if request.method in ("POST", "PUT", "PATCH"):
        chunks = []
        total = 0
        async for chunk in request.stream():
            total += len(chunk)
            if total > _MAX_REQUEST_BODY_BYTES:
                return _R("Request body too large", status_code=413)
            chunks.append(chunk)
        request._body = b"".join(chunks)  # type: ignore[attr-defined]

    return await call_next(request)


# ── Agency API request correlation (CCR-012) ──────────────────────────────────
# Outermost middleware: echoes X-Request-ID for /agency/v1 routes,
# generates req_<hex12> when absent. Applies to all responses including errors.
app.add_middleware(AgencyRequestIDMiddleware)

# ── Static files (pixel tracker.js) ──────────────────────────────────────────
_static_dir = Path(__file__).parent.parent / "static"
if _static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(_static_dir)), name="static")


@app.get("/p/t.js", include_in_schema=False)
def tracker_js_adblock_alias():
    """
    Anti-adblock alias for /static/tracker.js.
    Served under a neutral path so EasyPrivacy's ||*/tracker.js$script rule
    doesn't block it. Used automatically when a client has a first-party CNAME.
    """
    from fastapi.responses import Response as _Resp
    _f = _static_dir / "tracker.js"
    return _Resp(
        content=_f.read_bytes(),
        media_type="application/javascript",
        headers={"Cache-Control": "public, max-age=3600"},
    )


# ── Routers ───────────────────────────────────────────────────────────────────
# 2026-06-19: tracking migrado para Shopify nativo (Meta CAPI) + Simprosys (GA4/Google Ads).
# 2026-10-17: webhook receiver reativado para o Noro Tracking v4.3 (pedidos = Business Truth,
#   ligação pedido→visita em modo sombra). Envios de conversão continuam controlados por
#   CONVERSION_SENDS_DISABLED (core/send_guard.py) — usar "*" para modo só-tracking global.
# O pixel legado (/p/e) segue desativado: o tracker v4.3 usa /agency/v1/.../tracking/events.
# 2026-11-27 (PRD §21.2 S1-S3): todos os routers legados exigem autenticação
# (SCOPE_ADMIN_ONLY em rotas destrutivas/admin, SCOPE_INTERNAL_OPERATOR nos demais).
_INTERNAL_DEPS = [Depends(SCOPE_INTERNAL_OPERATOR)]

app.include_router(ecommerce_webhooks.router)
# app.include_router(pixel.router)
app.include_router(insights.router, dependencies=_INTERNAL_DEPS)
app.include_router(meta_ads.router, dependencies=_INTERNAL_DEPS)
app.include_router(audiences.router)       # SCOPE_ADMIN_ONLY no router
app.include_router(setup.router)           # SCOPE_ADMIN_ONLY / SCOPE_PLATFORM_WRITE por rota
app.include_router(portal_users.router)    # SCOPE_ADMIN_ONLY no router
app.include_router(cname.router, dependencies=_INTERNAL_DEPS)
app.include_router(attribution.router, dependencies=_INTERNAL_DEPS)
app.include_router(live.router, dependencies=_INTERNAL_DEPS)
app.include_router(cogs.router, dependencies=_INTERNAL_DEPS)
app.include_router(pacing.router, dependencies=_INTERNAL_DEPS)
app.include_router(journey.router, dependencies=_INTERNAL_DEPS)
app.include_router(creatives.router, dependencies=_INTERNAL_DEPS)
app.include_router(integrations.router, dependencies=_INTERNAL_DEPS)
app.include_router(alerts_router.router, dependencies=_INTERNAL_DEPS)
app.include_router(diagnostics.router, dependencies=_INTERNAL_DEPS)
app.include_router(google_ads_dashboard.router, dependencies=_INTERNAL_DEPS)
app.include_router(klaviyo_webhook.router)
app.include_router(fireflies_webhook.router)
app.include_router(lgpd.router)            # SCOPE_ADMIN_ONLY no router
app.include_router(annotations.router, dependencies=_INTERNAL_DEPS)
app.include_router(sync_router.router, dependencies=_INTERNAL_DEPS)
app.include_router(finance_router.router)  # SCOPE_ADMIN_ONLY por rota
app.include_router(ai_visibility_router.router, dependencies=_INTERNAL_DEPS)
app.include_router(merchant_center_router.router, dependencies=_INTERNAL_DEPS)
app.include_router(content_router.router)         # SCOPE_INTERNAL_OPERATOR no router
app.include_router(content_router.public_router)  # aprovação pública por token único
app.include_router(tiktok_ads.router, dependencies=_INTERNAL_DEPS)
app.include_router(pinterest_ads.router, dependencies=_INTERNAL_DEPS)
app.include_router(search_console_router.router, dependencies=_INTERNAL_DEPS)
app.include_router(shopify_revenue_router.router, dependencies=_INTERNAL_DEPS)
app.include_router(shopify_app_router.router)     # OAuth HMAC + SCOPE_INTERNAL_OPERATOR em /urls
app.include_router(technical_seo_router.router)   # SCOPE_INTERNAL_OPERATOR + SCOPE_ADMIN_ONLY em /apply
app.include_router(agency_os.router)              # AGENCY_OS_INGEST_KEY por rota

# ── Agency API v1 ────────────────────────────────────────────────────────────
app.include_router(agency_v1_router)
# New canonical API has no startup work and never invokes the legacy publisher.
from .agency_api.v1.canonical_reports import router as canonical_reports_router
app.include_router(canonical_reports_router)
from .agency_api.v1.change_digest import router as change_digest_router
app.include_router(change_digest_router)
app.include_router(agency_v1_admin_router)

# ── Noro Platform Public REST API (v1) ────────────────────────────────────────
app.include_router(public_api_router)


# ── CNAME verify echo (root-level — called via customer's CNAME) ─────────────
@app.get("/_verify/{secret}", include_in_schema=False)
def cname_verify_echo(secret: str):
    """
    Echoes the secret in plain text. Used to verify that a customer's
    CNAME (track.cliente.com) is correctly routing to our infrastructure.
    """
    from fastapi.responses import PlainTextResponse
    return PlainTextResponse(secret)


# ── Health check (PRD §21.2 S7 — minimal public response) ─────────────────────
# Detailed diagnostics (jobs, scheduler, key flags, GA4 stats) live on the
# authenticated admin endpoint GET /agency/v1/admin/health-details.
@app.get("/health", tags=["meta"])
def health_check():
    from .database import get_supabase
    db_status = "ok"
    try:
        sb = get_supabase()
        sb.table("clients").select("id").limit(1).execute()
    except Exception:
        logger.exception("public /health db check failed")
        db_status = "error"
    return {
        "status": "ok" if db_status == "ok" else "degraded",
        "version": settings.APP_VERSION,
        "db": db_status,
    }
