//! Length-match groups (M3-R-07 + M3-R-05).
//!
//! A length-match group declares that every net in the group should
//! reach the same total trace length (within `tolerance_mm`). The
//! M3-R-05 analyzer reads this declaration to compute deltas and
//! propose serpentine tuning segments.
//!
//! Typical use: DDR data buses, parallel MCU↔FPGA interconnects,
//! HS Ethernet diff pairs that must match each other end-to-end.

use serde::{Deserialize, Serialize};

/// A named length-match group.
#[cfg_attr(feature = "ts-export", derive(ts_rs::TS))]
#[cfg_attr(feature = "ts-export", ts(export))]
#[derive(Debug, Clone, Default, PartialEq, Serialize, Deserialize)]
pub struct LengthGroup {
    /// Display name (e.g. `"DDR3_DQ_BYTE0"`, `"RGMII_TX"`).
    pub name: String,
    /// Net names that belong to this group. The analyzer walks each
    /// net's track polylines to compute the running total length.
    pub nets: Vec<String>,
    /// Target length in mm. `0.0` = "match the longest net";
    /// > 0 = explicit target the analyzer drives every member to.
    pub target_length_mm: f64,
    /// Tolerance (mm) — nets within `±tolerance_mm` of the target are
    /// considered matched. Typical values: 0.127 mm (5 mil) for diff
    /// pairs, 0.5 mm for parallel buses below 800 Mbps.
    pub tolerance_mm: f64,
}
