"""`ui_outline_create_polygon` — append board-outline geometry to the
project as `gr_poly` records on the `Edge.Cuts` layer (M2-P-05 extension
for M2-T-05).

The PCB editor's M2-T-05 OutlineTool calls this with the outer outline
(`outline_mm`) and any number of internal cutouts (`cutouts_mm`).
Outline polygons are persisted as a dedicated `board_outline` list
under the `pcb` slice — the on-disk emitter (`crates/ki/src/format/v9`)
fans them out as `(gr_poly ...)` records on `Edge.Cuts` with the
configured `stroke_width_mm` (KiCad default 0.05 mm).

Each `outline_uuid` returned identifies the *outer* polygon; the
cutouts hang off it so they can be edited as a group. The editor can
later move or delete a complete outline (outer + its cutouts) by
addressing the single uuid.
"""

from __future__ import annotations

import uuid
from typing import Any


def ui_outline_create_polygon(
    project: dict[str, Any],
    *,
    outline_mm: list[tuple[float, float]],
    cutouts_mm: list[list[tuple[float, float]]] | None = None,
    stroke_width_mm: float = 0.05,
    layer: str = "Edge.Cuts",
) -> dict[str, Any]:
    if len(outline_mm) < 3:
        return {
            "ok": False,
            "error": "outline_mm needs at least 3 points to form a polygon",
        }
    if layer != "Edge.Cuts":
        return {
            "ok": False,
            "error": (
                "board outlines must live on Edge.Cuts; "
                f"got layer={layer!r}"
            ),
        }
    cutouts = cutouts_mm or []
    for i, cutout in enumerate(cutouts):
        if len(cutout) < 3:
            return {
                "ok": False,
                "error": (
                    f"cutouts_mm[{i}] needs at least 3 points to form "
                    "a polygon"
                ),
            }
    pcb = project.setdefault("pcb", {})
    outline_uuid = str(uuid.uuid4())
    pcb.setdefault("board_outlines", []).append(
        {
            "uuid": outline_uuid,
            "layer": layer,
            "stroke_width_mm": float(stroke_width_mm),
            "outline_mm": [[float(x), float(y)] for (x, y) in outline_mm],
            "cutouts_mm": [
                [[float(x), float(y)] for (x, y) in cutout]
                for cutout in cutouts
            ],
        }
    )
    return {
        "ok": True,
        "outline_uuid": outline_uuid,
        "layer": layer,
        "cutout_count": len(cutouts),
        "project": project,
    }


__all__ = ["ui_outline_create_polygon"]
