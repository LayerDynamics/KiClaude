"""`kc_route_freerouting` — drive Freerouting via kiconnector (M2-P-04).

The kiconnector wrapper exports DSN via `kicad-cli pcb export dsn`,
runs Freerouting headless to produce a `.ses` file, then imports the
SES back via `kicad-cli pcb import ses`. This tool just kicks off
the round-trip; the editor re-runs DRC after the import lands.

Freerouting is GPL-licensed and ships as a `.jar`; we sandbox via
subprocess only (no JNI), so kiclaude's MIT/Apache license posture
is preserved (spec NFR-009).
"""

from __future__ import annotations

from typing import Any

from claude_agent_sdk import tool

from kc_mcp._envelope import envelope, error_envelope
from kc_mcp.clients import kiconnector_post


@tool(
    "kc_route_freerouting",
    "Auto-route a PCB via Freerouting. Exports DSN, runs Freerouting "
    "headless, imports SES back. Returns {ok, ses_path, log}. "
    "Spec FR-027 + NFR-009 (no GPL contamination).",
    {
        "pcb_path": str,
        "freerouting_jar": str,
        "timeout_s": float,
        "passes": int,
    },
)
async def kc_route_freerouting(args: dict[str, Any]) -> dict[str, Any]:
    pcb_path = args.get("pcb_path", "")
    if not pcb_path:
        return error_envelope("`pcb_path` is required")
    body: dict[str, Any] = {"pcb_path": pcb_path}
    jar = args.get("freerouting_jar")
    if isinstance(jar, str) and jar:
        body["freerouting_jar"] = jar
    passes = args.get("passes")
    if isinstance(passes, int) and passes > 0:
        body["passes"] = passes
    timeout_s = args.get("timeout_s")
    if isinstance(timeout_s, (int, float)) and timeout_s > 0:
        body["timeout_s"] = float(timeout_s)
    try:
        result = await kiconnector_post("/tools/freerouting", body)
    except Exception as e:
        return error_envelope(
            f"kiconnector /tools/freerouting failed: {e}",
            pcb_path=pcb_path,
        )
    payload = dict(result)
    payload.setdefault("ok", True)
    payload.setdefault("pcb_path", pcb_path)
    return envelope(payload)


__all__ = ["kc_route_freerouting"]
