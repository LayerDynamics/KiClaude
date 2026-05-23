//! `KiCad` file-format I/O.
//!
//! The `v9` submodule implements the KCIR mapper for `KiCad` 9 — the
//! current schema for `.kicad_pro`, `.kicad_sch`, and `.kicad_pcb` files.
//! [`KiProject`] is the user-facing entry point: open a directory, get
//! back a fully populated [`kcir::Project`](crate::kcir::Project).
//!
//! Subsequent `KiCad` major versions will gain their own submodules
//! (`v10`, `v11`, …) — `KiProject` will dispatch on the detected
//! generator version. See SPEC §9.1.

pub mod v9;

pub use v9::{KiProject, OpenError};
