//! Length-match analyzer + tuning queue — M3-R-05.
//!
//! Reads a [`Pcb`](kiclaude_ki::kcir::Pcb)'s declared
//! [`LengthGroup`](kiclaude_ki::kcir::LengthGroup) collection,
//! computes the actual routed length of every net that belongs to
//! each group, and produces a **tuning queue** — a per-net list of
//! deltas vs the group's target with concrete serpentine-segment
//! suggestions.
//!
//! ## Length model
//!
//! Per-net length = sum of every track segment's Euclidean length
//! over `points_mm[0..i+1]`. Vias add a negligible (~mm) electrical
//! length we treat as zero — the M3 use case is matching to a few
//! tens of microns on tens-of-mm runs, so via length is below the
//! tolerance floor.
//!
//! ## Target resolution
//!
//! - `target_length_mm > 0` → use it directly.
//! - `target_length_mm == 0` → "match the longest net" — the
//!   analyzer picks the max observed length as the implicit target.
//!
//! ## Tuning suggestion
//!
//! When a net is shorter than target by `Δ`, the analyzer proposes
//! adding `N` serpentine segments, each contributing `2 · (Δ / N)`
//! extra length (out-and-back pair). `N` is chosen so each segment
//! adds ≤ `MAX_SEGMENT_GAIN_MM` (default 5 mm), giving the placer
//! room to lay each loop without violating clearance against the
//! pre-routing density.
//!
//! When a net is *longer* than target, the analyzer flags it as
//! `TooLong` with no auto-fix — shortening is a re-route, not a
//! tuning step.

#![allow(clippy::cast_precision_loss)]

use serde::{Deserialize, Serialize};

use kiclaude_ki::kcir::{LengthGroup, Pcb, Track};

/// Maximum length contribution per serpentine segment (mm). The
/// analyzer picks a serpentine count so no single loop adds more
/// than this. 5 mm leaves enough open routing channel on a typical
/// 4-layer board to add the loop without DRC violation.
pub const MAX_SEGMENT_GAIN_MM: f64 = 5.0;

/// One member of a length-match group's report.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct LengthMatchMember {
    /// Net name (as declared in the group).
    pub net: String,
    /// Current routed length in mm — `0.0` if the net has no
    /// tracks (silently unrouted nets surface here so the editor
    /// can flag them).
    pub current_length_mm: f64,
    /// `current_length_mm - target_length_mm`. Negative = too
    /// short; positive = too long; near zero = matched.
    pub delta_mm: f64,
    /// Bucket the editor renders the row in.
    pub status: LengthMatchStatus,
    /// Suggested serpentine-segment count, when the net is
    /// `TooShort`. Zero for any other status.
    pub suggested_serpentine_count: u32,
    /// Per-suggested-segment length gain (`2 · (Δ / N)`). Zero
    /// unless `suggested_serpentine_count > 0`.
    pub suggested_segment_gain_mm: f64,
}

/// One length-match group's report — emitted by [`analyze`].
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct LengthMatchReport {
    /// Group name (as declared in `Pcb.length_groups`).
    pub name: String,
    /// Effective target after resolving "match the longest" (0 →
    /// observed max).
    pub target_length_mm: f64,
    /// Tolerance copied from the declaration. Nets within `±tol`
    /// of the target are flagged `InRange`.
    pub tolerance_mm: f64,
    /// Per-net member rows.
    pub members: Vec<LengthMatchMember>,
}

/// Bucket the analyzer assigns each member based on `|delta_mm|`
/// vs the group's tolerance.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum LengthMatchStatus {
    /// `|delta| ≤ tolerance` — net is matched.
    InRange,
    /// `delta < -tolerance` — net is shorter than target.
    /// The analyzer emits a tuning suggestion.
    TooShort,
    /// `delta > +tolerance` — net is longer than target. No
    /// auto-fix; the user must re-route.
    TooLong,
    /// The net is declared in the group but has no tracks on the
    /// board. Editor should highlight as missing-route.
    Unrouted,
}

/// Run the analyzer against `pcb` and return one report per group.
#[must_use]
pub fn analyze(pcb: &Pcb) -> Vec<LengthMatchReport> {
    pcb.length_groups
        .iter()
        .map(|group| analyze_group(group, &pcb.tracks))
        .collect()
}

