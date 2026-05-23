---
name: kicad-schematic
description: Edit KiCad schematics through kiclaude's typed kc_* MCP tools. Use when the user asks you to add a symbol, route a wire, place a label, attach an MPN, or check a design for errors against a .kicad_pro project.
allowed-tools:
  - mcp__kiclaude__kc_ping
  - mcp__kiclaude__kc_project_open
  - mcp__kiclaude__kc_project_save
  - mcp__kiclaude__kc_kcir_get
  - mcp__kiclaude__kc_validate
  - mcp__kiclaude__kc_erc
  - mcp__kiclaude__kc_symbol_add
  - mcp__kiclaude__kc_symbol_edit
  - mcp__kiclaude__kc_wire_connect
  - mcp__kiclaude__kc_label_attach
  - mcp__kiclaude__kc_mpn_resolve
  - mcp__kiclaude__kc_snapshot_create
  - mcp__kiclaude__kc_snapshot_revert
---

# kicad-schematic — kiclaude schematic editing skill

You are editing a real KiCad project that the user has opened in the
kiclaude browser. Every persistent edit goes through one of the
twelve `kc_*` MCP tools listed above; never propose changes as
free-form text the user has to type. **The `.kicad_sch` / `.kicad_pro`
files on disk are the contract — `kc_project_save` is how your work
becomes durable.**

## First principles you must obey

1. **No raw coordinates.** You see refdes / lib_id / net names — never
   `(x, y)` placements. Coordinate-driven tools (`ui_symbol_place_xy`,
   `ui_wire_draw_points`, …) are not in your tool list and never will
   be. Describe placement declaratively: "near the MCU", "south edge",
   "between R1 and R2 on the GND net" — the placement engine in kiclaude
   resolves this against the live KCIR.

2. **Every MPN must resolve.** If you need a specific part, call
   `kc_mpn_resolve` first and only commit a footprint/value pair that
   came back `found: true` from a real distributor lookup. Hallucinated
   MPNs are a workflow-breaking failure.

3. **Snapshot before destructive work.** Before a multi-step edit
   (e.g. swapping a regulator family) call `kc_snapshot_create` with a
   descriptive label so the user can revert the whole batch from the
   ActivityJournal sidebar with one click.

4. **Validate before declaring done.** After any mutation, call
   `kc_validate` (KC001..KC011 KCIR checks) and `kc_erc` (kicad-cli ERC)
   and report the findings. "ERC clean" is not assumed — it's evidenced.

## Tool-by-tool guide

### Read-only

| Tool | When to use |
|---|---|
| `kc_ping` | Sanity-check the MCP server is reachable. Use once at session start if the user's first ask isn't a quick read. |
| `kc_project_open(path)` | Open a KiCad project directory. Returns a `project_id` you pass to every subsequent tool. Always call this first if the user references a path. |
| `kc_kcir_get(project_id, view?)` | Read the live KCIR. Pass `view: ["schematic"]` if you only need symbols/wires; pass `["summary"]` for refdes/footprint counts; pass `["full"]` only when truly necessary (large payload). |
| `kc_validate(project_id)` | Run KC001..KC011 structural checks. Returns `findings[]` with `code`, `severity`, `message`. Read-only — safe to call anywhere. |
| `kc_erc(project_id, kicad_project_path)` | Shells out to `kicad-cli sch erc`. Slower (~1–3s). Returns the list of electrical violations. |
| `kc_mpn_resolve(mpn, … hints)` | Distributor + library lookup for a manufacturer part number. Returns `found: true/false`, `lib_id` (e.g. `Device:R`), and a recommended `footprint` (e.g. `Resistor_SMD:R_0805_2012Metric`). |

### Mutating

Each of these triggers the M1-P-06 permission gate and creates an
auto-snapshot the ActivityJournal can revert.

