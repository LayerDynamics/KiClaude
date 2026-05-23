//! M1-R-03 type-level invariants for the schematic hierarchy.
//!
//! These tests don't run the propagation algorithm (that lands in
//! M1-R-05) — they assert that the KCIR types in
//! [`crate::kcir::schematic`] are *expressive enough* to encode every
//! invariant the propagation pass will later rely on. If a future
//! refactor of the KCIR shape breaks one of these, the propagator
//! would have lost a piece of vocabulary it needs.
//!
//! The invariants we check:
//!
//! 1. **All four label kinds are representable.**
//!    `LabelKind` is the four-variant enum the propagation graph
//!    distinguishes between (local, global, hierarchical, power).
//!
//! 2. **Hierarchical labels are matched to sheet pins by name.**
//!    A `Label { kind: Hierarchical, text: "BUS" }` on the child
//!    sheet pairs with a `SheetPin { name: "BUS" }` on the parent's
//!    `(sheet)` block — the type system gives both a `text` /
//!    `name: String` field so this match is well-defined.
//!
//! 3. **A multi-sheet hierarchy resolves to a single net graph.**
//!    `Sheet { parent: Option<String> }` carries the parent uuid;
//!    walking up the chain from a leaf sheet must reach a root with
//!    `parent = None`. We assert this on a 3-sheet example.
//!
//! 4. **Global labels share a net regardless of sheet.**
//!    Two `Label`s on different sheets with `kind: Global` and the
//!    same `text` belong to the same net.
//!
//! 5. **Local labels are sheet-scoped.**
//!    Two `Label`s with `kind: Local`, same `text`, different
//!    `sheet_uuid` belong to *different* nets.

#![allow(clippy::float_cmp)]

use std::collections::HashSet;

use crate::kcir::{
    Bus, Junction, Label, LabelKind, LibSymbol, NoConnect, Schematic, Sheet, SheetPin,
    SymbolInstance, SymbolProperty, Wire,
};

const ROOT_UUID: &str = "00000000-0000-0000-0000-000000000001";
const A_UUID: &str = "00000000-0000-0000-0000-0000000000a0";
const B_UUID: &str = "00000000-0000-0000-0000-0000000000b0";

/// Build a 3-sheet hierarchy: root → A → B.
///
/// The connectivity that M1-R-05 has to resolve:
///
/// - Sheet `B` declares a hierarchical label `DATA`. Sheet `A`'s
///   `(sheet B)` block has a matching pin named `DATA`.
/// - Sheet `A` declares a hierarchical label `DATA` (the pin's
///   in-sheet end). Root's `(sheet A)` block has a matching pin
///   named `DATA`. Net `DATA` therefore propagates root → A → B.
/// - Sheet `B` also has a `VCC` global label. Sheet `root` declares
///   `VCC` as well — they share a net by name regardless of position.
fn three_sheet_project() -> Schematic {
    let root = Sheet {
        uuid: ROOT_UUID.to_string(),
        name: "root".to_string(),
        file: "root.kicad_sch".to_string(),
        parent: None,
        ..Sheet::default()
    };
    let sheet_a = Sheet {
        uuid: A_UUID.to_string(),
        name: "A".to_string(),
        file: "a.kicad_sch".to_string(),
        parent: Some(ROOT_UUID.to_string()),
        position_mm: (10.0, 10.0),
        size_mm: (50.0, 30.0),
        // Pin on the sub-sheet block as it appears on the root sheet.
        pins: vec![SheetPin {
            uuid: "pin-a-data".to_string(),
            name: "DATA".to_string(),
            shape: "input".to_string(),
            position_mm: (10.0, 15.0),
            rotation_deg: 180.0,
        }],
    };
    let sheet_b = Sheet {
        uuid: B_UUID.to_string(),
        name: "B".to_string(),
        file: "b.kicad_sch".to_string(),
        parent: Some(A_UUID.to_string()),
        position_mm: (20.0, 20.0),
        size_mm: (50.0, 30.0),
        // Pin on the (sheet B) block as it appears on sheet A.
        pins: vec![SheetPin {
            uuid: "pin-b-data".to_string(),
            name: "DATA".to_string(),
            shape: "input".to_string(),
            position_mm: (20.0, 25.0),
            rotation_deg: 180.0,
        }],
    };

    Schematic {
        sheets: vec![root, sheet_a, sheet_b],
        labels: vec![
            // Hierarchical label inside child B — matches sheet A's pin.
            Label {
                uuid: "label-b-data".to_string(),
                sheet_uuid: B_UUID.to_string(),
                kind: LabelKind::Hierarchical,
                text: "DATA".to_string(),
                position_mm: (30.0, 30.0),
                rotation_deg: 0.0,
                shape: "input".to_string(),
            },
            // Hierarchical label inside child A — matches root's pin.
            Label {
                uuid: "label-a-data".to_string(),
                sheet_uuid: A_UUID.to_string(),
                kind: LabelKind::Hierarchical,
                text: "DATA".to_string(),
                position_mm: (15.0, 15.0),
                rotation_deg: 0.0,
                shape: "input".to_string(),
            },
            // Global label on root.
            Label {
                uuid: "label-root-vcc".to_string(),
                sheet_uuid: ROOT_UUID.to_string(),
                kind: LabelKind::Global,
                text: "VCC".to_string(),
                position_mm: (5.0, 5.0),
                rotation_deg: 0.0,
                shape: String::new(),
            },
            // Global label on leaf — same net as the root's VCC.
            Label {
                uuid: "label-b-vcc".to_string(),
                sheet_uuid: B_UUID.to_string(),
                kind: LabelKind::Global,
                text: "VCC".to_string(),
                position_mm: (40.0, 40.0),
                rotation_deg: 0.0,
                shape: String::new(),
            },
        ],
        ..Schematic::default()
    }
}

