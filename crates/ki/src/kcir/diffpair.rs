//! Differential pair declaration (M3-R-07 + M3-R-04).
//!
//! KCIR carries diff pairs as named collections on the PCB. Each
//! [`DiffPair`] references two nets by name and declares the target
//! impedance + spacing the impedance solver (M3-R-02) drives toward
//! when picking trace widths.
//!
//! Per-net `Net.diff_pair: Option<NetRef>` (in [`super::nets`]) is a
//! back-reference — it lets the schematic side annotate a net as
//! "part of this pair". The authoritative declaration lives here.

use serde::{Deserialize, Serialize};

/// A declared differential pair.
#[cfg_attr(feature = "ts-export", derive(ts_rs::TS))]
#[cfg_attr(feature = "ts-export", ts(export))]
#[derive(Debug, Clone, Default, PartialEq, Serialize, Deserialize)]
pub struct DiffPair {
    /// Display name (e.g. `"USB_D"`, `"MDIO_TRD0"`).
    pub name: String,
    /// Net name of the positive leg.
    pub net_positive: String,
    /// Net name of the negative leg.
    pub net_negative: String,
    /// Target differential impedance in ohms (90 for USB 2.0,
    /// 100 for LVDS / Ethernet, 85 for SATA, …). `0.0` = unspecified.
    pub target_impedance_ohms: f64,
    /// Target trace-to-trace gap in mm. `0.0` = unspecified
    /// (the solver picks one).
    pub target_gap_mm: f64,
    /// Optional length-match group this pair belongs to. Empty if
    /// the pair isn't grouped.
    #[serde(default)]
    pub length_group: String,
    /// Optional per-pair length-skew tolerance (mm). The legs of a
    /// diff pair are typically matched to ≤ 5 mil = 0.127 mm.
    #[serde(default)]
    pub skew_tolerance_mm: f64,
}
