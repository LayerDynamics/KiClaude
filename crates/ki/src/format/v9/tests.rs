//! Integration tests for the M0-R-05 KCIR mapper.
//!
//! Float comparisons here check that values parsed from string literals
//! survive the s-expression → KCIR mapping unchanged. The IEEE-754 bits
//! for a given decimal literal are deterministic across both ends of
//! the round-trip, so direct equality is correct and `clippy::float_cmp`
//! is intentionally allowed for this module.

#![allow(clippy::float_cmp)]

use std::fs;

use pretty_assertions::assert_eq;
use tempfile::TempDir;

use super::{KiProject, OpenError};

/// A blinky-style `.kicad_pro` JSON with the M0-relevant fields populated.
const BLINKY_PRO: &str = r#"{
  "meta": { "filename": "blinky.kicad_pro", "generator": "kiclaude", "version": 3 },
  "net_settings": {
    "classes": [
      { "name": "Default", "clearance": 0.2, "track_width": 0.25,
        "via_diameter": 0.6, "via_drill": 0.3 }
    ]
  },
  "schematic": {
    "top_level_sheets": [
      { "uuid": "11111111-1111-1111-1111-111111111111",
        "name": "blinky", "filename": "blinky.kicad_sch" }
    ]
  },
  "libraries": {
    "pinned_footprint_libs": ["Resistor_SMD"],
    "pinned_symbol_libs": ["Device"]
  },
  "text_variables": { "BOARD": "blinky" }
}"#;

/// A blinky-style `.kicad_pcb` exercising the M0-R-05 acceptance set:
/// project name, one footprint, one track, one zone, one net populated.
const BLINKY_PCB: &str = r#"(kicad_pcb (version 20240108) (generator kiclaude)

  (general
    (thickness 1.6)
  )

  (paper "A4")
  (layers
    (0 "F.Cu" signal)
    (31 "B.Cu" signal)
    (44 "Edge.Cuts" user)
  )

  (setup
    (pad_to_mask_clearance 0.0)
  )

  (net 0 "")
  (net 1 "VCC")

  (footprint "Resistor_SMD:R_0603_1608Metric"
    (layer "F.Cu")
    (uuid "22222222-2222-2222-2222-222222222222")
    (at 100.0 50.0 90.0)
    (property "Reference" "R1" (at 0 -1.5 0) (layer "F.SilkS"))
    (property "Value" "10k" (at 0 1.5 0) (layer "F.Fab"))
  )

  (segment
    (start 100.0 50.0) (end 110.0 50.0)
    (width 0.25) (layer "F.Cu") (net 1)
    (uuid "33333333-3333-3333-3333-333333333333")
  )

  (zone
    (net 1) (net_name "VCC") (layer "F.Cu")
    (uuid "44444444-4444-4444-4444-444444444444")
    (connect_pads thermal_reliefs (clearance 0.5))
    (min_thickness 0.25)
    (polygon (pts (xy 90 40) (xy 120 40) (xy 120 60) (xy 90 60)))
  )

  (gr_line (start 90 40) (end 120 40) (stroke (width 0.05) (type default)) (layer "Edge.Cuts") (uuid "55555555-5555-5555-5555-555555555555"))
  (gr_line (start 120 40) (end 120 60) (stroke (width 0.05) (type default)) (layer "Edge.Cuts") (uuid "66666666-6666-6666-6666-666666666666"))
)
"#;

/// Write the blinky fixture into `dir/<stem>.kicad_pro` + `dir/<stem>.kicad_pcb`.
fn write_blinky_fixture(dir: &std::path::Path, stem: &str) {
    fs::write(dir.join(format!("{stem}.kicad_pro")), BLINKY_PRO).expect("write pro");
    fs::write(dir.join(format!("{stem}.kicad_pcb")), BLINKY_PCB).expect("write pcb");
}

