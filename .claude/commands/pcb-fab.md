---
name: pcb-fab
description: Run a pre-flight DFM dry-run, then generate the full fab bundle (gerbers, drill, pick-and-place, BOM) via kc_export_fab. Surfaces a manifest of every file produced so the user can verify before shipping to a board house.
argument-hint: "[target: generic | jlcpcb | oshpark | pcbway]   default: generic"
allowed-tools:
  - mcp__kiclaude__kc_kcir_get
  - mcp__kiclaude__kc_drc
  - mcp__kiclaude__kc_export_fab
  - mcp__kiclaude__kc_snapshot_create
  - mcp__kiclaude__kc_project_save
---

# /pcb-fab — pre-flight + fab bundle

The last command before a board ships to a fab. Runs a DFM dry-run
against the fab's accept list, then emits gerbers + drill + PnP + BOM
in one call. If DRC isn't clean, this command refuses to proceed —
the fab doesn't accept broken boards and neither do you.

Argument: `$ARGUMENTS` — optional fab target.

| Target | Output convention | Notes |
|---|---|---|
| `generic` | Protel filename extensions (`.GTL`, `.GBL`, …), Excellon drill, IPC-2581 BOM | Lowest common denominator. Use when you don't know the board house yet. |
| `jlcpcb` | RS-274X gerbers in a zip, Excellon drill, CPL position file (both sides) | JLC accepts panelised + de-panelised. Pair with `/panelize` if needed. |
| `oshpark` | RS-274X with `.GTS`/`.GBS` extensions, top-side PnP only | OSHPark assembles top-side only. |
| `pcbway` | RS-274X, Excellon drill, top-side PnP, BOM in PCBWay column order | PCBWay's PnP wants front-side; back-side goes into a separate file. |

Anything else → stop and list the supported targets.

## Sequence

1. **Confirm DRC is clean.** `kc_drc(pcb_path)`. If errors exist,
   **refuse**. Print the issue count and suggest `/drc-fix`. Don't
   "proceed anyway" even on user request — the fab will reject the
   board and you'll have to re-pay setup costs.

   Warnings are non-blocking but echo them; the user often wants to
   see them one more time before signing off.

2. **Sanity-check the design.** `kc_kcir_get(project_id, view=["pcb.outline","pcb.layers","pcb.footprints","pcb.nets"])`.
   Surface, in plain English:
   - Board outline dimensions (`bbox` of `pcb.outline`).
   - Layer count (count of `pcb.layers` with `kind: "signal" | "power"`).
   - Footprint count, split by `(smd, through_hole, other)`.
   - Net count (excluding unconnected pads).

   If the outline is empty, **stop** — the fab needs it.

3. **DFM dry-run (M2-Q-03).** Call the kiserver DFM checker
   indirectly: there's no Claude-facing tool, but you can ask the
   user to run `kiclaude build $project --target <target>` in their
   shell, then paste the DFM JSON back. Surface the dry-run findings
   by severity:
   - `error` items are blocking (e.g. min track below JLC 0.127 mm).
   - `warning` items are advisory (e.g. silk over pad). Echo them
     and proceed unless the user objects.

   The DFM checker doesn't mutate the board; it reads `.kicad_pcb`
   and reports.

4. **Snapshot for traceability.** `kc_snapshot_create(project_id, label="before /pcb-fab <target>")`.
   This isn't for rollback — it's for matching the gerber zip to the
   exact `.kicad_pcb` state on a future audit.

5. **Save the project.** `kc_project_save(project_id)`. The exporter
   reads the on-disk file, not the in-memory KCIR; if the user has
   unsaved tweaks, they'll vanish from the gerbers.

6. **Export.** `kc_export_fab(pcb_path, sch_path=<.kicad_sch path>, output_dir=<tempdir or user-supplied>, target=<target>, timeout_s=180)`.

   The response carries:
   ```json
   {
     "ok": true,
     "target": "jlcpcb",
     "output_dir": "/tmp/kiclaude-fab-XXX",
     "artifacts": {
       "gerbers": { "files": [...], "duration_ms": ... },
       "drill":   { "files": [...], "duration_ms": ... },
       "pos":     { "files": [...], "duration_ms": ... },
       "bom":     { "files": [...], "duration_ms": ... }
     }
   }
   ```

7. **Print the manifest.** One section per artifact:

   ```
   /pcb-fab <target> done:
     output_dir: <path>
     gerbers (<N> files, <ms> ms):
       - F.Cu.gtl  ⊥ <bytes>
       - B.Cu.gbl  ⊥ <bytes>
       - F.Mask.gts ⊥ <bytes>
       - B.Mask.gbs ⊥ <bytes>
       - F.Silk.gto ⊥ <bytes>
       - B.Silk.gbo ⊥ <bytes>
       - Edge.Cuts.gko ⊥ <bytes>
     drill:
       - <basename>.drl ⊥ <bytes>
     pos:
       - <basename>-pos.csv ⊥ <bytes>
     bom:
       - <basename>-bom.csv ⊥ <bytes>
     snapshot: <id from step 4>
   ```

   Use `⊥` (or `·`) — anything unambiguous so the user can scan.

8. **Closing reminder.** Tell the user:
   - The output_dir is ephemeral if it was a tempdir; suggest moving
     into the project's `fab/` directory before they wipe the temp.
   - The snapshot id is the audit handle; if the fab calls with a
     question in 3 months, they can reproduce the exact gerbers by
     reverting to that snapshot and re-running `/pcb-fab <target>`.
   - The PnP CSV column order is target-specific — surface it for
     JLC/OSHPark/PCBWay explicitly:
     | Target | Required columns |
     |---|---|
     | jlcpcb | Designator, Val, Package, Mid X, Mid Y, Rotation, Layer |
     | oshpark | Refdes, Value, Package, X, Y, Rotation, Side |
     | pcbway | Refdes, Comment, Footprint, MidX, MidY, Rotation, Layer |

## When to refuse

- **DRC errors present.** Always refuse, even with `--force`. Make
  the user run `/drc-fix` first.
- **No board outline.** Same — the fab can't make a board without
  an Edge.Cuts polygon.
- **Floating pads / unconnected critical nets.** Stop and ask whether
  the unconnected state is intentional (test point) or a bug.
- **A footprint missing a `mpn` or `lib_id`.** PnP and BOM emit
  garbage rows; force the user to call `kc_symbol_edit` first.

## Notes for Claude

- This command is read-only on the design (snapshot aside). It
  produces files on disk; it does not mutate `.kicad_pcb`. The
  PreToolUse gate auto-approves it as a result.
- The exporter timeout (`timeout_s`) defaults to 180s — bump it for
  >10-layer boards or boards with thousands of pads.
- The DFM checker (M2-Q-03) lives in `services/kiserver/src/kiserver/dfm.py`
  and is invoked via `kiclaude build` — there's no `kc_dfm` tool
  yet. If the user wants the check in-conversation, ask them to
  paste the JSON output.
