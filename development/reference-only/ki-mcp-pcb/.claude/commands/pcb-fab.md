---
description: Produce a fab-house-ready zip from a routed board.
argument-hint: <board path> [--target jlcpcb|oshpark|pcbway|generic]
---

Run the MCP tool `pcb_export_fab` with the arguments parsed from:

$ARGUMENTS

After export, summarize: file list, total trace count, layer count, and any DFM warnings. Then offer to upload the package or to render a 3D STEP preview.
