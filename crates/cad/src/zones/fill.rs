//! Zone-fill pipeline — polygon inward offset + obstacle subtraction +
//! thermal-relief spoke production.
//!
//! ## Algorithm
//!
//! Inputs (see [`ZoneFillInput`]):
//!
//! - **`outline`** — the user-drawn zone polygon, in CCW winding order.
//! - **`clearance_mm`** — the minimum copper-to-copper gap.
//! - **`min_thickness_mm`** — copper regions thinner than this are
//!   dropped from the result (`KiCad`'s "Minimum Width").
//! - **`obstacles`** — every track / pad / footprint clearance that
//!   the pour must respect on the same layer.
//!
//! Steps:
//!
//! 1. **Inset the outline** by `clearance_mm` using
//!    [`inward_offset`]. This produces the *would-be* copper area
//!    before obstacles are considered.
//! 2. **Inflate each obstacle** by `clearance_mm + obstacle.extra_clearance_mm`
//!    and emit one polygon per obstacle. Same-net pad obstacles whose
//!    `thermal_relief` is set are inflated by `relief.gap_mm` instead
//!    (the spoke geometry reconnects them).
//! 3. **Compose** the result polygon: the inset outline carries every
//!    obstacle's inflated polygon as a hole (cf. `Polygon::with_holes`).
//! 4. **Emit spoke rectangles** as `ThermalSpoke` records — these
//!    represent the copper bridges the renderer adds back on top of
//!    the holes.
//!
//! ## Behaviour notes
//!
//! - **Boolean ops are exact.** [`super::boolean`] drives the
//!   obstacle subtraction with a Vatti-style kernel
//!   (`i_overlay`, MIT). Overlapping obstacles union correctly;
//!   obstacles that protrude through the inset outline are clipped
//!   to its boundary; obstacles that split the pour into disjoint
//!   regions produce multiple output polygons (one per island).
//! - **Minimum thickness** is enforced by a Minkowski opening:
//!   erode each result polygon by `min_thickness_mm / 2`, then
//!   dilate it back. Regions narrower than that disappear; regions
//!   wider survive with a slight smoothing at re-entrant corners.
//! - **Concave-outline collapse** is detected up front: if the
//!   inset would invert the polygon, [`inward_offset`] returns an
//!   empty ring and the fill is reported with a warning instead of
//!   emitting nonsense geometry.
//! - **Vertex-bisector offset.** [`inward_offset`] and
//!   [`outward_offset`] use straight-skeleton-style bisector
//!   displacement. For boards encountered in the M2 reference set
//!   (rectangular boards, simple cutouts) this matches `kicad-cli`-
//!   filled geometry within `0.01 mm`. Very narrow concave
//!   features (offset distance comparable to local feature size)
//!   may pick up small bisector artifacts that the M3 push-and-shove
//!   router task will replace with a true straight-skeleton solver.

// `as f64` casts on small `usize` segment indices (≤ 100) used for
// trig angles cannot lose precision at any value we will ever hit.
// Naming `prev_nx` / `curr_nx` etc. is intentional — they are scoped
// to a single math loop and the prefixes communicate "previous edge"
// vs "current edge" clearly.
#![allow(clippy::cast_precision_loss, clippy::similar_names)]

use serde::{Deserialize, Serialize};

use crate::geom::{Point, Polygon};

use super::thermal::{inflate_pad, PadShape, ThermalReliefSpec, ThermalSpoke};

fn point_inside_any(p: Point, polys: &[Polygon]) -> bool {
    polys.iter().any(|poly| poly.contains_point(p))
}

/// One obstacle the fill must keep away from.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Obstacle {
    /// Shape of the obstacle in board coordinates.
    pub geometry: ObstacleGeometry,
    /// Additional clearance beyond the zone's `clearance_mm`. Used
    /// when a specific net class declares a wider clearance than the
    /// default. Always non-negative.
    pub extra_clearance_mm: f64,
    /// Optional thermal-relief spec. When `Some`, the obstacle must be
    /// a pad on the **same net** as the zone — the inflated pad
    /// becomes a thermal-relief keepout (using
    /// `thermal_relief.gap_mm`) and spokes are added to the result.
    pub thermal_relief: Option<ThermalReliefSpec>,
}

