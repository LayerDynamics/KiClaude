"""Unit tests for the M3 design-intent validators KC020-KC050.

Exercises the pure `_run_validators` over synthetic KCIR dicts — no
kiserver round-trip. Covers decoupling (KC020), power-rail source
(KC021), length-match membership (KC030), diff-pair declaration
(KC031), controlled-impedance achievability (KC040), and analog/digital
partition isolation (KC050).
"""

from __future__ import annotations

from typing import Any

from kc_mcp.tools.validate import _microstrip_z0, _run_validators


def _codes(findings: list[dict[str, Any]], code: str) -> list[dict[str, Any]]:
    return [f for f in findings if f["code"] == code]


def _project(
    *,
    nets: list[dict[str, Any]] | None = None,
    footprints: list[dict[str, Any]] | None = None,
    length_groups: list[dict[str, Any]] | None = None,
    diff_pairs: list[dict[str, Any]] | None = None,
    net_classes: list[dict[str, Any]] | None = None,
    stackup: dict[str, Any] | None = None,
    symbols: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "schematic": {"sheets": [], "symbols": symbols or [], "labels": []},
        "design_rules": {"via_diameter_mm": 0.45, "clearance_mm": 0.1, "trace_width_mm": 0.1},
        "net_classes": net_classes or [],
        "stackup": stackup or {},
        "pcb": {
            "nets": nets or [],
            "footprints": footprints or [],
            "length_groups": length_groups or [],
            "diff_pairs": diff_pairs or [],
            "signoff": {},
        },
    }


def _fp(refdes: str, *nets: str) -> dict[str, Any]:
    return {"refdes": refdes, "uuid": refdes.lower(), "pads": [{"net": n} for n in nets]}


# --- KC020 decoupling ------------------------------------------------------


def test_kc020_ic_without_bypass_cap_is_error() -> None:
    proj = _project(
        nets=[{"name": "+3V3"}, {"name": "GND"}],
        footprints=[_fp("U1", "+3V3", "GND")],  # no cap on +3V3
    )
    kc = _codes(_run_validators(proj), "KC020")
    assert len(kc) == 1 and kc[0]["severity"] == "error"
    assert "+3V3" in kc[0]["message"]


def test_kc020_ic_with_bypass_cap_clears() -> None:
    proj = _project(
        nets=[{"name": "+3V3"}, {"name": "GND"}],
        footprints=[_fp("U1", "+3V3", "GND"), _fp("C1", "+3V3", "GND")],
    )
    assert _codes(_run_validators(proj), "KC020") == []


def test_kc020_unpowered_ic_is_not_flagged() -> None:
    proj = _project(nets=[{"name": "GND"}], footprints=[_fp("U1", "GND")])
    assert _codes(_run_validators(proj), "KC020") == []


# --- KC021 power-rail source ----------------------------------------------


def test_kc021_rail_with_only_passives_is_error() -> None:
    proj = _project(
        nets=[{"name": "+3V3"}, {"name": "GND"}],
        footprints=[_fp("C1", "+3V3", "GND"), _fp("R1", "+3V3", "GND")],
    )
    kc = _codes(_run_validators(proj), "KC021")
    assert len(kc) == 1 and kc[0]["severity"] == "error"


def test_kc021_rail_with_regulator_clears() -> None:
    proj = _project(
        nets=[{"name": "+3V3"}, {"name": "GND"}],
        footprints=[_fp("VR1", "+3V3", "GND"), _fp("C1", "+3V3", "GND")],
    )
    assert _codes(_run_validators(proj), "KC021") == []


def test_kc021_rail_with_pwr_flag_clears() -> None:
    proj = _project(
        nets=[{"name": "+3V3"}, {"name": "GND"}],
        footprints=[_fp("C1", "+3V3", "GND")],
        symbols=[{"is_power_flag": True, "value": "+3V3"}],
    )
    assert _codes(_run_validators(proj), "KC021") == []


# --- KC030 length-match membership ----------------------------------------


def test_kc030_group_with_one_member_is_error() -> None:
    proj = _project(length_groups=[{"name": "DDR_BYTE0", "nets": ["DQ0"]}])
    kc = _codes(_run_validators(proj), "KC030")
    assert len(kc) == 1 and kc[0]["severity"] == "error"


def test_kc030_group_with_two_members_clears() -> None:
    proj = _project(length_groups=[{"name": "DDR_BYTE0", "nets": ["DQ0", "DQ1"]}])
    assert _codes(_run_validators(proj), "KC030") == []


