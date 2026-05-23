//! `.kicad_sym` parser.
//!
//! Reads a single `.kicad_sym` library file into a [`SymbolLib`].
//! Each top-level `(symbol "Name" …)` form becomes a [`LibSymbolEntry`]
//! capturing the metadata kiclaude searches on (name, description,
//! keywords, footprint filter, datasheet, default reference prefix).
//!
//! The pin geometry of each symbol is NOT lifted into KCIR — kiclaude
//! treats `.kicad_sym` content as opaque once indexed. If pin
//! placement is needed downstream, the caller re-reads the file
//! through the s-expression layer.

use std::fs;
use std::path::Path;

use serde::{Deserialize, Serialize};
use thiserror::Error;

use crate::sexpr::{parse_str, ParseError, SNode};

/// A single `(symbol "Name" …)` entry from a `.kicad_sym` library.
#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct LibSymbolEntry {
    /// The symbol's bare name as it appears in the `(symbol "Name" …)`
    /// form (e.g. `"R"` for a resistor, `"STM32G030F6P6"` for a chip).
    /// The full `lib_id` callers usually want is `"<libname>:<name>"`,
    /// constructed by the [`super::Index`] when the library is loaded.
    pub name: String,
    /// `Reference` property — the default refdes prefix (e.g. `"R"`,
    /// `"U"`, `"C"`).
    pub reference: String,
    /// `Value` property — the symbol's default value, usually matches
    /// `name` for generic parts.
    pub value: String,
    /// `Description` property — human-readable summary used by the
    /// library picker UI.
    pub description: String,
    /// `Datasheet` property — URL or filename.
    pub datasheet: String,
    /// `ki_keywords` property — space-separated search terms the
    /// upstream `KiCad` library curation adds to common parts.
    pub keywords: String,
    /// `ki_fp_filters` property — space-separated footprint pattern
    /// list (e.g. `"R_0603* R_0805* R_1206*"`). Used by the footprint
    /// picker to narrow the choices.
    pub footprint_filter: String,
    /// `Footprint` property — the default footprint `lib_id`
    /// (`"<fp-lib>:<footprint>"`), empty if the symbol is generic.
    pub footprint: String,
    /// `Manufacturer_Part_Number` (or `MPN`) property — surfaced for
    /// BOM sourcing.
    pub mpn: String,
    /// True if the symbol comes from the `power:` library namespace.
    pub is_power: bool,
}

/// A parsed `.kicad_sym` file.
#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct SymbolLib {
    /// `(version YYYYMMDD)` stamp.
    pub version: u32,
    /// `(generator <name>)` — tool that last wrote the file.
    pub generator: String,
    /// All top-level `(symbol …)` entries.
    pub symbols: Vec<LibSymbolEntry>,
}

/// Errors [`parse_symbol_lib`] can return.
#[derive(Debug, Error)]
pub enum LibParseError {
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
    #[error("S-expression in {path} has no top-level form")]
    Empty { path: std::path::PathBuf },
    #[error("top-level form in {path} is not `(kicad_symbol_lib …)`")]
    NotKicadSymbolLib { path: std::path::PathBuf },
}

/// Parse a `.kicad_sym` file from disk.
///
/// # Errors
/// Returns [`LibParseError`] on I/O failure or malformed S-expression.
pub fn parse_symbol_lib(path: &Path) -> Result<SymbolLib, LibParseError> {
    let text = fs::read_to_string(path).map_err(|source| LibParseError::Io {
        path: path.to_path_buf(),
        source,
    })?;
    let nodes = parse_str(&text).map_err(|source| LibParseError::InvalidSexpr {
        path: path.to_path_buf(),
        source,
    })?;
    let root = nodes.into_iter().next().ok_or(LibParseError::Empty {
        path: path.to_path_buf(),
    })?;
    if root.head_symbol() != Some("kicad_symbol_lib") {
        return Err(LibParseError::NotKicadSymbolLib {
            path: path.to_path_buf(),
        });
    }
    Ok(map_lib(&root))
}

/// Parse a `.kicad_sym` library from a string (the in-memory entry
/// point — handy for tests + the wasm path).
///
/// # Errors
/// Returns [`LibParseError`] when the S-expression is malformed or
/// the root form is not `(kicad_symbol_lib …)`. The `path` field on
/// the returned variants is left as the empty string since no file is
/// involved — use [`parse_symbol_lib`] when you need real provenance.
pub fn parse_symbol_lib_text(text: &str) -> Result<SymbolLib, LibParseError> {
    let nodes = parse_str(text).map_err(|source| LibParseError::InvalidSexpr {
        path: std::path::PathBuf::new(),
        source,
    })?;
    let root = nodes.into_iter().next().ok_or(LibParseError::Empty {
        path: std::path::PathBuf::new(),
    })?;
    if root.head_symbol() != Some("kicad_symbol_lib") {
        return Err(LibParseError::NotKicadSymbolLib {
            path: std::path::PathBuf::new(),
        });
    }
    Ok(map_lib(&root))
}

fn map_lib(root: &SNode) -> SymbolLib {
    use crate::format::v9::sexpr_helpers::{atom_str, body_children, find_child, find_children};

    let version = find_child(root, "version")
        .and_then(|n| body_children(n).next())
        .and_then(atom_str)
        .and_then(|s| s.parse::<u32>().ok())
        .unwrap_or(0);
    let generator = find_child(root, "generator")
        .and_then(|n| body_children(n).next())
        .and_then(atom_str)
        .unwrap_or("")
        .to_string();

    let symbols = find_children(root, "symbol")
        .iter()
        .map(|s| map_lib_symbol_entry(s))
        .collect();

    SymbolLib {
        version,
        generator,
        symbols,
    }
}

fn map_lib_symbol_entry(node: &SNode) -> LibSymbolEntry {
    use crate::format::v9::sexpr_helpers::{atom_str, body_children, find_children};

    let name = body_children(node)
        .next()
        .and_then(atom_str)
        .unwrap_or("")
        .to_string();

    let mut entry = LibSymbolEntry {
        name: name.clone(),
        is_power: false,
        ..LibSymbolEntry::default()
    };

    for prop in find_children(node, "property") {
        let body: Vec<&SNode> = body_children(prop).collect();
        let key = body.first().and_then(|n| atom_str(n)).unwrap_or("");
        let value = body
            .get(1)
            .and_then(|n| atom_str(n))
            .unwrap_or("")
            .to_string();
        match key {
            "Reference" => entry.reference = value,
            "Value" => entry.value = value,
            "Description" | "ki_description" => entry.description = value,
            "Datasheet" => entry.datasheet = value,
            "ki_keywords" | "Keywords" => entry.keywords = value,
            "ki_fp_filters" | "Footprint Filter" => entry.footprint_filter = value,
            "Footprint" => entry.footprint = value,
            "Manufacturer_Part_Number" | "MPN" => entry.mpn = value,
            _ => {}
        }
    }

    // `power:`-namespace symbols are flagged so downstream UI doesn't
    // show them in the regular component picker.
    entry.is_power = entry.reference.starts_with("#PWR") || entry.reference == "#FLG";
    entry
}
