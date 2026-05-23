"""Entry point for `python -m kc_mcp.stdio`.

The kiclaude CLI's `kiclaude mcp stdio` subcommand shells out to this
module. It runs the kc_mcp `build_server()` via the stdio MCP
transport from the `mcp` Python SDK.
"""

from __future__ import annotations

import sys


def main() -> int:
    """Stdio entry point. Returns the process exit code."""
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError:  # pragma: no cover — surface as a clear error
        sys.stderr.write(
            "kc_mcp.stdio: the `mcp` Python SDK is not installed; "
            "run `uv sync --all-packages` from the kiclaude repo root.\n"
        )
        return 127

    from kc_mcp._version import __version__
    from kc_mcp.tools.ping import kc_ping

    server: FastMCP = FastMCP("kiclaude", version=__version__)

    @server.tool()
    async def kc_ping_tool() -> dict[str, object]:
        """kiclaude liveness probe — returns versions + timestamp."""
        result = await kc_ping.handler({})  # type: ignore[attr-defined]
        return result.get("structured", result)

    server.run("stdio")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
