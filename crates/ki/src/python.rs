//! PyO3 bindings — exposes the M0 KCIR surface to Python as the
//! `ki_native` extension module.
//!
//! Built by `maturin develop --features python` via
//! `crates/ki/pyproject.toml`. Native `cargo build` ignores this module
//! (gated by `#[cfg(feature = "python")]` in `lib.rs`).
//!
//! API surface (M0):
//! - `__version__` — crate version string.
//! - `kcir_version` — KCIR schema version string.
//! - `open_project(path: str) -> dict` — opens a `KiCad` project
//!   directory and returns the resulting `kcir::Project` as a nested
//!   Python dict (serialized via `serde_json` → `json.loads`, which is
//!   the lossless cross-language round-trip).

use pyo3::prelude::*;
use pyo3::types::PyDict;

use crate::format::KiProject;

/// `from ki_native import open_project; open_project("examples/blinky")`
/// → `dict`. Returns the in-memory `kcir::Project` as a Python dict.
///
/// # Errors
/// Raises `ValueError` if the directory is missing, malformed, or
/// contains an unparseable `.kicad_pro` / `.kicad_pcb`.
#[pyfunction]
fn open_project(py: Python<'_>, path: &str) -> PyResult<PyObject> {
    let opened = KiProject::open(path)
        .map_err(|e| pyo3::exceptions::PyValueError::new_err(format!("{e}")))?;
    let json_str = serde_json::to_string(&opened.project)
        .map_err(|e| pyo3::exceptions::PyValueError::new_err(format!("serialize: {e}")))?;
    let json_mod = py.import("json")?;
    let obj = json_mod.call_method1("loads", (json_str,))?;
    Ok(obj.unbind())
}

/// `from ki_native import emit_pcb; emit_pcb(pcb_dict)` → str. Round-
/// trip companion: take a dict shaped like `kcir::Pcb` and produce
/// canonical `.kicad_pcb` text.
///
/// # Errors
/// Raises `ValueError` if `pcb_dict` cannot be deserialized into a
/// `kcir::Pcb` (use `kcir::Project.pcb` from `open_project`'s result).
#[pyfunction]
fn emit_pcb(py: Python<'_>, pcb_dict: Bound<'_, PyAny>) -> PyResult<String> {
    let json_mod = py.import("json")?;
    let json_str: String = json_mod.call_method1("dumps", (pcb_dict,))?.extract()?;
    let pcb: crate::kcir::Pcb = serde_json::from_str(&json_str)
        .map_err(|e| pyo3::exceptions::PyValueError::new_err(format!("deserialize: {e}")))?;
    Ok(crate::format::v9::emit_pcb(&pcb))
}

/// `from ki_native import save_project; save_project(project_dict,
/// target_dir)` — write `target_dir/<stem>.kicad_pcb` (canonical) and
/// `target_dir/<stem>.kicad_sch` (canonical, root sheet only).
///
/// Returns the list of files written.
///
/// # Errors
/// Raises `ValueError` if the dict isn't a valid `kcir::Project`, or
/// `OSError` on I/O failure.
#[pyfunction]
fn save_project(
    py: Python<'_>,
    project_dict: Bound<'_, PyAny>,
    target_dir: &str,
) -> PyResult<Vec<String>> {
    let json_mod = py.import("json")?;
    let json_str: String = json_mod.call_method1("dumps", (project_dict,))?.extract()?;
    let project: crate::kcir::Project = serde_json::from_str(&json_str).map_err(|e| {
        pyo3::exceptions::PyValueError::new_err(format!("deserialize project: {e}"))
    })?;
    let dir = std::path::PathBuf::from(target_dir);
    if !dir.is_dir() {
        return Err(pyo3::exceptions::PyValueError::new_err(format!(
            "target dir does not exist: {target_dir}"
        )));
    }
    let stem = if project.name.is_empty() {
        "project".to_string()
    } else {
        project.name.clone()
    };

    let pcb_path = dir.join(format!("{stem}.kicad_pcb"));
    let pcb_text = crate::format::v9::emit_pcb(&project.pcb);
    std::fs::write(&pcb_path, &pcb_text).map_err(|e| {
        pyo3::exceptions::PyOSError::new_err(format!("write {}: {e}", pcb_path.display()))
    })?;
    let mut written = vec![pcb_path.display().to_string()];

    // Write the root sheet's `.kicad_sch` when at least one sheet
    // exists. Multi-sheet writeback (one file per sheet) lands in
    // M1-P-01's M1-T-05 follow-up.
    if let Some(root) = project
        .schematic
        .sheets
        .iter()
        .find(|s| s.parent.is_none())
        .cloned()
    {
        let sch_path = dir.join(format!("{stem}.kicad_sch"));
        let sch_text = crate::format::v9::sch_emit::emit_sch_canonical_for_schematic(
            &project.schematic,
            &root.uuid,
        );
        std::fs::write(&sch_path, &sch_text).map_err(|e| {
            pyo3::exceptions::PyOSError::new_err(format!("write {}: {e}", sch_path.display()))
        })?;
        written.push(sch_path.display().to_string());
    }

    let _ = py;
    Ok(written)
}

