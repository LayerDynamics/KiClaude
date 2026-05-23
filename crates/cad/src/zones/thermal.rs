//! Thermal-relief computation for pads on the same net as a zone.
//!
//! A naive zone-fill puts a copper pour right up against a pad, which
//! is bad for hand-soldering and reflow — the pad acts as a heat sink
//! pulling solder paste through the joint before it can wet. `KiCad`'s
//! "thermal relief" mode carves the pour back from the pad with a
//! small `gap_mm`, then leaves N narrow `spoke_width_mm` bridges of
//! copper connecting the pad to the surrounding pour.
//!
//! This module computes:
//!
//! 1. A **keepout polygon** — the inflated pad shape that the pour
//!    must not occupy. Same shape as a "no thermal relief" inflated
//!    pad; it's the spokes that make the relief work.
//! 2. The **spoke rectangles** — narrow bars of copper, oriented at
//!    either the cardinal directions (`spoke_count = 4`) or at any
//!    multiple of `360° / spoke_count` (`KiCad` supports 2 and 4 in
//!    common practice).
//!
//! The caller is expected to:
//! - Use the keepout polygon as one of the zone's holes (the
//!   `Obstacle::PadKeepout` variant in [`crate::zones::fill`]).
//! - Render the spokes as part of the filled copper alongside the
//!   pour's main polygons.
//!
//! See SPEC FR-023.

// All `as f64` casts in this module are on small `usize` indices into
// segment lists (≤ 100) used for trig angles. The cast cannot lose
// precision at any value we will ever hit.
#![allow(clippy::cast_precision_loss)]

use serde::{Deserialize, Serialize};

use crate::geom::{Point, Polygon};

/// Approximation count for circular arcs in pad shapes. 64 segments
/// per full circle keeps the sagitta error below `0.001 mm` for
/// 1 mm-radius pads — comfortably inside the M2-R-05 `0.01 mm`
/// fidelity gate.
const CIRCLE_SEGMENTS: usize = 64;

/// Pad copper shape. `KiCad` has a couple more (oval, custom) but the
/// four below cover ≥99% of real boards' thermal-relief candidates.
#[derive(Debug, Clone, Copy, PartialEq, Serialize, Deserialize)]
pub enum PadShape {
    /// Circular pad with the given radius in mm.
    Circle { radius_mm: f64 },
    /// Axis-aligned rectangle with `(width, height)` in mm.
    Rect { size_mm: (f64, f64) },
    /// Oval (stadium / pill) — a rectangle with semicircular caps on
    /// the short sides. Caller passes the bounding-box size; the
    /// shorter dimension becomes the cap radius.
    Oval { size_mm: (f64, f64) },
    /// Rounded rectangle. `corner_radius_mm` is clamped to half the
    /// shorter dimension if it's larger.
    RoundRect {
        size_mm: (f64, f64),
        corner_radius_mm: f64,
    },
}

impl PadShape {
    /// Half-extent along the pad's local X (before rotation).
    #[must_use]
    pub fn half_extent_x(&self) -> f64 {
        match *self {
            PadShape::Circle { radius_mm } => radius_mm,
            PadShape::Rect { size_mm }
            | PadShape::Oval { size_mm }
            | PadShape::RoundRect { size_mm, .. } => size_mm.0 / 2.0,
        }
    }

    /// Half-extent along the pad's local Y (before rotation).
    #[must_use]
    pub fn half_extent_y(&self) -> f64 {
        match *self {
            PadShape::Circle { radius_mm } => radius_mm,
            PadShape::Rect { size_mm }
            | PadShape::Oval { size_mm }
            | PadShape::RoundRect { size_mm, .. } => size_mm.1 / 2.0,
        }
    }

    /// Maximum radial extent from the pad's centre — the farthest
    /// distance any point on the pad copper sits from its centroid.
    /// Used by [`build_spokes`] to size thermal-relief spokes long
    /// enough to clear the inflated keepout boundary at any angle.
    #[must_use]
    pub fn max_radial_extent(&self) -> f64 {
        match *self {
            PadShape::Circle { radius_mm } => radius_mm,
            // Rect / RoundRect corners are at the bounding-box
            // diagonal even when the corner is rounded — the
            // RoundRect corner radius shrinks the *interior* of the
            // corner but the outer extent is still the bbox.
            PadShape::Rect { size_mm } | PadShape::RoundRect { size_mm, .. } => {
                let hx = size_mm.0 * 0.5;
                let hy = size_mm.1 * 0.5;
                (hx * hx + hy * hy).sqrt()
            }
            // An oval is a stadium — its extreme is along the long
            // axis at distance `max(w, h) / 2` from the centre.
            PadShape::Oval { size_mm } => size_mm.0.max(size_mm.1) * 0.5,
        }
    }
}

