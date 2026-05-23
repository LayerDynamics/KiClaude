"""`ui_symbol_edit_props` — coordinate-free property edit for the
M1-T-04 schematic property panel.

Edits the named fields on an existing `SymbolInstance` and updates
the matching `Reference` / `Value` / `Footprint` / `Datasheet`
entries in its `properties` array so the canonical .kicad_sch emit
keeps both shapes in sync.
"""

from __future__ import annotations

from typing import Any


def ui_symbol_edit_props(
    project: dict[str, Any],
    *,
    symbol_uuid: str,
    refdes: str | None = None,
    value: str | None = None,
    footprint: str | None = None,
    mpn: str | None = None,
    datasheet: str | None = None,
) -> dict[str, Any]:
    if not symbol_uuid:
        return {"ok": False, "error": "symbol_uuid is required"}
    schematic = project.get("schematic") or {}
    symbols = schematic.get("symbols") or []
    target = next((s for s in symbols if s.get("uuid") == symbol_uuid), None)
    if target is None:
        return {"ok": False, "error": f"no symbol with uuid={symbol_uuid}"}

    changed: list[str] = []
    if refdes is not None and refdes != target.get("refdes", ""):
        target["refdes"] = refdes
        changed.append("refdes")
    if value is not None and value != target.get("value", ""):
        target["value"] = value
        changed.append("value")
    if footprint is not None and footprint != target.get("footprint", ""):
        target["footprint"] = footprint
        changed.append("footprint")
    if mpn is not None and mpn != target.get("mpn", ""):
        target["mpn"] = mpn
        changed.append("mpn")
    if datasheet is not None and datasheet != target.get("datasheet", ""):
        target["datasheet"] = datasheet
        changed.append("datasheet")

    # Mirror the changes into the `properties` array so the .kicad_sch
    # emit writes them back consistently.
    mirror = {
        "Reference": target.get("refdes", ""),
        "Value": target.get("value", ""),
        "Footprint": target.get("footprint", ""),
        "MPN": target.get("mpn", ""),
        "Datasheet": target.get("datasheet", ""),
    }
    for prop in target.get("properties", []) or []:
        key = prop.get("key", "")
        if key in mirror:
            prop["value"] = mirror[key]
    # Ensure every changed standard property exists in `properties`
    # (the parser may have omitted empty fields).
    existing_keys = {p.get("key", "") for p in target.get("properties", []) or []}
    for key in ("Reference", "Value", "Footprint", "MPN", "Datasheet"):
        if mirror[key] and key not in existing_keys:
            target.setdefault("properties", []).append(
                {
                    "key": key,
                    "value": mirror[key],
                    "position_mm": list(target.get("position_mm", [0.0, 0.0])),
                    "rotation_deg": 0.0,
                    "hide": False,
                }
            )
    return {
        "ok": True,
        "symbol_uuid": symbol_uuid,
        "changed_fields": changed,
        "project": project,
    }


__all__ = ["ui_symbol_edit_props"]
