//! Spatial indexing structures.
//!
//! For M0 we ship a basic R-tree with linear node splits. Performance is
//! adequate for typical PCB scales (≤ 100k objects); a later pass can
//! swap in R*-tree or STR bulk-loading once we have benchmarks to
//! justify the complexity.

pub mod rtree;

pub use rtree::RTree;
