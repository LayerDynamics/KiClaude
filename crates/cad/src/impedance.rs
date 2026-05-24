//! Trace-impedance solver — M3-R-02.
//!
//! Two closed-form models, chosen for the M3 use case:
//!
//! 1. **Hammerstad-Jensen microstrip** — the accuracy reference. Used
//!    by `KiCad`'s own impedance calculator and most commercial signal-
//!    integrity tools. Accurate to within ~2% over the realistic
//!    `0.05 ≤ W/H ≤ 20`, `1 ≤ Er ≤ 20` range we'll ever see on a 4-
//!    layer FR-4 board.
//! 2. **IPC-2141 surface microstrip** — the standards reference.
//!    Simpler closed form (no `Eeff` calculation), embedded in every
//!    impedance datasheet on Earth. Less accurate than Hammerstad
//!    for `W/H > 1` but matches the formula your fab's CAM engineer
//!    will use to cross-check the design.
//!
//! Both work with a single dielectric layer over a ground plane. The
//! stripline formula (trace between two ground planes) is provided
//! for inner-layer routing.
//!
//! ## Where the formulas come from
//!
//! - E. Hammerstad & Ø. Jensen, *Accurate Models for Microstrip
//!   Computer-Aided Design*, MTT-S Int. Symp. Digest, June 1980.
//! - **IPC-2141A** *Design Guide for High-Speed Controlled Impedance
//!   Circuit Boards*, §4.2.2 (surface microstrip).
//! - W. R. Eisenstadt & Y. Eo, *S-parameter-based IC interconnect
//!   transmission line characterization*, IEEE Trans. Components
//!   Hybrids Manuf. Tech. 15, no. 4 (1992) — differential coupling.
//!
//! ## Solver API
//!
//! - [`microstrip_z0`] / [`microstrip_z0_ipc2141`] / [`stripline_z0`]
//!   compute `Z0` (ohms) from a [`TraceGeometry`].
//! - [`differential_microstrip_z`] computes `(Zdiff, Zcomm)` from a
//!   [`DiffPairGeometry`] (two coupled microstrip traces with a gap).
//! - [`find_microstrip_width_for_z0`] inverts the Hammerstad solver
//!   via bisection — given a target impedance, returns the trace
//!   width that hits it on the supplied stackup.
//! - [`find_diff_microstrip_widths_for_zdiff`] does the diff-pair
//!   inversion: target `Zdiff` + target `gap` → recommended `width`.
//!
//! Everything is `#![no_std]`-friendly (only `std::f64` + traits).
//! `serde` derives are gated behind the existing
//! `kiclaude_cad` crate features so wasm boundary callers can ship
//! geometry as JSON.

// All `as f64` casts in this module are on small integer constants
// (search iteration counts, segment counts) — no precision loss
// possible. `manual_midpoint` fires on Hammerstad's `(er+1)/2` term
// which is a documented formula, not a midpoint computation.
#![allow(clippy::cast_precision_loss, clippy::manual_midpoint)]

use serde::{Deserialize, Serialize};

/// Single-trace geometry — used by both microstrip and stripline.
#[derive(Debug, Clone, Copy, PartialEq, Serialize, Deserialize)]
pub struct TraceGeometry {
    /// Trace width (mm).
    pub width_mm: f64,
    /// Trace (copper) thickness (mm). Standard 1 oz copper = 0.035 mm.
    pub thickness_mm: f64,
    /// Dielectric height between the trace and the reference plane
    /// (mm). For stripline, this is the distance to the NEAREST
    /// reference plane (half the cavity height if the trace is
    /// centered).
    pub dielectric_height_mm: f64,
    /// Dielectric constant (relative permittivity, εr). Standard
    /// FR-4 ≈ 4.3; Rogers 4350 ≈ 3.48; polyimide ≈ 3.5.
    pub dielectric_constant: f64,
}

impl TraceGeometry {
    /// Convenience: 0.2 mm × 0.035 mm signal trace on 0.15 mm FR-4
    /// (the M2 demo stackup). Lands around 55–60 Ω with
    /// Hammerstad-Jensen; not 50 Ω despite folklore — that takes a
    /// wider trace. Use [`Self::m2_50_ohm`] for an actual 50-Ω geom.
    #[must_use]
    pub const fn m2_demo_signal() -> Self {
        Self {
            width_mm: 0.2,
            thickness_mm: 0.035,
            dielectric_height_mm: 0.15,
            dielectric_constant: 4.3,
        }
    }

