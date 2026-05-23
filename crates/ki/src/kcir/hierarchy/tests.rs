//! M1-R-05 acceptance tests for label propagation across a
//! multi-sheet hierarchy. Eight tests cover the corner cases the
//! plan calls out: orphan label, duplicate global, conflicting
//! hierarchical, plus the "happy path" three-sheet hierarchy.

use std::collections::BTreeSet;

use crate::kcir::hierarchy::{breadth_first_sheets, endpoints_for_label, resolve_nets, LabelRef};
use crate::kcir::{Label, LabelKind, Schematic, Sheet, SheetPin, SymbolInstance};

const ROOT: &str = "00000000-0000-0000-0000-000000000001";
const A: &str = "00000000-0000-0000-0000-0000000000a0";
const B: &str = "00000000-0000-0000-0000-0000000000b0";

fn label(uuid: &str, sheet: &str, kind: LabelKind, text: &str) -> Label {
    Label {
        uuid: uuid.to_string(),
        sheet_uuid: sheet.to_string(),
        kind,
        text: text.to_string(),
        position_mm: (0.0, 0.0),
        rotation_deg: 0.0,
        shape: String::new(),
    }
}

fn three_sheet() -> Schematic {
    let root = Sheet {
        uuid: ROOT.to_string(),
        name: "root".to_string(),
        file: "root.kicad_sch".to_string(),
        parent: None,
        ..Sheet::default()
    };
    let sheet_a = Sheet {
        uuid: A.to_string(),
        name: "A".to_string(),
        file: "a.kicad_sch".to_string(),
        parent: Some(ROOT.to_string()),
        pins: vec![SheetPin {
            uuid: "pin-a-data".to_string(),
            name: "DATA".to_string(),
            shape: "input".to_string(),
            position_mm: (0.0, 0.0),
            rotation_deg: 0.0,
        }],
        ..Sheet::default()
    };
    let sheet_b = Sheet {
        uuid: B.to_string(),
        name: "B".to_string(),
        file: "b.kicad_sch".to_string(),
        parent: Some(A.to_string()),
        pins: vec![SheetPin {
            uuid: "pin-b-data".to_string(),
            name: "DATA".to_string(),
            shape: "input".to_string(),
            position_mm: (0.0, 0.0),
            rotation_deg: 0.0,
        }],
        ..Sheet::default()
    };
    Schematic {
        sheets: vec![root, sheet_a, sheet_b],
        labels: vec![
            label("l-b-data", B, LabelKind::Hierarchical, "DATA"),
            label("l-a-data", A, LabelKind::Hierarchical, "DATA"),
            label("l-root-vcc", ROOT, LabelKind::Global, "VCC"),
            label("l-b-vcc", B, LabelKind::Global, "VCC"),
        ],
        ..Schematic::default()
    }
}

#[test]
fn t1_happy_path_three_sheet_hierarchy() {
    let s = three_sheet();
    let graph = resolve_nets(&s);
    // The "DATA" net pools both hierarchical labels + both sheet pins.
    let data_net = graph.nets.get("DATA").expect("DATA net present");
    assert!(data_net.contains(&LabelRef::Label {
        sheet_uuid: B.to_string(),
        label_uuid: "l-b-data".to_string(),
        kind: LabelKind::Hierarchical
    }));
    assert!(data_net.contains(&LabelRef::Label {
        sheet_uuid: A.to_string(),
        label_uuid: "l-a-data".to_string(),
        kind: LabelKind::Hierarchical
    }));
    assert!(data_net.contains(&LabelRef::SheetPin {
        sheet_uuid: A.to_string(),
        pin_uuid: "pin-a-data".to_string(),
        name: "DATA".to_string(),
    }));
    assert!(data_net.contains(&LabelRef::SheetPin {
        sheet_uuid: B.to_string(),
        pin_uuid: "pin-b-data".to_string(),
        name: "DATA".to_string(),
    }));
}

