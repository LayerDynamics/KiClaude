//! Head-advance routing loop — M3-R-03 (3/N).
//!
//! [`route_shove`] is the top-level push-and-shove entry point the
//! wasm bridge + `kc_track_route` call. It walks a head from `start`
//! to `end`, placing the route as a sequence of segments and shoving
//! obstacles aside via [`super::engine::shove_head`] as it goes.
//!
//! ## Strategy (v1)
//!
//! The route is attempted as a single direct segment `start → end`
//! first (the common interactive case: the user drags one trace and
//! the tool shoves everything in its path). If the direct head
//! resolves, that's the route. If it's blocked by a wall or the shove
//! budget is exhausted, the caller is told to **fall back to
//! walk-around** ([`super::super::walkaround::route`]) — a `PnS` result
//! that shoves zero items and reports `FellBack` is still valid; the
//! walk-around router then finds a path *around* the wall.
//!
//! Multi-segment head paths (L-shaped shoves, escape routing out of a
//! congested fanout) are a later milestone; v1 ships the direct-shove
//! case which is where `PnS` earns its keep over walk-around.

use serde::{Deserialize, Serialize};

use crate::geom::Point;

use super::engine::{shove_head, ShoveOutcome};
use super::world::{HeadSegment, ShoveItem, ShoveWorld};
use super::ShoveBudget;

/// Inputs to the push-and-shove router.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ShoveRouteInput {
    pub start_mm: Point,
    pub end_mm: Point,
    pub track_width_mm: f64,
    pub layer: String,
    pub net: String,
    /// Copper-to-copper clearance (mm).
    pub clearance_mm: f64,
    /// Shove budget. Omit for the conservative default.
    #[serde(default = "default_budget")]
    pub budget: ShoveBudget,
}

fn default_budget() -> ShoveBudget {
    ShoveBudget::default()
}

/// One displaced obstacle, reported so the caller can persist the
/// moved tracks back into the project.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct MovedTrack {
    pub item_id: u32,
    pub net: String,
    pub layer: String,
    pub width_mm: f64,
    pub points_mm: Vec<Point>,
}

/// The outcome of a push-and-shove route attempt.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(tag = "status", rename_all = "snake_case")]
pub enum ShoveRouteResult {
    /// The route was placed (possibly after shoving). `route_mm` is
    /// the new track's polyline; `moved` lists every obstacle that
    /// was pushed (with its new geometry).
    Routed {
        route_mm: Vec<Point>,
        moved: Vec<MovedTrack>,
        shoves_applied: u32,
    },
    /// `PnS` could not place the route (wall in the way, cycle, or
    /// budget exhausted). The caller should fall back to walk-around.
    /// `reason` carries the engine's verdict for diagnostics.
    FellBack { reason: String },
    /// Malformed input (zero-length route, non-positive width, …).
    InvalidInput { message: String },
}

/// Run the push-and-shove router. On `Routed` the returned `moved`
/// list is the set of obstacles whose geometry changed; the caller
/// applies them + adds `route_mm` as the new track.
#[must_use]
pub fn route_shove(world: &ShoveWorld, input: &ShoveRouteInput) -> ShoveRouteResult {
    if input.track_width_mm <= 0.0 {
        return ShoveRouteResult::InvalidInput {
            message: "track_width_mm must be positive".to_string(),
        };
    }
    if input.start_mm.distance_to(&input.end_mm) < super::geom::EPS {
        return ShoveRouteResult::InvalidInput {
            message: "start and end coincide".to_string(),
        };
    }

    // Work on a clone so a FellBack outcome doesn't perturb the
    // caller's world (shove_head already rolls back internally, but
    // cloning here keeps the diff computation simple).
    let mut working = world.clone();
    let head = HeadSegment {
        a: input.start_mm,
        b: input.end_mm,
        width_mm: input.track_width_mm,
    };
    let outcome = shove_head(&mut working, head, &input.layer, &input.net, input.budget);

    match outcome {
        ShoveOutcome::Resolved { shoves_applied } => {
            let moved = diff_moved_tracks(world, &working);
            ShoveRouteResult::Routed {
                route_mm: vec![input.start_mm, input.end_mm],
                moved,
                shoves_applied,
            }
        }
        ShoveOutcome::BlockedByWall { item_id } => ShoveRouteResult::FellBack {
            reason: format!("blocked by fixed item {}", item_id.0),
        },
        ShoveOutcome::Cycle { item_id } => ShoveRouteResult::FellBack {
            reason: format!("shove cycle at item {}", item_id.0),
        },
        ShoveOutcome::BudgetExhausted => ShoveRouteResult::FellBack {
            reason: "shove budget exhausted".to_string(),
        },
    }
}

