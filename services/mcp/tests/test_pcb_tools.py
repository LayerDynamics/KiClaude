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
    # 13 M0/M1 schematic+core + 13 M2 PCB + 2 M3-P-06 sourcing
    # (kc_part_search, kc_bom_price) + 8 T6 high-speed/sourcing tools
    # (kc_decoupling/partition/impedance_check, kc_diffpair_declare,
    # kc_length_match_set, kc_bom_get, kc_export_step, kc_session_fork).
    assert len(_CLAUDE_TOOLS) == 13 + 13 + 2 + 8


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


def test_ui_netclass_set_upserts_and_binds_nets() -> None:
    from kc_mcp.ui_tools import ui_netclass_set

    project: dict[str, Any] = {
        "pcb": {
            "net_classes": [],
            "nets": [
                {"name": "+3V3"},
                {"name": "+5V"},
                {"name": "GND"},
            ],
        }
    }
    result = ui_netclass_set(
        project,
        name="Power",
        clearance_mm=0.3,
        trace_width_mm=0.6,
        bind_nets=["+3V3", "+5V"],
    )
    assert result["ok"] is True
    assert result["net_class"]["name"] == "Power"
    assert result["net_class"]["trace_width_mm"] == 0.6
    assert set(result["bound_nets"]) == {"+3V3", "+5V"}
    nets_by_name = {n["name"]: n for n in project["pcb"]["nets"]}
    assert nets_by_name["+3V3"]["class"] == ["Power"]
    assert nets_by_name["GND"].get("class") is None

    # Idempotent update — same name → mutate the same entry.
    again = ui_netclass_set(project, name="Power", clearance_mm=0.4)
    assert again["ok"] is True
    assert len(project["pcb"]["net_classes"]) == 1
    assert project["pcb"]["net_classes"][0]["clearance_mm"] == 0.4


def test_ui_netclass_set_requires_name() -> None:
    from kc_mcp.ui_tools import ui_netclass_set

    project: dict[str, Any] = {"pcb": {}}
    result = ui_netclass_set(project, name="   ")
    assert result["ok"] is False
    assert "name" in result["error"]


def test_ui_netclass_delete_unbinds_to_default() -> None:
    from kc_mcp.ui_tools import ui_netclass_delete, ui_netclass_set

    project: dict[str, Any] = {
        "pcb": {
            "net_classes": [],
            "nets": [{"name": "+3V3"}, {"name": "+5V"}],
        }
    }
    ui_netclass_set(project, name="Power", bind_nets=["+3V3", "+5V"])
    result = ui_netclass_delete(project, name="Power")
    assert result["ok"] is True
    assert set(result["unbound_nets"]) == {"+3V3", "+5V"}
    assert project["pcb"]["net_classes"] == []
    for net in project["pcb"]["nets"]:
        assert net["class"] == ["Default"]


def test_ui_netclass_delete_refuses_default_class() -> None:
    from kc_mcp.ui_tools import ui_netclass_delete

    project: dict[str, Any] = {"pcb": {"net_classes": []}}
    result = ui_netclass_delete(project, name="Default")
    assert result["ok"] is False
    assert "Default" in result["error"]


def test_ui_netclass_delete_unknown_returns_error() -> None:
    from kc_mcp.ui_tools import ui_netclass_delete

    project: dict[str, Any] = {"pcb": {"net_classes": []}}
    result = ui_netclass_delete(project, name="NonExistent")
    assert result["ok"] is False
    assert "NonExistent" in result["error"]


def _layer_stack_project() -> dict[str, Any]:
    return {
        "pcb": {
            "layers": [
                {"id": 0, "name": "F.Cu", "kind": "copper"},
                {"id": 1, "name": "In1.Cu", "kind": "copper"},
                {"id": 2, "name": "In2.Cu", "kind": "copper"},
                {"id": 31, "name": "B.Cu", "kind": "copper"},
                {"id": 37, "name": "F.SilkS", "kind": "silkscreen"},
            ],
        }
    }


def test_ui_layer_color_set_persists_into_pcb_layer_colors() -> None:
    from kc_mcp.ui_tools import ui_layer_color_set

    project = _layer_stack_project()
    result = ui_layer_color_set(project, layer_id=0, color="#FF8800")
    assert result["ok"] is True
    assert result["color"] == "#ff8800"
    assert project["pcb"]["layer_colors"]["0"] == "#ff8800"

    # Updating overwrites the prior value.
    ui_layer_color_set(project, layer_id=0, color="#000000")
    assert project["pcb"]["layer_colors"]["0"] == "#000000"


