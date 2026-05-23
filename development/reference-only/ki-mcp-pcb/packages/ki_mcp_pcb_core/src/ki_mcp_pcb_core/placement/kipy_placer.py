"""KiCad IPC auto-placement via the ``kicad-python`` (kipy) API.

This is the *online* counterpart to the pure-Python :func:`plan_placement`.
``plan_placement`` decides where each component *should* go based on
declarative hints; ``KipyPlacer`` actually moves the corresponding
``FootprintInstance`` objects in a *running* KiCad's PCB editor.

Why is this its own module? Because kipy requires:

  * KiCad 9+ installed locally
  * The ``kicad-python`` PyPI package installed in the current env
  * A KiCad instance running on the same machine with the IPC API enabled
    (Preferences → API → Enable IPC; this is opt-in until KiCad 10).

None of those can be assumed in CI, on Linux servers, or inside a sandbox.
So everything here lazy-imports kipy, every public entry point reports a
structured :class:`KipyStatus` rather than raising, and tests inject a
fake kipy client via :func:`set_kicad_factory_for_tests`.

Connection model
----------------
KiCad's IPC is a per-document RPC over a local socket. The high-level
flow we use:

  1. ``KiCad()`` — open a client; raises if no KiCad is listening.
  2. ``kicad.get_board()`` — handle to the currently-open PCB document.
  3. ``board.begin_commit()`` — start a transaction.
  4. mutate ``FootprintInstance.position`` for each refdes we want to move
     (kipy stores positions in nanometers, but exposes ``from_xy_mm`` on
     ``Vector2``).
  5. ``board.update_items(...)`` to push the in-memory changes back.
  6. ``board.push_commit(commit)`` — apply atomically; user sees one undo.

We don't add or remove footprints from inside this module — that's a
populator concern. We only move ones already in the PCB.

Stability stance
----------------
The kipy API is still evolving (KiCad 9.x). To stay forward-compatible,
the actual attribute accesses on kipy objects are kept inside small
internal helpers, so a future shape change is one-place editable. The
public surface (``KipyStatus``, ``KipyPlacer``, ``probe``) is stable.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from ki_mcp_pcb_core.cir.models import Board, Component
from ki_mcp_pcb_core.placement import Placement, plan_placement

# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


KipyStatusCode = Literal[
    "ok",
    "kipy_unavailable",     # ``kicad-python`` not installed
    "kicad_unreachable",    # kipy installed, but no KiCad listening
    "no_open_board",        # KiCad running, but no board open
    "no_matching_refdes",   # We tried to move components, none matched
    "commit_failed",        # Connection OK, but the transaction blew up
]


@dataclass(frozen=True)
class KipyStatus:
    """Outcome of a kipy operation.

    All public entry points return one of these; nothing raises out of
    this module under normal use. Tests rely on this.
    """

    code: KipyStatusCode
    detail: str = ""
    kicad_version: str | None = None
    moved: list[str] = field(default_factory=list)   # refdes that we moved
    skipped: list[str] = field(default_factory=list) # refdes we couldn't find

    @property
    def ok(self) -> bool:
        return self.code == "ok"


# ---------------------------------------------------------------------------
# Test seam: factory for the kipy client
# ---------------------------------------------------------------------------


# A factory returns a ``KiCad``-like client. We accept ``Any`` because we
# can't depend on kipy at import time and because the test fake doesn't
# have to inherit from anything kipy-specific.
KicadFactory = Callable[[], Any]


def _default_factory() -> Any:
    """Try to instantiate the real :class:`kipy.KiCad` client.

    Raises if kipy is not installed, or if no KiCad is listening on the
    local IPC socket. Callers must handle both via :class:`KipyStatus`.
    """
    try:
        from kipy import KiCad
    except ImportError as exc:
        raise _KipyUnavailable(str(exc)) from exc
    return KiCad()  # may raise on no listener — caller catches generically


_factory: KicadFactory = _default_factory


def set_kicad_factory_for_tests(factory: KicadFactory | None) -> KicadFactory:
    """Swap the factory used by :class:`KipyPlacer`. Returns the previous one.

    Pass ``None`` to restore the real one.
    """
    global _factory
    prev = _factory
    _factory = factory or _default_factory
    return prev


class _KipyUnavailable(ImportError):
    """Internal marker — kipy isn't installed. Surfaces as
    ``KipyStatus(code="kipy_unavailable")``."""


# ---------------------------------------------------------------------------
# Public probe
# ---------------------------------------------------------------------------


def probe() -> KipyStatus:
    """Cheap connectivity check. Used by ``kimp doctor`` and the CLI.

    Tries to open a kipy client, asks for a version string, and returns
    without touching any board. Costs ~one socket round-trip when KiCad
    is running, instantaneous otherwise.
    """
    try:
        client = _factory()
    except _KipyUnavailable as exc:
        return KipyStatus(code="kipy_unavailable", detail=str(exc))
    except Exception as exc:
        return KipyStatus(code="kicad_unreachable", detail=str(exc))
    version = _client_version(client)
    return KipyStatus(code="ok", detail="kipy connected", kicad_version=version)


def _client_version(client: Any) -> str | None:
    """Try a few common kipy version accessors, fall back to ``None``.

    kipy's exact spelling of "version" has wobbled across pre-1.0
    releases; we tolerate any of them without breaking callers.
    """
    for attr in ("get_version", "version"):
        v = getattr(client, attr, None)
        if v is None:
            continue
        try:
            value = v() if callable(v) else v
        except Exception:
            continue
        if value is not None:
            return str(value)
    return None


# ---------------------------------------------------------------------------
# Placer
# ---------------------------------------------------------------------------


class KipyPlacer:
    """Apply a planned placement to a live KiCad PCB via IPC.

    Typical use::

        placer = KipyPlacer()
        status = placer.apply_to_board(board)
        if not status.ok:
            print(status.code, status.detail)

    The placer never opens or saves a PCB file directly; KiCad owns the
    document. The user is expected to have the project's ``.kicad_pcb``
    open in KiCad's PCB editor before invoking.
    """

    def __init__(
        self,
        *,
        board_width_mm: float = 50.0,
        board_height_mm: float = 40.0,
        spacing_mm: float = 15.0,
    ) -> None:
        self.board_width_mm = board_width_mm
        self.board_height_mm = board_height_mm
        self.spacing_mm = spacing_mm

    # -- Public API ---------------------------------------------------------

    def apply_to_board(self, board: Board) -> KipyStatus:
        """Plan placement from declarative hints, then push it via kipy."""
        plan = plan_placement(
            board.components,
            board_width_mm=self.board_width_mm,
            board_height_mm=self.board_height_mm,
            spacing_mm=self.spacing_mm,
        )
        return self.apply_placements(plan)

    def apply_components(self, components: Iterable[Component]) -> KipyStatus:
        """Like :meth:`apply_to_board` but for a subset of components."""
        plan = plan_placement(
            list(components),
            board_width_mm=self.board_width_mm,
            board_height_mm=self.board_height_mm,
            spacing_mm=self.spacing_mm,
        )
        return self.apply_placements(plan)

    def apply_placements(self, placements: Iterable[Placement]) -> KipyStatus:
        """Push pre-computed placements. Wraps the whole batch in one commit."""
        try:
            client = _factory()
        except _KipyUnavailable as exc:
            return KipyStatus(code="kipy_unavailable", detail=str(exc))
        except Exception as exc:
            return KipyStatus(code="kicad_unreachable", detail=str(exc))

        try:
            kicad_board = client.get_board()
        except Exception as exc:
            return KipyStatus(code="no_open_board", detail=str(exc))
        if kicad_board is None:
            return KipyStatus(code="no_open_board",
                              detail="kipy returned no board handle")

        return _commit_placements(kicad_board, list(placements))


# ---------------------------------------------------------------------------
# Internal: commit / move
# ---------------------------------------------------------------------------


def _commit_placements(
    kicad_board: Any,
    placements: list[Placement],
) -> KipyStatus:
    """Move footprints inside a single kipy commit.

    Defensive: kipy will tolerate moving zero items, but we still want
    to surface "no refdes matched" so the user knows their PCB wasn't
    silently left alone because of a typo.
    """
    try:
        footprints = list(kicad_board.get_footprints())
    except Exception as exc:
        return KipyStatus(code="commit_failed",
                          detail=f"get_footprints failed: {exc}")

    by_refdes = _index_footprints_by_refdes(footprints)
    target_by_refdes = {p.refdes: p for p in placements}

    moved: list[str] = []
    skipped: list[str] = []
    items_to_update: list[Any] = []

    for refdes, target in target_by_refdes.items():
        fp = by_refdes.get(refdes)
        if fp is None:
            skipped.append(refdes)
            continue
        if not _set_footprint_position_mm(fp, target.x_mm, target.y_mm):
            skipped.append(refdes)
            continue
        moved.append(refdes)
        items_to_update.append(fp)

    if not moved:
        return KipyStatus(
            code="no_matching_refdes",
            detail=(f"{len(placements)} placements requested; "
                    f"matched 0 footprints in the open board"),
            moved=moved,
            skipped=skipped,
        )

    try:
        commit = kicad_board.begin_commit()
        kicad_board.update_items(items_to_update)
        kicad_board.push_commit(commit, "kimp autoplace")
    except TypeError:
        # Older kipy: push_commit takes no message argument.
        try:
            kicad_board.push_commit(commit)
        except Exception as exc:
            return KipyStatus(code="commit_failed", detail=str(exc),
                              moved=moved, skipped=skipped)
    except Exception as exc:
        return KipyStatus(code="commit_failed", detail=str(exc),
                          moved=moved, skipped=skipped)

    return KipyStatus(code="ok",
                      detail=f"placed {len(moved)} footprint(s)",
                      moved=moved, skipped=skipped)


def _index_footprints_by_refdes(footprints: list[Any]) -> dict[str, Any]:
    """Map ``"U1"``-style reference → :class:`FootprintInstance`.

    kipy went through a couple of shapes for the reference field; we
    probe in order: ``reference_field.text`` (current), ``reference``
    (older), and finally ``ref``.
    """
    out: dict[str, Any] = {}
    for fp in footprints:
        ref = _footprint_reference(fp)
        if ref:
            out[ref] = fp
    return out


def _footprint_reference(fp: Any) -> str | None:
    rf = getattr(fp, "reference_field", None)
    if rf is not None:
        text = getattr(rf, "text", None) or getattr(rf, "value", None)
        if isinstance(text, str) and text:
            return text
    for attr in ("reference", "ref"):
        val = getattr(fp, attr, None)
        if isinstance(val, str) and val:
            return val
    return None


def _set_footprint_position_mm(fp: Any, x_mm: float, y_mm: float) -> bool:
    """Assign ``fp.position`` from millimetres. Returns False on schema mismatch.

    kipy stores positions in nanometres and builds a :class:`Vector2`
    through classmethod constructors. We prefer ``from_xy_mm`` (millimetre
    input); a kipy build predating it still exposes the nanometre
    ``from_xy``, so we convert and use that. Either way ``fp.position`` is
    *replaced* with a fresh ``Vector2`` — the same model the live-PCB
    commit path uses — never mutated in place.

    The import is unguarded on purpose: this helper only runs after
    :func:`_default_factory` already imported kipy successfully, so a
    failure here is a real bug worth a traceback, not a silent skip.
    """
    from kipy.geometry import Vector2

    try:
        if hasattr(Vector2, "from_xy_mm"):
            position = Vector2.from_xy_mm(x_mm, y_mm)
        elif hasattr(Vector2, "from_xy"):
            # Older kipy: only the nanometre constructor exists.
            position = Vector2.from_xy(
                round(x_mm * 1_000_000), round(y_mm * 1_000_000)
            )
        else:
            return False
        fp.position = position
    except Exception:
        return False
    return True


# ---------------------------------------------------------------------------
# Convenience
# ---------------------------------------------------------------------------


def autoplace_board(
    board: Board,
    *,
    board_width_mm: float = 50.0,
    board_height_mm: float = 40.0,
    spacing_mm: float = 15.0,
) -> KipyStatus:
    """One-shot helper used by the CLI and the MCP tool.

    Equivalent to constructing a :class:`KipyPlacer` and calling
    :meth:`apply_to_board`.
    """
    return KipyPlacer(
        board_width_mm=board_width_mm,
        board_height_mm=board_height_mm,
        spacing_mm=spacing_mm,
    ).apply_to_board(board)


def autoplace_from_project(
    project_path: Path,
    *,
    board_width_mm: float = 50.0,
    board_height_mm: float = 40.0,
    spacing_mm: float = 15.0,
) -> KipyStatus:
    """Convenience wrapper that loads CIR from ``project_path`` and plans.

    Uses :class:`KiCadBackend.read_project` so the call works against an
    already-emitted project on disk. The PCB the placer talks to via IPC
    must be the same one the user has open in KiCad.
    """
    from ki_mcp_pcb_core.backends.kicad import KiCadBackend

    board = KiCadBackend().read_project(Path(project_path))
    return autoplace_board(
        board,
        board_width_mm=board_width_mm,
        board_height_mm=board_height_mm,
        spacing_mm=spacing_mm,
    )


__all__ = [
    "KicadFactory",
    "KipyPlacer",
    "KipyStatus",
    "KipyStatusCode",
    "autoplace_board",
    "autoplace_from_project",
    "probe",
    "set_kicad_factory_for_tests",
]
