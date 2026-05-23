//! Test-only crate that hosts the `M0-Q-02` round-trip CI gate.
//!
//! All assertion logic lives under `tests/`. This file exists only so
//! Cargo treats the directory as a package; downstream code should
//! depend on the integration tests, not this lib.

#![deny(rust_2018_idioms)]
