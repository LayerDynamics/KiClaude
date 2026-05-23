//! M0-Q-02 — Golden-file round-trip CI gate.
//!
//! Walks every `examples/**/*.kicad_pcb` under the workspace, opens the
//! containing project via `kiclaude-ki`, re-emits the PCB through the
//! canonical emitter, and asserts byte-identical output for the
//! M0 reference project `examples/blinky/blinky.kicad_pcb`.
//!
//! Two assertions guard the gate:
//!
//! 1. `parse → emit` of `examples/blinky/blinky.kicad_pcb` produces
//!    bytes equal to the source file.
//! 2. The snapshot fixture at `tests/golden/blinky.kicad_pcb` is
//!    byte-identical to `examples/blinky/blinky.kicad_pcb`. The
//!    snapshot exists so a drift in either file fails the gate;
//!    intentional changes must update both, in lockstep.
//!
//! Other PCBs under `examples/` are walked too, but only checked for
//! "round-trip cleanly" — i.e. parse succeeds and emit produces a
//! non-empty result. Tightening every example to byte-identity lands
//! in M2-Q-01.

use std::fs;
use std::path::{Path, PathBuf};

use kiclaude_ki::format::v9::{emit_pcb, KiProject};
use pretty_assertions::assert_eq;
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

/// Format a unified-style diff between two strings, capped to the
/// first ~50 changed lines so failure messages remain useful.
fn format_diff(label_a: &str, a: &str, label_b: &str, b: &str) -> String {
    let diff = TextDiff::from_lines(a, b);
    let mut out = format!("--- {label_a}\n+++ {label_b}\n");
    let mut lines_emitted = 0usize;
    for change in diff.iter_all_changes() {
        let sign = match change.tag() {
            ChangeTag::Delete => "-",
            ChangeTag::Insert => "+",
            ChangeTag::Equal => " ",
        };
        let text = change.value().trim_end_matches('\n');
        out.push_str(&format!("{sign} {text}\n"));
        lines_emitted += 1;
        if lines_emitted >= 200 {
            out.push_str("... (diff truncated)\n");
            break;
        }
    }
    out
}

#[test]
fn blinky_round_trip_is_byte_identical() {
    let root = workspace_root();
    let blinky_pcb = root.join("examples/blinky/blinky.kicad_pcb");
    let blinky_dir = root.join("examples/blinky");

    let original_bytes = fs::read_to_string(&blinky_pcb)
        .unwrap_or_else(|err| panic!("read {}: {err}", blinky_pcb.display()));

    let opened = KiProject::open(&blinky_dir)
        .unwrap_or_else(|err| panic!("open {}: {err}", blinky_dir.display()));

    let re_emitted = emit_pcb(&opened.project.pcb);

    if re_emitted != original_bytes {
        let diff = format_diff(
            "examples/blinky/blinky.kicad_pcb (on-disk)",
            &original_bytes,
            "emit(parse(...)) (canonical)",
            &re_emitted,
        );
        panic!(
            "M0-Q-02 round-trip diverged for examples/blinky/blinky.kicad_pcb\n\n{diff}\n\
             The on-disk file must be in the emitter's canonical form. If the\n\
             change to the emitter is intentional, regenerate the example with\n\
             `cargo test -p kiclaude-golden -- --ignored regenerate_blinky_canonical`."
        );
    }
}

#[test]
fn golden_snapshot_matches_examples_blinky() {
    let root = workspace_root();
    let example = fs::read_to_string(root.join("examples/blinky/blinky.kicad_pcb"))
        .expect("read examples/blinky/blinky.kicad_pcb");
    let snapshot = fs::read_to_string(root.join("tests/golden/blinky.kicad_pcb"))
        .expect("read tests/golden/blinky.kicad_pcb");
    if example != snapshot {
        let diff = format_diff(
            "examples/blinky/blinky.kicad_pcb",
            &example,
            "tests/golden/blinky.kicad_pcb",
            &snapshot,
        );
        panic!(
            "M0-Q-02 snapshot drift between examples/blinky/blinky.kicad_pcb \
             and tests/golden/blinky.kicad_pcb\n\n{diff}\n\
             Both files must change together. Update tests/golden/blinky.kicad_pcb \
             (or examples/blinky/blinky.kicad_pcb) so they match."
        );
    }
    assert_eq!(example, snapshot, "snapshot must match example");
}

#[test]
fn every_example_pcb_round_trips_cleanly() {
    let root = workspace_root();
    let examples_root = root.join("examples");
    if !examples_root.is_dir() {
        return;
    }

    let mut walked = 0usize;
    for entry in WalkDir::new(&examples_root)
        .into_iter()
        .filter_map(Result::ok)
    {
        let path = entry.path();
        if path.extension().and_then(|s| s.to_str()) != Some("kicad_pcb") {
            continue;
        }
        let project_dir = path
            .parent()
            .unwrap_or_else(|| panic!("kicad_pcb at workspace root: {}", path.display()));
        let opened = KiProject::open(project_dir)
            .unwrap_or_else(|err| panic!("open {}: {err}", project_dir.display()));
        let emitted = emit_pcb(&opened.project.pcb);
        assert!(
            !emitted.is_empty(),
            "emit produced empty output for {}",
            path.display()
        );
        // Re-parse the emitted text via the s-expression layer to
        // catch any malformed output that would still serialize as a
        // non-empty string.
        let nodes = kiclaude_ki::sexpr::parse_str(&emitted).unwrap_or_else(|err| {
            panic!(
                "re-parse of emit({}) failed: {err}\nemit output:\n{emitted}",
                path.display()
            )
        });
        assert_eq!(
            nodes.first().and_then(|n| n.head_symbol()),
            Some("kicad_pcb"),
            "emit() produced something whose root is not `kicad_pcb` for {}",
            path.display()
        );
        walked += 1;
    }
    assert!(
        walked >= 1,
        "expected at least one example .kicad_pcb under {}",
        examples_root.display()
    );
}

/// Manual helper to regenerate `examples/blinky/blinky.kicad_pcb` from
/// the canonical emitter when an intentional formatting change lands.
///
/// Marked `#[ignore]` so it never runs in CI; invoke explicitly with
/// `cargo test -p kiclaude-golden -- --ignored regenerate_blinky_canonical`.
#[test]
#[ignore]
fn regenerate_blinky_canonical() {
    let root = workspace_root();
    let blinky_dir = root.join("examples/blinky");
    let opened = KiProject::open(&blinky_dir).expect("open blinky");
    let canonical = emit_pcb(&opened.project.pcb);
    let target = root.join("examples/blinky/blinky.kicad_pcb");
    fs::write(&target, &canonical).expect("write blinky.kicad_pcb");
    let snapshot = root.join("tests/golden/blinky.kicad_pcb");
    fs::write(&snapshot, &canonical).expect("write tests/golden/blinky.kicad_pcb");
    eprintln!(
        "regenerated {} and {} ({} bytes)",
        target.display(),
        snapshot.display(),
        canonical.len()
    );
}