/// Integration (M0-R-05 acceptance): `KiProject::open` on a blinky-style
/// directory returns a `kcir::Project` with name, one footprint, one
/// track, one zone, one net populated.
#[test]
fn integration_open_blinky_populates_kcir() {
    let dir = TempDir::new().expect("tempdir");
    write_blinky_fixture(dir.path(), "blinky");

    let opened = KiProject::open(dir.path()).expect("open succeeds");

    assert_eq!(opened.project.name, "blinky", "project name resolved");
    assert_eq!(opened.pcb_path.is_some(), true, "pcb path captured");

    let pcb = &opened.project.pcb;
    assert_eq!(pcb.footprints.len(), 1, "one footprint mapped");
    assert_eq!(pcb.footprints[0].refdes, "R1");
    assert_eq!(pcb.footprints[0].value, "10k");
    assert_eq!(pcb.footprints[0].position_mm, (100.0, 50.0));
    assert_eq!(pcb.footprints[0].rotation_deg, 90.0);

    assert_eq!(pcb.tracks.len(), 1, "one track mapped");
    assert_eq!(pcb.tracks[0].net, "VCC");
    assert_eq!(pcb.tracks[0].width_mm, 0.25);
    assert_eq!(pcb.tracks[0].points_mm, vec![(100.0, 50.0), (110.0, 50.0)]);

    assert_eq!(pcb.zones.len(), 1, "one zone mapped");
    assert_eq!(pcb.zones[0].net, "VCC");
    assert_eq!(pcb.zones[0].outline_mm.len(), 4);

    // KCIR `nets` does not include the placeholder "no-net" (id 0).
    assert_eq!(pcb.nets.len(), 1, "one named net mapped");
    assert_eq!(pcb.nets[0].name, "VCC");

    // Layers from `(layers …)` block.
    assert_eq!(pcb.layers.len(), 3);
    assert_eq!(pcb.layers[0].name, "F.Cu");

    // Net classes inherited from the .kicad_pro.
    assert_eq!(opened.project.net_classes.len(), 1);
    assert_eq!(opened.project.net_classes[0].name, "Default");
    assert_eq!(opened.project.net_classes[0].trace_width_mm, 0.25);

    // Top-level sheet from .kicad_pro.
    assert_eq!(opened.project.schematic.sheets.len(), 1);
    assert_eq!(opened.project.schematic.sheets[0].name, "blinky");

    // Pinned library list.
    assert_eq!(opened.project.libraries.footprint_libs.len(), 1);
    assert_eq!(
        opened.project.libraries.footprint_libs[0].name,
        "Resistor_SMD"
    );

    // Outline from Edge.Cuts gr_lines.
    assert_eq!(pcb.outline.points_mm.len(), 4, "two edges → 4 endpoints");
}

/// `KiProject::open` resolves project name from `meta.filename` stem even
/// when the directory name differs.
#[test]
fn smoke_open_resolves_name_from_meta_filename() {
    let dir = TempDir::new().expect("tempdir");
    // Note: directory name != file stem on purpose.
    fs::write(
        dir.path().join("renamed.kicad_pro"),
        r#"{ "meta": { "filename": "blinky.kicad_pro" } }"#,
    )
    .expect("write");
    let opened = KiProject::open(dir.path()).expect("open");
    assert_eq!(opened.project.name, "blinky");
}

/// `KiProject::open` falls back to the disk stem when `meta.filename` is
/// missing.
#[test]
fn smoke_open_falls_back_to_disk_stem() {
    let dir = TempDir::new().expect("tempdir");
    fs::write(dir.path().join("foo.kicad_pro"), r"{}").expect("write");
    let opened = KiProject::open(dir.path()).expect("open");
    assert_eq!(opened.project.name, "foo");
}

/// `KiProject::open` works when only the `.kicad_pro` is present (no PCB
/// yet) and leaves `pcb_path` as `None`.
#[test]
fn smoke_open_without_pcb_file() {
    let dir = TempDir::new().expect("tempdir");
    fs::write(dir.path().join("solo.kicad_pro"), r"{}").expect("write");
    let opened = KiProject::open(dir.path()).expect("open");
    assert!(opened.pcb_path.is_none());
    assert!(opened.project.pcb.footprints.is_empty());
}

/// `KiProject::open` rejects a non-directory path.
#[test]
fn smoke_open_rejects_non_directory() {
    let err = KiProject::open("/nonexistent/path/for/m0r05/test").expect_err("should fail");
    assert!(matches!(err, OpenError::NotADir(_)));
}

/// `KiProject::open` errors when the directory has no `.kicad_pro` file.
#[test]
fn smoke_open_no_project_file() {
    let dir = TempDir::new().expect("tempdir");
    fs::write(dir.path().join("notes.txt"), "not a project").expect("write");
    let err = KiProject::open(dir.path()).expect_err("should fail");
    assert!(matches!(err, OpenError::NoProjectFile(_)));
}

