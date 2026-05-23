"""Tests for the fallback .ato parser used by the M1 demo.

When atopile is installed (M2+), these tests still exercise the
fallback by passing source text directly — the dispatch layer
prefers atopile only when importing succeeds.
"""

from __future__ import annotations

from pathlib import Path

from ki_mcp_pcb_core.parsers.ato import _fallback_parse

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


def test_demo_ato_parses_components() -> None:
    text = (EXAMPLES / "esp32_s3_blinky.ato").read_text(encoding="utf-8")
    board = _fallback_parse(text, "esp32-blinky")

    mpns = {c.mpn for c in board.components}
    # The four explicitly-instantiated parts must show up
    assert "ESP32-S3-WROOM-1" in mpns
    assert "USB4105-GF-A" in mpns
    assert "AMS1117-3.3" in mpns
    assert "LTST-C190KGKT" in mpns


def test_demo_ato_creates_decoupling_caps() -> None:
    text = (EXAMPLES / "esp32_s3_blinky.ato").read_text(encoding="utf-8")
    board = _fallback_parse(text, "esp32-blinky")
    caps = [c for c in board.components if c.refdes.startswith("C")]
    # 100nF decoupler + 22uF bulk = 2 caps
    assert len(caps) == 2


def test_demo_ato_creates_gnd_net() -> None:
    text = (EXAMPLES / "esp32_s3_blinky.ato").read_text(encoding="utf-8")
    board = _fallback_parse(text, "esp32-blinky")
    nets_by_name = {n.name: n for n in board.nets}
    assert "GND" in nets_by_name
    assert nets_by_name["GND"].net_class == "ground"


def test_demo_ato_refdes_are_unique() -> None:
    text = (EXAMPLES / "esp32_s3_blinky.ato").read_text(encoding="utf-8")
    board = _fallback_parse(text, "esp32-blinky")
    refdes = [c.refdes for c in board.components]
    assert len(refdes) == len(set(refdes))


def test_fallback_handles_blank_lines_and_comments() -> None:
    text = """
module X:
    a = new ESP32_S3_WROOM_1  // a comment

    // standalone comment
    b = new LED_0603_red

    a.VDD ~ b.A
"""
    board = _fallback_parse(text, "x")
    assert len(board.components) == 2
    # Connection makes at least one net
    assert len(board.nets) >= 1