#[test]
fn t2_global_label_shares_net_across_sheets() {
    let s = three_sheet();
    let graph = resolve_nets(&s);
    let vcc_net = graph.nets.get("VCC").expect("VCC net present");
    let root_label = LabelRef::Label {
        sheet_uuid: ROOT.to_string(),
        label_uuid: "l-root-vcc".to_string(),
        kind: LabelKind::Global,
    };
    let b_label = LabelRef::Label {
        sheet_uuid: B.to_string(),
        label_uuid: "l-b-vcc".to_string(),
        kind: LabelKind::Global,
    };
    assert!(vcc_net.contains(&root_label));
    assert!(vcc_net.contains(&b_label));
}

#[test]
fn t3_local_labels_with_same_name_on_different_sheets_are_separate_nets() {
    let s = Schematic {
        sheets: vec![
            Sheet {
                uuid: ROOT.to_string(),
                name: "root".to_string(),
                file: "root.kicad_sch".to_string(),
                parent: None,
                ..Sheet::default()
            },
            Sheet {
                uuid: A.to_string(),
                name: "A".to_string(),
                file: "a.kicad_sch".to_string(),
                parent: Some(ROOT.to_string()),
                ..Sheet::default()
            },
        ],
        labels: vec![
            label("l1", ROOT, LabelKind::Local, "RX"),
            label("l2", A, LabelKind::Local, "RX"),
        ],
        ..Schematic::default()
    };
    let graph = resolve_nets(&s);
    assert!(
        graph.nets.contains_key(&format!("{ROOT}/RX")),
        "root's RX gets its own net"
    );
    assert!(
        graph.nets.contains_key(&format!("{A}/RX")),
        "A's RX gets its own net"
    );
    // Their endpoint sets must be disjoint.
    let root_net = &graph.nets[&format!("{ROOT}/RX")];
    let a_net = &graph.nets[&format!("{A}/RX")];
    let inter: BTreeSet<_> = root_net.intersection(a_net).collect();
    assert!(inter.is_empty());
}

#[test]
fn t4_orphan_hierarchical_label_with_no_matching_pin() {
    // A hierarchical label exists on sheet A but the (sheet A) block
    // has no matching pin — the label is dangling.
    let mut s = three_sheet();
    // Remove the pin on sheet A.
    s.sheets[1].pins.clear();
    // Add a hierarchical label on A that no longer has a pin.
    s.labels
        .push(label("l-orphan", A, LabelKind::Hierarchical, "ORPHAN"));
    let graph = resolve_nets(&s);
    assert!(
        graph
            .orphan_labels
            .iter()
            .any(|r| matches!(r, LabelRef::Label { label_uuid, .. } if label_uuid == "l-orphan")),
        "ORPHAN label is recorded as orphan"
    );
    // The pre-existing "DATA" hierarchical label on A also became
    // orphan when we cleared the pins — verify.
    assert!(graph.orphan_labels.iter().any(|r| matches!(
        r,
        LabelRef::Label { label_uuid, .. } if label_uuid == "l-a-data"
    )));
}

#[test]
fn t5_duplicate_global_label_pools_to_one_net() {
    let s = Schematic {
        sheets: vec![Sheet {
            uuid: ROOT.to_string(),
            name: "root".to_string(),
            file: "root.kicad_sch".to_string(),
            parent: None,
            ..Sheet::default()
        }],
        labels: vec![
            label("g1", ROOT, LabelKind::Global, "GND"),
            label("g2", ROOT, LabelKind::Global, "GND"),
            label("g3", ROOT, LabelKind::Global, "GND"),
        ],
        ..Schematic::default()
    };
    let graph = resolve_nets(&s);
    let gnd = graph.nets.get("GND").expect("GND present");
    assert_eq!(gnd.len(), 3, "all three globals pool into a single net");
}

