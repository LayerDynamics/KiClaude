"""M3-P-01 — Octopart / Nexar GraphQL adapter.

Tests use httpx.MockTransport against documented Nexar Supply GraphQL
response shapes. No real network — but the adapter code under test is
the same code that runs in production against `OCTOPART_CLIENT_ID` +
`OCTOPART_CLIENT_SECRET`.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from kc_mcp.distributors import (
    DistributorAuthError,
    DistributorTransportError,
    OctopartAdapter,
)
from kc_mcp.distributors.octopart import (
    DEFAULT_TOKEN_URL,
)

TOKEN_RESPONSE = {"access_token": "octopart-test-token", "expires_in": 900, "token_type": "Bearer"}

# Nexar Supply v4 GraphQL response — one part, two sellers, three
# offers total.
GRAPHQL_RESPONSE: dict[str, Any] = {
    "data": {
        "supSearchMpn": {
            "results": [
                {
                    "part": {
                        "id": "1234",
                        "mpn": "STM32F103C8T6",
                        "manufacturer": {"name": "STMicroelectronics"},
                        "shortDescription": "ARM Cortex-M3 MCU 64KB Flash",
                        "octopartUrl": "https://octopart.com/stm32f103c8t6-stmicroelectronics-1234",
                        "bestImage": {"url": "https://octopart.com/img/1234.png"},
                        "sellers": [
                            {
                                "company": {"name": "Digi-Key"},
                                "offers": [
                                    {
                                        "sku": "497-6063-ND",
                                        "inventoryLevel": 420,
                                        "moq": 1,
                                        "packaging": "Cut Tape",
                                        "clickUrl": "https://digikey.com/p/497-6063-ND",
                                        "updated": "2026-05-24T00:00:00Z",
                                        "prices": [
                                            {"quantity": 1, "price": 2.50, "currency": "USD",
                                             "convertedPrice": 2.50, "convertedCurrency": "USD"},
                                            {"quantity": 10, "price": 2.10, "currency": "USD",
                                             "convertedPrice": 2.10, "convertedCurrency": "USD"},
                                        ],
                                    }
                                ],
                            },
                            {
                                "company": {"name": "Mouser Electronics"},
                                "offers": [
                                    {
                                        "sku": "511-STM32F103C8T6",
                                        "inventoryLevel": 6000,
                                        "moq": 1,
                                        "packaging": "Bulk",
                                        "clickUrl": "https://mouser.com/p/511-STM32F103C8T6",
                                        "updated": "2026-05-24T00:00:00Z",
                                        "prices": [
                                            {"quantity": 1, "price": 2.45, "currency": "USD",
                                             "convertedPrice": 2.45, "convertedCurrency": "USD"},
                                        ],
                                    },
                                    {
                                        "sku": "511-STM32F103C8T6TR",
                                        "inventoryLevel": 12000,
                                        "moq": 1500,
                                        "packaging": "Tape & Reel",
                                        "clickUrl": "https://mouser.com/p/511-STM32F103C8T6TR",
                                        "updated": "2026-05-24T00:00:00Z",
                                        "prices": [
                                            {"quantity": 1500, "price": 1.42, "currency": "USD",
                                             "convertedPrice": 1.42, "convertedCurrency": "USD"},
                                        ],
                                    },
                                ],
                            },
                        ],
                    }
                }
            ]
        }
    }
}

NO_RESULTS_RESPONSE = {"data": {"supSearchMpn": {"results": []}}}


def _adapter_with_handler(handler: httpx.MockTransport, **kwargs: Any) -> OctopartAdapter:
    client = httpx.AsyncClient(transport=handler)
    return OctopartAdapter(
        client_id=kwargs.pop("client_id", "test-client-id"),
        client_secret=kwargs.pop("client_secret", "test-client-secret"),
        client=client,
        **kwargs,
    )


# ---------------------------------------------------------------------
# Auth gating
# ---------------------------------------------------------------------


async def test_missing_credentials_raises_auth_error() -> None:
    adapter = OctopartAdapter(client_id="", client_secret="")
    try:
        with pytest.raises(DistributorAuthError, match="credentials missing"):
            await adapter.lookup("STM32F103C8T6")
    finally:
        await adapter.aclose()


async def test_empty_mpn_returns_empty_without_token_mint() -> None:
    token_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal token_calls
        if str(request.url) == DEFAULT_TOKEN_URL:
            token_calls += 1
            return httpx.Response(200, json=TOKEN_RESPONSE)
        return httpx.Response(200, json=GRAPHQL_RESPONSE)

    adapter = _adapter_with_handler(httpx.MockTransport(handler))
    assert await adapter.lookup("") == []
    assert await adapter.lookup("   ") == []
    assert token_calls == 0
    await adapter.aclose()


async def test_token_mint_401_surfaces_auth_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == DEFAULT_TOKEN_URL:
            return httpx.Response(401, json={"error": "invalid_client"})
        return httpx.Response(200, json={})

    adapter = _adapter_with_handler(httpx.MockTransport(handler))
    try:
        with pytest.raises(DistributorAuthError, match="rejected credentials"):
            await adapter.lookup("STM32F103C8T6")
    finally:
        await adapter.aclose()


async def test_token_mint_500_surfaces_transport_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == DEFAULT_TOKEN_URL:
            return httpx.Response(500, text="boom")
        return httpx.Response(200, json={})

    adapter = _adapter_with_handler(httpx.MockTransport(handler))
    try:
        with pytest.raises(DistributorTransportError):
            await adapter.lookup("STM32F103C8T6")
    finally:
        await adapter.aclose()


async def test_token_is_cached_across_calls() -> None:
    token_calls = 0
    gql_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal token_calls, gql_calls
        if str(request.url) == DEFAULT_TOKEN_URL:
            token_calls += 1
            return httpx.Response(200, json=TOKEN_RESPONSE)
        gql_calls += 1
        assert request.headers.get("authorization") == "Bearer octopart-test-token"
        return httpx.Response(200, json=GRAPHQL_RESPONSE)

    adapter = _adapter_with_handler(httpx.MockTransport(handler))
    await adapter.lookup("STM32F103C8T6")
    await adapter.lookup("STM32F103C8T6")
    assert token_calls == 1
    assert gql_calls == 2
    await adapter.aclose()


async def test_concurrent_first_calls_only_mint_one_token() -> None:
    import asyncio

    token_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal token_calls
        if str(request.url) == DEFAULT_TOKEN_URL:
            token_calls += 1
            return httpx.Response(200, json=TOKEN_RESPONSE)
        return httpx.Response(200, json=GRAPHQL_RESPONSE)

    adapter = _adapter_with_handler(httpx.MockTransport(handler))
    await asyncio.gather(adapter.lookup("A"), adapter.lookup("B"))
    assert token_calls == 1
    await adapter.aclose()


# ---------------------------------------------------------------------
# GraphQL response parsing
# ---------------------------------------------------------------------


async def test_lookup_returns_one_quote_per_seller_offer_pair() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == DEFAULT_TOKEN_URL:
            return httpx.Response(200, json=TOKEN_RESPONSE)
        return httpx.Response(200, json=GRAPHQL_RESPONSE)

    adapter = _adapter_with_handler(httpx.MockTransport(handler))
    quotes = await adapter.lookup("STM32F103C8T6")
    # 1 Digi-Key offer + 2 Mouser offers = 3 quotes.
    assert len(quotes) == 3
    by_sku = {q.distributor_sku: q for q in quotes}
    dk = by_sku["497-6063-ND"]
    mouser_bulk = by_sku["511-STM32F103C8T6"]
    mouser_reel = by_sku["511-STM32F103C8T6TR"]
    # Seller surfaces as the distributor (lowercased, spaces → underscore).
    assert dk.distributor == "octopart-via-digi-key"
    assert mouser_bulk.distributor == "octopart-via-mouser_electronics"
    assert dk.manufacturer == "STMicroelectronics"
    assert dk.in_stock_qty == 420
    assert dk.moq == 1
    assert len(dk.price_breaks) == 2
    assert dk.price_breaks[0].unit_price_usd == pytest.approx(2.50)
    assert dk.price_breaks[1].min_qty == 10
    assert mouser_reel.moq == 1500
    assert mouser_reel.in_stock_qty == 12000
    # extras carry seller, packaging, octopart url
    assert dk.extras["seller_company"] == "Digi-Key"
    assert dk.extras["packaging"] == "Cut Tape"
    assert dk.extras["octopart_url"].startswith("https://octopart.com/")
    await adapter.aclose()


async def test_lookup_uses_converted_price_when_present() -> None:
    """Octopart Supply tier converts non-USD prices to a single
    currency on `convertedPrice`. The adapter must prefer that field
    so the aggregator's cheapest-mix selection actually compares
    USD-to-USD when sellers quote in EUR/JPY."""
    payload = json.loads(json.dumps(GRAPHQL_RESPONSE))
    # Swap one price to native EUR + converted USD.
    payload["data"]["supSearchMpn"]["results"][0]["part"]["sellers"][0]["offers"][0]["prices"] = [
        {"quantity": 1, "price": 2.10, "currency": "EUR",
         "convertedPrice": 2.30, "convertedCurrency": "USD"},
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == DEFAULT_TOKEN_URL:
            return httpx.Response(200, json=TOKEN_RESPONSE)
        return httpx.Response(200, json=payload)

    adapter = _adapter_with_handler(httpx.MockTransport(handler))
    quotes = await adapter.lookup("STM32F103C8T6")
    dk = next(q for q in quotes if q.distributor_sku == "497-6063-ND")
    assert dk.price_breaks[0].unit_price_usd == pytest.approx(2.30)
    await adapter.aclose()


async def test_lookup_falls_back_to_native_price_when_converted_missing() -> None:
    payload = json.loads(json.dumps(GRAPHQL_RESPONSE))
    payload["data"]["supSearchMpn"]["results"][0]["part"]["sellers"][0]["offers"][0]["prices"] = [
        {"quantity": 1, "price": 1.95, "currency": "USD"},
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == DEFAULT_TOKEN_URL:
            return httpx.Response(200, json=TOKEN_RESPONSE)
        return httpx.Response(200, json=payload)

    adapter = _adapter_with_handler(httpx.MockTransport(handler))
    quotes = await adapter.lookup("STM32F103C8T6")
    dk = next(q for q in quotes if q.distributor_sku == "497-6063-ND")
    assert dk.price_breaks[0].unit_price_usd == pytest.approx(1.95)
    await adapter.aclose()


async def test_lookup_returns_empty_when_no_results() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == DEFAULT_TOKEN_URL:
            return httpx.Response(200, json=TOKEN_RESPONSE)
        return httpx.Response(200, json=NO_RESULTS_RESPONSE)

    adapter = _adapter_with_handler(httpx.MockTransport(handler))
    assert await adapter.lookup("PHANTOM-9999") == []
    await adapter.aclose()


async def test_graphql_auth_error_in_envelope_clears_token_cache() -> None:
    """Nexar surfaces auth errors as a 200 + errors[] array. The
    adapter must raise + drop the cached token so the next call
    re-mints."""
    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == DEFAULT_TOKEN_URL:
            return httpx.Response(200, json=TOKEN_RESPONSE)
        return httpx.Response(
            200,
            json={"errors": [{"message": "Unauthorized"}], "data": None},
        )

    adapter = _adapter_with_handler(httpx.MockTransport(handler))
    try:
        with pytest.raises(DistributorAuthError):
            await adapter.lookup("STM32F103C8T6")
        assert adapter._token_cache is None  # type: ignore[attr-defined]
    finally:
        await adapter.aclose()


async def test_graphql_non_auth_errors_in_envelope_are_transport_failures() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == DEFAULT_TOKEN_URL:
            return httpx.Response(200, json=TOKEN_RESPONSE)
        return httpx.Response(
            200,
            json={"errors": [{"message": "Query failed: invalid limit"}], "data": None},
        )

    adapter = _adapter_with_handler(httpx.MockTransport(handler))
    try:
        with pytest.raises(DistributorTransportError, match="invalid limit"):
            await adapter.lookup("STM32F103C8T6")
    finally:
        await adapter.aclose()


async def test_graphql_401_at_http_layer_clears_token_cache() -> None:
    token_calls = 0
    state = {"first": True}

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal token_calls
        if str(request.url) == DEFAULT_TOKEN_URL:
            token_calls += 1
            return httpx.Response(200, json=TOKEN_RESPONSE)
        if state["first"]:
            state["first"] = False
            return httpx.Response(401, text="Unauthorized")
        return httpx.Response(200, json=GRAPHQL_RESPONSE)

    adapter = _adapter_with_handler(httpx.MockTransport(handler))
    try:
        with pytest.raises(DistributorAuthError):
            await adapter.lookup("STM32F103C8T6")
        # Next call must mint again — cache was cleared.
        await adapter.lookup("STM32F103C8T6")
        assert token_calls == 2
    finally:
        await adapter.aclose()


async def test_lookup_sends_graphql_query_with_mpn_variable() -> None:
    captured: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == DEFAULT_TOKEN_URL:
            return httpx.Response(200, json=TOKEN_RESPONSE)
        captured.append(json.loads(request.content.decode("utf-8")))
        return httpx.Response(200, json=GRAPHQL_RESPONSE)

    adapter = _adapter_with_handler(httpx.MockTransport(handler))
    await adapter.lookup("LM358D")
    assert len(captured) == 1
    assert captured[0]["variables"] == {"q": "LM358D"}
    assert "supSearchMpn" in captured[0]["query"]
    await adapter.aclose()
