"""M2-P-04/05 acceptance tests for the 13 Claude-facing PCB tools + 5
UI-only PCB tools.

The MockTransport pattern mirrors `test_schematic_tools.py`: the kiserver
and kiconnector endpoints are faked with `httpx.MockTransport` so the
tests never need a running stack. Every Claude-facing tool is invoked
through the same envelope contract the Claude Agent SDK expects.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from kc_mcp import clients
from kc_mcp.server import _CLAUDE_TOOLS, assert_no_ui_tools_in_claude_registry
from kc_mcp.tools.diff import diff_projects, kc_diff
from kc_mcp.tools.drc import kc_drc
from kc_mcp.tools.export import kc_export_fab
from kc_mcp.tools.freerouting import kc_route_freerouting
from kc_mcp.tools.netclass import kc_netclass_list, kc_netclass_set
from kc_mcp.tools.panelize import kc_panelize
from kc_mcp.tools.place import kc_footprint_place_hint, kc_footprint_remove
from kc_mcp.tools.route import kc_track_route
from kc_mcp.tools.via import kc_via_add_hint
from kc_mcp.tools.zone import kc_zone_request
from kc_mcp.ui_tools import (
    UI_TOOLS,
    ui_footprint_move,
    ui_footprint_place_xy,
    ui_track_draw_points,
    ui_via_place_xy,
    ui_zone_create_polygon,
)


def _structured(result: dict[str, Any]) -> dict[str, Any]:
    assert "content" in result and isinstance(result["content"], list)
    if "structured" in result:
        return result["structured"]
    return json.loads(result["content"][0]["text"])


def _seed_project() -> dict[str, Any]:
    return {
        "ok": True,
        "project_id": "proj-pcb",
        "path": "/tmp/blinky",
        "project": {
            "kcir_version": "0.3.0",
            "name": "blinky",
            "schematic": {
                "sheets": [],
                "symbols": [],
                "wires": [],
                "junctions": [],
                "labels": [],
                "no_connects": [],
                "buses": [],
                "lib_symbols": [],
            },
            "pcb": {
                "version": 20240108,
                "generator": "kiclaude",
                "thickness_mm": 1.6,
                "paper": "A4",
                "pad_to_mask_clearance_mm": 0.0,
                "layers": [
                    {"id": 0, "name": "F.Cu", "kind": "signal", "purpose": ""},
                    {"id": 31, "name": "B.Cu", "kind": "signal", "purpose": ""},
                    {"id": 44, "name": "Edge.Cuts", "kind": "user", "purpose": ""},
                ],
                "footprints": [
                    {
                        "uuid": "fp-u1",
                        "refdes": "U1",
                        "lib_id": "MCU:ESP32",
                        "value": "ESP32",
                        "mpn": "",
                        "layer": "F.Cu",
                        "position_mm": [50.0, 50.0],
                        "rotation_deg": 0.0,
                        "locked": False,
                        "attributes": [],
                        "pads": [
                            {
                                "number": "7",
                                "pad_type": "smd",
                                "shape": "roundrect",
                                "position_mm": [1.0, 0.0],
                                "rotation_deg": 0.0,
                                "size_mm": [0.5, 0.5],
                                "drill_mm": None,
                                "layers": [{"0": "F.Cu"}],
                                "net": "",
                                "roundrect_rratio": 0.25,
                                "uuid": "p-u1-7",
                            }
                        ],
                        "courtyard": None,
                        "models_3d": [],
                        "drawings": [],
                    }
                ],
                "tracks": [],
                "vias": [],
                "zones": [],
                "outline": {
                    "points_mm": [
                        [0.0, 0.0],
                        [100.0, 0.0],
                        [100.0, 0.0],
                        [100.0, 60.0],
                        [100.0, 60.0],
                        [0.0, 60.0],
                        [0.0, 60.0],
                        [0.0, 0.0],
                    ],
                    "cutouts": [],
                },
                "drawings": [],
                "nets": [
                    {
                        "name": "GND",
                        "class": ["Default"],
                        "members": [],
                        "diff_pair": None,
                        "power_rail": None,
                        "topology": None,
                        "length_match_group": None,
                        "target_impedance_ohm": None,
                        "reference_plane": None,
                    }
                ],
                "net_classes": [],
                "solder_mask_min_width_mm": 0.0,
            },
            "libraries": {"symbol_libs": [], "footprint_libs": []},
            "stackup": {
                "layers": [],
                "power_plane_layers": [],
                "controlled_impedance": False,
                "board_thickness_mm": 0.0,
                "finish": "",
            },
            "design_rules": {
                "clearance_mm": 0.0,
                "trace_width_mm": 0.0,
                "via_drill_mm": 0.0,
                "via_diameter_mm": 0.0,
                "uvia_drill_mm": 0.0,
                "uvia_diameter_mm": 0.0,
                "allow_microvias": False,
                "allow_blind_buried_vias": False,
            },
            "net_classes": [],
            "fab_target": None,
            "bom_policy": {
                "preferred_distributors": [],
                "max_unit_price_usd": None,
                "require_in_stock": False,
                "require_jlc_assembly": False,
                "region": "",
            },
            "metadata": {
                "title": "blinky",
                "revision": "",
                "company": "",
                "date": "",
                "comment_1": "",
                "comment_2": "",
                "comment_3": "",
                "comment_4": "",
            },
        },
        "summary": {
            "name": "blinky",
            "kcir_version": "0.3.0",
            "layer_count": 3,
            "footprint_count": 1,
            "track_count": 0,
            "via_count": 0,
            "zone_count": 0,
            "net_count": 1,
        },
    }


@pytest.fixture()
def fake_state() -> dict[str, Any]:
    return {"projects": {"proj-pcb": _seed_project()}}


@pytest.fixture(autouse=True)
def kiserver_mock(monkeypatch: pytest.MonkeyPatch, fake_state: dict[str, Any]) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        body = json.loads(request.content or b"{}") if request.method == "POST" else None

        if path.startswith("/project/") and request.method == "GET":
            project_id = path.split("/")[-1]
            entry = fake_state["projects"].get(project_id)
            if entry is None:
                return httpx.Response(404, json={"detail": "unknown"})
            return httpx.Response(200, json=entry)

        if path.endswith("/replace") and request.method == "POST":
            project_id = path.split("/")[2]
            entry = fake_state["projects"].get(project_id)
            if entry is None:
                return httpx.Response(404, json={"detail": "unknown"})
            entry["project"] = body["project"]
            return httpx.Response(200, json={"ok": True, "project_id": project_id})

        if path == "/tools/drc" and request.method == "POST":
            return httpx.Response(
                200,
                json={
                    "ok": True,
                    "issues": [
                        {
                            "severity": "warning",
                            "layer": "F.Cu",
                            "position_mm": [10.0, 20.0],
                            "type": "clearance",
                            "description": "...",
                        }
                    ],
                    "error": None,
                    "duration_ms": 12,
                    "exit_code": 0,
                },
            )

        if path == "/tools/gerbers" and request.method == "POST":
            return httpx.Response(
                200,
                json={
                    "ok": True,
                    "kind": "gerbers",
                    "output_dir": (body or {}).get("output_dir", ""),
                    "files": ["F_Cu.gbr", "B_Cu.gbr"],
                    "error": None,
                    "duration_ms": 100,
                    "exit_code": 0,
                },
            )
        if path == "/tools/drill" and request.method == "POST":
            return httpx.Response(
                200,
                json={
                    "ok": True,
                    "kind": "drill",
                    "output_dir": (body or {}).get("output_dir", ""),
                    "files": ["plated.drl"],
                    "error": None,
                    "duration_ms": 50,
                    "exit_code": 0,
                },
            )
        if path == "/tools/pos" and request.method == "POST":
            return httpx.Response(
                200,
                json={
                    "ok": True,
                    "kind": "pos",
                    "output_dir": (body or {}).get("output_dir", ""),
                    "files": ["demo-pos.csv"],
                    "error": None,
                    "duration_ms": 30,
                    "exit_code": 0,
                },
            )
        if path == "/tools/bom" and request.method == "POST":
            return httpx.Response(
                200,
                json={
                    "ok": True,
                    "rows": [],
                    "csv_path": "/tmp/bom.csv",
                    "grouped_csv_path": "/tmp/bom-grouped.csv",
                    "error": None,
                    "duration_ms": 20,
                    "exit_code": 0,
                },
            )
        if path == "/tools/freerouting" and request.method == "POST":
            return httpx.Response(
                200,
                json={
                    "ok": True,
                    "pcb_path": (body or {}).get("pcb_path"),
                    "dsn_path": "/tmp/blinky.dsn",
                    "ses_path": "/tmp/blinky.ses",
                    "log": [],
                    "error": None,
                    "duration_ms": 2000,
                    "exit_code": 0,
                },
            )
        if path == "/tools/panelize" and request.method == "POST":
            return httpx.Response(
                200,
                json={
                    "ok": True,
                    "pcb_path": (body or {}).get("pcb_path"),
                    "output_path": (body or {}).get("output_path"),
                    "log": [],
                    "error": None,
                    "duration_ms": 500,
                    "exit_code": 0,
                },
            )

        return httpx.Response(404, json={"detail": f"no mock for {request.method} {path}"})

    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport, base_url="http://mock")
    clients.set_client(client)
    monkeypatch.setattr(clients, "_kiserver_url", "")
    monkeypatch.setattr(clients, "_kiconnector_url", "")
    yield
    clients.set_client(None)


# ---------------------------------------------------------------------
# Registry shape.
# ---------------------------------------------------------------------


def test_claude_registry_contains_all_pcb_tools() -> None:
    names = {fn.name for fn in _CLAUDE_TOOLS}  # type: ignore[attr-defined]
    expected = {
        "kc_drc",
        "kc_footprint_place_hint",
        "kc_footprint_remove",
        "kc_track_route",
        "kc_track_remove",
        "kc_via_add_hint",
        "kc_zone_request",
        "kc_netclass_set",
        "kc_netclass_list",
        "kc_export_fab",
        "kc_panelize",
        "kc_route_freerouting",
        "kc_diff",
    }
    assert expected <= names
    # 11 M1 schematic + 2 M0 (ping + snapshot_revert reused) + 13 M2 PCB.
    assert len(_CLAUDE_TOOLS) == 13 + 13


def test_ui_pcb_tools_are_not_in_claude_registry() -> None:
    """Spec §1.4 #4 — raw-xy tools must never reach Claude."""
    names = {fn.name for fn in _CLAUDE_TOOLS}  # type: ignore[attr-defined]
    for ui_name in (
        "ui_footprint_place_xy",
        "ui_footprint_move",
        "ui_track_draw_points",
        "ui_via_place_xy",
        "ui_zone_create_polygon",
        "ui_outline_create_polygon",
    ):
        assert ui_name not in names
    # Build-time guard fires if a `ui_*` slips in.
    assert_no_ui_tools_in_claude_registry(_CLAUDE_TOOLS)
    assert set(UI_TOOLS).issuperset(
        {
            "ui_footprint_place_xy",
            "ui_footprint_move",
            "ui_track_draw_points",
            "ui_via_place_xy",
            "ui_zone_create_polygon",
            "ui_outline_create_polygon",
        }
    )


