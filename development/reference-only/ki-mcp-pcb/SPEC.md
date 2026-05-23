# ki-mcp-pcb — Project Specification

**Status:** Draft v0.1 — 2026-05-17
**Owner:** layerdynamics@proton.me
**One-liner:** Reusable Python core + CLI + MCP server that lets a user (or Claude Code) turn a plain-text description of a circuit into a manufacturable KiCad PCB.

---

## 1. Vision

`ki-mcp-pcb` is the toolchain that makes "vibe-coding a PCB" reliable instead of lucky.

A user describes a board in plain English (or a structured `.ato`/YAML spec). The toolchain:

1. Parses intent into a **Canonical Intermediate Representation (CIR)** — a typed, validated electrical model.
2. Synthesizes a KiCad **schematic + netlist** from the CIR.
3. **Places** components using a mix of rule-based heuristics and LLM-assisted hints.
4. **Routes** via KiCad's autorouter, Freerouting, or interactive hand-off.
5. **Validates** with ERC, DRC, and design-intent checks (decoupling coverage, return-path sanity, length matching where declared).
6. **Exports** Gerbers, drill files, BOM, pick-and-place, 3D STEP, and a fab-ready manufacturing package.
7. Exposes every step as an **MCP tool** so Claude Code (or any MCP client) can drive the whole pipeline end-to-end.

The same engine powers a Python library, a `kimp` CLI, an MCP server, and (later) an optional web viewer.

---

## 2. Delivery Shapes

Per the project brief, we ship all four shapes from a single codebase:

| Shape | Purpose | Audience |
|---|---|---|
| **Python library** (`ki_mcp_pcb_core`) | Reusable primitives — parsers, synthesis, placement, routing, validation, export | Library consumers, advanced users, the CLI/MCP/web on top of it |
| **CLI** (`kimp`) | One-shot commands — `kimp build board.ato`, `kimp route board.kicad_pcb` | Hardware engineers in a terminal |
| **MCP server** | Stateless tool surface (FastMCP) callable from Claude Code | Claude Code as the primary front end |
| **Web viewer** ✅ shipped (`ki_mcp_pcb_web`) | FastAPI + single-page vanilla-JS browser viewer. Drop a CIR file, see validation/components/nets/BOM/impedance. KiCanvas-embedded `.kicad_pcb` preview. | Showing collaborators, non-CLI users |

**Primary integration target: Claude Code.** Every CLI verb has a matching MCP tool, and the repo ships with a `.claude/` directory containing slash commands and skills so a fresh `claude` session in this repo can drive a board end-to-end.

---

## 3. EDA Backend Decision

**Primary backend: KiCad 9+** (matches the `ki` in the project name and is the only fully scriptable, open-source EDA suite mature enough for production work).

The toolchain uses three KiCad-facing layers:

| Layer | Library | Purpose |
|---|---|---|
| File I/O | `kiutils` | Round-trip parse/emit `.kicad_sch`, `.kicad_pcb`, `.kicad_pro`, symbol/footprint libs |
| Live editor control | `kicad-python` (a.k.a. `kipy`, the KiCad 9 IPC API over Protobuf/NNG) | Drive a running KiCad instance for placement, interactive routing, screenshot capture |
| Headless build | `kicad-cli` | ERC, DRC, Gerber, drill, BOM, 3D STEP, PDF plots — runs in CI without a display |

**Schematic synthesis layer (CIR → KiCad):** we plan to support both, with `atopile` as the recommended default:

- **`atopile`** — a declarative `.ato` DSL with native KiCad output, strong validation, and a compiler that maps cleanly to our CIR. We treat `.ato` as a first-class human-authorable input *and* a viable internal target for synthesis.
- **`skidl`** — Python escape hatch for power users who want to express a circuit imperatively or compose generators (e.g., parametric DDR fly-by termination).

**Backend abstraction:** every KiCad-specific call sits behind a `Backend` interface (`backends/kicad.py`). Future adapters (Horizon EDA, LibrePCB) can be added without touching the CIR or higher layers — but no other backend ships in v1.

---

## 4. Input Format: Natural Language → DSL → Artifacts

Three input tiers, all converging on the same CIR:

