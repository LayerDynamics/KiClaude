"""ki-mcp-pcb MCP server.

The server entrypoint (``main``) lives in :mod:`ki_mcp_pcb_server.server`
and depends on the ``mcp`` package being installed. The pure tool logic
in :mod:`ki_mcp_pcb_server.tools` has no such dependency and can be
imported in isolation — that's what the contract tests do.

We intentionally do NOT re-export ``main`` here so importing the package
doesn't trigger the FastMCP import.
"""
