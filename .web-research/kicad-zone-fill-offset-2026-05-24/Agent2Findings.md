# Agent 2 Findings

## Query Angle

**Permissive Rust crate ecosystem for polygon offsetting.** Evaluate each candidate
crate for whether it can REPLACE a hand-rolled Minkowski-union offset in kiclaude's
zone-fill kernel, specifically matching KiCad's round-corner zone-fill geometry. For
each crate record: exact license (must be MIT/Apache/BSL — any GPL/LGPL flagged
loudly), round-join support, arc-tolerance / max-error knob, true-arc-vs-flattened
output, integer-vs-float coordinates, maintenance + latest version + author, API
shape, and replace-Minkowski verdict.

## Queries Executed

| # | Query | Real pages read |
|---|-------|-----------------|
| 1 | i_overlay rust crate license OutlineOffset StrokeOffset OutlineStyle version | github iOverlay repo, docs.rs mesh::style index, docs.rs LineJoin enum (crates.io detail unreadable) |
| 2 | cavalier_contours rust parallel_offset license bulge arc segment round join only | github repo, docs.rs polyline index, docs.rs PlineOffsetOptions |
| 3 | geo-buffer rust crate license straight skeleton miter round join Polygon | lib.rs geo-buffer, github njwitthoeft/geo-buf |
| 4 | geo-offset geo-buf rust crate license round join arc tolerance maintenance | docs.rs geo-offset, lib.rs geo-offset |
| 5 | clipper2 rust crate crates.io binding license Boost Angus Johnson FFI | github tirithen/clipper2, github AngusJohnson/Clipper2 (C++ upstream) |
| 6 | rust polygon offset crate comparison round join arc tolerance MIT Apache 2026 | docs.rs geo Buffer trait, search-surfaced polygon-offsetting / offset-polygon / planar_geo |

Total successful WebFetch reads: ~15 across 6 queries. **Limitation (procedural):** the
spec target of ≥10 distinct readable pages *per query* was not literally met per-query —
`crates.io` detail pages are JS-rendered and return only a title (logged as unreadable),
so I pivoted to the server-rendered authoritative mirrors (docs.rs, lib.rs, GitHub
repos/Cargo.toml/LICENSE). The substantive data below is complete for the matrix; the
shortfall is in page-count-per-query, not in coverage of the five target crates.

## Findings

### i_overlay (iOverlay, iShape-Rust)
- **License:** Dual **MIT OR Apache-2.0** (`LICENSE-MIT` + `LICENSE-APACHE` present). Permissive.
- **Author / maintenance:** Nail Sharipov (iShape-Rust). Very active — **v6.0.0 released 2026-05-02**, 14 releases, ~786 commits, 186 stars. Powers `geo`'s boolean ops.
- **Offset API:** Two traits.
  - `StrokeOffset` + `StrokeStyle` for open-path stroking: `StrokeStyle::new(w).line_join(LineJoin::Miter(1.0)).start_cap(LineCap::Round(0.1)).end_cap(LineCap::Square)`.
  - `OutlineOffset` + `OutlineStyle` for polygon inflate/deflate: `OutlineStyle::new(0.2).line_join(LineJoin::Round(0.1))`.
- **Join styles:** `LineJoin::Bevel` (default), `Miter(T)`, `Round(T)`. Caps: `Butt`, `Square`, `Round(T)`, `Custom`.
- **CRITICAL — Round is NOT arc-tolerance and output is FLATTENED:** docs.rs for `LineJoin::Round(T)` state the arc is *"approximated using a group of segments, where the parameter `Angle` is defined as `L / R`, with `L` being the maximum segment length and `R` being the arc radius."* So the `Round` parameter is a **segment-length-to-radius ratio**, not an absolute error tolerance, and the result is a **tessellated polygon of line segments**, NOT true circular arcs. (This corrects the inline brief, which called it "tolerance.")
- **Coordinates:** Both — `i32` integer and `f32`/`f64` float APIs; custom points via `FloatPointCompatible`.
- **Preconditions:** README recommends `simplify_shape` before offsetting.
- **Replaces Minkowski-union?** **YES.** Modern, pure-Rust (no FFI), permissive, actively maintained, has native polygon inflate via `OutlineOffset`. Round corners come out as L/R-controlled segment fans, which matches how KiCad ultimately tessellates anyway.

