"""M3-P-03 — Digi-Key V4 adapter.

Tests use httpx.MockTransport with response payloads modelled on the
documented Digi-Key V4 OpenAPI schema. No real network — but the
adapter code under test is the same code that runs in production
against real credentials. Without the mock transport (i.e. the
production path) the adapter calls real Digi-Key endpoints with the
DIGIKEY_CLIENT_ID/SECRET env vars.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from kc_mcp.distributors import (
    DigiKeyAdapter,
    DistributorAuthError,
    DistributorTransportError,
)
from kc_mcp.distributors.digikey import (
    KEYWORD_SEARCH_PATH,
    PRODUCT_DETAILS_PATH_FMT,
    TOKEN_PATH,
)

TOKEN_RESPONSE = {
    "access_token": "test-token",
    "expires_in": 600,
    "token_type": "Bearer",
}

# Documented Digi-Key V4 ProductDetails response shape — mirrors the
# fields the adapter parses (and ignores the rest, which V4 includes).
PRODUCT_DETAILS_RESPONSE: dict[str, Any] = {
    "Product": {
        "Manufacturer": {"Id": 497, "Name": "STMicroelectronics"},
        "ManufacturerProductNumber": "STM32F103C8T6",
        "Description": {
            "ProductDescription": "ARM Cortex-M3 32-bit MCU, 64KB Flash",
            "DetailedDescription": "ARM Cortex-M3 32-bit MCU, 64KB Flash, 20KB SRAM, LQFP-48",
        },
        "ProductStatus": {"Id": 0, "Status": "Active"},
        "Category": {"Name": "Microcontrollers"},
        "RoHSStatus": "ROHS3 Compliant",
        "ProductVariations": [
            {
                "DigiKeyProductNumber": "497-6063-1-ND",
                "PackageType": {"Id": 0, "Name": "Cut Tape (CT)"},
                "QuantityAvailableforPackageType": 420,
                "MinimumOrderQuantity": 1,
                "StandardPricing": [
                    {"BreakQuantity": 1, "UnitPrice": 2.50, "TotalPrice": 2.50},
                    {"BreakQuantity": 10, "UnitPrice": 2.10, "TotalPrice": 21.00},
                    {"BreakQuantity": 100, "UnitPrice": 1.65, "TotalPrice": 165.00},
                ],
                "ProductUrl": "https://www.digikey.com/en/products/detail/stmicroelectronics/STM32F103C8T6/1646338",
            },
            {
                "DigiKeyProductNumber": "497-6063-2-ND",
                "PackageType": {"Id": 1, "Name": "Tape & Reel (TR)"},
                "QuantityAvailableforPackageType": 1500,
                "MinimumOrderQuantity": 1000,
                "StandardPricing": [
                    {"BreakQuantity": 1000, "UnitPrice": 1.45, "TotalPrice": 1450.00},
                ],
                "ProductUrl": "https://www.digikey.com/en/products/detail/stmicroelectronics/STM32F103C8T6/1646339",
            },
        ],
    }
}

KEYWORD_SEARCH_RESPONSE: dict[str, Any] = {
    "Products": [
        {
            "ManufacturerProductNumber": "STM32F103C8T6",
            "Description": {"ProductDescription": "ARM Cortex-M3"},
        }
    ],
    "ProductsCount": 1,
    "ExactMatches": [],
}


def _adapter_with_handler(
    handler: httpx.MockTransport,
    **adapter_kwargs: Any,
) -> DigiKeyAdapter:
    client = httpx.AsyncClient(transport=handler, base_url="https://sandbox-api.digikey.com")
    return DigiKeyAdapter(
        client_id=adapter_kwargs.pop("client_id", "test-client-id"),
        client_secret=adapter_kwargs.pop("client_secret", "test-client-secret"),
        client=client,
        **adapter_kwargs,
    )


# ---------------------------------------------------------------------
# Token mint
# ---------------------------------------------------------------------


async def test_lookup_mints_and_caches_oauth_token() -> None:
    token_calls = 0
    details_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal token_calls, details_calls
        if request.url.path == TOKEN_PATH:
            token_calls += 1
            return httpx.Response(200, json=TOKEN_RESPONSE)
        if "productdetails" in request.url.path:
            details_calls += 1
            # Verify the bearer is what we minted.
            assert request.headers.get("authorization") == "Bearer test-token"
            return httpx.Response(200, json=PRODUCT_DETAILS_RESPONSE)
        return httpx.Response(404)

    adapter = _adapter_with_handler(httpx.MockTransport(handler))
    quotes = await adapter.lookup("STM32F103C8T6")
    assert len(quotes) == 2  # two variations
    assert token_calls == 1
    # Second lookup reuses the cached token — only details is called.
    await adapter.lookup("STM32F103C8T6")
    assert token_calls == 1
    assert details_calls == 2
    await adapter.aclose()


async def test_missing_credentials_raises_auth_error() -> None:
    """No client_id / client_secret in env or constructor → every
    lookup raises DistributorAuthError (no fake mode, no silent
    fallback)."""
    adapter = DigiKeyAdapter(client_id="", client_secret="")
    try:
        with pytest.raises(DistributorAuthError, match="credentials missing"):
            await adapter.lookup("STM32F103C8T6")
    finally:
        await adapter.aclose()


async def test_oauth_401_surfaces_auth_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == TOKEN_PATH:
            return httpx.Response(401, json={"error": "invalid_client"})
        return httpx.Response(200, json={})

    adapter = _adapter_with_handler(httpx.MockTransport(handler))
    try:
        with pytest.raises(DistributorAuthError, match="rejected credentials"):
            await adapter.lookup("STM32F103C8T6")
    finally:
        await adapter.aclose()


async def test_oauth_500_surfaces_transport_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == TOKEN_PATH:
            return httpx.Response(500, text="Internal Server Error")
        return httpx.Response(200, json={})

    adapter = _adapter_with_handler(httpx.MockTransport(handler))
    try:
        with pytest.raises(DistributorTransportError):
            await adapter.lookup("STM32F103C8T6")
    finally:
        await adapter.aclose()


# ---------------------------------------------------------------------
# Product details parsing
# ---------------------------------------------------------------------


async def test_lookup_returns_one_quote_per_variation() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == TOKEN_PATH:
            return httpx.Response(200, json=TOKEN_RESPONSE)
        return httpx.Response(200, json=PRODUCT_DETAILS_RESPONSE)

    adapter = _adapter_with_handler(httpx.MockTransport(handler))
    quotes = await adapter.lookup("STM32F103C8T6")
    assert len(quotes) == 2
    by_sku = {q.distributor_sku: q for q in quotes}
    cut_tape = by_sku["497-6063-1-ND"]
    reel = by_sku["497-6063-2-ND"]
    assert cut_tape.manufacturer == "STMicroelectronics"
    assert "ARM Cortex-M3" in cut_tape.description
    assert cut_tape.moq == 1
    assert cut_tape.in_stock_qty == 420
    assert cut_tape.lifecycle == "active"
    assert len(cut_tape.price_breaks) == 3
    assert cut_tape.price_breaks[0].min_qty == 1
    assert cut_tape.price_breaks[0].unit_price_usd == 2.50
    assert cut_tape.price_breaks[-1].min_qty == 100
    assert cut_tape.product_url.endswith("/1646338")
    assert reel.moq == 1000
    assert reel.in_stock_qty == 1500
    assert reel.extras.get("packaging") == "Tape & Reel (TR)"
    await adapter.aclose()


async def test_lookup_returns_empty_on_product_404() -> None:
    """Not-found at Digi-Key is normal — every other distributor is
    consulted via the aggregator anyway. Must NOT raise."""
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == TOKEN_PATH:
            return httpx.Response(200, json=TOKEN_RESPONSE)
        if "productdetails" in request.url.path:
            return httpx.Response(404)
        if request.url.path == KEYWORD_SEARCH_PATH:
            return httpx.Response(200, json={"Products": [], "ProductsCount": 0})
        return httpx.Response(404)

    adapter = _adapter_with_handler(httpx.MockTransport(handler))
    quotes = await adapter.lookup("PHANTOM-PART-9999")
    assert quotes == []
    await adapter.aclose()


async def test_lookup_falls_through_to_keyword_search_when_details_404s() -> None:
    """When the direct productdetails miss the keyword search rescues."""
    search_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal search_calls
        if request.url.path == TOKEN_PATH:
            return httpx.Response(200, json=TOKEN_RESPONSE)
        if request.url.path == KEYWORD_SEARCH_PATH:
            search_calls += 1
            assert json.loads(request.content.decode("utf-8"))["Keywords"] == "fuzzy match"
            return httpx.Response(200, json=KEYWORD_SEARCH_RESPONSE)
        if "productdetails" in request.url.path:
            # First details call (with the raw user MPN) misses; the
            # second call (with the keyword-search-resolved MPN) hits.
            if request.url.path.endswith(PRODUCT_DETAILS_PATH_FMT.format("fuzzy match")):
                return httpx.Response(404)
            return httpx.Response(200, json=PRODUCT_DETAILS_RESPONSE)
        return httpx.Response(404)

    adapter = _adapter_with_handler(httpx.MockTransport(handler))
    quotes = await adapter.lookup("fuzzy match")
    assert search_calls == 1
    assert len(quotes) == 2
    # MPN echoed back is the user's input, NOT the keyword-resolved
    # one — so the aggregator can match cache keys.
    assert all(q.mpn == "fuzzy match" for q in quotes)
    await adapter.aclose()


async def test_lookup_lifecycle_taxonomy_normalisation() -> None:
    nrnd_product = json.loads(json.dumps(PRODUCT_DETAILS_RESPONSE))
    nrnd_product["Product"]["ProductStatus"]["Status"] = "Not For New Designs"

    def handler(_request: httpx.Request) -> httpx.Response:
        if _request.url.path == TOKEN_PATH:
            return httpx.Response(200, json=TOKEN_RESPONSE)
        return httpx.Response(200, json=nrnd_product)

    adapter = _adapter_with_handler(httpx.MockTransport(handler))
    quotes = await adapter.lookup("STM32F103C8T6")
    assert quotes
    assert all(q.lifecycle == "nrnd" for q in quotes)
    await adapter.aclose()


async def test_lookup_handles_obsolete_lifecycle_string() -> None:
    obsolete_product = json.loads(json.dumps(PRODUCT_DETAILS_RESPONSE))
    obsolete_product["Product"]["ProductStatus"]["Status"] = "Discontinued at Digi-Key"

    def handler(_request: httpx.Request) -> httpx.Response:
        if _request.url.path == TOKEN_PATH:
            return httpx.Response(200, json=TOKEN_RESPONSE)
        return httpx.Response(200, json=obsolete_product)

    adapter = _adapter_with_handler(httpx.MockTransport(handler))
    quotes = await adapter.lookup("STM32F103C8T6")
    assert quotes
    assert all(q.lifecycle == "obsolete" for q in quotes)
    await adapter.aclose()


async def test_concurrent_first_call_only_mints_one_token() -> None:
    import asyncio

    token_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal token_calls
        if request.url.path == TOKEN_PATH:
            token_calls += 1
            return httpx.Response(200, json=TOKEN_RESPONSE)
        return httpx.Response(200, json=PRODUCT_DETAILS_RESPONSE)

    adapter = _adapter_with_handler(httpx.MockTransport(handler))
    # Fire two concurrent lookups — they must share one token mint.
    await asyncio.gather(adapter.lookup("MPN-A"), adapter.lookup("MPN-B"))
    assert token_calls == 1
    await adapter.aclose()


async def test_401_on_product_details_forces_token_refresh_next_call() -> None:
    """If the token cache holds a stale token and Digi-Key 401s, the
    adapter must clear the cache so the next call re-mints."""
    token_calls = 0
    details_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal token_calls, details_calls
        if request.url.path == TOKEN_PATH:
            token_calls += 1
            return httpx.Response(200, json=TOKEN_RESPONSE)
        if "productdetails" in request.url.path:
            details_calls += 1
            return httpx.Response(401, text="Unauthorized")
        return httpx.Response(404)

    adapter = _adapter_with_handler(httpx.MockTransport(handler))
    try:
        with pytest.raises(DistributorAuthError):
            await adapter.lookup("STM32F103C8T6")
        # Cache was cleared — next attempt would re-mint.
        assert adapter._token_cache is None  # type: ignore[attr-defined]
    finally:
        await adapter.aclose()


async def test_empty_mpn_returns_empty_without_mint() -> None:
    """Skip the network entirely on an empty MPN — guards the
    aggregator against burning quota on user typos."""
    token_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal token_calls
        if request.url.path == TOKEN_PATH:
            token_calls += 1
            return httpx.Response(200, json=TOKEN_RESPONSE)
        return httpx.Response(200, json=PRODUCT_DETAILS_RESPONSE)

    adapter = _adapter_with_handler(httpx.MockTransport(handler))
    assert await adapter.lookup("") == []
    assert await adapter.lookup("   ") == []
    assert token_calls == 0
    await adapter.aclose()
