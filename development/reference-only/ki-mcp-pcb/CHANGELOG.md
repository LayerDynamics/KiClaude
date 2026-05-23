# Changelog

All notable changes to ki-mcp-pcb. Date format: ISO 8601 (YYYY-MM-DD).

## [Unreleased]

### KiCad IPC auto-place (kipy)

- `placement/kipy_placer.py` — optional integration with KiCad 9's IPC
  API via `kicad-python`. Lazy-imports kipy, every operation returns a
  structured `KipyStatus(code=...)` instead of raising. Tests inject a
  fake client via `set_kicad_factory_for_tests` so the suite runs
  without a KiCad install.
- `KipyPlacer.apply_to_board(board)` plans hint-driven placement
  (declarative `placement_hint` strings only — CLAUDE.md rule 5) then
  pushes coordinates atomically inside one `begin_commit()` /
  `push_commit()`. Refdes that aren't in the open PCB are reported under
  `skipped`; an empty intersection surfaces as `no_matching_refdes`.
- `kimp autoplace <source.yaml|.ato>` CLI verb and `pcb_autoplace` MCP
  tool — both delegate to `autoplace_board()` and forward the structured
  status without ever raising at the surface.
- `kimp doctor` learned a `kipy` check that distinguishes "not installed
  (autoplace disabled)" from "installed but no KiCad listening" without
  failing the doctor exit code — the rest of the text-to-fab pipeline
  doesn't require IPC.
- Optional dependency moved out from under the `kicad` extra into its
  own `ipc` extra: `uv sync --extra ipc`.

## [0.0.1] — 2026-05-17

Initial pre-alpha. Spec + every milestone in `SPEC.md §6` closed,
plus the four delivery shapes promised in `SPEC.md §2` (library, CLI,
MCP server, web viewer) all shipping.

### Foundations (M0)

- Repo scaffold: uv workspace, packages under `packages/*`, ruff +
  mypy strict + pytest gates wired.
- **CIR v0.1** Pydantic schema with structural validation: duplicate
  refdes (CIR001), dangling net members (CIR002, CIR003), missing
  ground warning (CIR010), stackup/fab layer-count mismatch (CIR020).
- JSON Schema snapshot in `tests/golden/board_schema.json` — diff
  fails on any contract drift.
- LLM eval harness — semantic-equivalence judge (component set, MPN,
  net membership, fab target) with a mock parser stand-in.

### M1 — Hobbyist 2-layer (autonomous text → fab zip)

- KiCad backend via `kiutils` — `.kicad_pro`/`.kicad_pcb`/`.kicad_sch`/
  `.net` round-trip cleanly. `KiCadBackend.read_project` re-derives a
  Board from the emitted netlist.
- `kicad-cli` subprocess wrappers: ERC, DRC, Gerber, drill, STEP,
  pick-and-place. All mocked in unit tests; real KiCad in the CI
  `kicad-build` job.
- **pcbnew populator** (`scripts/kicad_populate.py`) — reads our
  netlist, runs `BOARD_NETLIST_UPDATER`, grid-places footprints. No
  manual KiCad GUI step.
- Freerouting CLI integration. Footprint registry
  (`libs/footprints.yaml`). MPN resolver that fails closed on unknown
  parts. BOM CSV + JLC fab zip orchestrator.
- Hand-rolled `.ato` parser as a fallback when atopile isn't
  installable. End-to-end demo: `examples/blinky.yaml`.

### M2 — Mixed-signal 4-layer

- **CIR v0.2** — additive: `power_plane_layers`, `Component.partition`
  (analog/digital/rf/power), `Component.decoupling_pins`,
  `Component.is_bridge`, `Net.power_rail` / `Net.partition` /
  `Net.cross_partition_ok`, `Net.length_match_group`. Migration
  `0.1 → 0.2`.
- **CIR030** decoupling coverage, **CIR040** length-match groups,
  **CIR050** partition isolation.
- Schematic synthesis (`synthesis/schematic.py`) via kiutils —
  emit real `.kicad_sch` with `SchematicSymbol` + `GlobalLabel`
  blocks so ERC has something to check.
- `Stackup.default_4layer_fr4()` — SIG/GND/PWR/SIG.
- Demo: `examples/stm32_audio.yaml` (STM32F407 + WM8731 codec).

### M3 — High-speed digital

- **CIR v0.3** — `Net.diff_pair_with`, `Net.reference_plane`,
  `Net.trace_width_mm`, `Net.trace_spacing_mm`. Migration `0.2 → 0.3`.
- **CIR060** diff-pair declarations, **CIR070** controlled impedance
  (Hammerstad / IPC-2141 closed form), **CIR080** post-route length
  tuning, **CIR090** return-path validator.
- `signal_integrity.impedance` — microstrip / stripline /
  differential variants. `signal_integrity.length_tuning` —
  consumes the pcbnew measurement script's JSON output and emits a
  per-group tuning queue.
- Demo: `examples/usb_eth_phy.yaml` (USB 2.0 HS + 100BASE-T).

### M4 — RF / DDR / BGA (co-pilot)

- **CIR v0.4** — `Net.cpwg_gap_mm`, `Net.topology` (`fly_by` /
  `t_branch` / `star`), `Net.fly_by_order`, `Component.bga_pitch_mm`,
  `Board.signoff` (`Signoff` model with `rf_reviewed` /
  `ddr_reviewed` / `bga_fanout_reviewed` / `reviewer` /
  `reviewed_at`). Migration `0.3 → 0.4`.
- **CIR100** DDR fly-by topology, **CIR110** BGA fanout feasibility.
- Wadell/Wen grounded-CPWG impedance solver. Found and fixed a
  Hilberg-cases-swapped bug en route. BGA fanout templates in
  `libs/bga_fanout.yaml`.
- Demo: `examples/esp32_c6_rf.yaml` (ESP32-C6 + 2.4 GHz CPWG + DDR3L
  fly-by sketch). Documented as co-pilot, not autonomous — sign-off
  flags are the audit trail.

### Gap-fill (the four "what's still ahead" items from M4 close)

- Real `parse_nl` via Anthropic SDK — prompted with the
  Pydantic-generated JSON Schema; YAML draft written to disk before
  any KiCad files touched. `ANTHROPIC_API_KEY` opt-in.
- Real JLC parts library sourcing (`sourcing/jlc.py`) — CSV-backed,
  no API key. `check_sourcing(..., include_live_jlc=True)` enriches
  with price + stock.
- `kimp diff` + `pcb_diff` MCP tool — structural diff over CIR
  (YAML / `.ato` / `.kicad_pro`).
- Live LLM eval harness — same fixtures, real Anthropic calls when
  the key is set.

### UI

- `ki_mcp_pcb_web` — FastAPI server + single-page vanilla-JS viewer.
  Drop a CIR file, see validation/components/nets/BOM/impedance.
  KiCanvas tab for embedded PCB preview.
- `kimp serve` CLI verb. Optional `[web]` extra.

### Numbers

| | Count |
|---|---|
| Tests passing | **269** |
| Source files (mypy strict) | **45** |
| MCP tools | **19** |
| CLI verbs | **9** (`version`, `validate`, `build`, `route`, `fab`, `ask`, `diff`, `serve`, `doctor`) |
| Diagnostic codes | **10** (CIR001 / 002 / 003 / 010 / 020 / 030 / 040 / 050 / 060 / 070 / 080 / 090 / 100 / 110) |
| Demo boards | **4** (blinky / stm32_audio / usb_eth_phy / esp32_c6_rf) |
| CIR migrations | **3** (`0.1→0.2→0.3→0.4`) |
| Footprint registry parts | **~40** |
