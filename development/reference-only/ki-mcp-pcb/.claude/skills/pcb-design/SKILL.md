---
name: pcb-design
description: Guidance for Claude Code when driving the ki-mcp-pcb pipeline. Use whenever the user asks to design, modify, route, review, or fabricate a PCB in this repo.
---

# pcb-design

This skill loads when the user is doing PCB work in `ki-mcp-pcb`. Read `SPEC.md` and `CLAUDE.md` at the repo root for the full project rules; this file is the operational playbook.

## When to load

Triggers: "new board", "design a PCB", "route this", "ERC", "DRC", "fab package", "BOM", "decoupling", "partition", "length match", "diff pair", "impedance", "USB", "Ethernet", "return path", any mention of `.kicad_sch` / `.kicad_pcb` / `.ato` in this repo.

## Tool surface

All work goes through the `ki-mcp-pcb` MCP server.

| Tool | When to call |
|---|---|
| `pcb_version` | Smoke test connectivity. |
| `pcb_doctor` | Verify the user's env (kicad-cli, kiutils, pcbnew, freerouting, java). |
| `pcb_validate_cir` | After authoring or editing a CIR YAML / `.ato`. **Mandatory before synthesis.** |
| `pcb_decoupling_check` | M2: targeted CIR030 check — every IC with declared decoupling_pins has a bypass cap to ground. |
| `pcb_partition_check` | M2: targeted CIR050 check — analog/digital/rf isolation. |
| `pcb_impedance_check` | M3: CIR070 — achievable Zo per net vs. declared target. Reports per-net achievable values + trace geometry used. |
| `pcb_return_path_check` | M3: CIR090 — reference planes exist + are declared on high-speed nets. |
| `pcb_length_tuning` | M3: post-route — feed the measured-lengths JSON, get a tuning queue. |
| `pcb_parse_intent` | Convert NL → draft `.ato`. Always show the draft to the user before continuing. |
| `pcb_synthesize` | Generate KiCad project skeleton (.kicad_pro + .kicad_sch + .kicad_pcb + .net) from CIR. |
| `pcb_build` | The end-to-end pipeline. Preferred over calling individual stages. |
| `pcb_route` | Auto-route via Freerouting. |
| `pcb_drc` / `pcb_erc` | Mandatory before declaring a board "done". |
| `pcb_export_fab` | Produce the fab zip. |

## Hard rules (do not violate)

1. **NL → DSL → CIR → KiCad**, never NL → KiCad directly. The DSL is the audit boundary.
2. **Every MPN must resolve** at synthesis. If sourcing can't find a part in stock, stop and ask.
3. **Declarative placement hints only.** "USB on south edge, MCU centered, decouplers within 2 mm of supply pins" — never raw coordinates.
4. **ERC/DRC failures block.** Do not "ignore" violations to ship a fab package.
5. **Pro-stack features (RF/BGA fanout) are co-pilot, not autonomous.** Scaffold; require human sign-off.
6. **Cross-partition signals need explicit intent.** Either route through a bridge (`is_bridge=True` component) or set `cross_partition_ok=true` on the net. Never both.
7. **Diff pairs need bidirectional `diff_pair_with`** AND a shared `length_match_group` AND a `reference_plane`. CIR060 + CIR090 enforce.
8. **Controlled-impedance nets need explicit `trace_width_mm` and `trace_spacing_mm`** — defaults won't hit typical 90/100 Ω targets on JLC stackups. Use `pcb_impedance_check` to verify.

## CIR vocabulary by milestone

### M1 — Hobbyist 2-layer
- `Component.refdes`, `mpn`, `value`, `footprint`, `symbol`, `placement_hint`
- `Net.name`, `members`, `net_class`
- `Stackup.default_2layer_fr4()`

### M2 — Mixed-signal 4-layer
- `Component.partition` (`analog`/`digital`/`rf`/`power`/`isolated`)
- `Component.decoupling_pins` — supply pins needing bypass
- `Component.is_bridge` — ferrite bead / opto / coupling cap
- `Net.power_rail` — `"3V3"`, `"AVDD"`, etc.
- `Net.partition`, `Net.cross_partition_ok`
- `Net.length_match_group`
- `Stackup.default_4layer_fr4()` + `power_plane_layers`

### M3 — High-speed digital
- `Net.diff_pair_with` — refdes-style cross-reference to partner net
- `Net.target_impedance_ohm` — driven by the protocol (90 USB, 100 Ethernet, 50 RF)
- `Net.reference_plane` — stackup copper layer (e.g. `"In1.Cu"`)
- `Net.trace_width_mm`, `Net.trace_spacing_mm` — geometry override per net
- `Constraint(kind="controlled_impedance", ...)` + `Constraint(kind="length_match", tolerance_pct=...)`

## Milestone playbook

### M1 — 2-layer ✅
- Every IC has decoupling within 2 mm of supply pins.
- Ground pour both sides, stitched with vias.
- BOM: prefer JLC basic parts.
- Pipeline runs autonomously end-to-end via `kimp build`.

### M2 — 4-layer mixed-signal ✅
- Stackup: SIG / GND / PWR / SIG.
- Partition every IC (analog/digital/rf/power).
- Bridge components declared with `is_bridge=True`.
- Decoupling pins declared on each IC; CIR030 enforces.
- ERC is real (synthesis emits a populated `.kicad_sch`).

### M3 — High-speed digital ✅
- Diff pairs routed together, length-matched within declared tolerance.
- Controlled impedance from stackup; use `pcb_impedance_check` to verify achievable Zo before routing.
- Every HS net declares a `reference_plane`; CIR090 catches typos.
- Post-route: run `scripts/kicad_measure_lengths.py`, then `pcb_length_tuning` to see what needs adjustment.

### M4 — RF / DDR / BGA (upcoming, co-pilot only)
- Scaffold the stackup, length groups, BGA fanout templates.
- A human EE signs off.

## Failure modes and fixes

- **Synthesis fails "MPN not found"** → ask user; never guess. Add to `libs/footprints.yaml` if appropriate.
- **CIR030 (decoupling)** → add a cap to each named supply rail OR drop the pin from `decoupling_pins`.
- **CIR040 (length-match)** → ≥2 nets in the group, plus a `length_match` Constraint with `tolerance_pct`.
- **CIR050 (partition)** → bridge component, OR `cross_partition_ok=true`.
- **CIR060 (diff pair)** → both nets must reference each other in `diff_pair_with` AND share a length-match group.
- **CIR070 (impedance unreachable)** → widen/narrow trace via `trace_width_mm`, change `trace_spacing_mm`, or change the dielectric thickness in the stackup. Use the impedance solver to find a working combo.
- **CIR090 (reference plane)** → set `reference_plane` to a copper layer name from the stackup (e.g. `"In1.Cu"`).
- **DRC red on clearance** → check `fab.min_space_mm` matches the fab's real rules.

## Conventions

- One board per CIR file. Multi-board projects under `boards/<name>/`.
- Reference designators uppercase letter + integer (`^[A-Z]+[0-9]+$`).
- Net names uppercase, underscores. GND, 3V3, 5V0, VBUS, AVDD.
- Always check `SPEC.md` before promising functionality. If it's M4+ work, say so.