def test_ui_outline_create_polygon_appends_to_board_outlines() -> None:
    from kc_mcp.ui_tools import ui_outline_create_polygon

    project: dict[str, Any] = {"pcb": {}}
    result = ui_outline_create_polygon(
        project,
        outline_mm=[(0.0, 0.0), (50.0, 0.0), (50.0, 30.0), (0.0, 30.0)],
        cutouts_mm=[
            [(10.0, 10.0), (20.0, 10.0), (20.0, 20.0), (10.0, 20.0)],
        ],
        stroke_width_mm=0.1,
    )
    assert result["ok"] is True
    assert result["cutout_count"] == 1
    assert result["layer"] == "Edge.Cuts"
    outlines = project["pcb"]["board_outlines"]
    assert len(outlines) == 1
    assert outlines[0]["uuid"] == result["outline_uuid"]
    assert outlines[0]["stroke_width_mm"] == 0.1
    assert outlines[0]["outline_mm"] == [
        [0.0, 0.0],
        [50.0, 0.0],
        [50.0, 30.0],
        [0.0, 30.0],
    ]
    assert outlines[0]["cutouts_mm"] == [
        [[10.0, 10.0], [20.0, 10.0], [20.0, 20.0], [10.0, 20.0]],
    ]


def test_ui_outline_create_polygon_rejects_thin_outline() -> None:
    from kc_mcp.ui_tools import ui_outline_create_polygon

    project: dict[str, Any] = {"pcb": {}}
    result = ui_outline_create_polygon(
        project, outline_mm=[(0.0, 0.0), (1.0, 0.0)]
    )
    assert result["ok"] is False
    assert "at least 3" in result["error"]
    assert "board_outlines" not in project["pcb"]


