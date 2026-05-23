//! M1-R-07 acceptance: a 0.1 document round-trips through
//! [`migrate_to_current`] into a 0.2 document that deserializes
//! into [`Project`](crate::kcir::Project) cleanly.

use serde_json::json;

use crate::kcir::migrations::{migrate_to_current, MigrationError};
use crate::kcir::Project;

#[test]
fn migrate_empty_v0_1_project_to_current() {
    let mut doc = json!({
        "kcir_version": "0.1.0",
        "name": "blinky",
        "schematic": { "sheets": [], "symbols": [], "wires": [], "junctions": [],
                        "labels": [], "no_connects": [], "buses": [] },
        "pcb": { "version": 0, "generator": "", "thickness_mm": 0.0, "paper": "",
                  "pad_to_mask_clearance_mm": 0.0, "layers": [], "footprints": [],
                  "tracks": [], "vias": [], "zones": [],
                  "outline": { "points_mm": [], "cutouts": [] }, "drawings": [], "nets": [] },
        "libraries": { "symbol_libs": [], "footprint_libs": [] },
        "stackup": { "layers": [], "power_plane_layers": [], "controlled_impedance": false,
                      "board_thickness_mm": 0.0, "finish": "" },
        "design_rules": { "clearance_mm": 0.0, "trace_width_mm": 0.0, "via_drill_mm": 0.0,
                           "via_diameter_mm": 0.0, "uvia_drill_mm": 0.0, "uvia_diameter_mm": 0.0,
                           "allow_microvias": false, "allow_blind_buried_vias": false },
        "net_classes": [],
        "fab_target": null,
        "bom_policy": { "preferred_distributors": [], "max_unit_price_usd": null,
                         "require_in_stock": false, "require_jlc_assembly": false, "region": "" },
        "metadata": { "title": "", "revision": "", "company": "", "date": "",
                       "comment_1": "", "comment_2": "", "comment_3": "", "comment_4": "" }
    });
    migrate_to_current(&mut doc).expect("migrate");
    assert_eq!(doc["kcir_version"], crate::KCIR_VERSION);
    // The result must deserialize cleanly into the current `Project`.
    let _project: Project = serde_json::from_value(doc).expect("deserialize migrated doc");
}

#[test]
fn migrate_fills_additive_fields_on_schematic_entities() {
    let mut doc = json!({
        "kcir_version": "0.1.0",
        "name": "x",
        "schematic": {
            "sheets": [
                { "uuid": "s1", "name": "root", "file": "x.kicad_sch", "parent": null }
            ],
            "symbols": [
                { "uuid": "u1", "sheet_uuid": "s1", "lib_id": "Device:R", "refdes": "R1",
                  "value": "10k", "footprint": "", "mpn": "", "datasheet": "",
                  "position_mm": [0.0, 0.0], "rotation_deg": 0.0, "mirrored": false }
            ],
            "wires": [], "junctions": [],
            "labels": [
                { "uuid": "l1", "sheet_uuid": "s1", "kind": "local", "text": "RX",
                  "position_mm": [0.0, 0.0], "rotation_deg": 0.0 }
            ],
            "no_connects": [
                { "uuid": "n1", "sheet_uuid": "s1",
                  "at": { "refdes": "", "pad": "" } }
            ],
            "buses": []
        },
        "pcb": { "version": 0, "generator": "", "thickness_mm": 0.0, "paper": "",
                  "pad_to_mask_clearance_mm": 0.0, "layers": [], "footprints": [],
                  "tracks": [], "vias": [], "zones": [],
                  "outline": { "points_mm": [], "cutouts": [] }, "drawings": [], "nets": [] },
        "libraries": { "symbol_libs": [], "footprint_libs": [] },
        "stackup": { "layers": [], "power_plane_layers": [], "controlled_impedance": false,
                      "board_thickness_mm": 0.0, "finish": "" },
        "design_rules": { "clearance_mm": 0.0, "trace_width_mm": 0.0, "via_drill_mm": 0.0,
                           "via_diameter_mm": 0.0, "uvia_drill_mm": 0.0, "uvia_diameter_mm": 0.0,
                           "allow_microvias": false, "allow_blind_buried_vias": false },
        "net_classes": [], "fab_target": null,
        "bom_policy": { "preferred_distributors": [], "max_unit_price_usd": null,
                         "require_in_stock": false, "require_jlc_assembly": false, "region": "" },
        "metadata": { "title": "", "revision": "", "company": "", "date": "",
                       "comment_1": "", "comment_2": "", "comment_3": "", "comment_4": "" }
    });
    migrate_to_current(&mut doc).expect("migrate");

    let sheet = &doc["schematic"]["sheets"][0];
    assert_eq!(sheet["position_mm"], json!([0.0, 0.0]));
    assert_eq!(sheet["size_mm"], json!([0.0, 0.0]));
    assert_eq!(sheet["pins"], json!([]));

    let symbol = &doc["schematic"]["symbols"][0];
    assert_eq!(symbol["unit"], 1);
    assert_eq!(symbol["in_bom"], true);
    assert_eq!(symbol["on_board"], true);
    assert_eq!(symbol["dnp"], false);
    assert_eq!(symbol["is_power_flag"], false);
    assert_eq!(symbol["is_power_symbol"], false);
    assert_eq!(symbol["properties"], json!([]));

    let label = &doc["schematic"]["labels"][0];
    assert_eq!(label["shape"], "");

    let nc = &doc["schematic"]["no_connects"][0];
    assert_eq!(nc["position_mm"], json!([0.0, 0.0]));

    // lib_symbols now exists on the schematic.
    assert_eq!(doc["schematic"]["lib_symbols"], json!([]));

    // Final deserialization works.
    let _project: Project = serde_json::from_value(doc).expect("deserialize migrated doc");
}

#[test]
fn migrate_already_at_current_is_a_no_op() {
    let mut doc = serde_json::to_value(Project::default()).expect("serialize");
    let before = doc.clone();
    migrate_to_current(&mut doc).expect("migrate");
    assert_eq!(doc, before, "current → current must be the identity");
}

#[test]
fn migrate_rejects_newer_versions() {
    let mut doc = json!({ "kcir_version": "99.0.0" });
    let err = migrate_to_current(&mut doc).expect_err("must reject");
    assert!(
        matches!(&err, MigrationError::NewerThanCurrent { found, .. } if found == "99.0.0"),
        "got {err:?}"
    );
}

#[test]
fn migrate_rejects_missing_version() {
    let mut doc = json!({});
    let err = migrate_to_current(&mut doc).expect_err("must reject");
    assert!(matches!(err, MigrationError::MissingVersion));
}

#[test]
fn migrate_rejects_invalid_semver() {
    let mut doc = json!({ "kcir_version": "not-a-version" });
    let err = migrate_to_current(&mut doc).expect_err("must reject");
    assert!(matches!(err, MigrationError::InvalidVersion(_)));
}
