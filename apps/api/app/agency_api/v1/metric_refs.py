"""
metric_refs — {{placeholder}} resolution.

Etapa 1 stub: replaces {{mN}} with a human-readable label so the API
returns something useful without real snapshot data.

Etapa 9 replaces resolve_statement() with real snapshot lookups from
core_metric_snapshots, formatted by presentation type and client currency.
"""

import re
from typing import Any

_PLACEHOLDER = re.compile(r"\{\{(\w+)\}\}")


def resolve_statement(statement: str, metric_refs: dict[str, Any]) -> str:
    """Replace {{mN}} placeholders with stub labels. Real resolution in Etapa 9."""
    def _sub(m: re.Match) -> str:
        key = m.group(1)
        ref = metric_refs.get(key)
        if ref is None:
            return f"[{key}?MISSING_REF]"
        presentation = (
            ref.get("presentation", "?") if isinstance(ref, dict) else "?"
        )
        return f"[{key}:{presentation}]"

    return _PLACEHOLDER.sub(_sub, statement)


def missing_refs(statement: str, metric_refs: dict[str, Any]) -> list[str]:
    """Return placeholder keys that are referenced in statement but absent from metric_refs."""
    keys_in_statement = set(_PLACEHOLDER.findall(statement))
    return sorted(k for k in keys_in_statement if k not in metric_refs)


def validate_refs_in_blocks(blocks: list[dict[str, Any]]) -> list[str]:
    """
    Validate all blocks in a list (used for narratives and diagnosis facts).
    Returns a flat list of error strings (empty = valid).
    """
    errors: list[str] = []
    for i, block in enumerate(blocks):
        statement = block.get("statement", "")
        refs = block.get("metric_refs") or {}
        missing = missing_refs(statement, refs)
        if missing:
            errors.append(
                f"Block {i}: placeholder(s) {missing} not in metric_refs"
            )
    return errors