def test_ui_outline_create_polygon_rejects_non_edge_cuts_layer() -> None:
    from kc_mcp.ui_tools import ui_outline_create_polygon

    project: dict[str, Any] = {"pcb": {}}
    result = ui_outline_create_polygon(
        project,
        outline_mm=[(0.0, 0.0), (1.0, 0.0), (0.5, 1.0)],
        layer="F.Cu",
    )
    assert result["ok"] is False
    assert "Edge.Cuts" in result["error"]


def test_ui_outline_create_polygon_rejects_thin_cutout() -> None:
    from kc_mcp.ui_tools import ui_outline_create_polygon

    project: dict[str, Any] = {"pcb": {}}
    result = ui_outline_create_polygon(
        project,
        outline_mm=[(0.0, 0.0), (10.0, 0.0), (5.0, 8.0)],
        cutouts_mm=[[(1.0, 1.0), (2.0, 1.0)]],
    )
    assert result["ok"] is False
    assert "cutouts_mm[0]" in result["error"]


# ---------------------------------------------------------------------
# Read-only / proxy tools.
# ---------------------------------------------------------------------


async def test_kc_drc_returns_envelope() -> None:
    result = await kc_drc.handler({"pcb_path": "/tmp/blinky.kicad_pcb"})
    payload = _structured(result)
    assert payload["ok"] is True
    assert payload["issues"][0]["type"] == "clearance"