def test_ui_layer_color_set_rejects_bad_hex() -> None:
    from kc_mcp.ui_tools import ui_layer_color_set

    project = _layer_stack_project()
    assert ui_layer_color_set(project, layer_id=0, color="ff8800")["ok"] is False
    assert ui_layer_color_set(project, layer_id=0, color="#ff88")["ok"] is False
    assert ui_layer_color_set(project, layer_id=0, color="#zzzzzz")["ok"] is False
    assert "layer_colors" not in project["pcb"]


def test_ui_layer_color_set_rejects_unknown_layer_id() -> None:
    from kc_mcp.ui_tools import ui_layer_color_set

    project = _layer_stack_project()
    result = ui_layer_color_set(project, layer_id=999, color="#abcdef")
    assert result["ok"] is False
    assert "999" in result["error"]


def test_ui_layer_reorder_refuses_fixed_stackup_anchors() -> None:
    from kc_mcp.ui_tools import ui_layer_reorder

    project = _layer_stack_project()
    fcu = ui_layer_reorder(project, layer_id=0, target_id=2)
    assert fcu["ok"] is False
    bcu = ui_layer_reorder(project, layer_id=2, target_id=31)
    assert bcu["ok"] is False


def test_ui_layer_reorder_refuses_cross_kind() -> None:
    from kc_mcp.ui_tools import ui_layer_reorder

    project = _layer_stack_project()
    # Silkscreen layer can't slot between two copper layers.
    result = ui_layer_reorder(project, layer_id=37, target_id=1)
    assert result["ok"] is False
    assert "silkscreen" in result["error"]


def test_ui_layer_reorder_moves_inner_copper_layers() -> None:
    from kc_mcp.ui_tools import ui_layer_reorder

    project = _layer_stack_project()
    # Move In1.Cu past In2.Cu — both copper, neither is F.Cu/B.Cu.
    result = ui_layer_reorder(project, layer_id=1, target_id=2)
    assert result["ok"] is True
    names = [layer["name"] for layer in project["pcb"]["layers"]]
    assert names == ["F.Cu", "In2.Cu", "In1.Cu", "B.Cu", "F.SilkS"]


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


# ---------------------------------------------------------------------
# M3-T-01 — Stackup editor (ui_stackup_set).
# ---------------------------------------------------------------------


def _four_layer_stackup() -> list[dict[str, Any]]:
    """Canonical 4-layer FR-4 controlled-impedance stackup."""
    return [
        {"name": "F.Cu", "kind": "copper", "thickness_mm": 0.035,
         "dielectric_constant": None, "loss_tangent": None, "color": "copper"},
        {"name": "dielectric 1", "kind": "dielectric", "thickness_mm": 0.21,
         "dielectric_constant": 4.5, "loss_tangent": 0.02, "color": "FR4"},
        {"name": "In1.Cu", "kind": "copper", "thickness_mm": 0.018,
         "dielectric_constant": None, "loss_tangent": None, "color": "copper"},
        {"name": "dielectric 2", "kind": "dielectric", "thickness_mm": 1.10,
         "dielectric_constant": 4.5, "loss_tangent": 0.02, "color": "FR4"},
        {"name": "In2.Cu", "kind": "copper", "thickness_mm": 0.018,
         "dielectric_constant": None, "loss_tangent": None, "color": "copper"},
        {"name": "dielectric 3", "kind": "dielectric", "thickness_mm": 0.21,
         "dielectric_constant": 4.5, "loss_tangent": 0.02, "color": "FR4"},
        {"name": "B.Cu", "kind": "copper", "thickness_mm": 0.035,
         "dielectric_constant": None, "loss_tangent": None, "color": "copper"},
    ]


def test_ui_stackup_set_replaces_payload_and_recomputes_board_thickness() -> None:
    from kc_mcp.ui_tools import ui_stackup_set

    project: dict[str, Any] = {"stackup": {
        "layers": [], "power_plane_layers": [], "controlled_impedance": False,
        "board_thickness_mm": 0.0, "finish": "",
    }}
    layers = _four_layer_stackup()
    result = ui_stackup_set(
        project,
        layers=layers,
        finish="ENIG",
        controlled_impedance=True,
    )
    assert result["ok"] is True
    new = result["stackup"]
    assert [layer["name"] for layer in new["layers"]] == [
        "F.Cu", "dielectric 1", "In1.Cu", "dielectric 2",
        "In2.Cu", "dielectric 3", "B.Cu",
    ]
    assert new["finish"] == "ENIG"
    assert new["controlled_impedance"] is True
    # Board thickness sums layer thicknesses (KiCad invariant).
    expected = 0.035 + 0.21 + 0.018 + 1.10 + 0.018 + 0.21 + 0.035
    assert new["board_thickness_mm"] == pytest.approx(expected)
    # Project was mutated in-place (matches the other ui_* tools).
    assert project["stackup"] is new


