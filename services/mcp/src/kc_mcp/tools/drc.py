"""`kc_drc` — drive `kicad-cli pcb drc` via the kiconnector broker
(M2-P-04). Read-only — auto-approved by the PreToolUse permission gate.
"""

from __future__ import annotations

from typing import Any

from claude_agent_sdk import tool

from kc_mcp._envelope import envelope, error_envelope
from kc_mcp.clients import kiconnector_post


@tool(
    "kc_drc",
    "Run KiCad's Design Rules Check on a PCB. Returns "
    "{ok, issues:[{severity, layer, position_mm, type, description}]}. "
    "kicad-cli is the source of truth (SPEC D8); the Rust live-overlay "
    "DRC kernel is for editor feedback only. Read-only.",
    {
        "pcb_path": str,
        "timeout_s": float,
        "severity_min": str,
    },
)
async def kc_drc(args: dict[str, Any]) -> dict[str, Any]:
    pcb_path = args.get("pcb_path", "")
    if not pcb_path:
        return error_envelope("`pcb_path` is required")
    body: dict[str, Any] = {"pcb_path": pcb_path}
    timeout_s = args.get("timeout_s")
    if isinstance(timeout_s, (int, float)) and timeout_s > 0:
        body["timeout_s"] = float(timeout_s)
    severity_min = args.get("severity_min")
    if isinstance(severity_min, str) and severity_min:
        body["severity_min"] = severity_min
    try:
        result = await kiconnector_post("/tools/drc", body)
    except Exception as e:
        return error_envelope(
            f"kiconnector /tools/drc failed: {e}",
            pcb_path=pcb_path,
        )
    payload = dict(result)
    payload.setdefault("ok", True)
    return envelope(payload)


__all__ = ["kc_drc"]
