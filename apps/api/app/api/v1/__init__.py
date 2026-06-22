"""Noro Platform REST API — v1."""

from fastapi import APIRouter

from .routes.alerts import router as alerts_router
from .routes.clients import router as clients_router
from .routes.meta import router as meta_router
from .routes.performance import router as performance_router

router = APIRouter(prefix="/api/v1")

router.include_router(meta_router)
router.include_router(clients_router)
router.include_router(performance_router)
router.include_router(alerts_router)
