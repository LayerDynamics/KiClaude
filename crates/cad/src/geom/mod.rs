//! Geometric primitives — points, bounding boxes, polylines, polygons,
//! arcs. Pure-Rust, allocation-light, no `KiCad`-specific dependencies.
//!
//! All coordinates are 64-bit floats in millimeters by convention; the
//! types themselves carry no unit metadata. Callers that mix coordinate
//! systems (e.g. internal-units `KiCad` storage vs. `mm` UI) are
//! responsible for unit conversion before constructing these values.

pub mod arc;
pub mod bbox;
pub mod point;
pub mod polygon;
pub mod polyline;

pub use arc::Arc;
pub use bbox::BBox;
pub use point::Point;
pub use polygon::Polygon;
pub use polyline::Polyline;
