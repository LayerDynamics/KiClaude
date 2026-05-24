//! Shove-world obstacle model — M3-R-03.
//!
//! The push-and-shove router operates on a [`ShoveWorld`]: a flat set
//! of [`ShoveItem`]s, each either *shovable* (an existing track on
//! the same net-class the router may push aside) or *fixed* (pads,
//! vias, locked tracks, board-edge keepouts — walls the router must
//! route/shove around but can never move).
//!
//! v1 scope (pinned — see the M3-R-03 plan note):
//! - Straight segments only. A `Track` is a polyline; collisions are
//!   tested segment-by-segment.
//! - Vias + pads are **fixed**. The movable-via shove is a later
//!   milestone.
//! - Per-layer collisions only — two items collide only when they
//!   share a copper layer.
//!
//! The single-step shove and the recursive head-advance loop live in
//! sibling modules (next session). This module owns the world model
//! plus the collision-query primitive they build on, and reserves
//! the cycle-detection and dual-budget API shape so the shove
//! algorithm slots in without refactoring the world.

use std::collections::HashSet;

use serde::{Deserialize, Serialize};

use crate::geom::Point;

use super::geom::{segment_segment_distance, EPS};

/// Stable identifier for a world item. The shove algorithm threads a
/// `HashSet<ItemId>` "shove path" to detect cycles (shoving A pushes
/// B which pushes A …) and bail before infinite recursion.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, PartialOrd, Ord, Serialize, Deserialize)]
pub struct ItemId(pub u32);

/// One obstacle in the shove world.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub enum ShoveItem {
    /// A copper track — a polyline of ≥ 2 points on one layer.
    Track {
        id: ItemId,
        /// Net name — the router never collides a track with its own
        /// net (you're allowed to touch your own copper).
        net: String,
        layer: String,
        width_mm: f64,
        points_mm: Vec<Point>,
        /// `true` → the user pinned this track; it becomes a fixed
        /// wall even though it's a track.
        locked: bool,
    },
    /// A via — always fixed in v1.
    Via {
        id: ItemId,
        net: String,
        position_mm: Point,
        diameter_mm: f64,
        /// Copper layers the via spans (through-hole = all; blind /
        /// buried = a contiguous subset).
        layers: Vec<String>,
    },
    /// A pad — always fixed.
    Pad {
        id: ItemId,
        net: String,
        position_mm: Point,
        /// Bounding radius for the v1 circular-approximation collision
        /// test (real pad polygons land with the arc milestone).
        radius_mm: f64,
        layers: Vec<String>,
    },
}

impl ShoveItem {
    #[must_use]
    pub fn id(&self) -> ItemId {
        match self {
            Self::Track { id, .. } | Self::Via { id, .. } | Self::Pad { id, .. } => *id,
        }
    }

    #[must_use]
    pub fn net(&self) -> &str {
        match self {
            Self::Track { net, .. } | Self::Via { net, .. } | Self::Pad { net, .. } => net,
        }
    }

    /// Whether the router may push this item. Only unlocked tracks
    /// are shovable in v1; vias + pads + locked tracks are walls.
    #[must_use]
    pub fn is_shovable(&self) -> bool {
        matches!(self, Self::Track { locked: false, .. })
    }

    /// Does this item exist on `layer`?
    #[must_use]
    pub fn on_layer(&self, layer: &str) -> bool {
        match self {
            Self::Track { layer: l, .. } => l == layer,
            Self::Via { layers, .. } | Self::Pad { layers, .. } => {
                layers.iter().any(|l| l == layer)
            }
        }
    }

    /// Half-width / radius of this item's copper, for clearance math.
    #[must_use]
    pub fn half_extent_mm(&self) -> f64 {
        match self {
            Self::Track { width_mm, .. } => width_mm / 2.0,
            Self::Via { diameter_mm, .. } => diameter_mm / 2.0,
            Self::Pad { radius_mm, .. } => *radius_mm,
        }
    }
}

/// A segment of the head line being routed — what the world is
/// queried against.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct HeadSegment {
    pub a: Point,
    pub b: Point,
    pub width_mm: f64,
}

