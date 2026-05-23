//! Vatti-style polygon boolean kernel for zone fill.
//!
//! Wraps the MIT-licensed [`i_overlay`] crate behind the `Polygon`
//! type the rest of `crates/cad` uses, so callers stay in one type
//! system. The kernel is what makes [`super::fill::fill_zone`]'s
//! obstacle subtraction exact:
//!
//! - **Union** merges overlapping obstacles into a single hole
//!   region.
//! - **Difference** clips obstacles to the inset outline so holes
//!   that would otherwise "leak out" of the polygon are properly
//!   trimmed.
//! - **Multi-island output** — when an obstacle splits the pour into
//!   disjoint copper regions, the kernel returns one [`Polygon`] per
//!   region.
//!
//! ## Winding-order convention
//!
//! `i_overlay` expects **clockwise outer contours and
//! counter-clockwise holes**. The rest of `crates/cad` stores outer
//! contours in CCW order (so signed area is positive). The
//! conversion helpers [`to_overlay_shape`] / [`from_overlay_shape`]
//! handle the reversal transparently.

// `as f64` casts on small `usize` segment indices (≤ 64) used for
// arc-tessellation angles cannot lose precision at any value we hit.
#![allow(clippy::cast_precision_loss)]

use i_overlay::core::fill_rule::FillRule;
use i_overlay::core::overlay_rule::OverlayRule;
use i_overlay::float::single::SingleFloatOverlay;

use crate::geom::{Point, Polygon};

/// One contour in the `i_overlay` shape format.
type IoContour = Vec<[f64; 2]>;
/// One polygon — first contour is outer (CW), the rest are holes (CCW).
type IoShape = Vec<IoContour>;
/// A collection of polygons (`i_overlay`'s native multi-result type).
type IoShapes = Vec<IoShape>;

/// Convert a `crates/cad` [`Polygon`] to an `i_overlay` shape.
///
/// The outer ring is emitted in clockwise order (reversed if the
/// input was CCW). Hole rings are emitted in counter-clockwise order
/// (reversed if the input was CW).
#[must_use]
pub fn to_overlay_shape(poly: &Polygon) -> IoShape {
    let mut shape = IoShape::with_capacity(1 + poly.holes.len());
    shape.push(ring_to_winding(&poly.points, Winding::Clockwise));
    for hole in &poly.holes {
        shape.push(ring_to_winding(hole, Winding::CounterClockwise));
    }
    shape
}

/// Inverse of [`to_overlay_shape`]. The returned `Polygon` has a CCW
/// outer ring (`crates/cad`'s convention) and CCW holes; downstream
/// code that uses `contains_point` is winding-agnostic.
#[must_use]
pub fn from_overlay_shape(shape: &IoShape) -> Polygon {
    let mut iter = shape.iter();
    let outer = match iter.next() {
        Some(c) => contour_to_points(c, Winding::CounterClockwise),
        None => Vec::new(),
    };
    let holes = iter
        .map(|c| contour_to_points(c, Winding::CounterClockwise))
        .collect();
    Polygon::with_holes(outer, holes)
}

/// Union of a set of polygons. Returns one [`Polygon`] per connected
/// region in the union; overlapping inputs are merged.
///
/// An empty input slice returns an empty `Vec`.
#[must_use]
pub fn polygon_union(polygons: &[Polygon]) -> Vec<Polygon> {
    if polygons.is_empty() {
        return Vec::new();
    }
    if polygons.len() == 1 {
        return vec![polygons[0].clone()];
    }
    let subj: IoShapes = polygons.iter().map(to_overlay_shape).collect();
    // Empty clip — Union of subj-only is the same as union(subj).
    let clip: IoShape = Vec::new();
    let result = subj.overlay(&clip, OverlayRule::Union, FillRule::NonZero);
    shapes_to_polygons(&result)
}

/// `subject \ union(clip)` — subtract the union of all `clip`
/// polygons from `subject`. Returns one [`Polygon`] per resulting
/// island; an empty result (subject fully covered) is `Vec::new()`.
#[must_use]
pub fn polygon_difference(subject: &Polygon, clip: &[Polygon]) -> Vec<Polygon> {
    if clip.is_empty() {
        return vec![subject.clone()];
    }
    let subj_shape = to_overlay_shape(subject);
    let clip_shapes: IoShapes = clip.iter().map(to_overlay_shape).collect();
    // `i_overlay` accepts `Shapes` (multi-polygon) as the clip
    // operand — overlapping clips are automatically unioned.
    let result: IoShapes =
        vec![subj_shape].overlay(&clip_shapes, OverlayRule::Difference, FillRule::NonZero);
    shapes_to_polygons(&result)
}

