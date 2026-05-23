//! `.kicad_sym` symbol-library index (M1-R-04).
//!
//! Reads a `sym-lib-table` (the user's library registry) and the
//! `.kicad_sym` files it points at, then exposes a ranked-search
//! [`Index`] over every symbol in the resolved set. The MCP server's
//! `kc_symbol_search` tool (M1-P-04) is the primary downstream user.
//!
//! Submodules:
//!
//! - [`symbol`] — `.kicad_sym` parser → [`LibSymbolEntry`].
//! - [`lib_table`] — `sym-lib-table` parser → [`LibraryRow`]s.
//! - [`index`] — the searchable [`Index`] over a resolved set of
//!   symbols, with substring + keyword scoring.

pub mod footprint;
pub mod index;
pub mod lib_table;
pub mod symbol;

pub use footprint::{
    parse_footprint_file, parse_footprint_text, FootprintEntry, FootprintHit, FootprintIndex,
    FootprintLoadError, FootprintParseError,
};
pub use index::{Index, SearchHit};
pub use lib_table::{parse_sym_lib_table, parse_sym_lib_table_text, LibraryRow, SymLibTable};
pub use symbol::{parse_symbol_lib, parse_symbol_lib_text, LibSymbolEntry, SymbolLib};

#[cfg(test)]
mod tests;
