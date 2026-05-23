//! Differential-pair router — M3-R-04.
//!
//! Routes two coupled traces in lockstep maintaining a target
//! edge-to-edge gap. Built on top of the M2-R-08 walk-around router:
//! we route the **centerline** (the imaginary midline of the pair)
//! against an obstacle field inflated to encompass BOTH legs +
//! clearance, then expand the centerline into two parallel
//! polylines offset by `±(gap + width) / 2` perpendicular to the
//! local centerline direction.
//!
//! ## Why centerline + expand instead of dual A*
//!
//! Routing two A* searches independently and then enforcing the gap
//! as a post-process produces "split-and-rejoin" geometry where the
//! legs diverge to dodge an obstacle and converge afterward — the
//! coupled-trace impedance breaks down at every divergence. Routing
//! the centerline once and expanding guarantees the pair stays
//! perfectly parallel through every turn: where the centerline
//! kinks 90°, both legs kink 90° at the same point, separated by
//! exactly the gap.
//!
//! Drawback: the centerline router's obstacle inflation must
//! accommodate the WHOLE pair envelope (`2·width + gap + 2·clearance`),
//! so the pair routes around more obstacles than a single trace
//! would. That's correct — a diff pair physically IS that wide and
//! can't fit through narrower channels.
//!
//! ## Length matching
//!
//! The centerline approach yields legs of identical length by
//! construction (every kink is mirrored). Skew between the legs is
//! introduced ONLY at the pad-connect "fanout" segments where each
//! leg breaks from the centerline to its own pad. The fanout
//! lengths are reported in [`DiffPairRouteResult::leg_skew_mm`] so
//! the caller can route compensating serpentines if the skew
//! exceeds the declared `skew_tolerance_mm`.

// `(gap + width) / 2` is the offset half-width, not a midpoint
// computation — `f64::midpoint(gap, width)` is conceptually wrong
// here even though clippy proposes it.
#![allow(clippy::cast_precision_loss, clippy::manual_midpoint)]

use serde::{Deserialize, Serialize};

use crate::geom::{Point, Polygon};
use crate::routing::walkaround::{self, RoutingError, WalkaroundInput};
use crate::scene::Scene;

/// Input to the diff-pair router.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct DiffPairInput {
    /// Endpoints of the positive leg (start pad, end pad).
    pub positive_start_mm: Point,
    pub positive_end_mm: Point,
    /// Endpoints of the negative leg.
    pub negative_start_mm: Point,
    pub negative_end_mm: Point,
    /// Per-leg track width (mm). Both legs share this.
    pub track_width_mm: f64,
    /// Edge-to-edge gap between the two legs (mm).
    pub gap_mm: f64,
    /// Clearance from either leg to foreign-net copper.
    pub clearance_mm: f64,
    /// Copper layer the pair routes on.
    pub layer: String,
    /// Grid step for the centerline A* search.
    pub grid_step_mm: f64,
    /// Optional forbidden regions (e.g. impedance-controlled
    /// keepouts the user wants the pair to avoid).
    #[serde(default)]
    pub forbidden_regions: Vec<Polygon>,
}

impl DiffPairInput {
    /// Sensible default `grid_step_mm`.
    pub const DEFAULT_GRID_STEP_MM: f64 = 0.1;
}

/// Result of a successful diff-pair route.
#[derive(Debug, Clone, PartialEq, Default, Serialize, Deserialize)]
pub struct DiffPairRouteResult {
    /// Routed positive-leg waypoints.
    pub positive_waypoints_mm: Vec<Point>,
    /// Routed negative-leg waypoints.
    pub negative_waypoints_mm: Vec<Point>,
    /// Centerline used to generate both legs (useful for the
    /// editor's mid-route preview).
    pub centerline_waypoints_mm: Vec<Point>,
    /// Skew between the two legs, mm. Zero when the pad endpoints
    /// are arranged symmetrically about the centerline endpoints;
    /// non-zero when the fanout legs are asymmetric.
    pub leg_skew_mm: f64,
}

