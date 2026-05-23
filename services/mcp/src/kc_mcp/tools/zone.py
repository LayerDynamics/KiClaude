"""`kc_zone_request` — declarative copper-zone creation (M2-P-04).

The Claude-facing surface lets the agent ask for "a GND pour on the
back layer covering the whole board". The tool figures out the
polygon from the board outline + an optional inset (`margin_mm`).
Raw polygon authoring lives in `ui_zone_create_polygon`.
"""

from __future__ import annotations

import uuid
from typing import Any

from claude_agent_sdk import tool

from kc_mcp._envelope import envelope, error_envelope
from kc_mcp.clients import kiserver_get, kiserver_post


@tool(
    "kc_zone_request",
    "Request a copper zone on a layer + net. Outline defaults to the "
    "board outline shrunk by `margin_mm`. Use `thermal_relief: true` "
    "for pads, `hatched: true` for hatched fills. Returns the zone "
    "uuid + resolved outline.",
    {
        "project_id": str,
        "net": str,
        "layer": str,
        "margin_mm": float,
        "thermal_relief": bool,
        "hatched": bool,
        "clearance_mm": float,
    },
)
async def kc_zone_request(args: dict[str, Any]) -> dict[str, Any]:
    project_id = args.get("project_id", "")
    net = (args.get("net") or "").strip()
    layer = (args.get("layer") or "F.Cu").strip()
    if not project_id or not net:
        return error_envelope("`project_id` and `net` are required")
    project = await _fetch_project(project_id)
    if project is None:
        return error_envelope(
            f"kiserver could not return project_id={project_id}",
            project_id=project_id,
        )
    pcb = project.setdefault("pcb", {})

    margin_mm = float(args.get("margin_mm") or 0.0)
    outline_pts = _resolve_outline(pcb, margin_mm)
    if not outline_pts:
        return error_envelope("board outline is empty or unbounded; supply an outline first")

    zone_uuid = str(uuid.uuid4())
    new_zone = {
        "uuid": zone_uuid,
        "layer": layer,
        "net": net,
        "outline_mm": [[x, y] for (x, y) in outline_pts],
        "cutouts_mm": [],
        "hatched": bool(args.get("hatched") or False),
        "clearance_mm": float(args.get("clearance_mm") or 0.0),
        "thermal_relief": bool(args.get("thermal_relief") or False),
        "thermal_gap_mm": 0.0,
        "thermal_bridge_width_mm": 0.0,
        "min_thickness_mm": 0.25,
        "connect_pads": "thermal_reliefs" if args.get("thermal_relief") else "yes",
        "filled_polygons": [],
    }
    pcb.setdefault("zones", []).append(new_zone)
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
            "zone_uuid": zone_uuid,
            "net": net,
            "layer": layer,
            "outline_mm": new_zone["outline_mm"],
            "thermal_relief": new_zone["thermal_relief"],
            "hatched": new_zone["hatched"],
        }
    )


def _resolve_outline(pcb: dict[str, Any], margin_mm: float) -> list[tuple[float, float]]:
    """Pick a zone outline from the board outline, shrunk by `margin_mm`.

    The board outline lives in `pcb.outline.points_mm` as a flat list
    of segment endpoints (start0, end0, start1, end1, …). We take its
    bounding box and inset by `margin_mm`. For non-rectangular
    boards the centroid-based inset would distort the shape — the
    M2-R-05 zone-fill kernel will do proper polygon offsetting; this
    is the bootstrap.
    """
    pts: list[tuple[float, float]] = []
    raw = (pcb.get("outline") or {}).get("points_mm") or []
    for p in raw:
        try:
            pts.append((float(p[0]), float(p[1])))
        except (TypeError, ValueError, IndexError):
            continue
    if not pts:
        return []
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    minx, maxx = min(xs) + margin_mm, max(xs) - margin_mm
    miny, maxy = min(ys) + margin_mm, max(ys) - margin_mm
    if minx >= maxx or miny >= maxy:
        return []
    return [(minx, miny), (maxx, miny), (maxx, maxy), (minx, maxy)]


async def _fetch_project(project_id: str) -> dict[str, Any] | None:
    try:
        result = await kiserver_get(f"/project/{project_id}")
    except Exception:
        return None
    project = result.get("project")
    if not isinstance(project, dict):
        return None
    return project


__all__ = ["kc_zone_request"]
