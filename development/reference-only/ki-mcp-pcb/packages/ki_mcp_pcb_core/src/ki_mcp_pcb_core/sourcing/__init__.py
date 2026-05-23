"""Component sourcing.

Two layers:

  1. :func:`check_sourcing` — fast CIR-level walkthrough. Uses the local
     ``libs/footprints.yaml`` registry to decide whether each component
     has a known LCSC mapping. Conservative; doesn't hit the network.
  2. :mod:`ki_mcp_pcb_core.sourcing.jlc` — optional live lookup against
     the LCSC parts catalog (free download, no API key). Caches the
     CSV locally; callers opt in via ``include_live_jlc=True``.

Per CLAUDE.md rule #6 ("every MPN must resolve"), :func:`check_sourcing`
returns a structured report; the pipeline fails closed on any
``"missing"`` entries before synthesis or fab.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal

from ki_mcp_pcb_core.cir.models import Board
from ki_mcp_pcb_core.synthesis.resolver import load_registry

Status = Literal[
    "in_stock_jlc",       # registry has LCSC + (optionally) live JLC confirms stock
    "registry_only",      # registry resolves but no LCSC known
    "missing",            # MPN not in registry and no explicit footprint
]


@dataclass(frozen=True)
class SourcingEntry:
    refdes: str
    mpn: str
    status: Status
    lcsc: str | None
    note: str = ""
    # Optional live data — only populated when include_live_jlc=True
    unit_price_usd: float | None = None
    stock: int | None = None


@dataclass(frozen=True)
class SourcingReport:
    entries: list[SourcingEntry]

    @property
    def ok(self) -> bool:
        return all(e.status != "missing" for e in self.entries)

    @property
    def missing(self) -> list[SourcingEntry]:
        return [e for e in self.entries if e.status == "missing"]


def check_sourcing(
    board: Board,
    *,
    registry_path: Path | None = None,
    include_live_jlc: bool = False,
) -> SourcingReport:
    """Walk the BOM and report each component's sourcing status.

    With ``include_live_jlc=True`` we additionally hit the JLC parts
    catalog (downloaded + cached locally) to fill in price and stock.
    No-op when the catalog can't be fetched — the entry keeps its
    registry-derived status and ``unit_price_usd`` / ``stock`` stay None.
    """
    registry = load_registry(registry_path)
    entries: list[SourcingEntry] = []
    for comp in board.components:
        entry = registry.get(comp.mpn)
        if entry is None and comp.footprint is None:
            entries.append(SourcingEntry(
                refdes=comp.refdes,
                mpn=comp.mpn,
                status="missing",
                lcsc=None,
                note="MPN not in libs/footprints.yaml and no explicit footprint set",
            ))
        elif entry and entry.lcsc:
            entries.append(SourcingEntry(
                refdes=comp.refdes, mpn=comp.mpn, status="in_stock_jlc",
                lcsc=entry.lcsc, note="LCSC mapping known",
            ))
        else:
            entries.append(SourcingEntry(
                refdes=comp.refdes, mpn=comp.mpn, status="registry_only",
                lcsc=None, note="Resolves in registry but no LCSC — needs distributor query",
            ))

    if include_live_jlc:
        entries = _enrich_with_live_jlc(entries)

    return SourcingReport(entries=entries)


def _enrich_with_live_jlc(entries: list[SourcingEntry]) -> list[SourcingEntry]:
    """Fill ``unit_price_usd`` + ``stock`` from the JLC parts catalog.

    Gracefully degrades when the catalog isn't available locally —
    each entry keeps whatever status the registry-derived pass gave it.
    """
    from ki_mcp_pcb_core.sourcing.jlc import JLCLookupError, lookup_by_lcsc

    out: list[SourcingEntry] = []
    for e in entries:
        if e.lcsc is None:
            out.append(e)
            continue
        try:
            info = lookup_by_lcsc(e.lcsc)
        except JLCLookupError:
            out.append(e)
            continue
        if info is None:
            out.append(replace(e, note=f"{e.note}; LCSC not found in catalog"))
            continue
        out.append(replace(
            e,
            unit_price_usd=info.unit_price_usd,
            stock=info.stock,
            note=(
                f"{e.note}; JLC live: ${info.unit_price_usd:.4f}/unit, "
                f"stock={info.stock}"
            ),
        ))
    return out
