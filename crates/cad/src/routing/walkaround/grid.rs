// Integer-cell grid math: i32 indices are bounded by board size /
// step (typically < 5000) so f64↔i32 casts are precision-safe.
#![allow(
    clippy::cast_precision_loss,
    clippy::cast_possible_truncation,
    clippy::cast_possible_wrap,
    clippy::cast_sign_loss
)]

//! Uniform-cell grid for the walk-around router's A* search.
//!
//! The grid covers the axis-aligned bounding box of `(start, end)`
//! padded by a margin so the router can excursion AROUND obstacles
//! that sit between (or beside) the endpoints. Each cell records
//! whether its centre lies inside any inflated obstacle — that's the
//! "blocked" predicate A* consults.

use crate::geom::{BBox, Point, Polygon};

use super::inflation::InflatedObstacle;

/// Padding (multiplier on diagonal) applied to the start↔end bbox so
/// the router can deviate around obstacles. `1.5×` is generous enough
/// for the M2 reference set without exploding the grid size.
const BBOX_PADDING_MULTIPLIER: f64 = 1.5;

/// Cell-aligned grid. Cells are indexed by `(col, row)` with `(0, 0)`
/// at the SW corner of the search region.
#[derive(Debug, Clone)]
pub struct Grid {
    /// SW corner of cell (0, 0) in board mm.
    pub origin_mm: Point,
    pub step_mm: f64,
    pub width: i32,
    pub height: i32,
    /// `blocked[row * width + col]` — `true` means the cell centre is
    /// inside at least one obstacle.
    blocked: Vec<bool>,
    pub start_cell: (i32, i32),
    pub end_cell: (i32, i32),
}

impl Grid {
    /// Build a grid covering `start..=end` padded for routing
    /// excursion. Cells whose centres fall inside any obstacle or
    /// forbidden region are marked blocked.
    #[must_use]
    pub fn from_obstacles(
        start: Point,
        end: Point,
        step_mm: f64,
        obstacles: &[InflatedObstacle],
        forbidden: &[Polygon],
    ) -> Self {
        let bb = BBox::new(
            start.x.min(end.x),
            start.y.min(end.y),
            start.x.max(end.x),
            start.y.max(end.y),
        );
        // Padding has two contributors: a fraction of the diagonal
        // (for detours scaling with route length) and an
        // obstacle-margin floor (so walls extending beyond the bbox
        // don't trap the search). Obstacles up to the floor distance
        // beyond the bbox are still routable around.
        let diag = (bb.width().hypot(bb.height())).max(step_mm * 4.0);
        let diag_pad = diag * (BBOX_PADDING_MULTIPLIER - 1.0) * 0.5;
        let obstacle_pad = obstacle_extent_pad(&bb, obstacles, forbidden);
        let pad = diag_pad.max(obstacle_pad).max(step_mm * 8.0);
        let min_x = bb.min.x - pad;
        let min_y = bb.min.y - pad;
        let max_x = bb.max.x + pad;
        let max_y = bb.max.y + pad;
        let width = ((max_x - min_x) / step_mm).ceil() as i32 + 1;
        let height = ((max_y - min_y) / step_mm).ceil() as i32 + 1;
        let width = width.max(2);
        let height = height.max(2);

        let n = (width as usize) * (height as usize);
        let mut blocked = vec![false; n];

        for row in 0..height {
            for col in 0..width {
                let p = Point::new(
                    min_x + f64::from(col) * step_mm,
                    min_y + f64::from(row) * step_mm,
                );
                let mut is_blocked = false;
                for ob in obstacles {
                    if ob.polygon.contains_point(p) {
                        is_blocked = true;
                        break;
                    }
                }
                if !is_blocked {
                    for fb in forbidden {
                        if fb.contains_point(p) {
                            is_blocked = true;
                            break;
                        }
                    }
                }
                if is_blocked {
                    blocked[(row * width + col) as usize] = true;
                }
            }
        }

        let origin_mm = Point::new(min_x, min_y);
        let start_cell = snap_cell(start, origin_mm, step_mm, width, height);
        let end_cell = snap_cell(end, origin_mm, step_mm, width, height);
        Self {
            origin_mm,
            step_mm,
            width,
            height,
            blocked,
            start_cell,
            end_cell,
        }
    }

