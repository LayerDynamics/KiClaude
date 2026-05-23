---
name: route-freerouting
description: Auto-route the active PCB via Freerouting (GPL subprocess; SPEC NFR-009 isolation). Exports DSN, runs Freerouting headless, imports the SES, and re-runs DRC. Use when manual + walk-around routing has driven the easy nets but the remaining net set is still large.
argument-hint: "[--passes <int>] [--timeout-s <float>] [--jar <path>]"
allowed-tools:
  - mcp__kiclaude__kc_kcir_get
  - mcp__kiclaude__kc_drc
  - mcp__kiclaude__kc_route_freerouting
  - mcp__kiclaude__kc_snapshot_create
  - mcp__kiclaude__kc_snapshot_revert
  - mcp__kiclaude__kc_project_save
---

# /route-freerouting — auto-route via Freerouting

Freerouting is the fallback router for stubborn boards. It is GPL,
so kiclaude isolates it behind a subprocess invocation — no JNI, no
linkage — and `kc_route_freerouting` round-trips through the
kiconnector broker (M2-P-06). The board file on disk is the only
artifact that crosses the licence boundary.

Argument: `$ARGUMENTS` — optional. Supported flags:

| Flag | Default | Notes |
|---|---|---|
| `--passes` | `100` | Freerouting's `--passes` arg. Higher = longer + better. Boards under 100 nets rarely need >50. |
| `--timeout-s` | `300` | Wall-clock cap on the subprocess. Kill on overrun. |
| `--jar` | `$KICLAUDE_FREEROUTING_JAR` env, else `freerouting.jar` on PATH | Override only when testing a specific build. |

## Sequence

1. **Snapshot.** `kc_snapshot_create(project_id, label="before /route-freerouting")`.
   This is the rollback anchor if Freerouting emits a SES the user
   doesn't like. Quote the snapshot id in step 6's summary.

2. **Sanity-check the inputs.**
   - `kc_kcir_get(project_id, view=["pcb.nets","pcb.net_classes","pcb.footprints","pcb.outline","pcb.tracks"])`.
   - Confirm a board outline exists. Freerouting requires a closed
     Edge.Cuts polygon — if `pcb.outline` is empty, **stop** and ask
     the user to draw one via the M2-T-05 outline tool.
   - Confirm every footprint has a final position. If any refdes
     sits at `(0, 0)` with no other placement, surface them and ask
     before continuing.
   - Confirm net classes look sane — Freerouting reads
     `trace_width_mm`, `clearance_mm`, `via_diameter_mm`,
     `via_drill_mm`. If any class has zeros, warn explicitly.

3. **Pre-flight DRC.** `kc_drc(pcb_path)`. Note the current error and
   warning counts so step 5 can compute the delta. Freerouting won't
   delete tracks — pre-existing DRC errors persist unless the affected
   net was unrouted before the run.

4. **Run Freerouting.** `kc_route_freerouting(pcb_path, passes=<N>, timeout_s=<T>, freerouting_jar=<path>)`.
   - Surface the response's `log` field verbatim — Freerouting's
     stdout names the nets it failed to complete.
   - If `ok: false`, **stop**. Suggest `kc_snapshot_revert(<id from step 1>)`
     and ask whether to relax net-class widths or move a footprint.

5. **Post-flight DRC.** `kc_drc(pcb_path)`. Report the delta:

   ```
   /route-freerouting result:
     passes:        <N>
     duration:      <ms>
     log tail:      <last 6 lines of Freerouting stdout>
     SES path:      <ses_path from tool response>
     DRC delta:     <before>.errors → <after>.errors, <before>.warnings → <after>.warnings
     unrouted nets: <list parsed from log; empty if Freerouting reports none>
   ```

   If DRC degraded (more errors than before), surface the new
   errors with their `(layer, position_mm, type)` and ask whether
   to revert. Do not auto-revert.

6. **Save when the user approves.** `kc_project_save(project_id)`.
   Also remind the user that any subsequent `kc_track_route` calls
   will append to (not replace) what Freerouting wrote — the typical
   follow-up is `/drc-fix` on the residual issues, not another
   `/route-freerouting` pass.

## When NOT to call `/route-freerouting`

- **Before `/route-power`.** Freerouting honours net classes but
  doesn't know which nets are rails. Run `/route-power` first so
  the rails are wide before Freerouting sees the board.
- **On boards with controlled-impedance nets.** Freerouting
  doesn't know about diff pairs or length-match groups in M2 —
  those land in M3 with the PnS router. Routing USB / HS lanes
  through Freerouting will pass DRC but fail signal-integrity
  intent.
- **As a "first attempt".** Run the placeholder walk-around
  router (via `kc_track_route`) on the easy nets first so that
  Freerouting has fewer pairs to chew on; `--passes 50` on a
  half-routed board often beats `--passes 200` on a blank one.

## Notes for Claude

- The subprocess is bounded by `--timeout-s`. If the tool returns
  early with `ok: false` and a timeout marker in the log, ask
  whether to bump the timeout, lower `--passes`, or move on with
  a partial route.
- Freerouting writes intermediate `.dsn` + `.ses` files under the
  kiconnector's temp dir; the kc_route_freerouting response
  surfaces the SES path so the user can inspect the raw output.
- Never edit the SES file manually — kiclaude's importer expects
  the exact format Freerouting emitted. If a re-import is needed,
  call `kc_route_freerouting` again rather than re-running just
  the import.
