//! Axis-aligned bounding box.
//!
//! Stored as `min`/`max` corners. An "empty" bbox is represented with
//! `min.x > max.x` so [`BBox::empty`] composes well with unions —
//! `BBox::empty().union(b) == b` for any non-empty `b`.

use serde::{Deserialize, Serialize};

use super::point::Point;

/// Axis-aligned bounding box in millimeters.
#[derive(Debug, Clone, Copy, PartialEq, Serialize, Deserialize)]
pub struct BBox {
    pub min: Point,
    pub max: Point,
}

impl BBox {
    /// A bbox covering `(min_x..=max_x, min_y..=max_y)`. The constructor
    /// normalizes input so `min` always holds the lower corner.
    #[must_use]
    pub fn new(min_x: f64, min_y: f64, max_x: f64, max_y: f64) -> Self {
        Self {
            min: Point::new(min_x.min(max_x), min_y.min(max_y)),
            max: Point::new(min_x.max(max_x), min_y.max(max_y)),
        }
    }

    /// A bbox covering a single point — zero-area but well-defined.
    #[must_use]
    pub fn from_point(p: Point) -> Self {
        Self { min: p, max: p }
    }

    /// The empty bbox sentinel — has `min.x > max.x`, so any union with
    /// it returns the other operand unchanged.
    #[must_use]
    pub fn empty() -> Self {
        Self {
            min: Point::new(f64::INFINITY, f64::INFINITY),
            max: Point::new(f64::NEG_INFINITY, f64::NEG_INFINITY),
        }
    }

    /// `true` if this bbox holds no area (sentinel-empty).
    #[must_use]
    pub fn is_empty(&self) -> bool {
        self.min.x > self.max.x || self.min.y > self.max.y
    }

    /// Width along the X axis. Zero for an empty bbox by construction.
    #[must_use]
    pub fn width(&self) -> f64 {
        (self.max.x - self.min.x).max(0.0)
    }

    /// Height along the Y axis. Zero for an empty bbox.
    #[must_use]
    pub fn height(&self) -> f64 {
        (self.max.y - self.min.y).max(0.0)
    }

    /// Area. Zero for an empty bbox.
    #[must_use]
    pub fn area(&self) -> f64 {
        self.width() * self.height()
    }

    /// `true` iff this bbox overlaps `other` (closed intervals on both
    /// axes — touching edges count as intersecting).
    #[must_use]
    pub fn intersects(&self, other: &Self) -> bool {
        if self.is_empty() || other.is_empty() {
            return false;
        }
        self.min.x <= other.max.x
            && self.max.x >= other.min.x
            && self.min.y <= other.max.y
            && self.max.y >= other.min.y
    }

    /// `true` iff `p` lies inside (or on the boundary of) this bbox.
    #[must_use]
    pub fn contains_point(&self, p: Point) -> bool {
        if self.is_empty() {
            return false;
        }
        p.x >= self.min.x && p.x <= self.max.x && p.y >= self.min.y && p.y <= self.max.y
    }

    /// Smallest bbox containing both `self` and `other`.
    #[must_use]
    pub fn union(&self, other: &Self) -> Self {
        if self.is_empty() {
            return *other;
        }
        if other.is_empty() {
            return *self;
        }
        Self {
            min: Point::new(self.min.x.min(other.min.x), self.min.y.min(other.min.y)),
            max: Point::new(self.max.x.max(other.max.x), self.max.y.max(other.max.y)),
        }
    }

    /// Increase in area required to grow this bbox to also cover `other`.
    #[must_use]
    pub fn enlargement_to_cover(&self, other: &Self) -> f64 {
        if other.is_empty() {
            return 0.0;
        }
        let combined = self.union(other);
        combined.area() - if self.is_empty() { 0.0 } else { self.area() }
    }
}

impl Default for BBox {
    fn default() -> Self {
        Self::empty()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use pretty_assertions::assert_eq;

    #[test]
    fn smoke_constructor_normalizes_corners() {
        let b = BBox::new(5.0, 5.0, 1.0, 1.0);
        assert_eq!(b.min, Point::new(1.0, 1.0));
        assert_eq!(b.max, Point::new(5.0, 5.0));
    }

    #[test]
    fn smoke_empty_union_is_identity() {
        let b = BBox::new(0.0, 0.0, 2.0, 2.0);
        assert_eq!(BBox::empty().union(&b), b);
        assert_eq!(b.union(&BBox::empty()), b);
    }

    #[test]
    fn smoke_intersection_at_edge_is_true() {
        let a = BBox::new(0.0, 0.0, 1.0, 1.0);
        let b = BBox::new(1.0, 0.0, 2.0, 1.0);
        assert!(a.intersects(&b));
    }

    #[test]
    fn smoke_intersection_disjoint_is_false() {
        let a = BBox::new(0.0, 0.0, 1.0, 1.0);
        let b = BBox::new(2.0, 2.0, 3.0, 3.0);
        assert!(!a.intersects(&b));
    }

    #[test]
    fn smoke_contains_point() {
        let b = BBox::new(0.0, 0.0, 2.0, 2.0);
        assert!(b.contains_point(Point::new(1.0, 1.0)));
        assert!(b.contains_point(Point::new(0.0, 0.0)));
        assert!(!b.contains_point(Point::new(3.0, 1.0)));
    }
}
