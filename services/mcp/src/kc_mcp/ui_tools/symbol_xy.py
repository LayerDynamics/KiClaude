"""`ui_symbol_place_xy` — drag-drop symbol placement (M1-P-05).

Mirrors `kc_symbol_add` but the contract makes the (x, y) the
primary input, not a hint. Used by the React schematic editor when
the user drops a symbol from the library sidebar at a precise pixel-
converted coordinate.

Pure mutation function — the kiserver / gateway pipes a project
dict in and gets the mutated dict + new symbol uuid back.
"""

from __future__ import annotations

import uuid
from typing import Any


def ui_symbol_place_xy(
    project: dict[str, Any],
    *,
    sheet_uuid: str,
    lib_id: str,
    position_mm: tuple[float, float],
    rotation_deg: float = 0.0,
    refdes: str = "",
    value: str = "",
) -> dict[str, Any]:
    """Append a new symbol instance to `project["schematic"]["symbols"]`.

    Returns `{ok, symbol_uuid, project}` so the caller can both stash
    the mutated project and report the new symbol back to the UI.
    """
    if not lib_id:
        return {"ok": False, "error": "lib_id is required"}
    sheet_uuid = sheet_uuid or _root_sheet_uuid(project)
    if not sheet_uuid:
        return {"ok": False, "error": "project has no schematic sheets"}
    symbol_uuid = str(uuid.uuid4())
    is_power_symbol = lib_id.startswith("power:")
    is_power_flag = lib_id == "power:PWR_FLAG"
    new_symbol = {
        "uuid": symbol_uuid,
        "sheet_uuid": sheet_uuid,
        "lib_id": lib_id,
        "refdes": refdes,
        "value": value,
        "footprint": "",
        "mpn": "",
        "datasheet": "",
        "position_mm": [float(position_mm[0]), float(position_mm[1])],
        "rotation_deg": float(rotation_deg),
        "mirrored": False,
        "unit": 1,
        "in_bom": not is_power_flag,
        "on_board": True,
        "dnp": False,
        "is_power_flag": is_power_flag,
        "is_power_symbol": is_power_symbol,
        "properties": [
            {
                "key": "Reference",
                "value": refdes,
                "position_mm": [position_mm[0], position_mm[1] - 2.0],
                "rotation_deg": 0.0,
                "hide": False,
            },
            {
                "key": "Value",
                "value": value,
                "position_mm": [position_mm[0], position_mm[1] + 2.0],
                "rotation_deg": 0.0,
                "hide": False,
            },
        ],
    }
    project.setdefault("schematic", {}).setdefault("symbols", []).append(new_symbol)
    return {
        "ok": True,
        "symbol_uuid": symbol_uuid,
        "sheet_uuid": sheet_uuid,
        "lib_id": lib_id,
        "project": project,
    }


def _root_sheet_uuid(project: dict[str, Any]) -> str:
    sheets = project.get("schematic", {}).get("sheets", []) or []
    for s in sheets:
        if s.get("parent") in (None, ""):
            return str(s.get("uuid", ""))
    return str(sheets[0]["uuid"]) if sheets else ""


__all__ = ["ui_symbol_place_xy"]
