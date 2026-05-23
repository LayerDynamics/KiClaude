//! Drill ↔ copper check.
//!
//! A drill hole (mechanical bore through the board) sitting too close
//! to foreign-net copper can short or weaken the board during
//! fabrication. The rule:
//!
//! ```text
//!     (drill_edge_to_copper_edge) ≥ min_drill_to_copper_mm
//! ```
//!
//! We check every through-hole feature (via, THT pad) against every
//! copper item on a shared layer that isn't on the same net:
//!
//! - vs tracks: drill-edge to track-edge
//! - vs vias (other through-holes): drill-edge to via-pad-edge
//! - vs SMD pads: drill-edge to pad-edge
//! - vs other THT pads: drill-edge to drill-edge AND drill-edge to
//!   pad-edge
//!
//! Drill-to-drill (hole-to-hole) clearance is technically a separate
//! fab rule — `kicad-cli` reports it as `hole_clearance`. We DO check
//! drill-vs-other-drill here so the kernel surfaces it under
//! `drill_to_copper_violation`; the cross-check test will treat
//! `hole_clearance` reports from `kicad-cli` as compatible matches.

use super::{DrcInput, DrcIssue, DrcIssueKind, DrcPad, DrcPadShape, DrcSeverity};

#[cfg(test)]
use super::DrcVia;

/// Run the drill-to-copper check and return findings.
#[must_use]
pub fn check(input: &DrcInput) -> Vec<DrcIssue> {
    if input.min_drill_to_copper_mm <= 0.0 {
        return Vec::new();
    }
    let min = input.min_drill_to_copper_mm;
    let mut issues = Vec::new();

    let drills = collect_drills(input);

    // Drill vs track.
    for drill in &drills {
        for track in &input.tracks {
            if !shares_layer(&drill.layers, &track.layer) {
                continue;
            }
            if !drill.net.is_empty() && drill.net == track.net {
                continue;
            }
            let dist = segment_point_distance(track.start_mm, track.end_mm, drill.center)
                - track.width_mm * 0.5
                - drill.drill_mm * 0.5;
            if dist < min {
                issues.push(DrcIssue {
                    severity: DrcSeverity::Error,
                    kind: DrcIssueKind::DrillToCopperViolation,
                    position_mm: drill.center,
                    layer: track.layer.clone(),
                    description: format!(
                        "drill {} ({}) to track clearance {dist:.3} mm < minimum {min:.3} mm",
                        drill.id, drill.kind_label,
                    ),
                    items: vec![drill.id.clone(), track.uuid.clone()],
                    deficit_mm: (min - dist).max(0.0),
                });
            }
        }
    }

    // Drill vs other drill (hole-to-hole).
    for i in 0..drills.len() {
        for j in (i + 1)..drills.len() {
            let a = &drills[i];
            let b = &drills[j];
            if !a.layers.iter().any(|la| b.layers.iter().any(|lb| la == lb)) {
                continue;
            }
            if !a.net.is_empty() && a.net == b.net {
                continue;
            }
            let centre = a.center.distance_to(&b.center);
            let drill_edges = centre - (a.drill_mm + b.drill_mm) * 0.5;
            if drill_edges < min {
                issues.push(DrcIssue {
                    severity: DrcSeverity::Error,
                    kind: DrcIssueKind::DrillToCopperViolation,
                    position_mm: midpoint(a.center, b.center),
                    layer: a.layers.first().cloned().unwrap_or_else(|| "any".into()),
                    description: format!(
                        "drill-to-drill clearance {drill_edges:.3} mm < minimum {min:.3} mm ({} ↔ {})",
                        a.id, b.id,
                    ),
                    items: vec![a.id.clone(), b.id.clone()],
                    deficit_mm: (min - drill_edges).max(0.0),
                });
            }
        }
    }

    // Drill vs pad copper (SMD or THT pad outer on a different net).
    for drill in &drills {
        for pad in &input.pads {
            if !pad.layers.iter().any(|pl| layers_share(&drill.layers, pl)) {
                continue;
            }
            // Skip the pad that owns this drill — only check against
            // *other* pads.
            if drill.owner_pad.as_deref() == Some(&pad_id(pad)) {
                continue;
            }
            if !drill.net.is_empty() && drill.net == pad.net {
                continue;
            }
            let pad_radius = pad_outer_radius(pad);
            let dist = drill.center.distance_to(&pad.center_mm) - drill.drill_mm * 0.5 - pad_radius;
            if dist < min {
                issues.push(DrcIssue {
                    severity: DrcSeverity::Error,
                    kind: DrcIssueKind::DrillToCopperViolation,
                    position_mm: drill.center,
                    layer: pad.layers.first().cloned().unwrap_or_else(|| "any".into()),
                    description: format!(
                        "drill {} to pad {}.{} clearance {dist:.3} mm < minimum {min:.3} mm",
                        drill.id, pad.footprint_refdes, pad.number,
                    ),
                    items: vec![drill.id.clone(), pad_id(pad)],
                    deficit_mm: (min - dist).max(0.0),
                });
            }
        }
    }

    issues
}

