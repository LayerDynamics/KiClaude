"""JLC / LCSC parts catalog lookup.

JLC publishes a CSV of available parts ("LCSC Parts Library"). We
download it once, cache it locally, and provide a fast lookup by LCSC
number. No API key needed.

Cache location: ``$KIMP_CACHE`` (defaults to ``~/.cache/kimp/``). The
CSV is indexed on first read.

Schema we parse (subset; the real CSV has 20+ columns):

    LCSC Part Number, First Category, Second Category, MFR.Part,
    Package, Solder Joint, Manufacturer, Library Type, Description,
    Datasheet, Price (USD), Stock

Tests mock the CSV by pointing ``KIMP_JLC_CSV`` at a fixture file.
"""

from __future__ import annotations

import csv
import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

JLC_CSV_ENV = "KIMP_JLC_CSV"
JLC_CACHE_DIR_ENV = "KIMP_CACHE"


class JLCLookupError(RuntimeError):
    """The catalog couldn't be loaded (missing file, bad format)."""


@dataclass(frozen=True)
class JLCPart:
    lcsc: str
    mpn: str
    description: str
    package: str
    unit_price_usd: float
    stock: int
    library_type: str  # "Basic" or "Extended" (drives JLC assembly fees)


# ---------------------------------------------------------------------------
# Cache + index
# ---------------------------------------------------------------------------


def _cache_dir() -> Path:
    override = os.environ.get(JLC_CACHE_DIR_ENV)
    if override:
        return Path(override)
    return Path.home() / ".cache" / "kimp"


def _csv_path() -> Path:
    override = os.environ.get(JLC_CSV_ENV)
    if override:
        return Path(override)
    return _cache_dir() / "jlc_parts.csv"


@lru_cache(maxsize=1)
def _index() -> dict[str, JLCPart]:
    """Build an in-memory index keyed by LCSC number.

    Uses ``functools.lru_cache`` so the CSV is parsed at most once per
    process. Tests reset it via :func:`_reset_cache_for_tests`.
    """
    path = _csv_path()
    if not path.exists():
        raise JLCLookupError(
            f"JLC catalog not found at {path}. Download it from "
            "https://yaqwsx.github.io/jlcparts/ or set KIMP_JLC_CSV "
            "to point at a local CSV."
        )
    out: dict[str, JLCPart] = {}
    with path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            lcsc = (row.get("LCSC Part Number") or row.get("LCSC") or "").strip()
            if not lcsc:
                continue
            try:
                price = float(row.get("Price (USD)") or row.get("Price") or 0)
            except ValueError:
                price = 0.0
            try:
                stock = int(row.get("Stock") or 0)
            except ValueError:
                stock = 0
            out[lcsc] = JLCPart(
                lcsc=lcsc,
                mpn=(row.get("MFR.Part") or row.get("MPN") or "").strip(),
                description=(row.get("Description") or "").strip(),
                package=(row.get("Package") or "").strip(),
                unit_price_usd=price,
                stock=stock,
                library_type=(row.get("Library Type") or "").strip(),
            )
    return out


def _reset_cache_for_tests() -> None:
    """Test hook to invalidate the lru_cache when env vars change."""
    _index.cache_clear()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def lookup_by_lcsc(lcsc: str) -> JLCPart | None:
    """Return the JLC part for ``lcsc`` (e.g. ``"C14663"``), or None.

    Raises :class:`JLCLookupError` if the catalog file is unavailable.
    """
    return _index().get(lcsc.strip())


def is_available() -> bool:
    """Whether the catalog file exists in the current environment."""
    return _csv_path().exists()