/// User-facing thermal-relief tuning.
#[derive(Debug, Clone, Copy, PartialEq, Serialize, Deserialize)]
pub struct ThermalReliefSpec {
    /// Gap between the pad copper and the pour, in mm. The pour is
    /// kept at least this far from the inflated pad outline.
    pub gap_mm: f64,
    /// Width of each spoke rectangle, in mm. Must be > 0; `KiCad`'s
    /// default is 0.5 mm.
    pub spoke_width_mm: f64,
    /// Number of spokes. `KiCad` supports 2 or 4; 4 is the default.
    pub spoke_count: u8,
    /// Rotation of the spoke pattern relative to the pad's local
    /// frame, degrees. 0 → first spoke points along +X.
    pub spoke_rotation_deg: f64,
}

impl Default for ThermalReliefSpec {
    fn default() -> Self {
        Self {
            gap_mm: 0.5,
            spoke_width_mm: 0.5,
            spoke_count: 4,
            spoke_rotation_deg: 0.0,
        }
    }
}

/// One copper spoke linking a pad to the surrounding pour.
///
/// Geometrically it is a thin rectangle, defined by its centerline
/// `(inner, outer)` and a perpendicular `width_mm`. The two endpoints
/// give the orientation directly so renderers don't have to recompute
/// the spoke axis from the pad center.
#[derive(Debug, Clone, Copy, PartialEq, Serialize, Deserialize)]
pub struct ThermalSpoke {
    /// Spoke endpoint on the pad-side.
    pub inner: Point,
    /// Spoke endpoint on the pour-side.
    pub outer: Point,
    /// Spoke width perpendicular to the (inner → outer) axis, in mm.
    pub width_mm: f64,
}

impl ThermalSpoke {
    /// Render the spoke as a closed rectangle polygon — useful for
    /// rasterizing or for unioning into the filled-copper output.
    ///
    /// Vertices are emitted in CCW order so the polygon's signed area
    /// is positive, consistent with the rest of `crates/cad`.
    #[must_use]
    pub fn to_polygon(&self) -> Polygon {
        let dx = self.outer.x - self.inner.x;
        let dy = self.outer.y - self.inner.y;
        let len = (dx * dx + dy * dy).sqrt();
        if len < f64::EPSILON {
            return Polygon::new(vec![self.inner]);
        }
        // Unit along + unit perpendicular (rotated +90°, i.e. CCW).
        let ux = dx / len;
        let uy = dy / len;
        let nx = -uy;
        let ny = ux;
        let h = self.width_mm / 2.0;
        let p0 = Point::new(self.inner.x - nx * h, self.inner.y - ny * h);
        let p1 = Point::new(self.outer.x - nx * h, self.outer.y - ny * h);
        let p2 = Point::new(self.outer.x + nx * h, self.outer.y + ny * h);
        let p3 = Point::new(self.inner.x + nx * h, self.inner.y + ny * h);
        Polygon::new(vec![p0, p1, p2, p3])
    }
}

/// Output of [`compute_thermal_relief`] — the keepout ring after
/// spoke cuts, plus the spokes themselves for renderer use.
///
/// The keepout is supplied as one or more pieces: cutting a closed
/// ring with N radial spokes splits it into N disconnected pieces
/// (or fewer if some spokes don't fully separate the ring).
///
/// Each piece should be subtracted from the pour as an obstacle.
/// Because the spokes were cut OUT of the keepout, the space they
/// occupied stays as pour copper — exactly the copper bridge
/// connecting the pad to the surrounding pour.
#[derive(Debug, Clone, PartialEq)]
pub struct ThermalRelief {
    /// Inflated pad outline with the spoke rectangles cut out.
    /// Use each piece as a hole in the pour polygon.
    pub keepout_pieces: Vec<Polygon>,
    /// Spoke rectangles that reconnect the pad to the pour. The
    /// spoke geometry is already accounted for in `keepout_pieces`
    /// (they're the cuts) — this list is retained for renderers that
    /// want to highlight spokes in a different colour.
    pub spokes: Vec<ThermalSpoke>,
}

