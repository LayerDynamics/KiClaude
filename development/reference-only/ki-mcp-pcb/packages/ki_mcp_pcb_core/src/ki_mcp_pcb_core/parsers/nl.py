"""Natural language → CIR.

The LLM-backed parser. The MCP layer can also drive this, but having a
first-class library function is useful for the CLI (``kimp ask``) and
for the LLM eval harness in tests/llm_evals/.

Design choices:

  * We send Claude the **JSON Schema** for ``Board`` (Pydantic-generated)
    so the model knows the exact shape to return — no schema drift between
    code and prompt.
  * We use the Anthropic Python SDK if available; otherwise raise
    :class:`NLParserUnavailableError` with a clear message.
  * The model returns YAML (one fenced block); we parse it through the
    standard YAML parser so the rest of the pipeline sees a normal CIR.
  * Per CLAUDE.md rule #1, we always emit a YAML draft to disk for human
    review *before* returning the parsed Board. The DSL is the audit
    boundary.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

from ki_mcp_pcb_core.cir.models import Board
from ki_mcp_pcb_core.parsers.yaml import parse_yaml

ANTHROPIC_MODEL = "claude-sonnet-4-6"
ANTHROPIC_ENV_VAR = "ANTHROPIC_API_KEY"


# ---------------------------------------------------------------------------
# Errors and types
# ---------------------------------------------------------------------------


class NLParserError(RuntimeError):
    """The NL parser failed in a recoverable way."""


class NLParserUnavailableError(NLParserError):
    """The Anthropic SDK isn't installed or no API key is set."""


@dataclass(frozen=True)
class NLParseResult:
    """The result of NL → CIR.

    ``draft_yaml`` is the model's raw output; ``board`` is the parsed Board.
    The CLI writes ``draft_yaml`` to disk so the user can review it before
    any KiCad files are touched.
    """

    board: Board
    draft_yaml: str


# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------


_SYSTEM_PROMPT = """\
You are the natural-language layer of ki-mcp-pcb, a text-to-PCB toolchain.

Your job: turn a user's plain-English PCB description into a YAML
Canonical Intermediate Representation (CIR) document that the toolchain
can validate, synthesize, and fabricate from.

Hard rules — these are not negotiable:
1. Output ONLY a YAML document inside a single fenced ```yaml block.
   No prose before or after.
2. Use REAL, IN-STOCK manufacturer part numbers (MPNs). Common JLC parts:
     - ESP32-S3-WROOM-1, STM32F407VGT6, RP2040, ATSAMD21E18A-AU
     - AMS1117-3.3, AP2112K-3.3, NCP1117ST33T3G
     - GRM188R71C104KA01D (100nF 0603), GRM21BR60J106KE19L (10uF 0805)
     - USB4105-GF-A (USB-C), 10118194-0001LF (USB micro-B)
     - LTST-C190KGKT (green LED), 1N5819HW-7-F (Schottky diode)
3. Refdes prefixes: U for ICs, C for caps, R for resistors, L for inductors,
   D for diodes, J for connectors, Y for crystals, SW for switches.
4. Use uppercase net names. Ground is GND. Power rails are 3V3, 5V0/VBUS,
   AVDD, etc.
5. Set cir_version to the latest version the schema declares.
6. Set fab.name to "jlcpcb" and fab.layer_count to 2 (hobbyist) or 4
   (mixed-signal / high-speed) as appropriate.
7. For mixed-signal boards, set partition on components and add
   cross_partition_ok: true to any bus that legitimately spans
   digital ↔ analog.
8. For differential pairs (USB, Ethernet), declare both halves with
   bidirectional diff_pair_with AND a shared length_match_group AND a
   target_impedance_ohm AND a reference_plane.

If anything is ambiguous, make a reasonable conservative choice and add
a YAML comment (# ...) explaining what you assumed.
"""


_USER_TEMPLATE = """\
The CIR schema (JSON Schema, for reference):

```json
{schema}
```

User's PCB description:

{description}

Produce the YAML CIR document.
"""


