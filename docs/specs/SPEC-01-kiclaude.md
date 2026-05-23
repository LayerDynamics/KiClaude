# SPEC-01 — kiclaude

**Status:** Draft v0.1 — 2026-05-21
**Owner:** layerdynamics@proton.me
**One-liner:** A browser-native, KiCad-compatible EDA suite — schematic + PCB + manufacturing — with the Claude Agent SDK and Claude Code as first-class collaborators on every action.

---

## 1. Identity

### 1.1 What kiclaude IS

kiclaude is a **web-only**, **KiCad-file-format-compatible** electronics design automation (EDA) application. It opens, edits, and saves `.kicad_pro` / `.kicad_sch` / `.kicad_pcb` files round-trip-fidelity with KiCad 9+, but it is **not** a KiCad GUI puppet — kiclaude ships its own rendering, geometry, validation, and synthesis engine and never requires a running KiCad instance.

The marquee differentiator is that **Claude is a first-class collaborator, not an add-on**. Every UI action exposes a typed tool to Claude. Every Claude action surfaces in the UI with a visible audit trail. The chat sidebar is a persistent panel, not a modal. Slash commands and skills the user types in the editor are the same `.claude/` artifacts that work from Claude Code on the command line.

### 1.2 What kiclaude IS NOT (v1)

- **Not a KiCad fork.** kiclaude reads/writes KiCad's file format; it does not embed any KiCad source.
- **Not a clone of Flux / EasyEDA / Altium 365.** Those are cloud-locked walled gardens; kiclaude is browser-first but local-file-native and open-format-native.
- **Not a thin chat-only wrapper.** The visual editor is fully featured — selection, drag, properties, multi-layer routing, zone fills, DRC overlays. Claude operates alongside, not instead of, the visual editor.
- **Not a successor to or rewrite of ki-mcp-pcb.** ki-mcp-pcb is referenced for ideas (Canonical IR design, validators, MCP wiring) but kiclaude ships an independent Rust+TypeScript engine. The two products coexist; ki-mcp-pcb remains the Python toolchain shape.
- **Not a desktop app.** No Electron, no Tauri in v1. Distribution is a hosted SaaS + an optional local sync daemon (`kiconnector`) for filesystem access where the browser's File System Access API isn't enough.

### 1.3 Primary audiences

| Audience | Need | How kiclaude serves them |
|---|---|---|
| Hardware engineers fluent in KiCad | Faster iteration on routine boards; AI to do the parts they hate | Full editor parity for 2-/4-layer boards; Claude handles decoupling, length-match, BOM, fab export |
| Makers / "vibe-coders" who can describe a circuit but can't draw a schematic | Text → manufacturable board | Claude-driven `nl → KCIR → schematic → PCB → fab` pipeline; user reviews each step visually |
| Teams shipping hardware-as-code | Reproducible boards in CI; review-friendly diffs | Git-native project layout; CLI for headless build; PR-style "board diff" view |
| Educators / students | A web-only EDA that runs on a Chromebook | No install; deep links to shared projects; Claude as a tireless TA |

### 1.4 First principles (do not violate)

1. **KiCad file format is the contract.** All persistent state lives in `.kicad_sch` / `.kicad_pcb` / `.kicad_pro`. Round-trip fidelity is a CI gate.
2. **The KCIR is the in-memory contract.** Every internal transformation passes through the kiclaude Canonical Intermediate Representation (KCIR) — a Rust-defined, serde-serializable model with TypeScript and JSON Schema mirrors.
3. **Claude operates through typed tools, never free-form file edits.** Claude does not get raw `Write` access to `.kicad_pcb`. Every mutation Claude proposes is a structured MCP tool call.
4. **Claude reasons in declarative hints; raw coordinates are UI-only.** Mirroring ki-mcp-pcb's rule: tools that take literal `x_mm, y_mm` coordinates (e.g., `kc_footprint_place_xy`) are exposed ONLY to the React frontend (called from drag-and-drop, the property panel, snap actions). Claude-facing tools are declarative (`kc_footprint_place_hint` with constraints like "near MCU", "south edge", "within 2 mm of supply pins"); the placer resolves the hint to coordinates server-side. The MCP tool registry enforces this split via two disjoint tool sets (see §A.2).
5. **Every MPN must resolve.** Synthesis fails closed if a part can't be found in a real distributor's stock list. No hallucinated parts.
6. **MCP tools are stateless.** State lives in files (browser FS Access handle or `kiconnector` daemon path). Tools take paths and arguments in, return structured JSON out.
7. **No free-form prose from MCP tools.** Structured JSON only. Claude narrates on top.
8. **Local-first, cloud-optional.** A user must be able to run kiclaude entirely on their machine (browser + local `kiconnector` daemon) with no cloud account beyond the Anthropic API key.

---

## 2. Background & Competitive Context

**The landscape (May 2026)** — AI in EDA in 2026 sorts into four camps:

| Camp | Examples | Posture |
|---|---|---|
| AI-native cloud EDA | Flux.ai, Quilter, DeepPCB | AI is a foundational copilot; cloud-locked; closed file format; subscription |
| Cloud EDA with bolt-on AI | EasyEDA, Altium 365 | Basic chat, no requirements-driven planning; cloud-locked |
| Desktop EDA + plugins | KiCad 9, LibrePCB | No native AI; AI lives as third-party plugins; full control of files |
| Hardware-as-code | tscircuit | TypeScript/React DSL; AI-friendly because text-first; no visual editor parity |

**Where kiclaude fits:** *browser-native, open file format, AI-native, local-first.* Today, no product occupies this quadrant. Flux owns "AI-native cloud" but locks files; KiCad owns "open format" but has no AI; tscircuit owns "code-first" but is not a visual editor. kiclaude is the AI-native, browser-native KiCad — Claude Code's hardware counterpart.

**Why now:**
- KiCad 9's S-expression formats and the kicad-cli headless tool are stable enough to be a build target without bundling KiCad's GUI.
- The Claude Agent SDK ships in-process MCP tools, hooks, and session resume — the primitives needed for tool-heavy domain assistants.
- The File System Access API hits >70% of installed-browser share, enabling true local-file editing in the browser.
- kicanvas (MIT) and the Rust/WASM stack make a 60+ FPS canvas EDA viable in the browser without C++ porting.

---

## 3. Problem Statement

Designing a PCB today forces a hard choice between three painful options:

- **KiCad**: open, free, file-format-clean, but every action is manual. AI plugins are second-class. No collaboration. Steep onboarding.
- **Flux / cloud EDA**: AI helps but data is locked in a vendor cloud, files aren't portable, subscription required, no offline.
- **tscircuit**: AI-friendly because it's code, but no visual editor for review; ERC/DRC immature; team review of "PRs" of a PCB is hard without a visual diff.

A senior hardware engineer should be able to say *"add a USB-C PD trigger for 9 V to this board, with proper decoupling and a fuse"* in chat, see Claude propose a schematic edit, accept it visually, watch the placement update, route the new traces, run DRC, and export gerbers — all in a browser tab, all reviewable in a Git diff, all without sacrificing the open KiCad format the rest of the company already uses.

That product does not exist. kiclaude is that product.

---

## 4. Functional Requirements

Numbering convention: **FR-NNN**. "Must" is a v1 gate. "Should" is a v1 target with a documented fallback. "May" is post-v1.

### 4.1 Project & file management

| ID | Requirement |
|---|---|
| FR-001 | Must open a KiCad 9 `.kicad_pro` project from local disk via File System Access API (Chromium) or local `kiconnector` daemon (Firefox/Safari). |
| FR-002 | Must read and parse all files in a KiCad project: `.kicad_pro`, `.kicad_sch` (multi-sheet), `.kicad_pcb`, `.kicad_sym` libs, `.pretty` footprint dirs, `fp-lib-table`, `sym-lib-table`. |
| FR-003 | Must write back changes to disk byte-for-byte identical to KiCad 9's own writer for unchanged sections (deterministic emitter, golden-file CI). |
| FR-004 | Must support creating a new project from a template or from a blank state. |
| FR-005 | Should support Git-native autosave: changes write to disk on every commit-worthy action; user runs `git` outside kiclaude. |
| FR-006 | Should expose a project diff view: side-by-side schematic and PCB render at HEAD vs working tree. |
| FR-007 | May support optional cloud project sync (S3 + Postgres metadata) for teams; off by default. |

### 4.2 Schematic capture

