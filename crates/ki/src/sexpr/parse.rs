//! S-expression parser — token stream → [`SNode`] tree.
//!
//! Recursive-descent over the flat token vector produced by
//! [`super::lex::tokenize`]. Each node's byte span in the original source is
//! preserved so [`super::emit`] can emit unmodified trees byte-identically.

use thiserror::Error;

use super::ast::SNode;
use super::lex::{tokenize, LexError, Token, TokenKind};

/// Errors the parser can surface.
#[derive(Debug, Clone, PartialEq, Eq, Error)]
pub enum ParseError {
    /// A lexer error bubbled up from [`tokenize`].
    #[error("lex error: {0}")]
    Lex(#[from] LexError),
    /// A `)` appeared at the top level with no matching `(`.
    #[error("unexpected `)` at byte {at}")]
    UnexpectedRParen { at: usize },
    /// A `(` opened a list that never closed before EOF.
    #[error("unclosed list opened at byte {open_at}")]
    UnclosedList { open_at: usize },
}

/// Parse a token stream into one or more S-expression trees.
///
/// Most `KiCad` files have a single top-level form (e.g. `(kicad_pcb …)`),
/// but the parser tolerates multiple top-level forms because that's useful
/// for test fixtures and `(generator …)` headers.
///
/// # Errors
/// Returns [`ParseError`] on unbalanced parens.
pub fn parse(tokens: &[Token]) -> Result<Vec<SNode>, ParseError> {
    let mut pos = 0usize;
    let mut nodes = Vec::new();
    while pos < tokens.len() {
        nodes.push(parse_one(tokens, &mut pos)?);
    }
    Ok(nodes)
}

/// Convenience: tokenize then parse a string in one step.
///
/// # Errors
/// Returns [`ParseError::Lex`] on lexer errors, or any parser error on
/// unbalanced parens.
pub fn parse_str(source: &str) -> Result<Vec<SNode>, ParseError> {
    let tokens = tokenize(source)?;
    parse(&tokens)
}

fn parse_one(tokens: &[Token], pos: &mut usize) -> Result<SNode, ParseError> {
    let tok = &tokens[*pos];
    match &tok.kind {
        TokenKind::LParen => parse_list(tokens, pos),
        TokenKind::RParen => Err(ParseError::UnexpectedRParen { at: tok.span.start }),
        TokenKind::Symbol(_) | TokenKind::String { .. } => {
            let node = SNode::Atom {
                token: tok.kind.clone(),
                span: tok.span.clone(),
            };
            *pos += 1;
            Ok(node)
        }
    }
}

fn parse_list(tokens: &[Token], pos: &mut usize) -> Result<SNode, ParseError> {
    // Caller has verified tokens[*pos] is LParen.
    let open_at = tokens[*pos].span.start;
    *pos += 1;
    let mut children = Vec::new();
    loop {
        if *pos >= tokens.len() {
            return Err(ParseError::UnclosedList { open_at });
        }
        let next = &tokens[*pos];
        if matches!(next.kind, TokenKind::RParen) {
            let end = next.span.end;
            *pos += 1;
            return Ok(SNode::List {
                children,
                span: open_at..end,
            });
        }
        children.push(parse_one(tokens, pos)?);
    }
}

#[cfg(test)]
mod tests {
    use super::super::emit::{emit_all_canonical, emit_all_from_source, emit_canonical};
    use super::*;
    use pretty_assertions::assert_eq;
    use proptest::prelude::*;

    /// Smoke: empty input parses to an empty node vec.
    #[test]
    fn smoke_empty_input_yields_no_nodes() {
        let nodes = parse_str("").expect("empty parses");
        assert!(nodes.is_empty());
    }

    /// Smoke: a single atom at the top level parses.
    #[test]
    fn smoke_top_level_atom() {
        let nodes = parse_str("hello").expect("parses");
        assert_eq!(nodes.len(), 1);
        let SNode::Atom {
            token: TokenKind::Symbol(s),
            span,
        } = &nodes[0]
        else {
            panic!("expected Symbol atom, got {:?}", nodes[0]);
        };
        assert_eq!(s, "hello");
        assert_eq!(*span, 0..5);
    }

    /// Smoke: a single empty list parses with span covering both parens.
    #[test]
    fn smoke_empty_list_span_covers_parens() {
        let nodes = parse_str("()").expect("parses");
        assert_eq!(nodes.len(), 1);
        let SNode::List { children, span } = &nodes[0] else {
            panic!("expected List, got {:?}", nodes[0]);
        };
        assert!(children.is_empty());
        assert_eq!(*span, 0..2);
    }

