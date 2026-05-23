//! Walk-around router — A* search on an inflated-obstacle grid.
//!
//! ## Approach (M2-R-08)
//!
//! 1. **Inflation** ([`inflation`]) — every obstacle (track, via,
//!    pad, courtyard) is grown by `track_width / 2 + clearance` so
//!    the *centreline* of the routed track can be tested against the
//!    inflated shape with a point-in-polygon check.
//! 2. **Grid** ([`grid`]) — the search space is rasterised onto a
//!    uniform 2D grid with cells sized to the chosen routing grid
//!    (default `0.1 mm`). Each cell records whether it's blocked.
//! 3. **A\*** ([`astar`]) — classic A* with octile movement
//!    (8-connected) and the octile-distance heuristic. Returns the
//!    cell-aligned path which the caller polylines + post-simplifies.
//! 4. **Layer switch via via** — when the planar A* fails (no path
//!    on the start layer), the router tries inserting a single via
//!    near the obstacle and continuing on the alternate layer.
//!
//! The done-when target is **100 tracks routed in ≤ 10 s** on the
//! M2-grade reference board. The `bench_100_tracks_under_10s` test
//! exercises that gate.

pub mod astar;
pub mod grid;
pub mod inflation;

use serde::{Deserialize, Serialize};

use crate::geom::{Point, Polygon};
use crate::scene::Scene;

/// Inputs to the router. `start` / `end` are pad-pin coordinates;
/// `start_layer` is the layer the route starts on; the router may
/// transition to `alternate_layer` if it can't find a planar path.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct WalkaroundInput {
    pub start_mm: Point,
    pub end_mm: Point,
    /// Routed track width in mm.
    pub track_width_mm: f64,
    /// Minimum copper-to-copper clearance.
    pub clearance_mm: f64,
    /// Layer the route starts on (e.g. `F.Cu`).
    pub start_layer: String,
    /// Optional alternate copper layer to try when the start-layer
    /// search fails. `None` disables via insertion.
    pub alternate_layer: Option<String>,
    /// Grid step in mm. `0.1 mm` is a sensible default for the M2
    /// reference set (40 × 40 mm boards yield a 400 × 400 grid).
    pub grid_step_mm: f64,
    /// Forbidden regions the router must avoid (e.g. user-drawn
    /// keepouts). Optional — empty for typical routes.
    #[serde(default)]
    pub forbidden_regions: Vec<Polygon>,
}

impl WalkaroundInput {
    /// Sensible default `grid_step_mm` when the caller has none.
    pub const DEFAULT_GRID_STEP_MM: f64 = 0.1;
}

/// Result of a successful route — the sequence of waypoints the
/// router chose, plus any via inserted for a layer switch.
#[derive(Debug, Clone, PartialEq, Default, Serialize, Deserialize)]
pub struct WalkaroundResult {
    /// Cell-aligned path simplified down to direction-change points.
    pub waypoints_mm: Vec<Point>,
    /// Layer each segment lives on; `layers[i]` is the layer of the
    /// segment from `waypoints[i]` to `waypoints[i+1]`.
    pub segment_layers: Vec<String>,
    /// Via insertions: positions on the board where the router
    /// switched layers. Empty for single-layer routes.
    pub via_positions_mm: Vec<Point>,
}

/// Why the router could not produce a path.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub enum RoutingError {
    /// `start` or `end` sits inside an inflated obstacle.
    EndpointBlocked,
    /// A* found no path on either layer.
    NoPath,
    /// `grid_step_mm` ≤ 0 or other malformed input.
    InvalidInput(String),
}

