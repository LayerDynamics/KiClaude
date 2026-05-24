"""Price aggregator + fan-out coordinator — M3-P-05.

Owns:

- The list of registered [`DistributorAdapter`][..base.DistributorAdapter]
  instances.
- The [`PriceCache`][..cache.PriceCache] (TTL'd SQLite).
- The per-adapter timeout policy + soft-fail handling.
- The "cheapest mix" selection across distributors for a BOM.

The aggregator is what `kc_bom_price` calls. Adapters never know
about each other.

## Fan-out semantics

`price(mpn)` fan-outs across every registered adapter under
`asyncio.gather(return_exceptions=True)`. Each adapter's lookup is
wrapped in `asyncio.wait_for(..., timeout)` so a hung Mouser doesn't
block a fast Digi-Key. Exceptions are caught + logged + soft-failed:

- [`DistributorAuthError`][..base.DistributorAuthError] surfaces once
  per (distributor, run) in the returned `errors` map. The next
  call still tries that distributor (so a credential-fix doesn't
  need a process restart) but no further auth errors are surfaced
  for it within the same call.
- [`DistributorTransportError`][..base.DistributorTransportError]
  surfaces the message; the aggregator returns whatever did succeed.
- Any other exception is captured with `type(e).__name__: str(e)`.

## Cheapest-mix selection

For a multi-part BOM (`price_bom([mpn1, mpn2, ...])`), the
aggregator picks the cheapest distributor per part at the requested
quantity, sums the line totals, and reports the per-distributor
spend so the user can collapse the order onto the fewest carts.

If a part has no live in-stock quote anywhere, it's reported with
`status="not_found"` and excluded from the total — the BOM panel
flags those rows in yellow without breaking the total.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import timedelta

import structlog

from .base import (
    DistributorAdapter,
    DistributorAuthError,
    DistributorError,
    DistributorTransportError,
    PartQuote,
)
from .cache import DEFAULT_TTL, PriceCache

log = structlog.get_logger(__name__)

#: Per-adapter timeout. Tuned so a fan-out of 4 distributors at 10s
#: each still fits the chat-turn budget (the panel will show a
#: spinner up to ~40s worst case, with anything faster surfacing as
#: it lands).
DEFAULT_PER_ADAPTER_TIMEOUT_S = 10.0


@dataclass(slots=True)
class PartPricing:
    """Aggregated pricing for one MPN across every distributor.

    `quotes` is the full list (one per distributor variation);
    `cheapest_at_qty` is the chosen winner per `requested_qty`. The
    BOM panel renders both: the winner up top, the alternatives in
    an expandable detail row."""

    mpn: str
    requested_qty: int
    quotes: list[PartQuote]
    errors: dict[str, str]
    """`{distributor: error_message}` for distributors that failed
    on THIS part. Empty when every adapter answered (with success
    or "not found")."""

    cheapest: PartQuote | None
    cheapest_unit_price_usd: float | None

    @property
    def line_total_usd(self) -> float | None:
        """`cheapest_unit_price_usd * requested_qty` when a winner
        exists. `None` if every distributor missed or every quote
        was below the requested quantity's MOQ."""
        if self.cheapest_unit_price_usd is None:
            return None
        return self.cheapest_unit_price_usd * self.requested_qty


@dataclass(slots=True)
class BomPricing:
    """Aggregated pricing for an entire BOM. Returned by `price_bom`."""

    parts: list[PartPricing]
    """One per requested MPN. Length matches the input list."""

    distributor_totals_usd: dict[str, float]
    """Sum of `line_total_usd` per winning distributor — lets the UI
    show "Buy from Digi-Key: $42.18, Mouser: $17.05" so the user can
    cart-split."""

    grand_total_usd: float
    """Sum of every `line_total_usd`. Excludes parts where every
    distributor missed."""

    missing_mpns: list[str]
    """MPNs that no distributor returned a live in-stock quote for.
    The BOM panel surfaces these in yellow."""

    errors: dict[str, list[str]] = field(default_factory=dict)
    """`{distributor: [error_messages, ...]}` — aggregated across
    every part in the run, deduplicated per distributor."""


