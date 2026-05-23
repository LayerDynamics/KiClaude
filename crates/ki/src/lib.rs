//! `kiclaude-ki` — `KiCad` file-format parsers, emitters, and KCIR.
//!
//! This crate is the source of truth for the kiclaude Canonical Intermediate
//! Representation (KCIR) and the deterministic, round-trip-faithful I/O layer
//! for `KiCad` 9 `.kicad_pro`, `.kicad_sch`, and `.kicad_pcb` files.
//!
//! See `docs/specs/SPEC-01-kiclaude.md` §7 (KCIR) and §9 (`KiCad` compatibility).

#![deny(rust_2018_idioms, missing_debug_implementations)]
#![warn(clippy::pedantic)]
#![allow(clippy::module_name_repetitions)]

pub mod annotate;
pub mod format;
pub mod kcir;
pub mod library;
pub mod sexpr;

#[cfg(target_arch = "wasm32")]
pub mod wasm;

#[cfg(feature = "python")]
pub mod python;

/// The KCIR schema version this crate emits. See SPEC §7.1.
///
/// Bump on every breaking change and add a migration under
/// `kcir::migrations`. Additive changes also bump the minor version.
pub const KCIR_VERSION: &str = "0.3.0";

/// Crate version, surfaced through bindings and the `kc_ping` MCP tool.
pub const CRATE_VERSION: &str = env!("CARGO_PKG_VERSION");
