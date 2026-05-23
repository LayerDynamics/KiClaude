//! M1-Q-01 — Schematic golden-file round-trip CI gate.
//!
//! Walks every `examples/**/*.kicad_sch` under the workspace, parses it
//! via [`kiclaude_ki::sexpr::parse_str`], then runs
//! [`emit_sch`][kiclaude_ki::format::v9::emit_sch] and asserts the
//! result is byte-identical to the on-disk source.
//!
//! The schematic emitter's contract is "byte-identical for unmodified
//! nodes" (M1-R-02). This gate enforces that contract across 10
//! reference projects so any regression in the emitter's whitespace
//! handling, sub-form passthrough, or top-level span recovery fails
//! CI before it lands.
//!
//! ## Acceptance
//!
//! - Discovers ≥ 10 `.kicad_sch` files under `examples/`.
//! - For each file: parses, re-emits, asserts byte-equal.
//! - Re-parses the emitted text to confirm it remains structurally
//!   valid (catches "looks identical but lost a paren" failure modes).

use std::fs;
use std::path::{Path, PathBuf};

use kiclaude_ki::format::v9::emit_sch;
use kiclaude_ki::sexpr::parse_str;
use similar::{ChangeTag, TextDiff};
use walkdir::WalkDir;

/// Resolve the workspace root from this crate's manifest dir.
/// `tests/golden/` → `../../`.
fn workspace_root() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .and_then(Path::parent)
        .expect("workspace root is two parents above tests/golden")
        .to_path_buf()
}

/// Format a unified-style diff between two strings, capped to ~200
/// changed lines so failure messages stay useful.
fn format_diff(label_a: &str, a: &str, label_b: &str, b: &str) -> String {
    let diff = TextDiff::from_lines(a, b);
    let mut out = format!("--- {label_a}\n+++ {label_b}\n");
    let mut lines = 0usize;
    for change in diff.iter_all_changes() {
        let sign = match change.tag() {
            ChangeTag::Delete => "-",
            ChangeTag::Insert => "+",
            ChangeTag::Equal => " ",
        };
        out.push_str(&format!("{sign} {}", change.value()));
        if !change.value().ends_with('\n') {
            out.push('\n');
        }
        lines += 1;
        if lines >= 200 {
            out.push_str("... (diff truncated)\n");
            break;
        }
    }
    out
}

/// Collect every `.kicad_sch` file under `examples/`.
fn discover_examples() -> Vec<PathBuf> {
    let root = workspace_root().join("examples");
    if !root.is_dir() {
        return Vec::new();
    }
    let mut out: Vec<PathBuf> = WalkDir::new(&root)
        .into_iter()
        .filter_map(Result::ok)
        .filter(|e| e.path().extension().and_then(|s| s.to_str()) == Some("kicad_sch"))
        .map(|e| e.path().to_path_buf())
        .collect();
    out.sort();
    out
}

/// One sheet at a time: parse, emit, assert byte-equal, re-parse the
/// emit output to catch structural drift the byte check misses.
fn assert_round_trip(path: &Path) {
    let src =
        fs::read_to_string(path).unwrap_or_else(|err| panic!("read {}: {err}", path.display()));
    let nodes = parse_str(&src).unwrap_or_else(|err| panic!("parse {}: {err}", path.display()));
    let root = nodes
        .first()
        .unwrap_or_else(|| panic!("no top-level form in {}", path.display()));
    let emitted = emit_sch(root, &src);
    if emitted != src {
        let diff = format_diff(
            &format!("{} (on-disk)", path.display()),
            &src,
            "emit_sch(parse(...))",
            &emitted,
        );
        panic!(
            "M1-Q-01 schematic round-trip diverged for {}\n\n{diff}",
            path.display()
        );
    }
    // Re-parse to confirm structural integrity.
    let reparsed = parse_str(&emitted).unwrap_or_else(|err| {
        panic!(
            "re-parse of emit({}) failed: {err}\nemit output:\n{emitted}",
            path.display()
        )
    });
    let reparsed_root = reparsed.first().unwrap_or_else(|| {
        panic!(
            "re-parsed emit({}) produced no top-level form",
            path.display()
        )
    });
    assert_eq!(
        reparsed_root.head_symbol(),
        Some("kicad_sch"),
        "re-parsed root of {} is not (kicad_sch …)",
        path.display(),
    );
}

#[test]
fn at_least_ten_reference_projects_are_present() {
    let examples = discover_examples();
    assert!(
        examples.len() >= 10,
        "M1-Q-01 requires ≥ 10 reference .kicad_sch projects under examples/; found {}: {:#?}",
        examples.len(),
        examples,
    );
}

#[test]
fn every_example_schematic_round_trips_byte_identical() {
    let examples = discover_examples();
    assert!(
        !examples.is_empty(),
        "no .kicad_sch fixtures found under examples/",
    );
    for path in &examples {
        assert_round_trip(path);
    }
}

#[test]
fn blinky_sch_is_byte_identical() {
    // Spot-check the canonical fixture so the broader walk's failure
    // mode (which lists *all* failing files) is supplemented by a
    // clear "blinky specifically broke" signal.
    let path = workspace_root().join("examples/blinky/blinky.kicad_sch");
    assert!(
        path.is_file(),
        "M1-Q-01 expects examples/blinky/blinky.kicad_sch to exist",
    );
    assert_round_trip(&path);
}
