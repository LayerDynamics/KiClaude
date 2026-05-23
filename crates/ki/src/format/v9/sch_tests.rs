//! M1-R-01 acceptance tests for the `.kicad_sch` parser.
//!
//! Five fixtures exercise the construct space the plan calls out:
//!
//! 1. **minimal** — just `(kicad_sch …)` with title block + empty
//!    `(lib_symbols)`. Matches the M0 blinky shipped fixture.
//! 2. **wires + junctions** — two-net layout with a junction.
//! 3. **labels (all four kinds)** — local + global + hierarchical +
//!    a `power:` symbol that surfaces as a power-symbol instance.
//! 4. **no-connects + `PWR_FLAG`** — three NC markers and a `PWR_FLAG`.
//! 5. **multi-sheet (sheet block + sheet pins)** — root sheet
//!    referencing a sub-sheet with two pins.
//!
//! The success criterion is the same for all five: the parser
//! returns a [`ParsedSheet`], populates the expected KCIR entities,
//! and surfaces no error.

#![allow(clippy::float_cmp)]

use std::fs;

use pretty_assertions::assert_eq;
use tempfile::TempDir;

use super::sch::map_sch;
use super::KiProject;
use crate::kcir::LabelKind;
use crate::sexpr::parse_str;

// Re-export the fixture strings so sibling test modules (e.g.
// `sch_emit_tests`) can drive the same construct coverage.
pub(super) const MINIMAL_PUB: &str = MINIMAL;
pub(super) const WIRES_JUNCTIONS_PUB: &str = WIRES_JUNCTIONS;
pub(super) const LABELS_ALL_KINDS_PUB: &str = LABELS_ALL_KINDS;
pub(super) const NO_CONNECTS_PWR_FLAG_PUB: &str = NO_CONNECTS_PWR_FLAG;
pub(super) const MULTISHEET_PUB: &str = MULTISHEET;

/// Fixture 1 — minimal sheet (matches `examples/blinky/blinky.kicad_sch`).
const MINIMAL: &str = r#"(kicad_sch (version 20240108) (generator "kiclaude") (generator_version "0.1")
  (uuid "11111111-1111-1111-1111-111111111111")
  (paper "A4")
  (title_block
    (title "blinky")
    (date "")
    (rev "0.1")
    (company "")
  )
  (lib_symbols
  )
  (sheet_instances
    (path "/" (page "1"))
  )
)
"#;

/// Fixture 2 — wires + junctions + symbol instances.
const WIRES_JUNCTIONS: &str = r#"(kicad_sch (version 20240108) (generator "kiclaude")
  (uuid "22222222-2222-2222-2222-222222222222")
  (paper "A4")
  (lib_symbols
    (symbol "Device:R"
      (property "Reference" "R" (at 0 0 0))
      (property "Value" "R" (at 0 0 0))
    )
  )
  (symbol (lib_id "Device:R") (at 50.8 50.8 0) (unit 1) (in_bom yes) (on_board yes)
    (uuid "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
    (property "Reference" "R1" (at 52 49 0))
    (property "Value" "10k" (at 52 53 0))
    (property "Footprint" "Resistor_SMD:R_0603_1608Metric" (at 0 0 0))
    (property "Datasheet" "" (at 0 0 0))
  )
  (symbol (lib_id "Device:R") (at 76.2 50.8 0) (unit 1) (in_bom yes) (on_board yes)
    (uuid "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
    (property "Reference" "R2" (at 78 49 0))
    (property "Value" "1k" (at 78 53 0))
  )
  (wire (pts (xy 50.8 45.0) (xy 76.2 45.0)) (stroke (width 0) (type default)) (uuid "10000000-0000-0000-0000-000000000001"))
  (wire (pts (xy 63.5 45.0) (xy 63.5 60.0)) (stroke (width 0) (type default)) (uuid "10000000-0000-0000-0000-000000000002"))
  (junction (at 63.5 45.0) (diameter 0) (color 0 0 0 0) (uuid "20000000-0000-0000-0000-000000000001"))
)
"#;

/// Fixture 3 — all four label kinds. The "power" kind shows up as a
/// `power:` symbol instance (`KiCad`'s native representation); the
/// other three are explicit `_label` forms.
const LABELS_ALL_KINDS: &str = r##"(kicad_sch (version 20240108) (generator "kiclaude")
  (uuid "33333333-3333-3333-3333-333333333333")
  (paper "A4")
  (lib_symbols
    (symbol "power:VCC"
      (property "Reference" "#PWR" (at 0 0 0))
      (property "Value" "VCC" (at 0 0 0))
    )
  )
  (symbol (lib_id "power:VCC") (at 100.0 50.0 0) (unit 1) (in_bom yes) (on_board yes)
    (uuid "cccccccc-cccc-cccc-cccc-cccccccccc01")
    (property "Reference" "#PWR01" (at 100 48 0))
    (property "Value" "VCC" (at 100 46 0))
  )
  (label "DATA0" (at 50.0 60.0 0) (effects (font (size 1.27 1.27))) (uuid "30000000-0000-0000-0000-000000000001"))
  (global_label "USB_DP" (shape bidirectional) (at 60.0 60.0 0) (effects (font (size 1.27 1.27))) (uuid "30000000-0000-0000-0000-000000000002"))
  (hierarchical_label "SPI_MOSI" (shape input) (at 70.0 60.0 0) (effects (font (size 1.27 1.27))) (uuid "30000000-0000-0000-0000-000000000003"))
)
"##;

