//! Recursive shove engine — M3-R-03 (2/N).
//!
//! [`shove_head`] is the core of push-and-shove: given a provisional
//! *head* segment (the track being routed), it pushes every shovable
//! obstacle aside until the head fits at the required clearance, or
//! reports why it can't (a wall, a shove cycle, or budget exhaustion).
//!
//! ## Algorithm
//!
//! The head is never itself a world item — it's the line being added.
//! The top-level loop repeatedly:
//!
//! 1. queries the head's collisions against the world,
//! 2. takes the first unresolved one,
//! 3. if it's a wall (via / pad / locked track) → `BlockedByWall`,
//! 4. otherwise computes the [`super::geom::push_vector`] that moves
//!    the obstacle's colliding segment just past the clearance
//!    envelope, applies it, then **recurses**: the moved obstacle
//!    becomes a new head that must clear *its* own neighbours.
//!
//! Two independent bounds stop runaway work (per the advisor):
//! `max_recursion_depth` caps one shove chain's depth;
//! `max_total_shoves` caps the total displacements across the call.
//!
//! Cycle detection is explicit: the `path` set holds every item
//! currently being shoved up-stack. If resolving a collision would
//! require shoving an item already on the path (A pushes B pushes A),
//! the engine returns `Cycle` rather than looping forever.
//!
//! ## Transaction semantics
//!
//! [`shove_head`] works on a *clone* of the world and only commits
//! the moved obstacles back into the caller's world when the head
//! fully resolves. A failed shove (wall / cycle / budget) leaves the
//! caller's world untouched — so the route's head-advance loop can
//! try a shove, and on failure fall back to walk-around without
//! having corrupted the board.

use std::collections::HashSet;

use super::geom::{push_vector, translate};
use super::world::{HeadSegment, ItemId, ShoveItem, ShoveWorld};
use super::ShoveBudget;

/// The result of attempting to fit a head into the world by shoving.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ShoveOutcome {
    /// The head fits — all collisions resolved (possibly zero). The
    /// world (on commit) reflects every moved obstacle.
    Resolved {
        /// Number of obstacle displacements applied.
        shoves_applied: u32,
    },
    /// A fixed wall (via / pad / locked track) blocks the head and
    /// cannot be moved. The caller should fall back to walk-around.
    BlockedByWall { item_id: ItemId },
    /// Resolving a collision would require re-shoving an item already
    /// being shoved up-stack — a cycle. Fall back to walk-around.
    Cycle { item_id: ItemId },
    /// Recursion depth or total-shove budget exhausted before the
    /// head fit. Fall back to walk-around.
    BudgetExhausted,
}

impl ShoveOutcome {
    /// `true` only for [`ShoveOutcome::Resolved`].
    #[must_use]
    pub fn is_resolved(&self) -> bool {
        matches!(self, Self::Resolved { .. })
    }
}

/// Try to make room for `head` (on `head_layer`, belonging to
/// `head_net`) by shoving obstacles. On [`ShoveOutcome::Resolved`]
/// the moved obstacles are committed into `world`; on any failure
/// `world` is left exactly as it was.
pub fn shove_head(
    world: &mut ShoveWorld,
    head: HeadSegment,
    head_layer: &str,
    head_net: &str,
    budget: ShoveBudget,
) -> ShoveOutcome {
    let mut working = world.clone();
    let mut path: HashSet<ItemId> = HashSet::new();
    let mut total: u32 = 0;
    let outcome = shove_into(
        &mut working,
        head,
        head_layer,
        head_net,
        budget,
        0,
        &mut path,
        &mut total,
    );
    if outcome.is_resolved() {
        *world = working;
    }
    outcome
}