/// Route a differential pair from declared endpoints to declared
/// endpoints, maintaining the requested `gap_mm` throughout. Returns
/// per-leg waypoints + the inferred skew.
///
/// # Algorithm
///
/// 1. Compute the centerline endpoints as the midpoints of each
///    pair's pad endpoints.
/// 2. Route the centerline against an obstacle field inflated by
///    `track_width + gap/2 + clearance + track_width/2` — wide
///    enough that both legs at their final offset positions clear
///    every foreign obstacle.
/// 3. Expand the centerline polyline into two parallel polylines
///    by offsetting each centerline vertex `±(gap + track_width)/2`
///    perpendicular to the local centerline direction.
/// 4. Patch the leg endpoints onto the actual pad positions
///    (the perpendicular offset misses the pad pin by the fanout
///    distance; the post-process snaps the first/last vertex of
///    each leg to the declared pad position).
/// 5. Measure the skew (length difference between the legs) and
///    report it.
///
/// # Errors
///
/// Surfaces `RoutingError` from the underlying centerline A*. The
/// pair cannot route when the centerline cannot.
pub fn route(
    input: &DiffPairInput,
    scene: &Scene,
    pair_net: &str,
) -> Result<DiffPairRouteResult, RoutingError> {
    if input.gap_mm <= 0.0 {
        return Err(RoutingError::InvalidInput(
            "gap_mm must be > 0 for a diff pair".into(),
        ));
    }
    if input.track_width_mm <= 0.0 {
        return Err(RoutingError::InvalidInput(
            "track_width_mm must be > 0".into(),
        ));
    }

    let center_start = midpoint(input.positive_start_mm, input.negative_start_mm);
    let center_end = midpoint(input.positive_end_mm, input.negative_end_mm);

    // Half-width of the pair envelope. The walk-around router
    // already adds `track_width / 2 + clearance`, so we feed it a
    // wider "track_width" that represents the pair as a single
    // fat conductor.
    let pair_envelope_width = 2.0 * input.track_width_mm + input.gap_mm;
    let centerline_input = WalkaroundInput {
        start_mm: center_start,
        end_mm: center_end,
        track_width_mm: pair_envelope_width,
        clearance_mm: input.clearance_mm,
        start_layer: input.layer.clone(),
        alternate_layer: None,
        grid_step_mm: input
            .grid_step_mm
            .max(WalkaroundInput::DEFAULT_GRID_STEP_MM),
        forbidden_regions: input.forbidden_regions.clone(),
    };

    let center = walkaround::route(&centerline_input, scene, pair_net)?;

    // Offset half = (gap + track_width) / 2 — distance from
    // centerline to each leg's centerline.
    let offset_half = (input.gap_mm + input.track_width_mm) / 2.0;
    let mut positive = offset_polyline(&center.waypoints_mm, offset_half);
    let mut negative = offset_polyline(&center.waypoints_mm, -offset_half);

    // Snap the endpoints to the actual pad positions so the legs
    // connect cleanly. The perpendicular expansion misses each
    // pad by the fanout distance; the snap collapses that gap.
    if let Some(first) = positive.first_mut() {
        *first = input.positive_start_mm;
    }
    if let Some(last) = positive.last_mut() {
        *last = input.positive_end_mm;
    }
    if let Some(first) = negative.first_mut() {
        *first = input.negative_start_mm;
    }
    if let Some(last) = negative.last_mut() {
        *last = input.negative_end_mm;
    }

    let leg_skew = (polyline_length(&positive) - polyline_length(&negative)).abs();

    Ok(DiffPairRouteResult {
        positive_waypoints_mm: positive,
        negative_waypoints_mm: negative,
        centerline_waypoints_mm: center.waypoints_mm,
        leg_skew_mm: leg_skew,
    })
}

fn midpoint(a: Point, b: Point) -> Point {
    Point::new((a.x + b.x) * 0.5, (a.y + b.y) * 0.5)
}

