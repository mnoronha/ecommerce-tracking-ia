import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .config import settings
from .routers import ecommerce_webhooks, insights, meta_ads, pixel

logging.basicConfig(
    level=logging.DEBUG if settings.DEBUG else logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
)

app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    debug=settings.DEBUG,
    docs_url="/docs",
    redoc_url="/redoc",
)

# ── CORS ──────────────────────────────────────────────────────────────────────
origins = (
    [o.strip() for o in settings.CORS_ORIGINS.split(",")]
    if settings.CORS_ORIGINS != "*"
    else ["*"]
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Static files (pixel tracker.js) ──────────────────────────────────────────
_static_dir = Path(__file__).parent.parent / "static"
if _static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(_static_dir)), name="static")

# ── Routers ───────────────────────────────────────────────────────────────────
app.include_router(ecommerce_webhooks.router)
app.include_router(pixel.router)
app.include_router(insights.router)
app.include_router(meta_ads.router)


# ── Health check ──────────────────────────────────────────────────────────────
@app.get("/health", tags=["meta"])
async def health_check():
    from .database import get_supabase
    db_status = "ok"
    db_error = None
    try:
        sb = get_supabase()
        sb.table("clients").select("id").limit(1).execute()
    except Exception as e:
        db_status = "error"
        db_error = str(e)[:200]
    return {
        "status": "ok",
        "version": settings.APP_VERSION,
        "db": db_status,
        "db_error": db_error,
        "supabase_url": settings.SUPABASE_URL[:40] + "..." if settings.SUPABASE_URL else "NOT SET",
        "service_key_set": bool(settings.SUPABASE_SERVICE_KEY),
    }
