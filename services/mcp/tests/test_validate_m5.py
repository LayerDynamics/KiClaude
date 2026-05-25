"""Unit tests for the M5 co-pilot validators KC060 + KC070.

Exercises the pure `_run_validators` function over synthetic KCIR
dicts — no kiserver round-trip needed. Covers the DDR fly-by node-count
+ sign-off gate (KC060) and the BGA fanout feasibility + sign-off gate
(KC070), including the `pcb.signoff` interactions added in KCIR 0.5.
"""

from __future__ import annotations

from typing import Any

from kc_mcp.tools.validate import _is_bga_footprint, _min_pad_pitch, _run_validators


def _codes(findings: list[dict[str, Any]], code: str) -> list[dict[str, Any]]:
    return [f for f in findings if f["code"] == code]


def _project(
    *,
    nets: list[dict[str, Any]] | None = None,
    footprints: list[dict[str, Any]] | None = None,
    signoff: dict[str, bool] | None = None,
    design_rules: dict[str, float] | None = None,
) -> dict[str, Any]:
    """Minimal KCIR project dict with just the PCB fields the M5
    validators read. The structural KC001..KC011 validators tolerate the
    empty schematic."""
    return {
        "schematic": {"sheets": [], "symbols": [], "labels": []},
        "design_rules": design_rules
        or {"via_diameter_mm": 0.45, "clearance_mm": 0.1, "trace_width_mm": 0.1},
        "pcb": {
            "nets": nets or [],
            "footprints": footprints or [],
            "signoff": signoff or {},
        },
    }


def _bga_pads(
    pitch: float, rows: int = 4, cols: int = 4, net_prefix: str = "B"
) -> list[dict[str, Any]]:
    """A `rows`-by-`cols` ball grid at `pitch` mm, centered on the origin."""
    pads: list[dict[str, Any]] = []
    x0 = -(cols - 1) * pitch / 2.0
    y0 = -(rows - 1) * pitch / 2.0
    for r in range(rows):
        for c in range(cols):
            pads.append(
                {
                    "number": f"{chr(ord('A') + r)}{c + 1}",
                    "position_mm": [x0 + c * pitch, y0 + r * pitch],
                    "size_mm": [pitch * 0.5, pitch * 0.5],
                    "net": f"{net_prefix}{r}{c}",
                }
            )
    return pads


# --------------------------------------------------------------------------
# KC060 — DDR fly-by topology
# --------------------------------------------------------------------------


def test_kc060_flyby_with_too_few_nodes_is_error() -> None:
    proj = _project(
        nets=[{"name": "DDR_CK+", "topology": "fly_by", "members": ["U1-1", "U2-1"]}]
    )
    kc060 = _codes(_run_validators(proj), "KC060")
    assert len(kc060) == 1
    assert kc060[0]["severity"] == "error"
    assert "2 node" in kc060[0]["message"]


def test_kc060_flyby_three_nodes_unreviewed_is_warning() -> None:
    proj = _project(
        nets=[
            {"name": "DDR_CK+", "topology": "fly_by", "members": ["U1-1", "U2-1", "U3-1"]}
        ],
        signoff={"ddr_reviewed": False},
    )
    kc060 = _codes(_run_validators(proj), "KC060")
    assert len(kc060) == 1
    assert kc060[0]["severity"] == "warning"


def test_kc060_flyby_three_nodes_reviewed_clears() -> None:
    proj = _project(
        nets=[
            {"name": "DDR_CK+", "topology": "fly_by", "members": ["U1-1", "U2-1", "U3-1"]}
        ],
        signoff={"ddr_reviewed": True},
    )
    assert _codes(_run_validators(proj), "KC060") == []


def test_kc060_ignores_non_flyby_nets() -> None:
    proj = _project(
        nets=[
            {"name": "GND", "topology": "star", "members": ["U1-1"]},
            {"name": "DATA", "members": ["U1-2", "U2-2"]},
        ]
    )
    assert _codes(_run_validators(proj), "KC060") == []