/// Compute which tracks moved between the original world and the
/// post-shove world, by id. Only tracks can move in v1.
fn diff_moved_tracks(before: &ShoveWorld, after: &ShoveWorld) -> Vec<MovedTrack> {
    let mut moved = Vec::new();
    for item in after.items() {
        let ShoveItem::Track {
            id,
            net,
            layer,
            width_mm,
            points_mm,
            ..
        } = item
        else {
            continue;
        };
        if let Some(ShoveItem::Track {
            points_mm: before_pts,
            ..
        }) = before.get(*id)
        {
            if before_pts != points_mm {
                moved.push(MovedTrack {
                    item_id: id.0,
                    net: net.clone(),
                    layer: layer.clone(),
                    width_mm: *width_mm,
                    points_mm: points_mm.clone(),
                });
            }
        }
    }
    moved.sort_by_key(|m| m.item_id);
    moved
}

#[cfg(test)]
#[allow(clippy::float_cmp)]
mod tests {
    use super::super::world::ItemId;
    use super::*;

    /// Ids of the tracks a route result reports as shoved.
    fn moved_ids(result: &ShoveRouteResult) -> Vec<ItemId> {
        match result {
            ShoveRouteResult::Routed { moved, .. } => {
                moved.iter().map(|m| ItemId(m.item_id)).collect()
            }
            _ => Vec::new(),
        }
    }

    fn pt(x: f64, y: f64) -> Point {
        Point::new(x, y)
    }

    fn track(id: u32, net: &str, pts: &[(f64, f64)], width: f64, locked: bool) -> ShoveItem {
        ShoveItem::Track {
            id: ItemId(id),
            net: net.to_string(),
            layer: "F.Cu".to_string(),
            width_mm: width,
            points_mm: pts.iter().map(|&(x, y)| pt(x, y)).collect(),
            locked,
        }
    }

    fn via(id: u32, net: &str, pos: (f64, f64), dia: f64) -> ShoveItem {
        ShoveItem::Via {
            id: ItemId(id),
            net: net.to_string(),
            position_mm: pt(pos.0, pos.1),
            diameter_mm: dia,
            layers: vec!["F.Cu".to_string()],
        }
    }

    fn input(start: (f64, f64), end: (f64, f64)) -> ShoveRouteInput {
        ShoveRouteInput {
            start_mm: pt(start.0, start.1),
            end_mm: pt(end.0, end.1),
            track_width_mm: 0.25,
            layer: "F.Cu".to_string(),
            net: "DATA".to_string(),
            clearance_mm: 0.2,
            budget: ShoveBudget::default(),
        }
    }

    #[test]
    fn clear_lane_routes_with_no_moves() {
        let world = ShoveWorld::new(0.2);
        let res = route_shove(&world, &input((0.0, 0.0), (10.0, 0.0)));
        match res {
            ShoveRouteResult::Routed {
                route_mm,
                moved,
                shoves_applied,
            } => {
                assert_eq!(route_mm, vec![pt(0.0, 0.0), pt(10.0, 0.0)]);
                assert!(moved.is_empty());
                assert_eq!(shoves_applied, 0);
            }
            other => panic!("expected Routed, got {other:?}"),
        }
    }

    #[test]
    fn parallel_track_in_the_way_is_shoved_and_reported() {
        let mut world = ShoveWorld::new(0.2);
        world.add(track(1, "VCC", &[(0.0, 0.3), (10.0, 0.3)], 0.25, false));
        let res = route_shove(&world, &input((0.0, 0.0), (10.0, 0.0)));
        match res {
            ShoveRouteResult::Routed {
                moved,
                shoves_applied,
                ..
            } => {
                assert_eq!(shoves_applied, 1);
                assert_eq!(moved.len(), 1);
                assert_eq!(moved[0].item_id, 1);
                assert!(moved[0].points_mm.iter().all(|p| p.y >= 0.45 - 1e-6));
            }
            other => panic!("expected Routed, got {other:?}"),
        }
    }