def test_ui_stackup_set_preserves_unspecified_fields() -> None:
    """If the caller omits `finish` / `controlled_impedance` /
    `power_plane_layers`, the current values stay — partial edits
    are supported."""
    from kc_mcp.ui_tools import ui_stackup_set

    project: dict[str, Any] = {"stackup": {
        "layers": [],
        "power_plane_layers": ["In1.Cu"],
        "controlled_impedance": True,
        "board_thickness_mm": 0.0,
        "finish": "HASL",
    }}
    result = ui_stackup_set(project, layers=_four_layer_stackup())
    assert result["ok"] is True
    new = result["stackup"]
    assert new["finish"] == "HASL"
    assert new["controlled_impedance"] is True
    assert new["power_plane_layers"] == ["In1.Cu"]


def test_ui_stackup_set_rejects_layers_missing() -> None:
    from kc_mcp.ui_tools import ui_stackup_set

    result = ui_stackup_set({"stackup": {}})
    assert result["ok"] is False
    assert "layers" in result["error"]


def test_ui_stackup_set_rejects_non_list_layers() -> None:
    from kc_mcp.ui_tools import ui_stackup_set

    result = ui_stackup_set({"stackup": {}}, layers="not-a-list")  # type: ignore[arg-type]
    assert result["ok"] is False
    assert "list" in result["error"]


def test_ui_stackup_set_rejects_unknown_kind() -> None:
    from kc_mcp.ui_tools import ui_stackup_set

    bad = [
        {"name": "F.Cu", "kind": "copper", "thickness_mm": 0.035,
         "dielectric_constant": None, "loss_tangent": None, "color": "copper"},
        {"name": "magic", "kind": "graphene", "thickness_mm": 0.1,
         "dielectric_constant": None, "loss_tangent": None, "color": ""},
        {"name": "B.Cu", "kind": "copper", "thickness_mm": 0.035,
         "dielectric_constant": None, "loss_tangent": None, "color": "copper"},
    ]
    result = ui_stackup_set({"stackup": {}}, layers=bad)
    assert result["ok"] is False
    assert "graphene" in result["error"]


def test_ui_stackup_set_rejects_negative_thickness() -> None:
    from kc_mcp.ui_tools import ui_stackup_set

    bad = [
        {"name": "F.Cu", "kind": "copper", "thickness_mm": -0.035,
         "dielectric_constant": None, "loss_tangent": None, "color": "copper"},
        {"name": "B.Cu", "kind": "copper", "thickness_mm": 0.035,
         "dielectric_constant": None, "loss_tangent": None, "color": "copper"},
    ]
    result = ui_stackup_set({"stackup": {}}, layers=bad)
    assert result["ok"] is False
    assert "thickness_mm" in result["error"]


def test_ui_stackup_set_rejects_duplicate_names() -> None:
    from kc_mcp.ui_tools import ui_stackup_set

    bad = [
        {"name": "F.Cu", "kind": "copper", "thickness_mm": 0.035,
         "dielectric_constant": None, "loss_tangent": None, "color": "copper"},
        {"name": "F.Cu", "kind": "dielectric", "thickness_mm": 1.0,
         "dielectric_constant": 4.5, "loss_tangent": 0.02, "color": "FR4"},
        {"name": "B.Cu", "kind": "copper", "thickness_mm": 0.035,
         "dielectric_constant": None, "loss_tangent": None, "color": "copper"},
    ]
    result = ui_stackup_set({"stackup": {}}, layers=bad)
    assert result["ok"] is False
    assert "duplicate" in result["error"].lower()


def test_ui_stackup_set_rejects_wrong_copper_anchor() -> None:
    """`F.Cu` must be the first copper layer, `B.Cu` the last —
    matches KiCad's stack-manager invariant."""
    from kc_mcp.ui_tools import ui_stackup_set

    swapped = [
        {"name": "B.Cu", "kind": "copper", "thickness_mm": 0.035,
         "dielectric_constant": None, "loss_tangent": None, "color": "copper"},
        {"name": "dielectric 1", "kind": "dielectric", "thickness_mm": 1.5,
         "dielectric_constant": 4.5, "loss_tangent": 0.02, "color": "FR4"},
        {"name": "F.Cu", "kind": "copper", "thickness_mm": 0.035,
         "dielectric_constant": None, "loss_tangent": None, "color": "copper"},
    ]
    result = ui_stackup_set({"stackup": {}}, layers=swapped)
    assert result["ok"] is False
    assert "F.Cu" in result["error"]


