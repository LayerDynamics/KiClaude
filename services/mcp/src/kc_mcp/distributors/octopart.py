"""Octopart / Nexar GraphQL adapter — M3-P-01.

Implements the [`DistributorAdapter`][..base.DistributorAdapter]
contract against Nexar's GraphQL API (Octopart's commercial
successor — sign up at https://portal.nexar.com).

Auth: OAuth 2.0 `client_credentials` grant against
`https://identity.nexar.com/connect/token`; the issued bearer is
attached as `Authorization: Bearer <token>` on every GraphQL call.
Token expires-in is respected with the same 30s skew the Digi-Key
adapter uses, and concurrent first-mints serialise via asyncio.Lock.

GraphQL query: `supSearchMpn(q: <mpn>, limit: 1)` → `results[0].part`
returns an aggregated part with one or more `sellers[].offers[]`. Each
seller/offer pair becomes one `PartQuote` — meaning the aggregator
gets a real cross-distributor view (Digi-Key + Mouser + Avnet + …)
from a single Octopart call. Note: when both Octopart AND the
direct Digi-Key adapter are configured, the aggregator may surface
duplicate offers for the same physical SKU; the cheapest-mix
selector still picks correctly because it just compares unit prices.

Credentials are read from env at construction time:

- `OCTOPART_CLIENT_ID` — the Nexar app's Client ID
- `OCTOPART_CLIENT_SECRET` — the corresponding secret
- `OCTOPART_TOKEN_URL` — defaults to
  `https://identity.nexar.com/connect/token`
- `OCTOPART_GRAPHQL_URL` — defaults to `https://api.nexar.com/graphql`
- `OCTOPART_SCOPES` — defaults to `"supply.domain"` (the scope
  required for the Supply tier; promo / sample / design tiers add
  their own)

Without credentials, every `lookup()` raises
[`DistributorAuthError`][..base.DistributorAuthError]. No fakes.
"""

from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx

from .base import (
    DistributorAdapter,
    DistributorAuthError,
    DistributorTransportError,
    PartQuote,
    PriceBreakpoint,
)

DEFAULT_TOKEN_URL = "https://identity.nexar.com/connect/token"  # noqa: S105 — OAuth endpoint URL, not a secret
DEFAULT_GRAPHQL_URL = "https://api.nexar.com/graphql"
DEFAULT_SCOPES = "supply.domain"
DEFAULT_TIMEOUT_SECONDS = 12.0
TOKEN_REFRESH_SKEW_SECONDS = 30.0

#: GraphQL query — minimal-yet-complete shape for one MPN. We
#: explicitly request every field the adapter parses so a schema
#: shrink on the Octopart side surfaces as a real GraphQL error
#: instead of silent data loss.
_SEARCH_QUERY = """
query SupplySearch($q: String!) {
  supSearchMpn(q: $q, limit: 1) {
    results {
      part {
        id
        mpn
        manufacturer { name }
        shortDescription
        bestImage { url }
        octopartUrl
        sellers {
          company { name }
          offers {
            sku
            inventoryLevel
            moq
            packaging
            clickUrl
            updated
            prices {
              quantity
              price
              currency
              convertedPrice
              convertedCurrency
            }
          }
        }
      }
    }
  }
}
""".strip()


@dataclass(slots=True)
class _TokenCache:
    access_token: str
    expires_at_monotonic: float


def _normalise_lifecycle(_part: dict[str, Any]) -> str:
    """Nexar's Supply tier doesn't return JEDEC lifecycle on every
    part — when present it lives under `part.specs[]`. The Supply
    query above is deliberately minimal to keep token cost down;
    the M4 plan promotes lifecycle to a first-class field. Until
    then every quote ships as `unknown` (the BOM panel still shows
    it, just without colour-coding the bucket)."""
    return "unknown"


