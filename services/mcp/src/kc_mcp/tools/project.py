"""`kc_project_open` + `kc_project_save` (M1-P-04)."""

from __future__ import annotations

from typing import Any

from claude_agent_sdk import tool

from kc_mcp._envelope import envelope, error_envelope
from kc_mcp.clients import kiserver_post


@tool(
    "kc_project_open",
    "Open a KiCad project directory and return its KCIR summary + "
    "project_id. The project_id is used by every subsequent kc_* call.",
    {"path": str},
)
async def kc_project_open(args: dict[str, Any]) -> dict[str, Any]:
    path = args.get("path", "")
    if not path:
        return error_envelope("`path` is required")
    try:
        result = await kiserver_post(
            "/project/open",
            {"path": path, "view": ["summary", "metadata"]},
        )
    except Exception as e:
        return error_envelope(f"kiserver /project/open failed: {e}", path=path)
    payload = dict(result)
    payload["ok"] = True
    return envelope(payload)


@tool(
    "kc_project_save",
    "Write the current KCIR state back to disk for an opened project. "
    "Idempotent. Returns the list of files written.",
    {"project_id": str, "target_dir": str},
)
async def kc_project_save(args: dict[str, Any]) -> dict[str, Any]:
    project_id = args.get("project_id", "")
    target_dir = args.get("target_dir") or None
    if not project_id:
        return error_envelope("`project_id` is required")
    body: dict[str, Any] = {}
    if target_dir:
        body["target_dir"] = target_dir
    try:
        result = await kiserver_post(f"/project/{project_id}/save", body)
    except Exception as e:
        return error_envelope(
            f"kiserver /project/{project_id}/save failed: {e}",
            project_id=project_id,
        )
    payload = dict(result)
    payload["ok"] = True
    return envelope(payload)


__all__ = ["kc_project_open", "kc_project_save"]
