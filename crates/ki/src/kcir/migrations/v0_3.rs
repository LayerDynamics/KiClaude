//! M2-R-09: KCIR `0.2.0` → `0.3.0` migration.
//!
//! 0.3 introduces the full PCB editing surface (M2-R-03):
//!
//! - `Pcb` gains `solder_mask_min_width_mm`, `net_classes`.
//! - `FootprintInstance` gains `attributes`, `pads`, `courtyard`,
//!   `models_3d`, `drawings`.
//! - `Track` gains `locked`.
//! - `Via` gains `kind`, `locked`.
//! - `Zone` gains `cutouts_mm`, `hatched`, `clearance_mm`,
//!   `thermal_gap_mm`, `thermal_bridge_width_mm`, `min_thickness_mm`,
//!   `connect_pads`, `filled_polygons`.
//!
//! Every addition is purely additive — older 0.2 documents simply lack
//! the field. The migration fills each with the matching
//! [`Default::default()`] so the deserializer doesn't choke. Tolerant
//! of partial pre-migrations: each helper only inserts when the field
//! is missing.

use serde_json::{json, Map, Value};

/// In-place rewrite of a 0.2.x KCIR `Project` JSON document into 0.3.
pub fn migrate(doc: &mut Value) {
    let Value::Object(top) = doc else {
        return;
    };
    if let Some(pcb) = top.get_mut("pcb") {
        migrate_pcb(pcb);
    }
    top.insert(
        "kcir_version".to_string(),
        Value::String("0.3.0".to_string()),
    );
}

fn migrate_pcb(pcb: &mut Value) {
    let Value::Object(map) = pcb else {
        return;
    };
    map.entry("solder_mask_min_width_mm")
        .or_insert_with(|| json!(0.0));
    map.entry("net_classes").or_insert_with(|| json!([]));

    if let Some(Value::Array(footprints)) = map.get_mut("footprints") {
        for fp in footprints {
            patch_footprint(fp);
        }
    }
    if let Some(Value::Array(tracks)) = map.get_mut("tracks") {
        for t in tracks {
            patch_track(t);
        }
    }
    if let Some(Value::Array(vias)) = map.get_mut("vias") {
        for v in vias {
            patch_via(v);
        }
    }
    if let Some(Value::Array(zones)) = map.get_mut("zones") {
        for z in zones {
            patch_zone(z);
        }
    }
}

fn patch_footprint(fp: &mut Value) {
    let Value::Object(map) = fp else {
        return;
    };
    let inserts: &[DefaultInsert] = &[
        ("attributes", || json!([])),
        ("pads", || json!([])),
        ("courtyard", || Value::Null),
        ("models_3d", || json!([])),
        ("drawings", || json!([])),
    ];
    insert_missing(map, inserts);
}

fn patch_track(t: &mut Value) {
    let Value::Object(map) = t else {
        return;
    };
    map.entry("locked").or_insert_with(|| json!(false));
}

fn patch_via(v: &mut Value) {
    let Value::Object(map) = v else {
        return;
    };
    map.entry("kind").or_insert_with(|| json!(""));
    map.entry("locked").or_insert_with(|| json!(false));
}

fn patch_zone(z: &mut Value) {
    let Value::Object(map) = z else {
        return;
    };
    let inserts: &[DefaultInsert] = &[
        ("cutouts_mm", || json!([])),
        ("hatched", || json!(false)),
        ("clearance_mm", || json!(0.0)),
        ("thermal_gap_mm", || json!(0.0)),
        ("thermal_bridge_width_mm", || json!(0.0)),
        ("min_thickness_mm", || json!(0.0)),
        ("connect_pads", || json!("yes")),
        ("filled_polygons", || json!([])),
    ];
    insert_missing(map, inserts);
}

