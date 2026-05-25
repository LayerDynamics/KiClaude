"""M1-P-04 integration tests for the 11 Claude-facing schematic tools.

Each tool is invoked via the same `await tool_fn(args)` shape the
Claude Agent SDK uses, with httpx wired through `MockTransport` so we
never need a running kiserver/kiconnector. The end-to-end shape we
assert is: every tool returns a `{content: [...], structured: {...}}`
envelope with `structured.ok in {True, False}`.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from kc_mcp import clients
from kc_mcp.distributors.aggregator import PartPricing
from kc_mcp.server import _CLAUDE_TOOLS, build_server
from kc_mcp.tools.erc import kc_erc
from kc_mcp.tools.kcir import kc_kcir_get
from kc_mcp.tools.label import kc_label_attach
from kc_mcp.tools.mpn import kc_mpn_resolve
from kc_mcp.tools.project import kc_project_open, kc_project_save
from kc_mcp.tools.snapshot import (
    _clear_for_tests,
    get_snapshot_meta,
    get_snapshot_project,
    kc_snapshot_create,
    kc_snapshot_revert,
    list_snapshots,
    record_snapshot,
)
from kc_mcp.tools.sourcing import set_aggregator_factory
from kc_mcp.tools.symbol import kc_symbol_add, kc_symbol_edit
from kc_mcp.tools.validate import kc_validate
from kc_mcp.tools.wire import kc_wire_connect


def _structured(result: dict[str, Any]) -> dict[str, Any]:
    """Pull the structured payload out of an MCP envelope."""
    assert "content" in result and isinstance(result["content"], list)
    assert result["content"], "content list is empty"
    if "structured" in result:
        return result["structured"]
    # Some envelopes omit `structured` and only carry the JSON text.
    return json.loads(result["content"][0]["text"])


@pytest.fixture()
def fake_state() -> dict[str, Any]:
    """In-memory project registry the MockTransport mirrors. Each
    test gets a fresh copy so state doesn't leak."""
    _clear_for_tests()
    return {
        "projects": {
            "proj-1": _seed_project("proj-1", "blinky"),
        }
    }


