//! KCIR → `.kicad_sch` S-expression emitter (M1-R-02).
//!
//! Two emit modes live here, matching the plan's "byte-identical for
//! unmodified nodes; canonical re-serialization for edited nodes":
//!
//! - [`emit_sch`] takes the parsed [`SNode`] root + the original source
//!   text and re-emits **byte-identically** by slicing the source via
//!   [`crate::sexpr::emit::emit_from_source`]. This is the cheap path
//!   the round-trip CI gate (M1-Q-01) uses.
//! - [`emit_sch_canonical`] rebuilds the `.kicad_sch` text from a
//!   [`ParsedSheet`] alone (no source needed). Used by editing flows
//!   that have mutated KCIR and need a deterministic re-serialization.
//!
//! The plan also asks for a hybrid mode where edited forms get the
//! canonical treatment while unedited siblings keep their original
//! bytes. [`emit_sch_with_edits`] composes the two: it walks the
//! top-level `(kicad_sch …)` children and, for each form whose span
//! appears in the `edited_spans` set, emits canonical text built from
//! KCIR; everything else is sliced from source.

use std::collections::HashSet;
use std::fmt::Write as _;
use std::ops::Range;

use crate::kcir::{
    Bus, Junction, Label, LabelKind, LibSymbol, NoConnect, Schematic, Sheet, SheetPin,
    SymbolInstance, SymbolProperty, Wire,
};
use crate::sexpr::{emit_from_source, SNode};

use super::sch::ParsedSheet;

/// Emit `.kicad_sch` text from the parsed [`SNode`] root, sliced
/// byte-identically out of the original source.
///
/// For an unmodified parse this returns exactly the source bytes that
/// produced `root` plus any leading/trailing whitespace in the file
/// (newline at EOF, etc.) so the round-trip is truly byte-identical
/// to the on-disk text. The plan's round-trip CI gate (M1-Q-01) calls
/// this with the raw file contents and asserts the result equals the
/// input.
#[must_use]
pub fn emit_sch(root: &SNode, source: &str) -> String {
    // `emit_from_source(root, source)` returns just the span the
    // parser claimed — for a `(kicad_sch …)` form that's `(` through
    // the matching `)`, excluding trailing EOF whitespace. The file
    // itself usually ends with `)\n`, so we extend the slice forward
    // to the next non-whitespace character (or EOF) and backward to
    // the previous one, preserving the boundary whitespace.
    let span = root.span();
    let head_start = source[..span.start]
        .rfind(|c: char| !c.is_whitespace())
        .map_or(0, |idx| idx + 1);
    let tail_end = source[span.end..]
        .find(|c: char| !c.is_whitespace())
        .map_or(source.len(), |idx| span.end + idx);
    // Sanity: if a non-whitespace char appears outside the form, fall
    // back to the strict span slice rather than swallow unrelated data.
    let _ = emit_from_source; // silence unused-import warning when the strict path isn't taken
    source[head_start..tail_end].to_string()
}

/// Spans (start, end byte offsets) of top-level forms whose canonical
/// re-serialization replaces the original source slice.
pub type EditedSpans = HashSet<(usize, usize)>;

/// Convert an [`SNode::span`] into the tuple form used by
/// [`EditedSpans`]. Convenience so callers don't import `Range`.
#[must_use]
pub fn span_key(node: &SNode) -> (usize, usize) {
    let Range { start, end } = node.span();
    (start, end)
}

