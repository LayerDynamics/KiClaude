"""M3-P-04 — JLCPCB parts library adapter.

Same testing model as Digi-Key/Mouser/Octopart: httpx.MockTransport
against the documented JLCPCB SMT-shopping-cart endpoint shape. The
adapter code under test is the same code that runs against real
JLCPCB endpoints in production (with `JLCPCB_SESSION_COOKIE` for
higher quota; without it the anonymous flow still works).

FX rate: every test passes `cny_to_usd=0.14` explicitly so the FX
fetch never fires during unit tests — keeping them deterministic +
offline. The FX module cache is reset between tests via
`reset_fx_cache_for_tests`.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from kc_mcp.distributors import (
    DistributorAuthError,
    DistributorTransportError,
    JlcpcbAdapter,
)
from kc_mcp.distributors.jlcpcb import SEARCH_PATH, reset_fx_cache_for_tests

DETERMINISTIC_FX = 0.14  # 1 CNY = 0.14 USD for every test

# Documented JLCPCB SMT-component-list response shape.
SEARCH_RESPONSE: dict[str, Any] = {
    "code": 200,
    "message": "success",
    "data": {
        "componentInfoList": [
            {
                "componentCode": "C25804",
                "lcscPartNo": "C25804",
                "componentBrandEn": "STMicroelectronics",
                "componentBrand": "意法半导体",
                "describe": "ARM Cortex-M3 32-bit MCU 64K Flash LQFP-48",
                "componentSpecificationEn": "LQFP-48",
                "componentLibraryType": "expand",
                "firstSortAccessoryName": "Microcontrollers",
                "dataManualUrl": "https://datasheet.lcsc.com/lcsc/2103301034_STMicroelectronics_STM32F103C8T6.pdf",
                "minQuantity": 1,
                "stockCount": 8456,
                "componentPriceList": [
                    {"startNumber": 1, "endNumber": 9, "productPrice": 17.50},  # CNY
                    {"startNumber": 10, "endNumber": 99, "productPrice": 15.20},
                    {"startNumber": 100, "endNumber": 9999, "productPrice": 12.80},
                ],
            },
            {
                "componentCode": "C14857",
                "lcscPartNo": "C14857",
                "componentBrandEn": "Murata",
                "describe": "100nF X7R 0603 Cap",
                "componentSpecificationEn": "0603",
                "componentLibraryType": "base",
                "minQuantity": 50,
                "stockCount": 1_000_000,
                "componentPriceList": [
                    {"startNumber": 50, "endNumber": 999, "productPrice": 0.0143},
                ],
            },
        ]
    },
}

EMPTY_RESPONSE = {"code": 200, "message": "success", "data": {"componentInfoList": []}}


def _adapter_with_handler(handler: httpx.MockTransport, **kwargs: Any) -> JlcpcbAdapter:
    client = httpx.AsyncClient(transport=handler, base_url="https://jlcpcb.com")
    return JlcpcbAdapter(
        client=client,
        cny_to_usd=kwargs.pop("cny_to_usd", DETERMINISTIC_FX),
        **kwargs,
    )


@pytest.fixture(autouse=True)
def _reset_fx() -> None:
    reset_fx_cache_for_tests()
    yield
    reset_fx_cache_for_tests()


# ---------------------------------------------------------------------
# Lookup parsing
# ---------------------------------------------------------------------


async def test_lookup_returns_one_quote_per_component_with_cny_to_usd_conversion() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == SEARCH_PATH
        return httpx.Response(200, json=SEARCH_RESPONSE)

    adapter = _adapter_with_handler(httpx.MockTransport(handler))
    quotes = await adapter.lookup("STM32F103C8T6")
    assert len(quotes) == 2
    by_sku = {q.distributor_sku: q for q in quotes}
    mcu = by_sku["C25804"]
    cap = by_sku["C14857"]
    assert mcu.manufacturer == "STMicroelectronics"
    assert "Cortex-M3" in mcu.description
    assert mcu.in_stock_qty == 8456
    assert mcu.moq == 1
    assert mcu.lifecycle == "active"  # stock > 0
    # 17.50 CNY x 0.14 USD/CNY = 2.45 USD at qty 1
    assert mcu.price_breaks[0].unit_price_usd == pytest.approx(17.50 * DETERMINISTIC_FX)
    assert mcu.price_breaks[2].min_qty == 100
    assert mcu.price_breaks[2].unit_price_usd == pytest.approx(12.80 * DETERMINISTIC_FX)
    # Capacitor pricing
    assert cap.moq == 50
    assert cap.in_stock_qty == 1_000_000
    assert cap.extras["jlc_assembly_basic"] is True
    assert cap.extras["jlc_assembly_extended"] is False
    # MCU is in the expand library, not basic.
    assert mcu.extras["jlc_assembly_basic"] is False
    assert mcu.extras["jlc_assembly_extended"] is True
    await adapter.aclose()


async def test_lookup_empty_mpn_returns_empty_without_call() -> None:
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json=EMPTY_RESPONSE)

    adapter = _adapter_with_handler(httpx.MockTransport(handler))
    assert await adapter.lookup("") == []
    assert await adapter.lookup("   ") == []
    assert calls == 0
    await adapter.aclose()


async def test_lookup_returns_empty_on_no_components() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=EMPTY_RESPONSE)

    adapter = _adapter_with_handler(httpx.MockTransport(handler))
    assert await adapter.lookup("PHANTOM") == []
    await adapter.aclose()


async def test_lookup_handles_alternate_componentPageInfo_envelope() -> None:
    """Some JLCPCB endpoints wrap the list under `componentPageInfo.list`
    instead of `componentInfoList`. The adapter must handle both
    so a backend revision doesn't silently break sourcing."""

    alt = {
        "code": 200,
        "data": {
            "componentPageInfo": {
                "list": [SEARCH_RESPONSE["data"]["componentInfoList"][0]]
            }
        },
    }

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=alt)

    adapter = _adapter_with_handler(httpx.MockTransport(handler))
    quotes = await adapter.lookup("STM32F103C8T6")
    assert len(quotes) == 1
    assert quotes[0].distributor_sku == "C25804"
    await adapter.aclose()