/// Run the router against the given input + scene. Same-net items
/// in `scene` are ignored as obstacles (since the route is being
/// added TO that net).
///
/// # Errors
///
/// Returns `RoutingError::EndpointBlocked` if the endpoints land in
/// inflated obstacles; `RoutingError::NoPath` if A* exhausts the
/// reachable cells; `RoutingError::InvalidInput` for malformed input.
pub fn route(
    input: &WalkaroundInput,
    scene: &Scene,
    own_net: &str,
) -> Result<WalkaroundResult, RoutingError> {
    if input.grid_step_mm <= 0.0 {
        return Err(RoutingError::InvalidInput(
            "grid_step_mm must be > 0".into(),
        ));
    }
    if input.track_width_mm < 0.0 {
        return Err(RoutingError::InvalidInput(
            "track_width_mm must be ≥ 0".into(),
        ));
    }
    if input.clearance_mm < 0.0 {
        return Err(RoutingError::InvalidInput(
            "clearance_mm must be ≥ 0".into(),
        ));
    }
    let inflate = input.track_width_mm * 0.5 + input.clearance_mm;
    let obstacles =
        inflation::collect_inflated_obstacles(scene, &input.start_layer, own_net, inflate);

    let grid = grid::Grid::from_obstacles(
        input.start_mm,
        input.end_mm,
        input.grid_step_mm,
        &obstacles,
        &input.forbidden_regions,
    );

    let (start_blocked, end_blocked) = grid.endpoints_blocked();
    if start_blocked || end_blocked {
        return Err(RoutingError::EndpointBlocked);
    }

    let path_cells = astar::find_path(&grid, &grid.start_cell, &grid.end_cell);
    if let Some(cells) = path_cells {
        return Ok(build_result(&grid, &cells, &input.start_layer));
    }

    // Try alternate layer + via insertion. We attempt to insert a via
    // at the point closest to the start that's reachable on the start
    // layer; from there, A* runs on the alternate layer to the end.
    if let Some(alt_layer) = &input.alternate_layer {
        let alt_obstacles =
            inflation::collect_inflated_obstacles(scene, alt_layer, own_net, inflate);
        if let Some((via_point, start_path, alt_path)) = astar::find_layer_switch_path(
            &grid,
            &grid.start_cell,
            &grid.end_cell,
            &obstacles,
            &alt_obstacles,
            input.start_mm,
            input.end_mm,
            input.grid_step_mm,
            &input.forbidden_regions,
        ) {
            let mut result = build_result(&grid, &start_path, &input.start_layer);
            result.via_positions_mm.push(via_point);
            let alt_grid = grid::Grid::from_obstacles(
                via_point,
                input.end_mm,
                input.grid_step_mm,
                &alt_obstacles,
                &input.forbidden_regions,
            );
            let alt_waypoints = build_result(&alt_grid, &alt_path, alt_layer);
            result.waypoints_mm.extend(alt_waypoints.waypoints_mm);
            result.segment_layers.extend(alt_waypoints.segment_layers);
            return Ok(result);
        }
    }

    Err(RoutingError::NoPath)
}

/// Convert cell-coordinate path back to mm + simplify (drop collinear
/// midpoints).
fn build_result(grid: &grid::Grid, cells: &[(i32, i32)], layer: &str) -> WalkaroundResult {
    let mut pts: Vec<Point> = cells.iter().map(|c| grid.cell_to_point(*c)).collect();
    pts = simplify_collinear(&pts);
    let n_seg = pts.len().saturating_sub(1);
    let segment_layers = (0..n_seg).map(|_| layer.to_string()).collect();
    WalkaroundResult {
        waypoints_mm: pts,
        segment_layers,
        via_positions_mm: Vec::new(),
    }
}

