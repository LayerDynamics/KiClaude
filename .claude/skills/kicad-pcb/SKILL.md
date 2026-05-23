---
name: kicad-pcb
description: Edit the PCB side of a KiCad project through kiclaude's typed kc_* MCP tools — place footprints, route tracks, drop vias, request copper zones, set net classes, run DRC, export fab. Use when the user asks you to lay out a board, route a net, fix DRC, or generate fab artifacts against a .kicad_pcb.
allowed-tools:
  - mcp__kiclaude__kc_ping
  - mcp__kiclaude__kc_project_open
  - mcp__kiclaude__kc_project_save
  - mcp__kiclaude__kc_kcir_get
  - mcp__kiclaude__kc_validate
  - mcp__kiclaude__kc_drc
  - mcp__kiclaude__kc_footprint_place_hint
  - mcp__kiclaude__kc_footprint_remove
  - mcp__kiclaude__kc_track_route
  - mcp__kiclaude__kc_track_remove
  - mcp__kiclaude__kc_via_add_hint
  - mcp__kiclaude__kc_zone_request
  - mcp__kiclaude__kc_netclass_set
  - mcp__kiclaude__kc_netclass_list
  - mcp__kiclaude__kc_export_fab
  - mcp__kiclaude__kc_panelize
  - mcp__kiclaude__kc_route_freerouting
  - mcp__kiclaude__kc_diff
  - mcp__kiclaude__kc_snapshot_create
  - mcp__kiclaude__kc_snapshot_revert
---

# kicad-pcb — kiclaude PCB editing skill

You are editing a real `.kicad_pcb` file inside a KiCad project the
user has opened in kiclaude. Every persistent edit goes through one
of the `kc_*` MCP tools listed above; never propose changes as
free-form text the user has to type. **The `.kicad_pcb` on disk is
the contract — `kc_project_save` is how your work becomes durable
and `kc_diff` is how reviewers see what you changed.**

## First principles you must obey