    /// Cell-space → board mm.
    #[must_use]
    pub fn cell_to_point(&self, cell: (i32, i32)) -> Point {
        Point::new(
            self.origin_mm.x + f64::from(cell.0) * self.step_mm,
            self.origin_mm.y + f64::from(cell.1) * self.step_mm,
        )
    }

    /// Is `cell` within bounds AND not blocked?
    #[must_use]
    pub fn is_walkable(&self, cell: (i32, i32)) -> bool {
        if cell.0 < 0 || cell.1 < 0 || cell.0 >= self.width || cell.1 >= self.height {
            return false;
        }
        !self.blocked[(cell.1 * self.width + cell.0) as usize]
    }

    /// Is start / end blocked? Returned as `(start_blocked, end_blocked)`.
    #[must_use]
    pub fn endpoints_blocked(&self) -> (bool, bool) {
        (
            !self.is_walkable(self.start_cell),
            !self.is_walkable(self.end_cell),
        )
    }
}

/// Largest distance any obstacle / forbidden polygon extends beyond
/// the start↔end bbox. Used as a floor on grid padding so the router
/// can escape walls whose endpoints sit outside the route's bbox.
fn obstacle_extent_pad(bb: &BBox, obstacles: &[InflatedObstacle], forbidden: &[Polygon]) -> f64 {
    let mut max_extent: f64 = 0.0;
    for ob in obstacles {
        let obb = ob.polygon.bounding_box();
        max_extent = max_extent
            .max((obb.max.x - bb.max.x).max(0.0))
            .max((bb.min.x - obb.min.x).max(0.0))
            .max((obb.max.y - bb.max.y).max(0.0))
            .max((bb.min.y - obb.min.y).max(0.0));
    }
    for fb in forbidden {
        let fbb = fb.bounding_box();
        max_extent = max_extent
            .max((fbb.max.x - bb.max.x).max(0.0))
            .max((bb.min.x - fbb.min.x).max(0.0))
            .max((fbb.max.y - bb.max.y).max(0.0))
            .max((bb.min.y - fbb.min.y).max(0.0));
    }
    max_extent
}

fn snap_cell(p: Point, origin: Point, step: f64, width: i32, height: i32) -> (i32, i32) {
    let col = ((p.x - origin.x) / step).round() as i32;
    let row = ((p.y - origin.y) / step).round() as i32;
    (col.clamp(0, width - 1), row.clamp(0, height - 1))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::geom::Polygon;

    #[test]
    fn smoke_empty_grid_all_walkable() {
        let g = Grid::from_obstacles(Point::new(0.0, 0.0), Point::new(10.0, 0.0), 0.5, &[], &[]);
        assert!(g.is_walkable(g.start_cell));
        assert!(g.is_walkable(g.end_cell));
    }

    #[test]
    fn smoke_obstacle_blocks_cells_inside_it() {
        let rect = Polygon::new(vec![
            Point::new(4.0, -2.0),
            Point::new(6.0, -2.0),
            Point::new(6.0, 2.0),
            Point::new(4.0, 2.0),
        ]);
        let g = Grid::from_obstacles(
            Point::new(0.0, 0.0),
            Point::new(10.0, 0.0),
            0.5,
            &[InflatedObstacle { polygon: rect }],
            &[],
        );
        // Cell at (5.0, 0.0) should be blocked.
        let col = ((5.0 - g.origin_mm.x) / g.step_mm).round() as i32;
        let row = ((0.0 - g.origin_mm.y) / g.step_mm).round() as i32;
        assert!(!g.is_walkable((col, row)), "middle cell should be blocked");
    }

    #[test]
    fn smoke_forbidden_region_blocks_cells() {
        let rect = Polygon::new(vec![
            Point::new(4.0, -2.0),
            Point::new(6.0, -2.0),
            Point::new(6.0, 2.0),
            Point::new(4.0, 2.0),
        ]);
        let g = Grid::from_obstacles(
            Point::new(0.0, 0.0),
            Point::new(10.0, 0.0),
            0.5,
            &[],
            &[rect],
        );
        let col = ((5.0 - g.origin_mm.x) / g.step_mm).round() as i32;
        let row = ((0.0 - g.origin_mm.y) / g.step_mm).round() as i32;
        assert!(!g.is_walkable((col, row)));
    }
}