def _parse_quote(
    *, mpn: str, part: dict[str, Any], seller_name: str, offer: dict[str, Any]
) -> PartQuote:
    """Map one (part, seller, offer) triple into a [`PartQuote`].

    Octopart's `prices[]` has a 1:1 quantity / unit-price relationship
    after `convertedPrice` normalisation — we prefer the converted
    USD price so cross-distributor selection actually compares
    apples-to-apples."""
    manufacturer_name = (part.get("manufacturer") or {}).get("name") or ""
    description = part.get("shortDescription") or ""
    distributor_sku = str(offer.get("sku") or "")
    product_url = str(offer.get("clickUrl") or part.get("octopartUrl") or "")
    in_stock_qty = int(offer.get("inventoryLevel") or 0)
    moq = max(1, int(offer.get("moq") or 1))

    price_breaks: list[PriceBreakpoint] = []
    for entry in offer.get("prices") or []:
        if not isinstance(entry, dict):
            continue
        qty_raw = entry.get("quantity") or 1
        try:
            qty = max(1, int(qty_raw))
        except (TypeError, ValueError):
            qty = 1
        # convertedPrice (USD by default per Nexar config) is the
        # canonical view; fall back to the native `price` when the
        # conversion field is absent (older payloads / paid tiers).
        unit_raw = entry.get("convertedPrice")
        if unit_raw is None:
            unit_raw = entry.get("price")
        try:
            unit_price = float(unit_raw) if unit_raw is not None else 0.0
        except (TypeError, ValueError):
            unit_price = 0.0
        price_breaks.append(PriceBreakpoint(min_qty=qty, unit_price_usd=unit_price))
    price_breaks.sort(key=lambda b: b.min_qty)

    seller_slug = seller_name.lower().replace(" ", "_") if seller_name else ""
    return PartQuote(
        # Surface the *seller* as the distributor — that's the
        # downstream cart the user actually opens. Aggregator + cache
        # still namespace by adapter name internally via the
        # `octopart-via-<seller>` key the cache_id uses below.
        distributor=f"octopart-via-{seller_slug}" if seller_slug else "octopart",
        mpn=mpn,
        distributor_sku=distributor_sku,
        manufacturer=str(manufacturer_name),
        description=str(description),
        in_stock_qty=in_stock_qty,
        moq=moq,
        lifecycle=_normalise_lifecycle(part),
        price_breaks=tuple(price_breaks),
        product_url=product_url,
        quoted_at=datetime.now(UTC),
        extras={
            "octopart_part_id": part.get("id"),
            "packaging": offer.get("packaging"),
            "updated": offer.get("updated"),
            "octopart_url": part.get("octopartUrl"),
            "image_url": (part.get("bestImage") or {}).get("url"),
            "seller_company": seller_name,
        },
    )


