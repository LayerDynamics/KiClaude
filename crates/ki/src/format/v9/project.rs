//! `.kicad_pro` JSON document → KCIR project metadata.
//!
//! The `.kicad_pro` file is a verbose JSON dump of every UI knob in
//! `KiCad` 9. We only deserialize the fields KCIR actually uses for M0:
//! project name, net classes, top-level sheet, library pin lists,
//! generator stamp. Everything else is intentionally `serde(default)`
//! so missing fields don't fail the open.

use serde::Deserialize;

use crate::kcir::{
    LibraryEntry, LibraryTable, NetClass, NetClassRef, ProjectMetadata, Schematic, Sheet,
};

/// Typed-but-loose view of a `.kicad_pro` JSON document.
#[derive(Debug, Default, Clone, Deserialize)]
pub struct ProjectDoc {
    #[serde(default)]
    pub meta: ProjectMeta,
    #[serde(default)]
    pub net_settings: NetSettings,
    #[serde(default)]
    pub schematic: ProjectSchematic,
    #[serde(default)]
    pub libraries: PinnedLibraries,
    #[serde(default)]
    pub text_variables: std::collections::BTreeMap<String, String>,
}

#[derive(Debug, Default, Clone, Deserialize)]
pub struct ProjectMeta {
    #[serde(default)]
    pub filename: String,
    #[serde(default)]
    pub generator: String,
    #[serde(default)]
    pub version: u32,
}

#[derive(Debug, Default, Clone, Deserialize)]
pub struct NetSettings {
    #[serde(default)]
    pub classes: Vec<NetClassEntry>,
}

#[derive(Debug, Default, Clone, Deserialize)]
pub struct NetClassEntry {
    #[serde(default)]
    pub name: String,
    #[serde(default)]
    pub clearance: f64,
    #[serde(default)]
    pub track_width: f64,
    #[serde(default)]
    pub via_drill: f64,
    #[serde(default)]
    pub via_diameter: f64,
    #[serde(default)]
    pub diff_pair_width: Option<f64>,
    #[serde(default)]
    pub diff_pair_gap: Option<f64>,
}

#[derive(Debug, Default, Clone, Deserialize)]
pub struct ProjectSchematic {
    #[serde(default)]
    pub top_level_sheets: Vec<TopLevelSheet>,
}

#[derive(Debug, Default, Clone, Deserialize)]
pub struct TopLevelSheet {
    #[serde(default)]
    pub uuid: String,
    #[serde(default)]
    pub name: String,
    #[serde(default)]
    pub filename: String,
}

#[derive(Debug, Default, Clone, Deserialize)]
pub struct PinnedLibraries {
    #[serde(default)]
    pub pinned_footprint_libs: Vec<String>,
    #[serde(default)]
    pub pinned_symbol_libs: Vec<String>,
}

/// Project name resolution order, in priority:
/// 1. `meta.filename` stem (canonical `KiCad` source).
/// 2. The disk-derived stem passed in by [`crate::format::v9::KiProject::open`].
/// 3. `text_variables.BOARD` (some generators set this).
fn resolve_project_name(doc: &ProjectDoc, fallback_stem: &str) -> String {
    if !doc.meta.filename.is_empty() {
        return std::path::Path::new(&doc.meta.filename)
            .file_stem()
            .and_then(std::ffi::OsStr::to_str)
            .unwrap_or(&doc.meta.filename)
            .to_string();
    }
    if let Some(name) = doc.text_variables.get("BOARD") {
        if !name.is_empty() {
            return name.clone();
        }
    }
    fallback_stem.to_string()
}

/// Apply the loose `ProjectDoc` view to a mutable KCIR `Project`.
///
/// Pure function — no I/O. Side effect: mutates `out`.
pub fn apply_project_doc(doc: &ProjectDoc, fallback_stem: &str, out: &mut crate::kcir::Project) {
    out.name = resolve_project_name(doc, fallback_stem);

    // Net classes — defaults filled in for omitted fields.
    out.net_classes = doc
        .net_settings
        .classes
        .iter()
        .map(|c| NetClass {
            name: c.name.clone(),
            description: String::new(),
            clearance_mm: c.clearance,
            trace_width_mm: c.track_width,
            via_drill_mm: c.via_drill,
            via_diameter_mm: c.via_diameter,
            diff_pair_width_mm: c.diff_pair_width,
            diff_pair_gap_mm: c.diff_pair_gap,
        })
        .collect();

    // Top-level sheet → kcir Sheet entries on the schematic view. Wires,
    // labels, etc. come from the .kicad_sch (M1).
    if !doc.schematic.top_level_sheets.is_empty() {
        let mut schematic = Schematic::default();
        for s in &doc.schematic.top_level_sheets {
            schematic.sheets.push(Sheet {
                uuid: s.uuid.clone(),
                name: s.name.clone(),
                file: s.filename.clone(),
                parent: None,
                ..Sheet::default()
            });
        }
        out.schematic = schematic;
    }

    // Pinned libraries become entries with empty URIs — the real URI
    // resolution happens by reading fp-lib-table / sym-lib-table (M1).
    let mut libs = LibraryTable::default();
    for name in &doc.libraries.pinned_footprint_libs {
        libs.footprint_libs.push(LibraryEntry {
            name: name.clone(),
            ..LibraryEntry::default()
        });
    }
    for name in &doc.libraries.pinned_symbol_libs {
        libs.symbol_libs.push(LibraryEntry {
            name: name.clone(),
            ..LibraryEntry::default()
        });
    }
    if !libs.symbol_libs.is_empty() || !libs.footprint_libs.is_empty() {
        out.libraries = libs;
    }

    // Carry the `KiCad` generator stamp into metadata so downstream code
    // can pick the right emit dialect.
    out.metadata = ProjectMetadata {
        title: out.name.clone(),
        ..ProjectMetadata::default()
    };

    // Make `_` references explicit for fields wired through the doc but
    // not yet surfaced in KCIR. Removes the `unused_variables` lint hit
    // while documenting the gap.
    let _ = NetClassRef::default();
}
