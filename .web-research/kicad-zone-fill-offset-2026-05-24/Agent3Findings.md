# Agent 3 Findings

## Query Angle

**Pitfalls and numerical robustness of polygon offsetting** — specifically
as it bears on the kiclaude question of whether a Rust f64 offsetter (our
Minkowski-union-of-edge-rectangles-plus-vertex-discs on top of the
`i_overlay`/`overlay` boolean kernel) can ever reach *exact* fidelity with
KiCad's zone-fill round-corner geometry, which is produced by Clipper on
**integer-nanometre** coordinates.

The throughline of the evidence: **the boolean-union architecture we already
use is the robust part; the corner-arc divergence is not a bug we can polish
away in f64 — it is a structural consequence of (a) how round joins are
discretised into line segments, (b) arc-tolerance interacting with coordinate
rounding, and (c) KiCad snapping every vertex to the integer-nm lattice while
we keep f64.** Bit-exact fidelity to KiCad in f64 is not realistically
achievable; *geometric* fidelity (within tolerance) is, and only if we match
KiCad's arc-segmentation policy and snap to the same lattice.

## Queries Executed

| # | Query | Pages read (incl. fetched) |
|---|-------|----------------------------|
| 1 | polygon offset self-intersection robustness Minkowski union slivers | search + Campen/RWTH, ScienceDirect Minkowski, offset-polygon refs |
| 2 | Clipper2 issue round join spike artifact offset | #934, #593, McNeel forum, #319 |
| 3 | i_overlay simplify_shape valid polygon offset precondition rust | iOverlay README + docs.rs |
| 4 | floating point vs integer coordinates polygon offset fidelity nanometer | Wikipedia robust-geom, Shewchuk/lattice refs |
| 5 | polygon deflate collapsing edges holes negative offset robustness | CGAL straight-skeleton manual, gdspy #3, fcacciola survey (down) |
| 6 | hand rolled polygon offset fragile why use library georust geo buffer | georust #641, geo-buffer docs.rs + lib.rs |
| 6b | Clipper arc tolerance 0.25 default integer rounding pointless | jsclipper wiki, ArcTolerance/Rounding docs (403, mirrored) |

(Several authoritative PDFs/pages were unreadable — arXiv binary PDF, two
documentation.help pages returning 403, fcacciola host down. Each is logged in
strikethrough form in Sources.md, and its substance was corroborated through a
readable mirror.)

## Findings

### 1. Round-join spikes are a *known, open* Clipper2 bug — and they compound (Clipper2 #934)

Clipper2 issue **#934 (Jan 2025)** documents an "unexpected spike" when
offsetting with the **Round** join type. Key points relevant to us:
- The spike "used to be a lot worse, which got improved in the main branch
  recently, **but there is still an unexpected spike**" — i.e. *unresolved in
  the reference implementation we are trying to match.*
- "With a low enough arc tolerance, this gets smoothed out" — the artifact is
  arc-tolerance-sensitive, not a clean geometric truth.
- Critically: "**the spike … gets exaggerated greatly when performing
  subsequent offset operations**." Zone fill in KiCad is a *chain* of offset
  operations (deflate by clearance, re-inflate by min-width / thermal relief),
  so any per-step corner discrepancy is not static — it accumulates.

**Implication for kiclaude:** "KiCad's corner geometry" is itself not a stable,
canonical curve — it carries Clipper's arc-discretisation artifacts, which
differ by Clipper version and by arc-tolerance. Matching it exactly in f64
would mean *reproducing Clipper's specific spikes*, not producing the
mathematically correct rounded corner. This is a strong argument that bit-exact
fidelity is the wrong target.

### 2. Arc distance-violation in round joins came from an angle threshold, not arc count (Clipper2 #726)

Discussion **#726** ("Offset distance violation on arcs"): Clipper2's round
joins were violating the requested offset distance more than Clipper1. Root
cause was **not** the arc approximation itself but an **angle-based
optimisation** — code applying mitering when `cos_a > 0.99` (~8°). The fix:
the maintainer changed the condition to `cos_a > 0.999 && join_type_ !=
JoinType::Round`, i.e. **disable the miter shortcut for round joins** and emit
explicit perpendicular segments. Also noted: offset quality must be measured
**segment-to-segment**, not vertex-to-segment — vertices sitting on the offset
circle does not mean the *edges between them* respect the offset distance
(they chord inside it).

