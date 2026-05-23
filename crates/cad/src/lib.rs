//! `kiclaude-cad` — CAD primitives for kiclaude.
//!
//! Provides pure-Rust geometry, spatial indexing, and the DRC kernel that
//! drives both the in-browser live-feedback overlays and any server-side
//! pre-flight checks. The authoritative DRC for CI gates remains
//! `kicad-cli pcb drc` per SPEC §9.3 (decision D8).
//!
//! See `docs/specs/SPEC-01-kiclaude.md` §6.3.

#![deny(rust_2018_idioms, missing_debug_implementations)]
#![warn(clippy::pedantic)]
#![allow(clippy::module_name_repetitions)]

pub mod drc;
pub mod geom;
pub mod index;
pub mod routing;
pub mod scene;
pub mod zones;

#[cfg(target_arch = "wasm32")]
pub mod wasm;

pub use geom::{Arc, BBox, Point, Polygon, Polyline};
pub use index::RTree;
pub use zones::{
    fill_zone, polygon_normalize_ring, Obstacle, ObstacleGeometry, PadShape, ThermalReliefSpec,
    ThermalSpoke, ZoneFillInput, ZoneFillResult,
};

/// Crate version, exposed so downstream bindings can surface it.
pub const CRATE_VERSION: &str = env!("CARGO_PKG_VERSION");
