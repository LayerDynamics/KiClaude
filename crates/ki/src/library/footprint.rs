//! `.kicad_mod` footprint-library indexer (M2-R-04).
//!
//! Reads an `fp-lib-table` (the user's footprint-library registry) and
//! walks each library's `.pretty/` directory — each `.kicad_mod` file
//! is one footprint — then exposes a ranked-search [`FootprintIndex`]
//! over every footprint in the resolved set. The MCP server's
//! `kc_footprint_place_hint` tool (M2-P-04) is the primary downstream
//! user, alongside the React library sidebar's footprint tab.
//!
//! The `KiCad` file shape:
//!
//! ```text
//! (footprint "R_0603_1608Metric"
//!   (version 20240108)
//!   (generator "kicad")
//!   (descr "0603 resistor, hand-soldering")
//!   (tags "resistor")
//!   (attr smd)
//!   (pad …))
//! ```
//!
//! Multiple footprints per file are *not* supported by KiCad-9, so the
//! parser only lifts the first top-level form. The `<libname>:<name>`
//! `lib_id` is constructed from the library directory's basename (minus
//! the `.pretty` suffix) and the `(footprint "Name" …)` head.

use std::collections::HashMap;
use std::fs;
use std::hash::BuildHasher;
use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};
use thiserror::Error;

use crate::format::v9::sexpr_helpers::{atom_str, body_children, find_child, find_children};
use crate::sexpr::{parse_str, ParseError, SNode};

use super::lib_table::{resolve_uri, LibraryRow, SymLibTable};

/// One footprint extracted from a `.kicad_mod` file.
#[derive(Debug, Clone, Default, PartialEq, Serialize, Deserialize)]
pub struct FootprintEntry {
    /// The footprint's bare name (head of the `(footprint "Name" …)`
    /// form). The fully-qualified `lib_id` callers want is
    /// `"<libname>:<name>"`.
    pub name: String,
    /// `(descr "…")` — human-readable summary.
    pub description: String,
    /// `(tags "…")` — space-separated keywords used by the picker.
    pub tags: String,
    /// `(attr smd|through_hole|exclude_from_pos_files|exclude_from_bom)`
    /// space-joined into a single string for serialization.
    pub attributes: String,
    /// `KiCad`'s schema stamp on this footprint (rarely interesting
    /// downstream but preserved for round-trip).
    pub version: u32,
    /// `(generator "<tool>")` field — usually `"kicad"` or
    /// `"kicad_footprint_editor"`.
    pub generator: String,
    /// Pad count (pre-aggregated so the picker doesn't have to re-parse).
    pub pad_count: u32,
    /// Footprint width × height in mm (the courtyard bounding box if
    /// present, otherwise the pad bounding box, otherwise `(0.0, 0.0)`).
    pub size_mm: (f64, f64),
}

impl FootprintEntry {
    /// `true` when the footprint is annotated as SMD.
    #[must_use]
    pub fn is_smd(&self) -> bool {
        self.attributes.split_whitespace().any(|t| t == "smd")
    }

    /// `true` when the footprint is annotated as through-hole.
    #[must_use]
    pub fn is_through_hole(&self) -> bool {
        self.attributes
            .split_whitespace()
            .any(|t| t == "through_hole")
    }
}

/// Errors raised by [`parse_footprint_file`].
#[derive(Debug, Error)]
pub enum FootprintParseError {
    #[error("I/O error reading {path}: {source}")]
    Io {
        path: PathBuf,
        #[source]
        source: std::io::Error,
    },
    #[error("invalid S-expression in {path}: {source}")]
    InvalidSexpr {
        path: PathBuf,
        #[source]
        source: ParseError,
    },
    #[error("S-expression in {path} has no top-level form")]
    Empty { path: PathBuf },
    #[error("top-level form in {path} is not `(footprint …)`")]
    NotFootprint { path: PathBuf },
}

/// Parse a single `.kicad_mod` from disk.
///
/// # Errors
/// Returns [`FootprintParseError`] on I/O failure, malformed
/// S-expression, or non-footprint top-level form.
pub fn parse_footprint_file(path: &Path) -> Result<FootprintEntry, FootprintParseError> {
    let text = fs::read_to_string(path).map_err(|source| FootprintParseError::Io {
        path: path.to_path_buf(),
        source,
    })?;
    parse_footprint_text(&text).map_err(|err| with_path(err, path))
}

