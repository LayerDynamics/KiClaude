// Arc/segment indices are small `usize` constants (≤ 64) — the
// `usize → f64` cast cannot lose precision.
#![allow(clippy::cast_precision_loss)]

//! Obstacle inflation — convert scene items on the routing layer into
//! convex obstacle polygons sized by `track_width / 2 + clearance`.
//!
//! The router's A* tests POINT-IN-OBSTACLE on a cell centre; that
//! means each obstacle must be inflated by the full clearance budget
//! (half-track-width on the **routing** side, plus the per-net
//! clearance, plus the obstacle's own half-width / radius on its
//! side). The walk-around router pre-computes these inflated shapes
//! once at grid-build time.

use crate::geom::{Point, Polygon};
use crate::scene::{Scene, SceneItem};

/// Inflated obstacle polygon. We keep the polygon rather than just a
/// bbox so concave-keepout shapes work; for the simple
/// capsule/disc/rect/polygon cases the polygons are convex anyway.
#[derive(Debug, Clone)]
pub struct InflatedObstacle {
    pub polygon: Polygon,
}

/// Inflate every scene item on `layer` that's NOT on `own_net` by
/// `delta_mm`, returning the obstacle list the router uses.
#[must_use]
pub fn collect_inflated_obstacles(
    scene: &Scene,
    layer: &str,
    own_net: &str,
    delta_mm: f64,
) -> Vec<InflatedObstacle> {
    let mut out = Vec::new();
    for (_, item) in scene.iter() {
        if !item_on_layer(item, layer) {
            continue;
        }
        if item_on_net(item, own_net) {
            continue;
        }
        out.push(inflate_item(item, delta_mm));
    }
    out
}

fn item_on_layer(item: &SceneItem, layer: &str) -> bool {
    item.layers().iter().any(|l| layers_match(l, layer))
}

fn layers_match(a: &str, b: &str) -> bool {
    if a == b {
        return true;
    }
    let is_cu = |s: &str| s == "F.Cu" || s == "B.Cu" || s.starts_with("In") && s.ends_with(".Cu");
    if a == "*.Cu" {
        return is_cu(b);
    }
    if b == "*.Cu" {
        return is_cu(a);
    }
    false
}

fn item_on_net(item: &SceneItem, net: &str) -> bool {
    if net.is_empty() {
        return false;
    }
    match item {
        SceneItem::Track { net: n, .. }
        | SceneItem::Via { net: n, .. }
        | SceneItem::Pad { net: n, .. } => n == net,
        SceneItem::Courtyard { .. } => false,
    }
}

fn inflate_item(item: &SceneItem, delta: f64) -> InflatedObstacle {
    match item {
        SceneItem::Track {
            start_mm,
            end_mm,
            width_mm,
            ..
        } => InflatedObstacle {
            polygon: inflate_capsule(*start_mm, *end_mm, *width_mm * 0.5 + delta),
        },
        SceneItem::Via {
            position_mm,
            diameter_mm,
            ..
        } => InflatedObstacle {
            polygon: disc_polygon(*position_mm, *diameter_mm * 0.5 + delta, 24),
        },
        SceneItem::Pad {
            center_mm,
            size_mm,
            rotation_deg,
            ..
        } => InflatedObstacle {
            polygon: inflate_rotated_rect(*center_mm, *size_mm, *rotation_deg, delta),
        },
        SceneItem::Courtyard { polygon, .. } => InflatedObstacle {
            polygon: polygon.clone(),
        },
    }
}

/// Capsule polygon — rectangle along the centreline + semicircular
/// endcaps approximated by `CAP_SEGMENTS` segments each.
fn inflate_capsule(start: Point, end: Point, half_width: f64) -> Polygon {
    const CAP_SEGMENTS: usize = 12;
    let dx = end.x - start.x;
    let dy = end.y - start.y;
    let len = dx.hypot(dy);
    if len < f64::EPSILON {
        return disc_polygon(start, half_width, CAP_SEGMENTS * 2);
    }
    let ux = dx / len;
    let uy = dy / len;
    let nx = -uy;
    let ny = ux;
    let h = half_width;
    let start_angle = ny.atan2(nx);
    let step = std::f64::consts::PI / (CAP_SEGMENTS as f64);
    let mut pts = Vec::with_capacity((CAP_SEGMENTS + 1) * 2);
    // Endcap at `start`, semicircle around back.
    for i in 0..=CAP_SEGMENTS {
        let a = start_angle + step * (i as f64);
        let (s, c) = a.sin_cos();
        pts.push(Point::new(start.x + c * h, start.y + s * h));
    }
    // Endcap at `end`, semicircle around the opposite side.
    let end_angle = start_angle + std::f64::consts::PI;
    for i in 0..=CAP_SEGMENTS {
        let a = end_angle + step * (i as f64);
        let (s, c) = a.sin_cos();
        pts.push(Point::new(end.x + c * h, end.y + s * h));
    }
    Polygon::new(pts)
}

