"""Integration tests for the M3 high-speed / sourcing MCP tools (T6):
kc_decoupling_check, kc_partition_check, kc_impedance_check,
kc_diffpair_declare, kc_length_match_set, kc_bom_get, kc_export_step,
kc_session_fork.

A self-contained `httpx.MockTransport` stands in for kiserver +
kiconnector and captures POST bodies so mutations can be asserted.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from kc_mcp import clients
from kc_mcp.tools.bom import kc_bom_get
from kc_mcp.tools.export import kc_export_step
from kc_mcp.tools.highspeed import (
    kc_decoupling_check,
    kc_diffpair_declare,
    kc_impedance_check,
    kc_length_match_set,
    kc_partition_check,
)
from kc_mcp.tools.session import kc_session_fork


def _payload(result: dict[str, Any]) -> dict[str, Any]:
    """Unwrap a kc_* tool's MCP envelope into the structured payload."""
    return json.loads(result["content"][0]["text"])


def _seed_project() -> dict[str, Any]:
    return {
        "schematic": {"sheets": [], "symbols": [], "labels": []},
        "design_rules": {"via_diameter_mm": 0.45, "clearance_mm": 0.1, "trace_width_mm": 0.1},
        "net_classes": [{"name": "Sig", "track_width_mm": 0.25}],
        "stackup": {
            "layers": [
                {"kind": "copper", "name": "F.Cu"},
                {"kind": "dielectric", "dielectric_constant": 4.5, "thickness_mm": 0.2},
                {"kind": "copper", "name": "B.Cu"},
            ]
        },
        "pcb": {
            "nets": [
                {"name": "+3V3"},
                {"name": "GND"},
                {"name": "AGND"},
                {"name": "DGND"},
                {"name": "USB_D+"},
                {"name": "USB_D-"},
                {"name": "CLK", "class": "Sig", "target_impedance_ohm": 50.0},
            ],
            "footprints": [
                {"refdes": "U1", "uuid": "u1", "mpn": "STM32G031", "value": "STM32",
                 "lib_id": "MCU:STM32", "pads": [{"net": "+3V3"}, {"net": "GND"}]},
                {"refdes": "FB1", "uuid": "fb1", "mpn": "BLM", "value": "FB",
                 "lib_id": "L:0402", "pads": [{"net": "AGND"}, {"net": "DGND"}]},
                {"refdes": "R9", "uuid": "r9", "mpn": "", "value": "0R",
                 "lib_id": "R:0402", "pads": [{"net": "AGND"}, {"net": "DGND"}]},
                {"refdes": "C1", "uuid": "c1", "mpn": "", "value": "100nF",
                 "lib_id": "C:0402", "pads": [{"net": "GND"}]},
            ],
            "length_groups": [{"name": "DDR0", "nets": ["DQ0", "DQ1"], "tolerance_mm": 0.5}],
            "diff_pairs": [],
            "signoff": {},
        },
    }


