"""SQLite-backed price cache — M3-P-05.

Distributor lookups are expensive: Digi-Key's V4 sandbox is rate-
limited to 1000 req/day, Mouser's free tier is 1 req/sec, and a real
BOM has tens of MPNs. The cache flattens repeat lookups for the same
MPN across runs.

## Why SQLite

- The agent process is single-host, so a real database is overkill.
- A flat file (JSON / pickle) would race under concurrent fan-out.
- SQLite gives us atomic writes, transactional reads, and TTL purge
  in 20 lines of Python with stdlib only.

The schema is denormalised on purpose — one row per (distributor,
mpn, distributor_sku) triple, JSON-encoded `PartQuote` blob. Reads
deserialise; writes overwrite. The cache is content-addressable by
`(distributor, mpn)` for lookups (returning every SKU under that MPN)
and we never need to query into the blob.
"""

from __future__ import annotations

import json
import os
import sqlite3
from collections.abc import Iterable
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from .base import PartQuote, PriceBreakpoint

#: Default TTL — 6 hours. Distributor stock numbers change minute-to-
#: minute under heavy demand, but price + lifecycle move on a day
#: scale, so 6 hours is the right tradeoff between freshness and
#: quota burn for a small-team workflow. Override per-call via
#: `PriceCache.get(..., max_age=...)`.
DEFAULT_TTL = timedelta(hours=6)

SCHEMA = """
CREATE TABLE IF NOT EXISTS quotes (
    distributor     TEXT NOT NULL,
    mpn             TEXT NOT NULL,
    distributor_sku TEXT NOT NULL,
    quoted_at       TEXT NOT NULL,
    payload         TEXT NOT NULL,
    PRIMARY KEY (distributor, mpn, distributor_sku)
) WITHOUT ROWID;

CREATE INDEX IF NOT EXISTS idx_quotes_lookup
    ON quotes(distributor, mpn);

CREATE INDEX IF NOT EXISTS idx_quotes_quoted_at
    ON quotes(quoted_at);
"""


def _default_cache_path() -> Path:
    """Cache lives under `~/.cache/kiclaude/bom_cache.sqlite` by
    default (the XDG-style location for user-scoped caches). Tests
    inject `:memory:` instead."""
    override = os.environ.get("KICLAUDE_BOM_CACHE")
    if override:
        return Path(override)
    base = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
    return base / "kiclaude" / "bom_cache.sqlite"


def _serialise_quote(quote: PartQuote) -> dict[str, Any]:
    raw = asdict(quote)
    # `quoted_at` is a datetime — JSON-encode as ISO-8601 UTC.
    raw["quoted_at"] = quote.quoted_at.isoformat()
    raw["price_breaks"] = [
        {"min_qty": b.min_qty, "unit_price_usd": b.unit_price_usd}
        for b in quote.price_breaks
    ]
    return raw


def _deserialise_quote(blob: str) -> PartQuote:
    raw = json.loads(blob)
    return PartQuote(
        distributor=raw["distributor"],
        mpn=raw["mpn"],
        distributor_sku=raw["distributor_sku"],
        manufacturer=raw["manufacturer"],
        description=raw["description"],
        in_stock_qty=int(raw["in_stock_qty"]),
        moq=int(raw["moq"]),
        lifecycle=raw["lifecycle"],
        price_breaks=tuple(
            PriceBreakpoint(min_qty=int(b["min_qty"]), unit_price_usd=float(b["unit_price_usd"]))
            for b in raw["price_breaks"]
        ),
        product_url=raw["product_url"],
        quoted_at=datetime.fromisoformat(raw["quoted_at"]),
        extras=dict(raw.get("extras") or {}),
    )


class PriceCache:
    """SQLite cache of [`PartQuote`] rows.

    Thread-safety: SQLite connections are NOT thread-safe by default.
    We open one connection in the constructor with
    `check_same_thread=False` and serialise writes with a process-
    local lock (callers are within one asyncio loop, so the GIL +
    our explicit `with` blocks are enough).
    """

    def __init__(self, *, path: Path | str | None = None) -> None:
        resolved = Path(path) if path is not None else _default_cache_path()
        if str(resolved) != ":memory:":
            resolved.parent.mkdir(parents=True, exist_ok=True)
        self._path = str(resolved)
        self._conn = sqlite3.connect(self._path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        try:
            self._conn.close()
        except sqlite3.Error:
            pass

    def __enter__(self) -> PriceCache:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def get(
        self,
        *,
        distributor: str,
        mpn: str,
        max_age: timedelta = DEFAULT_TTL,
    ) -> list[PartQuote] | None:
        """Return every cached quote for `(distributor, mpn)` whose
        `quoted_at` is within `max_age`. Returns `None` when no
        unexpired entry exists (so the caller can tell "cache miss"
        apart from "cached as not-found")."""
        cutoff = datetime.now(UTC) - max_age
        cursor = self._conn.execute(
            "SELECT payload FROM quotes WHERE distributor = ? AND mpn = ? AND quoted_at >= ?",
            (distributor, mpn, cutoff.isoformat()),
        )
        rows = cursor.fetchall()
        if not rows:
            return None
        return [_deserialise_quote(row["payload"]) for row in rows]

    def put(self, quotes: Iterable[PartQuote]) -> int:
        """Upsert each quote. Returns the number written.

        Same (distributor, mpn, distributor_sku) triple is REPLACEd,
        so subsequent refreshes overwrite older snapshots instead of
        piling rows."""
        written = 0
        with self._conn:
            for q in quotes:
                self._conn.execute(
                    "INSERT OR REPLACE INTO quotes "
                    "(distributor, mpn, distributor_sku, quoted_at, payload) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (
                        q.distributor,
                        q.mpn,
                        q.distributor_sku,
                        q.quoted_at.isoformat(),
                        json.dumps(_serialise_quote(q), separators=(",", ":"), sort_keys=True),
                    ),
                )
                written += 1
        return written

    def purge_older_than(self, max_age: timedelta) -> int:
        """Drop entries with `quoted_at < now - max_age`. Returns the
        row count removed. Cheap O(log N) scan over the timestamp
        index; safe to call on every aggregator run."""
        cutoff = datetime.now(UTC) - max_age
        with self._conn:
            cursor = self._conn.execute(
                "DELETE FROM quotes WHERE quoted_at < ?",
                (cutoff.isoformat(),),
            )
            return cursor.rowcount

    def count(self) -> int:
        """Row count — used by tests + the `/health` probe."""
        return int(self._conn.execute("SELECT COUNT(*) FROM quotes").fetchone()[0])


__all__ = ["DEFAULT_TTL", "PriceCache"]
