// Float tie-break comparisons are intentional bitwise-equality checks
// (zero-enlargement / equal-area cases). `total_cmp` would be heavier and
// less readable for what these conditionals express.
#![allow(clippy::float_cmp)]

//! A simple R-tree with linear node splits.
//!
//! Each tree node holds up to [`MAX_ENTRIES`] children. On insert we
//! descend by minimum bbox enlargement; on overflow we split the node
//! using a linear-time pick-seeds heuristic (Guttman 1984's simplified
//! variant). Performance is O(log n) average insert and O(log n + k)
//! query for k results in a balanced tree.
//!
//! This implementation is deliberately straightforward — it does not
//! attempt to be the fastest R-tree available. The M0 acceptance test
//! is a 10k-element correctness fuzz that compares query results
//! against brute force; that's the contract.

use crate::geom::BBox;

/// Maximum entries per node before a split is triggered.
pub const MAX_ENTRIES: usize = 8;

/// Minimum entries per node after a split.
pub const MIN_ENTRIES: usize = 3;

/// An R-tree mapping bounding boxes to user payloads.
#[derive(Debug, Clone)]
pub struct RTree<T> {
    root: Node<T>,
    len: usize,
}

#[derive(Debug, Clone)]
enum Node<T> {
    Leaf { entries: Vec<(BBox, T)>, bbox: BBox },
    Internal { children: Vec<Node<T>>, bbox: BBox },
}

impl<T> Node<T> {
    fn bbox(&self) -> BBox {
        match self {
            Self::Leaf { bbox, .. } | Self::Internal { bbox, .. } => *bbox,
        }
    }
}

impl<T: Clone> Default for RTree<T> {
    fn default() -> Self {
        Self::new()
    }
}

impl<T: Clone> RTree<T> {
    /// Construct an empty tree.
    #[must_use]
    pub fn new() -> Self {
        Self {
            root: Node::Leaf {
                entries: Vec::new(),
                bbox: BBox::empty(),
            },
            len: 0,
        }
    }

    /// Number of entries in the tree.
    #[must_use]
    pub fn len(&self) -> usize {
        self.len
    }

    /// `true` iff no entries have been inserted.
    #[must_use]
    pub fn is_empty(&self) -> bool {
        self.len == 0
    }

    /// Insert an entry. Multiple entries with the same bbox or payload
    /// are allowed — the tree stores them all.
    pub fn insert(&mut self, bbox: BBox, payload: T) {
        let split = insert_into(&mut self.root, bbox, payload);
        if let Some(extra) = split {
            // Root split — grow the tree by one level.
            let old_root = std::mem::replace(
                &mut self.root,
                Node::Leaf {
                    entries: Vec::new(),
                    bbox: BBox::empty(),
                },
            );
            let new_root = Node::Internal {
                bbox: old_root.bbox().union(&extra.bbox()),
                children: vec![old_root, extra],
            };
            self.root = new_root;
        }
        self.len += 1;
    }

    /// All payloads whose stored bbox intersects `query`. Order is
    /// unspecified.
    #[must_use]
    pub fn query(&self, query: BBox) -> Vec<&T> {
        let mut out = Vec::new();
        collect_intersecting(&self.root, query, &mut out);
        out
    }

    /// Like [`query`](Self::query) but borrows-only — returns refs to the
    /// stored bbox alongside the payload. Useful for callers that need
    /// to filter further (e.g. exact polygon-in-rect tests).
    #[must_use]
    pub fn query_with_bbox(&self, query: BBox) -> Vec<(BBox, &T)> {
        let mut out = Vec::new();
        collect_intersecting_with_bbox(&self.root, query, &mut out);
        out
    }
}

fn collect_intersecting<'a, T>(node: &'a Node<T>, query: BBox, out: &mut Vec<&'a T>) {
    if !node.bbox().intersects(&query) {
        return;
    }
    match node {
        Node::Leaf { entries, .. } => {
            for (b, p) in entries {
                if b.intersects(&query) {
                    out.push(p);
                }
            }
        }
        Node::Internal { children, .. } => {
            for c in children {
                collect_intersecting(c, query, out);
            }
        }
    }
}

fn collect_intersecting_with_bbox<'a, T>(
    node: &'a Node<T>,
    query: BBox,
    out: &mut Vec<(BBox, &'a T)>,
) {
    if !node.bbox().intersects(&query) {
        return;
    }
    match node {
        Node::Leaf { entries, .. } => {
            for (b, p) in entries {
                if b.intersects(&query) {
                    out.push((*b, p));
                }
            }
        }
        Node::Internal { children, .. } => {
            for c in children {
                collect_intersecting_with_bbox(c, query, out);
            }
        }
    }
}

