"""M3-P-06 — kc_part_search + kc_bom_price MCP tool wrappers.

Exercises the tool-level argument validation + envelope shape +
end-to-end happy path through a fake `PriceAggregator` injected via
the public `set_aggregator_factory` seam (no monkey-patching of
private state)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import pytest
from kc_mcp.distributors import (
    BomPricing,
    PartPricing,
    PartQuote,
    PriceBreakpoint,
)
from kc_mcp.distributors.aggregator import PriceAggregator
from kc_mcp.tools.sourcing import (
    kc_bom_price,
    kc_part_search,
    set_aggregator_factory,
)


def _quote(mpn: str, distributor: str = "digikey", unit_price: float = 1.0) -> PartQuote:
    return PartQuote(
        distributor=distributor,
        mpn=mpn,
        distributor_sku=f"{distributor[:3].upper()}-{mpn}",
        manufacturer="Test Mfg",
        description=f"{mpn} test description",
        in_stock_qty=1_000,
        moq=1,
        lifecycle="active",
        price_breaks=(PriceBreakpoint(min_qty=1, unit_price_usd=unit_price),),
        product_url=f"https://{distributor}.com/{mpn}",
        quoted_at=datetime.now(UTC),
    )


class _FakeAggregator(PriceAggregator):
    """Real PriceAggregator subclass — no adapters, override `price`
    and `price_bom` to return canned results. Keeps the production
    types intact so the envelope shaping path is the same as live."""

    def __init__(
        self,
        *,
        part_responses: dict[str, PartPricing] | None = None,
        bom_response: BomPricing | None = None,
    ) -> None:
        super().__init__(adapters=[])
        self._part_responses = part_responses or {}
        self._bom_response = bom_response
        self.close_calls = 0
        self.price_calls: list[tuple[str, int, bool]] = []
        self.bom_calls: list[tuple[list[tuple[str, int]], bool]] = []

    async def price(
        self, mpn: str, *, qty: int = 1, force_refresh: bool = False
    ) -> PartPricing:
        self.price_calls.append((mpn, qty, force_refresh))
        if mpn in self._part_responses:
            return self._part_responses[mpn]
        return PartPricing(
            mpn=mpn, requested_qty=qty, quotes=[], errors={}, cheapest=None,
            cheapest_unit_price_usd=None,
        )

    async def price_bom(
        self,
        bom: Any,
        *,
        force_refresh: bool = False,
    ) -> BomPricing:
        items: list[tuple[str, int]] = [
            (e[0], e[1]) if isinstance(e, tuple) else (str(e), 1) for e in bom
        ]
        self.bom_calls.append((items, force_refresh))
        if self._bom_response is not None:
            return self._bom_response
        return BomPricing(
            parts=[],
            distributor_totals_usd={},
            grand_total_usd=0.0,
            missing_mpns=[],
            errors={},
        )

    async def aclose(self) -> None:
        self.close_calls += 1


@pytest.fixture(autouse=True)
def _reset_factory() -> None:
    yield
    set_aggregator_factory(None)


def _structured(payload: dict[str, Any]) -> dict[str, Any]:
    """Pull the `structured` payload out of an envelope. Mirrors what
    the MCP client side does."""
    assert payload.get("structured") is not None
    return payload["structured"]


# ---------------------------------------------------------------------
# kc_part_search
# ---------------------------------------------------------------------


async def test_kc_part_search_returns_aggregator_payload_in_envelope() -> None:
    q = _quote("STM32F103C8T6", unit_price=2.50)
    pricing = PartPricing(
        mpn="STM32F103C8T6",
        requested_qty=5,
        quotes=[q],
        errors={},
        cheapest=q,
        cheapest_unit_price_usd=2.50,
    )
    agg = _FakeAggregator(part_responses={"STM32F103C8T6": pricing})
    set_aggregator_factory(lambda: agg)

    envelope = await kc_part_search.handler({"mpn": "STM32F103C8T6", "qty": 5})
    body = _structured(envelope)
    assert body["ok"] is True
    assert body["mpn"] == "STM32F103C8T6"
    assert body["requested_qty"] == 5
    assert body["line_total_usd"] == pytest.approx(2.50 * 5)
    assert body["cheapest"]["distributor"] == "digikey"
    assert body["cheapest"]["distributor_sku"] == "DIG-STM32F103C8T6"
    # quoted_at serialised as ISO-8601
    assert "T" in body["cheapest"]["quoted_at"]
    # The text frame is JSON-equal to the structured payload.
    decoded = json.loads(envelope["content"][0]["text"])
    assert decoded == body
    assert agg.price_calls == [("STM32F103C8T6", 5, False)]
    assert agg.close_calls == 1


async def test_kc_part_search_requires_mpn() -> None:
    envelope = await kc_part_search.handler({"mpn": "   "})
    body = _structured(envelope)
    assert body["ok"] is False
    assert "mpn" in body["error"]


async def test_kc_part_search_defaults_qty_to_one() -> None:
    agg = _FakeAggregator()
    set_aggregator_factory(lambda: agg)
    await kc_part_search.handler({"mpn": "X"})
    assert agg.price_calls == [("X", 1, False)]


async def test_kc_part_search_rejects_qty_below_one() -> None:
    set_aggregator_factory(lambda: _FakeAggregator())
    envelope = await kc_part_search.handler({"mpn": "X", "qty": 0})
    assert _structured(envelope)["ok"] is False


async def test_kc_part_search_force_refresh_flag_propagates() -> None:
    agg = _FakeAggregator()
    set_aggregator_factory(lambda: agg)
    await kc_part_search.handler({"mpn": "X", "force_refresh": True})
    assert agg.price_calls[0][2] is True


async def test_kc_part_search_closes_aggregator_even_on_exception() -> None:
    class _BoomAggregator(_FakeAggregator):
        async def price(self, *args: Any, **kwargs: Any) -> PartPricing:
            raise RuntimeError("boom")

    boom = _BoomAggregator()
    set_aggregator_factory(lambda: boom)
    with pytest.raises(RuntimeError):
        await kc_part_search.handler({"mpn": "X"})
    assert boom.close_calls == 1


# ---------------------------------------------------------------------
# kc_bom_price
# ---------------------------------------------------------------------


async def test_kc_bom_price_accepts_object_entries_with_qty() -> None:
    mcu = _quote("MCU", "mouser", unit_price=2.10)
    cap = _quote("CAP", "digikey", unit_price=0.10)
    bom_response = BomPricing(
        parts=[
            PartPricing(
                mpn="MCU", requested_qty=5, quotes=[mcu], errors={},
                cheapest=mcu, cheapest_unit_price_usd=2.10,
            ),
            PartPricing(
                mpn="CAP", requested_qty=100, quotes=[cap], errors={},
                cheapest=cap, cheapest_unit_price_usd=0.10,
            ),
        ],
        distributor_totals_usd={"mouser": 10.5, "digikey": 10.0},
        grand_total_usd=20.5,
        missing_mpns=[],
        errors={},
    )
    agg = _FakeAggregator(bom_response=bom_response)
    set_aggregator_factory(lambda: agg)

    envelope = await kc_bom_price.handler(
        {"parts": [{"mpn": "MCU", "qty": 5}, {"mpn": "CAP", "qty": 100}]}
    )
    body = _structured(envelope)
    assert body["ok"] is True
    assert body["grand_total_usd"] == pytest.approx(20.5)
    assert body["distributor_totals_usd"] == {"mouser": 10.5, "digikey": 10.0}
    assert len(body["parts"]) == 2
    assert body["parts"][0]["cheapest"]["distributor"] == "mouser"
    # The aggregator saw the right tuples.
    assert agg.bom_calls[0][0] == [("MCU", 5), ("CAP", 100)]


async def test_kc_bom_price_accepts_bare_mpn_strings_as_qty_one() -> None:
    agg = _FakeAggregator(
        bom_response=BomPricing(
            parts=[], distributor_totals_usd={}, grand_total_usd=0.0,
            missing_mpns=[], errors={},
        )
    )
    set_aggregator_factory(lambda: agg)
    await kc_bom_price.handler({"parts": ["A", "B"]})
    assert agg.bom_calls[0][0] == [("A", 1), ("B", 1)]


async def test_kc_bom_price_requires_non_empty_parts_list() -> None:
    assert _structured(await kc_bom_price.handler({}))["ok"] is False
    assert _structured(await kc_bom_price.handler({"parts": []}))["ok"] is False
    assert _structured(await kc_bom_price.handler({"parts": "X"}))["ok"] is False


async def test_kc_bom_price_rejects_missing_mpn_field() -> None:
    set_aggregator_factory(lambda: _FakeAggregator())
    envelope = await kc_bom_price.handler({"parts": [{"qty": 5}]})
    body = _structured(envelope)
    assert body["ok"] is False
    assert "mpn" in body["error"]


async def test_kc_bom_price_rejects_non_int_qty() -> None:
    set_aggregator_factory(lambda: _FakeAggregator())
    envelope = await kc_bom_price.handler({"parts": [{"mpn": "X", "qty": "five"}]})
    body = _structured(envelope)
    assert body["ok"] is False
    assert "qty" in body["error"]


async def test_kc_bom_price_surfaces_missing_mpns_in_envelope() -> None:
    bom = BomPricing(
        parts=[
            PartPricing(
                mpn="REAL", requested_qty=1, quotes=[], errors={},
                cheapest=None, cheapest_unit_price_usd=None,
            ),
            PartPricing(
                mpn="PHANTOM", requested_qty=1, quotes=[], errors={},
                cheapest=None, cheapest_unit_price_usd=None,
            ),
        ],
        distributor_totals_usd={},
        grand_total_usd=0.0,
        missing_mpns=["PHANTOM"],
        errors={"digikey": ["timeout after 10.0s"]},
    )
    agg = _FakeAggregator(bom_response=bom)
    set_aggregator_factory(lambda: agg)
    envelope = await kc_bom_price.handler({"parts": [{"mpn": "REAL"}, {"mpn": "PHANTOM"}]})
    body = _structured(envelope)
    assert body["missing_mpns"] == ["PHANTOM"]
    assert body["errors"] == {"digikey": ["timeout after 10.0s"]}


async def test_kc_bom_price_closes_aggregator_after_call() -> None:
    agg = _FakeAggregator()
    set_aggregator_factory(lambda: agg)
    await kc_bom_price.handler({"parts": ["X"]})
    assert agg.close_calls == 1


async def test_kc_bom_price_force_refresh_flag_propagates() -> None:
    agg = _FakeAggregator()
    set_aggregator_factory(lambda: agg)
    await kc_bom_price.handler({"parts": ["X"], "force_refresh": True})
    assert agg.bom_calls[0][1] is True
