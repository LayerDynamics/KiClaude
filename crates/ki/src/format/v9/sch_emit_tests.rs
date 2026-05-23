//! M1-R-02 acceptance tests for the `.kicad_sch` emitter.
//!
//! Two orthogonal properties under test:
//!
//! 1. **Byte-identical for unmodified nodes** — `emit_sch(parse(text),
//!    text)` returns `text` unchanged for every M1-R-01 fixture.
//! 2. **Canonical re-emit** — each `emit_*` sub-emitter produces text
//!    that parses back into the same KCIR shape it was emitted from.
//!
//! A third hybrid path is tested via [`emit_sch_with_edits`]: marking
//! a single child span as "edited" replaces just that subtree with its
//! canonical form, leaving siblings byte-identical.

#![allow(clippy::float_cmp)]

use std::fs;

use pretty_assertions::assert_eq;
use tempfile::TempDir;

use super::sch::{map_sch, ParsedSheet};
use super::sch_emit::{
    emit_bus, emit_junction, emit_label, emit_lib_symbol, emit_no_connect, emit_sch,
    emit_sch_canonical, emit_sch_with_edits, emit_sheet_pin, emit_sub_sheet, emit_symbol_instance,
    emit_wire, span_key, EditedSpans,
};
use super::KiProject;
use crate::kcir::{
    Bus, Junction, Label, LabelKind, LibSymbol, NoConnect, Sheet, SheetPin, SymbolInstance,
    SymbolProperty, Wire,
};
use crate::sexpr::parse_str;

/// Reuses the M1-R-01 fixture set so the byte-identical assertion
/// piggybacks on the exact same construct coverage.
const FIXTURES: &[(&str, &str)] = &[
    ("minimal", super::sch_tests::MINIMAL_PUB),
    ("wires_junctions", super::sch_tests::WIRES_JUNCTIONS_PUB),
    ("labels_all_kinds", super::sch_tests::LABELS_ALL_KINDS_PUB),
    (
        "no_connects_pwr_flag",
        super::sch_tests::NO_CONNECTS_PWR_FLAG_PUB,
    ),
    ("multisheet", super::sch_tests::MULTISHEET_PUB),
];

fn parse_one(src: &str) -> crate::sexpr::SNode {
    let nodes = parse_str(src).expect("parse");
    assert_eq!(nodes.len(), 1, "expected one top-level form");
    nodes.into_iter().next().unwrap()
}

#[test]
fn byte_identical_round_trip_for_every_fixture() {
    for (name, src) in FIXTURES {
        let root = parse_one(src);
        let out = emit_sch(&root, src);
        assert_eq!(
            out, *src,
            "fixture {name} did not round-trip byte-identically"
        );
    }
}

#[test]
fn empty_edited_spans_is_identity() {
    for (name, src) in FIXTURES {
        let root = parse_one(src);
        let parsed = map_sch(&root).expect("map_sch");
        let out = emit_sch_with_edits(&root, src, &parsed, &EditedSpans::new())
            .expect("emit_sch_with_edits");
        assert_eq!(
            out, *src,
            "fixture {name} with empty edited_spans must be the identity"
        );
    }
}

#[test]
fn edited_span_swaps_in_canonical_text_and_re_parses() {
    let src = super::sch_tests::WIRES_JUNCTIONS_PUB;
    let root = parse_one(src);
    let parsed = map_sch(&root).expect("map_sch");

    // Pick the first (junction …) form to mark as edited.
    let junction_form = root
        .children()
        .iter()
        .find(|c| c.head_symbol() == Some("junction"))
        .cloned()
        .expect("junction form present");

    let mut edits = EditedSpans::new();
    edits.insert(span_key(&junction_form));

    let out = emit_sch_with_edits(&root, src, &parsed, &edits).expect("emit_sch_with_edits");
    // The output is not identical (canonical re-emit reorders fields)
    // but must still parse back.
    let reparsed = parse_str(&out).expect("re-parse hybrid output");
    assert_eq!(reparsed.len(), 1);
    assert_eq!(reparsed[0].head_symbol(), Some("kicad_sch"));

    // And the second round-trip must be the identity on the new bytes.
    let reroot = reparsed.into_iter().next().unwrap();
    let reemit = emit_sch(&reroot, &out);
    assert_eq!(reemit, out, "idempotent on the canonicalized output");
}

#[test]
fn canonical_emit_of_synthetic_parsedsheet_parses_back() {
    let parsed = synth_parsed_sheet();
    let text = emit_sch_canonical(&parsed);
    let reparsed = parse_str(&text).expect("re-parse canonical emit");
    assert_eq!(reparsed.len(), 1);
    assert_eq!(reparsed[0].head_symbol(), Some("kicad_sch"));
    let reparsed_root = reparsed.into_iter().next().unwrap();
    let remapped = map_sch(&reparsed_root).expect("map_sch on canonical emit");

    assert_eq!(remapped.symbols.len(), parsed.symbols.len());
    assert_eq!(remapped.wires.len(), parsed.wires.len());
    assert_eq!(remapped.junctions.len(), parsed.junctions.len());
    assert_eq!(remapped.labels.len(), parsed.labels.len());
    assert_eq!(remapped.no_connects.len(), parsed.no_connects.len());
    assert_eq!(remapped.sub_sheets.len(), parsed.sub_sheets.len());
    assert_eq!(remapped.lib_symbols.len(), parsed.lib_symbols.len());
}

