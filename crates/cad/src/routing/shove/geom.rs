//! 2-D segment geometry for the push-and-shove router — M3-R-03.
//!
//! The shove algorithm's atom is [`push_vector`]: given a *head*
//! segment (the track being routed) and an *obstacle* segment (an
//! existing shovable track), compute the minimum perpendicular
//! displacement to apply to the obstacle so the two thick
//! centerlines clear `required_clearance`.
//!
//! Everything here is straight-segment-only — arcs are deferred to a
//! later `PnS` milestone per the v1 scope. The functions are pure +
//! allocation-free so the shove loop can call them in its hot path
//! without GC pressure.

use crate::geom::Point;

/// A 2-D displacement. Distinct from `Point` so a "move this item
/// by Δ" never gets confused with "the item is at position Δ".
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Vec2 {
    pub dx: f64,
    pub dy: f64,
}

impl Vec2 {
    pub const ZERO: Self = Self { dx: 0.0, dy: 0.0 };

    #[must_use]
    pub const fn new(dx: f64, dy: f64) -> Self {
        Self { dx, dy }
    }

    #[must_use]
    pub fn length(&self) -> f64 {
        (self.dx * self.dx + self.dy * self.dy).sqrt()
    }

    #[must_use]
    pub fn length_squared(&self) -> f64 {
        self.dx * self.dx + self.dy * self.dy
    }

    /// Unit vector in the same direction, or `None` when this vector
    /// is (near-)zero-length and has no defined direction.
    #[must_use]
    pub fn normalized(&self) -> Option<Self> {
        let len = self.length();
        if len < EPS {
            return None;
        }
        Some(Self {
            dx: self.dx / len,
            dy: self.dy / len,
        })
    }

    #[must_use]
    pub fn scaled(&self, factor: f64) -> Self {
        Self {
            dx: self.dx * factor,
            dy: self.dy * factor,
        }
    }
}

/// Apply a displacement to a point.
#[must_use]
pub fn translate(p: Point, v: Vec2) -> Point {
    Point::new(p.x + v.dx, p.y + v.dy)
}

/// Floating-point tolerance. Distances below this are treated as
/// zero — at 1 nm it's three orders of magnitude under the tightest
/// fab grid (1 µm) so it never swallows a real geometric difference.
pub const EPS: f64 = 1e-6;

#[must_use]
fn sub(a: Point, b: Point) -> Vec2 {
    Vec2::new(a.x - b.x, a.y - b.y)
}

#[must_use]
fn dot(a: Vec2, b: Vec2) -> f64 {
    a.dx * b.dx + a.dy * b.dy
}

/// Closest point on segment `[s0, s1]` to the query point `p`, as a
/// parametric `t ∈ [0, 1]` plus the point itself.
#[must_use]
pub fn closest_on_segment(p: Point, s0: Point, s1: Point) -> (f64, Point) {
    let d = sub(s1, s0);
    let len_sq = d.length_squared();
    if len_sq < EPS * EPS {
        // Degenerate segment — both endpoints coincide.
        return (0.0, s0);
    }
    let t = (dot(sub(p, s0), d) / len_sq).clamp(0.0, 1.0);
    (t, Point::new(s0.x + d.dx * t, s0.y + d.dy * t))
}

/// Minimum distance between two segments `[a0, a1]` and `[b0, b1]`,
/// plus the closest pair of points (one on each segment).
///
/// Handles the four sub-cases (endpoint/endpoint, endpoint/interior,
/// crossing) uniformly by sampling each endpoint against the other
/// segment and, when the segments are non-parallel and cross, taking
/// the zero-distance intersection.
#[must_use]
pub fn segment_segment_closest(a0: Point, a1: Point, b0: Point, b1: Point) -> (f64, Point, Point) {
    // If the segments intersect, the distance is zero at the
    // intersection point.
    if let Some(hit) = segment_intersection(a0, a1, b0, b1) {
        return (0.0, hit, hit);
    }
    // Otherwise the minimum is achieved at one of the four
    // endpoint-to-other-segment projections. Seed `best` with the
    // first so there's no Option / no panic path.
    let (_, p) = closest_on_segment(a0, b0, b1);
    let mut best: (f64, Point, Point) = (a0.distance_to(&p), a0, p);
    let mut consider = |pa: Point, pb: Point| {
        let d = pa.distance_to(&pb);
        if d < best.0 {
            best = (d, pa, pb);
        }
    };
    let (_, p) = closest_on_segment(a1, b0, b1);
    consider(a1, p);
    let (_, p) = closest_on_segment(b0, a0, a1);
    consider(p, b0);
    let (_, p) = closest_on_segment(b1, a0, a1);
    consider(p, b1);
    best
}