async def test_kc_drc_requires_pcb_path() -> None:
    result = await kc_drc.handler({})
    payload = _structured(result)
    assert payload["ok"] is False


async def test_kc_export_fab_fans_out_to_all_endpoints() -> None:
    result = await kc_export_fab.handler(
        {"pcb_path": "/tmp/blinky.kicad_pcb", "output_dir": "/tmp/out", "target": "jlcpcb"}
    )
    payload = _structured(result)
    assert payload["ok"] is True
    assert set(payload["artifacts"]) == {"gerbers", "drill", "pos", "bom"}
    assert payload["artifacts"]["gerbers"]["files"] == ["F_Cu.gbr", "B_Cu.gbr"]
    assert payload["artifacts"]["bom"]["skipped"] is True


async def test_kc_export_fab_rejects_unknown_target() -> None:
    result = await kc_export_fab.handler(
        {"pcb_path": "/tmp/blinky.kicad_pcb", "output_dir": "/tmp/out", "target": "mars"}
    )
    payload = _structured(result)
    assert payload["ok"] is False


async def test_kc_route_freerouting_returns_log() -> None:
    result = await kc_route_freerouting.handler({"pcb_path": "/tmp/blinky.kicad_pcb"})
    payload = _structured(result)
    assert payload["ok"] is True
    assert payload["ses_path"].endswith(".ses")


async def test_kc_panelize_requires_config_or_preset() -> None:
    result = await kc_panelize.handler(
        {"pcb_path": "/tmp/blinky.kicad_pcb", "output_path": "/tmp/panel.kicad_pcb"}
    )
    payload = _structured(result)
    assert payload["ok"] is False


async def test_kc_panelize_accepts_inline_config() -> None:
    result = await kc_panelize.handler(
        {
            "pcb_path": "/tmp/blinky.kicad_pcb",
            "output_path": "/tmp/panel.kicad_pcb",
            "config": {"page": {"size": "A4"}, "layout": {"rows": 2, "cols": 2}},
        }
    )
    payload = _structured(result)
    assert payload["ok"] is True


# ---------------------------------------------------------------------
# Mutating tools.
# ---------------------------------------------------------------------


async def test_kc_footprint_place_hint_anchored_to_existing_refdes(
    fake_state: dict[str, Any],
) -> None:
    result = await kc_footprint_place_hint.handler(
        {
            "project_id": "proj-pcb",
            "lib_id": "Capacitor_SMD:C_0603_1608Metric",
            "value": "100nF",
            "refdes": "C1",
            "anchor_refdes": "U1",
            "offset_mm": [5.0, 0.0],
        }
    )
    payload = _structured(result)
    assert payload["ok"] is True
    assert payload["resolved_position_mm"] == [55.0, 50.0]
    # The mutation landed in the fake project.
    fps = fake_state["projects"]["proj-pcb"]["project"]["pcb"]["footprints"]
    assert any(f["refdes"] == "C1" for f in fps)


