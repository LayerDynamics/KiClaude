"""`ui_footprint_move` — drag-to-move an existing footprint (M2-P-05).

The PCB editor calls this for both keyboard nudges and drag releases.
Locked footprints refuse the move so the UI can show "release lock to
move" rather than silently overriding the lock bit.
"""

from __future__ import annotations

from typing import Any


def ui_footprint_move(
    project: dict[str, Any],
    *,
    footprint_uuid: str,
    position_mm: tuple[float, float],
    rotation_deg: float | None = None,
    layer: str | None = None,
) -> dict[str, Any]:
    """Move (and optionally rotate / flip) a footprint by uuid."""
    if not footprint_uuid:
        return {"ok": False, "error": "footprint_uuid is required"}
    pcb = project.setdefault("pcb", {})
    target: dict[str, Any] | None = None
    for fp in pcb.get("footprints", []) or []:
        if fp.get("uuid") == footprint_uuid:
            target = fp
            break
    if target is None:
        return {
            "ok": False,
            "error": f"no footprint with uuid={footprint_uuid}",
        }
    if target.get("locked"):
        return {
            "ok": False,
            "error": f"footprint {footprint_uuid} is locked; release lock first",
        }
    target["position_mm"] = [float(position_mm[0]), float(position_mm[1])]
    if rotation_deg is not None:
        target["rotation_deg"] = float(rotation_deg)
    if layer:
        target["layer"] = str(layer)
    return {
        "ok": True,
        "footprint_uuid": footprint_uuid,
        "position_mm": target["position_mm"],
        "rotation_deg": target.get("rotation_deg", 0.0),
        "layer": target.get("layer", ""),
        "project": project,
    }


__all__ = ["ui_footprint_move"]
