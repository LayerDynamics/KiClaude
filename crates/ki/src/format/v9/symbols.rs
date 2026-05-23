//! Library-symbol / property / sheet-pin parsing helpers used by
//! [`super::sch`] (the `.kicad_sch` mapper).
//!
//! All functions here take a parsed [`SNode`] subtree and return KCIR
//! types or convenience tuples. They assume the subtree's head has
//! already been matched by the caller.

use super::sexpr_helpers::{atom_f64, atom_str, body_children, find_child, find_children};
use crate::kcir::{LibSymbol, SheetPin, SymbolProperty};
use crate::sexpr::SNode;

/// Parse a single `(property "Key" "Value" (at x y rot) (effects …))`
/// form into a [`SymbolProperty`]. Missing optional fields default to
/// zero / `false`.
#[must_use]
pub fn parse_property(node: &SNode) -> SymbolProperty {
    let body: Vec<&SNode> = body_children(node).collect();
    let key = body
        .first()
        .and_then(|n| atom_str(n))
        .unwrap_or("")
        .to_string();
    let value = body
        .get(1)
        .and_then(|n| atom_str(n))
        .unwrap_or("")
        .to_string();
    let (x, y, rot) = read_at(node);
    let hide = read_hide_effect(node);
    SymbolProperty {
        key,
        value,
        position_mm: (x, y),
        rotation_deg: rot,
        hide,
    }
}

/// Parse a `(pin "name" shape (at x y rot) (effects …) (uuid …))`
/// child of a `(sheet …)` block.
#[must_use]
pub fn parse_sheet_pin(node: &SNode) -> SheetPin {
    let body: Vec<&SNode> = body_children(node).collect();
    let name = body
        .first()
        .and_then(|n| atom_str(n))
        .unwrap_or("")
        .to_string();
    let shape = body
        .get(1)
        .and_then(|n| atom_str(n))
        .unwrap_or("passive")
        .to_string();
    let (x, y, rot) = read_at(node);
    let uuid = find_child(node, "uuid")
        .and_then(|n| body_children(n).next())
        .and_then(atom_str)
        .unwrap_or("")
        .to_string();
    SheetPin {
        uuid,
        name,
        shape,
        position_mm: (x, y),
        rotation_deg: rot,
    }
}

/// Parse a `(symbol "LIB:NAME" …)` entry inside `(lib_symbols …)`.
///
/// Library symbols can contain nested `(symbol "name_0_1" …)` units —
/// we keep only the top-level identification + properties for M1-R-01.
/// Pin geometry round-trips through the raw S-expression layer when
/// the emitter lands in M1-R-02.
#[must_use]
pub fn parse_lib_symbol(node: &SNode) -> LibSymbol {
    let lib_id = body_children(node)
        .next()
        .and_then(|n| atom_str(n))
        .unwrap_or("")
        .to_string();
    let properties = find_children(node, "property")
        .iter()
        .map(|p| parse_property(p))
        .collect();
    let is_power = lib_id.starts_with("power:");
    LibSymbol {
        lib_id,
        properties,
        is_power,
    }
}

/// Read an `(at x y [rot])` child of `parent`. Returns `(0, 0, 0)` if
/// the `(at …)` form is absent.
#[must_use]
pub fn read_at(parent: &SNode) -> (f64, f64, f64) {
    find_child(parent, "at").map_or((0.0, 0.0, 0.0), |n| {
        let body: Vec<&SNode> = body_children(n).collect();
        let x = body.first().and_then(|n| atom_f64(n)).unwrap_or(0.0);
        let y = body.get(1).and_then(|n| atom_f64(n)).unwrap_or(0.0);
        let rot = body.get(2).and_then(|n| atom_f64(n)).unwrap_or(0.0);
        (x, y, rot)
    })
}

/// True if any `(effects …)` child of `parent` carries the `hide` flag.
///
/// `KiCad` emits `(effects (font …) hide)` for hidden properties. Some
/// older versions use `(effects (font …) (hide yes))`.
#[must_use]
pub fn read_hide_effect(parent: &SNode) -> bool {
    let Some(effects) = find_child(parent, "effects") else {
        return false;
    };
    for child in body_children(effects) {
        if atom_str(child) == Some("hide") {
            return true;
        }
        if child.head_symbol() == Some("hide") {
            let val = body_children(child).next().and_then(atom_str);
            return matches!(val, Some("yes" | "true"));
        }
    }
    false
}

/// Read a boolean-shaped form like `(in_bom yes)` / `(on_board no)` /
/// `(dnp yes)`. Returns `default_value` when the form is absent.
#[must_use]
pub fn read_yes_no(parent: &SNode, head: &str, default_value: bool) -> bool {
    let Some(form) = find_child(parent, head) else {
        return default_value;
    };
    match body_children(form).next().and_then(atom_str) {
        Some("yes" | "true") => true,
        Some("no" | "false") => false,
        _ => default_value,
    }
}