/// One collision the head has with a world item.
#[derive(Debug, Clone, PartialEq)]
pub struct Collision {
    /// The item the head collided with.
    pub item_id: ItemId,
    /// `true` when the item can be shoved; `false` for walls.
    pub shovable: bool,
    /// Center-to-center distance at the closest approach.
    pub center_distance_mm: f64,
    /// The clearance the pair must satisfy (sum of half-extents +
    /// the required copper clearance).
    pub required_center_distance_mm: f64,
    /// For a track item, the index of the obstacle segment that
    /// collided (so the shove step knows which segment to push).
    /// `None` for vias / pads (single-shape items).
    pub obstacle_segment_index: Option<usize>,
}

impl Collision {
    /// How deep the head penetrates the required clearance envelope.
    /// Always positive for a real collision.
    #[must_use]
    pub fn penetration_mm(&self) -> f64 {
        (self.required_center_distance_mm - self.center_distance_mm).max(0.0)
    }
}

/// The obstacle set the router shoves within.
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct ShoveWorld {
    items: Vec<ShoveItem>,
    /// Copper-to-copper clearance the router must maintain (mm).
    pub clearance_mm: f64,
}

impl ShoveWorld {
    #[must_use]
    pub fn new(clearance_mm: f64) -> Self {
        Self {
            items: Vec::new(),
            clearance_mm,
        }
    }

    pub fn add(&mut self, item: ShoveItem) {
        self.items.push(item);
    }

    #[must_use]
    pub fn items(&self) -> &[ShoveItem] {
        &self.items
    }

    #[must_use]
    pub fn get(&self, id: ItemId) -> Option<&ShoveItem> {
        self.items.iter().find(|i| i.id() == id)
    }

    pub fn get_mut(&mut self, id: ItemId) -> Option<&mut ShoveItem> {
        self.items.iter_mut().find(|i| i.id() == id)
    }

    /// Every collision the head segment has against world items on
    /// `head_layer`, excluding `head_net` (own-net copper is never an
    /// obstacle) and excluding any item id in `ignore` (the shove
    /// path — items currently being moved up-stack, to break cycles).
    ///
    /// The `ignore` set is the cycle-detection hook the recursive
    /// shove threads through; this session's collision query already
    /// honours it so the shove loop can be added without touching
    /// the world API.
    #[must_use]
    pub fn collisions_with(
        &self,
        head: HeadSegment,
        head_layer: &str,
        head_net: &str,
        ignore: &HashSet<ItemId>,
    ) -> Vec<Collision> {
        let head_half = head.width_mm / 2.0;
        let mut out = Vec::new();
        for item in &self.items {
            if item.net() == head_net {
                continue;
            }
            if !item.on_layer(head_layer) {
                continue;
            }
            if ignore.contains(&item.id()) {
                continue;
            }
            let required = head_half + item.half_extent_mm() + self.clearance_mm;
            if let Some(collision) = collision_for(head, item, required) {
                out.push(collision);
            }
        }
        out
    }
}

/// Compute the collision (if any) between a head segment and one
/// world item at the given required center distance.
#[must_use]
fn collision_for(head: HeadSegment, item: &ShoveItem, required: f64) -> Option<Collision> {
    match item {
        ShoveItem::Track {
            id,
            points_mm,
            locked,
            ..
        } => {
            let mut best: Option<(f64, usize)> = None;
            for (idx, window) in points_mm.windows(2).enumerate() {
                let dist = segment_segment_distance(head.a, head.b, window[0], window[1]);
                if best.is_none_or(|(bd, _)| dist < bd) {
                    best = Some((dist, idx));
                }
            }
            let (dist, idx) = best?;
            if dist >= required - EPS {
                return None;
            }
            Some(Collision {
                item_id: *id,
                shovable: !*locked,
                center_distance_mm: dist,
                required_center_distance_mm: required,
                obstacle_segment_index: Some(idx),
            })
        }
        ShoveItem::Via {
            id, position_mm, ..
        }
        | ShoveItem::Pad {
            id, position_mm, ..
        } => {
            // Point-shape items: distance from the head segment to the
            // item center.
            let (_, head_pt) = super::geom::closest_on_segment(*position_mm, head.a, head.b);
            let dist = head_pt.distance_to(position_mm);
            if dist >= required - EPS {
                return None;
            }
            Some(Collision {
                item_id: *id,
                shovable: false,
                center_distance_mm: dist,
                required_center_distance_mm: required,
                obstacle_segment_index: None,
            })
        }
    }
}

