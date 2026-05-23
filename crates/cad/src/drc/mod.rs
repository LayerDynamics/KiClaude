//! Design Rules Check (DRC) kernel — pure-Rust geometric verification
//! of clearance, courtyard, annular-ring, and drill-to-copper rules
//! against a PCB.
//!
//! ## Authority and scope
//!
//! Per SPEC §9.3 (decision D8) the **authoritative** DRC is
//! `kicad-cli pcb drc`. This Rust kernel runs in two contexts:
//!
//! 1. **Live editor feedback** — the React PCB editor calls into
//!    wasm-compiled `kiclaude-cad` to render clearance violations as
//!    the user drags tracks / moves footprints. The kernel must be
//!    fast (≤ 100 ms broad-phase on a 5000-track board, gated by
//!    M2-R-07's R-tree) and conservative (never report a violation
//!    `kicad-cli` wouldn't).
//! 2. **Pre-flight sanity** — the agent's `/drc-fix` command consults
//!    `kc_drc` (= `kicad-cli`) directly, but `services/server` may
//!    surface this kernel's overlays alongside.
//!
//! ## Checks implemented
//!
//! | Check | Module | Pairs covered |
//! |---|---|---|
//! | Track ↔ track clearance | [`clearance`] | same-layer, foreign-net |
//! | Track ↔ pad clearance   | [`clearance`] | same-layer, foreign-net |
//! | Pad ↔ pad clearance     | [`clearance`] | same-layer, foreign-net |
//! | Footprint courtyard collision | [`courtyard`] | F.CrtYd / B.CrtYd |
//! | Annular ring (via)      | [`annular`]  | (`diameter` − `drill`) / 2 < min |
//! | Annular ring (THT pad)  | [`annular`]  | as above on pads with drill |
//! | Drill ↔ copper          | [`drill`]    | drill edge to nearest foreign copper edge |
//!
//! ## What this kernel does NOT cover
//!
//! - Hole-to-hole clearance (separate fab rule, not implemented)
//! - Silk-over-pad (a silkscreen rule, in `kicad-cli` only)
//! - Differential-pair gap / length-match (M3 work)
//! - Stackup-aware impedance violations (M3 work)
//! - Edge-of-board clearance (would need the inverse of the outline; defer to `kicad-cli`)
//!
//! Anything in the deliberately-omitted column is a "we trust
//! `kicad-cli`" boundary, not a future bug.

pub mod annular;
pub mod clearance;
pub mod courtyard;
pub mod drill;

use serde::{Deserialize, Serialize};

use crate::geom::{Point, Polygon};

/// One DRC finding. Carries enough position + identity info that the
/// React editor can show a marker on the canvas and the user can
/// jump to it.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct DrcIssue {
    /// `Error` = blocks fab; `Warning` = advisory.
    pub severity: DrcSeverity,
    /// Which check produced this finding.
    pub kind: DrcIssueKind,
    /// Board-coordinate position the editor should fly to (mm).
    pub position_mm: Point,
    /// Copper layer the issue lives on (`F.Cu`, `In1.Cu`, …) or
    /// `"any"` for layer-independent issues (e.g. courtyard).
    pub layer: String,
    /// One-line human description. Suitable for the React DRC panel.
    pub description: String,
    /// Identifiers of the items involved — typically refdes, pad
    /// number, track or via uuid. Two-item lists mean the pair
    /// triggering the rule.
    pub items: Vec<String>,
    /// Distance below the rule threshold, in mm. Negative numbers
    /// would mean "compliant" so should never appear here; the
    /// renderer can use this for severity bucketing.
    pub deficit_mm: f64,
}

/// Severity tier — matches `kicad-cli pcb drc`'s output schema.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum DrcSeverity {
    Error,
    Warning,
}

/// Which check produced an issue. The variant set deliberately mirrors
/// `kicad-cli pcb drc`'s `type` values so that comparison tooling can
/// align them by name.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum DrcIssueKind {
    /// Two copper items closer than the active clearance rule.
    ClearanceViolation,
    /// Two footprint courtyards overlap on the same side.
    CourtyardOverlap,
    /// Via or THT-pad annular ring narrower than the minimum.
    AnnularRingViolation,
    /// A drill (mechanical hole) sits closer to foreign-net copper
    /// than the drill-clearance rule allows.
    DrillToCopperViolation,
}

/// Inputs to the kernel — the geometric primitives that DRC reasons
/// over. The caller flattens KCIR / `Pcb` data into this shape; this
/// keeps the kernel a pure function of geometry, not of file format.
#[derive(Debug, Clone, Default, PartialEq, Serialize, Deserialize)]
pub struct DrcInput {
    pub tracks: Vec<DrcTrack>,
    pub vias: Vec<DrcVia>,
    pub pads: Vec<DrcPad>,
    pub courtyards: Vec<DrcCourtyard>,
    /// Default clearance when no net-class rule applies (mm).
    pub default_clearance_mm: f64,
    /// Minimum annular ring width allowed in mm. Same as `kicad-cli`'s
    /// "min annular width" setup value.
    pub min_annular_ring_mm: f64,
    /// Minimum drill-to-copper clearance in mm.
    pub min_drill_to_copper_mm: f64,
    /// Per-net-class clearance overrides. Lookup key = net class name.
    pub net_class_clearances_mm: std::collections::HashMap<String, f64>,
    /// Net → net-class membership. Used to resolve `net_class_clearances_mm`.
    pub net_to_class: std::collections::HashMap<String, String>,
}

