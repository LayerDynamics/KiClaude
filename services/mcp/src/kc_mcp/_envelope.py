"""MCP tool response envelope helpers.

Every kc_* tool returns the same shape — a `content` array carrying
a single text item with a JSON dump of the structured result, plus
the structured object on a `structured` key for downstream consumers
that prefer the parsed form.
"""

from __future__ import annotations

import json
from typing import Any


def envelope(payload: dict[str, Any]) -> dict[str, Any]:
    """Wrap a structured `payload` in the standard MCP tool envelope."""
    return {
        "content": [
            {"type": "text", "text": json.dumps(payload, sort_keys=True, separators=(",", ":"))}
        ],
        "structured": payload,
    }


def error_envelope(message: str, **extra: Any) -> dict[str, Any]:
    """Standard error shape: `{ok: false, error, ...extra}`."""
    payload: dict[str, Any] = {"ok": False, "error": message}
    payload.update(extra)
    return envelope(payload)


__all__ = ["envelope", "error_envelope"]
