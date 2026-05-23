//! M3-R-07: KCIR `0.3.0` → `0.4.0` migration.
//!
//! 0.4 adds the M3 high-speed surface to [`Pcb`](crate::kcir::Pcb):
//!
//! - `diff_pairs: Vec<DiffPair>` — declared differential pairs used
//!   by the M3-R-04 diff-pair router and the M3-R-02 impedance solver.
//! - `length_groups: Vec<LengthGroup>` — declared length-match groups
//!   the M3-R-05 analyzer reads to compute deltas + tuning queues.
//!
//! Both additions are purely additive — older 0.3 documents simply
//! lack the field. The migration fills each with an empty `[]` so the
//! deserializer doesn't choke. The two new types
//! ([`DiffPair`](crate::kcir::DiffPair),
//! [`LengthGroup`](crate::kcir::LengthGroup)) carry their own
//! `Default` impls so library code that constructs a fresh `Pcb`
//! also gets sensible empties.
//!
//! Note: `Stackup` already lives on `Project` as of 0.1, and
//! `Net.diff_pair: Option<NetRef>` + `NetClass.diff_pair_width_mm` /
//! `diff_pair_gap_mm` already existed in 0.3. 0.4 only adds the
//! **collection** types — the per-entity back-references stay where
//! they were.

use serde_json::Value;

/// In-place rewrite of a 0.3.x KCIR `Project` JSON document into 0.4.
pub fn migrate(doc: &mut Value) {
    let Value::Object(top) = doc else {
        return;
    };
    if let Some(pcb) = top.get_mut("pcb") {
        migrate_pcb(pcb);
    }
    top.insert(
        "kcir_version".to_string(),
        Value::String("0.4.0".to_string()),
    );
}

fn migrate_pcb(pcb: &mut Value) {
    let Value::Object(map) = pcb else {
        return;
    };
    map.entry("diff_pairs")
        .or_insert_with(|| Value::Array(Vec::new()));
    map.entry("length_groups")
        .or_insert_with(|| Value::Array(Vec::new()));
}