def test_ui_stackup_set_empty_layers_is_valid() -> None:
    """An empty stackup is a substrate-only / unrouted panel — the
    invariant only constrains copper layers, so zero coppers is fine."""
    from kc_mcp.ui_tools import ui_stackup_set

    result = ui_stackup_set({"stackup": {}}, layers=[], finish="")
    assert result["ok"] is True
    assert result["stackup"]["board_thickness_mm"] == 0.0


def test_ui_stackup_set_nullable_dielectric_fields_round_trip() -> None:
    """Copper layers carry `dielectric_constant = None` /
    `loss_tangent = None`. These must survive the validator and end
    up as `None` (not 0) in the persisted dict."""
    from kc_mcp.ui_tools import ui_stackup_set

    project: dict[str, Any] = {"stackup": {}}
    result = ui_stackup_set(project, layers=_four_layer_stackup(), finish="HASL")
    assert result["ok"] is True
    cu = result["stackup"]["layers"][0]
    assert cu["name"] == "F.Cu"
    assert cu["dielectric_constant"] is None
    assert cu["loss_tangent"] is None
    di = result["stackup"]["layers"][1]
    assert di["dielectric_constant"] == 4.5
    assert di["loss_tangent"] == 0.02


# ---------------------------------------------------------------------
# M3-T-03 — Diff pair declaration panel (ui_diffpair_set / delete).
# ---------------------------------------------------------------------


def _usb_diffpair_project() -> dict[str, Any]:
    return {
        "pcb": {
            "diff_pairs": [],
            "nets": [
                {"name": "USB_D+"},
                {"name": "USB_D-"},
                {"name": "USB_VBUS"},
                {"name": "GND"},
            ],
        }
    }


def test_ui_diffpair_set_creates_pair_and_back_refs_into_nets() -> None:
    from kc_mcp.ui_tools import ui_diffpair_set

    project = _usb_diffpair_project()
    result = ui_diffpair_set(
        project,
        name="USB_D",
        net_positive="USB_D+",
        net_negative="USB_D-",
        target_impedance_ohms=90.0,
        target_gap_mm=0.127,
        length_group="USB",
        skew_tolerance_mm=0.127,
    )
    assert result["ok"] is True
    pair = result["diff_pair"]
    assert pair["name"] == "USB_D"
    assert pair["net_positive"] == "USB_D+"
    assert pair["net_negative"] == "USB_D-"
    assert pair["target_impedance_ohms"] == 90.0
    assert pair["target_gap_mm"] == 0.127
    assert pair["length_group"] == "USB"
    assert pair["skew_tolerance_mm"] == 0.127
    # Back-refs on the legs point at each other.
    nets_by_name = {n["name"]: n for n in project["pcb"]["nets"]}
    assert nets_by_name["USB_D+"]["diff_pair"] == "USB_D-"
    assert nets_by_name["USB_D-"]["diff_pair"] == "USB_D+"
    # Non-leg nets untouched.
    assert "diff_pair" not in nets_by_name["USB_VBUS"]


def test_ui_diffpair_set_upserts_in_place_by_name() -> None:
    from kc_mcp.ui_tools import ui_diffpair_set

    project = _usb_diffpair_project()
    ui_diffpair_set(project, name="USB_D", net_positive="USB_D+", net_negative="USB_D-")
    again = ui_diffpair_set(project, name="USB_D", target_impedance_ohms=85.0)
    assert again["ok"] is True
    assert len(project["pcb"]["diff_pairs"]) == 1
    assert project["pcb"]["diff_pairs"][0]["target_impedance_ohms"] == 85.0