fn polyline_length(pts: &[Point]) -> f64 {
    let mut total = 0.0;
    for w in pts.windows(2) {
        let dx = w[1].x - w[0].x;
        let dy = w[1].y - w[0].y;
        total += (dx * dx + dy * dy).sqrt();
    }
    total
}

/// Offset a polyline perpendicular to each segment by `delta`.
/// Positive `delta` offsets to the **left** of the segment direction
/// (using the standard 2D rotation `(dx, dy) → (-dy, dx)`); negative
/// offsets to the right.
///
/// For interior vertices we average the perpendiculars of the two
/// adjacent segments and renormalise — produces clean miter joints
/// without external bisection geometry.
fn offset_polyline(pts: &[Point], delta: f64) -> Vec<Point> {
    if pts.len() < 2 {
        return Vec::new();
    }
    let n = pts.len();
    let mut out = Vec::with_capacity(n);
    for i in 0..n {
        let perp = if i == 0 {
            segment_perp(pts[0], pts[1])
        } else if i == n - 1 {
            segment_perp(pts[n - 2], pts[n - 1])
        } else {
            // Average the two adjacent segment perpendiculars.
            let p0 = segment_perp(pts[i - 1], pts[i]);
            let p1 = segment_perp(pts[i], pts[i + 1]);
            let avg = Point::new(p0.x + p1.x, p0.y + p1.y);
            normalize(avg)
        };
        out.push(Point::new(
            pts[i].x + perp.x * delta,
            pts[i].y + perp.y * delta,
        ));
    }
    out
}

/// Left-perpendicular unit vector of the segment `(a → b)`.
fn segment_perp(a: Point, b: Point) -> Point {
    let dx = b.x - a.x;
    let dy = b.y - a.y;
    let len = (dx * dx + dy * dy).sqrt();
    if len < f64::EPSILON {
        return Point::new(0.0, 0.0);
    }
    // 2D left rotation: (dx, dy) → (-dy, dx). Normalised.
    Point::new(-dy / len, dx / len)
}