# --------------------------------------------------------------------------
# KC070 — BGA fanout feasibility
# --------------------------------------------------------------------------


def test_kc070_tight_pitch_unreviewed_is_error() -> None:
    # need = 0.45 + 0.1 + 0.1 = 0.65; pitch 0.5 < 0.65 → infeasible.
    proj = _project(
        footprints=[
            {"refdes": "U2", "uuid": "u2", "lib_id": "Package_BGA:X", "pads": _bga_pads(0.5)}
        ],
        signoff={"bga_fanout_reviewed": False},
    )
    kc070 = _codes(_run_validators(proj), "KC070")
    assert len(kc070) == 1
    assert kc070[0]["severity"] == "error"
    assert "HDI/microvia" in kc070[0]["message"]


def test_kc070_tight_pitch_reviewed_downgrades_to_info() -> None:
    proj = _project(
        footprints=[
            {"refdes": "U2", "uuid": "u2", "lib_id": "Package_BGA:X", "pads": _bga_pads(0.5)}
        ],
        signoff={"bga_fanout_reviewed": True},
    )
    kc070 = _codes(_run_validators(proj), "KC070")
    assert len(kc070) == 1
    assert kc070[0]["severity"] == "info"


def test_kc070_feasible_pitch_unreviewed_is_warning() -> None:
    # pitch 0.8 >= need 0.65 → feasible, but not yet reviewed.
    proj = _project(
        footprints=[
            {"refdes": "U2", "uuid": "u2", "lib_id": "Package_BGA:X", "pads": _bga_pads(0.8)}
        ],
        signoff={"bga_fanout_reviewed": False},
    )
    kc070 = _codes(_run_validators(proj), "KC070")
    assert len(kc070) == 1
    assert kc070[0]["severity"] == "warning"


def test_kc070_feasible_pitch_reviewed_clears() -> None:
    proj = _project(
        footprints=[
            {"refdes": "U2", "uuid": "u2", "lib_id": "Package_BGA:X", "pads": _bga_pads(0.8)}
        ],
        signoff={"bga_fanout_reviewed": True},
    )
    assert _codes(_run_validators(proj), "KC070") == []


def test_kc070_ignores_non_bga_footprints() -> None:
    # A 2-pad 0402 cap is never a BGA, even at a tight pitch.
    proj = _project(
        footprints=[
            {
                "refdes": "C1",
                "uuid": "c1",
                "lib_id": "Capacitor_SMD:C_0402_1005Metric",
                "pads": [
                    {"number": "1", "position_mm": [-0.5, 0.0], "size_mm": [0.3, 0.4], "net": "G"},
                    {"number": "2", "position_mm": [0.5, 0.0], "size_mm": [0.3, 0.4], "net": "V"},
                ],
            }
        ]
    )
    assert _codes(_run_validators(proj), "KC070") == []


# --------------------------------------------------------------------------
# Detection + geometry helpers
# --------------------------------------------------------------------------


def test_is_bga_detects_by_lib_id_and_by_grid() -> None:
    assert _is_bga_footprint("Package_BGA:DDR3L_BGA-16_4x4", [])
    # No "BGA" in the name, but a 4x4 grid → detected by geometry.
    assert _is_bga_footprint("Custom:MysteryGrid", _bga_pads(0.8))
    # A single row of 9 pads is not a grid.
    row = [
        {"number": str(i), "position_mm": [float(i), 0.0], "size_mm": [0.4, 0.4], "net": ""}
        for i in range(9)
    ]
    assert not _is_bga_footprint("Custom:Header", row)


def test_min_pad_pitch_matches_grid_pitch() -> None:
    pitch = _min_pad_pitch(_bga_pads(0.8))
    assert pitch is not None
    assert abs(pitch - 0.8) < 1e-9
    assert _min_pad_pitch([]) is None