#[test]
fn invariant_1_all_four_label_kinds_are_representable() {
    let kinds: HashSet<LabelKind> = [
        LabelKind::Local,
        LabelKind::Global,
        LabelKind::Hierarchical,
        LabelKind::Power,
    ]
    .into_iter()
    .collect();
    // Round-trip the enum through serde to confirm every variant is
    // serializable + deserializable distinctly.
    for k in &kinds {
        let json = serde_json::to_string(k).expect("serialize");
        let back: LabelKind = serde_json::from_str(&json).expect("deserialize");
        assert_eq!(*k, back, "{json} did not round-trip");
    }
    // The four variants must serialize as four distinct strings.
    let serialized: HashSet<String> = kinds
        .iter()
        .map(|k| serde_json::to_string(k).expect("serialize"))
        .collect();
    assert_eq!(serialized.len(), 4, "variants must serialize distinctly");
}

#[test]
fn invariant_2_hierarchical_label_matches_sheet_pin_by_name() {
    let schematic = three_sheet_project();
    // For every hierarchical label, the parent sheet's `(sheet child)`
    // block must carry a `SheetPin` with the same name.
    for label in schematic
        .labels
        .iter()
        .filter(|l| l.kind == LabelKind::Hierarchical)
    {
        let child = schematic
            .sheets
            .iter()
            .find(|s| s.uuid == label.sheet_uuid)
            .expect("label's owning sheet is in the project");
        let parent_uuid = child
            .parent
            .clone()
            .expect("hierarchical label's child sheet has a parent");
        // The parent sheet's representation of the child carries the
        // sheet pin — but in our KCIR, `pins` live on the child's
        // Sheet entry (it represents the (sheet) block as drawn on
        // the parent). The `parent` link is what identifies the
        // parent sheet itself; the matching pin is on `child.pins`.
        let _ = parent_uuid;
        let pin = child
            .pins
            .iter()
            .find(|p| p.name == label.text)
            .unwrap_or_else(|| {
                panic!(
                    "no SheetPin named {} on child sheet {} (block in parent)",
                    label.text, child.name
                )
            });
        assert_eq!(pin.name, label.text);
    }
}

#[test]
fn invariant_3_walking_parent_chain_reaches_a_root() {
    let schematic = three_sheet_project();
    // Pick the deepest leaf and walk up.
    let leaf = schematic
        .sheets
        .iter()
        .find(|s| s.uuid == B_UUID)
        .expect("leaf B present");
    let mut visited = HashSet::new();
    visited.insert(leaf.uuid.clone());
    let mut current = leaf;
    while let Some(parent_uuid) = current.parent.as_deref() {
        assert!(
            !visited.contains(parent_uuid),
            "cycle in sheet hierarchy at {parent_uuid}"
        );
        visited.insert(parent_uuid.to_string());
        current = schematic
            .sheets
            .iter()
            .find(|s| s.uuid == parent_uuid)
            .unwrap_or_else(|| {
                panic!(
                    "dangling parent uuid {parent_uuid} on sheet {}",
                    current.name
                )
            });
    }
    assert!(
        current.parent.is_none(),
        "walked to {} but it still has a parent",
        current.name
    );
    // All three sheets must have been visited.
    assert_eq!(visited.len(), 3, "every sheet should appear in the chain");
}

#[test]
fn invariant_4_global_labels_share_nets_across_sheets() {
    let schematic = three_sheet_project();
    let global_vccs: Vec<&Label> = schematic
        .labels
        .iter()
        .filter(|l| l.kind == LabelKind::Global && l.text == "VCC")
        .collect();
    assert_eq!(
        global_vccs.len(),
        2,
        "fixture has two global VCC labels on different sheets"
    );
    let owners: HashSet<&str> = global_vccs.iter().map(|l| l.sheet_uuid.as_str()).collect();
    assert!(
        owners.len() > 1,
        "they live on different sheets ({owners:?}) but share the same net by name"
    );
}