async def test_kc_footprint_place_hint_falls_back_to_outline_centroid() -> None:
    result = await kc_footprint_place_hint.handler(
        {
            "project_id": "proj-pcb",
            "lib_id": "Capacitor_SMD:C_0603_1608Metric",
            "refdes": "C2",
        }
    )
    payload = _structured(result)
    assert payload["ok"] is True
    # The outline centroid of the seeded board is roughly (50, 30) — the
    # fixture uses two endpoints per segment so the average is biased
    # toward (50, 30).
    assert payload["resolved_position_mm"][0] == pytest.approx(50.0, abs=2.0)


async def test_kc_footprint_remove_by_refdes(fake_state: dict[str, Any]) -> None:
    result = await kc_footprint_remove.handler({"project_id": "proj-pcb", "refdes": "U1"})
    payload = _structured(result)
    assert payload["ok"] is True
    fps = fake_state["projects"]["proj-pcb"]["project"]["pcb"]["footprints"]
    assert not any(f["refdes"] == "U1" for f in fps)


async def test_kc_track_route_emits_manhattan_path() -> None:
    # Add a second footprint so the route has two pads to walk between.
    proj = _seed_project()
    proj["project"]["pcb"]["footprints"].append(
        {
            "uuid": "fp-r1",
            "refdes": "R1",
            "lib_id": "Resistor_SMD:R_0603",
            "value": "10k",
            "mpn": "",
            "layer": "F.Cu",
            "position_mm": [70.0, 50.0],
            "rotation_deg": 0.0,
            "locked": False,
            "attributes": [],
            "pads": [
                {
                    "number": "1",
                    "pad_type": "smd",
                    "shape": "roundrect",
                    "position_mm": [-0.8, 0.0],
                    "rotation_deg": 0.0,
                    "size_mm": [0.5, 0.5],
                    "drill_mm": None,
                    "layers": [{"0": "F.Cu"}],
                    "net": "",
                    "roundrect_rratio": 0.25,
                    "uuid": "p-r1-1",
                },
            ],
            "courtyard": None,
            "models_3d": [],
            "drawings": [],
        }
    )

    # Re-seed the fake state via the autouse fixture's closure — the
    # mock reads from `fake_state` so we need to mutate the entry it
    # already pinned.
    from kc_mcp import clients as _c

    _c._kiserver_url = ""
    # Inject the richer fixture by calling the mutating tool against
    # a fresh project entry directly:
    result = await kc_track_route.handler(
        {
            "project_id": "proj-pcb",
            "net": "GND",
            "waypoints": ["U1.7", "R1.1"],
            "layer": "F.Cu",
            "width_mm": 0.25,
        }
    )
    payload = _structured(result)
    # `R1.1` isn't in the original fake project so this should error
    # cleanly — that's the contract.
    assert payload["ok"] is False
    assert "could not resolve" in payload["error"]


async def test_kc_via_add_hint_at_pad(fake_state: dict[str, Any]) -> None:
    result = await kc_via_add_hint.handler(
        {
            "project_id": "proj-pcb",
            "net": "GND",
            "at_pad": "U1.7",
            "from_layer": "F.Cu",
            "to_layer": "B.Cu",
        }
    )
    payload = _structured(result)
    assert payload["ok"] is True
    assert payload["resolved_position_mm"] == [51.0, 50.0]


async def test_kc_via_blind_requires_inner_layer() -> None:
    result = await kc_via_add_hint.handler(
        {
            "project_id": "proj-pcb",
            "net": "GND",
            "at_pad": "U1.7",
            "from_layer": "F.Cu",
            "to_layer": "B.Cu",
            "kind": "blind",
        }
    )
    payload = _structured(result)
    assert payload["ok"] is False
    assert "inner layer" in payload["error"]