/// Internal — every drill in the design with enough metadata to check.
#[derive(Debug, Clone)]
#[allow(clippy::struct_field_names)] // `drill_mm` matches the upstream DrcVia / DrcPad field name.
struct Drill {
    id: String,
    /// Diagnostic label — `"via"` or `"pad"` — for the description line.
    kind_label: &'static str,
    /// Refdes.padnum if the drill is owned by a pad; `None` for vias.
    owner_pad: Option<String>,
    net: String,
    center: crate::geom::Point,
    drill_mm: f64,
    layers: Vec<String>,
}

fn collect_drills(input: &DrcInput) -> Vec<Drill> {
    let mut out = Vec::with_capacity(input.vias.len() + input.pads.len());
    for via in &input.vias {
        if via.drill_mm > 0.0 {
            out.push(Drill {
                id: via.uuid.clone(),
                kind_label: "via",
                owner_pad: None,
                net: via.net.clone(),
                center: via.position_mm,
                drill_mm: via.drill_mm,
                layers: via.layers.clone(),
            });
        }
    }
    for pad in &input.pads {
        if pad.drill_mm > 0.0 {
            out.push(Drill {
                id: pad_id(pad),
                kind_label: "pad",
                owner_pad: Some(pad_id(pad)),
                net: pad.net.clone(),
                center: pad.center_mm,
                drill_mm: pad.drill_mm,
                layers: pad.layers.clone(),
            });
        }
    }
    out
}

fn pad_id(pad: &DrcPad) -> String {
    format!("{}.{}", pad.footprint_refdes, pad.number)
}

fn pad_outer_radius(pad: &DrcPad) -> f64 {
    let hx = pad.size_mm.0 * 0.5;
    let hy = pad.size_mm.1 * 0.5;
    match pad.shape {
        DrcPadShape::Circle => hx.min(hy),
        DrcPadShape::Oval => hx.max(hy),
        DrcPadShape::Rect | DrcPadShape::RoundRect => (hx * hx + hy * hy).sqrt(),
    }
}

fn shares_layer(drill_layers: &[String], layer: &str) -> bool {
    drill_layers
        .iter()
        .any(|l| l == layer || is_wild_match(l, layer))
}

fn layers_share(drill_layers: &[String], layer: &str) -> bool {
    shares_layer(drill_layers, layer)
}

fn is_wild_match(a: &str, b: &str) -> bool {
    a == "*.Cu" && (b == "F.Cu" || b == "B.Cu" || (b.starts_with("In") && b.ends_with(".Cu")))
}

fn midpoint(a: crate::geom::Point, b: crate::geom::Point) -> crate::geom::Point {
    crate::geom::Point::new((a.x + b.x) * 0.5, (a.y + b.y) * 0.5)
}