/// Parse a footprint from a string (test + wasm entry point).
///
/// # Errors
/// Returns [`FootprintParseError`] when the form is malformed or its
/// head isn't `(footprint …)`. The `path` field on returned variants
/// is left empty — use [`parse_footprint_file`] for real provenance.
pub fn parse_footprint_text(text: &str) -> Result<FootprintEntry, FootprintParseError> {
    let nodes = parse_str(text).map_err(|source| FootprintParseError::InvalidSexpr {
        path: PathBuf::new(),
        source,
    })?;
    let root = nodes.into_iter().next().ok_or(FootprintParseError::Empty {
        path: PathBuf::new(),
    })?;
    if root.head_symbol() != Some("footprint") {
        return Err(FootprintParseError::NotFootprint {
            path: PathBuf::new(),
        });
    }
    Ok(map_footprint(&root))
}

fn with_path(err: FootprintParseError, path: &Path) -> FootprintParseError {
    match err {
        FootprintParseError::Io { source, .. } => FootprintParseError::Io {
            path: path.to_path_buf(),
            source,
        },
        FootprintParseError::InvalidSexpr { source, .. } => FootprintParseError::InvalidSexpr {
            path: path.to_path_buf(),
            source,
        },
        FootprintParseError::Empty { .. } => FootprintParseError::Empty {
            path: path.to_path_buf(),
        },
        FootprintParseError::NotFootprint { .. } => FootprintParseError::NotFootprint {
            path: path.to_path_buf(),
        },
    }
}

fn map_footprint(root: &SNode) -> FootprintEntry {
    let name = body_children(root)
        .next()
        .and_then(atom_str)
        .unwrap_or("")
        .to_string();
    let description = find_child(root, "descr")
        .and_then(|n| body_children(n).next())
        .and_then(atom_str)
        .unwrap_or("")
        .to_string();
    let tags = find_child(root, "tags")
        .and_then(|n| body_children(n).next())
        .and_then(atom_str)
        .unwrap_or("")
        .to_string();
    let attributes = find_child(root, "attr")
        .map(|n| {
            body_children(n)
                .filter_map(atom_str)
                .collect::<Vec<_>>()
                .join(" ")
        })
        .unwrap_or_default();
    let version = find_child(root, "version")
        .and_then(|n| body_children(n).next())
        .and_then(atom_str)
        .and_then(|s| s.parse::<u32>().ok())
        .unwrap_or(0);
    let generator = find_child(root, "generator")
        .and_then(|n| body_children(n).next())
        .and_then(atom_str)
        .unwrap_or("")
        .to_string();

    let pads = find_children(root, "pad");
    let pad_count = u32::try_from(pads.len()).unwrap_or(u32::MAX);

    // Bounding box: prefer the courtyard polygons (fp_line on F.CrtYd
    // / B.CrtYd) since they define the placement extent; fall back to
    // pads if no courtyard exists.
    let size_mm = compute_size(root, &pads);

    FootprintEntry {
        name,
        description,
        tags,
        attributes,
        version,
        generator,
        pad_count,
        size_mm,
    }
}

fn compute_size(root: &SNode, pads: &[&SNode]) -> (f64, f64) {
    let mut min = (f64::INFINITY, f64::INFINITY);
    let mut max = (f64::NEG_INFINITY, f64::NEG_INFINITY);
    let mut have_any = false;

    let courtyard_pts = courtyard_points(root);
    if courtyard_pts.is_empty() {
        for pad in pads {
            let (x, y) = pad_position(pad);
            let (w, h) = pad_size(pad);
            min.0 = min.0.min(x - w / 2.0);
            min.1 = min.1.min(y - h / 2.0);
            max.0 = max.0.max(x + w / 2.0);
            max.1 = max.1.max(y + h / 2.0);
            have_any = true;
        }
    } else {
        for (x, y) in &courtyard_pts {
            min.0 = min.0.min(*x);
            min.1 = min.1.min(*y);
            max.0 = max.0.max(*x);
            max.1 = max.1.max(*y);
            have_any = true;
        }
    }

    if !have_any {
        return (0.0, 0.0);
    }
    ((max.0 - min.0).max(0.0), (max.1 - min.1).max(0.0))
}