/// Compute the thermal-relief geometry for a single pad.
///
/// `center` is the pad's centroid in board coordinates (mm). `rot_deg`
/// is the rotation of the pad's local frame relative to the board
/// (counter-clockwise positive). The spokes are emitted along the
/// pad's local X axis (`spoke_rotation_deg = 0`) rotated to the board
/// frame using `rot_deg + spec.spoke_rotation_deg`.
///
/// # Panics
///
/// Never panics. Invalid input (e.g. `spoke_count = 0`,
/// `spoke_width_mm = 0`) produces an empty spoke list — the caller can
/// fall back to no-relief subtraction.
#[must_use]
pub fn compute_thermal_relief(
    center: Point,
    pad: PadShape,
    rot_deg: f64,
    spec: ThermalReliefSpec,
) -> ThermalRelief {
    let keepout = inflate_pad(center, pad, rot_deg, spec.gap_mm);
    let spokes = if spec.spoke_count == 0 || spec.spoke_width_mm <= 0.0 {
        Vec::new()
    } else {
        build_spokes(center, pad, rot_deg, spec)
    };
    // Cut the spoke rectangles out of the keepout. The result is one
    // polygon per connected piece of the keepout-ring-after-cuts; for
    // the canonical "4 spokes" case the inflated pad is split into 4
    // corner pieces. We rely on the boolean kernel for the
    // difference — it correctly handles overlapping spokes and the
    // case where a spoke fully bisects the keepout.
    let keepout_pieces = if spokes.is_empty() {
        vec![keepout]
    } else {
        let spoke_polys: Vec<Polygon> = spokes.iter().map(ThermalSpoke::to_polygon).collect();
        crate::zones::boolean::polygon_difference(&keepout, &spoke_polys)
    };
    ThermalRelief {
        keepout_pieces,
        spokes,
    }
}

/// Inflate a pad by `delta_mm` and emit the result as a polygon in
/// the board frame.
///
/// Public so the zone-fill obstacle code can reuse it for the
/// non-thermal-relief case (a same-net pad with `thermal_relief =
/// None` produces a hole shaped like the inflated pad with no spoke
/// carve-out, which is just this output).
#[must_use]
pub fn inflate_pad(center: Point, pad: PadShape, rot_deg: f64, delta_mm: f64) -> Polygon {
    let local = match pad {
        PadShape::Circle { radius_mm } => {
            circle_polygon((0.0, 0.0), radius_mm + delta_mm, CIRCLE_SEGMENTS)
        }
        PadShape::Rect { size_mm } => {
            // Minkowski sum of an axis-aligned rect with a disc of
            // radius `delta_mm` → a (w+2δ) × (h+2δ) rounded rect with
            // corner radius δ.
            let d = delta_mm.max(0.0);
            rounded_rect_polygon((size_mm.0 + 2.0 * d, size_mm.1 + 2.0 * d), d)
        }
        PadShape::Oval { size_mm } => {
            // Oval = rounded rect with corner radius min(w, h)/2.
            // Inflating an oval grows both bounding box and corner
            // radius by δ.
            let r = size_mm.0.min(size_mm.1) / 2.0;
            let d = delta_mm.max(0.0);
            rounded_rect_polygon((size_mm.0 + 2.0 * d, size_mm.1 + 2.0 * d), r + d)
        }
        PadShape::RoundRect {
            size_mm,
            corner_radius_mm,
        } => {
            // Minkowski sum of a rounded rect with a disc grows the
            // bounding box by 2δ and the corner radius by δ.
            let r = corner_radius_mm
                .min(size_mm.0 / 2.0)
                .min(size_mm.1 / 2.0)
                .max(0.0);
            let d = delta_mm.max(0.0);
            rounded_rect_polygon((size_mm.0 + 2.0 * d, size_mm.1 + 2.0 * d), r + d)
        }
    };
    rotate_translate(local, rot_deg, center)
}

/// Public wrapper around the spoke-construction helper so
/// [`super::fill::fill_zone`] can validate candidate spokes against
/// adjacent obstacles before committing them to the keepout cut.
#[must_use]
pub fn build_spokes_public(
    center: Point,
    pad: PadShape,
    rot_deg: f64,
    spec: ThermalReliefSpec,
) -> Vec<ThermalSpoke> {
    build_spokes(center, pad, rot_deg, spec)
}

