"""`kc_wire_connect` — append a wire segment (M1-P-04)."""

from __future__ import annotations

import uuid
from typing import Any

from claude_agent_sdk import tool

from kc_mcp._envelope import envelope, error_envelope
from kc_mcp.clients import kiserver_get, kiserver_post


@tool(
    "kc_wire_connect",
    "Draw a wire segment on a sheet between an ordered list of "
    "(x, y) points. Returns the new wire's uuid.",
    {
        "project_id": str,
        "sheet_uuid": str,
        "points_mm": list[list[float]],
    },
)
async def kc_wire_connect(args: dict[str, Any]) -> dict[str, Any]:
    project_id = args.get("project_id", "")
    if not project_id:
        return error_envelope("`project_id` is required")
    points_raw = args.get("points_mm") or []
    if len(points_raw) < 2:
        return error_envelope("`points_mm` must contain at least 2 points")
    try:
        points = [[float(p[0]), float(p[1])] for p in points_raw]
    except (TypeError, ValueError, IndexError) as e:
        return error_envelope(f"invalid `points_mm`: {e}")

    try:
        result = await kiserver_get(f"/project/{project_id}")
    except Exception as e:
        return error_envelope(
            f"kiserver could not return project_id={project_id}: {e}",
            project_id=project_id,
        )
    project = result.get("project")
    if not isinstance(project, dict):
        return error_envelope(f"unexpected /project/{project_id} payload")

    sheet_uuid = args.get("sheet_uuid") or _root_sheet_uuid(project)
    if not sheet_uuid:
        return error_envelope("project has no schematic sheets to draw onto")

    wire_uuid = str(uuid.uuid4())
    project.setdefault("schematic", {}).setdefault("wires", []).append(
        {
            "uuid": wire_uuid,
            "sheet_uuid": sheet_uuid,
            "points_mm": points,
        }
    )
    try:
        await kiserver_post(f"/project/{project_id}/replace", {"project": project})
    except Exception as e:
        return error_envelope(
            f"kiserver replace failed: {e}",
            project_id=project_id,
        )
    return envelope(
        {
            "ok": True,
            "project_id": project_id,
            "wire_uuid": wire_uuid,
            "sheet_uuid": sheet_uuid,
            "points_mm": points,
        }
    )


def _root_sheet_uuid(project: dict[str, Any]) -> str:
    sheets = project.get("schematic", {}).get("sheets", []) or []
    for s in sheets:
        if s.get("parent") in (None, ""):
            return str(s.get("uuid", ""))
    return str(sheets[0]["uuid"]) if sheets else ""


__all__ = ["kc_wire_connect"]
