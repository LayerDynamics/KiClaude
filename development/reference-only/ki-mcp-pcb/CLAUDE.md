# CLAUDE.md — guidance for Claude Code in this repo

This file is loaded automatically by Claude Code. Read it before doing work here.

## What this project is

`ki-mcp-pcb` turns plain-text circuit descriptions into manufacturable KiCad PCBs. It ships as a Python library, a `kimp` CLI, and an MCP server — all from one monorepo. The MCP server is the primary integration with you (Claude Code).

The authoritative design document is [`SPEC.md`](./SPEC.md). When something here conflicts with the spec, the spec wins.

## Architectural rules — do not violate

1. **CIR is the contract.** Every transformation passes through the typed Pydantic CIR in `packages/ki_mcp_pcb_core/src/ki_mcp_pcb_core/cir/`. Do not synthesize KiCad files directly from natural language or DSL — always go NL → DSL → CIR → KiCad.
2. **KiCad is the only backend in v1.** A `Backend` abstraction exists; resist adding a second backend until M4. Don't add backend-specific code outside `packages/ki_mcp_pcb_core/src/ki_mcp_pcb_core/backends/`.
3. **MCP tools must be stateless.** State lives in files on disk. A tool takes paths and arguments in, returns structured JSON out. No hidden globals, no in-memory caches that survive a call.
4. **No free-form prose from MCP tools.** Return structured JSON. Claude (you) does the narration on top.
5. **Declarative placement only.** When the LLM influences placement, it does so via declarative hints ("MCU center, decouplers within 2 mm of supply pins, USB-C on south edge"), never via raw coordinates.
6. **Every MPN must resolve.** Synthesis fails closed if a part can't be found in a real distributor's stock list. No hallucinated parts.

## How to work in this repo

- **Layout:** uv workspace, packages under `packages/*`. Each package has its own `pyproject.toml`. Tests live next to the code (`packages/*/tests/`) plus the top-level `tests/` for cross-package end-to-end.
- **Run things via uv:** `uv run kimp ...`, `uv run pytest`, `uv run ruff check`, `uv run mypy`. Don't reach for `pip` or system Python.
- **Lint + types are required.** ruff and mypy strict are configured at the root. Don't add `# type: ignore` without a reason in a comment.
- **CIR schema changes are breaking.** Bump `cir_version` and add a migration in `cir/migrations.py` rather than silently changing a field's meaning.
- **KiCad files are golden-tested.** When you change synthesis, regenerate the golden files in `tests/golden/` deliberately — never commit a stale one.

## When the user asks for new functionality

The default flow:

1. Check `SPEC.md` to see which milestone (M0–M4) the feature belongs to. If it's beyond the current milestone, flag the milestone slip explicitly before implementing.
2. Identify which CIR fields need to change (if any). Schema first.
3. Add or update the parser, synthesizer, or validator.
4. Wire the capability into the CLI and MCP server in parallel — they should never drift.
5. Add a regression test that runs end-to-end on a small example.

## Things that look helpful but aren't

- **Don't auto-generate footprints.** v1 picks from existing symbol/footprint libraries. Footprint creation is out of scope.
- **Don't bypass ERC/DRC** because output "looks fine." If the checks fail, the pipeline fails.
- **Don't promise autonomous RF/DDR routing.** Per `SPEC.md §6`, M4 is co-pilot only. Tooling scaffolds; a human EE signs off.
- **Don't add a second EDA backend** before the KiCad backend is solid and milestone-tested.

## Useful entry points

- `packages/ki_mcp_pcb_core/src/ki_mcp_pcb_core/cir/models.py` — start here for the data model (CIR v0.2 as of M2)
- `packages/ki_mcp_pcb_core/src/ki_mcp_pcb_core/cir/validation.py` — design-intent validators (CIR001 … CIR050)
- `packages/ki_mcp_pcb_core/src/ki_mcp_pcb_core/synthesis/schematic.py` — CIR → .kicad_sch (M2)
- `packages/ki_mcp_pcb_core/src/ki_mcp_pcb_core/signal_integrity/impedance.py` — Hammerstad/IPC-2141 impedance solver (M3)
- `packages/ki_mcp_pcb_core/src/ki_mcp_pcb_core/signal_integrity/length_tuning.py` — post-route length-match analyzer (M3)
- `scripts/kicad_measure_lengths.py` — pcbnew side of the length-measurement loop (M3)
- `examples/usb_eth_phy.yaml` — M3 demo with USB HS + Ethernet diff pairs
- `examples/esp32_c6_rf.yaml` — M4 demo with 50 Ω CPWG + DDR fly-by + BGA fanout
- `libs/bga_fanout.yaml` — pitch → escape-routing recommendations
- `packages/ki_mcp_pcb_core/src/ki_mcp_pcb_core/placement/kipy_placer.py` — optional `kicad-python` (kipy) IPC bridge: pushes hint-driven placements to a running KiCad
- `packages/ki_mcp_pcb_server/src/ki_mcp_pcb_server/tools.py` — MCP tool implementations (server.py is just wiring)
- `packages/ki_mcp_pcb_cli/src/ki_mcp_pcb_cli/main.py` — CLI surface (mirrors MCP)
- `examples/blinky.yaml` — minimal 2-layer M1 demo (text → fab autonomously)
- `examples/stm32_audio.yaml` — mixed-signal M2 demo with partitions + decoupling + length-match
- `.claude/commands/` — slash commands users invoke; they orchestrate the MCP tools

