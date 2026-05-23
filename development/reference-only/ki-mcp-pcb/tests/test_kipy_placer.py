"""Tests for the kipy auto-placement bridge.

Every test injects a fake kipy *client* via
:func:`set_kicad_factory_for_tests`, so no running KiCad is needed. The
fakes mimic only the slice of the kipy surface ``KipyPlacer`` touches.
The two placement-assertion tests additionally exercise the real
``kipy.geometry.Vector2`` — kipy is a dev dependency, so it imports.
"""

from __future__ import annotations

from typing import Any

import pytest
from ki_mcp_pcb_core.cir.models import Board, Component
from ki_mcp_pcb_core.placement import Placement
from ki_mcp_pcb_core.placement.kipy_placer import (
    KipyPlacer,
    KipyStatus,
    autoplace_board,
    probe,
    set_kicad_factory_for_tests,
)

# ---------------------------------------------------------------------------
# Fake kipy surface
# ---------------------------------------------------------------------------


class _FakeField:
    def __init__(self, text: str) -> None:
        self.text = text


class _FakeFootprint:
    def __init__(self, ref: str) -> None:
        self.reference_field = _FakeField(ref)
        # The placer reassigns this to a real kipy Vector2 via
        # Vector2.from_xy_mm(); the initial value is just a placeholder.
        self.position: Any = None


class _FakeBoard:
    def __init__(self, refs: list[str]) -> None:
        self._fps = [_FakeFootprint(r) for r in refs]
        self.commits: list[tuple[str, Any]] = []

    def get_footprints(self) -> list[_FakeFootprint]:
        return self._fps

    def begin_commit(self) -> str:
        return "commit-handle"

    def update_items(self, items: list[Any]) -> None:
        self.commits.append(("update", len(items)))

    def push_commit(self, commit: Any, msg: str | None = None) -> None:
        self.commits.append(("push", msg))


class _FakeKicad:
    def __init__(self, board: _FakeBoard | None,
                 version: str | None = "9.0.0-fake") -> None:
        self._board = board
        self._version = version

    def get_board(self) -> _FakeBoard | None:
        return self._board

    def get_version(self) -> str | None:
        return self._version


@pytest.fixture(autouse=True)
def _restore_factory():
    """Reset the kipy factory after every test so leaks don't cross."""
    yield
    set_kicad_factory_for_tests(None)


# ---------------------------------------------------------------------------
# probe()
# ---------------------------------------------------------------------------


def test_probe_reports_kipy_unavailable_when_factory_raises_import() -> None:
    from ki_mcp_pcb_core.placement.kipy_placer import _KipyUnavailable

    def boom() -> Any:
        raise _KipyUnavailable("no kipy")

    set_kicad_factory_for_tests(boom)
    status = probe()
    assert status.code == "kipy_unavailable"
    assert "no kipy" in status.detail


def test_probe_reports_kicad_unreachable_on_connection_failure() -> None:
    def boom() -> Any:
        raise ConnectionRefusedError("no listener")

    set_kicad_factory_for_tests(boom)
    status = probe()
    assert status.code == "kicad_unreachable"
    assert "no listener" in status.detail


def test_probe_returns_ok_and_version_on_happy_path() -> None:
    fake = _FakeKicad(board=_FakeBoard([]))
    set_kicad_factory_for_tests(lambda: fake)
    status = probe()
    assert status.ok
    assert status.kicad_version == "9.0.0-fake"


def test_probe_tolerates_missing_version_accessor() -> None:
    class VersionlessKicad:
        def get_board(self) -> None:
            return None

    set_kicad_factory_for_tests(lambda: VersionlessKicad())
    status = probe()
    assert status.ok
    assert status.kicad_version is None


# ---------------------------------------------------------------------------
# KipyPlacer.apply_*
# ---------------------------------------------------------------------------


def _board_with(refs: list[str]) -> Board:
    return Board(name="t",
                 components=[Component(refdes=r, mpn="X") for r in refs])


def test_apply_to_board_moves_matching_refdes_in_one_commit() -> None:
    fake_board = _FakeBoard(["U1", "C1", "R1"])
    set_kicad_factory_for_tests(lambda: _FakeKicad(fake_board))

    status = KipyPlacer().apply_to_board(_board_with(["U1", "C1", "R1"]))

    assert status.ok
    assert sorted(status.moved) == ["C1", "R1", "U1"]
    # One update + one push — atomic.
    kinds = [k for k, _ in fake_board.commits]
    assert kinds == ["update", "push"]


def test_apply_to_board_reports_skipped_refdes_when_pcb_misses_them() -> None:
    fake_board = _FakeBoard(["U1"])
    set_kicad_factory_for_tests(lambda: _FakeKicad(fake_board))

    status = KipyPlacer().apply_to_board(_board_with(["U1", "R99"]))

    assert status.ok
    assert status.moved == ["U1"]
    assert status.skipped == ["R99"]


def test_apply_to_board_returns_no_matching_refdes_when_pcb_empty() -> None:
    fake_board = _FakeBoard([])  # no footprints
    set_kicad_factory_for_tests(lambda: _FakeKicad(fake_board))

    status = KipyPlacer().apply_to_board(_board_with(["U1", "C1"]))

    assert status.code == "no_matching_refdes"
    assert status.moved == []
    assert sorted(status.skipped) == ["C1", "U1"]
    # No commit pushed when nothing to move.
    assert fake_board.commits == []


def test_apply_to_board_reports_no_open_board() -> None:
    set_kicad_factory_for_tests(lambda: _FakeKicad(board=None))
    status = KipyPlacer().apply_to_board(_board_with(["U1"]))
    assert status.code == "no_open_board"