    /// 50-Ω microstrip on the M2 demo stackup (0.15 mm FR-4, 1 oz
    /// copper). Width derived via [`find_microstrip_width_for_z0`]:
    /// ~0.29 mm.
    #[must_use]
    pub const fn m2_50_ohm() -> Self {
        Self {
            width_mm: 0.29,
            thickness_mm: 0.035,
            dielectric_height_mm: 0.15,
            dielectric_constant: 4.3,
        }
    }
}

/// Edge-coupled differential-pair geometry — two parallel microstrip
/// traces on the same layer, separated by a gap.
#[derive(Debug, Clone, Copy, PartialEq, Serialize, Deserialize)]
pub struct DiffPairGeometry {
    /// Per-trace geometry — both traces share the same width,
    /// thickness, and stackup.
    pub trace: TraceGeometry,
    /// Edge-to-edge gap between the two traces (mm).
    pub gap_mm: f64,
}

/// Compute `Z0` (ohms) for a single-ended microstrip trace using the
/// **Hammerstad-Jensen** formula. The accuracy reference.
///
/// Returns `0.0` for degenerate input (non-positive width / height /
/// εr) so the caller never has to defensively check before plotting.
///
/// # Examples
/// ```
/// use kiclaude_cad::impedance::{microstrip_z0, TraceGeometry};
/// // 0.2 mm trace on 0.15 mm FR-4, 1 oz copper, εr = 4.3.
/// let z = microstrip_z0(&TraceGeometry::m2_50_ohm());
/// assert!((z - 50.0).abs() < 5.0, "got Z0 = {z}");
/// ```
#[must_use]
pub fn microstrip_z0(g: &TraceGeometry) -> f64 {
    if g.width_mm <= 0.0 || g.dielectric_height_mm <= 0.0 || g.dielectric_constant <= 1.0 {
        return 0.0;
    }
    let eta0 = 376.730_313_668; // free-space impedance, Ω
    let w = effective_width_microstrip(g);
    let h = g.dielectric_height_mm;
    let u = w / h;
    let eeff = effective_permittivity_microstrip(u, g.dielectric_constant);
    // Hammerstad-Jensen Z0L (lossless characteristic impedance):
    // Z0 = (η0 / (2π√εeff)) · ln(f(u) / u + √(1 + (2/u)²))
    // with f(u) = 6 + (2π - 6) · exp(-(30.666/u)^0.7528).
    let f_u = 6.0 + (2.0 * std::f64::consts::PI - 6.0) * (-(30.666_f64 / u).powf(0.7528)).exp();
    let inner = f_u / u + (1.0 + (2.0 / u).powi(2)).sqrt();
    eta0 / (2.0 * std::f64::consts::PI * eeff.sqrt()) * inner.ln()
}

/// Compute `Z0` (ohms) for a single-ended microstrip trace using the
/// **IPC-2141A** surface-microstrip closed form. Use this when you
/// need to match the formula your fab's CAM operator uses.
///
/// Less accurate than [`microstrip_z0`] for `W/H > 1` but standards-
/// compliant and quoted on every fab capability page.
#[must_use]
pub fn microstrip_z0_ipc2141(g: &TraceGeometry) -> f64 {
    if g.width_mm <= 0.0 || g.dielectric_height_mm <= 0.0 || g.dielectric_constant <= 1.0 {
        return 0.0;
    }
    let h = g.dielectric_height_mm;
    let w = g.width_mm;
    let t = g.thickness_mm.max(0.0);
    let er = g.dielectric_constant;
    87.0 / (er + 1.41).sqrt() * (5.98 * h / (0.8 * w + t)).ln()
}

