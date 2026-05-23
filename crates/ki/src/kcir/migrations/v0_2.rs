//! M1-R-07: KCIR `0.1.0` → `0.2.0` migration.
//!
//! 0.2 introduces the full schematic shape (M1-R-01/R-03):
//!
//! - `Sheet` gains `position_mm`, `size_mm`, `pins`.
//! - `SymbolInstance` gains `unit`, `in_bom`, `on_board`, `dnp`,
//!   `is_power_flag`, `is_power_symbol`, `properties`.
//! - `Schematic` gains `lib_symbols`.
//! - `Label` gains `shape`.
//! - `NoConnect` gains `position_mm`.
//!
//! Every addition is purely additive — older documents simply lack
//! the field, and the migration's job is to fill it with the
//! appropriate `Default::default()` so the deserializer doesn't
//! choke.
//!
//! The migration is intentionally tolerant of partially-migrated
//! documents (e.g. a 0.1.x file that already happens to carry some
//! of the 0.2 fields): each helper checks for the key and inserts
//! only when missing.

use serde_json::{json, Map, Value};

/// In-place rewrite of a 0.1.x KCIR `Project` JSON document into 0.2.
pub fn migrate(doc: &mut Value) {
    let Value::Object(top) = doc else {
        return;
    };
    if let Some(schematic) = top.get_mut("schematic") {
        migrate_schematic(schematic);
    }
    top.insert(
        "kcir_version".to_string(),
        Value::String("0.2.0".to_string()),
    );
}

fn migrate_schematic(schematic: &mut Value) {
    let Value::Object(map) = schematic else {
        return;
    };
    // Additive: lib_symbols array.
    map.entry("lib_symbols").or_insert_with(|| json!([]));

    if let Some(Value::Array(sheets)) = map.get_mut("sheets") {
        for sheet in sheets {
            patch_sheet(sheet);
        }
    }
    if let Some(Value::Array(symbols)) = map.get_mut("symbols") {
        for sym in symbols {
            patch_symbol(sym);
        }
    }
    if let Some(Value::Array(labels)) = map.get_mut("labels") {
        for label in labels {
            patch_label(label);
        }
    }
    if let Some(Value::Array(nc)) = map.get_mut("no_connects") {
        for n in nc {
            patch_no_connect(n);
        }
    }
}

fn patch_sheet(sheet: &mut Value) {
    let Value::Object(map) = sheet else {
        return;
    };
    map.entry("position_mm").or_insert_with(zero_pair);
    map.entry("size_mm").or_insert_with(zero_pair);
    map.entry("pins").or_insert_with(|| json!([]));
}

type DefaultFactory = fn() -> Value;
type DefaultInsert = (&'static str, DefaultFactory);

fn patch_symbol(sym: &mut Value) {
    let Value::Object(map) = sym else {
        return;
    };
    let inserts: &[DefaultInsert] = &[
        ("unit", || json!(1)),
        ("in_bom", || json!(true)),
        ("on_board", || json!(true)),
        ("dnp", || json!(false)),
        ("is_power_flag", || json!(false)),
        ("is_power_symbol", || json!(false)),
        ("properties", || json!([])),
    ];
    insert_missing(map, inserts);
}

fn patch_label(label: &mut Value) {
    let Value::Object(map) = label else {
        return;
    };
    map.entry("shape").or_insert_with(|| json!(""));
}

fn patch_no_connect(nc: &mut Value) {
    let Value::Object(map) = nc else {
        return;
    };
    map.entry("position_mm").or_insert_with(zero_pair);
}

fn zero_pair() -> Value {
    json!([0.0, 0.0])
}

fn insert_missing(map: &mut Map<String, Value>, inserts: &[DefaultInsert]) {
    for (key, make) in inserts {
        map.entry((*key).to_string()).or_insert_with(make);
    }
}
