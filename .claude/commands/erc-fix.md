---
name: erc-fix
description: Read the current ERC output, pick the highest-severity issue, propose a single fix, wait for approval, apply it, and re-run ERC. Loop until ERC is clean or the user stops.
allowed-tools:
  - mcp__kiclaude__kc_kcir_get
  - mcp__kiclaude__kc_validate
  - mcp__kiclaude__kc_erc
  - mcp__kiclaude__kc_symbol_add
  - mcp__kiclaude__kc_symbol_edit
  - mcp__kiclaude__kc_wire_connect
  - mcp__kiclaude__kc_label_attach
  - mcp__kiclaude__kc_snapshot_create
  - mcp__kiclaude__kc_snapshot_revert
  - mcp__kiclaude__kc_project_save
---

# /erc-fix — interactive ERC remediation loop

A focused loop that drives one ERC error to zero at a time. Designed
to be re-runnable — every iteration ends with a fresh ERC pass so the
user can call `/erc-fix` again on the next-highest issue.

## Sequence

1. **Snapshot before the first fix.**
   `kc_snapshot_create(project_id, label="before /erc-fix iter <N>")`
   where `<N>` is the iteration count starting at 1. If the user
   regrets a proposed fix mid-loop, they revert from the
   ActivityJournal.

2. **Run ERC.**
   `kc_erc(project_id, project_path)`. If the report is clean
   (`issues == []` after dropping `exclusion` and `ignore`), say so
   and exit the loop. Quote the duration_ms so the user has a feel
   for the workflow cost.

3. **Pick the issue to fix.** Sort `issues` by severity:
   `error` > `warning`. Within a tier, prefer:
   - Issues on the active sheet over remote sheets.
   - Issues with concrete `position_mm` over sheet-level findings.
   - Issues that involve a single net (easier to repair) over those
     spanning many.

4. **Diagnose and propose one fix.** Cite the issue verbatim
   (`type`, `description`, `sheet`, `position_mm`), then explain in
   2–3 sentences:
   - Why this rule triggered.
   - What the fix is going to be.
   - Which tool call you'll make.
   Do **not** call the mutating tool yet — the M1-P-06 permission
   gate will pause it anyway, but stating intent before calling
   gives the user a chance to redirect.

5. **Apply exactly one mutation.** A single `kc_symbol_add`,
   `kc_symbol_edit`, `kc_wire_connect`, or `kc_label_attach` call.
   Never bundle two mutations in one iteration — that defeats the
   loop's "one issue at a time" guarantee.

6. **Re-run validators.**
   - `kc_validate` → confirm no KC001..KC011 regressions.
   - `kc_erc` → confirm the original issue is gone AND no new error
     has appeared.

   If the original issue persists, surface that fact to the user and
   stop the loop — don't blindly try another tactic.

7. **Report and ask whether to continue.** Summary block:

   ```
   /erc-fix iter <N>:
     fixed: <type> on <sheet> @ <position> — <description>
     applied: <tool name> ( <one-line of arguments> )
     ERC delta: <before>.errors → <after>.errors, <before>.warnings → <after>.warnings
   next-highest: <type> on <sheet> ...   [or "ERC clean"]
   ```

   Then ask: "Continue with the next issue?" Don't auto-iterate
   without the user's nod — they may want to inspect the diff
   first.

8. **On user-stop**, run `kc_project_save` so the partial progress
   sticks, then end the command.

## Tactics by issue type

| `type` | Default fix |
|---|---|
| `pin_not_driven` | Tie pin to its expected level via `kc_label_attach` (a global label such as `+3V3` or `GND` is usually right). For inputs left floating, propose a 10 kΩ pull-up/-down. |
| `pin_not_connected` | If the pin really is intentional NC, propose a No-Connect flag through `kc_wire_connect` to a `kc_label_attach(kind="local", text="NC")`. Otherwise, route the wire. |
| `two_outputs` | Identify which output is correct; convert the other to an open-drain pull-up or remove the conflicting connection. |
| `power_pin_no_supply` | Add the missing supply rail label (`+3V3`, `+5V`, `VBAT`, …) and wire the pin to it. |
| `hierarchical_label_mismatch` | Compare the child sheet's label to the parent sheet pin. Either rename the label or add the missing pin via `kc_label_attach(kind="hierarchical")`. |
| `bus_unconnected` | Walk the bus members and confirm every signal has a matching label on the bus. |

For any other `type`, **stop and ask**. Don't guess at a fix the spec
doesn't cover.

## Notes for Claude

- Each iteration is one snapshot + one mutation + one re-ERC. The
  loop's value is in the cadence, not the throughput.
- Never silently widen scope. If fixing one error needs three tool
  calls, say so explicitly and ask before proceeding.
- The user can `kc_snapshot_revert` to undo any iteration. Reference
  the snapshot label you wrote in step 1 in your iteration summary
  so they know what label to revert to.