/// Rotated rect inflated by `delta` (Minkowski sum with disc → rounded
/// rect with corner radius `delta`).
#[allow(clippy::many_single_char_names)] // standard rect-geometry naming
fn inflate_rotated_rect(center: Point, size: (f64, f64), rot_deg: f64, delta: f64) -> Polygon {
    const CORNER_SEGS: usize = 6;
    let hx = size.0 * 0.5;
    let hy = size.1 * 0.5;
    let d = delta.max(0.0);
    let (sin_r, cos_r) = rot_deg.to_radians().sin_cos();
    let outer = if d <= f64::EPSILON {
        vec![(-hx, -hy), (hx, -hy), (hx, hy), (-hx, hy)]
    } else {
        let mut p = Vec::with_capacity(CORNER_SEGS * 4 + 4);
        let step = std::f64::consts::FRAC_PI_2 / (CORNER_SEGS as f64);
        let corners = [
            (hx, -hy, -std::f64::consts::FRAC_PI_2),
            (hx, hy, 0.0),
            (-hx, hy, std::f64::consts::FRAC_PI_2),
            (-hx, -hy, std::f64::consts::PI),
        ];
        for (cx, cy, start_angle) in corners {
            for i in 0..=CORNER_SEGS {
                let a = start_angle + step * (i as f64);
                let (s, c) = a.sin_cos();
                p.push((cx + c * d, cy + s * d));
            }
        }
        p
    };
    Polygon::new(
        outer
            .into_iter()
            .map(|(x, y)| {
                Point::new(
                    center.x + x * cos_r - y * sin_r,
                    center.y + x * sin_r + y * cos_r,
                )
            })
            .collect(),
    )
}

fn disc_polygon(center: Point, radius: f64, segments: usize) -> Polygon {
    let mut pts = Vec::with_capacity(segments);
    let step = std::f64::consts::TAU / (segments as f64);
    for i in 0..segments {
        let a = step * (i as f64);
        let (s, c) = a.sin_cos();
        pts.push(Point::new(center.x + c * radius, center.y + s * radius));
    }
    Polygon::new(pts)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::scene::{Scene, SceneItem};

    #[test]
    fn smoke_collect_skips_other_layer() {
        let mut scene = Scene::new();
        scene.insert(SceneItem::Track {
            start_mm: Point::new(0.0, 0.0),
            end_mm: Point::new(10.0, 0.0),
            width_mm: 0.2,
            layer: "B.Cu".into(),
            net: "OTHER".into(),
            uuid: "t".into(),
        });
        let obs = collect_inflated_obstacles(&scene, "F.Cu", "NEW", 0.3);
        assert!(obs.is_empty());
    }

    #[test]
    fn smoke_collect_skips_same_net() {
        let mut scene = Scene::new();
        scene.insert(SceneItem::Track {
            start_mm: Point::new(0.0, 0.0),
            end_mm: Point::new(10.0, 0.0),
            width_mm: 0.2,
            layer: "F.Cu".into(),
            net: "NEW".into(),
            uuid: "t".into(),
        });
        let obs = collect_inflated_obstacles(&scene, "F.Cu", "NEW", 0.3);
        assert!(obs.is_empty());
    }

    #[test]
    fn smoke_track_inflated_to_capsule() {
        let mut scene = Scene::new();
        scene.insert(SceneItem::Track {
            start_mm: Point::new(0.0, 0.0),
            end_mm: Point::new(10.0, 0.0),
            width_mm: 0.2,
            layer: "F.Cu".into(),
            net: "OTHER".into(),
            uuid: "t".into(),
        });
        let obs = collect_inflated_obstacles(&scene, "F.Cu", "NEW", 0.3);
        assert_eq!(obs.len(), 1);
        let bb = obs[0].polygon.bounding_box();
        // Original is a 10-mm-long 0.2-wide line; inflated by 0.4
        // (track half-w 0.1 + delta 0.3) → bbox approx
        // (-0.4, -0.4)..(10.4, 0.4).
        assert!(bb.min.x <= -0.39 && bb.max.x >= 10.39);
        assert!(bb.min.y <= -0.39 && bb.max.y >= 0.39);
    }

    #[test]
    fn smoke_wildcard_cu_layer_matches() {
        let mut scene = Scene::new();
        scene.insert(SceneItem::Via {
            position_mm: Point::new(5.0, 0.0),
            diameter_mm: 0.6,
            drill_mm: 0.3,
            layers: vec!["*.Cu".into()],
            net: "OTHER".into(),
            uuid: "v".into(),
        });
        let obs = collect_inflated_obstacles(&scene, "F.Cu", "NEW", 0.3);
        assert_eq!(obs.len(), 1);
    }
}
