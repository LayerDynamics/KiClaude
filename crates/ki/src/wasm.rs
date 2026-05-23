//! `wasm-bindgen` shim — exposes the M0 KCIR surface to JavaScript.
//!
//! Compiled only for the `wasm32-unknown-unknown` target via a
//! `#[cfg(target_arch = "wasm32")]` gate in `lib.rs`, so native builds
//! pay no compile-time or binary cost for these bindings.
//!
//! API surface (kept deliberately small for M0; richer edit operations
//! land in M2):
//! - [`crate_version`] / [`kcir_version`] — version strings for the UI.
//! - [`open_project_from_strings`] — parse a `.kicad_pro` JSON + a
//!   `.kicad_pcb` S-expression and return the resulting `kcir::Project`
//!   as a JS value via `serde-wasm-bindgen`.
//! - [`emit_pcb_from_json`] — take a JSON-encoded `kcir::Pcb` and return
//!   the canonical `.kicad_pcb` text.

use wasm_bindgen::prelude::*;

use crate::format::v9::{emit_pcb, pcb::map_pcb, project};
use crate::kcir;
use crate::sexpr::parse_str;

/// `kiclaude-ki` crate version, e.g. `"0.1.0"`.
#[wasm_bindgen(js_name = crateVersion)]
#[must_use]
pub fn crate_version() -> String {
    crate::CRATE_VERSION.to_string()
}

/// KCIR schema version, e.g. `"0.1.0"`.
#[wasm_bindgen(js_name = kcirVersion)]
#[must_use]
pub fn kcir_version() -> String {
    crate::KCIR_VERSION.to_string()
}

/// Parse a project from its raw `.kicad_pro` JSON + `.kicad_pcb`
/// S-expression text. Returns the resulting [`kcir::Project`] as a JS
/// object (via `serde-wasm-bindgen`).
///
/// # Errors
/// Returns a `JsValue` string containing the human-readable error if
/// either text is malformed or the PCB root isn't `(kicad_pcb …)`.
#[wasm_bindgen(js_name = openProjectFromStrings)]
pub fn open_project_from_strings(
    pro_json: &str,
    pcb_sexpr: &str,
    fallback_name: &str,
) -> Result<JsValue, JsValue> {
    let pro_doc: project::ProjectDoc = serde_json::from_str(pro_json)
        .map_err(|e| JsValue::from_str(&format!("invalid .kicad_pro JSON: {e}")))?;
    let mut project = kcir::Project::default();
    project::apply_project_doc(&pro_doc, fallback_name, &mut project);

    if !pcb_sexpr.trim().is_empty() {
        let nodes =
            parse_str(pcb_sexpr).map_err(|e| JsValue::from_str(&format!("parse error: {e}")))?;
        let root = nodes
            .first()
            .ok_or_else(|| JsValue::from_str("empty .kicad_pcb"))?;
        if root.head_symbol() != Some("kicad_pcb") {
            return Err(JsValue::from_str("root form is not (kicad_pcb …)"));
        }
        project.pcb =
            map_pcb(root).map_err(|m| JsValue::from_str(&format!("mapping error: {m}")))?;
    }

    serde_wasm_bindgen::to_value(&project)
        .map_err(|e| JsValue::from_str(&format!("serde-wasm-bindgen: {e}")))
}

/// Emit a `.kicad_pcb` text from a JSON-encoded [`kcir::Pcb`].
///
/// # Errors
/// Returns a string error if the JSON cannot be deserialized into a
/// `kcir::Pcb` value.
#[wasm_bindgen(js_name = emitPcbFromJson)]
pub fn emit_pcb_from_json(pcb_json: &str) -> Result<String, JsValue> {
    let pcb: kcir::Pcb = serde_json::from_str(pcb_json)
        .map_err(|e| JsValue::from_str(&format!("invalid kcir::Pcb JSON: {e}")))?;
    Ok(emit_pcb(&pcb))
}
