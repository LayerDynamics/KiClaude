//! M2-Q-01 — PCB golden-file round-trip CI gate.
//!
//! 10 canonical-form `.kicad_pcb` fixtures (the existing blinky plus
//! nine more under [`tests/golden/fixtures/`]) parse → emit
//! byte-identically. Each fixture targets a specific feature surface
//! that M2-R-01/02 must cover:
//!
//! | File                            | Feature surface                    |
//! |---------------------------------|------------------------------------|
//! | `blinky.kicad_pcb`              | Footprints + Edge.Cuts outline     |
//! | `resistor_smd_pads.kicad_pcb`   | SMD pads, courtyard polygon        |
//! | `tht_pin_header.kicad_pcb`      | Through-hole pads with drills      |
//! | `four_layer_blind_via.kicad_pcb`| 4-layer stack, blind via           |
//! | `locked_critical_track.kicad_pcb`| Locked track + locked via          |
//! | `led_with_zone.kicad_pcb`       | Zone with thermal reliefs          |
//! | `zone_with_cutout.kicad_pcb`    | Zone cutout polygon                |
//! | `hatched_ground_plane.kicad_pcb`| Hatched fill style                 |
//! | `netclass_diff_pair.kicad_pcb`  | net_class with diff-pair fields    |
//! | `silkscreen_text.kicad_pcb`     | gr_text + gr_circle drawings       |
//!
//! Two tiers are guarded here:
//!
//! 1. **Tier A** (this gate) — byte-identical round-trip on all 10
//!    fixtures. Asserts kiclaude owns its canonical form.
//! 2. **Tier B** — every `.kicad_pcb` we ship under `development/
//!    resources/kicad/.../template/` parses without panic. The
//!    KiCad-9 reference library carries non-canonical formatting we
//!    don't try to byte-match; the contract is only that the parser
//!    survives them.
//!
//! Drift recovery: run
//! `cargo test -p kiclaude-golden -- --ignored regenerate_pcb_fixtures`
//! to re-emit the fixtures after an intentional canonical-form change.

use std::fs;
use std::path::{Path, PathBuf};

use kiclaude_ki::format::v9::emit_pcb;
use kiclaude_ki::format::v9::pcb::map_pcb;
use kiclaude_ki::kcir::{
    FootprintCourtyard, FootprintInstance, Layer, LayerRef, Model3D, Net, NetClass, NetClassRef,
    Outline, Pad, Pcb, Track, Via, Zone,
};
use kiclaude_ki::sexpr::parse_str;
use pretty_assertions::assert_eq;
use similar::{ChangeTag, TextDiff};
use walkdir::WalkDir;

fn workspace_root() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .and_then(Path::parent)
        .expect("workspace root is two parents above tests/golden")
        .to_path_buf()
}

fn fixtures_dir() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("fixtures")
}

fn format_diff(label_a: &str, a: &str, label_b: &str, b: &str) -> String {
    let diff = TextDiff::from_lines(a, b);
    let mut out = format!("--- {label_a}\n+++ {label_b}\n");
    let mut lines_emitted = 0usize;
    for change in diff.iter_all_changes() {
        let sign = match change.tag() {
            ChangeTag::Delete => "-",
            ChangeTag::Insert => "+",
            ChangeTag::Equal => " ",
        };
        let text = change.value().trim_end_matches('\n');
        out.push_str(&format!("{sign} {text}\n"));
        lines_emitted += 1;
        if lines_emitted >= 200 {
            out.push_str("... (diff truncated)\n");
            break;
        }
    }
    out
}

// ---------------------------------------------------------------------
// Fixture builders. Each function returns a KCIR `Pcb` that exercises a
// distinct feature; the canonical bytes are produced by `emit_pcb`.
// ---------------------------------------------------------------------

fn two_layer_signal_stack() -> Vec<Layer> {
    vec![
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
    ]
}

