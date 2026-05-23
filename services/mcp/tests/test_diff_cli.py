"""M2-T-11 acceptance tests for the `kiclaude diff` Python entry."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from kc_mcp.diff_cli import (
    PcbSummary,
    diff_pcbs,
    main,
    parse_pcb,
    parse_sexpr,
    render_svg_diff,
)

PCB_A = """(kicad_pcb (version 20240108) (generator kiclaude)
  (general (thickness 1.6))
  (paper "A4")
  (layers (0 "F.Cu" signal) (31 "B.Cu" signal) (44 "Edge.Cuts" user))
  (setup (pad_to_mask_clearance 0.0))
  (net 0 "")
  (net 1 "GND")
  (footprint "MCU:ESP32"
    (layer "F.Cu")
    (uuid "fp-u1")
    (at 50 50 0)
    (property "Reference" "U1")
    (property "Value" "ESP32")
  )
  (segment (start 0 0) (end 10 0) (width 0.25) (layer "F.Cu") (net 1)
    (uuid "t-1")
  )
)
"""

PCB_B = """(kicad_pcb (version 20240108) (generator kiclaude)
  (general (thickness 1.6))
  (paper "A4")
  (layers (0 "F.Cu" signal) (31 "B.Cu" signal) (44 "Edge.Cuts" user))
  (setup (pad_to_mask_clearance 0.0))
  (net 0 "")
  (net 1 "GND")
  (footprint "MCU:ESP32"
    (layer "F.Cu")
    (uuid "fp-u1")
    (at 60 50 0)
    (property "Reference" "U1")
    (property "Value" "ESP32-S3")
  )
  (footprint "Resistor_SMD:R_0603"
    (layer "F.Cu")
    (uuid "fp-r1")
    (at 70 50 0)
    (property "Reference" "R1")
    (property "Value" "10k")
  )
  (segment (start 0 0) (end 10 0) (width 0.25) (layer "F.Cu") (net 1)
    (uuid "t-1")
  )
  (segment (start 10 0) (end 20 0) (width 0.25) (layer "F.Cu") (net 1)
    (uuid "t-2")
  )
)
"""


def test_parse_sexpr_round_trips_basic_forms() -> None:
    nodes = parse_sexpr('(kicad_pcb (version 20240108) (paper "A4"))')
    assert nodes[0][0] == "kicad_pcb"
    assert nodes[0][1] == ["version", "20240108"]
    assert nodes[0][2] == ["paper", "A4"]


def test_parse_sexpr_handles_escaped_strings() -> None:
    nodes = parse_sexpr(r'(say "hello \"world\"")')
    assert nodes[0][1] == 'hello "world"'


def test_parse_pcb_extracts_identity_fields() -> None:
    summary = parse_pcb(PCB_A)
    assert len(summary.footprints) == 1
    assert summary.footprints[0]["refdes"] == "U1"
    assert summary.footprints[0]["uuid"] == "fp-u1"
    assert len(summary.tracks) == 1
    assert summary.tracks[0]["uuid"] == "t-1"
    assert summary.tracks[0]["points_mm"] == [[0.0, 0.0], [10.0, 0.0]]


def test_diff_pcbs_detects_added_modified_unchanged() -> None:
    before = parse_pcb(PCB_A)
    after = parse_pcb(PCB_B)
    delta = diff_pcbs(before, after)
    # R1 is new.
    assert any(f["refdes"] == "R1" for f in delta["footprints"]["added"])
    # U1 changed value + position.
    mods = delta["footprints"]["modified"]
    assert any(m["uuid"] == "fp-u1" and "value" in m["changes"] for m in mods)
    # A new track t-2 was added.
    assert any(t["uuid"] == "t-2" for t in delta["tracks"]["added"])
    assert delta["tracks"]["removed"] == []


def test_diff_pcbs_empty_on_identical_input() -> None:
    summary = parse_pcb(PCB_A)
    delta = diff_pcbs(summary, parse_pcb(PCB_A))
    for section in delta.values():
        assert section["added"] == []
        assert section["removed"] == []
        assert section["modified"] == []


def test_main_outputs_json_and_exit_codes(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    a = tmp_path / "a.kicad_pcb"
    b = tmp_path / "b.kicad_pcb"
    a.write_text(PCB_A)
    b.write_text(PCB_B)
    rc = main([str(a), str(b)])
    captured = capsys.readouterr()
    assert rc == 1, "structural changes present"
    payload = json.loads(captured.out)
    assert payload["before"].endswith("a.kicad_pcb")
    assert any(t["uuid"] == "t-2" for t in payload["delta"]["tracks"]["added"])


def test_main_pr_mode_returns_zero_when_identical(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    a = tmp_path / "a.kicad_pcb"
    a.write_text(PCB_A)
    rc = main([str(a), str(a), "--pr", "--no-color"])
    out = capsys.readouterr().out
    assert rc == 0, "no changes between a file and itself"
    assert "no changes" in out


def test_main_rejects_missing_path(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    rc = main([str(tmp_path / "does-not-exist.kicad_pcb"), str(tmp_path / "also-missing.kicad_pcb")])
    assert rc == 2
    err = capsys.readouterr().err
    assert "not found" in err


@pytest.mark.asyncio
async def test_render_svg_diff_handles_missing_pcbdraw(tmp_path: Path) -> None:
    a = tmp_path / "a.kicad_pcb"
    b = tmp_path / "b.kicad_pcb"
    a.write_text(PCB_A)
    b.write_text(PCB_B)
    result = await render_svg_diff(a, b, tmp_path / "diff.svg")
    # On a dev box without pcbdraw on PATH the call must return a
    # graceful error rather than raising.
    assert isinstance(result, dict)
    assert "ok" in result and "error" in result