    /// Smoke: a flat list of atoms.
    #[test]
    fn smoke_flat_list() {
        let nodes = parse_str("(version 20240108)").expect("parses");
        assert_eq!(nodes.len(), 1);
        let SNode::List { children, span } = &nodes[0] else {
            panic!("expected List")
        };
        assert_eq!(*span, 0..18);
        assert_eq!(children.len(), 2);
        assert_eq!(nodes[0].head_symbol(), Some("version"));
        let SNode::Atom {
            token: TokenKind::Symbol(s),
            ..
        } = &children[1]
        else {
            panic!("expected Symbol, got {:?}", children[1]);
        };
        assert_eq!(s, "20240108");
    }

    /// Smoke: nested lists nest correctly.
    #[test]
    fn smoke_nested_list_structure() {
        let nodes = parse_str("(a (b c) d)").expect("parses");
        assert_eq!(nodes.len(), 1);
        let root = &nodes[0];
        assert_eq!(root.head_symbol(), Some("a"));
        assert_eq!(root.children().len(), 3);
        let inner = &root.children()[1];
        assert_eq!(inner.head_symbol(), Some("b"));
        assert_eq!(inner.children().len(), 2);
    }

    /// Smoke: an unexpected `)` at the top level is an error pointing at the
    /// offending byte.
    #[test]
    fn smoke_unexpected_rparen_errors() {
        let err = parse_str(")").expect_err("should error");
        assert_eq!(err, ParseError::UnexpectedRParen { at: 0 });
    }

    /// Smoke: an unclosed `(` is an error pointing at the opening byte.
    #[test]
    fn smoke_unclosed_list_errors() {
        let err = parse_str("(a b c").expect_err("should error");
        assert_eq!(err, ParseError::UnclosedList { open_at: 0 });
    }