/// A track segment — single straight piece of a polyline. The KCIR
/// representation can be a polyline of N vertices; the caller is
/// responsible for splitting into segments before feeding here.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct DrcTrack {
    pub uuid: String,
    pub net: String,
    pub layer: String,
    pub start_mm: Point,
    pub end_mm: Point,
    pub width_mm: f64,
}

/// A via — through-hole or blind/buried.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct DrcVia {
    pub uuid: String,
    pub net: String,
    pub position_mm: Point,
    /// Copper layers the via reaches (typically `F.Cu`/`B.Cu` for
    /// through-hole; an inner pair for blind/buried).
    pub layers: Vec<String>,
    pub drill_mm: f64,
    pub diameter_mm: f64,
}

/// A pad (SMD or through-hole). Through-hole pads carry a `drill_mm`
/// > 0; SMD pads have `drill_mm = 0`.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct DrcPad {
    pub footprint_refdes: String,
    pub number: String,
    pub net: String,
    pub center_mm: Point,
    pub size_mm: (f64, f64),
    pub rotation_deg: f64,
    pub layers: Vec<String>,
    pub shape: DrcPadShape,
    pub drill_mm: f64,
}

/// Pad copper shape — mirrors the spoke-relief shape vocabulary in
/// [`crate::zones::PadShape`].
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum DrcPadShape {
    Rect,
    RoundRect,
    Circle,
    Oval,
}

/// A footprint courtyard polygon. `kicad-cli` flags courtyards that
/// overlap on the same physical side as a `courtyard_overlap` issue.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct DrcCourtyard {
    pub footprint_refdes: String,
    /// `F.CrtYd` or `B.CrtYd` — courtyards never live on copper.
    pub layer: String,
    pub polygon: Polygon,
}

/// Run every check the kernel knows about and return the union of
/// findings. The caller can filter by `severity` / `kind` / `layer`.
///
/// Order is not stable across calls — the React overlay sorts by
/// `(severity, layer, kind, position)` before display.
///
/// # Panics
///
/// Never panics. Empty / malformed inputs return an empty `Vec`.
#[must_use]
pub fn check_all(input: &DrcInput) -> Vec<DrcIssue> {
    let mut issues = Vec::new();
    issues.extend(clearance::check(input));
    issues.extend(courtyard::check(input));
    issues.extend(annular::check(input));
    issues.extend(drill::check(input));
    issues
}

/// Resolve the clearance to apply between two named nets.
///
/// Returns the max of the two nets' class clearances, falling back to
/// `default_clearance_mm` when a net has no class entry. Two items on
/// the *same* net return 0 so the caller can skip the check.
#[must_use]
pub fn clearance_between(input: &DrcInput, net_a: &str, net_b: &str) -> f64 {
    if !net_a.is_empty() && net_a == net_b {
        return 0.0;
    }
    let ca = net_clearance(input, net_a);
    let cb = net_clearance(input, net_b);
    ca.max(cb).max(input.default_clearance_mm)
}

fn net_clearance(input: &DrcInput, net: &str) -> f64 {
    if net.is_empty() {
        return input.default_clearance_mm;
    }
    let Some(class) = input.net_to_class.get(net) else {
        return input.default_clearance_mm;
    };
    input
        .net_class_clearances_mm
        .get(class)
        .copied()
        .unwrap_or(input.default_clearance_mm)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn smoke_check_all_on_empty_input_returns_empty() {
        let input = DrcInput::default();
        assert!(check_all(&input).is_empty());
    }

    #[test]
    fn smoke_clearance_between_same_net_is_zero() {
        let input = DrcInput {
            default_clearance_mm: 0.25,
            ..DrcInput::default()
        };
        assert!(clearance_between(&input, "GND", "GND").abs() < 1e-12);
    }

    #[test]
    fn smoke_clearance_between_falls_back_to_default() {
        let input = DrcInput {
            default_clearance_mm: 0.3,
            ..DrcInput::default()
        };
        assert!((clearance_between(&input, "GND", "VCC") - 0.3).abs() < 1e-9);
    }

    #[test]
    fn smoke_clearance_between_picks_max_of_two_classes() {
        let mut classes = std::collections::HashMap::new();
        classes.insert("Power".to_string(), 0.5);
        classes.insert("Signal".to_string(), 0.2);
        let mut nets = std::collections::HashMap::new();
        nets.insert("VCC".to_string(), "Power".to_string());
        nets.insert("D0".to_string(), "Signal".to_string());
        let input = DrcInput {
            default_clearance_mm: 0.15,
            net_class_clearances_mm: classes,
            net_to_class: nets,
            ..DrcInput::default()
        };
        // Power (0.5) > Signal (0.2) > default (0.15) → 0.5.
        assert!((clearance_between(&input, "VCC", "D0") - 0.5).abs() < 1e-9);
    }

    #[test]
    fn smoke_clearance_between_unknown_net_uses_default() {
        let input = DrcInput {
            default_clearance_mm: 0.25,
            ..DrcInput::default()
        };
        assert!((clearance_between(&input, "FOO", "BAR") - 0.25).abs() < 1e-9);
    }

    #[test]
    fn smoke_empty_net_treated_as_unrouted() {
        // Two unconnected items (empty net) still need clearance,
        // since `""` is not the same as a real same-net match.
        let input = DrcInput {
            default_clearance_mm: 0.25,
            ..DrcInput::default()
        };
        assert!((clearance_between(&input, "", "") - 0.25).abs() < 1e-9);
    }
}
