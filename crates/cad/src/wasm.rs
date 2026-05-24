//! `wasm-bindgen` shim — exposes the M0 CAD primitive surface to JS.
//!
//! Compiled only for `wasm32-unknown-unknown` via a `#[cfg]` gate in
//! `lib.rs`. Native builds pay no compile-time or binary cost for
//! these bindings.

use wasm_bindgen::prelude::*;

use crate::drc::{check_all, DrcInput, DrcIssue};
use crate::geom::{BBox, Point, Polygon};
use crate::impedance::{
    differential_microstrip_z_json, find_diff_microstrip_widths_for_zdiff,
    find_microstrip_width_for_z0, microstrip_z0_json, stripline_z0_json,
};
use crate::length_match::analyze_json as length_match_analyze_json;
use crate::three_scene::scene_from_pcb;
use crate::zones::fill::{fill_zone, ZoneFillInput, ZoneFillResult};
use kiclaude_ki::kcir::Pcb;

/// `kiclaude-cad` crate version.
#[wasm_bindgen(js_name = crateVersion)]
#[must_use]
pub fn crate_version() -> String {
    crate::CRATE_VERSION.to_string()
}

/// Test whether a 2-D point lies inside a polygon described by a flat
/// `[x0, y0, x1, y1, …]` array.
///
/// Returns `false` for polygons with fewer than 3 vertices.
#[wasm_bindgen(js_name = polygonContainsPoint)]
#[must_use]
pub fn polygon_contains_point(polygon_xy: &[f64], x: f64, y: f64) -> bool {
    if polygon_xy.len() < 6 || polygon_xy.len() % 2 != 0 {
        return false;
    }
    let points: Vec<Point> = polygon_xy
        .chunks_exact(2)
        .map(|c| Point::new(c[0], c[1]))
        .collect();
    Polygon::new(points).contains_point(Point::new(x, y))
}

/// Bounding box of a polygon described by a flat `[x0, y0, x1, y1, …]`
/// array. Returns a JS object `{ minX, minY, maxX, maxY }`.
///
/// # Errors
/// Returns an error when the array is empty or has an odd length.
#[wasm_bindgen(js_name = polygonBoundingBox)]
pub fn polygon_bounding_box(polygon_xy: &[f64]) -> Result<JsValue, JsValue> {
    if polygon_xy.is_empty() {
        return Err(JsValue::from_str("polygon has no points"));
    }
    if polygon_xy.len() % 2 != 0 {
        return Err(JsValue::from_str(
            "polygon array length is not a multiple of 2",
        ));
    }
    let points: Vec<Point> = polygon_xy
        .chunks_exact(2)
        .map(|c| Point::new(c[0], c[1]))
        .collect();
    let bb = Polygon::new(points).bounding_box();
    let obj = BBoxJs {
        min_x: bb.min.x,
        min_y: bb.min.y,
        max_x: bb.max.x,
        max_y: bb.max.y,
    };
    serde_wasm_bindgen::to_value(&obj)
        .map_err(|e| JsValue::from_str(&format!("serde-wasm-bindgen: {e}")))
}

/// JS-shaped bbox — separate from `BBox` because wasm-bindgen prefers
/// flat, snake-cased fields renamed to JS conventions via serde.
#[derive(serde::Serialize)]
struct BBoxJs {
    #[serde(rename = "minX")]
    min_x: f64,
    #[serde(rename = "minY")]
    min_y: f64,
    #[serde(rename = "maxX")]
    max_x: f64,
    #[serde(rename = "maxY")]
    max_y: f64,
}

// Make the import-test compiler smile: `BBox` is referenced in
// `BBoxJs` shape comments above but not in code, so keep an explicit
// touch here to ensure rename refactors propagate.
const _: fn() = || {
    let _ = std::mem::size_of::<BBox>();
};

/// Run the full DRC kernel (M2-R-06) against a serialized [`DrcInput`].
///
/// Input is a JSON string matching `DrcInput`'s serde shape; output is
/// a JSON string carrying `Vec<DrcIssue>` (or a JS error on parse
/// failure). The string-in/string-out boundary is chosen so the
/// React `RouteTool` (M2-T-03) can build the input via JSON.stringify
/// of a TS interface that mirrors the Rust shape without needing a
/// hand-marshalled wasm-bindgen struct surface per field.
///
/// # Errors
/// Returns a JS error when the input JSON cannot be parsed as
/// `DrcInput`. Output serialization failures are also surfaced as JS
/// errors — both indicate the caller is shipping mis-shaped data and
/// should be treated as a programming bug, not a runtime warning.
#[wasm_bindgen(js_name = checkDrc)]
pub fn check_drc(input_json: &str) -> Result<String, JsValue> {
    let input: DrcInput = serde_json::from_str(input_json)
        .map_err(|e| JsValue::from_str(&format!("invalid DrcInput JSON: {e}")))?;
    let issues: Vec<DrcIssue> = check_all(&input);
    serde_json::to_string(&issues)
        .map_err(|e| JsValue::from_str(&format!("DrcIssue serialization: {e}")))
}

/// Run the M2-R-05 zone-fill pipeline against a serialized
/// [`ZoneFillInput`] and return the resulting [`ZoneFillResult`] as
/// JSON. Used by the React [`ZoneTool`] (M2-T-04) to compute the
/// live fill preview while the user is still drawing the zone
/// outline — the wasm round-trip is cheap enough (<5ms on the M2
/// reference fixtures) to fire on each pointer move.
///
/// # Errors
/// Returns a JS error when the input JSON cannot be parsed as
/// `ZoneFillInput` or when the output cannot be serialized.
#[wasm_bindgen(js_name = fillZone)]
pub fn fill_zone_wasm(input_json: &str) -> Result<String, JsValue> {
    let input: ZoneFillInput = serde_json::from_str(input_json)
        .map_err(|e| JsValue::from_str(&format!("invalid ZoneFillInput JSON: {e}")))?;
    let result: ZoneFillResult = fill_zone(&input);
    serde_json::to_string(&result)
        .map_err(|e| JsValue::from_str(&format!("ZoneFillResult serialization: {e}")))
}