/// Fixture 4 — no-connects and a `PWR_FLAG` instance.
const NO_CONNECTS_PWR_FLAG: &str = r##"(kicad_sch (version 20240108) (generator "kiclaude")
  (uuid "44444444-4444-4444-4444-444444444444")
  (paper "A4")
  (lib_symbols
    (symbol "power:PWR_FLAG"
      (property "Reference" "#FLG" (at 0 0 0))
      (property "Value" "PWR_FLAG" (at 0 0 0))
    )
  )
  (symbol (lib_id "power:PWR_FLAG") (at 110.0 50.0 0) (unit 1) (in_bom no) (on_board yes)
    (uuid "dddddddd-dddd-dddd-dddd-dddddddddd01")
    (property "Reference" "#FLG01" (at 110 48 0))
    (property "Value" "PWR_FLAG" (at 110 46 0))
  )
  (no_connect (at 80.0 50.0) (uuid "40000000-0000-0000-0000-000000000001"))
  (no_connect (at 82.0 50.0) (uuid "40000000-0000-0000-0000-000000000002"))
  (no_connect (at 84.0 50.0) (uuid "40000000-0000-0000-0000-000000000003"))
)
"##;

/// Fixture 5 — root sheet with one sub-sheet block + two hierarchical
/// sheet pins on the block.
const MULTISHEET: &str = r#"(kicad_sch (version 20240108) (generator "kiclaude")
  (uuid "55555555-5555-5555-5555-555555555555")
  (paper "A4")
  (lib_symbols)
  (sheet (at 200.0 100.0) (size 50.0 30.0) (fields_autoplaced yes)
    (stroke (width 0.1524) (type solid))
    (fill (color 0 0 0 0.0))
    (uuid "ee000000-0000-0000-0000-000000000001")
    (property "Sheetname" "Power" (at 200 99 0))
    (property "Sheetfile" "power.kicad_sch" (at 200 132 0))
    (pin "VCC" input (at 200.0 105.0 180) (effects (font (size 1.27 1.27))) (uuid "ee000000-0000-0000-0000-000000000011"))
    (pin "GND" output (at 200.0 115.0 180) (effects (font (size 1.27 1.27))) (uuid "ee000000-0000-0000-0000-000000000012"))
  )
)
"#;

/// Parse helper: assert the input parses and exactly one top-level
/// form is produced, returning it.
fn parse_one(src: &str) -> crate::sexpr::SNode {
    let nodes = parse_str(src).expect("parse");
    assert_eq!(nodes.len(), 1, "expected one top-level form");
    nodes.into_iter().next().unwrap()
}

#[test]
fn fixture_1_minimal_parses_without_error() {
    let root = parse_one(MINIMAL);
    let parsed = map_sch(&root).expect("map_sch");
    assert_eq!(parsed.sheet.uuid, "11111111-1111-1111-1111-111111111111");
    assert!(parsed.symbols.is_empty());
    assert!(parsed.wires.is_empty());
    assert!(parsed.junctions.is_empty());
    assert!(parsed.labels.is_empty());
    assert!(parsed.no_connects.is_empty());
    assert!(parsed.sub_sheets.is_empty());
}

#[test]
fn fixture_2_wires_junctions_lifts_full_kcir() {
    let root = parse_one(WIRES_JUNCTIONS);
    let parsed = map_sch(&root).expect("map_sch");
    assert_eq!(parsed.lib_symbols.len(), 1);
    assert_eq!(parsed.lib_symbols[0].lib_id, "Device:R");

    assert_eq!(parsed.symbols.len(), 2);
    let r1 = &parsed.symbols[0];
    assert_eq!(r1.refdes, "R1");
    assert_eq!(r1.value, "10k");
    assert_eq!(r1.lib_id, "Device:R");
    assert_eq!(r1.position_mm, (50.8, 50.8));
    assert_eq!(r1.unit, 1);
    assert!(r1.in_bom);
    assert!(r1.on_board);
    assert!(!r1.is_power_flag);
    assert!(!r1.is_power_symbol);
    assert_eq!(r1.footprint, "Resistor_SMD:R_0603_1608Metric");
    assert!(
        r1.properties.iter().any(|p| p.key == "Reference"),
        "Reference property kept in `properties`"
    );

    let r2 = &parsed.symbols[1];
    assert_eq!(r2.refdes, "R2");
    assert_eq!(r2.value, "1k");

    assert_eq!(parsed.wires.len(), 2);
    assert_eq!(parsed.wires[0].points_mm, vec![(50.8, 45.0), (76.2, 45.0)]);
    assert_eq!(parsed.wires[1].points_mm, vec![(63.5, 45.0), (63.5, 60.0)]);

    assert_eq!(parsed.junctions.len(), 1);
    assert_eq!(parsed.junctions[0].position_mm, (63.5, 45.0));
}

