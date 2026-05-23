//! Top-level KCIR [`Project`] and its direct children.

use serde::{Deserialize, Serialize};

use super::{BomPolicy, DesignRules, FabTarget, NetClass, Pcb, Schematic, Stackup};

/// A kiclaude project — the in-memory equivalent of a `KiCad` project on
/// disk (`.kicad_pro` + `.kicad_sch` + `.kicad_pcb` + library tables).
///
/// See `docs/specs/SPEC-01-kiclaude.md` §7.2 for field semantics.
#[cfg_attr(feature = "ts-export", derive(ts_rs::TS))]
#[cfg_attr(feature = "ts-export", ts(export))]
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Project {
    /// KCIR schema version (semver string). See `KCIR_VERSION` in `lib.rs`.
    pub kcir_version: String,
    /// Human-readable project name. Mirrors the `.kicad_pro` `meta.filename`
    /// stem and the schematic title block.
    pub name: String,
    /// Schematic view (multi-sheet hierarchy of symbols, wires, labels).
    pub schematic: Schematic,
    /// PCB view (footprints, tracks, vias, zones, layers).
    pub pcb: Pcb,
    /// Resolved library table for symbols + footprints.
    pub libraries: LibraryTable,
    /// Physical layer stackup.
    pub stackup: Stackup,
    /// Design rules (clearance, trace widths, drill sizes).
    pub design_rules: DesignRules,
    /// Per-class net constraints. Mirrors `(net_class …)` in `.kicad_pcb`.
    pub net_classes: Vec<NetClass>,
    /// Selected fab target preset, if any.
    pub fab_target: Option<FabTarget>,
    /// BOM sourcing policy.
    pub bom_policy: BomPolicy,
    /// Title block + free-form metadata.
    pub metadata: ProjectMetadata,
}

impl Default for Project {
    fn default() -> Self {
        Self {
            kcir_version: crate::KCIR_VERSION.to_string(),
            name: String::new(),
            schematic: Schematic::default(),
            pcb: Pcb::default(),
            libraries: LibraryTable::default(),
            stackup: Stackup::default(),
            design_rules: DesignRules::default(),
            net_classes: Vec::new(),
            fab_target: None,
            bom_policy: BomPolicy::default(),
            metadata: ProjectMetadata::default(),
        }
    }
}

impl Project {
    /// Build a project with a given name and otherwise default state.
    #[must_use]
    pub fn new(name: impl Into<String>) -> Self {
        Self {
            name: name.into(),
            ..Self::default()
        }
    }
}

/// `KiCad`-style title block plus extension fields.
#[cfg_attr(feature = "ts-export", derive(ts_rs::TS))]
#[cfg_attr(feature = "ts-export", ts(export))]
#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct ProjectMetadata {
    pub title: String,
    pub revision: String,
    pub company: String,
    pub date: String,
    pub comment_1: String,
    pub comment_2: String,
    pub comment_3: String,
    pub comment_4: String,
}

/// Aggregated library table (symbol libs + footprint libs).
#[cfg_attr(feature = "ts-export", derive(ts_rs::TS))]
#[cfg_attr(feature = "ts-export", ts(export))]
#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct LibraryTable {
    pub symbol_libs: Vec<LibraryEntry>,
    pub footprint_libs: Vec<LibraryEntry>,
}

/// A single library row, mirroring a `sym-lib-table` / `fp-lib-table` entry.
#[cfg_attr(feature = "ts-export", derive(ts_rs::TS))]
#[cfg_attr(feature = "ts-export", ts(export))]
#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct LibraryEntry {
    pub name: String,
    pub uri: String,
    /// Library kind — typically "`KiCad`", "Legacy", or "Cloud".
    pub kind: String,
    pub options: String,
    pub descr: String,
}
