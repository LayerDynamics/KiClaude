//! S-expression abstract syntax tree.
//!
//! [`SNode`] is the parser's output — a tree of lists and atoms with each
//! node's byte span in the original source preserved. The span lets the
//! [`emit`](super::emit) layer reconstruct the source text byte-identically
//! for unmodified trees without the AST having to carry trivia.

use std::ops::Range;

use super::lex::TokenKind;

/// One node in the S-expression tree.
#[derive(Debug, Clone, PartialEq)]
pub enum SNode {
    /// `(head child1 child2 …)` — span covers the opening `(` through the
    /// closing `)` inclusive.
    List {
        children: Vec<SNode>,
        span: Range<usize>,
    },
    /// An atom (symbol or string) — span matches the underlying token's
    /// span exactly, so for strings it includes the surrounding quotes.
    Atom {
        token: TokenKind,
        span: Range<usize>,
    },
}

impl SNode {
    /// Byte span this node covers in the original source.
    #[must_use]
    pub fn span(&self) -> Range<usize> {
        match self {
            Self::List { span, .. } | Self::Atom { span, .. } => span.clone(),
        }
    }

    /// `true` if this node is a list (rather than an atom).
    #[must_use]
    pub fn is_list(&self) -> bool {
        matches!(self, Self::List { .. })
    }

    /// If this node is a list whose first child is a symbol atom, return the
    /// symbol text — this is the conventional "head" of a `KiCad`
    /// S-expression form (e.g. `kicad_pcb`, `version`, `net`).
    #[must_use]
    pub fn head_symbol(&self) -> Option<&str> {
        let Self::List { children, .. } = self else {
            return None;
        };
        match children.first()? {
            Self::Atom {
                token: TokenKind::Symbol(s),
                ..
            } => Some(s.as_str()),
            _ => None,
        }
    }

    /// Children of a list node, or an empty slice for atoms. Lets callers
    /// walk the tree without pattern-matching every site.
    #[must_use]
    pub fn children(&self) -> &[SNode] {
        match self {
            Self::List { children, .. } => children,
            Self::Atom { .. } => &[],
        }
    }
}