/// The recursive worker. Mutates `world` in place (the caller passes
/// a throwaway clone). `path` is the active shove stack for cycle
/// detection; `total` accumulates displacements across the whole
/// call for the global budget.
#[allow(clippy::too_many_arguments)]
fn shove_into(
    world: &mut ShoveWorld,
    head: HeadSegment,
    head_layer: &str,
    head_net: &str,
    budget: ShoveBudget,
    depth: u32,
    path: &mut HashSet<ItemId>,
    total: &mut u32,
) -> ShoveOutcome {
    // Items this head has already pushed clear — excluded from
    // re-query so the loop is guaranteed to make progress (each
    // iteration retires a distinct item) and FP residue at the
    // exact clearance boundary can't re-trigger a shove.
    let mut resolved: HashSet<ItemId> = HashSet::new();

    loop {
        let collisions = world.collisions_with(head, head_layer, head_net, &resolved);
        let Some(collision) = collisions.into_iter().next() else {
            return ShoveOutcome::Resolved {
                shoves_applied: *total,
            };
        };

        if !collision.shovable {
            return ShoveOutcome::BlockedByWall {
                item_id: collision.item_id,
            };
        }
        // Shoving an item already on the active stack = cycle.
        if path.contains(&collision.item_id) {
            return ShoveOutcome::Cycle {
                item_id: collision.item_id,
            };
        }
        if depth >= budget.max_recursion_depth {
            return ShoveOutcome::BudgetExhausted;
        }
        let Some(seg_idx) = collision.obstacle_segment_index else {
            // Shovable items are always tracks, which always carry a
            // segment index; this arm is unreachable but handled
            // defensively as a wall.
            return ShoveOutcome::BlockedByWall {
                item_id: collision.item_id,
            };
        };

        // Read the obstacle's colliding segment + its own routing
        // metadata (it becomes the next head when we recurse).
        let Some((o0, o1, obs_net, obs_layer, obs_width)) =
            read_track_segment(world, collision.item_id, seg_idx)
        else {
            return ShoveOutcome::BlockedByWall {
                item_id: collision.item_id,
            };
        };

        let Some(push) = push_vector(
            head.a,
            head.b,
            o0,
            o1,
            collision.required_center_distance_mm,
        ) else {
            // Reported as a collision but the precise push solver says
            // it's already clear (FP boundary). Retire it and continue.
            resolved.insert(collision.item_id);
            continue;
        };

        // Apply the push to the obstacle's two segment vertices.
        if let Some(ShoveItem::Track { points_mm, .. }) = world.get_mut(collision.item_id) {
            points_mm[seg_idx] = translate(points_mm[seg_idx], push);
            points_mm[seg_idx + 1] = translate(points_mm[seg_idx + 1], push);
        }
        *total += 1;
        if *total > budget.max_total_shoves {
            return ShoveOutcome::BudgetExhausted;
        }

        // The moved obstacle now pushes its own neighbours.
        path.insert(collision.item_id);
        let moved_head = HeadSegment {
            a: translate(o0, push),
            b: translate(o1, push),
            width_mm: obs_width,
        };
        let sub = shove_into(
            world,
            moved_head,
            &obs_layer,
            &obs_net,
            budget,
            depth + 1,
            path,
            total,
        );
        path.remove(&collision.item_id);
        if !sub.is_resolved() {
            return sub;
        }
        resolved.insert(collision.item_id);
    }
}

/// Read segment `[idx, idx+1]` of the track with `id`, plus the
/// track's net / layer / width. `None` when the item isn't a track
/// or the index is out of range.
fn read_track_segment(
    world: &ShoveWorld,
    id: ItemId,
    idx: usize,
) -> Option<(crate::geom::Point, crate::geom::Point, String, String, f64)> {
    match world.get(id)? {
        ShoveItem::Track {
            points_mm,
            net,
            layer,
            width_mm,
            ..
        } => {
            let a = *points_mm.get(idx)?;
            let b = *points_mm.get(idx + 1)?;
            Some((a, b, net.clone(), layer.clone(), *width_mm))
        }
        _ => None,
    }
}

#[cfg(test)]
#[allow(clippy::float_cmp)]
mod tests {
    use super::*;
    use crate::geom::Point;
    use crate::routing::shove::world::segment_clearance_ok;

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

    fn track_points(world: &ShoveWorld, id: u32) -> Vec<Point> {
        match world.get(ItemId(id)).unwrap() {
            ShoveItem::Track { points_mm, .. } => points_mm.clone(),
            _ => panic!("not a track"),
        }
    }

