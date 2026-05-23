//! S-expression lexer.
//!
//! Hand-rolled, allocation-light, byte-offset-preserving. Whitespace is
//! consumed (not emitted as tokens) but each token's source range is
//! recorded so an emitter can reconstruct exact formatting if desired.
//!
//! Token grammar:
//! - `(` and `)` are their own tokens.
//! - `"..."` is a quoted string. Backslash escapes `\\`, `\"`, `\n`, `\t`,
//!   `\r`. Other backslash sequences pass through verbatim.
//! - Everything else, up to the next whitespace or paren, is a symbol.
//!   Numbers are NOT lexed as a distinct kind — typed domain code parses
//!   them from symbols where it expects them, so float precision survives
//!   a round-trip (`"0.10"` stays `"0.10"`).
//!
//! Comments: `KiCad` S-expressions don't use comments in shipped files, so
//! the lexer doesn't recognize any. If we ever need to preserve them,
//! that's a future task tracked in the spec.

use std::ops::Range;

use thiserror::Error;

/// A single lexed token.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Token {
    pub kind: TokenKind,
    /// Byte range in the source string covered by this token.
    pub span: Range<usize>,
}

/// What kind of token this is.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum TokenKind {
    /// `(`
    LParen,
    /// `)`
    RParen,
    /// Unquoted bareword. Stored verbatim; number conversion is deferred.
    Symbol(String),
    /// Quoted string. The stored value has escape sequences resolved
    /// (e.g. `\"` → `"`). The `raw` field preserves the on-disk form
    /// so emitters can choose between "as-written" and re-canonicalized.
    String { value: String, raw: String },
}

impl TokenKind {
    /// Compact, kind-only label for error messages.
    #[must_use]
    pub fn name(&self) -> &'static str {
        match self {
            Self::LParen => "(",
            Self::RParen => ")",
            Self::Symbol(_) => "symbol",
            Self::String { .. } => "string",
        }
    }
}

/// Errors the lexer can surface. Spans point into the source string.
#[derive(Debug, Clone, PartialEq, Eq, Error)]
pub enum LexError {
    #[error("unterminated string starting at byte {start}")]
    UnterminatedString { start: usize },
    #[error("unexpected end of input inside escape sequence at byte {at}")]
    DanglingEscape { at: usize },
    #[error("empty symbol at byte {at}")]
    EmptySymbol { at: usize },
}

/// Tokenize an S-expression source string.
///
/// # Errors
/// Returns [`LexError`] on unterminated strings or dangling escapes.
pub fn tokenize(input: &str) -> Result<Vec<Token>, LexError> {
    let bytes = input.as_bytes();
    let mut tokens = Vec::new();
    let mut i = 0usize;

    while i < bytes.len() {
        let c = bytes[i];
        match c {
            // Whitespace.
            b' ' | b'\t' | b'\r' | b'\n' => {
                i += 1;
            }
            // Parens.
            b'(' => {
                tokens.push(Token {
                    kind: TokenKind::LParen,
                    span: i..i + 1,
                });
                i += 1;
            }
            b')' => {
                tokens.push(Token {
                    kind: TokenKind::RParen,
                    span: i..i + 1,
                });
                i += 1;
            }
            // Quoted string.
            b'"' => {
                let start = i;
                i += 1; // consume opening quote
                let value_start = i;
                let mut value = String::new();
                loop {
                    if i >= bytes.len() {
                        return Err(LexError::UnterminatedString { start });
                    }
                    match bytes[i] {
                        b'"' => break,
                        b'\\' => {
                            if i + 1 >= bytes.len() {
                                return Err(LexError::DanglingEscape { at: i });
                            }
                            let esc = bytes[i + 1];
                            match esc {
                                b'"' => value.push('"'),
                                b'\\' => value.push('\\'),
                                b'n' => value.push('\n'),
                                b't' => value.push('\t'),
                                b'r' => value.push('\r'),
                                other => {
                                    // Unknown escape: preserve verbatim.
                                    value.push('\\');
                                    value.push(other as char);
                                }
                            }
                            i += 2;
                        }
                        _ => {
                            // Copy a contiguous UTF-8 run for speed/correctness.
                            let run_start = i;
                            while i < bytes.len() && bytes[i] != b'"' && bytes[i] != b'\\' {
                                i += 1;
                            }
                            // Safe because we advanced one byte at a time over
                            // ASCII and our input was a valid &str slice.
                            value.push_str(&input[run_start..i]);
                        }
                    }
                }
                let value_end = i;
                // Raw text of the string body, between (but not including)
                // the quotes — useful for emitters that want byte fidelity.
                let raw = input[value_start..value_end].to_string();
                i += 1; // consume closing quote
                tokens.push(Token {
                    kind: TokenKind::String { value, raw },
                    span: start..i,
                });
            }
            // Bareword / symbol.
            _ => {
                let start = i;
                while i < bytes.len() {
                    let b = bytes[i];
                    if matches!(b, b' ' | b'\t' | b'\r' | b'\n' | b'(' | b')' | b'"') {
                        break;
                    }
                    i += 1;
                }
                if i == start {
                    return Err(LexError::EmptySymbol { at: start });
                }
                let text = &input[start..i];
                tokens.push(Token {
                    kind: TokenKind::Symbol(text.to_string()),
                    span: start..i,
                });
            }
        }
    }

    Ok(tokens)
}

#[cfg(test)]
mod tests {
    use super::*;
    use pretty_assertions::assert_eq;
    use proptest::prelude::*;

    /// Smoke: empty input → empty token vec.
    #[test]
    fn smoke_empty_input() {
        let tokens = tokenize("").expect("empty input lexes");
        assert!(tokens.is_empty());
    }

