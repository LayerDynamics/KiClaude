"""Distributor adapter base class + shared dataclasses — M3-P-05.

Every per-distributor adapter (Digi-Key, Mouser, Octopart, JLCPCB)
implements [`DistributorAdapter`][DistributorAdapter] and is plugged
into the [`PriceAggregator`][..aggregator.PriceAggregator] fan-out.
The aggregator is the only thing `kc_bom_price` knows about; it owns
the cache, the timeout policy, and the cheapest-mix selection.

## Why a per-call adapter ABC instead of one mega-client

- **Independent failure isolation.** A Digi-Key 503 must not block a
  Mouser response. The aggregator races each adapter under its own
  timeout and falls back to whatever returned.
- **Independent rate-limiting.** Digi-Key's v4 has its own quota
  (1000 req/day on sandbox); Mouser is 1 req/sec free tier. The
  adapter owns its own throttle.
- **Independent caching key spaces.** A Digi-Key part number isn't a
  Mouser part number, even when they're the same physical chip.
  Cache entries are namespaced by `(distributor, mpn)`.

## What the adapter MUST return

Every successful lookup yields one or more [`PartQuote`] rows. The
aggregator does its own dedup + cheapest-mix selection across rows;
adapters do not need to merge results across distributors.

The MPN passed in is the manufacturer's part number (e.g.
`STM32F103C8T6`); adapters resolve that to their own internal SKU
(Digi-Key's `497-6063-ND`, Mouser's `511-STM32F103C8T6`, etc.) and
include it as `distributor_sku` in the returned quotes.

## Failure modes

- Auth fails (no credentials in env) → raise
  [`DistributorAuthError`][DistributorAuthError]. The aggregator
  surfaces this once per distributor + skips that distributor for the
  rest of the call.
- Network/HTTP error → raise
  [`DistributorTransportError`][DistributorTransportError]. Aggregator
  treats this as a soft fail.
- Part not found at the distributor → return an empty list. NOT an
  exception — a missing part is a real, common outcome and the
  aggregator just notes the absence per (distributor, mpn).
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(frozen=True, slots=True)
class PriceBreakpoint:
    """One row on a distributor's quantity-pricing ladder.

    Distributors quote `unit_price_usd` at `min_qty` and apply that
    price for every order with `qty ≥ min_qty` and `qty < next_break`.
    The aggregator handles the lookup against the requested quantity.
    """

    min_qty: int
    unit_price_usd: float


@dataclass(frozen=True, slots=True)
class PartQuote:
    """One MPN ↔ distributor offer at a point in time."""

    distributor: str
    """`"digikey"`, `"mouser"`, `"octopart"`, `"jlcpcb"`."""

    mpn: str
    """The MPN the user asked for (echoed back so the aggregator can
    key results without losing track)."""

    distributor_sku: str
    """The distributor's own part number — DK `497-6063-ND`, Mouser
    `511-STM32F103C8T6`, etc. Empty string when the distributor
    didn't return one."""

    manufacturer: str
    description: str
    in_stock_qty: int
    moq: int
    """Minimum order quantity per distributor policy."""

    lifecycle: str
    """`"active"`, `"nrnd"`, `"obsolete"`, `"preview"`, `"unknown"`.
    Maps each distributor's status taxonomy onto the JEDEC-ish
    canonical set."""

    price_breaks: tuple[PriceBreakpoint, ...]
    """Quantity-price ladder, sorted by `min_qty` ascending."""

    product_url: str
    """Full URL to the distributor's product detail page — surfaced
    in the BOM panel so the user can click through."""

    quoted_at: datetime
    """When the distributor returned this quote. Used by the cache
    TTL check."""

    extras: dict[str, Any] = field(default_factory=dict)
    """Distributor-specific fields the aggregator doesn't model
    (RoHS status, REACH, packaging code, etc.). Preserved for the
    UI to surface without forcing an ABC change."""

    def unit_price_at_qty(self, qty: int) -> float | None:
        """Look up the unit price for an order of `qty`. Returns
        `None` when `qty < moq` or the price ladder is empty."""
        if qty < self.moq or not self.price_breaks:
            return None
        # price_breaks is sorted ascending — pick the highest break
        # with min_qty <= qty.
        winning: PriceBreakpoint | None = None
        for brk in self.price_breaks:
            if brk.min_qty <= qty:
                winning = brk
            else:
                break
        return winning.unit_price_usd if winning is not None else None


class DistributorError(Exception):
    """Base for every adapter-raised exception."""


class DistributorAuthError(DistributorError):
    """Adapter has no credentials or the credentials are rejected."""


class DistributorTransportError(DistributorError):
    """Network / HTTP layer failed — the aggregator soft-fails and
    moves on."""


class DistributorAdapter(abc.ABC):
    """Per-distributor lookup contract.

    Implementations are stateless except for an HTTP client + token
    cache. The aggregator instantiates each adapter once per
    `PriceAggregator` instance and reuses it across calls.
    """

    #: Short canonical name — matches `PartQuote.distributor`. Must
    #: be lowercase, no spaces.
    name: str

    @abc.abstractmethod
    async def lookup(self, mpn: str) -> list[PartQuote]:
        """Return every available quote for `mpn` at this distributor.

        Multiple quotes are possible when one MPN maps to several
        distributor SKUs (cut tape vs reel, JLC global vs basic, etc.).
        Empty list = part not found — NOT an exception.
        """

    @abc.abstractmethod
    async def aclose(self) -> None:
        """Release any held resources (HTTP clients, sockets). The
        aggregator calls this at teardown."""
