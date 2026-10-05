"""
Agency API v1 — X-Request-ID correlation middleware (CCR-012).

For every /agency/v1 request:
  - If X-Request-ID is present in request headers, echo it unchanged in response.
  - If absent, generate req_<hex12> and include it in the response.
  - Store in request.state.request_id for log correlation throughout the request.

Scope: path-filtered to /agency/v1 only. Other routes are not affected.
Security: only a non-sensitive correlation ID is added. No auth or credential
information is exposed through this header.
"""

from __future__ import annotations

import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

_AGENCY_PREFIX = "/agency/v1"
_HEADER_IN  = "x-request-id"   # ASGI normalizes incoming headers to lowercase
_HEADER_OUT = "X-Request-ID"   # response header — preserves conventional casing


class AgencyRequestIDMiddleware(BaseHTTPMiddleware):
    """
    Echo X-Request-ID on all /agency/v1 responses.

    Applies to both success and error responses (401, 403, 404, 422, 503, etc.)
    because the middleware wraps the full call_next chain, including FastAPI's
    exception handlers.
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        if not request.url.path.startswith(_AGENCY_PREFIX):
            return await call_next(request)

        req_id = request.headers.get(_HEADER_IN) or f"req_{uuid.uuid4().hex[:12]}"
        request.state.request_id = req_id

        response = await call_next(request)
        response.headers[_HEADER_OUT] = req_id
        return response