fn build_spokes(
    center: Point,
    pad: PadShape,
    rot_deg: f64,
    spec: ThermalReliefSpec,
) -> Vec<ThermalSpoke> {
    let count = usize::from(spec.spoke_count);
    let mut spokes = Vec::with_capacity(count);
    // Inner radius: longest pad half-extent, so the spoke starts on
    // the pad itself. We use the larger of half-extent-x and -y so
    // the spoke always emerges from the pad copper rather than from
    let max_radial = pad.max_radial_extent();
    // Outer extent: same in every direction — slightly past the
    // inflated keepout's farthest reach so each spoke positively
    // pokes out into the pour.
    let outer_r = max_radial + spec.gap_mm + spec.spoke_width_mm.max(0.05);
    let base_rad = (rot_deg + spec.spoke_rotation_deg).to_radians();
    let step = std::f64::consts::TAU / (count as f64);
    for i in 0..count {
        let angle = base_rad + step * (i as f64);
        let (s, c) = angle.sin_cos();
        // Per-spoke inner radius — the distance from the pad centre
        // to the pad's outer-copper edge IN THIS SPOKE DIRECTION.
        // For a circle this is the radius; for a rect it's the
        // bounding-box edge at this angle, which can be less than
        // the bounding-box diagonal.
        let inner_r = pad_edge_distance_in_direction(pad, c, s);
        let inner = Point::new(center.x + c * inner_r, center.y + s * inner_r);
        let outer = Point::new(center.x + c * outer_r, center.y + s * outer_r);
        spokes.push(ThermalSpoke {
            inner,
            outer,
            width_mm: spec.spoke_width_mm,
        });
    }
    let _ = max_radial; // referenced by `outer_r` already; silences `unused` if reordered.
    spokes
}

/// Distance from the pad centre to its outer-copper edge along the
/// unit direction `(c, s)`. For a circle this is the radius; for a
/// rect-family pad the edge is the bounding-box rectangle, so the
/// distance is `min(half_extent_x / |c|, half_extent_y / |s|)`. Used
/// by [`build_spokes`] to land each spoke on the pad's actual edge in
/// its own direction.
fn pad_edge_distance_in_direction(pad: PadShape, c: f64, s: f64) -> f64 {
    match pad {
        PadShape::Circle { radius_mm } => radius_mm,
        PadShape::Rect { size_mm }
        | PadShape::Oval { size_mm }
        | PadShape::RoundRect { size_mm, .. } => {
            let hx = size_mm.0 * 0.5;
            let hy = size_mm.1 * 0.5;
            let ac = c.abs().max(1e-9);
            let as_ = s.abs().max(1e-9);
            (hx / ac).min(hy / as_)
        }
    }
}

/// Polygon approximation of a circle at the local origin.
fn circle_polygon(center: (f64, f64), radius: f64, segments: usize) -> Polygon {
    let mut pts = Vec::with_capacity(segments);
    let step = std::f64::consts::TAU / (segments as f64);
    for i in 0..segments {
        let a = step * (i as f64);
        let (s, c) = a.sin_cos();
        pts.push(Point::new(center.0 + c * radius, center.1 + s * radius));
    }
    Polygon::new(pts)
}

/// Polygon approximation of a rounded rectangle centered at the local
/// origin. `size = (width, height)`. Each corner uses
/// `CIRCLE_SEGMENTS / 4` arc segments.
fn rounded_rect_polygon(size: (f64, f64), radius: f64) -> Polygon {
    let hw = size.0 / 2.0;
    let hh = size.1 / 2.0;
    let r = radius.max(0.0).min(hw).min(hh);
    if r <= f64::EPSILON {
        return Polygon::new(vec![
            Point::new(-hw, -hh),
            Point::new(hw, -hh),
            Point::new(hw, hh),
            Point::new(-hw, hh),
        ]);
    }
    let corner_segs = (CIRCLE_SEGMENTS / 4).max(1);
    let step = std::f64::consts::FRAC_PI_2 / (corner_segs as f64);
    let mut pts = Vec::with_capacity(corner_segs * 4 + 4);
    // CCW from bottom-right corner's arc center.
    let corners = [
        (hw - r, -hh + r, -std::f64::consts::FRAC_PI_2),
        (hw - r, hh - r, 0.0),
        (-hw + r, hh - r, std::f64::consts::FRAC_PI_2),
        (-hw + r, -hh + r, std::f64::consts::PI),
    ];
    for (cx, cy, start_angle) in corners {
        for i in 0..=corner_segs {
            let a = start_angle + step * (i as f64);
            let (s, c) = a.sin_cos();
            pts.push(Point::new(cx + c * r, cy + s * r));
        }
    }
    Polygon::new(pts)
}

