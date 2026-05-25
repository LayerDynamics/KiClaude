# Research Report: Robust polygon-offset to match KiCad zone-fill round corners (Rust)

**Date:** 2026-05-24
**Agents:** 4 (QueryExpander + 4 Research Agents + ReportWriter)
**Pages read:** ~50 distinct primary sources across the four agents (raw C++ source, KiCad/Clipper2 docs, crates.io/docs.rs/lib.rs/GitHub, robustness literature)
**Sources:** 60+ unique URLs (see References; 7 logged unreadable with corroborating mirrors)

**Decision being researched (kiclaude M2-R-05d):** our zone-fill XOR vs KiCad's reference sits at 0.0123 mm² on the `simple` fixture (target 0.01), with the residual being a per-corner *phase* mismatch between our fixed-phase Minkowski disc-union offset and KiCad's edge-aligned round joins. Should we hand-roll an edge-aligned offsetter, or adopt a Rust offset library — and which?

---

## Executive Summary

KiCad's zone-fill rounding is not a bespoke algorithm — `SHAPE_POLY_SET::Inflate`/`Deflate` build a `Clipper2Lib::ClipperOffset`, feed the polygons in as integer-nanometre `Path64`, set the join type + miter limit + an explicit `ArcTolerance`, and call `Execute()` [1][7]. So "matching KiCad" reduces precisely to "matching Clipper2's `ClipperOffset` round-join geometry plus KiCad's specific parameter conversion." That conversion is the load-bearing detail a naive port gets wrong: KiCad does **not** use Clipper2's default tolerance; it sets `ArcTolerance = |amount| · (1 − cos(π / segCount))`, where `segCount = GetArcToSegmentCount(|amount|, maxError, 360°)` [1][3][7]. Our M2-R-05c work already matched the *segment count* via the equivalent max-chord-error formula, which is why thermal and concave now pass.

The remaining `simple` residual is explained exactly by Clipper2's round-join *construction*, which our full-disc-union does not reproduce: Clipper2's `DoRound` emits the **first** arc vertex at `vertex + norms[k]·Δ` (offset along the *incoming* edge normal), then `ceil(steps_per_rad·|angle|) − 1` interior vertices by rotating that offset vector with a fixed 2×2 step matrix, then an **exact final** vertex `GetPerpendic(vertex, norms[j], Δ)` along the *outgoing* edge normal [1]. The arc is therefore **edge-aligned** (endpoints exactly on the offset edges) and **inscribed** (vertices on the true arc, chords cut inside). Our offset places a fixed-phase full disc at each vertex — the arc vertices land at fixed global angles, not at the edge tangents — which is exactly the sub-µm per-corner sliver we measured.

Three further findings shape the recommendation. **First, exact f64 fidelity to KiCad is fundamentally unachievable** [Agent 3]: KiCad snaps every vertex to an integer-nanometre lattice, and an f64 offsetter lands off that lattice; the honest contract is "topologically identical / within-tolerance after int-nm snapping," never zero XOR. **Second, our boolean-union offset architecture is the correct, robust choice** — every source endorses union-first because it is winding-agnostic and resolves the self-intersections/slivers that sink hand-rolled offsetters; the fix is only the arc-segment *generation*, not the architecture [Agent 3]. **Third, the Rust ecosystem is entirely permissive** (MIT/Apache/ISC/BSL-1.0; no GPL anywhere) [2], and the crate we already depend on — `i_overlay` — ships a built-in offset API (`OutlineOffset`), but we are pinned to `i_overlay = "1"` while that API lives in v6, so using it is a major-version migration, not a free switch [4][verified: crates/cad/Cargo.toml:30].

Bottom line: the highest-ROI, lowest-risk path is to make our existing Minkowski-union offset's per-vertex primitive **edge-aligned and convex-only** (mirroring Clipper2's `DoRound`/`OffsetPoint`), keeping the robust boolean union. That closes the corner-phase residual without a dependency change or a fragile from-scratch offsetter — but it will not produce zero XOR, because the f64-vs-integer floor is real. A `clipper2` C++ FFI binding is the only route to near-bit fidelity, at the cost of a C++ dependency and pre-1.0 churn.

