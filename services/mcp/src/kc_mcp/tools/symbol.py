"""`kc_symbol_add` + `kc_symbol_edit` — KCIR symbol mutations (M1-P-04).

Both tools follow the same shape: fetch the current KCIR via
`kiserver`, mutate the dict in place, push the mutated KCIR back via
`POST /project/{id}/replace`, and return the touched symbol's uuid in
the response envelope.
"""

from __future__ import annotations

import uuid
from typing import Any

from claude_agent_sdk import tool

from kc_mcp._envelope import envelope, error_envelope
from kc_mcp.clients import kiserver_get, kiserver_post


@tool(
    "kc_symbol_add",
    "Place a new symbol instance on a sheet. Returns the new symbol's "
    "uuid. The refdes can be left blank (assigned by kc_validate's "
    "annotation pass).",
    {
        "project_id": str,
        "sheet_uuid": str,
        "lib_id": str,
        "value": str,
        "refdes": str,
        "position_mm": list[float],
        "rotation_deg": float,
    },
)
async def kc_symbol_add(args: dict[str, Any]) -> dict[str, Any]:
    project_id = args.get("project_id", "")
    sheet_uuid = args.get("sheet_uuid", "")
    lib_id = args.get("lib_id", "")
    if not project_id or not lib_id:
        return error_envelope("`project_id` and `lib_id` are required")
    project = await _fetch_project(project_id)
    if project is None:
        return error_envelope(
            f"kiserver could not return project_id={project_id}",
            project_id=project_id,
        )

    sheet_uuid = sheet_uuid or _root_sheet_uuid(project)
    if not sheet_uuid:
        return error_envelope("project has no schematic sheets to place into")

    pos_raw = args.get("position_mm") or [0.0, 0.0]
    position_mm = [
        float(pos_raw[0]) if len(pos_raw) > 0 else 0.0,
        float(pos_raw[1]) if len(pos_raw) > 1 else 0.0,
    ]
    rotation_deg = float(args.get("rotation_deg") or 0.0)
    refdes = str(args.get("refdes") or "")
    value = str(args.get("value") or "")
    is_power_symbol = lib_id.startswith("power:")
    is_power_flag = lib_id == "power:PWR_FLAG"

    symbol_uuid = str(uuid.uuid4())
    new_symbol = {
        "uuid": symbol_uuid,
        "sheet_uuid": sheet_uuid,
        "lib_id": lib_id,
        "refdes": refdes,
        "value": value,
        "footprint": "",
        "mpn": "",
        "datasheet": "",
        "position_mm": position_mm,
        "rotation_deg": rotation_deg,
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

    try:
        await kiserver_post(f"/project/{project_id}/replace", {"project": project})
    except Exception as e:
        return error_envelope(
            f"kiserver replace failed: {e}",
            project_id=project_id,
        )
    return envelope(
        {
            "ok": True,
            "project_id": project_id,
            "symbol_uuid": symbol_uuid,
            "refdes": refdes,
            "lib_id": lib_id,
            "sheet_uuid": sheet_uuid,
        }
    )


@tool(
    "kc_symbol_edit",
    "Modify properties of an existing symbol instance (value, refdes, "
    "footprint, mpn, datasheet, position). Returns the edited symbol's "
    "uuid + the list of fields changed.",
    {
        "project_id": str,
        "symbol_uuid": str,
        "refdes": str,
        "value": str,
        "footprint": str,
        "mpn": str,
        "datasheet": str,
        "position_mm": list[float],
        "rotation_deg": float,
    },
)
async def kc_symbol_edit(args: dict[str, Any]) -> dict[str, Any]:
    project_id = args.get("project_id", "")
    symbol_uuid = args.get("symbol_uuid", "")
    if not project_id or not symbol_uuid:
        return error_envelope("`project_id` and `symbol_uuid` are required")
    project = await _fetch_project(project_id)
    if project is None:
        return error_envelope(
            f"kiserver could not return project_id={project_id}",
            project_id=project_id,
        )
    symbols = project.get("schematic", {}).get("symbols", []) or []
    target = next((s for s in symbols if s.get("uuid") == symbol_uuid), None)
    if target is None:
        return error_envelope(
            f"no symbol with uuid={symbol_uuid} on project {project_id}",
            project_id=project_id,
            symbol_uuid=symbol_uuid,
        )
    changed: list[str] = []
    _string_field(target, args, "refdes", changed)
    _string_field(target, args, "value", changed)
    _string_field(target, args, "footprint", changed)
    _string_field(target, args, "mpn", changed)
    _string_field(target, args, "datasheet", changed)
    if "position_mm" in args and args["position_mm"] is not None:
        pos = args["position_mm"]
        new_pos = [
            float(pos[0]) if len(pos) > 0 else 0.0,
            float(pos[1]) if len(pos) > 1 else 0.0,
        ]
        if new_pos != target.get("position_mm"):
            target["position_mm"] = new_pos
            changed.append("position_mm")
    if "rotation_deg" in args and args["rotation_deg"] is not None:
        rot = float(args["rotation_deg"])
        if rot != target.get("rotation_deg"):
            target["rotation_deg"] = rot
            changed.append("rotation_deg")
    # Reflect refdes/value into properties array (M1-R-01 stored both).
    for prop in target.get("properties", []) or []:
        if prop.get("key") == "Reference" and "refdes" in changed:
            prop["value"] = target.get("refdes", "")
        elif prop.get("key") == "Value" and "value" in changed:
            prop["value"] = target.get("value", "")

    try:
        await kiserver_post(f"/project/{project_id}/replace", {"project": project})
    except Exception as e:
        return error_envelope(
            f"kiserver replace failed: {e}",
            project_id=project_id,
        )
    return envelope(
        {
            "ok": True,
            "project_id": project_id,
            "symbol_uuid": symbol_uuid,
            "changed_fields": changed,
        }
    )


async def _fetch_project(project_id: str) -> dict[str, Any] | None:
    try:
        result = await kiserver_get(f"/project/{project_id}")
    except Exception:
        return None
    project = result.get("project")
    if not isinstance(project, dict):
        return None
    return project


def _root_sheet_uuid(project: dict[str, Any]) -> str:
    sheets = project.get("schematic", {}).get("sheets", []) or []
    for s in sheets:
        if s.get("parent") in (None, ""):
            return str(s.get("uuid", ""))
    return str(sheets[0]["uuid"]) if sheets else ""


def _string_field(
    target: dict[str, Any],
    args: dict[str, Any],
    key: str,
    changed: list[str],
) -> None:
    if key not in args or args[key] is None:
        return
    new = str(args[key])
    if new != target.get(key, ""):
        target[key] = new
        changed.append(key)


__all__ = ["kc_symbol_add", "kc_symbol_edit"]
