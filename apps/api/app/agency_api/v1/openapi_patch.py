"""
Agency API v1.1 — OpenAPI schema patch.

Injected by main.py via a custom app.openapi() override.
This file is the canonical source for all metadata that FastAPI's declarative
decorators cannot express natively:

  CCR-001  BearerAuth securityScheme + security per operation + x-grant-scopes
  CCR-003  Standard error responses (401/403/409/429/503) + ErrorOut schema
  CCR-004  Optional X-Request-ID / X-Correlation-ID request headers
  CCR-007  Documented via AlertStatus enum description in schemas.py

After any change to this file or to router.py, regenerate the canonical JSON:

    cd apps/api
    python -c "
from app.main import app
import json
print(json.dumps(app.openapi(), indent=2))
" > ../../docs/agency-api-v1.openapi.json

The _GRANT_MAP must mirror the require_scopes() calls in router.py.
Keep both in sync when adding or changing routes.
"""

from __future__ import annotations

# ── Scope shorthand (must mirror auth.py constants) ───────────────────────────
_A    = ["agency_admin"]
_AH   = ["agency_admin", "hermes_service"]
_AP   = ["agency_admin", "platform_web"]
_AHP  = ["agency_admin", "hermes_service", "platform_web"]
_AHPC = ["agency_admin", "hermes_service", "platform_web", "client_viewer"]

# Maps FastAPI operationId → allowed scopes.
# Mirrors require_scopes() in router.py — keep in sync.
_GRANT_MAP: dict[str, list[str]] = {
    # ── Read endpoints ────────────────────────────────────────────────────────
    "list_clients_agency_v1_clients_get":                                                              _AHP,
    "get_client_truth_agency_v1_clients__client_id__truth_get":                                        _AHP,
    "get_client_health_agency_v1_clients__client_id__health_get":                                      _AHP,
    "get_pipeline_health_agency_v1_clients__client_id__pipeline_health_get":                           _AHP,
    "get_metrics_agency_v1_clients__client_id__metrics_get":                                           _AHP,
    "get_changes_agency_v1_clients__client_id__changes_get":                                           _AHP,
    "get_recommendations_agency_v1_clients__client_id__recommendations_get":                           _AHP,
    "get_report_contracts_agency_v1_clients__client_id__report_contracts_get":                         _AHP,
    "get_reports_agency_v1_clients__client_id__reports_get":                                           _AHPC,
    "list_alerts_agency_v1_alerts_get":                                                                _AHP,
    "get_alert_context_agency_v1_alerts__alert_id__context_get":                                       _AHP,
    "list_alert_rule_suggestions_agency_v1_alert_rule_suggestions_get":                                _AHP,
    "system_health_agency_v1_system_health_get":                                                       _AHP,
    "list_jobs_agency_v1_jobs_get":                                                                    _A,
    "get_job_agency_v1_jobs__job_id__get":                                                             _A,
    # ── Write endpoints ───────────────────────────────────────────────────────
    "create_diagnosis_agency_v1_diagnoses_post":                                                       _AH,
    "create_recommendation_agency_v1_recommendations_post":                                            _AH,
    "create_change_agency_v1_changes_post":                                                            _AH,
    "create_action_event_agency_v1_action_events_post":                                                _AHP,
    "create_alert_feedback_agency_v1_alert_feedback_post":                                             _AH,
    "create_report_narrative_agency_v1_report_narratives_post":                                        _AH,
    "transition_narrative_agency_v1_report_narratives__narrative_id__transitions_post":                _AHP,
    # CCR-001: hermes_service ADDED — transmits human decision, never decides autonomously
    "decide_alert_rule_suggestion_agency_v1_alert_rule_suggestions__suggestion_id__decision_post":     _AHP,
    "create_learning_candidate_agency_v1_learning_candidates_post":                                    _AH,
    # CCR-001: platform_web ADDED — replaces SCOPE_ADMIN_ONLY per governance decision
    "replay_job_agency_v1_jobs__job_id__replay_post":                                                  _AP,
}