| ID | Requirement |
|---|---|
| FR-010 | Must render multi-sheet schematics with KiCad-fidelity glyphs (wires, junctions, labels, hierarchical labels, no-connects, power flags). |
| FR-011 | Must support placing symbols from the user's symbol libraries (`sym-lib-table`) and from kiclaude's bundled library mirror. |
| FR-012 | Must support drawing wires, buses, junctions, labels (local, global, hierarchical), no-connect markers, and PWR_FLAG. |
| FR-013 | Must support multi-sheet hierarchy with sheet pins and propagation of hierarchical labels. |
| FR-014 | Must run ERC equivalent to `kicad-cli sch erc` and surface results inline in the editor. |
| FR-015 | Must support symbol property editing (value, footprint assignment, MPN, datasheet URL). |
| FR-016 | Should support symbol annotation (auto-assign refdes) with KiCad-compatible behavior. |
| FR-017 | Should support net classes and per-net constraints (mirrored in `.kicad_pcb` net classes). |

### 4.3 PCB layout

| ID | Requirement |
|---|---|
| FR-020 | Must render board files with multi-layer compositing, zones, tracks, pads, vias, silkscreen, courtyards, and 3D-anchor positions. |
| FR-021 | Must support placing, moving, rotating, and locking footprints with snap-to-grid. |
| FR-022 | Must support manual track routing with layer switching, via insertion, push-and-shove (initial: walk-around; FR-026 covers shove). |
| FR-023 | Must support copper zones (polygon fills) with thermal reliefs and clearance settings. |
| FR-024 | Must support board outline editing (rectangular, polygonal) and cutouts. |
| FR-025 | Must surface DRC results from `kicad-cli pcb drc` inline with click-to-locate. |
| FR-026 | Should support push-and-shove interactive routing equivalent to KiCad's PNS router (Rust port; M3+). |
| FR-027 | Should support invoking Freerouting as an external service for autoroute (DSN out → SES in). |
| FR-028 | Should support length-match tuning queue (read measured lengths; suggest meander adjustments). |
| FR-029 | May support hardware-accelerated 3D PCB preview with STEP model placement (via `kithree`). |

### 4.4 Manufacturing & export

| ID | Requirement |
|---|---|
| FR-030 | Must export Gerber + drill files via `kicad-cli pcb export gerbers`/`drill` through `kiconnector`. |
| FR-031 | Must export BOM in CSV + IPC-2581 with per-line distributor stock/price lookup. |
| FR-032 | Must export pick-and-place (`.pos`) via `kicad-cli pcb export pos`. |
| FR-033 | Must export 3D STEP via `kicad-cli pcb export step`. |
| FR-034 | Must produce a fab-target package (JLCPCB, OSHPark, PCBWay, generic) with correct filenames, layer pairings, drill format. |
| FR-035 | Should support panelization via KiKit (CLI wrapper through `kiconnector`). |
| FR-036 | Should run DFM dry-run against fab rule sets and surface errors before export. |

### 4.5 Parts & library management

| ID | Requirement |
|---|---|
| FR-040 | Must index the user's local symbol/footprint libraries and the bundled mirror at session start. |
| FR-041 | Must look up MPN → symbol + footprint candidates with confidence score. |
| FR-042 | Must integrate live distributor APIs (Octopart, Mouser, Digi-Key, JLCPCB parts library) for stock/price; degrades to cached data offline. |
| FR-043 | Should support importing symbols/footprints from a `.kicad_sym`/`.kicad_mod` file dropped onto the editor. |
| FR-044 | May auto-pull SnapEDA / Ultra Librarian parts on demand with user confirmation. |

### 4.6 Claude-driven editing

| ID | Requirement |
|---|---|
| FR-050 | Must expose a persistent chat sidebar on every editor view (schematic, PCB, BOM, 3D). |
| FR-051 | Must run Claude through the Claude Agent SDK (Python) in the `services/agent` backend, streaming to the frontend over WebSocket. |
| FR-052 | Must register an in-process MCP server (`services/mcp`) that exposes every domain action as a typed tool (see §A.2 for the catalog). |
| FR-053 | Must enforce permission gates via Agent SDK hooks: read-only tools auto-approved; mutating tools surface an in-UI approve/deny prompt unless the project is in "trusted" mode. |
| FR-054 | Must ship a `.claude/` directory in every kiclaude project with skills and slash commands that ALSO work from Claude Code on the CLI in the same project directory. |
| FR-055 | Must persist chat sessions per project (resume via `ClaudeAgentOptions.resume`). |
| FR-056 | Must surface every Claude tool call as a journal entry in the editor's activity panel with: tool name, input JSON, output JSON, timestamp, and revert button. |
| FR-057 | Should support background agent runs (e.g., "overnight: try 5 placement variants and rank by DRC violation count + total trace length"). |
| FR-058 | Should support subagent delegation (e.g., a "decoupling-auditor" subagent that runs while the main agent works on routing). |
| FR-059 | Should expose `AskUserQuestion` from the Agent SDK as in-UI multiple-choice cards when Claude needs disambiguation. |
| FR-060 | May expose Anthropic API direct mode (no Agent SDK) for users who want one-shot prompts with no tool use. |

### 4.7 CLI (headless)

| ID | Requirement |
|---|---|
| FR-070 | Must ship `packages/cli` as `kiclaude` — a Node.js CLI for headless build/validate/export/diff. |
| FR-071 | Must mirror every MCP tool's input/output schema so anything Claude can do, the CLI can do (and vice versa). |
| FR-072 | Must support `kiclaude build <project> --out dist/` running the full pipeline in CI. |
| FR-073 | Should support `kiclaude diff <pcb-a> <pcb-b>` producing visual + textual board diffs in PR-friendly formats. |

### 4.8 Collaboration & sharing

| ID | Requirement |
|---|---|
| FR-080 | Should support "share a read-only link" backed by a content-addressed snapshot in S3. |
| FR-081 | May support real-time multiplayer editing (CRDT-backed). v1 is single-editor with Git as the merge tool. |

---

## 5. Non-Functional Requirements

| ID | Requirement | Measurement |
|---|---|---|
| NFR-001 | Initial page load to first interactive editor ≤ 3 s on mid-range laptop, 50 Mbps. | Lighthouse + Playwright measurement |
| NFR-002 | Schematic render must hold ≥ 60 FPS for boards up to 200 components / 8 sheets; ≥ 30 FPS up to 1000 components. | Frame-timing test in CI |
| NFR-003 | PCB render must hold ≥ 60 FPS for boards up to 1000 footprints / 5000 tracks on a discrete GPU; ≥ 30 FPS on integrated GPU. | Frame-timing test in CI |
| NFR-004 | Round-trip parse → emit of any KiCad 9 reference project produces byte-identical output for unchanged sections. | Golden-file CI |
| NFR-005 | DRC on a 1000-footprint board completes in ≤ 5 s (delegated to kicad-cli, but the UI must surface progress within 100 ms of click). | Benchmark suite |
| NFR-006 | First tool call from Claude to first visible result in the UI ≤ 800 ms p95 on local dev; ≤ 1500 ms p95 on production. | OTel span SLO |
| NFR-007 | All Claude tool calls must be auditable: every call writes a journal entry with tool name, input, output, timestamp, model version. | Hook contract; e2e test |
| NFR-008 | The app must function offline (cached service worker shell) for projects already loaded; live distributor lookups gracefully degrade. | Playwright offline test |
| NFR-009 | Source-available under a permissive license (MIT or Apache-2.0). Freerouting integration must remain a separately-installed service to avoid GPL contamination. | License audit |
| NFR-010 | All data leaving the user's machine to a third party must be opt-in (Anthropic API: required for chat, declared at first run; distributor APIs: opt-in toggle). | Privacy review |
| NFR-011 | a11y: keyboard-navigable for every editor action; ARIA labels on all canvas-overlay controls. | axe-core CI |
| NFR-012 | Browser support: latest two stable versions of Chrome, Edge, Safari, Firefox. Full File System Access in Chromium; OPFS-backed fallback elsewhere. | Cross-browser CI matrix |

---

## 6. Architecture

### 6.1 System diagram (high level)

