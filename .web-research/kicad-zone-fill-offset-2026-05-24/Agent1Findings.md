# Agent 1 Findings

## Query Angle

**Core offset algorithm** — reverse-engineer exactly how Clipper2 `ClipperOffset`
(`clipper.offset.cpp`: `DoRound` / `OffsetPoint` / `DoGroupOffset`) and KiCad's
`SHAPE_POLY_SET::Inflate` / `Deflate` (`ROUND_ALL_CORNERS`) construct round-join
arcs, and whether KiCad's zone filler delegates offsetting to Clipper2 internally.
The goal is to reproduce KiCad zone-fill round-corner geometry in Rust bit-for-vertex.

## Queries Executed

| # | Query | Pages fetched / read |
|---|-------|----------------------|
| 1 | Clipper2 clipper.offset.cpp DoRound steps_per_rad_ arc_tolerance source code | Raw clipper.offset.cpp (2 distinct fetches: DoRound/BuildNormals/GetUnitNormal pass + OffsetPoint/DoGroupOffset pass), DeepWiki Offsetting Operations, clipper.offset.h header = 4 |
| 2 | KiCad geometry_utils.h/.cpp GetArcToSegmentCount MIN_SEGCOUNT_FOR_CIRCLE | geometry_utils.cpp doxygen source (2 fetches: GetArcToSegmentCount + constants/correction pass), geometry_utils.h reference = 3 |
| 3 | KiCad shape_poly_set.cpp Inflate Deflate CORNER_STRATEGY ROUND_ALL_CORNERS ClipperOffset | shape_poly_set.cpp raw (GitLab master), SHAPE_POLY_SET class reference (doxygen) = 2 |
| 4 | KiCad zone_filler.cpp Inflate Deflate Clipper2 maxError ARC_HIGH_DEF | zone_filler.cpp doxygen source = 1 |
| 5 | Clipper2 ClipperOffset ArcTolerance default 0.25 InflatePaths jtRound | search result aggregation (InflatePaths.htm, ArcTolerance.htm, ClipperOffset _Body) + clipper.offset.h confirms default = 1 (+ search) |
|   | Clipper2 Offset Trigonometry page | unreadable (SVG-only) |

Total distinct primary source pages read: **~11** (raw C++ sources counted once per file but fetched with multiple extraction prompts). All seven seed queries from ExpandedSearches.md were covered.

---

## Findings

### 1. Clipper2 `clipper.offset.cpp` — `DoGroupOffset` round-join setup (PRIMARY SOURCE, verbatim)

This is the once-per-group precompute (constant `group_delta_`, i.e. the normal
zone case). Run when `group.join_type == JoinType::Round || group.end_type == EndType::Round`:

```cpp
double abs_delta = std::fabs(group_delta_);
...
// arcTol - when arc_tolerance_ is undefined (0) then curve imprecision
// will be relative to the size of the offset (delta).
double arcTol = (arc_tolerance_ > floating_point_tolerance) ?
    std::min(abs_delta, arc_tolerance_) : abs_delta * arc_const;

double steps_per_360 = std::min(PI / std::acos(1 - arcTol / abs_delta), abs_delta * PI);
step_sin_ = std::sin(2 * PI / steps_per_360);
step_cos_ = std::cos(2 * PI / steps_per_360);
if (group_delta_ < 0.0) step_sin_ = -step_sin_;
steps_per_rad_ = steps_per_360 / (2 * PI);
```

Notes:
- `arc_const = 0.002` (declared `const double arc_const = 0.002; // <-- 1/500`). So
  default arc tolerance = `abs_delta / 500` = radius/500.
- The seed-fact form `steps_per_360 = min(PI/acos(1 - arcTol/Δ), Δ·PI)` is **confirmed
  verbatim** (`abs_delta * PI` is the upper cap).
- The `arcTol` selection uses `std::min(abs_delta, arc_tolerance_)` when a tolerance is
  set (clamps tolerance to never exceed the radius), else `abs_delta * arc_const`.
- `if (group_delta_ < 0.0) step_sin_ = -step_sin_;` — sign of rotation flips for
  **deflate (negative delta)**. This matters for KiCad `Deflate` (clearance shrink).

### 2. Clipper2 `DoRound` — the arc-emitting loop (PRIMARY SOURCE, verbatim)

