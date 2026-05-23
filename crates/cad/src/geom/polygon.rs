//! Simple polygon — a closed ring of straight segments, plus optional
//! holes (inner rings) for `KiCad`-style zone cutouts.
//!
//! All polygons are treated as implicitly closed: the segment from
//! `points.last()` back to `points.first()` is part of the boundary.

use serde::{Deserialize, Serialize};

use super::bbox::BBox;
use super::point::Point;

/// A closed ring with optional inner cutouts.
#[derive(Debug, Clone, Default, PartialEq, Serialize, Deserialize)]
pub struct Polygon {
    /// Outer ring, in either winding order. The polygon is implicitly
    /// closed — no need to repeat the first point at the end.
    pub points: Vec<Point>,
    /// Inner cutouts — each is itself a closed ring. Points inside an
    /// odd number of holes are considered outside the polygon.
    pub holes: Vec<Vec<Point>>,
}

impl Polygon {
    /// New polygon with no cutouts.
    #[must_use]
    pub fn new(points: Vec<Point>) -> Self {
        Self {
            points,
            holes: Vec::new(),
        }
    }

    /// New polygon with the given cutouts.
    #[must_use]
    pub fn with_holes(points: Vec<Point>, holes: Vec<Vec<Point>>) -> Self {
        Self { points, holes }
    }

    /// Axis-aligned bounding box of the outer ring. Holes are ignored —
    /// they can only ever shrink containment, never grow it.
    #[must_use]
    pub fn bounding_box(&self) -> BBox {
        if self.points.is_empty() {
            return BBox::empty();
        }
        let mut bbox = BBox::from_point(self.points[0]);
        for p in &self.points[1..] {
            bbox = bbox.union(&BBox::from_point(*p));
        }
        bbox
    }

    /// `true` iff `p` lies inside the polygon (including on the boundary)
    /// and not inside any hole.
    ///
    /// Uses the standard even-odd ray-casting algorithm: shoot a ray to
    /// the right (positive X) from `p` and count edge crossings. Even
    /// → outside, odd → inside. The cheap bbox-precheck short-circuits
    /// most "definitely outside" queries.
    #[must_use]
    pub fn contains_point(&self, p: Point) -> bool {
        if !self.bounding_box().contains_point(p) {
            return false;
        }
        if !point_in_ring(p, &self.points) {
            return false;
        }
        // Inside outer ring — disqualify if also inside any hole.
        for hole in &self.holes {
            if point_in_ring(p, hole) {
                return false;
            }
        }
        true
    }

    /// Number of edges (== `points.len()`, since the ring is closed).
    #[must_use]
    pub fn edge_count(&self) -> usize {
        self.points.len()
    }
}

/// Even-odd ray-casting test for a single closed ring.
fn point_in_ring(p: Point, ring: &[Point]) -> bool {
    if ring.len() < 3 {
        return false;
    }
    let mut inside = false;
    let n = ring.len();
    let mut j = n - 1;
    for i in 0..n {
        let pi = ring[i];
        let pj = ring[j];
        // Edge from pj → pi crosses the horizontal ray y = p.y to the
        // right of p.x?
        let crosses_y = (pi.y > p.y) != (pj.y > p.y);
        if crosses_y {
            let x_at_y = (pj.x - pi.x) * (p.y - pi.y) / (pj.y - pi.y) + pi.x;
            if p.x < x_at_y {
                inside = !inside;
            }
        }
        j = i;
    }
    inside
}

#[cfg(test)]
mod tests {
    use super::*;
    use pretty_assertions::assert_eq;
    use proptest::prelude::*;

    fn square() -> Polygon {
        Polygon::new(vec![
            Point::new(0.0, 0.0),
            Point::new(10.0, 0.0),
            Point::new(10.0, 10.0),
            Point::new(0.0, 10.0),
        ])
    }

    #[test]
    fn smoke_bbox_of_square() {
        let bb = square().bounding_box();
        assert_eq!(bb, BBox::new(0.0, 0.0, 10.0, 10.0));
    }

    #[test]
    fn smoke_contains_interior_point() {
        assert!(square().contains_point(Point::new(5.0, 5.0)));
    }

    #[test]
    fn smoke_does_not_contain_exterior_point() {
        assert!(!square().contains_point(Point::new(15.0, 5.0)));
        assert!(!square().contains_point(Point::new(-1.0, 5.0)));
    }

    #[test]
    fn smoke_contains_with_hole() {
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
        assert!(p.contains_point(Point::new(1.0, 1.0)));
        assert!(!p.contains_point(Point::new(5.0, 5.0)));
    }

    #[test]
    fn smoke_concave_l_shape() {
        // L-shape: contains a point in the "arm" but not in the corner
        // that the L excludes.
        let l = Polygon::new(vec![
            Point::new(0.0, 0.0),
            Point::new(10.0, 0.0),
            Point::new(10.0, 4.0),
            Point::new(4.0, 4.0),
            Point::new(4.0, 10.0),
            Point::new(0.0, 10.0),
        ]);
        assert!(l.contains_point(Point::new(2.0, 2.0)));
        assert!(l.contains_point(Point::new(8.0, 2.0)));
        assert!(!l.contains_point(Point::new(8.0, 8.0)));
    }

    proptest! {
        /// Integration: for the unit square, any point inside (0,10)x(0,10)
        /// is reported as inside; any point clearly outside is reported as
        /// outside. Boundary skirted by `0.5` to avoid the ambiguous
        /// boundary case.
        #[test]
        fn integration_contains_point_unit_square(
            x in 0.5_f64..9.5,
            y in 0.5_f64..9.5,
            dx in 11.0_f64..100.0,
            dy in 11.0_f64..100.0,
        ) {
            let sq = square();
            prop_assert!(sq.contains_point(Point::new(x, y)));
            prop_assert!(!sq.contains_point(Point::new(dx, dy)));
            prop_assert!(!sq.contains_point(Point::new(-dx, y)));
        }
    }
}
