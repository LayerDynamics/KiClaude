//! S-expression emit — [`SNode`] tree → string.
//!
//! Two emit modes are provided, serving different needs:
//!
//! - [`emit_from_source`] / [`emit_all_from_source`]: cheap, byte-identical
//!   slicing of the original source. Used to prove byte-fidelity of an
//!   unmodified parse (the M0-R-04 round-trip gate).
//! - [`emit_canonical`]: rebuild a string from the AST alone, no source
//!   required. Produces semantically equivalent but normalized-whitespace
//!   output — what callers want after they've *modified* the tree.
//!
//! Both modes preserve string escapes via [`TokenKind::String::raw`].

use super::ast::SNode;
use super::lex::TokenKind;

/// Emit a node back as the exact source bytes it was parsed from.
///
/// Returns the slice `&source[node.span()]`, which for an unmodified parse
/// equals the on-disk text of that node.
///
/// # Panics
/// Panics if `node.span()` is outside the bounds of `source` — which can
/// only happen if the caller pairs a node with a different source string
/// than it was parsed from.
#[must_use]
pub fn emit_from_source<'a>(node: &SNode, source: &'a str) -> &'a str {
    &source[node.span()]
}

/// Emit a slice of top-level nodes as the source range they collectively
/// cover. Inter-node whitespace from the original source is preserved
/// because the slice spans across it.
///
/// Returns the empty string for an empty input slice.
#[must_use]
pub fn emit_all_from_source<'a>(nodes: &[SNode], source: &'a str) -> &'a str {
    if nodes.is_empty() {
        return "";
    }
    let start = nodes[0].span().start;
    let end = nodes[nodes.len() - 1].span().end;
    &source[start..end]
}

/// Emit a node from the AST alone, producing normalized output (single
/// space between siblings, no leading/trailing whitespace inside lists).
///
/// Re-parsing this output gives back a tree that is structurally equal to
/// the input (atom kinds and nesting preserved). It is **not** in general
/// byte-identical to the source — use [`emit_from_source`] for that.
#[must_use]
pub fn emit_canonical(node: &SNode) -> String {
    let mut out = String::new();
    write_canonical(node, &mut out);
    out
}

/// Emit a sequence of top-level nodes canonically, separated by single
/// spaces. Mirror of [`emit_canonical`] for multi-form inputs.
#[must_use]
pub fn emit_all_canonical(nodes: &[SNode]) -> String {
    let mut out = String::new();
    for (i, node) in nodes.iter().enumerate() {
        if i > 0 {
            out.push(' ');
        }
        write_canonical(node, &mut out);
    }
    out
}

fn write_canonical(node: &SNode, out: &mut String) {
    match node {
        SNode::Atom { token, .. } => write_atom(token, out),
        SNode::List { children, .. } => {
            out.push('(');
            for (i, child) in children.iter().enumerate() {
                if i > 0 {
                    out.push(' ');
                }
                write_canonical(child, out);
            }
            out.push(')');
        }
    }
}

fn write_atom(token: &TokenKind, out: &mut String) {
    match token {
        TokenKind::Symbol(s) => out.push_str(s),
        TokenKind::String { raw, .. } => {
            out.push('"');
            out.push_str(raw);
            out.push('"');
        }
        // Parens never appear inside an `SNode::Atom` — the parser only
        // builds atoms from Symbol/String tokens. Treat as a no-op rather
        // than panic so future refactors don't crash production callers.
        TokenKind::LParen | TokenKind::RParen => {}
    }
}

#[cfg(test)]
mod tests {
    use super::super::parse::parse_str;
    use super::*;
    use pretty_assertions::assert_eq;

    /// Smoke: [`emit_from_source`] on a single parsed list slices back the
    /// exact bytes of the original form.
    #[test]
    fn smoke_emit_from_source_is_byte_identical() {
        let src = "(version 20240108)";
        let nodes = parse_str(src).expect("parses");
        assert_eq!(emit_from_source(&nodes[0], src), src);
    }

    /// Smoke: [`emit_canonical`] of a simple form produces the same text
    /// when the source already has single-space separators.
    #[test]
    fn smoke_emit_canonical_matches_minimal_source() {
        let src = "(version 20240108)";
        let nodes = parse_str(src).expect("parses");
        assert_eq!(emit_canonical(&nodes[0]), src);
    }

    /// Smoke: [`emit_canonical`] normalizes whitespace.
    #[test]
    fn smoke_emit_canonical_normalizes_whitespace() {
        let src = "(  a   b\n  c )";
        let nodes = parse_str(src).expect("parses");
        assert_eq!(emit_canonical(&nodes[0]), "(a b c)");
    }

    /// Smoke: [`emit_canonical`] preserves quoted strings via the `raw`
    /// form.
    #[test]
    fn smoke_emit_canonical_preserves_string_escapes() {
        let src = r#"(name "hello \"world\"")"#;
        let nodes = parse_str(src).expect("parses");
        assert_eq!(emit_canonical(&nodes[0]), src);
    }

    /// Smoke: [`emit_all_from_source`] on multi-form input returns the
    /// range covered, preserving inter-form whitespace.
    #[test]
    fn smoke_emit_all_from_source_preserves_inter_form_whitespace() {
        let src = "(a)   (b)";
        let nodes = parse_str(src).expect("parses");
        assert_eq!(emit_all_from_source(&nodes, src), src);
    }

    /// Smoke: [`emit_all_from_source`] on empty input returns empty
    /// string.
    #[test]
    fn smoke_emit_all_from_source_empty() {
        let nodes: Vec<SNode> = Vec::new();
        assert_eq!(emit_all_from_source(&nodes, ""), "");
    }
}