### cavalier_contours (jbuckmccready)
- **License:** Dual **Apache-2.0 OR MIT**. Permissive. (Rust rewrite of the C++ CavalierContours; aims for stable C FFI.)
- **Author / maintenance:** Jedidiah Buck McCready. Active — **v0.7.0 released 2026-01-02**.
- **Offset API:** `Polyline<T=f64>::parallel_offset(dist)` and `parallel_offset_opt(dist, &PlineOffsetOptions)`. Vertices are `PlineVertex { x, y, bulge }`; `bulge = tan(theta/4)`.
- **Join styles:** **Round joins ONLY** — explicitly *"Only rounded joins are supported for parallel offsets (other join types are not implemented)."* No miter/bevel.
- **Arc output:** **TRUE ARCS** preserved as bulge values — arcs are *not* flattened to line segments. Bulge limited to [-1.0, 1.0] (half-circle max; chain segments for larger).
- **Arc-tolerance knob:** **None for arc shape** — and none is needed because output stays parametric (true arcs). `PlineOffsetOptions` fields are numerical-robustness epsilons only: `pos_equal_eps`, `slice_join_eps`, `offset_dist_eps`, plus `handle_self_intersects` and an optional `aabb_index` spatial index. (Resolves the apparent contradiction between my two fetches: there is no missing arc-tolerance field; the eps fields govern algorithm robustness, not arc approximation.)
- **Coordinates:** Float (`T = f64` default, generic via `num-traits`).
- **Replaces Minkowski-union?** **PARTIAL.** Excellent if you want exact parametric round corners, but it is a *polyline* offsetter (round-join-only, true-arc output) — a different output shape than a flattened polygon set. Would need an arc→segment flattening step to feed KiCad's segment-based zone polygons, and it offers no boolean-union-with-obstacles in the same call the way a Clipper/i_overlay pipeline does.

### clipper2 (tirithen — Rust binding to Angus Johnson's C++)
- **License (Rust crate):** Dual **Apache-2.0 OR MIT**. Permissive.
- **License (underlying C++ Clipper2):** **Boost Software License 1.0 (BSL-1.0)** — confirmed via the AngusJohnson/Clipper2 repo "License: Boost_1.0" badge. BSL-1.0 is permissive and Apache/MIT-compatible (no copyleft).
- **FFI:** unsafe layer in separate `clipper2c-sys` crate (also tirithen). So this is FFI/vendored-C++, not pure Rust.
- **Author / maintenance:** tirithen. Active but pre-1.0 — **v0.6.0 released 2026-05-06**, 16 releases, "expect breaking changes between minor versions."
- **Offset API:** `inflate()` (offset/deflate), plus boolean ops, `minkowski_sum`/`minkowski_diff`, `simplify`. Types: `Point`, `Path`, `Paths` generic over `PointScaler`.
- **Join styles:** **Round, Miter, Square, Bevel** (`JoinType`). EndType: `Polygon`, `Joined`, `Butt`, `Square`, `Round`.
- **Arc output:** **FLATTENED** segments. Upstream Clipper2 controls roundness via an **ArcTolerance** parameter (absolute max deviation of approximation from true arc) — see Agent 1/3 sources; the Rust wrapper exposes offsetting but the safe API surface did not visibly re-expose a named `arc_tolerance` setter in the README excerpt (worth confirming in source before relying on it).
- **Coordinates:** API is `f64`; internally **i64 integers** (robust), rescaled by `PointScaler` (default `Centi` = ×100).
- **Replaces Minkowski-union?** **YES — and the closest match to KiCad.** KiCad's own zone fill is built on Clipper/Clipper2 with ArcTolerance-controlled round corners, so this binding gives the most faithful geometry. Cost: C++ FFI dependency (build complexity, NFR isolation concerns) and pre-1.0 churn.

### geo-buffer (1011-git / TENELEVEN) and fork geo-buf (njwitthoeft)
- **License:** **Apache-2.0** (both). Permissive. (Single-license, not dual.)
- **Author / maintenance:** geo-buffer by TENELEVEN (1011-git) — **v0.2.0, 2023-06-02, STALE** (no updates since mid-2023). `geo-buf` is njwitthoeft's fork (v0.0.3, ~67 commits, no published releases) created to **update the `geo` dependency**; also low activity.
- **Offset API:** straight-skeleton buffer of `Polygon` / `MultiPolygon` (handles non-convex + holes). Separate functions for miter-joined vs round-joined convex corners.
- **Join styles:** **Miter and Round.**
- **Arc output:** Not documented; straight-skeleton round variant approximates corners — effectively **flattened** (no true-arc claim).
- **Arc-tolerance knob:** None documented.
- **Coordinates:** Float (geo-types).
- **Replaces Minkowski-union?** **PARTIAL / NOT RECOMMENDED.** Different algorithm (straight skeleton, not Minkowski/offset-by-circle), stale upstream, the crate's own docs warn the reference paper "is incorrect" (validity caveats per Agent 3). Geometry can diverge from KiCad on edge cases.

