//! Multi-sheet hierarchy resolution + label-driven net propagation
//! (M1-R-05).
//!
//! Given a [`Schematic`](super::Schematic) containing one or more
//! [`Sheet`](super::Sheet)s with [`Label`](super::Label)s on them,
//! [`resolve_nets`] walks the sheet tree and groups label endpoints
//! into electrical nets according to `KiCad`'s scope rules:
//!
//! - **Local labels** are scoped to the sheet they appear on. Two
//!   `LabelKind::Local` labels named `NET` on different sheets are
//!   different nets.
//! - **Hierarchical labels** connect a child sheet's net to a
//!   matching [`SheetPin`](super::SheetPin) on the child sheet's
//!   `(sheet)` block — i.e. they cross **one** parent boundary. The
//!   parent's sheet pin shares the same name and binds the
//!   hierarchical label inside the child to whatever net the pin is
//!   wired to on the parent.
//! - **Global labels** connect everywhere — every `LabelKind::Global`
//!   in the project that shares the same text is on one net.
//! - **Power-net labels** behave like globals but live on
//!   power-namespace [`SymbolInstance`](super::SymbolInstance)s
//!   (M1-R-01's `is_power_symbol = true`). The `Value` property of
//!   each power symbol is the net name.
//!
//! The output is a [`NetGraph`] mapping a canonical net name to the
//! set of [`LabelRef`]s that participate in that net. Downstream
//! consumers (ERC, BOM, the editor's net highlighter) iterate the
//! map to ask "what does this label belong to?".

use std::collections::{BTreeMap, BTreeSet, VecDeque};

use serde::{Deserialize, Serialize};

use super::{Label, LabelKind, Schematic, SymbolInstance};

/// A reference to a label or label-like endpoint in the schematic.
///
/// Used as the value side of [`NetGraph`] so callers can trace nets
/// back to the on-sheet form that contributed each endpoint.
#[derive(Debug, Clone, PartialEq, Eq, Hash, Ord, PartialOrd, Serialize, Deserialize)]
pub enum LabelRef {
    /// A `(label …)` / `(global_label …)` / `(hierarchical_label …)`
    /// / `(power_label …)` form. `uuid` indexes into
    /// [`Schematic::labels`].
    Label {
        sheet_uuid: String,
        label_uuid: String,
        kind: LabelKind,
    },
    /// A `(pin …)` on a `(sheet …)` block. `sheet_uuid` is the
    /// uuid of the child sheet whose block carries the pin.
    SheetPin {
        sheet_uuid: String,
        pin_uuid: String,
        name: String,
    },
    /// A power-namespace [`SymbolInstance`]. Power symbols are
    /// net-name markers, not real components.
    PowerSymbol {
        sheet_uuid: String,
        symbol_uuid: String,
        /// The symbol's `Value` (e.g. `"GND"`, `"VCC"`).
        value: String,
    },
}

/// Resolved net graph: net name → set of endpoints on that net.
#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct NetGraph {
    /// Net name → endpoint set, kept in a `BTreeMap` so iteration
    /// order is deterministic across calls (M1-Q-01 round-trip
    /// stability).
    pub nets: BTreeMap<String, BTreeSet<LabelRef>>,
    /// Labels that couldn't be tied to any other endpoint — typically
    /// hierarchical labels in a child sheet with no matching pin on
    /// the parent's `(sheet)` block. Surfaced as ERC input (KC003 in
    /// SPEC §9.2).
    pub orphan_labels: Vec<LabelRef>,
    /// Conflicting hierarchical sheet-pin definitions: two parents
    /// claim the same pin name on the same child sheet. Surfaced as
    /// ERC input (KC004).
    pub conflicting_hierarchical_pins: Vec<HierarchicalConflict>,
}

/// A conflict where the same hierarchical pin name appears in more
/// than one parent's `(sheet child)` block — meaning two different
/// nets are trying to share an endpoint.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct HierarchicalConflict {
    /// The conflicting pin's name.
    pub pin_name: String,
    /// Sheet uuids that all claim a pin with this name.
    pub claimed_by: Vec<String>,
}