```
┌──────────────────────────────────────────────────────────────────────────┐
│                              Browser (client/)                            │
│  ┌──────────────────────────────────────────────────────────────────┐    │
│  │  React 19 SPA (Vite, Radix UI, Tailwind 4)                        │    │
│  │  ┌──────────┬──────────┬──────────┬─────────┐  ┌─────────────┐   │    │
│  │  │ Schematic│   PCB    │   BOM    │   3D    │  │ Claude chat │   │    │
│  │  │ editor   │  editor  │   view   │ (kithree)│ │ sidebar      │   │    │
│  │  └────┬─────┴────┬─────┴────┬─────┴────┬────┘  └──────┬──────┘   │    │
│  │       │          │          │          │              │           │    │
│  │       └──────────┴─── KCIR client store (Zustand) ────┘           │    │
│  │                            │                                       │    │
│  │   ┌────────────────────────┴───────────────────────────────────┐  │    │
│  │   │  Rust → WASM core (crates/ki, crates/cad)                   │  │    │
│  │   │   • KiCad S-expr parse/emit • Geometry (polygons, traces)    │  │    │
│  │   │   • Hit-test, snap, DRC kernel  • KCIR serde model           │  │    │
│  │   └────────────────────────┬─────────────────────────────────────┘  │    │
│  │                            │                                          │   │
│  │  ┌─────── kicanvas embedding ──────┐  ┌── File System Access API ──┐ │   │
│  │  │ WebGL/Canvas2D viewports         │  │ FileSystemDirectoryHandle  │ │   │
│  │  │ (read-only base + edit overlay)  │  │ (Chromium) / OPFS (other)  │ │   │
│  │  └──────────────────────────────────┘  └────────────────────────────┘ │   │
│  └────────────────────────────┬──────────────────────────────────────────┘   │
└─────────────────────────────────┼─────────────────────────────────────────────┘
                                  │ WebSocket (chat stream + tool events)
                                  │ HTTPS REST (project, build, export, sync)
┌─────────────────────────────────┴─────────────────────────────────────────────┐
│                          Backend (services/)                                   │
│  ┌──────────────┐  ┌────────────┐  ┌────────────────┐  ┌──────────────────┐  │
│  │ services/    │  │ services/  │  │ services/      │  │ services/        │  │
│  │ server       │──│ agent      │──│ mcp            │  │ kiserver         │  │
│  │ (TS gateway) │  │ (Py Agent  │  │ (Py FastMCP    │  │ (Py FastAPI:     │  │
│  │              │  │ SDK)       │  │ in-process)    │  │ projects, sync,  │  │
│  │              │  │            │  │                │  │ snapshots, BOM)  │  │
│  └──────┬───────┘  └─────┬──────┘  └────────┬───────┘  └─────────┬────────┘  │
│         │                │                  │                    │            │
│         └──────────────────────────┬────────┴────────────────────┘            │
│                                    │                                          │
│              ┌─────────────────────┴───────────────────────┐                  │
│              │ services/kiconnector (Py: subprocess pool)   │                  │
│              │  • kicad-cli (DRC, ERC, gerbers, BOM, STEP)  │                  │
│              │  • freerouting (jar, external service mode)  │                  │
│              │  • KiKit (panelization)                       │                  │
│              │  • optional: kicad-python IPC for live KiCad  │                  │
│              └───────────────────────────────────────────────┘                 │
└────────────────────────────────────────────────────────────────────────────────┘
```

### 6.2 Frontend (`client/`)

**Stack** (locked, per existing scaffold):
- React 19 with React Compiler (`babel-plugin-react-compiler`)
- Vite 8 dev server / Rolldown-based builds
- Radix UI primitives + Tailwind CSS v4
- TypeScript ~6.0
- Bundler integrates the Rust→WASM core via `vite-plugin-wasm` (to be added)

**Module layout** (`client/src/`):

```
src/
├── main.tsx                    # entry
├── App.tsx                     # router + workspace shell
├── lib/
│   ├── wasm.ts                 # Rust core bootstrap (crates/ki, crates/cad)
│   ├── kicanvas-bridge.ts      # wraps kicanvas custom elements
│   ├── fs.ts                   # File System Access API + OPFS fallback
│   └── ws.ts                   # WebSocket client to services/server
├── stores/
│   ├── projectStore.ts         # Zustand: current project + dirty state
│   ├── kcirStore.ts            # KCIR-shaped in-memory model (mirrors Rust)
│   ├── selectionStore.ts       # selection + tool mode
│   ├── chatStore.ts            # Claude chat session, streaming buffer
│   └── activityStore.ts        # tool-call journal (FR-056)
├── api/                        # typed clients: server, agent, kiserver
├── components/
│   ├── workspace/              # split panes, layer panel, tool dock
│   ├── schematic/              # schematic canvas + overlay editing UI
│   ├── pcb/                    # PCB canvas + layer pickers + routing tools
│   ├── bom/                    # BOM table + sourcing panel
│   ├── three/                  # 3D viewport using @kiclaude/kithree
│   ├── chat/                   # ChatSidebar, ToolCallCard, AskUserQuestionCard
│   ├── activity/               # ActivityJournal, ToolCallRow
│   └── shared/                 # Radix + Tailwind components
├── pages/                      # /, /project/:id, /share/:token, /settings
└── assets/                     # bundled icons, default project templates
```

**Rendering strategy** — kicanvas is embedded as a baseline WebGL viewport for both schematic and PCB. kiclaude does NOT fork kicanvas; we depend on it via npm and layer an interactive edit overlay on top (selection rectangles, drag handles, snap previews, DRC violation markers). The Rust→WASM core owns hit-testing, geometry, and the editable scene graph; kicanvas owns the read-only render pass.

**State model** — single source of truth is the KCIR-shaped store in `kcirStore.ts`. UI components read from the store; mutations go through typed actions that:
1. Update the in-memory KCIR.
2. Send a typed event to the Rust core for geometry update.
3. Enqueue a write to `services/server` (debounced) for persistence.
4. Optionally surface as a tool call in the activity journal if the change originated from Claude.

### 6.3 Browser-side core (Rust → WASM)

**Crates** (in `crates/`):

- **`crates/ki`** — KiCad domain.
  - S-expression lexer + parser + emitter (hand-rolled, deterministic).
  - `.kicad_pro`, `.kicad_sch`, `.kicad_pcb`, `.kicad_sym`, `.kicad_mod` types.
  - KCIR ↔ KiCad file mappers (the canonical "round-trip" code).
  - Symbol/footprint library indexing.
  - Built once; consumed by browser (via `wasm-pack`) AND by `services/kiserver` (via PyO3).

- **`crates/cad`** — CAD primitives.
  - 2-D geometry: polygons, polylines, arcs, vias.
  - Layer/net data structures.
  - Spatial index (R-tree) for hit-test and broad-phase DRC.
  - DRC kernel: clearance checks, courtyard collisions, annular ring, drill-to-copper.
  - Pure Rust, no `std::fs`; usable from browser and server.

Both crates publish:
- A WASM bundle with TypeScript bindings (`wasm-bindgen` + `tsify`) for the browser.
- A native Python wheel (`maturin`) for `services/kiserver`.

### 6.4 Backend services (`services/`)

#### 6.4.1 `services/server` (TypeScript)

Public-facing HTTP + WebSocket gateway. Responsibilities:
- Static file serving (built React bundle).
- Auth (API key for v1; OAuth/JWT post-v1).
- WebSocket multiplexing for chat streams and tool-call events.
- Reverse proxy to `services/agent`, `services/kiserver`, `services/kiconnector`.
- Session management (cookie + per-project session id).

Stack: Node.js 22, Hono or Express, ws library, Zod for input validation.

#### 6.4.2 `services/agent` (Python)

Claude Agent SDK orchestrator. Responsibilities:
- One `ClaudeSDKClient` instance per active project session.
- Loads the project's `.claude/` directory as `setting_sources` so skills and slash commands resolve.
- Registers the in-process MCP server from `services/mcp` (via `create_sdk_mcp_server`).
- Installs hooks for: `PreToolUse` (permission gate), `PostToolUse` (audit log), `SessionStart` / `SessionEnd` (telemetry), `UserPromptSubmit` (preflight).
- Streams `Message`s back to `services/server` over an internal channel (stdout JSONL or a Unix socket).
- Implements `AskUserQuestion` round-tripping to the UI as multiple-choice cards.

Stack: Python 3.11+, FastAPI for the local control plane, `claude-agent-sdk` for the loop, `anyio` for async, `uv` for env management.

API key handling: `ANTHROPIC_API_KEY` is read from server-side env or per-user config file; **the key never leaves the backend**.

Bedrock / Vertex / Azure / Claude-Platform-on-AWS routes are supported via the Agent SDK's env-var passthrough (`CLAUDE_CODE_USE_BEDROCK=1`, etc.) and exposed in `kiclaude` settings.

#### 6.4.3 `services/mcp` (Python)

In-process MCP server hosting the kiclaude tool surface. Each tool is a `@tool`-decorated Python function with a Pydantic input model, called directly by Claude through `services/agent`.

Tool implementation pattern:
1. Receive structured input.
2. Resolve the target project via session context (project root path).
3. Call into `services/kiserver` (for KCIR-level ops) or `services/kiconnector` (for KiCad-cli/external-tool ops).
4. Return structured JSON.

The MCP server runs in the same Python process as `services/agent`; no subprocess overhead. (See Agent SDK in-process MCP pattern.)

#### 6.4.4 `services/kiserver` (Python)

Heavy-compute backend. Responsibilities:
- Project storage layout (file tree mirror or canonical store).
- KCIR <-> KiCad file conversion using the Rust `crates/ki` via PyO3.
- Synthesis pipeline (KCIR → .kicad_sch + .kicad_pcb), wrapping the Rust engine.
- BOM compilation + distributor API fan-out (Octopart, Mouser, Digi-Key, JLC).
- Snapshot / versioning service (content-addressed object store; backend pluggable: local FS, S3).
- HTTP API for the frontend; not user-facing (proxied through `server`).

