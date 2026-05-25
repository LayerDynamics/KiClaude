# Todo — Doc-vs-Code Gap Audit (kiclaude)

**Generated:** 2026-05-25
**Method:** Read line-by-line — `docs/specs/SPEC-01-kiclaude.md`,
`docs/plans/2026-05-21-kiclaude-m0-m3.md`,
`docs/plans/2026-05-24-m2-r-05d-edge-aligned-offset.md` — then verified
each claim against the **current source tree** (file existence + content
reads + greps), ignoring the plan's `[x]` checkboxes. Every line below
cites the evidence it rests on. Findings the plan *claims* done but the
code contradicts are flagged; findings that are merely "built in a
different file than the plan named" are listed separately as **not gaps**.

## What kiclaude is (so the gaps mean something)

kiclaude is **browser-native, AI-native, KiCad-compatible EDA** — Claude
Code's hardware counterpart. The user opens a `.kicad_pro` in the browser,
chats with Claude, and **Claude proposes every edit as a typed MCP tool
call** that round-trips to the on-disk KiCad files. Two product promises
frame this audit:

- *"Claude operates through typed tools"* (first principle #3) and *"the MCP
  server exposes **every** domain action as a typed tool"* (FR-052). So a
  capability that exists only in Rust/UI but has **no Claude-facing MCP
  tool** is a real product gap — Claude literally cannot use it.
- *"Every MPN must resolve … no hallucinated parts"* (first principle #5)
  and *"local-first, cloud-optional / offline"* (#8, FR-040) depend on a
  **bundled library + parts mirror** that the repo does not yet contain.

The codebase is far along: round-trip parser/emitter, KCIR + migrations to
v0.4, DRC kernel, walk-around + push-and-shove routers, impedance solver,
length-match analyzer, zone fill, distributor adapters, the full React
editor surface, CLI, and 3 hosted Python services all exist and test green.
The gaps below are the **edges that the docs promise but the tree does not
yet deliver** — concentrated in the Claude-facing tool surface, the
design-intent validators, and the bundled libraries.

---

## Verdict

| # | Gap | Severity | Docs source | Status in plan |
|---|-----|----------|-------------|----------------|
| 1 | 8 Claude-facing MCP tools never registered | **High** | SPEC §A.2.1, FR-052 | claimed done (M3-P-06) |
| 2 | Design-intent validators KC020/021/030/031/040/050 absent | **High** | SPEC §7.3 | partially scoped |
| 3 | Bundled `libs/` symbol+footprint+3D mirror absent | **High** | SPEC §9.5, §12, D6, FR-040 | referenced, never landed |
| 4 | `kc_mpn_resolve` still M1 heuristic (no distributor call) | **Med** | SPEC §A.2.1, FR-041, FP#5 | claimed "(full)" (M3-P-06) |
| 5 | 5 slash commands absent (`/add-led /add-usb-c /board-diff /snapshot /revert`) | **Med** | SPEC §A.3 | not all scheduled |
| 6 | STEP **geometry** parsing absent (`crates/ki/src/format/step.rs`) | **Med** | FR-029, M3-R-06 | deferred to M4 (acknowledged) |
| 7 | Zone-fill `simple` XOR 0.0123 > 0.01 target | **Low** | M2-R-05c / M2-R-05d | open (honestly documented) |
| 8 | FR-043 drop-to-import `.kicad_sym`/`.kicad_mod` absent | **Low** | FR-043 ("Should") | never scheduled |
| 9 | `docs/architecture/`, `docs/observability/`, `docs/ADR/` absent | **Low** | SPEC §12, §8.8, X-01/X-04 | cross-milestone |
| 10 | Top-level `SPEC.md` redirect absent | **Trivial** | SPEC §17 step 8 | not scheduled |

---

## 1. Eight Claude-facing MCP tools are documented but never registered  — **High**

SPEC §A.2.1 enumerates the Claude-facing tool catalog. The live registry is
`_CLAUDE_TOOLS` in `services/mcp/src/kc_mcp/server.py:42-75` (28 tools). The
following SPEC §A.2.1 tools are **absent from the entire repo** — a grep for
each name across `services/` and `packages/` returned **0 files**:

| Missing tool | SPEC §A.2.1 contract | Backing capability that EXISTS but isn't exposed |
|---|---|---|
| `kc_diffpair_declare` | `{net_a, net_b, target_impedance, length_match_group}` | UI tool `ui_tools/diffpair_edit.py`; router `crates/cad/src/routing/diffpair.rs` |
| `kc_length_match_set` | `{group, tolerance_mm}` | analyzer `crates/cad/src/length_match.rs`; UI `ui_tools/lengthgroup_edit.py` |
| `kc_impedance_check` | `{net?}` → results[] | solver `crates/cad/src/impedance.rs` (wasm-exported, used by UI only) |
| `kc_decoupling_check` | `{}` → missing[] | none (validator KC020 also missing — see §2) |
| `kc_partition_check` | `{}` → violations[] | none (validator KC050 also missing — see §2) |
| `kc_bom_get` | `{project_id}` → BOM JSON | `kc_bom_price` exists; `kc_bom_get` does not |
| `kc_export_step` | `{project_id, out_path}` | kiconnector `POST /tools/step` + `export.py::export_step` — no MCP wrapper |
| `kc_session_fork` | `{session_id, label?}` | SPEC §8.4 (`options.fork`); not surfaced as a tool |

**Why this matters:** M3-P-06's "Done when" lists the first six + `kc_bom_get`
as deliverables, but its own Status note admits it only shipped
`kc_part_search` + `kc_bom_price` ("registry count bumped 26 → 28"). The plan
checkbox is `[x]`; the code is not. This violates FR-052 ("expose **every**
domain action as a typed tool") and FR-071 ("CLI mirrors every MCP tool").
The math/analysis engines are done — these are thin declarative MCP wrappers
over capabilities that already exist.

**Evidence:** `services/mcp/src/kc_mcp/server.py:18-75`; grep of all 8 names → 0 hits.

**To do:** Add 8 `@tool`-decorated functions under `services/mcp/src/kc_mcp/tools/`
(declarative inputs only, per FP#4), register them in `_CLAUDE_TOOLS`, bump the
registry-count contract test, and mirror them as CLI subcommands (FR-071).
`kc_decoupling_check`/`kc_partition_check`/`kc_impedance_check` also need their
validator backends from §2.

---

## 2. Design-intent validators KC020–KC050 are absent; KC001–KC011 numbering drifts from the spec  — **High**

SPEC §7.3 defines validators KC001–KC081. `services/mcp/src/kc_mcp/tools/validate.py:53-247`
implements **KC001–KC011 only**, and their meanings **do not match SPEC §7.3**:

| Code | SPEC §7.3 meaning | What `validate.py` actually checks |
|---|---|---|
| KC001 | Unique refdes per project | symbol has a non-empty refdes (uniqueness is at code KC006) |
| KC002 | Every net member resolves to a pad | footprint has a `lib_id` |
| KC003 | Ground net present | hierarchical label has a matching sheet pin |
| KC004 | Stackup matches fab layer count | two sub-sheets don't claim the same pin |
| KC010 | Assigned footprints exist in `fp-lib-table` | non-power symbol has a non-empty Value |
| KC011 | Assigned symbols exist in `sym-lib-table` | footprint has a matching schematic symbol |

So there are two distinct problems:

- **Numbering drift (Med):** the 11 implemented checks are useful structural
  validators but are mis-numbered against the spec. Either renumber them or
  amend SPEC §7.3 so `kc_validate`'s advertised "(KC001..KC081)" is truthful.
- **Genuinely missing (High):** the **design-intent** validators —
  **KC020** (every IC has a bypass cap), **KC021** (power rail has a source),
  **KC030** (length-match group ≥2 members), **KC031** (diff pairs declared
  bidirectionally + shared length group), **KC040** (controlled-impedance
  achievable on the stackup, Hammerstad), **KC050** (partition isolation) —
  have **no implementation anywhere**. These are the M3 mixed-signal /
  high-speed checks the `/pcb-review` command (`M3-C-05`) and the missing
  `kc_decoupling_check`/`kc_partition_check`/`kc_impedance_check` tools (§1)
  are supposed to call. (KC060 DDR / KC070 BGA are M5, out of scope. KC080/
  KC081 are correctly delegated to `kc_drc`/`kc_erc`.)

**Evidence:** `services/mcp/src/kc_mcp/tools/validate.py:1,21,53-247`; grep of
`KC020|KC021|KC030|KC031|KC040|KC050` in source → 0 hits.

**To do:** Implement KC020/021/030/031/040/050 (the solver for KC040 already
exists in `crates/cad/src/impedance.rs`), wire them into `kc_validate` and the
new `kc_*_check` tools, and reconcile the KC001–011 numbering with SPEC §7.3.

---

## 3. The bundled symbol/footprint/3D mirror (`libs/`) does not exist  — **High**

SPEC §9.5, §12, decision **D6**, and FR-040 require kiclaude to ship a
**pinned bundled mirror** of `kicad-symbols`, `kicad-footprints`, and
`kicad-packages3D`, served from `services/kiserver` and lazy-loaded in the
browser. The repo has **no top-level `libs/` directory**, and the library
indexer reads **only the user's `sym-lib-table`** — there is no bundled-mirror
code path.

**Why this matters:** FR-040 says libraries are indexed "at session start"
from "the user's local libraries **and the bundled mirror**." Without the
mirror, a project that references the standard KiCad libs (every example does
— see `examples/*/sym-lib-table`) cannot resolve symbols/footprints offline,
breaking first principle #8 (local-first/offline) and undercutting #5 (parts
resolve). The 3D viewer (M3-T-06) likewise has no STEP models to place.

**Evidence:** `ls libs/` → absent; `services/kiserver/src/kiserver/library.py:121-127`
indexes a `sym_lib_table_path` only; grep `kicad-symbols-mirror|libs/kicad`
in `crates/ki/src/library` + `services/kiserver/src` → only test stubs
(`/tmp/test-libs`, `/opt/libs`). The Rust indexer `crates/ki/src/library/`
and the kiserver indexer both work — they just have nothing bundled to point at.

**To do:** Land the pinned `libs/` mirror (or a download-on-install script
per D6), and add the bundled-mirror path to both indexers' session-start scan.

---

## 4. `kc_mpn_resolve` is still the M1 heuristic, not the M3 distributor-backed resolver  — **Med**

SPEC §A.2.1 specifies `kc_mpn_resolve(mpn)` → `{symbol_candidates,
footprint_candidates, stock}`, and M3-P-06's "Done when" lists
`kc_mpn_resolve (full)`. The actual tool in
`services/mcp/src/kc_mcp/tools/mpn.py:1-16,51-` is an **M1 local regex
shape-check** that returns `found:false` and **never calls a distributor**
— its own docstring says "M3 wires this to the Octopart / Mouser / Digi-Key
APIs." That wiring did not happen for this tool.

The real distributor adapters **do** exist (`distributors/{digikey,mouser,
octopart,jlcpcb}.py`) and are reachable through the separate `kc_part_search`
/ `kc_bom_price` tools — so the capability is present, but `kc_mpn_resolve`
itself is unchanged and does not meet its SPEC contract (stock/candidates).

**Evidence:** `services/mcp/src/kc_mcp/tools/mpn.py:1-16` (docstring) and the
regex-only `_resolve_impl`.

**To do:** Upgrade `kc_mpn_resolve` to call `build_default_aggregator()` for
stock + the library indexers (§3) for symbol/footprint candidates.

---

## 5. Five slash commands from SPEC §A.3 are absent  — **Med**

SPEC §A.3 lists 19 commands; `.claude/commands/` contains **14**. Missing:

| Missing command | SPEC §A.3 purpose |
|---|---|
| `/add-led [pin]` | add a status LED + current-limit resistor |
| `/add-usb-c [pd] [data]` | add a USB-C connector with optional PD trigger / data |
| `/board-diff <ref>` | visual + textual diff against a Git ref (backed by `kc_diff`, which exists) |
| `/snapshot [message]` | create a named snapshot (backed by `kc_snapshot_create`, which exists) |
| `/revert <snapshot>` | revert to a snapshot (backed by `kc_snapshot_revert`, which exists) |

Three of the five (`/board-diff`, `/snapshot`, `/revert`) are trivial wrappers
over MCP tools that already exist — only the command markdown is missing.
`/add-led` and `/add-usb-c` need synthesis logic similar to the existing
`/add-mcu`. (The plan only scheduled a subset of §A.3 as `M*-C-*` tasks, so
these were never tasked — but they are in the SPEC.)

**Evidence:** `ls .claude/commands/` (14 files) vs SPEC §A.3 (19 rows).

---

## 6. 3D STEP **geometry** parsing is not implemented  — **Med (acknowledged deferral)**

FR-029 and M3-R-06 call for reading `.step` files from the bundled mirror.
The plan names `crates/ki/src/format/step.rs` and `crates/cad/src/three/scene.rs`;
**neither path exists**. The scene-description half is built in
`crates/cad/src/three_scene.rs` (it walks `Model3D` path refs and emits
placement transforms), but it **does not parse STEP geometry** — and the M3-T-06
note states STEP loading "is deferred to M4 (needs occt-import-js, ~20 MB wasm)";
`packages/kithree` renders marker boxes instead of real models.

This is an **honestly-documented deferral**, not a hidden gap — listed here so
the M4 scope is explicit and the absent `crates/ki/src/format/step.rs` is on record.

**Evidence:** `crates/cad/src/three_scene.rs:1-40`; `find crates -iname '*step*'`
shows no STEP reader (`.step` appears only in path-string literals).

---

## 7. Zone-fill `simple` fixture still 0.0123 mm² XOR vs the 0.01 target  — **Low (documented)**

M2-R-05c and M2-R-05d are the only `[ ]` checkboxes in the plan. The
`2026-05-24-m2-r-05d-edge-aligned-offset.md` plan records **six** approaches
empirically ruled out (edge-aligned sector, phase disc, integer kernel, f64
`ClipperOffset` port, integer-nm engine, blocked `clipper2` FFI) and concludes
the residual is **algorithmic** (KiCad's exact min-width opening sequence), not
precision. The gate is held at `XOR ≤ 0.015 / Hausdorff ≤ 0.01` in
`tests/golden/tests/zone_fill.rs`. No deception here — the plan recommends
accepting 0.0123. Reaching 0.01 requires a full pure-Rust port of KiCad's
`ZONE_FILLER` min-width sequence (multi-week). **Decision needed:** fund the
port or formally accept 0.015 and close the checkbox.

---

## 8–10. Lower-severity / housekeeping gaps

- **8 — FR-043 drop-to-import (`Low`, "Should"):** no drag-drop import of a
  `.kicad_sym`/`.kicad_mod` onto the editor. Grep for `SnapEDA|Ultra Librarian|
  import .kicad_mod` → 0 hits. Never scheduled in the plan; FR-044 (SnapEDA /
  Ultra Librarian) is "May"/post-v1.
- **9 — docs scaffolding (`Low`):** SPEC §12 lists `docs/architecture/` and
  `docs/observability/`; §8.8 promises a Grafana preset under
  `docs/observability/`; X-01 wants `docs/ADR/`. `docs/` contains only `plans/`
  and `specs/`. These are cross-milestone (X-tasks), not milestone-gated.
- **10 — `SPEC.md` redirect (`Trivial`):** SPEC §17 step 8 asks for a top-level
  `SPEC.md` → `docs/specs/SPEC-01-kiclaude.md`. Absent (`ls SPEC.md`).

---

## NOT gaps — verified present, just built differently than the plan named

These were checked because the plan's "Files:" paths don't exist; the
functionality **is implemented elsewhere** and is wired in. Listed so it's
clear they were verified, not missed:

| Plan-named path (absent) | Actual location (present + wired) |
|---|---|
| `services/agent/src/agent/agents/{decoupling_auditor,bom_sourcer,placement_explorer}.py` | `services/agent/src/agent/subagents/__init__.py` — 3 `AgentDefinition`s, registered via `bridge.py:87` `agents=all_subagents()` |
| `crates/cad/src/si/impedance/{microstrip,stripline}.rs` | `crates/cad/src/impedance.rs` (Hammerstad + IPC-2141 + stripline + diff-pair) |
| `crates/cad/src/routing/pns/diffpair.rs` | `crates/cad/src/routing/diffpair.rs` |
| `crates/cad/src/three/scene.rs` | `crates/cad/src/three_scene.rs` |

All 8 SPEC §A.2.2 **UI-only** tools are present (`ui_tools/`), the
registration-time `assert_no_ui_tools_in_claude_registry` guard (FP#4) is
enforced (`server.py:78-95`), all 5 SPEC §A.4 skills exist, and the `tests/`
tree (`golden/`, `e2e/`, `a11y/`, `perf/`, `audits/`) is fully scaffolded with
real test files.

## M4/M5 items pulled forward and IMPLEMENTED (2026-05-25)

At the user's direction, the five parked M4/M5 items below were implemented
this session — real, tested, no stubs. P4 (CRDT vendor) was decided: **Yjs**
(see `docs/ADR/0001-crdt-yjs.md`).

| Item | What landed | Tests |
|---|---|---|
| **KC060 + KC070 validators** (M5) | `services/mcp/src/kc_mcp/tools/validate.py` — DDR fly-by node-count + sign-off gate; BGA fanout pitch-feasibility + sign-off gate; reads the new `pcb.signoff` | `test_validate_m5.py` (11) |
| **`examples/esp32_c6_rf/`** (M5) | Real 4-layer board: ESP32-C6 module, DDR3L **BGA 4×4 ball grid** (KC070), U.FL RF connector, GND plane on In1.Cu, CPWG feed track; `.kicad_pcb`/`.kicad_sch`/`.kicad_pro` + lib tables. Round-trips byte-identical; opens via `KiProject::open`. New dev tool `crates/ki/examples/canonicalize.rs` | golden round-trip + `integration_open_esp32_c6_rf_reference_project` |
| **FR-007 cloud sync** | Content-addressed object store `kiserver/object_store.py` (`LocalFs` + lazy-`boto3` `S3`, env-selected) + `sync.py` (push/pull KiCad files via manifest) + routes `POST /project/{id}/sync/push`, `POST /sync/pull` | object-store contract suite over both backends (27, S3 via `moto`), sync (8), routes (4) |
| **FR-080 share link** | `kiserver` `POST /project/{id}/share` + `GET /share/{token}` + `GET /share/{token}/file` (token = content-addressed manifest key, immutable/read-only) + client `#/share/<token>` route + `SharePage` (read-only kicanvas preview + downloads) | share routes (6), `SharePage.test.tsx` (7), router (extended) |
| **FR-081 CRDT multiplayer** | Yjs relay: `services/server/src/crdt.ts` (`CrdtRoom`/`CrdtHub`, authoritative `Y.Doc`, update-based sync) + `WS /crdt/:projectId` (off by default, `KICLAUDE_MULTIPLAYER`) + client `client/src/lib/crdt.ts` (`CrdtSession`). ADR-0001 records P4=Yjs | server (11 incl. 2-peer convergence) + client (6 incl. convergence) |

Supporting change: **KCIR `Signoff`** added to `pcb` with migration 0.4→0.5
(`crates/ki/src/kcir/migrations/v0_5.rs`, version bump, ts-rs regen) and a
**`PreToolUse` hard gate** (`services/agent/.../permission.py::targets_signoff`)
that denies any Claude attempt to flip `pcb.signoff.*` even in trusted mode —
"the LLM cannot flip sign-off" (SPEC §11 M5).

**Still deferred (not requested):** FR-044 SnapEDA/Ultra Librarian
(May/post-v1), KiCad IPC bridge (M5). The CRDT layer converges at the
JSON-document level; KCIR-aware semantic merge is post-v1 (ADR-0001).
The original M0–M3 gaps §1–§5 above remain open.

---

## Recommended order of attack

1. **§3 bundled `libs/` mirror** — unblocks offline part/library resolution,
   the 3D viewer's models, and §4. Foundational.
2. **§1 + §2 together** — the 8 missing MCP tools and the 6 missing validators
   are the same M3 high-speed/sourcing surface; `kc_decoupling_check` ⇄ KC020,
   `kc_partition_check` ⇄ KC050, `kc_impedance_check` ⇄ KC040. Build the
   validators, then the declarative tool wrappers, then the CLI mirrors.
3. **§4 `kc_mpn_resolve` (full)** — depends on §3 (candidates) + the existing
   aggregator (stock).
4. **§5 the 3 trivial commands** (`/board-diff`, `/snapshot`, `/revert`) — pure
   markdown over existing tools; then `/add-led`, `/add-usb-c`.
5. **§7 zone-fill decision**, **§6 STEP (M4)**, **§8–10 housekeeping** — as
   bandwidth allows.

---

## Sequential execution backlog (2026-05-25)

Worked top-to-bottom; each lands as its own commit with tests. Honesty
note: **T10 and T11 are deliberately NOT "completed"** — they are
multi-week / explicitly-deferred efforts that cannot be done without
faking, so they're recorded with their blockers, not stubbed.

- [x] **T1 — `SPEC.md` root redirect** (Todo §10). `SPEC.md` → `docs/specs/SPEC-01-kiclaude.md` (symlink, git mode 120000). ✅
- [x] **T2 — Resolve SPEC §16.2 P4 status** (Todo §11). P4 row marked RESOLVED → Yjs, pointing at ADR-0001. ✅
- [x] **T3 — `docs/architecture/` + `docs/observability/`** (Todo §9). Architecture overview (`architecture/README.md`) + OTel collector config + importable Grafana dashboard + README, grounded in the real `agent.hook.*` span contract. JSON/YAML validated. ✅
- [x] **T4 — 5 slash commands** (Todo §5). `/snapshot`, `/revert`, `/board-diff`, `/add-led`, `/add-usb-c` added under `.claude/commands/` (registered, verified in the skills list). ✅
- [x] **T5 — Design-intent validators** (Todo §2). KC020/021/030/031/040/050 in `validate.py` (KC040 via a real IPC-2141A microstrip estimate; numeric Hammerstad check deferred to `kc_impedance_check`/T6). 19 new unit tests; KC001–011 numbering reconciliation documented in the docstring. ✅
- [x] **T6 — 8 Claude-facing MCP tools** (Todo §1). All 8 registered in `_CLAUDE_TOOLS` (registry 28→36): checks reuse the validators; diffpair/length-match reuse the UI mutation logic + persist via `/replace`; export_step→kiconnector; bom_get from KCIR; session_fork backed by a new kiserver `/session/fork` route writing an agent-readable manifest. 11 tool tests + 2 route tests. ✅ (note: kc_session_fork records the fork; wiring the agent loop to resume `options.fork` from it is a follow-up.) 
- [x] **T7 — `kc_mpn_resolve` (full)** (Todo §4). Now hits the real distributor aggregator (stock/price/lifecycle/datasheet, fails closed per FP#5) + pulls symbol/footprint candidates from a new kiserver `GET /project/{id}/library/search` route over the existing `LibraryIndex`. 5 mpn tests + 3 route tests; existing tests made hermetic. ✅ (candidates are richer once T9's bundled mirror is wired). 
- [x] **T8 — FR-043 drop-to-import** (Todo §8). Drag-drop a `.kicad_sym`/`.kicad_mod` onto the editor → kiserver `POST /project/{id}/library/import` (writes the file into a project-local `imported-libs/` or `<nick>.pretty/`, idempotently registers a `sym-lib-table`/`fp-lib-table` row, strips path traversal) + client `useLibraryImport` hook & `LibraryImportDropZone` (extension-inferred kind, error surfacing). 6 route tests + 5 client tests; client typecheck green. ✅ (also fixed a pre-existing `BomView.tsx` TS6133 by wiring the `lastRequestId` stale-response guard into its async `load`.)
- [x] **T9 — Bundled `libs/` mirror** (Todo §3). Real pinned mirror under `libs/`: 5 whole `.kicad_sym` libraries (Device, power, Connector, Regulator_Switching, RF_Module) + the exact `.kicad_mod` footprints the examples place, all fetched from KiCad GitLab at tag `9.0.0` and SHA-256-pinned in `MANIFEST.toml` by `scripts/populate_libs.py` (`--pin` re-fetches+repins; default mode verifies + self-heals per D6). `sym-lib-table`/`fp-lib-table` register them via `${KICLAUDE_BUNDLED_LIBS}`; `LICENSE.md` carries the CC-BY-SA-4.0 + KiCad Library Exception attribution. kiserver `GET /project/{id}/library/search` now resolves the project's own libs **and** the bundled mirror (`bundled_libs_dir()` + `_merge_hits`, FR-040). Empirical gate: `Device:R`/`Connector:USB_C_*` resolve through the route from a project with an empty table (9 tests). **Also fixed the examples' fictional/wrong library refs to real KiCad 9.0.0 parts** (per the user's call): blinky `MCU_Espressif:`→`RF_Module:ESP32-S3-WROOM-1`; esp32_c6_rf `ESP32-C6-WROOM-1`→`ESP32-C6-MINI-1`, fabricated `Package_BGA:DDR3L_BGA-16_4x4_P0.8mm`→real `BGA-16_1.92x1.92mm_Layout4x4_P0.5mm` (Value `MT41K256M16-DDR3L`→`BGA-16`, since that real DRAM is FBGA-96), `RF_Connectors:U.FL_Molex_73412-0110`→`Connector_Coaxial:U.FL_Molex_MCRF_73412-0110_Vertical`; buck_subsystem `Regulator_Switching:TPS562201`→pin-identical real `TPS562202` (+ matching datasheet URL). Golden round-trip (M0-Q-02) + snapshot re-verified green. ✅
- [ ] **T10 — STEP geometry parsing** (Todo §6) — **BLOCKED, not completing.** Real `.step` mesh parsing needs `occt-import-js` (~20 MB wasm); SPEC §11 defers it to M4. The placement scene (`three_scene.rs`) + marker-box viewer already exist. Recorded, not stubbed.
- [ ] **T11 — Zone-fill `simple` XOR ≤ 0.01** (Todo §7) — **BLOCKED, not completing.** `docs/plans/2026-05-24-m2-r-05d-edge-aligned-offset.md` empirically ruled this out across 6 attempts; needs a multi-week from-scratch KiCad `ClipperOffset` port. Gate held at the documented 0.015 floor. Decision item, not an implementable task.
