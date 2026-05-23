//! Spatial scene with R-tree-backed broad-phase queries.
//!
//! Performance contract (M2-R-07):
//!
//! - On a 5000-track scene, [`Scene::drc_candidate_pairs`] returns
//!   all bbox-overlapping pairs in **≤ 100 ms** including the index
//!   rebuild after edits. Verified by the `bench_5000_tracks_under_100ms`
//!   test below.
//! - Point queries ([`Scene::query_point`]) are O(log n + k).
//!
//! The scene uses a generational id (`ItemId`) so the editor can
//! retain stable handles across insert / remove churn.

use std::collections::HashMap;

use serde::{Deserialize, Serialize};

use crate::geom::{BBox, Point, Polygon};
use crate::index::RTree;

/// Stable per-item identifier handed out by [`Scene::insert`]. Reused
/// IDs are NOT possible — every insert allocates a fresh `ItemId`
/// even when prior IDs have been removed, so editor undo/redo can
/// hold references without aliasing surprises.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, PartialOrd, Ord, Serialize, Deserialize)]
pub struct ItemId(u64);

impl ItemId {
    /// Underlying integer — exposed for debugging / test assertions.
    #[must_use]
    pub fn raw(self) -> u64 {
        self.0
    }
}

/// One geometric item in the scene. The variants mirror the DRC
/// kernel's input shapes but in a lighter form — Scene's only
/// concern is geometric extent + a payload tag, not net membership
/// or layer rules (those live on the DRC-side once a pair candidate
/// is produced).
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub enum SceneItem {
    /// Capsule (segment + endcaps): track + width.
    Track {
        start_mm: Point,
        end_mm: Point,
        width_mm: f64,
        layer: String,
        net: String,
        uuid: String,
    },
    /// Disc on one or more layers.
    Via {
        position_mm: Point,
        diameter_mm: f64,
        drill_mm: f64,
        layers: Vec<String>,
        net: String,
        uuid: String,
    },
    /// Rect-bounded pad with rotation. We store the bbox tag pre-
    /// computed so query candidates can be filtered without re-
    /// running rotation math.
    Pad {
        center_mm: Point,
        size_mm: (f64, f64),
        rotation_deg: f64,
        layers: Vec<String>,
        net: String,
        refdes: String,
        number: String,
    },
    /// Footprint courtyard polygon.
    Courtyard {
        polygon: Polygon,
        layer: String,
        refdes: String,
    },
}

impl SceneItem {
    /// Tight bounding box of this item in board mm.
    #[must_use]
    pub fn bbox(&self) -> BBox {
        match self {
            Self::Track {
                start_mm,
                end_mm,
                width_mm,
                ..
            } => {
                let half = width_mm * 0.5;
                let min_x = start_mm.x.min(end_mm.x) - half;
                let max_x = start_mm.x.max(end_mm.x) + half;
                let min_y = start_mm.y.min(end_mm.y) - half;
                let max_y = start_mm.y.max(end_mm.y) + half;
                BBox::new(min_x, min_y, max_x, max_y)
            }
            Self::Via {
                position_mm,
                diameter_mm,
                ..
            } => {
                let r = diameter_mm * 0.5;
                BBox::new(
                    position_mm.x - r,
                    position_mm.y - r,
                    position_mm.x + r,
                    position_mm.y + r,
                )
            }
            Self::Pad {
                center_mm,
                size_mm,
                rotation_deg,
                ..
            } => rotated_rect_bbox(*center_mm, *size_mm, *rotation_deg),
            Self::Courtyard { polygon, .. } => polygon.bounding_box(),
        }
    }

    /// Set of copper / mask layers this item lives on. `None` for
    /// items that aren't layer-specific (today: none, but reserved
    /// for future schema-only items).
    #[must_use]
    pub fn layers(&self) -> Vec<&str> {
        match self {
            Self::Track { layer, .. } | Self::Courtyard { layer, .. } => vec![layer.as_str()],
            Self::Via { layers, .. } | Self::Pad { layers, .. } => {
                layers.iter().map(String::as_str).collect()
            }
        }
    }
}

