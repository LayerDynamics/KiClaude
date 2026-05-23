---
name: drc-fix
description: Read the current DRC output, pick the highest-severity issue, propose a single fix (move a footprint, reroute a track, drop a via, widen a class), wait for approval, apply it, and re-run DRC. Loop until DRC is clean or the user stops.
allowed-tools:
  - mcp__kiclaude__kc_kcir_get
  - mcp__kiclaude__kc_drc
  - mcp__kiclaude__kc_netclass_list
  - mcp__kiclaude__kc_netclass_set
  - mcp__kiclaude__kc_footprint_place_hint
  - mcp__kiclaude__kc_footprint_remove
  - mcp__kiclaude__kc_track_route
  - mcp__kiclaude__kc_track_remove
  - mcp__kiclaude__kc_via_add_hint
  - mcp__kiclaude__kc_zone_request
  - mcp__kiclaude__kc_snapshot_create
  - mcp__kiclaude__kc_snapshot_revert
  - mcp__kiclaude__kc_project_save
---

# /drc-fix — interactive DRC remediation loop

DRC's analogue to `/erc-fix`. One iteration drives one DRC violation
to zero; re-run the command on the next-highest issue. The loop is
designed for resumption — every iteration ends with a fresh DRC pass
so the surface area between iterations stays small.

## Sequence

1. **Snapshot.** `kc_snapshot_create(project_id, label="before /drc-fix iter <N>")`
   where `<N>` is the iteration count starting at 1. Quote the
   snapshot id in step 7's summary so the user has a clear rollback.

2. **Run DRC.** `kc_drc(pcb_path)`. If `issues == []`, say so and
   exit. Quote `duration_ms` so the user sees how long DRC takes
   on this board — useful for the M2-Q-04 frame-timing context.

3. **Pick the issue to fix.** Sort `issues` by severity:
   `error` > `warning`. Within a tier, prefer:
   - Issues with a concrete `position_mm` over board-wide findings.
   - Issues on the active layer over those on the back side (so the
     user can see them in the M2-T-06 DRC overlay).
   - Issues that involve a single net (easier to repair) over ones
     spanning multiple nets.
   - Issues that touch a footprint with a known refdes (easier to
     describe to the user) over anonymous polygon collisions.

4. **Diagnose.** Cite the issue verbatim (`type`, `description`,
   `layer`, `position_mm`, `refdes` if applicable). In 2–3 sentences,
   explain:
   - Which two objects are colliding.
   - Why they collide (clearance violation? courtyard overlap?
     annular ring? drill-to-copper?).
   - Which tool call you'll make to fix it (see "Tactics" below).

5. **Apply exactly one mutation.** A single tool call — never bundle
   two. Compatible mutations:
   - `kc_track_remove(net, track_uuids=[…])` — pull a violating
     track so a re-route can land cleaner.
   - `kc_track_route(net, waypoints=[…], layer, width_mm)` — reroute
     around the obstacle.
   - `kc_via_add_hint(net, at_pad=…, from_layer=…, to_layer=…)` —
     stitch through to break the collision.
   - `kc_footprint_place_hint(refdes, anchor_refdes, edge, offset_mm)` —
     nudge a colliding footprint.
   - `kc_netclass_set(name, clearance_mm=…, trace_width_mm=…)` —
     widen / loosen a class; affects every net bound to it.
   - `kc_zone_request(net, layer, margin_mm, …)` — replace a
     manually-routed copper region with a pour.

   The M1-P-06 PreToolUse gate will pause for approval; if the user
   redirects, restart at step 4 with the redirected plan.

6. **Re-run DRC.** `kc_drc(pcb_path)`. Confirm:
   - The original issue is gone.
   - No new error has appeared.

   If the original issue persists or a new error appeared, **stop**
   the loop — do not blindly try another tactic. Show the user the
   delta and ask whether to revert via the snapshot from step 1.

7. **Report.** Summary block:

   ```
   /drc-fix iter <N>:
     fixed: <type> on <layer> @ <position> — <description>
     applied: <tool name> (<one-line of arguments>)
     DRC delta: <before>.errors → <after>.errors, <before>.warnings → <after>.warnings
   next-highest: <type> on <layer> ...   [or "DRC clean"]
   snapshot: <id from step 1>
   ```

   Then ask: "Continue with the next issue?" Don't auto-iterate —
   the user may want to inspect the diff.

8. **On user-stop**, run `kc_project_save(project_id)` so the
   partial progress sticks, and end the command.

## Tactics by issue type

| `type` | Default fix |
|---|---|
| `clearance_violation` (track-to-track) | Identify the lower-priority net (signal beats power, slow beats fast). `kc_track_remove` it, then `kc_track_route` along a different waypoint sequence or layer. |
| `clearance_violation` (track-to-pad) | Reroute the track to enter the pad from a different angle (`kc_track_remove` + `kc_track_route` with a different penultimate waypoint). |
| `clearance_violation` (pad-to-pad) | Two footprints are too close. `kc_footprint_place_hint(refdes=<smaller one>, anchor_refdes=<other>, edge="east"/"west"/"north"/"south", offset_mm=<clearance + margin>)`. |
| `courtyard_overlap` | Same as pad-to-pad — separate the footprints via `kc_footprint_place_hint`. Never silently shrink courtyards. |
| `annular_ring_violation` | The via drill is too close to its pad edge. `kc_netclass_set(name=<via's class>, via_drill_mm=<smaller>, via_diameter_mm=<larger>)` — but warn that this affects every via bound to the class. |
| `drill_to_copper_violation` | Same family — typically the via drill is too close to an adjacent copper feature. Tighten the via's clearance via `kc_netclass_set` OR reposition the via. |
| `missing_connection` | A net's pads aren't all on the same copper. `kc_track_route(net, waypoints=[<missing pair>])` — call attention to whether the missing leg is intentional (NC) before routing. |
| `silk_over_pad` | Adjust the silkscreen, NOT the pad — note this needs a `ui_*` REST call (no kc_ tool); surface as an action-needed line and end the iteration. |
| `unconnected_items` | Same as `missing_connection`; ignore if the items are flagged `unconnected_intentional`. |
| `unknown` | **Stop and ask.** Don't guess at a fix the schema doesn't define. |

## When to widen the class instead of reroute

Widening (via `kc_netclass_set`) is the right move when:
- **More than 3 issues of the same `type` share a root cause** —
  e.g. five `clearance_violation` events all between the same two
  net classes.
- **The clearance values look obviously wrong** — `0.05 mm` on a
  JLCPCB 2-layer board, for instance, where the fab minimum is
  `0.127 mm`.
- **The user has stated a fab target** in the project config; the
  active class's clearance violates the target's minimum.

Otherwise, prefer per-net surgery via routing/footprint tools — it
preserves the design intent of the other nets.

## Notes for Claude

- One iteration = one snapshot + one mutation + one re-DRC. The
  loop's value is the cadence, not throughput.
- If a single mutation would touch 3+ nets (e.g. widening the
  Default class), surface that scope before calling — the user
  may prefer per-net mutations.
- The Rust live-overlay DRC (M2-R-06, when it lands) shows
  violations in the editor canvas; `kc_drc` is `kicad-cli` (the
  source of truth per SPEC D8). Quote which one you're acting on
  if a delta appears between them.
- `kc_snapshot_revert(<id from step 1>)` undoes everything since
  the iteration started. Mention it in step 7 so the user has the
  rollback handle visible.
