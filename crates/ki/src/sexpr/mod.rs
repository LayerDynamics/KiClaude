//! S-expression I/O — the lexer, parser, AST, and emitter for `KiCad`'s
//! file format.
//!
//! `KiCad` 6+ uses a Specctra-derived S-expression syntax for `.kicad_pro`,
//! `.kicad_sch`, `.kicad_pcb`, `.kicad_sym`, and `.kicad_mod`. This module
//! is the deterministic, byte-fidelity-preserving I/O layer the rest of
//! the crate (and the format mappers under `format::v9`) build on top of.

pub mod ast;
pub mod emit;
pub mod lex;
pub mod parse;

pub use ast::SNode;
pub use emit::{emit_all_canonical, emit_all_from_source, emit_canonical, emit_from_source};
pub use lex::{tokenize, LexError, Token, TokenKind};
pub use parse::{parse, parse_str, ParseError};