class PriceAggregator:
    """The single fan-out + cache + cheapest-mix coordinator."""

    def __init__(
        self,
        *,
        adapters: Iterable[DistributorAdapter] | None = None,
        cache: PriceCache | None = None,
        per_adapter_timeout_s: float = DEFAULT_PER_ADAPTER_TIMEOUT_S,
        cache_ttl: timedelta = DEFAULT_TTL,
    ) -> None:
        self._adapters: list[DistributorAdapter] = list(adapters or [])
        self._cache = cache if cache is not None else PriceCache()
        self._timeout_s = per_adapter_timeout_s
        self._cache_ttl = cache_ttl

    def add_adapter(self, adapter: DistributorAdapter) -> None:
        self._adapters.append(adapter)

    @property
    def adapters(self) -> tuple[DistributorAdapter, ...]:
        return tuple(self._adapters)

    async def aclose(self) -> None:
        for adapter in self._adapters:
            with contextlib.suppress(Exception):
                await adapter.aclose()
        self._cache.close()

    # ----------------------------------------------------------------
    # Per-part price
    # ----------------------------------------------------------------

    async def price(
        self,
        mpn: str,
        *,
        qty: int = 1,
        force_refresh: bool = False,
    ) -> PartPricing:
        """Look up `mpn` across every registered distributor.

        Reads from cache when fresh + not `force_refresh`. Misses
        fan-out across adapters with per-adapter timeouts.
        """
        if qty < 1:
            raise ValueError("qty must be >= 1")
        all_quotes: list[PartQuote] = []
        errors: dict[str, str] = {}

        # Snapshot which adapters have cached quotes; the rest fan out.
        adapters_to_query: list[DistributorAdapter] = []
        for adapter in self._adapters:
            if not force_refresh:
                cached = self._cache.get(
                    distributor=adapter.name, mpn=mpn, max_age=self._cache_ttl
                )
                if cached is not None:
                    all_quotes.extend(cached)
                    continue
            adapters_to_query.append(adapter)

        if adapters_to_query:
            results = await asyncio.gather(
                *(self._query_one(adapter, mpn) for adapter in adapters_to_query),
                return_exceptions=False,
            )
            for adapter, (quotes, err) in zip(adapters_to_query, results, strict=True):
                if err is not None:
                    errors[adapter.name] = err
                    continue
                # Cache the response even when it's empty — "not
                # found at this distributor" is a useful signal we
                # don't want to re-fetch on every refresh.
                if quotes:
                    self._cache.put(quotes)
                all_quotes.extend(quotes)

        cheapest, unit_price = _pick_cheapest(all_quotes, qty=qty)
        return PartPricing(
            mpn=mpn,
            requested_qty=qty,
            quotes=all_quotes,
            errors=errors,
            cheapest=cheapest,
            cheapest_unit_price_usd=unit_price,
        )

    async def _query_one(
        self, adapter: DistributorAdapter, mpn: str
    ) -> tuple[list[PartQuote], str | None]:
        try:
            quotes = await asyncio.wait_for(adapter.lookup(mpn), timeout=self._timeout_s)
            return quotes, None
        except TimeoutError:
            return [], f"timeout after {self._timeout_s:.1f}s"
        except DistributorAuthError as e:
            log.warning("distributor_auth_error", distributor=adapter.name, mpn=mpn, error=str(e))
            return [], f"auth: {e}"
        except DistributorTransportError as e:
            log.warning(
                "distributor_transport_error",
                distributor=adapter.name,
                mpn=mpn,
                error=str(e),
            )
            return [], f"transport: {e}"
        except DistributorError as e:
            return [], f"{type(e).__name__}: {e}"
        except Exception as e:
            log.exception("distributor_unexpected_error", distributor=adapter.name, mpn=mpn)
            return [], f"{type(e).__name__}: {e}"

    # ----------------------------------------------------------------
    # Whole-BOM aggregator
    # ----------------------------------------------------------------

    async def price_bom(
        self,
        bom: Iterable[tuple[str, int]] | Iterable[str],
        *,
        force_refresh: bool = False,
    ) -> BomPricing:
        """Price every `(mpn, qty)` pair (qty defaults to 1 when the
        caller passes bare MPNs). Returns a [`BomPricing`] with the
        cheapest distributor per line + the cart-split breakdown."""
        items: list[tuple[str, int]] = []
        for entry in bom:
            if isinstance(entry, tuple):
                mpn, qty = entry
                items.append((str(mpn), int(qty) if qty else 1))
            else:
                items.append((str(entry), 1))

        results = await asyncio.gather(
            *(self.price(mpn, qty=qty, force_refresh=force_refresh) for mpn, qty in items),
        )

        distributor_totals: dict[str, float] = {}
        missing: list[str] = []
        errors_per_distributor: dict[str, list[str]] = {}
        grand_total = 0.0
        for part in results:
            for dist, msg in part.errors.items():
                errors_per_distributor.setdefault(dist, [])
                if msg not in errors_per_distributor[dist]:
                    errors_per_distributor[dist].append(msg)
            if part.line_total_usd is None:
                missing.append(part.mpn)
                continue
            grand_total += part.line_total_usd
            winner = part.cheapest
            if winner is not None:
                distributor_totals[winner.distributor] = (
                    distributor_totals.get(winner.distributor, 0.0) + part.line_total_usd
                )

        return BomPricing(
            parts=results,
            distributor_totals_usd=distributor_totals,
            grand_total_usd=grand_total,
            missing_mpns=missing,
            errors=errors_per_distributor,
        )


