"""`kc_label_attach` — drop a label on a sheet (M1-P-04)."""

from __future__ import annotations

import uuid
from typing import Any

from claude_agent_sdk import tool

from kc_mcp._envelope import envelope, error_envelope
from kc_mcp.clients import kiserver_get, kiserver_post

_ALLOWED_KINDS = {"local", "global", "hierarchical", "power"}


@tool(
    "kc_label_attach",
    "Attach a label to a sheet. `kind` is one of "
    "{local, global, hierarchical, power}. Returns the new label's uuid.",
    {
        "project_id": str,
        "sheet_uuid": str,
        "kind": str,
        "text": str,
        "position_mm": list[float],
        "rotation_deg": float,
        "shape": str,
    },
)
async def kc_label_attach(args: dict[str, Any]) -> dict[str, Any]:
    project_id = args.get("project_id", "")
    text = args.get("text", "")
    kind = (args.get("kind") or "local").lower()
    if not project_id or not text:
        return error_envelope("`project_id` and `text` are required")
    if kind not in _ALLOWED_KINDS:
        return error_envelope(f"`kind` must be one of {sorted(_ALLOWED_KINDS)}, got {kind!r}")
    try:
        result = await kiserver_get(f"/project/{project_id}")
    except Exception as e:
        return error_envelope(
            f"kiserver could not return project_id={project_id}: {e}",
            project_id=project_id,
        )
    project = result.get("project")
    if not isinstance(project, dict):
        return error_envelope(f"unexpected /project/{project_id} payload")

    sheet_uuid = args.get("sheet_uuid") or _root_sheet_uuid(project)
    if not sheet_uuid:
        return error_envelope("project has no schematic sheets to attach to")

    pos_raw = args.get("position_mm") or [0.0, 0.0]
    position_mm = [
        float(pos_raw[0]) if len(pos_raw) > 0 else 0.0,
        float(pos_raw[1]) if len(pos_raw) > 1 else 0.0,
    ]
    label_uuid = str(uuid.uuid4())
    label = {
        "uuid": label_uuid,
        "sheet_uuid": sheet_uuid,
        "kind": kind,
        "text": text,
        "position_mm": position_mm,
        "rotation_deg": float(args.get("rotation_deg") or 0.0),
        "shape": str(args.get("shape") or ""),
    }
    project.setdefault("schematic", {}).setdefault("labels", []).append(label)
    # If hierarchical, also ensure a matching pin exists on the
    # owning sheet's (sheet …) block (KCIR stores pins on the child
    # sheet itself). This keeps the resolver happy.
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
                        "shape": label["shape"] or "input",
                        "position_mm": position_mm,
                        "rotation_deg": label["rotation_deg"],
                    }
                )
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
            "label_uuid": label_uuid,
            "sheet_uuid": sheet_uuid,
            "kind": kind,
            "text": text,
        }
    )


def _root_sheet_uuid(project: dict[str, Any]) -> str:
    sheets = project.get("schematic", {}).get("sheets", []) or []
    for s in sheets:
        if s.get("parent") in (None, ""):
            return str(s.get("uuid", ""))
    return str(sheets[0]["uuid"]) if sheets else ""


__all__ = ["kc_label_attach"]