Stack: Python 3.11+, FastAPI, Pydantic v2, `httpx` for distributors, `kiutils` only as a reference (the Rust core is the primary parser).

#### 6.4.5 `services/kiconnector` (Python)

Local-tool subprocess broker. Responsibilities:
- Spawning `kicad-cli` for DRC, ERC, gerber/drill/BOM/pos/STEP export.
- Running `freerouting` (as a separate process or a remote service; never linked) and round-tripping DSN ↔ SES.
- Running `KiKit` for panelization.
- Optional `kicad-python` IPC bridge to a running KiCad (for users who want hybrid workflow, M5+).
- Filesystem watcher: when the project directory changes outside kiclaude, push reload events to the UI.

Stack: Python 3.11+, FastAPI, `anyio` subprocess primitives, `watchfiles`.

**Deployment shape**: `kiconnector` is the ONLY service that must run on the user's local machine when the SaaS frontend is hosted remotely — it provides the bridge to the user's local `kicad-cli` install and their filesystem. The hosted SaaS exposes a "Connect local toolchain" QR-code/token flow.

### 6.5 CLI package (`packages/cli`)

Node-based `kiclaude` binary. Implementation:
- Uses the TypeScript Claude Agent SDK (`@anthropic-ai/claude-agent-sdk`) so the CLI itself is Claude-augmentable when run with `--claude`.
- Calls the same backend services as the web UI when they are available; falls back to spawning a local `kicad-cli` + Rust core directly for fully offline operation.
- Mirrors every MCP tool 1:1 as a subcommand (`kiclaude pcb drc <path>`, `kiclaude bom price <path>`, `kiclaude build <project>`).

### 6.6 3D viewer package (`packages/kithree`)

three.js-based PCB 3-D viewer. Implementation:
- Reads KCIR + STEP component models (from `kicad-packages3D` mirror).
- Renders board substrate, copper layers, silkscreen, components in 3D.
- Used by the React frontend's `/three/` view; published as an npm package so it can also be embedded in other apps (datasheet sites, marketplace listings).

### 6.7 The `.claude/` directory (per project)

Every kiclaude project carries a `.claude/` directory. The SAME assets work in:
- The kiclaude in-app chat (`services/agent` reads them via `setting_sources`).
- Claude Code on the CLI when the user runs `claude` in the project root.

Structure:

```
.claude/
├── settings.json               # MCP servers, model, allowed_tools
├── CLAUDE.md                   # project-specific guidance (board purpose, constraints)
├── commands/                   # slash commands (see §A.3)
│   ├── add-decoupling.md
│   ├── route-power.md
│   ├── add-mcu.md
│   ├── pcb-review.md
│   ├── pcb-fab.md
│   └── ... (~20 in v1)
└── skills/
    ├── kicad-schematic/SKILL.md
    ├── kicad-pcb/SKILL.md
    ├── manufacturing/SKILL.md
    └── design-review/SKILL.md
```

`settings.json` auto-registers kiclaude's local MCP server when run via Claude Code CLI:

```json
{
  "mcpServers": {
    "kiclaude": {
      "command": "kiclaude",
      "args": ["mcp", "stdio"]
    }
  }
}
```

When run from the in-app chat, `services/agent` registers the same tools directly in-process — no subprocess.

---

## 7. Data Model: KCIR

### 7.1 Design goals

- **Lossless round-trip** with KiCad 9 files. Anything in the file format we can't represent is preserved as an opaque blob attached to the closest KCIR node.
- **Serde-friendly** in Rust; auto-derived TypeScript types via `ts-rs`; JSON Schema generated for MCP tool inputs.
- **Versioned.** Every KCIR document carries a `kcir_version` (e.g. `"0.1"`); migrations are explicit.
- **Compositional.** A board's schematic, PCB, and BOM are three views into one KCIR `Project`.

### 7.2 Top-level types (sketch)

```rust
// crates/ki/src/kcir/mod.rs
pub struct Project {
    pub kcir_version: SemVer,
    pub name: String,
    pub schematic: Schematic,    // multi-sheet
    pub pcb: Pcb,
    pub libraries: LibraryTable, // symbol + footprint + 3D
    pub stackup: Stackup,
    pub design_rules: DesignRules,
    pub net_classes: Vec<NetClass>,
    pub fab_target: Option<FabTarget>,
    pub bom_policy: BomPolicy,
    pub metadata: ProjectMetadata,
}

pub struct Schematic {
    pub sheets: Vec<Sheet>,
    pub symbols: Vec<SymbolInstance>,
    pub wires: Vec<Wire>,
    pub junctions: Vec<Junction>,
    pub labels: Vec<Label>,        // local, hierarchical, global, power
    pub no_connects: Vec<NoConnect>,
    pub buses: Vec<Bus>,
}

pub struct Pcb {
    pub layers: Vec<Layer>,
    pub footprints: Vec<FootprintInstance>,
    pub tracks: Vec<Track>,
    pub vias: Vec<Via>,
    pub zones: Vec<Zone>,
    pub outline: Outline,
    pub drawings: Vec<Drawing>,    // silkscreen, fabrication, courtyard
    pub nets: Vec<Net>,
}

pub struct Net {
    pub name: String,
    pub class: NetClassRef,
    pub members: Vec<PadRef>,
    pub diff_pair: Option<NetRef>,
    pub power_rail: Option<String>,
    pub topology: Option<Topology>,     // fly_by, daisy, star
    pub length_match_group: Option<String>,
    pub target_impedance_ohm: Option<f64>,
    pub reference_plane: Option<LayerRef>,
}
```

(Full types live in `crates/ki/src/kcir/`; this is the contract sketch.)

### 7.3 Validators

A baseline matching ki-mcp-pcb's CIR codes, extended for visual-editor concerns:

| Code | Check | Severity |
|---|---|---|
| KC001 | Unique refdes per project | error |
| KC002 | Every net member resolves to a real pad | error |
| KC003 | Ground net present | error |
| KC004 | Stackup matches fab layer count | error |
| KC010 | All assigned footprints exist in `fp-lib-table` | error |
| KC011 | All assigned symbols exist in `sym-lib-table` | error |
| KC020 | Every IC has at least one bypass cap to ground | error (geometric proximity is a warning at v1) |
| KC021 | Power rails declared in net classes have at least one source (regulator / PWR_FLAG) | error |
| KC030 | Length-match groups have ≥2 members | error |
| KC031 | Diff pairs declared bidirectionally and share a length-match group | error |
| KC040 | Controlled-impedance targets achievable on declared stackup (Hammerstad solver) | warning at 10%, error at 20% |
| KC050 | Partition isolation (analog/digital/RF) not violated except through declared bridges | error |
| KC060 | DDR fly-by topology has ≥3 nodes and signed off | warning until `signoff.ddr_reviewed` |
| KC070 | BGA fanout feasible on fab DFM | warning/error |
| KC080 | DRC clean (delegated to `kicad-cli pcb drc`) | error |
| KC081 | ERC clean (delegated to `kicad-cli sch erc`) | error |

### 7.4 Persistence

The canonical persistent form is the **KiCad project on disk** (`.kicad_pro` + siblings). KCIR is the in-memory model; we never write KCIR to disk as the source of truth. Snapshots for time-travel UI are content-addressed gzip-pickles of KCIR keyed by SHA-256, but a `kicad-cli` user reading the same directory sees only the KiCad files.

---

## 8. Claude Integration Architecture

### 8.1 Three integration layers

| Layer | Where | What it does |
|---|---|---|
| **Layer A — In-app Claude (Agent SDK)** | `services/agent` + chat sidebar | Persistent chat per project, full tool access, streaming UI, hooks for permission/audit |
| **Layer B — External Claude Code CLI** | User runs `claude` in the project dir | Same skills + slash commands + MCP server work via `.claude/` filesystem config |
| **Layer C — Anthropic API direct** | Optional, opt-in | Single-shot prompts (e.g., "explain this net's intent") that don't need the agent loop |

All three layers see the SAME `.claude/` configuration and the SAME MCP tool surface — the only difference is the loop driver (Agent SDK vs Claude Code CLI vs raw API).

### 8.2 Backend flow (Layer A, primary)

