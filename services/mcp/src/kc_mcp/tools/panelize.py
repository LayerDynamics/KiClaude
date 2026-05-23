"""`kc_panelize` — drive `kikit panelize` via kiconnector (M2-P-04).

Returns the path to the produced panel `.kicad_pcb`. KiKit reads a
JSON / preset config describing the panel layout (grid, mousebites,
tab style, etc.). The MCP tool accepts either a path to a saved
preset or an inline config dict that kiconnector serializes to disk
before invoking kikit.
"""

from __future__ import annotations

from typing import Any

from claude_agent_sdk import tool

from kc_mcp._envelope import envelope, error_envelope
from kc_mcp.clients import kiconnector_post


@tool(
    "kc_panelize",
    "Generate a panel from a board using KiKit. Returns "
    "{ok, panel_path, log}. Accepts an inline `config` dict (KiKit "
    "JSON preset format) or a `preset_path` pointing at a saved one. "
    "Spec FR-035.",
    {
        "pcb_path": str,
        "output_path": str,
        "config": dict,
        "preset_path": str,
        "timeout_s": float,
    },
)
async def kc_panelize(args: dict[str, Any]) -> dict[str, Any]:
    pcb_path = args.get("pcb_path", "")
    output_path = args.get("output_path", "")
    if not pcb_path or not output_path:
        return error_envelope("`pcb_path` and `output_path` are required")
    config = args.get("config")
    preset_path = args.get("preset_path")
    if not config and not preset_path:
        return error_envelope("either `config` (inline KiKit JSON) or `preset_path` is required")
    body: dict[str, Any] = {
        "pcb_path": pcb_path,
        "output_path": output_path,
    }
    if isinstance(config, dict) and config:
        body["config"] = config
    if isinstance(preset_path, str) and preset_path:
        body["preset_path"] = preset_path
    timeout_s = args.get("timeout_s")
    if isinstance(timeout_s, (int, float)) and timeout_s > 0:
        body["timeout_s"] = float(timeout_s)
    try:
        result = await kiconnector_post("/tools/panelize", body)
    except Exception as e:
        return error_envelope(
            f"kiconnector /tools/panelize failed: {e}",
            pcb_path=pcb_path,
        )
    payload = dict(result)
    payload.setdefault("ok", True)
    payload.setdefault("pcb_path", pcb_path)
    payload.setdefault("output_path", output_path)
    return envelope(payload)


__all__ = ["kc_panelize"]
