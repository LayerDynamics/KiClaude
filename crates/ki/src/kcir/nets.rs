//! Nets, net classes, and the typed cross-references they live in.

use serde::{Deserialize, Serialize};

/// A net — a set of pads electrically tied together. See SPEC §7.2.
#[cfg_attr(feature = "ts-export", derive(ts_rs::TS))]
#[cfg_attr(feature = "ts-export", ts(export))]
#[derive(Debug, Clone, Default, PartialEq, Serialize, Deserialize)]
pub struct Net {
    pub name: String,
    pub class: NetClassRef,
    pub members: Vec<PadRef>,
    pub diff_pair: Option<NetRef>,
    pub power_rail: Option<String>,
    pub topology: Option<Topology>,
    pub length_match_group: Option<String>,
    pub target_impedance_ohm: Option<f64>,
    pub reference_plane: Option<LayerRef>,
}

/// A reusable bundle of per-net constraints (width, clearance, via size).
#[cfg_attr(feature = "ts-export", derive(ts_rs::TS))]
#[cfg_attr(feature = "ts-export", ts(export))]
#[derive(Debug, Clone, Default, PartialEq, Serialize, Deserialize)]
pub struct NetClass {
    pub name: String,
    pub description: String,
    pub clearance_mm: f64,
    pub trace_width_mm: f64,
    pub via_drill_mm: f64,
    pub via_diameter_mm: f64,
    pub diff_pair_width_mm: Option<f64>,
    pub diff_pair_gap_mm: Option<f64>,
}

/// Reference to a `NetClass` by name.
#[cfg_attr(feature = "ts-export", derive(ts_rs::TS))]
#[cfg_attr(feature = "ts-export", ts(export))]
#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct NetClassRef(pub String);

/// Reference to a `Net` by name.
#[cfg_attr(feature = "ts-export", derive(ts_rs::TS))]
#[cfg_attr(feature = "ts-export", ts(export))]
#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct NetRef(pub String);

/// Reference to a specific pad on a footprint, e.g. `U1.7`.
#[cfg_attr(feature = "ts-export", derive(ts_rs::TS))]
#[cfg_attr(feature = "ts-export", ts(export))]
#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct PadRef {
    pub refdes: String,
    pub pad: String,
}

/// Reference to a layer by name.
#[cfg_attr(feature = "ts-export", derive(ts_rs::TS))]
#[cfg_attr(feature = "ts-export", ts(export))]
#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct LayerRef(pub String);

/// Net topology constraint — applied to address/data buses and similar.
#[cfg_attr(feature = "ts-export", derive(ts_rs::TS))]
#[cfg_attr(feature = "ts-export", ts(export))]
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Topology {
    #[default]
    DaisyChain,
    FlyBy,
    Star,
    Bus,
}