# --- KC031 diff-pair declaration ------------------------------------------


def test_kc031_missing_leg_is_error() -> None:
    proj = _project(diff_pairs=[{"name": "USB", "net_positive": "USB_D+", "net_negative": ""}])
    kc = _codes(_run_validators(proj), "KC031")
    assert any(f["severity"] == "error" for f in kc)


def test_kc031_unknown_net_is_error() -> None:
    proj = _project(
        nets=[{"name": "USB_D+"}],
        diff_pairs=[{"name": "USB", "net_positive": "USB_D+", "net_negative": "USB_D-"}],
    )
    kc = _codes(_run_validators(proj), "KC031")
    assert any("unknown" in f["message"].lower() for f in kc)


def test_kc031_fully_declared_pair_clears() -> None:
    proj = _project(
        nets=[
            {"name": "USB_D+", "diff_pair": "USB_D-"},
            {"name": "USB_D-", "diff_pair": "USB_D+"},
        ],
        diff_pairs=[
            {
                "name": "USB",
                "net_positive": "USB_D+",
                "net_negative": "USB_D-",
                "length_group": "USB",
            }
        ],
    )
    assert _codes(_run_validators(proj), "KC031") == []


def test_kc031_one_way_backref_and_no_group_warn() -> None:
    proj = _project(
        nets=[{"name": "USB_D+", "diff_pair": "USB_D-"}, {"name": "USB_D-"}],
        diff_pairs=[{"name": "USB", "net_positive": "USB_D+", "net_negative": "USB_D-"}],
    )
    kc = _codes(_run_validators(proj), "KC031")
    assert len(kc) == 2 and all(f["severity"] == "warning" for f in kc)


# --- KC040 controlled impedance -------------------------------------------

_STACKUP = {
    "layers": [
        {"kind": "copper", "name": "F.Cu"},
        {"kind": "dielectric", "dielectric_constant": 4.5, "thickness_mm": 0.2},
        {"kind": "copper", "name": "B.Cu"},
    ]
}


def _imp_project(width: float, target: float, stackup: dict[str, Any] | None = _STACKUP):
    return _project(
        nets=[{"name": "CLK", "class": "Sig", "target_impedance_ohm": target}],
        net_classes=[{"name": "Sig", "track_width_mm": width}],
        stackup=stackup,
    )


def test_kc040_on_target_width_clears() -> None:
    # ~52.6 ohm at 0.30 mm on FR4 (er 4.5, h 0.2) — within 10% of 50.
    assert _codes(_run_validators(_imp_project(0.30, 50.0)), "KC040") == []


def test_kc040_slightly_off_is_warning() -> None:
    # ~58 ohm at 0.25 mm: 10-20% off 50.
    kc = _codes(_run_validators(_imp_project(0.25, 50.0)), "KC040")
    assert len(kc) == 1 and kc[0]["severity"] == "warning"


def test_kc040_way_off_is_error() -> None:
    # ~13 ohm at 1.0 mm — way under 50.
    kc = _codes(_run_validators(_imp_project(1.0, 50.0)), "KC040")
    assert len(kc) == 1 and kc[0]["severity"] == "error"


def test_kc040_missing_stackup_is_warning() -> None:
    kc = _codes(_run_validators(_imp_project(0.30, 50.0, stackup={})), "KC040")
    assert len(kc) == 1 and kc[0]["severity"] == "warning"


def test_microstrip_z0_is_monotonic_in_width() -> None:
    # Wider trace → lower impedance.
    assert _microstrip_z0(0.2, 0.2, 4.5) > _microstrip_z0(0.5, 0.2, 4.5)


# --- KC050 partition isolation --------------------------------------------


def test_kc050_two_ground_bridges_is_error() -> None:
    proj = _project(
        nets=[{"name": "AGND"}, {"name": "DGND"}],
        footprints=[_fp("FB1", "AGND", "DGND"), _fp("R9", "AGND", "DGND")],
    )
    kc = _codes(_run_validators(proj), "KC050")
    assert len(kc) == 1 and kc[0]["severity"] == "error"


def test_kc050_single_bridge_clears() -> None:
    proj = _project(
        nets=[{"name": "AGND"}, {"name": "DGND"}],
        footprints=[_fp("FB1", "AGND", "DGND")],
    )
    assert _codes(_run_validators(proj), "KC050") == []
