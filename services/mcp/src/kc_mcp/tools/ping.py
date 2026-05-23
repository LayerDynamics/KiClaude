"""`kc_ping` — M0 sanity tool.

Returns the kiclaude version triple plus a discovered `kicad-cli`
version (or `"not installed"`) so the agent can verify that:

1. the MCP server is reachable;
2. the `ki_native` PyO3 module is loadable on this host;
3. `kicad-cli` is on `PATH` (needed for ERC/DRC/gerber gates).
"""

from __future__ import annotations

import asyncio
import shutil
from datetime import UTC, datetime
from typing import Any

from claude_agent_sdk import tool

from kc_mcp._version import __version__ as kc_mcp_version


async def _kicad_cli_version() -> str:
    """Return the `kicad-cli --version` output or `"not installed"`."""
    binary = shutil.which("kicad-cli")
    if binary is None:
        return "not installed"
    try:
        proc = await asyncio.create_subprocess_exec(
            binary,
            "--version",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5.0)
    except (TimeoutError, FileNotFoundError, PermissionError):
        return "not installed"
    return stdout.decode("utf-8", errors="replace").strip() or "unknown"


def _kcir_version() -> str:
    """Best-effort native KCIR version. Falls back to `"unknown"` if
    `ki_native` is not built / installed (Python-only test runs)."""
    try:
        import ki_native  # type: ignore[import-not-found]

        return str(getattr(ki_native, "kcir_version", "unknown"))
    except ImportError:
        return "unknown"


@tool(
    "kc_ping",
    "Ping the kiclaude MCP server. Returns ok, kc_mcp version, KCIR "
    "version, kicad-cli version (or 'not installed'), and a UTC timestamp.",
    {},
)
async def kc_ping(_args: dict[str, Any]) -> dict[str, Any]:
    """The kiclaude MCP sanity tool. Takes no arguments.

    Returns a dict shaped::

        {
            "content": [{"type": "text", "text": "<json blob>"}]
        }

    where the JSON blob carries `{ok, version, kcir_version,
    kicad_cli_version, ts}`. The Claude Agent SDK wraps any plain dict
    return value in this MCP-style envelope automatically.
    """
    payload = {
        "ok": True,
        "version": kc_mcp_version,
        "kcir_version": _kcir_version(),
        "kicad_cli_version": await _kicad_cli_version(),
        "ts": datetime.now(UTC).isoformat(),
    }
    return {
        "content": [
            {"type": "text", "text": _json(payload)},
        ],
        "structured": payload,
    }


def _json(obj: dict[str, Any]) -> str:
    """Local `json.dumps` wrapper — keeps the import explicit at the
    use site so refactors don't accidentally drop sort/indent settings."""
    import json

    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


__all__ = ["kc_ping"]
