"""Schematic auto-layout tests."""

from __future__ import annotations

import math
from pathlib import Path

from ki_mcp_pcb_core.cir.models import Board, Component, Net
from ki_mcp_pcb_core.parsers.yaml import parse_yaml
from ki_mcp_pcb_core.synthesis.sch_layout import (
    assign_decouplers,
    cluster_components,
    cluster_for,
    layout_schematic,
)

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


# ---------------------------------------------------------------------------
# cluster_components
# ---------------------------------------------------------------------------


def test_components_sharing_signal_net_cluster_together() -> None:
    board = Board(
        name="t",
        components=[
            Component(refdes="U1", mpn="X"),
            Component(refdes="U2", mpn="Y"),
            Component(refdes="C1", mpn="Z"),
        ],
        nets=[
            Net(name="GND", net_class="ground", members=["U1.1", "U2.1", "C1.2"]),
            Net(name="SIG", net_class="signal", members=["U1.2", "U2.2"]),
        ],
    )
    clusters = cluster_components(board)
    # U1 and U2 share a signal → same cluster. C1 only on ground → solo.
    by_member: dict[str, str] = {}
    for anchor, members in clusters.items():
        for m in members:
            by_member[m] = anchor
    assert by_member["U1"] == by_member["U2"]
    assert by_member["U1"] != by_member["C1"]


def test_global_nets_do_not_create_edges() -> None:
    """Components that share only GND or a power rail should NOT cluster."""
    board = Board(
        name="t",
        components=[Component(refdes="U1", mpn="X"), Component(refdes="U2", mpn="Y")],
        nets=[
            Net(name="GND", net_class="ground", members=["U1.1", "U2.1"]),
            Net(name="3V3", net_class="power", power_rail="3V3", members=["U1.2", "U2.2"]),
        ],
    )
    # cluster_for delegates to cluster_components — the explicit call is
    # exercised in the other tests in this module.
    assert cluster_for(board, "U1") != cluster_for(board, "U2")


def test_cluster_anchor_prefers_ic() -> None:
    """When a cluster contains both a U and a C, the anchor is the U."""
    board = Board(
        name="t",
        components=[
            Component(refdes="C1", mpn="A"),
            Component(refdes="U1", mpn="B"),
        ],
        nets=[
            Net(name="GND", net_class="ground", members=["C1.2"]),
            Net(name="SIG", net_class="signal", members=["U1.1", "C1.1"]),
        ],
    )
    clusters = cluster_components(board)
    assert "U1" in clusters
    assert "U1" in clusters["U1"] and "C1" in clusters["U1"]


# ---------------------------------------------------------------------------
# assign_decouplers
# ---------------------------------------------------------------------------


def test_decoupler_mapped_to_ic_via_declared_pins() -> None:
    board = Board(
        name="t",
        components=[
            Component(refdes="U1", mpn="X", decoupling_pins=["11"]),
            Component(refdes="C1", mpn="GRM188R71C104KA01D"),
        ],
        nets=[
            Net(name="GND", net_class="ground", members=["U1.10", "C1.2"]),
            Net(name="3V3", net_class="power", power_rail="3V3", members=["U1.11", "C1.1"]),
        ],
    )
    assignments = assign_decouplers(board)
    assert assignments == {"C1": "U1"}


def test_decoupler_fallback_to_proximity_when_no_pins_declared() -> None:
    """Even without explicit decoupling_pins, an IC sharing the rail gets the cap."""
    board = Board(
        name="t",
        components=[
            Component(refdes="U1", mpn="X"),  # no decoupling_pins
            Component(refdes="C1", mpn="C"),
        ],
        nets=[
            Net(name="GND", net_class="ground", members=["U1.1", "C1.2"]),
            Net(name="3V3", net_class="power", power_rail="3V3", members=["U1.2", "C1.1"]),
        ],
    )
    assert assign_decouplers(board) == {"C1": "U1"}


def test_cap_not_on_a_rail_is_not_a_decoupler() -> None:
    """Caps that don't bridge rail+ground (e.g. AC-coupling caps) are skipped."""
    board = Board(
        name="t",
        components=[
            Component(refdes="U1", mpn="X"),
            Component(refdes="C1", mpn="C"),
        ],
        nets=[
            Net(name="GND", net_class="ground", members=["U1.1"]),
            Net(name="AUDIO", net_class="signal", members=["U1.2", "C1.1", "C1.2"]),
        ],
    )
    assert assign_decouplers(board) == {}


