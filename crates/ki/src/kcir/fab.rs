//! Fabrication targets, design rules, and BOM sourcing policy.

use serde::{Deserialize, Serialize};

/// A named fab-target preset (e.g. JLCPCB 2-layer green HASL).
#[cfg_attr(feature = "ts-export", derive(ts_rs::TS))]
#[cfg_attr(feature = "ts-export", ts(export))]
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct FabTarget {
    pub preset: FabTargetPreset,
    pub min_trace_mm: f64,
    pub min_space_mm: f64,
    pub min_drill_mm: f64,
    pub min_annular_ring_mm: f64,
    pub layer_count: u8,
    pub soldermask_color: String,
    pub silkscreen_color: String,
    pub finish: String,
}

impl Default for FabTarget {
    fn default() -> Self {
        Self {
            preset: FabTargetPreset::Generic,
            min_trace_mm: 0.15,
            min_space_mm: 0.15,
            min_drill_mm: 0.3,
            min_annular_ring_mm: 0.13,
            layer_count: 2,
            soldermask_color: "green".to_string(),
            silkscreen_color: "white".to_string(),
            finish: "HASL".to_string(),
        }
    }
}

/// Built-in fab presets.
#[cfg_attr(feature = "ts-export", derive(ts_rs::TS))]
#[cfg_attr(feature = "ts-export", ts(export))]
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum FabTargetPreset {
    Jlcpcb,
    Oshpark,
    Pcbway,
    #[default]
    Generic,
}

/// Design rule check thresholds. Mirrors `(design_settings …)` in
/// `.kicad_pcb` for the bits kiclaude consumes directly.
#[cfg_attr(feature = "ts-export", derive(ts_rs::TS))]
#[cfg_attr(feature = "ts-export", ts(export))]
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct DesignRules {
    pub clearance_mm: f64,
    pub trace_width_mm: f64,
    pub via_drill_mm: f64,
    pub via_diameter_mm: f64,
    pub uvia_drill_mm: f64,
    pub uvia_diameter_mm: f64,
    pub allow_microvias: bool,
    pub allow_blind_buried_vias: bool,
}

impl Default for DesignRules {
    fn default() -> Self {
        Self {
            clearance_mm: 0.2,
            trace_width_mm: 0.25,
            via_drill_mm: 0.4,
            via_diameter_mm: 0.8,
            uvia_drill_mm: 0.2,
            uvia_diameter_mm: 0.4,
            allow_microvias: false,
            allow_blind_buried_vias: false,
        }
    }
}

/// BOM sourcing policy — how `kc_bom_price` should choose distributors and
/// parts. Filled out in M3.
#[cfg_attr(feature = "ts-export", derive(ts_rs::TS))]
#[cfg_attr(feature = "ts-export", ts(export))]
#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct BomPolicy {
    pub preferred_distributors: Vec<String>,
    pub max_unit_price_usd: Option<u32>,
    pub require_in_stock: bool,
    pub require_jlc_assembly: bool,
    pub region: String,
}
