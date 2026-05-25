"""Tests for the full kc_mpn_resolve (T7): live distributor stock via
the aggregator (injected fake) + library candidates via a mocked
kiserver `/library/search`.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
from kc_mcp import clients
from kc_mcp.distributors.aggregator import PartPricing
from kc_mcp.distributors.base import PartQuote, PriceBreakpoint
from kc_mcp.tools.mpn import kc_mpn_resolve
from kc_mcp.tools.sourcing import set_aggregator_factory


def _payload(result: dict[str, Any]) -> dict[str, Any]:
    return json.loads(result["content"][0]["text"])


def _quote(mpn: str) -> PartQuote:
    return PartQuote(
        distributor="digikey",
        mpn=mpn,
        distributor_sku="DK-1",
        manufacturer="STMicroelectronics",
        description="ARM MCU",
        in_stock_qty=500,
        moq=1,
        lifecycle="active",
        price_breaks=(PriceBreakpoint(min_qty=1, unit_price_usd=1.50),),
        product_url="https://www.digikey.com/x",
        quoted_at=datetime.now(UTC),
        extras={},
    )


class _Agg:
    """Duck-typed aggregator returning a canned PartPricing."""

    def __init__(self, pricing: PartPricing) -> None:
        self._pricing = pricing

    async def price(self, mpn: str, *, qty: int = 1, force_refresh: bool = False) -> PartPricing:
        return self._pricing

    async def aclose(self) -> None:
        return None


def _use_aggregator(pricing: PartPricing) -> None:
    set_aggregator_factory(lambda: _Agg(pricing))  # type: ignore[arg-type,return-value]


@pytest.fixture(autouse=True)
def _reset_factory():  # type: ignore[no-untyped-def]
    yield
    set_aggregator_factory(None)
    clients.set_client(None)


async def test_blank_mpn_errors() -> None:
    out = _payload(await kc_mpn_resolve.handler({"mpn": "   "}))
    assert out["ok"] is False
    assert "required" in out["error"]


async def test_shape_rejection_is_not_found() -> None:
    # Case 1: does not match the MPN shape at all
    out = _payload(await kc_mpn_resolve.handler({"mpn": "!!!"}))
    assert out["ok"] is True
    assert out["found"] is False
    assert out["reason"] == "not_an_mpn_shape"
    assert out["confidence"] == 0.0

    # Case 2: matches the MPN shape but is missing a digit or a letter
    out_missing = _payload(await kc_mpn_resolve.handler({"mpn": "ABCDEF"}))
    assert out_missing["ok"] is True
    assert out_missing["found"] is False
    assert out_missing["reason"] == "missing_digit_or_letter"
    assert out_missing["confidence"] == 0.0


async def test_distributor_hit_marks_found_with_stock() -> None:
    q = _quote("STM32G030F6P6")
    _use_aggregator(
        PartPricing(
            mpn="STM32G030F6P6",
            requested_qty=1,
            quotes=[q],
            errors={},
            cheapest=q,
            cheapest_unit_price_usd=1.50,
        )
    )
    out = _payload(await kc_mpn_resolve.handler({"mpn": "STM32G030F6P6"}))
    assert out["found"] is True
    assert out["confidence"] >= 0.97
    assert out["stock"]["cheapest"]["in_stock_qty"] == 500
    assert out["stock"]["cheapest"]["lifecycle"] == "active"


async def test_no_distributor_hit_is_not_found() -> None:
    _use_aggregator(
        PartPricing(
            mpn="STM32G030F6P6",
            requested_qty=1,
            quotes=[],
            errors={},
            cheapest=None,
            cheapest_unit_price_usd=None,
        )
    )
    out = _payload(
        await kc_mpn_resolve.handler({"mpn": "STM32G030F6P6", "manufacturer": "ST"})
    )
    assert out["found"] is False
    assert 0.7 <= out["confidence"] < 0.97  # metadata-scaled, not a distributor hit


async def test_library_candidates_from_project(monkeypatch: pytest.MonkeyPatch) -> None:
    _use_aggregator(
        PartPricing(
            mpn="STM32G030F6P6", requested_qty=1, quotes=[], errors={}, cheapest=None,
            cheapest_unit_price_usd=None,
        )
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if "/library/search" in request.url.path and request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "ok": True,
                    "hits": [
                        {
                            "lib_id": "MCU_ST_STM32G0:STM32G030F6Px",
                            "description": "Cortex-M0+ MCU",
                            "datasheet": "https://st.com/g030.pdf",
                            "footprint": "Package_SO:TSSOP-20",
                            "footprint_filter": "TSSOP*",
                            "score": 12.5,
                        }
                    ],
                },
            )
        return httpx.Response(404, json={"detail": "no mock"})

    clients.set_client(httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://mock"))
    monkeypatch.setattr(clients, "_kiserver_url", "")

    out = _payload(await kc_mpn_resolve.handler({"mpn": "STM32G030F6P6", "project_id": "p1"}))
    assert out["symbol_candidates"][0]["lib_id"] == "MCU_ST_STM32G0:STM32G030F6Px"
    assert "Package_SO:TSSOP-20" in out["footprint_candidates"]
    # candidates without a distributor hit nudge confidence above bare metadata.
    assert out["found"] is False
