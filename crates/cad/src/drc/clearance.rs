//! Copper-to-copper clearance — track ↔ track, track ↔ pad, pad ↔ pad
//! pair checks. Same-layer, foreign-net pairs only.
//!
//! Geometry:
//! - A track is a capsule (rectangle + two semicircular endcaps).
//! - A via is a disc.
//! - A pad approximates as a rotated rounded-rectangle (for `Rect` /
//!   `RoundRect` / `Oval` shapes) or as a disc (for `Circle`).
//!
//! The "distance" between two shapes is the **minimum gap** between
//! their outer edges. We compute this analytically (no rasterisation)
//! using closed-form formulas per shape pair. The check is conservative
//! when the analytic distance is hard (rotated rect ↔ rotated rect) —
//! we approximate the pad by its bounding circle in such cases, which
//! may under-report clearance (= over-report violations). The
//! cross-check test against `kicad-cli` filters out any of our
//! false-positives that `kicad-cli` doesn't agree with, per the
//! M2-R-06 done-when.

use crate::geom::Point;

use super::{
    clearance_between, DrcInput, DrcIssue, DrcIssueKind, DrcPad, DrcPadShape, DrcSeverity,
};

#[cfg(test)]
use super::{DrcTrack, DrcVia};

/// Run all clearance checks and return findings.
#[must_use]
pub fn check(input: &DrcInput) -> Vec<DrcIssue> {
    let mut issues = Vec::new();
    track_track(input, &mut issues);
    track_via(input, &mut issues);
    track_pad(input, &mut issues);
    via_via(input, &mut issues);
    via_pad(input, &mut issues);
    pad_pad(input, &mut issues);
    issues
}

fn track_track(input: &DrcInput, out: &mut Vec<DrcIssue>) {
    let n = input.tracks.len();
    for i in 0..n {
        for j in (i + 1)..n {
            let a = &input.tracks[i];
            let b = &input.tracks[j];
            if !share_layer(&a.layer, &b.layer) {
                continue;
            }
            if same_net(&a.net, &b.net) {
                continue;
            }
            let required = clearance_between(input, &a.net, &b.net);
            let centre_dist = segment_segment_distance(a.start_mm, a.end_mm, b.start_mm, b.end_mm);
            let gap = centre_dist - (a.width_mm + b.width_mm) * 0.5;
            if gap < required {
                let pos = midpoint(
                    closest_point_segment(a.start_mm, a.end_mm, b.start_mm),
                    b.start_mm,
                );
                out.push(DrcIssue {
                    severity: DrcSeverity::Error,
                    kind: DrcIssueKind::ClearanceViolation,
                    position_mm: pos,
                    layer: a.layer.clone(),
                    description: format!(
                        "track-to-track clearance {gap:.3} mm < required {required:.3} mm on layer {} (nets {} vs {})",
                        a.layer, a.net, b.net,
                    ),
                    items: vec![a.uuid.clone(), b.uuid.clone()],
                    deficit_mm: (required - gap).max(0.0),
                });
            }
        }
    }
}

fn track_via(input: &DrcInput, out: &mut Vec<DrcIssue>) {
    for track in &input.tracks {
        for via in &input.vias {
            if !via.layers.iter().any(|l| layers_match(&track.layer, l)) {
                continue;
            }
            if same_net(&track.net, &via.net) {
                continue;
            }
            let required = clearance_between(input, &track.net, &via.net);
            let dist = segment_point_distance(track.start_mm, track.end_mm, via.position_mm);
            let gap = dist - track.width_mm * 0.5 - via.diameter_mm * 0.5;
            if gap < required {
                out.push(DrcIssue {
                    severity: DrcSeverity::Error,
                    kind: DrcIssueKind::ClearanceViolation,
                    position_mm: via.position_mm,
                    layer: track.layer.clone(),
                    description: format!(
                        "track-to-via clearance {gap:.3} mm < required {required:.3} mm (nets {} vs {})",
                        track.net, via.net,
                    ),
                    items: vec![track.uuid.clone(), via.uuid.clone()],
                    deficit_mm: (required - gap).max(0.0),
                });
            }
        }
    }
}