/// Minimum centerline distance between two segments.
#[must_use]
pub fn segment_segment_distance(a0: Point, a1: Point, b0: Point, b1: Point) -> f64 {
    segment_segment_closest(a0, a1, b0, b1).0
}

/// Intersection point of two segments, or `None` if they don't cross.
/// Returns the crossing point for proper (non-parallel) intersections.
#[must_use]
pub fn segment_intersection(a0: Point, a1: Point, b0: Point, b1: Point) -> Option<Point> {
    let r = sub(a1, a0);
    let s = sub(b1, b0);
    let denom = r.dx * s.dy - r.dy * s.dx;
    if denom.abs() < EPS {
        // Parallel or collinear — no single crossing point. Collinear
        // overlap is handled by the endpoint projections in
        // `segment_segment_closest`, so report "no proper crossing".
        return None;
    }
    let qp = sub(b0, a0);
    let t = (qp.dx * s.dy - qp.dy * s.dx) / denom;
    let u = (qp.dx * r.dy - qp.dy * r.dx) / denom;
    if (0.0..=1.0).contains(&t) && (0.0..=1.0).contains(&u) {
        return Some(Point::new(a0.x + r.dx * t, a0.y + r.dy * t));
    }
    None
}

/// The displacement to apply to the **obstacle** segment so its
/// centerline clears the **head** segment's centerline by
/// `required_clearance`.
///
/// Returns:
/// - `None` when the two segments are already at or beyond the
///   required clearance (no shove needed).
/// - `Some(Vec2::ZERO)` is never returned — a zero-length push means
///   "already clear", which is the `None` case. Any returned vector
///   has positive length.
///
/// The push direction is the unit vector from the head's closest
/// point toward the obstacle's closest point (i.e. away from the
/// head). When the two closest points coincide (segments cross or
/// touch), the push direction falls back to the head segment's
/// left-hand normal so a crossing obstacle is still pushed off the
/// head deterministically.
///
/// Magnitude is `required_clearance - current_distance` — exactly
/// enough to reach the clearance boundary, no more (the shove loop
/// adds its own margin if it wants hysteresis).
#[must_use]
pub fn push_vector(
    head0: Point,
    head1: Point,
    obstacle0: Point,
    obstacle1: Point,
    required_clearance: f64,
) -> Option<Vec2> {
    let (dist, head_pt, obs_pt) = segment_segment_closest(head0, head1, obstacle0, obstacle1);
    if dist >= required_clearance - EPS {
        return None;
    }
    let needed = required_clearance - dist;
    // Direction: from head's closest point toward the obstacle's.
    let away = sub(obs_pt, head_pt);
    let dir = away.normalized().unwrap_or_else(|| {
        // Closest points coincide (crossing / touching). Push along
        // the head segment's left normal: rotate the head direction
        // +90°. If the head is itself degenerate, push +x.
        let head_dir = sub(head1, head0);
        head_dir
            .normalized()
            .map_or(Vec2::new(1.0, 0.0), |u| Vec2::new(-u.dy, u.dx))
    });
    Some(dir.scaled(needed))
}

#[cfg(test)]
#[allow(clippy::float_cmp)]
mod tests {
    use super::*;

    fn pt(x: f64, y: f64) -> Point {
        Point::new(x, y)
    }

    #[test]
    fn closest_on_segment_clamps_to_endpoints() {
        let (t, p) = closest_on_segment(pt(-5.0, 0.0), pt(0.0, 0.0), pt(10.0, 0.0));
        assert_eq!(t, 0.0);
        assert_eq!(p, pt(0.0, 0.0));
        let (t, p) = closest_on_segment(pt(15.0, 0.0), pt(0.0, 0.0), pt(10.0, 0.0));
        assert_eq!(t, 1.0);
        assert_eq!(p, pt(10.0, 0.0));
    }

    #[test]
    fn closest_on_segment_projects_interior() {
        let (t, p) = closest_on_segment(pt(3.0, 4.0), pt(0.0, 0.0), pt(10.0, 0.0));
        assert!((t - 0.3).abs() < 1e-9);
        assert_eq!(p, pt(3.0, 0.0));
    }

    #[test]
    fn parallel_segments_distance_is_gap() {
        // Two horizontal segments 2 mm apart.
        let d = segment_segment_distance(pt(0.0, 0.0), pt(10.0, 0.0), pt(0.0, 2.0), pt(10.0, 2.0));
        assert!((d - 2.0).abs() < 1e-9, "got {d}");
    }