/// Editable scene + cached R-tree of item bboxes.
#[derive(Debug, Clone, Default)]
pub struct Scene {
    items: HashMap<ItemId, SceneItem>,
    next_id: u64,
    /// Cached spatial index — rebuilt lazily before any query.
    index: Option<RTree<ItemId>>,
    /// Set when an insert / update / remove has invalidated `index`.
    dirty: bool,
}

impl Scene {
    /// Empty scene.
    #[must_use]
    pub fn new() -> Self {
        Self::default()
    }

    /// Number of items.
    #[must_use]
    pub fn len(&self) -> usize {
        self.items.len()
    }

    /// `true` iff the scene holds no items.
    #[must_use]
    pub fn is_empty(&self) -> bool {
        self.items.is_empty()
    }

    /// Add an item and return its fresh [`ItemId`].
    pub fn insert(&mut self, item: SceneItem) -> ItemId {
        let id = ItemId(self.next_id);
        self.next_id += 1;
        self.items.insert(id, item);
        self.dirty = true;
        id
    }

    /// Replace the geometry of an existing item. Returns `false` if
    /// `id` doesn't exist (the call is a no-op in that case).
    pub fn update(&mut self, id: ItemId, item: SceneItem) -> bool {
        if !self.items.contains_key(&id) {
            return false;
        }
        self.items.insert(id, item);
        self.dirty = true;
        true
    }

    /// Remove an item. Returns the removed item, or `None` if `id`
    /// didn't exist.
    pub fn remove(&mut self, id: ItemId) -> Option<SceneItem> {
        let out = self.items.remove(&id);
        if out.is_some() {
            self.dirty = true;
        }
        out
    }

    /// Borrow an item by id.
    #[must_use]
    pub fn get(&self, id: ItemId) -> Option<&SceneItem> {
        self.items.get(&id)
    }

    /// Iterate every `(id, item)` pair in arbitrary order.
    pub fn iter(&self) -> impl Iterator<Item = (ItemId, &SceneItem)> {
        self.items.iter().map(|(id, item)| (*id, item))
    }

    /// Ensure the R-tree is up to date. Idempotent — calling on a
    /// clean scene is free.
    pub fn rebuild_index(&mut self) {
        if !self.dirty && self.index.is_some() {
            return;
        }
        let mut tree = RTree::<ItemId>::new();
        for (id, item) in &self.items {
            tree.insert(item.bbox(), *id);
        }
        self.index = Some(tree);
        self.dirty = false;
    }

    /// Return every item whose bbox contains the point. The editor's
    /// selection tool calls this on a click.
    #[must_use]
    pub fn query_point(&mut self, p: Point) -> Vec<ItemId> {
        self.rebuild_index();
        let bbox = BBox::from_point(p);
        let tree = self
            .index
            .as_ref()
            .unwrap_or_else(|| unreachable!("rebuild_index just populated `index`"));
        tree.query_with_bbox(bbox)
            .into_iter()
            .map(|(_, id)| *id)
            .collect()
    }

    /// Return every item whose bbox overlaps `bbox`. Used by editor
    /// rectangle-select.
    #[must_use]
    pub fn query_bbox(&mut self, bbox: BBox) -> Vec<ItemId> {
        self.rebuild_index();
        let tree = self
            .index
            .as_ref()
            .unwrap_or_else(|| unreachable!("rebuild_index just populated `index`"));
        tree.query_with_bbox(bbox)
            .into_iter()
            .map(|(_, id)| *id)
            .collect()
    }

