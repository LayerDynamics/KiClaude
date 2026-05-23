//! 2-D point type — a thin newtype-like over `(f64, f64)` for clarity.
//!
//! Kept as a struct (rather than a bare tuple) so call sites read as
//! `Point { x, y }` instead of `(x, y)` — there are enough `(f64, f64)`
//! tuples in the `KiCad` mapping layer to make the explicit form worth
//! the extra characters.

use serde::{Deserialize, Serialize};

/// A 2-D point in millimeters.
#[derive(Debug, Clone, Copy, PartialEq, Default, Serialize, Deserialize)]
pub struct Point {
    pub x: f64,
    pub y: f64,
}

impl Point {
    /// New point at `(x, y)`.
    #[must_use]
    pub const fn new(x: f64, y: f64) -> Self {
        Self { x, y }
    }

    /// Squared Euclidean distance to `other`. Avoids the `sqrt` when
    /// callers only need ordering.
    #[must_use]
    pub fn distance_squared_to(&self, other: &Self) -> f64 {
        let dx = self.x - other.x;
        let dy = self.y - other.y;
        dx * dx + dy * dy
    }

    /// Euclidean distance to `other`.
    #[must_use]
    pub fn distance_to(&self, other: &Self) -> f64 {
        self.distance_squared_to(other).sqrt()
    }
}

impl From<(f64, f64)> for Point {
    fn from((x, y): (f64, f64)) -> Self {
        Self { x, y }
    }
}

impl From<Point> for (f64, f64) {
    fn from(p: Point) -> Self {
        (p.x, p.y)
    }
}