```
┌─────────────────────────────┐
│  Tier 1: Natural language   │   "ESP32-S3 dev board, USB-C
│  (Claude Code or kimp ask)  │    PD trigger 9V, 4× GPIO header,
└──────────────┬──────────────┘    SK6812 status LED, JLC-fabbable"
               │ LLM parse
               ▼
┌─────────────────────────────┐
│  Tier 2: .ato / .yaml DSL   │   Human-readable, deterministic,
│  (canonical, diffable,       │   version-controllable. The
│   review-able)              │    contract between LLM and toolchain.
└──────────────┬──────────────┘
               │ deterministic
               ▼
┌─────────────────────────────┐
│  Tier 3: CIR (Pydantic)     │   Typed electrical model:
│                             │    components, nets, constraints,
└──────────────┬──────────────┘    stackup, fab targets.
               │
               ▼
        KiCad artifacts
```

**Why this shape:** LLMs are good at intent → DSL, bad at intent → binary files. The DSL is the audit boundary — a human (or another LLM) can review the `.ato` before any KiCad files are touched. Re-runs are deterministic from the DSL down.

---

## 5. Architecture

### 5.1 Canonical Intermediate Representation (CIR)

Pydantic-typed model. Sketch:

```python
class Board(BaseModel):
    name: str
    stackup: Stackup            # layer count, dielectric, controlled-Z hints
    outline: Outline            # mm-based polygon or "auto"
    components: list[Component] # MPN, footprint, value, refdes, attrs
    nets: list[Net]             # name, members, class (power/HS/diff/RF)
    constraints: list[Constraint]  # length-match, max-stub, separation, keep-out
    fab: FabTarget              # JLC / OSHPark / generic; min trace/space, drill
    bom_policy: BOMPolicy       # preferred distributors, price cap, in-stock
```

CIR is the **stable contract**. Schemas are versioned (`cir_version: "0.3"`); migrations are explicit.

### 5.2 Module Layout

```
ki-mcp-pcb/
├── pyproject.toml
├── README.md
├── SPEC.md                       ← this file
├── CLAUDE.md                     ← guidance for Claude Code in this repo
├── packages/
│   ├── ki_mcp_pcb_core/
│   │   ├── cir/                  # Pydantic schemas, validation, migrations
│   │   ├── parsers/
│   │   │   ├── nl.py             # natural-language → CIR (LLM-backed)
│   │   │   ├── ato.py            # .ato → CIR (wraps atopile compiler)
│   │   │   └── yaml.py           # YAML/TOML → CIR
│   │   ├── synthesis/
│   │   │   ├── schematic.py      # CIR → KiCad schematic via skidl/atopile
│   │   │   ├── footprint_pick.py # MPN → footprint resolution + symbol mapping
│   │   │   └── netlist.py
│   │   ├── placement/
│   │   │   ├── rules.py          # decoupling cluster, connector edge, MCU center
│   │   │   ├── heuristic.py      # force-directed + group constraints
│   │   │   └── llm_hints.py      # accept natural-language placement hints
│   │   ├── routing/
│   │   │   ├── freerouting.py    # spawn Freerouting CLI
│   │   │   ├── kicad_router.py   # KiCad 9 native autoroute via IPC
│   │   │   └── manual_hooks.py   # emit interactive routing TODOs
│   │   ├── validation/
│   │   │   ├── erc.py            # wraps kicad-cli sch erc
│   │   │   ├── drc.py            # wraps kicad-cli pcb drc
│   │   │   ├── decoupling.py     # every IC has bypass caps within Nmm
│   │   │   ├── length_match.py   # check declared length-match groups
│   │   │   └── return_path.py    # HS signals have unbroken reference plane
│   │   ├── export/
│   │   │   ├── gerbers.py
│   │   │   ├── bom.py            # IPC-2581 + CSV
│   │   │   ├── pick_and_place.py
│   │   │   ├── step.py           # 3D STEP
│   │   │   └── fab_packages/     # JLC, OSHPark, PCBWay presets
│   │   ├── sourcing/             # Octopart / Mouser / Digikey / JLC API
│   │   ├── backends/
│   │   │   └── kicad.py          # the only backend in v1
│   │   └── render/               # SVG/PNG previews for chat
│   ├── ki_mcp_pcb_cli/           # `kimp` (Typer-based)
│   ├── ki_mcp_pcb_server/        # MCP server (FastMCP)
│   └── ki_mcp_pcb_web/           # optional Next.js viewer (M4+)
├── scripts/                       # one-shot reusable scripts (see §10)
├── examples/                      # blinky → mixed-signal → high-speed
├── libs/                          # vendored symbol/footprint additions
├── tests/                         # unit + golden-file + end-to-end
└── .claude/
    ├── commands/                  # /pcb-new, /pcb-route, /pcb-fab, ...
    ├── skills/
    │   └── pcb-design/SKILL.md
    └── settings.json              # MCP server auto-registration
```