fn track_pad(input: &DrcInput, out: &mut Vec<DrcIssue>) {
    for track in &input.tracks {
        for pad in &input.pads {
            if !pad.layers.iter().any(|l| layers_match(&track.layer, l)) {
                continue;
            }
            if same_net(&track.net, &pad.net) {
                continue;
            }
            let required = clearance_between(input, &track.net, &pad.net);
            let dist = segment_point_distance(track.start_mm, track.end_mm, pad.center_mm);
            let gap = dist - track.width_mm * 0.5 - pad_outer_radius(pad);
            if gap < required {
                out.push(DrcIssue {
                    severity: DrcSeverity::Error,
                    kind: DrcIssueKind::ClearanceViolation,
                    position_mm: pad.center_mm,
                    layer: track.layer.clone(),
                    description: format!(
                        "track-to-pad clearance {gap:.3} mm < required {required:.3} mm (track net {} vs pad {}.{} net {})",
                        track.net, pad.footprint_refdes, pad.number, pad.net,
                    ),
                    items: vec![
                        track.uuid.clone(),
                        format!("{}.{}", pad.footprint_refdes, pad.number),
                    ],
                    deficit_mm: (required - gap).max(0.0),
                });
            }
        }
    }
}

fn via_via(input: &DrcInput, out: &mut Vec<DrcIssue>) {
    let n = input.vias.len();
    for i in 0..n {
        for j in (i + 1)..n {
            let a = &input.vias[i];
            let b = &input.vias[j];
            if !a.layers.iter().any(|la| b.layers.iter().any(|lb| la == lb)) {
                continue;
            }
            if same_net(&a.net, &b.net) {
                continue;
            }
            let required = clearance_between(input, &a.net, &b.net);
            let dist = distance(a.position_mm, b.position_mm);
            let gap = dist - (a.diameter_mm + b.diameter_mm) * 0.5;
            if gap < required {
                out.push(DrcIssue {
                    severity: DrcSeverity::Error,
                    kind: DrcIssueKind::ClearanceViolation,
                    position_mm: midpoint(a.position_mm, b.position_mm),
                    layer: a.layers.first().cloned().unwrap_or_else(|| "any".into()),
                    description: format!(
                        "via-to-via clearance {gap:.3} mm < required {required:.3} mm (nets {} vs {})",
                        a.net, b.net,
                    ),
                    items: vec![a.uuid.clone(), b.uuid.clone()],
                    deficit_mm: (required - gap).max(0.0),
                });
            }
        }
    }
}

fn via_pad(input: &DrcInput, out: &mut Vec<DrcIssue>) {
    for via in &input.vias {
        for pad in &input.pads {
            if !pad
                .layers
                .iter()
                .any(|pl| via.layers.iter().any(|vl| layers_match(pl, vl)))
            {
                continue;
            }
            if same_net(&via.net, &pad.net) {
                continue;
            }
            let required = clearance_between(input, &via.net, &pad.net);
            let dist = distance(via.position_mm, pad.center_mm);
            let gap = dist - via.diameter_mm * 0.5 - pad_outer_radius(pad);
            if gap < required {
                out.push(DrcIssue {
                    severity: DrcSeverity::Error,
                    kind: DrcIssueKind::ClearanceViolation,
                    position_mm: midpoint(via.position_mm, pad.center_mm),
                    layer: via.layers.first().cloned().unwrap_or_else(|| "any".into()),
                    description: format!(
                        "via-to-pad clearance {gap:.3} mm < required {required:.3} mm (via net {} vs pad {}.{} net {})",
                        via.net, pad.footprint_refdes, pad.number, pad.net,
                    ),
                    items: vec![
                        via.uuid.clone(),
                        format!("{}.{}", pad.footprint_refdes, pad.number),
                    ],
                    deficit_mm: (required - gap).max(0.0),
                });
            }
        }
    }
}