/// `KiProject::open` errors when the directory has multiple `.kicad_pro`
/// files (ambiguous).
#[test]
fn smoke_open_multiple_project_files() {
    let dir = TempDir::new().expect("tempdir");
    fs::write(dir.path().join("a.kicad_pro"), r"{}").expect("write");
    fs::write(dir.path().join("b.kicad_pro"), r"{}").expect("write");
    let err = KiProject::open(dir.path()).expect_err("should fail");
    let OpenError::MultipleProjectFiles { names, .. } = err else {
        panic!("expected MultipleProjectFiles, got something else");
    };
    assert_eq!(
        names,
        vec!["a.kicad_pro".to_string(), "b.kicad_pro".to_string()]
    );
}

/// Invalid JSON in `.kicad_pro` returns a structured error.
#[test]
fn smoke_open_invalid_pro_json() {
    let dir = TempDir::new().expect("tempdir");
    fs::write(dir.path().join("bad.kicad_pro"), "this is not json").expect("write");
    let err = KiProject::open(dir.path()).expect_err("should fail");
    assert!(matches!(err, OpenError::InvalidProjectJson { .. }));
}

/// Invalid S-expression in `.kicad_pcb` returns a structured error.
#[test]
fn smoke_open_invalid_pcb_sexpr() {
    let dir = TempDir::new().expect("tempdir");
    fs::write(dir.path().join("p.kicad_pro"), r"{}").expect("write");
    fs::write(dir.path().join("p.kicad_pcb"), "(unclosed list").expect("write");
    let err = KiProject::open(dir.path()).expect_err("should fail");
    assert!(matches!(err, OpenError::InvalidPcbSexpr { .. }));
}

/// A `.kicad_pcb` whose root form is not `kicad_pcb` is rejected.
#[test]
fn smoke_open_pcb_with_wrong_root() {
    let dir = TempDir::new().expect("tempdir");
    fs::write(dir.path().join("p.kicad_pro"), r"{}").expect("write");
    fs::write(dir.path().join("p.kicad_pcb"), "(kicad_sch (version 1))").expect("write");
    let err = KiProject::open(dir.path()).expect_err("should fail");
    assert!(matches!(err, OpenError::NotKicadPcb { .. }));
}