fn courtyard_points(root: &SNode) -> Vec<(f64, f64)> {
    let mut points = Vec::new();
    for line in find_children(root, "fp_line") {
        if !line_on_courtyard(line) {
            continue;
        }
        if let Some(start) = find_child(line, "start") {
            points.push(point_from(start));
        }
        if let Some(end) = find_child(line, "end") {
            points.push(point_from(end));
        }
    }
    for poly in find_children(root, "fp_poly") {
        if !line_on_courtyard(poly) {
            continue;
        }
        if let Some(pts) = find_child(poly, "pts") {
            for xy in body_children(pts) {
                if matches!(xy.head_symbol(), Some("xy")) {
                    let body: Vec<&SNode> = body_children(xy).collect();
                    let x = body
                        .first()
                        .and_then(|n| atom_str(n))
                        .and_then(|s| s.parse::<f64>().ok())
                        .unwrap_or(0.0);
                    let y = body
                        .get(1)
                        .and_then(|n| atom_str(n))
                        .and_then(|s| s.parse::<f64>().ok())
                        .unwrap_or(0.0);
                    points.push((x, y));
                }
            }
        }
    }
    points
}

fn line_on_courtyard(form: &SNode) -> bool {
    find_child(form, "layer")
        .and_then(|n| body_children(n).next())
        .and_then(atom_str)
        .is_some_and(|s| s == "F.CrtYd" || s == "B.CrtYd")
}

fn pad_position(pad: &SNode) -> (f64, f64) {
    find_child(pad, "at").map_or((0.0, 0.0), |n| {
        let body: Vec<&SNode> = body_children(n).collect();
        let x = body
            .first()
            .and_then(|n| atom_str(n))
            .and_then(|s| s.parse::<f64>().ok())
            .unwrap_or(0.0);
        let y = body
            .get(1)
            .and_then(|n| atom_str(n))
            .and_then(|s| s.parse::<f64>().ok())
            .unwrap_or(0.0);
        (x, y)
    })
}

fn pad_size(pad: &SNode) -> (f64, f64) {
    find_child(pad, "size").map_or((0.0, 0.0), |n| {
        let body: Vec<&SNode> = body_children(n).collect();
        let w = body
            .first()
            .and_then(|n| atom_str(n))
            .and_then(|s| s.parse::<f64>().ok())
            .unwrap_or(0.0);
        let h = body
            .get(1)
            .and_then(|n| atom_str(n))
            .and_then(|s| s.parse::<f64>().ok())
            .unwrap_or(0.0);
        (w, h)
    })
}

fn point_from(form: &SNode) -> (f64, f64) {
    let body: Vec<&SNode> = body_children(form).collect();
    let x = body
        .first()
        .and_then(|n| atom_str(n))
        .and_then(|s| s.parse::<f64>().ok())
        .unwrap_or(0.0);
    let y = body
        .get(1)
        .and_then(|n| atom_str(n))
        .and_then(|s| s.parse::<f64>().ok())
        .unwrap_or(0.0);
    (x, y)
}

// ---------------------------------------------------------------------
// Index
// ---------------------------------------------------------------------

#[derive(Debug, Clone, PartialEq)]
struct IndexedFootprint {
    lib_id: String,
    library_name: String,
    source_path: Option<PathBuf>,
    entry: FootprintEntry,
    haystack: String,
}

/// One hit from [`FootprintIndex::search`].
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct FootprintHit {
    pub lib_id: String,
    pub library: String,
    pub name: String,
    pub description: String,
    pub tags: String,
    pub attributes: String,
    pub pad_count: u32,
    pub size_mm: (f64, f64),
    pub score: f32,
}

/// A non-fatal error from [`FootprintIndex::from_lib_table`].
#[derive(Debug, Clone)]
pub struct FootprintLoadError {
    pub library: String,
    pub uri: String,
    pub message: String,
}

/// Searchable index over a resolved set of `.kicad_mod` footprints.
#[derive(Debug, Clone, Default)]
pub struct FootprintIndex {
    footprints: Vec<IndexedFootprint>,
    libraries: HashMap<String, LibraryRow>,
    library_paths: HashMap<String, PathBuf>,
    errors: Vec<FootprintLoadError>,
}

impl FootprintIndex {
    /// Build an empty index.
    #[must_use]
    pub fn new() -> Self {
        Self::default()
    }