fn pad_pad(input: &DrcInput, out: &mut Vec<DrcIssue>) {
    let n = input.pads.len();
    for i in 0..n {
        for j in (i + 1)..n {
            let a = &input.pads[i];
            let b = &input.pads[j];
            if !a
                .layers
                .iter()
                .any(|la| b.layers.iter().any(|lb| layers_match(la, lb)))
            {
                continue;
            }
            if same_net(&a.net, &b.net) {
                continue;
            }
            let required = clearance_between(input, &a.net, &b.net);
            let dist = distance(a.center_mm, b.center_mm);
            let gap = dist - pad_outer_radius(a) - pad_outer_radius(b);
            if gap < required {
                out.push(DrcIssue {
                    severity: DrcSeverity::Error,
                    kind: DrcIssueKind::ClearanceViolation,
                    position_mm: midpoint(a.center_mm, b.center_mm),
                    layer: a.layers.first().cloned().unwrap_or_else(|| "any".into()),
                    description: format!(
                        "pad-to-pad clearance {gap:.3} mm < required {required:.3} mm ({}.{} net {} vs {}.{} net {})",
                        a.footprint_refdes, a.number, a.net,
                        b.footprint_refdes, b.number, b.net,
                    ),
                    items: vec![
                        format!("{}.{}", a.footprint_refdes, a.number),
                        format!("{}.{}", b.footprint_refdes, b.number),
                    ],
                    deficit_mm: (required - gap).max(0.0),
                });
            }
        }
    }
}

/// Conservative outer-radius approximation of a pad — the
/// bounding-circle radius. For round pads this is exact; for rect /
/// roundrect / oval it's a slight over-estimate (uses the bbox
/// diagonal). Conservative-over means we MAY flag clearance issues
/// `kicad-cli` doesn't; the cross-check filters such reports.
fn pad_outer_radius(pad: &DrcPad) -> f64 {
    let hx = pad.size_mm.0 * 0.5;
    let hy = pad.size_mm.1 * 0.5;
    match pad.shape {
        DrcPadShape::Circle => hx.min(hy),
        DrcPadShape::Oval => hx.max(hy),
        DrcPadShape::Rect | DrcPadShape::RoundRect => (hx * hx + hy * hy).sqrt(),
    }
}

fn same_net(a: &str, b: &str) -> bool {
    !a.is_empty() && a == b
}

fn share_layer(a: &str, b: &str) -> bool {
    layers_match(a, b)
}

/// Layer-membership predicate that resolves wildcards.
///
/// `*.Cu` matches every copper layer (`F.Cu`, `In1.Cu`, `B.Cu`, …).
/// `F&B.Cu` matches `F.Cu` and `B.Cu`. Anything else is an exact
/// name match. This mirrors `KiCad`'s pad-layer set vocabulary.
fn layers_match(a: &str, b: &str) -> bool {
    if a == b {
        return true;
    }
    let is_wild_cu = |s: &str| s == "*.Cu" || s == "F&B.Cu";
    let is_cu = |s: &str| s == "F.Cu" || s == "B.Cu" || s.starts_with("In") && s.ends_with(".Cu");
    if is_wild_cu(a) {
        // `*.Cu` matches any copper layer; `F&B.Cu` matches only F/B copper.
        if a == "*.Cu" {
            return is_cu(b);
        }
        return b == "F.Cu" || b == "B.Cu";
    }
    if is_wild_cu(b) {
        if b == "*.Cu" {
            return is_cu(a);
        }
        return a == "F.Cu" || a == "B.Cu";
    }
    false
}

fn distance(a: Point, b: Point) -> f64 {
    a.distance_to(&b)
}

fn midpoint(a: Point, b: Point) -> Point {
    Point::new((a.x + b.x) * 0.5, (a.y + b.y) * 0.5)
}

/// Shortest distance from a point to a line segment.
fn segment_point_distance(s: Point, e: Point, p: Point) -> f64 {
    let dx = e.x - s.x;
    let dy = e.y - s.y;
    let len2 = dx * dx + dy * dy;
    if len2 < f64::EPSILON {
        return s.distance_to(&p);
    }
    let t = ((p.x - s.x) * dx + (p.y - s.y) * dy) / len2;
    let t = t.clamp(0.0, 1.0);
    let proj = Point::new(s.x + t * dx, s.y + t * dy);
    proj.distance_to(&p)
}

/// Closest point on segment `s..e` to external point `p`. Used when
/// reporting the on-screen marker for a track-track violation.
fn closest_point_segment(s: Point, e: Point, p: Point) -> Point {
    let dx = e.x - s.x;
    let dy = e.y - s.y;
    let len2 = dx * dx + dy * dy;
    if len2 < f64::EPSILON {
        return s;
    }
    let t = (((p.x - s.x) * dx + (p.y - s.y) * dy) / len2).clamp(0.0, 1.0);
    Point::new(s.x + t * dx, s.y + t * dy)
}