type DefaultFactory = fn() -> Value;
type DefaultInsert = (&'static str, DefaultFactory);

fn insert_missing(map: &mut Map<String, Value>, inserts: &[DefaultInsert]) {
    for (key, make) in inserts {
        map.entry((*key).to_string()).or_insert_with(make);
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn seed_0_2() -> Value {
        json!({
            "kcir_version": "0.2.0",
            "pcb": {
                "version": 20_240_108,
                "generator": "kiclaude",
                "layers": [],
                "footprints": [
                    {
                        "uuid": "u-1",
                        "refdes": "R1",
                        "lib_id": "Device:R",
                        "value": "10k",
                        "mpn": "",
                        "layer": "F.Cu",
                        "position_mm": [0.0, 0.0],
                        "rotation_deg": 0.0,
                        "locked": false
                    }
                ],
                "tracks": [
                    {
                        "uuid": "t-1",
                        "layer": "F.Cu",
                        "net": "VCC",
                        "points_mm": [[0.0, 0.0], [1.0, 0.0]],
                        "width_mm": 0.25
                    }
                ],
                "vias": [
                    {
                        "uuid": "v-1",
                        "net": "GND",
                        "position_mm": [0.0, 0.0],
                        "from_layer": "F.Cu",
                        "to_layer": "B.Cu",
                        "drill_mm": 0.3,
                        "diameter_mm": 0.6
                    }
                ],
                "zones": [
                    {
                        "uuid": "z-1",
                        "layer": "F.Cu",
                        "net": "GND",
                        "outline_mm": [[0.0, 0.0], [1.0, 0.0]],
                        "thermal_relief": true
                    }
                ]
            }
        })
    }

    #[test]
    fn migrate_inserts_new_pcb_fields() {
        let mut doc = seed_0_2();
        migrate(&mut doc);
        assert_eq!(doc["kcir_version"], "0.3.0");
        assert_eq!(doc["pcb"]["solder_mask_min_width_mm"], 0.0);
        assert!(doc["pcb"]["net_classes"].is_array());
    }

    #[test]
    fn migrate_inserts_footprint_pads_and_courtyard() {
        let mut doc = seed_0_2();
        migrate(&mut doc);
        let fp = &doc["pcb"]["footprints"][0];
        assert!(fp["pads"].is_array());
        assert!(fp["attributes"].is_array());
        assert!(fp["models_3d"].is_array());
        assert!(fp["drawings"].is_array());
        assert!(fp["courtyard"].is_null());
    }

    #[test]
    fn migrate_inserts_track_locked() {
        let mut doc = seed_0_2();
        migrate(&mut doc);
        assert_eq!(doc["pcb"]["tracks"][0]["locked"], false);
    }

    #[test]
    fn migrate_inserts_via_kind_and_locked() {
        let mut doc = seed_0_2();
        migrate(&mut doc);
        assert_eq!(doc["pcb"]["vias"][0]["kind"], "");
        assert_eq!(doc["pcb"]["vias"][0]["locked"], false);
    }

    #[test]
    fn migrate_inserts_zone_defaults_with_connect_pads_yes() {
        let mut doc = seed_0_2();
        migrate(&mut doc);
        let z = &doc["pcb"]["zones"][0];
        assert!(z["cutouts_mm"].is_array());
        assert_eq!(z["connect_pads"], "yes");
        assert_eq!(z["clearance_mm"], 0.0);
        assert_eq!(z["min_thickness_mm"], 0.0);
        assert!(z["filled_polygons"].is_array());
    }

    #[test]
    fn migrate_is_idempotent_on_already_migrated_docs() {
        let mut doc = seed_0_2();
        migrate(&mut doc);
        let once = doc.clone();
        migrate(&mut doc);
        assert_eq!(doc, once, "migration must be idempotent");
    }

    #[test]
    fn migrate_preserves_existing_field_values() {
        let mut doc = seed_0_2();
        // Inject a non-default value for `connect_pads` to confirm it
        // survives migration.
        doc["pcb"]["zones"][0]["connect_pads"] = json!("thru_hole_only");
        doc["pcb"]["zones"][0]["clearance_mm"] = json!(0.5);
        migrate(&mut doc);
        assert_eq!(doc["pcb"]["zones"][0]["connect_pads"], "thru_hole_only");
        assert_eq!(doc["pcb"]["zones"][0]["clearance_mm"], 0.5);
    }
}