/// Rotate a polygon by `rot_deg` (CCW positive, around the local
/// origin) then translate to `to`. Used to lift pad-local geometry
/// into the board frame.
fn rotate_translate(p: Polygon, rot_deg: f64, to: Point) -> Polygon {
    let rad = rot_deg.to_radians();
    let (s, c) = rad.sin_cos();
    let mapped = p
        .points
        .into_iter()
        .map(|pt| {
            let rx = pt.x * c - pt.y * s;
            let ry = pt.x * s + pt.y * c;
            Point::new(rx + to.x, ry + to.y)
        })
        .collect();
    Polygon::new(mapped)
}

#[cfg(test)]
mod tests {
    use super::*;
    use pretty_assertions::assert_eq;

    fn signed_area(ring: &[Point]) -> f64 {
        let n = ring.len();
        if n < 3 {
            return 0.0;
        }
        let mut sum = 0.0;
        for i in 0..n {
            let a = ring[i];
            let b = ring[(i + 1) % n];
            sum += a.x * b.y - b.x * a.y;
        }
        sum * 0.5
    }

    #[test]
    fn smoke_circle_inflate_grows_radius() {
        let p = inflate_pad(
            Point::new(0.0, 0.0),
            PadShape::Circle { radius_mm: 1.0 },
            0.0,
            0.5,
        );
        // The polygon approximates a circle of radius 1.5. Verify by
        // checking each vertex is at distance ~1.5 from the origin.
        for pt in &p.points {
            let d = (pt.x * pt.x + pt.y * pt.y).sqrt();
            assert!((d - 1.5).abs() < 1e-9, "vertex distance: {d}");
        }
    }

    #[test]
    fn smoke_rect_inflate_grows_extent() {
        // A 2x4 rect inflated by 0.5 → bounding box 3x5 with rounded
        // corners. Verify the AABB.
        let p = inflate_pad(
            Point::new(10.0, 20.0),
            PadShape::Rect {
                size_mm: (2.0, 4.0),
            },
            0.0,
            0.5,
        );
        let bb = p.bounding_box();
        assert!((bb.min.x - 8.5).abs() < 1e-9);
        assert!((bb.max.x - 11.5).abs() < 1e-9);
        assert!((bb.min.y - 17.5).abs() < 1e-9);
        assert!((bb.max.y - 22.5).abs() < 1e-9);
    }

    #[test]
    fn smoke_inflate_polygon_winding_is_ccw() {
        let p = inflate_pad(
            Point::new(0.0, 0.0),
            PadShape::Rect {
                size_mm: (2.0, 2.0),
            },
            0.0,
            0.1,
        );
        // CCW = positive signed area in math convention.
        assert!(signed_area(&p.points) > 0.0);
    }

    #[test]
    fn smoke_four_spokes_at_cardinal_directions() {
        let r = compute_thermal_relief(
            Point::new(0.0, 0.0),
            PadShape::Circle { radius_mm: 1.0 },
            0.0,
            ThermalReliefSpec {
                gap_mm: 0.5,
                spoke_width_mm: 0.4,
                spoke_count: 4,
                spoke_rotation_deg: 0.0,
            },
        );
        assert_eq!(r.spokes.len(), 4);
        // Inner end now sits on the pad's outer copper edge (= radius
        // for a circle). Outer end is inner + gap + spoke_width:
        //   inner = 1.0, outer = 1.0 + 0.5 + 0.4 = 1.9.
        let expected_inner = 1.0;
        let expected_outer = 1.0 + 0.5 + 0.4;
        // First spoke: rot_deg=0 + spoke_rotation_deg=0 → along +X.
        assert!((r.spokes[0].inner.x - expected_inner).abs() < 1e-9);
        assert!(r.spokes[0].inner.y.abs() < 1e-9);
        assert!((r.spokes[0].outer.x - expected_outer).abs() < 1e-9);
        assert!(r.spokes[0].outer.y.abs() < 1e-9);
        // Second spoke: at +90° → along +Y.
        assert!(r.spokes[1].outer.x.abs() < 1e-9);
        assert!((r.spokes[1].outer.y - expected_outer).abs() < 1e-9);
    }