fn four_layer_signal_stack() -> Vec<Layer> {
    vec![
        Layer {
            id: 0,
            name: "F.Cu".to_string(),
            kind: "signal".to_string(),
            purpose: String::new(),
        },
        Layer {
            id: 1,
            name: "In1.Cu".to_string(),
            kind: "power".to_string(),
            purpose: String::new(),
        },
        Layer {
            id: 2,
            name: "In2.Cu".to_string(),
            kind: "power".to_string(),
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
    ]
}

fn fixture_resistor_smd_pads() -> Pcb {
    Pcb {
        version: 20_240_108,
        generator: "kiclaude".to_string(),
        thickness_mm: 1.6,
        paper: "A4".to_string(),
        layers: two_layer_signal_stack(),
        nets: vec![Net {
            name: "VCC".to_string(),
            ..Net::default()
        }],
        footprints: vec![FootprintInstance {
            uuid: "11111111-1111-1111-1111-aaaaaaaaaaa1".to_string(),
            refdes: "R1".to_string(),
            lib_id: "Resistor_SMD:R_0603_1608Metric".to_string(),
            value: "10k".to_string(),
            layer: LayerRef("F.Cu".to_string()),
            position_mm: (100.0, 50.0),
            rotation_deg: 0.0,
            attributes: vec!["smd".to_string()],
            pads: vec![
                Pad {
                    number: "1".to_string(),
                    pad_type: "smd".to_string(),
                    shape: "roundrect".to_string(),
                    position_mm: (-0.8, 0.0),
                    rotation_deg: 0.0,
                    size_mm: (0.95, 0.95),
                    drill_mm: None,
                    layers: vec![LayerRef("F.Cu".to_string()), LayerRef("F.Mask".to_string())],
                    net: "VCC".to_string(),
                    roundrect_rratio: Some(0.25),
                    uuid: "p1-r1-1".to_string(),
                },
                Pad {
                    number: "2".to_string(),
                    pad_type: "smd".to_string(),
                    shape: "roundrect".to_string(),
                    position_mm: (0.8, 0.0),
                    rotation_deg: 0.0,
                    size_mm: (0.95, 0.95),
                    drill_mm: None,
                    layers: vec![LayerRef("F.Cu".to_string()), LayerRef("F.Mask".to_string())],
                    net: String::new(),
                    roundrect_rratio: Some(0.25),
                    uuid: "p1-r1-2".to_string(),
                },
            ],
            courtyard: Some(FootprintCourtyard {
                layer: LayerRef("F.CrtYd".to_string()),
                points_mm: vec![(-1.7, -0.9), (1.7, -0.9), (1.7, 0.9), (-1.7, 0.9)],
                width_mm: 0.05,
            }),
            ..FootprintInstance::default()
        }],
        outline: Outline {
            points_mm: vec![
                (0.0, 0.0),
                (50.0, 0.0),
                (50.0, 0.0),
                (50.0, 30.0),
                (50.0, 30.0),
                (0.0, 30.0),
                (0.0, 30.0),
                (0.0, 0.0),
            ],
            cutouts: Vec::new(),
        },
        ..Pcb::default()
    }
}

fn fixture_tht_pin_header() -> Pcb {
    Pcb {
        version: 20_240_108,
        generator: "kiclaude".to_string(),
        thickness_mm: 1.6,
        paper: "A4".to_string(),
        layers: two_layer_signal_stack(),
        nets: vec![
            Net {
                name: "+3V3".to_string(),
                ..Net::default()
            },
            Net {
                name: "GND".to_string(),
                ..Net::default()
            },
        ],
        footprints: vec![FootprintInstance {
            uuid: "22222222-2222-2222-2222-aaaaaaaaaaa1".to_string(),
            refdes: "J1".to_string(),
            lib_id: "Connector_PinHeader_2.54mm:PinHeader_1x04_P2.54mm_Vertical".to_string(),
            value: "Conn_01x04".to_string(),
            layer: LayerRef("F.Cu".to_string()),
            position_mm: (50.0, 50.0),
            rotation_deg: 0.0,
            attributes: vec!["through_hole".to_string()],
            pads: (1..=4)
                .map(|i| Pad {
                    number: i.to_string(),
                    pad_type: "thru_hole".to_string(),
                    shape: "circle".to_string(),
                    position_mm: (f64::from(i - 1) * 2.54, 0.0),
                    rotation_deg: 0.0,
                    size_mm: (1.7, 1.7),
                    drill_mm: Some((1.0, 1.0)),
                    layers: vec![LayerRef("*.Cu".to_string()), LayerRef("*.Mask".to_string())],
                    net: match i {
                        1 => "+3V3".to_string(),
                        4 => "GND".to_string(),
                        _ => String::new(),
                    },
                    roundrect_rratio: None,
                    uuid: format!("p-j1-{i}"),
                })
                .collect(),
            ..FootprintInstance::default()
        }],
        ..Pcb::default()
    }
}

fn fixture_four_layer_blind_via() -> Pcb {
    Pcb {
        version: 20_240_108,
        generator: "kiclaude".to_string(),
        thickness_mm: 1.6,
        paper: "A4".to_string(),
        layers: four_layer_signal_stack(),
        nets: vec![
            Net {
                name: "USB_DP".to_string(),
                ..Net::default()
            },
            Net {
                name: "USB_DM".to_string(),
                ..Net::default()
            },
        ],
        vias: vec![
            Via {
                uuid: "v-blind-1".to_string(),
                net: "USB_DP".to_string(),
                position_mm: (50.0, 30.0),
                from_layer: LayerRef("F.Cu".to_string()),
                to_layer: LayerRef("In1.Cu".to_string()),
                drill_mm: 0.15,
                diameter_mm: 0.3,
                kind: "blind".to_string(),
                locked: false,
            },
            Via {
                uuid: "v-buried-1".to_string(),
                net: "USB_DM".to_string(),
                position_mm: (52.0, 30.0),
                from_layer: LayerRef("In1.Cu".to_string()),
                to_layer: LayerRef("In2.Cu".to_string()),
                drill_mm: 0.15,
                diameter_mm: 0.3,
                kind: "buried".to_string(),
                locked: false,
            },
        ],
        ..Pcb::default()
    }
}

fn fixture_locked_critical_track() -> Pcb {
    Pcb {
        version: 20_240_108,
        generator: "kiclaude".to_string(),
        thickness_mm: 1.6,
        paper: "A4".to_string(),
        layers: two_layer_signal_stack(),
        nets: vec![Net {
            name: "CLK_25MHZ".to_string(),
            ..Net::default()
        }],
        tracks: vec![Track {
            uuid: "t-clk-1".to_string(),
            layer: LayerRef("F.Cu".to_string()),
            net: "CLK_25MHZ".to_string(),
            points_mm: vec![(10.0, 10.0), (60.0, 10.0)],
            width_mm: 0.25,
            locked: true,
        }],
        vias: vec![Via {
            uuid: "v-locked-1".to_string(),
            net: "CLK_25MHZ".to_string(),
            position_mm: (60.0, 10.0),
            from_layer: LayerRef("F.Cu".to_string()),
            to_layer: LayerRef("B.Cu".to_string()),
            drill_mm: 0.3,
            diameter_mm: 0.6,
            kind: String::new(),
            locked: true,
        }],
        ..Pcb::default()
    }
}

fn fixture_led_with_zone() -> Pcb {
    Pcb {
        version: 20_240_108,
        generator: "kiclaude".to_string(),
        thickness_mm: 1.6,
        paper: "A4".to_string(),
        layers: two_layer_signal_stack(),
        nets: vec![Net {
            name: "GND".to_string(),
            ..Net::default()
        }],
        zones: vec![Zone {
            uuid: "z-gnd-1".to_string(),
            layer: LayerRef("F.Cu".to_string()),
            net: "GND".to_string(),
            outline_mm: vec![(0.0, 0.0), (60.0, 0.0), (60.0, 40.0), (0.0, 40.0)],
            cutouts_mm: Vec::new(),
            hatched: false,
            clearance_mm: 0.3,
            thermal_relief: true,
            thermal_gap_mm: 0.4,
            thermal_bridge_width_mm: 0.3,
            min_thickness_mm: 0.25,
            connect_pads: "thermal_reliefs".to_string(),
            filled_polygons: Vec::new(),
        }],
        ..Pcb::default()
    }
}

fn fixture_zone_with_cutout() -> Pcb {
    Pcb {
        version: 20_240_108,
        generator: "kiclaude".to_string(),
        thickness_mm: 1.6,
        paper: "A4".to_string(),
        layers: two_layer_signal_stack(),
        nets: vec![Net {
            name: "GND".to_string(),
            ..Net::default()
        }],
        zones: vec![Zone {
            uuid: "z-cutout-1".to_string(),
            layer: LayerRef("F.Cu".to_string()),
            net: "GND".to_string(),
            outline_mm: vec![(0.0, 0.0), (60.0, 0.0), (60.0, 40.0), (0.0, 40.0)],
            cutouts_mm: vec![vec![(20.0, 15.0), (40.0, 15.0), (40.0, 25.0), (20.0, 25.0)]],
            hatched: false,
            clearance_mm: 0.2,
            thermal_relief: false,
            thermal_gap_mm: 0.0,
            thermal_bridge_width_mm: 0.0,
            min_thickness_mm: 0.25,
            connect_pads: "yes".to_string(),
            filled_polygons: Vec::new(),
        }],
        ..Pcb::default()
    }
}

fn fixture_hatched_ground_plane() -> Pcb {
    Pcb {
        version: 20_240_108,
        generator: "kiclaude".to_string(),
        thickness_mm: 1.6,
        paper: "A4".to_string(),
        layers: two_layer_signal_stack(),
        nets: vec![Net {
            name: "GND".to_string(),
            ..Net::default()
        }],
        zones: vec![Zone {
            uuid: "z-hatch-1".to_string(),
            layer: LayerRef("B.Cu".to_string()),
            net: "GND".to_string(),
            outline_mm: vec![(0.0, 0.0), (40.0, 0.0), (40.0, 40.0), (0.0, 40.0)],
            cutouts_mm: Vec::new(),
            hatched: true,
            clearance_mm: 0.3,
            thermal_relief: false,
            thermal_gap_mm: 0.0,
            thermal_bridge_width_mm: 0.0,
            min_thickness_mm: 0.25,
            connect_pads: "yes".to_string(),
            filled_polygons: Vec::new(),
        }],
        ..Pcb::default()
    }
}

fn fixture_netclass_diff_pair() -> Pcb {
    Pcb {
        version: 20_240_108,
        generator: "kiclaude".to_string(),
        thickness_mm: 1.6,
        paper: "A4".to_string(),
        layers: two_layer_signal_stack(),
        net_classes: vec![
            NetClass {
                name: "Default".to_string(),
                description: "Default class".to_string(),
                clearance_mm: 0.2,
                trace_width_mm: 0.25,
                via_drill_mm: 0.3,
                via_diameter_mm: 0.6,
                diff_pair_width_mm: None,
                diff_pair_gap_mm: None,
            },
            NetClass {
                name: "DiffPair_USB".to_string(),
                description: "USB 2.0 / 90 ohm diff pair".to_string(),
                clearance_mm: 0.2,
                trace_width_mm: 0.2,
                via_drill_mm: 0.3,
                via_diameter_mm: 0.5,
                diff_pair_width_mm: Some(0.18),
                diff_pair_gap_mm: Some(0.12),
            },
        ],
        nets: vec![
            Net {
                name: "USB_DP".to_string(),
                class: NetClassRef("DiffPair_USB".to_string()),
                ..Net::default()
            },
            Net {
                name: "USB_DM".to_string(),
                class: NetClassRef("DiffPair_USB".to_string()),
                ..Net::default()
            },
        ],
        tracks: vec![
            Track {
                uuid: "t-dp-1".to_string(),
                layer: LayerRef("F.Cu".to_string()),
                net: "USB_DP".to_string(),
                points_mm: vec![(0.0, 0.0), (20.0, 0.0)],
                width_mm: 0.18,
                locked: false,
            },
            Track {
                uuid: "t-dm-1".to_string(),
                layer: LayerRef("F.Cu".to_string()),
                net: "USB_DM".to_string(),
                points_mm: vec![(0.0, 0.3), (20.0, 0.3)],
                width_mm: 0.18,
                locked: false,
            },
        ],
        ..Pcb::default()
    }
}

fn fixture_silkscreen_text() -> Pcb {
    use kiclaude_ki::kcir::Drawing;
    Pcb {
        version: 20_240_108,
        generator: "kiclaude".to_string(),
        thickness_mm: 1.6,
        paper: "A4".to_string(),
        layers: two_layer_signal_stack(),
        drawings: vec![
            Drawing {
                uuid: "d-text-1".to_string(),
                layer: LayerRef("F.SilkS".to_string()),
                kind: "gr_text".to_string(),
                points_mm: vec![(25.0, 5.0)],
                width_mm: 0.15,
                text: "kiclaude v0.1".to_string(),
            },
            Drawing {
                uuid: "d-circle-1".to_string(),
                layer: LayerRef("F.SilkS".to_string()),
                kind: "gr_circle".to_string(),
                points_mm: vec![(50.0, 20.0), (52.0, 20.0)],
                width_mm: 0.1,
                text: String::new(),
            },
        ],
        ..Pcb::default()
    }
}

/// One footprint that carries a 3D model — verifies the model survives
/// the round-trip. Not in the 10-fixture set but kept as a regression
/// guard for `crates/ki::kcir::Model3D`.
#[allow(dead_code)]
fn fixture_with_3d_model() -> Pcb {
    Pcb {
        version: 20_240_108,
        generator: "kiclaude".to_string(),
        thickness_mm: 1.6,
        paper: "A4".to_string(),
        layers: two_layer_signal_stack(),
        footprints: vec![FootprintInstance {
            uuid: "3d-1".to_string(),
            refdes: "U1".to_string(),
            lib_id: "Package_QFN:QFN-48-1EP_7x7mm_P0.5mm".to_string(),
            value: "STM32F411".to_string(),
            layer: LayerRef("F.Cu".to_string()),
            position_mm: (50.0, 50.0),
            rotation_deg: 0.0,
            models_3d: vec![Model3D::identity(
                "${KICAD9_3DMODEL_DIR}/Package_QFN.3dshapes/QFN-48-1EP_7x7mm_P0.5mm.step",
            )],
            ..FootprintInstance::default()
        }],
        ..Pcb::default()
    }
}

/// `(filename, builder)` pair for one canonical fixture.
type Fixture = (&'static str, fn() -> Pcb);

/// All ten canonical fixture files paired with their KCIR builder. The
/// blinky fixture lives at the workspace level (next to `Cargo.toml`)
/// for historical reasons; the rest live under `fixtures/`.
fn all_fixtures() -> Vec<Fixture> {
    vec![
        ("resistor_smd_pads.kicad_pcb", fixture_resistor_smd_pads),
        ("tht_pin_header.kicad_pcb", fixture_tht_pin_header),
        (
            "four_layer_blind_via.kicad_pcb",
            fixture_four_layer_blind_via,
        ),
        (
            "locked_critical_track.kicad_pcb",
            fixture_locked_critical_track,
        ),
        ("led_with_zone.kicad_pcb", fixture_led_with_zone),
        ("zone_with_cutout.kicad_pcb", fixture_zone_with_cutout),
        (
            "hatched_ground_plane.kicad_pcb",
            fixture_hatched_ground_plane,
        ),
        ("netclass_diff_pair.kicad_pcb", fixture_netclass_diff_pair),
        ("silkscreen_text.kicad_pcb", fixture_silkscreen_text),
    ]
}

// ---------------------------------------------------------------------
// Tier A — byte-identical round-trip across 10 canonical PCBs.
// ---------------------------------------------------------------------

#[test]
fn ten_canonical_pcbs_round_trip_byte_identical() {
    let dir = fixtures_dir();
    assert!(
        dir.is_dir(),
        "fixtures dir {} missing — run \
         `cargo test -p kiclaude-golden -- --ignored regenerate_pcb_fixtures` first",
        dir.display()
    );

    let mut walked = 0usize;
    for (name, builder) in all_fixtures() {
        let path = dir.join(name);
        let original = fs::read_to_string(&path)
            .unwrap_or_else(|err| panic!("read {}: {err}", path.display()));
        let parsed_nodes =
            parse_str(&original).unwrap_or_else(|err| panic!("parse {}: {err}", path.display()));
        let root = parsed_nodes
            .first()
            .unwrap_or_else(|| panic!("empty parse for {}", path.display()));
        let kcir = map_pcb(root).unwrap_or_else(|err| panic!("map_pcb {}: {err}", path.display()));
        let re_emitted = emit_pcb(&kcir);

        if re_emitted != original {
            let diff = format_diff(
                &format!("{} (on-disk)", path.display()),
                &original,
                "emit(map_pcb(...)) (canonical)",
                &re_emitted,
            );
            panic!(
                "M2-Q-01 round-trip diverged for {}\n\n{diff}\n\
                 If the canonical form was intentionally changed, run\n  \
                 cargo test -p kiclaude-golden -- --ignored regenerate_pcb_fixtures",
                path.display(),
            );
        }

        // Cross-check with the synthesized KCIR: building the fixture
        // from scratch and emitting must produce the same bytes the
        // file holds.
        let synthesized = emit_pcb(&builder());
        assert_eq!(
            synthesized,
            original,
            "synthesized KCIR for {} does not match its on-disk fixture",
            path.display()
        );
        walked += 1;
    }

    // blinky lives next to tests/golden/, not under fixtures/.
    let blinky_path = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("blinky.kicad_pcb");
    let blinky_text = fs::read_to_string(&blinky_path)
        .unwrap_or_else(|err| panic!("read {}: {err}", blinky_path.display()));
    let nodes = parse_str(&blinky_text).expect("blinky parses");
    let kcir = map_pcb(&nodes[0]).expect("blinky map_pcb");
    let re_emitted = emit_pcb(&kcir);
    assert_eq!(
        re_emitted, blinky_text,
        "blinky round-trip is the M0 gate and must stay byte-identical"
    );
    walked += 1;

    assert_eq!(
        walked, 10,
        "M2-Q-01 expects exactly 10 reference PCBs (got {walked})"
    );
}

// ---------------------------------------------------------------------
// Tier B — every shipped KiCad-9 reference board parses without panic.
// ---------------------------------------------------------------------

#[test]
fn every_shipped_kicad_reference_board_parses() {
    let root = workspace_root();
    let candidates = [
        root.join("development/resources/kicad/kicad-library/template"),
        root.join("development/resources/kicad/freerouting/examples"),
    ];
    let mut walked = 0usize;
    let mut failed: Vec<(PathBuf, String)> = Vec::new();
    for base in &candidates {
        if !base.is_dir() {
            continue;
        }
        for entry in WalkDir::new(base).into_iter().filter_map(Result::ok) {
            let path = entry.path();
            if path.extension().and_then(|s| s.to_str()) != Some("kicad_pcb") {
                continue;
            }
            let text = match fs::read_to_string(path) {
                Ok(t) => t,
                Err(err) => {
                    failed.push((path.to_path_buf(), format!("read: {err}")));
                    continue;
                }
            };
            let parsed = match parse_str(&text) {
                Ok(nodes) => nodes,
                Err(err) => {
                    failed.push((path.to_path_buf(), format!("parse_str: {err}")));
                    continue;
                }
            };
            let Some(root_node) = parsed.first() else {
                failed.push((path.to_path_buf(), "empty parse".to_string()));
                continue;
            };
            if let Err(err) = map_pcb(root_node) {
                failed.push((path.to_path_buf(), format!("map_pcb: {err}")));
                continue;
            }
            walked += 1;
        }
    }
    assert!(
        walked >= 1,
        "Tier B expected at least one .kicad_pcb under the KiCad reference resources (walked {walked})"
    );
    if !failed.is_empty() {
        let mut report = String::from("Tier B parse failures:\n");
        for (p, msg) in &failed {
            report.push_str(&format!("  {}: {msg}\n", p.display()));
        }
        panic!("{report}");
    }
}

// ---------------------------------------------------------------------
// M2-R-02 — "edit one footprint, only that node's bytes change".
// ---------------------------------------------------------------------

#[test]
fn editing_one_footprint_leaves_other_top_level_forms_byte_identical() {
    let dir = fixtures_dir();
    let path = dir.join("tht_pin_header.kicad_pcb");
    let original = fs::read_to_string(&path).expect("read tht_pin_header");

    let nodes = parse_str(&original).expect("parses");
    let mut kcir = map_pcb(&nodes[0]).expect("map_pcb");

    // Mutate the J1 footprint's `value` field. The byte change must be
    // confined to the `(footprint …)` block containing J1 — every
    // line outside that block must be byte-identical.
    let j1 = kcir
        .footprints
        .iter_mut()
        .find(|f| f.refdes == "J1")
        .expect("J1 present");
    j1.value = "PinHeader_4P".to_string();

    let after = emit_pcb(&kcir);
    assert_ne!(after, original, "value change must change SOMETHING");

    let original_lines: Vec<&str> = original.lines().collect();
    let after_lines: Vec<&str> = after.lines().collect();
    assert_eq!(
        original_lines.len(),
        after_lines.len(),
        "editing a property must not add or remove lines from the file"
    );

    // The tht_pin_header fixture has exactly one footprint (J1), so
    // we just bracket the single `(footprint …)` block and assert
    // every other line is byte-identical.
    let mut inside_footprint = false;
    let mut paren_depth: i32 = 0;
    for (i, (orig, new)) in original_lines.iter().zip(after_lines.iter()).enumerate() {
        if !inside_footprint && orig.trim_start().starts_with("(footprint ") {
            inside_footprint = true;
            paren_depth = 0;
        }
        if !inside_footprint {
            assert_eq!(
                orig, new,
                "line {i} outside the J1 footprint diverged:\n- {orig}\n+ {new}"
            );
        }
        if inside_footprint {
            paren_depth += i32::try_from(orig.matches('(').count()).unwrap_or(0);
            paren_depth -= i32::try_from(orig.matches(')').count()).unwrap_or(0);
            if paren_depth <= 0 {
                inside_footprint = false;
            }
        }
    }
}

// ---------------------------------------------------------------------
// Regenerate helper — writes the 9 fixtures from canonical emit.
// ---------------------------------------------------------------------

/// Re-emit every fixture in canonical form. Run when the emitter's
/// canonical form has been intentionally changed.
#[test]
#[ignore]
fn regenerate_pcb_fixtures() {
    let dir = fixtures_dir();
    fs::create_dir_all(&dir).expect("mkdir fixtures");
    for (name, builder) in all_fixtures() {
        let canonical = emit_pcb(&builder());
        let path = dir.join(name);
        fs::write(&path, &canonical).expect("write fixture");
        eprintln!("regenerated {} ({} bytes)", path.display(), canonical.len());
    }
}