    #[test]
    fn crossing_segments_distance_is_zero() {
        let d =
            segment_segment_distance(pt(0.0, 0.0), pt(10.0, 10.0), pt(0.0, 10.0), pt(10.0, 0.0));
        assert!(d < 1e-9, "got {d}");
    }

    #[test]
    fn perpendicular_t_distance() {
        // Head along x; obstacle perpendicular, its closest endpoint
        // 1.5 mm above the head.
        let d = segment_segment_distance(pt(0.0, 0.0), pt(10.0, 0.0), pt(5.0, 1.5), pt(5.0, 8.0));
        assert!((d - 1.5).abs() < 1e-9, "got {d}");
    }

    #[test]
    fn segment_intersection_finds_crossing() {
        let hit = segment_intersection(pt(0.0, 0.0), pt(10.0, 10.0), pt(0.0, 10.0), pt(10.0, 0.0));
        assert!(hit.is_some());
        let h = hit.unwrap();
        assert!((h.x - 5.0).abs() < 1e-9 && (h.y - 5.0).abs() < 1e-9);
    }

    #[test]
    fn segment_intersection_none_for_parallel() {
        assert!(
            segment_intersection(pt(0.0, 0.0), pt(10.0, 0.0), pt(0.0, 2.0), pt(10.0, 2.0))
                .is_none()
        );
    }

    #[test]
    fn segment_intersection_none_when_not_reaching() {
        // Non-parallel but the segments are short enough not to meet.
        assert!(
            segment_intersection(pt(0.0, 0.0), pt(1.0, 0.0), pt(5.0, -5.0), pt(5.0, 5.0)).is_none()
        );
    }

    #[test]
    fn push_vector_none_when_already_clear() {
        // 2 mm apart, need 1 mm clearance → already clear.
        let v = push_vector(
            pt(0.0, 0.0),
            pt(10.0, 0.0),
            pt(0.0, 2.0),
            pt(10.0, 2.0),
            1.0,
        );
        assert!(v.is_none());
    }

    #[test]
    fn push_vector_none_at_exact_clearance() {
        // Exactly 1 mm apart, need 1 mm → no shove.
        let v = push_vector(
            pt(0.0, 0.0),
            pt(10.0, 0.0),
            pt(0.0, 1.0),
            pt(10.0, 1.0),
            1.0,
        );
        assert!(v.is_none());
    }

    #[test]
    fn push_vector_parallel_overlap_pushes_perpendicular_by_shortfall() {
        // 0.4 mm apart, need 1.0 mm → push the obstacle +0.6 mm in y
        // (away from the head, which is below it).
        let v = push_vector(
            pt(0.0, 0.0),
            pt(10.0, 0.0),
            pt(0.0, 0.4),
            pt(10.0, 0.4),
            1.0,
        )
        .expect("shove needed");
        assert!(
            (v.dx).abs() < 1e-9,
            "push should be vertical, got dx={}",
            v.dx
        );
        assert!(
            (v.dy - 0.6).abs() < 1e-9,
            "push dy should be 0.6, got {}",
            v.dy
        );
    }

    #[test]
    fn push_vector_applied_actually_reaches_clearance() {
        let h0 = pt(0.0, 0.0);
        let h1 = pt(10.0, 0.0);
        let o0 = pt(0.0, 0.4);
        let o1 = pt(10.0, 0.4);
        let v = push_vector(h0, h1, o0, o1, 1.0).expect("shove needed");
        let n0 = translate(o0, v);
        let n1 = translate(o1, v);
        let new_dist = segment_segment_distance(h0, h1, n0, n1);
        assert!(
            (new_dist - 1.0).abs() < 1e-6,
            "after shove dist={new_dist}, want 1.0"
        );
    }

    #[test]
    fn push_vector_crossing_falls_back_to_head_normal() {
        // Head along +x; obstacle crosses it. Closest points coincide
        // → push along head's left normal (+y).
        let v = push_vector(
            pt(0.0, 0.0),
            pt(10.0, 0.0),
            pt(5.0, -2.0),
            pt(5.0, 2.0),
            1.0,
        )
        .expect("crossing always needs a shove");
        assert!(v.length() > 0.0);
        // Head dir is +x → left normal is +y. The push must have a
        // non-trivial y-component (it's along ±y for a vertical
        // crossing obstacle).
        assert!(v.dy.abs() > 1e-9);
    }

    #[test]
    fn vec2_normalized_none_for_zero() {
        assert!(Vec2::ZERO.normalized().is_none());
        let u = Vec2::new(3.0, 4.0).normalized().unwrap();
        assert!((u.length() - 1.0).abs() < 1e-9);
    }
}
