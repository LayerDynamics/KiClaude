//! Physical layer stackup.

use serde::{Deserialize, Serialize};

/// The physical layer/dielectric stackup. Defaults to a 2-layer FR-4
/// preset, which is the M2 demo target.
#[cfg_attr(feature = "ts-export", derive(ts_rs::TS))]
#[cfg_attr(feature = "ts-export", ts(export))]
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Stackup {
    pub layers: Vec<StackupLayer>,
    pub power_plane_layers: Vec<String>,
    pub controlled_impedance: bool,
    pub board_thickness_mm: f64,
    pub finish: String,
}

impl Default for Stackup {
    fn default() -> Self {
        // 2-layer FR-4 default: top copper, FR-4 dielectric, bottom copper.
        Self {
            layers: vec![
                StackupLayer {
                    name: "F.Cu".to_string(),
                    kind: StackupLayerKind::Copper,
                    thickness_mm: 0.035,
                    dielectric_constant: None,
                    loss_tangent: None,
                    color: "copper".to_string(),
                },
                StackupLayer {
                    name: "dielectric 1".to_string(),
                    kind: StackupLayerKind::Dielectric,
                    thickness_mm: 1.51,
                    dielectric_constant: Some(4.5),
                    loss_tangent: Some(0.02),
                    color: "fr4".to_string(),
                },
                StackupLayer {
                    name: "B.Cu".to_string(),
                    kind: StackupLayerKind::Copper,
                    thickness_mm: 0.035,
                    dielectric_constant: None,
                    loss_tangent: None,
                    color: "copper".to_string(),
                },
            ],
            power_plane_layers: Vec::new(),
            controlled_impedance: false,
            board_thickness_mm: 1.58,
            finish: "HASL".to_string(),
        }
    }
}

/// A single layer in the stackup.
#[cfg_attr(feature = "ts-export", derive(ts_rs::TS))]
#[cfg_attr(feature = "ts-export", ts(export))]
#[derive(Debug, Clone, Default, PartialEq, Serialize, Deserialize)]
pub struct StackupLayer {
    pub name: String,
    pub kind: StackupLayerKind,
    pub thickness_mm: f64,
    pub dielectric_constant: Option<f64>,
    pub loss_tangent: Option<f64>,
    pub color: String,
}

/// What a layer is made of.
#[cfg_attr(feature = "ts-export", derive(ts_rs::TS))]
#[cfg_attr(feature = "ts-export", ts(export))]
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum StackupLayerKind {
    #[default]
    Copper,
    Dielectric,
    Soldermask,
    Silkscreen,
    Paste,
    Adhesive,
}