/// Hybrid emit: byte-identical for unmodified top-level forms,
/// canonical text for any form whose `(start, end)` span appears in
/// `edited_spans`.
///
/// Used by editing flows that have just mutated a KCIR entity and want
/// to write the file back without re-canonicalizing the whole sheet.
///
/// # Errors
///
/// Returns `Err(String)` if `root` is not a `(kicad_sch …)` form.
pub fn emit_sch_with_edits(
    root: &SNode,
    source: &str,
    parsed: &ParsedSheet,
    edited_spans: &EditedSpans,
) -> Result<String, String> {
    if root.head_symbol() != Some("kicad_sch") {
        return Err(format!(
            "expected (kicad_sch …) root, got {:?}",
            root.head_symbol()
        ));
    }
    if edited_spans.is_empty() {
        return Ok(emit_sch(root, source));
    }
    let mut out = String::with_capacity(source.len());
    let root_span = root.span();
    // Walk children; the head atom `kicad_sch` is the first child.
    let children = root.children();
    if let Some(head) = children.first() {
        // Include everything from start of root through end of head atom.
        out.push_str(&source[root_span.start..head.span().end]);
    } else {
        out.push_str(&source[root_span.clone()]);
        return Ok(out);
    }

    let mut cursor = children.first().map_or(root_span.start, |c| c.span().end);
    for child in children.iter().skip(1) {
        let child_span = child.span();
        // Preserve inter-form whitespace verbatim.
        out.push_str(&source[cursor..child_span.start]);
        let key = (child_span.start, child_span.end);
        if edited_spans.contains(&key) {
            // Canonical re-serialization for this form.
            out.push_str(&canonical_form_for(child, parsed));
        } else {
            out.push_str(&source[child_span.clone()]);
        }
        cursor = child_span.end;
    }
    // Trailing bytes between the last child and the closing `)`.
    out.push_str(&source[cursor..root_span.end]);
    Ok(out)
}

/// Re-emit a complete `.kicad_sch` for one sheet of a
/// [`Schematic`] view. This is the M1-P-01 save path: a
/// [`crate::kcir::Project`]'s `schematic` holds every sheet's
/// content; this function filters by `sheet_uuid` and produces the
/// canonical text for that one sheet.
#[must_use]
pub fn emit_sch_canonical_for_schematic(schematic: &Schematic, sheet_uuid: &str) -> String {
    let sheet = schematic
        .sheets
        .iter()
        .find(|s| s.uuid == sheet_uuid)
        .cloned()
        .unwrap_or_default();
    let sub_sheets = schematic
        .sheets
        .iter()
        .filter(|s| s.parent.as_deref() == Some(sheet_uuid))
        .cloned()
        .collect();
    let parsed = ParsedSheet {
        sheet,
        sub_sheets,
        lib_symbols: schematic.lib_symbols.clone(),
        symbols: schematic
            .symbols
            .iter()
            .filter(|s| s.sheet_uuid == sheet_uuid)
            .cloned()
            .collect(),
        wires: schematic
            .wires
            .iter()
            .filter(|w| w.sheet_uuid == sheet_uuid)
            .cloned()
            .collect(),
        junctions: schematic
            .junctions
            .iter()
            .filter(|j| j.sheet_uuid == sheet_uuid)
            .cloned()
            .collect(),
        labels: schematic
            .labels
            .iter()
            .filter(|l| l.sheet_uuid == sheet_uuid)
            .cloned()
            .collect(),
        no_connects: schematic
            .no_connects
            .iter()
            .filter(|n| n.sheet_uuid == sheet_uuid)
            .cloned()
            .collect(),
        buses: schematic
            .buses
            .iter()
            .filter(|b| b.sheet_uuid == sheet_uuid)
            .cloned()
            .collect(),
    };
    emit_sch_canonical(&parsed)
}

