"""Build the kiclaude in-process MCP server config.

[`build_server`][build_server] gathers every `kc_*` tool defined in
[`kc_mcp.tools`][kc_mcp.tools] and assembles them into an
[`McpSdkServerConfig`][claude_agent_sdk.McpSdkServerConfig] via
[`claude_agent_sdk.create_sdk_mcp_server`][claude_agent_sdk.create_sdk_mcp_server].
The agent service (M0-P-06) reads the returned config into
`ClaudeAgentOptions.mcp_servers`.
"""

from __future__ import annotations

from typing import Any

from claude_agent_sdk import McpSdkServerConfig, create_sdk_mcp_server

from ._version import __version__
from .tools.diff import kc_diff
from .tools.drc import kc_drc
from .tools.erc import kc_erc
from .tools.export import kc_export_fab
from .tools.freerouting import kc_route_freerouting
from .tools.kcir import kc_kcir_get
from .tools.label import kc_label_attach
from .tools.mpn import kc_mpn_resolve
from .tools.netclass import kc_netclass_list, kc_netclass_set
from .tools.panelize import kc_panelize
from .tools.ping import kc_ping
from .tools.place import kc_footprint_place_hint, kc_footprint_remove
from .tools.project import kc_project_open, kc_project_save
from .tools.route import kc_track_remove, kc_track_route
from .tools.snapshot import kc_snapshot_create, kc_snapshot_revert
from .tools.symbol import kc_symbol_add, kc_symbol_edit
from .tools.validate import kc_validate
from .tools.via import kc_via_add_hint
from .tools.wire import kc_wire_connect
from .tools.zone import kc_zone_request

# Claude-facing tools registered with the kiclaude MCP server:
# `kc_ping` + 11 M1-P-04 schematic tools + 13 M2-P-04 PCB tools.
_CLAUDE_TOOLS = [
    kc_ping,
    kc_project_open,
    kc_project_save,
    kc_kcir_get,
    kc_validate,
    kc_erc,
    kc_symbol_add,
    kc_symbol_edit,
    kc_wire_connect,
    kc_label_attach,
    kc_mpn_resolve,
    kc_snapshot_create,
    kc_snapshot_revert,
    # M2-P-04 PCB tools (13).
    kc_drc,
    kc_footprint_place_hint,
    kc_footprint_remove,
    kc_track_route,
    kc_track_remove,
    kc_via_add_hint,
    kc_zone_request,
    kc_netclass_set,
    kc_netclass_list,
    kc_export_fab,
    kc_panelize,
    kc_route_freerouting,
    kc_diff,
]


def assert_no_ui_tools_in_claude_registry(tools: list[Any]) -> None:
    """Enforce SPEC §1.4 #4 — UI-only tools MUST NOT reach Claude.

    Runs at boot inside [`build_server`][build_server]; any callable
    / `SdkMcpTool` whose name starts with `ui_` aborts the process
    with a `RuntimeError` rather than letting a raw-coordinate tool
    leak into the Claude-facing MCP server.
    """
    bad: list[str] = []
    for tool_obj in tools:
        name = getattr(tool_obj, "name", None) or getattr(tool_obj, "__name__", "")
        if isinstance(name, str) and name.startswith("ui_"):
            bad.append(name)
    if bad:
        raise RuntimeError(
            "UI-only tools must not be registered with the Claude MCP "
            f"server (SPEC §1.4 #4). Offenders: {bad}"
        )


def build_server(name: str = "kiclaude", version: str | None = None) -> McpSdkServerConfig:
    """Construct the kiclaude MCP server config.

    Args:
        name: Server identifier. Defaults to ``"kiclaude"``; the agent
            service reads tools off the matching key in
            ``mcp_servers``.
        version: Server semver string. Defaults to the package version.

    Returns:
        An [`McpSdkServerConfig`][claude_agent_sdk.McpSdkServerConfig]
        ready to drop into
        [`ClaudeAgentOptions.mcp_servers`][claude_agent_sdk.ClaudeAgentOptions].
    """
    assert_no_ui_tools_in_claude_registry(_CLAUDE_TOOLS)
    return create_sdk_mcp_server(
        name=name,
        version=version or __version__,
        tools=_CLAUDE_TOOLS,
    )


__all__ = ["assert_no_ui_tools_in_claude_registry", "build_server"]