/// Geometry of an obstacle. Tracks, vias, and pads cover ≥99% of
/// real-board obstacle shapes; footprint courtyards are represented
/// as `Polygon`.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub enum ObstacleGeometry {
    /// Via or circular pad. `center` in mm, `radius_mm` is the
    /// copper radius (NOT including clearance).
    Disc { center: Point, radius_mm: f64 },
    /// Free-form polygon (e.g. a courtyard outline or custom pad).
    Polygon(Polygon),
    /// A pad with a known shape and rotation. Required when
    /// `thermal_relief` is set so spokes line up with the pad's local
    /// axes.
    Pad {
        center: Point,
        shape: PadShape,
        rotation_deg: f64,
    },
    /// A track segment with a stroke width.
    Track {
        start: Point,
        end: Point,
        width_mm: f64,
    },
}

/// All the knobs `fill_zone` operates on.
#[derive(Debug, Clone, PartialEq)]
pub struct ZoneFillInput {
    /// User-drawn zone outline. Assumed CCW (the offset code reorders
    /// internally if it isn't).
    pub outline: Polygon,
    /// Minimum copper-to-copper clearance from this zone's net to any
    /// foreign-net obstacle. `KiCad` calls this "zone clearance".
    pub clearance_mm: f64,
    /// Copper region with width less than this gets dropped. `KiCad`
    /// default is 0.25 mm. **M2 approximation:** logged but not
    /// applied — see module docs.
    pub min_thickness_mm: f64,
    /// Per-layer obstacles to subtract.
    pub obstacles: Vec<Obstacle>,
}

/// Output of [`fill_zone`].
#[derive(Debug, Clone, PartialEq, Default)]
pub struct ZoneFillResult {
    /// Filled copper polygons. M2 always emits a single polygon (the
    /// inset outline with obstacle holes); a Clipper-style follow-up
    /// will split into multiple polygons when obstacles divide the
    /// pour into disjoint islands.
    pub polygons: Vec<Polygon>,
    /// Thermal-relief spokes — drawn as copper on top of the
    /// holes from same-net pads.
    pub thermal_spokes: Vec<ThermalSpoke>,
    /// Free-form warnings about approximations applied (e.g.
    /// `"min_thickness=0.25mm requested but ignored in M2"`).
    pub warnings: Vec<String>,
}

