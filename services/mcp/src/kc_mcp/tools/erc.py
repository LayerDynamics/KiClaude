"""`kc_erc` — drive kicad-cli ERC via the kiconnector subprocess broker."""

from __future__ import annotations

from typing import Any

from claude_agent_sdk import tool

from kc_mcp._envelope import envelope, error_envelope
from kc_mcp.clients import kiconnector_post


@tool(
    "kc_erc",
    "Run KiCad's Electrical Rules Check on a schematic. Returns "
    "{ok, issues:[{severity, sheet, position_mm, type, description}]}. "
    "Read-only (auto-approved by the PreToolUse permission gate).",
    {"project_path": str, "timeout_s": float},
)
async def kc_erc(args: dict[str, Any]) -> dict[str, Any]:
    project_path = args.get("project_path", "")
    if not project_path:
        return error_envelope("`project_path` is required")
    body: dict[str, Any] = {"project_path": project_path}
    timeout_s = args.get("timeout_s")
    if isinstance(timeout_s, (int, float)) and timeout_s > 0:
        body["timeout_s"] = float(timeout_s)
    try:
        result = await kiconnector_post("/tools/erc", body)
    except Exception as e:
        return error_envelope(
            f"kiconnector /tools/erc failed: {e}",
            project_path=project_path,
        )
    payload = dict(result)
    payload.setdefault("ok", True)
    return envelope(payload)


__all__ = ["kc_erc"]