// ─────────────────────────────────────────────────────────────────────
// M3-T-02 — Impedance solver bridges for the Net inspector.
//
// All forward (geometry → Z0) entry points take a JSON `TraceGeometry`
// / `DiffPairGeometry` blob — same shape that
// [`crate::impedance::SingleEndedResult`] / [`DifferentialResult`]
// document. Both helpers are tested on the native side via
// `cargo test -p kiclaude-cad`; the wasm shims below are pure
// `?`-propagation, so a parse error reaches the React panel as a
// readable JS `Error`.
// ─────────────────────────────────────────────────────────────────────

/// Forward-solve both microstrip `Z0` models. Returns JSON
/// `{ z0_hammerstad_ohms, z0_ipc2141_ohms }`.
///
/// # Errors
/// Returns a JS error when the input is not a valid
/// `TraceGeometry` JSON.
#[wasm_bindgen(js_name = microstripZ0)]
pub fn microstrip_z0_wasm(input_json: &str) -> Result<String, JsValue> {
    microstrip_z0_json(input_json).map_err(|e| JsValue::from_str(&e))
}

/// Forward-solve the IPC-2141A stripline `Z0` and return the bare
/// ohms value (single number — no second formula to cross-check
/// against on the stripline path).
///
/// # Errors
/// Returns a JS error when the input is not a valid
/// `TraceGeometry` JSON.
#[wasm_bindgen(js_name = striplineZ0)]
pub fn stripline_z0_wasm(input_json: &str) -> Result<f64, JsValue> {
    stripline_z0_json(input_json).map_err(|e| JsValue::from_str(&e))
}

/// Forward-solve a differential pair. Returns JSON
/// `{ zdiff_ohms, zcomm_ohms, z0_single_ended_ohms }`.
///
/// # Errors
/// Returns a JS error when the input is not a valid
/// `DiffPairGeometry` JSON.
#[wasm_bindgen(js_name = differentialMicrostripZ)]
pub fn differential_microstrip_z_wasm(input_json: &str) -> Result<String, JsValue> {
    differential_microstrip_z_json(input_json).map_err(|e| JsValue::from_str(&e))
}

/// Bisection solver: trace width (mm) that hits `target_ohms` on the
/// given stackup. Returns `f64::NAN` when the target is unreachable —
/// keeps the wasm boundary pure-numeric so callers don't need
/// `Option<number>` discriminants.
#[wasm_bindgen(js_name = solveMicrostripWidthForZ0)]
#[must_use]
pub fn solve_microstrip_width_for_z0_wasm(
    target_ohms: f64,
    height_mm: f64,
    er: f64,
    thickness_mm: f64,
) -> f64 {
    find_microstrip_width_for_z0(target_ohms, height_mm, er, thickness_mm).unwrap_or(f64::NAN)
}

/// Bisection solver: per-trace width (mm) that hits a target
/// `Zdiff` for the given gap + stackup. Returns `f64::NAN` when
/// unreachable.
#[wasm_bindgen(js_name = solveDiffMicrostripWidthForZdiff)]
#[must_use]
pub fn solve_diff_microstrip_width_for_zdiff_wasm(
    target_zdiff_ohms: f64,
    gap_mm: f64,
    height_mm: f64,
    er: f64,
    thickness_mm: f64,
) -> f64 {
    find_diff_microstrip_widths_for_zdiff(target_zdiff_ohms, gap_mm, height_mm, er, thickness_mm)
        .unwrap_or(f64::NAN)
}

// ─────────────────────────────────────────────────────────────────────
// M3-T-04 — Length-match analyzer bridge for the Length Match panel.
// ─────────────────────────────────────────────────────────────────────

/// Run the M3-R-05 analyzer against a serialised KCIR [`Pcb`] and
/// return `Vec<LengthMatchReport>` as JSON. Powers the `LengthMatchPanel`
/// status column without requiring a kiserver round-trip — sub-ms on
/// realistic group counts.
///
/// # Errors
/// Returns a JS error when the input is not a valid `Pcb` JSON.
#[wasm_bindgen(js_name = analyzeLengthMatch)]
pub fn analyze_length_match_wasm(pcb_json: &str) -> Result<String, JsValue> {
    length_match_analyze_json(pcb_json).map_err(|e| JsValue::from_str(&e))
}

/// M3-T-06/T-07 — produce a `ThreeScene` from a serialised KCIR
/// [`Pcb`] so the kithree viewer can render the board + every
/// footprint's `Model3D` placement. The JSON shape is the
/// `crates/cad/src/three_scene.rs::ThreeScene` serde mirror,
/// consumed by `@kiclaude/kithree::loadThreeScene`.
///
/// # Errors
/// Returns a JS error when the input is not a valid `Pcb` JSON.
#[wasm_bindgen(js_name = sceneFromPcb)]
pub fn scene_from_pcb_wasm(pcb_json: &str) -> Result<String, JsValue> {
    let pcb: Pcb = serde_json::from_str(pcb_json)
        .map_err(|e| JsValue::from_str(&format!("invalid Pcb JSON: {e}")))?;
    let scene = scene_from_pcb(&pcb);
    serde_json::to_string(&scene)
        .map_err(|e| JsValue::from_str(&format!("ThreeScene serialisation: {e}")))
}
