"""Cursor-based pagination helpers."""

import base64
import json
from datetime import datetime, timezone


def encode_cursor(payload: dict) -> str:
    return base64.urlsafe_b64encode(json.dumps(payload).encode()).decode()


def decode_cursor(cursor: str) -> dict:
    try:
        return json.loads(base64.urlsafe_b64decode(cursor.encode()))
    except Exception:
        return {}


def paginated_response(
    data: list,
    total_count: int,
    limit: int,
    cursor_field: str = "created_at",
    request_id: str = "req_unknown",
) -> dict:
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    has_more = len(data) == limit
    cursor_next = None
    if has_more and data:
        last = data[-1]
        cursor_next = encode_cursor({
            "id": last.get("id"),
            cursor_field: str(last.get(cursor_field, "")),
        })
    return {
        "data": data,
        "pagination": {
            "cursor_next": cursor_next,
            "cursor_prev": None,
            "has_more": has_more,
            "total_count": total_count,
        },
        "metadata": {
            "request_id": request_id,
            "generated_at": now,
        },
    }


def single_response(data: dict, request_id: str = "req_unknown") -> dict:
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    return {
        "data": data,
        "metadata": {
            "request_id": request_id,
            "generated_at": now,
        },
    }