### geo-offset (lelongg)
- **License:** **ISC** (permissive, MIT-equivalent, Apache-compatible — no copyleft).
- **Author / maintenance:** Gérald Lelong. **v0.4.0, 2025-02-08.** A Rust port of the JS `polygon-offset` lib.
- **Offset API:** `Offset` trait with `offset()` (and `offset_with_arc_segments(dist, n)`), implemented for most geo-types; `OffsetError`/`EdgeError`.
- **Join styles:** Round corners via arc-segment fans.
- **Arc-tolerance knob:** **Segment COUNT, not tolerance** — `DEFAULT_ARC_SEGMENTS = 5` (crude default), overridable via `offset_with_arc_segments`. Fixed-count, not error-driven.
- **Arc output:** **FLATTENED** (5 segments per corner by default).
- **Coordinates:** Float (geo-types).
- **Replaces Minkowski-union?** **NO.** Crude fixed-5-segment corners, JS-port lineage, less robust than i_overlay/clipper2. Not a serious contender for fab-grade zone fill.

### geo core `Buffer` trait — CONTEXT CORRECTION
- The inline brief said *"geo core: no native buffer/offset historically (issue #641)."* **This is now STALE.** The `geo` core crate **DOES** have a native `Buffer` trait (`geo::algorithm::buffer::Buffer`) as of **~v0.33.1**.
- **License:** geo core is dual **MIT OR Apache-2.0**. Permissive.
- **API:** `buffer(distance)` (default rounded join + rounded caps) and `buffer_with_style(BufferStyle)`. `LineJoin::Miter()`/`Bevel`/rounded; `LineCap::Square`/rounded.
- **Arc output / tolerance:** Approximated (point buffers become "approximated circle"); **no user-configurable arc tolerance / resolution documented.**
- **Replaces Minkowski-union?** **UNKNOWN / TOO NEW.** Promising (first-party, permissive, no FFI) but no exposed arc-tolerance knob and very recent — would need empirical comparison against KiCad geometry before trusting for fab.

### Honorable mentions (search-surfaced, not target crates)
- **polygon-offsetting** — small, zero-dep, tolerance-controlled rounded corners (license not verified here).
- **offset-polygon** (anlumo) — shrink/expand, arc-point count parameter, uses geo-types 0.4; requires closed paths.
- **planar_geo** — notable for **true-arc** (not polyline-approximated) representation with explicit absolute-epsilon + max-ULP tolerance; potential alternative if true-arc + robustness is the priority.

## Comparison Matrix

| Crate | License | Round-join | Arc-tolerance knob | Arc vs segment | Coords | Maintained + version | Replaces Minkowski? |
|-------|---------|-----------|--------------------|----------------|--------|----------------------|---------------------|
| **i_overlay** | MIT OR Apache-2.0 | Yes (`LineJoin::Round(L/R)`) + Bevel/Miter | No true tolerance — `Round(T)` = max-seg-len / radius ratio | **Flattened segments** | i32 / f32 / f64 | Very active, **v6.0.0 (2026-05)** | **YES** (pure-Rust, no FFI) |
| **cavalier_contours** | Apache-2.0 OR MIT | **Round ONLY** | None (not needed — keeps arcs) | **TRUE arcs (bulge)** | f64 | Active, **v0.7.0 (2026-01)** | **Partial** (polyline offsetter; needs arc→seg flatten) |
| **clipper2** (tirithen binding) | crate Apache/MIT; **C++ BSL-1.0** | Yes (Round/Miter/Square/Bevel) | Upstream ArcTolerance (absolute); Rust re-exposure unconfirmed | **Flattened segments** | API f64, internal i64 | Active pre-1.0, **v0.6.0 (2026-05)** | **YES** — closest to KiCad (but C++ FFI) |
| **geo-buffer** / **geo-buf** | Apache-2.0 | Yes (Round + Miter) | None documented | Flattened (straight skeleton) | f64 | **Stale** (geo-buffer v0.2.0 2023; geo-buf fork v0.0.3) | **Partial / no** (different algo, stale, validity caveats) |
| **geo-offset** | **ISC** | Yes (arc-segment fan) | **Segment COUNT** (default 5), not tolerance | **Flattened** (5 seg/corner) | f64 | v0.4.0 (2025-02) | **No** (crude, JS-port) |
| **geo core `Buffer`** | MIT OR Apache-2.0 | Yes (default rounded) + Miter/Bevel | **None documented** | Flattened (approximated) | f64 | Active, **~v0.33.1** (new) | **Unknown** (too new, no tolerance knob) |

