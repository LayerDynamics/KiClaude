"""kiclaude in-process MCP server — exposes kc_* tools.

Public API:

- [`build_server()`][build_server] — returns an
  [`McpSdkServerConfig`][claude_agent_sdk.McpSdkServerConfig] suitable
  for [`ClaudeAgentOptions.mcp_servers`][claude_agent_sdk.ClaudeAgentOptions].
- [`kc_ping`][kc_ping] — the M0 sanity tool. Returns the version
  triple `{ok, version, kcir_version, kicad_cli_version}` plus the
  current timestamp so the agent can verify the server is alive and
  that kicad-cli is discoverable on this host.
"""

from ._version import __version__
from .server import build_server
from .tools.ping import kc_ping

__all__ = ["__version__", "build_server", "kc_ping"]
