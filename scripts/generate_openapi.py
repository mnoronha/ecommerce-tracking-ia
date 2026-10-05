"""
Generate docs/agency-api-v1.openapi.json from the Agency API v1 router.

Run from the repo root:
    python scripts/generate_openapi.py

Requires apps/api on PYTHONPATH. Set it via:
    PYTHONPATH=apps/api python scripts/generate_openapi.py
"""

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "apps", "api"))

# Minimal env so config.py doesn't throw on missing vars
os.environ.setdefault("SUPABASE_URL", "http://stub")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "stub")

from fastapi import FastAPI
from app.agency_api.v1.router import router

app = FastAPI(
    title="Noro Agency API",
    version="1.1",
    description=(
        "Deterministic Core API — single source of truth for Platform and Hermes.\n\n"
        "Auth: `Authorization: Bearer <scope_token>`. "
        "Scope tokens are configured as env vars per consumer role "
        "(agency_admin, hermes_service, platform_web, client_viewer).\n\n"
        "All contracts carry `schema_version: '1.1'`."
    ),
)
app.include_router(router)

out_path = os.path.join(
    os.path.dirname(__file__), "..", "docs", "agency-api-v1.openapi.json"
)
os.makedirs(os.path.dirname(out_path), exist_ok=True)

schema = app.openapi()
with open(out_path, "w", encoding="utf-8") as f:
    json.dump(schema, f, indent=2, ensure_ascii=False)

print(f"Generated: {os.path.abspath(out_path)}")
print(f"  Paths: {len(schema.get('paths', {}))}")
print(f"  Schemas: {len(schema.get('components', {}).get('schemas', {}))}")