## Key Takeaways

1. **License sweep result: ALL permissive — NO GPL/LGPL found.** Every candidate is MIT,
   Apache-2.0, dual MIT/Apache, ISC (geo-offset), or BSL-1.0 (Clipper2 C++ upstream). ISC
   and BSL-1.0 are both permissive and Apache/MIT-compatible with no copyleft, so any of
   these can ship inside kiclaude without the GPL-isolation concerns that apply to
   Freerouting/kicad-cli.
2. **Only `cavalier_contours` outputs TRUE arcs (bulge).** Every other crate — i_overlay,
   clipper2, geo-buffer/geo-buf, geo-offset, and geo core `Buffer` — tessellates round
   corners into **flattened line segments**. This is the single biggest discriminator: if
   kiclaude wants parametric arcs in the KCIR it must use cavalier_contours (or
   `planar_geo`); for flattened polygon zone fill that matches KiCad's tessellated output,
   the segment-based crates are the natural fit.
3. **i_overlay `LineJoin::Round` is NOT an absolute tolerance.** It is an `L/R`
   (max-segment-length / arc-radius) ratio that controls tessellation density, and it
   yields flattened segments. (Corrects the inline brief.) clipper2's upstream
   **ArcTolerance** is the only true absolute-max-error knob among the segment-flattening
   crates — and it is exactly the knob KiCad uses.
4. **Best replacements for a hand-rolled Minkowski-union offset, ranked:**
   - **clipper2** — most faithful to KiCad (KiCad's own zone fill is Clipper-based, integer
     i64 robustness, ArcTolerance round corners). Cost: C++ FFI + pre-1.0.
   - **i_overlay** — best pure-Rust option (no FFI, permissive, very active, native polygon
     inflate). Round corners are L/R-controlled segment fans.
   - **cavalier_contours** — if true-arc output is the goal (round-join only).
   - geo core `Buffer` / geo-buffer / geo-offset — not recommended as a drop-in (too new /
     stale / crude respectively).
5. **CONTEXT CORRECTION:** geo core now ships a native `Buffer` trait (~v0.33.1); the brief's
   claim that geo has "no native buffer" is outdated. It is still unproven for fab-grade
   geometry (no documented arc-tolerance knob).

## Conflicts or Contradictions Found

- **Inline brief vs. docs (i_overlay):** brief described `LineJoin::Round(tolerance)` as a
  tolerance; docs.rs say the parameter is `L/R` (max-segment-length / radius) and the arc
  is "approximated using a group of segments." Resolved in favor of the docs — it is a
  segment-density ratio producing flattened output, not an absolute tolerance.
- **Inline brief vs. docs (geo core):** brief said geo has "no native buffer/offset
  historically (issue #641)"; geo core now has a `Buffer` trait at ~v0.33.1. Resolved in
  favor of current docs — the historical statement is stale.
- **Within cavalier_contours fetches:** the README fetch implied arcs stay as bulge (true
  arcs) while the `PlineOffsetOptions` fetch found no arc-tolerance field (only eps fields).
  Resolved: there is no missing knob — arcs are preserved as true arcs in output, so no
  arc-approximation tolerance is required; the eps fields (`pos_equal_eps`,
  `slice_join_eps`, `offset_dist_eps`) are numerical-robustness parameters for the offset
  algorithm, not arc tessellation.
- **clipper2 ArcTolerance re-exposure:** upstream Clipper2 definitively has an absolute
  ArcTolerance; whether tirithen's safe Rust wrapper re-exposes it by that name was not
  confirmed in the README excerpt — flagged as needing a source-level check before relying
  on it.
- **crates.io unreadability:** crates.io detail pages are JS-rendered and returned only the
  page title via WebFetch; license/version data was instead sourced from docs.rs, lib.rs,
  and GitHub LICENSE/Cargo.toml. The two failed crates.io fetches are logged as unreadable
  in Sources.md.

## COMPLETE
