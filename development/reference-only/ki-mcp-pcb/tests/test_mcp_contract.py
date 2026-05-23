"""MCP tool contract tests.

Hard rules (from CLAUDE.md):
  - MCP tools must be stateless — no hidden globals
  - return structured JSON, never free-form prose
  - every tool returns a JSON-serializable dict with documented keys

These tests exercise the tool *functions* directly (bypassing FastMCP)
so we don't need the ``mcp`` package installed.
"""

from __future__ import annotations

import inspect
import json
from pathlib import Path
from typing import Any

import pytest
from ki_mcp_pcb_server import tools as t

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


@pytest.mark.parametrize("tool_name,fn", list(t.ALL_TOOLS.items()))
def test_tool_is_callable_and_returns_dict(tool_name: str, fn: Any) -> None:
    sig = inspect.signature(fn)
    # Supply a stand-in for every required positional arg.
    args = []
    for param in sig.parameters.values():
        if param.default is inspect.Parameter.empty:
            ann = param.annotation
            if ann is str or ann == "str":
                args.append(str(EXAMPLES / "blinky.yaml"))
            elif ann is int or ann == "int":
                args.append(0)
            elif ann is list or "list" in str(ann):
                args.append([])
            else:
                args.append("")
    out = fn(*args)
    assert isinstance(out, dict), f"{tool_name} returned {type(out).__name__}, not dict"


@pytest.mark.parametrize("tool_name,fn", list(t.ALL_TOOLS.items()))
def test_tool_output_is_json_serializable(tool_name: str, fn: Any) -> None:
    sig = inspect.signature(fn)
    args = ["" if p.default is inspect.Parameter.empty else p.default
            for p in sig.parameters.values()]
    # Replace empty strings with the example path where it's likely a file arg.
    args = [str(EXAMPLES / "blinky.yaml") if a == "" else a for a in args]
    out = fn(*args)
    json.dumps(out)  # raises if non-serializable


def test_pcb_version_has_required_keys() -> None:
    out = t.tool_version()
    assert {"server_version", "core_version", "cir_version"} <= set(out)


def test_pcb_validate_cir_happy_path() -> None:
    out = t.tool_validate_cir(str(EXAMPLES / "blinky.yaml"))
    assert out.get("issues") == []
    # validate_board returns ValidationReport.model_dump(); ok is a property
    # not a model field, so it isn't in the dump. Use the issues list as the
    # ground truth instead.
    assert all(i.get("severity") != "error" for i in out["issues"])


def test_pcb_validate_cir_missing_file_returns_error_shape() -> None:
    out = t.tool_validate_cir("/nope/does-not-exist.yaml")
    assert out["ok"] is False
    assert any(i["code"] == "FS001" for i in out["issues"])


def test_pcb_validate_cir_wrong_extension_returns_error_shape(tmp_path: Path) -> None:
    bad = tmp_path / "x.unknown"
    bad.write_text("")
    out = t.tool_validate_cir(str(bad))
    assert out["ok"] is False
    assert any(i["code"] == "PARSE002" for i in out["issues"])


@pytest.mark.parametrize(
    "fn",
    # tool_synthesize, tool_build, tool_doctor, tool_parse_intent are real.
    # Remaining stubs:
    [t.tool_place, t.tool_route, t.tool_drc, t.tool_erc, t.tool_export_fab],
)
def test_m1_stubs_advertise_milestone(fn: Any) -> None:
    """Stubs must say 'this is M1' explicitly, never silently return success."""
    sig = inspect.signature(fn)
    args = [
        "" if p.default is inspect.Parameter.empty else p.default
        for p in sig.parameters.values()
    ]
    out = fn(*args)
    assert out["ok"] is False
    assert "milestone" in out
    assert isinstance(out["message"], str) and out["message"]


def test_tool_functions_are_pure() -> None:
    """Calling a tool twice with the same args must return equal output."""
    args = (str(EXAMPLES / "blinky.yaml"),)
    a = t.tool_validate_cir(*args)
    b = t.tool_validate_cir(*args)
    assert a == b


def test_tool_count_matches_documented_surface() -> None:
    """SPEC.md §5.3 declares a tool surface — guard against silent drift."""
    documented = {
        "pcb_version",
        "pcb_validate_cir",
        "pcb_parse_intent",
        "pcb_synthesize",
        "pcb_place",
        "pcb_route",
        "pcb_drc",
        "pcb_erc",
        "pcb_export_fab",
        "pcb_build",
        "pcb_doctor",
        "pcb_decoupling_check",
        "pcb_partition_check",
        "pcb_impedance_check",
        "pcb_return_path_check",
        "pcb_length_tuning",
        "pcb_ddr_topology_check",
        "pcb_bga_fanout_check",
        "pcb_diff",
        "pcb_autoplace",
    }
    assert set(t.ALL_TOOLS) == documented