def _pick_cheapest(
    quotes: Iterable[PartQuote], *, qty: int
) -> tuple[PartQuote | None, float | None]:
    """Return the (quote, unit_price) pair with the lowest unit price
    at `qty`, ignoring quotes that don't satisfy the MOQ or that are
    out of stock. Returns `(None, None)` when no quote qualifies."""
    best: tuple[PartQuote | None, float | None] = (None, None)
    for q in quotes:
        if q.in_stock_qty < qty:
            continue
        price = q.unit_price_at_qty(qty)
        if price is None:
            continue
        if best[1] is None or price < best[1]:
            best = (q, price)
    return best


# ----------------------------------------------------------------
# Default-built aggregator — used by the kc_bom_price MCP tool.
# ----------------------------------------------------------------


def build_default_aggregator(
    *,
    cache: PriceCache | None = None,
    include_digikey: bool | None = None,
    include_mouser: bool | None = None,
    include_octopart: bool | None = None,
    include_jlcpcb: bool | None = None,
) -> PriceAggregator:
    """Construct the aggregator with every distributor whose
    credentials are present in env, plus the shared SQLite cache.

    Per-distributor `include_*` overrides credential detection
    (tests pass `False` to keep the aggregator empty; production
    code lets it autoload from env)."""
    cache = cache or PriceCache()
    aggregator = PriceAggregator(cache=cache)

    digikey_enabled = (
        include_digikey
        if include_digikey is not None
        else bool(
            os.environ.get("DIGIKEY_CLIENT_ID")
            and os.environ.get("DIGIKEY_CLIENT_SECRET")
        )
    )
    if digikey_enabled:
        from .digikey import DigiKeyAdapter

        aggregator.add_adapter(DigiKeyAdapter())

    mouser_enabled = (
        include_mouser
        if include_mouser is not None
        else bool(os.environ.get("MOUSER_API_KEY"))
    )
    if mouser_enabled:
        from .mouser import MouserAdapter

        aggregator.add_adapter(MouserAdapter())

    octopart_enabled = (
        include_octopart
        if include_octopart is not None
        else bool(
            os.environ.get("OCTOPART_CLIENT_ID")
            and os.environ.get("OCTOPART_CLIENT_SECRET")
        )
    )
    if octopart_enabled:
        from .octopart import OctopartAdapter

        aggregator.add_adapter(OctopartAdapter())

    # JLCPCB has anonymous quota — enable unconditionally by default.
    # The opt-out is `include_jlcpcb=False` for offline / test runs
    # that don't want the FX-rate fetch to fire.
    jlcpcb_enabled = include_jlcpcb if include_jlcpcb is not None else True
    if jlcpcb_enabled:
        from .jlcpcb import JlcpcbAdapter

        aggregator.add_adapter(JlcpcbAdapter())

    return aggregator


__all__ = [
    "BomPricing",
    "PartPricing",
    "PriceAggregator",
    "build_default_aggregator",
]