/// Integration (M0-R-06 acceptance): a canonical-form `.kicad_pcb` file
/// open → save round-trips byte-identically. The fixture is built by
/// [`emit_pcb`] on a known [`Pcb`] so the on-disk format matches the
/// emitter's canonical output exactly.
#[test]
fn integration_open_save_round_trip_is_byte_identical() {
    use super::emit_pcb;
    use crate::kcir::{FootprintInstance, Layer, LayerRef, Net, Pcb, Track, Zone};

    let mut original = Pcb {
        version: 20_240_108,
        generator: "kiclaude".to_string(),
        thickness_mm: 1.6,
        paper: "A4".to_string(),
        layers: vec![
            Layer {
                id: 0,
                name: "F.Cu".to_string(),
                kind: "signal".to_string(),
                purpose: String::new(),
            },
            Layer {
                id: 31,
                name: "B.Cu".to_string(),
                kind: "signal".to_string(),
                purpose: String::new(),
            },
            Layer {
                id: 44,
                name: "Edge.Cuts".to_string(),
                kind: "user".to_string(),
                purpose: String::new(),
            },
        ],
        nets: vec![Net {
            name: "VCC".to_string(),
            ..Net::default()
        }],
        footprints: vec![FootprintInstance {
            uuid: "22222222-2222-2222-2222-222222222222".to_string(),
            refdes: "R1".to_string(),
            lib_id: "Resistor_SMD:R_0603_1608Metric".to_string(),
            value: "10k".to_string(),
            layer: LayerRef("F.Cu".to_string()),
            position_mm: (100.0, 50.0),
            rotation_deg: 90.0,
            ..FootprintInstance::default()
        }],
        tracks: vec![Track {
            uuid: "33333333-3333-3333-3333-333333333333".to_string(),
            layer: LayerRef("F.Cu".to_string()),
            net: "VCC".to_string(),
            points_mm: vec![(100.0, 50.0), (110.0, 50.0)],
            width_mm: 0.25,
            ..Track::default()
        }],
        zones: vec![Zone {
            uuid: "44444444-4444-4444-4444-444444444444".to_string(),
            layer: LayerRef("F.Cu".to_string()),
            net: "VCC".to_string(),
            outline_mm: vec![(90.0, 40.0), (120.0, 40.0), (120.0, 60.0), (90.0, 60.0)],
            thermal_relief: true,
            ..Zone::default()
        }],
        ..Pcb::default()
    };
    // Suppress unused-mut warning while documenting intent: the round-trip
    // test mutates the layer name in a planned follow-up.
    original.paper = "A4".to_string();

    let canonical = emit_pcb(&original);

    // Write canonical-form fixture to a tempdir.
    let dir = TempDir::new().expect("tempdir");
    fs::write(
        dir.path().join("rt.kicad_pro"),
        r#"{ "meta": { "filename": "rt.kicad_pro" } }"#,
    )
    .expect("write pro");
    fs::write(dir.path().join("rt.kicad_pcb"), &canonical).expect("write pcb");

    // Open via the mapper.
    let opened = KiProject::open(dir.path()).expect("open");

    // Cross-check: emit from the opened KCIR equals the canonical bytes.
    let re_emitted = emit_pcb(&opened.project.pcb);
    assert_eq!(re_emitted, canonical, "emit(open(canonical)) == canonical");

    // Save round-trip: KiProject::save_pcb writes byte-identical bytes.
    let saved_path = opened.save_pcb().expect("save");
    let saved = fs::read_to_string(&saved_path).expect("read back");
    assert_eq!(saved, canonical, "save → read is byte-identical");

    // Header fields survived the round-trip.
    assert_eq!(opened.project.pcb.version, 20_240_108);
    assert_eq!(opened.project.pcb.generator, "kiclaude");
    assert_eq!(opened.project.pcb.paper, "A4");
}

/// A multi-via, multi-segment fixture verifies net resolution works
/// across more than one item per kind.
#[test]
fn integration_multiple_tracks_vias_zones_resolve_nets() {
    let dir = TempDir::new().expect("tempdir");
    fs::write(
        dir.path().join("multi.kicad_pro"),
        r#"{"meta":{"filename":"multi.kicad_pro"}}"#,
    )
    .expect("write");
    let pcb = r#"(kicad_pcb (version 20240108) (generator kiclaude)
      (layers (0 "F.Cu" signal) (31 "B.Cu" signal))
      (net 0 "")
      (net 1 "VCC")
      (net 2 "GND")
      (segment (start 0 0) (end 1 0) (width 0.2) (layer "F.Cu") (net 1) (uuid "a"))
      (segment (start 2 0) (end 3 0) (width 0.2) (layer "F.Cu") (net 2) (uuid "b"))
      (via (at 5 5) (size 0.6) (drill 0.3) (layers "F.Cu" "B.Cu") (net 1) (uuid "c"))
      (zone (net 2) (layer "F.Cu") (uuid "d") (polygon (pts (xy 0 0) (xy 1 0) (xy 1 1))))
    )"#;
    fs::write(dir.path().join("multi.kicad_pcb"), pcb).expect("write");
    let opened = KiProject::open(dir.path()).expect("open");
    let pcb = &opened.project.pcb;
    assert_eq!(pcb.nets.len(), 2);
    assert_eq!(pcb.tracks.len(), 2);
    assert_eq!(pcb.tracks[0].net, "VCC");
    assert_eq!(pcb.tracks[1].net, "GND");
    assert_eq!(pcb.vias.len(), 1);
    assert_eq!(pcb.vias[0].net, "VCC");
    assert_eq!(pcb.vias[0].drill_mm, 0.3);
    assert_eq!(pcb.vias[0].diameter_mm, 0.6);
    assert_eq!(pcb.zones.len(), 1);
    assert_eq!(pcb.zones[0].net, "GND");
}

