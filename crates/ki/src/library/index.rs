//! Searchable [`Index`] over a resolved set of `.kicad_sym` libraries.
//!
//! Construction:
//! - [`Index::from_lib_table`] reads a [`super::SymLibTable`], resolves
//!   each library's URI through environment variables + caller-supplied
//!   overrides, parses every `.kicad_sym` file, and stores every
//!   symbol under its `<libname>:<symbol>` `lib_id`.
//! - [`Index::add_library`] folds a single already-parsed
//!   [`super::SymbolLib`] in (handy for tests and the cache path in
//!   M1-P-02).
//!
//! Search: [`Index::search`] scores every indexed symbol against the
//! query (case-insensitive substring match across name, description,
//! keywords) and returns the top hits sorted by score. The plan calls
//! out `Index::search("STM32G0")` as the M1-R-04 acceptance probe.

use std::collections::HashMap;
use std::hash::BuildHasher;
use std::path::PathBuf;

use serde::{Deserialize, Serialize};

use super::lib_table::{resolve_uri, LibraryRow, SymLibTable};
use super::symbol::{parse_symbol_lib, LibParseError, LibSymbolEntry, SymbolLib};

/// One indexed symbol plus the library it came from.
#[derive(Debug, Clone, PartialEq, Eq)]
struct IndexedSymbol {
    /// `<libname>:<symbol-name>` — what callers pass back as `lib_id`.
    lib_id: String,
    /// The originating library row (for the descr / kind columns).
    library_name: String,
    /// The parsed `.kicad_sym` entry.
    entry: LibSymbolEntry,
    /// Pre-lowered version of the searchable haystack
    /// (`name keywords description value`) for fast scoring.
    haystack: String,
}

/// A single result row from [`Index::search`].
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct SearchHit {
    /// `<libname>:<symbol-name>`.
    pub lib_id: String,
    /// Symbol name without the library prefix.
    pub name: String,
    /// Originating library short name.
    pub library: String,
    /// `Description` property (may be empty).
    pub description: String,
    /// `ki_fp_filters` — footprint patterns the picker should preferr.
    pub footprint_filter: String,
    /// Default refdes prefix (`"R"`, `"U"`, …).
    pub reference: String,
    /// Default value field.
    pub value: String,
    /// Default footprint `lib_id` (may be empty).
    pub footprint: String,
    /// Datasheet URL (may be empty).
    pub datasheet: String,
    /// MPN if the library curates one (may be empty).
    pub mpn: String,
    /// Whether the symbol is a power-net marker (filtered out of
    /// the regular component picker).
    pub is_power: bool,
    /// Match score, in `[0.0, 1.0]`. Higher is better. Returned so
    /// downstream UI can colour-rank results.
    pub score: f32,
}

/// Searchable index over a resolved set of `.kicad_sym` libraries.
#[derive(Debug, Clone, Default)]
pub struct Index {
    symbols: Vec<IndexedSymbol>,
    /// Library short name → originating row (carries `descr`, `kind`).
    libraries: HashMap<String, LibraryRow>,
    /// Per-library file path (when known) — useful for tooling that
    /// wants to point at the source `.kicad_sym`.
    library_paths: HashMap<String, PathBuf>,
    /// Errors encountered while loading; non-fatal so the index still
    /// indexes whatever libraries did load cleanly.
    errors: Vec<LoadError>,
}

/// Per-library load failure surfaced by [`Index::from_lib_table`].
#[derive(Debug, Clone)]
pub struct LoadError {
    pub library: String,
    pub uri: String,
    pub message: String,
}

impl Index {
    /// Build an empty index.
    #[must_use]
    pub fn new() -> Self {
        Self::default()
    }