async def test_lookup_lifecycle_falls_to_unknown_when_out_of_stock() -> None:
    payload = {
        "code": 200,
        "data": {
            "componentInfoList": [
                {
                    "componentCode": "C-DEAD",
                    "componentBrandEn": "X",
                    "describe": "X",
                    "minQuantity": 1,
                    "stockCount": 0,
                    "componentPriceList": [],
                }
            ]
        },
    }

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    adapter = _adapter_with_handler(httpx.MockTransport(handler))
    quotes = await adapter.lookup("X")
    assert quotes[0].lifecycle == "unknown"
    await adapter.aclose()


# ---------------------------------------------------------------------
# Auth + transport
# ---------------------------------------------------------------------


async def test_401_response_raises_auth_error() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="Unauthorized")

    adapter = _adapter_with_handler(httpx.MockTransport(handler))
    try:
        with pytest.raises(DistributorAuthError, match="rejected"):
            await adapter.lookup("X")
    finally:
        await adapter.aclose()


async def test_500_response_raises_transport_error() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    adapter = _adapter_with_handler(httpx.MockTransport(handler))
    try:
        with pytest.raises(DistributorTransportError):
            await adapter.lookup("X")
    finally:
        await adapter.aclose()


async def test_envelope_with_session_error_raises_auth() -> None:
    """JLCPCB returns 200 with `code != 200` + a login-related message
    when the session cookie has expired. The adapter must map that
    to auth so the aggregator surfaces a clear "refresh cookie"
    error to the user."""

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"code": 401, "message": "please login first", "data": None},
        )

    adapter = _adapter_with_handler(httpx.MockTransport(handler))
    try:
        with pytest.raises(DistributorAuthError, match="session"):
            await adapter.lookup("X")
    finally:
        await adapter.aclose()


async def test_envelope_with_generic_error_raises_transport() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"code": 500, "message": "Internal error: shard down", "data": None},
        )

    adapter = _adapter_with_handler(httpx.MockTransport(handler))
    try:
        with pytest.raises(DistributorTransportError, match="shard down"):
            await adapter.lookup("X")
    finally:
        await adapter.aclose()


async def test_session_cookie_when_present_attaches_to_request() -> None:
    seen_cookies: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_cookies.append(request.headers.get("cookie"))
        return httpx.Response(200, json=SEARCH_RESPONSE)

    adapter = _adapter_with_handler(
        httpx.MockTransport(handler),
        session_cookie="secretKey=abc123; deviceId=xyz",
    )
    await adapter.lookup("STM32F103C8T6")
    assert any("secretKey=abc123" in (c or "") for c in seen_cookies)
    await adapter.aclose()


async def test_no_session_cookie_still_runs_anonymous_flow() -> None:
    """JLCPCB serves anonymous queries — the adapter must NOT raise
    DistributorAuthError when there's no cookie."""
    seen_cookies: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_cookies.append(request.headers.get("cookie"))
        return httpx.Response(200, json=SEARCH_RESPONSE)

    adapter = _adapter_with_handler(
        httpx.MockTransport(handler),
        session_cookie="",
    )
    quotes = await adapter.lookup("STM32F103C8T6")
    assert len(quotes) == 2
    # No cookie header sent.
    assert all(c is None for c in seen_cookies)
    await adapter.aclose()


async def test_fx_rate_env_override_short_circuits_network_fetch() -> None:
    """The unit-test path uses cny_to_usd= explicitly; the env
    override pathway is a separate guarantee for deployed runs
    that want a pinned rate."""
    import os

    os.environ["JLCPCB_CNY_TO_USD"] = "0.20"
    try:
        adapter = JlcpcbAdapter(client=httpx.AsyncClient())
        try:
            rate = await adapter._get_cny_to_usd()  # type: ignore[attr-defined]
            assert rate == pytest.approx(0.20)
        finally:
            await adapter.aclose()
    finally:
        del os.environ["JLCPCB_CNY_TO_USD"]
