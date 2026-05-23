//! Annular ring check.
//!
//! For any through-hole feature (via or THT pad), the **annular ring**
//! is the copper width between the drill hole and the outer pad edge.
//! If this width is below `min_annular_ring_mm`, the fab might drill
//! through the copper ring during PCB fabrication, breaking the
//! electrical connection.
//!
//! Formula:
//!
//! ```text
//!     annular_width = (outer_diameter - drill_diameter) / 2
//! ```
//!
//! For round vias and circular THT pads this is exact. For
//! rectangular / round-rect THT pads the annular ring is measured
//! along the **shortest** axis — the dimension where the drill is
//! closest to the pad outer edge. `kicad-cli` uses the same
//! convention (it picks the smallest of the four ring-distances on
//! rectangular pads).

use super::{DrcInput, DrcIssue, DrcIssueKind, DrcPad, DrcPadShape, DrcSeverity, DrcVia};

#[cfg(test)]
use crate::geom::Point;

/// Run the annular-ring check across all vias and THT pads.
#[must_use]
pub fn check(input: &DrcInput) -> Vec<DrcIssue> {
    let mut issues = Vec::new();
    if input.min_annular_ring_mm <= 0.0 {
        return issues;
    }
    for via in &input.vias {
        if let Some(issue) = check_via(via, input.min_annular_ring_mm) {
            issues.push(issue);
        }
    }
    for pad in &input.pads {
        if pad.drill_mm <= 0.0 {
            continue; // SMD — no annular ring concept.
        }
        if let Some(issue) = check_pad(pad, input.min_annular_ring_mm) {
            issues.push(issue);
        }
    }
    issues
}

fn check_via(via: &DrcVia, min_ring: f64) -> Option<DrcIssue> {
    if via.drill_mm <= 0.0 || via.diameter_mm <= 0.0 {
        return None;
    }
    let ring = (via.diameter_mm - via.drill_mm) * 0.5;
    if ring >= min_ring {
        return None;
    }
    Some(DrcIssue {
        severity: DrcSeverity::Error,
        kind: DrcIssueKind::AnnularRingViolation,
        position_mm: via.position_mm,
        layer: via.layers.first().cloned().unwrap_or_else(|| "any".into()),
        description: format!(
            "via annular ring {ring:.3} mm < minimum {min_ring:.3} mm (drill {} mm, diameter {} mm, net {})",
            via.drill_mm, via.diameter_mm, via.net,
        ),
        items: vec![via.uuid.clone()],
        deficit_mm: (min_ring - ring).max(0.0),
    })
}

fn check_pad(pad: &DrcPad, min_ring: f64) -> Option<DrcIssue> {
    let outer = pad_outer_short_axis(pad);
    if outer <= 0.0 || pad.drill_mm <= 0.0 {
        return None;
    }
    let ring = (outer - pad.drill_mm) * 0.5;
    if ring >= min_ring {
        return None;
    }
    Some(DrcIssue {
        severity: DrcSeverity::Error,
        kind: DrcIssueKind::AnnularRingViolation,
        position_mm: pad.center_mm,
        layer: pad.layers.first().cloned().unwrap_or_else(|| "any".into()),
        description: format!(
            "pad {}.{} annular ring {ring:.3} mm < minimum {min_ring:.3} mm (drill {} mm, pad shortest dim {} mm)",
            pad.footprint_refdes, pad.number, pad.drill_mm, outer,
        ),
        items: vec![format!("{}.{}", pad.footprint_refdes, pad.number)],
        deficit_mm: (min_ring - ring).max(0.0),
    })
}