/// Minimum distance between two line segments. Uses the standard
/// "candidate endpoint" reduction: if the segments don't intersect,
/// the minimum is achieved at an endpoint of one segment projected to
/// the other.
fn segment_segment_distance(a0: Point, a1: Point, b0: Point, b1: Point) -> f64 {
    if segments_intersect(a0, a1, b0, b1) {
        return 0.0;
    }
    let candidates = [
        segment_point_distance(a0, a1, b0),
        segment_point_distance(a0, a1, b1),
        segment_point_distance(b0, b1, a0),
        segment_point_distance(b0, b1, a1),
    ];
    candidates.into_iter().fold(f64::INFINITY, f64::min)
}

fn segments_intersect(p1: Point, p2: Point, p3: Point, p4: Point) -> bool {
    let d1 = orient(p3, p4, p1);
    let d2 = orient(p3, p4, p2);
    let d3 = orient(p1, p2, p3);
    let d4 = orient(p1, p2, p4);
    if ((d1 > 0.0 && d2 < 0.0) || (d1 < 0.0 && d2 > 0.0))
        && ((d3 > 0.0 && d4 < 0.0) || (d3 < 0.0 && d4 > 0.0))
    {
        return true;
    }
    if d1 == 0.0 && on_segment(p3, p4, p1) {
        return true;
    }
    if d2 == 0.0 && on_segment(p3, p4, p2) {
        return true;
    }
    if d3 == 0.0 && on_segment(p1, p2, p3) {
        return true;
    }
    if d4 == 0.0 && on_segment(p1, p2, p4) {
        return true;
    }
    false
}

fn orient(a: Point, b: Point, c: Point) -> f64 {
    (b.x - a.x) * (c.y - a.y) - (b.y - a.y) * (c.x - a.x)
}