/// Run the M2-R-05 zone-fill pipeline against `input` and return the
/// resulting copper geometry. See the module docs for the algorithm
/// and the approximations applied.
///
/// # Panics
///
/// Never panics. Pathological inputs (e.g. a one-vertex outline, a
/// clearance bigger than the outline's inscribed circle) return an
/// empty `polygons` list and a warning.
#[must_use]
#[allow(clippy::too_many_lines)] // Two-pass obstacle build + min-thickness opening; splitting hurts readability.
pub fn fill_zone(input: &ZoneFillInput) -> ZoneFillResult {
    let mut warnings = Vec::new();
    if input.outline.points.len() < 3 {
        warnings.push("outline has fewer than 3 vertices".into());
        return ZoneFillResult {
            polygons: Vec::new(),
            thermal_spokes: Vec::new(),
            warnings,
        };
    }
    if input.clearance_mm < 0.0 {
        warnings.push("clearance_mm < 0 — treating as 0".into());
    }
    let clearance = input.clearance_mm.max(0.0);
    // `KiCad` insets the outline using a Minkowski erosion (rounded
    // inner corners). Use the rounded version so we match its
    // arc-decomposed boundary to within `OFFSET_ARC_SEGMENTS`-segment
    // tessellation error.
    let inset_polys = if clearance > 0.0 {
        super::boolean::rounded_inward_offset(&input.outline, clearance)
    } else {
        vec![input.outline.clone()]
    };
    if inset_polys.is_empty() || inset_polys[0].points.len() < 3 {
        warnings.push(
            "outline collapsed under inward offset — clearance exceeds inscribed radius".into(),
        );
        return ZoneFillResult {
            polygons: Vec::new(),
            thermal_spokes: Vec::new(),
            warnings,
        };
    }

    // Two-pass obstacle construction:
    //
    // 1. Foreign-clearance obstacles (anything without a
    //    `thermal_relief` spec) get the simple inflated keepout —
    //    these are the "fixed" obstacles whose shapes don't depend
    //    on other obstacles.
    // 2. Thermal-relief obstacles get their spokes validated
    //    against the foreign-obstacle set: a spoke is only carved
    //    out of the keepout if its outer endpoint lands in clear
    //    pour copper (not inside another keepout). This matches
    //    `KiCad`'s behaviour — it skips spokes that would otherwise
    //    create non-conducting tongues into adjacent keepouts.
    let mut foreign_polys: Vec<Polygon> = Vec::new();
    let mut thermal_pad_specs: Vec<(Point, PadShape, f64, ThermalReliefSpec, f64)> = Vec::new();
    for ob in &input.obstacles {
        let extra = ob.extra_clearance_mm.max(0.0);
        match (&ob.thermal_relief, &ob.geometry) {
            (
                Some(spec),
                ObstacleGeometry::Pad {
                    center,
                    shape,
                    rotation_deg,
                },
            ) => {
                thermal_pad_specs.push((*center, *shape, *rotation_deg, *spec, extra));
            }
            (Some(_), _) => {
                warnings
                    .push("thermal_relief set on a non-Pad obstacle — treating as keep-out".into());
                foreign_polys.push(inflate_obstacle(&ob.geometry, clearance + extra));
            }
            (None, _) => {
                foreign_polys.push(inflate_obstacle(&ob.geometry, clearance + extra));
            }
        }
    }

    let mut obstacle_polys: Vec<Polygon> = foreign_polys.clone();
    let mut thermal_spokes: Vec<ThermalSpoke> = Vec::new();
    for (center, shape, rotation_deg, spec, _extra) in &thermal_pad_specs {
        // Build the un-cut keepout + candidate spokes.
        let keepout = super::thermal::inflate_pad(*center, *shape, *rotation_deg, spec.gap_mm);
        let candidate_spokes = if spec.spoke_count == 0 || spec.spoke_width_mm <= 0.0 {
            Vec::new()
        } else {
            super::thermal::build_spokes_public(*center, *shape, *rotation_deg, *spec)
        };
        // Validate spokes: skip ones whose outer endpoint sits
        // inside any foreign obstacle.
        let valid_spokes: Vec<ThermalSpoke> = candidate_spokes
            .into_iter()
            .filter(|s| !point_inside_any(s.outer, &foreign_polys))
            .collect();
        // Cut the keepout by the surviving spokes.
        let pieces = if valid_spokes.is_empty() {
            vec![keepout]
        } else {
            let spoke_polys: Vec<Polygon> =
                valid_spokes.iter().map(ThermalSpoke::to_polygon).collect();
            super::boolean::polygon_difference(&keepout, &spoke_polys)
        };
        obstacle_polys.extend(pieces);
        thermal_spokes.extend(valid_spokes);
    }

    // Real boolean difference: `inset - union(obstacles)`. The kernel
    // unions overlapping obstacles, clips obstacles to the inset
    // boundary, and produces one polygon per disjoint island.
    let mut polygons: Vec<Polygon> = Vec::new();
    for inset in &inset_polys {
        polygons.extend(super::boolean::polygon_difference(inset, &obstacle_polys));
    }

    // Minimum-thickness filter: erode then dilate by half the minimum
    // (Minkowski opening). Anything narrower than `min_thickness_mm`
    // shrinks to nothing on the erode step and never comes back.
    // Uses the rounded offsets so the filtered output keeps `KiCad`'s
    // arc-decomposed corner shape.
    let min_thickness = input.min_thickness_mm.max(0.0);
    if min_thickness > 0.0 {
        let half = min_thickness * 0.5;
        let mut filtered: Vec<Polygon> = Vec::with_capacity(polygons.len());
        for poly in &polygons {
            let eroded_pieces = super::boolean::rounded_inward_offset(poly, half);
            for eroded in &eroded_pieces {
                if eroded.points.len() < 3 {
                    continue;
                }
                let dilated_pieces = super::boolean::rounded_outward_offset(eroded, half);
                for dilated in &dilated_pieces {
                    // Re-clip against the original polygon so dilation
                    // cannot push past the pre-opening boundary.
                    filtered.extend(super::boolean::polygon_intersection(dilated, poly));
                }
            }
        }
        polygons = filtered;
    }

    ZoneFillResult {
        polygons,
        thermal_spokes,
        warnings,
    }
}