/// Outer pad dimension along the shortest axis. For a circular pad
/// this is its diameter; for a rect / round-rect it's the smaller of
/// the two sides; for an oval the smaller dimension.
fn pad_outer_short_axis(pad: &DrcPad) -> f64 {
    match pad.shape {
        DrcPadShape::Circle => pad.size_mm.0.min(pad.size_mm.1),
        DrcPadShape::Oval | DrcPadShape::Rect | DrcPadShape::RoundRect => {
            pad.size_mm.0.min(pad.size_mm.1)
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn via(uuid: &str, dia: f64, drill: f64) -> DrcVia {
        DrcVia {
            uuid: uuid.into(),
            net: "N1".into(),
            position_mm: Point::new(0.0, 0.0),
            layers: vec!["F.Cu".into(), "B.Cu".into()],
            drill_mm: drill,
            diameter_mm: dia,
        }
    }

    fn tht_pad(num: &str, shape: DrcPadShape, w: f64, h: f64, drill: f64) -> DrcPad {
        DrcPad {
            footprint_refdes: "J1".into(),
            number: num.into(),
            net: "N1".into(),
            center_mm: Point::new(0.0, 0.0),
            size_mm: (w, h),
            rotation_deg: 0.0,
            layers: vec!["F.Cu".into()],
            shape,
            drill_mm: drill,
        }
    }

    #[test]
    fn smoke_via_with_thin_ring_flags() {
        // Diameter 0.5, drill 0.35 → ring = 0.075. Below 0.1 minimum.
        let v = via("v0", 0.5, 0.35);
        let input = DrcInput {
            vias: vec![v],
            min_annular_ring_mm: 0.1,
            ..DrcInput::default()
        };
        let issues = check(&input);
        assert_eq!(issues.len(), 1);
        assert_eq!(issues[0].kind, DrcIssueKind::AnnularRingViolation);
        assert!((issues[0].deficit_mm - 0.025).abs() < 1e-9);
    }

    #[test]
    fn smoke_via_with_fat_ring_passes() {
        let v = via("v0", 0.8, 0.4);
        let input = DrcInput {
            vias: vec![v],
            min_annular_ring_mm: 0.1,
            ..DrcInput::default()
        };
        assert!(check(&input).is_empty());
    }

    #[test]
    fn smoke_zero_min_annular_disables_check() {
        let v = via("v0", 0.30, 0.30);
        let input = DrcInput {
            vias: vec![v],
            min_annular_ring_mm: 0.0,
            ..DrcInput::default()
        };
        assert!(check(&input).is_empty());
    }

    #[test]
    fn smoke_smd_pad_skipped() {
        let p = tht_pad("1", DrcPadShape::Rect, 1.0, 1.0, 0.0);
        let input = DrcInput {
            pads: vec![p],
            min_annular_ring_mm: 0.1,
            ..DrcInput::default()
        };
        assert!(check(&input).is_empty());
    }

    #[test]
    fn smoke_tht_pad_rect_uses_shorter_axis() {
        // 1.5×1.2 rectangle with 1.0 drill. Shorter axis = 1.2.
        // Ring = (1.2 - 1.0)/2 = 0.1. With minimum 0.15 → fails.
        let p = tht_pad("1", DrcPadShape::Rect, 1.5, 1.2, 1.0);
        let input = DrcInput {
            pads: vec![p],
            min_annular_ring_mm: 0.15,
            ..DrcInput::default()
        };
        let issues = check(&input);
        assert_eq!(issues.len(), 1);
        assert!((issues[0].deficit_mm - 0.05).abs() < 1e-9);
    }

    #[test]
    fn smoke_tht_pad_circle_uses_diameter() {
        let p = tht_pad("1", DrcPadShape::Circle, 1.0, 1.0, 0.8);
        let input = DrcInput {
            pads: vec![p],
            min_annular_ring_mm: 0.15,
            ..DrcInput::default()
        };
        let issues = check(&input);
        assert_eq!(issues.len(), 1);
        // Ring = (1.0 - 0.8)/2 = 0.1; deficit 0.05.
        assert!((issues[0].deficit_mm - 0.05).abs() < 1e-9);
    }
}
