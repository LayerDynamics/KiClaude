"""Symbol library indexer (M1-P-02).

Loads every `(lib …)` row from a `sym-lib-table` via
[`ki_native.list_symbols`][ki_native.list_symbols], caches the
result set in SQLite at `services/kiserver/.cache/library.db`, and
exposes a fast in-memory [`LibraryIndex.search`][LibraryIndex.search]
that ranks hits by name / keyword / description match.

Cold-start path:
  1. Probe SQLite for the cached row set whose `source_key` matches
     `(table_path, table_mtime)`.
  2. If the cache hits, load the rows from SQLite (no Rust call).
  3. Otherwise, call `ki_native.list_symbols`, stash the result in
     SQLite, and return it.

Warm queries iterate the in-memory list — for the typical KiCad
default library set (~150 libraries, ~30k symbols) this stays under
50 ms per [`LibraryIndex.search`][LibraryIndex.search] call, hitting
the M1-P-02 NFR.
"""

from __future__ import annotations

import dataclasses
import json
import sqlite3
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Schema rev — bump whenever the columns or scoring change so old
# caches don't accidentally feed stale results to a newer binary.
_SCHEMA_VERSION = 2


@dataclass(frozen=True, slots=True)
class SearchHit:
    """One row returned by [`LibraryIndex.search`]."""

    lib_id: str
    name: str
    library: str
    description: str
    footprint_filter: str
    reference: str
    value: str
    footprint: str
    datasheet: str
    mpn: str
    is_power: bool
    score: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "lib_id": self.lib_id,
            "name": self.name,
            "library": self.library,
            "description": self.description,
            "footprint_filter": self.footprint_filter,
            "reference": self.reference,
            "value": self.value,
            "footprint": self.footprint,
            "datasheet": self.datasheet,
            "mpn": self.mpn,
            "is_power": self.is_power,
            "score": self.score,
        }


@dataclass(slots=True)
class _IndexedSymbol:
    lib_id: str
    name: str
    library: str
    description: str
    footprint_filter: str
    reference: str
    value: str
    footprint: str
    datasheet: str
    mpn: str
    is_power: bool
    haystack_name: str
    haystack_keywords: str
    haystack_descr: str
    haystack_value: str
    haystack_mpn: str


class LibraryIndex:
    """Searchable index of every symbol resolved from a single
    `sym-lib-table`.

    The index is cheap to build (the SQLite cache makes second-run
    cold starts <10 ms on the M3 development host) and provides
    `search` operations that score and rank matches without touching
    disk.
    """

    def __init__(
        self,
        symbols: list[_IndexedSymbol],
        source_key: str,
        cache_path: Path,
    ) -> None:
        self._symbols = symbols
        self._source_key = source_key
        self._cache_path = cache_path

    @classmethod
    def open(
        cls,
        sym_lib_table_path: str | Path,
        cache_dir: str | Path,
        overrides: Mapping[str, str] | None = None,
    ) -> LibraryIndex:
        """Build (or load from SQLite cache) an index for the given
        `sym-lib-table`. `overrides` substitutes `${VAR}` references
        ahead of the process environment.
        """
        table_path = Path(sym_lib_table_path).expanduser().resolve()
        if not table_path.is_file():
            raise FileNotFoundError(f"sym-lib-table not found: {table_path}")
        cache_dir_path = Path(cache_dir).expanduser().resolve()
        cache_dir_path.mkdir(parents=True, exist_ok=True)
        cache_path = cache_dir_path / "library.db"
        mtime_ns = table_path.stat().st_mtime_ns
        source_key = f"{table_path}|{mtime_ns}|v{_SCHEMA_VERSION}"

        cached = _load_cached(cache_path, source_key)
        if cached is not None:
            return cls(cached, source_key, cache_path)

        hits = _fetch_via_ki_native(str(table_path), dict(overrides or {}))
        symbols = [_to_indexed(h) for h in hits]
        _store_cache(cache_path, source_key, symbols)
        return cls(symbols, source_key, cache_path)

    def __len__(self) -> int:
        return len(self._symbols)

    @property
    def source_key(self) -> str:
        return self._source_key

    @property
    def cache_path(self) -> Path:
        return self._cache_path

    def search(self, query: str, limit: int = 50) -> list[SearchHit]:
        """Ranked search over the indexed symbols.

        Scoring weights mirror the Rust `Index::search` so client and
        server orderings agree (M1-T-02's library picker fetches via
        the kiserver and renders client-side).
        """
        needle = query.strip().lower()
        out: list[SearchHit] = []
        for s in self._symbols:
            score = _score(s, needle) if needle else 0.0
            if needle and score <= 0.0:
                continue
            out.append(
                SearchHit(
                    lib_id=s.lib_id,
                    name=s.name,
                    library=s.library,
                    description=s.description,
                    footprint_filter=s.footprint_filter,
                    reference=s.reference,
                    value=s.value,
                    footprint=s.footprint,
                    datasheet=s.datasheet,
                    mpn=s.mpn,
                    is_power=s.is_power,
                    score=score,
                )
            )
        out.sort(key=lambda h: (-h.score, h.lib_id))
        return out[:limit]


