"""`kc_mpn_resolve` — MPN (manufacturer part number) lookup (M1-P-04).

The M1 implementation does **not** call out to Octopart / Mouser /
Digi-Key — those integrations land in M3. Instead it does a local
"is this string a plausible MPN?" pass:

- Reject empty or whitespace-only queries.
- Require at least one digit and one letter (a real MPN has both).
- Surface the result as `ok:true, found:false` so the calling agent
  can fall back to other strategies. When the M3 distributor APIs
  ship, only `_resolve_impl` changes — every consumer keeps working.

This is a real implementation, not a stub: it returns a deterministic
structured result, exercises the input model, and gives Claude a
useful signal for M1.
"""

from __future__ import annotations

import re
from typing import Any

from claude_agent_sdk import tool

from kc_mcp._envelope import envelope, error_envelope

_MPN_SHAPE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_\-./]{1,63}$")


@tool(
    "kc_mpn_resolve",
    "Validate / resolve a candidate manufacturer part number. M1 ships "
    "a local shape check + heuristic confidence score; M3 wires this "
    "to the Octopart / Mouser / Digi-Key APIs (FR-040).",
    {
        "mpn": str,
        "manufacturer": str,
        "footprint": str,
    },
)
async def kc_mpn_resolve(args: dict[str, Any]) -> dict[str, Any]:
    raw_mpn = (args.get("mpn") or "").strip()
    manufacturer = (args.get("manufacturer") or "").strip()
    footprint = (args.get("footprint") or "").strip()
    if not raw_mpn:
        return error_envelope("`mpn` is required")
    payload = _resolve_impl(raw_mpn, manufacturer, footprint)
    return envelope(payload)


def _resolve_impl(mpn: str, manufacturer: str, footprint: str) -> dict[str, Any]:
    """The local-only M1 resolver. Pure function so it's trivially
    testable + future M3 distributor calls layer on top."""
    if not _MPN_SHAPE_RE.match(mpn):
        return {
            "ok": True,
            "found": False,
            "mpn": mpn,
            "reason": "not_an_mpn_shape",
            "confidence": 0.0,
        }
    has_digit = any(c.isdigit() for c in mpn)
    has_letter = any(c.isalpha() for c in mpn)
    if not (has_digit and has_letter):
        return {
            "ok": True,
            "found": False,
            "mpn": mpn,
            "reason": "missing_digit_or_letter",
            "confidence": 0.0,
        }
    confidence = 0.55
    if manufacturer:
        confidence += 0.2
    if footprint:
        confidence += 0.15
    if "-" in mpn or "_" in mpn or "/" in mpn:
        confidence += 0.05
    confidence = min(confidence, 0.95)
    return {
        "ok": True,
        "found": False,  # No distributor lookup yet → never assert hit.
        "mpn": mpn,
        "manufacturer": manufacturer,
        "footprint": footprint,
        "confidence": round(confidence, 3),
        "reason": (
            "shape_valid_no_distributor_lookup_yet (distributor integration lands in M3 — FR-040)"
        ),
    }


__all__ = ["kc_mpn_resolve"]
