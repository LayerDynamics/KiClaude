# Expanded Searches: Robust polygon-offset to match KiCad's zone-fill round-corner geometry in Rust

Generated: 2026-05-24

## Root Topic

Robust polygon-offset approach to match KiCad's zone-fill round-corner geometry in Rust — specifically: (1) how KiCad's `SHAPE_POLY_SET::Inflate`/`Deflate` and ClipperLib2 (Angus Johnson) construct `jtRound` join arcs (exact starting angle, angular step, inscribed-vs-on-arc vertex placement, arc tolerance handling); (2) permissively-licensed (MIT/Apache/BSL, NO GPL) Rust crates for robust polygon offsetting with round joins and configurable arc tolerance — `cavalier_contours`, `geo`/`geo-offset`/`geo-buffer`, `i_overlay`/`i_float`/`i_shape`, Clipper2 Rust bindings (`clipper2` crate); (3) whether the `i_overlay`/`overlay` Rust crate has a built-in offset/inflate/stroke API.

## Orientation Notes (from Phase 1 — seed facts for the agents)

These facts were confirmed during initial exploration and should anchor the research agents:

- **Clipper2 `DoRound` / arc-step formula (from `clipper.offset.cpp`):**
  `arcTol = (arc_tolerance_ > tol) ? min(abs_delta, arc_tolerance_) : abs_delta * arc_const;`
  `steps_per_360 = min(PI / acos(1 - arcTol / abs_delta), abs_delta * PI);`
  `steps_per_rad_ = steps_per_360 / (2*PI);`
  Default `arc_const = 0.002` (= 1/500, i.e. tolerance ≈ radius/500 when ArcTolerance unset). Vertices are placed by incrementally rotating the offset vector with a precomputed rotation matrix (`step_sin_`, `step_cos_`); step count = `ceil(steps_per_rad_ * abs(angle))`. Default ArcTolerance = 0.25 units. ArcTolerance only matters for `jtRound`/`etRound`.