/// M3-R-01 contract: a non-default `Stackup` survives emit → parse with
/// every per-layer field (`name`, `kind`, `thickness`,
/// `dielectric_constant`, `loss_tangent`, `material`/`color`) intact,
/// and the trailing `(copper_finish …)` round-trips into
/// `Stackup::finish`.
#[test]
fn stackup_round_trips_through_emit_and_parse() {
    use super::pcb::map_stackup_from_pcb;
    use crate::format::v9::emit::emit_pcb_with_stackup;
    use crate::kcir::{Layer, Pcb, Stackup, StackupLayer, StackupLayerKind};
    use crate::sexpr::parse_str;

    let copper = |name: &str| StackupLayer {
        name: name.to_string(),
        kind: StackupLayerKind::Copper,
        thickness_mm: 0.035,
        dielectric_constant: None,
        loss_tangent: None,
        color: "copper".to_string(),
    };
    let dielectric = |name: &str, thickness: f64| StackupLayer {
        name: name.to_string(),
        kind: StackupLayerKind::Dielectric,
        thickness_mm: thickness,
        dielectric_constant: Some(4.5),
        loss_tangent: Some(0.02),
        color: "FR4".to_string(),
    };
    let original = Stackup {
        layers: vec![
            copper("F.Cu"),
            dielectric("dielectric 1", 0.21),
            copper("In1.Cu"),
            dielectric("dielectric 2", 1.10),
            copper("In2.Cu"),
            dielectric("dielectric 3", 0.21),
            copper("B.Cu"),
        ],
        power_plane_layers: Vec::new(),
        controlled_impedance: false,
        // 0.035 + 0.21 + 0.035 + 1.10 + 0.035 + 0.21 + 0.035 = 1.66.
        board_thickness_mm: 1.66,
        finish: "ENIG".to_string(),
    };

    let pcb = Pcb {
        version: 20_240_108,
        generator: "kiclaude".to_string(),
        thickness_mm: 1.66,
        paper: "A4".to_string(),
        layers: vec![
            Layer {
                id: 0,
                name: "F.Cu".to_string(),
                kind: "signal".to_string(),
                purpose: String::new(),
            },
            Layer {
                id: 31,
                name: "B.Cu".to_string(),
                kind: "signal".to_string(),
                purpose: String::new(),
            },
        ],
        ..Pcb::default()
    };

    let text = emit_pcb_with_stackup(&pcb, Some(&original));
    // Sanity: the stackup landed inside `(setup …)`.
    assert!(text.contains("(stackup"), "stackup block missing\n{text}");
    assert!(
        text.contains("(copper_finish \"ENIG\")"),
        "copper_finish line missing\n{text}"
    );

    let nodes = parse_str(&text).expect("re-parse emitted bytes");
    let root = nodes.first().expect("at least one top-level form");
    let back = map_stackup_from_pcb(root).expect("stackup re-parses");

    assert_eq!(back.layers.len(), original.layers.len(), "layer count");
    for (i, (orig, got)) in original.layers.iter().zip(back.layers.iter()).enumerate() {
        assert_eq!(orig.name, got.name, "layer {i} name");
        assert_eq!(orig.kind, got.kind, "layer {i} kind");
        assert_eq!(orig.thickness_mm, got.thickness_mm, "layer {i} thickness");
        assert_eq!(
            orig.dielectric_constant, got.dielectric_constant,
            "layer {i} epsilon_r"
        );
        assert_eq!(
            orig.loss_tangent, got.loss_tangent,
            "layer {i} loss_tangent"
        );
        // Dielectric color is the material name we wrote; copper is the
        // parser's "copper" default — both round-trip.
        assert_eq!(orig.color, got.color, "layer {i} color/material");
    }
    assert_eq!(back.finish, original.finish, "copper_finish");
    // Parser sums layer thicknesses into board_thickness_mm.
    assert!(
        (back.board_thickness_mm - original.board_thickness_mm).abs() < 1e-9,
        "board_thickness sums match (got {}, want {})",
        back.board_thickness_mm,
        original.board_thickness_mm
    );
}

/// M3-R-01 negative: a default `Stackup` (the in-memory placeholder)
/// must NOT inject a `(stackup …)` block when `save_pcb()` writes. This
/// protects the M0-Q-02 byte-identity gate against fixtures that never
/// carried a stackup.
#[test]
fn save_pcb_omits_stackup_block_for_default_project() {
    use super::emit_pcb;
    use crate::kcir::Pcb;

    let pcb = Pcb::default();
    let text = emit_pcb(&pcb);
    assert!(
        !text.contains("(stackup"),
        "default emit must not include a stackup block\n{text}"
    );
}
