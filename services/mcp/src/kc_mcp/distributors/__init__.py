"""kiclaude distributor adapters + BOM pricer fan-out (M3-P-03/P-05).

See:

- [`base`][.base] — `DistributorAdapter` ABC, `PartQuote` dataclass,
  the exception family.
- [`cache`][.cache] — SQLite-backed price cache with TTL.
- [`aggregator`][.aggregator] — fan-out coordinator + cheapest-mix
  selection. The `kc_bom_price` MCP tool consumes this.
- [`digikey`][.digikey] — Digi-Key Product Information V4 adapter
  (real HTTP, OAuth client_credentials, no fakes). Requires
  `DIGIKEY_CLIENT_ID` + `DIGIKEY_CLIENT_SECRET` env vars; without
  them every call raises `DistributorAuthError` and the aggregator
  soft-fails over to whatever other adapters are configured.

Other distributor adapters (Mouser / Octopart / JLCPCB) plug into
the same `DistributorAdapter` ABC + cache and will land as M3-P-01,
M3-P-02, M3-P-04 — no changes to the aggregator are required.
"""

from __future__ import annotations

from .aggregator import (
    BomPricing,
    PartPricing,
    PriceAggregator,
    build_default_aggregator,
)
from .base import (
    DistributorAdapter,
    DistributorAuthError,
    DistributorError,
    DistributorTransportError,
    PartQuote,
    PriceBreakpoint,
)
from .cache import DEFAULT_TTL, PriceCache
from .digikey import DigiKeyAdapter

__all__ = [
    "DEFAULT_TTL",
    "BomPricing",
    "DigiKeyAdapter",
    "DistributorAdapter",
    "DistributorAuthError",
    "DistributorError",
    "DistributorTransportError",
    "PartPricing",
    "PartQuote",
    "PriceAggregator",
    "PriceBreakpoint",
    "PriceCache",
    "build_default_aggregator",
]
