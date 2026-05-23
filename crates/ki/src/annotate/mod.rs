//! Reference designator annotation (M1-R-06).
//!
//! Implements `KiCad`'s "Annotate Schematic" default behavior: walk
//! every [`SymbolInstance`](crate::kcir::SymbolInstance) in
//! declaration order across the sheet hierarchy, group by reference
//! prefix (`R`, `C`, `U`, …), and assign `<prefix><N>` numbers
//! starting at 1 within each prefix. Symbols whose refdes already
//! has a numeric tail keep that number unless [`AnnotateOptions::reset`]
//! is set.
//!
//! Power-namespace symbols (`is_power_symbol = true`) get auto-named
//! `#PWR<NNN>` and `PWR_FLAG` instances get `#FLG<NNN>`, matching
//! `KiCad`'s special-case behavior. These pools are kept separate from
//! the regular component refdes pool so a fresh annotation of a
//! schematic with `R1, R2, #PWR01, #PWR02` does not collide.

use std::collections::BTreeMap;

use serde::{Deserialize, Serialize};

use crate::kcir::{Schematic, SymbolInstance};

/// Options controlling the annotation pass.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub struct AnnotateOptions {
    /// If true, every symbol gets a fresh number regardless of its
    /// existing refdes. If false, symbols with an existing
    /// `<prefix><N>` form keep their N and only un-annotated
    /// (`<prefix>?` / `<prefix>`) symbols receive numbers. Default
    /// mirrors `KiCad`'s "Re-annotate all" toggle.
    pub reset: bool,
    /// When `reset` is false, refdes numbers start from this value
    /// for newly annotated symbols (relative to the largest existing
    /// number in each prefix bucket). Default `1`.
    pub start_at: u32,
}

impl Default for AnnotateOptions {
    fn default() -> Self {
        Self {
            reset: false,
            start_at: 1,
        }
    }
}

/// Summary of an annotation pass.
#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct AnnotateReport {
    /// Number of symbols whose refdes changed.
    pub renamed: u32,
    /// Number of symbols that already had a final refdes and were
    /// left untouched.
    pub kept: u32,
}

/// Annotate every symbol in `schematic` in place.
///
/// # Algorithm
///
/// 1. Group symbols by their reference prefix (everything up to the
///    first digit).
/// 2. Within each group:
///    - If `opts.reset` is true, blow away every existing number and
///      assign `1, 2, 3, …` in declaration order.
///    - Otherwise: collect every numbered refdes, keep them, and
///      fill un-annotated symbols (those whose refdes ends in `?`,
///      is empty, or is bare-prefix) with the next free number.
/// 3. Power symbols and `PWR_FLAG` instances are handled in their own
///    pools (`#PWR<NNN>` / `#FLG<NNN>`).
///
/// Power-flag instances always receive `#FLG<NNN>` regardless of
/// their incoming refdes.
pub fn annotate(schematic: &mut Schematic, opts: AnnotateOptions) -> AnnotateReport {
    let mut report = AnnotateReport::default();
    // Snapshot existing refdes assignments to preserve them when
    // `reset` is false.
    let mut next_by_prefix: BTreeMap<String, u32> = BTreeMap::new();

    if !opts.reset {
        for s in &schematic.symbols {
            let (prefix, num) = split_prefix_number(&effective_prefix(s));
            if let Some(n) = num {
                let entry = next_by_prefix.entry(prefix).or_insert(0);
                *entry = (*entry).max(n + 1);
            }
        }
        // Bump every prefix's cursor at least to `opts.start_at`.
        for v in next_by_prefix.values_mut() {
            if *v < opts.start_at {
                *v = opts.start_at;
            }
        }
    }

    for s in &mut schematic.symbols {
        let prefix = annotation_prefix(s);
        let new_refdes = if opts.reset {
            let cursor = next_by_prefix
                .entry(prefix.clone())
                .or_insert(opts.start_at);
            let n = *cursor;
            *cursor += 1;
            format!("{prefix}{n}")
        } else if has_final_number(&s.refdes) {
            // Already final — keep it.
            report.kept += 1;
            continue;
        } else {
            let cursor = next_by_prefix
                .entry(prefix.clone())
                .or_insert(opts.start_at);
            let n = *cursor;
            *cursor += 1;
            format!("{prefix}{n}")
        };
        if new_refdes == s.refdes {
            report.kept += 1;
        } else {
            s.refdes.clone_from(&new_refdes);
            report.renamed += 1;
            // Mirror the refdes change into the `Reference` property
            // so the emitter writes it back consistently.
            for prop in &mut s.properties {
                if prop.key == "Reference" {
                    prop.value.clone_from(&new_refdes);
                }
            }
        }
    }

    report
}

/// The annotation prefix this symbol belongs to.
fn annotation_prefix(s: &SymbolInstance) -> String {
    if s.is_power_flag {
        return "#FLG".to_string();
    }
    if s.is_power_symbol {
        return "#PWR".to_string();
    }
    let candidate = effective_prefix(s);
    let prefix = split_prefix_number(&candidate).0;
    if prefix.is_empty() {
        "U".to_string()
    } else {
        prefix
    }
}

/// The prefix portion of a refdes-ish string. Falls back to the
/// symbol's `Reference` property when the instance's `refdes` field
/// is empty (pre-annotation).
fn effective_prefix(s: &SymbolInstance) -> String {
    if !s.refdes.is_empty() {
        return s.refdes.clone();
    }
    for prop in &s.properties {
        if prop.key == "Reference" && !prop.value.is_empty() {
            return prop.value.clone();
        }
    }
    String::new()
}

/// Split `"R12"` into (`"R"`, `Some(12)`), `"R?"` / `"R"` / `""` into
/// (`"R"`, `None`). A trailing `?` placeholder is consumed into the
/// prefix-or-tail boundary so the returned `prefix` is always the
/// bare letter run.
fn split_prefix_number(refdes: &str) -> (String, Option<u32>) {
    let mut prefix_end = refdes.len();
    for (i, c) in refdes.char_indices() {
        if c.is_ascii_digit() || c == '?' {
            prefix_end = i;
            break;
        }
    }
    let prefix: String = refdes[..prefix_end].chars().collect();
    let tail = &refdes[prefix_end..];
    let num = if tail.is_empty() || tail == "?" {
        None
    } else {
        tail.parse::<u32>().ok()
    };
    (prefix, num)
}

/// True if the refdes ends in a digit run (i.e. it's already fully
/// annotated).
fn has_final_number(refdes: &str) -> bool {
    let (_, num) = split_prefix_number(refdes);
    num.is_some()
}

#[cfg(test)]
mod tests;