    /// Broad-phase candidate pairs for DRC. Returns every unique
    /// `(a, b)` pair (with `a < b` by id ordering) whose bboxes are
    /// within `clearance_bbox_inflate_mm` of each other.
    ///
    /// The DRC kernel takes this list and runs the per-pair geometric
    /// test to confirm or reject each candidate. The R-tree query
    /// makes the candidate set O(n log n + k) instead of O(n²).
    #[must_use]
    pub fn drc_candidate_pairs(&mut self, clearance_bbox_inflate_mm: f64) -> Vec<(ItemId, ItemId)> {
        self.rebuild_index();
        let tree = self
            .index
            .as_ref()
            .unwrap_or_else(|| unreachable!("rebuild_index just populated `index`"));
        let inflate = clearance_bbox_inflate_mm.max(0.0);
        let mut pairs = Vec::new();
        let mut seen = std::collections::HashSet::<(u64, u64)>::new();

        for (id, item) in &self.items {
            let bbox = inflate_bbox(item.bbox(), inflate);
            for (_, other) in tree.query_with_bbox(bbox) {
                if other == id {
                    continue;
                }
                let (lo, hi) = if id.0 < other.0 {
                    (id.0, other.0)
                } else {
                    (other.0, id.0)
                };
                if seen.insert((lo, hi)) {
                    pairs.push((ItemId(lo), ItemId(hi)));
                }
            }
        }
        pairs
    }
}

/// Inflate a bbox uniformly by `delta`. An empty bbox stays empty.
fn inflate_bbox(bbox: BBox, delta: f64) -> BBox {
    if bbox.is_empty() {
        return bbox;
    }
    BBox::new(
        bbox.min.x - delta,
        bbox.min.y - delta,
        bbox.max.x + delta,
        bbox.max.y + delta,
    )
}