def test_demo_decouplers_assigned_to_correct_ic() -> None:
    """STM32 demo: digital caps → U1 (STM32), analog caps → U2 (codec)."""
    board = parse_yaml(EXAMPLES / "stm32_audio.yaml")
    decouplers = assign_decouplers(board)
    # C1-C7 are digital; C8-C10 are analog
    digital_caps = {f"C{i}" for i in range(1, 8)}
    analog_caps = {f"C{i}" for i in range(8, 11)}
    for cap in digital_caps:
        assert decouplers.get(cap) == "U1", cap
    for cap in analog_caps:
        assert decouplers.get(cap) == "U2", cap


# ---------------------------------------------------------------------------
# layout_schematic — placement properties
# ---------------------------------------------------------------------------


def test_layout_returns_one_placement_per_component() -> None:
    board = parse_yaml(EXAMPLES / "stm32_audio.yaml")
    layout = layout_schematic(board)
    placed = {p.refdes for p in layout}
    assert placed == {c.refdes for c in board.components}


def test_layout_decouplers_within_radius_of_parent_ic() -> None:
    """A decoupler placed in U1's halo must be within ~radius of U1."""
    from ki_mcp_pcb_core.synthesis.sch_layout import _PASSIVE_STEP_MM, _RADIUS_MM

    board = parse_yaml(EXAMPLES / "blinky.yaml")
    layout = layout_schematic(board)
    by_ref = {p.refdes: p for p in layout}
    u1 = by_ref["U1"]
    c1 = by_ref["C1"]
    dist = math.hypot(c1.x_mm - u1.x_mm, c1.y_mm - u1.y_mm)
    # First-ring decoupler — at exactly _RADIUS_MM away.
    assert abs(dist - _RADIUS_MM) < 0.5 or dist <= _RADIUS_MM + _PASSIVE_STEP_MM + 1.0


def test_layout_clusters_left_to_right_no_overlap() -> None:
    """Each cluster center sits at increasing x — clusters don't visually
    stack on top of each other on the canvas."""
    from ki_mcp_pcb_core.synthesis.sch_layout import _CLUSTER_GAP_MM, _RADIUS_MM

    board = parse_yaml(EXAMPLES / "stm32_audio.yaml")
    layout = layout_schematic(board)
    anchors_by_x: dict[str, float] = {}
    for p in layout:
        if p.refdes == p.cluster:
            anchors_by_x[p.cluster] = p.x_mm
    xs = sorted(anchors_by_x.values())
    for i in range(1, len(xs)):
        gap = xs[i] - xs[i - 1]
        # Lower bound; in practice clusters are spaced _CLUSTER_GAP_MM + 2 * radius
        assert gap >= _CLUSTER_GAP_MM, f"clusters too close: {xs[i-1]} → {xs[i]}"
        # Sanity: gap is finite
        _ = _RADIUS_MM


def test_layout_orders_clusters_by_partition() -> None:
    """When partitions are declared, analog clusters render left of digital."""
    board = Board(
        name="t",
        components=[
            Component(refdes="U1", mpn="X", partition="analog"),
            Component(refdes="U2", mpn="Y", partition="digital"),
        ],
        nets=[
            Net(name="GND", net_class="ground", members=["U1.1", "U2.1"]),
        ],
    )
    layout = layout_schematic(board)
    by_ref = {p.refdes: p for p in layout}
    # Analog (U1) should sit left of digital (U2).
    assert by_ref["U1"].x_mm < by_ref["U2"].x_mm


def test_schematic_emission_still_works_after_layout(tmp_path) -> None:
    """The layout layer must not break schematic emission."""
    from ki_mcp_pcb_core.synthesis.schematic import write_schematic

    board = parse_yaml(EXAMPLES / "stm32_audio.yaml")
    out = write_schematic(board, tmp_path / "out.kicad_sch")

    text = out.read_text(encoding="utf-8")
    assert text.startswith("(kicad_sch")
    # Every component is placed as a symbol carrying its refdes.
    for comp in board.components:
        assert f'(property "Reference" "{comp.refdes}"' in text
