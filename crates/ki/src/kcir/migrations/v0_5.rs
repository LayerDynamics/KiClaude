//! M5: KCIR `0.4.0` → `0.5.0` migration.
//!
//! 0.5 adds the M5 design sign-off surface to
//! [`Pcb`](crate::kcir::Pcb):
//!
//! - `signoff: Signoff` — per-board human review gates
//!   ([`Signoff`](crate::kcir::Signoff)) consumed by the KC060 (DDR
//!   fly-by) and KC070 (BGA fanout) validators. Each flag starts
//!   `false` so an un-reviewed board surfaces those validators as
//!   warnings; the agent's `PreToolUse` permission gate forbids Claude
//!   from setting them.
//!
//! The addition is purely additive — older 0.4 documents simply lack
//! the field. The migration inserts the default
//! `{rf_reviewed:false, ddr_reviewed:false, bga_fanout_reviewed:false}`
//! object so the deserializer doesn't choke.

use serde_json::{json, Value};

/// In-place rewrite of a 0.4.x KCIR `Project` JSON document into 0.5.
pub fn migrate(doc: &mut Value) {
    let Value::Object(top) = doc else {
        return;
    };
    if let Some(pcb) = top.get_mut("pcb") {
        migrate_pcb(pcb);
    }
    top.insert(
        "kcir_version".to_string(),
        Value::String("0.5.0".to_string()),
    );
}

fn migrate_pcb(pcb: &mut Value) {
    let Value::Object(map) = pcb else {
        return;
    };
    map.entry("signoff").or_insert_with(|| {
        json!({
            "rf_reviewed": false,
            "ddr_reviewed": false,
            "bga_fanout_reviewed": false,
        })
    });
}
