// A* uses short variable names (`g`, `h`, `f`) by convention; the
// per-cell `as f64` casts on i32 grid indices are precision-safe
// for the boards we route on (≤ 5000-cell side).
#![allow(
    clippy::cast_precision_loss,
    clippy::similar_names,
    clippy::many_single_char_names,
    clippy::too_many_arguments
)]

//! A* search over the routing grid.
//!
//! Octile movement (8-connected) and the octile heuristic give an
//! admissible bound for diagonal moves. Each move costs the
//! Euclidean step length (1 for orthogonal, √2 for diagonal). Cells
//! that aren't `Grid::is_walkable` are skipped.

use std::cmp::Ordering;
use std::collections::{BinaryHeap, HashMap};

use crate::geom::{Point, Polygon};

use super::grid::Grid;
use super::inflation::InflatedObstacle;

const SQRT2: f64 = std::f64::consts::SQRT_2;

#[derive(Debug, Clone, Copy)]
struct Open {
    cell: (i32, i32),
    f_score: f64,
}

impl PartialEq for Open {
    fn eq(&self, other: &Self) -> bool {
        self.f_score == other.f_score && self.cell == other.cell
    }
}
impl Eq for Open {}

impl Ord for Open {
    fn cmp(&self, other: &Self) -> Ordering {
        // BinaryHeap is a max-heap; invert so the lowest f_score
        // comes out first.
        other
            .f_score
            .partial_cmp(&self.f_score)
            .unwrap_or(Ordering::Equal)
            .then_with(|| other.cell.cmp(&self.cell))
    }
}
impl PartialOrd for Open {
    fn partial_cmp(&self, other: &Self) -> Option<Ordering> {
        Some(self.cmp(other))
    }
}

/// Run A* on `grid` from `start` to `end`. Returns the reconstructed
/// path including endpoints, or `None` if no path exists.
#[must_use]
pub fn find_path(grid: &Grid, start: &(i32, i32), end: &(i32, i32)) -> Option<Vec<(i32, i32)>> {
    if !grid.is_walkable(*start) || !grid.is_walkable(*end) {
        return None;
    }
    if start == end {
        return Some(vec![*start]);
    }

    let mut g_score: HashMap<(i32, i32), f64> = HashMap::new();
    let mut came_from: HashMap<(i32, i32), (i32, i32)> = HashMap::new();
    let mut open = BinaryHeap::new();
    g_score.insert(*start, 0.0);
    open.push(Open {
        cell: *start,
        f_score: octile_distance(*start, *end),
    });

    while let Some(Open { cell, .. }) = open.pop() {
        if cell == *end {
            return Some(reconstruct(&came_from, cell));
        }
        let g_current = *g_score.get(&cell).unwrap_or(&f64::INFINITY);
        for (n, step_cost) in neighbours(cell) {
            if !grid.is_walkable(n) {
                continue;
            }
            let tentative = g_current + step_cost;
            let prior = *g_score.get(&n).unwrap_or(&f64::INFINITY);
            if tentative < prior {
                came_from.insert(n, cell);
                g_score.insert(n, tentative);
                let f = tentative + octile_distance(n, *end);
                open.push(Open {
                    cell: n,
                    f_score: f,
                });
            }
        }
    }
    None
}

fn reconstruct(came_from: &HashMap<(i32, i32), (i32, i32)>, end: (i32, i32)) -> Vec<(i32, i32)> {
    let mut path = vec![end];
    let mut cur = end;
    while let Some(&prev) = came_from.get(&cur) {
        path.push(prev);
        cur = prev;
    }
    path.reverse();
    path
}

fn neighbours(cell: (i32, i32)) -> [((i32, i32), f64); 8] {
    let (x, y) = cell;
    [
        ((x + 1, y), 1.0),
        ((x - 1, y), 1.0),
        ((x, y + 1), 1.0),
        ((x, y - 1), 1.0),
        ((x + 1, y + 1), SQRT2),
        ((x + 1, y - 1), SQRT2),
        ((x - 1, y + 1), SQRT2),
        ((x - 1, y - 1), SQRT2),
    ]
}

fn octile_distance(a: (i32, i32), b: (i32, i32)) -> f64 {
    let dx = f64::from((a.0 - b.0).abs());
    let dy = f64::from((a.1 - b.1).abs());
    let (lo, hi) = if dx < dy { (dx, dy) } else { (dy, dx) };
    (hi - lo) + SQRT2 * lo
}

/// Type alias for the layer-switch return — keeps clippy's
/// `very_complex_type` lint happy without obscuring the signature.
type LayerSwitchPath = (Point, Vec<(i32, i32)>, Vec<(i32, i32)>);

