"""`ui_track_draw_points` — manual track routing from raw points (M2-P-05).

Used by the PCB editor's M2-T-03 routing tool: the user clicks to set
each waypoint and double-clicks to finish. The polyline of clicked
points lands here as a flat list of `(x, y)` pairs.
"""

from __future__ import annotations

import uuid
from itertools import pairwise
from typing import Any


def ui_track_draw_points(
    project: dict[str, Any],
    *,
    net: str,
    layer: str,
    points_mm: list[tuple[float, float]],
    width_mm: float,
    locked: bool = False,
) -> dict[str, Any]:
    """Append one track per consecutive `(start, end)` pair from the
    supplied polyline."""
    if not net:
        return {"ok": False, "error": "net is required"}
    if not layer:
        return {"ok": False, "error": "layer is required"}
    if len(points_mm) < 2:
        return {"ok": False, "error": "points_mm needs at least two points"}
    pcb = project.setdefault("pcb", {})
    tracks = pcb.setdefault("tracks", [])
    created: list[str] = []
    for (sx, sy), (ex, ey) in pairwise(points_mm):
        t_uuid = str(uuid.uuid4())
        tracks.append(
            {
                "uuid": t_uuid,
                "layer": layer,
                "net": net,
                "points_mm": [[float(sx), float(sy)], [float(ex), float(ey)]],
                "width_mm": float(width_mm),
                "locked": bool(locked),
            }
        )
        created.append(t_uuid)
    return {
        "ok": True,
        "net": net,
        "layer": layer,
        "track_uuids": created,
        "project": project,
    }


__all__ = ["ui_track_draw_points"]
