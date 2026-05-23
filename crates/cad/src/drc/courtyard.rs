//! Courtyard overlap check.
//!
//! Each footprint declares an `F.CrtYd` (front) or `B.CrtYd` (back)
//! polygon that represents its "no-fly zone" — the clear area its
//! body / leads need. Courtyards on the **same side** that overlap
//! are a fab-or-assembly red flag (parts may collide during reflow
//! or hand-soldering). `kicad-cli` flags these as
//! `courtyards_overlap`.
//!
//! The check is pure-geometric: for each pair of courtyards on the
//! same layer, ask whether their polygons intersect (any point of one
//! inside the other, or any edge crossing). We use even-odd
//! `contains_point` plus an edge-pair intersection test; this catches
//! every overlap including the degenerate cases where one courtyard
//! sits entirely inside another.

// `as f64` cast on the courtyard ring vertex count is safe — the
// largest pad footprint courtyards in practice have ≤ 64 vertices,
// well within f64's 52-bit mantissa.
#![allow(clippy::cast_precision_loss)]

use crate::geom::Point;

use super::{DrcCourtyard, DrcInput, DrcIssue, DrcIssueKind, DrcSeverity};

/// Run the courtyard-overlap check and return findings.
#[must_use]
pub fn check(input: &DrcInput) -> Vec<DrcIssue> {
    let mut issues = Vec::new();
    let n = input.courtyards.len();
    for i in 0..n {
        for j in (i + 1)..n {
            let a = &input.courtyards[i];
            let b = &input.courtyards[j];
            if a.layer != b.layer {
                continue;
            }
            if !polygons_overlap(a, b) {
                continue;
            }
            // Position marker: centroid of the bbox intersection, if
            // any, or the midpoint between courtyard centroids.
            let pos = overlap_marker(a, b);
            issues.push(DrcIssue {
                severity: DrcSeverity::Warning,
                kind: DrcIssueKind::CourtyardOverlap,
                position_mm: pos,
                layer: a.layer.clone(),
                description: format!(
                    "courtyard overlap on {}: {} ↔ {}",
                    a.layer, a.footprint_refdes, b.footprint_refdes,
                ),
                items: vec![a.footprint_refdes.clone(), b.footprint_refdes.clone()],
                deficit_mm: 0.0,
            });
        }
    }
    issues
}

fn polygons_overlap(a: &DrcCourtyard, b: &DrcCourtyard) -> bool {
    // Cheap bbox reject first.
    if !a
        .polygon
        .bounding_box()
        .intersects(&b.polygon.bounding_box())
    {
        return false;
    }
    // Either polygon containing the other's first vertex, or any edge
    // pair intersecting, signals overlap.
    if let Some(p) = a.polygon.points.first() {
        if b.polygon.contains_point(*p) {
            return true;
        }
    }
    if let Some(p) = b.polygon.points.first() {
        if a.polygon.contains_point(*p) {
            return true;
        }
    }
    rings_intersect(&a.polygon.points, &b.polygon.points)
}