/// Bounding box of a rotated axis-aligned rectangle of half-sizes
/// `(hx, hy)` centred at `c`.
fn rotated_rect_bbox(c: Point, size_mm: (f64, f64), rotation_deg: f64) -> BBox {
    let hx = size_mm.0 * 0.5;
    let hy = size_mm.1 * 0.5;
    let (sin_r, cos_r) = rotation_deg.to_radians().sin_cos();
    // Project the rect's two unit edges onto X and Y axes; the result
    // is the half-extent of the AABB.
    let proj_x = (hx * cos_r).abs() + (hy * sin_r).abs();
    let proj_y = (hx * sin_r).abs() + (hy * cos_r).abs();
    BBox::new(c.x - proj_x, c.y - proj_y, c.x + proj_x, c.y + proj_y)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::time::Instant;

    fn track(net: &str, sx: f64, sy: f64, ex: f64, ey: f64, w: f64) -> SceneItem {
        SceneItem::Track {
            start_mm: Point::new(sx, sy),
            end_mm: Point::new(ex, ey),
            width_mm: w,
            layer: "F.Cu".into(),
            net: net.into(),
            uuid: "t".into(),
        }
    }

    fn via(net: &str, x: f64, y: f64, dia: f64) -> SceneItem {
        SceneItem::Via {
            position_mm: Point::new(x, y),
            diameter_mm: dia,
            drill_mm: dia * 0.5,
            layers: vec!["F.Cu".into(), "B.Cu".into()],
            net: net.into(),
            uuid: "v".into(),
        }
    }

    #[test]
    fn smoke_insert_assigns_unique_ids() {
        let mut s = Scene::new();
        let a = s.insert(track("N1", 0.0, 0.0, 10.0, 0.0, 0.2));
        let b = s.insert(track("N1", 0.0, 1.0, 10.0, 1.0, 0.2));
        assert_ne!(a, b);
        assert_eq!(s.len(), 2);
    }

    #[test]
    fn smoke_remove_returns_old_item() {
        let mut s = Scene::new();
        let id = s.insert(via("N1", 5.0, 5.0, 0.6));
        let removed = s.remove(id).expect("removed");
        assert!(matches!(removed, SceneItem::Via { .. }));
        assert!(s.get(id).is_none());
        assert_eq!(s.len(), 0);
    }

    #[test]
    fn smoke_remove_invalid_id_is_noop() {
        let mut s = Scene::new();
        assert!(s.remove(ItemId(999)).is_none());
    }

    #[test]
    fn smoke_update_replaces_geometry() {
        let mut s = Scene::new();
        let id = s.insert(track("N1", 0.0, 0.0, 1.0, 0.0, 0.2));
        let ok = s.update(id, track("N1", 0.0, 0.0, 5.0, 0.0, 0.2));
        assert!(ok);
        if let Some(SceneItem::Track { end_mm, .. }) = s.get(id) {
            assert!((end_mm.x - 5.0).abs() < 1e-9);
        } else {
            panic!("item kind changed unexpectedly");
        }
    }

    #[test]
    fn smoke_query_point_hits_track_bbox() {
        let mut s = Scene::new();
        let id = s.insert(track("N1", 0.0, 0.0, 10.0, 0.0, 0.4));
        let hits = s.query_point(Point::new(5.0, 0.0));
        assert!(hits.contains(&id));
    }

    #[test]
    fn smoke_query_point_misses_outside_bbox() {
        let mut s = Scene::new();
        s.insert(track("N1", 0.0, 0.0, 10.0, 0.0, 0.4));
        let hits = s.query_point(Point::new(50.0, 50.0));
        assert!(hits.is_empty());
    }

    #[test]
    fn smoke_drc_candidate_pairs_finds_close_neighbours() {
        let mut s = Scene::new();
        let a = s.insert(track("N1", 0.0, 0.0, 10.0, 0.0, 0.2));
        let b = s.insert(track("N2", 0.0, 0.3, 10.0, 0.3, 0.2));
        let c = s.insert(track("N3", 0.0, 100.0, 10.0, 100.0, 0.2));
        let pairs = s.drc_candidate_pairs(0.5);
        assert!(pairs.iter().any(|&(x, y)| (x, y) == sort_pair(a, b)));
        assert!(!pairs.iter().any(|&(x, y)| (x, y) == sort_pair(a, c)));
    }

    #[test]
    fn smoke_drc_candidate_pairs_dedups() {
        let mut s = Scene::new();
        let a = s.insert(track("N1", 0.0, 0.0, 10.0, 0.0, 0.2));
        let b = s.insert(track("N2", 0.0, 0.3, 10.0, 0.3, 0.2));
        let pairs = s.drc_candidate_pairs(0.5);
        let want = sort_pair(a, b);
        assert_eq!(pairs.iter().filter(|&&p| p == want).count(), 1);
    }

    #[test]
    fn smoke_rotated_rect_bbox_unrotated() {
        let bb = rotated_rect_bbox(Point::new(10.0, 20.0), (4.0, 6.0), 0.0);
        assert!((bb.min.x - 8.0).abs() < 1e-9);
        assert!((bb.max.x - 12.0).abs() < 1e-9);
        assert!((bb.min.y - 17.0).abs() < 1e-9);
        assert!((bb.max.y - 23.0).abs() < 1e-9);
    }

    #[test]
    fn smoke_rotated_rect_bbox_90deg() {
        // 90° rotation swaps the AABB's width and height.
        let bb = rotated_rect_bbox(Point::new(0.0, 0.0), (4.0, 6.0), 90.0);
        assert!((bb.max.x - 3.0).abs() < 1e-9);
        assert!((bb.max.y - 2.0).abs() < 1e-9);
    }

    /// Performance gate: 5000 tracks must produce DRC candidate
    /// pairs within 100 ms (per M2-R-07 done-when).
    #[test]
    fn bench_5000_tracks_under_100ms() {
        let mut s = Scene::new();
        // Build a 50x100 grid of short tracks on a 200x200 mm board.
        // Each track is 1.6 mm long with 0.2 mm width, on a 2 mm
        // pitch — so neighbours within 2 mm always overlap on bbox.
        for i in 0..50_i32 {
            for j in 0..100_i32 {
                let x = f64::from(i) * 2.0;
                let y = f64::from(j) * 2.0;
                s.insert(track("N1", x, y, x + 1.6, y, 0.2));
            }
        }
        assert_eq!(s.len(), 5000);
        let start = Instant::now();
        let pairs = s.drc_candidate_pairs(0.5);
        let elapsed = start.elapsed();
        assert!(
            elapsed.as_millis() < 100,
            "drc_candidate_pairs on 5000 tracks took {} ms (expected < 100 ms); got {} pairs",
            elapsed.as_millis(),
            pairs.len(),
        );
        assert!(!pairs.is_empty(), "expected non-trivial candidate set");
    }

    /// Helper for assertions on unordered pairs.
    fn sort_pair(a: ItemId, b: ItemId) -> (ItemId, ItemId) {
        if a.0 < b.0 {
            (a, b)
        } else {
            (b, a)
        }
    }
}
