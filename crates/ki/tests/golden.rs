//! Golden-file round-trip CI gate (M0-Q-02).
//!
//! Walks every `examples/**/*.kicad_pcb` in the repo, parses each to
//! KCIR, emits it back, and asserts byte-identical output. Any change
//! to `crates/ki/src/format/v9/` that breaks this gate must be either
//! reverted or paired with regenerated golden files.
//!
//! This test is the cross-language anchor for kiclaude's first
//! principle #1: "KiCad file format is the contract. Round-trip
//! fidelity is a CI gate."

use std::fs;
use std::path::{Path, PathBuf};

use kiclaude_ki::format::v9::{emit_pcb, pcb};
use kiclaude_ki::sexpr::parse_str;

/// Walk `examples/` and yield every `.kicad_pcb` file. Recurses one
/// level deep — kiclaude's example projects sit in their own
/// directories under `examples/`.
fn collect_example_pcbs() -> Vec<PathBuf> {
    let repo_root = repo_root();
    let examples = repo_root.join("examples");
    let mut found = Vec::new();
    if !examples.is_dir() {
        return found;
    }
    let entries = fs::read_dir(&examples).expect("read examples/");
    for entry in entries.flatten() {
        let project_dir = entry.path();
        if !project_dir.is_dir() {
            continue;
        }
        let sub = fs::read_dir(&project_dir).expect("read example project dir");
        for f in sub.flatten() {
            let p = f.path();
            if p.extension().and_then(|s| s.to_str()) == Some("kicad_pcb") {
                found.push(p);
            }
        }
    }
    found.sort();
    found
}

/// Walk up from the crate dir to the workspace root (the dir
/// containing `examples/`).
fn repo_root() -> PathBuf {
    let crate_dir = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    crate_dir
        .parent()
        .and_then(Path::parent)
        .expect("crate is two levels under repo root")
        .to_path_buf()
}

/// Assert byte-identical round-trip for a single `.kicad_pcb` file.
fn assert_round_trip(path: &Path) {
    let src = fs::read_to_string(path).unwrap_or_else(|e| panic!("read {}: {e}", path.display()));
    let nodes = parse_str(&src).unwrap_or_else(|e| panic!("parse {}: {e}", path.display()));
    let root = nodes
        .first()
        .unwrap_or_else(|| panic!("{} is empty", path.display()));
    let pcb = pcb::map_pcb(root).unwrap_or_else(|m| panic!("map_pcb {}: {m}", path.display()));
    let emitted = emit_pcb(&pcb);
    assert_eq!(
        emitted,
        src,
        "byte-identical round-trip failed for {}",
        path.display()
    );
}

#[test]
fn round_trip_blinky() {
    let path = repo_root().join("examples/blinky/blinky.kicad_pcb");
    assert!(
        path.is_file(),
        "examples/blinky/blinky.kicad_pcb must exist (M0-C-03)"
    );
    assert_round_trip(&path);
}

#[test]
fn round_trip_all_examples() {
    let pcbs = collect_example_pcbs();
    assert!(
        !pcbs.is_empty(),
        "no .kicad_pcb files found under examples/ — M0-C-03 must produce at least one"
    );
    for path in &pcbs {
        assert_round_trip(path);
    }
}

/// Smoke: ensure the example walker actually descended into project
/// subdirs. Catches accidentally moving examples/blinky/ up to the
/// `examples/` root (where the walker wouldn't see it).
#[test]
fn examples_walker_finds_at_least_blinky() {
    let pcbs = collect_example_pcbs();
    let has_blinky = pcbs
        .iter()
        .any(|p| p.file_name().and_then(|n| n.to_str()) == Some("blinky.kicad_pcb"));
    assert!(has_blinky, "expected blinky.kicad_pcb in scan: {pcbs:?}");
}