@pytest.fixture()
def mock(monkeypatch: pytest.MonkeyPatch):
    state = {"project": _seed_project()}
    posted: list[tuple[str, dict[str, Any]]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        body = json.loads(request.content or b"{}") if request.method == "POST" else {}
        if request.method == "POST":
            posted.append((path, body))
        if path.startswith("/project/") and request.method == "GET":
            return httpx.Response(200, json={"ok": True, "project": state["project"]})
        if path.endswith("/replace") and request.method == "POST":
            state["project"] = body["project"]
            return httpx.Response(200, json={"ok": True})
        if path.endswith("/session/fork") and request.method == "POST":
            return httpx.Response(
                200,
                json={
                    "ok": True,
                    "new_session_id": "fork-123",
                    "forked_from": body.get("parent_session_id"),
                },
            )
        if path == "/tools/step" and request.method == "POST":
            return httpx.Response(200, json={"ok": True, "step": "/out/board.step"})
        return httpx.Response(404, json={"detail": f"no mock for {request.method} {path}"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://mock")
    clients.set_client(client)
    monkeypatch.setattr(clients, "_kiserver_url", "")
    monkeypatch.setattr(clients, "_kiconnector_url", "")
    yield {"state": state, "posted": posted}
    clients.set_client(None)


async def test_decoupling_check_flags_unbypassed_ic(mock) -> None:  # type: ignore[no-untyped-def]
    out = _payload(await kc_decoupling_check.handler({"project_id": "p1"}))
    assert out["ok"] is True
    assert any(f["code"] == "KC020" for f in out["missing"])


async def test_partition_check_flags_double_ground_bridge(mock) -> None:  # type: ignore[no-untyped-def]
    out = _payload(await kc_partition_check.handler({"project_id": "p1"}))
    assert out["ok"] is True
    assert any(f["code"] == "KC050" for f in out["violations"])


async def test_impedance_check_returns_per_net_result(mock) -> None:  # type: ignore[no-untyped-def]
    out = _payload(await kc_impedance_check.handler({"project_id": "p1"}))
    assert out["ok"] is True
    clk = next(r for r in out["results"] if r["net"] == "CLK")
    assert clk["target_ohm"] == 50.0
    assert clk["achieved_ohm"] is not None
    assert clk["status"] in {"ok", "warning", "error"}


async def test_diffpair_declare_mutates_and_persists(mock) -> None:  # type: ignore[no-untyped-def]
    out = _payload(
        await kc_diffpair_declare.handler(
            {
                "project_id": "p1",
                "net_a": "USB_D+",
                "net_b": "USB_D-",
                "target_impedance": 90.0,
                "length_match_group": "USB",
            }
        )
    )
    assert out["ok"] is True
    replace = [b for (p, b) in mock["posted"] if p.endswith("/replace")]
    assert replace, "expected a /replace persist call"
    pairs = replace[-1]["project"]["pcb"]["diff_pairs"]
    assert any(dp["net_positive"] == "USB_D+" and dp["net_negative"] == "USB_D-" for dp in pairs)


async def test_diffpair_declare_unknown_net_errors(mock) -> None:  # type: ignore[no-untyped-def]
    out = _payload(
        await kc_diffpair_declare.handler(
            {"project_id": "p1", "net_a": "USB_D+", "net_b": "NOPE", "target_impedance": 90.0}
        )
    )
    assert out["ok"] is False
    assert not any(p.endswith("/replace") for (p, _) in mock["posted"])


async def test_length_match_set_on_existing_group_persists(mock) -> None:  # type: ignore[no-untyped-def]
    out = _payload(
        await kc_length_match_set.handler(
            {"project_id": "p1", "group": "DDR0", "tolerance_mm": 0.25}
        )
    )
    assert out["ok"] is True
    replace = [b for (p, b) in mock["posted"] if p.endswith("/replace")]
    grp = next(g for g in replace[-1]["project"]["pcb"]["length_groups"] if g["name"] == "DDR0")
    assert grp["tolerance_mm"] == 0.25


async def test_length_match_set_missing_group_errors(mock) -> None:  # type: ignore[no-untyped-def]
    out = _payload(
        await kc_length_match_set.handler(
            {"project_id": "p1", "group": "DOES_NOT_EXIST", "tolerance_mm": 0.25}
        )
    )
    assert out["ok"] is False  # can't set tolerance on a group that isn't declared


async def test_bom_get_groups_by_mpn(mock) -> None:  # type: ignore[no-untyped-def]
    out = _payload(await kc_bom_get.handler({"project_id": "p1"}))
    assert out["ok"] is True
    u1 = next(line for line in out["lines"] if line["mpn"] == "STM32G031")
    assert u1["qty"] == 1 and u1["refdes"] == ["U1"] and u1["sourced"] is True
    assert out["placement_count"] == 4


async def test_export_step_calls_kiconnector(mock) -> None:  # type: ignore[no-untyped-def]
    out = _payload(
        await kc_export_step.handler({"pcb_path": "/p/board.kicad_pcb", "output_dir": "/out"})
    )
    assert out["ok"] is True
    assert any(p == "/tools/step" for (p, _) in mock["posted"])


async def test_session_fork_returns_new_id(mock) -> None:  # type: ignore[no-untyped-def]
    out = _payload(
        await kc_session_fork.handler(
            {"project_id": "p1", "session_id": "parent-sess", "label": "what-if"}
        )
    )
    assert out["ok"] is True
    assert out["new_session_id"] == "fork-123"
    assert out["parent_session_id"] == "parent-sess"


def test_eight_tools_registered() -> None:
    from kc_mcp.server import _CLAUDE_TOOLS

    names = {getattr(t, "name", None) for t in _CLAUDE_TOOLS}
    assert {
        "kc_decoupling_check",
        "kc_partition_check",
        "kc_impedance_check",
        "kc_diffpair_declare",
        "kc_length_match_set",
        "kc_bom_get",
        "kc_export_step",
        "kc_session_fork",
    } <= names