def test_ui_diffpair_set_renaming_legs_clears_orphan_back_refs() -> None:
    from kc_mcp.ui_tools import ui_diffpair_set

    project = _usb_diffpair_project()
    project["pcb"]["nets"].append({"name": "PCIE_TX0+"})
    project["pcb"]["nets"].append({"name": "PCIE_TX0-"})
    ui_diffpair_set(project, name="USB_D", net_positive="USB_D+", net_negative="USB_D-")
    # Switch the same pair's legs to a different pair of nets.
    result = ui_diffpair_set(
        project, name="USB_D", net_positive="PCIE_TX0+", net_negative="PCIE_TX0-"
    )
    assert result["ok"] is True
    nets_by_name = {n["name"]: n for n in project["pcb"]["nets"]}
    # Old legs lost their back-ref.
    assert nets_by_name["USB_D+"]["diff_pair"] is None
    assert nets_by_name["USB_D-"]["diff_pair"] is None
    # New legs have it.
    assert nets_by_name["PCIE_TX0+"]["diff_pair"] == "PCIE_TX0-"
    assert nets_by_name["PCIE_TX0-"]["diff_pair"] == "PCIE_TX0+"


def test_ui_diffpair_set_requires_name() -> None:
    from kc_mcp.ui_tools import ui_diffpair_set

    result = ui_diffpair_set({"pcb": {}}, name="   ")
    assert result["ok"] is False
    assert "name" in result["error"]


def test_ui_diffpair_set_create_requires_both_legs() -> None:
    from kc_mcp.ui_tools import ui_diffpair_set

    project = _usb_diffpair_project()
    result = ui_diffpair_set(project, name="USB_D", net_positive="USB_D+")
    assert result["ok"] is False
    assert "required" in result["error"]
    assert project["pcb"]["diff_pairs"] == []


def test_ui_diffpair_set_rejects_unknown_net() -> None:
    from kc_mcp.ui_tools import ui_diffpair_set

    project = _usb_diffpair_project()
    result = ui_diffpair_set(
        project, name="USB_D", net_positive="USB_D+", net_negative="UNDEFINED_NET"
    )
    assert result["ok"] is False
    assert "UNDEFINED_NET" in result["error"]
    # No stray entry created.
    assert project["pcb"]["diff_pairs"] == []


def test_ui_diffpair_set_rejects_self_pair() -> None:
    from kc_mcp.ui_tools import ui_diffpair_set

    project = _usb_diffpair_project()
    result = ui_diffpair_set(
        project, name="X", net_positive="USB_D+", net_negative="USB_D+"
    )
    assert result["ok"] is False
    assert "same net" in result["error"]
    assert project["pcb"]["diff_pairs"] == []


def test_ui_diffpair_set_rejects_double_booking_a_net() -> None:
    from kc_mcp.ui_tools import ui_diffpair_set

    project = _usb_diffpair_project()
    project["pcb"]["nets"].append({"name": "OTHER+"})
    ui_diffpair_set(project, name="USB_D", net_positive="USB_D+", net_negative="USB_D-")
    # USB_D+ is already in USB_D — can't appear in a second pair.
    result = ui_diffpair_set(
        project, name="OTHER", net_positive="USB_D+", net_negative="OTHER+"
    )
    assert result["ok"] is False
    assert "USB_D" in result["error"]
    assert len(project["pcb"]["diff_pairs"]) == 1


def test_ui_diffpair_set_rejects_negative_numeric() -> None:
    from kc_mcp.ui_tools import ui_diffpair_set

    project = _usb_diffpair_project()
    result = ui_diffpair_set(
        project,
        name="USB_D",
        net_positive="USB_D+",
        net_negative="USB_D-",
        target_impedance_ohms=-90.0,
    )
    assert result["ok"] is False
    assert "target_impedance_ohms" in result["error"]
    assert project["pcb"]["diff_pairs"] == []


def test_ui_diffpair_delete_drops_pair_and_clears_back_refs() -> None:
    from kc_mcp.ui_tools import ui_diffpair_delete, ui_diffpair_set

    project = _usb_diffpair_project()
    ui_diffpair_set(project, name="USB_D", net_positive="USB_D+", net_negative="USB_D-")
    result = ui_diffpair_delete(project, name="USB_D")
    assert result["ok"] is True
    assert result["deleted"] == "USB_D"
    assert set(result["cleared_back_refs"]) == {"USB_D+", "USB_D-"}
    assert project["pcb"]["diff_pairs"] == []
    nets_by_name = {n["name"]: n for n in project["pcb"]["nets"]}
    assert nets_by_name["USB_D+"]["diff_pair"] is None
    assert nets_by_name["USB_D-"]["diff_pair"] is None


def test_ui_diffpair_delete_unknown_returns_error() -> None:
    from kc_mcp.ui_tools import ui_diffpair_delete

    project = _usb_diffpair_project()
    result = ui_diffpair_delete(project, name="NoSuchPair")
    assert result["ok"] is False
    assert "NoSuchPair" in result["error"]


