//! Polyline — open or closed sequence of straight segments.
//!
//! Distinct from [`Polygon`](super::polygon::Polygon) in that a polyline
//! is not necessarily closed; `KiCad` uses it for traces, silkscreen
//! lines, and unclosed graphic shapes.

use serde::{Deserialize, Serialize};

use super::bbox::BBox;
use super::point::Point;

/// A connected sequence of straight segments between consecutive points.
#[derive(Debug, Clone, Default, PartialEq, Serialize, Deserialize)]
pub struct Polyline {
    pub points: Vec<Point>,
    pub width_mm: f64,
}

impl Polyline {
    /// New polyline from a vec of points and a stroke width.
    #[must_use]
    pub fn new(points: Vec<Point>, width_mm: f64) -> Self {
        Self { points, width_mm }
    }

    /// Number of point-to-point segments. Zero for fewer than two points.
    #[must_use]
    pub fn segment_count(&self) -> usize {
        self.points.len().saturating_sub(1)
    }

    /// Total length of the polyline as the sum of segment lengths.
    #[must_use]
    pub fn length(&self) -> f64 {
        self.points
            .windows(2)
            .map(|w| w[0].distance_to(&w[1]))
            .sum()
    }

    /// Axis-aligned bounding box, expanded by half the stroke width to
    /// reflect the visual extent of the line.
    #[must_use]
    pub fn bounding_box(&self) -> BBox {
        if self.points.is_empty() {
            return BBox::empty();
        }
        let mut bbox = BBox::from_point(self.points[0]);
        for p in &self.points[1..] {
            bbox = bbox.union(&BBox::from_point(*p));
        }
        let pad = self.width_mm * 0.5;
        BBox::new(
            bbox.min.x - pad,
            bbox.min.y - pad,
            bbox.max.x + pad,
            bbox.max.y + pad,
        )
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use pretty_assertions::assert_eq;

    #[test]
    fn smoke_segment_count() {
        let pl = Polyline::new(
            vec![
                Point::new(0.0, 0.0),
                Point::new(1.0, 0.0),
                Point::new(2.0, 1.0),
            ],
            0.2,
        );
        assert_eq!(pl.segment_count(), 2);
    }

    #[test]
    fn smoke_length_sums_segments() {
        let pl = Polyline::new(
            vec![
                Point::new(0.0, 0.0),
                Point::new(3.0, 0.0),
                Point::new(3.0, 4.0),
            ],
            0.0,
        );
        // 3 + 4 = 7
        assert!((pl.length() - 7.0).abs() < 1e-9);
    }

    #[test]
    fn smoke_bbox_expands_by_half_width() {
        let pl = Polyline::new(vec![Point::new(0.0, 0.0), Point::new(10.0, 0.0)], 0.5);
        let b = pl.bounding_box();
        assert!((b.min.x - -0.25).abs() < 1e-9);
        assert!((b.max.x - 10.25).abs() < 1e-9);
        assert!((b.min.y - -0.25).abs() < 1e-9);
        assert!((b.max.y - 0.25).abs() < 1e-9);
    }

    #[test]
    fn smoke_empty_bbox_is_empty() {
        assert!(Polyline::default().bounding_box().is_empty());
    }
}