/// Normalize a single self-touching contour into a list of proper
/// outer-with-holes polygons.
///
/// Used to canonicalise `KiCad`'s "slit" filled-polygon
/// representation — `KiCad` emits a polygon with holes as a single
/// ring whose boundary goes into each hole via a zero-width slit,
/// traces the hole, and exits back along the same slit. Running the
/// ring through the boolean kernel with `OverlayRule::Subject` and
/// `FillRule::EvenOdd` resolves the slits and returns the topology
/// the rest of `crates/cad` expects (outer + holes per shape).
#[must_use]
pub fn polygon_normalize_ring(ring: &[Point]) -> Vec<Polygon> {
    if ring.len() < 3 {
        return Vec::new();
    }
    let subj: IoContour = ring.iter().map(|p| [p.x, p.y]).collect();
    let clip: IoShape = Vec::new();
    let result: IoShapes = subj.overlay(&clip, OverlayRule::Subject, FillRule::EvenOdd);
    shapes_to_polygons(&result)
}

/// Intersection of two polygons. Returns one [`Polygon`] per
/// connected region of the intersection.
#[must_use]
pub fn polygon_intersection(a: &Polygon, b: &Polygon) -> Vec<Polygon> {
    let subj = to_overlay_shape(a);
    let clip = to_overlay_shape(b);
    let result = subj.overlay(&clip, OverlayRule::Intersect, FillRule::NonZero);
    shapes_to_polygons(&result)
}

/// Number of arc segments per full circle when approximating
/// rounded-corner offsets. `64` keeps sagitta error below `0.001 mm`
/// at the typical `0.5 mm` clearance, well inside the `0.01 mm`
/// M2-R-05 tolerance.
pub const OFFSET_ARC_SEGMENTS: usize = 64;

/// **Rounded** outward (Minkowski-sum-with-disc) offset by
/// `delta_mm`. Every edge gets a parallel rectangle of half-width
/// `delta_mm`; every vertex gets a disc approximation of radius
/// `delta_mm`. The union of those primitives is the offset polygon.
///
/// Used by [`super::fill::fill_zone`] to match `KiCad`'s rounded
/// corner-rounding behaviour on inflated obstacles and on the
/// min-thickness-opening dilate step.
#[must_use]
pub fn rounded_outward_offset(polygon: &Polygon, delta_mm: f64) -> Vec<Polygon> {
    if polygon.points.len() < 3 || delta_mm <= 0.0 {
        return vec![polygon.clone()];
    }
    // Build primitives from every ring (outer + each hole). Holes
    // matter for the bbox-complement trick used by
    // [`rounded_inward_offset`]: the inner edge of the complement
    // ring must also be grown so the eroded polygon shrinks
    // properly.
    let mut prims = minkowski_disc_primitives(&polygon.points, delta_mm);
    for hole in &polygon.holes {
        prims.extend(minkowski_disc_primitives(hole, delta_mm));
    }
    // Union the source polygon back in so the offset never shrinks
    // the input — important for thin polygons where the edge-rect +
    // vertex-disc primitives may not fully cover the original
    // interior.
    prims.push(polygon.clone());
    polygon_union(&prims)
}

/// **Rounded** inward (Minkowski-erosion-by-disc) offset by
/// `delta_mm`. Uses the universe-complement trick:
///
/// ```text
///   inward(P, d) = U \ outward( U \ P, d )
/// ```
///
/// where `U` is a bounding box around `P` inflated by `2*d` so the
/// outward step has room to grow without clipping the universe.
///
/// Returns one [`Polygon`] per island of the eroded result — a thin
/// polygon may erode into multiple disconnected pieces or vanish
/// entirely.
#[must_use]
pub fn rounded_inward_offset(polygon: &Polygon, delta_mm: f64) -> Vec<Polygon> {
    if polygon.points.len() < 3 {
        return Vec::new();
    }
    if delta_mm <= 0.0 {
        return vec![polygon.clone()];
    }
    let bb = polygon.bounding_box();
    let margin = delta_mm * 4.0;
    let universe = Polygon::new(vec![
        Point::new(bb.min.x - margin, bb.min.y - margin),
        Point::new(bb.max.x + margin, bb.min.y - margin),
        Point::new(bb.max.x + margin, bb.max.y + margin),
        Point::new(bb.min.x - margin, bb.max.y + margin),
    ]);
    let exterior = polygon_difference(&universe, std::slice::from_ref(polygon));
    if exterior.is_empty() {
        // Polygon fully filled universe — erosion of an infinitely
        // large region by delta is still the same region.
        return vec![polygon.clone()];
    }
    let mut grown_pieces: Vec<Polygon> = Vec::new();
    for piece in &exterior {
        grown_pieces.extend(rounded_outward_offset(piece, delta_mm));
    }
    if grown_pieces.is_empty() {
        return vec![polygon.clone()];
    }
    polygon_difference(&universe, &grown_pieces)
}