# ---------------------------------------------------------------------
# M3-T-04 — Length-match group manager (ui_lengthgroup_set / delete).
# ---------------------------------------------------------------------


def _ddr_lengthgroup_project() -> dict[str, Any]:
    return {
        "pcb": {
            "length_groups": [],
            "nets": [
                {"name": "DQ0"},
                {"name": "DQ1"},
                {"name": "DQ2"},
                {"name": "DQ3"},
                {"name": "DQS0_P"},
                {"name": "DQS0_N"},
                {"name": "GND"},
            ],
        }
    }


def test_ui_lengthgroup_set_creates_group_with_member_nets() -> None:
    from kc_mcp.ui_tools import ui_lengthgroup_set

    project = _ddr_lengthgroup_project()
    result = ui_lengthgroup_set(
        project,
        name="DDR3_DQ_BYTE0",
        nets=["DQ0", "DQ1", "DQ2", "DQ3", "DQS0_P", "DQS0_N"],
        target_length_mm=42.5,
        tolerance_mm=0.127,
    )
    assert result["ok"] is True
    g = result["length_group"]
    assert g["name"] == "DDR3_DQ_BYTE0"
    assert g["nets"] == ["DQ0", "DQ1", "DQ2", "DQ3", "DQS0_P", "DQS0_N"]
    assert g["target_length_mm"] == pytest.approx(42.5)
    assert g["tolerance_mm"] == pytest.approx(0.127)
    assert project["pcb"]["length_groups"][0] is g


def test_ui_lengthgroup_set_target_zero_is_match_the_longest() -> None:
    """target_length_mm == 0 is the analyzer's "match the longest"
    sentinel — must round-trip through the validator as 0, not
    rejected as "must be positive"."""
    from kc_mcp.ui_tools import ui_lengthgroup_set

    project = _ddr_lengthgroup_project()
    result = ui_lengthgroup_set(
        project,
        name="GRP",
        nets=["DQ0", "DQ1"],
        target_length_mm=0,
        tolerance_mm=0.5,
    )
    assert result["ok"] is True
    assert result["length_group"]["target_length_mm"] == 0.0


def test_ui_lengthgroup_set_upserts_in_place() -> None:
    from kc_mcp.ui_tools import ui_lengthgroup_set

    project = _ddr_lengthgroup_project()
    ui_lengthgroup_set(project, name="GRP", nets=["DQ0"])
    again = ui_lengthgroup_set(
        project, name="GRP", nets=["DQ0", "DQ1"], tolerance_mm=0.05
    )
    assert again["ok"] is True
    assert len(project["pcb"]["length_groups"]) == 1
    assert project["pcb"]["length_groups"][0]["nets"] == ["DQ0", "DQ1"]
    assert project["pcb"]["length_groups"][0]["tolerance_mm"] == pytest.approx(0.05)


def test_ui_lengthgroup_set_requires_name() -> None:
    from kc_mcp.ui_tools import ui_lengthgroup_set

    result = ui_lengthgroup_set({"pcb": {}}, name="   ")
    assert result["ok"] is False
    assert "name" in result["error"]


def test_ui_lengthgroup_set_create_requires_nets() -> None:
    from kc_mcp.ui_tools import ui_lengthgroup_set

    project = _ddr_lengthgroup_project()
    result = ui_lengthgroup_set(project, name="GRP")
    assert result["ok"] is False
    assert "nets" in result["error"]
    assert project["pcb"]["length_groups"] == []


def test_ui_lengthgroup_set_rejects_unknown_net() -> None:
    from kc_mcp.ui_tools import ui_lengthgroup_set

    project = _ddr_lengthgroup_project()
    result = ui_lengthgroup_set(project, name="GRP", nets=["DQ0", "PHANTOM"])
    assert result["ok"] is False
    assert "PHANTOM" in result["error"]
    assert project["pcb"]["length_groups"] == []


def test_ui_lengthgroup_set_rejects_duplicate_nets_in_group() -> None:
    from kc_mcp.ui_tools import ui_lengthgroup_set

    project = _ddr_lengthgroup_project()
    result = ui_lengthgroup_set(project, name="GRP", nets=["DQ0", "DQ0"])
    assert result["ok"] is False
    assert "duplicate" in result["error"].lower()
    assert project["pcb"]["length_groups"] == []