### 5.3 MCP Tool Surface (for Claude Code)

Every CLI verb has a 1:1 MCP tool. Initial set:

| Tool | Input | Output |
|---|---|---|
| `pcb_parse_intent` | natural-language string | CIR JSON + draft `.ato` |
| `pcb_validate_cir` | CIR JSON | errors[], warnings[] |
| `pcb_synthesize` | CIR or `.ato` path | schematic + netlist paths, ERC report |
| `pcb_place` | board path, placement hints | updated board path, preview PNG |
| `pcb_route` | board path, router choice, rules | routed board, DRC report |
| `pcb_drc` / `pcb_erc` | file path | report JSON |
| `pcb_export_fab` | board path, fab target | zip of gerbers/drill/BOM/PnP |
| `pcb_bom_price` | BOM JSON, region | priced BOM with stock |
| `pcb_render` | sch/pcb path, view | PNG/SVG bytes |
| `pcb_diff` | two board paths | visual + textual diff |
| `pcb_autoplace` | CIR source path, board dims | structured status: moved / skipped refdes from a running KiCad |

All tools are stateless (the board files on disk are the state). Tools return structured JSON, never free-form prose, so Claude Code can chain them reliably.

### 5.4 Claude Code Integration

The repo ships its own Claude Code plugin under `.claude/`:

- **Slash commands** — `/pcb-new`, `/pcb-add`, `/pcb-route`, `/pcb-review`, `/pcb-fab`, each a thin orchestration prompt that calls the MCP tools in sequence.
- **Skill** — `pcb-design/SKILL.md` teaches Claude when to reach for which MCP tool, how to interpret ERC/DRC output, and the design-review checklist for each milestone.
- **Settings** — `.claude/settings.json` auto-registers the local MCP server so `claude` in this repo "just works."

---

## 6. Scope by Milestone

The brief targets full pro stack (RF, DDR, BGA fanout). That is **aspirational for the LLM-driven path** — the spec acknowledges this and ladders up. Manual hand-off at any step is a first-class outcome, not a failure.

