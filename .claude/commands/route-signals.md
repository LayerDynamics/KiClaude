---
name: route-signals
description: Walk-around route signal nets on the active PCB in dependency order — clocks/crystals first, fast buses next, low-speed last. Calls kc_track_route per net; each is gated by the M1-P-06 PreToolUse approval.
argument-hint: "[--include <nets>] [--exclude <nets>] [--layer F.Cu|B.Cu] [--max-corners <int>]"
allowed-tools:
  - mcp__kiclaude__kc_kcir_get
  - mcp__kiclaude__kc_drc
  - mcp__kiclaude__kc_netclass_list
  - mcp__kiclaude__kc_track_route
  - mcp__kiclaude__kc_track_remove
  - mcp__kiclaude__kc_via_add_hint
  - mcp__kiclaude__kc_snapshot_create
  - mcp__kiclaude__kc_snapshot_revert
  - mcp__kiclaude__kc_project_save
---

# /route-signals — walk-around route the signal mesh

Signal routing always follows `/route-power` (M2-C-02). The rails set
the topology; the signals fill the gaps. This command routes one net
at a time in a dependency order chosen to minimise rework: anything
clock-domain-defining first, then anything bandwidth-defining, then
anything else.

Argument: `$ARGUMENTS` — optional. Supported flags:

| Flag | Default | Notes |
|---|---|---|
| `--include <nets>` | every signal net not yet fully connected | Comma-separated. Power nets are excluded — use `/route-power` for those. |
| `--exclude <nets>` | empty | Useful for leaving a manual-route net alone (e.g. an antenna feed). |
| `--layer` | `F.Cu`, with `kc_via_add_hint` to swap when a power rail blocks | Forces single-layer routing if set; multi-layer escape requires a via hint per crossing. |
| `--max-corners` | 6 | Hard cap per net. Above this, the command stops and asks the user to reconsider the placement. |

## Dependency-order ranking

Sort the unrouted signal set by this key, lowest = route first:

1. **Crystal / oscillator return paths.** Anything in a net named
   `XTAL*`, `OSC*`, `CLK_IN`, `MCO`. These define jitter; route them
   point-to-point with the shortest possible path.

2. **Differential pairs.** Treat `Net.diff_pair_with` partners as a
   single unit; route both legs back-to-back so the M2 walk-around
   placeholder doesn't desync them. M3-R-04 makes this first-class;
   for M2 we just sequence them adjacently in the queue.

3. **Source-synchronous buses.** SPI, I²C, UART, parallel data —
   group by net-name prefix (`SPI1_*`, `I2C2_*`, `UART_*`). Within
   a group, route the strobe/clock line first, then the data lines
   so they nestle alongside it.

4. **Low-speed control.** GPIOs, resets, enables, status LEDs —
   any net that doesn't fall into 1–3. Order alphabetically so the
   run is deterministic.

5. **Test points / unused.** Skip unless `--include` names them.

## Sequence

1. **Snapshot.** `kc_snapshot_create(project_id, label="before /route-signals")`.

2. **Read the design.** `kc_kcir_get(project_id, view=["pcb.nets","pcb.net_classes","pcb.footprints","pcb.tracks"])`.
   Build the unrouted signal set: nets that aren't power-class and
   that have at least one pad pair without a connecting track.

3. **Apply filters.** Resolve `--include` / `--exclude` against the
   net list. If `--include` names a net that doesn't exist, stop
   and list candidates matching the closest prefix.

4. **Rank.** Sort by the ranking above. Print the queue before any
   tool call so the user can redirect:

   ```
   /route-signals queue (<count> nets):
     1. XTAL_HSE  (2 pads)        [rank: clock]
     2. USB_DP    (4 pads)        [rank: diff pair w/ USB_DM]
     3. USB_DM    (4 pads)        [rank: diff pair w/ USB_DP]
     4. SPI1_CLK  (3 pads)        [rank: strobe]
     5. SPI1_MOSI (3 pads)        [rank: bus data]
     ...
   ```

5. **Route, one net at a time.** For each net:
   - Build the waypoint list as `[refdes.pad, …]` in pad-cluster
     order (nearest neighbour from the net's centroid).
   - Resolve trace width from the net's class via `kc_netclass_list`.
     Fall back to `0.25 mm` and warn if no class is bound.
   - Estimate the corner count from the waypoint geometry. If it
     exceeds `--max-corners`, **stop** and ask: the route is asking
     for a topology change (move a footprint, escape via a different
     layer), not a router gymnastic.
   - `kc_track_route(project_id, net, waypoints, layer, width_mm)`.
   - Per-net DRC: `kc_drc(pcb_path)`. On regression, stop and ask.

6. **Layer escapes via `kc_via_add_hint`.** When the active layer is
   blocked by an already-routed power rail, propose a via at the
   blocking refdes.pad. Surface the reason in plain English:
   "`SPI1_MOSI` is blocked by `+3V3` from U2.5 to C7.1; dropping a
   via at U2.5 to reroute on B.Cu". Wait for approval.

7. **After every 5 successful nets**, print a checkpoint:

   ```
   /route-signals checkpoint:
     routed: 5 nets       DRC delta: 0 → 0
     remaining: 12 nets
     est. via count: 3
   ```

   The user can stop here and resume later — the snapshot from step
   1 plus the per-net tracks make the partial state safe to leave.

8. **End-of-queue.** `kc_drc(pcb_path)` once more. If the board is
   DRC-clean, `kc_project_save(project_id)` and report:

   ```
   /route-signals done:
     nets routed: <N>
     tracks written: <T>
     vias added: <V>
     DRC: clean (<duration> ms)
     snapshot: <id from step 1>
   ```

## Tactics

| Net pattern | Default approach |
|---|---|
| `XTAL_*`, `OSC_*` | Shortest path, minimum corners, single layer. If a corner is unavoidable, prefer 45° via two-point Manhattan over a 90°. Drop a ground guard via if the user adds a guard zone. |
| Diff pair `A` / `B` | Route `A` first; route `B` with the same waypoint sequence but offset by `diff_pair_gap_mm` from the bound class. Length-matching is M3 — for M2 just route them adjacently. |
| Strobe / clock in a bus | Slightly wider trace (one step above the data lines' class width) if the class allows. Route between the source and the largest receiver cluster. |
| Bus data | Route in declared bit order (e.g. `D0..D7`). Keep them within a fan-out span; do not split a bus across layers without telling the user. |
| Random GPIO | Whatever Manhattan path the placeholder router emits. Don't agonise — M2-R-08 will do better; the user can hand-tune the worst offenders. |

## Notes for Claude

- **Never** route a signal under a switching node (`SW`, `LX` rails
  from `/route-power`). If the M2-P-04 placeholder picks such a
  midpoint, stop and ask the user to relocate the affected
  footprint via the schematic.
- The placeholder Manhattan router doesn't see clearances. The
  `kc_drc` step in 5 is the source of truth — do not skip it
  because "the track looks fine in the response".
- If the user has not yet run `/route-power`, surface that as a
  warning at step 4 and ask before continuing. Routing signals
  before rails almost always means re-routing later.
