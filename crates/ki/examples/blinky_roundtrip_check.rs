//! Ad-hoc verification: parse `examples/blinky/blinky.kicad_pcb` and
//! confirm `emit_pcb(map_pcb(parse(src)))` is byte-identical to the
//! file's text. Run with `cargo run -p kiclaude-ki --example blinky_roundtrip_check`.
//!
//! Used during M0-C-03 development to confirm the hand-written blinky
//! PCB matches the canonical form. Promoted to a real CI gate in M0-Q-02.

use std::fs;
use std::process::ExitCode;

use kiclaude_ki::format::v9::{emit_pcb, pcb};
use kiclaude_ki::sexpr::parse_str;

fn main() -> ExitCode {
    let path = "examples/blinky/blinky.kicad_pcb";
    let src = match fs::read_to_string(path) {
        Ok(s) => s,
        Err(e) => {
            eprintln!("could not read {path}: {e}");
            return ExitCode::from(1);
        }
    };
    let nodes = match parse_str(&src) {
        Ok(n) => n,
        Err(e) => {
            eprintln!("parse failed: {e}");
            return ExitCode::from(1);
        }
    };
    let Some(root) = nodes.first() else {
        eprintln!("empty .kicad_pcb");
        return ExitCode::from(1);
    };
    let pcb = match pcb::map_pcb(root) {
        Ok(p) => p,
        Err(m) => {
            eprintln!("map_pcb failed: {m}");
            return ExitCode::from(1);
        }
    };
    let emitted = emit_pcb(&pcb);
    if src == emitted {
        println!("BYTE-IDENTICAL ({} bytes)", src.len());
        ExitCode::SUCCESS
    } else {
        eprintln!(
            "MISMATCH: src.len()={}, emitted.len()={}",
            src.len(),
            emitted.len()
        );
        // Show first diff for diagnostics.
        for (i, (a, b)) in src.bytes().zip(emitted.bytes()).enumerate() {
            if a != b {
                let lo = i.saturating_sub(40);
                let hi_a = (i + 40).min(src.len());
                let hi_b = (i + 40).min(emitted.len());
                eprintln!("first diff at byte {i}:");
                eprintln!("  src    : {:?}", &src[lo..hi_a]);
                eprintln!("  emitted: {:?}", &emitted[lo..hi_b]);
                break;
            }
        }
        if src.len() != emitted.len() && src.bytes().zip(emitted.bytes()).all(|(a, b)| a == b) {
            let i = src.len().min(emitted.len());
            eprintln!("trailing diff at byte {i}:");
            eprintln!("  src    : {:?}", &src[i..]);
            eprintln!("  emitted: {:?}", &emitted[i..]);
        }
        ExitCode::from(2)
    }
}
