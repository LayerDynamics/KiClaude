# ki-mcp-pcb-server

MCP server (FastMCP) that exposes the `ki-mcp-pcb` pipeline as tools for Claude Code.

Tools are stateless — board files on disk are the state. Each tool takes paths/arguments in, returns structured JSON out. Claude does the narration.

Run with:

```bash
uv run ki-mcp-pcb-server
```

For local Claude Code, the repo's `.claude/settings.json` auto-registers this server.
