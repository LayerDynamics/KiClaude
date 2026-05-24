"""Digi-Key Product Information V4 adapter — M3-P-03.

Implements the [`DistributorAdapter`][..base.DistributorAdapter]
contract against Digi-Key's V4 Product Information API:

- OAuth 2.0 `client_credentials` token mint at
  `POST /v1/oauth2/token` (the OAuth host is the same as the
  sandbox / production base URL).
- Keyword search → product detail walk at
  `POST /products/v4/search/keyword` and
  `GET /products/v4/search/{productNumber}/productdetails`.

Credentials are read from env at construction time:

- `DIGIKEY_CLIENT_ID` — the developer-portal app's Client ID
- `DIGIKEY_CLIENT_SECRET` — the corresponding secret
- `DIGIKEY_BASE_URL` — defaults to `https://sandbox-api.digikey.com`;
  set to `https://api.digikey.com` once your production app is
  approved
- `DIGIKEY_LOCALE_SITE` — defaults to `"US"`; controls pricing
  currency and stock locale (`"DE"`, `"JP"`, etc. valid)
- `DIGIKEY_LOCALE_CURRENCY` — defaults to `"USD"`
- `DIGIKEY_CUSTOMER_ID` — defaults to `"0"`; non-zero customer ids
  unlock contract pricing on production

No fallback / mock data. Without credentials, every `lookup()` call
raises [`DistributorAuthError`][..base.DistributorAuthError]; the
aggregator catches and skips. With credentials, real HTTP traffic
flows to Digi-Key.

## Token caching

Digi-Key tokens last ~10 minutes and are returned with an
`expires_in` field. We cache the token + expiry in-process and only
mint a new one when `expires_in - 30s` has elapsed (the 30s skew
buys time for in-flight requests).
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

DEFAULT_BASE_URL = "https://sandbox-api.digikey.com"
TOKEN_PATH = "/v1/oauth2/token"  # noqa: S105 — OAuth endpoint URL path, not a secret
KEYWORD_SEARCH_PATH = "/products/v4/search/keyword"
PRODUCT_DETAILS_PATH_FMT = "/products/v4/search/{}/productdetails"

# Token-refresh skew — refresh `skew` seconds early so a request that
# starts just before expiry doesn't 401 mid-flight.
TOKEN_REFRESH_SKEW_SECONDS = 30.0

# Network timeout per HTTP call. Digi-Key V4 typically responds in
# <500ms; 10s is generous + matches their published SLA ceiling.
DEFAULT_TIMEOUT_SECONDS = 10.0


@dataclass(slots=True)
class _TokenCache:
    access_token: str
    expires_at_monotonic: float  # time.monotonic() value past which we refresh


def _normalise_lifecycle(raw: Any) -> str:
    """Map Digi-Key's `ProductStatus.Status` strings onto the canonical
    [`PartQuote.lifecycle`][..base.PartQuote.lifecycle] taxonomy.

    Digi-Key uses verbose descriptions like `"Active"`,
    `"Not For New Designs"`, `"Discontinued at Digi-Key"`, etc. We
    fold them down to the four canonical buckets the BOM panel
    renders."""
    s = (str(raw) if raw is not None else "").strip().lower()
    if not s:
        return "unknown"
    if "active" in s:
        return "active"
    if "not for new" in s or "nrnd" in s:
        return "nrnd"
    if "obsolete" in s or "discontinued" in s or "end of life" in s:
        return "obsolete"
    if "preview" in s or "pre-release" in s or "in progress" in s:
        return "preview"
    return "unknown"


def _parse_quote(
    *,
    mpn: str,
    product: dict[str, Any],
    variation: dict[str, Any],
) -> PartQuote:
    """Map one ProductVariation block to a [`PartQuote`].

    Digi-Key's V4 product/details response carries a `Product` with
    one or more `ProductVariations` (cut tape, full reel, etc.). The
    aggregator wants each variation as its own quote so the user can
    pick reel-only on a 5k-piece order."""
    manufacturer = (
        product.get("Manufacturer", {}).get("Name")
        or product.get("Manufacturer", {}).get("Value")
        or ""
    )
    description = (
        product.get("Description", {}).get("ProductDescription")
        or product.get("Description", {}).get("DetailedDescription")
        or product.get("ProductDescription")
        or ""
    )
    lifecycle = _normalise_lifecycle(
        (product.get("ProductStatus") or {}).get("Status")
    )

    in_stock_qty = int(variation.get("QuantityAvailableforPackageType") or 0)
    moq = int(variation.get("MinimumOrderQuantity") or 1)
    distributor_sku = str(variation.get("DigiKeyProductNumber") or "")
    product_url = str(variation.get("ProductUrl") or product.get("ProductUrl") or "")

    price_breaks_raw = variation.get("StandardPricing") or []
    price_breaks = tuple(
        PriceBreakpoint(
            min_qty=int(brk.get("BreakQuantity") or 1),
            unit_price_usd=float(brk.get("UnitPrice") or 0.0),
        )
        for brk in price_breaks_raw
    )

    return PartQuote(
        distributor="digikey",
        mpn=mpn,
        distributor_sku=distributor_sku,
        manufacturer=str(manufacturer),
        description=str(description),
        in_stock_qty=in_stock_qty,
        moq=moq,
        lifecycle=lifecycle,
        price_breaks=price_breaks,
        product_url=product_url,
        quoted_at=datetime.now(UTC),
        extras={
            "packaging": (variation.get("PackageType") or {}).get("Name"),
            "rohs_status": product.get("RoHSStatus"),
            "category": (product.get("Category") or {}).get("Name"),
        },
    )


class DigiKeyAdapter(DistributorAdapter):
    """Real Digi-Key V4 client. Reads credentials from env at
    construction time; raises [`DistributorAuthError`] on every
    `lookup()` call when credentials are absent so the aggregator
    can surface the message + skip cleanly."""

    name = "digikey"

    def __init__(
        self,
        *,
        client_id: str | None = None,
        client_secret: str | None = None,
        base_url: str | None = None,
        client: httpx.AsyncClient | None = None,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        locale_site: str | None = None,
        locale_currency: str | None = None,
        customer_id: str | None = None,
    ) -> None:
        self._client_id = client_id or os.environ.get("DIGIKEY_CLIENT_ID", "")
        self._client_secret = client_secret or os.environ.get("DIGIKEY_CLIENT_SECRET", "")
        self._base_url = (
            base_url or os.environ.get("DIGIKEY_BASE_URL") or DEFAULT_BASE_URL
        ).rstrip("/")
        self._timeout = timeout_seconds
        self._locale_site = locale_site or os.environ.get("DIGIKEY_LOCALE_SITE", "US")
        self._locale_currency = (
            locale_currency or os.environ.get("DIGIKEY_LOCALE_CURRENCY", "USD")
        )
        self._customer_id = customer_id or os.environ.get("DIGIKEY_CUSTOMER_ID", "0")
        # The caller can inject a pre-built AsyncClient (tests use
        # httpx.MockTransport this way) or let us build one against
        # the resolved base URL.
        self._owns_client = client is None
        self._http = client or httpx.AsyncClient(
            base_url=self._base_url,
            timeout=httpx.Timeout(self._timeout),
        )
        self._token_cache: _TokenCache | None = None
        self._token_lock = asyncio.Lock()

    async def aclose(self) -> None:
        if self._owns_client:
            await self._http.aclose()

    # ----------------------------------------------------------------
    # OAuth 2.0 client_credentials token mint
    # ----------------------------------------------------------------

    async def _get_token(self) -> str:
        if not self._client_id or not self._client_secret:
            raise DistributorAuthError(
                "Digi-Key credentials missing: set DIGIKEY_CLIENT_ID + DIGIKEY_CLIENT_SECRET"
            )
        cached = self._token_cache
        if cached is not None and time.monotonic() < cached.expires_at_monotonic:
            return cached.access_token
        # Serialise concurrent mint attempts so a fan-out doesn't
        # burn N tokens on the first request.
        async with self._token_lock:
            cached = self._token_cache
            if cached is not None and time.monotonic() < cached.expires_at_monotonic:
                return cached.access_token
            try:
                resp = await self._http.post(
                    TOKEN_PATH,
                    data={
                        "grant_type": "client_credentials",
                        "client_id": self._client_id,
                        "client_secret": self._client_secret,
                    },
                    headers={"content-type": "application/x-www-form-urlencoded"},
                )
            except httpx.HTTPError as e:
                raise DistributorTransportError(
                    f"Digi-Key token mint network error: {e}"
                ) from e
            if resp.status_code == 401 or resp.status_code == 403:
                raise DistributorAuthError(
                    f"Digi-Key rejected credentials: HTTP {resp.status_code} — "
                    f"{resp.text[:200]}"
                )
            if resp.status_code >= 400:
                raise DistributorTransportError(
                    f"Digi-Key token mint HTTP {resp.status_code}: {resp.text[:200]}"
                )
            body = resp.json()
            token = body.get("access_token")
            expires_in = float(body.get("expires_in") or 0)
            if not token or expires_in <= 0:
                raise DistributorTransportError(
                    f"Digi-Key token response missing access_token / expires_in: {body!r}"
                )
            self._token_cache = _TokenCache(
                access_token=str(token),
                expires_at_monotonic=(
                    time.monotonic() + max(expires_in - TOKEN_REFRESH_SKEW_SECONDS, 1.0)
                ),
            )
            return self._token_cache.access_token

    # ----------------------------------------------------------------
    # Public API
    # ----------------------------------------------------------------

    async def lookup(self, mpn: str) -> list[PartQuote]:
        if not mpn or not mpn.strip():
            return []
        token = await self._get_token()
        headers = {
            "authorization": f"Bearer {token}",
            "x-digikey-client-id": self._client_id,
            "x-digikey-locale-site": self._locale_site,
            "x-digikey-locale-currency": self._locale_currency,
            "x-digikey-customer-id": self._customer_id,
            "content-type": "application/json",
            "accept": "application/json",
        }

        # Try the direct product-details endpoint first — it's the
        # cheap path when the MPN matches a Digi-Key product number
        # 1:1 (which it usually does for major manufacturers).
        direct = await self._product_details(mpn=mpn, headers=headers)
        if direct:
            return direct

        # Fall back to keyword search → first hit's product-details.
        search_body = {
            "Keywords": mpn,
            "RecordCount": 5,
            "RecordStartPosition": 0,
            "Sort": {
                "SortOption": "SortByDigiKeyPartNumber",
                "Direction": "Ascending",
                "SortParameter": "DigiKeyPartNumber",
            },
            "RequestedQuantity": 1,
        }
        try:
            resp = await self._http.post(
                KEYWORD_SEARCH_PATH,
                json=search_body,
                headers=headers,
            )
        except httpx.HTTPError as e:
            raise DistributorTransportError(f"Digi-Key keyword search network error: {e}") from e
        if resp.status_code == 404:
            return []
        if resp.status_code == 401 or resp.status_code == 403:
            # Force a token refresh on the next call — current one is
            # stale/revoked.
            self._token_cache = None
            raise DistributorAuthError(
                f"Digi-Key keyword search rejected token: HTTP {resp.status_code}"
            )
        if resp.status_code >= 400:
            raise DistributorTransportError(
                f"Digi-Key keyword search HTTP {resp.status_code}: {resp.text[:200]}"
            )
        body = resp.json()
        products = body.get("Products") or []
        if not products:
            return []
        # Use the first matching product's MPN to call product-details
        # — keyword search may return partial pricing without the full
        # ProductVariations block.
        first = products[0]
        target_mpn = (
            first.get("ManufacturerProductNumber")
            or first.get("ManufacturerPartNumber")
            or mpn
        )
        return await self._product_details(mpn=str(target_mpn), headers=headers, fallback_mpn=mpn)

    async def _product_details(
        self,
        *,
        mpn: str,
        headers: dict[str, str],
        fallback_mpn: str | None = None,
    ) -> list[PartQuote]:
        """Fetch one product's full detail + every variation as a
        list of quotes. Returns [] on 404 (part not found at this
        distributor)."""
        url = PRODUCT_DETAILS_PATH_FMT.format(httpx.URL(mpn).path or mpn)
        try:
            resp = await self._http.get(url, headers=headers)
        except httpx.HTTPError as e:
            raise DistributorTransportError(f"Digi-Key product details network error: {e}") from e
        if resp.status_code == 404:
            return []
        if resp.status_code == 401 or resp.status_code == 403:
            self._token_cache = None
            raise DistributorAuthError(
                f"Digi-Key product details rejected token: HTTP {resp.status_code}"
            )
        if resp.status_code >= 400:
            # Anything in the 4xx/5xx bucket above already-handled
            # codes — treat as transport so the aggregator soft-fails.
            raise DistributorTransportError(
                f"Digi-Key product details HTTP {resp.status_code}: {resp.text[:200]}"
            )
        body = resp.json()
        product = body.get("Product") or {}
        if not product:
            return []
        variations = product.get("ProductVariations") or []
        echo_mpn = fallback_mpn or mpn
        if not variations:
            # Older V4 responses inline pricing directly on Product —
            # promote it to a one-element variation list so the parse
            # path is uniform.
            variations = [
                {
                    "DigiKeyProductNumber": product.get("ProductNumber") or "",
                    "QuantityAvailableforPackageType": product.get("QuantityAvailable") or 0,
                    "MinimumOrderQuantity": product.get("MinimumOrderQuantity") or 1,
                    "StandardPricing": product.get("StandardPricing") or [],
                    "ProductUrl": product.get("ProductUrl") or "",
                    "PackageType": product.get("PackageType") or {},
                }
            ]
        return [
            _parse_quote(mpn=echo_mpn, product=product, variation=variation)
            for variation in variations
        ]


__all__ = ["DEFAULT_BASE_URL", "DigiKeyAdapter"]