    #[test]
    fn no_collision_resolves_with_zero_shoves() {
        let mut world = ShoveWorld::new(0.2);
        world.add(track(
            1,
            "VCC",
            "F.Cu",
            &[(0.0, 5.0), (10.0, 5.0)],
            0.25,
            false,
        ));
        let out = shove_head(
            &mut world,
            head((0.0, 0.0), (10.0, 0.0), 0.25),
            "F.Cu",
            "DATA",
            ShoveBudget::default(),
        );
        assert_eq!(out, ShoveOutcome::Resolved { shoves_applied: 0 });
    }

    #[test]
    fn single_parallel_track_is_pushed_clear() {
        let mut world = ShoveWorld::new(0.2);
        // Obstacle 0.3 mm above head; required center dist 0.45 mm.
        world.add(track(
            1,
            "VCC",
            "F.Cu",
            &[(0.0, 0.3), (10.0, 0.3)],
            0.25,
            false,
        ));
        let out = shove_head(
            &mut world,
            head((0.0, 0.0), (10.0, 0.0), 0.25),
            "F.Cu",
            "DATA",
            ShoveBudget::default(),
        );
        assert_eq!(out, ShoveOutcome::Resolved { shoves_applied: 1 });
        // The track moved up to clear: its y should now be ≥ 0.45.
        let pts = track_points(&world, 1);
        assert!(
            pts.iter().all(|p| p.y >= 0.45 - 1e-6),
            "track not pushed clear: {pts:?}"
        );
        // And it actually satisfies clearance against the head now.
        assert!(segment_clearance_ok(
            head((0.0, 0.0), (10.0, 0.0), 0.25),
            pts[0],
            pts[1],
            0.25,
            0.2,
        ));
    }

    #[test]
    fn via_blocks_the_head() {
        let mut world = ShoveWorld::new(0.2);
        world.add(via(7, "VCC", (5.0, 0.3), 0.6, &["F.Cu"]));
        let out = shove_head(
            &mut world,
            head((0.0, 0.0), (10.0, 0.0), 0.25),
            "F.Cu",
            "DATA",
            ShoveBudget::default(),
        );
        assert_eq!(out, ShoveOutcome::BlockedByWall { item_id: ItemId(7) });
    }

    #[test]
    fn locked_track_blocks_the_head() {
        let mut world = ShoveWorld::new(0.2);
        world.add(track(
            1,
            "VCC",
            "F.Cu",
            &[(0.0, 0.3), (10.0, 0.3)],
            0.25,
            true,
        ));
        let out = shove_head(
            &mut world,
            head((0.0, 0.0), (10.0, 0.0), 0.25),
            "F.Cu",
            "DATA",
            ShoveBudget::default(),
        );
        assert_eq!(out, ShoveOutcome::BlockedByWall { item_id: ItemId(1) });
    }

    #[test]
    fn failed_shove_leaves_world_untouched() {
        let mut world = ShoveWorld::new(0.2);
        // A shovable track close to the head, then a wall just beyond
        // it so shoving the track into the wall fails. The shovable
        // track must NOT remain moved after the failed transaction.
        world.add(track(
            1,
            "VCC",
            "F.Cu",
            &[(0.0, 0.3), (10.0, 0.3)],
            0.25,
            false,
        ));
        world.add(via(2, "GND", (5.0, 0.55), 0.3, &["F.Cu"])); // wall above the track
        let before = track_points(&world, 1);
        let out = shove_head(
            &mut world,
            head((0.0, 0.0), (10.0, 0.0), 0.25),
            "F.Cu",
            "DATA",
            ShoveBudget::default(),
        );
        assert_eq!(out, ShoveOutcome::BlockedByWall { item_id: ItemId(2) });
        // World rolled back: the shovable track is where it started.
        assert_eq!(
            track_points(&world, 1),
            before,
            "world must roll back on failed shove"
        );
    }