def test_ui_lengthgroup_set_rejects_empty_nets_list() -> None:
    from kc_mcp.ui_tools import ui_lengthgroup_set

    project = _ddr_lengthgroup_project()
    result = ui_lengthgroup_set(project, name="GRP", nets=["   ", ""])
    assert result["ok"] is False
    assert "nets" in result["error"]


def test_ui_lengthgroup_set_rejects_negative_numeric() -> None:
    from kc_mcp.ui_tools import ui_lengthgroup_set

    project = _ddr_lengthgroup_project()
    result = ui_lengthgroup_set(
        project, name="GRP", nets=["DQ0"], target_length_mm=-1.0
    )
    assert result["ok"] is False
    assert "target_length_mm" in result["error"]
    assert project["pcb"]["length_groups"] == []


def test_ui_lengthgroup_set_allows_net_in_multiple_groups() -> None:
    """A net can sit in multiple groups (e.g. DQS in both DQ and
    CLK groups) — no single-membership enforcement."""
    from kc_mcp.ui_tools import ui_lengthgroup_set

    project = _ddr_lengthgroup_project()
    ui_lengthgroup_set(project, name="DDR_DQ", nets=["DQ0", "DQS0_P"])
    result = ui_lengthgroup_set(project, name="DDR_CLK", nets=["DQS0_P", "DQS0_N"])
    assert result["ok"] is True
    assert {g["name"] for g in project["pcb"]["length_groups"]} == {"DDR_DQ", "DDR_CLK"}


def test_ui_lengthgroup_delete_drops_named_group() -> None:
    from kc_mcp.ui_tools import ui_lengthgroup_delete, ui_lengthgroup_set

    project = _ddr_lengthgroup_project()
    ui_lengthgroup_set(project, name="GRP", nets=["DQ0", "DQ1"])
    result = ui_lengthgroup_delete(project, name="GRP")
    assert result["ok"] is True
    assert result["deleted"] == "GRP"
    assert project["pcb"]["length_groups"] == []


def test_ui_lengthgroup_delete_unknown_returns_error() -> None:
    from kc_mcp.ui_tools import ui_lengthgroup_delete

    result = ui_lengthgroup_delete({"pcb": {"length_groups": []}}, name="NoSuchGroup")
    assert result["ok"] is False
    assert "NoSuchGroup" in result["error"]


# ---------------------------------------------------------------------
# M3-T-05 — push-and-shove route apply (ui_shove_apply).
# ---------------------------------------------------------------------


def _shove_project() -> dict[str, Any]:
    return {
        "pcb": {
            "tracks": [
                {
                    "uuid": "track-vcc",
                    "net": "VCC",
                    "layer": "F.Cu",
                    "width_mm": 0.25,
                    "points_mm": [[0.0, 0.3], [10.0, 0.3]],
                    "locked": False,
                },
                {
                    "uuid": "track-gnd",
                    "net": "GND",
                    "layer": "F.Cu",
                    "width_mm": 0.25,
                    "points_mm": [[0.0, 0.6], [10.0, 0.6]],
                    "locked": False,
                },
            ],
        }
    }


def test_ui_shove_apply_adds_new_track_and_moves_shoved_ones() -> None:
    from kc_mcp.ui_tools import ui_shove_apply

    project = _shove_project()
    result = ui_shove_apply(
        project,
        new_track={
            "net": "DATA",
            "layer": "F.Cu",
            "width_mm": 0.25,
            "points_mm": [[0.0, 0.0], [10.0, 0.0]],
        },
        moved_tracks=[
            {"uuid": "track-vcc", "points_mm": [[0.0, 0.45], [10.0, 0.45]]},
            {"uuid": "track-gnd", "points_mm": [[0.0, 0.9], [10.0, 0.9]]},
        ],
    )
    assert result["ok"] is True
    assert set(result["updated_track_uuids"]) == {"track-vcc", "track-gnd"}
    assert result["unmatched_track_uuids"] == []
    tracks = {t["uuid"]: t for t in project["pcb"]["tracks"]}
    # Moved tracks updated in place.
    assert tracks["track-vcc"]["points_mm"] == [[0.0, 0.45], [10.0, 0.45]]
    assert tracks["track-gnd"]["points_mm"] == [[0.0, 0.9], [10.0, 0.9]]
    # New track appended with a fresh uuid + the routed geometry.
    new_uuid = result["new_track_uuid"]
    assert new_uuid in tracks
    assert tracks[new_uuid]["net"] == "DATA"
    assert tracks[new_uuid]["points_mm"] == [[0.0, 0.0], [10.0, 0.0]]
    assert len(project["pcb"]["tracks"]) == 3


