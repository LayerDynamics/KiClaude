"""ki-mcp-pcb MCP server.

Built on FastMCP from the official ``mcp`` package. Tool surface mirrors
SPEC.md §5.3. Every tool here has a 1:1 counterpart in ``kimp`` CLI.

The actual tool logic lives in ``ki_mcp_pcb_server.tools`` so it can be
unit-tested without the ``mcp`` transport. This file is just the wiring.
"""

from __future__ import annotations

from ki_mcp_pcb_server import tools as t

try:
    from mcp.server.fastmcp import FastMCP
except ImportError as exc:  # pragma: no cover - import is required at runtime
    raise ImportError(
        "ki-mcp-pcb-server requires the `mcp` package. Run `uv sync` to install."
    ) from exc


mcp = FastMCP("ki-mcp-pcb")

# Each MCP tool wraps a plain function from ``tools.py``. Keep the
# wrappers thin — anything more than a delegate belongs in tools.py.

mcp.tool()(t.tool_version)
mcp.tool()(t.tool_validate_cir)
mcp.tool()(t.tool_parse_intent)
mcp.tool()(t.tool_synthesize)
mcp.tool()(t.tool_place)
mcp.tool()(t.tool_route)
mcp.tool()(t.tool_drc)
mcp.tool()(t.tool_erc)
mcp.tool()(t.tool_export_fab)
mcp.tool()(t.tool_build)
mcp.tool()(t.tool_doctor)
mcp.tool()(t.tool_decoupling_check)
mcp.tool()(t.tool_partition_check)
mcp.tool()(t.tool_impedance_check)
mcp.tool()(t.tool_return_path_check)
mcp.tool()(t.tool_length_tuning)
mcp.tool()(t.tool_ddr_topology_check)
mcp.tool()(t.tool_bga_fanout_check)
mcp.tool()(t.tool_diff)
mcp.tool()(t.tool_autoplace)


def main() -> None:
    """Run the server over stdio (default Claude Code transport)."""
    mcp.run()


if __name__ == "__main__":  # pragma: no cover
    main()