**Implication:** to match KiCad we must replicate the *exact segmentation
decision logic* (when a corner is treated as a straight miter vs. emitted as N
arc segments, and how many segments), not merely the offset distance. Our
"full vertex-disc" Minkowski approach produces a *different* arc phase/sampling
than Clipper's per-join segment emission — that is precisely the divergence the
parent context describes, and it is baked into the algorithm choice.

### 3. Offsetting has hard input preconditions — invalid input → slivers/garbage (Clipper2 #593, ClipperOffset docs, i_overlay)

Multiple independent sources converge on the same precondition list:

- **ClipperOffset docs (angusj.com):** "Offsetting should **not** be performed
  on intersecting closed paths, as doing so will almost always produce
  undesirable results." Recommends a **Union first**, and `SimplifyPaths`
  **before and between** offsetting operations because "redundant segments …
  can cause unexpected blemishes." Winding: solution winding matches input;
  holes must comply with NonZero (outer one way, holes opposite).
- **i_overlay README (our kernel):** "Offsetting a polygon works reliably
  **only with valid polygons**. Ensure: **no self-intersections**, outer
  boundaries **counter-clockwise**, holes **clockwise** — unless
  `main_direction` is set. **If polygon validity cannot be guaranteed, it is
  recommended to apply `simplify_shape` before offsetting.**"
- **Clipper2 #593:** even on a *valid* 64-vertex circle, a large negative
  offset (`-10`, scaled `-41943`) that should collapse to empty instead
  returns **two sliver fragments** — "no obvious self-intersections" in input,
  so the algorithm itself produced the slivers. Confirms slivers are an
  *output* failure mode, not only an input one.

**Implication:** our boolean-union architecture is *winding-agnostic and robust
by construction* — this is the right call and matches every source's advice
(union-first, then offset). The risk is on the **input side**: feeding
`i_overlay`'s offset a non-simplified or wrongly-wound polygon. We should run
`simplify_shape` (correct FillRule) on every input before offsetting, exactly
as iOverlay instructs.

### 4. Hole orientation handling differs between Clipper versions (Clipper2 #319)

Clipper2 **#319**: Clipper1 would "inflate outline, deflate holes, and vice
versa"; Clipper2 instead "inflates/deflates **all** contours uniformly"
regardless of hole vs. outline role. This is governed entirely by **winding
orientation** (outer CCW, holes CW) plus the chosen offset sign — not by any
"this contour is a hole" flag.

**Implication:** whatever KiCad/Clipper version produced the golden output
determines hole behaviour. If we don't match the winding/sign convention KiCad
used, holes in a zone (e.g. around a keepout or pad) will inflate when they
should deflate. This is a second axis of version-dependence on top of the arc
spikes.

### 5. Integer-nm is the source of robustness — and the hard ceiling on f64 fidelity

This is the central answer to the parent's question. Strongly and repeatedly
sourced:

- **Clipper FAQ:** "The Clipper Library uses **integer coordinates internally
  in order to preserve numerical robustness**." Floating input is converted to
  integers before clipping and back after.
- **jsclipper / Clipper docs:** "By using an integer type for polygon
  coordinates, the Clipper Library has been able to **avoid problems of
  numerical robustness** that can cause havoc with geometric computations."
- **Wikipedia (Robust geometric computation):** the failure mechanism is
  **sign corruption in predicates** — when an inexact f64 value near zero gets
  the wrong sign, "the resulting inconsistencies can propagate through the
  algorithm," causing ill-formed output, crashes, or infinite loops. "f64
  cannot guarantee exact fidelity for geometric operations — rounding errors
  accumulate unpredictably through branching logic." Three mitigations: integer
  coordinates, exact/symbolic arithmetic, or float filters.
- **Lattice-rounding literature (arXiv cs/0604059, via search summary):** when
  boolean-op vertices are computed in float then **rounded to the integer
  lattice**, "the output polygons can **intersect spuriously**" — the rounding
  step itself manufactures self-intersections that weren't in the exact result.

**Implication — vertex divergence is unavoidable and structural:**
1. KiCad emits every vertex snapped to integer nm. Our f64 offsetter, even if
   it used identical arc maths, would land vertices at sub-nm positions that
   *differ from KiCad's rounded ones*. Equality at the vertex level is
   impossible without snapping to the same lattice with the same rounding rule.
2. Conversely, if we *do* snap to int-nm, we inherit the same spurious-
   self-intersection risk Clipper has — which is exactly why Clipper runs a
   cleanup/union pass. Our `i_overlay` union pass is the analogous safety net.