def _seed_project(project_id: str, name: str) -> dict[str, Any]:
    return {
        "ok": True,
        "project_id": project_id,
        "path": f"/tmp/{name}",
        "project": {
            "kcir_version": "0.2.0",
            "name": name,
            "schematic": {
                "sheets": [
                    {
                        "uuid": "sheet-root",
                        "name": name,
                        "file": f"{name}.kicad_sch",
                        "parent": None,
                        "position_mm": [0.0, 0.0],
                        "size_mm": [0.0, 0.0],
                        "pins": [],
                    }
                ],
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
                "layers": [],
                "footprints": [],
                "tracks": [],
                "vias": [],
                "zones": [],
                "outline": {"points_mm": [], "cutouts": []},
                "drawings": [],
                "nets": [],
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
                "title": name,
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
            "name": name,
            "kcir_version": "0.2.0",
            "layer_count": 0,
            "footprint_count": 0,
            "track_count": 0,
            "via_count": 0,
            "zone_count": 0,
            "net_count": 0,
        },
    }


@pytest.fixture(autouse=True)
def kiserver_mock(monkeypatch: pytest.MonkeyPatch, fake_state: dict[str, Any]) -> None:
    """Install a `MockTransport` that mimics kiserver + kiconnector."""

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        body = json.loads(request.content or b"{}") if request.method == "POST" else None

        # kiserver /project/open
        if path == "/project/open" and request.method == "POST":
            payload = _seed_project("proj-new", "demo")
            fake_state["projects"]["proj-new"] = payload
            return _ok(payload)

        # kiserver GET /project/{id}
        if path.startswith("/project/") and request.method == "GET":
            project_id = path.split("/")[-1]
            entry = fake_state["projects"].get(project_id)
            if entry is None:
                return httpx.Response(404, json={"detail": "unknown"})
            return _ok(entry)

        # kiserver POST /project/{id}/replace
        if path.endswith("/replace") and request.method == "POST":
            project_id = path.split("/")[2]
            entry = fake_state["projects"].get(project_id)
            if entry is None:
                return httpx.Response(404, json={"detail": "unknown"})
            entry["project"] = body["project"]
            return _ok({"ok": True, "project_id": project_id, "summary": entry["summary"]})

        # kiserver POST /project/{id}/save
        if path.endswith("/save") and request.method == "POST":
            project_id = path.split("/")[2]
            return _ok(
                {
                    "ok": True,
                    "project_id": project_id,
                    "target_dir": (body or {}).get("target_dir") or "/tmp",
                    "written": [f"/tmp/{project_id}.kicad_pcb"],
                }
            )

        # kiconnector POST /tools/erc
        if path == "/tools/erc" and request.method == "POST":
            return _ok(
                {
                    "ok": True,
                    "issues": [
                        {
                            "severity": "warning",
                            "sheet": "/root",
                            "position_mm": [10.0, 20.0],
                            "type": "no_connect",
                            "description": "unconnected pin",
                        }
                    ],
                    "error": None,
                    "duration_ms": 42,
                    "exit_code": 0,
                }
            )

        return httpx.Response(404, json={"detail": f"no mock for {request.method} {path}"})

    transport = httpx.MockTransport(handler)
    # Use one shared async client across every call so the mock stays
    # mounted for the duration of the test.
    client = httpx.AsyncClient(transport=transport, base_url="http://mock")
    clients.set_client(client)
    monkeypatch.setattr(clients, "_kiserver_url", "")
    monkeypatch.setattr(clients, "_kiconnector_url", "")
    yield
    clients.set_client(None)


def _ok(payload: dict[str, Any]) -> httpx.Response:
    return httpx.Response(200, json=payload)


# ---------------------------------------------------------------------
# Per-tool registration shape.
# ---------------------------------------------------------------------


def test_build_server_exposes_all_schematic_tools() -> None:
    server = build_server()
    # The M1-P-04 schematic suite is 13 tools (kc_ping + 11 schematic
    # tools + kc_snapshot_revert added by M1-T-08). After M2-P-04 the
    # registry also carries 13 PCB tools — total 26. We assert the
    # schematic subset is fully present rather than pinning a total
    # so future milestones can extend the registry freely.
    names = {fn.name for fn in _CLAUDE_TOOLS}  # type: ignore[attr-defined]
    assert {
        "kc_ping",
        "kc_project_open",
        "kc_project_save",
        "kc_kcir_get",
        "kc_validate",
        "kc_erc",
        "kc_symbol_add",
        "kc_symbol_edit",
        "kc_wire_connect",
        "kc_label_attach",
        "kc_mpn_resolve",
        "kc_snapshot_create",
        "kc_snapshot_revert",
    } <= names
    assert server is not None


# ---------------------------------------------------------------------
# Read-only tools.
# ---------------------------------------------------------------------


async def test_kc_project_open_returns_summary() -> None:
    result = await kc_project_open.handler({"path": "/tmp/demo"})
    payload = _structured(result)
    assert payload["ok"] is True
    assert payload["project_id"] == "proj-new"
    assert payload["summary"]["name"] == "demo"


async def test_kc_project_save_passes_target_dir_through() -> None:
    result = await kc_project_save.handler({"project_id": "proj-1", "target_dir": "/tmp/out"})
    payload = _structured(result)
    assert payload["ok"] is True
    assert payload["target_dir"] == "/tmp/out"
    assert any(p.endswith(".kicad_pcb") for p in payload["written"])


async def test_kc_kcir_get_returns_requested_views() -> None:
    result = await kc_kcir_get.handler({"project_id": "proj-1", "view": ["summary", "schematic"]})
    payload = _structured(result)
    assert payload["ok"] is True
    assert "summary" in payload
    assert "schematic" in payload
    assert "pcb" not in payload


async def test_kc_validate_emits_findings_or_empty_list() -> None:
    result = await kc_validate.handler({"project_id": "proj-1"})
    payload = _structured(result)
    assert payload["ok"] is True
    assert "findings" in payload and isinstance(payload["findings"], list)
    assert payload["summary"]["error"] >= 0


async def test_kc_erc_returns_issues_envelope() -> None:
    result = await kc_erc.handler({"project_path": "/tmp/blinky.kicad_sch"})
    payload = _structured(result)
    assert payload["ok"] is True
    assert payload["issues"][0]["type"] == "no_connect"


# ---------------------------------------------------------------------
# Mutating tools.
# ---------------------------------------------------------------------


async def test_kc_symbol_add_appends_to_schematic(fake_state: dict[str, Any]) -> None:
    result = await kc_symbol_add.handler(
        {
            "project_id": "proj-1",
            "sheet_uuid": "sheet-root",
            "lib_id": "Device:R",
            "value": "10k",
            "refdes": "R1",
            "position_mm": [50.0, 50.0],
        }
    )
    payload = _structured(result)
    assert payload["ok"] is True
    project = fake_state["projects"]["proj-1"]["project"]
    assert len(project["schematic"]["symbols"]) == 1
    assert project["schematic"]["symbols"][0]["refdes"] == "R1"


async def test_kc_symbol_edit_changes_only_named_fields(
    fake_state: dict[str, Any],
) -> None:
    project = fake_state["projects"]["proj-1"]["project"]
    project["schematic"]["symbols"].append(
        {
            "uuid": "sym-1",
            "sheet_uuid": "sheet-root",
            "lib_id": "Device:R",
            "refdes": "R1",
            "value": "10k",
            "footprint": "",
            "mpn": "",
            "datasheet": "",
            "position_mm": [50.0, 50.0],
            "rotation_deg": 0.0,
            "mirrored": False,
            "unit": 1,
            "in_bom": True,
            "on_board": True,
            "dnp": False,
            "is_power_flag": False,
            "is_power_symbol": False,
            "properties": [
                {
                    "key": "Reference",
                    "value": "R1",
                    "position_mm": [50.0, 48.0],
                    "rotation_deg": 0.0,
                    "hide": False,
                },
                {
                    "key": "Value",
                    "value": "10k",
                    "position_mm": [50.0, 52.0],
                    "rotation_deg": 0.0,
                    "hide": False,
                },
            ],
        }
    )
    result = await kc_symbol_edit.handler(
        {"project_id": "proj-1", "symbol_uuid": "sym-1", "value": "4.7k"}
    )
    payload = _structured(result)
    assert payload["ok"] is True
    assert payload["changed_fields"] == ["value"]
    edited = fake_state["projects"]["proj-1"]["project"]["schematic"]["symbols"][0]
    assert edited["value"] == "4.7k"
    val_prop = next(p for p in edited["properties"] if p["key"] == "Value")
    assert val_prop["value"] == "4.7k"


async def test_kc_wire_connect_appends_a_wire(fake_state: dict[str, Any]) -> None:
    result = await kc_wire_connect.handler(
        {
            "project_id": "proj-1",
            "sheet_uuid": "sheet-root",
            "points_mm": [[0.0, 0.0], [10.0, 0.0]],
        }
    )
    payload = _structured(result)
    assert payload["ok"] is True
    wires = fake_state["projects"]["proj-1"]["project"]["schematic"]["wires"]
    assert len(wires) == 1
    assert wires[0]["points_mm"] == [[0.0, 0.0], [10.0, 0.0]]


async def test_kc_label_attach_appends_a_label(fake_state: dict[str, Any]) -> None:
    result = await kc_label_attach.handler(
        {
            "project_id": "proj-1",
            "sheet_uuid": "sheet-root",
            "kind": "global",
            "text": "VCC",
            "position_mm": [5.0, 5.0],
        }
    )
    payload = _structured(result)
    assert payload["ok"] is True
    labels = fake_state["projects"]["proj-1"]["project"]["schematic"]["labels"]
    assert len(labels) == 1
    assert labels[0]["kind"] == "global"
    assert labels[0]["text"] == "VCC"


async def test_kc_label_attach_hierarchical_creates_matching_sheet_pin(
    fake_state: dict[str, Any],
) -> None:
    await kc_label_attach.handler(
        {
            "project_id": "proj-1",
            "sheet_uuid": "sheet-root",
            "kind": "hierarchical",
            "text": "DATA",
            "shape": "input",
        }
    )
    sheet = fake_state["projects"]["proj-1"]["project"]["schematic"]["sheets"][0]
    assert any(p["name"] == "DATA" for p in sheet["pins"])


class _NoHitAggregator:
    """Duck-typed aggregator that resolves nothing — keeps this test
    hermetic now that kc_mpn_resolve hits the distributor aggregator."""

    async def price(self, mpn: str, *, qty: int = 1, force_refresh: bool = False) -> PartPricing:
        return PartPricing(
            mpn=mpn, requested_qty=qty, quotes=[], errors={}, cheapest=None,
            cheapest_unit_price_usd=None,
        )

    async def aclose(self) -> None:
        return None


async def test_kc_mpn_resolve_returns_structured_envelope() -> None:
    set_aggregator_factory(lambda: _NoHitAggregator())  # type: ignore[arg-type,return-value]
    try:
        result = await kc_mpn_resolve.handler(
            {"mpn": "STM32G030F6P6", "manufacturer": "STMicro"}
        )
    finally:
        set_aggregator_factory(None)
    payload = _structured(result)
    assert payload["ok"] is True
    # No distributor returned the part, so `found` stays False; confidence
    # scales with the supplied metadata (manufacturer → >= 0.7).
    assert payload["found"] is False
    assert payload["confidence"] >= 0.7


async def test_kc_mpn_resolve_rejects_blank_input() -> None:
    result = await kc_mpn_resolve.handler({"mpn": "   "})
    payload = _structured(result)
    assert payload["ok"] is False
    assert "required" in payload["error"]


async def test_kc_snapshot_create_records_a_revertable_snapshot(
    fake_state: dict[str, Any],
) -> None:
    result = await kc_snapshot_create.handler({"project_id": "proj-1", "label": "before-add"})
    payload = _structured(result)
    assert payload["ok"] is True
    snaps = list_snapshots("proj-1")
    assert len(snaps) == 1
    assert snaps[0]["label"] == "before-add"


# ----------------------------------------------------------------
# M1-T-08 snapshot revert + helpers.
# ----------------------------------------------------------------


async def test_kc_snapshot_revert_round_trips_through_replace(
    fake_state: dict[str, Any],
) -> None:
    """Create a snapshot, mutate the in-memory project state, then
    revert and confirm the kiserver received a /replace POST with the
    original payload."""
    # Snapshot the seeded blinky.
    created = _structured(
        await kc_snapshot_create.handler({"project_id": "proj-1", "label": "baseline"})
    )
    assert created["ok"] is True
    snap_id = created["snapshot_id"]

    # Mutate the mock state to simulate a kc_symbol_add side effect.
    fake_state["projects"]["proj-1"]["project"]["schematic"]["symbols"].append(
        {"uuid": "sym-x", "lib_id": "Device:R", "refdes": "R1"}
    )
    assert len(fake_state["projects"]["proj-1"]["project"]["schematic"]["symbols"]) == 1

    # Revert via the @tool entry point.
    reverted = _structured(
        await kc_snapshot_revert.handler({"project_id": "proj-1", "snapshot_id": snap_id})
    )
    assert reverted["ok"] is True
    assert reverted["snapshot_id"] == snap_id
    # kiserver mock applies the /replace so the symbol list returns to
    # the empty snapshot state.
    assert fake_state["projects"]["proj-1"]["project"]["schematic"]["symbols"] == []


async def test_kc_snapshot_revert_rejects_unknown_snapshot(
    fake_state: dict[str, Any],
) -> None:
    result = await kc_snapshot_revert.handler(
        {"project_id": "proj-1", "snapshot_id": "does-not-exist"}
    )
    payload = _structured(result)
    assert payload["ok"] is False
    assert "no snapshot" in payload["error"]


async def test_kc_snapshot_revert_validates_inputs() -> None:
    blank_project = _structured(
        await kc_snapshot_revert.handler({"project_id": "", "snapshot_id": "s"})
    )
    assert blank_project["ok"] is False
    assert "project_id" in blank_project["error"]
    blank_snap = _structured(
        await kc_snapshot_revert.handler({"project_id": "proj-1", "snapshot_id": ""})
    )
    assert blank_snap["ok"] is False
    assert "snapshot_id" in blank_snap["error"]


def test_record_snapshot_inserts_without_an_http_round_trip(
    fake_state: dict[str, Any],
) -> None:
    """The agent's auto-snapshot path uses `record_snapshot()` to
    avoid bouncing through Claude or kiserver-on-localhost loops."""
    _clear_for_tests()
    ts = record_snapshot(
        "proj-x",
        "snap-direct",
        "auto:test",
        {"kcir_version": "0.2.0", "schematic": {"symbols": []}},
    )
    assert ts
    meta = get_snapshot_meta("proj-x", "snap-direct")
    assert meta is not None
    assert meta["label"] == "auto:test"
    payload = get_snapshot_project("proj-x", "snap-direct")
    assert payload is not None
    assert payload["kcir_version"] == "0.2.0"


def test_get_snapshot_helpers_return_none_for_unknown_ids() -> None:
    _clear_for_tests()
    assert get_snapshot_project("p", "missing") is None
    assert get_snapshot_meta("p", "missing") is None