    /// Build an index by resolving every (non-disabled) row in
    /// `table` and walking each library's `.pretty/` directory.
    ///
    /// `overrides` lets callers swap test-fixture paths for the
    /// `${KICAD9_FOOTPRINT_DIR}` / `${KIPROJMOD}` variables `KiCad` uses
    /// in its installed table.
    #[must_use]
    pub fn from_lib_table<S: BuildHasher>(
        table: &SymLibTable,
        overrides: &HashMap<String, String, S>,
    ) -> Self {
        let mut index = Self::new();
        for row in &table.libraries {
            if row.disabled {
                continue;
            }
            let resolved = resolve_uri(&row.uri, overrides);
            let path = PathBuf::from(&resolved);
            match index.add_pretty_dir(&row.name, &path) {
                Ok(_) => {
                    index.libraries.insert(row.name.clone(), row.clone());
                    index.library_paths.insert(row.name.clone(), path);
                }
                Err(message) => index.errors.push(FootprintLoadError {
                    library: row.name.clone(),
                    uri: resolved,
                    message,
                }),
            }
        }
        index
    }

    /// Index every `.kicad_mod` under `dir`. Returns the count of
    /// successfully indexed footprints; per-file errors are pushed to
    /// the index's `errors` list rather than aborting the whole walk.
    ///
    /// # Errors
    /// Returns an error string if the directory itself doesn't exist
    /// or can't be read.
    pub fn add_pretty_dir(&mut self, library_name: &str, dir: &Path) -> Result<usize, String> {
        let read = fs::read_dir(dir).map_err(|e| format!("read_dir {}: {e}", dir.display()))?;
        let mut entries: Vec<_> = read.filter_map(Result::ok).collect();
        entries.sort_by_key(std::fs::DirEntry::path);
        let mut indexed = 0usize;
        for entry in entries {
            let path = entry.path();
            if path.extension().and_then(|s| s.to_str()) != Some("kicad_mod") {
                continue;
            }
            match parse_footprint_file(&path) {
                Ok(fp) => {
                    self.add_footprint(library_name, fp, Some(path));
                    indexed += 1;
                }
                Err(err) => self.errors.push(FootprintLoadError {
                    library: library_name.to_string(),
                    uri: path.display().to_string(),
                    message: err.to_string(),
                }),
            }
        }
        Ok(indexed)
    }

    /// Fold a single parsed [`FootprintEntry`] in. Useful for tests
    /// and the in-memory cache path on the MCP side.
    pub fn add_footprint(
        &mut self,
        library_name: &str,
        entry: FootprintEntry,
        source_path: Option<PathBuf>,
    ) {
        let lib_id = format!("{library_name}:{}", entry.name);
        let haystack = format!(
            "{} {} {} {}",
            entry.name.to_ascii_lowercase(),
            entry.tags.to_ascii_lowercase(),
            entry.description.to_ascii_lowercase(),
            entry.attributes.to_ascii_lowercase(),
        );
        self.footprints.push(IndexedFootprint {
            lib_id,
            library_name: library_name.to_string(),
            source_path,
            entry,
            haystack,
        });
    }

    #[must_use]
    pub fn len(&self) -> usize {
        self.footprints.len()
    }

    #[must_use]
    pub fn is_empty(&self) -> bool {
        self.footprints.is_empty()
    }

    /// Errors collected during load (non-fatal).
    #[must_use]
    pub fn errors(&self) -> &[FootprintLoadError] {
        &self.errors
    }

    /// Library short-name → originating row.
    #[must_use]
    pub fn libraries(&self) -> &HashMap<String, LibraryRow> {
        &self.libraries
    }

    /// Library short-name → on-disk path (when known).
    #[must_use]
    pub fn library_paths(&self) -> &HashMap<String, PathBuf> {
        &self.library_paths
    }

    /// Ranked pattern search. The acceptance probe is
    /// `search_by_pattern("USB-C", 20)` returning a `Connector:*`
    /// USB-C receptacle hit at the top.
    ///
    /// Scoring weights (mirrors M1 symbol indexer):
    /// - name exact:        +1.5
    /// - name prefix:       +1.0
    /// - name substring:    +0.7
    /// - tags substring:    +0.4
    /// - descr substring:   +0.2
    /// - attr substring:    +0.1
    /// - through-hole bonus when caller queries with a `_TH` /
    ///   `_THT` token (kept inside `score_match`).
    #[must_use]
    pub fn search_by_pattern(&self, pattern: &str, limit: usize) -> Vec<FootprintHit> {
        let needle = pattern.trim().to_ascii_lowercase();
        let mut hits: Vec<FootprintHit> = self
            .footprints
            .iter()
            .filter_map(|fp| {
                let score = score_match(fp, &needle);
                if needle.is_empty() || score > 0.0 {
                    Some(hit_from(fp, score))
                } else {
                    None
                }
            })
            .collect();
        hits.sort_by(|a, b| {
            b.score
                .partial_cmp(&a.score)
                .unwrap_or(std::cmp::Ordering::Equal)
                .then_with(|| a.lib_id.cmp(&b.lib_id))
        });
        hits.truncate(limit);
        hits
    }
}