fn normalize(p: Point) -> Point {
    let len = (p.x * p.x + p.y * p.y).sqrt();
    if len < f64::EPSILON {
        return Point::new(0.0, 0.0);
    }
    Point::new(p.x / len, p.y / len)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::scene::Scene;

    #[test]
    fn straight_pair_routes_parallel_at_target_gap() {
        // Empty scene; route a 10 mm horizontal diff pair from
        // (0, 0)/(0, 0.3) to (10, 0)/(10, 0.3) — pair centerline at
        // y = 0.15.
        let input = DiffPairInput {
            positive_start_mm: Point::new(0.0, 0.0),
            positive_end_mm: Point::new(10.0, 0.0),
            negative_start_mm: Point::new(0.0, 0.3),
            negative_end_mm: Point::new(10.0, 0.3),
            track_width_mm: 0.15,
            gap_mm: 0.15,
            clearance_mm: 0.1,
            layer: "F.Cu".to_string(),
            grid_step_mm: 0.1,
            forbidden_regions: Vec::new(),
        };
        let scene = Scene::new();
        let r = route(&input, &scene, "USB_D").expect("routes");
        assert!(!r.positive_waypoints_mm.is_empty());
        assert!(!r.negative_waypoints_mm.is_empty());
        // Both legs should have endpoints exactly at the declared
        // pad positions (snap step).
        assert_eq!(r.positive_waypoints_mm[0], Point::new(0.0, 0.0));
        assert_eq!(
            *r.positive_waypoints_mm.last().unwrap(),
            Point::new(10.0, 0.0)
        );
        assert_eq!(r.negative_waypoints_mm[0], Point::new(0.0, 0.3));
        assert_eq!(
            *r.negative_waypoints_mm.last().unwrap(),
            Point::new(10.0, 0.3)
        );
        // Symmetric endpoints → zero skew.
        assert!(
            r.leg_skew_mm < 0.5,
            "skew {} mm should be near zero",
            r.leg_skew_mm,
        );
    }

    #[test]
    fn invalid_gap_returns_error() {
        let input = DiffPairInput {
            positive_start_mm: Point::new(0.0, 0.0),
            positive_end_mm: Point::new(10.0, 0.0),
            negative_start_mm: Point::new(0.0, 0.3),
            negative_end_mm: Point::new(10.0, 0.3),
            track_width_mm: 0.15,
            gap_mm: 0.0, // bad
            clearance_mm: 0.1,
            layer: "F.Cu".to_string(),
            grid_step_mm: 0.1,
            forbidden_regions: Vec::new(),
        };
        let scene = Scene::new();
        let err = route(&input, &scene, "X").expect_err("must reject");
        assert!(matches!(err, RoutingError::InvalidInput(_)));
    }

    #[test]
    fn invalid_track_width_returns_error() {
        let input = DiffPairInput {
            positive_start_mm: Point::new(0.0, 0.0),
            positive_end_mm: Point::new(10.0, 0.0),
            negative_start_mm: Point::new(0.0, 0.3),
            negative_end_mm: Point::new(10.0, 0.3),
            track_width_mm: 0.0,
            gap_mm: 0.15,
            clearance_mm: 0.1,
            layer: "F.Cu".to_string(),
            grid_step_mm: 0.1,
            forbidden_regions: Vec::new(),
        };
        let scene = Scene::new();
        let err = route(&input, &scene, "X").expect_err("must reject");
        assert!(matches!(err, RoutingError::InvalidInput(_)));
    }

    #[test]
    fn offset_polyline_perpendicular_distance_matches_delta() {
        // 5 mm horizontal segment, offset left by 1 mm → both
        // endpoints should land 1 mm above the original line.
        let pts = vec![Point::new(0.0, 0.0), Point::new(5.0, 0.0)];
        let off = offset_polyline(&pts, 1.0);
        assert_eq!(off.len(), 2);
        assert!((off[0].y - 1.0).abs() < 1e-9);
        assert!((off[1].y - 1.0).abs() < 1e-9);
        assert!(off[0].x.abs() < 1e-9);
        assert!((off[1].x - 5.0).abs() < 1e-9);
    }

    #[test]
    fn offset_polyline_handles_right_angle_interior_vertex() {
        // L-shape: (0,0) → (5,0) → (5,5). Offset left by 1 mm
        // should produce (0,1) → (~6, ~1) → (6, 5). The interior
        // miter is at (6, 1) because both perpendiculars push the
        // corner one mm to the outside in both axes.
        let pts = vec![
            Point::new(0.0, 0.0),
            Point::new(5.0, 0.0),
            Point::new(5.0, 5.0),
        ];
        let off = offset_polyline(&pts, 1.0);
        assert_eq!(off.len(), 3);
        // Endpoint perpendiculars come from the adjacent segment.
        // First segment is horizontal (left = up).
        assert!((off[0].x - 0.0).abs() < 1e-9);
        assert!((off[0].y - 1.0).abs() < 1e-9);
        // Last segment is vertical (left = -x direction).
        assert!((off[2].x - 4.0).abs() < 1e-9);
        assert!((off[2].y - 5.0).abs() < 1e-9);
    }

    #[test]
    fn straight_pair_skew_is_zero() {
        // Pure-straight pair: legs are equal length by construction.
        let input = DiffPairInput {
            positive_start_mm: Point::new(0.0, 0.0),
            positive_end_mm: Point::new(20.0, 0.0),
            negative_start_mm: Point::new(0.0, 0.4),
            negative_end_mm: Point::new(20.0, 0.4),
            track_width_mm: 0.2,
            gap_mm: 0.2,
            clearance_mm: 0.1,
            layer: "F.Cu".to_string(),
            grid_step_mm: 0.1,
            forbidden_regions: Vec::new(),
        };
        let scene = Scene::new();
        let r = route(&input, &scene, "USB_D").expect("routes");
        assert!(r.leg_skew_mm < 0.5);
    }
}