/// Build the Minkowski-sum primitives that, when unioned, produce a
/// rounded outward offset of the input ring: one rectangle per edge
/// + one disc per vertex.
fn minkowski_disc_primitives(ring: &[Point], delta_mm: f64) -> Vec<Polygon> {
    let n = ring.len();
    let mut prims = Vec::with_capacity(n * 2);
    for i in 0..n {
        let a = ring[i];
        let b = ring[(i + 1) % n];
        let dx = b.x - a.x;
        let dy = b.y - a.y;
        let len = dx.hypot(dy);
        if len > f64::EPSILON {
            // Unit normal (perpendicular) to the edge; sign doesn't
            // matter — the rectangle is symmetric, and the final
            // union absorbs both orientations.
            let nx = -dy / len;
            let ny = dx / len;
            let off = delta_mm;
            prims.push(Polygon::new(vec![
                Point::new(a.x + nx * off, a.y + ny * off),
                Point::new(b.x + nx * off, b.y + ny * off),
                Point::new(b.x - nx * off, b.y - ny * off),
                Point::new(a.x - nx * off, a.y - ny * off),
            ]));
        }
        prims.push(disc_polygon(a, delta_mm, OFFSET_ARC_SEGMENTS));
    }
    prims
}

/// Polygon approximation of a disc with `segments` arc segments. Used
/// by [`minkowski_disc_primitives`] and the round-corner offset
/// helpers.
fn disc_polygon(center: Point, radius: f64, segments: usize) -> Polygon {
    let mut pts = Vec::with_capacity(segments);
    let step = std::f64::consts::TAU / (segments as f64);
    for i in 0..segments {
        let a = step * (i as f64);
        let (s, c) = a.sin_cos();
        pts.push(Point::new(center.x + c * radius, center.y + s * radius));
    }
    Polygon::new(pts)
}

fn shapes_to_polygons(shapes: &IoShapes) -> Vec<Polygon> {
    shapes.iter().map(from_overlay_shape).collect()
}

#[derive(Clone, Copy)]
enum Winding {
    Clockwise,
    CounterClockwise,
}

/// Signed area of a ring of `[x, y]` pairs. Positive ⇒ CCW (y-up
/// math convention).
fn signed_area_io(ring: &[[f64; 2]]) -> f64 {
    let n = ring.len();
    if n < 3 {
        return 0.0;
    }
    let mut sum = 0.0;
    for i in 0..n {
        let a = ring[i];
        let b = ring[(i + 1) % n];
        sum += a[0] * b[1] - b[0] * a[1];
    }
    sum * 0.5
}

/// Signed area of a ring of `Point`. Positive ⇒ CCW.
fn signed_area_points(ring: &[Point]) -> f64 {
    let n = ring.len();
    if n < 3 {
        return 0.0;
    }
    let mut sum = 0.0;
    for i in 0..n {
        let a = ring[i];
        let b = ring[(i + 1) % n];
        sum += a.x * b.y - b.x * a.y;
    }
    sum * 0.5
}

fn ring_to_winding(ring: &[Point], target: Winding) -> IoContour {
    let area = signed_area_points(ring);
    let is_ccw = area > 0.0;
    let want_ccw = matches!(target, Winding::CounterClockwise);
    let mut out: IoContour = ring.iter().map(|p| [p.x, p.y]).collect();
    if is_ccw != want_ccw {
        out.reverse();
    }
    out
}