fn score_match(fp: &IndexedFootprint, needle_lower: &str) -> f32 {
    if needle_lower.is_empty() {
        return 0.0;
    }
    let name_l = fp.entry.name.to_ascii_lowercase();
    let tags_l = fp.entry.tags.to_ascii_lowercase();
    let descr_l = fp.entry.description.to_ascii_lowercase();
    let attr_l = fp.entry.attributes.to_ascii_lowercase();

    let mut score = 0.0f32;
    if name_l == needle_lower {
        score += 1.5;
    } else if name_l.starts_with(needle_lower) {
        score += 1.0;
    } else if name_l.contains(needle_lower) {
        score += 0.7;
    }
    if tags_l.contains(needle_lower) {
        score += 0.4;
    }
    if descr_l.contains(needle_lower) {
        score += 0.2;
    }
    if attr_l.contains(needle_lower) {
        score += 0.1;
    }
    // Caller-side hint: if they asked for `_th` / `_tht`, prefer
    // through-hole. If they asked for `smd`, prefer SMD.
    if (needle_lower.contains("_th") || needle_lower.contains("tht")) && fp.entry.is_through_hole()
    {
        score += 0.3;
    }
    if needle_lower.contains("smd") && fp.entry.is_smd() {
        score += 0.3;
    }
    score.min(2.5)
}

fn hit_from(fp: &IndexedFootprint, score: f32) -> FootprintHit {
    FootprintHit {
        lib_id: fp.lib_id.clone(),
        library: fp.library_name.clone(),
        name: fp.entry.name.clone(),
        description: fp.entry.description.clone(),
        tags: fp.entry.tags.clone(),
        attributes: fp.entry.attributes.clone(),
        pad_count: fp.entry.pad_count,
        size_mm: fp.entry.size_mm,
        score,
    }
}