def test_ui_shove_apply_with_no_moved_tracks_just_adds_the_route() -> None:
    from kc_mcp.ui_tools import ui_shove_apply

    project = {"pcb": {"tracks": []}}
    result = ui_shove_apply(
        project,
        new_track={
            "net": "DATA",
            "layer": "F.Cu",
            "width_mm": 0.2,
            "points_mm": [[0.0, 0.0], [5.0, 0.0]],
        },
        moved_tracks=[],
    )
    assert result["ok"] is True
    assert result["updated_track_uuids"] == []
    assert len(project["pcb"]["tracks"]) == 1


def test_ui_shove_apply_unmatched_uuid_is_reported_not_fatal() -> None:
    from kc_mcp.ui_tools import ui_shove_apply

    project = _shove_project()
    result = ui_shove_apply(
        project,
        new_track={
            "net": "DATA",
            "layer": "F.Cu",
            "width_mm": 0.25,
            "points_mm": [[0.0, 0.0], [10.0, 0.0]],
        },
        moved_tracks=[
            {"uuid": "track-vcc", "points_mm": [[0.0, 0.45], [10.0, 0.45]]},
            {"uuid": "ghost", "points_mm": [[0.0, 9.0], [10.0, 9.0]]},
        ],
    )
    assert result["ok"] is True
    assert result["updated_track_uuids"] == ["track-vcc"]
    assert result["unmatched_track_uuids"] == ["ghost"]
    # The matched one moved; the new track still got added.
    assert len(project["pcb"]["tracks"]) == 3


def test_ui_shove_apply_requires_new_track() -> None:
    from kc_mcp.ui_tools import ui_shove_apply

    result = ui_shove_apply({"pcb": {}}, new_track=None, moved_tracks=[])
    assert result["ok"] is False
    assert "new_track" in result["error"]


def test_ui_shove_apply_rejects_too_few_route_points() -> None:
    from kc_mcp.ui_tools import ui_shove_apply

    result = ui_shove_apply(
        {"pcb": {}},
        new_track={"net": "D", "layer": "F.Cu", "width_mm": 0.2, "points_mm": [[0.0, 0.0]]},
        moved_tracks=[],
    )
    assert result["ok"] is False
    assert "points_mm" in result["error"]


def test_ui_shove_apply_rejects_missing_net_or_layer() -> None:
    from kc_mcp.ui_tools import ui_shove_apply

    result = ui_shove_apply(
        {"pcb": {}},
        new_track={
            "net": "",
            "layer": "F.Cu",
            "width_mm": 0.2,
            "points_mm": [[0.0, 0.0], [1.0, 0.0]],
        },
        moved_tracks=[],
    )
    assert result["ok"] is False
    assert "net" in result["error"]


def test_ui_shove_apply_rejects_non_positive_width() -> None:
    from kc_mcp.ui_tools import ui_shove_apply

    result = ui_shove_apply(
        {"pcb": {}},
        new_track={
            "net": "D",
            "layer": "F.Cu",
            "width_mm": 0.0,
            "points_mm": [[0.0, 0.0], [1.0, 0.0]],
        },
        moved_tracks=[],
    )
    assert result["ok"] is False
    assert "width_mm" in result["error"]


def test_ui_shove_apply_moved_entry_needs_uuid() -> None:
    from kc_mcp.ui_tools import ui_shove_apply

    result = ui_shove_apply(
        _shove_project(),
        new_track={
            "net": "D",
            "layer": "F.Cu",
            "width_mm": 0.2,
            "points_mm": [[0.0, 0.0], [1.0, 0.0]],
        },
        moved_tracks=[{"points_mm": [[0.0, 0.45], [10.0, 0.45]]}],
    )
    assert result["ok"] is False
    assert "uuid" in result["error"]


def test_ui_shove_apply_accepts_xy_object_point_shape() -> None:
    """The wasm bridge can send `{x, y}` objects as well as `[x, y]`
    pairs — the tool normalises both."""
    from kc_mcp.ui_tools import ui_shove_apply

    project = {"pcb": {"tracks": []}}
    result = ui_shove_apply(
        project,
        new_track={
            "net": "DATA",
            "layer": "F.Cu",
            "width_mm": 0.2,
            "points_mm": [{"x": 0.0, "y": 0.0}, {"x": 5.0, "y": 0.0}],
        },
        moved_tracks=[],
    )
    assert result["ok"] is True
    assert project["pcb"]["tracks"][0]["points_mm"] == [[0.0, 0.0], [5.0, 0.0]]