fn contour_to_points(contour: &[[f64; 2]], target: Winding) -> Vec<Point> {
    let area = signed_area_io(contour);
    let is_ccw = area > 0.0;
    let want_ccw = matches!(target, Winding::CounterClockwise);
    let mut out: Vec<Point> = contour.iter().map(|p| Point::new(p[0], p[1])).collect();
    if is_ccw != want_ccw {
        out.reverse();
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    fn square(min: f64, max: f64) -> Polygon {
        Polygon::new(vec![
            Point::new(min, min),
            Point::new(max, min),
            Point::new(max, max),
            Point::new(min, max),
        ])
    }

    fn approx_area(p: &Polygon) -> f64 {
        let outer = signed_area_points(&p.points).abs();
        let holes: f64 = p.holes.iter().map(|h| signed_area_points(h).abs()).sum();
        (outer - holes).max(0.0)
    }

    #[test]
    fn smoke_union_disjoint_squares_produces_two_polygons() {
        let a = square(0.0, 1.0);
        let b = square(2.0, 3.0);
        let result = polygon_union(&[a, b]);
        assert_eq!(result.len(), 2, "disjoint inputs → two output islands");
    }

    #[test]
    fn smoke_union_overlapping_merges_into_one() {
        let a = square(0.0, 2.0);
        let b = square(1.0, 3.0);
        let result = polygon_union(&[a, b]);
        assert_eq!(result.len(), 1);
        // L-shaped union area = 2*2 + 2*2 - 1*1 = 7.
        let area = approx_area(&result[0]);
        assert!((area - 7.0).abs() < 1e-6, "L-union area: {area}");
    }

    #[test]
    fn smoke_difference_removes_overlap() {
        let outline = square(0.0, 10.0);
        let hole = square(4.0, 6.0);
        let result = polygon_difference(&outline, &[hole]);
        assert_eq!(result.len(), 1);
        let area = approx_area(&result[0]);
        assert!((area - (100.0 - 4.0)).abs() < 1e-6, "donut area: {area}");
    }

    #[test]
    fn smoke_difference_splits_into_two_islands() {
        // 10×10 square split into two halves by a thin vertical bar.
        let outline = square(0.0, 10.0);
        let bar = Polygon::new(vec![
            Point::new(4.0, -1.0),
            Point::new(6.0, -1.0),
            Point::new(6.0, 11.0),
            Point::new(4.0, 11.0),
        ]);
        let result = polygon_difference(&outline, &[bar]);
        assert_eq!(result.len(), 2, "expected two island halves");
        let total_area: f64 = result.iter().map(approx_area).sum();
        // 10×10 minus the 2×10 bar slice (clipped to outline).
        assert!((total_area - 80.0).abs() < 1e-6, "total area: {total_area}");
    }

    #[test]
    fn smoke_difference_unions_overlapping_clips() {
        // Two overlapping holes that should merge into one larger hole.
        let outline = square(0.0, 10.0);
        let h1 = square(3.0, 6.0);
        let h2 = square(5.0, 8.0);
        let result = polygon_difference(&outline, &[h1, h2]);
        assert_eq!(result.len(), 1, "merged hole stays inside one shape");
        // Hole geometry should be the union of (3..6)×(3..6) and (5..8)×(5..8):
        //   3*3 + 3*3 - 1*1 = 17
        // Result area = 100 - 17 = 83.
        let area = approx_area(&result[0]);
        assert!(
            (area - 83.0).abs() < 1e-6,
            "donut-with-merged-hole area: {area}"
        );
    }

    #[test]
    fn smoke_difference_clips_overflowing_clip_to_boundary() {
        // Clip extends beyond the outline; result must still be a valid
        // polygon inside the outline.
        let outline = square(0.0, 10.0);
        let huge = square(-5.0, 5.0);
        let result = polygon_difference(&outline, &[huge]);
        assert_eq!(result.len(), 1);
        // outline minus huge ∩ outline = outline minus square(0..5, 0..5)
        let area = approx_area(&result[0]);
        assert!((area - (100.0 - 25.0)).abs() < 1e-6, "clipped area: {area}");
    }

    #[test]
    fn smoke_difference_fully_covered_subject_returns_empty() {
        let outline = square(0.0, 1.0);
        let huge = square(-10.0, 10.0);
        let result = polygon_difference(&outline, &[huge]);
        assert!(result.is_empty(), "fully-covered subject must vanish");
    }

    #[test]
    fn smoke_intersection_overlapping_returns_lens() {
        let a = square(0.0, 2.0);
        let b = square(1.0, 3.0);
        let result = polygon_intersection(&a, &b);
        assert_eq!(result.len(), 1);
        // Intersection square is (1..2)×(1..2) = 1.
        let area = approx_area(&result[0]);
        assert!((area - 1.0).abs() < 1e-6, "intersection area: {area}");
    }

    #[test]
    fn smoke_roundtrip_polygon_with_hole_preserves_topology() {
        let p = Polygon::with_holes(
            vec![
                Point::new(0.0, 0.0),
                Point::new(10.0, 0.0),
                Point::new(10.0, 10.0),
                Point::new(0.0, 10.0),
            ],
            vec![vec![
                Point::new(4.0, 4.0),
                Point::new(6.0, 4.0),
                Point::new(6.0, 6.0),
                Point::new(4.0, 6.0),
            ]],
        );
        let shape = to_overlay_shape(&p);
        let back = from_overlay_shape(&shape);
        assert_eq!(back.points.len(), 4);
        assert_eq!(back.holes.len(), 1);
        // Hole-bearing donut area = 100 - 4 = 96.
        let area = approx_area(&back);
        assert!((area - 96.0).abs() < 1e-9, "round-trip area: {area}");
    }

    #[test]
    fn smoke_winding_is_normalized_on_input() {
        // CW outer ring on input — should still parse correctly.
        let cw_outer = Polygon::new(vec![
            Point::new(0.0, 0.0),
            Point::new(0.0, 10.0),
            Point::new(10.0, 10.0),
            Point::new(10.0, 0.0),
        ]);
        let result = polygon_union(&[cw_outer]);
        assert_eq!(result.len(), 1);
        let area = approx_area(&result[0]);
        assert!((area - 100.0).abs() < 1e-9, "CW input area: {area}");
    }
}