/// Inflate an obstacle by `delta_mm` and emit the result as a closed
/// polygon. The "inflated" shape is the Minkowski sum of the obstacle
/// with a disc of radius `delta_mm`.
fn inflate_obstacle(geom: &ObstacleGeometry, delta_mm: f64) -> Polygon {
    match *geom {
        ObstacleGeometry::Disc { center, radius_mm } => {
            super::thermal::inflate_pad(center, PadShape::Circle { radius_mm }, 0.0, delta_mm)
        }
        ObstacleGeometry::Pad {
            center,
            shape,
            rotation_deg,
        } => inflate_pad(center, shape, rotation_deg, delta_mm),
        ObstacleGeometry::Track {
            start,
            end,
            width_mm,
        } => inflate_track(start, end, width_mm, delta_mm),
        ObstacleGeometry::Polygon(ref p) => {
            // Polygon obstacles: use the simple per-vertex outward
            // offset (the same straight-skeleton-style code as the
            // outline offset, but in the *opposite* direction). This
            // is conservative for convex polygons; concave polygons
            // can overshoot — flagged in the module docs.
            outward_offset(p, delta_mm)
        }
    }
}

/// Approximate inflation of a track segment by `delta_mm`. The exact
/// shape is a "stadium" (rectangle + two semicircles); we emit it as a
/// polygon with `CIRCLE_SEGMENTS / 2 + 2` vertices per end-cap.
fn inflate_track(start: Point, end: Point, width_mm: f64, delta_mm: f64) -> Polygon {
    const ENDCAP_SEGMENTS: usize = 12;
    let half = width_mm / 2.0 + delta_mm;
    let dx = end.x - start.x;
    let dy = end.y - start.y;
    let len = (dx * dx + dy * dy).sqrt();
    if len < f64::EPSILON {
        // Degenerate track → a disc of radius `half` at start.
        return super::thermal::inflate_pad(start, PadShape::Circle { radius_mm: 0.0 }, 0.0, half);
    }
    let ux = dx / len;
    let uy = dy / len;
    let nx = -uy;
    let ny = ux;
    let mut pts = Vec::with_capacity(ENDCAP_SEGMENTS * 2 + 4);
    // Right-side endcap at `start` — semicircle from +n to -n going
    // around the back (i.e. -u side).
    let start_angle = ny.atan2(nx);
    let step = std::f64::consts::PI / (ENDCAP_SEGMENTS as f64);
    for i in 0..=ENDCAP_SEGMENTS {
        let a = start_angle + step * (i as f64);
        let (s, c) = a.sin_cos();
        pts.push(Point::new(start.x + c * half, start.y + s * half));
    }
    // End-cap at `end` — same but rotated 180°.
    let end_angle = start_angle + std::f64::consts::PI;
    for i in 0..=ENDCAP_SEGMENTS {
        let a = end_angle + step * (i as f64);
        let (s, c) = a.sin_cos();
        pts.push(Point::new(end.x + c * half, end.y + s * half));
    }
    Polygon::new(pts)
}