fn analyze_group(group: &LengthGroup, tracks: &[Track]) -> LengthMatchReport {
    let mut lengths: Vec<(String, f64)> = group
        .nets
        .iter()
        .map(|net| (net.clone(), net_length_mm(net, tracks)))
        .collect();

    // Resolve `target_length_mm == 0` → match-the-longest by picking
    // the max observed routed length. If every net is unrouted the
    // implicit target is 0 (everything reads as `Unrouted`).
    let effective_target = if group.target_length_mm > 0.0 {
        group.target_length_mm
    } else {
        lengths.iter().map(|(_, l)| *l).fold(0.0_f64, f64::max)
    };
    let tolerance = group.tolerance_mm.max(0.0);

    let members: Vec<LengthMatchMember> = lengths
        .drain(..)
        .map(|(net, current)| {
            let (status, delta, suggested_n, gain) = if current == 0.0 {
                (LengthMatchStatus::Unrouted, -effective_target, 0, 0.0)
            } else {
                let delta = current - effective_target;
                if delta.abs() <= tolerance {
                    (LengthMatchStatus::InRange, delta, 0, 0.0)
                } else if delta < 0.0 {
                    let shortfall = -delta;
                    // Pick the smallest segment count that keeps
                    // each loop at or below MAX_SEGMENT_GAIN_MM.
                    // `shortfall` is positive (we just took its abs)
                    // and clamped to ≤ 1e6 to guard against
                    // pathological inputs before the `as u32` cast.
                    let raw = (shortfall / MAX_SEGMENT_GAIN_MM).ceil().max(1.0);
                    #[allow(clippy::cast_possible_truncation, clippy::cast_sign_loss)]
                    let n = raw.min(1_000_000.0) as u32;
                    let per_segment_gain = shortfall / f64::from(n);
                    (LengthMatchStatus::TooShort, delta, n, per_segment_gain)
                } else {
                    (LengthMatchStatus::TooLong, delta, 0, 0.0)
                }
            };
            LengthMatchMember {
                net,
                current_length_mm: current,
                delta_mm: delta,
                status,
                suggested_serpentine_count: suggested_n,
                suggested_segment_gain_mm: gain,
            }
        })
        .collect();

    LengthMatchReport {
        name: group.name.clone(),
        target_length_mm: effective_target,
        tolerance_mm: tolerance,
        members,
    }
}

/// Sum of segment lengths for every track on `net`. Vias are
/// excluded — see the module docstring.
#[must_use]
pub fn net_length_mm(net: &str, tracks: &[Track]) -> f64 {
    let mut total = 0.0_f64;
    for track in tracks {
        if track.net != net {
            continue;
        }
        for window in track.points_mm.windows(2) {
            let (x0, y0) = window[0];
            let (x1, y1) = window[1];
            let dx = x1 - x0;
            let dy = y1 - y0;
            total += (dx * dx + dy * dy).sqrt();
        }
    }
    total
}

#[cfg(test)]
mod tests {
    use super::*;
    use kiclaude_ki::kcir::LayerRef;

    fn track(net: &str, pts: &[(f64, f64)]) -> Track {
        Track {
            uuid: format!("t-{net}-{}", pts.len()),
            layer: LayerRef("F.Cu".to_string()),
            net: net.to_string(),
            points_mm: pts.to_vec(),
            width_mm: 0.2,
            locked: false,
        }
    }

    #[test]
    fn net_length_sums_segment_distances() {
        let tracks = vec![
            track("D0", &[(0.0, 0.0), (10.0, 0.0)]),
            track("D0", &[(10.0, 0.0), (10.0, 5.0)]),
        ];
        let l = net_length_mm("D0", &tracks);
        assert!((l - 15.0).abs() < 1e-9, "got {l}");
    }

    #[test]
    fn unrouted_net_returns_zero_length() {
        let tracks = vec![track("D0", &[(0.0, 0.0), (10.0, 0.0)])];
        let l = net_length_mm("D1", &tracks);
        assert!(l == 0.0);
    }

