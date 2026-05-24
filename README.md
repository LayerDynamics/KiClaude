# kiclaude

**Claude Code, but for circuit boards.**

kiclaude is a browser-native, AI-native, KiCad-compatible EDA suite. You open a
`.kicad_pro` project in a browser tab, chat with Claude in the sidebar, and
every action — *"add a USB-C PD trigger for 9 V, with proper decoupling and a
fuse"*, *"route the high-speed pairs first, then power"*, *"is this board ready
to fab?"* — becomes a typed tool call that round-trips back to real
`.kicad_sch` / `.kicad_pcb` files on disk. Diffable. Reviewable. Yours.

Every chat lives next to a fully-featured visual editor: selection, drag,
multi-layer routing, zone fills, DRC overlays. Claude is a first-class
collaborator, not a chrome strip glued onto someone else's UI.

---

## Why this exists

Designing a PCB today forces a hard choice:

- **KiCad** is open and file-clean, but every action is manual. AI plugins are
  second-class citizens.
- **Flux / cloud EDA** has the AI, but your files live in a vendor cloud and you
  pay rent on your own designs.
- **tscircuit** is text-first (great for AI), but you lose the visual review
  that humans actually need.

kiclaude lives in the empty quadrant: **browser-native, open file format,
AI-native, local-first.** The files stay on your disk. The AI ships in the box.
The visual editor is real. None of those are bolted on.

## What's in the box

- **A real KiCad-format engine**, in Rust + WASM — schematic parser, PCB
  parser, deterministic emitter (round-trip-or-die, gated in CI), KCIR
  canonical intermediate representation, geometry kernel, R-tree index, DRC.
- **A typed MCP tool catalog** (`kc_*` for Claude, `ui_*` for the editor) so
  every edit is auditable and gated. Claude doesn't get raw `Write` access to
  your `.kicad_pcb` — it gets `kc_footprint_place_hint("near MCU, south edge")`
  and the placer resolves it.
- **A persistent chat sidebar** powered by the Claude Agent SDK, with session
  resume, hook-gated approvals, and a journal of every tool call (with revert).
- **The same `.claude/` artifacts work everywhere.** Slash commands like
  `/add-mcu esp32-s3`, `/pcb-review`, `/diffpair`, `/route-power`, `/bom-price`,
  `/pcb-fab` run from the in-app sidebar **and** from Claude Code on the CLI.
- **Headless `kiclaude` CLI** so anything Claude can do, CI can do.
- **A three.js board viewer** (`kithree`) for the 3D crowd.
- **An optional local daemon** (`kiconnector`) so Firefox and Safari users get
  filesystem access without waiting for FSA parity.

## Eight rules we won't break

1. The **KiCad file format** is the contract. Round-trip fidelity is a CI gate.
2. The **KCIR** is the in-memory contract — Rust-defined, TS + JSON Schema
   mirrors generated from it.
3. Claude operates through **typed tools, never free-form file edits**.
4. Claude reasons in **declarative hints**; raw coordinates are UI-only.
5. **Every MPN must resolve** against a real distributor. No hallucinated parts.
6. MCP tools are **stateless**. State lives in files.
7. **No prose from MCP tools** — structured JSON only; Claude narrates on top.
8. **Local-first, cloud-optional.** Anthropic API key is the only network
   dependency, and that's opt-in at first run.

If a future PR violates one of these, that's the PR that needs to lose. Not the
rule.

## Architecture, at a squint

```
                +-------------------------------+
                |  client/  (React + Vite +     |
                |  Radix + Tailwind + Zustand)  |
                |  kicanvas + kithree viewports |
                +---------------+---------------+
                                | WebSocket + REST :8080
                +---------------v---------------+
                |  services/server  (Hono GW)   |
                +---+-----------+-----------+---+
                    |           |           |
            :8082   |   :8083   |   :8084   |
       +------------v+ +--------v---+ +-----v----------+
       |   agent     | |  kiserver  | |  kiconnector   |
       | (Claude SDK)| | (PyO3 + ki)| | (kicad-cli)    |
       +------+------+ +-----+------+ +----------------+
              |              |
              | in-process MCP (kc_* tools)
              v              v
       +------------------------------+
       |  services/mcp                |
       |  + crates/ki  (KCIR, format) |
       |  + crates/cad (geom, DRC)    |
       +------------------------------+
                       ^
                       |  round-trip
                       v
              .kicad_pro / .kicad_sch / .kicad_pcb
```