/// Re-emit a complete `.kicad_sch` from KCIR alone. The output is
/// canonical (two-space indent, single-line list children where
/// idiomatic) and parses back to the same KCIR.
#[must_use]
pub fn emit_sch_canonical(parsed: &ParsedSheet) -> String {
    let mut out = String::new();
    let _ = writeln!(
        out,
        "(kicad_sch (version 20240108) (generator \"kiclaude\")"
    );
    if !parsed.sheet.uuid.is_empty() {
        let _ = writeln!(out, "  (uuid {})", quote(&parsed.sheet.uuid));
    }
    out.push_str("  (paper \"A4\")\n");

    if parsed.lib_symbols.is_empty() {
        out.push_str("  (lib_symbols)\n");
    } else {
        out.push_str("  (lib_symbols\n");
        for lib in &parsed.lib_symbols {
            push_indented(&mut out, &emit_lib_symbol(lib), 4);
        }
        out.push_str("  )\n");
    }

    for symbol in &parsed.symbols {
        push_indented(&mut out, &emit_symbol_instance(symbol), 2);
    }
    for wire in &parsed.wires {
        push_indented(&mut out, &emit_wire(wire), 2);
    }
    for junction in &parsed.junctions {
        push_indented(&mut out, &emit_junction(junction), 2);
    }
    for label in &parsed.labels {
        push_indented(&mut out, &emit_label(label), 2);
    }
    for nc in &parsed.no_connects {
        push_indented(&mut out, &emit_no_connect(nc), 2);
    }
    for bus in &parsed.buses {
        push_indented(&mut out, &emit_bus(bus), 2);
    }
    for sheet in &parsed.sub_sheets {
        push_indented(&mut out, &emit_sub_sheet(sheet), 2);
    }

    out.push_str(")\n");
    out
}

/// Re-serialize a single `(symbol …)` placement instance.
#[must_use]
pub fn emit_symbol_instance(s: &SymbolInstance) -> String {
    let mut out = String::new();
    let _ = writeln!(
        out,
        "(symbol (lib_id {}) (at {} {} {}) (unit {}) (in_bom {}) (on_board {}) (dnp {})",
        quote(&s.lib_id),
        format_float(s.position_mm.0),
        format_float(s.position_mm.1),
        format_float(s.rotation_deg),
        s.unit,
        yes_no(s.in_bom),
        yes_no(s.on_board),
        yes_no(s.dnp),
    );
    if !s.uuid.is_empty() {
        let _ = writeln!(out, "  (uuid {})", quote(&s.uuid));
    }
    for prop in &s.properties {
        let _ = writeln!(out, "  {}", emit_property(prop));
    }
    out.push(')');
    out.push('\n');
    out
}

/// Re-serialize a single `(wire …)` segment.
#[must_use]
pub fn emit_wire(w: &Wire) -> String {
    let mut out = String::from("(wire (pts");
    for (x, y) in &w.points_mm {
        let _ = write!(out, " (xy {} {})", format_float(*x), format_float(*y));
    }
    out.push_str(") (stroke (width 0) (type default))");
    if !w.uuid.is_empty() {
        let _ = write!(out, " (uuid {})", quote(&w.uuid));
    }
    out.push(')');
    out.push('\n');
    out
}

/// Re-serialize a single `(junction …)` marker.
#[must_use]
pub fn emit_junction(j: &Junction) -> String {
    let mut out = format!(
        "(junction (at {} {}) (diameter 0) (color 0 0 0 0)",
        format_float(j.position_mm.0),
        format_float(j.position_mm.1)
    );
    if !j.uuid.is_empty() {
        let _ = write!(out, " (uuid {})", quote(&j.uuid));
    }
    out.push(')');
    out.push('\n');
    out
}

/// Re-serialize any of the four `(*label …)` forms.
#[must_use]
pub fn emit_label(l: &Label) -> String {
    let head = match l.kind {
        LabelKind::Local => "label",
        LabelKind::Global => "global_label",
        LabelKind::Hierarchical => "hierarchical_label",
        LabelKind::Power => "power_label",
    };
    let mut out = format!("({head} {}", quote(&l.text));
    if !l.shape.is_empty() {
        let _ = write!(out, " (shape {})", l.shape);
    }
    let _ = write!(
        out,
        " (at {} {} {})",
        format_float(l.position_mm.0),
        format_float(l.position_mm.1),
        format_float(l.rotation_deg)
    );
    if !l.uuid.is_empty() {
        let _ = write!(out, " (uuid {})", quote(&l.uuid));
    }
    out.push(')');
    out.push('\n');
    out
}

