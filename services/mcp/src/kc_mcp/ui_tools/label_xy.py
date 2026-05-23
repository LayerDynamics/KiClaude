"""`ui_label_place_xy` — direct-coordinate label placement (M1-P-05)."""

from __future__ import annotations

import uuid
from typing import Any

_ALLOWED_KINDS = {"local", "global", "hierarchical", "power"}


def ui_label_place_xy(
    project: dict[str, Any],
    *,
    sheet_uuid: str,
    kind: str,
    text: str,
    position_mm: tuple[float, float],
    rotation_deg: float = 0.0,
    shape: str = "",
) -> dict[str, Any]:
    if not text:
        return {"ok": False, "error": "text is required"}
    if kind not in _ALLOWED_KINDS:
        return {
            "ok": False,
            "error": f"kind must be one of {sorted(_ALLOWED_KINDS)}, got {kind!r}",
        }
    sheet_uuid = sheet_uuid or _root_sheet_uuid(project)
    if not sheet_uuid:
        return {"ok": False, "error": "project has no schematic sheets"}
    label_uuid = str(uuid.uuid4())
    pos = [float(position_mm[0]), float(position_mm[1])]
    project.setdefault("schematic", {}).setdefault("labels", []).append(
        {
            "uuid": label_uuid,
            "sheet_uuid": sheet_uuid,
            "kind": kind,
            "text": text,
            "position_mm": pos,
            "rotation_deg": float(rotation_deg),
            "shape": shape,
        }
    )
    if kind == "hierarchical":
        owning_sheet = next(
            (
                s
                for s in project.get("schematic", {}).get("sheets", []) or []
                if s.get("uuid") == sheet_uuid
            ),
            None,
        )
        if owning_sheet is not None:
            pins = owning_sheet.setdefault("pins", [])
            if not any(p.get("name") == text for p in pins):
                pins.append(
                    {
                        "uuid": str(uuid.uuid4()),
                        "name": text,
                        "shape": shape or "input",
                        "position_mm": pos,
                        "rotation_deg": float(rotation_deg),
                    }
                )
    return {
        "ok": True,
        "label_uuid": label_uuid,
        "sheet_uuid": sheet_uuid,
        "kind": kind,
        "text": text,
        "project": project,
    }


def _root_sheet_uuid(project: dict[str, Any]) -> str:
    sheets = project.get("schematic", {}).get("sheets", []) or []
    for s in sheets:
        if s.get("parent") in (None, ""):
            return str(s.get("uuid", ""))
    return str(sheets[0]["uuid"]) if sheets else ""


__all__ = ["ui_label_place_xy"]