| Path | What lives there |
|---|---|
| `crates/ki/` | KCIR types, S-expression lexer/parser, KiCad 9 emitter, PyO3 + wasm bindings |
| `crates/cad/` | Geometry, R-tree index, DRC kernel, length-match, impedance solver, 3D scene |
| `services/agent/` | FastAPI driver around the Claude Agent SDK (`:8082`) |
| `services/mcp/` | In-process MCP server with all `kc_*` and `ui_*` tools |
| `services/kiserver/` | FastAPI surface around the PyO3-loaded `ki_native` (`:8083`) |
| `services/kiconnector/` | Local broker for `kicad-cli` + Freerouting (`:8084`) |
| `services/server/` | Hono WebSocket + REST gateway (`:8080`) |
| `client/` | The browser editor (React 19, Vite, Tailwind, Zustand) |
| `packages/cli/` | The `kiclaude` Node CLI |
| `packages/kithree/` | three.js board viewer |
| `examples/blinky/` | Reference project the round-trip CI runs against |

## Quick start

```bash
# Rust
cargo test --workspace
cargo clippy --workspace --all-targets -- -D warnings

# Wasm bindings (consumed by client/)
wasm-pack build --target web crates/ki
wasm-pack build --target web crates/cad

# Python services
uv sync --all-extras
uv run pytest

# Node side
pnpm install
pnpm -r typecheck
pnpm -F client dev          # browser editor on http://localhost:5173

# CLI
pnpm -F @kiclaude/cli build
kiclaude --version
kiclaude mcp stdio          # talk to the MCP server from any MCP-capable host
kiclaude validate examples/blinky
```

For end-to-end Playwright smoke (gated on `ANTHROPIC_API_KEY`):

```bash
pnpm -F e2e test
```

## A taste of the slash commands

These ship in `.claude/commands/` and work both in-app and in Claude Code:

| Command | What it does |
|---|---|
| `/add-mcu esp32-s3` | Adds an MCU + USB + LDO + decoupling, ERC-clean |
| `/add-power buck-3v3-from-vbus` | Drops in a complete power subsystem |
| `/add-decoupling` | Audits every IC for missing bypass caps, proposes one per pin |
| `/diffpair` | Declares a diff pair, solves for impedance, routes it |
| `/route-power` | Routes power nets at net-class width with per-net approvals |
| `/route-signals` | Walk-around routes signal nets in dependency order |
| `/length-match` | Analyses declared length-match groups, queues serpentine tuning |
| `/explore-placements` | Spawns a sub-agent that tries N placements, ranks by track length × clearance headroom |
| `/erc-fix` / `/drc-fix` | Reads the highest-severity issue, proposes one fix, applies on approval, loops |
| `/pcb-review` | Full M3 design review: stackup, net classes, diff pairs, length match, decoupling, DRC |
| `/pcb-fab` | DFM dry-run, then full fab bundle (gerbers + drill + P&P + BOM) |
| `/bom-price` | Fan-out across Octopart / Mouser / Digi-Key / JLCPCB, returns cheapest mix |

## Status

We're tracking the [`M0–M3` plan](docs/plans/2026-05-21-kiclaude-m0-m3.md) —
139 of 146 tasks done as of this writing. The remaining set is the push-and-shove
router (the bottleneck), three live-distributor adapters, and a couple of UI gestures.
The full spec lives at [`docs/specs/SPEC-01-kiclaude.md`](docs/specs/SPEC-01-kiclaude.md);
if it disagrees with this README, the spec wins.

## License

Apache-2.0. The Freerouting integration runs as a separately-installed
subprocess so the GPL doesn't bleed into the rest of the tree
(see `NFR-009` in the spec, and `scripts/license_audit.sh` in CI).

## Contributing

The bar:

- `cargo test --workspace` is green
- `uv run pytest` is green
- `pnpm -r typecheck && pnpm -r test` are green
- Golden round-trip (`tests/golden/`) is green — if it isn't, the file emitter
  is wrong, even if KiCad opens the output
- New MCP tools land in the typed registry with input/output schemas and a
  note on whether they're Claude-facing (declarative) or UI-only (coordinate)

Open an issue, open a PR, or — easiest of all — open the project in the
browser and ask Claude to do it.
