"""`kc_mpn_resolve` — resolve an MPN against live distributors + the
project's symbol libraries (FR-040/FR-041/FR-042).

The "full" resolver (Todo §4 / T7), upgrading the M1 shape-only check:

1. **Shape pre-filter** — reject strings that can't be an MPN before
   spending a network call.
2. **Live distributor lookup** — the same `kc_mcp.distributors`
   aggregator `kc_part_search` uses (shared `set_aggregator_factory`
   seam), returning stock / price-breaks / lifecycle / datasheet. Fails
   closed: `found` is true ONLY when a real distributor returns the
   part (first principle #5 — no hallucinated parts).
3. **Library candidates** — when a `project_id` is supplied, search the
   project's symbol libraries (kiserver `/library/search`) for matching
   `symbol_candidates` (lib_ids) and `footprint_candidates` (from the
   hits' footprint filters). Best-effort; stock is the load-bearing
   result.
"""

from __future__ import annotations

import contextlib
import re
from typing import Any
from urllib.parse import quote as urlquote

from claude_agent_sdk import tool

from kc_mcp._envelope import envelope, error_envelope
from kc_mcp.clients import kiserver_get
from kc_mcp.tools import sourcing
from kc_mcp.tools.sourcing import _pricing_to_payload

_MPN_SHAPE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_\-./]{1,63}$")


@tool(
    "kc_mpn_resolve",
    "Resolve a manufacturer part number against live distributors — "
    "returns stock, price breaks, lifecycle, and datasheet — plus "
    "symbol + footprint candidates from the project's libraries when "
    "`project_id` is supplied. Fails closed: `found` is true only when "
    "a real distributor returns the part (FR-042 / first principle #5).",
    {
        "mpn": str,
        "manufacturer": str,
        "footprint": str,
        "project_id": str,
    },
)
async def kc_mpn_resolve(args: dict[str, Any]) -> dict[str, Any]:
    raw_mpn = (args.get("mpn") or "").strip()
    if not raw_mpn:
        return error_envelope("`mpn` is required")
    manufacturer = (args.get("manufacturer") or "").strip()
    footprint = (args.get("footprint") or "").strip()
    project_id = (args.get("project_id") or "").strip()

    reason = _shape_rejection(raw_mpn)
    if reason:
        return envelope(
            {
                "ok": True,
                "found": False,
                "mpn": raw_mpn,
                "reason": reason,
                "confidence": 0.0,
                "stock": None,
                "symbol_candidates": [],
                "footprint_candidates": [],
            }
        )

    # 2. Live distributor lookup (FP#5 — every MPN resolves against a real
    # distributor or fails closed).
    found = False
    stock: dict[str, Any] = {"quotes": []}
    aggregator = sourcing._factory()
    try:
        part = await aggregator.price(raw_mpn, qty=1)
        found = bool(getattr(part, "quotes", None))
        stock = _pricing_to_payload(part)
    except Exception as e:
        stock = {"quotes": [], "error": f"distributor lookup failed: {e}"}
    finally:
        with contextlib.suppress(Exception):
            await aggregator.aclose()

    # 3. Library candidates (best-effort; needs project context).
    symbol_candidates, footprint_candidates = await _library_candidates(project_id, raw_mpn)

    confidence = _confidence(
        raw_mpn,
        manufacturer,
        footprint,
        found=found,
        has_candidates=bool(symbol_candidates),
    )
    return envelope(
        {
            "ok": True,
            "found": found,
            "mpn": raw_mpn,
            "manufacturer": manufacturer,
            "footprint": footprint,
            "stock": stock,
            "symbol_candidates": symbol_candidates,
            "footprint_candidates": footprint_candidates,
            "confidence": confidence,
        }
    )


def _shape_rejection(mpn: str) -> str | None:
    """Return a rejection reason if `mpn` can't be a real part number,
    else `None`. A real MPN has a valid shape and both a digit and a
    letter."""
    if not _MPN_SHAPE_RE.match(mpn):
        return "not_an_mpn_shape"
    if not (any(c.isdigit() for c in mpn) and any(c.isalpha() for c in mpn)):
        return "missing_digit_or_letter"
    return None


async def _library_candidates(
    project_id: str, mpn: str
) -> tuple[list[dict[str, Any]], list[str]]:
    """Symbol + footprint candidates from the project's library index,
    or empty lists when there's no project / no matching libraries."""
    if not project_id:
        return [], []
    try:
        res = await kiserver_get(
            f"/project/{urlquote(project_id)}/library/search?query={urlquote(mpn)}"
        )
    except Exception:
        return [], []
    hits = res.get("hits", []) or []
    symbols = [
        {
            "lib_id": h.get("lib_id", ""),
            "description": h.get("description", ""),
            "datasheet": h.get("datasheet", ""),
            "score": h.get("score"),
        }
        for h in hits
    ]
    footprints: list[str] = []
    seen: set[str] = set()
    for h in hits:
        fp = (h.get("footprint") or h.get("footprint_filter") or "").strip()
        if fp and fp not in seen:
            seen.add(fp)
            footprints.append(fp)
    return symbols, footprints


def _confidence(
    mpn: str, manufacturer: str, footprint: str, *, found: bool, has_candidates: bool
) -> float:
    """Heuristic confidence in `[0, 1]`. A live distributor hit dominates;
    otherwise it scales with the supplied metadata + shape."""
    if found:
        return 0.99 if has_candidates else 0.97
    score = 0.55
    if manufacturer:
        score += 0.2
    if footprint:
        score += 0.15
    if any(sep in mpn for sep in "-_/"):
        score += 0.05
    if has_candidates:
        score += 0.05
    return round(min(score, 0.95), 3)


__all__ = ["kc_mpn_resolve"]
