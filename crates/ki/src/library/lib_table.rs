//! `sym-lib-table` (or `fp-lib-table`) parser.
//!
//! The library table is a small S-expression file at the project root
//! listing every `.kicad_sym` (or `.kicad_mod`) library `KiCad` has
//! pinned. Each row has the shape:
//!
//! ```text
//! (lib (name "Device")
//!      (type KiCad)
//!      (uri "${KICAD9_SYMBOL_DIR}/Device.kicad_sym")
//!      (options "")
//!      (descr "Device symbol library"))
//! ```
//!
//! [`resolve_uri`] expands `${VAR}` references against the process's
//! environment + an optional override map so library paths work both
//! in the user's `KiCad` install and in test fixtures.

use std::collections::HashMap;
use std::fs;
use std::hash::BuildHasher;
use std::path::Path;

use serde::{Deserialize, Serialize};
use thiserror::Error;

use crate::format::v9::sexpr_helpers::{atom_str, body_children, find_child, find_children};
use crate::sexpr::{parse_str, ParseError};

/// A single row of a `sym-lib-table` / `fp-lib-table`.
#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct LibraryRow {
    /// Short name the project references libraries by (e.g. `"Device"`).
    pub name: String,
    /// Library type — typically `"KiCad"`, `"Legacy"`, or `"Cloud"`.
    pub kind: String,
    /// Raw URI as written on disk (may contain `${VAR}` references).
    pub uri: String,
    /// Library-format-specific options (e.g. cache TTL for Cloud libs).
    pub options: String,
    /// Free-form description.
    pub descr: String,
    /// Disabled rows are present but skipped at load time.
    pub disabled: bool,
}

/// A parsed `sym-lib-table` (or `fp-lib-table`).
#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct SymLibTable {
    /// `(version N)` stamp — current KiCad-9 default is `7`.
    pub version: u32,
    /// Every row in declaration order.
    pub libraries: Vec<LibraryRow>,
}

/// Errors [`parse_sym_lib_table`] can return.
#[derive(Debug, Error)]
pub enum LibTableError {
    #[error("I/O error reading {path}: {source}")]
    Io {
        path: std::path::PathBuf,
        #[source]
        source: std::io::Error,
    },
    #[error("invalid S-expression in {path}: {source}")]
    InvalidSexpr {
        path: std::path::PathBuf,
        #[source]
        source: ParseError,
    },
    #[error("top-level form in {path} is not `(sym_lib_table …)` or `(fp_lib_table …)`")]
    UnknownRoot { path: std::path::PathBuf },
}

/// Parse a library table from disk.
///
/// # Errors
/// Returns [`LibTableError`] on I/O failure or malformed S-expression.
pub fn parse_sym_lib_table(path: &Path) -> Result<SymLibTable, LibTableError> {
    let text = fs::read_to_string(path).map_err(|source| LibTableError::Io {
        path: path.to_path_buf(),
        source,
    })?;
    let nodes = parse_str(&text).map_err(|source| LibTableError::InvalidSexpr {
        path: path.to_path_buf(),
        source,
    })?;
    let root = nodes.into_iter().next().ok_or(LibTableError::UnknownRoot {
        path: path.to_path_buf(),
    })?;
    let head = root.head_symbol();
    if head != Some("sym_lib_table") && head != Some("fp_lib_table") {
        return Err(LibTableError::UnknownRoot {
            path: path.to_path_buf(),
        });
    }
    Ok(map_table(&root))
}

/// Parse a library table from a string.
///
/// # Errors
/// Returns [`LibTableError`] when the S-expression is malformed or
/// the root form is not `(sym_lib_table …)` / `(fp_lib_table …)`.
pub fn parse_sym_lib_table_text(text: &str) -> Result<SymLibTable, LibTableError> {
    let nodes = parse_str(text).map_err(|source| LibTableError::InvalidSexpr {
        path: std::path::PathBuf::new(),
        source,
    })?;
    let root = nodes.into_iter().next().ok_or(LibTableError::UnknownRoot {
        path: std::path::PathBuf::new(),
    })?;
    let head = root.head_symbol();
    if head != Some("sym_lib_table") && head != Some("fp_lib_table") {
        return Err(LibTableError::UnknownRoot {
            path: std::path::PathBuf::new(),
        });
    }
    Ok(map_table(&root))
}

fn map_table(root: &crate::sexpr::SNode) -> SymLibTable {
    let version = find_child(root, "version")
        .and_then(|n| body_children(n).next())
        .and_then(atom_str)
        .and_then(|s| s.parse::<u32>().ok())
        .unwrap_or(0);

    let libraries = find_children(root, "lib")
        .iter()
        .map(|row| {
            let body_field = |key: &str| -> String {
                find_child(row, key)
                    .and_then(|n| body_children(n).next())
                    .and_then(atom_str)
                    .unwrap_or("")
                    .to_string()
            };
            let disabled = find_child(row, "disabled").is_some()
                || matches!(
                    find_child(row, "hidden")
                        .and_then(|n| body_children(n).next())
                        .and_then(atom_str),
                    Some("yes" | "true")
                );
            LibraryRow {
                name: body_field("name"),
                kind: body_field("type"),
                uri: body_field("uri"),
                options: body_field("options"),
                descr: body_field("descr"),
                disabled,
            }
        })
        .collect();

    SymLibTable { version, libraries }
}

/// Expand `${VAR}` references in a library URI.
///
/// Lookup order:
/// 1. The `overrides` map (test fixtures, integration shims).
/// 2. The process environment.
/// 3. Left in place when neither knows the variable.
///
/// `~` at the start of an unexpanded URI expands to `$HOME` when set.
#[must_use]
pub fn resolve_uri<S: BuildHasher>(raw: &str, overrides: &HashMap<String, String, S>) -> String {
    let mut out = String::with_capacity(raw.len());
    let bytes = raw.as_bytes();
    let mut i = 0;
    while i < bytes.len() {
        if bytes[i] == b'$' && bytes.get(i + 1) == Some(&b'{') {
            if let Some(end_rel) = raw[i + 2..].find('}') {
                let var_name = &raw[i + 2..i + 2 + end_rel];
                let value = overrides
                    .get(var_name)
                    .cloned()
                    .or_else(|| std::env::var(var_name).ok())
                    .unwrap_or_else(|| format!("${{{var_name}}}"));
                out.push_str(&value);
                i += 2 + end_rel + 1;
                continue;
            }
        }
        out.push(bytes[i] as char);
        i += 1;
    }
    if let Some(rest) = out.strip_prefix("~/") {
        if let Ok(home) = std::env::var("HOME") {
            return format!("{home}/{rest}");
        }
    }
    out
}
