---
name: manufacturing
description: Reference for board-house conventions when generating fab artifacts — gerber filename rules, drill formats, BOM column orders, pick-and-place CSV layouts, panel and accept-list differences between JLCPCB, OSHPark, PCBWay, and generic fabs. Use whenever a question touches "what does this fab need" or "why did the fab reject the bundle".
allowed-tools:
  - mcp__kiclaude__kc_kcir_get
  - mcp__kiclaude__kc_drc
  - mcp__kiclaude__kc_export_fab
  - mcp__kiclaude__kc_panelize
  - mcp__kiclaude__kc_project_save
---

# manufacturing — board-house ground truth

You are advising on the manufacturing side of a kiclaude project.
Every board house has its own filename, format, and accept-list
conventions; the ones below are the ones the M2 `kc_export_fab`
target presets emit. When the user asks "which extension does
JLC want?" or "why is OSHPark rejecting my drill file?", the answer
is in this skill — read first, then call the right tool.

## Targets at a glance

| Target | Layers accepted | Gerber format | Drill format | PnP file(s) | BOM format |
|---|---|---|---|---|---|
| `generic` | 2 / 4 / 6 / 8 | RS-274X, Protel exts (`.GTL` / `.GBL` / `.GTS` / `.GBS` / `.GTO` / `.GBO` / `.GKO`) | Excellon 2 metric (`.drl`) | one CSV, both sides | IPC-2581 (XML) |
| `jlcpcb` | 1 / 2 / 4 / 6 / 8 / up to 32 | RS-274X, Protel exts | Excellon 2 metric (`.drl` + `-NPTH.drl`) | one CPL CSV, both sides | csv with `Comment`, `Designator`, `Footprint`, `LCSC Part #` |
| `oshpark` | 2 / 4 / 6 | RS-274X, OSH exts (`.GTL` / `.GBL` / `.GTS` / `.GBS`) | Excellon 2 imperial OR metric | single front-side CSV | csv (any sensible order) |
| `pcbway` | 1 / 2 / 4 / 6 / 8 / up to 32 | RS-274X, Protel exts | Excellon 2 metric | front + back CSV split | xls / csv with PCBWay column order |

`kc_export_fab(target=…)` picks the right layer set and filename
extensions. The CSV column orders are emitted automatically; if the
user changed defaults, surface a warning before the fab gets the
zip.

## Gerber layer naming

The Protel extension convention KiCad uses by default:

| Layer | Protel ext | RS-274X content |
|---|---|---|
| `F.Cu` | `.GTL` | top copper |
| `B.Cu` | `.GBL` | bottom copper |
| `In1.Cu` ... `In30.Cu` | `.G2L`, `.G3L`, … | inner copper, sequential |
| `F.Mask` | `.GTS` | top solder-mask opening |
| `B.Mask` | `.GBS` | bottom solder-mask opening |
| `F.Paste` | `.GTP` | top solder-paste stencil |
| `B.Paste` | `.GBP` | bottom solder-paste stencil |
| `F.Silkscreen` | `.GTO` | top silkscreen |
| `B.Silkscreen` | `.GBO` | bottom silkscreen |
| `Edge.Cuts` | `.GKO` | board outline (KO = "Keep Out") |
| `F.Fab` | `.GM2` | top fab notes (informational) |
| `B.Fab` | `.GM3` | bottom fab notes |