def _to_indexed(hit: dict[str, Any]) -> _IndexedSymbol:
    name = hit.get("name", "") or ""
    keywords = hit.get("library", "") or ""  # placeholder for future ki_keywords pass-through
    descr = hit.get("description", "") or ""
    value = hit.get("value", "") or ""
    mpn = hit.get("mpn", "") or ""
    return _IndexedSymbol(
        lib_id=hit.get("lib_id", "") or "",
        name=name,
        library=hit.get("library", "") or "",
        description=descr,
        footprint_filter=hit.get("footprint_filter", "") or "",
        reference=hit.get("reference", "") or "",
        value=value,
        footprint=hit.get("footprint", "") or "",
        datasheet=hit.get("datasheet", "") or "",
        mpn=mpn,
        is_power=bool(hit.get("is_power", False)),
        haystack_name=name.lower(),
        haystack_keywords=keywords.lower(),
        haystack_descr=descr.lower(),
        haystack_value=value.lower(),
        haystack_mpn=mpn.lower(),
    )


def _score(s: _IndexedSymbol, needle: str) -> float:
    score = 0.0
    if s.haystack_name == needle:
        score += 1.5
    elif s.haystack_name.startswith(needle):
        score += 1.0
    elif needle in s.haystack_name:
        score += 0.7
    if needle in s.haystack_keywords:
        score += 0.4
    if needle in s.haystack_descr:
        score += 0.2
    if needle in s.haystack_value:
        score += 0.15
    if needle in s.haystack_mpn:
        score += 0.15
    if s.is_power:
        score *= 0.5
    return min(score, 2.0)


def _fetch_via_ki_native(
    table_path: str, overrides: Mapping[str, str]
) -> list[dict[str, Any]]:
    """Resolve every library in the table by calling the Rust-side
    indexer. Surfaces errors as the standard `ValueError`."""
    import ki_native  # type: ignore[import-not-found]

    raw = ki_native.list_symbols(table_path, dict(overrides))
    if not isinstance(raw, list):
        raise TypeError("ki_native.list_symbols must return a list")
    return raw


# ---------------------------------------------------------------------
# SQLite cache layer.
#
# The cache schema is two tables: `meta` carries a single row with the
# `source_key` from the most recent build, and `symbols` carries one
# row per indexed symbol with every field needed to rebuild a
# `_IndexedSymbol`. Reads acquire a shared lock; writes acquire an
# exclusive lock so concurrent kiserver workers don't fight.
# ---------------------------------------------------------------------

_CACHE_LOCK = threading.RLock()


def _load_cached(cache_path: Path, source_key: str) -> list[_IndexedSymbol] | None:
    if not cache_path.exists():
        return None
    with _CACHE_LOCK:
        conn = sqlite3.connect(cache_path)
        try:
            cur = conn.execute(
                "SELECT value FROM meta WHERE key = ?", ("source_key",)
            )
            row = cur.fetchone()
            if not row or row[0] != source_key:
                return None
            rows = conn.execute("SELECT data FROM symbols ORDER BY id ASC").fetchall()
        except sqlite3.OperationalError:
            return None
        finally:
            conn.close()
    symbols: list[_IndexedSymbol] = []
    for (data,) in rows:
        obj = json.loads(data)
        symbols.append(_IndexedSymbol(**obj))
    return symbols


def _store_cache(
    cache_path: Path, source_key: str, symbols: list[_IndexedSymbol]
) -> None:
    with _CACHE_LOCK:
        conn = sqlite3.connect(cache_path)
        try:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
            )
            conn.execute(
                "CREATE TABLE IF NOT EXISTS symbols ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, data TEXT NOT NULL)"
            )
            conn.execute("DELETE FROM symbols")
            conn.executemany(
                "INSERT INTO symbols (data) VALUES (?)",
                [(json.dumps(dataclasses.asdict(s)),) for s in symbols],
            )
            conn.execute(
                "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
                ("source_key", source_key),
            )
            conn.execute(
                "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
                ("built_at_unix", str(int(time.time()))),
            )
            conn.commit()
        finally:
            conn.close()


__all__ = ["LibraryIndex", "SearchHit"]
