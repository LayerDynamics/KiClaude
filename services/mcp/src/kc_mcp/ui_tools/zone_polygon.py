"""`ui_zone_create_polygon` — draw a copper zone outline from a polygon
of raw points (M2-P-05).

Used by the PCB editor's M2-T-04 zone tool: the user clicks a polygon
in the canvas, picks a net + layer, and the editor sends the raw
polygon over here. The Claude-facing `kc_zone_request` derives a
rectangle from the board outline instead.
"""

from __future__ import annotations

import uuid
from typing import Any


def ui_zone_create_polygon(
    project: dict[str, Any],
    *,
    net: str,
    layer: str,
    outline_mm: list[tuple[float, float]],
    thermal_relief: bool = False,
    hatched: bool = False,
    clearance_mm: float = 0.0,
) -> dict[str, Any]:
    if not net:
        return {"ok": False, "error": "net is required"}
    if not layer:
        return {"ok": False, "error": "layer is required"}
    if len(outline_mm) < 3:
        return {
            "ok": False,
            "error": "outline_mm needs at least 3 points to form a polygon",
        }
    pcb = project.setdefault("pcb", {})
    zone_uuid = str(uuid.uuid4())
    pcb.setdefault("zones", []).append(
        {
            "uuid": zone_uuid,
            "layer": layer,
            "net": net,
            "outline_mm": [[float(x), float(y)] for (x, y) in outline_mm],
            "cutouts_mm": [],
            "hatched": bool(hatched),
            "clearance_mm": float(clearance_mm),
            "thermal_relief": bool(thermal_relief),
            "thermal_gap_mm": 0.0,
            "thermal_bridge_width_mm": 0.0,
            "min_thickness_mm": 0.25,
            "connect_pads": "thermal_reliefs" if thermal_relief else "yes",
            "filled_polygons": [],
        }
    )
    return {
        "ok": True,
        "zone_uuid": zone_uuid,
        "net": net,
        "layer": layer,
        "project": project,
    }


__all__ = ["ui_zone_create_polygon"]