    #[test]
    fn smoke_two_spokes_opposite() {
        let r = compute_thermal_relief(
            Point::new(0.0, 0.0),
            PadShape::Rect {
                size_mm: (2.0, 2.0),
            },
            0.0,
            ThermalReliefSpec {
                gap_mm: 0.3,
                spoke_width_mm: 0.4,
                spoke_count: 2,
                spoke_rotation_deg: 0.0,
            },
        );
        assert_eq!(r.spokes.len(), 2);
        // 180° apart.
        let dot =
            r.spokes[0].outer.x * r.spokes[1].outer.x + r.spokes[0].outer.y * r.spokes[1].outer.y;
        // dot product should be -|outer|^2 (180° apart).
        let mag = (r.spokes[0].outer.x.powi(2) + r.spokes[0].outer.y.powi(2)).sqrt();
        let expected_dot = -(mag * mag);
        assert!(
            (dot - expected_dot).abs() < 1e-9,
            "dot {dot} vs expected {expected_dot}",
        );
    }

    #[test]
    fn smoke_zero_spokes_emits_empty_list() {
        let r = compute_thermal_relief(
            Point::new(0.0, 0.0),
            PadShape::Circle { radius_mm: 1.0 },
            0.0,
            ThermalReliefSpec {
                gap_mm: 0.5,
                spoke_width_mm: 0.4,
                spoke_count: 0,
                spoke_rotation_deg: 0.0,
            },
        );
        assert!(r.spokes.is_empty());
    }

    #[test]
    fn smoke_spoke_to_polygon_has_correct_width() {
        let s = ThermalSpoke {
            inner: Point::new(0.0, 0.0),
            outer: Point::new(2.0, 0.0),
            width_mm: 0.4,
        };
        let p = s.to_polygon();
        assert_eq!(p.points.len(), 4);
        let bb = p.bounding_box();
        assert!((bb.max.y - 0.2).abs() < 1e-9);
        assert!((bb.min.y + 0.2).abs() < 1e-9);
        assert!((bb.max.x - 2.0).abs() < 1e-9);
        assert!(bb.min.x.abs() < 1e-9);
    }

    #[test]
    fn smoke_rotated_spokes_track_pad_rotation() {
        // Pad rotated 90°, spoke pattern unchanged → first spoke
        // should point along +Y (was +X before rotation).
        let r = compute_thermal_relief(
            Point::new(0.0, 0.0),
            PadShape::Rect {
                size_mm: (2.0, 1.0),
            },
            90.0,
            ThermalReliefSpec::default(),
        );
        assert!(
            r.spokes[0].outer.x.abs() < 1e-9,
            "first spoke should now point along +Y, got x={}",
            r.spokes[0].outer.x,
        );
        assert!(r.spokes[0].outer.y > 0.0);
    }

    #[test]
    fn smoke_oval_inflate_grows_uniformly() {
        // An oval (size 4x2) inflated by 0.3 → bounding box 4.6x2.6
        // because the long axis grows by 2*delta and the short axis
        // grows by 2*delta as well.
        let p = inflate_pad(
            Point::new(0.0, 0.0),
            PadShape::Oval {
                size_mm: (4.0, 2.0),
            },
            0.0,
            0.3,
        );
        let bb = p.bounding_box();
        assert!(
            (bb.width() - 4.6).abs() < 1e-9,
            "oval width post-inflate: {} vs 4.6",
            bb.width(),
        );
        assert!(
            (bb.height() - 2.6).abs() < 1e-9,
            "oval height post-inflate: {} vs 2.6",
            bb.height(),
        );
    }

    #[test]
    fn smoke_round_rect_inflate_clamps_corner_radius() {
        // RoundRect 4x4 with corner_radius=3 → clamped to 2.
        // Inflated by 0.5, effective corner radius = 2.5.
        let p = inflate_pad(
            Point::new(0.0, 0.0),
            PadShape::RoundRect {
                size_mm: (4.0, 4.0),
                corner_radius_mm: 3.0,
            },
            0.0,
            0.5,
        );
        let bb = p.bounding_box();
        // Width: 4 + 2*0.5 = 5.
        assert!((bb.width() - 5.0).abs() < 1e-9);
        assert!((bb.height() - 5.0).abs() < 1e-9);
    }
}
