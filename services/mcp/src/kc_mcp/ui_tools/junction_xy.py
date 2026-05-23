"""`ui_junction_place_xy` — drop a junction marker at an exact mm
position (M1-T-03)."""

from __future__ import annotations

import uuid
from typing import Any


def ui_junction_place_xy(
    project: dict[str, Any],
    *,
    sheet_uuid: str,
    position_mm: tuple[float, float],
) -> dict[str, Any]:
    sheet_uuid = sheet_uuid or _root_sheet_uuid(project)
    if not sheet_uuid:
        return {"ok": False, "error": "project has no schematic sheets"}
    junction_uuid = str(uuid.uuid4())
    project.setdefault("schematic", {}).setdefault("junctions", []).append(
        {
            "uuid": junction_uuid,
            "sheet_uuid": sheet_uuid,
            "position_mm": [float(position_mm[0]), float(position_mm[1])],
        }
    )
    return {
        "ok": True,
        "junction_uuid": junction_uuid,
        "sheet_uuid": sheet_uuid,
        "position_mm": [float(position_mm[0]), float(position_mm[1])],
        "project": project,
    }


def _root_sheet_uuid(project: dict[str, Any]) -> str:
    sheets = project.get("schematic", {}).get("sheets", []) or []
    for s in sheets:
        if s.get("parent") in (None, ""):
            return str(s.get("uuid", ""))
    return str(sheets[0]["uuid"]) if sheets else ""


__all__ = ["ui_junction_place_xy"]