| Tool | When to use | Don't |
|---|---|---|
| `kc_symbol_add(project_id, lib_id, value, refdes?, sheet_uuid?, footprint?, mpn?, hints?)` | Drop a new symbol on a sheet. Prefer letting kiclaude auto-assign the refdes (omit it). Set `mpn` only after `kc_mpn_resolve` confirms it. | Don't pass a position — that's UI-only. |
| `kc_symbol_edit(project_id, symbol_uuid, value?, refdes?, footprint?, mpn?, dnp?, in_bom?, on_board?)` | Adjust an existing symbol's metadata. The `symbol_uuid` comes from `kc_kcir_get`. | Don't change `lib_id` here — swap the symbol entirely with delete+add. |
| `kc_wire_connect(project_id, from_ref, to_ref, sheet_uuid?, net_hint?)` | Connect two pins / labels / junctions by reference (e.g. `"U1.VDD"`, `"R3.1"`, label name `"3V3"`). | Don't try to wire by coordinates. |
| `kc_label_attach(project_id, kind, text, target_ref, sheet_uuid?)` | Attach a `local`, `global`, or `hierarchical` label to a wire / pin endpoint. See "Label kinds" below. | Don't use `text-only` labels for power — use `global` (e.g. `+3V3`) so it propagates across sheets. |
| `kc_snapshot_create(project_id, label)` | Manual safety net before a multi-tool edit. Returns `snapshot_id`. | Don't snapshot before every single call — auto-snapshot already covers that. |
| `kc_snapshot_revert(project_id, snapshot_id)` | Roll back to a named snapshot (usually from the user's `/revert` request). | |
| `kc_project_save(project_id, target_dir?)` | Write the in-memory project back to disk. Required to persist edits across sessions. | Don't call this on every mutation — batch at the end of a logical change set. |

## Label kinds — what's the difference?

- **`local`** — Lives on one sheet. The same `text` on two different
  sheets means two different nets. Use for short scoped signals
  ("RX", "CS").
- **`hierarchical`** — Crosses sheet boundaries through a sheet pin.
  Use when an inner sheet exposes a signal to its parent. Requires
  the parent sheet to declare a matching pin.
- **`global`** — Same net everywhere in the project. Power rails
  (`+3V3`, `GND`, `VBUS`) are almost always global. Reach for global
  only when the signal really is global; otherwise prefer local +
  hierarchical for documentation hygiene.

When in doubt: **power → global, exposed interface → hierarchical,
internal wire → local**.

## Hierarchical-sheet etiquette

- The root sheet has no parent. Every other sheet's `parent` field
  points to a sheet UUID. `kc_kcir_get(view: ["schematic"])` returns
  the full `sheets[]` tree.
- A child sheet must declare a `sheet_pin` for every hierarchical
  label it exposes. If the user adds a hierarchical label inside a
  child, you must also call `kc_label_attach` on the parent's matching
  pin (or surface the gap so the user does).
- Refdes uniqueness is project-wide, not sheet-local. Two `R1`s
  across sheets is a KC005 finding.

## Refdes conventions

kiclaude follows IEEE 315 / KiCad defaults:

| Prefix | Family |
|---|---|
| `R` | Resistor |
| `C` | Capacitor |
| `L` | Inductor |
| `D` | Diode (including LED) |
| `Q` | Transistor (BJT/MOSFET) |
| `U` | IC / module |
| `J` | Connector / jack |
| `Y` / `X` | Crystal / oscillator |
| `SW` | Switch |
| `TP` | Test point |
| `F` | Fuse |
| `K` | Relay |
| `H` | Mounting hole / hardware |

**You should omit the refdes when calling `kc_symbol_add`** —
kiclaude's annotation pass (KiCad-compatible) assigns the next free
number in the family. Only specify a refdes when the user explicitly
asks for one ("call it R7").

## Decision tree — common asks

> **"Add a 10k pull-up to MCU.RESET"**
> 1. `kc_mpn_resolve` for a 10 kΩ 0603 5% resistor → confirm
>    `lib_id: "Device:R"`, `footprint: "Resistor_SMD:R_0603_1608Metric"`.
> 2. `kc_symbol_add(project_id, lib_id="Device:R", value="10k",
>    footprint=…, hints=["near MCU", "RESET net"])`.
> 3. `kc_wire_connect(project_id, from_ref="R<new>.1",
>    to_ref="+3V3")`.
> 4. `kc_wire_connect(project_id, from_ref="R<new>.2",
>    to_ref="U1.RESET")`.
> 5. `kc_validate` + `kc_erc` → report findings.
> 6. `kc_project_save` → confirm written paths.

> **"Why is ERC failing?"**
> 1. `kc_erc(project_id, project_path)` → group by `severity`.
> 2. Quote each error verbatim and propose a one-tool fix per error
>    *without applying it* — the user approves each via the
>    PreToolUse gate.

> **"Undo the last batch"**
> The user normally clicks "Revert" in the ActivityJournal sidebar —
> you only call `kc_snapshot_revert` when they explicitly say
> "revert in chat" or you're inside a `/revert` slash command.

## Output discipline

- Cite the tool you used + the structured response shape in your
  reply — e.g. `"kc_symbol_add → {ok: true, symbol_uuid: "..."}"`.
- After mutations, summarize the **diff** ("added R7 (10k pullup),
  connected to +3V3 and U1.RESET, ERC clean"). The user is reading
  Git diffs alongside your chat — keep your prose anchored to what
  actually changed.
- Never invent UUIDs, refdes numbers, or net names. Read them from
  the most recent `kc_kcir_get`.

## When to stop and ask

- A mutation requires raw coordinates → ask the user to drag it from
  the library sidebar (raw coordinates are UI-only).
- An MPN couldn't be resolved → ask which substitute is acceptable.
- ERC produces a finding you can't categorize → ask before guessing.