| Milestone | Capability | Demo | Honest LLM autonomy |
|---|---|---|---|
| **M0 — Foundations** (≈3 wk) | CIR schema, `.ato`/YAML parsers, MCP skeleton, KiCad 9 + kicad-cli wired, CI green | `kimp validate examples/blinky.ato` | n/a — plumbing |
| **M1 — Hobbyist 2-layer** ✅ closed 2026-05-17 | MCU + USB + LDO + LED-class boards, single-side SMT + THT, JLC fab package. Pipeline: parse → validate → sourcing → synthesize → populate (pcbnew) → DRC → fab zip. No manual KiCad step. | "ESP32-S3 blinky" — text in, gerbers out, no human edits | **High** — LLM drives end-to-end |
| **M2 — Mixed-signal 4-layer** ✅ closed 2026-05-17 | 4-layer FR-4 default stackup with power planes. Three new design-intent validators: decoupling coverage (CIR030), length-match groups (CIR040), partition isolation (CIR050). Real `.kicad_sch` synthesis via kiutils with global labels — ERC has something to chew on. | "STM32 + audio codec" (`examples/stm32_audio.yaml`) — declares analog/digital/power partitions, ferrite-bead bridge, I2S length-match group | **Medium** — LLM proposes, human approves placement |
| **M3 — High-speed digital** ✅ closed 2026-05-17 | Diff pairs (CIR060), controlled impedance from stackup geometry (CIR070, IPC-2141 / Hammerstad), return-path validator (CIR090), post-route length-tuning queue (CIR080). Stackup impedance solver with per-net trace-width / spacing overrides. | "USB 2.0 HS + 100BASE-T PHY" (`examples/usb_eth_phy.yaml`) — 3 diff pairs (USB±, ETH TX±, ETH RX±), declared reference planes, solver-tuned trace geometry hitting 90 Ω / 100 Ω targets. | **Assisted** — LLM sets up constraints, KiCad/human finishes geometric tuning post-route. |
| **M4 — RF / DDR / BGA fanout** ✅ closed 2026-05-17 (scaffolding only) | RF stackup helpers — grounded CPWG impedance solver (Wadell/Wen) for 50 Ω microstrip + 50 Ω CPWG; DDR fly-by topology declarations + CIR100 validator; BGA fanout template registry (`libs/bga_fanout.yaml`) + CIR110 feasibility check against fab DFM; `Board.signoff` field for explicit human-EE acknowledgment of high-stakes features. | "ESP32-C6 + 2.4 GHz CPWG antenna + DDR3L fly-by sketch" (`examples/esp32_c6_rf.yaml`) | **Co-pilot only** as designed — the validators catch structural mistakes (mismatched fly-by order, infeasible BGA pitch on the chosen fab, impedance target wildly off the achievable). Final routing + sign-off is the human EE's call. `Board.signoff.{rf,ddr,bga_fanout}_reviewed` flags suppress the "needs human review" warnings. |

**Cross-cutting throughout all milestones:** sourcing API integration, fab-checker (DFM rules per target fab), 3D STEP export, IPC-2581 BOM.

**KiCad 9 IPC bridge (post-M4) ✅ shipped 2026-05-18.** `placement.kipy_placer` wires the toolchain to a running KiCad via `kicad-python` (kipy). `kimp autoplace` and the `pcb_autoplace` MCP tool plan declarative-hint placements through `placement.plan_placement`, then push them atomically inside one `begin_commit()` / `push_commit()` — the user sees a single undo entry. The bridge degrades gracefully through a structured `KipyStatus` (`kipy_unavailable`, `kicad_unreachable`, `no_open_board`, `no_matching_refdes`, `commit_failed`, `ok`), so the rest of the text-to-fab pipeline still runs without it. Pinned via the `ipc` extra (`uv sync --extra ipc`) to keep kipy's evolving API surface from breaking core users.

---

## 7. Non-Goals (v1)

- **Autonomous RF/DDR layout.** We scaffold and validate; we do not promise an LLM will route DDR correctly without review.
- **Schematic capture UI.** KiCad's eeschema remains the editor of record for visual edits. We round-trip cleanly.
- **Component creation from scratch.** We pick from existing symbol/footprint libraries and resolve MPN → existing footprint. New footprint generation is a stretch goal.
- **Simulation.** No SPICE in v1 (CIR is designed to support it later via ngspice).
- **Non-KiCad backends.** The interface exists; no second backend ships.

---

## 8. Quality Gates

Every PR runs:

1. **Unit tests** on CIR validation, parsers, synthesis primitives.
2. **Golden-file tests** — example boards re-synthesize to byte-identical `.kicad_sch` / `.kicad_pcb` modulo timestamps.
3. **End-to-end smoke** — `kimp build examples/blinky.ato && kicad-cli pcb drc … && kicad-cli pcb export gerbers …` returns 0.
4. **Round-trip diff** — CIR → KiCad → re-parse → CIR, semantic diff is empty.
5. **Fab dry-run** — generated gerbers pass JLCPCB's public DFM API (or a local mirror of its rules) for the JLC target.

Milestone demos must DRC-clean and produce a fab package that an operator could send out without manual edits.

---

## 9. Risks & Mitigations

