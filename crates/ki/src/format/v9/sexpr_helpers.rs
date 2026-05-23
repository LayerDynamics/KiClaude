//! Helpers for navigating [`SNode`] trees produced by the s-expression
//! parser. Each helper takes the shape "find this subform / coerce this
//! atom" and returns `Option` for missing fields and `Result<_, String>`
//! for present-but-malformed ones — the mapper decides which to treat
//! as fatal.

use crate::sexpr::{SNode, TokenKind};

/// Iterate over the direct children of a list node, skipping the head.
///
/// Returns an empty iterator for atoms or empty lists.
pub fn body_children(node: &SNode) -> impl Iterator<Item = &SNode> {
    let all = node.children();
    let skip = usize::from(!all.is_empty());
    all.iter().skip(skip)
}

/// Find the first child sub-list whose head symbol is `head`.
#[must_use]
pub fn find_child<'a>(node: &'a SNode, head: &str) -> Option<&'a SNode> {
    node.children()
        .iter()
        .find(|c| c.head_symbol() == Some(head))
}

/// Find every child sub-list whose head symbol is `head`.
#[must_use]
pub fn find_children<'a>(node: &'a SNode, head: &str) -> Vec<&'a SNode> {
    node.children()
        .iter()
        .filter(|c| c.head_symbol() == Some(head))
        .collect()
}

/// Coerce an atom (Symbol or String) to its text value.
#[must_use]
pub fn atom_str(node: &SNode) -> Option<&str> {
    match node {
        SNode::Atom {
            token: TokenKind::Symbol(s),
            ..
        } => Some(s.as_str()),
        SNode::Atom {
            token: TokenKind::String { value, .. },
            ..
        } => Some(value.as_str()),
        _ => None,
    }
}

/// Coerce an atom to its f64 value, parsing from the symbol/string text.
#[must_use]
pub fn atom_f64(node: &SNode) -> Option<f64> {
    atom_str(node).and_then(|s| s.parse::<f64>().ok())
}

/// Coerce an atom to its i32 value.
#[must_use]
pub fn atom_i32(node: &SNode) -> Option<i32> {
    atom_str(node).and_then(|s| s.parse::<i32>().ok())
}

/// Collect a sequence of pairs from forms like `(xy 1.0 2.0)` inside a
/// `(pts …)` container. Returns the pairs in order.
#[must_use]
pub fn collect_xy_points(pts_node: &SNode) -> Vec<(f64, f64)> {
    let mut out = Vec::new();
    for xy in find_children(pts_node, "xy") {
        let body: Vec<&SNode> = body_children(xy).collect();
        if body.len() >= 2 {
            if let (Some(x), Some(y)) = (atom_f64(body[0]), atom_f64(body[1])) {
                out.push((x, y));
            }
        }
    }
    out
}