**Edge.Cuts MUST be a single closed polygon.** Open polylines are
the #1 cause of "fab cannot determine the board outline" rejects.
Cutouts inside the outline live on the same layer as inner closed
polygons (KiCad's Edge.Cuts handles this natively).

## Drill format

Excellon 2 metric is the universal accept. The conventions:

- **Tool table at the top** — `M48` header followed by `T01C0.300`,
  `T02C0.500`, etc., one line per drill diameter, in mm.
- **Coordinate format** — leading zeros suppressed (`LZ` off),
  absolute (`ABSOLUTE`), 3 integer + 3 decimal digits (`METRIC,LZ,3.3`).
- **Plated vs. non-plated** — most fabs accept a single `.drl` with
  both kinds; JLC explicitly wants two files: `<basename>.drl`
  (plated) and `<basename>-NPTH.drl` (non-plated through hole). The
  jlcpcb target preset emits both.
- **Slots** — represented as G85 line segments between two
  coordinates after a `G00`/`G01` motion command. KiCad emits these
  automatically; never hand-edit.

OSHPark accepts imperial (`INCH,LZ,2.4`) but their pipeline
converts on intake — emit metric to keep the manifest portable.

## Pick-and-place CSV

Different fabs want different column orders. Default emit:

| Target | Required columns (in order) |
|---|---|
| `generic` | `Designator`, `Value`, `Package`, `Mid X`, `Mid Y`, `Rotation`, `Layer` |
| `jlcpcb` | `Designator`, `Val`, `Package`, `Mid X`, `Mid Y`, `Rotation`, `Layer` |
| `oshpark` | `Refdes`, `Value`, `Package`, `X`, `Y`, `Rotation`, `Side` |
| `pcbway` | `Refdes`, `Comment`, `Footprint`, `MidX`, `MidY`, `Rotation`, `Layer` |

JLC's "Layer" column wants `T` for top, `B` for bottom — NOT
`F.Cu`/`B.Cu` and NOT `top`/`bottom`. The jlcpcb preset normalises.

**Mid X / Mid Y** is the centroid of the footprint in board
coordinates, mm, positive Y up (mathematical convention), origin at
the board outline's bottom-left corner. KiCad's grid convention is
positive Y down — `kc_export_fab` flips Y to match the fab convention.

**Rotation** is degrees, CCW positive. JLC quirk: certain rotated
footprints (most QFN / WLCSP) need a per-MPN correction from JLC's
internal database — surface a warning when the M3 `decoupling-auditor`
subagent notices a known mis-rotation pattern.

## BOM

| Target | Format | Notes |
|---|---|---|
| `generic` | IPC-2581 XML inside the gerber zip | The "right answer" — every fab can ingest it. |
| `jlcpcb` | CSV with `Comment`, `Designator`, `Footprint`, `LCSC Part #` columns | LCSC numbers are the assembly key. If a part has no LCSC #, JLC won't assemble it (will charge for sourcing or refuse). |
| `oshpark` | CSV, columns flexible | OSHPark assembly is via OSH Stencils; they accept Digikey part numbers. |
| `pcbway` | XLS preferred, CSV accepted | Columns: `Refdes`, `Quantity`, `Description`, `Manufacturer`, `Manufacturer #`, `Package`, `Notes` |

The MPN resolver (M1 stub → M3 full) writes the right column for
each target. When the user pastes a fab rejection email like
"part XYZ has no source", the cause is almost always a missing
JLC/Digikey number in the BOM — point them at `kc_symbol_edit` to
correct the part's MPN.

## DFM minimums by target

Approximate; the kiserver DFM module (M2-Q-03) holds the
authoritative table.

| Rule | jlcpcb (2-layer, hobby) | jlcpcb (4-layer, pro) | oshpark | pcbway |
|---|---|---|---|---|
| Min track | 0.127 mm (5 mil) | 0.0762 mm (3 mil) | 0.152 mm (6 mil) | 0.10 mm (4 mil) |
| Min clearance | 0.127 mm | 0.0762 mm | 0.152 mm | 0.10 mm |
| Min annular ring | 0.13 mm | 0.075 mm | 0.10 mm | 0.10 mm |
| Min drill | 0.3 mm | 0.15 mm (laser only) | 0.25 mm | 0.20 mm |
| Min via diameter | 0.45 mm | 0.30 mm | 0.40 mm | 0.40 mm |
| Min silk line width | 0.153 mm | 0.153 mm | 0.10 mm | 0.10 mm |
| Min silk-to-pad | 0.1 mm | 0.1 mm | 0.05 mm | 0.1 mm |
| Min board thickness | 0.4 mm | 0.4 mm | 0.61 mm | 0.4 mm |
| Max board thickness | 3.2 mm | 3.2 mm | 1.575 mm | 6.0 mm |

When the user is on `2-layer JLC` and a design has 0.1 mm tracks,
the DFM dry-run flags them as `error` (not warning) — the fab
will reject the board, not just charge extra.

## Panelisation

`kc_panelize` wraps KiKit. Conventions:

- **Tab-and-mouse-bite** panels: 2 mm tabs, 0.5 mm mouse bites.
  The default. Works for most JLC / PCBWay panels.
- **V-groove** panels: 2 mm cut depth on 1.6 mm board. Only JLC
  charges no fee for V-grooves; OSHPark doesn't support them at all.
- **Frame size**: 5 mm gutter all sides for assembly fiducials.
- **Fiducials**: three 1 mm × 1.5 mm copper dots in an L-shape on
  the frame's corners. KiKit emits them when you pass
  `kc_panelize(layout="grid", fiducials=true)`.

## Common fab rejection causes (and how to fix)

| Reject reason | Cause | Fix |
|---|---|---|
| "Board outline missing / not closed" | Edge.Cuts is a polyline, not a polygon | M2-T-05 outline tool; or `kc_kcir_get(view=["pcb.outline"])` and inspect manually |
| "PnP file rotation is wrong" | KiCad CCW vs fab CW convention; or a JLC rotated-package mismatch | `kc_export_fab` normalises CCW; for JLC mis-rotations, edit the symbol's `rotation_deg` via `kc_symbol_edit` |
| "BOM has no LCSC numbers" | MPN resolver didn't run, or the part has no MPN | M1 → M3 sourcing tools: `kc_mpn_resolve(mpn=…, distributors=["lcsc"])` |
| "Silk over pad" | DFM warning ignored | M2-T-06 DRC overlay shows the violation; nudge the silkscreen in the source footprint (no kc_ tool for this in M2 — flag as action-needed) |
| "Drill too small" | Net class via_drill_mm below the fab's minimum | `kc_netclass_set(name=<class>, via_drill_mm=<fab minimum>)` |
| "Track too thin on inner layer" | M3 design with `In1.Cu` < 0.0762 mm | Same — widen via `kc_netclass_set` |
| "Solder mask sliver too thin" | `solder_mask_min_width_mm` below the fab's minimum | Edit `pcb.solder_mask_min_width_mm` via a future `ui_*` tool, or by hand in `.kicad_pcb` for now |

## Notes for Claude

- The M3 `parts-sourcing` skill will own MPN-to-distributor matching
  in detail; this skill points at the right column for each fab but
  does not run the lookups itself.
- When the user says "send to JLC", they almost always mean JLCPCB's
  combined PCB + assembly service. The bundle differs from
  bare-PCB-only: assembly needs the BOM and CPL, bare PCB just needs
  the gerbers + drill. Ask which one before exporting.
- Stackup choices live in the M3 stackup editor (M3-T-01); for M2,
  2-layer boards default to 1.6 mm FR4 — explicit and on-record.
- When the fab's quote comes back surprising ($20 instead of $5),
  the most common cause is V-cuts plus copper too close to the
  cut — KiCad's DRC misses this; offer to widen edge-clearance
  via the net-class instead of relying on the DRC alone.