- **KiCad (`geometry_utils.h`):** `int GetArcToSegmentCount(int aRadius, int aErrorMax, const EDA_ANGLE& aArcAngle)` — returns segment count (>=1) so the max chord-to-arc deviation stays under `aErrorMax`. `SHAPE_POLY_SET::Inflate(int aAmount, CORNER_STRATEGY, int aMaxError, bool aSimplify)`; `CORNER_STRATEGY` includes `ROUND_ALL_CORNERS` (in `corner_strategy.h`); KiCad coordinates are integer nanometres. Note: KiCad's zone filler internally calls Clipper, so matching KiCad ≈ matching Clipper2's offset semantics plus KiCad's `aMaxError` → segment-count mapping.
- **`i_overlay` (iShape-Rust) HAS a built-in offset API:** `StrokeOffset` (LineCap: Butt/Square/Round/Custom), `OutlineOffset` + `OutlineStyle`, `LineJoin::Round(tolerance)` / Bevel / Miter. Dual MIT/Apache-2.0. Very active (v6.0.0, May 2026). Powers `geo`'s boolean ops. Recommends `simplify_shape` first for validity.
- **`cavalier_contours`:** parallel offset of polylines with line+arc segments; ONLY rounded joins supported; uses TRUE arc segments (bulge values), not flattened polylines; dual MIT/Apache; active (v0.7.0, Jan 2026).
- **`geo-buffer`:** straight-skeleton buffering; miter or round joins; Polygon/MultiPolygon only.
- **`geo` core:** native buffer/offset historically NOT in core (issue #641); offset lives in companion crates.

## Related Topics by Relevance

| Relevance | Topic | Why Related |
|-----------|-------|-------------|
| High | Clipper2 `clipper.offset.cpp` `DoRound`/`OffsetPoint` internals | This is the literal algorithm KiCad's zone filler delegates to; matching it is the surest path to byte-fidelity. Covers start angle, angular step, rotation-matrix vertex placement. |
| High | KiCad `ZONE_FILLER` + `SHAPE_POLY_SET::Inflate`/`Deflate` + `corner_strategy.h` | Shows exactly how KiCad calls the offsetter for zones: sign of delta, corner strategy, `aMaxError`, and the deflate-then-inflate (thermal/spoke) sequencing that shapes round corners. |
| High | `i_overlay` `StrokeOffset`/`OutlineOffset`/`OutlineStyle`/`LineJoin::Round` API | Most promising permissive Rust crate WITH a built-in offset API and explicit round-join tolerance; directly answers root-topic part (3). |
| High | `GetArcToSegmentCount` ↔ ArcTolerance equivalence (max-error → step count) | The conversion bridge: KiCad expresses precision as max chord error; Clipper as ArcTolerance. To match KiCad output you must reproduce its segment-count rule, not just "round joins". |
| Medium | `cavalier_contours` arc-aware offset (bulge/true-arc representation) | Alternate model that keeps arcs as arcs instead of flattening — relevant if kiclaude wants exact arcs, but mismatches KiCad's flattened-to-segments zone polygons. License/maintenance check needed. |
| Medium | Integer (nm) vs floating-point coordinates in offset fidelity | KiCad uses int64 nm; Clipper2 rounds to integers. Choosing f64 in Rust can diverge vertex-for-vertex from KiCad. Affects which crate/precision mode reproduces KiCad exactly. |
| Medium | Numerical robustness / self-intersection handling in polygon offset | Why hand-rolled Minkowski-union offsetters are fragile (sliver faces, self-intersections, holes); motivates using a hardened library and `simplify_shape`/`Fracture`/`Unfracture` passes. |
| Medium | `geo` crate buffering status (issue #641, PRs, straight-skeleton) + `geo-buffer`/`geo-offset`/`geo-buf` | Maps the rest of the permissive Rust ecosystem and whether `geo` core ever shipped offset; clarifies which companion crate to depend on and its join/tolerance support. |
| Low | `clipper2` Rust crate (FFI/binding to Angus Johnson's C++) | A binding would give bit-identical results to KiCad but carries C++ build + (possibly) Boost-license nuance and FFI cost; verify license is Boost/MIT not GPL, and binding maintenance. |
| Low | Minkowski sum / disc-convolution offset theory | Background theory on offsetting as Minkowski sum with a disc; explains why round joins arise and why arc tolerance exists; useful for validating correctness, low for implementation choice. |

## Agent Query Angle Assignments

Agent 1 — Core offset algorithm: Reverse-engineer exactly how Clipper2 `ClipperOffset` and KiCad's `SHAPE_POLY_SET::Inflate`/`Deflate` build `jtRound`/`ROUND_ALL_CORNERS` arcs. Pin down: the `steps_per_360 = min(PI/acos(1 - arcTol/Δ), Δ·PI)` formula, `arc_const = 0.002` default, `steps_per_rad_`, `DoRound` rotation-matrix vertex placement (sin/cos increment), the starting offset vector / angle, how the sweep `angle` is derived from edge normals (`atan2(sin_a, cos_a)`), and inscribed-vs-on-arc placement. Primary sources: `clipper.offset.cpp`/`.h` (raw GitHub), Clipper2 DeepWiki, KiCad `shape_poly_set.cpp` / `geometry_utils.cpp` source.

Agent 2 — Permissive Rust crate ecosystem for polygon offsetting: For EACH of `i_overlay`, `cavalier_contours`, `geo`/`geo-buffer`/`geo-offset`/`geo-buf`, and the `clipper2` binding crate, record: exact license (must be MIT/Apache/BSL — flag any GPL), round-join support, arc-tolerance/max-error config knob, true-arc vs flattened-segment output, integer vs float coords, maintenance status + latest version + author, API shape, and whether it replaces a Minkowski-union/hand-rolled offset. Produce a comparison matrix.

Agent 3 — Pitfalls and numerical robustness: Why hand-rolled offsetters are fragile (self-intersections, slivers, hole handling, collapsing edges at large deflate); floating-point f64 vs KiCad's integer-nm coordinates and the vertex-divergence risk; rounding/snapping; the `simplify_shape`/`Fracture`/`Unfracture` and "valid polygon" preconditions; how arc-tolerance interacts with coordinate rounding (Clipper note: tolerances below 0.25 pointless once snapped to int). Sources: Clipper2 issues (e.g. #934 round-join spike), iOverlay validity docs, georust offset discussions, robustness papers.

Agent 4 — `i_overlay`/iShape ecosystem offset API + `geo` buffering status: Deep-dive `i_overlay`'s `StrokeOffset`, `OutlineOffset`, `OutlineStyle`, `LineJoin::Round(tolerance)`, `LineCap` — concrete API signatures, examples, how tolerance maps to arc steps, and whether output matches a KiCad-style flattened arc. Catalog the iShape-Rust ecosystem (`i_shape`, `i_float`, `i_tree`, `i_key_sort`, fixed-point `i_float`). Separately, confirm `geo` core buffer/offset status (issue #641, any merged PR, straight-skeleton work) and the state of `geo-buffer`/`geo-offset`/`geo-buf`. Author attribution: Nail Sharipov / iShape.

## Suggested Search Queries per Agent

### Agent 1 Queries
- "Clipper2 clipper.offset.cpp DoRound steps_per_rad_ arc_tolerance source"
- "Clipper2 OffsetPoint atan2 sin_a cos_a edge normal sweep angle round join"
- "Clipper2 steps_per_360 PI acos 1 - arcTol abs_delta formula"
- "KiCad geometry_utils.cpp GetArcToSegmentCount MIN_SEGCOUNT_FOR_CIRCLE max error"
- "KiCad shape_poly_set.cpp Inflate inflate2 CORNER_STRATEGY ROUND_ALL_CORNERS"
- "KiCad ZONE_FILLER deflate inflate round corners thermal spoke arc segments"
- "ClipperOffset round join arc chord inscribed vertex placement starting angle"

### Agent 2 Queries
- "i_overlay rust crate license OutlineOffset StrokeOffset OutlineStyle version"
- "cavalier_contours rust parallel_offset license bulge arc segment round join only"
- "geo-buffer rust crate license straight skeleton miter round join Polygon"
- "geo-offset geo-buf rust crate license round join arc tolerance maintenance"
- "clipper2 rust crate crates.io binding license Boost Angus Johnson FFI"
- "rust polygon offset crate comparison round join arc tolerance MIT Apache 2026"
- "geo crate offset buffer companion crate which to use 2026"

### Agent 3 Queries
- "polygon offset self-intersection robustness Minkowski union slivers"
- "Clipper2 issue round join spike artifact offset 934"
- "i_overlay simplify_shape valid polygon offset precondition"
- "floating point vs integer coordinates polygon offset fidelity nanometer"
- "polygon deflate collapsing edges holes negative offset robustness"
- "hand rolled polygon offset fragile why use library georust"
- "arc tolerance integer rounding offset coordinates snap precision"

### Agent 4 Queries
- "iOverlay StrokeOffset OutlineOffset LineJoin Round tolerance LineCap example"
- "iShape-Rust i_shape i_float i_tree i_key_sort ecosystem crates Nail Sharipov"
- "i_overlay buffer offset example rust round join arc steps documentation"
- "georust geo issue 641 buffering offset straight skeleton merged status 2026"
- "geo-buffer geo-offset geo-buf differences rust which maintained round join"
- "i_float fixed point integer coordinates iShape robust boolean offset"
- "i_overlay powers geo crate boolean operations offset outline rust"
