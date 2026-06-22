"""Standardised error responses for Noro Platform REST API."""

from fastapi import Request, HTTPException
from fastapi.responses import JSONResponse


class NoroPlatformError(Exception):
    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        details: dict | None = None,
    ):
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details or {}


def _error_body(code: str, message: str, details: dict, request_id: str) -> dict:
    return {
        "error": {
            "code": code,
            "message": message,
            "details": details,
            "request_id": request_id,
        }
    }


async def noro_error_handler(request: Request, exc: NoroPlatformError) -> JSONResponse:
    req_id = getattr(request.state, "request_id", "req_unknown")
    return JSONResponse(
        status_code=exc.status_code,
        content=_error_body(exc.code, exc.message, exc.details, req_id),
    )


async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    req_id = getattr(request.state, "request_id", "req_unknown")
    code = {
        400: "BAD_REQUEST",
        401: "UNAUTHORIZED",
        403: "FORBIDDEN",
        404: "NOT_FOUND",
        409: "CONFLICT",
        422: "VALIDATION_ERROR",
        429: "RATE_LIMIT_EXCEEDED",
        500: "INTERNAL_ERROR",
        503: "SERVICE_UNAVAILABLE",
    }.get(exc.status_code, "ERROR")
    return JSONResponse(
        status_code=exc.status_code,
        content=_error_body(code, str(exc.detail), {}, req_id),
    )


# ── Common error factories ────────────────────────────────────────────────────

def not_found(resource: str) -> NoroPlatformError:
    return NoroPlatformError(404, "NOT_FOUND", f"{resource} not found")


def forbidden(reason: str = "Insufficient permissions") -> NoroPlatformError:
    return NoroPlatformError(403, "FORBIDDEN", reason)


def bad_request(message: str, details: dict | None = None) -> NoroPlatformError:
    return NoroPlatformError(400, "BAD_REQUEST", message, details)