def _build_messages(description: str) -> list[dict[str, str]]:
    schema = json.dumps(Board.model_json_schema(), indent=2)
    return [
        {
            "role": "user",
            "content": _USER_TEMPLATE.format(schema=schema, description=description),
        }
    ]


# ---------------------------------------------------------------------------
# Entrypoints
# ---------------------------------------------------------------------------


def parse_nl(
    text: str,
    *,
    draft_path: Path | None = None,
    model: str = ANTHROPIC_MODEL,
    api_key: str | None = None,
) -> NLParseResult:
    """Parse a natural-language PCB description into CIR via Claude.

    Raises :class:`NLParserUnavailableError` if the SDK or API key is
    missing. Tests typically bypass this with :func:`parse_nl_with_client`
    using a mock client.
    """
    client = _make_client(api_key)
    return _parse_with_client(client, text, draft_path=draft_path, model=model)


def parse_nl_with_client(
    client: object,
    text: str,
    *,
    draft_path: Path | None = None,
    model: str = ANTHROPIC_MODEL,
) -> NLParseResult:
    """Parse using an explicit client. The test harness passes a mock here."""
    return _parse_with_client(client, text, draft_path=draft_path, model=model)


# ---------------------------------------------------------------------------
# Implementation
# ---------------------------------------------------------------------------


def _make_client(api_key: str | None) -> object:
    try:
        import anthropic
    except ImportError as exc:  # pragma: no cover — depends on local env
        raise NLParserUnavailableError(
            "The `anthropic` package isn't installed. "
            "Run `uv sync --extra llm` or `pip install anthropic`."
        ) from exc

    key = api_key or os.environ.get(ANTHROPIC_ENV_VAR)
    if not key:
        raise NLParserUnavailableError(
            f"Set {ANTHROPIC_ENV_VAR} to a valid Anthropic API key, or pass "
            "api_key= explicitly."
        )
    return anthropic.Anthropic(api_key=key)


_YAML_BLOCK_RE = re.compile(r"```(?:yaml|yml)?\s*\n(.*?)```", re.DOTALL)


def _extract_yaml(text: str) -> str:
    """Pull the YAML out of a fenced ```yaml block. Falls back to raw text."""
    m = _YAML_BLOCK_RE.search(text)
    return (m.group(1) if m else text).strip()


def _parse_with_client(
    client: object,
    text: str,
    *,
    draft_path: Path | None,
    model: str,
) -> NLParseResult:
    messages = _build_messages(text)
    response = client.messages.create(  # type: ignore[attr-defined]
        model=model,
        system=_SYSTEM_PROMPT,
        max_tokens=4096,
        messages=messages,
    )
    body = _response_text(response)
    yaml_text = _extract_yaml(body)

    if draft_path is not None:
        draft_path.parent.mkdir(parents=True, exist_ok=True)
        draft_path.write_text(yaml_text + "\n", encoding="utf-8")

    try:
        board = parse_yaml(yaml_text)
    except Exception as exc:
        raise NLParserError(
            f"Model output didn't parse as a CIR YAML document: {exc}\n"
            f"---\n{yaml_text[:1000]}\n---"
        ) from exc

    return NLParseResult(board=board, draft_yaml=yaml_text)


def _response_text(response: object) -> str:
    """Extract the text content from an Anthropic SDK response.

    Real response objects expose ``.content`` as a list of blocks with
    ``.text`` attributes. The test mock can return a plain string in a
    ``.text`` attribute.
    """
    direct = getattr(response, "text", None)
    if isinstance(direct, str):
        return direct
    content = getattr(response, "content", None)
    if isinstance(content, list) and content:
        first = content[0]
        if isinstance(first, dict) and "text" in first:
            return str(first["text"])
        inner = getattr(first, "text", None)
        if isinstance(inner, str):
            return inner
    if isinstance(response, str):
        return response
    raise NLParserError(f"Unexpected response shape: {type(response).__name__}")