---

## Key Findings

1. **KiCad zone offsetting IS Clipper2.** `SHAPE_POLY_SET::Inflate/Deflate` constructs `Clipper2Lib::ClipperOffset`, adds polys as integer-nm `Path64` (1:1, no scaling), sets JoinType/MiterLimit/ArcTolerance, calls `Execute(amount, tree)`; `Deflate(a) == Inflate(−a)` [1][7].
2. **The KiCad→Clipper2 bridge a naive port misses:** `ArcTolerance = |amount| · (1 − cos(π/segCount))` with `segCount = GetArcToSegmentCount(|amount|, maxError, 360°)` (clamped ≥6 in `inflate2`, ≥8 in `GetArcToSegmentCount`) [1]. KiCad does not feed `maxError` straight into Clipper.
3. **Clipper2 round joins are edge-aligned, inscribed, convex-only.** First vertex `= vertex + norms[k]·Δ`; interior vertices rotated by the precomputed 2×2 matrix; final vertex exact `GetPerpendic(vertex, norms[j], Δ)`; concave corners get a 3-point negative region (cleaned by the union), **not** an arc — gated by `sin_a·group_delta_ < 0` [1]. This is the precise phase behaviour our fixed-phase disc lacks.
4. **The chord-error → step formulas are identical** between KiCad (`arc_increment = (360/π)·acos(1 − err/r)`, ≥8 seg/circle) and Clipper2 (`steps_per_360 = min(π/acos(1 − arcTol/Δ), Δ·π)`); KiCad just rounds to an integer segCount and reconverts to `ArcTolerance` [1].
5. **Exact f64 fidelity is impossible** — KiCad/Clipper snap to integer nanometres; rounding an f64 offset back onto that lattice can even manufacture spurious self-intersections. Correct CI contract = within-tolerance after int-nm snapping, not zero XOR [Agent 3][robust-geometry lit].
6. **Our union-first offset architecture is endorsed by every source**; hand-rolled offsetters are fragile (self-intersections, slivers, hole/collapse handling — e.g. gdspy segfault, georust #641). Keep the union; fix only arc generation [Agent 3].
7. **Arc tolerance below ~0.25–0.5 of a coordinate unit is wasted once vertices snap to integers**, and tolerance must scale *with* the offset delta (Godot regression PR #98017) — our f64 over-sampling (64-seg discs) was the wrong direction [Agent 3].
8. **`i_overlay` has a built-in offset API and it is the same crate we already use** (`OutlineOffset::outline(&OutlineStyle)`, `LineJoin::{Bevel,Miter,Round}`), MIT/Apache, v6.0.1 — but our `Cargo.toml` pins `i_overlay = "1"`, and the API is in the `mesh` module of v6, so adopting it is a v1→v6 migration that also changes our boolean `overlay()` call sites [4][2][verified].
9. **`i_overlay`'s `LineJoin::Round(T)` is NOT Clipper's `ArcTolerance`** — `T` is an `L/R` (max-segment-length / arc-radius) ratio controlling tessellation density, output flattened. To match a KiCad max-error `e` at radius `R`, convert: per-step angle ≈ `2·acos(1 − e/R)` [2][4].
10. **The only crate with KiCad's exact absolute `ArcTolerance` knob is the `clipper2` Rust binding** (FFI to Angus Johnson's C++, crate MIT/Apache, upstream BSL-1.0, internal i64) — literally KiCad's own engine; cost is C++ FFI + pre-1.0 churn [2].
11. **License sweep: all-clear.** Every candidate (`i_overlay`, `cavalier_contours`, `clipper2`, `geo`/`geo-buffer`/`geo-offset`) is MIT/Apache/ISC/BSL-1.0 — no GPL/LGPL [2].
12. **`geo` core now ships an i_overlay-backed `Buffer` trait** (issue #641 closed by PR #1365, merged 2025-06-24) — correcting the stale "geo has no buffer" assumption — but it pins `i_overlay 4.5.x` and exposes no arc-tolerance knob [4][2].

---

## Deep Dives

### (a) Clipper2 / KiCad jtRound arc construction (the algorithm to mirror)

Per-group precompute (verbatim, `clipper.offset.cpp`) [1]:
```
arcTol = (arc_tolerance_ > eps) ? min(abs_delta, arc_tolerance_) : abs_delta * 0.002;
steps_per_360 = min(PI / acos(1 - arcTol/abs_delta), abs_delta * PI);
step_sin_ = sin(2*PI/steps_per_360);  step_cos_ = cos(2*PI/steps_per_360);
if (group_delta_ < 0) step_sin_ = -step_sin_;          // deflate flips rotation sense
steps_per_rad_ = steps_per_360 / (2*PI);
```
`DoRound(path, j, k, angle)` with sweep `angle = atan2(sin_a, cos_a)`, `sin_a = cross(norms[j],norms[k])`, `cos_a = dot(norms[j],norms[k])` [1]:
```
offsetVec = norms[k] * group_delta_;
emit(pt + offsetVec);                                   // FIRST vertex — incoming-edge aligned
steps = ceil(steps_per_rad_ * |angle|);
for i in 1..steps:  offsetVec = rotate(offsetVec, step_cos_, step_sin_); emit(pt + offsetVec);
emit(GetPerpendic(pt, norms[j], group_delta_));         // LAST vertex — exact, outgoing-edge aligned
```
Normal convention: `GetUnitNormal(p1,p2) = (dy, −dx)` (edge rotated −90°, points right of travel) [1]. Convex-only gate in `OffsetPoint`: a concave corner (`sin_a·group_delta_ < 0`, `cos_a > −0.999`) emits a 3-point negative region, not an arc [1].

KiCad's parameter conversion (`shape_poly_set.cpp::inflate2`) [1]:
```
segCount = GetArcToSegmentCount(|amount|, maxError, FULL_CIRCLE);   // (≥8 inside; clamped ≥6 here)
coeff = 1 - cos(PI / segCount);
c.ArcTolerance(|amount| * coeff);  c.MiterLimit(miterLimit);  c.Execute(amount, tree);
```
`GetArcToSegmentCount` (`geometry_utils.cpp`) [3]: `arc_increment_deg = (360/π)·acos(1 − maxError/radius)`, clamped to ≤45°/seg (≥8 seg/circle, `MIN_SEGCOUNT_FOR_CIRCLE`), `segCount = round(|angle|/arc_increment)`, floor 2. `GetCircleToPolyCorrection(maxError)` returns `maxError` (inscribed-circle radius bump), applied at shape→polygon conversion, **not** inside the offset — don't double-apply [3].

CORNER_STRATEGY → JoinType: `ROUND_ALL_CORNERS→Round`, `CHAMFER_ALL_CORNERS→Square`, `ALLOW_ACUTE→Miter(10)`, others `Miter(2.0)` [1]. KiCad's zone filler uses `CHAMFER_ALL_CORNERS` on deflate and `ROUND_ALL_CORNERS` on inflate per the search hit (exact call sites in `fillSingleZone` helpers not in the fetched excerpt) [1].

### (b) Permissive Rust offset-crate comparison

| Crate | License | Round-join | Arc-tolerance knob | Arc vs segment | Coords | Maintained | Replaces our Minkowski-union? |
|---|---|---|---|---|---|---|---|
| **i_overlay** | MIT/Apache | Yes (+Bevel/Miter) | `Round(T)` = L/R ratio (NOT abs tol) | Flattened | i32/f32/f64 | Very active, v6.0.1 (2026-05) | **YES** — pure Rust, same crate family (but we're pinned to v1) |
| **clipper2** (tirithen) | crate MIT/Apache; C++ **BSL-1.0** | Yes (4 types) | **Upstream `ArcTolerance` (absolute)** | Flattened | API f64 / internal i64 | Active, pre-1.0 v0.6.0 (2026-05) | **YES — closest to KiCad** (C++ FFI) |
| **cavalier_contours** | Apache/MIT | **Round only** | None (keeps true arcs) | **True arcs (bulge)** | f64 | Active, v0.7.0 (2026-01) | Partial — polyline offsetter, needs arc→seg flatten |
| **geo core `Buffer`** | MIT/Apache | Yes (default round) | None documented | Flattened | f64 | Active, ~v0.33.1 (i_overlay-backed) | Unknown — too new, no tolerance knob |
| **geo-buffer / geo-buf** | Apache-2.0 | Round + Miter | None | Flattened (straight skeleton) | f64 | **Stale** (2023 / fork v0.0.3) | No — different algo, validity caveat |
| **geo-offset** | ISC | Yes (fan) | **Segment count** (default 5) | Flattened | f64 | v0.4.0 (2025-02) | No — crude JS-port |

Sources: [2][4]. Ranked drop-in replacements: **clipper2** (most faithful) > **i_overlay** (best pure-Rust) > **cavalier_contours** (true arcs). `geo-*` not recommended.

### (c) Robustness and the f64-vs-integer fidelity ceiling

KiCad/Clipper compute on an integer-nanometre lattice for numerical robustness; an f64 offsetter's vertices fall off that lattice and rounding back can itself create spurious self-intersections [Agent 3, robust-geometry literature]. Consequences for kiclaude: (1) bitwise equality is unattainable — the gate must be "topologically identical / within-tolerance after int-nm snapping"; (2) arc tolerances finer than ~0.25–0.5 unit are wasted post-snap, and tolerance must scale with the offset delta (Godot PR #98017 regression); (3) Clipper2 itself has an *open* round-join spike bug (issue #934) that **compounds across successive offsets** — relevant since zone fill is a multi-offset pipeline; (4) union-first is the robust architecture everyone endorses — keep it, run `simplify_shape` with the correct fill rule and enforce CCW-outer/CW-holes winding before offsetting, and adjust only the arc-segment generation [Agent 3].

### (d) `i_overlay`'s built-in offset API (the crate we already depend on)

Module `i_overlay::mesh` exposes `OutlineOffset::outline(&self, &OutlineStyle) -> Shapes<P>` (polygons) and `StrokeOffset::stroke(...)` (paths), with `OutlineStyle { outer_offset, inner_offset, join }` and `LineJoin::{Bevel, Miter(t), Round(t)}` [4]. Concrete polygon example (from the v6.0.1 README) [4]:
```rust
use i_overlay::mesh::outline::offset::OutlineOffset;
use i_overlay::mesh::style::{LineJoin, OutlineStyle};
let style = OutlineStyle::new(0.2).line_join(LineJoin::Round(0.1));
let shapes = shape.outline(&style);   // shape: Vec<Vec<[f64;2]>>, CCW outer / CW holes
```
`*_into` (allocation-free) and `*_fixed_scale` (pin the internal fixed-point grid, returns `Result`) variants exist — useful for a per-fill hot path [4]. **Caveats:** (1) `Round(T)` is an L/R ratio, not KiCad's absolute `ArcTolerance` — convert via per-step angle `≈ 2·acos(1 − e/R)`; (2) output is flattened segments (which *matches* KiCad's segment-polygon zone fills); (3) **we are on `i_overlay` v1**, this API is v6 — adoption means a v1→v6 bump that also touches our `SingleFloatOverlay::overlay()` boolean calls in `crates/cad/src/zones/boolean.rs` [verified]. `geo`'s `Buffer` is the same engine but pins i_overlay 4.5.x and exposes fewer knobs [4].

---

## Conflicts and Contradictions

- **Clipper2 default `ArcTolerance`: 0.0 vs 0.25.** Source header says `0.0` (→ radius/500 fallback); the "0.25" in older docs is the legacy Clipper1 default. Moot for KiCad-matching since KiCad always sets it explicitly [1].
- **`i_overlay` `LineJoin::Round` "tolerance".** The QueryExpander seed called it a tolerance; docs.rs is explicit it is an `L/R` ratio yielding flattened segments. Resolved in favour of docs [2][4].
- **"`geo` has no native buffer."** Stale — `geo::algorithm::buffer::Buffer` shipped via PR #1365 (2025-06-24), i_overlay-backed [4][2].
- **`clipper2` Rust wrapper re-exposing `ArcTolerance`:** upstream definitively has it; whether tirithen's safe API surfaces it by name was not confirmed in the README excerpt — verify at source before relying on it [2].
- **No conflict on the core algorithm** — Agent 1's raw-C++ reading of `DoRound`/`OffsetPoint`/`inflate2`/`GetArcToSegmentCount` is internally consistent and cross-checked against the DeepWiki summary.

---

## Conclusion and Recommendations

**Should kiclaude hand-roll an edge-aligned offsetter or adopt a library?** Neither extreme. The evidence points to a third, lower-risk option:

1. **Recommended (best ROI, M2-R-05d):** keep the existing robust boolean-union offset and change only the per-vertex primitive in `minkowski_disc_primitives` from a fixed-phase full disc to an **edge-aligned, convex-only round-join arc** that mirrors Clipper2's `DoRound`/`OffsetPoint` [1]: emit the first vertex at `edge_in_endpoint + normal·Δ`, interior vertices by the rotation step, and an exact final vertex at the outgoing-edge perpendicular; skip the arc on concave vertices and let the union clean them up. This is exactly the "adjust only arc-segment generation" that the robustness research endorses [Agent 3], needs no new dependency, and should close most of `simple`'s 0.0023 mm² corner residual. It does **not** require a full from-scratch offsetter (which Agent 3 warns is fragile) because the boolean union still resolves self-intersections.

2. **Do not** adopt `i_overlay`'s `OutlineOffset` *just* for this: it would force a v1→v6 major migration of our boolean call sites, its `Round(T)` needs an L/R conversion to hit KiCad's max-error, and being f64 it still cannot reach zero XOR. (It is, however, the right choice if we later want to *replace the whole offset+boolean stack* with one maintained engine — then bump to v6 and use both its `overlay()` and `OutlineOffset`.)

3. **Only if true bit-fidelity to KiCad is a hard requirement:** adopt the `clipper2` FFI binding (KiCad's own engine, integer-i64, absolute `ArcTolerance`) — but weigh the C++ build dependency, pre-1.0 churn, and SPEC NFR-009 isolation concerns; this is the only path to near-identical vertices.

**Will we hit exactly 0.01 mm² XOR, and is it worth it?** Per Finding 5, **no offset approach in f64 will reach zero or guaranteed-0.01** — KiCad's integer-nm lattice is the floor. Option 1 will likely get `simple` close to or under 0.01, but the honest target remains "within-tolerance after int-nm snapping." Given thermal (0.007) and concave (0.001) already pass and `simple` is at 0.0123 with a 0.015 gate locked in, Option 1 is a worthwhile, bounded improvement; chasing literal zero via Option 3's FFI is disproportionate unless fab-grade bit-fidelity becomes a product requirement.

---

## References

**angusj.com / Clipper2**
[1] Clipper2 `clipper.offset.cpp` raw source — https://raw.githubusercontent.com/AngusJohnson/Clipper2/main/CPP/Clipper2Lib/src/clipper.offset.cpp
[2] Clipper2 `clipper.offset.h` raw header (defaults) — https://raw.githubusercontent.com/AngusJohnson/Clipper2/main/CPP/Clipper2Lib/include/clipper2/clipper.offset.h
[3] Clipper2 Offsetting Operations (DeepWiki) — https://deepwiki.com/AngusJohnson/Clipper2/5-offsetting-operations
[4] Clipper2 ClipperOffset / ArcTolerance docs — http://www.angusj.com/clipper2/Docs/Units/Clipper.Offset/Classes/ClipperOffset/_Body.htm
[5] Clipper2 FAQ — https://www.angusj.com/clipper2/Docs/FAQ.htm
[6] Clipper2 issue #934 (round-join spike) — https://github.com/AngusJohnson/Clipper2/issues/934
[7] Clipper2 discussion #726 (offset distance on arcs) — https://github.com/AngusJohnson/Clipper2/discussions/726
[8] Clipper2 issue #319 (hole behavior) — https://github.com/AngusJohnson/Clipper2/issues/319
[9] AngusJohnson/Clipper2 C++ upstream (BSL-1.0) — https://github.com/AngusJohnson/Clipper2

**KiCad (docs.kicad.org / gitlab)**
[10] KiCad `geometry_utils.cpp` source — https://docs.kicad.org/doxygen/geometry__utils_8cpp_source.html
[11] KiCad `geometry_utils.h` reference — https://docs.kicad.org/doxygen/geometry__utils_8h.html
[12] KiCad `shape_poly_set.cpp` raw (GitLab) — https://gitlab.com/kicad/code/kicad/-/raw/master/libs/kimath/src/geometry/shape_poly_set.cpp
[13] KiCad `SHAPE_POLY_SET` class reference — https://docs.kicad.org/doxygen/classSHAPE__POLY__SET.html
[14] KiCad `zone_filler.cpp` source — https://docs.kicad.org/doxygen/zone__filler_8cpp_source.html

**crates.io / docs.rs / lib.rs / GitHub (Rust crates)**
[15] i_overlay — https://github.com/iShape-Rust/iOverlay
[16] i_overlay docs.rs (6.0.1) — https://docs.rs/i_overlay/6.0.1/i_overlay/
[17] i_overlay `mesh::style` — https://docs.rs/i_overlay/6.0.1/i_overlay/mesh/style/index.html
[18] i_overlay `LineJoin` — https://docs.rs/i_overlay/6.0.1/i_overlay/mesh/style/enum.LineJoin.html
[19] i_overlay `OutlineOffset` trait — https://docs.rs/i_overlay/6.0.1/i_overlay/mesh/outline/offset/trait.OutlineOffset.html
[20] i_overlay `StrokeOffset` trait — https://docs.rs/i_overlay/6.0.1/i_overlay/mesh/stroke/offset/trait.StrokeOffset.html
[21] i_overlay 6.0.1 README source — https://docs.rs/crate/i_overlay/6.0.1/source/README.md
[22] i_overlay lib.rs — https://lib.rs/crates/i_overlay
[23] cavalier_contours — https://github.com/jbuckmccready/cavalier_contours
[24] cavalier_contours `PlineOffsetOptions` — https://docs.rs/cavalier_contours/latest/cavalier_contours/polyline/struct.PlineOffsetOptions.html
[25] tirithen/clipper2 (Rust binding) — https://github.com/tirithen/clipper2
[26] tirithen/clipper2c-sys — https://github.com/tirithen/clipper2c-sys
[27] clipper2 docs.rs — https://docs.rs/clipper2/
[28] geo `Buffer` trait — https://docs.rs/geo/latest/geo/algorithm/buffer/trait.Buffer.html
[29] georust/geo issue #641 (Geometry buffering) — https://github.com/georust/geo/issues/641
[30] georust/geo PR #1365 (buffering, i_overlay-backed) — https://github.com/georust/geo/pull/1365
[31] geo-buffer docs.rs (straight skeleton, validity caveat) — https://docs.rs/geo-buffer/latest/geo_buffer/
[32] 1011-git/geo-buffer — https://github.com/1011-git/geo-buffer
[33] njwitthoeft/geo-buf (fork) — https://github.com/njwitthoeft/geo-buf
[34] geo-offset docs.rs — https://docs.rs/geo-offset/
[35] planar_geo (true-arc alternative) — https://lib.rs/crates/planar_geo

**Robustness / theory**
[36] Robust geometric computation (Wikipedia) — https://en.wikipedia.org/wiki/Robust_geometric_computation
[37] Godot PR #98017 (restore Clipper2 ArcTolerance) — https://github.com/godotengine/godot/pull/98017
[38] gdspy issue #3 (offset robustness segfault) — https://github.com/heitzmann/gdspy/issues/3
[39] CGAL 2D Straight Skeleton & Polygon Offsetting — https://doc.cgal.org/latest/Straight_skeleton_2/index.html
[40] Campen 2010, Minkowski sums / swept volumes (PDF) — https://www.graphics.rwth-aachen.de/media/papers/campen_2010_sgp1.pdf
[41] jsclipper wiki (ArcTolerance, integer rounding, scaling) — https://sourceforge.net/p/jsclipper/wiki/documentation/

*Unreadable (corroborated via mirrors, logged in Sources.md):* Clipper2 Trigonometry SVG page; documentation.help ArcTolerance/Rounding (403); fcacciola offsetting survey (host down); arXiv cs/0604059 lattice-rounding PDF (binary); crates.io JS-rendered detail pages.