    #[test]
    fn group_with_explicit_target_buckets_each_member() {
        let mut pcb = Pcb::default();
        pcb.length_groups.push(LengthGroup {
            name: "RGMII_TX".to_string(),
            nets: vec![
                "TX0".to_string(),
                "TX1".to_string(),
                "TX2".to_string(),
                "MISSING".to_string(),
            ],
            target_length_mm: 50.0,
            tolerance_mm: 0.5,
        });
        // TX0: 49.8 mm (within ±0.5 mm) → InRange
        pcb.tracks.push(track("TX0", &[(0.0, 0.0), (49.8, 0.0)]));
        // TX1: 45.0 mm (5 mm short) → TooShort, expect 1 serpentine (gain 5 mm)
        pcb.tracks.push(track("TX1", &[(0.0, 1.0), (45.0, 1.0)]));
        // TX2: 56.0 mm (6 mm long) → TooLong
        pcb.tracks.push(track("TX2", &[(0.0, 2.0), (56.0, 2.0)]));
        // MISSING: no tracks → Unrouted

        let reports = analyze(&pcb);
        assert_eq!(reports.len(), 1);
        let r = &reports[0];
        assert_eq!(r.name, "RGMII_TX");
        assert!((r.target_length_mm - 50.0).abs() < 1e-9);
        assert_eq!(r.members.len(), 4);

        let by_net = |name: &str| r.members.iter().find(|m| m.net == name).unwrap();
        assert_eq!(by_net("TX0").status, LengthMatchStatus::InRange);
        let tx1 = by_net("TX1");
        assert_eq!(tx1.status, LengthMatchStatus::TooShort);
        assert!((tx1.delta_mm - -5.0).abs() < 1e-9);
        // 5 mm shortfall / 5 mm-per-segment cap → 1 segment.
        assert_eq!(tx1.suggested_serpentine_count, 1);
        assert!((tx1.suggested_segment_gain_mm - 5.0).abs() < 1e-9);
        assert_eq!(by_net("TX2").status, LengthMatchStatus::TooLong);
        // TooLong gets no tuning suggestion — the user must re-route.
        assert_eq!(by_net("TX2").suggested_serpentine_count, 0);
        let missing = by_net("MISSING");
        assert_eq!(missing.status, LengthMatchStatus::Unrouted);
        assert!(missing.current_length_mm == 0.0);
    }

    #[test]
    fn target_zero_picks_longest_observed() {
        let mut pcb = Pcb::default();
        pcb.length_groups.push(LengthGroup {
            name: "DDR_BYTE0".to_string(),
            nets: vec!["DQ0".to_string(), "DQ1".to_string(), "DQ2".to_string()],
            target_length_mm: 0.0, // → match-the-longest
            tolerance_mm: 0.127,
        });
        pcb.tracks.push(track("DQ0", &[(0.0, 0.0), (30.0, 0.0)]));
        pcb.tracks.push(track("DQ1", &[(0.0, 1.0), (32.0, 1.0)])); // longest
        pcb.tracks.push(track("DQ2", &[(0.0, 2.0), (28.0, 2.0)]));

        let reports = analyze(&pcb);
        let r = &reports[0];
        assert!((r.target_length_mm - 32.0).abs() < 1e-9);
        let by_net = |name: &str| r.members.iter().find(|m| m.net == name).unwrap();
        assert_eq!(by_net("DQ1").status, LengthMatchStatus::InRange);
        assert_eq!(by_net("DQ0").status, LengthMatchStatus::TooShort);
        assert_eq!(by_net("DQ2").status, LengthMatchStatus::TooShort);
    }

    #[test]
    fn large_shortfall_splits_into_multiple_serpentines() {
        let mut pcb = Pcb::default();
        pcb.length_groups.push(LengthGroup {
            name: "PCIe_TX".to_string(),
            nets: vec!["TX_P".to_string()],
            target_length_mm: 100.0,
            tolerance_mm: 0.1,
        });
        // 25 mm shortfall — at 5 mm per loop cap, 5 serpentines.
        pcb.tracks.push(track("TX_P", &[(0.0, 0.0), (75.0, 0.0)]));

        let reports = analyze(&pcb);
        let m = &reports[0].members[0];
        assert_eq!(m.status, LengthMatchStatus::TooShort);
        assert_eq!(m.suggested_serpentine_count, 5);
        assert!((m.suggested_segment_gain_mm - 5.0).abs() < 1e-9);
    }

    #[test]
    fn empty_groups_produces_empty_reports() {
        let pcb = Pcb::default();
        assert_eq!(analyze(&pcb), Vec::new());
    }
}