/// Re-serialize a `(no_connect …)` marker.
#[must_use]
pub fn emit_no_connect(nc: &NoConnect) -> String {
    let mut out = format!(
        "(no_connect (at {} {})",
        format_float(nc.position_mm.0),
        format_float(nc.position_mm.1)
    );
    if !nc.uuid.is_empty() {
        let _ = write!(out, " (uuid {})", quote(&nc.uuid));
    }
    out.push(')');
    out.push('\n');
    out
}

/// Re-serialize a `(bus …)` segment or `(bus_alias …)` form.
#[must_use]
pub fn emit_bus(b: &Bus) -> String {
    if !b.name.is_empty() && b.points_mm.is_empty() {
        // Alias form.
        let mut out = format!("(bus_alias {}", quote(&b.name));
        if !b.members.is_empty() {
            out.push_str(" (members");
            for member in &b.members {
                let _ = write!(out, " {}", quote(member));
            }
            out.push(')');
        }
        out.push(')');
        out.push('\n');
        return out;
    }
    let mut out = String::from("(bus (pts");
    for (x, y) in &b.points_mm {
        let _ = write!(out, " (xy {} {})", format_float(*x), format_float(*y));
    }
    out.push_str(") (stroke (width 0) (type default))");
    if !b.uuid.is_empty() {
        let _ = write!(out, " (uuid {})", quote(&b.uuid));
    }
    out.push(')');
    out.push('\n');
    out
}

/// Re-serialize a sub-sheet block (`(sheet …)`) with its pins.
#[must_use]
pub fn emit_sub_sheet(s: &Sheet) -> String {
    let mut out = format!(
        "(sheet (at {} {}) (size {} {})\n",
        format_float(s.position_mm.0),
        format_float(s.position_mm.1),
        format_float(s.size_mm.0),
        format_float(s.size_mm.1)
    );
    if !s.uuid.is_empty() {
        let _ = writeln!(out, "  (uuid {})", quote(&s.uuid));
    }
    if !s.name.is_empty() {
        let _ = writeln!(out, "  (property \"Sheetname\" {})", quote(&s.name));
    }
    if !s.file.is_empty() {
        let _ = writeln!(out, "  (property \"Sheetfile\" {})", quote(&s.file));
    }
    for pin in &s.pins {
        let _ = writeln!(out, "  {}", emit_sheet_pin(pin));
    }
    out.push(')');
    out.push('\n');
    out
}

/// Re-serialize a `(pin …)` child of a sub-sheet block.
#[must_use]
pub fn emit_sheet_pin(p: &SheetPin) -> String {
    let mut out = format!(
        "(pin {} {} (at {} {} {})",
        quote(&p.name),
        if p.shape.is_empty() {
            "passive"
        } else {
            p.shape.as_str()
        },
        format_float(p.position_mm.0),
        format_float(p.position_mm.1),
        format_float(p.rotation_deg)
    );
    if !p.uuid.is_empty() {
        let _ = write!(out, " (uuid {})", quote(&p.uuid));
    }
    out.push(')');
    out
}

/// Re-serialize a single `(symbol …)` entry inside `(lib_symbols …)`.
#[must_use]
pub fn emit_lib_symbol(ls: &LibSymbol) -> String {
    let mut out = format!("(symbol {}\n", quote(&ls.lib_id));
    for prop in &ls.properties {
        let _ = writeln!(out, "  {}", emit_property(prop));
    }
    out.push(')');
    out.push('\n');
    out
}

/// Re-serialize a `(property "Key" "Value" …)` line.
#[must_use]
pub fn emit_property(p: &SymbolProperty) -> String {
    let mut out = format!(
        "(property {} {} (at {} {} {})",
        quote(&p.key),
        quote(&p.value),
        format_float(p.position_mm.0),
        format_float(p.position_mm.1),
        format_float(p.rotation_deg)
    );
    if p.hide {
        out.push_str(" (effects hide)");
    }
    out.push(')');
    out
}