```
[React chat input]
     │ WebSocket
     ▼
[services/server]      ←── auth, session multiplexing
     │ stdin / unix socket
     ▼
[services/agent: ClaudeSDKClient]
     │   • options.mcp_servers = { kiclaude: services/mcp (in-process) }
     │   • options.hooks = { PreToolUse, PostToolUse, SessionStart, ... }
     │   • options.setting_sources = [.claude/]
     │   • options.resume = <session_id>
     │
     │ async iterator of Messages
     ▼
[Tool calls intercepted by hooks → routed to services/mcp tools]
     │
     │ each tool calls services/kiserver or services/kiconnector
     │
     ▼
[Streamed: Assistant text + tool_use + tool_result] → WebSocket → React
     │
     ▼
[activityStore captures every tool_use/tool_result for the journal]
```

### 8.3 Hooks contract

| Hook | Purpose |
|---|---|
| `SessionStart` | Load KCIR snapshot, push initial system prompt, register session id with `services/server` |
| `UserPromptSubmit` | Sanitize user input; attach project context (current view, selection) |
| `PreToolUse` | Permission gate. Read-only tools auto-allow. Mutating tools either auto-allow (trusted mode) or surface an in-UI approval card via `services/server` and block until resolved. Tool-specific matchers in `HookMatcher` allow fine-grained policies (e.g. `kc_place_*` always-prompt, `kc_get_*` always-allow). |
| `PostToolUse` | Audit log entry → `activityStore` + OpenTelemetry span. |
| `Stop` | Persist transcript; record cost + tokens to telemetry. |
| `SessionEnd` | Flush snapshots; release Anthropic session id for resume. |

### 8.4 Sessions

`ClaudeSDKClient` per active project session. Session id captured from the `SystemMessage` of subtype `init` and stored in the project's `.kiclaude/sessions/` directory. Resume on reload via `options.resume`. Fork via `options.fork` for exploring "what if" branches; the UI surfaces the active branch in a session-tree picker.

### 8.5 Slash commands (selection — full list in §A.3)

- `/add-mcu` — ask Claude to add an MCU subsystem (USB, power, decoupling) given a series/family.
- `/add-decoupling` — Claude scans for power rails missing bypass caps and proposes additions.
- `/route-power` — Claude routes power nets first, declaratively (widths from net class).
- `/length-match` — set up length-match groups for declared diff pairs/buses.
- `/erc-fix` — Claude reads ERC output and proposes fixes one at a time.
- `/drc-fix` — same for DRC.
- `/pcb-review` — produces a structured design review (decoupling, partitions, return paths, BOM risk).
- `/pcb-fab` — packages for the configured fab target with a pre-flight DFM check.

### 8.6 Skills (selection — full list in §A.4)

Each skill lives in `.claude/skills/<name>/SKILL.md` and teaches Claude when and how to use the kiclaude MCP tools for that domain (schematic, PCB, manufacturing, design-review).

### 8.7 Subagents (FR-058)

The Agent SDK's `agents:` option declares specialist subagents. v1 ships:

- `decoupling-auditor` — read-only; scans for missing bypass caps and weak rail decoupling. Outputs JSON.
- `bom-sourcer` — read-only; resolves MPNs to live distributor stock; flags risk parts.
- `placement-explorer` — runs N placement variants in parallel and ranks by DRC + length. Background.

Subagents are invoked via the `Agent` tool which is pre-approved.

### 8.8 Observability

OpenTelemetry spans emitted from every hook. Mirrors the patterns in the `claude-code-monitoring-guide` reference. Defaults: stdout JSONL; optional Prometheus exporter; optional Grafana dashboard preset shipped in `docs/observability/`.

Metrics tracked: tokens in/out per session, cache hit rate, tool-call distribution, average cost per design action, p95 tool latency.

---

## 9. KiCad Compatibility & File-Format I/O

### 9.1 Round-trip contract

- Parse a KiCad 9 file → emit it back unchanged → byte-identical for unchanged sections (allowing the timestamp + tstamps fields to update on mutation only). This is a golden-file CI gate.
- Open any of the 50 reference projects in `kicad-library-examples/` and editing nothing, then save, must not change the file.
- Editing one footprint's position must change only that footprint's S-expression node; siblings unchanged.

### 9.2 What kiclaude does NOT use KiCad-the-app for

- Rendering: kiclaude renders natively (kicanvas + Rust+WASM).
- File I/O: native Rust parser/emitter.
- DRC: kiclaude implements its own DRC kernel in `crates/cad`; `kicad-cli pcb drc` runs in parallel as the source of truth and as cross-check.
- Symbol/footprint library handling: native Rust.

### 9.3 What kiclaude DOES use kicad-cli for (via `services/kiconnector`)

- ERC: `kicad-cli sch erc` — accepted as authoritative.
- DRC: `kicad-cli pcb drc` — accepted as authoritative; the Rust DRC kernel exists for live-feedback overlays only.
- Gerber export: `kicad-cli pcb export gerbers`.
- Drill export: `kicad-cli pcb export drill`.
- Position-file (PnP) export: `kicad-cli pcb export pos`.
- BOM export: `kicad-cli sch export bom`.
- 3D STEP export: `kicad-cli pcb export step`.
- Plot PDFs for review.

Rationale: these operations are deterministic, well-tested in `kicad-cli`, and reimplementing them in Rust is a five-year effort that adds no value.

### 9.4 What kiclaude does NOT use the KiCad IPC API for in v1

Per official docs (May 2026), the KiCad 9 IPC API:
- Covers the PCB editor only (no schematic, no library editor).
- Requires a running KiCad GUI (no headless).
- Cannot plot or export files.
- Has no plans for a standalone library mode.

These constraints make IPC unsuitable as kiclaude's foundation. We treat it as an **optional bridge** (M5+, via `services/kiconnector`) for the niche workflow of "edit in kiclaude, finalize in KiCad."

### 9.5 Symbol/footprint library strategy

- **Bundled mirror:** kiclaude ships a versioned mirror of the official `kicad-symbols`, `kicad-footprints`, and `kicad-packages3D` repos (CC0/CC-BY-SA). Update cadence: monthly; pinned at install time. Distribution: served from `services/kiserver`; lazy-loaded in the browser via IndexedDB.
- **User libraries:** read from `sym-lib-table` / `fp-lib-table` in the user's project.
- **External providers:** SnapEDA + Ultra Librarian (M3+, opt-in, user confirmation per fetch).

### 9.6 Freerouting (license isolation)

Freerouting is GPLv3. To avoid GPL contamination of kiclaude (which is MIT/Apache-2.0):
- Freerouting is **never linked**.
- It runs as a separately-installed process invoked over its CLI (`-de in.dsn -do out.ses`) or its REST API (beta).
- `services/kiconnector` exec's it; no Freerouting code, headers, or jars ship inside kiclaude.
- The docs surface this and provide an install link.

---

## 10. Tooling Integrations

| Tool | Role | License | Integration shape |
|---|---|---|---|
| **kicad-cli** | ERC, DRC, gerber/drill/PnP/STEP/BOM export | GPLv3 (binary; we never link it) | `services/kiconnector` subprocess; user installs KiCad 9 |
| **kicanvas** | Browser-side WebGL viewport | MIT | `client/` npm dep; layered with our overlay |
| **freerouting** | Autoroute | GPLv3 | `services/kiconnector` subprocess; user installs Freerouting |
| **KiKit** | Panelization | MIT | `services/kiconnector` subprocess; Python install |
| **kicad-python (kipy)** | IPC to a running KiCad (optional, M5+) | GPLv3 (process boundary) | `services/kiconnector`; user opt-in |
| **PcbDraw** | Static SVG board renders for shareable links | MIT | `services/kiserver` subprocess |
| **Anthropic Agent SDK** | Claude orchestration | Anthropic Commercial Terms | `services/agent` (Python) + `packages/cli` (TypeScript) |
| **MCP / FastMCP** | Tool protocol | MIT | `services/mcp` |
| **Three.js** | 3D viewer | MIT | `packages/kithree` |
| **Octopart / Mouser / Digi-Key / JLCPCB APIs** | Live BOM data | Their TOS | `services/kiserver` httpx clients; user-supplied keys |

---

## 11. Implementation Milestones

**Total roadmap: ~54 weeks (≈ 13 months).** This is a full KiCad-alternative app; v1 is not small. The phasing is:

| Phase | Milestones | Duration | What ships |
|---|---|---|---|
| **v1.0** (public beta) | M0 + M1 + M2 | ~22 weeks (≈ 5.5 months) | Web app that opens any KiCad project, edits the schematic, edits 2-layer PCBs, exports manufacturing files, with the full Claude chat sidebar. |
| **v1.5** (general availability) | M3 + M4 | +20 weeks (~10 months cumulative) | 4-layer, controlled impedance, diff pairs, decoupling auditor, distributor APIs, panelization, share links. |
| **v2.0** | M5 | +12 weeks (≈ 13 months cumulative) | RF/DDR/BGA co-pilot scaffolding, optional KiCad IPC bridge, optional multiplayer. |