#[cfg(test)]
#[allow(clippy::float_cmp)]
mod tests {
    use super::*;

    fn pt(x: f64, y: f64) -> Point {
        Point::new(x, y)
    }

    fn track(
        id: u32,
        net: &str,
        layer: &str,
        pts: &[(f64, f64)],
        width: f64,
        locked: bool,
    ) -> ShoveItem {
        ShoveItem::Track {
            id: ItemId(id),
            net: net.to_string(),
            layer: layer.to_string(),
            width_mm: width,
            points_mm: pts.iter().map(|&(x, y)| pt(x, y)).collect(),
            locked,
        }
    }

    fn via(id: u32, net: &str, pos: (f64, f64), dia: f64, layers: &[&str]) -> ShoveItem {
        ShoveItem::Via {
            id: ItemId(id),
            net: net.to_string(),
            position_mm: pt(pos.0, pos.1),
            diameter_mm: dia,
            layers: layers.iter().map(|s| (*s).to_string()).collect(),
        }
    }

    fn head(a: (f64, f64), b: (f64, f64), width: f64) -> HeadSegment {
        HeadSegment {
            a: pt(a.0, a.1),
            b: pt(b.0, b.1),
            width_mm: width,
        }
    }

    #[test]
    fn shovable_only_for_unlocked_tracks() {
        assert!(track(1, "n", "F.Cu", &[(0.0, 0.0), (1.0, 0.0)], 0.2, false).is_shovable());
        assert!(!track(1, "n", "F.Cu", &[(0.0, 0.0), (1.0, 0.0)], 0.2, true).is_shovable());
        assert!(!via(2, "n", (0.0, 0.0), 0.6, &["F.Cu"]).is_shovable());
    }

    #[test]
    fn own_net_never_collides() {
        let mut world = ShoveWorld::new(0.2);
        world.add(track(
            1,
            "GND",
            "F.Cu",
            &[(0.0, 0.0), (10.0, 0.0)],
            0.25,
            false,
        ));
        // Head on the SAME net, right on top of it.
        let cols = world.collisions_with(
            head((0.0, 0.0), (10.0, 0.0), 0.25),
            "F.Cu",
            "GND",
            &HashSet::new(),
        );
        assert!(cols.is_empty(), "own-net copper must not be an obstacle");
    }

    #[test]
    fn different_layer_never_collides() {
        let mut world = ShoveWorld::new(0.2);
        world.add(track(
            1,
            "VCC",
            "B.Cu",
            &[(0.0, 0.0), (10.0, 0.0)],
            0.25,
            false,
        ));
        let cols = world.collisions_with(
            head((0.0, 0.0), (10.0, 0.0), 0.25),
            "F.Cu",
            "DATA",
            &HashSet::new(),
        );
        assert!(
            cols.is_empty(),
            "B.Cu track must not collide with an F.Cu head"
        );
    }

    #[test]
    fn parallel_track_within_clearance_collides_and_is_shovable() {
        let mut world = ShoveWorld::new(0.2);
        // Obstacle 0.3 mm above the head. Required center distance =
        // 0.125 (head half) + 0.125 (obs half) + 0.2 (clearance) =
        // 0.45 mm. Actual = 0.3 → collision.
        world.add(track(
            1,
            "VCC",
            "F.Cu",
            &[(0.0, 0.3), (10.0, 0.3)],
            0.25,
            false,
        ));
        let cols = world.collisions_with(
            head((0.0, 0.0), (10.0, 0.0), 0.25),
            "F.Cu",
            "DATA",
            &HashSet::new(),
        );
        assert_eq!(cols.len(), 1);
        let c = &cols[0];
        assert_eq!(c.item_id, ItemId(1));
        assert!(c.shovable);
        assert!((c.center_distance_mm - 0.3).abs() < 1e-9);
        assert!((c.required_center_distance_mm - 0.45).abs() < 1e-9);
        assert!(c.penetration_mm() > 0.0);
        assert_eq!(c.obstacle_segment_index, Some(0));
    }