class OctopartAdapter(DistributorAdapter):
    """Real Nexar/Octopart GraphQL client. Raises
    [`DistributorAuthError`] on every `lookup()` without credentials
    so the aggregator can soft-fail cleanly."""

    name = "octopart"

    def __init__(
        self,
        *,
        client_id: str | None = None,
        client_secret: str | None = None,
        token_url: str | None = None,
        graphql_url: str | None = None,
        scopes: str | None = None,
        client: httpx.AsyncClient | None = None,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._client_id = (
            client_id
            if client_id is not None
            else os.environ.get("OCTOPART_CLIENT_ID", "")
        )
        self._client_secret = (
            client_secret
            if client_secret is not None
            else os.environ.get("OCTOPART_CLIENT_SECRET", "")
        )
        self._token_url = (
            token_url or os.environ.get("OCTOPART_TOKEN_URL") or DEFAULT_TOKEN_URL
        )
        self._graphql_url = (
            graphql_url or os.environ.get("OCTOPART_GRAPHQL_URL") or DEFAULT_GRAPHQL_URL
        )
        self._scopes = scopes or os.environ.get("OCTOPART_SCOPES") or DEFAULT_SCOPES
        self._timeout = timeout_seconds
        self._owns_client = client is None
        self._http = client or httpx.AsyncClient(timeout=httpx.Timeout(self._timeout))
        self._token_cache: _TokenCache | None = None
        self._token_lock = asyncio.Lock()

    async def aclose(self) -> None:
        if self._owns_client:
            await self._http.aclose()

    # ----------------------------------------------------------------
    # OAuth client_credentials mint
    # ----------------------------------------------------------------

    async def _get_token(self) -> str:
        if not self._client_id or not self._client_secret:
            raise DistributorAuthError(
                "Octopart credentials missing: set OCTOPART_CLIENT_ID + OCTOPART_CLIENT_SECRET"
            )
        cached = self._token_cache
        if cached is not None and time.monotonic() < cached.expires_at_monotonic:
            return cached.access_token
        async with self._token_lock:
            cached = self._token_cache
            if cached is not None and time.monotonic() < cached.expires_at_monotonic:
                return cached.access_token
            try:
                resp = await self._http.post(
                    self._token_url,
                    data={
                        "grant_type": "client_credentials",
                        "client_id": self._client_id,
                        "client_secret": self._client_secret,
                        "scope": self._scopes,
                    },
                    headers={"content-type": "application/x-www-form-urlencoded"},
                )
            except httpx.HTTPError as e:
                raise DistributorTransportError(
                    f"Octopart token mint network error: {e}"
                ) from e
            if resp.status_code in (401, 403):
                raise DistributorAuthError(
                    f"Octopart rejected credentials: HTTP {resp.status_code} — "
                    f"{resp.text[:200]}"
                )
            if resp.status_code >= 400:
                raise DistributorTransportError(
                    f"Octopart token mint HTTP {resp.status_code}: {resp.text[:200]}"
                )
            body = resp.json()
            token = body.get("access_token")
            expires_in = float(body.get("expires_in") or 0)
            if not token or expires_in <= 0:
                raise DistributorTransportError(
                    f"Octopart token response missing access_token/expires_in: {body!r}"
                )
            self._token_cache = _TokenCache(
                access_token=str(token),
                expires_at_monotonic=(
                    time.monotonic() + max(expires_in - TOKEN_REFRESH_SKEW_SECONDS, 1.0)
                ),
            )
            return self._token_cache.access_token

    # ----------------------------------------------------------------
    # GraphQL lookup
    # ----------------------------------------------------------------

    async def lookup(self, mpn: str) -> list[PartQuote]:
        if not mpn or not mpn.strip():
            return []
        token = await self._get_token()
        try:
            resp = await self._http.post(
                self._graphql_url,
                json={"query": _SEARCH_QUERY, "variables": {"q": mpn}},
                headers={
                    "authorization": f"Bearer {token}",
                    "content-type": "application/json",
                    "accept": "application/json",
                },
            )
        except httpx.HTTPError as e:
            raise DistributorTransportError(f"Octopart GraphQL network error: {e}") from e
        if resp.status_code in (401, 403):
            self._token_cache = None
            raise DistributorAuthError(
                f"Octopart rejected token: HTTP {resp.status_code}"
            )
        if resp.status_code >= 400:
            raise DistributorTransportError(
                f"Octopart GraphQL HTTP {resp.status_code}: {resp.text[:200]}"
            )
        body = resp.json()
        errors = body.get("errors") or []
        if errors:
            messages = [str(e.get("message") or "") for e in errors if isinstance(e, dict)]
            # GraphQL auth errors show up with a 200 + errors[].
            if any("Unauthorized" in m or "auth" in m.lower() for m in messages):
                self._token_cache = None
                raise DistributorAuthError(f"Octopart GraphQL auth error: {'; '.join(messages)}")
            raise DistributorTransportError(f"Octopart GraphQL errors: {'; '.join(messages)}")
        data = body.get("data") or {}
        search = data.get("supSearchMpn") or {}
        results = search.get("results") or []
        quotes: list[PartQuote] = []
        for result in results:
            if not isinstance(result, dict):
                continue
            part = result.get("part") or {}
            for seller in part.get("sellers") or []:
                if not isinstance(seller, dict):
                    continue
                seller_name = str((seller.get("company") or {}).get("name") or "")
                for offer in seller.get("offers") or []:
                    if not isinstance(offer, dict):
                        continue
                    quotes.append(
                        _parse_quote(
                            mpn=mpn,
                            part=part,
                            seller_name=seller_name,
                            offer=offer,
                        )
                    )
        return quotes


__all__ = ["DEFAULT_GRAPHQL_URL", "DEFAULT_TOKEN_URL", "OctopartAdapter"]