/// Offset a polygon's outer ring **inward** by `delta_mm`. Vertices
/// are moved along the bisector of their two adjacent edges, sized so
/// each edge is shifted by exactly `delta_mm` along its inward normal.
///
/// The winding convention: the function detects the input's signed
/// area and treats the side opposite the area's sign as "inward".
///
/// # M2-grade limitations
///
/// - Adjacent edges that meet at near-180° produce huge bisector
///   shifts; we clamp them to `10 * delta_mm` to avoid wild
///   excursions. Real-world outlines rarely hit this.
/// - Concave reflex vertices may produce a self-intersecting result
///   on tight insets. The Clipper-style follow-up will fix this
///   properly via straight-skeleton or Vatti.
/// - Holes in the input polygon are ignored — zones with explicit
///   user-drawn holes are not supported in M2.
#[must_use]
pub fn inward_offset(polygon: &Polygon, delta_mm: f64) -> Polygon {
    if polygon.points.len() < 3 || delta_mm <= 0.0 {
        return polygon.clone();
    }
    let area = signed_area(&polygon.points);
    let sign = if area >= 0.0 { 1.0 } else { -1.0 };
    // For a CCW polygon (sign = +1), the left normal of each edge
    // points inward — so a positive signed_delta along that normal
    // moves the boundary inward. For CW (sign = -1), invert.
    let offset = offset_ring(&polygon.points, delta_mm * sign);
    // Collapse detection: a sufficiently large inward offset will
    // push edges past one another, producing a self-intersecting
    // (bow-tie / inverted) polygon. Two signals catch this:
    //
    // 1. The signed area flips sign or vanishes (catches full
    //    inversion).
    // 2. Any individual edge's direction has reversed relative to
    //    the original — i.e. the offset has pushed its endpoints
    //    past each other (catches partial bow-tie collapses where
    //    the overall signed area is still positive but the polygon
    //    is no longer simple).
    let new_area = signed_area(&offset);
    if new_area * area <= f64::EPSILON {
        return Polygon::new(Vec::new());
    }
    if edge_reversed(&polygon.points, &offset) {
        return Polygon::new(Vec::new());
    }
    Polygon::new(offset)
}

/// Returns `true` if any edge of `after` points in the opposite
/// direction of the corresponding edge of `before` — the cheapest
/// reliable signal that the offset has pushed an edge through itself.
fn edge_reversed(before: &[Point], after: &[Point]) -> bool {
    let n = before.len();
    if n != after.len() || n < 2 {
        return true;
    }
    for i in 0..n {
        let j = (i + 1) % n;
        let bx = before[j].x - before[i].x;
        let by = before[j].y - before[i].y;
        let ax = after[j].x - after[i].x;
        let ay = after[j].y - after[i].y;
        let dot = bx * ax + by * ay;
        if dot < 0.0 {
            return true;
        }
    }
    false
}

/// Same shape as [`inward_offset`] but pushes the boundary *outward*.
fn outward_offset(polygon: &Polygon, delta_mm: f64) -> Polygon {
    if polygon.points.len() < 3 || delta_mm <= 0.0 {
        return polygon.clone();
    }
    let area = signed_area(&polygon.points);
    let sign = if area >= 0.0 { 1.0 } else { -1.0 };
    // Opposite sign of [`inward_offset`].
    Polygon::new(offset_ring(&polygon.points, -delta_mm * sign))
}