If a shorter v1 is needed (e.g., 3 months), the cut line is "M0 + M1 only" — a schematic-editing browser app with Claude, no PCB layout. The PCB editor is what makes kiclaude a KiCad alternative; cutting M2 from v1 is not recommended.

Each milestone is concrete: a single representative board, a list of must-pass tests, and a demo script. Milestones close only when every gate (§13) is green.

### M0 — Plumbing (target: 4 weeks)

Goal: every layer of the architecture is wired but nearly empty.

- `crates/ki` parses a hello-world `.kicad_pro` project (1 schematic, 1 board, 1 component).
- `crates/cad` exposes a polygon + R-tree.
- WASM builds; `client/` boots and renders the parsed project in kicanvas.
- `services/server` boots and proxies WebSocket to `services/agent`.
- `services/agent` runs the Agent SDK with one no-op MCP tool (`kc_ping`) and streams to the browser.
- `services/kiconnector` runs `kicad-cli --version` and surfaces it.
- `.claude/settings.json` registers `kiclaude` MCP server; running `claude` in the project succeeds.
- CI: matrix (linux/macos), wasm-pack build, Python uv sync, end-to-end smoke via Playwright.

Demo: open kiclaude in a browser, see "blinky" project rendered, chat "what's the project name?" — Claude reads via a tool, replies.

### M1 — Schematic editor parity (target: 8 weeks)

Goal: kiclaude can open, edit, and save any of 10 reference schematics with round-trip fidelity.

- Schematic render (wires, junctions, labels, no-connects, PWR_FLAG, multi-sheet hierarchy).
- Place symbol from library; edit value/footprint/MPN.
- Draw wire, junction, label.
- Annotate refdes.
- ERC via `kicad-cli sch erc` surfaced in UI.
- Save → byte-identical to the original for unedited nodes.
- 10 reference projects pass round-trip CI.
- Slash command `/add-mcu` works for STM32, ESP32, RP2040.

Demo: open `examples/esp32_s3_blinky/`, add an LED + resistor in chat, save, see git diff.

### M2 — PCB editor parity, 2-layer (target: 10 weeks)

Goal: kiclaude can place, route, and DRC-check 2-layer boards.

- PCB render (multi-layer, footprints, tracks, vias, zones, silkscreen, courtyards).
- Place / move / rotate footprints with snap.
- Manual track routing (walk-around; no shove yet).
- Polygon zone fills.
- DRC via `kicad-cli` + live overlay.
- Net class editing.
- Slash command `/route-power` works for 2-layer boards.
- Gerber + drill + BOM + PnP export.

Demo: open `examples/blinky`, place footprints chat-driven ("MCU center, USB south, LED north"), route manually, export gerbers, send to JLCPCB.

### M3 — 4-layer, mixed-signal, high-speed-lite (target: 12 weeks)

Goal: 4-layer stackup, controlled-impedance hints, length-match groups, diff pairs, decoupling auditor.

- Stackup editor.
- Controlled-impedance solver (Hammerstad/IPC-2141) surfaced in the net inspector.
- Diff pair declaration + length-match groups.
- Decoupling auditor subagent.
- Push-and-shove routing (Rust port of PNS; initial: BasicWalkaround → PnS variant).
- 3D STEP export + `kithree` 3D viewer.
- Distributor APIs live (Octopart + Mouser + JLC).

Demo: `examples/usb_eth_phy.yaml` — declare diff pairs, hit 90/100 Ω with solver-tuned widths, DRC clean.

### M4 — Manufacturing polish + cloud sync (target: 8 weeks)

Goal: real teams can use kiclaude in production for routine boards.

- KiKit panelization integration.
- Fab-target presets (JLC, OSHPark, PCBWay) with DFM dry-run.
- Optional cloud sync (S3 + Postgres) — opt-in.
- Read-only share link (content-addressed snapshots).
- Activity journal with revert per tool call.
- Settings: model selection (Opus/Sonnet/Haiku), API providers (Bedrock/Vertex/Azure/Anthropic direct).

Demo: ship the kiclaude team's own dev board with kiclaude end-to-end. Internal dogfood becomes the gate.

### M5 — Co-pilot for RF/DDR/BGA + KiCad IPC bridge (target: 12 weeks, co-pilot only)

Goal: scaffolding + validators for advanced workflows; explicit human EE sign-off gates.

- Wadell/Wen CPWG impedance solver.
- DDR fly-by topology validator (KC060).
- BGA fanout template registry + KC070.
- `Board.signoff.{rf,ddr,bga_fanout}_reviewed` flags; LLM cannot flip them.
- Optional `kicad-python` IPC bridge in `services/kiconnector` (for "finish in KiCad" hand-off).
- Optional CRDT multiplayer (off by default).

Demo: `examples/esp32_c6_rf` — RF antenna feed, DDR3L fly-by sketch, BGA fanout — all flagged "needs human review" until signed off in UI.

---

## 12. Repository Layout (target end-state)

```
/Users/ryanoboyle/kiclaude/
├── README.md
├── SPEC.md → docs/specs/SPEC-01-kiclaude.md
├── docs/
│   ├── specs/           # this document and successors
│   ├── architecture/    # ADRs, diagrams
│   └── observability/   # dashboards, OTel collector configs
├── client/              # React 19 SPA (existing scaffold)
│   ├── src/
│   ├── public/
│   └── package.json
├── crates/              # Rust workspace
│   ├── ki/              # KiCad parsers + KCIR
│   ├── cad/             # Geometry + DRC kernel
│   └── Cargo.toml
├── services/
│   ├── server/          # TS HTTP/WS gateway (Hono or Express)
│   ├── agent/           # Python Agent SDK orchestrator (FastAPI)
│   ├── mcp/             # Python FastMCP in-process tools
│   ├── kiserver/        # Python heavy compute (FastAPI)
│   └── kiconnector/     # Python subprocess broker (FastAPI)
├── packages/
│   ├── cli/             # TS Node.js CLI
│   └── kithree/         # TS three.js 3D viewer
├── examples/            # reference projects covering each milestone
│   ├── blinky/
│   ├── esp32_s3_blinky/
│   ├── stm32_audio/
│   ├── usb_eth_phy/
│   └── esp32_c6_rf/
├── libs/                # bundled symbol/footprint/3D mirror (pinned)
├── tests/               # cross-package e2e (Playwright + pytest)
├── .claude/             # kiclaude's OWN Claude Code config (skills, commands)
├── pnpm-workspace.yaml  # pnpm: client + packages/* + services/server
├── pyproject.toml       # uv workspace: services/{agent,mcp,kiserver,kiconnector}
├── Cargo.toml           # Rust workspace
└── pnpm-lock.yaml / uv.lock / Cargo.lock
```

The existing scaffold is preserved. M0 fills in `crates/{ki,cad}`, `services/{agent,mcp,kiserver,kiconnector,server}`, and `packages/{cli,kithree}`.

---

## 13. Quality Gates

Every PR runs:

1. **Rust:** `cargo fmt --check`, `cargo clippy --all-targets -- -D warnings`, `cargo test --workspace`, `cargo wasm-pack build --target web`.
2. **TypeScript:** `pnpm -r typecheck`, `pnpm -r lint`, `pnpm -r test`, `pnpm -F client build`.
3. **Python:** `uv run ruff check`, `uv run mypy --strict`, `uv run pytest`.
4. **Golden-file round-trip:** every `examples/**/*.kicad_*` parses + emits byte-identical for unedited nodes.
5. **End-to-end smoke (Playwright):** open `examples/blinky`, run `/pcb-fab`, gerbers produced.
6. **KCIR schema diff:** if any field changes, a `kcir_version` bump + migration is required (CI fails otherwise).
7. **License audit:** no GPL-licensed Rust/TS/Python code in `crates/`, `client/`, `packages/`, or `services/*` (other than fork-isolated subprocess calls).
8. **A11y:** axe-core scan of the main editor view; zero serious violations.
9. **Telemetry contract:** every `PreToolUse` / `PostToolUse` hook emits a span with `tool_name`, `session_id`, `project_id`, `duration_ms`. CI lint asserts.

Milestone demos must DRC-clean and produce a fab package an operator could send out without manual edits.

---

## 14. Security & Privacy

| Concern | Posture |
|---|---|
| Anthropic API key | Stored server-side only. Never sent to the browser. Per-user config file (`~/.config/kiclaude/credentials.json`) or env var. |
| Distributor API keys (Octopart/Mouser/Digi-Key) | Same — server-side only. |
| Project files | Local-first. The browser holds a `FileSystemDirectoryHandle` or accesses via the local `kiconnector` daemon. Hosted SaaS never sees the files unless the user explicitly enables cloud sync. |
| Chat transcripts | Stored in `<project>/.kiclaude/sessions/`. Encrypted at rest if cloud sync is on. |
| Telemetry | Off by default. Opt-in to send OTel spans (without payloads) to the kiclaude telemetry endpoint. Payloads (tool inputs/outputs) NEVER leave the user's machine without explicit opt-in. |
| MCP tool permissions | `PreToolUse` hook gates mutating tools. Default: prompt-on-mutate. Trusted mode (auto-approve) requires per-project opt-in via `.claude/settings.json`. |
| Freerouting subprocess | Receives DSN (board geometry). User informed in docs that this leaves kiclaude's process boundary. |
| Vendor lock-in | None. Project files remain valid KiCad projects; user can stop using kiclaude any time and open in KiCad 9. |

