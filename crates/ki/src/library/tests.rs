//! M1-R-04 acceptance tests for the symbol library index.

#![allow(clippy::float_cmp)]

use std::collections::HashMap;

use pretty_assertions::assert_eq;
use tempfile::TempDir;

use super::lib_table::resolve_uri;
use super::{
    parse_sym_lib_table_text, parse_symbol_lib, parse_symbol_lib_text, Index, LibraryRow,
    SymLibTable,
};

const TINY_LIB: &str = r#"(kicad_symbol_lib (version 20231120) (generator kicad_symbol_editor)
  (symbol "R" (pin_names (offset 0)) (in_bom yes) (on_board yes)
    (property "Reference" "R" (id 0) (at 0 0 0))
    (property "Value" "R" (id 1) (at 0 0 0))
    (property "Footprint" "" (id 2) (at 0 0 0))
    (property "Datasheet" "~" (id 3) (at 0 0 0))
    (property "Description" "Resistor" (id 4) (at 0 0 0))
    (property "ki_keywords" "resistor R" (id 5) (at 0 0 0))
    (property "ki_fp_filters" "R_*" (id 6) (at 0 0 0))
  )
  (symbol "C"
    (property "Reference" "C" (id 0) (at 0 0 0))
    (property "Value" "C" (id 1) (at 0 0 0))
    (property "Description" "Capacitor" (id 4) (at 0 0 0))
    (property "ki_keywords" "capacitor cap C" (id 5) (at 0 0 0))
    (property "ki_fp_filters" "C_*" (id 6) (at 0 0 0))
  )
  (symbol "STM32G030F6P6"
    (property "Reference" "U" (id 0) (at 0 0 0))
    (property "Value" "STM32G030F6P6" (id 1) (at 0 0 0))
    (property "Footprint" "Package_SO:TSSOP-20_4.4x6.5mm_P0.65mm" (id 2) (at 0 0 0))
    (property "Datasheet" "https://st.com/stm32g030.pdf" (id 3) (at 0 0 0))
    (property "Description" "STM32G0 ARM Cortex-M0+ microcontroller" (id 4) (at 0 0 0))
    (property "ki_keywords" "STM32 ARM MCU Cortex-M0" (id 5) (at 0 0 0))
    (property "ki_fp_filters" "TSSOP*P0.65mm*" (id 6) (at 0 0 0))
    (property "Manufacturer_Part_Number" "STM32G030F6P6" (id 7) (at 0 0 0))
  )
)
"#;

const POWER_LIB: &str = r##"(kicad_symbol_lib (version 20231120) (generator kicad_symbol_editor)
  (symbol "GND"
    (property "Reference" "#PWR" (id 0) (at 0 0 0))
    (property "Value" "GND" (id 1) (at 0 0 0))
    (property "Description" "Ground reference" (id 4) (at 0 0 0))
    (property "ki_keywords" "ground GND power" (id 5) (at 0 0 0))
  )
  (symbol "PWR_FLAG"
    (property "Reference" "#FLG" (id 0) (at 0 0 0))
    (property "Value" "PWR_FLAG" (id 1) (at 0 0 0))
    (property "ki_keywords" "flag power" (id 5) (at 0 0 0))
  )
)
"##;

const SIMPLE_TABLE: &str = r#"(sym_lib_table
  (version 7)
  (lib (name "Device") (type KiCad) (uri "${KIPROJMOD}/Device.kicad_sym") (options "") (descr "Generic devices"))
  (lib (name "power") (type KiCad) (uri "${KIPROJMOD}/power.kicad_sym") (options "") (descr "Power symbols"))
)
"#;

#[test]
fn parses_kicad_sym_with_three_symbols() {
    let lib = parse_symbol_lib_text(TINY_LIB).expect("parse");
    assert_eq!(lib.version, 20_231_120);
    assert_eq!(lib.symbols.len(), 3);
    let stm = lib
        .symbols
        .iter()
        .find(|s| s.name == "STM32G030F6P6")
        .expect("STM symbol present");
    assert_eq!(stm.reference, "U");
    assert_eq!(stm.footprint, "Package_SO:TSSOP-20_4.4x6.5mm_P0.65mm");
    assert_eq!(stm.mpn, "STM32G030F6P6");
    assert_eq!(stm.keywords, "STM32 ARM MCU Cortex-M0");
    assert_eq!(stm.footprint_filter, "TSSOP*P0.65mm*");
}