fn on_segment(a: Point, b: Point, p: Point) -> bool {
    p.x >= a.x.min(b.x) && p.x <= a.x.max(b.x) && p.y >= a.y.min(b.y) && p.y <= a.y.max(b.y)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn track(uuid: &str, net: &str, sx: f64, sy: f64, ex: f64, ey: f64, w: f64) -> DrcTrack {
        DrcTrack {
            uuid: uuid.into(),
            net: net.into(),
            layer: "F.Cu".into(),
            start_mm: Point::new(sx, sy),
            end_mm: Point::new(ex, ey),
            width_mm: w,
        }
    }

    fn pad(refdes: &str, num: &str, net: &str, cx: f64, cy: f64, w: f64, h: f64) -> DrcPad {
        DrcPad {
            footprint_refdes: refdes.into(),
            number: num.into(),
            net: net.into(),
            center_mm: Point::new(cx, cy),
            size_mm: (w, h),
            rotation_deg: 0.0,
            layers: vec!["F.Cu".into()],
            shape: DrcPadShape::Rect,
            drill_mm: 0.0,
        }
    }

    fn via(uuid: &str, net: &str, cx: f64, cy: f64, dia: f64, drill: f64) -> DrcVia {
        DrcVia {
            uuid: uuid.into(),
            net: net.into(),
            position_mm: Point::new(cx, cy),
            layers: vec!["F.Cu".into(), "B.Cu".into()],
            drill_mm: drill,
            diameter_mm: dia,
        }
    }

    fn input_with(
        tracks: Vec<DrcTrack>,
        pads: Vec<DrcPad>,
        vias: Vec<DrcVia>,
        clearance: f64,
    ) -> DrcInput {
        DrcInput {
            tracks,
            pads,
            vias,
            default_clearance_mm: clearance,
            ..DrcInput::default()
        }
    }

    #[test]
    fn smoke_parallel_tracks_below_clearance_flag() {
        // Two parallel tracks, width 0.2 each, 0.1 mm gap → < 0.25
        // default clearance.
        let t0 = track("t0", "N1", 0.0, 0.0, 10.0, 0.0, 0.2);
        let t1 = track("t1", "N2", 0.0, 0.3, 10.0, 0.3, 0.2);
        let input = input_with(vec![t0, t1], vec![], vec![], 0.25);
        let issues = check(&input);
        assert_eq!(issues.len(), 1);
        assert_eq!(issues[0].kind, DrcIssueKind::ClearanceViolation);
    }

    #[test]
    fn smoke_parallel_tracks_above_clearance_pass() {
        let t0 = track("t0", "N1", 0.0, 0.0, 10.0, 0.0, 0.2);
        let t1 = track("t1", "N2", 0.0, 0.6, 10.0, 0.6, 0.2);
        let input = input_with(vec![t0, t1], vec![], vec![], 0.25);
        assert!(check(&input).is_empty());
    }

    #[test]
    fn smoke_same_net_tracks_ignored() {
        let t0 = track("t0", "GND", 0.0, 0.0, 10.0, 0.0, 0.2);
        let t1 = track("t1", "GND", 0.0, 0.1, 10.0, 0.1, 0.2);
        let input = input_with(vec![t0, t1], vec![], vec![], 0.25);
        assert!(check(&input).is_empty());
    }

    #[test]
    fn smoke_different_layer_tracks_ignored() {
        let mut t0 = track("t0", "N1", 0.0, 0.0, 10.0, 0.0, 0.2);
        let mut t1 = track("t1", "N2", 0.0, 0.1, 10.0, 0.1, 0.2);
        t0.layer = "F.Cu".into();
        t1.layer = "B.Cu".into();
        let input = input_with(vec![t0, t1], vec![], vec![], 0.25);
        assert!(check(&input).is_empty());
    }

    #[test]
    fn smoke_track_pad_too_close_flags() {
        let t0 = track("t0", "N1", 0.0, 0.0, 10.0, 0.0, 0.2);
        let p1 = pad("R1", "1", "N2", 5.0, 0.4, 0.5, 0.6);
        let input = input_with(vec![t0], vec![p1], vec![], 0.25);
        let issues = check(&input);
        // Pad bbox-diagonal radius = sqrt(0.25^2 + 0.3^2) ≈ 0.39.
        // Track half-width = 0.1. Centre distance = 0.4.
        // Gap = 0.4 - 0.1 - 0.39 ≈ -0.09 < 0.25 → violation.
        assert!(!issues.is_empty());
        assert_eq!(issues[0].kind, DrcIssueKind::ClearanceViolation);
    }

    #[test]
    fn smoke_via_via_too_close_flags() {
        let v0 = via("v0", "N1", 0.0, 0.0, 0.6, 0.3);
        let v1 = via("v1", "N2", 0.7, 0.0, 0.6, 0.3);
        let input = input_with(vec![], vec![], vec![v0, v1], 0.25);
        let issues = check(&input);
        // Centre-to-centre 0.7, sum radii 0.6 → gap 0.1 < 0.25.
        assert_eq!(issues.len(), 1);
    }

    #[test]
    fn smoke_pad_pad_too_close_flags() {
        let p0 = pad("R1", "1", "N1", 0.0, 0.0, 0.5, 0.5);
        let p1 = pad("R2", "1", "N2", 0.8, 0.0, 0.5, 0.5);
        let input = input_with(vec![], vec![p0, p1], vec![], 0.25);
        let issues = check(&input);
        // Pad radii ≈ 0.354; centre dist 0.8; gap ≈ 0.092.
        assert_eq!(issues.len(), 1);
    }

    #[test]
    fn smoke_layers_match_wildcard_cu() {
        assert!(layers_match("F.Cu", "*.Cu"));
        assert!(layers_match("*.Cu", "F.Cu"));
        assert!(layers_match("In3.Cu", "*.Cu"));
        assert!(!layers_match("F.SilkS", "*.Cu"));
    }

    #[test]
    fn smoke_layers_match_f_and_b_cu_skips_inner() {
        assert!(layers_match("F&B.Cu", "F.Cu"));
        assert!(layers_match("F&B.Cu", "B.Cu"));
        assert!(!layers_match("F&B.Cu", "In1.Cu"));
    }

    #[test]
    fn smoke_segment_segment_distance_perpendicular() {
        let d = segment_segment_distance(
            Point::new(0.0, 0.0),
            Point::new(10.0, 0.0),
            Point::new(5.0, 1.0),
            Point::new(5.0, 5.0),
        );
        assert!((d - 1.0).abs() < 1e-9);
    }

    #[test]
    fn smoke_segment_segment_distance_crossing_is_zero() {
        let d = segment_segment_distance(
            Point::new(0.0, 0.0),
            Point::new(10.0, 0.0),
            Point::new(5.0, -1.0),
            Point::new(5.0, 1.0),
        );
        assert!(d < 1e-9);
    }
}