// ---------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;

    const R_0603: &str = r#"(footprint "R_0603_1608Metric"
  (version 20240108)
  (generator "kicad")
  (descr "Resistor SMD 0603 (1608 Metric), hand-soldering")
  (tags "resistor 0603 1608Metric")
  (attr smd)
  (fp_line (start -1.7 -0.9) (end 1.7 -0.9) (stroke (width 0.05) (type solid)) (layer "F.CrtYd"))
  (fp_line (start -1.7 0.9) (end 1.7 0.9) (stroke (width 0.05) (type solid)) (layer "F.CrtYd"))
  (fp_line (start -1.7 -0.9) (end -1.7 0.9) (stroke (width 0.05) (type solid)) (layer "F.CrtYd"))
  (fp_line (start 1.7 -0.9) (end 1.7 0.9) (stroke (width 0.05) (type solid)) (layer "F.CrtYd"))
  (pad "1" smd roundrect (at -0.8 0) (size 0.95 0.95) (layers "F.Cu" "F.Mask"))
  (pad "2" smd roundrect (at 0.8 0) (size 0.95 0.95) (layers "F.Cu" "F.Mask"))
)
"#;

    const USB_C: &str = r#"(footprint "USB_C_Receptacle_GCT_USB4105-xx-A_16P_TopMnt"
  (version 20240108)
  (generator "kicad_footprint_editor")
  (descr "USB Type C Receptacle 16P SMD")
  (tags "usb usb-c connector")
  (attr smd exclude_from_pos_files)
  (pad "A1" smd roundrect (at -4.25 0) (size 0.3 1.65) (layers "F.Cu" "F.Mask"))
  (pad "A2" smd roundrect (at -3.75 0) (size 0.3 1.65) (layers "F.Cu" "F.Mask"))
)
"#;

    const PIN_HEADER_TH: &str = r#"(footprint "PinHeader_1x04_P2.54mm_Vertical"
  (version 20240108)
  (descr "Through hole pin header, 1x04, 2.54mm pitch")
  (tags "Through hole pin header THT 1x04 P2.54mm")
  (attr through_hole)
  (pad "1" thru_hole circle (at 0 0) (size 1.7 1.7) (drill 1.0) (layers "*.Cu" "*.Mask"))
)
"#;

    fn seed_index() -> FootprintIndex {
        let mut idx = FootprintIndex::new();
        idx.add_footprint(
            "Resistor_SMD",
            parse_footprint_text(R_0603).expect("R_0603 parse"),
            None,
        );
        idx.add_footprint(
            "Connector_USB",
            parse_footprint_text(USB_C).expect("USB_C parse"),
            None,
        );
        idx.add_footprint(
            "Connector_PinHeader_2.54mm",
            parse_footprint_text(PIN_HEADER_TH).expect("pin header parse"),
            None,
        );
        idx
    }

    #[test]
    fn parses_descr_tags_and_attr() {
        let fp = parse_footprint_text(R_0603).expect("parse");
        assert_eq!(fp.name, "R_0603_1608Metric");
        assert!(fp.description.contains("0603"));
        assert!(fp.tags.contains("resistor"));
        assert_eq!(fp.attributes, "smd");
        assert!(fp.is_smd());
        assert!(!fp.is_through_hole());
        assert_eq!(fp.pad_count, 2);
    }

    #[test]
    fn computes_courtyard_size() {
        let fp = parse_footprint_text(R_0603).expect("parse");
        let (w, h) = fp.size_mm;
        // 1.7 - (-1.7) = 3.4 mm wide, 0.9 - (-0.9) = 1.8 mm tall.
        assert!((w - 3.4).abs() < 1e-9, "{w}");
        assert!((h - 1.8).abs() < 1e-9, "{h}");
    }

    #[test]
    fn falls_back_to_pad_bbox_when_no_courtyard() {
        let fp = parse_footprint_text(PIN_HEADER_TH).expect("parse");
        let (w, h) = fp.size_mm;
        // Single 1.7×1.7 pad — bounding box is the pad itself.
        assert!((w - 1.7).abs() < 1e-9, "{w}");
        assert!((h - 1.7).abs() < 1e-9, "{h}");
    }

    #[test]
    fn parses_smd_with_extra_attr_flags() {
        let fp = parse_footprint_text(USB_C).expect("parse");
        assert!(fp.is_smd());
        assert!(fp.attributes.contains("exclude_from_pos_files"));
    }

    #[test]
    fn rejects_non_footprint_root() {
        let err = parse_footprint_text("(kicad_pcb (version 1))").expect_err("must fail");
        assert!(matches!(err, FootprintParseError::NotFootprint { .. }));
    }

    #[test]
    fn search_by_pattern_usb_c_matches() {
        let idx = seed_index();
        let hits = idx.search_by_pattern("USB-C", 5);
        // USB-C uses a dash; the connector library uses `USB_C` in its
        // name plus `usb-c` in tags. Tag substring covers the query.
        assert!(!hits.is_empty(), "no USB-C hits");
        assert_eq!(hits[0].library, "Connector_USB");
    }

    #[test]
    fn search_by_pattern_0603_matches_first() {
        let idx = seed_index();
        let hits = idx.search_by_pattern("0603", 5);
        assert!(!hits.is_empty(), "no 0603 hits");
        assert!(hits[0].name.contains("0603"));
    }

    #[test]
    fn search_by_pattern_empty_returns_all() {
        let idx = seed_index();
        let hits = idx.search_by_pattern("", 100);
        assert_eq!(hits.len(), 3);
    }

    #[test]
    fn through_hole_hint_lifts_tht_results() {
        let idx = seed_index();
        let hits = idx.search_by_pattern("thT", 5);
        assert!(!hits.is_empty());
        assert!(hits[0].name.contains("PinHeader"));
    }

    #[test]
    fn add_pretty_dir_walks_filesystem() {
        let tmp = tempfile::tempdir().expect("tmpdir");
        fs::write(tmp.path().join("R_0603.kicad_mod"), R_0603).expect("write");
        fs::write(tmp.path().join("USB_C.kicad_mod"), USB_C).expect("write");
        // A garbage file is ignored but recorded as an error.
        fs::write(tmp.path().join("bad.kicad_mod"), "(not_footprint)").expect("write bad");

        let mut idx = FootprintIndex::new();
        let n = idx.add_pretty_dir("MyLib", tmp.path()).expect("dir walk");
        assert_eq!(n, 2);
        assert_eq!(idx.len(), 2);
        assert_eq!(idx.errors().len(), 1);
        assert!(idx.errors()[0].message.contains("not `(footprint …)`"));
    }

    #[test]
    fn add_pretty_dir_returns_error_for_missing_directory() {
        let mut idx = FootprintIndex::new();
        let err = idx
            .add_pretty_dir("X", Path::new("/nonexistent/kiclaude/m2r04"))
            .expect_err("must fail");
        assert!(err.contains("read_dir"));
    }
}