#[test]
fn t6_conflicting_hierarchical_pin_records_a_conflict() {
    // Two children of the same parent both define a (sheet …) block
    // with a pin named "DATA". Because both children are drawn on
    // the same parent sheet, the pin name "DATA" is claimed by both
    // — KiCad would flag this in ERC.
    let s = Schematic {
        sheets: vec![
            Sheet {
                uuid: ROOT.to_string(),
                name: "root".to_string(),
                parent: None,
                ..Sheet::default()
            },
            Sheet {
                uuid: A.to_string(),
                name: "A".to_string(),
                parent: Some(ROOT.to_string()),
                pins: vec![SheetPin {
                    uuid: "pin-a-data".to_string(),
                    name: "DATA".to_string(),
                    ..SheetPin::default()
                }],
                ..Sheet::default()
            },
            Sheet {
                uuid: B.to_string(),
                name: "B".to_string(),
                parent: Some(ROOT.to_string()),
                pins: vec![SheetPin {
                    uuid: "pin-b-data".to_string(),
                    name: "DATA".to_string(),
                    ..SheetPin::default()
                }],
                ..Sheet::default()
            },
        ],
        ..Schematic::default()
    };
    let graph = resolve_nets(&s);
    assert_eq!(graph.conflicting_hierarchical_pins.len(), 1);
    let c = &graph.conflicting_hierarchical_pins[0];
    assert_eq!(c.pin_name, "DATA");
    assert_eq!(c.claimed_by.len(), 2);
}

#[test]
fn t7_power_symbols_contribute_their_value_as_net_name() {
    let s = Schematic {
        sheets: vec![Sheet {
            uuid: ROOT.to_string(),
            name: "root".to_string(),
            parent: None,
            ..Sheet::default()
        }],
        symbols: vec![
            SymbolInstance {
                uuid: "pwr-gnd".to_string(),
                sheet_uuid: ROOT.to_string(),
                lib_id: "power:GND".to_string(),
                value: "GND".to_string(),
                is_power_symbol: true,
                ..SymbolInstance::default()
            },
            SymbolInstance {
                uuid: "pwr-vcc".to_string(),
                sheet_uuid: ROOT.to_string(),
                lib_id: "power:VCC".to_string(),
                value: "+3V3".to_string(),
                is_power_symbol: true,
                ..SymbolInstance::default()
            },
            // PWR_FLAG is power-symbol but does NOT contribute a net.
            SymbolInstance {
                uuid: "flg".to_string(),
                sheet_uuid: ROOT.to_string(),
                lib_id: "power:PWR_FLAG".to_string(),
                value: "PWR_FLAG".to_string(),
                is_power_symbol: true,
                is_power_flag: true,
                ..SymbolInstance::default()
            },
        ],
        labels: vec![label("l-glob-vcc", ROOT, LabelKind::Global, "+3V3")],
        ..Schematic::default()
    };
    let graph = resolve_nets(&s);
    let gnd = graph.nets.get("GND").expect("GND from power symbol");
    assert!(gnd.iter().any(
        |r| matches!(r, LabelRef::PowerSymbol { symbol_uuid, .. } if symbol_uuid == "pwr-gnd")
    ));
    // The +3V3 power symbol shares its net with the +3V3 global label.
    let v3 = graph.nets.get("+3V3").expect("+3V3 net");
    assert!(v3
        .iter()
        .any(|r| matches!(r, LabelRef::PowerSymbol { value, .. } if value == "+3V3")));
    assert!(v3
        .iter()
        .any(|r| matches!(r, LabelRef::Label { label_uuid, .. } if label_uuid == "l-glob-vcc")));
    // PWR_FLAG must NOT have produced a net.
    assert!(!graph.nets.contains_key("PWR_FLAG"));
}

#[test]
fn t8_endpoints_for_label_returns_the_full_net() {
    let s = three_sheet();
    let graph = resolve_nets(&s);
    let label = &s.labels[0]; // l-b-data, the hierarchical label in B
    let endpoints = endpoints_for_label(&graph, label).expect("DATA net found");
    assert_eq!(endpoints.len(), 4, "two labels + two sheet pins");
}

#[test]
fn breadth_first_sheets_visits_root_then_children_then_grandchildren() {
    let s = three_sheet();
    let order = breadth_first_sheets(&s);
    let pos = |uuid: &str| order.iter().position(|u| u == uuid).expect("present");
    assert!(pos(ROOT) < pos(A), "root before A");
    assert!(pos(A) < pos(B), "A before B");
    assert_eq!(order.len(), 3);
}
