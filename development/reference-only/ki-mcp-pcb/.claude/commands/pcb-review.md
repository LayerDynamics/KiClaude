---
description: Design-review a board against the current milestone's checklist.
argument-hint: <board path>
---

You are doing a design review on:

$ARGUMENTS

Use the milestone checklist in `.claude/skills/pcb-design/SKILL.md` matching the board's complexity. Cover:

- Power: bypass cap coverage per IC, bulk cap sizing, LDO/regulator stability components.
- Ground: continuous return paths, no slots under high-speed nets.
- Signals: declared length-match groups within tolerance, diff pair routing.
- DFM: trace/space ≥ fab target minimums, drill/annular ring, soldermask sliver.
- BOM: every MPN resolved, no out-of-stock parts.

Report findings as a list of "issue → impact → suggested fix". Do not mutate files in this command — recommend only.
