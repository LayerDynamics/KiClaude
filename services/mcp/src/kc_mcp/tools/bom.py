"""`kc_bom_get` — the bill of materials as structured JSON (SPEC §A.2.1).

Read-only. Walks the opened project's footprints and groups them into
BOM lines by MPN (falling back to value + footprint when no MPN is
assigned, so un-sourced parts still appear). Pricing is a separate
concern — `kc_bom_price` fans the lines out to the distributor
aggregator; this tool just reports what's on the board.
"""

from __future__ import annotations

from typing import Any

from claude_agent_sdk import tool

from kc_mcp._envelope import envelope, error_envelope
from kc_mcp.clients import kiserver_get


@tool(
    "kc_bom_get",
    "Return the project's bill of materials as structured JSON — one "
    "line per distinct part (grouped by MPN, or value+footprint when "
    "un-sourced) with quantity and refdes list. Read-only.",
    {"project_id": str},
)
async def kc_bom_get(args: dict[str, Any]) -> dict[str, Any]:
    project_id = args.get("project_id", "")
    if not project_id:
        return error_envelope("`project_id` is required")
    try:
        result = await kiserver_get(f"/project/{project_id}")
    except Exception as e:
        return error_envelope(f"kiserver GET /project/{project_id} failed: {e}")
    project = result.get("project", {}) or {}
    lines = _bom_lines(project)
    return envelope(
        {
            "ok": True,
            "project_id": project_id,
            "lines": lines,
            "line_count": len(lines),
            "placement_count": sum(line["qty"] for line in lines),
        }
    )


def _bom_lines(project: dict[str, Any]) -> list[dict[str, Any]]:
    """Group footprints into BOM lines. Pure over the KCIR dict."""
    pcb = project.get("pcb", {}) or {}
    grouped: dict[tuple[str, str, str], dict[str, Any]] = {}
    for fp in pcb.get("footprints", []) or []:
        # Skip footprints explicitly excluded from the BOM (attr flag).
        attrs = fp.get("attributes", []) or []
        if "exclude_from_bom" in attrs:
            continue
        mpn = (fp.get("mpn") or "").strip()
        value = (fp.get("value") or "").strip()
        lib_id = (fp.get("lib_id") or "").strip()
        # Key on MPN when present (the unambiguous part id); otherwise on
        # value+footprint so identical un-sourced parts still merge.
        key = (mpn, "", "") if mpn else ("", value, lib_id)
        refdes = (fp.get("refdes") or "").strip()
        line = grouped.get(key)
        if line is None:
            line = {
                "mpn": mpn,
                "value": value,
                "footprint": lib_id,
                "qty": 0,
                "refdes": [],
                "sourced": bool(mpn),
            }
            grouped[key] = line
        line["qty"] += 1
        if refdes:
            line["refdes"].append(refdes)

    lines = list(grouped.values())
    for line in lines:
        line["refdes"].sort()
    # Stable, readable order: sourced parts first, then by mpn/value.
    lines.sort(key=lambda ln: (not ln["sourced"], ln["mpn"], ln["value"]))
    return lines


__all__ = ["kc_bom_get"]