---

## 15. Risks & Mitigations

| Risk | Mitigation |
|---|---|
| KiCad file-format drift (KiCad 10, 11 introduce new S-expr tokens) | Round-trip CI catches; KCIR's "opaque blob" passthrough preserves unknown nodes; format version tied to KiCad 9 in v1, KiCad 10 in M5+ |
| LLM hallucinates MPNs / footprints | Every MPN resolves against a live distributor or fails closed. Tool returns `{ok: false, reason: "mpn_not_found"}`. |
| Rust PNS port is hard | M2 ships walk-around only; PnS in M3; we accept Freerouting as the fallback for dense boards. |
| Browser performance on 5000+ track boards | Rust+WASM hot paths; tile-based render; LOD on zoom; benchmark CI gate at NFR-003. |
| Anthropic API outages | Agent SDK supports Bedrock/Vertex/Azure passthrough; offline mode disables chat but keeps the editor. |
| Subscription cost economics (Agent SDK credit tier from 2026-06-15) | Cost dashboard via OTel; user-supplied API key model. |
| Freerouting GPL | Process-boundary isolation; never link; document install separately. |
| KiCad 9 IPC immaturity | Treat as optional bridge; everything works without it. |
| File System Access API gaps on Firefox/Safari | OPFS fallback + local `kiconnector` daemon; documented browser matrix. |
| Concurrent edits collide (no CRDT in v1) | Single-editor lock per project file; Git is the merge tool; CRDT is M5+. |
| Scope creep into KiCad parity rabbit holes (e.g., hierarchical sheet pin propagation edge cases) | Reference-project golden-file suite IS the parity definition; if a reference project round-trips, parity is sufficient for v1. |
| Vendor-locked file format temptation | First principle #1 (KiCad file format is the contract) is non-negotiable; reviewers reject any PR adding a kiclaude-only format. |

---

## 16. Decisions Locked & Decisions Pending

### 16.1 Decisions locked in this spec (no further input required)

These were "open" during drafting; the spec now commits to them so M0 can start without ambiguity. Reversing any of these requires a new SPEC revision, not a one-off override.

