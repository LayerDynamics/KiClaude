//! KCIR — kiclaude Canonical Intermediate Representation.
//!
//! Top-level types live in submodules and are re-exported here. See
//! `docs/specs/SPEC-01-kiclaude.md` §7.2 for the contract.
//!
//! Every transformation in kiclaude passes through KCIR. The `KiCad` file
//! format remains the canonical persistent form; KCIR is the in-memory model.

mod diffpair;
mod fab;
pub mod hierarchy;
mod lengthgroup;
pub mod migrations;
mod nets;
mod pcb;
mod project;
mod schematic;
mod stackup;

pub use diffpair::DiffPair;
pub use fab::{BomPolicy, DesignRules, FabTarget, FabTargetPreset};
pub use lengthgroup::LengthGroup;
pub use nets::{LayerRef, Net, NetClass, NetClassRef, NetRef, PadRef, Topology};
pub use pcb::{
    check_invariants as check_pcb_invariants, Drawing, FootprintCourtyard, FootprintInstance,
    Layer, Model3D, Outline, Pad, Pcb, PcbInvariantError, Track, Via, Zone,
};
pub use project::{LibraryEntry, LibraryTable, Project, ProjectMetadata};
pub use schematic::{
    Bus, Junction, Label, LabelKind, LibSymbol, NoConnect, Schematic, Sheet, SheetPin,
    SymbolInstance, SymbolProperty, Wire,
};
pub use stackup::{Stackup, StackupLayer, StackupLayerKind};

#[cfg(test)]
mod hierarchy_invariants_tests;

#[cfg(test)]
mod tests {
    use super::Project;

    /// Integration test for M0-R-02: serde JSON round-trip of an empty `Project`
    /// is byte-identical.
    #[test]
    fn empty_project_roundtrips_byte_identical() {
        let original = Project::default();
        let json = serde_json::to_string(&original).expect("serialize");
        let parsed: Project = serde_json::from_str(&json).expect("deserialize");
        let json2 = serde_json::to_string(&parsed).expect("re-serialize");
        assert_eq!(json, json2, "JSON round-trip must be byte-identical");
        assert_eq!(
            original, parsed,
            "values must compare equal after round-trip"
        );
    }

    /// Pretty-printed (indented) form also round-trips byte-identical.
    #[test]
    fn empty_project_pretty_roundtrips_byte_identical() {
        let original = Project::default();
        let json = serde_json::to_string_pretty(&original).expect("serialize");
        let parsed: Project = serde_json::from_str(&json).expect("deserialize");
        let json2 = serde_json::to_string_pretty(&parsed).expect("re-serialize");
        assert_eq!(json, json2);
        assert_eq!(original, parsed);
    }

    /// The default `kcir_version` matches the crate constant.
    #[test]
    fn default_project_uses_declared_kcir_version() {
        let p = Project::default();
        assert_eq!(p.kcir_version, crate::KCIR_VERSION);
    }
}