## Design-intent vocabulary (M2)

- **Partition** — `analog` / `digital` / `rf` / `power` / `isolated`. Set on `Component.partition`. Validator CIR050 enforces nets don't cross partitions unless either (a) a `is_bridge=True` component is on the net, or (b) the net carries `cross_partition_ok=true` to indicate a reviewed crossing.
- **Decoupling intent** — `Component.decoupling_pins` lists the supply pin numbers that need bypass caps. CIR030 checks every named rail has at least one cap to ground. Geometric "within N mm" check is M3.
- **Length-match group** — `Net.length_match_group` puts nets in the same matched group. CIR040 validates the group has ≥2 members and the corresponding `Constraint(kind="length_match")` declares a tolerance.
- **Power rail** — `Net.power_rail` (e.g. `"3V3"`, `"VBUS"`, `"AVDD"`) classifies a power net for downstream decoupling lookup.

## High-speed vocabulary (M3)

- **Diff pair** — declare with `Net.diff_pair_with` (refdes-style cross-reference). CIR060 enforces bidirectional declaration and a shared length-match group.
- **Controlled impedance** — set `Net.target_impedance_ohm`. CIR070 uses the closed-form Hammerstad/IPC-2141 solver (`signal_integrity.impedance`) to check that the stackup + per-net trace geometry can hit the target within 20% (warning at 10%, error at 20%).
- **Trace geometry overrides** — `Net.trace_width_mm` and `Net.trace_spacing_mm` override conservative defaults when you need to hit a specific impedance. Diff pairs almost always need both set explicitly.
- **Reference plane** — `Net.reference_plane` names the stackup copper layer that carries this net's return current. CIR090 verifies the plane exists; geometric "plane is contiguous under the trace" detection is a post-route check that runs via the pcbnew measurement script.
- **Length tuning queue** — post-route `signal_integrity.length_tuning` reads measured trace lengths (from `scripts/kicad_measure_lengths.py`) and emits per-group tolerance reports + a queue of nets that need to be lengthened or shortened.

## RF / DDR / BGA vocabulary (M4 — co-pilot only)

- **CPWG** — grounded coplanar waveguide. Set `Net.cpwg_gap_mm` (trace-to-side-ground gap) and `target_impedance_ohm`. CIR070 dispatches to the Wadell/Wen solver instead of microstrip.
- **Fly-by topology** — DDR3/4 address+command nets. `Net.topology="fly_by"` + `Net.fly_by_order=[controller, ram_0, …, terminator]`. CIR100 enforces ≥3 nodes, all refdes exist, and the board has `signoff.ddr_reviewed=true`.
- **BGA fanout** — `Component.bga_pitch_mm` is looked up against `libs/bga_fanout.yaml`. CIR110 flags pitches needing HDI / micro-vias the fab target can't do, or trace/clearance below `fab.min_*`.
- **Sign-off gates** — `Board.signoff` carries `rf_reviewed`, `ddr_reviewed`, `bga_fanout_reviewed` plus `reviewer` / `reviewed_at`. M4 validators emit "needs human review" warnings until the relevant flag is true. An LLM cannot flip these on its own — the human commits the change.

## KiCad IPC auto-placement (optional)

The pure-Python pipeline never depends on KiCad running. `kimp autoplace` and the `pcb_autoplace` MCP tool are the bridge to a live KiCad PCB editor via the `kicad-python` (kipy) IPC API.

- Optional dependency: `uv sync --extra ipc` (in `ki-mcp-pcb-core`).
- The placer pushes **hint-driven** coordinates only — `Component.placement_hint` flows through `placement.plan_placement` to `kipy.FootprintInstance.position`. Raw LLM coordinates remain forbidden (CLAUDE.md rule 5).
- Every operation returns a `KipyStatus(code=...)` instead of raising. Common codes: `ok`, `kipy_unavailable`, `kicad_unreachable`, `no_open_board`, `no_matching_refdes`, `commit_failed`.
- The whole batch goes inside one `begin_commit()` / `push_commit()` so the user sees a single undo entry.
- Tests inject a fake kipy client via `set_kicad_factory_for_tests` — the suite runs without KiCad installed.

## When you're unsure

Ask. The user prefers a clarifying question over a wrong assumption that ripples through CIR or the backend abstraction.