```cpp
void ClipperOffset::DoRound(const Path64& path, size_t j, size_t k, double angle)
{
    if (deltaCallback64_) {            // ONLY for variable-width offset; recompute per-vertex
        double abs_delta = std::fabs(group_delta_);
        double arcTol = (arc_tolerance_ > floating_point_tolerance ?
            std::min(abs_delta, arc_tolerance_) : abs_delta * arc_const);
        double steps_per_360 = std::min(PI / std::acos(1 - arcTol / abs_delta), abs_delta * PI);
        step_sin_ = std::sin(2 * PI / steps_per_360);
        step_cos_ = std::cos(2 * PI / steps_per_360);
        if (group_delta_ < 0.0) step_sin_ = -step_sin_;
        steps_per_rad_ = steps_per_360 / (2 * PI);
    }

    Point64 pt = path[j];
    PointD offsetVec = PointD(norms[k].x * group_delta_, norms[k].y * group_delta_);

    if (j == k) offsetVec.Negate();
    path_out.emplace_back(pt.x + offsetVec.x, pt.y + offsetVec.y);  // FIRST arc vertex

    int steps = static_cast<int>(std::ceil(steps_per_rad_ * std::abs(angle)));
    for (int i = 1; i < steps; ++i) {
        offsetVec = PointD(offsetVec.x * step_cos_ - step_sin_ * offsetVec.y,
                           offsetVec.x * step_sin_ + offsetVec.y * step_cos_);   // 2D rotation
        path_out.emplace_back(pt.x + offsetVec.x, pt.y + offsetVec.y);
    }
    path_out.emplace_back(GetPerpendic(path[j], norms[j], group_delta_));  // LAST arc vertex (exact)
}
```

