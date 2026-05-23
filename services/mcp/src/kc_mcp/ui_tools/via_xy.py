"""`ui_via_place_xy` — drop a via at raw (x, y) (M2-P-05).

PCB routing UI tool. The user presses `V` mid-route to insert a via;
the editor calls this with the cursor coordinate. The Claude-facing
variant `kc_via_add_hint` takes pad/track hints instead.
"""

from __future__ import annotations

import uuid
from typing import Any


def ui_via_place_xy(
    project: dict[str, Any],
    *,
    net: str,
    position_mm: tuple[float, float],
    from_layer: str = "F.Cu",
    to_layer: str = "B.Cu",
    drill_mm: float = 0.3,
    diameter_mm: float = 0.6,
    kind: str = "",
    locked: bool = False,
) -> dict[str, Any]:
    if not net:
        return {"ok": False, "error": "net is required"}
    if kind and kind not in {"blind", "buried"}:
        return {"ok": False, "error": f"kind must be '', 'blind', or 'buried'; got {kind!r}"}
    pcb = project.setdefault("pcb", {})
    via_uuid = str(uuid.uuid4())
    pcb.setdefault("vias", []).append(
        {
            "uuid": via_uuid,
            "net": net,
            "position_mm": [float(position_mm[0]), float(position_mm[1])],
            "from_layer": from_layer,
            "to_layer": to_layer,
            "drill_mm": float(drill_mm),
            "diameter_mm": float(diameter_mm),
            "kind": kind,
            "locked": bool(locked),
        }
    )
    return {
        "ok": True,
        "via_uuid": via_uuid,
        "net": net,
        "project": project,
    }


__all__ = ["ui_via_place_xy"]
