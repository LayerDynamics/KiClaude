"""KiCad symbol library lookup.

Reads ``.kicad_sym`` files via kiutils and exposes a fast index keyed
by ``"LibraryName:SymbolName"`` → ``{pin_number: (x_mm, y_mm)}``.

We need this so the schematic synthesizer can place global labels at
the *actual* pin locations of the resolved symbols, rather than at
heuristic offsets. Two layers:

  1. ``find_symbol_lib_paths()`` — locates KiCad's stock symbol libraries
     on disk. Searches:
       * ``$KICAD_SYMBOLS`` env override
       * Linux: ``/usr/share/kicad/symbols``
       * macOS: ``/Applications/KiCad/KiCad.app/Contents/SharedSupport/symbols``
       * Windows: ``C:/Program Files/KiCad/9.0/share/kicad/symbols``
       * ``./libs/`` (project-local fallback)
  2. ``SymbolLibIndex`` — caches parsed libraries on demand. Calling
     ``index.pin_positions("Device:R")`` returns the pin map or None.

All operations gracefully degrade when no libraries are reachable —
callers fall back to the heuristic placement in ``sch_layout``.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

KICAD_SYMBOLS_ENV = "KICAD_SYMBOLS"

_DEFAULT_SEARCH_PATHS: tuple[str, ...] = (
    "/usr/share/kicad/symbols",
    "/usr/local/share/kicad/symbols",
    "/Applications/KiCad/KiCad.app/Contents/SharedSupport/symbols",
    "C:/Program Files/KiCad/9.0/share/kicad/symbols",
    "C:/Program Files/KiCad/8.0/share/kicad/symbols",
)


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


def find_symbol_lib_paths() -> list[Path]:
    """Return existing directories that contain ``.kicad_sym`` files.

    The first hit wins for any given library name; later directories
    are searched only if a name isn't found earlier.
    """
    candidates: list[Path] = []
    override = os.environ.get(KICAD_SYMBOLS_ENV)
    if override:
        candidates.append(Path(override))
    candidates.extend(Path(p) for p in _DEFAULT_SEARCH_PATHS)
    # Project-local libs/ — useful for fixtures and bespoke parts.
    here = Path.cwd() / "libs"
    if here.exists():
        candidates.append(here)
    return [p for p in candidates if p.is_dir()]


# ---------------------------------------------------------------------------
# Index
# ---------------------------------------------------------------------------


class SymbolLibIndex:
    """Lazy index of symbol pin coordinates.

    Holds a list of search paths; library files are parsed the first
    time a symbol from them is requested. Results are cached.
    """

    def __init__(self, search_paths: list[Path] | None = None) -> None:
        self.search_paths = search_paths if search_paths is not None else find_symbol_lib_paths()
        self._pin_cache: dict[str, dict[str, tuple[float, float]] | None] = {}

    def pin_positions(self, lib_id: str) -> dict[str, tuple[float, float]] | None:
        """``lib_id`` is the KiCad ``"LibName:SymbolName"`` string.

        Returns ``{pin_number: (x_mm, y_mm)}`` for the symbol, or None
        when the library or symbol isn't found.
        """
        if lib_id in self._pin_cache:
            return self._pin_cache[lib_id]
        lib_name, _, sym_name = lib_id.partition(":")
        if not lib_name or not sym_name:
            self._pin_cache[lib_id] = None
            return None
        lib_path = self._locate_library(lib_name)
        if lib_path is None:
            self._pin_cache[lib_id] = None
            return None
        positions = _extract_pin_positions(lib_path, sym_name)
        self._pin_cache[lib_id] = positions
        return positions

    def _locate_library(self, lib_name: str) -> Path | None:
        for parent in self.search_paths:
            candidate = parent / f"{lib_name}.kicad_sym"
            if candidate.is_file():
                return candidate
        return None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _extract_pin_positions(
    lib_path: Path, sym_name: str
) -> dict[str, tuple[float, float]] | None:
    """Parse ``lib_path`` and return the pin map for ``sym_name``.

    Lazy-imports kiutils so this module is importable even when kiutils
    isn't installed (in which case discovery still works but no symbol
    will resolve).
    """
    try:
        from kiutils.symbol import SymbolLib
    except ImportError:  # pragma: no cover — depends on env
        return None
    try:
        lib = SymbolLib.from_file(str(lib_path))
    except Exception:
        return None
    for sym in lib.symbols:
        if sym.entryName == sym_name:
            return _flatten_pins(sym)
    return None


def _flatten_pins(sym: object) -> dict[str, tuple[float, float]]:
    """Walk a Symbol's units, return ``{pin_number: (x, y)}``."""
    out: dict[str, tuple[float, float]] = {}
    units = getattr(sym, "units", []) or []
    for unit in units:
        for pin in getattr(unit, "pins", []) or []:
            num = getattr(pin, "number", None)
            pos = getattr(pin, "position", None)
            if num is None or pos is None:
                continue
            number_str = getattr(num, "number", None) if not isinstance(num, str) else num
            if not isinstance(number_str, str):
                number_str = str(num)
            x = float(getattr(pos, "X", 0.0))
            y = float(getattr(pos, "Y", 0.0))
            out[number_str] = (x, y)
    # Also walk top-level pins (some symbols put pins directly on Symbol).
    for pin in getattr(sym, "pins", []) or []:
        num = getattr(pin, "number", None)
        pos = getattr(pin, "position", None)
        if num is None or pos is None:
            continue
        number_str = getattr(num, "number", None) if not isinstance(num, str) else num
        if not isinstance(number_str, str):
            number_str = str(num)
        x = float(getattr(pos, "X", 0.0))
        y = float(getattr(pos, "Y", 0.0))
        out.setdefault(number_str, (x, y))
    return out


@lru_cache(maxsize=1)
def default_index() -> SymbolLibIndex:
    """Process-wide default index. Tests construct their own via ``SymbolLibIndex``."""
    return SymbolLibIndex()


__all__ = [
    "KICAD_SYMBOLS_ENV",
    "SymbolLibIndex",
    "default_index",
    "find_symbol_lib_paths",
]