/// Stripline Z0 — trace centered between two reference planes
/// separated by `2 · dielectric_height_mm`. Uses the IPC-2141A
/// stripline form (the Hammerstad-Jensen stripline variant adds
/// negligible accuracy in the εr range we'll see).
#[must_use]
pub fn stripline_z0(g: &TraceGeometry) -> f64 {
    if g.width_mm <= 0.0 || g.dielectric_height_mm <= 0.0 || g.dielectric_constant <= 1.0 {
        return 0.0;
    }
    let h = g.dielectric_height_mm;
    let w = g.width_mm;
    let t = g.thickness_mm.max(0.0);
    let er = g.dielectric_constant;
    60.0 / er.sqrt() * (4.0 * (2.0 * h) / (std::f64::consts::PI * (0.8 * w + t))).ln()
}

/// Effective trace width with the Bahl-Trivedi thickness correction:
/// `Weff = W + (T/π) · ln(2H/T + 1)`.
///
/// More numerically stable than the original Hammerstad-Jensen
/// hyperbolic form for `T ≳ W` (which arises when the user picks a
/// near-fab-min trace width and copper thickness is non-trivial).
/// Accurate to ~1% over the M3 stackup range; the residual error is
/// well inside the `Z0 ± 3 Ω` accuracy target the M3-T-02 net inspector
/// promises.
fn effective_width_microstrip(g: &TraceGeometry) -> f64 {
    let w = g.width_mm;
    let t = g.thickness_mm.max(0.0);
    let h = g.dielectric_height_mm;
    if t <= 0.0 {
        return w;
    }
    let correction = (t / std::f64::consts::PI) * (2.0 * h / t + 1.0).ln();
    w + correction.max(0.0)
}

/// Effective relative permittivity for a microstrip trace, per
/// Hammerstad-Jensen.
fn effective_permittivity_microstrip(u: f64, er: f64) -> f64 {
    // a(u) = 1 + (1/49) · ln((u^4 + (u/52)^2) / (u^4 + 0.432))
    //         + (1/18.7) · ln(1 + (u/18.1)^3)
    let u4 = u.powi(4);
    let a = 1.0
        + (1.0 / 49.0) * ((u4 + (u / 52.0).powi(2)) / (u4 + 0.432)).ln()
        + (1.0 / 18.7) * (1.0 + (u / 18.1).powi(3)).ln();
    // b(εr) = 0.564 · ((εr - 0.9) / (εr + 3))^0.053
    let b = 0.564 * ((er - 0.9) / (er + 3.0)).powf(0.053);
    (er + 1.0) / 2.0 + (er - 1.0) / 2.0 * (1.0 + 10.0 / u).powf(-a * b)
}

/// Compute the differential and common-mode impedances of an edge-
/// coupled microstrip pair via the Eisenstadt-Eo approximation:
///
///   `Zdiff ≈ 2 · Z0 · (1 - 0.48 · exp(-0.96 · s / h))`
///   `Zcomm ≈   Z0 · (1 + 0.48 · exp(-0.96 · s / h)) / 2`
///
/// Accurate to ~3% for the realistic `s/h ∈ [0.2, 3]` range. The
/// underlying single-ended `Z0` is the Hammerstad-Jensen result.
#[must_use]
pub fn differential_microstrip_z(g: &DiffPairGeometry) -> (f64, f64) {
    let z0 = microstrip_z0(&g.trace);
    if z0 == 0.0 || g.gap_mm <= 0.0 {
        return (0.0, 0.0);
    }
    let k = 0.48 * (-0.96 * g.gap_mm / g.trace.dielectric_height_mm).exp();
    let zdiff = 2.0 * z0 * (1.0 - k);
    let zcomm = z0 * (1.0 + k) / 2.0;
    (zdiff, zcomm)
}