/// `from ki_native import parse_pcb_text; parse_pcb_text(text)` →
/// `dict`. Parse a standalone `.kicad_pcb` source string into the
/// in-memory `kcir::Pcb` shape (returned as a Python dict). Used by
/// the M2-T-11 `kiclaude diff` CLI to load two PCBs without
/// synthesizing the surrounding `.kicad_pro` project.
///
/// # Errors
/// Raises `ValueError` if the input is not a parseable
/// `(kicad_pcb …)` form.
#[pyfunction]
fn parse_pcb_text(py: Python<'_>, text: &str) -> PyResult<PyObject> {
    let nodes = crate::sexpr::parse_str(text)
        .map_err(|e| pyo3::exceptions::PyValueError::new_err(format!("parse: {e}")))?;
    let root = nodes
        .first()
        .ok_or_else(|| pyo3::exceptions::PyValueError::new_err("empty input"))?;
    let pcb = crate::format::v9::pcb::map_pcb(root)
        .map_err(|e| pyo3::exceptions::PyValueError::new_err(format!("map_pcb: {e}")))?;
    let json_str = serde_json::to_string(&pcb)
        .map_err(|e| pyo3::exceptions::PyValueError::new_err(format!("serialize: {e}")))?;
    let json_mod = py.import("json")?;
    let obj = json_mod.call_method1("loads", (json_str,))?;
    Ok(obj.unbind())
}

/// `from ki_native import list_symbols; list_symbols(table_path,
/// overrides)` — resolve every `(lib …)` row in a `sym-lib-table`,
/// parse the matching `.kicad_sym` files, and return one dict per
/// symbol with the searchable fields the M1-P-02 indexer needs.
///
/// `overrides` is a `{var_name: value}` dict that expands `${VAR}`
/// references in library URIs ahead of the process environment.
///
/// # Errors
/// Raises `ValueError` if the table file is missing or malformed.
#[pyfunction]
fn list_symbols(
    py: Python<'_>,
    table_path: &str,
    overrides: Bound<'_, PyAny>,
) -> PyResult<PyObject> {
    let json_mod = py.import("json")?;
    let overrides_json: String = json_mod.call_method1("dumps", (overrides,))?.extract()?;
    let overrides: std::collections::HashMap<String, String> =
        serde_json::from_str(&overrides_json)
            .map_err(|e| pyo3::exceptions::PyValueError::new_err(format!("overrides dict: {e}")))?;

    let table = crate::library::parse_sym_lib_table(std::path::Path::new(table_path))
        .map_err(|e| pyo3::exceptions::PyValueError::new_err(format!("{e}")))?;
    let index = crate::library::Index::from_lib_table(&table, &overrides);
    let hits = index.search("", usize::MAX);
    let json_str = serde_json::to_string(&hits)
        .map_err(|e| pyo3::exceptions::PyValueError::new_err(format!("serialize hits: {e}")))?;
    let obj = json_mod.call_method1("loads", (json_str,))?;
    Ok(obj.unbind())
}

/// The `ki_native` Python module exposed by maturin.
#[pymodule]
fn ki_native(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(open_project, m)?)?;
    m.add_function(wrap_pyfunction!(emit_pcb, m)?)?;
    m.add_function(wrap_pyfunction!(save_project, m)?)?;
    m.add_function(wrap_pyfunction!(list_symbols, m)?)?;
    m.add_function(wrap_pyfunction!(parse_pcb_text, m)?)?;
    m.add("__version__", crate::CRATE_VERSION)?;
    m.add("kcir_version", crate::KCIR_VERSION)?;
    Ok(())
}

// Touch `PyDict` so refactors that drop the dependency don't silently
// break the documented "returns a Python dict" contract.
const _: fn() = || {
    let _ = std::mem::size_of::<Bound<'_, PyDict>>;
};
