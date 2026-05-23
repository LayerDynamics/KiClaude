//! M2-R-06 — cross-check our Rust DRC kernel against `kicad-cli pcb drc`.
//!
//! For each of 3 reference boards (blinky + the two zone fixtures), we:
//!
//! 1. Parse the PCB via `kiclaude-ki` → KCIR.
//! 2. Translate the KCIR `Pcb` into the kernel's `DrcInput` shape.
//! 3. Run `kiclaude_cad::drc::check_all`.
//! 4. Run `kicad-cli pcb drc --format json --severity-all`.
//! 5. Assert every issue our kernel reports has a kicad-cli
//!    counterpart of compatible type within a small position
//!    tolerance — i.e. **zero false-positives** that kicad-cli
//!    doesn't also flag.
//!
//! We deliberately do NOT assert the reverse direction (kicad-cli's
//! issues ⊆ ours) — KiCad runs a richer check set (silkscreen,
//! library-link, schematic-parity, …) we explicitly excluded per the
//! M2-R-06 scope notes.
//!
//! The test is skipped when `kicad-cli` isn't on PATH so it runs on
//! the dev box without forcing the wider workspace install.

use std::collections::HashMap;
use std::fs;
use std::path::{Path, PathBuf};
use std::process::Command;

use kiclaude_cad::drc::{
    check_all, DrcCourtyard, DrcInput, DrcIssue, DrcIssueKind, DrcPad, DrcPadShape, DrcTrack,
    DrcVia,
};
use kiclaude_cad::{Point, Polygon};
use kiclaude_ki::format::v9::pcb::map_pcb;
use kiclaude_ki::kcir::Pcb;
use kiclaude_ki::sexpr::parse_str;

/// Tolerance for "issue at the same place" when cross-matching our
/// kernel's findings to kicad-cli's. `0.5 mm` is loose enough that
/// small differences in how each tool picks the marker position
/// (centroid vs midpoint, etc.) don't cause spurious mismatches but
/// tight enough that two unrelated issues on the same board don't
/// collide.
const POSITION_TOLERANCE_MM: f64 = 0.5;

#[test]
fn drc_kernel_no_false_positives_on_blinky() {
    cross_check("examples/blinky/blinky.kicad_pcb");
}

#[test]
fn drc_kernel_no_false_positives_on_zone_simple() {
    cross_check("tests/golden/zones/simple_filled.kicad_pcb");
}

#[test]
fn drc_kernel_no_false_positives_on_zone_thermal() {
    cross_check("tests/golden/zones/thermal_filled.kicad_pcb");
}

fn cross_check(rel_path: &str) {
    let pcb_path = repo_root().join(rel_path);
    assert!(pcb_path.exists(), "fixture missing: {}", pcb_path.display());

    let pcb = load_pcb(&pcb_path);
    let input = build_drc_input(&pcb);
    let our_issues = check_all(&input);

    let Some(kicad_violations) = run_kicad_cli_drc(&pcb_path) else {
        eprintln!(
            "[drc] skipping {} — kicad-cli not on PATH",
            pcb_path.display(),
        );
        return;
    };

    let unmatched = find_unmatched(&our_issues, &kicad_violations);
    assert!(
        unmatched.is_empty(),
        "{}: {} Rust DRC issues have no kicad-cli counterpart: {:#?}",
        pcb_path.display(),
        unmatched.len(),
        unmatched,
    );
}

fn repo_root() -> PathBuf {
    // tests/golden/Cargo.toml → repo root is two levels up.
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .and_then(Path::parent)
        .map_or_else(
            || Path::new(env!("CARGO_MANIFEST_DIR")).to_path_buf(),
            Path::to_path_buf,
        )
}

fn load_pcb(path: &Path) -> Pcb {
    let text = fs::read_to_string(path).unwrap_or_else(|e| panic!("read {}: {e}", path.display()));
    let nodes = parse_str(&text).unwrap_or_else(|e| panic!("parse {}: {e:?}", path.display()));
    let root = nodes
        .into_iter()
        .next()
        .unwrap_or_else(|| panic!("{}: empty s-expression file", path.display()));
    map_pcb(&root).unwrap_or_else(|e| panic!("map_pcb {}: {e}", path.display()))
}

