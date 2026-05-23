"""Length-tuning analyzer (M3.6) — pure-function tests."""

from __future__ import annotations

import json
from pathlib import Path

from ki_mcp_pcb_core.cir.models import (
    Board,
    Component,
    Constraint,
    FabTarget,
    Net,
    Stackup,
)
from ki_mcp_pcb_core.signal_integrity import (
    Measurement,
    analyze_tuning,
    parse_measurements,
)


def _stm32_lite_board() -> Board:
    """Tiny board with a single 3-net length-match group."""
    return Board(
        name="lengths",
        stackup=Stackup.default_4layer_fr4(),
        fab=FabTarget(layer_count=4),
        components=[Component(refdes="U1", mpn="X"), Component(refdes="U2", mpn="Y")],
        nets=[
            Net(name="GND", net_class="ground", members=["U1.1", "U2.1"]),
            Net(name="A", net_class="high_speed", length_match_group="bus",
                members=["U1.2", "U2.2"]),
            Net(name="B", net_class="high_speed", length_match_group="bus",
                members=["U1.3", "U2.3"]),
            Net(name="C", net_class="high_speed", length_match_group="bus",
                members=["U1.4", "U2.4"]),
        ],
        constraints=[
            Constraint(kind="length_match", targets=["bus"], tolerance_pct=5.0),
        ],
    )


def test_analyze_tuning_within_tolerance() -> None:
    board = _stm32_lite_board()
    # All three nets within 5% of the longest (35 mm).
    measurements = {
        "A": Measurement(length_mm=35.0, trace_count=4),
        "B": Measurement(length_mm=34.0, trace_count=4),
        "C": Measurement(length_mm=33.5, trace_count=4),  # 4.3% short
    }
    report = analyze_tuning(board, measurements)
    assert report.ok
    assert len(report.queue) == 0
    assert len(report.groups) == 1
    assert report.groups[0].target_mm == 35.0


def test_analyze_tuning_emits_queue_when_out_of_tolerance() -> None:
    board = _stm32_lite_board()
    # Net B is way off — 10% short, outside 5% tolerance.
    measurements = {
        "A": Measurement(length_mm=35.0, trace_count=4),
        "B": Measurement(length_mm=31.0, trace_count=4),  # -11%
        "C": Measurement(length_mm=34.5, trace_count=4),
    }
    report = analyze_tuning(board, measurements)
    assert not report.ok
    assert len(report.queue) == 1
    action = report.queue[0]
    assert action.net == "B"
    assert action.direction == "lengthen"
    assert action.delta_mm == 4.0


def test_analyze_tuning_absolute_tolerance_via_constraint() -> None:
    board = _stm32_lite_board()
    # Swap the constraint for absolute mm tolerance
    board.constraints[0].tolerance_pct = None
    board.constraints[0].value_mm = 0.5
    measurements = {
        "A": Measurement(length_mm=35.0, trace_count=4),
        "B": Measurement(length_mm=34.6, trace_count=4),  # 0.4 short — within
        "C": Measurement(length_mm=34.0, trace_count=4),  # 1.0 short — out
    }
    report = analyze_tuning(board, measurements)
    assert not report.ok
    nets_in_queue = {a.net for a in report.queue}
    assert "C" in nets_in_queue
    assert "B" not in nets_in_queue


def test_parse_measurements_reads_pcbnew_report(tmp_path: Path) -> None:
    payload = {
        "pcb_path": "/whatever.kicad_pcb",
        "nets": {
            "USB_DP": {"length_mm": 12.34, "trace_count": 3},
            "USB_DM": {"length_mm": 12.50, "trace_count": 3},
        },
    }
    report_path = tmp_path / "lengths.json"
    report_path.write_text(json.dumps(payload), encoding="utf-8")

    measurements = parse_measurements(report_path)
    assert measurements["USB_DP"].length_mm == 12.34
    assert measurements["USB_DM"].trace_count == 3


def test_analyze_tuning_groups_with_no_measurements_skip_cleanly() -> None:
    board = _stm32_lite_board()
    # Empty measurements — nothing to compare
    report = analyze_tuning(board, {})
    assert report.ok
    assert not report.groups
    assert not report.queue