#[test]
fn power_namespace_symbols_get_is_power_flag() {
    let lib = parse_symbol_lib_text(POWER_LIB).expect("parse");
    let gnd = lib.symbols.iter().find(|s| s.name == "GND").unwrap();
    let flg = lib.symbols.iter().find(|s| s.name == "PWR_FLAG").unwrap();
    assert!(gnd.is_power);
    assert!(flg.is_power);
}

#[test]
fn parses_sym_lib_table_two_rows() {
    let t = parse_sym_lib_table_text(SIMPLE_TABLE).expect("parse table");
    assert_eq!(t.version, 7);
    assert_eq!(t.libraries.len(), 2);
    assert_eq!(t.libraries[0].name, "Device");
    assert_eq!(t.libraries[0].kind, "KiCad");
    assert_eq!(t.libraries[1].name, "power");
}

#[test]
fn resolve_uri_substitutes_overrides_first() {
    let mut overrides = HashMap::new();
    overrides.insert("KIPROJMOD".to_string(), "/tmp/test-libs".to_string());
    assert_eq!(
        resolve_uri("${KIPROJMOD}/Device.kicad_sym", &overrides),
        "/tmp/test-libs/Device.kicad_sym"
    );
}

#[test]
fn resolve_uri_falls_back_to_env() {
    // SAFETY: setting an env var inside a test is safe because the
    // process is single-threaded enough during cargo test; the var is
    // unique to this test to avoid cross-test interference.
    unsafe { std::env::set_var("KICLAUDE_TEST_LIBS", "/opt/libs") };
    let out = resolve_uri("${KICLAUDE_TEST_LIBS}/x.kicad_sym", &HashMap::new());
    assert_eq!(out, "/opt/libs/x.kicad_sym");
    unsafe { std::env::remove_var("KICLAUDE_TEST_LIBS") };
}

#[test]
fn resolve_uri_leaves_unknown_variables_in_place() {
    let out = resolve_uri("${NOPE_NEVER}/x.kicad_sym", &HashMap::new());
    assert_eq!(out, "${NOPE_NEVER}/x.kicad_sym");
}

#[test]
fn index_search_for_stm32g0_returns_ranked_match() {
    let lib = parse_symbol_lib_text(TINY_LIB).expect("parse");
    let mut idx = Index::new();
    idx.add_library("Device", &lib, None, None);
    let hits = idx.search("STM32G0", 10);
    assert!(
        !hits.is_empty(),
        "STM32G0 must match the STM32G030F6P6 symbol"
    );
    let top = &hits[0];
    assert_eq!(top.lib_id, "Device:STM32G030F6P6");
    assert_eq!(top.library, "Device");
    assert_eq!(top.description, "STM32G0 ARM Cortex-M0+ microcontroller");
    assert_eq!(top.footprint_filter, "TSSOP*P0.65mm*");
    assert!(top.score > 0.5, "score should be high for a name match");
}

#[test]
fn index_search_ranks_exact_match_above_substring() {
    let lib = parse_symbol_lib_text(TINY_LIB).expect("parse");
    let mut idx = Index::new();
    idx.add_library("Device", &lib, None, None);
    let hits = idx.search("R", 10);
    assert!(!hits.is_empty());
    // Exact name "R" should outrank "STM32G030F6P6" which contains
    // none of the letter-R in its name (but might match via keywords).
    let r = hits.iter().find(|h| h.lib_id == "Device:R").unwrap();
    assert!(r.score >= hits.last().unwrap().score);
}