    /// Smoke: only whitespace → empty token vec.
    #[test]
    fn smoke_whitespace_only() {
        let tokens = tokenize("   \n\t\r ").expect("whitespace lexes");
        assert!(tokens.is_empty());
    }

    /// Smoke: parens and a symbol.
    #[test]
    fn smoke_basic_form() {
        let tokens = tokenize("(version 20240108)").expect("lexes");
        assert_eq!(tokens.len(), 4);
        assert_eq!(tokens[0].kind, TokenKind::LParen);
        assert_eq!(tokens[1].kind, TokenKind::Symbol("version".to_string()));
        assert_eq!(tokens[2].kind, TokenKind::Symbol("20240108".to_string()));
        assert_eq!(tokens[3].kind, TokenKind::RParen);
        assert_eq!(tokens[0].span, 0..1);
        assert_eq!(tokens[1].span, 1..8);
        assert_eq!(tokens[2].span, 9..17);
        assert_eq!(tokens[3].span, 17..18);
    }

    /// Smoke: nested form.
    #[test]
    fn smoke_nested() {
        let tokens = tokenize("(a (b c) d)").expect("lexes");
        let names: Vec<_> = tokens.iter().map(|t| t.kind.name()).collect();
        assert_eq!(
            names,
            vec!["(", "symbol", "(", "symbol", "symbol", ")", "symbol", ")"]
        );
    }

    /// Smoke: string with escapes.
    #[test]
    fn smoke_string_escapes() {
        let tokens = tokenize(r#"(name "hello \"world\"")"#).expect("lexes");
        let TokenKind::String { ref value, ref raw } = tokens[2].kind else {
            panic!("expected String token, got {:?}", tokens[2].kind);
        };
        assert_eq!(value, "hello \"world\"");
        assert_eq!(raw, r#"hello \"world\""#);
    }

    /// Smoke: negative numbers and decimals lex as symbols (no number kind).
    #[test]
    fn smoke_numbers_are_symbols() {
        let tokens = tokenize("(at -1.5 0.25)").expect("lexes");
        assert_eq!(tokens[2].kind, TokenKind::Symbol("-1.5".to_string()));
        assert_eq!(tokens[3].kind, TokenKind::Symbol("0.25".to_string()));
    }

    /// Smoke: unterminated string is an error.
    #[test]
    fn smoke_unterminated_string_errors() {
        let err = tokenize(r#"(a "oops"#).expect_err("should error");
        assert!(matches!(err, LexError::UnterminatedString { .. }));
    }

    /// Integration: token spans cover the source contiguously up to whitespace.
    /// For every token, `&input[token.span]` is the on-disk text of that token
    /// (including outer quotes for strings).
    #[test]
    fn integration_spans_recover_token_text() {
        let src = r#"(at -1.5 "hello world" 0.25)"#;
        let tokens = tokenize(src).expect("lexes");
        for t in &tokens {
            let slice = &src[t.span.clone()];
            match &t.kind {
                TokenKind::LParen => assert_eq!(slice, "("),
                TokenKind::RParen => assert_eq!(slice, ")"),
                TokenKind::Symbol(s) => assert_eq!(slice, s),
                TokenKind::String { raw, .. } => {
                    assert!(slice.starts_with('"') && slice.ends_with('"'));
                    assert_eq!(&slice[1..slice.len() - 1], raw);
                }
            }
        }
    }

    /// Strategy for proptest: build a randomized but valid S-expression by
    /// composing parens, symbols, and quoted strings with whitespace.
    fn arb_atom() -> impl Strategy<Value = String> {
        prop_oneof![
            // Symbols: at least 1 char from a safe alphabet.
            "[A-Za-z_][A-Za-z0-9_.\\-+]{0,8}".prop_map(String::from),
            // Numbers (lex as symbols).
            "-?[0-9]{1,4}(\\.[0-9]{1,4})?".prop_map(String::from),
            // Quoted strings without escapes.
            "[A-Za-z0-9 _.\\-+:]{0,8}".prop_map(|s| format!("\"{s}\"")),
        ]
    }

    fn arb_sexpr_lite() -> impl Strategy<Value = String> {
        // One flat (head atom1 atom2 ...) form. Sufficient for round-trip
        // verification of the lexer; deeper nesting is parser territory.
        prop::collection::vec(arb_atom(), 1..6).prop_map(|atoms| format!("({})", atoms.join(" ")))
    }

    proptest! {
        /// Integration: lex(input) → re-emit token text → lex again produces
        /// the same token kinds in the same order.
        #[test]
        fn integration_lex_relex_identity(src in arb_sexpr_lite()) {
            let tokens_a = tokenize(&src).expect("first lex");
            // Re-emit the token text (joined by single spaces, paren-tight).
            let mut emitted = String::new();
            for (i, t) in tokens_a.iter().enumerate() {
                if i > 0 {
                    let prev = &tokens_a[i - 1].kind;
                    let need_sep = !matches!(prev, TokenKind::LParen)
                        && !matches!(t.kind, TokenKind::RParen);
                    if need_sep {
                        emitted.push(' ');
                    }
                }
                match &t.kind {
                    TokenKind::LParen => emitted.push('('),
                    TokenKind::RParen => emitted.push(')'),
                    TokenKind::Symbol(s) => emitted.push_str(s),
                    TokenKind::String { raw, .. } => {
                        emitted.push('"');
                        emitted.push_str(raw);
                        emitted.push('"');
                    }
                }
            }
            let tokens_b = tokenize(&emitted).expect("second lex");
            let kinds_a: Vec<_> = tokens_a.iter().map(|t| t.kind.clone()).collect();
            let kinds_b: Vec<_> = tokens_b.iter().map(|t| t.kind.clone()).collect();
            prop_assert_eq!(kinds_a, kinds_b);
        }
    }
}