def patch_agency_v1_openapi(schema: dict) -> None:
    """
    Mutate the FastAPI-generated OpenAPI schema in-place.
    Called once from the custom app.openapi() in main.py; result is cached.
    """
    # ── BearerAuth securityScheme ─────────────────────────────────────────────
    schema.setdefault("components", {}).setdefault("securitySchemes", {})["BearerAuth"] = {
        "type": "http",
        "scheme": "bearer",
        "description": (
            "Per-scope Bearer token (env-var, server-side only).\n\n"
            "| Scope | Env var | Allowed operations |\n"
            "|---|---|---|\n"
            "| `agency_admin` | `AGENCY_API_ADMIN_KEY` | All endpoints |\n"
            "| `hermes_service` | `AGENCY_API_HERMES_KEY` | All reads; intel writes; "
            "transmits human decisions to `/decision` |\n"
            "| `platform_web` | `AGENCY_API_PLATFORM_KEY` | All reads; action-event "
            "and replay writes; narrative transitions |\n"
            "| `client_viewer` | Supabase Auth JWT | Own-client read-only (reports) |\n\n"
            "Per-operation allowed scopes are listed in `x-grant-scopes`.\n\n"
            "**Note — `hermes_service` and `/decision`:** "
            "Hermes acts as a transport layer for a human decision made in Telegram. "
            "The `actor` field in the request body MUST contain the human actor_id, "
            "not the service identity. "
            "Enforcement of non-service identity is a Core invariant (Etapa 4). "
            "A future `approval_event_id` field will reference the canonical audit event."
        ),
    }

    # ── ErrorOut schema ───────────────────────────────────────────────────────
    schema["components"].setdefault("schemas", {}).setdefault("ErrorOut", {
        "title": "ErrorOut",
        "description": "Standard error envelope for 401, 403, 409, 429 and 503 responses.",
        "type": "object",
        "required": ["error"],
        "properties": {
            "error": {
                "type": "string",
                "title": "Error",
                "description": "Machine-readable error code (e.g. 'unauthorized', 'forbidden', 'conflict')",
            },
            "detail": {
                "anyOf": [{"type": "string"}, {"type": "null"}],
                "title": "Detail",
                "description": "Human-readable explanation. Absent on 4xx responses that must not leak information.",
            },
            "request_id": {
                "anyOf": [{"type": "string"}, {"type": "null"}],
                "title": "Request Id",
                "description": "Echoed from X-Request-ID request header, when provided.",
            },
        },
    })

    # ── Shared fragments ──────────────────────────────────────────────────────
    _err_ref = {"$ref": "#/components/schemas/ErrorOut"}
    _err_content = {"application/json": {"schema": _err_ref}}

    _std_errors: dict[str, dict] = {
        "401": {
            "description": "Unauthorised — missing, malformed or unrecognised Bearer token",
            "content": _err_content,
        },
        "403": {
            "description": "Forbidden — token valid but scope not permitted for this operation "
                           "(see x-grant-scopes)",
            "content": _err_content,
        },
        "429": {
            "description": "Too many requests — back off and retry after Retry-After seconds",
            "headers": {"Retry-After": {"schema": {"type": "integer"}}},
            "content": _err_content,
        },
        "503": {
            "description": "Agency API auth not configured on this server — check env vars",
            "content": _err_content,
        },
    }
    _conflict_error = {
        "description": "Conflict — idempotent replay submitted with a different payload, "
                       "or resource already exists",
        "content": _err_content,
    }

    # ── Optional tracing headers (CCR-004) ────────────────────────────────────
    _tracing_params = [
        {
            "name": "X-Request-ID",
            "in": "header",
            "required": False,
            "description": (
                "Client-generated UUID for this request. "
                "Echoed back in the response as X-Request-ID. "
                "Use the same value across retries of the same logical request."
            ),
            "schema": {"type": "string", "format": "uuid"},
        },
        {
            "name": "X-Correlation-ID",
            "in": "header",
            "required": False,
            "description": (
                "Trace ID propagated across the Hermes → Core → Platform call chain. "
                "Set by the first caller; forwarded unchanged by all intermediaries."
            ),
            "schema": {"type": "string"},
        },
    ]

    # ── Patch every /agency/v1/* operation ────────────────────────────────────
    for path, path_item in schema.get("paths", {}).items():
        if not path.startswith("/agency/v1"):
            continue
        for method, operation in path_item.items():
            if method not in ("get", "post", "put", "patch", "delete"):
                continue

            op_id: str = operation.get("operationId", "")

            # security
            operation.setdefault("security", [{"BearerAuth": []}])

            # grant scopes
            if op_id in _GRANT_MAP:
                operation["x-grant-scopes"] = _GRANT_MAP[op_id]

            # error responses
            responses = operation.setdefault("responses", {})
            for code, resp_def in _std_errors.items():
                responses.setdefault(code, resp_def)
            if method in ("post", "put", "patch", "delete"):
                responses.setdefault("409", _conflict_error)

            # tracing headers — append only if not already declared
            existing = {
                p.get("name")
                for p in operation.get("parameters", [])
                if isinstance(p, dict) and p.get("in") == "header"
            }
            for hdr in _tracing_params:
                if hdr["name"] not in existing:
                    operation.setdefault("parameters", []).append(hdr)