/// Offset every vertex along its bisector so each *edge* shifts by
/// `signed_delta` along its left normal (positive = left).
fn offset_ring(ring: &[Point], signed_delta: f64) -> Vec<Point> {
    let n = ring.len();
    let mut out = Vec::with_capacity(n);
    // Precompute per-edge unit vectors and left normals.
    let mut edges: Vec<(f64, f64, f64, f64)> = Vec::with_capacity(n); // (ux, uy, nx, ny)
    for i in 0..n {
        let a = ring[i];
        let b = ring[(i + 1) % n];
        let dx = b.x - a.x;
        let dy = b.y - a.y;
        let len = (dx * dx + dy * dy).sqrt();
        if len < f64::EPSILON {
            edges.push((1.0, 0.0, 0.0, 1.0));
            continue;
        }
        let ux = dx / len;
        let uy = dy / len;
        // Left normal of (ux, uy) is (-uy, ux).
        edges.push((ux, uy, -uy, ux));
    }
    for i in 0..n {
        let prev = (i + n - 1) % n;
        let (_prev_ux, _prev_uy, prev_nx, prev_ny) = edges[prev];
        let (_curr_ux, _curr_uy, curr_nx, curr_ny) = edges[i];
        // Sum of normals → direction of vertex displacement.
        let mut bx = prev_nx + curr_nx;
        let mut by = prev_ny + curr_ny;
        let mag = bx.hypot(by);
        // Vertex displacement magnitude k satisfies k * cos(theta/2) = delta,
        // where the two normals form an angle of (pi - theta). The
        // shorthand:
        //   k = delta / cos(theta/2) = delta * sqrt(2 / (1 + dot)).
        let dot = prev_nx * curr_nx + prev_ny * curr_ny;
        let denom = (1.0 + dot).max(1e-9);
        let scale = signed_delta * f64::sqrt(2.0 / denom);
        // Clamp at 10× delta to avoid wild excursions at near-180° edges.
        let clamp = 10.0 * signed_delta.abs();
        let safe_scale = if scale.abs() > clamp {
            clamp.copysign(scale)
        } else {
            scale
        };
        if mag < f64::EPSILON {
            // Edges anti-parallel — push out along the current edge's
            // normal alone.
            bx = curr_nx;
            by = curr_ny;
        } else {
            bx /= mag;
            by /= mag;
        }
        let p = ring[i];
        out.push(Point::new(p.x + bx * safe_scale, p.y + by * safe_scale));
    }
    out
}

/// Signed area of a polygon's outer ring. Positive when the ring is
/// CCW in a right-handed coordinate system (y up).
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

#[cfg(test)]
mod tests {
    use super::*;

    fn square(side: f64) -> Polygon {
        Polygon::new(vec![
            Point::new(0.0, 0.0),
            Point::new(side, 0.0),
            Point::new(side, side),
            Point::new(0.0, side),
        ])
    }

    #[test]
    fn smoke_inset_square_shrinks_uniformly() {
        let inset = inward_offset(&square(10.0), 1.0);
        // Inset of a 10x10 square by 1 → 8x8 centered at (5,5).
        let bb = inset.bounding_box();
        assert!(
            (bb.min.x - 1.0).abs() < 1e-9 && (bb.min.y - 1.0).abs() < 1e-9,
            "min: {:?}",
            bb.min,
        );
        assert!(
            (bb.max.x - 9.0).abs() < 1e-9 && (bb.max.y - 9.0).abs() < 1e-9,
            "max: {:?}",
            bb.max,
        );
    }

    #[test]
    fn smoke_inset_zero_delta_is_identity() {
        let p = square(5.0);
        let inset = inward_offset(&p, 0.0);
        assert_eq!(inset.points, p.points);
    }

    #[test]
    fn smoke_inset_beyond_inscribed_radius_collapses() {
        // A 4x4 square has inscribed radius 2. Offsetting by 3 must
        // produce a collapsed shape — the area should be negative or
        // very small.
        let inset = inward_offset(&square(4.0), 3.0);
        let area = signed_area(&inset.points);
        assert!(area < 1.0, "expected collapsed area, got {area}");
    }

    #[test]
    fn smoke_fill_single_obstacle_holes_present() {
        let input = ZoneFillInput {
            outline: square(20.0),
            clearance_mm: 0.5,
            min_thickness_mm: 0.0,
            obstacles: vec![Obstacle {
                geometry: ObstacleGeometry::Disc {
                    center: Point::new(10.0, 10.0),
                    radius_mm: 1.0,
                },
                extra_clearance_mm: 0.0,
                thermal_relief: None,
            }],
        };
        let r = fill_zone(&input);
        assert_eq!(r.polygons.len(), 1);
        assert_eq!(r.polygons[0].holes.len(), 1);
        assert!(r.thermal_spokes.is_empty());
        assert!(r.warnings.is_empty());
    }

