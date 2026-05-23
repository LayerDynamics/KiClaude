---
name: route-power
description: Route power nets (VBUS, +3V3, +5V, GND, etc.) on the active PCB using kc_track_route at net-class width. User approves each per-net batch through the M1-P-06 PreToolUse gate.
argument-hint: "[--rails <list>] [--layer F.Cu|B.Cu] [--width-mm <float>]   default: all rails the kicad project already declares as a power class"
allowed-tools:
  - mcp__kiclaude__kc_kcir_get
  - mcp__kiclaude__kc_drc
  - mcp__kiclaude__kc_netclass_list
  - mcp__kiclaude__kc_netclass_set
  - mcp__kiclaude__kc_track_route
  - mcp__kiclaude__kc_track_remove
  - mcp__kiclaude__kc_via_add_hint
  - mcp__kiclaude__kc_snapshot_create
  - mcp__kiclaude__kc_snapshot_revert
  - mcp__kiclaude__kc_project_save
---

# /route-power — route the power rails first

Power routing comes before signal routing for two reasons: (1) power
nets demand wider tracks that signal routing must work around, and
(2) DRC-clean power islands are the precondition for `/route-signals`
deciding via locations rationally. This command lays the rails;
`/route-signals` (M2-C-03) routes the signal mesh on top.

Argument: `$ARGUMENTS` — optional. Supported flags:

| Flag | Default | Notes |
|---|---|---|
| `--rails <a,b,c>` | every net bound to a class named `Power` or matching `+VBUS`, `+3V3`, `+5V`, `+12V`, `VBAT`, `GND` | Explicit list overrides the auto-pick. |
| `--layer` | `F.Cu` for non-ground; `B.Cu` for `GND` (pour candidate, not track) | If the user passes both, every rail goes on that layer. |
| `--width-mm` | net class `trace_width_mm` | If the rail has no class, falls back to `0.5 mm` and warns. |
| `--via-pair` | `F.Cu/B.Cu` | Inner-layer pairs require a stackup that declares them. |

## Sequence

1. **Snapshot.** `kc_snapshot_create(project_id, label="before /route-power")`.
   Quote the snapshot id in the iteration summary so the user can
   revert mid-loop without scrolling the ActivityJournal.

2. **Read the design.** `kc_kcir_get(project_id, view=["pcb.nets","pcb.net_classes","pcb.footprints","pcb.tracks","pcb.outline"])`.
   Resolve `$ARGUMENTS` to a concrete `rails: list[str]`. If the user
   asked for a rail the PCB doesn't declare, **stop** and list the
   nets that DO exist matching power-ish patterns — never invent a
   net name.

3. **Confirm net-class widths.** `kc_netclass_list(project_id)`.
   For each rail, identify its class. If a power rail is bound to
   the default `Default` class at `0.25 mm` width, propose
   `kc_netclass_set(name="Power", trace_width_mm=0.5, clearance_mm=0.25, bind_nets=[<rails>])`
   to widen it. **Do not** apply this without an explicit user nod —
   widening propagates to any existing tracks on the bound nets.

4. **Plan the rails.** For each rail, compute the routing batch:
   - Pads belonging to that net (`pcb.footprints[].pads` matching
     `pad.net == rail`).
   - Skip pads already connected by an existing track on the same
     net (look at `pcb.tracks` for a track whose `net == rail` and
     endpoints touch the pad center within `clearance_mm`).
   - Group pads into a single ordered waypoint list per rail. Start
     at the regulator output (or, for `GND`, at the largest cluster
     centroid) and snake through pads in nearest-neighbour order.

5. **Route, one rail at a time.** For each rail:
   - Announce the plan: `"Routing <rail> as <N> waypoints at <width> mm on <layer>"`.
   - Call `kc_track_route(project_id, net=<rail>, waypoints=[...], layer=<layer>, width_mm=<w>)`.
   - The PreToolUse gate prompts the user. On approve, the M2 walk-
     around router (or its M2-P-04 Manhattan placeholder until M2-R-08
     lands) emits the tracks.
   - If the response carries `unresolved: [...]`, stop and ask. Do
     not retry with different waypoints unless the user instructs.

6. **Add vias only when the rail has to cross.** If a rail's
   waypoint list spans layers (e.g. a 0.5 mm rail crossing a signal
   on `F.Cu`), call `kc_via_add_hint(project_id, net=<rail>, at_pad=<refdes.pad>, from_layer=F.Cu, to_layer=B.Cu)`
   at the changeover pad. Never speculatively drop vias.

7. **Per-rail DRC.** `kc_drc(pcb_path)` after each rail. If new errors
   appear, show the user the delta:

   ```
   /route-power <rail> (iter <N>):
     wrote: <count> tracks at <width> mm
     vias added: <count>
     DRC delta: <before>.errors → <after>.errors, <before>.warnings → <after>.warnings
   next: <rail>            [or "all rails done"]
   ```

   On DRC regression, stop and ask whether to revert the snapshot
   from step 1 or hand-tune the rail. Do not auto-iterate after a
   regression.

8. **Save when the user is happy.** `kc_project_save(project_id)`
   so the work hits disk. Print the snapshot id from step 1 so the
   user has a known-good rollback point.

## Tactics by rail kind

| Rail kind | Default approach |
|---|---|
| `GND` | Prefer a `B.Cu` pour (queue a follow-up `kc_zone_request` for the user) rather than tracks. If the user insists on tracks, snake the largest cluster first and short the rest via vias. |
| `+VBUS` / `+5V` | Wide trace (0.5–1.0 mm) on `F.Cu`. Avoid running next to crystals/clocks. |
| `+3V3` / `+1V8` | 0.4–0.5 mm trace; route radially from the regulator output, fanning to decoupling caps. |
| `VBAT` | Same as `+VBUS` but watch for inrush — never route under switching nodes. |
| Switch node (`SW`, `LX`) | Treat as power: short, fat trace from regulator pin to inductor pad; do NOT extend past the inductor. |

## Notes for Claude

- The Manhattan placeholder router (M2-P-04 boot) emits 90° corners
  via a midpoint. Live overlay DRC may flag the corners; that's the
  walk-around router's M2-R-08 work — note it in the summary but
  don't block on it.
- Never bypass the snapshot in step 1, even if the user said "just
  do it". The snapshot is how `/drc-fix` and the user's revert
  workflow stay sane.
- `kc_track_route` returns `created: [uuid…]`. Surface the count in
  the per-rail summary — that's the user's only visible signal that
  a tool actually ran (the M2-T-08 layer panel will show the new
  copper once it lands).