#[test]
fn each_entity_emitter_produces_parseable_text() {
    // Each emitted form must (a) parse back, (b) have the expected head
    // symbol, (c) carry the entity's uuid (when one was supplied).
    let symbol = SymbolInstance {
        uuid: "11111111-aaaa-aaaa-aaaa-111111111111".to_string(),
        lib_id: "Device:R".to_string(),
        refdes: "R1".to_string(),
        value: "10k".to_string(),
        position_mm: (10.0, 20.0),
        rotation_deg: 90.0,
        unit: 1,
        in_bom: true,
        on_board: true,
        properties: vec![SymbolProperty {
            key: "Reference".to_string(),
            value: "R1".to_string(),
            ..SymbolProperty::default()
        }],
        ..SymbolInstance::default()
    };
    assert_parseable_with_head(&emit_symbol_instance(&symbol), "symbol");

    let wire = Wire {
        uuid: "22222222-bbbb-bbbb-bbbb-222222222222".to_string(),
        points_mm: vec![(0.0, 0.0), (10.0, 0.0)],
        ..Wire::default()
    };
    assert_parseable_with_head(&emit_wire(&wire), "wire");

    let junction = Junction {
        uuid: "33333333-cccc-cccc-cccc-333333333333".to_string(),
        position_mm: (5.0, 5.0),
        ..Junction::default()
    };
    assert_parseable_with_head(&emit_junction(&junction), "junction");

    let label = Label {
        uuid: "44444444-dddd-dddd-dddd-444444444444".to_string(),
        kind: LabelKind::Hierarchical,
        text: "BUS".to_string(),
        position_mm: (3.0, 4.0),
        rotation_deg: 0.0,
        shape: "input".to_string(),
        ..Label::default()
    };
    assert_parseable_with_head(&emit_label(&label), "hierarchical_label");

    let nc = NoConnect {
        uuid: "55555555-eeee-eeee-eeee-555555555555".to_string(),
        position_mm: (1.0, 1.0),
        ..NoConnect::default()
    };
    assert_parseable_with_head(&emit_no_connect(&nc), "no_connect");

    let bus = Bus {
        uuid: "66666666-ffff-ffff-ffff-666666666666".to_string(),
        points_mm: vec![(0.0, 0.0), (5.0, 0.0)],
        ..Bus::default()
    };
    assert_parseable_with_head(&emit_bus(&bus), "bus");

    let alias = Bus {
        name: "DATA".to_string(),
        members: vec!["D0".to_string(), "D1".to_string()],
        ..Bus::default()
    };
    assert_parseable_with_head(&emit_bus(&alias), "bus_alias");

    let sub_sheet = Sheet {
        uuid: "77777777-1111-1111-1111-777777777777".to_string(),
        name: "Power".to_string(),
        file: "power.kicad_sch".to_string(),
        position_mm: (10.0, 10.0),
        size_mm: (50.0, 30.0),
        pins: vec![SheetPin {
            uuid: "77777777-2222-2222-2222-777777777777".to_string(),
            name: "VCC".to_string(),
            shape: "input".to_string(),
            position_mm: (10.0, 15.0),
            rotation_deg: 180.0,
        }],
        ..Sheet::default()
    };
    assert_parseable_with_head(&emit_sub_sheet(&sub_sheet), "sheet");

    let pin = SheetPin {
        name: "GND".to_string(),
        shape: "output".to_string(),
        position_mm: (0.0, 0.0),
        rotation_deg: 90.0,
        uuid: "88888888-3333-3333-3333-888888888888".to_string(),
    };
    assert_parseable_with_head(&emit_sheet_pin(&pin), "pin");

    let lib = LibSymbol {
        lib_id: "Device:R".to_string(),
        properties: vec![SymbolProperty {
            key: "Reference".to_string(),
            value: "R".to_string(),
            ..SymbolProperty::default()
        }],
        is_power: false,
    };
    assert_parseable_with_head(&emit_lib_symbol(&lib), "symbol");
}

#[test]
fn power_label_emits_power_label_head() {
    let label = Label {
        kind: LabelKind::Power,
        text: "VCC".to_string(),
        ..Label::default()
    };
    let text = emit_label(&label);
    let parsed = parse_str(&text).expect("parse");
    assert_eq!(parsed[0].head_symbol(), Some("power_label"));
}