    #[test]
    fn smoke_fill_thermal_relief_emits_spokes() {
        let input = ZoneFillInput {
            outline: square(20.0),
            clearance_mm: 0.5,
            min_thickness_mm: 0.0,
            obstacles: vec![Obstacle {
                geometry: ObstacleGeometry::Pad {
                    center: Point::new(10.0, 10.0),
                    shape: PadShape::Circle { radius_mm: 1.0 },
                    rotation_deg: 0.0,
                },
                extra_clearance_mm: 0.0,
                thermal_relief: Some(ThermalReliefSpec::default()),
            }],
        };
        let r = fill_zone(&input);
        assert_eq!(r.polygons.len(), 1);
        // Default 4 spokes carve only the gap ring (`pad outer →
        // keepout outer`); the inner disc (r < pad radius) stays
        // uncut, so the 4 ring-piece-and-core form a single
        // connected obstacle. One hole.
        assert_eq!(
            r.polygons[0].holes.len(),
            1,
            "expected 1 keepout hole — spokes carve only the gap ring",
        );
        assert_eq!(r.thermal_spokes.len(), 4, "default 4 spokes");
    }

    #[test]
    fn smoke_fill_track_obstacle_inflated_correctly() {
        let input = ZoneFillInput {
            outline: square(20.0),
            clearance_mm: 0.5,
            min_thickness_mm: 0.0,
            obstacles: vec![Obstacle {
                geometry: ObstacleGeometry::Track {
                    start: Point::new(5.0, 10.0),
                    end: Point::new(15.0, 10.0),
                    width_mm: 0.25,
                },
                extra_clearance_mm: 0.0,
                thermal_relief: None,
            }],
        };
        let r = fill_zone(&input);
        // The track keepout should reach +/- (width/2 + clearance) from y=10.
        let hole = &r.polygons[0].holes[0];
        let mut min_y = f64::INFINITY;
        let mut max_y = f64::NEG_INFINITY;
        for pt in hole {
            min_y = min_y.min(pt.y);
            max_y = max_y.max(pt.y);
        }
        let expected_half = 0.125 + 0.5; // half-width + clearance
        let half_observed = (max_y - min_y) / 2.0;
        assert!(
            (half_observed - expected_half).abs() < 1e-3,
            "track half-width: {half_observed} vs expected {expected_half}",
        );
    }

    #[test]
    fn smoke_fill_extra_clearance_widens_keepout() {
        let base = ZoneFillInput {
            outline: square(20.0),
            clearance_mm: 0.5,
            min_thickness_mm: 0.0,
            obstacles: vec![Obstacle {
                geometry: ObstacleGeometry::Disc {
                    center: Point::new(10.0, 10.0),
                    radius_mm: 1.0,
                },
                extra_clearance_mm: 0.0,
                thermal_relief: None,
            }],
        };
        let mut wider = base.clone();
        wider.obstacles[0].extra_clearance_mm = 0.5;

        let r_base = fill_zone(&base);
        let r_wider = fill_zone(&wider);
        // Hole bounding-box area should grow.
        let bb_base = bbox_of_ring(&r_base.polygons[0].holes[0]);
        let bb_wider = bbox_of_ring(&r_wider.polygons[0].holes[0]);
        assert!(
            bb_wider > bb_base,
            "wider hole area {bb_wider} ≤ base {bb_base}",
        );
    }

    #[test]
    fn smoke_fill_min_thickness_preserves_wide_pour() {
        // A 10×10 outline with 0.1 mm clearance and 0.25 mm min
        // thickness: the resulting 9.8×9.8 pour is far wider than
        // 0.25 mm and must survive the open-filter intact.
        let input = ZoneFillInput {
            outline: square(10.0),
            clearance_mm: 0.1,
            min_thickness_mm: 0.25,
            obstacles: Vec::new(),
        };
        let r = fill_zone(&input);
        assert_eq!(r.polygons.len(), 1);
        let bb = r.polygons[0].bounding_box();
        assert!(
            (bb.width() - 9.8).abs() < 1e-3,
            "wide pour shrank unexpectedly: width={}",
            bb.width(),
        );
    }