/// Translate KCIR `Pcb` into the DRC kernel's input shape.
fn build_drc_input(pcb: &Pcb) -> DrcInput {
    let mut input = DrcInput {
        default_clearance_mm: 0.2,
        min_annular_ring_mm: 0.05,
        min_drill_to_copper_mm: 0.2,
        ..DrcInput::default()
    };

    // Net classes — record class clearances, then map nets→class via
    // each net's `class` reference.
    for nc in &pcb.net_classes {
        if nc.clearance_mm > 0.0 {
            input
                .net_class_clearances_mm
                .insert(nc.name.clone(), nc.clearance_mm);
        }
    }
    for net in &pcb.nets {
        if !net.class.0.is_empty() {
            input
                .net_to_class
                .insert(net.name.clone(), net.class.0.clone());
        }
    }

    // Tracks — break polylines into segment pairs.
    for track in &pcb.tracks {
        let pts = &track.points_mm;
        if pts.len() < 2 {
            continue;
        }
        for i in 0..pts.len() - 1 {
            input.tracks.push(DrcTrack {
                uuid: track.uuid.clone(),
                net: track.net.clone(),
                layer: track.layer.0.clone(),
                start_mm: Point::new(pts[i].0, pts[i].1),
                end_mm: Point::new(pts[i + 1].0, pts[i + 1].1),
                width_mm: track.width_mm,
            });
        }
    }

    // Vias.
    for via in &pcb.vias {
        input.vias.push(DrcVia {
            uuid: via.uuid.clone(),
            net: via.net.clone(),
            position_mm: Point::new(via.position_mm.0, via.position_mm.1),
            layers: vec![via.from_layer.0.clone(), via.to_layer.0.clone()],
            drill_mm: via.drill_mm,
            diameter_mm: via.diameter_mm,
        });
    }

    // Pads and courtyards from footprints.
    for fp in &pcb.footprints {
        let (fx, fy) = fp.position_mm;
        let (sin_f, cos_f) = fp.rotation_deg.to_radians().sin_cos();

        for pad in &fp.pads {
            let (px, py) = pad.position_mm;
            let bx = fx + px * cos_f - py * sin_f;
            let by = fy + px * sin_f + py * cos_f;
            input.pads.push(DrcPad {
                footprint_refdes: fp.refdes.clone(),
                number: pad.number.clone(),
                net: pad.net.clone(),
                center_mm: Point::new(bx, by),
                size_mm: pad.size_mm,
                rotation_deg: fp.rotation_deg + pad.rotation_deg,
                layers: pad.layers.iter().map(|l| l.0.clone()).collect(),
                shape: pad_shape(&pad.shape),
                drill_mm: pad.drill_mm.map(|(d, _)| d).unwrap_or(0.0),
            });
        }

        if let Some(crt) = &fp.courtyard {
            let pts: Vec<Point> = crt
                .points_mm
                .iter()
                .map(|&(x, y)| {
                    let bx = fx + x * cos_f - y * sin_f;
                    let by = fy + x * sin_f + y * cos_f;
                    Point::new(bx, by)
                })
                .collect();
            if pts.len() >= 3 {
                input.courtyards.push(DrcCourtyard {
                    footprint_refdes: fp.refdes.clone(),
                    layer: crt.layer.0.clone(),
                    polygon: Polygon::new(pts),
                });
            }
        }
    }

    input
}

fn pad_shape(shape: &str) -> DrcPadShape {
    match shape {
        "circle" => DrcPadShape::Circle,
        "oval" => DrcPadShape::Oval,
        "roundrect" => DrcPadShape::RoundRect,
        _ => DrcPadShape::Rect,
    }
}

/// One violation pulled out of `kicad-cli pcb drc --format json`,
/// flattened to the fields we need for cross-matching.
#[derive(Debug, Clone)]
struct KicadViolation {
    /// `kicad-cli`'s `type` slug — e.g. `"clearance"`, `"hole_clearance"`.
    kind: String,
    /// Severity as reported by `kicad-cli` (`error` / `warning` /
    /// `exclusion`). Kept for debugging output when a cross-check
    /// fails; not used in the match logic itself.
    #[allow(dead_code)]
    severity: String,
    position_mm: Option<Point>,
}

