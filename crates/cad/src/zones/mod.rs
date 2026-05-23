//! Zone filling — polygon offset + obstacle subtraction + thermal-relief
//! spokes, all in pure Rust on top of [`crate::geom`] primitives.
//!
//! `KiCad`'s zone filler operates as a sequence of polygon boolean ops:
//!
//! 1. Inward-offset the user-drawn outline by `clearance_mm`.
//! 2. For each obstacle on the same layer, inflate by
//!    `clearance_mm + obstacle_extra_clearance` and subtract.
//! 3. For pads on the **same net** as the zone, replace the simple
//!    inflated keepout with a thermal-relief shape — the inflated pad
//!    with N narrow spoke rectangles carved out so a small strip of
//!    copper still connects the pad to the pour.
//! 4. Remove copper regions thinner than `min_thickness_mm`.
//!
//! Step 1, the obstacle-as-hole side of step 2, and step 3's spoke
//! computation are implemented here. Full polygon-boolean union of
//! overlapping obstacles, and the minimum-thickness simplification of
//! step 4, are M2-grade approximations — see the module docs in
//! [`fill`] for the exact set of cases the current implementation
//! handles correctly and the cases that need a Clipper-style follow-up.
//!
//! See SPEC §13 (`KiCad`-cli remains the source of truth for golden
//! comparisons) and SPEC FR-023 (zone fill is a kiclaude-core feature,
//! not a kicad-cli passthrough).

pub mod boolean;
pub mod fill;
pub mod thermal;

pub use boolean::{
    from_overlay_shape, polygon_difference, polygon_intersection, polygon_normalize_ring,
    polygon_union, rounded_inward_offset, rounded_outward_offset, to_overlay_shape,
};
pub use fill::{fill_zone, Obstacle, ObstacleGeometry, ZoneFillInput, ZoneFillResult};
pub use thermal::{
    compute_thermal_relief, PadShape, ThermalRelief, ThermalReliefSpec, ThermalSpoke,
};
