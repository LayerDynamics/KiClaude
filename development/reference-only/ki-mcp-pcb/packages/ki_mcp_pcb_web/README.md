# ki-mcp-pcb-web

Optional browser viewer for `ki-mcp-pcb`. Drop a CIR YAML (or `.ato`) in and see:

- Validation report (every CIR code, severity-colored)
- Component table with MPN + footprint + LCSC
- Net map (name + class + members)
- BOM (grouped by part)
- Impedance + length-tuning check status
- KiCanvas-rendered PCB preview when build artifacts exist

```bash
uv sync --extra web
uv run kimp serve --port 8765
# open http://localhost:8765
```

Stateless: state lives in the working directory and the user's KiCad install. The server is just a thin HTTP wrapper over the same core library the CLI and MCP server use.