1. **No raw coordinates.** Your tools take **hints**, never `(x, y)`.
   - `kc_footprint_place_hint` takes `anchor_refdes`, `edge`,
     `cluster`, and `offset_mm` — describe where the part goes
     ("south of U1", "edge: west", "cluster with C-prefix decoupling
     caps"). The placement engine resolves to a coordinate and
     reports its reasoning in the response.
   - `kc_track_route` takes `waypoints` as `refdes.pad` strings.
   - `kc_via_add_hint` takes `at_pad` or `at_track_uuid`.
   - `kc_zone_request` takes the board outline + a `margin_mm`.

   Raw-coordinate variants exist (`ui_footprint_place_xy`,
   `ui_track_draw_points`, …) for the React editor's drag-drop.
   They are not in your tool list and never will be. If you ever
   feel the urge to write `position_mm: [50.0, 23.4]`, stop —
   describe the placement instead.

2. **DRC: kicad-cli is the source of truth.** Always finish a
   routing session with `kc_drc` — the result IS the gate. The Rust
   live-overlay DRC kernel (visible in the editor while you work)
   is for fast feedback only; never claim a board is fab-ready
   without a passing `kc_drc` run.

3. **Net classes drive constraints.** Power nets, diff pairs, and
   any controlled-impedance bus must live under a `kc_netclass_set`
   class so the router + DRC honor the right width / clearance /
   via size. Bind the relevant nets via `bind_nets` in the same
   call — don't expect the router to infer the class.

4. **Snapshot before risky moves.** Before any `kc_route_freerouting`
   call, before a big cluster of `kc_footprint_remove` ops, before
   any zone re-pour, call `kc_snapshot_create` first. The user's
   ActivityJournal also auto-snapshots on every mutation, but an
   explicit named snapshot is the right anchor for "revert this
   experiment".

5. **Every fab export is a real run.** `kc_export_fab` shells out
   to `kicad-cli pcb export gerbers / drill / pos` and (when a
   schematic is present) `kicad-cli sch export bom`. Don't
   summarize the output — quote the `artifacts` map back to the
   user so they see the actual file names.

## Placement etiquette

- **Start from the schematic refdes list** — call `kc_kcir_get`
  with `view: ["schematic", "pcb"]` and walk through every refdes
  that has `on_board: true` but isn't yet in `pcb.footprints`.
- **Anchor first, cluster second.** Place the headline parts
  (MCU, connector, big regulator) using `edge` and explicit board
  hints. Then `cluster` the bypass caps and pull-up resistors
  against their MCU pins.
- **Group decoupling caps with their IC.** A 0603 cap that belongs
  to `U1.VDD` is `cluster: "C"` + `anchor_refdes: "U1"` with a
  short `offset_mm`. The tool reports the reasoning chain in the
  response — read it and confirm it matches user intent before
  moving on.
- **Use `kc_footprint_remove` for any do-over.** Editing an existing
  position by re-placing the same refdes will *add a second
  instance* — remove first, then place.

## Routing — when to use the Rust router vs. Freerouting

- **`kc_track_route` (the Rust walk-around router).** Default for
  power rails, short signal traces, and anything where the user
  cares about the geometry. Output is deterministic and the user
  can audit each track via `kc_diff`. Use this for the
  `/route-power` flow.

- **`kc_route_freerouting`.** Reach for this when:
  - The board has 50+ unrouted signal nets and the user explicitly
    asked to auto-route ("just route everything").
  - The walk-around router has failed to find a route and you've
    snapshot-rolled-back twice.

  The freerouting wrapper does the full DSN → Freerouting → SES
  round-trip; **re-run `kc_drc` after the SES import** — Freerouting
  occasionally produces clearance violations that the import step
  doesn't reject.

- **Never mix the two in one session without snapshotting.** If you
  start with Rust routing and switch to Freerouting mid-board, the
  manual tracks become Freerouting "fixed" inputs and the result
  is rarely what the user wanted.

## DRC interpretation

`kc_drc` returns issues grouped by `severity`. Workflow:

1. **Errors → fix immediately.** Clearance violations, courtyards
   overlap, drill-to-copper, missing connections. Do not move on
   to the next stage.
2. **Warnings → triage with the user.** Hatched zone vs. solid
   pour, silkscreen overlapping pads, etc. Warnings can be
   intentional (silkscreen over a fab marker, for example).
3. **Unconnected items.** These come from the schematic-parity
   pass. If the schematic adds a net, the PCB must route it.
   The fix is usually a `kc_track_route` call, not a DRC
   suppression.

The kicad-cli DRC pass is the gate; **don't claim "DRC clean"
based on the Rust live-overlay alone.** The overlay misses
schematic-parity, drill-to-copper, and any rule whose state lives
in the design rules JSON.

## Zone strategy

- **Default zone for GND is solid + thermal reliefs.** Call
  `kc_zone_request` with `net: "GND"`, `layer: "F.Cu"` (or B.Cu
  on a 2-layer board, both on a 4-layer one), `thermal_relief:
  true`. The wrapper sets `connect_pads: "thermal_reliefs"`
  automatically.
- **Hatched zones (`hatched: true`) are for analog grounds and
  RF.** Default to solid unless the user has a specific reason.
- **Cutouts go in via `ui_zone_create_polygon`** — that's a UI
  surface, not a Claude surface. If the user wants a keepout
  inside a zone, hand off to them: "draw the keepout polygon in
  the canvas and I'll re-run DRC".

## Fab export flow

The canonical "produce a JLC-ready zip" recipe:

1. `kc_snapshot_create` — label `"pre-fab"`.
2. `kc_drc` — must be clean for errors.
3. `kc_export_fab` with `target: "jlcpcb"` and an `output_dir`.
4. Quote the `artifacts` map back to the user with the file paths
   `gerbers.files`, `drill.files`, `pos.files`, `bom.csv_path`.
5. Remind the user that `kiclaude build <project>` does steps 2–4
   as a single CLI run for CI integration.

## Tools you'll reach for

| Goal                              | Tool                            |
|-----------------------------------|---------------------------------|
| Open / save the project           | `kc_project_open`, `kc_project_save` |
| Inspect the live PCB state        | `kc_kcir_get`                   |
| Place a footprint declaratively   | `kc_footprint_place_hint`       |
| Remove a footprint                | `kc_footprint_remove`           |
| Route a net (Rust walk-around)    | `kc_track_route`                |
| Remove tracks                     | `kc_track_remove`               |
| Drop a via                        | `kc_via_add_hint`               |
| Create a copper zone              | `kc_zone_request`               |
| Define / update a net class       | `kc_netclass_set` / `kc_netclass_list` |
| Run kicad-cli DRC                 | `kc_drc`                        |
| Auto-route the whole board        | `kc_route_freerouting`          |
| Export gerbers + drill + PnP + BOM | `kc_export_fab`                |
| Panelize for fab                  | `kc_panelize`                   |
| Diff two PCB snapshots            | `kc_diff`                       |
| Snapshot / revert                 | `kc_snapshot_create` / `kc_snapshot_revert` |

## Hand-off conventions

- After every multi-step operation, **call `kc_diff`** between the
  pre-step snapshot and the current state, and quote the section
  counts (`footprints: +2 ~1`, `tracks: +14`) to the user. That's
  how they audit your work without reading raw KCIR.
- When the user explicitly asks for the fab output ("send to JLC"),
  the answer is **a single `kc_export_fab` call**, not
  `kc_panelize` + manual gerber generation. Panelization is for
  multi-up boards, not single-board orders.
- If you ever encounter an MPN you can't resolve (`kc_mpn_resolve`
  returns `found: false`), DO NOT silently pick a footprint — ask
  the user. Hallucinated parts are the worst kind of round-trip
  bug because they look real until they don't fit.
