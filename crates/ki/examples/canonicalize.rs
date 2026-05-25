//! `canonicalize` — emit the round-trip-canonical form of a `.kicad_pcb`.
//!
//! Reads a `.kicad_pcb`, parses it to KCIR via [`pcb::map_pcb`], and
//! re-emits it with [`emit_pcb`]. The output is the exact byte stream
//! the golden round-trip gate (`crates/ki/tests/golden.rs`,
//! `tests/golden/round_trip_pcb.rs`) expects, because that gate asserts
//! `emit_pcb(map_pcb(parse(src))) == src`.
//!
//! This is the authoring tool for new `examples/**` reference boards:
//! hand-write a draft, run it through `canonicalize`, and commit the
//! output — which is, by construction, a fixed point of the
//! parse→map→emit pipeline and therefore round-trips byte-identically.
//!
//! Usage:
//!   cargo run -p kiclaude-ki --example canonicalize -- <in.kicad_pcb>            # print to stdout
//!   cargo run -p kiclaude-ki --example canonicalize -- <in.kicad_pcb> --write    # rewrite in place
//!   cargo run -p kiclaude-ki --example canonicalize -- <in.kicad_pcb> --check    # exit 1 if not canonical

use std::path::Path;
use std::process::ExitCode;

use kiclaude_ki::format::v9::{emit_pcb, emit_sch, pcb};
use kiclaude_ki::sexpr::parse_str;

fn main() -> ExitCode {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let Some(path_arg) = args.first() else {
        eprintln!(
            "usage: canonicalize <in.kicad_pcb> [--write|--check]\n\
             \n\
             default: print canonical form to stdout\n\
             --write: rewrite the file in place with its canonical form\n\
             --check: exit 1 if the file is not already canonical"
        );
        return ExitCode::FAILURE;
    };
    let write = args.iter().any(|a| a == "--write");
    let check = args.iter().any(|a| a == "--check");

    let path = Path::new(path_arg);
    let src = match std::fs::read_to_string(path) {
        Ok(s) => s,
        Err(e) => {
            eprintln!("read {}: {e}", path.display());
            return ExitCode::FAILURE;
        }
    };

    let ext = path.extension().and_then(|s| s.to_str()).unwrap_or("");
    let canonical = match ext {
        "kicad_pcb" => canonicalize_pcb(&src),
        "kicad_sch" => canonicalize_sch(&src),
        other => Err(format!(
            "unsupported extension {other:?}; expected .kicad_pcb or .kicad_sch"
        )),
    };
    let canonical = match canonical {
        Ok(c) => c,
        Err(e) => {
            eprintln!("canonicalize {}: {e}", path.display());
            return ExitCode::FAILURE;
        }
    };

    if check {
        if canonical == src {
            eprintln!("{}: already canonical", path.display());
            return ExitCode::SUCCESS;
        }
        eprintln!(
            "{}: NOT canonical (run without --check to see the canonical form)",
            path.display()
        );
        return ExitCode::FAILURE;
    }

    if write {
        if let Err(e) = std::fs::write(path, &canonical) {
            eprintln!("write {}: {e}", path.display());
            return ExitCode::FAILURE;
        }
        eprintln!(
            "{}: wrote canonical form ({} bytes)",
            path.display(),
            canonical.len()
        );
        return ExitCode::SUCCESS;
    }

    print!("{canonical}");
    ExitCode::SUCCESS
}

/// `.kicad_pcb`: `parse → map_pcb → emit_pcb` — the canonical form the
/// PCB golden gate (`tests/golden/tests/round_trip_pcb.rs`,
/// `crates/ki/tests/golden.rs`) asserts against.
fn canonicalize_pcb(src: &str) -> Result<String, String> {
    let nodes = parse_str(src).map_err(|e| format!("parse: {e}"))?;
    let root = nodes.first().ok_or_else(|| "empty document".to_string())?;
    let board = pcb::map_pcb(root).map_err(|m| format!("map_pcb: {m}"))?;
    Ok(emit_pcb(&board))
}

/// `.kicad_sch`: `parse → emit_sch(root, src)` — the source-aware
/// canonical form the schematic golden gate
/// (`tests/golden/tests/round_trip_sch.rs`) asserts against.
fn canonicalize_sch(src: &str) -> Result<String, String> {
    let nodes = parse_str(src).map_err(|e| format!("parse: {e}"))?;
    let root = nodes.first().ok_or_else(|| "empty document".to_string())?;
    Ok(emit_sch(root, src))
}