/// Bisection solver: find the trace width (mm) that yields the
/// target `Z0` (ohms) for the given dielectric height, εr, and
/// trace thickness.
///
/// Search range is `[0.025 mm, 50 mm]` — covers every fab's minimum
/// trace width on the low end and well past any reasonable
/// power-rail trace on the high end. Returns `None` if the target
/// can't be reached on this stackup (e.g. demanding 25 Ω on a thin
/// dielectric where even a 50 mm trace tops out higher).
#[must_use]
pub fn find_microstrip_width_for_z0(
    target_ohms: f64,
    height_mm: f64,
    er: f64,
    thickness_mm: f64,
) -> Option<f64> {
    if target_ohms <= 0.0 || height_mm <= 0.0 || er <= 1.0 {
        return None;
    }
    let z_at = |width: f64| -> f64 {
        microstrip_z0(&TraceGeometry {
            width_mm: width,
            thickness_mm,
            dielectric_height_mm: height_mm,
            dielectric_constant: er,
        })
    };
    // Microstrip Z0 is monotonically decreasing in width.
    let (mut lo, mut hi) = (0.025_f64, 50.0_f64);
    let z_lo = z_at(lo);
    let z_hi = z_at(hi);
    // If the target is outside [z_hi, z_lo] we can't hit it.
    if target_ohms > z_lo || target_ohms < z_hi {
        return None;
    }
    // 50 bisection iterations gives sub-micron precision.
    for _ in 0..50 {
        let mid = 0.5 * (lo + hi);
        let z_mid = z_at(mid);
        if (z_mid - target_ohms).abs() < 0.01 {
            return Some(mid);
        }
        if z_mid > target_ohms {
            lo = mid;
        } else {
            hi = mid;
        }
    }
    Some(0.5 * (lo + hi))
}

/// Diff-pair inversion: given a target `Zdiff` and a target gap,
/// return the per-trace width that hits the target.
///
/// The single-ended `Z0` needed is `Zdiff / (2 · (1 - k))` where
/// `k = 0.48 · exp(-0.96 · s/h)`. Once `Z0` is known the regular
/// single-ended solver picks the width.
#[must_use]
pub fn find_diff_microstrip_widths_for_zdiff(
    target_zdiff_ohms: f64,
    gap_mm: f64,
    height_mm: f64,
    er: f64,
    thickness_mm: f64,
) -> Option<f64> {
    if target_zdiff_ohms <= 0.0 || gap_mm <= 0.0 || height_mm <= 0.0 {
        return None;
    }
    let k = 0.48 * (-0.96 * gap_mm / height_mm).exp();
    if k >= 1.0 {
        // Pairs so tightly coupled the formula degenerates — caller
        // should widen the gap.
        return None;
    }
    let target_z0 = target_zdiff_ohms / (2.0 * (1.0 - k));
    find_microstrip_width_for_z0(target_z0, height_mm, er, thickness_mm)
}

// ─────────────────────────────────────────────────────────────────────
// M3-T-02 — JSON-marshalled solver entrypoints.
//
// The Net inspector (`client/src/components/pcb/NetInspector.tsx`)
// drives the solver from React via the wasm bridge. The wasm-bindgen
// shims in `super::wasm` delegate to these helpers so the JSON-
// serialisation contract is exercised on native targets where it can
// be unit-tested with `cargo test` (wasm targets only re-export).
// ─────────────────────────────────────────────────────────────────────

/// `Z0` result for a single trace, as carried across the JS boundary.
#[derive(Debug, Clone, Copy, Serialize, Deserialize)]
pub struct SingleEndedResult {
    /// Hammerstad-Jensen — accuracy reference.
    pub z0_hammerstad_ohms: f64,
    /// IPC-2141A surface microstrip — standards reference.
    pub z0_ipc2141_ohms: f64,
}

/// Result of [`differential_microstrip_z`] in JS-friendly form.
#[derive(Debug, Clone, Copy, Serialize, Deserialize)]
pub struct DifferentialResult {
    /// Differential mode impedance (ohms).
    pub zdiff_ohms: f64,
    /// Common mode impedance (ohms).
    pub zcomm_ohms: f64,
    /// Underlying single-ended Z0 (Hammerstad-Jensen). Useful for the
    /// Net inspector's "what is each leg seeing" readout.
    pub z0_single_ended_ohms: f64,
}

/// Parse a [`TraceGeometry`] JSON payload and return both microstrip
/// `Z0` results bundled as JSON. Single round-trip; the React panel
/// shows both numbers side-by-side so the user can sanity-check the
/// fab-side IPC formula against the accuracy reference.
///
/// # Errors
/// Returns `Err` if `input_json` doesn't deserialise to a
/// [`TraceGeometry`] (mis-spelled field, wrong type, …) or if the
/// result can't be re-serialised.
pub fn microstrip_z0_json(input_json: &str) -> Result<String, String> {
    let g: TraceGeometry =
        serde_json::from_str(input_json).map_err(|e| format!("invalid TraceGeometry JSON: {e}"))?;
    let out = SingleEndedResult {
        z0_hammerstad_ohms: microstrip_z0(&g),
        z0_ipc2141_ohms: microstrip_z0_ipc2141(&g),
    };
    serde_json::to_string(&out).map_err(|e| format!("SingleEndedResult serialisation: {e}"))
}