    /// Smoke: a lex error (unterminated string) propagates through `parse_str`.
    #[test]
    fn smoke_lex_error_propagates() {
        let err = parse_str(r#"(a "oops"#).expect_err("should error");
        assert!(matches!(
            err,
            ParseError::Lex(LexError::UnterminatedString { .. })
        ));
    }

    /// Smoke: parser tolerates multiple top-level forms (test-fixture friendly).
    #[test]
    fn smoke_multiple_top_level_forms() {
        let nodes = parse_str("(a) (b) (c)").expect("parses");
        assert_eq!(nodes.len(), 3);
        for (n, expected) in nodes.iter().zip(["a", "b", "c"]) {
            assert_eq!(n.head_symbol(), Some(expected));
        }
    }

    /// Integration: parse → `emit_all_from_source` returns the bytes from
    /// the first node's start to the last node's end. For input that
    /// starts at byte 0 and has no trailing whitespace, this is byte-
    /// identical to the input.
    #[test]
    fn integration_emit_from_source_is_byte_identical() {
        let src = "(kicad_pcb (version 20240108) (generator kiclaude))";
        let nodes = parse_str(src).expect("parses");
        assert_eq!(emit_all_from_source(&nodes, src), src);
    }

    /// Integration: a blinky-style minimal `.kicad_pcb` fixture (mirrored
    /// from the reference `ki-mcp-pcb` build output) parses to a single
    /// `kicad_pcb` form whose head children match the expected layout,
    /// and `emit_all_from_source` reproduces it byte-identically (modulo
    /// the trailing newline, which lies outside any node's span).
    ///
    /// The real `examples/blinky/` directory is owned by task M0-C-03 —
    /// using an inline fixture here keeps this task's scope clean.
    #[test]
    fn integration_blinky_minimal_pcb_round_trip() {
        // Mirror of development/reference-only/ki-mcp-pcb/build/blinky-min.kicad_pcb,
        // trimmed of the trailing newline so the span covers the whole
        // string and byte-identity is exact.
        let src = concat!(
            "(kicad_pcb (version 20211014) (generator kiutils)\n",
            "\n",
            "  (general\n",
            "    (thickness 1.6)\n",
            "  )\n",
            "\n",
            "  (paper \"A4\")\n",
            "  (layers\n",
            "    (0 \"F.Cu\" signal)\n",
            "    (31 \"B.Cu\" signal)\n",
            "    (32 \"B.Adhes\" user \"B.Adhesive\")\n",
            "  )\n",
            "\n",
            "  (setup\n",
            "    (pad_to_mask_clearance 0.0)\n",
            "  )\n",
            "\n",
            "  (net 0 \"\")\n",
            "\n",
            ")",
        );
        let nodes = parse_str(src).expect("blinky pcb parses");
        assert_eq!(nodes.len(), 1, "single top-level form");
        let root = &nodes[0];
        assert_eq!(root.head_symbol(), Some("kicad_pcb"));

        // Expected head structure: head symbol + version + generator +
        // general + paper + layers + setup + net = 8 children.
        assert_eq!(root.children().len(), 8);

        // Find each named subform by head symbol — order-tolerant.
        let by_head: std::collections::HashMap<&str, &SNode> = root
            .children()
            .iter()
            .filter_map(|c| c.head_symbol().map(|h| (h, c)))
            .collect();
        assert!(by_head.contains_key("version"));
        assert!(by_head.contains_key("generator"));
        assert!(by_head.contains_key("general"));
        assert!(by_head.contains_key("paper"));
        assert!(by_head.contains_key("layers"));
        assert!(by_head.contains_key("setup"));
        assert!(by_head.contains_key("net"));

        // Layers form contains 3 layer rows.
        let layers = by_head["layers"];
        assert_eq!(layers.children().len(), 4, "layers head + 3 rows");

        // Byte-identity gate: `emit_all_from_source` must reproduce the
        // exact input bytes.
        assert_eq!(emit_all_from_source(&nodes, src), src);
    }

    /// Integration: every node's `span` is a valid slice of the source.
    /// Catches off-by-one bugs in the parser.
    #[test]
    fn integration_all_spans_slice_cleanly() {
        let src = r#"(at -1.5 "hello world" 0.25)"#;
        let nodes = parse_str(src).expect("parses");
        for node in &nodes {
            walk_assert_span_in_bounds(node, src);
        }
    }

    fn walk_assert_span_in_bounds(node: &SNode, source: &str) {
        let span = node.span();
        assert!(
            span.end <= source.len(),
            "span {span:?} extends past source len {}",
            source.len()
        );
        // &str index requires UTF-8 boundary safety — fixture is ASCII.
        let _ = &source[span];
        for child in node.children() {
            walk_assert_span_in_bounds(child, source);
        }
    }

    // -------- proptest --------

    fn arb_atom() -> impl Strategy<Value = String> {
        prop_oneof![
            "[A-Za-z_][A-Za-z0-9_.\\-+]{0,8}".prop_map(String::from),
            "-?[0-9]{1,4}(\\.[0-9]{1,4})?".prop_map(String::from),
            "[A-Za-z0-9 _.\\-+:]{0,8}".prop_map(|s| format!("\"{s}\"")),
        ]
    }

    /// Recursive arbitrary S-expression: depth ≤ 4, ≤ 5 atoms or
    /// sub-lists per list. Big enough to flex nesting; small enough to
    /// keep proptest fast.
    fn arb_sexpr() -> impl Strategy<Value = String> {
        let leaf = arb_atom();
        leaf.prop_recursive(4, 32, 5, |inner| {
            prop::collection::vec(inner, 1..=5).prop_map(|parts| format!("({})", parts.join(" ")))
        })
    }

    proptest! {
        /// Integration: parse(src) → emit_canonical → parse produces the
        /// same structural tree (atoms equal, list nesting equal).
        ///
        /// Canonical emit normalizes whitespace, so byte-identity isn't
        /// expected here — what we check is that the AST survives a
        /// canonical-emit round-trip without losing information.
        #[test]
        fn integration_parse_emit_canonical_parse_round_trip(src in arb_sexpr()) {
            let tree_a = parse_str(&src).expect("first parse");
            let canonical = emit_all_canonical(&tree_a);
            let tree_b = parse_str(&canonical).expect("re-parse canonical");
            prop_assert_eq!(
                strip_spans_vec(&tree_a),
                strip_spans_vec(&tree_b),
            );
        }

        /// Integration: parse(src) → emit_from_source on each node
        /// returns the exact original slice for that node — byte-identity
        /// at every node, not just the root.
        #[test]
        fn integration_per_node_emit_from_source_is_byte_identical(src in arb_sexpr()) {
            let nodes = parse_str(&src).expect("parses");
            for node in &nodes {
                prop_assert!(check_per_node_byte_identity(node, &src));
            }
        }
    }

    /// Strip spans from a tree so structural equality ignores them. After
    /// `emit_canonical`, spans are different (whitespace is normalized),
    /// so structural comparison must ignore them.
    fn strip_spans(node: &SNode) -> StrippedNode {
        match node {
            SNode::Atom { token, .. } => StrippedNode::Atom(token.clone()),
            SNode::List { children, .. } => {
                StrippedNode::List(children.iter().map(strip_spans).collect())
            }
        }
    }

    fn strip_spans_vec(nodes: &[SNode]) -> Vec<StrippedNode> {
        nodes.iter().map(strip_spans).collect()
    }

    #[derive(Debug, Clone, PartialEq)]
    enum StrippedNode {
        Atom(TokenKind),
        List(Vec<StrippedNode>),
    }

    fn check_per_node_byte_identity(node: &SNode, source: &str) -> bool {
        let span = node.span();
        let from_src = &source[span.clone()];
        let from_emit = super::super::emit::emit_from_source(node, source);
        if from_src != from_emit {
            return false;
        }
        // Also assert: re-parsing the slice for this node round-trips
        // through canonical emit, proving the slice is self-contained.
        if let Ok(reparsed) = parse_str(from_src) {
            let canonical_a = emit_canonical(node);
            let canonical_b = emit_all_canonical(&reparsed);
            if canonical_a != canonical_b {
                return false;
            }
        }
        for child in node.children() {
            if !check_per_node_byte_identity(child, source) {
                return false;
            }
        }
        true
    }
}