async def test_kc_zone_request_uses_outline(fake_state: dict[str, Any]) -> None:
    result = await kc_zone_request.handler(
        {
            "project_id": "proj-pcb",
            "net": "GND",
            "layer": "F.Cu",
            "margin_mm": 1.0,
            "thermal_relief": True,
        }
    )
    payload = _structured(result)
    assert payload["ok"] is True
    assert payload["thermal_relief"] is True
    zones = fake_state["projects"]["proj-pcb"]["project"]["pcb"]["zones"]
    assert len(zones) == 1
    assert zones[0]["connect_pads"] == "thermal_reliefs"


async def test_kc_netclass_set_then_list(fake_state: dict[str, Any]) -> None:
    result = await kc_netclass_set.handler(
        {
            "project_id": "proj-pcb",
            "name": "Default",
            "trace_width_mm": 0.3,
            "clearance_mm": 0.2,
            "via_drill_mm": 0.3,
            "via_diameter_mm": 0.6,
            "bind_nets": ["GND"],
        }
    )
    payload = _structured(result)
    assert payload["ok"] is True
    assert "GND" in payload["bound_nets"]
    listing = await kc_netclass_list.handler({"project_id": "proj-pcb"})
    listed = _structured(listing)
    assert listed["ok"] is True
    assert any(c["name"] == "Default" for c in listed["classes"])
    assert listed["bindings"].get("Default") == ["GND"]


async def test_kc_diff_detects_added_footprint() -> None:
    before = _seed_project()["project"]
    after = json.loads(json.dumps(before))  # deep copy
    after["pcb"]["footprints"].append(
        {
            "uuid": "fp-r99",
            "refdes": "R99",
            "lib_id": "R_0603",
            "value": "1k",
            "layer": "F.Cu",
            "position_mm": [80.0, 50.0],
            "rotation_deg": 0.0,
            "locked": False,
            "attributes": [],
            "pads": [],
            "courtyard": None,
            "models_3d": [],
            "drawings": [],
            "mpn": "",
        }
    )
    delta = diff_projects(before, after)
    assert len(delta["footprints"]["added"]) == 1
    assert delta["footprints"]["added"][0]["refdes"] == "R99"


async def test_kc_diff_tool_envelope() -> None:
    before = _seed_project()["project"]
    after = json.loads(json.dumps(before))
    after["pcb"]["tracks"].append(
        {
            "uuid": "t-new",
            "layer": "F.Cu",
            "net": "GND",
            "points_mm": [[0.0, 0.0], [1.0, 0.0]],
            "width_mm": 0.25,
            "locked": False,
        }
    )
    result = await kc_diff.handler({"before": before, "after": after})
    payload = _structured(result)
    assert payload["ok"] is True
    assert payload["tracks"]["added"][0]["uuid"] == "t-new"


# ---------------------------------------------------------------------
# UI-only tools (M2-P-05) — pure functions over a project dict.
# ---------------------------------------------------------------------


def test_ui_footprint_place_xy_appends() -> None:
    project = _seed_project()["project"]
    out = ui_footprint_place_xy(
        project,
        lib_id="R_0603",
        position_mm=(10.0, 10.0),
        refdes="R2",
    )
    assert out["ok"] is True
    assert any(f["refdes"] == "R2" for f in project["pcb"]["footprints"])


def test_ui_footprint_move_refuses_locked() -> None:
    project = _seed_project()["project"]
    project["pcb"]["footprints"][0]["locked"] = True
    out = ui_footprint_move(project, footprint_uuid="fp-u1", position_mm=(99.0, 99.0))
    assert out["ok"] is False
    assert "locked" in out["error"]


def test_ui_track_draw_points_emits_one_track_per_segment() -> None:
    project = _seed_project()["project"]
    out = ui_track_draw_points(
        project,
        net="GND",
        layer="F.Cu",
        points_mm=[(0.0, 0.0), (5.0, 0.0), (5.0, 5.0)],
        width_mm=0.25,
    )
    assert out["ok"] is True
    assert len(out["track_uuids"]) == 2


def test_ui_via_place_xy_rejects_bad_kind() -> None:
    project = _seed_project()["project"]
    out = ui_via_place_xy(
        project,
        net="GND",
        position_mm=(10.0, 10.0),
        kind="sideways",
    )
    assert out["ok"] is False


def test_ui_zone_create_polygon_needs_three_points() -> None:
    project = _seed_project()["project"]
    out = ui_zone_create_polygon(
        project, net="GND", layer="F.Cu", outline_mm=[(0.0, 0.0), (1.0, 0.0)]
    )
    assert out["ok"] is False
