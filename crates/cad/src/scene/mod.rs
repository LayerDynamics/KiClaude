//! Editable scene with a spatial index for live DRC + selection.
//!
//! The React PCB editor manipulates a *scene* — the set of geometric
//! items currently on the board (tracks, vias, pads, courtyards).
//! Two queries dominate the editor's hot path:
//!
//! 1. **DRC broad-phase** — given a clearance, what pairs of items
//!    have bboxes within `bbox_inflate_mm` of each other? This is the
//!    candidate set the DRC kernel narrows in its O(n²)-worst-case
//!    pair-distance computations. The R-tree turns the candidate
//!    generation into O(n log n + k).
//! 2. **Point-query for selection** — when the user clicks at
//!    `(x, y)` in mm, which items' bboxes contain that point? Used by
//!    the editor's selection tool.
//!
//! Both queries route through this `Scene` type. It owns:
//! - a flat `HashMap<ItemId, SceneItem>` (the authoritative state)
//! - a lazy [`crate::index::RTree<ItemId>`] keyed on item bboxes
//!
//! Updates (insert / update / remove) mutate the hashmap and mark the
//! index dirty; the next query rebuilds the index from scratch. For
//! editor-scale boards (≤ ~5000 items) the rebuild is ≤ a few
//! milliseconds.

pub mod spatial;

pub use spatial::{ItemId, Scene, SceneItem};