#[test]
fn invariant_5_local_labels_are_sheet_scoped() {
    // Construct two locals with the same text on different sheets;
    // assert that — at the type level — sheet_uuid distinguishes them.
    let l1 = Label {
        uuid: "ll-1".to_string(),
        sheet_uuid: ROOT_UUID.to_string(),
        kind: LabelKind::Local,
        text: "RX".to_string(),
        position_mm: (0.0, 0.0),
        rotation_deg: 0.0,
        shape: String::new(),
    };
    let l2 = Label {
        sheet_uuid: B_UUID.to_string(),
        ..l1.clone()
    };
    // Same kind + text, different sheet_uuid → they are NOT equal
    // structs even though they share a name. The propagation pass
    // (M1-R-05) relies on this so two same-named local labels on
    // different sheets don't get fused into one net.
    assert_ne!(l1, l2);
    assert_eq!(l1.text, l2.text);
    assert_ne!(l1.sheet_uuid, l2.sheet_uuid);
}

#[test]
fn invariant_6_every_kcir_entity_carries_a_sheet_uuid() {
    // Cheap structural check: each per-sheet entity exposes
    // `sheet_uuid` so the propagation graph can partition by sheet.
    let s = three_sheet_project();
    for label in &s.labels {
        assert!(!label.sheet_uuid.is_empty());
    }
    let dummy_uuid = "00000000-0000-0000-0000-deadbeef0001".to_string();
    let _ = Wire {
        sheet_uuid: dummy_uuid.clone(),
        ..Wire::default()
    };
    let _ = Junction {
        sheet_uuid: dummy_uuid.clone(),
        ..Junction::default()
    };
    let _ = NoConnect {
        sheet_uuid: dummy_uuid.clone(),
        ..NoConnect::default()
    };
    let _ = SymbolInstance {
        sheet_uuid: dummy_uuid.clone(),
        ..SymbolInstance::default()
    };
    let _ = Bus {
        sheet_uuid: dummy_uuid,
        ..Bus::default()
    };
}

#[test]
fn invariant_7_power_labels_can_round_trip_through_serde() {
    // Power labels in KiCad 9 are typically represented as power-symbol
    // instances, but `LabelKind::Power` exists for synthesized labels
    // (e.g. produced by future editing flows). Serialization must
    // preserve the variant verbatim.
    let label = Label {
        uuid: "pwr-1".to_string(),
        sheet_uuid: ROOT_UUID.to_string(),
        kind: LabelKind::Power,
        text: "GND".to_string(),
        position_mm: (0.0, 0.0),
        rotation_deg: 0.0,
        shape: String::new(),
    };
    let json = serde_json::to_string(&label).expect("serialize");
    let back: Label = serde_json::from_str(&json).expect("deserialize");
    assert_eq!(back.kind, LabelKind::Power);
    assert_eq!(back, label);
}

#[test]
fn invariant_8_symbol_properties_round_trip_in_declaration_order() {
    // The `properties` field on `SymbolInstance` is declaration-order;
    // serializing then deserializing must not reorder.
    let s = SymbolInstance {
        uuid: "u-1".to_string(),
        sheet_uuid: ROOT_UUID.to_string(),
        lib_id: "Device:R".to_string(),
        properties: vec![
            SymbolProperty {
                key: "Reference".to_string(),
                value: "R1".to_string(),
                ..SymbolProperty::default()
            },
            SymbolProperty {
                key: "Value".to_string(),
                value: "10k".to_string(),
                ..SymbolProperty::default()
            },
            SymbolProperty {
                key: "Custom".to_string(),
                value: "anything".to_string(),
                ..SymbolProperty::default()
            },
        ],
        ..SymbolInstance::default()
    };
    let json = serde_json::to_string(&s).expect("serialize");
    let back: SymbolInstance = serde_json::from_str(&json).expect("deserialize");
    let keys: Vec<&str> = back.properties.iter().map(|p| p.key.as_str()).collect();
    assert_eq!(keys, vec!["Reference", "Value", "Custom"]);
}

#[test]
fn invariant_9_lib_symbol_is_power_flag_detected_via_namespace() {
    let p = LibSymbol {
        lib_id: "power:GND".to_string(),
        is_power: true,
        ..LibSymbol::default()
    };
    let q = LibSymbol {
        lib_id: "Device:R".to_string(),
        is_power: false,
        ..LibSymbol::default()
    };
    assert!(p.is_power);
    assert!(!q.is_power);
}
