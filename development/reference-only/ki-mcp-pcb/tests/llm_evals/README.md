# llm_evals/

End-to-end evals for the NL → CIR pipeline. Unit tests can't catch "the LLM omitted a part the user named" or "the parser hallucinated an MPN" — that needs a fixture-driven semantic-equivalence harness.

## Structure

```
llm_evals/
├── fixtures/             one JSON file per eval case
│   └── *.json            {prompt, expected_cir, tolerances, notes}
├── judge.py              semantic-equivalence checker (component/net set match,
│                         net_class match, constraint shape match, etc.)
├── runner.py             loads fixtures, calls a parser, compares
└── test_llm_evals.py     pytest entrypoint
```

## How the harness works

The runner is **parser-agnostic**: it accepts any callable `(prompt: str) -> Board` and runs it against every fixture. Today we ship a **mock parser** that round-trips an expected CIR directly — this exercises every piece of the harness *except* the LLM. When M1 lands the real `parse_nl`, you swap the parser and the same fixtures run.

The judge measures:

| Metric | What it catches |
|---|---|
| Component set match | LLM dropped or hallucinated a part |
| MPN exact match | LLM substituted a wrong part for a named one |
| Net class match | Power treated as signal, ground misclassified |
| Net membership Jaccard | Off-by-one pin mistakes, missing connections |
| Constraint shape | Declared length-match / impedance constraints preserved |
| Fab target match | LLM ignored "must be JLCPCB-fabbable" |

Each is reported as a fraction in `[0.0, 1.0]`. Fixtures declare per-metric pass thresholds; the test fails if any metric is below threshold.

## Adding an eval

1. Write the prompt as a string in a new file under `fixtures/`.
2. Hand-author the expected CIR.
3. Choose tolerances. Default (`fixtures/_defaults.json`) is strict; loosen as needed for fuzzy prompts.
4. Run: `uv run pytest tests/llm_evals/`.

## When this matters

M0: this harness exercises itself with a mock parser. Passing here means the *judge* works.
M1+: swap the real NL parser in `runner.py` and the same fixtures grade actual LLM output.