| Risk | Mitigation |
|---|---|
| KiCad 9 IPC API still maturing | Pin a specific kipy version; fall back to kiutils file-level edits where IPC is unstable |
| LLM hallucinates MPNs / footprints | Strict MPN resolver: every part must exist in Octopart/JLC stock list at synthesis time, else error |
| Autorouter output is poor quality on dense boards | Default to Freerouting for v1; emit "manual route" TODOs for declared HS nets; keep human-in-the-loop hooks |
| Placement is the hardest unsolved piece | Start rule-based + cluster heuristics; treat ML placement as M3+ research, not a v1 dependency |
| Atopile API churn | Keep `.ato` parsing behind our own parser interface; pin atopile version; CIR is the stable contract |
| Scope creep into pro/RF | Milestones are the contract — RF/DDR is M4 and explicitly assisted, not autonomous |

---

## 10. Reusable Scripts (per project brief)

The `scripts/` directory ships standalone tools each engineer can use without the full pipeline:

- `nl_to_ato.py` — pipe natural language in, get `.ato` out
- `bom_price.py` — CSV BOM → priced + in-stock CSV (Octopart/Mouser/JLC)
- `footprint_pick.py` — MPN → suggested footprint with confidence score
- `drc_summary.py` — kicad-cli DRC JSON → human-readable triage
- `gerber_pack.py` — board → fab-target-specific zip with the right filenames and layers
- `decoupling_check.py` — board file → list of ICs missing nearby bypass caps
- `length_audit.py` — board file → declared vs. actual trace lengths for length-matched groups
- `panelize.py` — single board → v-score / mouse-bite panel
- `step_render.py` — board → 3D STEP + rendered PNG

Each script is also exposed as an MCP tool and as a `kimp` subcommand.

---

## 11. Tech Stack Summary

- **Language:** Python 3.11+ (typed, ruff + mypy strict)
- **Schemas:** Pydantic v2
- **CLI:** Typer + Rich
- **MCP server:** FastMCP
- **EDA:** KiCad 9+ (`kicad-cli`, `kiutils`, `kicad-python`/kipy)
- **Synthesis:** atopile (default) + skidl (escape hatch)
- **Routing:** Freerouting (jar), KiCad native autoroute via IPC
- **Sourcing:** Octopart API, JLCPCB parts library, Mouser API
- **Packaging:** uv + hatch, monorepo via workspace
- **CI:** GitHub Actions matrix (Linux + macOS), KiCad in container
- **Docs:** mkdocs-material

---

## 12. Open Questions

These are the decisions that need an answer before M1 closes:

1. **DSL canonicalization** — is `.ato` the canonical human input, or do we keep a thin YAML alternative? (Recommendation: `.ato` canonical, YAML as a parser plugin.)
2. **Symbol/footprint sourcing** — KiCad stock libs only, or auto-pull from SnapEDA / Ultra Librarian on demand? (Recommendation: stock libs + user-vendored `libs/` in v1; auto-pull in M2.)
3. **LLM placement hints** — do we let the LLM produce coordinates directly, or only declarative hints ("MCU center, decouplers within 2 mm, USB connector on south edge")? (Strong recommendation: declarative only.)
4. **Web viewer scope** — do we ship our own viewer, or embed KiCanvas? (Recommendation: embed KiCanvas in M4, don't reinvent.)

---

## 13. Next Steps

Immediately after this spec is accepted:

1. Land repo scaffold (`pyproject.toml`, workspace layout, CI skeleton).
2. Draft CIR v0.1 Pydantic schema in `packages/ki_mcp_pcb_core/cir/`.
3. Stand up the MCP server with a no-op `pcb_validate_cir` tool and verify Claude Code connects.
4. Wire `kicad-cli` into CI inside a container; get a hello-world `.kicad_pcb` → Gerbers passing on every push.
5. Write the M1 demo target (`examples/esp32_s3_blinky.ato`) as the executable definition of "M1 done."

---

## Appendix A — Glossary

- **CIR** — Canonical Intermediate Representation; our typed electrical model.
- **DRC / ERC** — Design Rule Check (board) / Electrical Rule Check (schematic).
- **DFM** — Design For Manufacturing; fab-specific rules (min trace/space, drill, annular ring).
- **IPC API** — KiCad 9's new Protobuf-over-NNG plugin interface; supersedes SWIG bindings.
- **MPN** — Manufacturer Part Number.
- **MCP** — Model Context Protocol; how Claude Code talks to external tools.
- **Stackup** — physical layer/dielectric structure of the PCB.