def test_apply_to_board_reports_kipy_unavailable() -> None:
    from ki_mcp_pcb_core.placement.kipy_placer import _KipyUnavailable

    def boom() -> Any:
        raise _KipyUnavailable("no kipy")

    set_kicad_factory_for_tests(boom)
    status = KipyPlacer().apply_to_board(_board_with(["U1"]))
    assert status.code == "kipy_unavailable"


def test_apply_to_board_reports_kicad_unreachable() -> None:
    def boom() -> Any:
        raise ConnectionRefusedError("nope")

    set_kicad_factory_for_tests(boom)
    status = KipyPlacer().apply_to_board(_board_with(["U1"]))
    assert status.code == "kicad_unreachable"


def test_apply_to_board_reports_commit_failed_on_push_exception() -> None:
    fake_board = _FakeBoard(["U1"])
    # Override push_commit to blow up.
    def explode(*_a: object, **_k: object) -> None:
        raise RuntimeError("ipc transport died")
    fake_board.push_commit = explode  # type: ignore[method-assign]
    set_kicad_factory_for_tests(lambda: _FakeKicad(fake_board))

    status = KipyPlacer().apply_to_board(_board_with(["U1"]))
    assert status.code == "commit_failed"
    assert "ipc transport died" in status.detail


def test_apply_placements_writes_position_in_millimeters() -> None:
    """25 mm → 25_000_000 nm via Vector2.from_xy_mm() (kipy stores nm)."""
    fake_board = _FakeBoard(["U1"])
    set_kicad_factory_for_tests(lambda: _FakeKicad(fake_board))

    KipyPlacer().apply_placements([Placement(refdes="U1", x_mm=25.0, y_mm=10.0)])
    fp = fake_board._fps[0]
    assert fp.position.x == 25_000_000
    assert fp.position.y == 10_000_000


def test_apply_placements_uses_from_xy_fallback_when_from_xy_mm_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A kipy build without ``Vector2.from_xy_mm`` falls back to the nm ``from_xy``."""

    class _OldVector2:
        """Stub kipy Vector2 exposing only the nanometre constructor."""

        def __init__(self, x: int, y: int) -> None:
            self.x = x
            self.y = y

        @classmethod
        def from_xy(cls, x_nm: int, y_nm: int) -> _OldVector2:
            return cls(x_nm, y_nm)

    monkeypatch.setattr("kipy.geometry.Vector2", _OldVector2)

    fake_board = _FakeBoard(["U1"])
    set_kicad_factory_for_tests(lambda: _FakeKicad(fake_board))

    status = KipyPlacer().apply_placements(
        [Placement(refdes="U1", x_mm=25.0, y_mm=10.0)]
    )

    assert status.ok
    fp = fake_board._fps[0]
    assert isinstance(fp.position, _OldVector2)
    assert fp.position.x == 25_000_000
    assert fp.position.y == 10_000_000


def test_apply_to_board_honors_hint_south_edge() -> None:
    """Declarative hints flow through plan_placement to the live PCB."""
    fake_board = _FakeBoard(["J1"])
    set_kicad_factory_for_tests(lambda: _FakeKicad(fake_board))

    board = Board(name="t", components=[
        Component(refdes="J1", mpn="USB-C", placement_hint="south edge"),
    ])
    placer = KipyPlacer(board_width_mm=50.0, board_height_mm=40.0)
    status = placer.apply_to_board(board)

    assert status.ok
    fp = fake_board._fps[0]
    # plan_placement for "south edge" → (width/2, height - 2.0)
    assert fp.position.x == 25_000_000
    assert fp.position.y == 38_000_000


# ---------------------------------------------------------------------------
# autoplace_board convenience
# ---------------------------------------------------------------------------


def test_autoplace_board_helper_runs_the_full_pipeline() -> None:
    fake_board = _FakeBoard(["U1", "C1"])
    set_kicad_factory_for_tests(lambda: _FakeKicad(fake_board))

    status = autoplace_board(_board_with(["U1", "C1"]))

    assert isinstance(status, KipyStatus)
    assert status.ok
    assert sorted(status.moved) == ["C1", "U1"]


# ---------------------------------------------------------------------------
# MCP tool integration — uses the same fake factory.
# ---------------------------------------------------------------------------


def test_mcp_tool_autoplace_reports_unavailable_when_kipy_missing() -> None:
    """Default factory has no kipy installed in tests → unavailable."""
    from pathlib import Path

    from ki_mcp_pcb_server.tools import tool_autoplace

    examples = Path(__file__).resolve().parents[1] / "examples"
    out = tool_autoplace(str(examples / "blinky.yaml"))
    # In the test env, kipy isn't installed → code is "kipy_unavailable".
    # Any non-ok code is fine here — the contract is "structured, never raises".
    assert out["ok"] is False
    assert "code" in out
    assert isinstance(out["moved"], list)
    assert isinstance(out["skipped"], list)


def test_mcp_tool_autoplace_happy_path_with_fake_factory() -> None:
    from pathlib import Path

    from ki_mcp_pcb_server.tools import tool_autoplace

    examples = Path(__file__).resolve().parents[1] / "examples"
    # All blinky refs accepted by the fake board.
    from ki_mcp_pcb_core.parsers.yaml import parse_yaml as _py
    blinky = _py(examples / "blinky.yaml")
    refs = [c.refdes for c in blinky.components]
    fake_board = _FakeBoard(refs)
    set_kicad_factory_for_tests(lambda: _FakeKicad(fake_board))

    out = tool_autoplace(str(examples / "blinky.yaml"))
    assert out["ok"] is True
    assert out["code"] == "ok"
    assert sorted(out["moved"]) == sorted(refs)
