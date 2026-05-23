"""`ui_footprint_place_xy` — drag-drop footprint placement (M2-P-05).

Mirrors `kc_footprint_place_hint` but the (x, y) is the primary input.
Used by the PCB editor when the user drops a footprint from the
library sidebar at a pixel-converted coordinate.
"""

from __future__ import annotations

import uuid
from typing import Any


def ui_footprint_place_xy(
    project: dict[str, Any],
    *,
    lib_id: str,
    position_mm: tuple[float, float],
    layer: str = "F.Cu",
    rotation_deg: float = 0.0,
    refdes: str = "",
    value: str = "",
) -> dict[str, Any]:
    """Append a new footprint instance to `project["pcb"]["footprints"]`.

    Returns `{ok, footprint_uuid, project}` so the caller can both stash
    the mutated project and report the new footprint back to the UI.
    """
    if not lib_id:
        return {"ok": False, "error": "lib_id is required"}
    fp_uuid = str(uuid.uuid4())
    new_fp = {
        "uuid": fp_uuid,
        "refdes": refdes,
        "lib_id": lib_id,
        "value": value,
        "mpn": "",
        "layer": layer,
        "position_mm": [float(position_mm[0]), float(position_mm[1])],
        "rotation_deg": float(rotation_deg),
        "locked": False,
        "attributes": [],
        "pads": [],
        "courtyard": None,
        "models_3d": [],
        "drawings": [],
    }
    pcb = project.setdefault("pcb", {})
    pcb.setdefault("footprints", []).append(new_fp)
    return {
        "ok": True,
        "footprint_uuid": fp_uuid,
        "refdes": refdes,
        "lib_id": lib_id,
        "project": project,
    }


__all__ = ["ui_footprint_place_xy"]