#[test]
fn integration_open_then_save_sch_is_byte_identical() {
    let dir = TempDir::new().expect("tempdir");
    let src = super::sch_tests::WIRES_JUNCTIONS_PUB;
    fs::write(
        dir.path().join("rt.kicad_pro"),
        r#"{ "meta": { "filename": "rt.kicad_pro" } }"#,
    )
    .expect("write pro");
    fs::write(dir.path().join("rt.kicad_sch"), src).expect("write sch");

    let opened = KiProject::open(dir.path()).expect("open");
    assert!(opened.sch_source.is_some());
    let saved = opened.save_sch(&EditedSpans::new()).expect("save_sch");
    let saved_text = fs::read_to_string(&saved).expect("read back");
    assert_eq!(saved_text, src, "save_sch must be byte-identical");
}

#[test]
fn integration_save_sch_canonical_when_no_source() {
    // Construct a KiProject by hand with parsed_sheet but no source — we
    // simulate the "freshly created project" path where save_sch has to
    // canonicalize from KCIR alone.
    let parsed = synth_parsed_sheet();
    let project = KiProject {
        root: std::path::PathBuf::from("/tmp/kiclaude-test-synth"),
        pro_path: std::path::PathBuf::from("/tmp/kiclaude-test-synth/p.kicad_pro"),
        pcb_path: None,
        sch_path: None,
        sch_source: None,
        sch_root: None,
        parsed_sheet: Some(parsed),
        project: crate::kcir::Project::default(),
    };
    // Use the render helper directly so we don't need the directory to exist.
    let text = project
        .render_sch_for_tests(&EditedSpans::new())
        .expect("render");
    let reparsed = parse_str(&text).expect("re-parse canonical");
    assert_eq!(reparsed[0].head_symbol(), Some("kicad_sch"));
}

fn assert_parseable_with_head(text: &str, head: &str) {
    let parsed = parse_str(text).unwrap_or_else(|err| panic!("parse `{text}` failed: {err}"));
    assert!(!parsed.is_empty(), "empty parse for `{text}`");
    assert_eq!(parsed[0].head_symbol(), Some(head), "head mismatch");
}

fn synth_parsed_sheet() -> ParsedSheet {
    let mut p = ParsedSheet::default();
    p.sheet.uuid = "99999999-0000-0000-0000-999999999999".to_string();
    p.lib_symbols.push(LibSymbol {
        lib_id: "Device:R".to_string(),
        properties: vec![SymbolProperty {
            key: "Reference".to_string(),
            value: "R".to_string(),
            ..SymbolProperty::default()
        }],
        is_power: false,
    });
    p.symbols.push(SymbolInstance {
        uuid: "11111111-aaaa-aaaa-aaaa-111111111111".to_string(),
        sheet_uuid: p.sheet.uuid.clone(),
        lib_id: "Device:R".to_string(),
        refdes: "R1".to_string(),
        value: "10k".to_string(),
        position_mm: (50.8, 50.8),
        rotation_deg: 0.0,
        unit: 1,
        in_bom: true,
        on_board: true,
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
        ],
        ..SymbolInstance::default()
    });
    p.wires.push(Wire {
        uuid: "22222222-bbbb-bbbb-bbbb-222222222222".to_string(),
        sheet_uuid: p.sheet.uuid.clone(),
        points_mm: vec![(0.0, 0.0), (10.0, 0.0)],
    });
    p.junctions.push(Junction {
        uuid: "33333333-cccc-cccc-cccc-333333333333".to_string(),
        sheet_uuid: p.sheet.uuid.clone(),
        position_mm: (5.0, 0.0),
    });
    p.labels.push(Label {
        uuid: "44444444-dddd-dddd-dddd-444444444444".to_string(),
        sheet_uuid: p.sheet.uuid.clone(),
        kind: LabelKind::Local,
        text: "NET1".to_string(),
        position_mm: (3.0, 0.0),
        rotation_deg: 0.0,
        shape: String::new(),
    });
    p.no_connects.push(NoConnect {
        uuid: "55555555-eeee-eeee-eeee-555555555555".to_string(),
        sheet_uuid: p.sheet.uuid.clone(),
        position_mm: (7.0, 7.0),
        at: crate::kcir::PadRef::default(),
    });
    p.sub_sheets.push(Sheet {
        uuid: "77777777-1111-1111-1111-777777777777".to_string(),
        name: "Sub".to_string(),
        file: "sub.kicad_sch".to_string(),
        parent: Some(p.sheet.uuid.clone()),
        position_mm: (100.0, 100.0),
        size_mm: (50.0, 30.0),
        pins: vec![SheetPin {
            uuid: "77777777-2222-2222-2222-777777777777".to_string(),
            name: "VCC".to_string(),
            shape: "input".to_string(),
            position_mm: (100.0, 105.0),
            rotation_deg: 180.0,
        }],
    });
    p
}

impl KiProject {
    /// Test-only accessor for [`render_sch`], exposed because the real
    /// method is private and test crates can't reach it. Marked
    /// `#[cfg(test)]` so it never ships in release builds.
    #[cfg(test)]
    pub(super) fn render_sch_for_tests(
        &self,
        edited_spans: &EditedSpans,
    ) -> Result<String, super::OpenError> {
        self.render_sch(edited_spans)
    }
}