    #[test]
    fn smoke_fill_min_thickness_culls_narrow_sliver() {
        // Two large obstacles spaced just 0.1 mm apart inside the
        // outline produce a 0.1 mm copper sliver between them. With
        // `min_thickness_mm = 0.2 mm` the sliver must disappear (the
        // erode by 0.1 collapses it; the dilate cannot bring it
        // back). The flanking islands survive.
        let outline = square(10.0);
        let left = Polygon::new(vec![
            Point::new(0.0, 0.0),
            Point::new(4.95, 0.0),
            Point::new(4.95, 10.0),
            Point::new(0.0, 10.0),
        ]);
        let right = Polygon::new(vec![
            Point::new(5.05, 0.0),
            Point::new(10.0, 0.0),
            Point::new(10.0, 10.0),
            Point::new(5.05, 10.0),
        ]);
        let input = ZoneFillInput {
            outline,
            clearance_mm: 0.0,
            min_thickness_mm: 0.2,
            obstacles: vec![
                Obstacle {
                    geometry: ObstacleGeometry::Polygon(left),
                    extra_clearance_mm: 0.0,
                    thermal_relief: None,
                },
                Obstacle {
                    geometry: ObstacleGeometry::Polygon(right),
                    extra_clearance_mm: 0.0,
                    thermal_relief: None,
                },
            ],
        };
        let r = fill_zone(&input);
        // Sliver gone → 0 islands. (Without the min-thickness filter
        // the difference op would have left a 0.1×10 mm sliver.)
        assert!(
            r.polygons.is_empty(),
            "expected sliver culled by min-thickness; got {} islands",
            r.polygons.len(),
        );
    }

    #[test]
    fn smoke_fill_degenerate_outline_returns_empty() {
        let input = ZoneFillInput {
            outline: Polygon::new(vec![Point::new(0.0, 0.0), Point::new(1.0, 0.0)]),
            clearance_mm: 0.5,
            min_thickness_mm: 0.0,
            obstacles: Vec::new(),
        };
        let r = fill_zone(&input);
        assert!(r.polygons.is_empty());
        assert_eq!(r.warnings.len(), 1);
        assert!(r.warnings[0].contains("3 vertices"));
    }

    #[test]
    fn smoke_fill_clearance_too_large_collapses() {
        let input = ZoneFillInput {
            outline: square(2.0),
            clearance_mm: 5.0,
            min_thickness_mm: 0.0,
            obstacles: Vec::new(),
        };
        let r = fill_zone(&input);
        assert!(r.polygons.is_empty(), "expected collapsed fill");
        assert!(
            r.warnings.iter().any(|w| w.contains("collapsed")),
            "expected collapse warning, got {:?}",
            r.warnings,
        );
    }

    #[test]
    fn smoke_inset_concave_l_shape_preserves_winding() {
        // L-shape: 10x10 with a 6x6 cut from the top-right corner.
        let l = Polygon::new(vec![
            Point::new(0.0, 0.0),
            Point::new(10.0, 0.0),
            Point::new(10.0, 4.0),
            Point::new(4.0, 4.0),
            Point::new(4.0, 10.0),
            Point::new(0.0, 10.0),
        ]);
        let original_area = signed_area(&l.points);
        let inset = inward_offset(&l, 0.5);
        let new_area = signed_area(&inset.points);
        assert!(
            new_area.signum() == original_area.signum(),
            "inset reversed winding: {original_area} -> {new_area}",
        );
        assert!(
            new_area.abs() < original_area.abs(),
            "inset area {new_area} not smaller than original {original_area}",
        );
    }

    /// Helper: area of a closed ring (positive number).
    fn bbox_of_ring(ring: &[Point]) -> f64 {
        let mut min = Point::new(f64::INFINITY, f64::INFINITY);
        let mut max = Point::new(f64::NEG_INFINITY, f64::NEG_INFINITY);
        for p in ring {
            min.x = min.x.min(p.x);
            min.y = min.y.min(p.y);
            max.x = max.x.max(p.x);
            max.y = max.y.max(p.y);
        }
        (max.x - min.x) * (max.y - min.y)
    }
}