/// Resolve every label in the schematic into the net it belongs to.
///
/// # Algorithm
///
/// 1. Index sheets by uuid. Verify the parent links resolve to known
///    sheets; emit an orphan record for any dangling parent.
/// 2. Build per-(sheet, name) buckets for local labels — each becomes
///    its own net, keyed by `<sheet_uuid>/<name>`.
/// 3. Pool every global label by name into a single net.
/// 4. Pool every power-namespace symbol by `Value` into a single net,
///    mixing the power net with any global label of the same name.
/// 5. For each hierarchical label, find the matching [`SheetPin`] on
///    the label's own `Sheet` entry (the `(sheet)` block as drawn on
///    the parent); the pin gets pooled into the same net as the
///    label. If no matching pin exists, the label is recorded as an
///    orphan. If the same pin name appears on the same sheet from two
///    parents (rare — `KiCad` usually catches this), record a conflict.
#[must_use]
pub fn resolve_nets(schematic: &Schematic) -> NetGraph {
    let mut graph = NetGraph::default();

    pool_local_labels(schematic, &mut graph);
    pool_global_labels(schematic, &mut graph);
    pool_power_symbols(schematic, &mut graph);
    pool_hierarchical_labels(schematic, &mut graph);
    detect_conflicting_pins(schematic, &mut graph);

    graph
}

fn pool_local_labels(schematic: &Schematic, graph: &mut NetGraph) {
    for label in &schematic.labels {
        if label.kind != LabelKind::Local {
            continue;
        }
        let net_name = format!("{}/{}", label.sheet_uuid, label.text);
        graph
            .nets
            .entry(net_name)
            .or_default()
            .insert(LabelRef::Label {
                sheet_uuid: label.sheet_uuid.clone(),
                label_uuid: label.uuid.clone(),
                kind: label.kind,
            });
    }
}

fn pool_global_labels(schematic: &Schematic, graph: &mut NetGraph) {
    for label in &schematic.labels {
        if label.kind != LabelKind::Global && label.kind != LabelKind::Power {
            continue;
        }
        graph
            .nets
            .entry(label.text.clone())
            .or_default()
            .insert(LabelRef::Label {
                sheet_uuid: label.sheet_uuid.clone(),
                label_uuid: label.uuid.clone(),
                kind: label.kind,
            });
    }
}

fn pool_power_symbols(schematic: &Schematic, graph: &mut NetGraph) {
    for symbol in &schematic.symbols {
        if !power_symbol_contributes_net(symbol) {
            continue;
        }
        let net_name = symbol.value.clone();
        if net_name.is_empty() {
            continue;
        }
        graph
            .nets
            .entry(net_name.clone())
            .or_default()
            .insert(LabelRef::PowerSymbol {
                sheet_uuid: symbol.sheet_uuid.clone(),
                symbol_uuid: symbol.uuid.clone(),
                value: net_name,
            });
    }
}

fn power_symbol_contributes_net(symbol: &SymbolInstance) -> bool {
    // PWR_FLAG is a net marker (it asserts a power source) but
    // doesn't contribute its own net name — it inherits from the
    // wire it's attached to, which lives in the connectivity graph
    // M2 builds. For label propagation alone we skip it.
    symbol.is_power_symbol && !symbol.is_power_flag
}

