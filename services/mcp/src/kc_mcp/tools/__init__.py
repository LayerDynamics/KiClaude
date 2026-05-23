"""kiclaude MCP tools.

Each tool lives in its own module so the registry pattern in
[`kc_mcp.server`][kc_mcp.server] can collect them without coupling the
implementations to each other.
"""

from .ping import kc_ping

__all__ = ["kc_ping"]
