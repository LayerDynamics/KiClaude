# Agent 4 Findings

## Query Angle

`i_overlay` / iShape offset API deep-dive + `geo` buffering status. Concrete
API signatures and examples for `i_overlay`'s `StrokeOffset`, `OutlineOffset`,
`OutlineStyle`, `StrokeStyle`, `LineJoin::Round(tolerance)`, `LineCap`; how the
`Round` parameter maps to arc step count; whether output is flattened segments
(KiCad-style) or true arcs; catalog of the iShape-Rust ecosystem and author
Nail Sharipov; and confirmation of `geo` core buffer/offset status (issue #641,
PR #1365) plus the `geo-buffer` / `geo-offset` / `geo-buf` landscape. Confirm
which i_overlay version exposes the offset API and that `i_overlay` is the same
crate that `overlay`-family bool ops live under.

## Queries Executed

| # | Query | Outcome |
|---|-------|---------|
| 1 | iOverlay StrokeOffset OutlineOffset LineJoin Round tolerance LineCap example rust | Found GitHub, crates.io, docs.rs, lib.rs; confirmed trait/style names |
| 2 | iShape-Rust i_shape i_float i_tree i_key_sort ecosystem crates Nail Sharipov | Catalogued the org repos + author identity |
| 3 | i_overlay buffer offset example rust round join arc steps documentation docs.rs | Confirmed buffering = "offsets paths and polygons"; pointed to mesh module |
| 4 | georust geo issue 641 buffering offset straight skeleton merged status 2026 | Issue #641 closed via PR #1365; geo Buffer trait exists |
| 5 | geo-buffer geo-offset geo-buf differences rust which maintained round join | geo (i_overlay backed) is the maintained path; geo-buffer/geo-buf are straight-skeleton |
| 6 | i_overlay powers geo crate boolean operations offset outline rust 2026 | Confirmed "iOverlay powers polygon boolean operations in geo"; v6.0.1 (2026-05-18) |

Pages opened/read across these queries (docs.rs module + type pages, GitHub
repo + issue + PR, lib.rs, crates.io, geo Buffer trait, geo-buffer, geo-buf,
iShape org) — see `Sources.md` `## Agent 4` for the full URL list.

## Findings

### 1. The offset API EXISTS in `i_overlay` and is the same crate as our bool-ops dep

- `i_overlay` is published by **Nail Sharipov (GitHub `NailxSharipov`,
  org `iShape-Rust`)**, dual-licensed **MIT OR Apache-2.0**.
- Latest version: **6.0.1, released 2026-05-18** (6.0.0 was 2026-05-02). There
  are parallel-maintained 5.x / 4.5.x lines also bumped 2026-05-18.
- The crate's own feature list includes: *"Buffering: offsets paths and
  polygons."* alongside boolean ops, spatial predicates, polyline clip/slice,
  simplification, and fill rules. So the offset/outline/stroke API is part of
  the same `i_overlay` crate we already depend on for boolean ops — NOT a
  separate crate. (Note: the crate name on crates.io is `i_overlay`; the repo /
  brand name is "iOverlay". There is no separate `overlay` crate involved.)

### 2. Where the API lives (module tree)

The offset API is under the `mesh` module (NOT at the crate root, which is why
the docs.rs landing page does not surface it):

```
i_overlay::mesh
  ├─ style    (StrokeStyle, OutlineStyle, LineJoin, LineCap)
  ├─ outline  (outline::offset::OutlineOffset trait — polygon offsetting)
  └─ stroke   (stroke::offset::StrokeOffset trait — path/polyline offsetting)
```

### 3. Style types (verbatim from docs.rs 6.0.1)

```rust
// i_overlay::mesh::style

pub enum LineJoin<T: FloatNumber> {
    Bevel,        // "Cuts off the corner where two lines meet. This is the default."
    Miter(T),     // "Creates a sharp corner ... parameter Angle is a minimum sharp angle" (miter limit)
    Round(T),     // see note below
}

pub enum LineCap<P: FloatPointCompatible> {
    Butt,                 // squared-off end (default)
    Round(P::Scalar),     // semicircular arc, radius = half line width; param "Angle in radians"
    Square,               // squared-off, extended by half the line width
    Custom(Rc<[P]>),      // custom end via template points
}

pub struct OutlineStyle<T: FloatNumber> {
    pub outer_offset: T,
    pub inner_offset: T,
    pub join: LineJoin<T>,
}
impl<T: FloatNumber> OutlineStyle<T> {
    pub fn new(offset: T) -> Self;            // sets both offsets to `offset`
    pub fn offset(self, offset: T) -> Self;
    pub fn outer_offset(self, outer_offset: T) -> Self;
    pub fn inner_offset(self, inner_offset: T) -> Self;
    pub fn line_join(self, join: LineJoin<T>) -> Self;
}

pub struct StrokeStyle<P: FloatPointCompatible> {
    pub width: P::Scalar,
    pub start_cap: LineCap<P>,
    pub end_cap: LineCap<P>,
    pub join: LineJoin<P::Scalar>,
}
impl<P: FloatPointCompatible> StrokeStyle<P> {
    pub fn new(width: P::Scalar) -> Self;
    pub fn width(self, width: P::Scalar) -> Self;
    pub fn start_cap(self, cap: LineCap<P>) -> Self;
    pub fn end_cap(self, cap: LineCap<P>) -> Self;
    pub fn line_join(self, join: LineJoin<P::Scalar>) -> Self;
}
```

### 4. THE KEY ANSWER — what `LineJoin::Round(T)` controls + output shape

Verbatim doc for `Round(T)`:

> "Creates an arc corner where two lines meet. The arc is approximated using a
> group of segments, where the parameter `Angle` is defined as `L / R`, with
> `L` being the maximum segment length and `R` being the arc radius."

Interpretation (load-bearing for the KiCad parity goal):

- **The arc is flattened into straight line segments — the output is a polygon
  of segments, NOT true arc primitives.** This matches KiCad's zone-fill
  geometry, which also stores filled zones as segment polygons.
- The `Round` parameter is **NOT an absolute distance tolerance** like
  Clipper2's `ArcTolerance`. It is a **dimensionless ratio `L / R` = max chord
  length per unit radius**, i.e. effectively the arc step angle. Smaller value
  → more segments → smoother arc. Concretely, the number of segments for a
  90° corner is roughly `ceil((π/2) / (L/R))` (the swept angle divided by the
  per-segment angle ≈ `L/R` for small values). This differs from Clipper2,
  where `ArcTolerance` is a max deviation (sagitta) in coordinate units and
  segment count scales with `sqrt(radius/tolerance)`. **So a kiclaude port
  cannot pass the same numeric tolerance to both libraries** — the parameter
  semantics differ (ratio-of-radius vs absolute-deviation).
- `LineCap::Round(P::Scalar)` is documented as "Angle in radians" — i.e. the
  cap arc's per-segment step angle.

### 5. The offset traits — exact signatures (verbatim, docs.rs 6.0.1)

```rust
// i_overlay::mesh::outline::offset
pub trait OutlineOffset<P: FloatPointCompatible> {
    fn outline(&self, style: &OutlineStyle<P::Scalar>) -> Shapes<P>;
    fn outline_into(&self, style: &OutlineStyle<P::Scalar>,
                    output: &mut FloatFlatContoursBuffer<P>);
    fn outline_custom(&self, style: &OutlineStyle<P::Scalar>,
                      options: OverlayOptions<P::Scalar>) -> Shapes<P>;
    fn outline_custom_into(&self, style: &OutlineStyle<P::Scalar>,
                           options: OverlayOptions<P::Scalar>,
                           output: &mut FloatFlatContoursBuffer<P>);
    fn outline_fixed_scale(&self, style: &OutlineStyle<P::Scalar>,
                           scale: P::Scalar)
        -> Result<Shapes<P>, FixedScaleOverlayError>;
    fn outline_fixed_scale_into(&self, style: &OutlineStyle<P::Scalar>,
                                scale: P::Scalar,
                                output: &mut FloatFlatContoursBuffer<P>)
        -> Result<(), FixedScaleOverlayError>;
    fn outline_custom_fixed_scale(&self, style: &OutlineStyle<P::Scalar>,
                                  options: OverlayOptions<P::Scalar>,
                                  scale: P::Scalar)
        -> Result<Shapes<P>, FixedScaleOverlayError>;
    fn outline_custom_fixed_scale_into(&self, style: &OutlineStyle<P::Scalar>,
                                       options: OverlayOptions<P::Scalar>,
                                       scale: P::Scalar,
                                       output: &mut FloatFlatContoursBuffer<P>)
        -> Result<(), FixedScaleOverlayError>;
}

// i_overlay::mesh::stroke::offset
pub trait StrokeOffset<P: FloatPointCompatible> {
    fn stroke(&self, style: StrokeStyle<P>, is_closed_path: bool) -> Shapes<P>;
    fn stroke_into(&self, style: StrokeStyle<P>, is_closed_path: bool,
                   output: &mut FloatFlatContoursBuffer<P>);
    fn stroke_custom(&self, style: StrokeStyle<P>, is_closed_path: bool,
                     options: OverlayOptions<P::Scalar>) -> Shapes<P>;
    // ... plus _custom_into, _fixed_scale, _fixed_scale_into,
    //     _custom_fixed_scale, _custom_fixed_scale_into variants
}
```

Notes:
- `outline()` / `stroke()` return `Shapes<P>` (a `Vec` of shapes, each a
  `Vec` of contours = `Vec<[P]>`), where "outer boundary paths have a
  counterclockwise order, and holes have a clockwise order."
- `*_into` variants write into a reusable `FloatFlatContoursBuffer<P>`
  (allocation-free hot path — useful for kiclaude's per-fill loop).
- `*_fixed_scale` variants let you pin the internal fixed-point grid:
  `scale = 1.0 / grid_size`, returning `Result<_, FixedScaleOverlayError>` so
  precision overflow is reported instead of silently clamped. Plain
  `outline()`/`stroke()` auto-select the scale.
- `*_custom` variants take an `OverlayOptions` (fill rule, simplification, etc.).

### 6. Concrete usage examples (verbatim from the i_overlay 6.0.1 README)

Offsetting a **polygon (closed outline, with a hole)** — the case kiclaude
needs for zone fill:

```rust
use i_overlay::mesh::outline::offset::OutlineOffset;
use i_overlay::mesh::style::{LineJoin, OutlineStyle};

let shape = vec![
    vec![ /* outer contour: [f64;2] points, CCW */
        [2.0,1.0],[4.0,1.0],[5.0,2.0],[13.0,2.0],[13.0,3.0],[12.0,3.0],
        [12.0,4.0],[11.0,4.0],[11.0,3.0],[10.0,3.0],[9.0,4.0],[8.0,4.0],
        [8.0,3.0],[5.0,3.0],[5.0,4.0],[4.0,5.0],[2.0,5.0],[1.0,4.0],[1.0,2.0]
    ],
    vec![ /* hole: CW */
        [2.0,4.0],[4.0,4.0],[4.0,2.0],[2.0,2.0]
    ],
];

let style = OutlineStyle::new(0.2).line_join(LineJoin::Round(0.1));
let shapes = shape.outline(&style);
```

Offsetting a **path (open polyline → stroked band)**:

```rust
use i_overlay::mesh::stroke::offset::StrokeOffset;
use i_overlay::mesh::style::{LineCap, LineJoin, StrokeStyle};

let path = [[2.0,1.0],[5.0,1.0],[8.0,4.0],[11.0,4.0],
            [11.0,1.0],[8.0,1.0],[5.0,4.0],[2.0,4.0]];

let style = StrokeStyle::new(1.0)
    .line_join(LineJoin::Miter(1.0))
    .start_cap(LineCap::Round(0.1))
    .end_cap(LineCap::Square);

let shapes = path.stroke(style, false); // false = open path
```

For the kiclaude zone-fill / keepout use case, the relevant call is the polygon
outline with positive offset for outward inflate (e.g. clearance halo) or
negative offset for inward deflate, with `LineJoin::Round` to mirror KiCad's
rounded corners:

```rust
// inflate a zone outline by `clearance` mm with rounded corners:
let style = OutlineStyle::new(clearance_mm).line_join(LineJoin::Round(l_over_r));
let inflated: Shapes<[f64; 2]> = zone_outline.outline(&style);
// deflate (thermal/keepout shrink): pass a negative offset
let style = OutlineStyle::new(-clearance_mm).line_join(LineJoin::Round(l_over_r));
```

### 7. iShape-Rust ecosystem catalog (author Nail Sharipov)

- **iOverlay** — Boolean ops for 2D polygons (union/intersection/difference/xor)
  + the `mesh` offset/stroke/outline API. The crate kiclaude already uses.
- **iShape** — compact 2D data structures using `FixVec`.
- **iFloat** — fixed-point ("FixFloat") deterministic math + geometry primitives
  (this is the determinism layer the offset/bool engine builds on).
- **iTree** — red-black tree (sweep-line status structure).
- **iKeySort** — bin+counting hybrid sort (used to bucket events fast).
- **iTriangle** — 2D triangulation (fast/stable).
- **iMesh** — "Mesh builder for strokes and shapes" (the underlying mesh-builder
  the stroke/outline API uses).
- **iShape-js** — WASM 2D geometry lib (bool ops + buffering + triangulation)
  for JS/TS.
- **iShape-ffi** — C FFI bridge for iOverlay + iTriangle.
- **iShape-swift** — Swift bridge.
- **iCurve** — bezier-curve boolean ops ("Not Ready!").

Author: **Nail Sharipov** (handle `Nail_S` / `NailxSharipov`), iOS/Rust dev,
10+ yrs (Swift/Rust), computational-geometry & performance focus; active on
Habr and DEV. iOverlay has ~518k downloads/month and is depended on by ~461
crates (per lib.rs).

### 8. `geo` core buffer/offset status — CONFIRMED, backed by i_overlay

- **`georust/geo` issue #641 ("Geometry buffering")** — opened 2021-04-02 by
  `urschrei`; **now CLOSED**, implemented by **PR #1365** (merged **2025-06-24**,
  approved by `dabreegster` + `mthh`). The issue's history discusses straight
  skeleton / CGAL DCEL / Clipper as options, but the **merged implementation
  wraps iOverlay**, not a straight-skeleton.
- PR #1365 description: *"Geometry buffering constructs a new geometry whose
  boundary is some offset from the input boundary..."* and explicitly *"wraps
  functionality from the iOverlay library, similar to how the crate's BoolOps
  trait works,"* exposing a single unified `Buffer` trait across all geometry
  types (JTS/GEOS-style) rather than mirroring iOverlay's split outline/stroke
  APIs.
- **`geo::algorithm::buffer::Buffer` trait** (in geo 0.33.1):
  ```rust
  fn buffer(&self, distance: Self::Scalar) -> MultiPolygon; // rounded joins & caps by DEFAULT
  fn buffer_with_style(&self, style: BufferStyle<Self::Scalar>) -> MultiPolygon;
  // BufferStyle::new(distance).line_cap(LineCap::Square).line_join(LineJoin::Miter(1.0))
  ```
  Example:
  ```rust
  use geo::{wkt, Buffer};
  use geo::algorithm::buffer::{BufferStyle, LineCap, LineJoin};
  let lines = wkt! { MULTILINESTRING((0. 0.,2. 0.,1. 2.)) };
  let style = BufferStyle::new(0.5).line_cap(LineCap::Square).line_join(LineJoin::Miter(1.0));
  let buffered = lines.buffer_with_style(style);
  ```
- **Crucial version detail:** geo 0.33.1 pins **`i_overlay ^4.5.1, <4.6.0`** —
  i.e. geo's `Buffer` is built on the **4.5.x** line, NOT the current 6.0.1.
  The offset/mesh API (StrokeOffset/OutlineOffset/OutlineStyle) is present and
  stable across the 4.5.x → 6.0.1 range, so either depending on `i_overlay`
  directly (current 6.x) or going through `geo::Buffer` (older 4.5.x) is viable.
  Going direct to `i_overlay` gives access to `OutlineStyle::outer_offset` /
  `inner_offset` and the `*_into` allocation-free + `*_fixed_scale` precision
  variants that geo's wrapper does not surface.

### 9. The `geo-buffer` / `geo-offset` / `geo-buf` landscape (the alternatives)

- **`geo-buffer`** (`1011-git/geo-buffer`, v0.2.0) — **straight-skeleton**
  buffering (Felkel & Obdržálek 1998, which the docs note "is incorrect" and was
  patched for edge cases). Functions: `buffer_polygon`, `buffer_multi_polygon`,
  `buffer_polygon_rounded`, `buffer_multi_polygon_rounded`,
  `skeleton_of_polygon_to_linestring`. Supports miter (default) AND rounded
  variants. **But pins ancient `geo ^0.24.1` / `geo-types ^0.7.9`** → stale,
  effectively unmaintained for current geo.
- **`geo-buf`** (`njwitthoeft/geo-buf`) — a fork of geo-buffer updated to
  `geo 0.29.3` / `geo-types 0.7.15`; same straight-skeleton approach + rounded
  joins; 0 stars, 1 open PR, no release past 0.0.3 → also low-activity.
- **`geo-offset`** (Agent 2's territory) — separate older crate; deprecated-ish.
- **Verdict for kiclaude:** the maintained, robust path is **i_overlay directly
  (or geo's i_overlay-backed `Buffer`)**, NOT the straight-skeleton crates.
  i_overlay's offset reuses its proven robust boolean-op core (the same one we
  already trust for zone clipping), whereas the straight-skeleton crates carry a
  self-documented correctness caveat and are pinned to obsolete geo versions.

## Key Takeaways

1. **YES — `i_overlay` exposes a built-in offset/inflate/outline/stroke API**
   with round joins and a tolerance-like parameter. It is the SAME crate we
   already depend on for boolean ops (crates.io `i_overlay`, repo "iOverlay",
   MIT OR Apache-2.0, v6.0.1 @ 2026-05-18, by Nail Sharipov). There is no
   separate `overlay` crate.
2. The API is under `i_overlay::mesh`: `OutlineOffset::outline(&OutlineStyle)`
   for polygons and `StrokeOffset::stroke(StrokeStyle, is_closed)` for paths,
   with `OutlineStyle { outer_offset, inner_offset, join }` and
   `LineJoin::{Bevel, Miter(t), Round(t)}` / `LineCap::{Butt, Round(a), Square,
   Custom}`.
3. **`LineJoin::Round(T)` flattens the corner arc to line segments — output is a
   segment polygon (KiCad-style), not arc primitives.** The `T` param is a
   `L/R` ratio (max segment length / arc radius ≈ per-step angle), NOT an
   absolute deviation tolerance — so it is semantically different from
   Clipper2's `ArcTolerance` and from KiCad's `m_maxError`. A port must convert:
   given a desired max-deviation `e` and radius `R`, the equivalent chord-angle
   is `2*acos(1 - e/R)`, which you can feed as the `L/R` ratio.
4. This **directly replaces a hand-rolled Minkowski-union offset**: feed the
   zone/keepout outline, pick the offset sign (positive=inflate,
   negative=deflate), choose `LineJoin::Round` for KiCad-matching corners, and
   read back `Shapes<[f64;2]>`. The `*_into` + `*_fixed_scale` variants give an
   allocation-free, precision-controlled hot path.
5. **`geo` already ships buffering, and it is i_overlay-backed** (issue #641
   closed by PR #1365, merged 2025-06-24; geo's `Buffer` wraps iOverlay). Note
   geo pins `i_overlay 4.5.x`; depending on `i_overlay` directly (6.x) is the
   richer, more controllable option.
6. The straight-skeleton crates (`geo-buffer`, `geo-buf`) are the alternative
   but carry a self-documented correctness caveat and are pinned to obsolete
   geo versions — not recommended over i_overlay for kiclaude.

## Conflicts or Contradictions Found

- **Parameter semantics conflict (important):** The first WebSearch summary
  loosely called `LineJoin::Round`'s parameter a "tolerance." The authoritative
  docs.rs page is precise: it is the ratio `L/R` (max segment length / arc
  radius), i.e. a per-step angle, not an absolute distance tolerance. The seed
  hint's wording ("LineJoin::Round(tolerance)") is therefore approximately right
  in spirit but misleading numerically — do NOT treat it as Clipper2-style
  absolute `ArcTolerance`. Resolved in favor of docs.rs.
- **Version drift between i_overlay and geo:** i_overlay's current line is 6.0.1
  (2026-05), but `geo` 0.33.1 still pins `i_overlay ^4.5.1, <4.6.0`. Both expose
  the offset API; no functional contradiction, just a version-lag to be aware of
  when choosing direct-vs-via-geo integration. The seed's "v6.0.0 ~May 2026"
  matches i_overlay's own release; geo lagging is expected and not a conflict.
- **The `mesh`/offset API is not visible on the docs.rs crate landing page** (it
  only advertises boolean ops + string ops), which made one early fetch report
  "not found." It is fully documented under the `mesh` submodule pages and in
  the README. No real contradiction — just a docs-surface quirk; confirmed via
  module-level pages + README source.
- **geo-buffer maintenance vs. capability:** sources agree geo-buffer supports
  rounded joins, but it is pinned to `geo 0.24.1` (geo-buf fork updates to
  0.29.3). The "actively maintained / most explicit control" phrasing in one
  search summary overstates its currency relative to geo's own i_overlay-backed
  `Buffer`; the version pins are the tiebreaker.

## COMPLETE