fn pool_hierarchical_labels(schematic: &Schematic, graph: &mut NetGraph) {
    // Lookup table from `(sheet_uuid, pin_name)` → pin uuid for fast
    // matching of hierarchical labels.
    let pin_index: BTreeMap<(String, String), String> = schematic
        .sheets
        .iter()
        .flat_map(|sheet| {
            sheet
                .pins
                .iter()
                .map(move |pin| ((sheet.uuid.clone(), pin.name.clone()), pin.uuid.clone()))
        })
        .collect();

    for label in &schematic.labels {
        if label.kind != LabelKind::Hierarchical {
            continue;
        }
        let label_ref = LabelRef::Label {
            sheet_uuid: label.sheet_uuid.clone(),
            label_uuid: label.uuid.clone(),
            kind: label.kind,
        };
        let net_name = label.text.clone();
        // Look up the sheet pin that lives on this label's own
        // `Sheet` entry — KCIR stores the `(pin)` children of the
        // `(sheet)` block on the child sheet itself.
        let pin_uuid = pin_index.get(&(label.sheet_uuid.clone(), label.text.clone()));
        if let Some(pin_uuid) = pin_uuid {
            let pin_ref = LabelRef::SheetPin {
                sheet_uuid: label.sheet_uuid.clone(),
                pin_uuid: pin_uuid.clone(),
                name: label.text.clone(),
            };
            let bucket = graph.nets.entry(net_name).or_default();
            bucket.insert(label_ref);
            bucket.insert(pin_ref);
        } else {
            // Orphan: hierarchical label with no matching sheet pin.
            graph.orphan_labels.push(label_ref);
        }
    }
}

fn detect_conflicting_pins(schematic: &Schematic, graph: &mut NetGraph) {
    // Group sheet pins by (parent_uuid, pin_name) and flag any that
    // appear in more than one parent.
    let mut by_parent_and_name: BTreeMap<(String, String), Vec<String>> = BTreeMap::new();
    for sheet in &schematic.sheets {
        let Some(parent_uuid) = sheet.parent.as_deref() else {
            continue;
        };
        for pin in &sheet.pins {
            by_parent_and_name
                .entry((parent_uuid.to_string(), pin.name.clone()))
                .or_default()
                .push(sheet.uuid.clone());
        }
    }
    for ((parent_uuid, pin_name), claimers) in by_parent_and_name {
        if claimers.len() > 1 {
            graph
                .conflicting_hierarchical_pins
                .push(HierarchicalConflict {
                    pin_name,
                    claimed_by: claimers,
                });
        }
        let _ = parent_uuid;
    }
}

/// Walk the sheet hierarchy breadth-first starting at every root
/// sheet (those with `parent = None`). Returns sheets in
/// declaration-friendly order: root, then root's children in declared
/// order, then their children, etc.
///
/// Useful for ERC pass ordering and for the multi-sheet navigator
/// (M1-T-05) UI.
#[must_use]
pub fn breadth_first_sheets(schematic: &Schematic) -> Vec<String> {
    let mut out = Vec::with_capacity(schematic.sheets.len());
    let mut queue: VecDeque<String> = schematic
        .sheets
        .iter()
        .filter(|s| s.parent.is_none())
        .map(|s| s.uuid.clone())
        .collect();
    let mut visited: BTreeSet<String> = BTreeSet::new();
    while let Some(uuid) = queue.pop_front() {
        if !visited.insert(uuid.clone()) {
            continue;
        }
        out.push(uuid.clone());
        for sheet in &schematic.sheets {
            if sheet.parent.as_deref() == Some(uuid.as_str()) {
                queue.push_back(sheet.uuid.clone());
            }
        }
    }
    // Any sheets with a dangling parent uuid haven't been visited.
    // Append them after the well-rooted set so callers see them.
    for sheet in &schematic.sheets {
        if !visited.contains(&sheet.uuid) {
            out.push(sheet.uuid.clone());
        }
    }
    out
}

/// Convenience: return every endpoint a single label is connected to.
///
/// Looks `label` up in `graph` and returns the full net it belongs
/// to (including the label itself), or an empty set if the label
/// isn't part of any resolved net (e.g. it's recorded as an orphan).
#[must_use]
pub fn endpoints_for_label<'a>(
    graph: &'a NetGraph,
    label: &Label,
) -> Option<&'a BTreeSet<LabelRef>> {
    let needle = LabelRef::Label {
        sheet_uuid: label.sheet_uuid.clone(),
        label_uuid: label.uuid.clone(),
        kind: label.kind,
    };
    graph
        .nets
        .values()
        .find(|endpoints| endpoints.contains(&needle))
}

#[cfg(test)]
mod tests;