/// Run `kicad-cli pcb drc` and return the list of geometric
/// violations. `None` means `kicad-cli` isn't on PATH (test skipped).
fn run_kicad_cli_drc(pcb_path: &Path) -> Option<Vec<KicadViolation>> {
    let out_path = std::env::temp_dir().join(format!(
        "kiclaude_drc_{}.json",
        pcb_path.file_stem().and_then(|s| s.to_str()).unwrap_or("?"),
    ));
    let status = Command::new("kicad-cli")
        .args([
            "pcb",
            "drc",
            "--format",
            "json",
            "--severity-all",
            "--units",
            "mm",
            "--output",
        ])
        .arg(&out_path)
        .arg(pcb_path)
        .status();
    let status = match status {
        Ok(s) => s,
        Err(_) => return None,
    };
    if !status.success() {
        // `kicad-cli pcb drc` exits non-zero only with
        // `--exit-code-violations`; without it, a non-zero status
        // means the tool itself errored — propagate as test failure.
        panic!(
            "kicad-cli pcb drc failed (status {}) for {}",
            status,
            pcb_path.display(),
        );
    }
    let text = fs::read_to_string(&out_path)
        .unwrap_or_else(|e| panic!("read {}: {e}", out_path.display()));
    let json: serde_json::Value = serde_json::from_str(&text).expect("kicad drc json");
    let mut out = Vec::new();
    if let Some(arr) = json.get("violations").and_then(|v| v.as_array()) {
        for v in arr {
            let kind = v
                .get("type")
                .and_then(|t| t.as_str())
                .unwrap_or("")
                .to_string();
            let severity = v
                .get("severity")
                .and_then(|s| s.as_str())
                .unwrap_or("")
                .to_string();
            let position_mm = v
                .get("items")
                .and_then(|i| i.as_array())
                .and_then(|arr| arr.first())
                .and_then(|item| item.get("pos"))
                .and_then(|p| Some(Point::new(p.get("x")?.as_f64()?, p.get("y")?.as_f64()?)));
            out.push(KicadViolation {
                kind,
                severity,
                position_mm,
            });
        }
    }
    Some(out)
}

/// Find Rust kernel issues that don't have a matching kicad-cli
/// violation. Matching = same canonical kind + position within
/// `POSITION_TOLERANCE_MM`.
fn find_unmatched(ours: &[DrcIssue], theirs: &[KicadViolation]) -> Vec<DrcIssue> {
    let theirs_by_kind: HashMap<&str, Vec<&KicadViolation>> = {
        let mut m: HashMap<&str, Vec<&KicadViolation>> = HashMap::new();
        for v in theirs {
            m.entry(v.kind.as_str()).or_default().push(v);
        }
        m
    };

    let mut unmatched = Vec::new();
    for our in ours {
        let candidate_kinds = kicad_kind_for(our.kind);
        let mut matched = false;
        'outer: for k in candidate_kinds {
            let Some(bucket) = theirs_by_kind.get(k) else {
                continue;
            };
            for v in bucket {
                let pos_ok = v
                    .position_mm
                    .map(|p| p.distance_to(&our.position_mm) <= POSITION_TOLERANCE_MM)
                    .unwrap_or(true);
                if pos_ok {
                    matched = true;
                    break 'outer;
                }
            }
        }
        if !matched {
            unmatched.push(our.clone());
        }
    }
    let _ = unmatched.iter().map(|i| i.severity).collect::<Vec<_>>(); // silence pedantic-style warnings
    unmatched
}

/// Map our kernel's `DrcIssueKind` to the set of `kicad-cli` `type`
/// slugs that are compatible. `kicad-cli` distinguishes more
/// sub-categories than we do (e.g. it has separate slugs for
/// `clearance`, `hole_clearance`, `track_clearance`); any of them
/// counts as a match.
fn kicad_kind_for(kind: DrcIssueKind) -> &'static [&'static str] {
    match kind {
        DrcIssueKind::ClearanceViolation => &[
            "clearance",
            "track_clearance",
            "via_clearance",
            "pad_clearance",
            "copper_edge_clearance",
        ],
        DrcIssueKind::CourtyardOverlap => &["courtyard_overlap", "courtyards_overlap"],
        DrcIssueKind::AnnularRingViolation => &["annular_width"],
        DrcIssueKind::DrillToCopperViolation => &[
            "hole_clearance",
            "hole_to_hole",
            "drill_out_of_range",
            "drilled_holes_too_close",
        ],
    }
}