/// Insert into `node`. Returns `Some(sibling)` if `node` was split, or
/// `None` if it grew without overflowing.
fn insert_into<T: Clone>(node: &mut Node<T>, bbox: BBox, payload: T) -> Option<Node<T>> {
    match node {
        Node::Leaf { entries, bbox: nb } => {
            entries.push((bbox, payload));
            *nb = nb.union(&bbox);
            if entries.len() > MAX_ENTRIES {
                Some(split_leaf(node))
            } else {
                None
            }
        }
        Node::Internal { children, bbox: nb } => {
            // Choose child with min enlargement; tie-break on smaller area.
            let mut best = 0usize;
            let mut best_enlarge = f64::INFINITY;
            let mut best_area = f64::INFINITY;
            for (i, c) in children.iter().enumerate() {
                let cb = c.bbox();
                let enlarge = cb.enlargement_to_cover(&bbox);
                let area = cb.area();
                if enlarge < best_enlarge || (enlarge == best_enlarge && area < best_area) {
                    best = i;
                    best_enlarge = enlarge;
                    best_area = area;
                }
            }
            let split = insert_into(&mut children[best], bbox, payload);
            if let Some(extra) = split {
                children.push(extra);
            }
            *nb = nb.union(&bbox);
            if children.len() > MAX_ENTRIES {
                Some(split_internal(node))
            } else {
                None
            }
        }
    }
}

/// Linear pick-seeds split for an overfull leaf. Mutates `node` in
/// place to hold one group, returns the sibling holding the other.
fn split_leaf<T: Clone>(node: &mut Node<T>) -> Node<T> {
    let Node::Leaf { entries, .. } = node else {
        unreachable!("split_leaf called on non-leaf");
    };
    let taken = std::mem::take(entries);
    let (group_a, group_b) = linear_split(&taken, |e| e.0);
    let bbox_a = group_a
        .iter()
        .fold(BBox::empty(), |acc, (b, _)| acc.union(b));
    let bbox_b = group_b
        .iter()
        .fold(BBox::empty(), |acc, (b, _)| acc.union(b));
    *node = Node::Leaf {
        entries: group_a,
        bbox: bbox_a,
    };
    Node::Leaf {
        entries: group_b,
        bbox: bbox_b,
    }
}

/// Linear pick-seeds split for an overfull internal node.
fn split_internal<T: Clone>(node: &mut Node<T>) -> Node<T> {
    let Node::Internal { children, .. } = node else {
        unreachable!("split_internal called on non-internal");
    };
    let taken = std::mem::take(children);
    let (group_a, group_b) = linear_split(&taken, Node::bbox);
    let bbox_a = group_a
        .iter()
        .fold(BBox::empty(), |acc, c| acc.union(&c.bbox()));
    let bbox_b = group_b
        .iter()
        .fold(BBox::empty(), |acc, c| acc.union(&c.bbox()));
    *node = Node::Internal {
        children: group_a,
        bbox: bbox_a,
    };
    Node::Internal {
        children: group_b,
        bbox: bbox_b,
    }
}

