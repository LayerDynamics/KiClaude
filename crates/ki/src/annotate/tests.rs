//! M1-R-06 acceptance tests for [`super::annotate`].

use crate::annotate::{annotate, AnnotateOptions};
use crate::kcir::{Schematic, SymbolInstance, SymbolProperty};

fn instance(uuid: &str, refdes: &str, lib_id: &str) -> SymbolInstance {
    SymbolInstance {
        uuid: uuid.to_string(),
        lib_id: lib_id.to_string(),
        refdes: refdes.to_string(),
        properties: vec![SymbolProperty {
            key: "Reference".to_string(),
            value: refdes.to_string(),
            ..SymbolProperty::default()
        }],
        ..SymbolInstance::default()
    }
}

fn power_instance(uuid: &str, value: &str) -> SymbolInstance {
    SymbolInstance {
        uuid: uuid.to_string(),
        lib_id: format!("power:{value}"),
        value: value.to_string(),
        is_power_symbol: true,
        refdes: "#PWR?".to_string(),
        properties: vec![SymbolProperty {
            key: "Reference".to_string(),
            value: "#PWR?".to_string(),
            ..SymbolProperty::default()
        }],
        ..SymbolInstance::default()
    }
}

#[test]
fn fresh_annotation_assigns_sequential_numbers_per_prefix() {
    let mut s = Schematic {
        symbols: vec![
            instance("u1", "R?", "Device:R"),
            instance("u2", "R?", "Device:R"),
            instance("u3", "C?", "Device:C"),
            instance("u4", "R?", "Device:R"),
        ],
        ..Schematic::default()
    };
    let report = annotate(&mut s, AnnotateOptions::default());
    let refdes: Vec<_> = s.symbols.iter().map(|i| i.refdes.clone()).collect();
    assert_eq!(refdes, vec!["R1", "R2", "C1", "R3"]);
    assert_eq!(report.renamed, 4);
    assert_eq!(report.kept, 0);
}

#[test]
fn preserves_existing_numbers_unless_reset() {
    let mut s = Schematic {
        symbols: vec![
            instance("u1", "R1", "Device:R"),
            instance("u2", "R?", "Device:R"),
            instance("u3", "R3", "Device:R"),
        ],
        ..Schematic::default()
    };
    annotate(&mut s, AnnotateOptions::default());
    let refdes: Vec<_> = s.symbols.iter().map(|i| i.refdes.clone()).collect();
    // The "?" gets the next available number after the current max (3).
    assert_eq!(refdes, vec!["R1", "R4", "R3"]);
}

#[test]
fn reset_blows_away_existing_numbers_and_re_annotates() {
    let mut s = Schematic {
        symbols: vec![
            instance("u1", "R5", "Device:R"),
            instance("u2", "R12", "Device:R"),
            instance("u3", "C9", "Device:C"),
        ],
        ..Schematic::default()
    };
    annotate(
        &mut s,
        AnnotateOptions {
            reset: true,
            start_at: 1,
        },
    );
    let refdes: Vec<_> = s.symbols.iter().map(|i| i.refdes.clone()).collect();
    assert_eq!(refdes, vec!["R1", "R2", "C1"]);
}

#[test]
fn power_symbols_get_separate_pwr_pool() {
    let mut s = Schematic {
        symbols: vec![
            instance("u1", "R?", "Device:R"),
            power_instance("p1", "GND"),
            power_instance("p2", "VCC"),
            instance("u2", "R?", "Device:R"),
            power_instance("p3", "+3V3"),
        ],
        ..Schematic::default()
    };
    annotate(&mut s, AnnotateOptions::default());
    let refdes: Vec<_> = s.symbols.iter().map(|i| i.refdes.clone()).collect();
    assert_eq!(
        refdes,
        vec!["R1", "#PWR1", "#PWR2", "R2", "#PWR3"],
        "R and #PWR pools advance independently"
    );
}

#[test]
fn pwr_flag_uses_separate_flg_pool() {
    let mut s = Schematic {
        symbols: vec![
            SymbolInstance {
                uuid: "flg-a".to_string(),
                lib_id: "power:PWR_FLAG".to_string(),
                value: "PWR_FLAG".to_string(),
                is_power_symbol: true,
                is_power_flag: true,
                refdes: "#FLG?".to_string(),
                ..SymbolInstance::default()
            },
            SymbolInstance {
                uuid: "flg-b".to_string(),
                lib_id: "power:PWR_FLAG".to_string(),
                value: "PWR_FLAG".to_string(),
                is_power_symbol: true,
                is_power_flag: true,
                refdes: "#FLG?".to_string(),
                ..SymbolInstance::default()
            },
            power_instance("p1", "GND"),
        ],
        ..Schematic::default()
    };
    annotate(&mut s, AnnotateOptions::default());
    let refdes: Vec<_> = s.symbols.iter().map(|i| i.refdes.clone()).collect();
    assert_eq!(refdes, vec!["#FLG1", "#FLG2", "#PWR1"]);
}

#[test]
fn unknown_prefix_falls_back_to_u_pool() {
    let mut s = Schematic {
        symbols: vec![SymbolInstance {
            uuid: "u".to_string(),
            lib_id: "MCU:STM32".to_string(),
            refdes: String::new(), // no prefix at all
            ..SymbolInstance::default()
        }],
        ..Schematic::default()
    };
    annotate(&mut s, AnnotateOptions::default());
    assert_eq!(s.symbols[0].refdes, "U1");
}

#[test]
fn reference_property_is_updated_to_match_new_refdes() {
    let mut s = Schematic {
        symbols: vec![instance("u1", "R?", "Device:R")],
        ..Schematic::default()
    };
    annotate(&mut s, AnnotateOptions::default());
    assert_eq!(s.symbols[0].refdes, "R1");
    let prop = s.symbols[0]
        .properties
        .iter()
        .find(|p| p.key == "Reference")
        .expect("Reference property present");
    assert_eq!(prop.value, "R1");
}

#[test]
fn fully_annotated_symbols_are_left_untouched_when_not_reset() {
    let mut s = Schematic {
        symbols: vec![
            instance("u1", "R1", "Device:R"),
            instance("u2", "R2", "Device:R"),
            instance("u3", "C1", "Device:C"),
        ],
        ..Schematic::default()
    };
    let report = annotate(&mut s, AnnotateOptions::default());
    assert_eq!(report.renamed, 0);
    assert_eq!(report.kept, 3);
    let refdes: Vec<_> = s.symbols.iter().map(|i| i.refdes.clone()).collect();
    assert_eq!(refdes, vec!["R1", "R2", "C1"]);
}

#[test]
fn start_at_offset_shifts_first_assignment() {
    let mut s = Schematic {
        symbols: vec![
            instance("u1", "R?", "Device:R"),
            instance("u2", "R?", "Device:R"),
        ],
        ..Schematic::default()
    };
    annotate(
        &mut s,
        AnnotateOptions {
            reset: false,
            start_at: 10,
        },
    );
    let refdes: Vec<_> = s.symbols.iter().map(|i| i.refdes.clone()).collect();
    assert_eq!(refdes, vec!["R10", "R11"]);
}
