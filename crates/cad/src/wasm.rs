//! `wasm-bindgen` shim — exposes the M0 CAD primitive surface to JS.
//!
//! Compiled only for `wasm32-unknown-unknown` via a `#[cfg]` gate in
//! `lib.rs`. Native builds pay no compile-time or binary cost for
//! these bindings.

use wasm_bindgen::prelude::*;

use crate::geom::{BBox, Point, Polygon};

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
