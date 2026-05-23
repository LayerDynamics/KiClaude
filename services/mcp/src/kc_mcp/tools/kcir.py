"""`kc_kcir_get` — fetch the KCIR view of an opened project (M1-P-04)."""

from __future__ import annotations

from typing import Any

from claude_agent_sdk import tool

from kc_mcp._envelope import envelope, error_envelope
from kc_mcp.clients import kiserver_get

_ALLOWED_VIEWS = {"summary", "pcb", "schematic", "metadata", "full"}


@tool(
    "kc_kcir_get",
    "Return slice(s) of the KCIR project view. `view` is a list of "
    "{summary, pcb, schematic, metadata, full}; defaults to ['summary'].",
    {"project_id": str, "view": list[str]},
)
async def kc_kcir_get(args: dict[str, Any]) -> dict[str, Any]:
    project_id = args.get("project_id", "")
    raw_view = args.get("view") or ["summary"]
    view = [v for v in raw_view if v in _ALLOWED_VIEWS]
    if not project_id:
        return error_envelope("`project_id` is required")
    if not view:
        return error_envelope(
            f"none of `view`={raw_view} are valid; pick from {sorted(_ALLOWED_VIEWS)}"
        )
    try:
        result = await kiserver_get(f"/project/{project_id}")
    except Exception as e:
        return error_envelope(
            f"kiserver GET /project/{project_id} failed: {e}",
            project_id=project_id,
        )
    project = result.get("project", {})
    payload: dict[str, Any] = {
        "ok": True,
        "project_id": project_id,
        "view": view,
    }
    if "summary" in view:
        payload["summary"] = result.get("summary", {})
    if "pcb" in view:
        payload["pcb"] = project.get("pcb", {})
    if "schematic" in view:
        payload["schematic"] = project.get("schematic", {})
    if "metadata" in view:
        payload["metadata"] = project.get("metadata", {})
    if "full" in view:
        payload["project"] = project
    return envelope(payload)


__all__ = ["kc_kcir_get"]