Critical implementation facts for matching:
- **Starting offset vector** = `norms[k] * group_delta_` (the offset along the *incoming*
  edge's unit normal). It is placed first, exactly at the offset radius (ON the arc).
- **Step count** = `ceil(steps_per_rad_ * |angle|)` where `angle = atan2(sin_a, cos_a)`.
- The interior loop runs `i = 1 .. steps-1` (so it emits `steps-1` rotated points after the
  first), then the **final vertex is NOT a rotated approximation** — it is computed
  exactly via `GetPerpendic(path[j], norms[j], group_delta_)` (offset along the *outgoing*
  edge normal). So the arc's two endpoints are exact offset points; only the interior
  vertices are rotation-stepped. Vertices lie **ON the arc** (radius = |group_delta_|),
  i.e. inscribed-polygon vertices touch the true arc; chords cut inside it (sagitta error).
- **Rotation matrix** is the standard 2×2 CCW rotation by `2π/steps_per_360` per step:
  `x' = x·cos − y·sin`, `y' = x·sin + y·cos`. `step_sin_`/`step_cos_` are precomputed once.
- `pt` (the vertex `path[j]`) is the arc center; every emitted vertex = `pt + offsetVec`.

### 3. Clipper2 `OffsetPoint` — join-type dispatch + sweep angle (PRIMARY SOURCE, verbatim)

```cpp
double sin_a = CrossProduct(norms[j], norms[k]);   // sin of turn angle A
double cos_a = DotProduct(norms[j], norms[k]);     // cos of turn angle A
if (sin_a > 1.0) sin_a = 1.0;
else if (sin_a < -1.0) sin_a = -1.0;
...
if (cos_a > -0.999 && (sin_a * group_delta_ < 0))      // CONCAVE -> 3-point negative region (not arc)
{ ... GetPerpendic(norms[k]); path[j]; GetPerpendic(norms[j]); ... }
else if (cos_a > 0.999 && join_type_ != JoinType::Round)  // <2.5 deg, nearly straight -> miter
    DoMiter(path, j, k, cos_a);
else if (join_type_ == JoinType::Miter) { ... }
else if (join_type_ == JoinType::Round)
    DoRound(path, j, k, std::atan2(sin_a, cos_a));        // <-- ROUND join: sweep = atan2(sin_a,cos_a)
else if (join_type_ == JoinType::Bevel) DoBevel(path, j, k);
else DoSquare(path, j, k);
```

Critical facts:
- `sin_a = CrossProduct(norms[j], norms[k])`, `cos_a = DotProduct(norms[j], norms[k])`.
  `sin_a` clamped to [-1, 1]. Sweep angle passed to `DoRound` = `atan2(sin_a, cos_a)`.
  This is the **change in edge direction** (exterior turn angle), positive = left turn.
- **Convexity gate:** even with `JoinType::Round`, a CONCAVE corner (inside the offset,
  `sin_a * group_delta_ < 0`) does NOT get an arc — it emits a 3-vertex "negative region"
  (`GetPerpendic(k)`, `path[j]`, `GetPerpendic(j)`) that the later union operation cleans up.
  **So rounding only happens on convex corners** (for inflate, the outside corners). This
  is essential: a naive "round every vertex" Rust port will be wrong at concave vertices.
- Note `JoinType::Round` is explicitly excluded from the `cos_a > 0.999` near-straight
  miter shortcut, so very shallow round corners still go through `DoRound` (which will
  produce `steps = ceil(steps_per_rad_ * tiny_angle)` → usually 0 interior pts, just the
  two exact endpoints).

### 4. `BuildNormals` / `GetUnitNormal` — normal convention (PRIMARY SOURCE, verbatim)

```cpp
static PointD GetUnitNormal(const Point64& pt1, const Point64& pt2) {
    if (pt1 == pt2) return PointD(0.0, 0.0);
    double dx = (double)(pt2.x - pt1.x);
    double dy = (double)(pt2.y - pt1.y);
    double inverse_hypot = 1.0 / Hypot(dx, dy);
    dx *= inverse_hypot; dy *= inverse_hypot;
    return PointD(dy, -dx);     // normal = edge rotated -90 deg
}
```
`BuildNormals`: `norms[i] = GetUnitNormal(path[i], path[i+1])` for each edge, last wraps to
`GetUnitNormal(path[last], path[0])`. So `norms[k]` is the normal of the edge *ending* at
vertex j (incoming), `norms[j]` is the normal of the edge *leaving* vertex j (outgoing).
The normal points to the **right** of travel direction (`(dy, -dx)`); with positive delta
this offsets outward for CCW (positive-area) outer contours.

`OffsetPolygon` iterates: `for (j=0, k=size-1; j<size; k=j, ++j) OffsetPoint(group, path, j, k);`
so `k` is always the previous vertex index (wraps).

### 5. Clipper2 `clipper.offset.h` — defaults (PRIMARY SOURCE, verbatim) — RESOLVES the 0.25 conflict

```cpp
explicit ClipperOffset(double miter_limit = 2.0,
                       double arc_tolerance = 0.0,
                       bool preserve_collinear = false,
                       bool reverse_solution = false)
...
double delta_ = 0.0;        double group_delta_ = 0.0;   double temp_lim_ = 0.0;
double steps_per_rad_ = 0.0; double step_sin_ = 0.0;     double step_cos_ = 0.0;
double miter_limit_ = 0.0;   double arc_tolerance_ = 0.0;
```
**Default `arc_tolerance = 0.0` in Clipper2** (NOT 0.25). The "0.25 units" figure that
appears in older docs is the legacy Clipper1/`polyclipping` default and does NOT apply to
Clipper2's `ClipperOffset`. With `arc_tolerance_ == 0`, the `arcTol = abs_delta * arc_const`
(= radius/500) branch always fires. (`temp_lim_ = (miter_limit_ <= 1) ? 2.0 : 2.0/(miter_limit_*miter_limit_)`.)

### 6. KiCad `SHAPE_POLY_SET::Inflate` / `Deflate` / `inflate2` (PRIMARY SOURCE) — the KiCad→Clipper2 bridge

Signatures (doxygen):
```cpp
void Inflate(int aAmount, CORNER_STRATEGY aCornerStrategy, int aMaxError, bool aSimplify=false);
void Deflate(int aAmount, CORNER_STRATEGY aCornerStrategy, int aMaxError);   // negates amount internally
void inflate2(int aAmount, int aCircleSegCount, CORNER_STRATEGY aCornerStrategy, bool aSimplify=false);
void InflateWithLinkedHoles(int aFactor, CORNER_STRATEGY aCornerStrategy, int aMaxError);
```

**Inflate wrapper converts maxError → segment count → then delegates to inflate2:**
```cpp
int segCount = GetArcToSegmentCount( std::abs( aAmount ), aMaxError, FULL_CIRCLE );
inflate2( aAmount, segCount, aCornerStrategy, aSimplify );
```
(i.e. KiCad uses the offset distance `|aAmount|` as the arc *radius* and `FULL_CIRCLE` as the
arc angle to size the segment count.)

**CORNER_STRATEGY → Clipper2 JoinType mapping (verbatim from inflate2):**
```cpp
case CORNER_STRATEGY::ALLOW_ACUTE_CORNERS:   joinType = JoinType::Miter; miterLimit = 10; break;
case CORNER_STRATEGY::CHAMFER_ACUTE_CORNERS: joinType = JoinType::Miter;  break;
case CORNER_STRATEGY::ROUND_ACUTE_CORNERS:   joinType = JoinType::Miter;  break;
case CORNER_STRATEGY::CHAMFER_ALL_CORNERS:   joinType = JoinType::Square; break;
case CORNER_STRATEGY::ROUND_ALL_CORNERS:     joinType = JoinType::Round;  break;
```
Default `miterLimit = 2.0`, except `ALLOW_ACUTE_CORNERS` → 10.

**maxError/segCount → Clipper2 ArcTolerance (verbatim, the load-bearing conversion):**
```cpp
if( aCircleSegCount < 6 ) aCircleSegCount = 6;
double coeff;
if( aCircleSegCount > SEG_CNT_MAX || arc_tolerance_factor[aCircleSegCount] == 0 ) {
    coeff = 1.0 - cos( M_PI / aCircleSegCount );
    if( aCircleSegCount <= SEG_CNT_MAX ) arc_tolerance_factor[aCircleSegCount] = coeff;  // memoized
} else {
    coeff = arc_tolerance_factor[aCircleSegCount];
}
...
c.ArcTolerance( std::abs( aAmount ) * coeff );   // <-- KiCad sets Clipper2 ArcTolerance EXPLICITLY
c.MiterLimit( miterLimit );
```
So **KiCad does NOT use Clipper2's default radius/500 tolerance.** It computes:
```
ArcTolerance = |aAmount| * (1 - cos(pi / aCircleSegCount))
```
where `aCircleSegCount` itself came from `GetArcToSegmentCount(|aAmount|, aMaxError, 360deg)`.
`(1 - cos(pi/N))` is exactly the sagitta-fraction of the radius for an N-gon, so this makes
Clipper2's `steps_per_360 = PI/acos(1 - arcTol/Δ)` resolve back to ≈ `aCircleSegCount`.
There is also a clamp `if (aCircleSegCount < 6) aCircleSegCount = 6;` (min 6 segments per
full circle inside inflate2 — distinct from `MIN_SEGCOUNT_FOR_CIRCLE = 8` used by
`GetArcToSegmentCount`).

**Clipper2 invocation (verbatim):**
```cpp
ClipperOffset c;
for( const POLYGON& poly : m_polys ) {
    Paths64 paths;
    for( size_t i = 0; i < poly.size(); i++ )
        paths.push_back( poly[i].convertToClipper2( i == 0, zValues, arcBuffer ) );
    c.AddPaths( paths, joinType, EndType::Polygon );
}
c.ArcTolerance( std::abs( aAmount ) * coeff );
c.MiterLimit( miterLimit );
PolyTree64 tree;
c.Execute( aAmount, tree );        // <-- delta passed here; Deflate calls with -amount
importTree( tree, zValues, arcBuffer );
```

**Confirmed: KiCad's polygon offsetting delegates to Clipper2 `ClipperOffset` internally.**
Coordinates pass straight through as `Path64`/`Point64` 64-bit integers — KiCad's internal
unit is integer nanometres, used 1:1 as Clipper2 coordinates (`convertToClipper2`, no scaling
in the offset path). `EndType::Polygon` is always used for closed zone polygons.

### 7. KiCad `geometry_utils.cpp` `GetArcToSegmentCount` (PRIMARY SOURCE, verbatim)

```cpp
#define MIN_SEGCOUNT_FOR_CIRCLE 8            // line 40

int GetArcToSegmentCount( int aRadius, int aErrorMax, const EDA_ANGLE& aArcAngle ) {
    aRadius   = std::max( 1, aRadius );      // lines 48-49
    aErrorMax = std::max( 1, aErrorMax );
    double rel_error = (double)aErrorMax / aRadius;                       // line 52
    double arc_increment = 180 / M_PI * acos( 1.0 - rel_error ) * 2;      // line 54  (degrees)
    arc_increment = std::min( 360.0/MIN_SEGCOUNT_FOR_CIRCLE, arc_increment ); // line 58
    int segCount = KiROUND( fabs( aArcAngle.AsDegrees() ) / arc_increment );  // line 60
    return std::max( segCount, 2 );          // line 63
}
```
- `rel_error = errorMax / radius` (relative chord-to-arc deviation, the sagitta as a fraction
  of radius). `aErrorMax` doc: "the max distance between the middle of a segment and the circle"
  — i.e. the sagitta (chord-midpoint deviation), NOT half-chord.
- `arc_increment` (deg) = `360/π · acos(1 − rel_error)` (the `*2` plus the `180/π` factor).
  Equivalent radian arc-step per segment = `2·acos(1 − rel_error)`. This is the exact inverse
  of Clipper2's `steps_per_360 = π/acos(1 − arcTol/Δ)`: with `arcTol = errorMax`, `Δ = radius`,
  `steps_per_360 = π/acos(1−rel_error) = 360/arc_increment`. So KiCad and Clipper2 use the
  **same chord-error → step formula**; KiCad just rounds it to an integer segCount first and
  reconverts to an ArcTolerance coefficient `(1−cos(π/segCount))`.
- Clamp: arc step never coarser than `360/8 = 45°` → at least 8 segments for a full circle.
- `segCount = KiROUND(|arcAngle_deg| / arc_increment)` (rounded to nearest int), floor of 2.

### 8. KiCad inscribed-vs-circumscribed correction — `GetCircleToPolyCorrection` (PRIMARY SOURCE)

```cpp
int GetCircleToPolyCorrection( int aMaxError ) {        // ~lines 106-110
    return s_disable_arc_correction ? 0 : aMaxError;
}
```
KiCad's segmented circles are **inscribed** (vertices on the true circle, chords inside), so a
segmented circle is slightly too small. When converting a circle/round shape to polygon for
clearance, KiCad **enlarges the radius by `aMaxError`** (the sagitta) so the polygon fully
contains the true circle. This is applied at shape→polygon conversion sites
(`TransformCircleToPolygon` etc.), NOT inside the offset itself — but it is essential to
reproduce when matching KiCad's effective clearance geometry. Relevant to thermal-relief
drill discs and round pad knockouts.

### 9. KiCad `zone_filler.cpp` usage context (PARTIAL — from doxygen source)

- The zone filler uses `m_maxError` (= board's `ARC_HIGH_DEF`-derived max error) as the
  `aMaxError` argument to shape→polygon conversions (lines 1584, 1614, 1642, 1645, 1656,
  1662, 1675), with `ERROR_OUTSIDE` / `ERROR_INSIDE` flags controlling whether the polygon
  circumscribes or inscribes the true shape (i.e. which side the `GetCircleToPolyCorrection`
  bias goes).
- Bounding-box `Inflate(m_worstClearance)` calls (lines 699, 1051, 1720) are BOX2 inflations
  for spatial pre-filtering, NOT polygon offsets — do not confuse with `SHAPE_POLY_SET::Inflate`.
- The polygon-level Deflate/Inflate (clearance knockout, slivers removal via
  deflate-then-inflate, thermal spoke generation) live in `fillSingleZone`/helper methods not
  shown in the fetched excerpt; corner strategies used are `CHAMFER_ALL_CORNERS` (deflate) and
  `ROUND_ALL_CORNERS` (inflate) per the search hit. (Flagged as a gap for Agent 2/4 to confirm
  exact call sites; the offset *mechanics* are fully pinned via §1–§8.)

---

## Key Takeaways

1. **KiCad zone-fill offsetting IS Clipper2 offsetting.** `SHAPE_POLY_SET::Inflate/Deflate`
   build a `Clipper2Lib::ClipperOffset`, add the polys as `Path64` (integer nm, 1:1, no
   scaling), set JoinType + MiterLimit + ArcTolerance, and call `c.Execute(amount, tree)`.
   `Deflate(amount,...)` == `Inflate(-amount,...)`. To match KiCad in Rust you reproduce
   Clipper2's `ClipperOffset` plus KiCad's specific parameter conversion.

2. **The round-join arc math (Clipper2):** per group, with radius `Δ = |group_delta_|` and
   `arcTol`, compute `steps_per_360 = min(π/acos(1 − arcTol/Δ), Δ·π)`,
   `steps_per_rad_ = steps_per_360/(2π)`, `step_sin_ = sin(2π/steps_per_360)`,
   `step_cos_ = cos(2π/steps_per_360)` (negate `step_sin_` if delta<0). Per convex vertex
   with turn angle `A = atan2(sin_a, cos_a)`: emit first point at `vertex + norms[k]·Δ`, then
   `ceil(steps_per_rad_·|A|) − 1` interior points by rotating the offset vector with the
   2×2 matrix, then the exact final point `GetPerpendic(vertex, norms[j], Δ)`. Vertices lie
   ON the arc (inscribed polygon); chord midpoints sit inside by the sagitta.

3. **KiCad does NOT use Clipper2's default tolerance.** It sets `ArcTolerance` explicitly to
   `|aAmount| · (1 − cos(π / segCount))`, where `segCount = GetArcToSegmentCount(|aAmount|,
   aMaxError, 360°)`. To match KiCad exactly you MUST replicate this two-step conversion, not
   just feed `maxError` into Clipper. (`segCount` is clamped to ≥6 inside inflate2.)

4. **The chord-error formula is shared.** KiCad `GetArcToSegmentCount`:
   `arc_increment_deg = (360/π)·acos(1 − errorMax/radius)`, clamped so a full circle has ≥8
   segments (`MIN_SEGCOUNT_FOR_CIRCLE = 8`), `segCount = round(|angle_deg|/arc_increment)`,
   floor 2. This is the exact algebraic inverse of Clipper2's `steps_per_360 =
   π/acos(1 − arcTol/Δ)`. `errorMax` = sagitta (segment-midpoint-to-arc distance).

5. **Convex-only rounding.** Concave corners (`sin_a · group_delta_ < 0`, and `cos_a > −0.999`)
   are NOT arced — Clipper2 inserts a 3-point self-intersecting "negative region" cleaned by
   the final union. A correct Rust port must branch on convexity in `OffsetPoint`, exactly as
   Clipper2 does, or it will round inner corners that KiCad/Clipper leave sharp.

6. **Defaults to hard-code:** `arc_const = 0.002` (=1/500); Clipper2 `ClipperOffset` default
   `miter_limit = 2.0`, `arc_tolerance = 0.0`; `temp_lim_ = (miter_limit_ ≤ 1) ? 2.0 :
   2.0/(miter_limit_²)`. KiCad: default miterLimit 2.0 (10 for ALLOW_ACUTE), min seg counts
   8 (GetArcToSegmentCount) and 6 (inflate2 internal clamp). `GetCircleToPolyCorrection`
   returns `aMaxError` (inscribed-circle radius bump).

7. **Normal convention:** Clipper2 unit normal of edge p1→p2 is `(dy, −dx)` (edge rotated −90°,
   points to the right of travel). `norms[k]` = incoming edge, `norms[j]` = outgoing edge at
   vertex j. `sin_a = cross(norms[j], norms[k])`, `cos_a = dot(norms[j], norms[k])`, sin_a
   clamped to [−1,1].

8. **Coordinates are integer nanometres, 1:1 into Clipper2 `Point64`.** No floating scale
   factor in the KiCad offset path; offset vector arithmetic in `DoRound` is done in `double`
   then truncated to int when stored as `Point64`. A faithful Rust port should mirror the
   double-precision intermediate then `i64` truncation to match vertex coordinates exactly.

---

## Conflicts or Contradictions Found

1. **Default ArcTolerance: 0.0 vs 0.25.** Older Clipper docs (documentation.help/polyclipping,
   InflatePaths.htm legacy text) state the default ArcTolerance is **0.25 units**. The actual
   Clipper2 `clipper.offset.h` constructor declares `arc_tolerance = 0.0`. RESOLUTION: 0.25 is
   the legacy Clipper1 default and does not apply; Clipper2's default is 0.0, which triggers
   the `abs_delta * arc_const` (radius/500) fallback. For KiCad matching this is moot anyway
   because KiCad always sets ArcTolerance explicitly (Takeaway 3).

2. **`arc_tolerance_` threshold: `> 0.01` vs `> floating_point_tolerance`.** The DeepWiki
   summary and the first search snippet rendered the guard as `arc_tolerance_ > 0.01`. The raw
   `clipper.offset.cpp` source uses `arc_tolerance_ > floating_point_tolerance`. RESOLUTION:
   trust the source — `floating_point_tolerance` (a small epsilon ~1e-12), not 0.01. DeepWiki
   paraphrased loosely.

3. **Minimum segment count: 8 vs 6 vs 2.** Three different floors exist and are NOT
   contradictory — they apply at different stages: `GetArcToSegmentCount` clamps full-circle
   step ≤45° (≥8 seg) and floors result at 2; `inflate2` separately clamps its
   `aCircleSegCount` arg to ≥6 before computing the tolerance coefficient. They co-exist in
   the call chain (`Inflate` → GetArcToSegmentCount(≥8 for full circle) → inflate2(≥6)).

4. **Where the inscribed→circumscribed correction is applied.** Sources agree
   `GetCircleToPolyCorrection` returns `aMaxError`, but it is applied at shape→polygon
   conversion (TransformCircle...), not inside `Inflate`. No contradiction, but worth flagging
   so a Rust port doesn't double-apply the radius bump inside the offset.

## COMPLETE