fn rings_intersect(a: &[Point], b: &[Point]) -> bool {
    let na = a.len();
    let nb = b.len();
    if na < 2 || nb < 2 {
        return false;
    }
    for i in 0..na {
        let a0 = a[i];
        let a1 = a[(i + 1) % na];
        for j in 0..nb {
            let b0 = b[j];
            let b1 = b[(j + 1) % nb];
            if segments_intersect(a0, a1, b0, b1) {
                return true;
            }
        }
    }
    false
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

fn overlap_marker(a: &DrcCourtyard, b: &DrcCourtyard) -> Point {
    let bba = a.polygon.bounding_box();
    let bbb = b.polygon.bounding_box();
    if bba.intersects(&bbb) {
        let min_x = bba.min.x.max(bbb.min.x);
        let max_x = bba.max.x.min(bbb.max.x);
        let min_y = bba.min.y.max(bbb.min.y);
        let max_y = bba.max.y.min(bbb.max.y);
        return Point::new((min_x + max_x) * 0.5, (min_y + max_y) * 0.5);
    }
    let ca = centroid(&a.polygon.points);
    let cb = centroid(&b.polygon.points);
    Point::new((ca.x + cb.x) * 0.5, (ca.y + cb.y) * 0.5)
}

fn centroid(ring: &[Point]) -> Point {
    let n = ring.len();
    if n == 0 {
        return Point::new(0.0, 0.0);
    }
    let mut sx = 0.0;
    let mut sy = 0.0;
    for p in ring {
        sx += p.x;
        sy += p.y;
    }
    Point::new(sx / n as f64, sy / n as f64)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::geom::Polygon;

    fn rect_courtyard(refdes: &str, layer: &str, x: f64, y: f64, w: f64, h: f64) -> DrcCourtyard {
        let pts = vec![
            Point::new(x, y),
            Point::new(x + w, y),
            Point::new(x + w, y + h),
            Point::new(x, y + h),
        ];
        DrcCourtyard {
            footprint_refdes: refdes.into(),
            layer: layer.into(),
            polygon: Polygon::new(pts),
        }
    }

    #[test]
    fn smoke_overlapping_courtyards_flag() {
        let a = rect_courtyard("U1", "F.CrtYd", 0.0, 0.0, 5.0, 5.0);
        let b = rect_courtyard("U2", "F.CrtYd", 4.0, 4.0, 5.0, 5.0);
        let input = DrcInput {
            courtyards: vec![a, b],
            ..DrcInput::default()
        };
        let issues = check(&input);
        assert_eq!(issues.len(), 1);
        assert_eq!(issues[0].kind, DrcIssueKind::CourtyardOverlap);
        assert_eq!(issues[0].severity, DrcSeverity::Warning);
    }

    #[test]
    fn smoke_disjoint_courtyards_pass() {
        let a = rect_courtyard("U1", "F.CrtYd", 0.0, 0.0, 5.0, 5.0);
        let b = rect_courtyard("U2", "F.CrtYd", 10.0, 10.0, 5.0, 5.0);
        let input = DrcInput {
            courtyards: vec![a, b],
            ..DrcInput::default()
        };
        assert!(check(&input).is_empty());
    }

    #[test]
    fn smoke_different_layer_courtyards_dont_overlap() {
        let a = rect_courtyard("U1", "F.CrtYd", 0.0, 0.0, 5.0, 5.0);
        let b = rect_courtyard("U2", "B.CrtYd", 1.0, 1.0, 5.0, 5.0);
        let input = DrcInput {
            courtyards: vec![a, b],
            ..DrcInput::default()
        };
        assert!(check(&input).is_empty());
    }

    #[test]
    fn smoke_courtyard_inside_another_flags() {
        let outer = rect_courtyard("U1", "F.CrtYd", 0.0, 0.0, 10.0, 10.0);
        let inner = rect_courtyard("U2", "F.CrtYd", 2.0, 2.0, 3.0, 3.0);
        let input = DrcInput {
            courtyards: vec![outer, inner],
            ..DrcInput::default()
        };
        assert_eq!(check(&input).len(), 1);
    }

    #[test]
    fn smoke_three_overlapping_courtyards_report_three_pairs() {
        let a = rect_courtyard("U1", "F.CrtYd", 0.0, 0.0, 5.0, 5.0);
        let b = rect_courtyard("U2", "F.CrtYd", 2.0, 2.0, 5.0, 5.0);
        let c = rect_courtyard("U3", "F.CrtYd", 4.0, 4.0, 5.0, 5.0);
        let input = DrcInput {
            courtyards: vec![a, b, c],
            ..DrcInput::default()
        };
        // A∩B, B∩C, and A∩C all overlap.
        assert_eq!(check(&input).len(), 3);
    }
}