    #[test]
    fn chain_of_three_tracks_all_get_pushed() {
        let mut world = ShoveWorld::new(0.2);
        // Three stacked tracks; pushing the first cascades into the
        // others. Spacing 0.3 mm — each pair is under the 0.45 mm
        // required distance so each shove provokes the next.
        world.add(track(
            1,
            "A",
            "F.Cu",
            &[(0.0, 0.3), (10.0, 0.3)],
            0.25,
            false,
        ));
        world.add(track(
            2,
            "B",
            "F.Cu",
            &[(0.0, 0.6), (10.0, 0.6)],
            0.25,
            false,
        ));
        world.add(track(
            3,
            "C",
            "F.Cu",
            &[(0.0, 0.9), (10.0, 0.9)],
            0.25,
            false,
        ));
        let out = shove_head(
            &mut world,
            head((0.0, 0.0), (10.0, 0.0), 0.25),
            "F.Cu",
            "DATA",
            ShoveBudget::default(),
        );
        assert!(out.is_resolved(), "expected cascade resolve, got {out:?}");
        // All three ended up clear of each other + the head.
        let y1 = track_points(&world, 1)[0].y;
        let y2 = track_points(&world, 2)[0].y;
        let y3 = track_points(&world, 3)[0].y;
        assert!(y1 >= 0.45 - 1e-6, "track1 y={y1}");
        assert!(
            (y2 - y1) >= 0.45 - 1e-6,
            "track2-track1 gap too small: {y2} vs {y1}"
        );
        assert!(
            (y3 - y2) >= 0.45 - 1e-6,
            "track3-track2 gap too small: {y3} vs {y2}"
        );
    }

    #[test]
    fn recursion_depth_budget_exhausts_on_deep_chain() {
        let mut world = ShoveWorld::new(0.2);
        // A long tight stack that needs more recursion than the
        // budget allows.
        for i in 0..20u32 {
            let y = 0.3 + f64::from(i) * 0.3;
            world.add(track(
                i + 1,
                &format!("N{i}"),
                "F.Cu",
                &[(0.0, y), (10.0, y)],
                0.25,
                false,
            ));
        }
        let tight = ShoveBudget {
            max_recursion_depth: 3,
            max_total_shoves: 64,
        };
        let out = shove_head(
            &mut world,
            head((0.0, 0.0), (10.0, 0.0), 0.25),
            "F.Cu",
            "DATA",
            tight,
        );
        assert_eq!(out, ShoveOutcome::BudgetExhausted);
        // Rolled back — nothing moved.
        assert_eq!(track_points(&world, 1)[0].y, 0.3);
    }

    #[test]
    fn total_shove_budget_exhausts_independently_of_depth() {
        let mut world = ShoveWorld::new(0.2);
        for i in 0..20u32 {
            let y = 0.3 + f64::from(i) * 0.3;
            world.add(track(
                i + 1,
                &format!("N{i}"),
                "F.Cu",
                &[(0.0, y), (10.0, y)],
                0.25,
                false,
            ));
        }
        // Deep recursion allowed, but only 2 total shoves.
        let tight = ShoveBudget {
            max_recursion_depth: 100,
            max_total_shoves: 2,
        };
        let out = shove_head(
            &mut world,
            head((0.0, 0.0), (10.0, 0.0), 0.25),
            "F.Cu",
            "DATA",
            tight,
        );
        assert_eq!(out, ShoveOutcome::BudgetExhausted);
    }

    #[test]
    fn own_net_obstacle_is_not_shoved() {
        let mut world = ShoveWorld::new(0.2);
        world.add(track(
            1,
            "DATA",
            "F.Cu",
            &[(0.0, 0.3), (10.0, 0.3)],
            0.25,
            false,
        ));
        let before = track_points(&world, 1);
        let out = shove_head(
            &mut world,
            head((0.0, 0.0), (10.0, 0.0), 0.25),
            "F.Cu",
            "DATA",
            ShoveBudget::default(),
        );
        assert_eq!(out, ShoveOutcome::Resolved { shoves_applied: 0 });
        assert_eq!(track_points(&world, 1), before);
    }

    #[test]
    fn different_layer_obstacle_is_ignored() {
        let mut world = ShoveWorld::new(0.2);
        world.add(track(
            1,
            "VCC",
            "B.Cu",
            &[(0.0, 0.3), (10.0, 0.3)],
            0.25,
            false,
        ));
        let out = shove_head(
            &mut world,
            head((0.0, 0.0), (10.0, 0.0), 0.25),
            "F.Cu",
            "DATA",
            ShoveBudget::default(),
        );
        assert_eq!(out, ShoveOutcome::Resolved { shoves_applied: 0 });
    }
}