#[test]
fn fixture_3_all_four_label_kinds_recognized() {
    let root = parse_one(LABELS_ALL_KINDS);
    let parsed = map_sch(&root).expect("map_sch");

    // Local + global + hierarchical labels surface as Label entries.
    let kinds: Vec<LabelKind> = parsed.labels.iter().map(|l| l.kind).collect();
    assert!(kinds.contains(&LabelKind::Local));
    assert!(kinds.contains(&LabelKind::Global));
    assert!(kinds.contains(&LabelKind::Hierarchical));
    assert_eq!(parsed.labels.len(), 3);

    // The global label keeps its shape.
    let global = parsed
        .labels
        .iter()
        .find(|l| l.kind == LabelKind::Global)
        .expect("global label");
    assert_eq!(global.text, "USB_DP");
    assert_eq!(global.shape, "bidirectional");

    // Power "labels" in KiCad 9 are symbols from the `power:`
    // namespace; the parser flags them on the symbol instance.
    let power = parsed
        .symbols
        .iter()
        .find(|s| s.is_power_symbol)
        .expect("power symbol");
    assert_eq!(power.lib_id, "power:VCC");
    assert_eq!(power.value, "VCC");
    assert!(!power.is_power_flag);
}

#[test]
fn fixture_4_no_connects_and_pwr_flag() {
    let root = parse_one(NO_CONNECTS_PWR_FLAG);
    let parsed = map_sch(&root).expect("map_sch");

    assert_eq!(parsed.no_connects.len(), 3);
    let positions: Vec<(f64, f64)> = parsed.no_connects.iter().map(|nc| nc.position_mm).collect();
    assert_eq!(positions, vec![(80.0, 50.0), (82.0, 50.0), (84.0, 50.0)]);

    let pwr_flag = parsed
        .symbols
        .iter()
        .find(|s| s.is_power_flag)
        .expect("PWR_FLAG instance present");
    assert!(pwr_flag.is_power_symbol);
    assert_eq!(pwr_flag.lib_id, "power:PWR_FLAG");
    assert!(!pwr_flag.in_bom, "PWR_FLAG must not appear on BOM");
}

#[test]
fn fixture_5_sub_sheet_with_pins() {
    let root = parse_one(MULTISHEET);
    let parsed = map_sch(&root).expect("map_sch");

    assert_eq!(parsed.sub_sheets.len(), 1, "one sub-sheet expected");
    let sub = &parsed.sub_sheets[0];
    assert_eq!(sub.name, "Power");
    assert_eq!(sub.file, "power.kicad_sch");
    assert_eq!(sub.position_mm, (200.0, 100.0));
    assert_eq!(sub.size_mm, (50.0, 30.0));
    assert_eq!(sub.parent.as_deref(), Some(parsed.sheet.uuid.as_str()));

    assert_eq!(sub.pins.len(), 2);
    let names: Vec<&str> = sub.pins.iter().map(|p| p.name.as_str()).collect();
    assert_eq!(names, vec!["VCC", "GND"]);
    assert_eq!(sub.pins[0].shape, "input");
    assert_eq!(sub.pins[1].shape, "output");
}

/// Integration: full `KiProject::open` loads the M0 blinky fixture's
/// `.kicad_sch` alongside its `.kicad_pcb`. The seed sheet from the
/// `.kicad_pro` should be patched with the parsed uuid.
#[test]
fn integration_ki_project_open_loads_sch() {
    let dir = TempDir::new().expect("tempdir");
    fs::write(
        dir.path().join("demo.kicad_pro"),
        r#"{
          "meta": { "filename": "demo.kicad_pro" },
          "schematic": {
            "top_level_sheets": [
              { "uuid": "11111111-1111-1111-1111-111111111111",
                "name": "demo", "filename": "demo.kicad_sch" }
            ]
          }
        }"#,
    )
    .expect("write pro");
    fs::write(dir.path().join("demo.kicad_sch"), MINIMAL).expect("write sch");

    let opened = KiProject::open(dir.path()).expect("open");
    assert!(opened.sch_path.is_some(), "sch_path populated");
    let sheet = opened
        .project
        .schematic
        .sheets
        .iter()
        .find(|s| s.name == "demo")
        .expect("demo sheet present");
    assert_eq!(
        sheet.uuid, "11111111-1111-1111-1111-111111111111",
        ".kicad_sch uuid takes precedence over the .kicad_pro seed"
    );
}

/// Integration: opening a project whose `.kicad_sch` is structurally
/// malformed surfaces a typed `InvalidSchSexpr` / `NotKicadSch` error
/// rather than panicking or silently dropping the sheet.
#[test]
fn integration_invalid_sch_returns_typed_error() {
    let dir = TempDir::new().expect("tempdir");
    fs::write(dir.path().join("bad.kicad_pro"), r"{}").expect("write");
    fs::write(dir.path().join("bad.kicad_sch"), "(not_kicad_sch)").expect("write");
    let err = KiProject::open(dir.path()).expect_err("must reject");
    assert!(matches!(err, super::OpenError::NotKicadSch { .. }));
}