    #[test]
    fn wall_in_the_way_falls_back() {
        let mut world = ShoveWorld::new(0.2);
        world.add(via(7, "VCC", (5.0, 0.3), 0.6));
        let res = route_shove(&world, &input((0.0, 0.0), (10.0, 0.0)));
        match res {
            ShoveRouteResult::FellBack { reason } => {
                assert!(reason.contains("fixed item 7"), "reason: {reason}");
            }
            other => panic!("expected FellBack, got {other:?}"),
        }
    }

    #[test]
    fn budget_exhaustion_falls_back() {
        let mut world = ShoveWorld::new(0.2);
        for i in 0..20u32 {
            let y = 0.3 + f64::from(i) * 0.3;
            world.add(track(
                i + 1,
                &format!("N{i}"),
                &[(0.0, y), (10.0, y)],
                0.25,
                false,
            ));
        }
        let mut inp = input((0.0, 0.0), (10.0, 0.0));
        inp.budget = ShoveBudget {
            max_recursion_depth: 2,
            max_total_shoves: 64,
        };
        let res = route_shove(&world, &inp);
        assert!(
            matches!(res, ShoveRouteResult::FellBack { .. }),
            "expected FellBack, got {res:?}"
        );
    }

    #[test]
    fn zero_length_route_is_invalid() {
        let world = ShoveWorld::new(0.2);
        let res = route_shove(&world, &input((5.0, 5.0), (5.0, 5.0)));
        assert!(matches!(res, ShoveRouteResult::InvalidInput { .. }));
    }

    #[test]
    fn non_positive_width_is_invalid() {
        let world = ShoveWorld::new(0.2);
        let mut inp = input((0.0, 0.0), (10.0, 0.0));
        inp.track_width_mm = 0.0;
        let res = route_shove(&world, &inp);
        assert!(matches!(res, ShoveRouteResult::InvalidInput { .. }));
    }

    #[test]
    fn failed_route_does_not_mutate_caller_world() {
        let mut world = ShoveWorld::new(0.2);
        world.add(track(1, "VCC", &[(0.0, 0.3), (10.0, 0.3)], 0.25, false));
        world.add(via(2, "GND", (5.0, 0.55), 0.3));
        let snapshot = world.clone();
        let _ = route_shove(&world, &input((0.0, 0.0), (10.0, 0.0)));
        // route_shove takes &world (immutable) — proving non-mutation
        // structurally, but assert the snapshot is identical too.
        assert_eq!(
            format!("{:?}", world.items()),
            format!("{:?}", snapshot.items())
        );
    }

    #[test]
    fn budget_round_trips_through_json() {
        let inp = input((0.0, 0.0), (10.0, 0.0));
        let json = serde_json::to_string(&inp).unwrap();
        let back: ShoveRouteInput = serde_json::from_str(&json).unwrap();
        assert_eq!(back.budget, ShoveBudget::default());
        // Explicit budget survives.
        let custom = "{\"start_mm\":{\"x\":0,\"y\":0},\"end_mm\":{\"x\":1,\"y\":0},\
            \"track_width_mm\":0.25,\"layer\":\"F.Cu\",\"net\":\"D\",\"clearance_mm\":0.2,\
            \"budget\":{\"max_recursion_depth\":3,\"max_total_shoves\":9}}";
        let parsed: ShoveRouteInput = serde_json::from_str(custom).unwrap();
        assert_eq!(parsed.budget.max_recursion_depth, 3);
        assert_eq!(parsed.budget.max_total_shoves, 9);
    }

    #[test]
    fn moved_ids_helper_lists_shoved_tracks() {
        let mut world = ShoveWorld::new(0.2);
        world.add(track(1, "VCC", &[(0.0, 0.3), (10.0, 0.3)], 0.25, false));
        let res = route_shove(&world, &input((0.0, 0.0), (10.0, 0.0)));
        assert_eq!(moved_ids(&res), vec![ItemId(1)]);
    }
}