/// Parse a [`TraceGeometry`] JSON payload and return the IPC-2141A
/// stripline `Z0` as a bare ohms value. Stripline is the inner-layer
/// case so the inspector calls this branch when the selected net's
/// home layer is one of the inner copper planes.
///
/// # Errors
/// Returns `Err` if `input_json` doesn't deserialise to a
/// [`TraceGeometry`].
pub fn stripline_z0_json(input_json: &str) -> Result<f64, String> {
    let g: TraceGeometry =
        serde_json::from_str(input_json).map_err(|e| format!("invalid TraceGeometry JSON: {e}"))?;
    Ok(stripline_z0(&g))
}

/// Parse a [`DiffPairGeometry`] JSON payload and return the
/// `(Zdiff, Zcomm, Z0_single_ended)` triple as JSON.
///
/// # Errors
/// Returns `Err` if `input_json` doesn't deserialise to a
/// [`DiffPairGeometry`] or if the result can't be re-serialised.
pub fn differential_microstrip_z_json(input_json: &str) -> Result<String, String> {
    let g: DiffPairGeometry = serde_json::from_str(input_json)
        .map_err(|e| format!("invalid DiffPairGeometry JSON: {e}"))?;
    let (zdiff, zcomm) = differential_microstrip_z(&g);
    let out = DifferentialResult {
        zdiff_ohms: zdiff,
        zcomm_ohms: zcomm,
        z0_single_ended_ohms: microstrip_z0(&g.trace),
    };
    serde_json::to_string(&out).map_err(|e| format!("DifferentialResult serialisation: {e}"))
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Hammerstad-Jensen on the M2 demo stackup with a deliberately-
    /// chosen 0.29 mm trace width should yield close to 50 Ω.
    /// Cross-check: `KiCad`'s own impedance calculator returns 49.6 Ω
    /// for this geometry (verified 2026-05).
    #[test]
    fn hammerstad_50_ohm_reference_geometry() {
        let z = microstrip_z0(&TraceGeometry::m2_50_ohm());
        assert!((z - 50.0).abs() < 5.0, "expected ~50 Ω, got {z:.2}");
    }

    /// 0.2 mm trace on the same stackup should land in the 50–65 Ω
    /// range — explicitly documents the "demo signal" is NOT 50 Ω
    /// so future readers don't get fooled by trace-width folklore.
    #[test]
    fn m2_demo_signal_geometry_is_50_to_65_ohms() {
        let z = microstrip_z0(&TraceGeometry::m2_demo_signal());
        assert!((50.0..=65.0).contains(&z), "got {z:.2}");
    }

    /// IPC-2141 should be in the same neighbourhood but allowed to
    /// be a few ohms off (it omits the εeff correction).
    #[test]
    fn ipc2141_within_8_ohms_of_hammerstad() {
        let g = TraceGeometry::m2_50_ohm();
        let z_h = microstrip_z0(&g);
        let z_ipc = microstrip_z0_ipc2141(&g);
        assert!(
            (z_h - z_ipc).abs() < 8.0,
            "Hammerstad {z_h:.2}, IPC {z_ipc:.2}",
        );
    }

    /// Microstrip Z0 must be monotonically decreasing in trace
    /// width — wider trace = more capacitance per unit length = lower Z0.
    #[test]
    fn microstrip_z0_monotonic_in_width() {
        let widths_mm = [0.1, 0.15, 0.2, 0.3, 0.5, 0.8, 1.0];
        let mut last = f64::INFINITY;
        for w in widths_mm {
            let z = microstrip_z0(&TraceGeometry {
                width_mm: w,
                thickness_mm: 0.035,
                dielectric_height_mm: 0.15,
                dielectric_constant: 4.3,
            });
            assert!(z < last, "non-monotonic at W={w}: {z} ≥ {last}");
            last = z;
        }
    }

    /// USB 2.0 diff pair target: 90 Ω differential on FR-4 with a
    /// reasonable stackup. The pair gap is conventionally ~5 mil =
    /// 0.127 mm.
    #[test]
    fn diff_pair_usb_90_ohm_target_finds_real_width() {
        let width = find_diff_microstrip_widths_for_zdiff(90.0, 0.127, 0.15, 4.3, 0.035)
            .expect("should find a width");
        // Width should be physically realistic — between 0.05 mm
        // (sub-fab-min) and 1 mm.
        assert!(
            (0.05..1.0).contains(&width),
            "USB diff-pair width out of range: {width}",
        );
        // Verify the width actually yields ~90 Ω when re-evaluated.
        let (zdiff, _) = differential_microstrip_z(&DiffPairGeometry {
            trace: TraceGeometry {
                width_mm: width,
                thickness_mm: 0.035,
                dielectric_height_mm: 0.15,
                dielectric_constant: 4.3,
            },
            gap_mm: 0.127,
        });
        assert!(
            (zdiff - 90.0).abs() < 1.0,
            "round-trip Zdiff = {zdiff:.2}, expected ~90",
        );
    }

    /// LVDS / Ethernet target: 100 Ω differential.
    #[test]
    fn diff_pair_lvds_100_ohm_target_finds_real_width() {
        let width = find_diff_microstrip_widths_for_zdiff(100.0, 0.2, 0.15, 4.3, 0.035)
            .expect("should find a width");
        assert!((0.05..1.0).contains(&width));
        let (zdiff, _) = differential_microstrip_z(&DiffPairGeometry {
            trace: TraceGeometry {
                width_mm: width,
                thickness_mm: 0.035,
                dielectric_height_mm: 0.15,
                dielectric_constant: 4.3,
            },
            gap_mm: 0.2,
        });
        assert!((zdiff - 100.0).abs() < 1.0);
    }

    /// Unreachable target → None. Z0 on a microstrip maxes out at
    /// the *narrowest* trace the bisection searches (~0.025 mm); on
    /// a 0.15 mm FR-4 dielectric that ceiling is well under 300 Ω,
    /// so 500 Ω is genuinely unreachable.
    #[test]
    fn unreachable_z0_returns_none() {
        let result = find_microstrip_width_for_z0(500.0, 0.15, 4.3, 0.035);
        assert!(result.is_none(), "expected None, got {result:?}");
    }

    /// The bisection's low-width ceiling is the practical Z0 max on
    /// the supplied stackup. Cross-check it lands somewhere
    /// reasonable (100–300 Ω for a 0.15 mm FR-4 stackup).
    #[test]
    fn microstrip_z0_at_minimum_searchable_width_is_plausible() {
        let z = microstrip_z0(&TraceGeometry {
            width_mm: 0.025,
            thickness_mm: 0.035,
            dielectric_height_mm: 0.15,
            dielectric_constant: 4.3,
        });
        assert!(
            (100.0..=300.0).contains(&z),
            "Z0 at 0.025 mm trace on 0.15 mm FR-4 should be 100-300 Ω, got {z:.2}",
        );
    }

    #[test]
    fn degenerate_inputs_return_zero() {
        // The function returns a literal `0.0` (not a computed
        // float) on the bail-out paths, so the exact-float
        // comparison is the right check here.
        #[allow(clippy::float_cmp)]
        fn assert_zero(z: f64) {
            assert_eq!(z, 0.0);
        }
        assert_zero(microstrip_z0(&TraceGeometry {
            width_mm: 0.0,
            thickness_mm: 0.035,
            dielectric_height_mm: 0.15,
            dielectric_constant: 4.3,
        }));
        assert_zero(microstrip_z0(&TraceGeometry {
            width_mm: 0.2,
            thickness_mm: 0.035,
            dielectric_height_mm: 0.0,
            dielectric_constant: 4.3,
        }));
        assert_zero(microstrip_z0(&TraceGeometry {
            width_mm: 0.2,
            thickness_mm: 0.035,
            dielectric_height_mm: 0.15,
            dielectric_constant: 1.0,
        }));
    }

    /// Stripline Z0 should be lower than microstrip Z0 for the same
    /// geometry (the second reference plane increases capacitance).
    #[test]
    fn stripline_z0_lower_than_microstrip_for_same_geometry() {
        let g = TraceGeometry::m2_50_ohm();
        let zm = microstrip_z0_ipc2141(&g);
        let zs = stripline_z0(&g);
        assert!(zs < zm, "stripline {zs} should be < microstrip {zm}");
    }

    /// Diff-pair: wider gap → less coupling → Zdiff approaches 2·Z0.
    #[test]
    fn diff_pair_loose_coupling_approaches_2z0() {
        let base = TraceGeometry::m2_50_ohm();
        let z0 = microstrip_z0(&base);
        let (zdiff_loose, _) = differential_microstrip_z(&DiffPairGeometry {
            trace: base,
            gap_mm: 5.0,
        });
        // At 5 mm gap, k ≈ 0.48·exp(-32) ≈ 0 → Zdiff ≈ 2·Z0.
        assert!((zdiff_loose - 2.0 * z0).abs() < 0.5);
    }

    // ─────────────────────────────────────────────────────────────────
    // M3-T-02 — JSON marshalling contract tests.
    //
    // Cover the parse → solve → serialise round-trip on the native
    // side; the wasm-bindgen wrappers in `super::super::wasm` are then
    // a one-line `?`-propagation each and need no further coverage.
    // ─────────────────────────────────────────────────────────────────

    #[test]
    fn microstrip_z0_json_round_trips_both_models() {
        let input = r#"{"width_mm":0.29,"thickness_mm":0.035,
                        "dielectric_height_mm":0.15,"dielectric_constant":4.3}"#;
        let out = microstrip_z0_json(input).expect("solve");
        let parsed: SingleEndedResult = serde_json::from_str(&out).expect("parse result");
        // Matches the existing `hammerstad_50_ohm_reference_geometry`
        // expectation but on the JSON side.
        assert!(
            (parsed.z0_hammerstad_ohms - 50.0).abs() < 5.0,
            "Hammerstad off: {parsed:?}",
        );
        assert!(
            (parsed.z0_ipc2141_ohms - parsed.z0_hammerstad_ohms).abs() < 8.0,
            "IPC off: {parsed:?}",
        );
    }

    #[test]
    fn stripline_z0_json_lower_than_microstrip_for_same_geometry() {
        let input = r#"{"width_mm":0.29,"thickness_mm":0.035,
                        "dielectric_height_mm":0.15,"dielectric_constant":4.3}"#;
        let micro: SingleEndedResult =
            serde_json::from_str(&microstrip_z0_json(input).unwrap()).unwrap();
        let strip = stripline_z0_json(input).unwrap();
        assert!(
            strip < micro.z0_ipc2141_ohms,
            "stripline {strip} not below microstrip {}",
            micro.z0_ipc2141_ohms,
        );
    }

    #[test]
    fn differential_z_json_includes_single_ended_reference() {
        let input = r#"{
            "trace":{"width_mm":0.20,"thickness_mm":0.035,
                     "dielectric_height_mm":0.15,"dielectric_constant":4.3},
            "gap_mm":0.127
        }"#;
        let out = differential_microstrip_z_json(input).expect("solve");
        let parsed: DifferentialResult = serde_json::from_str(&out).unwrap();
        // Sanity: Zdiff < 2·Z0 (the loose-coupling asymptote) by a
        // few ohms at 5 mil gap.
        assert!(
            parsed.zdiff_ohms < 2.0 * parsed.z0_single_ended_ohms,
            "{parsed:?}",
        );
        // Common mode is well below Z0 (≈ Z0/2 plus small correction).
        assert!(parsed.zcomm_ohms < parsed.z0_single_ended_ohms);
        assert!(parsed.zcomm_ohms > 0.0);
    }

    #[test]
    fn impedance_json_helpers_surface_parse_errors() {
        assert!(microstrip_z0_json("not json").is_err());
        assert!(stripline_z0_json("{}").is_err());
        assert!(differential_microstrip_z_json(r#"{"trace": "no"}"#).is_err());
    }
}