| # | Decision | Rationale |
|---|---|---|
| D1 | **Routing engine:** ship walk-around router in Rust at M2; port a PnS-equivalent (push-and-shove) at M3; Freerouting subprocess for full-board autoroute throughout. | Walk-around is tractable in 10 weeks; PnS gives interactive feel; Freerouting fills the "just route it all" niche without GPL contamination. |
| D2 | **Session storage:** Agent SDK's default JSONL on disk through M3; migrate to SQLite in `services/agent` at M4 when activity journal needs cross-session query. | Lowest-friction path; matches Agent SDK out-of-the-box behavior; migration is a one-shot script later. |
| D3 | **Hosting:** SaaS image first; publish Helm chart + `docker compose` for self-hosters at M2. | Onboarding UX needs central control to nail; self-host follows once the SaaS is stable. |
| D4 | **CLI language:** TypeScript only (`packages/cli`). No Python CLI. Users who want a Python toolchain use ki-mcp-pcb's `kimp`. | Matches existing scaffold; avoids duplicating maintenance surface. |
| D5 | **License:** Apache-2.0 across `crates/`, `client/`, `packages/`, `services/`. | Patent grant matters in EDA; permissive enough for commercial adopters. |
| D6 | **3D STEP source:** bundled pinned mirror of `kicad-packages3D` (offline-first); fall through to KiCad GitLab on-demand for parts not in the mirror; cache fetched parts in `services/kiserver`. | Offline operation is a first principle (#8). |
| D7 | **KCIR persistence:** never. KiCad files on disk are the only canonical persistent form. KCIR snapshots are content-addressed in `<project>/.kiclaude/snapshots/` for time-travel UI only. | Vendor-lock-in avoidance is non-negotiable. |
| D8 | **DRC source of truth:** `kicad-cli pcb drc`. The Rust DRC kernel exists only for live-feedback overlays during interactive editing; CI gates use `kicad-cli`. | Reimplementing KiCad's DRC is a five-year project and adds zero value to v1. |

### 16.2 Decisions pending (owner-tagged with deadlines)

| # | Question | Owner | Decision required by |
|---|---|---|---|
| P1 | **Pricing model (post-v1):** free self-hosted + paid SaaS, or full open source with services revenue, or paid product? | layerdynamics@proton.me | M4 kickoff (≈ week 30) |
| P2 | **Telemetry endpoint:** kiclaude-hosted (defaults to layerdynamics-controlled OTel collector), or user-supplied only? Affects whether opt-in telemetry has a default destination. | layerdynamics@proton.me | M2 kickoff (≈ week 12) |
| P3 | **Distributor API keys for the SaaS:** ship a shared key (rate-limit per user) or require BYO key? Affects free-tier UX. | layerdynamics@proton.me | M3 kickoff (≈ week 22) |
| P4 | **CRDT vendor (if M5 multiplayer):** Yjs vs Automerge vs custom? | layerdynamics@proton.me | M5 kickoff (≈ week 42) — only if multiplayer stays in scope |

---

## 17. Next Steps (the M0 punch list)

Immediately after this spec is accepted:

1. Write `Cargo.toml` workspace + scaffold `crates/ki/src/lib.rs` and `crates/cad/src/lib.rs` with the KCIR sketch and the polygon type.
2. Wire `wasm-pack` and add `vite-plugin-wasm` to `client/`. Land "render a parsed `.kicad_pcb` in kicanvas" as a smoke test.
3. Stand up `services/agent` with one no-op MCP tool (`kc_ping`) and verify the Claude Code CLI connects to it via the `.claude/settings.json` we land.
4. Stand up `services/server` with WebSocket + REST scaffolding.
5. Stand up `services/kiconnector` with a `kicad-cli --version` endpoint and a "DRC a sample board" endpoint.
6. Land the M0 demo (`examples/blinky/`) as an executable definition of "M0 done."
7. Wire CI (Linux + macOS): Rust matrix, pnpm typecheck/test, uv ruff/mypy/pytest, Playwright e2e.
8. Land this spec at `docs/specs/SPEC-01-kiclaude.md` and a top-level `SPEC.md` → symlink/redirect.

---

## Appendix A — Catalogs

### A.1 Glossary

- **KCIR** — kiclaude Canonical Intermediate Representation; the typed in-memory model.
- **CIR** — ki-mcp-pcb's Canonical IR; conceptual ancestor of KCIR but distinct.
- **DRC / ERC** — Design Rule Check (board) / Electrical Rule Check (schematic).
- **DFM** — Design For Manufacturing.
- **IPC API** — KiCad 9's Protobuf-over-NNG plugin interface (PCB editor only).
- **MPN** — Manufacturer Part Number.
- **MCP** — Model Context Protocol; how Claude talks to external tools.
- **PNS** — Push-and-Shove router (KiCad's interactive router algorithm).
- **OPFS** — Origin Private File System; browser-sandboxed FS used as a fallback to File System Access API.
- **Stackup** — physical layer/dielectric structure of the PCB.

### A.2 MCP tool catalog (initial set; all live in `services/mcp`)

Tools are partitioned into two disjoint sets enforced by `services/mcp` at registration time (first principle #4):

- **Claude-facing tools** — declarative inputs (constraints, refdes, net names, hints). Registered with the Agent SDK's `mcp_servers` and visible to Claude.
- **UI-only tools** — accept literal coordinates / geometry from human-driven gestures. Registered in a separate registry consumed by the React frontend over HTTPS REST. **Never exposed to Claude.** A pre-registration assertion in `services/mcp` fails the boot if a UI-only tool name appears in the Claude-facing list.

#### A.2.1 Claude-facing tools

| Tool | Input | Output |
|---|---|---|
| `kc_ping` | none | `{ok, version, kcir_version, kicad_cli_version}` |
| `kc_project_open` | `{path}` | `{ok, project_id, summary}` |
| `kc_project_save` | `{project_id}` | `{ok, files_written[]}` |
| `kc_kcir_get` | `{project_id, view: "schematic"|"pcb"|"bom"}` | KCIR JSON subset |
| `kc_validate` | `{project_id}` | `{ok, issues[]}` (KC001..KC081) |
| `kc_erc` | `{project_id}` | `{ok, issues[]}` (delegated to `kicad-cli`) |
| `kc_drc` | `{project_id}` | `{ok, issues[]}` (delegated to `kicad-cli`) |
| `kc_symbol_add` | `{project_id, sheet, lib_id, near?: refdes \| "auto"}` | `{ok, refdes_assigned}` — placer picks coords |
| `kc_symbol_edit` | `{project_id, refdes, fields}` | `{ok}` |
| `kc_wire_connect` | `{project_id, sheet, from: pad_ref, to: pad_ref}` | `{ok, wire_id}` — router picks geometry |
| `kc_label_attach` | `{project_id, sheet, kind, name, at: pad_ref}` | `{ok}` |
| `kc_footprint_place_hint` | `{project_id, refdes, hints: PlacementHint[]}` — hints include `{anchor: "edge"\|"center"\|"near"\|"within_mm_of_supply", target?, distance_mm?, side?}` | `{ok, resolved_xy_mm, rotation_deg}` |
| `kc_track_route` | `{project_id, net, prefer_layer?, max_width_mm?}` | `{ok, tracks[]}` — router picks geometry |
| `kc_zone_request` | `{project_id, layer, net, area: "full_layer"\|"behind": refdes\|polygon_hint}` | `{ok, zone_id}` |
| `kc_netclass_set` | `{project_id, net, class}` | `{ok}` |
| `kc_diffpair_declare` | `{project_id, net_a, net_b, target_impedance, length_match_group}` | `{ok}` |
| `kc_length_match_set` | `{project_id, group, tolerance_mm}` | `{ok}` |
| `kc_impedance_check` | `{project_id, net?}` | `{ok, results[]}` |
| `kc_decoupling_check` | `{project_id}` | `{ok, missing[]}` |
| `kc_partition_check` | `{project_id}` | `{ok, violations[]}` |
| `kc_bom_get` | `{project_id}` | BOM JSON |
| `kc_bom_price` | `{project_id, region}` | priced BOM with stock |
| `kc_mpn_resolve` | `{mpn}` | `{ok, symbol_candidates, footprint_candidates, stock}` |
| `kc_export_fab` | `{project_id, target}` | `{ok, zip_path, files[]}` |
| `kc_export_step` | `{project_id, out_path}` | `{ok}` |
| `kc_panelize` | `{project_id, layout}` | `{ok, panel_path}` |
| `kc_route_freerouting` | `{project_id, options}` | `{ok, ses_path}` |
| `kc_diff` | `{project_id_a, project_id_b}` | structured diff |
| `kc_snapshot_create` | `{project_id, message?}` | `{ok, snapshot_id}` |
| `kc_snapshot_revert` | `{project_id, snapshot_id}` | `{ok}` |
| `kc_session_fork` | `{session_id, label?}` | `{ok, new_session_id}` |

#### A.2.2 UI-only tools (NOT exposed to Claude)

These accept literal coordinates from human gestures (drag, click, property-panel edit). They are called from `client/` via the REST API, never registered in `mcp_servers`.

| Tool | Input | Output |
|---|---|---|
| `ui_symbol_place_xy` | `{project_id, sheet, lib_id, x, y, rotation}` | `{ok, refdes_assigned}` |
| `ui_footprint_place_xy` | `{project_id, refdes, x_mm, y_mm, rotation_deg, layer}` | `{ok}` |
| `ui_footprint_move_delta` | `{project_id, refdes, dx_mm, dy_mm}` | `{ok}` |
| `ui_wire_draw_points` | `{project_id, sheet, points[]}` | `{ok, wire_id}` |
| `ui_label_place_xy` | `{project_id, sheet, kind, name, x, y}` | `{ok}` |
| `ui_track_draw_points` | `{project_id, layer, net, points[], width_mm}` | `{ok, track_id}` |
| `ui_via_place_xy` | `{project_id, x_mm, y_mm, from_layer, to_layer, net}` | `{ok}` |
| `ui_zone_create_polygon` | `{project_id, layer, net, outline_points[]}` | `{ok, zone_id}` |

Both sets share the underlying domain implementation in `services/kiserver`; only the **input shape** differs. All mutating tools in either set flow through `PreToolUse` (for Claude-facing) or the standard auth + audit middleware (for UI-only).

### A.3 Slash commands (initial set; under `.claude/commands/`)

| Command | Purpose |
|---|---|
| `/add-mcu <family>` | Add an MCU subsystem with decoupling + reset + boot pins wired |
| `/add-power <topology>` | Add a power tree (LDO, buck, USB-C PD) feeding declared rails |
| `/add-decoupling` | Scan for ICs missing bypass caps and add them |
| `/add-led [pin]` | Add a status LED + current-limit resistor |
| `/add-usb-c [pd] [data]` | Add a USB-C connector with optional PD trigger / data lines |
| `/route-power` | Route power nets first using net-class widths |
| `/route-signals` | Route signal nets (walk-around) |
| `/route-freerouting` | Hand off to Freerouting; round-trip SES |
| `/length-match <group>` | Set up a length-match group with auto-derived tolerance |
| `/diffpair <a> <b>` | Declare a diff pair |
| `/erc-fix` | Read ERC results and propose one fix at a time |
| `/drc-fix` | Same for DRC |
| `/pcb-review` | Structured design review (decoupling, partitions, return paths, BOM risk) |
| `/pcb-fab <target>` | Pre-flight DFM + export fab package |
| `/board-diff <ref>` | Visual + textual diff against a Git ref |
| `/bom-price [region]` | Live BOM pricing + stock check |
| `/explore-placements [n]` | Background subagent: try n placement variants and rank |
| `/snapshot [message]` | Create a named snapshot |
| `/revert <snapshot>` | Revert to a snapshot |

### A.4 Skills (initial set; under `.claude/skills/`)

| Skill | Teaches Claude... |
|---|---|
| `kicad-schematic` | How to read/edit the schematic (symbols, wires, labels, hierarchical sheets, ERC). Which kc_* tools apply. |
| `kicad-pcb` | How to read/edit the PCB (footprints, tracks, vias, zones, DRC). Net-class etiquette. Declarative placement. |
| `manufacturing` | Fab-target presets, DFM rules, gerber + drill + PnP + BOM packaging. |
| `design-review` | Structured review checklist by milestone tier (basic / mixed-signal / high-speed / RF). |
| `parts-sourcing` | MPN resolution, distributor preference order, stock-risk flagging. |

### A.5 Reference projects (under `examples/`)

| Project | Milestone | Demonstrates |
|---|---|---|
| `blinky/` | M0 | Minimal: ESP32-S3 + LED. Parses, renders, exports gerbers. |
| `esp32_s3_blinky/` | M1 | Schematic editing + ERC + chat-driven additions. |
| `stm32_audio/` | M3 | Mixed-signal partitions, ferrite bridge, I2S length-match. |
| `usb_eth_phy/` | M3 | USB 2.0 HS + 100BASE-T diff pairs, controlled impedance. |
| `esp32_c6_rf/` | M5 | RF antenna CPWG + DDR3L fly-by sketch + BGA fanout (co-pilot only). |

---

## Appendix B — Sources Consulted

- ki-mcp-pcb `SPEC.md` + `CLAUDE.md` (local, `/Users/ryanoboyle/kiclaude/development/reference-only/ki-mcp-pcb/`).
- ki-mcp-pcb packages: `ki_mcp_pcb_core`, `ki_mcp_pcb_server`, `ki_mcp_pcb_cli`, `ki_mcp_pcb_web`, `ki_mcp_pcb_gui` (file-level survey).
- Local repos: kicanvas, tscircuit, KiKit, PcbDraw, freerouting, kicad-library, kicad-packages3D, kicad-library-utils, kicad-python (under `/Users/ryanoboyle/kiclaude/development/resources/kicad/`).
- Local repos: claude-agent-sdk-python, claude-agent-sdk-demos, agent-sdk-workshop, anthropic-sdk-typescript, claude-desktop-buddy, claude-code-monitoring-guide, claude-cookbooks, anthropic-tokenizer-typescript, prompt-eng-interactive-tutorial, buffa, torchtyping (under `/Users/ryanoboyle/kiclaude/development/resources/claude/`).
- Web: KiCad IPC API developer documentation (https://dev-docs.kicad.org/en/apis-and-binding/ipc-api/).
- Web: KiCad S-expression file format docs (https://dev-docs.kicad.org/en/file-formats/).
- Web: Claude Agent SDK overview (https://code.claude.com/docs/en/agent-sdk/overview).
- Web: MDN / Chrome docs on File System Access API.
- Web: Flux.ai "AI Assistance Inside Every ECAD Tools" landscape post.
- Web: KiCad 9 IPC API forum thread (https://forum.kicad.info/t/kicad-9-0-python-api-ipc-api/57236).