    #[test]
    fn track_beyond_clearance_does_not_collide() {
        let mut world = ShoveWorld::new(0.2);
        // 1 mm away, well past the 0.45 mm required distance.
        world.add(track(
            1,
            "VCC",
            "F.Cu",
            &[(0.0, 1.0), (10.0, 1.0)],
            0.25,
            false,
        ));
        let cols = world.collisions_with(
            head((0.0, 0.0), (10.0, 0.0), 0.25),
            "F.Cu",
            "DATA",
            &HashSet::new(),
        );
        assert!(cols.is_empty());
    }

    #[test]
    fn locked_track_collides_but_is_not_shovable() {
        let mut world = ShoveWorld::new(0.2);
        world.add(track(
            1,
            "VCC",
            "F.Cu",
            &[(0.0, 0.3), (10.0, 0.3)],
            0.25,
            true,
        ));
        let cols = world.collisions_with(
            head((0.0, 0.0), (10.0, 0.0), 0.25),
            "F.Cu",
            "DATA",
            &HashSet::new(),
        );
        assert_eq!(cols.len(), 1);
        assert!(!cols[0].shovable, "locked track is a wall");
    }

    #[test]
    fn via_collides_as_fixed_wall() {
        let mut world = ShoveWorld::new(0.2);
        // Via center 0.4 mm above the head. Required = 0.125 + 0.3
        // (via radius) + 0.2 = 0.625. Actual 0.4 → collision.
        world.add(via(7, "VCC", (5.0, 0.4), 0.6, &["F.Cu", "B.Cu"]));
        let cols = world.collisions_with(
            head((0.0, 0.0), (10.0, 0.0), 0.25),
            "F.Cu",
            "DATA",
            &HashSet::new(),
        );
        assert_eq!(cols.len(), 1);
        assert!(!cols[0].shovable, "vias are fixed in v1");
        assert_eq!(cols[0].obstacle_segment_index, None);
        assert!((cols[0].center_distance_mm - 0.4).abs() < 1e-9);
    }

    #[test]
    fn ignore_set_suppresses_collisions_for_cycle_breaking() {
        let mut world = ShoveWorld::new(0.2);
        world.add(track(
            1,
            "VCC",
            "F.Cu",
            &[(0.0, 0.3), (10.0, 0.3)],
            0.25,
            false,
        ));
        let mut ignore = HashSet::new();
        ignore.insert(ItemId(1));
        let cols =
            world.collisions_with(head((0.0, 0.0), (10.0, 0.0), 0.25), "F.Cu", "DATA", &ignore);
        assert!(cols.is_empty(), "items on the shove path are skipped");
    }

    #[test]
    fn multi_segment_track_reports_closest_segment_index() {
        let mut world = ShoveWorld::new(0.2);
        // An L-shaped track. Segment 2 ((5,0.3)->(10,0.3)) runs
        // parallel to + just above the head. A short head under the
        // *interior* of segment 2 keeps its closest approach away
        // from the shared corner (5,0.3) — so segment 2 is
        // unambiguously closer than the vertical segment 1 (whose
        // nearest point is that corner, ~1 mm away from this head).
        world.add(track(
            1,
            "VCC",
            "F.Cu",
            &[(0.0, 5.0), (5.0, 5.0), (5.0, 0.3), (10.0, 0.3)],
            0.25,
            false,
        ));
        let cols = world.collisions_with(
            head((6.0, 0.0), (9.0, 0.0), 0.25),
            "F.Cu",
            "DATA",
            &HashSet::new(),
        );
        assert_eq!(cols.len(), 1);
        // Segment index 2 is the (5,0.3)->(10,0.3) run parallel to the head.
        assert_eq!(cols[0].obstacle_segment_index, Some(2));
    }

    #[test]
    fn get_and_get_mut_resolve_by_id() {
        let mut world = ShoveWorld::new(0.2);
        world.add(track(
            1,
            "VCC",
            "F.Cu",
            &[(0.0, 0.0), (10.0, 0.0)],
            0.25,
            false,
        ));
        world.add(via(2, "GND", (5.0, 5.0), 0.6, &["F.Cu"]));
        assert!(world.get(ItemId(1)).is_some());
        assert!(world.get(ItemId(2)).is_some());
        assert!(world.get(ItemId(99)).is_none());
        if let Some(ShoveItem::Track { width_mm, .. }) = world.get_mut(ItemId(1)) {
            *width_mm = 0.5;
        }
        assert_eq!(world.get(ItemId(1)).unwrap().half_extent_mm(), 0.25);
    }
}
