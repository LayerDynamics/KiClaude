# ki-mcp-pcb

> Turn plain text into a manufacturable PCB. Library, CLI, and MCP server in one repo — primarily driven through Claude Code.

**Status:** Pre-alpha (v0.0.1 scaffold). See [`SPEC.md`](./SPEC.md) for the full project specification.

```
text / .ato / yaml
       │
       ▼
   ┌─────────┐    ┌──────────────┐    ┌─────────────┐    ┌────────────┐
   │  Parse  │ ─▶ │  CIR (typed) │ ─▶ │  Synthesize │ ─▶ │  Place &   │ ─▶ Gerbers,
   │ (NL/DSL)│    │  electrical  │    │  KiCad sch  │    │  Route     │    BOM, STEP
   └─────────┘    │  model       │    │  + netlist  │    │  + DRC     │
                  └──────────────┘    └─────────────┘    └────────────┘
```

## Why

LLMs can describe a circuit. They cannot reliably emit a binary KiCad file. This project draws a sharp line between LLM-friendly text (natural language → `.ato` DSL → typed CIR) and deterministic tooling (CIR → KiCad → Gerbers). Every step is exposed as both a CLI command and an MCP tool so Claude Code can drive the pipeline end-to-end.

## Shapes shipped

| Package | What it is |
|---|---|
| `ki_mcp_pcb_core` | Python library — CIR schema, parsers, synthesis, placement, routing, validation, export, signal integrity, sourcing |
| `ki_mcp_pcb_cli` | `kimp` command-line tool |
| `ki_mcp_pcb_server` | MCP server (FastMCP) — same surface as the CLI, for Claude Code |
| `ki_mcp_pcb_web` | FastAPI server + single-page browser viewer with KiCanvas previews |
| `.claude/` | Slash commands + skill so a `claude` session in this repo just works |

## Quick start

```bash
# install (LLM-driven NL parser is in the optional 'llm' extra)
uv sync --all-extras

# verify your environment (kicad-cli, kiutils, pcbnew, freerouting, java)
uv run kimp doctor

# natural language → CIR YAML draft (requires ANTHROPIC_API_KEY)
uv run kimp ask "ESP32-S3 dev board with USB-C and one status LED" --draft my.yaml

# validate a CIR YAML spec
uv run kimp validate examples/blinky.yaml

# end-to-end: CIR → KiCad project → populated PCB → DRC → JLC fab zip
# (no manual KiCad step — pcbnew handles the netlist import)
uv run kimp build examples/blinky.yaml --out build/blinky/

# diff two CIR sources (or round-tripped KiCad project)
uv run kimp diff before.yaml after.yaml

# or, inside Claude Code in this repo:
#   /pcb-new "ESP32-S3 dev board with USB-C and 4 GPIO breakouts"
```

The build output lands in `build/blinky/`:
- `*.kicad_pro` / `*.kicad_sch` / `*.kicad_pcb` — KiCad project, ready to open
- `*.net` — KiCad netlist
- `fab/<board>-jlcpcb.zip` — Gerbers, drill, P&P, BOM, ready to upload

## Mixed-signal (M2)

```bash
# 4-layer STM32 + audio codec — partitions, decoupling, length matching
uv run kimp validate examples/stm32_audio.yaml
uv run kimp build examples/stm32_audio.yaml --out build/stm32-audio/ --route
```

The board declares `digital` / `analog` / `power` partitions, a ferrite-bead bridge between the digital 3V3 and analog AVDD rails, and an I2S bus marked as a reviewed cross-partition crossing. The pipeline catches missing decoupling (CIR030), malformed length-match groups (CIR040), and unintended partition crossings (CIR050).

## High-speed (M3)

```bash
# 4-layer STM32 + USB HS PHY + Ethernet PHY — diff pairs, controlled Z, length match
uv run kimp validate examples/usb_eth_phy.yaml
uv run kimp build examples/usb_eth_phy.yaml --out build/usb-eth/ --route
```

Three differential pairs (USB±, ETH TX±, ETH RX±), each with declared target impedance (90 Ω USB, 100 Ω Ethernet) and reference plane. Per-net trace widths and spacings are set so the IPC-2141 / Hammerstad impedance solver lands on the targets. Post-route, the length-tuning analyzer compares measured trace lengths against the declared `length_match_group` tolerance and emits a queue of nets to shorten/lengthen.

## RF / DDR / BGA (M4 — co-pilot)

```bash
# ESP32-C6 + 2.4 GHz CPWG antenna feed + DDR3L fly-by sketch
uv run kimp validate examples/esp32_c6_rf.yaml
```

Exercises the Wadell/Wen CPWG impedance solver (50 Ω, w=0.40 mm, gap=0.30 mm), the DDR fly-by topology validator (CIR100, ordered controller → DRAM → terminator), and the BGA fanout feasibility check (CIR110 vs `libs/bga_fanout.yaml`). M4 features require explicit `Board.signoff.{rf,ddr,bga_fanout}_reviewed=true` to suppress the "needs human EE review" warnings — autonomous fab is not the M4 contract.

## Roadmap

See [`SPEC.md §6`](./SPEC.md#6-scope-by-milestone). M0 (foundations) is what's scaffolded here. M1 (hobbyist 2-layer end-to-end) is the first user-facing milestone.

## Web viewer

```bash
uv sync --extra web
uv run kimp serve --port 8765
# open http://localhost:8765
```

Drop a CIR file in the browser; the viewer shows validation, components, nets, BOM, and impedance-check results in real time. A separate tab embeds KiCanvas for `.kicad_pcb` previews. The viewer is a thin HTTP wrapper around the same core library that powers `kimp build` — no logic duplication.

### GUI co-pilot

The browser GUI (`packages/ki_mcp_pcb_gui`) adds a Claude co-pilot pane so a
user can drive the whole text → PCB pipeline without a terminal. The co-pilot
needs the Claude Agent SDK, an optional extra:

```bash
uv sync --extra web --extra agent   # or: pip install 'ki-mcp-pcb-web[agent]'
```

Without the `agent` extra, `kimp serve` still runs — the pipeline panes work
and the chat pane shows a "co-pilot unavailable" message. Irreversible actions
the co-pilot takes (writing the working CIR, exporting a fab package, and any
shell command that touches the CIR file) are gated behind an approve/reject
prompt enforced server-side.

For local development of the GUI itself (Vite HMR + the FastAPI backend in one
process group), one command brings both up and tears both down on exit:

```bash
uv run ki-mcp-pcb-gui            # = `python packages/ki_mcp_pcb_gui/start.py dev`
# backend on http://127.0.0.1:8765 ; Vite on http://localhost:5173
# pass `--no-backend` if you'd rather run `uv run ki-mcp-pcb-web` yourself
```

## Roadmap & changelog

Milestones M0–M4 are closed (see [`SPEC.md`](./SPEC.md) and
[`CHANGELOG.md`](./CHANGELOG.md)). Next reasonable directions:
real Octopart/Mouser/Digikey enrichment alongside the JLC layer,
SnapEDA auto-pull for parts outside the registry, schematic
auto-layout so the emitted `.kicad_sch` is visually clean rather than
just structurally valid.

## License

MIT — see [`LICENSE`](./LICENSE).
