"""`ui_wire_draw_points` — direct multi-point wire draw (M1-P-05).

Used when the user draws a wire by clicking N points in the
schematic editor. Mirrors `kc_wire_connect` but the contract makes
the points the primary input rather than a derived hint.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Any


def ui_wire_draw_points(
    project: dict[str, Any],
    *,
    sheet_uuid: str,
    points_mm: Sequence[Sequence[float]],
) -> dict[str, Any]:
    if len(points_mm) < 2:
        return {"ok": False, "error": "at least 2 points required"}
    sheet_uuid = sheet_uuid or _root_sheet_uuid(project)
    if not sheet_uuid:
        return {"ok": False, "error": "project has no schematic sheets"}
    normalized = [[float(p[0]), float(p[1])] for p in points_mm]
    wire_uuid = str(uuid.uuid4())
    project.setdefault("schematic", {}).setdefault("wires", []).append(
        {
            "uuid": wire_uuid,
            "sheet_uuid": sheet_uuid,
            "points_mm": normalized,
        }
    )
    return {
        "ok": True,
        "wire_uuid": wire_uuid,
        "sheet_uuid": sheet_uuid,
        "points_mm": normalized,
        "project": project,
    }


def _root_sheet_uuid(project: dict[str, Any]) -> str:
    sheets = project.get("schematic", {}).get("sheets", []) or []
    for s in sheets:
        if s.get("parent") in (None, ""):
            return str(s.get("uuid", ""))
    return str(sheets[0]["uuid"]) if sheets else ""


__all__ = ["ui_wire_draw_points"]