/// Guttman 1984's "linear cost split": pick the two entries that are
/// farthest apart along some axis as seeds, then assign each remaining
/// entry to whichever seed group its bbox already grows less, with a
/// minimum-occupancy correction to keep both groups ≥ [`MIN_ENTRIES`].
fn linear_split<E: Clone, F>(items: &[E], get_bbox: F) -> (Vec<E>, Vec<E>)
where
    F: Fn(&E) -> BBox,
{
    debug_assert!(items.len() >= 2);
    // Pick seeds: the two entries whose bboxes' overall span ratio is
    // most extreme on any axis. Cheap deterministic approximation: the
    // pair with the maximum L∞ distance between bbox centers.
    let bboxes: Vec<BBox> = items.iter().map(&get_bbox).collect();
    let mut seed_a = 0usize;
    let mut seed_b = 1usize;
    let mut best = f64::NEG_INFINITY;
    for i in 0..items.len() {
        for j in (i + 1)..items.len() {
            let dx =
                ((bboxes[i].min.x + bboxes[i].max.x) - (bboxes[j].min.x + bboxes[j].max.x)).abs();
            let dy =
                ((bboxes[i].min.y + bboxes[i].max.y) - (bboxes[j].min.y + bboxes[j].max.y)).abs();
            let score = dx.max(dy);
            if score > best {
                best = score;
                seed_a = i;
                seed_b = j;
            }
        }
    }
    if seed_a == seed_b {
        seed_b = (seed_a + 1) % items.len();
    }
    let (lo, hi) = if seed_a < seed_b {
        (seed_a, seed_b)
    } else {
        (seed_b, seed_a)
    };

    let mut group_a: Vec<E> = vec![items[lo].clone()];
    let mut group_b: Vec<E> = vec![items[hi].clone()];
    let mut bbox_a = bboxes[lo];
    let mut bbox_b = bboxes[hi];

    let mut remaining: Vec<(usize, E)> = items
        .iter()
        .enumerate()
        .filter(|(i, _)| *i != lo && *i != hi)
        .map(|(i, e)| (i, e.clone()))
        .collect();

    while let Some((idx, item)) = remaining.pop() {
        let b = bboxes[idx];
        let remaining_count = remaining.len();
        // Forced-assignment rule to maintain min occupancy.
        if group_a.len() + remaining_count + 1 == MIN_ENTRIES {
            bbox_a = bbox_a.union(&b);
            group_a.push(item);
            continue;
        }
        if group_b.len() + remaining_count + 1 == MIN_ENTRIES {
            bbox_b = bbox_b.union(&b);
            group_b.push(item);
            continue;
        }
        let enlarge_a = bbox_a.enlargement_to_cover(&b);
        let enlarge_b = bbox_b.enlargement_to_cover(&b);
        let choose_a = enlarge_a < enlarge_b
            || (enlarge_a == enlarge_b
                && (bbox_a.area() < bbox_b.area()
                    || (bbox_a.area() == bbox_b.area() && group_a.len() <= group_b.len())));
        if choose_a {
            bbox_a = bbox_a.union(&b);
            group_a.push(item);
        } else {
            bbox_b = bbox_b.union(&b);
            group_b.push(item);
        }
    }

    (group_a, group_b)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::geom::BBox;
    use pretty_assertions::assert_eq;
    use proptest::prelude::*;

    fn brute_force(items: &[(BBox, u32)], query: BBox) -> Vec<u32> {
        let mut out: Vec<u32> = items
            .iter()
            .filter(|(b, _)| b.intersects(&query))
            .map(|(_, p)| *p)
            .collect();
        out.sort_unstable();
        out
    }

    fn sorted_query(tree: &RTree<u32>, query: BBox) -> Vec<u32> {
        let mut out: Vec<u32> = tree.query(query).into_iter().copied().collect();
        out.sort_unstable();
        out
    }

    /// Smoke: empty tree returns nothing on any query.
    #[test]
    fn smoke_empty_tree_returns_nothing() {
        let tree: RTree<u32> = RTree::new();
        assert!(tree.query(BBox::new(0.0, 0.0, 10.0, 10.0)).is_empty());
        assert_eq!(tree.len(), 0);
        assert!(tree.is_empty());
    }

    /// Smoke: single insert, single query that overlaps returns it.
    #[test]
    fn smoke_single_insert_single_query() {
        let mut tree: RTree<u32> = RTree::new();
        tree.insert(BBox::new(1.0, 1.0, 2.0, 2.0), 42);
        let hits = tree.query(BBox::new(0.0, 0.0, 5.0, 5.0));
        assert_eq!(hits.len(), 1);
        assert_eq!(*hits[0], 42);
    }

    /// Smoke: query that misses returns empty.
    #[test]
    fn smoke_query_miss_returns_empty() {
        let mut tree: RTree<u32> = RTree::new();
        tree.insert(BBox::new(0.0, 0.0, 1.0, 1.0), 1);
        assert!(tree.query(BBox::new(10.0, 10.0, 20.0, 20.0)).is_empty());
    }

    /// Smoke: many inserts forces multiple splits; tree still returns all
    /// of them on a query that overlaps everything.
    #[test]
    fn smoke_many_inserts_split_correctly() {
        let mut tree: RTree<u32> = RTree::new();
        for i in 0..50u32 {
            let x = f64::from(i) * 0.1;
            tree.insert(BBox::new(x, x, x + 0.05, x + 0.05), i);
        }
        let hits = tree.query(BBox::new(-100.0, -100.0, 100.0, 100.0));
        assert_eq!(hits.len(), 50);
        assert_eq!(tree.len(), 50);
    }

    proptest! {
        #![proptest_config(ProptestConfig::with_cases(8))]

        /// Integration (M0-R-07 acceptance gate): 10k-element correctness
        /// fuzz. Insert 10000 randomly placed boxes, then run 64 random
        /// queries and assert R-tree results equal brute-force results.
        #[test]
        fn integration_ten_thousand_element_correctness_fuzz(
            seed in any::<u64>(),
        ) {
            use rand::rngs::StdRng;
            use rand::{Rng, SeedableRng};

            let mut rng = StdRng::seed_from_u64(seed);
            let mut tree: RTree<u32> = RTree::new();
            let mut items: Vec<(BBox, u32)> = Vec::with_capacity(10_000);
            for i in 0..10_000u32 {
                let x: f64 = rng.gen_range(-1000.0..1000.0);
                let y: f64 = rng.gen_range(-1000.0..1000.0);
                let w: f64 = rng.gen_range(0.0..10.0);
                let h: f64 = rng.gen_range(0.0..10.0);
                let b = BBox::new(x, y, x + w, y + h);
                tree.insert(b, i);
                items.push((b, i));
            }
            prop_assert_eq!(tree.len(), 10_000);
            for _ in 0..64 {
                let qx: f64 = rng.gen_range(-1000.0..1000.0);
                let qy: f64 = rng.gen_range(-1000.0..1000.0);
                let qw: f64 = rng.gen_range(0.0..50.0);
                let qh: f64 = rng.gen_range(0.0..50.0);
                let query = BBox::new(qx, qy, qx + qw, qy + qh);
                let expected = brute_force(&items, query);
                let actual = sorted_query(&tree, query);
                prop_assert_eq!(actual, expected);
            }
        }
    }
}
