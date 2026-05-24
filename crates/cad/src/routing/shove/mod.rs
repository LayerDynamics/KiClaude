//! Push-and-shove router — M3-R-03 (in progress, multi-session).
//!
//! Unlike the [`super::walkaround`] router (A* on an inflated grid
//! that routes *around* obstacles), the push-and-shove router works
//! in continuous space and *moves existing tracks aside* to make
//! room, recursively, while keeping every pair at or beyond the
//! required copper clearance.
//!
//! ## v1 scope (pinned)
//!
//! - **Straight segments only** — no arc routing. Tracks are
//!   polylines tested segment-by-segment.
//! - **Vias + pads are fixed** — the router shoves tracks around
//!   them but never moves a via. The movable-via shove is a later
//!   milestone.
//! - **Per-layer** — collisions only matter between items sharing a
//!   copper layer.
//! - **Walk-around fall-through** — when the shove budget is
//!   exhausted (too many items, recursion too deep, or a cycle), the
//!   caller falls back to [`super::walkaround::route`]. A `PnS` run
//!   that shoves zero items is therefore still a valid result.
//!
//! ## Build order
//!
//! 1. [`geom`] — segment math; the [`geom::push_vector`] atom.
//!    **(landed)**
//! 2. [`world`] — the [`world::ShoveWorld`] obstacle model + the
//!    collision query, with the cycle-detection `ignore` set and
//!    item ids reserved. **(landed)**
//! 3. Single-step shove (push one obstacle to clear the head) +
//!    the recursive shove with cycle detection + dual budgets.
//!    **(next session)**
//! 4. Head-advance loop: walk the head toward the target, shoving as
//!    it goes; backtrack + fall through on budget exhaustion.
//!    **(next session)**
//! 5. wasm + `kc_track_route` mode wiring + the M3-T-05 gesture.
//!    **(next session)**

pub mod geom;
pub mod world;

pub use geom::{push_vector, segment_segment_distance, translate, Vec2};
pub use world::{Collision, HeadSegment, ItemId, ShoveItem, ShoveWorld};

/// Bounds on how hard the shove engine works before giving up and
/// signalling the caller to fall back to walk-around.
///
/// Per the advisor's guidance the two budgets are **independent**:
/// `max_recursion_depth` caps how deep one shove chain goes (A pushes
/// B pushes C …), while `max_total_shoves` caps the total number of
/// item moves across the whole route attempt. A wide-but-shallow
/// shove and a narrow-but-deep one are bounded separately.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct ShoveBudget {
    /// Maximum depth of a single recursive shove chain.
    pub max_recursion_depth: u32,
    /// Maximum number of item displacements across the route.
    pub max_total_shoves: u32,
}

impl Default for ShoveBudget {
    fn default() -> Self {
        // KiCad's interactive PnS defaults to a shove limit around
        // these magnitudes; tuned conservatively for v1 so a runaway
        // shove falls through to walk-around quickly rather than
        // spinning.
        Self {
            max_recursion_depth: 8,
            max_total_shoves: 64,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn budget_default_is_bounded() {
        let b = ShoveBudget::default();
        assert!(b.max_recursion_depth > 0);
        assert!(b.max_total_shoves >= b.max_recursion_depth);
    }
}