3. **Therefore: "exact KiCad fidelity in f64" is not achievable.** The
   reachable target is *geometric* fidelity: same arc-segment count and phase
   per join, then snap output to int-nm, and accept that the canonical contract
   is "within tolerance / topologically identical after snap," never bitwise.

### 6. Arc-tolerance below ~0.25 unit is pointless once snapped to int — and tolerance must SCALE with delta

Tightly sourced and directly on the parent's stated note:

- **jsclipper wiki / ArcTolerance docs:** default ArcTolerance = **0.25
  units**. "**Reducing tolerances below 0.25 will not improve smoothness since
  vertex coordinates will still be rounded to integer values.**" Realistic
  precision "can never be better than 0.5 since arc coordinates will still be
  rounded to integer values." The *only* way to finer precision is **coordinate
  scaling** before/after (e.g. ×10^8 for six decimals on a 10-unit offset).
- **Godot PR #98017:** a real regression — upgrading Clipper→Clipper2 dropped
  the explicit `arc_tolerance` (Clipper1 default was `0.25 unscaled`,
  multiplied by `SCALE_FACTOR` because Clipper is integer-internal). Without
  it, the fixed default **didn't scale with the offset delta**: too-fine
  tolerance for large deltas spawned excessive points and **nearly doubled
  JOIN_ROUND runtime** (v4.3 vs v4.2.2). Lesson: arc tolerance is
  **relative to delta**, and must be re-derived per offset magnitude.

**Implication:** to match KiCad's corner phase we must (a) use KiCad's own
arc-tolerance value (KiCad sets this when it calls Clipper for zone fill — its
`SHAPE_POLY_SET` offset wrappers; Agent 1's KiCad sources cover the exact
constant), and (b) work at the same integer-nm scale, since tolerance finer
than the lattice does nothing but cost time. Our f64 disc sampling is
effectively "infinite tolerance precision," which is the *wrong* direction — it
over-samples relative to where KiCad quantises, guaranteeing phase mismatch.

### 7. Deflate/negative-offset collapse: edges vanish, holes split, exactness matters (CGAL, gdspy #3, geo-buffer)

- **CGAL Straight-Skeleton manual:** parallel edges separated by `2t`, offset
  inward by `t`, "will just **collapse each other and vanish**, keeping the
  output a simple polygon." But at `t-ε` you get edges "so close they will
  almost intersect." With an **exact-constructions kernel** the output is
  guaranteed simple polygons; with **inexact (float) constructions** "the
  roundoff … will cause parallel edges that almost collapse … to become
  **really collinear or even cross each other**." CGAL's pragmatic answer:
  `Exact_predicates_inexact_constructions_kernel` (exact *predicates*, inexact
  *constructions*) — predicates are where robustness lives.
- **gdspy #3:** naive offset on a C-shape that self-closes under inflation
  **segfaults** (SIGSEGV) once overlap appears — a hand-rolled/underlying
  offset that doesn't union-clean self-intersections is genuinely unsafe, not
  just inaccurate.
- **geo-buffer (Rust):** during deflation "polygons **may split into multiple
  separate shapes**." It explicitly warns the underlying `geo`/`geo-types`
  "**do not enforce validity automatically nor does this crate**" — caller must
  validate. And candidly: its source paper's algorithm "**is incorrect**,"
  patched for edge cases — i.e. *even peer-reviewed offset algorithms ship
  wrong and need fixing.*

**Implication:** our union-based Minkowski approach side-steps the
self-intersection/segfault class entirely (the boolean union resolves overlaps
and splits), which is a real robustness win over straight-skeleton or
hand-rolled offsetters. The residual risk is purely *fidelity of the arc
phase*, plus correctly handling the empty/split result of a deflate.

### 8. Hand-rolled offsetting is fragile — the consensus reason (georust #641, fcacciola, geo-buffer)

- **georust/geo #641:** "The ability to buffer geometries is fundamental to a
  high-quality geometry library, yet implementing this capability has
  historically been lacking." Robust offset "requires a **fast, robust
  sweep-line algorithm** and the ability to **robustly compute a straight
  skeleton** … the fundamental first step." Hand-rolled approaches struggle
  with self-intersection, topological complexity, and numerical precision.
- **offset-polygon crate:** a minimal winding-number offsetter (v0.1.0, 67%
  documented) — "polygons **have to be closed** … otherwise you will get
  strange results," exposes a `CombinatorialExplosionError`, and says **nothing**
  about holes, slivers, or self-intersection. A textbook example of the
  fragile hand-rolled class.