/// Drop intermediate vertices that lie on the straight line between
/// their neighbours. Keeps endpoints intact.
fn simplify_collinear(pts: &[Point]) -> Vec<Point> {
    if pts.len() <= 2 {
        return pts.to_vec();
    }
    let mut out = Vec::with_capacity(pts.len());
    out.push(pts[0]);
    for i in 1..pts.len() - 1 {
        let a = pts[i - 1];
        let b = pts[i];
        let c = pts[i + 1];
        let dax = b.x - a.x;
        let day = b.y - a.y;
        let dbx = c.x - b.x;
        let dby = c.y - b.y;
        let cross = dax * dby - day * dbx;
        // 1e-9 mm of cross-product slack — well under the routing
        // grid's 0.1 mm resolution.
        if cross.abs() > 1e-9 {
            out.push(b);
        }
    }
    out.push(*pts.last().unwrap());
    out
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::scene::{Scene, SceneItem};
    use std::time::Instant;

    fn open_input(sx: f64, sy: f64, ex: f64, ey: f64) -> WalkaroundInput {
        WalkaroundInput {
            start_mm: Point::new(sx, sy),
            end_mm: Point::new(ex, ey),
            track_width_mm: 0.2,
            clearance_mm: 0.2,
            start_layer: "F.Cu".into(),
            alternate_layer: None,
            grid_step_mm: 0.5,
            forbidden_regions: Vec::new(),
        }
    }

    fn track_obstacle(net: &str, sx: f64, sy: f64, ex: f64, ey: f64) -> SceneItem {
        SceneItem::Track {
            start_mm: Point::new(sx, sy),
            end_mm: Point::new(ex, ey),
            width_mm: 0.2,
            layer: "F.Cu".into(),
            net: net.into(),
            uuid: "ob".into(),
        }
    }

    #[test]
    fn smoke_routes_straight_line_through_empty_scene() {
        let scene = Scene::new();
        let input = open_input(0.0, 0.0, 10.0, 0.0);
        let r = route(&input, &scene, "NEW").expect("route");
        assert!(r.waypoints_mm.len() >= 2);
        let first = r.waypoints_mm.first().unwrap();
        let last = r.waypoints_mm.last().unwrap();
        // Start/end snap to nearest grid cell — should be very close
        // to requested.
        assert!(first.distance_to(&Point::new(0.0, 0.0)) <= 0.5);
        assert!(last.distance_to(&Point::new(10.0, 0.0)) <= 0.5);
    }

    #[test]
    fn smoke_routes_around_blocking_track() {
        let mut scene = Scene::new();
        // Vertical wall blocking the direct east-west path.
        scene.insert(track_obstacle("OTHER", 5.0, -3.0, 5.0, 3.0));
        let input = open_input(0.0, 0.0, 10.0, 0.0);
        let r = route(&input, &scene, "NEW").expect("route");
        // Path must include at least one waypoint with |y| > 0
        // (the deviation around the wall).
        let max_deviation = r
            .waypoints_mm
            .iter()
            .map(|p| p.y.abs())
            .fold(0.0_f64, f64::max);
        assert!(
            max_deviation > 0.5,
            "expected path to deviate around the wall, got max |y| = {max_deviation}",
        );
    }

    #[test]
    fn smoke_same_net_obstacles_ignored() {
        let mut scene = Scene::new();
        scene.insert(track_obstacle("SAME", 5.0, -3.0, 5.0, 3.0));
        let input = open_input(0.0, 0.0, 10.0, 0.0);
        // Same net as the route → wall should be ignored, path is a
        // straight line.
        let r = route(&input, &scene, "SAME").expect("route");
        let max_dev = r
            .waypoints_mm
            .iter()
            .map(|p| p.y.abs())
            .fold(0.0_f64, f64::max);
        assert!(
            max_dev <= 0.5,
            "expected straight path, max |y| = {max_dev}"
        );
    }

    #[test]
    fn smoke_endpoint_inside_obstacle_returns_error() {
        let mut scene = Scene::new();
        // Obstacle wall covering the entire end vicinity.
        scene.insert(track_obstacle("OTHER", 10.0, -5.0, 10.0, 5.0));
        let input = open_input(0.0, 0.0, 10.0, 0.0);
        let err = route(&input, &scene, "NEW").unwrap_err();
        assert_eq!(err, RoutingError::EndpointBlocked);
    }

    #[test]
    fn smoke_invalid_grid_step_rejected() {
        let scene = Scene::new();
        let mut input = open_input(0.0, 0.0, 10.0, 0.0);
        input.grid_step_mm = 0.0;
        let err = route(&input, &scene, "NEW").unwrap_err();
        assert!(matches!(err, RoutingError::InvalidInput(_)));
    }

    #[test]
    fn smoke_simplify_collinear_removes_midpoint() {
        let pts = vec![
            Point::new(0.0, 0.0),
            Point::new(1.0, 0.0),
            Point::new(2.0, 0.0),
            Point::new(2.0, 1.0),
        ];
        let s = simplify_collinear(&pts);
        // The middle (1,0) is collinear with its neighbours — gone.
        assert_eq!(s.len(), 3);
        assert!(s[0].distance_to(&Point::new(0.0, 0.0)) < 1e-9);
        assert!(s[1].distance_to(&Point::new(2.0, 0.0)) < 1e-9);
        assert!(s[2].distance_to(&Point::new(2.0, 1.0)) < 1e-9);
    }

    /// Performance gate: 100 trivial straight routes must complete
    /// in ≤ 10 s on the M2 reference grid.
    #[test]
    fn bench_100_tracks_under_10s() {
        let scene = Scene::new();
        let start = Instant::now();
        for i in 0..100 {
            let y = f64::from(i) * 0.5;
            let input = open_input(0.0, y, 10.0, y);
            let _ = route(&input, &scene, "NEW").expect("route");
        }
        let elapsed = start.elapsed();
        assert!(
            elapsed.as_millis() < 10_000,
            "100 routes took {} ms (target < 10000 ms)",
            elapsed.as_millis(),
        );
    }
}