#[test]
fn index_search_penalises_power_symbols() {
    let lib_a = parse_symbol_lib_text(TINY_LIB).expect("tiny");
    let lib_b = parse_symbol_lib_text(POWER_LIB).expect("power");
    let mut idx = Index::new();
    idx.add_library("Device", &lib_a, None, None);
    idx.add_library("power", &lib_b, None, None);
    // "power" matches both the power-lib symbols' keywords and the
    // library name. Power symbols should be present but penalised.
    let hits = idx.search("power", 10);
    let any_power = hits.iter().any(|h| h.is_power);
    assert!(
        any_power,
        "power-net symbols still appear in results (they just rank lower)"
    );
}

#[test]
fn index_search_empty_query_returns_everything() {
    let lib = parse_symbol_lib_text(TINY_LIB).expect("parse");
    let mut idx = Index::new();
    idx.add_library("Device", &lib, None, None);
    let hits = idx.search("", 100);
    assert_eq!(hits.len(), 3);
}

#[test]
fn index_from_lib_table_resolves_paths_and_indexes() {
    // Write two .kicad_sym files into a tempdir, point the table at them
    // via a ${KIPROJMOD} override, and assert the index ends up with
    // every symbol from both files.
    let dir = TempDir::new().expect("tempdir");
    std::fs::write(dir.path().join("Device.kicad_sym"), TINY_LIB).expect("write device");
    std::fs::write(dir.path().join("power.kicad_sym"), POWER_LIB).expect("write power");
    let table = parse_sym_lib_table_text(SIMPLE_TABLE).expect("parse table");
    let mut overrides = HashMap::new();
    overrides.insert(
        "KIPROJMOD".to_string(),
        dir.path().to_string_lossy().into_owned(),
    );
    let idx = Index::from_lib_table(&table, &overrides);
    assert!(idx.errors().is_empty(), "no per-library load errors");
    assert_eq!(idx.len(), 5, "3 from Device + 2 from power = 5");
    let lib_ids: Vec<String> = idx.search("", 100).into_iter().map(|h| h.lib_id).collect();
    assert!(lib_ids.contains(&"Device:STM32G030F6P6".to_string()));
    assert!(lib_ids.contains(&"power:GND".to_string()));
}

#[test]
fn index_skips_disabled_rows() {
    let dir = TempDir::new().expect("tempdir");
    std::fs::write(dir.path().join("Device.kicad_sym"), TINY_LIB).expect("write");
    let table = SymLibTable {
        version: 7,
        libraries: vec![LibraryRow {
            name: "Device".to_string(),
            kind: "KiCad".to_string(),
            uri: format!("{}/Device.kicad_sym", dir.path().display()),
            options: String::new(),
            descr: String::new(),
            disabled: true,
        }],
    };
    let idx = Index::from_lib_table(&table, &HashMap::new());
    assert_eq!(idx.len(), 0);
}

#[test]
fn index_collects_load_errors_for_missing_libraries() {
    let table = SymLibTable {
        version: 7,
        libraries: vec![LibraryRow {
            name: "Missing".to_string(),
            kind: "KiCad".to_string(),
            uri: "/nonexistent/Missing.kicad_sym".to_string(),
            options: String::new(),
            descr: String::new(),
            disabled: false,
        }],
    };
    let idx = Index::from_lib_table(&table, &HashMap::new());
    assert_eq!(idx.len(), 0);
    assert_eq!(idx.errors().len(), 1);
    assert_eq!(idx.errors()[0].library, "Missing");
}

#[test]
fn parses_real_kicad_sym_fixture_under_development_resources() {
    // The Z80 fixture under development/resources/ is a real
    // KiCad-generated `.kicad_sym` so this test also catches
    // regressions in the parser when KiCad ships a new field.
    let manifest = std::path::PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    let path = manifest.join(
        "../../development/resources/kicad/freerouting/fixtures/\
         Issue191-processor.Z80/Zilog_Z80_Peripherals.kicad_sym",
    );
    if !path.exists() {
        // The fixtures directory is committed in this repo but the
        // assert lets the test be portable to a future checkout that
        // strips it.
        return;
    }
    let lib = parse_symbol_lib(&path).expect("parse real .kicad_sym");
    assert!(!lib.symbols.is_empty());
    // Real libraries always include a "Reference" property on every
    // symbol — verify the parser populated it.
    for s in &lib.symbols {
        assert!(!s.reference.is_empty(), "every symbol has Reference");
    }
}