**Implication:** this *validates the kiclaude decision* to build offset on top
of a boolean kernel (`i_overlay`) rather than hand-rolling a sweep-line/straight
skeleton. We should not abandon the union approach to chase KiCad's corner
phase — instead, keep the robust union and adjust only the **arc-segment
generation** (count + phase + tolerance + int-nm snap) to match Clipper.

## Key Takeaways

1. **Exact (bitwise) KiCad fidelity in f64 is not achievable.** KiCad snaps
   every vertex to integer nm; f64 lands them elsewhere; rounding to the same
   lattice can itself create spurious self-intersections (lattice-rounding
   literature). The honest contract is "topologically identical / within
   tolerance after snapping to int-nm," not bit equality.
2. **Match KiCad's arc *policy*, not a Platonic arc.** KiCad's corners carry
   Clipper's own discretisation, including a still-open round-join **spike bug
   (#934)** that **compounds across the multi-offset zone-fill pipeline**.
   Fidelity means reproducing segment count + phase + the same arc-tolerance and
   scale — exactly the dimension where our full-disc Minkowski sampling
   diverges.
3. **Arc tolerance below ~0.25 unit is wasted** once vertices snap to integers
   (realistic floor ~0.5); finer precision needs coordinate scaling. Tolerance
   is **relative to the offset delta** and must scale with it (Godot #98017).
   Our f64 over-sampling is the wrong direction.
4. **Our boolean-union architecture is correct and robust** — winding-agnostic,
   resolves self-intersections/overlaps, handles deflate split/collapse, and
   avoids the segfault/sliver class that fragile hand-rolled offsetters hit
   (gdspy #3, georust #641, offset-polygon). Every source endorses union-first.
5. **Honour offset preconditions on input:** run `simplify_shape` (correct
   FillRule) and ensure CCW-outer / CW-holes winding before every offset, per
   i_overlay and ClipperOffset docs. Slivers and blemishes come from skipping
   this.
6. **Hole behaviour is version- and winding-dependent** (Clipper2 #319). Match
   the winding/sign convention of the specific KiCad/Clipper version that
   produced the golden, or holes invert.
7. **Recommended concrete path** to close the corner divergence without
   abandoning the kernel: (a) discover KiCad's zone-fill arc-tolerance constant
   and offset scale (Agent 1's KiCad `SHAPE_POLY_SET`/`zone_filler` sources),
   (b) generate corner arcs with the **same segment count and starting phase**
   Clipper uses per join (not a uniformly sampled full disc), (c) work at
   integer-nm scale and snap output to the same lattice with the same rounding,
   (d) keep the `i_overlay` union as the robustness backstop, (e) treat the CI
   contract as topological-equality-after-snap / within-tolerance rather than
   bitwise.

## Conflicts or Contradictions Found

- **"Integer coordinates = robustness" vs. "rounding to integers creates
  spurious self-intersections."** Both are true at different stages: integer
  *arithmetic* makes predicates exact and robust (Clipper FAQ, Wikipedia),
  while the *rounding step* that maps a float intersection onto the lattice can
  introduce crossings (lattice-rounding paper). Reconciliation: this is exactly
  why Clipper (and our `i_overlay`) run a cleanup/union pass *after* rounding.
  The two claims are not in conflict once the pipeline order is considered.
- **Clipper1 vs. Clipper2 hole handling (#319)** and **round-join distance
  behaviour (#726, #934)** differ *between Clipper versions*. So "KiCad's
  corner geometry" is not a single fixed target — it depends on which Clipper
  version is vendored in the KiCad build that produced the golden file. Any
  fidelity claim must be pinned to a KiCad/Clipper version.
- **fcacciola survey "edges collapse and vanish" vs. gdspy "self-close →
  segfault."** Not a real contradiction: CGAL's straight-skeleton/exact path
  *gracefully* collapses parallel edges to empty; a naive offsetter without a
  union/cleanup stage instead self-intersects and crashes on the same input.
  The difference is the presence of a robust resolution stage — which we have.
- **No source claims f64 *can* reach exact integer-grid fidelity.** Every
  precision source (Wikipedia, Clipper FAQ/docs, CGAL) points the other way.
  This is a consistent, not contradictory, signal — and it is the answer to the
  parent's framing question: exact f64 fidelity is unreachable; matched
  geometry after int-nm snapping is the achievable goal.

## COMPLETE