    /// Build an index by resolving every (non-disabled) row in
    /// `table`, parsing the matching `.kicad_sym` file, and indexing
    /// every symbol in declaration order.
    ///
    /// `overrides` lets callers swap in test-fixture paths for the
    /// `${KICAD9_SYMBOL_DIR}` / `${KIPROJMOD}` style variables `KiCad`
    /// uses in its installed library table.
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
            match parse_symbol_lib(&path) {
                Ok(lib) => {
                    index.add_library(&row.name, &lib, Some(path.clone()), Some(row.clone()));
                }
                Err(err) => index.errors.push(LoadError {
                    library: row.name.clone(),
                    uri: resolved.clone(),
                    message: format_lib_err(&err),
                }),
            }
        }
        index
    }

    /// Fold an already-parsed [`SymbolLib`] into the index.
    ///
    /// `library_name` becomes the `<libname>` portion of every entry's
    /// `lib_id`. `source_path` and `row` are optional — they let
    /// callers attach provenance for downstream UI without forcing
    /// every code path to construct full [`LibraryRow`]s.
    pub fn add_library(
        &mut self,
        library_name: &str,
        lib: &SymbolLib,
        source_path: Option<PathBuf>,
        row: Option<LibraryRow>,
    ) {
        if let Some(r) = row {
            self.libraries.insert(library_name.to_string(), r);
        }
        if let Some(p) = source_path {
            self.library_paths.insert(library_name.to_string(), p);
        }
        for entry in &lib.symbols {
            let lib_id = format!("{library_name}:{}", entry.name);
            let haystack = format!(
                "{} {} {} {} {}",
                entry.name.to_ascii_lowercase(),
                entry.keywords.to_ascii_lowercase(),
                entry.description.to_ascii_lowercase(),
                entry.value.to_ascii_lowercase(),
                entry.mpn.to_ascii_lowercase(),
            );
            self.symbols.push(IndexedSymbol {
                lib_id,
                library_name: library_name.to_string(),
                entry: entry.clone(),
                haystack,
            });
        }
    }

    /// Returns the number of indexed symbols.
    #[must_use]
    pub fn len(&self) -> usize {
        self.symbols.len()
    }

    /// True if no symbols are indexed.
    #[must_use]
    pub fn is_empty(&self) -> bool {
        self.symbols.is_empty()
    }

    /// Errors collected during the most recent [`Self::from_lib_table`] call.
    #[must_use]
    pub fn errors(&self) -> &[LoadError] {
        &self.errors
    }

    /// Ranked search over the indexed symbols.
    ///
    /// Returns up to `limit` hits sorted by descending score. Scoring
    /// weights:
    /// - `name` exact prefix match: +1.0
    /// - `name` substring match: +0.7
    /// - `keywords` substring match: +0.4
    /// - `description` substring match: +0.2
    /// - `value` / `mpn` substring match: +0.15 each
    ///
    /// Empty queries return every symbol in declaration order with
    /// score 0.0.
    #[must_use]
    pub fn search(&self, query: &str, limit: usize) -> Vec<SearchHit> {
        let needle = query.trim().to_ascii_lowercase();
        let mut hits: Vec<SearchHit> = self
            .symbols
            .iter()
            .filter_map(|s| {
                let score = score_match(s, &needle);
                if needle.is_empty() || score > 0.0 {
                    Some(hit_from_indexed(s, score))
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

fn score_match(s: &IndexedSymbol, needle_lower: &str) -> f32 {
    if needle_lower.is_empty() {
        return 0.0;
    }
    let name_l = s.entry.name.to_ascii_lowercase();
    let keywords_l = s.entry.keywords.to_ascii_lowercase();
    let descr_l = s.entry.description.to_ascii_lowercase();
    let value_l = s.entry.value.to_ascii_lowercase();
    let mpn_l = s.entry.mpn.to_ascii_lowercase();

    let mut score = 0.0f32;
    if name_l == needle_lower {
        score += 1.5;
    } else if name_l.starts_with(needle_lower) {
        score += 1.0;
    } else if name_l.contains(needle_lower) {
        score += 0.7;
    }
    if keywords_l.contains(needle_lower) {
        score += 0.4;
    }
    if descr_l.contains(needle_lower) {
        score += 0.2;
    }
    if value_l.contains(needle_lower) {
        score += 0.15;
    }
    if mpn_l.contains(needle_lower) {
        score += 0.15;
    }
    // Penalise power-net markers so they don't push regular symbols
    // off the top of the results.
    if s.entry.is_power {
        score *= 0.5;
    }
    score.min(2.0)
}

fn hit_from_indexed(s: &IndexedSymbol, score: f32) -> SearchHit {
    SearchHit {
        lib_id: s.lib_id.clone(),
        name: s.entry.name.clone(),
        library: s.library_name.clone(),
        description: s.entry.description.clone(),
        footprint_filter: s.entry.footprint_filter.clone(),
        reference: s.entry.reference.clone(),
        value: s.entry.value.clone(),
        footprint: s.entry.footprint.clone(),
        datasheet: s.entry.datasheet.clone(),
        mpn: s.entry.mpn.clone(),
        is_power: s.entry.is_power,
        score,
    }
}

fn format_lib_err(err: &LibParseError) -> String {
    format!("{err}")
}