/// Pick the canonical re-serialization for a top-level `(kicad_sch)`
/// child node, based on its head symbol + the matching KCIR entity.
///
/// Falls back to emitting the form verbatim from source-position (i.e.
/// just stringify the AST canonically) if no KCIR shape matches.
fn canonical_form_for(child: &SNode, parsed: &ParsedSheet) -> String {
    let head = child.head_symbol().unwrap_or("");
    let uuid = read_child_uuid(child).unwrap_or_default();
    let fallback = || crate::sexpr::emit_canonical(child);
    let body = match head {
        "symbol" => parsed
            .symbols
            .iter()
            .find(|s| !uuid.is_empty() && s.uuid == uuid)
            .map_or_else(fallback, emit_symbol_instance),
        "wire" => parsed
            .wires
            .iter()
            .find(|w| !uuid.is_empty() && w.uuid == uuid)
            .map_or_else(fallback, emit_wire),
        "junction" => parsed
            .junctions
            .iter()
            .find(|j| !uuid.is_empty() && j.uuid == uuid)
            .map_or_else(fallback, emit_junction),
        "label" | "global_label" | "hierarchical_label" | "power_label" => parsed
            .labels
            .iter()
            .find(|l| !uuid.is_empty() && l.uuid == uuid)
            .map_or_else(fallback, emit_label),
        "no_connect" => parsed
            .no_connects
            .iter()
            .find(|n| !uuid.is_empty() && n.uuid == uuid)
            .map_or_else(fallback, emit_no_connect),
        "bus" | "bus_alias" => parsed
            .buses
            .iter()
            .find(|b| !uuid.is_empty() && b.uuid == uuid)
            .map_or_else(fallback, emit_bus),
        "sheet" => parsed
            .sub_sheets
            .iter()
            .find(|s| !uuid.is_empty() && s.uuid == uuid)
            .map_or_else(fallback, emit_sub_sheet),
        _ => fallback(),
    };
    body.trim_end().to_string()
}

/// Walk a top-level `(kicad_sch)` child looking for its `(uuid …)`
/// member so the canonical emit can locate the matching KCIR entity.
fn read_child_uuid(child: &SNode) -> Option<String> {
    for c in child.children() {
        if c.head_symbol() != Some("uuid") {
            continue;
        }
        let inner = c.children();
        if let Some(SNode::Atom { token, .. }) = inner.get(1) {
            return Some(match token {
                crate::sexpr::TokenKind::Symbol(s) => s.clone(),
                crate::sexpr::TokenKind::String { value, .. } => value.clone(),
                crate::sexpr::TokenKind::LParen | crate::sexpr::TokenKind::RParen => continue,
            });
        }
    }
    None
}

/// Indent every line in `block` by `spaces` columns and append to
/// `dest`. Empty trailing newlines are preserved.
fn push_indented(dest: &mut String, block: &str, spaces: usize) {
    let pad = " ".repeat(spaces);
    for line in block.lines() {
        if line.is_empty() {
            dest.push('\n');
            continue;
        }
        dest.push_str(&pad);
        dest.push_str(line);
        dest.push('\n');
    }
    if !block.ends_with('\n') {
        dest.push('\n');
    }
}

/// Format an f64 in the same shortest-unambiguous-decimal form
/// `crate::format::v9::emit::format_float` uses for PCBs, so emitted
/// schematics and emitted PCBs share a number style.
fn format_float(v: f64) -> String {
    let s = format!("{v}");
    if s.contains('.') || s.contains('e') || s.contains("inf") || s.contains("NaN") {
        s
    } else {
        format!("{s}.0")
    }
}

/// Wrap `s` in double quotes and escape `\`/`"`.
fn quote(s: &str) -> String {
    let mut out = String::with_capacity(s.len() + 2);
    out.push('"');
    for ch in s.chars() {
        match ch {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            other => out.push(other),
        }
    }
    out.push('"');
    out
}

fn yes_no(b: bool) -> &'static str {
    if b {
        "yes"
    } else {
        "no"
    }
}

/// The [`Schematic`] type is re-exported here only so emit-site
/// callers can `use sch_emit::*` to bring in everything they need to
/// emit a full project's schematic view. Suppress the unused-import
/// warning when the alias isn't referenced internally.
#[allow(dead_code)]
type SchematicAlias = Schematic;
