//! Circular arc — center + radius + angular range.
//!
//! `KiCad`'s S-expression uses a 3-point form `(arc (start …) (mid …)
//! (end …))`; this module exposes both that constructor and the more
//! algebra-friendly `(center, radius, start_angle, end_angle)` form.

use std::f64::consts::TAU;

use serde::{Deserialize, Serialize};

use super::bbox::BBox;
use super::point::Point;

/// A circular arc lying in the XY plane.
#[derive(Debug, Clone, Copy, Default, PartialEq, Serialize, Deserialize)]
pub struct Arc {
    pub center: Point,
    pub radius: f64,
    /// Angle (radians, CCW from +X) of the arc's start point.
    pub start_angle: f64,
    /// Angle (radians, CCW from +X) of the arc's end point. May exceed
    /// `start_angle` to represent CCW sweeps spanning more than π.
    pub end_angle: f64,
}

impl Arc {
    /// Construct from explicit center+radius+angles.
    #[must_use]
    pub const fn new(center: Point, radius: f64, start_angle: f64, end_angle: f64) -> Self {
        Self {
            center,
            radius,
            start_angle,
            end_angle,
        }
    }

    /// Construct from `KiCad`'s 3-point form. Returns `None` if the three
    /// points are colinear (no unique circle through them).
    #[must_use]
    pub fn from_three_points(start: Point, mid: Point, end: Point) -> Option<Self> {
        // Standard circumscribed-circle formula:
        // center = intersection of perpendicular bisectors of (start,mid)
        // and (mid,end).
        let ax = start.x;
        let ay = start.y;
        let bx = mid.x;
        let by = mid.y;
        let cx = end.x;
        let cy = end.y;
        let d = 2.0 * (ax * (by - cy) + bx * (cy - ay) + cx * (ay - by));
        if d.abs() < f64::EPSILON {
            return None;
        }
        let ux = ((ax * ax + ay * ay) * (by - cy)
            + (bx * bx + by * by) * (cy - ay)
            + (cx * cx + cy * cy) * (ay - by))
            / d;
        let uy = ((ax * ax + ay * ay) * (cx - bx)
            + (bx * bx + by * by) * (ax - cx)
            + (cx * cx + cy * cy) * (bx - ax))
            / d;
        let center = Point::new(ux, uy);
        let radius = center.distance_to(&start);
        let start_angle = (start.y - center.y).atan2(start.x - center.x);
        let end_angle = (end.y - center.y).atan2(end.x - center.x);
        Some(Self {
            center,
            radius,
            start_angle,
            end_angle,
        })
    }

    /// The arc's sweep, normalized to `(0, TAU]`.
    #[must_use]
    pub fn sweep(&self) -> f64 {
        let raw = self.end_angle - self.start_angle;
        let m = raw.rem_euclid(TAU);
        if m == 0.0 {
            TAU
        } else {
            m
        }
    }

    /// Bounding box of the arc itself (NOT the whole circle). Computes
    /// it by sampling the endpoints plus any of the cardinal angles
    /// (0, π/2, π, 3π/2) that lie within the sweep.
    #[must_use]
    pub fn bounding_box(&self) -> BBox {
        let start = self.point_at(self.start_angle);
        let end = self.point_at(self.end_angle);
        let mut bbox = BBox::from_point(start).union(&BBox::from_point(end));
        for k in 0..4_i32 {
            let a = f64::from(k) * std::f64::consts::FRAC_PI_2;
            if self.contains_angle(a) {
                bbox = bbox.union(&BBox::from_point(self.point_at(a)));
            }
        }
        bbox
    }

    fn point_at(&self, angle: f64) -> Point {
        Point::new(
            self.center.x + self.radius * angle.cos(),
            self.center.y + self.radius * angle.sin(),
        )
    }

    fn contains_angle(&self, angle: f64) -> bool {
        // Test whether `angle` lies in [start_angle, end_angle] taking the
        // CCW direction. Both ends inclusive (matters for bbox correctness).
        let s = self.start_angle.rem_euclid(TAU);
        let e = self.end_angle.rem_euclid(TAU);
        let a = angle.rem_euclid(TAU);
        if s <= e {
            a >= s && a <= e
        } else {
            // Wraps around 0.
            a >= s || a <= e
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::f64::consts::PI;

    #[test]
    fn smoke_quarter_arc_bbox() {
        // Quarter arc centered at origin, radius 5, from 0 to π/2.
        let a = Arc::new(Point::new(0.0, 0.0), 5.0, 0.0, std::f64::consts::FRAC_PI_2);
        let bb = a.bounding_box();
        // Arc sweeps from (5, 0) to (0, 5). Includes both endpoints; no
        // cardinal extremes interior to the open quarter (start is at 0
        // and end is at π/2, both endpoints — contains_angle returns true
        // for boundary, so 0 and π/2 are sampled; π and 3π/2 are not).
        assert!((bb.min.x - 0.0).abs() < 1e-9);
        assert!((bb.min.y - 0.0).abs() < 1e-9);
        assert!((bb.max.x - 5.0).abs() < 1e-9);
        assert!((bb.max.y - 5.0).abs() < 1e-9);
    }

    #[test]
    fn smoke_half_arc_bbox_includes_top() {
        // Half arc, radius 1, from 0 to π. Should include (0, 1) at top.
        let a = Arc::new(Point::new(0.0, 0.0), 1.0, 0.0, PI);
        let bb = a.bounding_box();
        assert!((bb.max.y - 1.0).abs() < 1e-9);
        assert!((bb.min.y - 0.0).abs() < 1e-9);
    }

    #[test]
    fn smoke_three_points_recovers_circle() {
        let a = Arc::from_three_points(
            Point::new(1.0, 0.0),
            Point::new(0.0, 1.0),
            Point::new(-1.0, 0.0),
        )
        .expect("not colinear");
        assert!((a.center.x).abs() < 1e-9);
        assert!((a.center.y).abs() < 1e-9);
        assert!((a.radius - 1.0).abs() < 1e-9);
    }

    #[test]
    fn smoke_three_points_colinear_returns_none() {
        assert!(Arc::from_three_points(
            Point::new(0.0, 0.0),
            Point::new(1.0, 0.0),
            Point::new(2.0, 0.0)
        )
        .is_none());
    }
}
