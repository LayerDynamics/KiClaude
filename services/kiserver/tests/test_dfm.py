"""Unit tests for `kiserver.dfm` — M2-T-09 pre-flight + M2-Q-03 gate."""

from __future__ import annotations

import pytest
from kiserver.dfm import get_preset, known_targets, run_dfm_check


def _project_with(tracks=(), vias=(), net_classes=()):
    return {
        "pcb": {
            "tracks": list(tracks),
            "vias": list(vias),
            "net_classes": list(net_classes),
        }
    }


def test_known_targets_covers_supported_fabs():
    assert set(known_targets()) == {"generic", "jlcpcb", "oshpark", "pcbway"}


def test_unknown_target_raises():
    with pytest.raises(KeyError):
        get_preset("not-a-fab")


def test_track_below_min_is_an_error_above_advise_is_clean():
    rules = get_preset("jlcpcb")
    project = _project_with(
        tracks=[
            {"uuid": "below-min", "width_mm": 0.05, "layer": "F.Cu"},
            {"uuid": "below-advise", "width_mm": 0.15, "layer": "F.Cu"},
            {"uuid": "clean", "width_mm": 0.3, "layer": "F.Cu"},
        ]
    )
    result = run_dfm_check(project, "jlcpcb")
    assert result["ok"] is False
    severities = {iss["rule"]: iss["severity"] for iss in result["issues"]}
    assert severities["min_track"] == "error"
    assert severities["advise_track"] == "warning"
    assert result["counts"] == {"error": 1, "warning": 1}
    # The clean track stays out of the issue list entirely.
    items = [iss["items"] for iss in result["issues"]]
    assert ["F.Cu", "track:clean"] not in items
    # JLC's hard floor matches the preset value.
    too_thin = next(iss for iss in result["issues"] if iss["rule"] == "min_track")
    assert too_thin["limit_mm"] == rules.min_track_mm


def test_via_drill_and_diameter_violations():
    project = _project_with(
        vias=[
            {"uuid": "v1", "drill_mm": 0.1, "diameter_mm": 0.2},
            {"uuid": "v2", "drill_mm": 0.4, "diameter_mm": 0.7},
        ]
    )
    result = run_dfm_check(project, "jlcpcb")
    issue_rules = sorted(iss["rule"] for iss in result["issues"])
    assert issue_rules == ["min_via_diameter", "min_via_drill"]
    assert result["ok"] is False


def test_netclass_clearance_below_min_is_error():
    project = _project_with(
        net_classes=[{"name": "Default", "clearance_mm": 0.05}]
    )
    result = run_dfm_check(project, "jlcpcb")
    assert result["ok"] is False
    assert result["issues"][0]["rule"] == "min_clearance"
    assert result["issues"][0]["items"] == ["net_class:Default"]


def test_clean_board_passes_with_no_issues():
    project = _project_with(
        tracks=[{"uuid": "t1", "width_mm": 0.3, "layer": "F.Cu"}],
        vias=[{"uuid": "v1", "drill_mm": 0.4, "diameter_mm": 0.8}],
        net_classes=[{"name": "Default", "clearance_mm": 0.2}],
    )
    result = run_dfm_check(project, "jlcpcb")
    assert result["ok"] is True
    assert result["issues"] == []


def test_generic_preset_is_strictest_envelope():
    """Generic should reject anything the strictest of the named
    fabs rejects, since it represents the worst-case envelope."""
    project = _project_with(
        tracks=[{"uuid": "t1", "width_mm": 0.1, "layer": "F.Cu"}],
    )
    jlc = run_dfm_check(project, "jlcpcb")
    generic = run_dfm_check(project, "generic")
    # JLC accepts 0.127 mm tracks but generic uses 0.2 mm floor;
    # a 0.1 mm track fails both.
    assert jlc["ok"] is False
    assert generic["ok"] is False