fn segment_point_distance(
    s: crate::geom::Point,
    e: crate::geom::Point,
    p: crate::geom::Point,
) -> f64 {
    let dx = e.x - s.x;
    let dy = e.y - s.y;
    let len2 = dx * dx + dy * dy;
    if len2 < f64::EPSILON {
        return s.distance_to(&p);
    }
    let t = (((p.x - s.x) * dx + (p.y - s.y) * dy) / len2).clamp(0.0, 1.0);
    crate::geom::Point::new(s.x + t * dx, s.y + t * dy).distance_to(&p)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::geom::Point;

    fn via(uuid: &str, net: &str, x: f64, y: f64, dia: f64, drill: f64) -> DrcVia {
        DrcVia {
            uuid: uuid.into(),
            net: net.into(),
            position_mm: Point::new(x, y),
            layers: vec!["F.Cu".into(), "B.Cu".into()],
            drill_mm: drill,
            diameter_mm: dia,
        }
    }

    fn tht_pad(refdes: &str, num: &str, net: &str, x: f64, y: f64, drill: f64) -> DrcPad {
        DrcPad {
            footprint_refdes: refdes.into(),
            number: num.into(),
            net: net.into(),
            center_mm: Point::new(x, y),
            size_mm: (1.7, 1.7),
            rotation_deg: 0.0,
            layers: vec!["F.Cu".into(), "B.Cu".into()],
            shape: DrcPadShape::Circle,
            drill_mm: drill,
        }
    }

    fn track(
        uuid: &str,
        net: &str,
        sx: f64,
        sy: f64,
        ex: f64,
        ey: f64,
        w: f64,
    ) -> super::super::DrcTrack {
        super::super::DrcTrack {
            uuid: uuid.into(),
            net: net.into(),
            layer: "F.Cu".into(),
            start_mm: Point::new(sx, sy),
            end_mm: Point::new(ex, ey),
            width_mm: w,
        }
    }

    #[test]
    fn smoke_drill_close_to_track_flags() {
        let v = via("v0", "VIA_NET", 0.0, 0.0, 0.8, 0.4);
        let t = track("t0", "OTHER", 0.0, 0.3, 5.0, 0.3, 0.2);
        let input = DrcInput {
            tracks: vec![t],
            vias: vec![v],
            min_drill_to_copper_mm: 0.2,
            ..DrcInput::default()
        };
        let issues = check(&input);
        // dist = 0.3 - 0.1 (half track w) - 0.2 (half drill) = 0.0 < 0.2 → flag.
        assert_eq!(issues.len(), 1);
        assert_eq!(issues[0].kind, DrcIssueKind::DrillToCopperViolation);
    }

    #[test]
    fn smoke_drill_same_net_track_no_violation() {
        let v = via("v0", "NET", 0.0, 0.0, 0.8, 0.4);
        let t = track("t0", "NET", 0.0, 0.3, 5.0, 0.3, 0.2);
        let input = DrcInput {
            tracks: vec![t],
            vias: vec![v],
            min_drill_to_copper_mm: 0.2,
            ..DrcInput::default()
        };
        assert!(check(&input).is_empty());
    }

    #[test]
    fn smoke_drill_to_drill_flags() {
        let v0 = via("v0", "A", 0.0, 0.0, 0.6, 0.3);
        let v1 = via("v1", "B", 0.4, 0.0, 0.6, 0.3);
        let input = DrcInput {
            vias: vec![v0, v1],
            min_drill_to_copper_mm: 0.2,
            ..DrcInput::default()
        };
        let issues = check(&input);
        // drill-edge to drill-edge = 0.4 - 0.3 = 0.1 < 0.2.
        assert!(issues
            .iter()
            .any(|i| i.description.contains("drill-to-drill")));
    }

    #[test]
    fn smoke_pad_owning_drill_doesnt_self_violate() {
        // A THT pad's drill must not flag a violation against its OWN
        // pad copper.
        let p = tht_pad("J1", "1", "NET", 0.0, 0.0, 1.0);
        let input = DrcInput {
            pads: vec![p],
            min_drill_to_copper_mm: 0.5,
            ..DrcInput::default()
        };
        let issues = check(&input);
        assert!(issues.is_empty(), "self-pair must not flag: {issues:?}");
    }

    #[test]
    fn smoke_zero_min_disables_check() {
        let v = via("v0", "A", 0.0, 0.0, 0.6, 0.3);
        let t = track("t0", "B", 0.0, 0.0, 1.0, 0.0, 0.5);
        let input = DrcInput {
            tracks: vec![t],
            vias: vec![v],
            min_drill_to_copper_mm: 0.0,
            ..DrcInput::default()
        };
        assert!(check(&input).is_empty());
    }
}