/// Try a layer-switch route: find a partial path on layer A that
/// ends just before the obstruction, then drop a via and continue on
/// layer B's grid to the original end.
///
/// Returns `(via_point, start_layer_path, alt_layer_path)` or `None`
/// when no viable switch exists.
#[allow(clippy::too_many_arguments)]
#[must_use]
pub fn find_layer_switch_path(
    grid: &Grid,
    start: &(i32, i32),
    end: &(i32, i32),
    _start_obstacles: &[InflatedObstacle],
    alt_obstacles: &[InflatedObstacle],
    start_mm: Point,
    end_mm: Point,
    grid_step_mm: f64,
    forbidden: &[Polygon],
) -> Option<LayerSwitchPath> {
    // Greedy first attempt: drop a via at the start. If the alternate
    // layer is unobstructed from start to end, this is the trivial
    // case.
    let alt_grid =
        super::grid::Grid::from_obstacles(start_mm, end_mm, grid_step_mm, alt_obstacles, forbidden);
    if let Some(alt_path) = find_path(&alt_grid, &alt_grid.start_cell, &alt_grid.end_cell) {
        // Start-layer path is just the start cell (no movement before
        // dropping the via).
        return Some((start_mm, vec![*start], alt_path));
    }

    // Fall-back: explore start-layer cells, drop a via at each
    // reachable cell, and check if the alternate layer reaches the
    // end from there. We iterate frontier cells in BFS order to find
    // the via location closest to the start that works.
    let reach = bfs_reachable(grid, *start, 64);
    for cell in reach {
        if cell == *end {
            continue;
        }
        let via_pt = grid.cell_to_point(cell);
        let alt = super::grid::Grid::from_obstacles(
            via_pt,
            end_mm,
            grid_step_mm,
            alt_obstacles,
            forbidden,
        );
        if let Some(alt_path) = find_path(&alt, &alt.start_cell, &alt.end_cell) {
            // Reconstruct start-layer path from `start` to `cell`.
            let start_path = find_path(grid, start, &cell)?;
            return Some((via_pt, start_path, alt_path));
        }
    }
    None
}

/// BFS-walk on the grid starting from `start`, returning up to
/// `limit` reachable cells in order of discovery.
fn bfs_reachable(grid: &Grid, start: (i32, i32), limit: usize) -> Vec<(i32, i32)> {
    let mut visited = std::collections::HashSet::new();
    let mut queue = std::collections::VecDeque::new();
    let mut out = Vec::new();
    queue.push_back(start);
    visited.insert(start);
    while let Some(cell) = queue.pop_front() {
        out.push(cell);
        if out.len() >= limit {
            break;
        }
        for (n, _) in neighbours(cell) {
            if grid.is_walkable(n) && visited.insert(n) {
                queue.push_back(n);
            }
        }
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::geom::{Point, Polygon};
    use crate::routing::walkaround::grid::Grid;
    use crate::routing::walkaround::inflation::InflatedObstacle;

    #[test]
    fn smoke_straight_line_finds_path() {
        let g = Grid::from_obstacles(Point::new(0.0, 0.0), Point::new(5.0, 0.0), 0.5, &[], &[]);
        let p = find_path(&g, &g.start_cell, &g.end_cell).expect("path");
        assert!(p.first() == Some(&g.start_cell));
        assert!(p.last() == Some(&g.end_cell));
    }

    #[test]
    fn smoke_blocked_endpoint_returns_none() {
        let block = Polygon::new(vec![
            Point::new(4.5, -1.0),
            Point::new(5.5, -1.0),
            Point::new(5.5, 1.0),
            Point::new(4.5, 1.0),
        ]);
        let g = Grid::from_obstacles(
            Point::new(0.0, 0.0),
            Point::new(5.0, 0.0),
            0.5,
            &[InflatedObstacle { polygon: block }],
            &[],
        );
        let p = find_path(&g, &g.start_cell, &g.end_cell);
        assert!(p.is_none());
    }

    #[test]
    fn smoke_routes_around_obstacle() {
        let wall = Polygon::new(vec![
            Point::new(2.5, -2.0),
            Point::new(3.0, -2.0),
            Point::new(3.0, 1.0),
            Point::new(2.5, 1.0),
        ]);
        let g = Grid::from_obstacles(
            Point::new(0.0, 0.0),
            Point::new(5.0, 0.0),
            0.5,
            &[InflatedObstacle { polygon: wall }],
            &[],
        );
        let p = find_path(&g, &g.start_cell, &g.end_cell).expect("should detour");
        // Some cell in the path must have y > 0 (the detour above the
        // wall).
        let detour_pts: Vec<_> = p.iter().filter(|c| g.cell_to_point(**c).y > 0.5).collect();
        assert!(!detour_pts.is_empty(), "expected a detour, got {p:?}");
    }

    #[test]
    fn smoke_octile_distance_diagonal() {
        let d = octile_distance((0, 0), (3, 3));
        // Pure diagonal of 3 cells.
        assert!((d - 3.0 * SQRT2).abs() < 1e-9);
    }

    #[test]
    fn smoke_octile_distance_l_shape() {
        let d = octile_distance((0, 0), (3, 5));
        // 3 diagonals + 2 orthogonals.
        let expected = SQRT2 * 3.0 + 2.0;
        assert!((d - expected).abs() < 1e-9);
    }
}
