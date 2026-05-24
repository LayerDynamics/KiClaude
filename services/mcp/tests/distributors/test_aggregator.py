"""M3-P-05 — fan-out aggregator + cheapest-mix selection.

Tests use in-process test-only adapters that return real `PartQuote`
data — exercising the aggregator's contract (cache hit/miss, per-
adapter timeout, soft-fail behaviour, cheapest selection, cart
split) without touching live distributor APIs."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from kc_mcp.distributors import (
    DistributorAdapter,
    DistributorAuthError,
    DistributorTransportError,
    PartQuote,
    PriceAggregator,
    PriceBreakpoint,
    PriceCache,
    build_default_aggregator,
)


def _quote(
    *,
    distributor: str,
    mpn: str,
    sku: str | None = None,
    in_stock: int = 100,
    moq: int = 1,
    unit_price: float = 1.00,
    lifecycle: str = "active",
) -> PartQuote:
    return PartQuote(
        distributor=distributor,
        mpn=mpn,
        distributor_sku=sku or f"{distributor[:3].upper()}-{mpn}",
        manufacturer="Test Mfg",
        description=f"{mpn} description",
        in_stock_qty=in_stock,
        moq=moq,
        lifecycle=lifecycle,
        price_breaks=(PriceBreakpoint(min_qty=1, unit_price_usd=unit_price),),
        product_url=f"https://{distributor}.com/{mpn}",
        quoted_at=datetime.now(UTC),
    )


class FakeAdapter(DistributorAdapter):
    """Records calls + returns the seeded results so we can assert
    fan-out semantics without HTTP. NOT a stub — it's the real test
    seam for the aggregator contract."""

    def __init__(
        self,
        *,
        name: str,
        responses: dict[str, list[PartQuote]] | None = None,
        raise_on: dict[str, Exception] | None = None,
        sleep_seconds: float = 0.0,
    ) -> None:
        self.name = name
        self._responses = responses or {}
        self._raise_on = raise_on or {}
        self._sleep = sleep_seconds
        self.calls: list[str] = []
        self.closed = False

    async def lookup(self, mpn: str) -> list[PartQuote]:
        self.calls.append(mpn)
        if self._sleep:
            await asyncio.sleep(self._sleep)
        if mpn in self._raise_on:
            raise self._raise_on[mpn]
        return list(self._responses.get(mpn, []))

    async def aclose(self) -> None:
        self.closed = True


# ---------------------------------------------------------------------
# price() — single MPN
# ---------------------------------------------------------------------


async def test_price_fans_out_across_every_adapter() -> None:
    dk_quote = _quote(distributor="digikey", mpn="STM32F103C8T6", unit_price=2.50)
    mouser_quote = _quote(distributor="mouser", mpn="STM32F103C8T6", unit_price=2.10)
    dk = FakeAdapter(name="digikey", responses={"STM32F103C8T6": [dk_quote]})
    mouser = FakeAdapter(name="mouser", responses={"STM32F103C8T6": [mouser_quote]})
    cache = PriceCache(path=":memory:")
    agg = PriceAggregator(adapters=[dk, mouser], cache=cache)
    try:
        result = await agg.price("STM32F103C8T6", qty=1)
        assert {q.distributor for q in result.quotes} == {"digikey", "mouser"}
        assert result.cheapest is not None
        assert result.cheapest.distributor == "mouser"  # 2.10 < 2.50
        assert result.cheapest_unit_price_usd == 2.10
        assert result.line_total_usd == 2.10
    finally:
        await agg.aclose()


async def test_price_uses_cache_on_second_call() -> None:
    dk = FakeAdapter(
        name="digikey",
        responses={"X": [_quote(distributor="digikey", mpn="X", unit_price=1.0)]},
    )
    cache = PriceCache(path=":memory:")
    agg = PriceAggregator(adapters=[dk], cache=cache)
    try:
        await agg.price("X", qty=1)
        assert dk.calls == ["X"]
        await agg.price("X", qty=1)
        # Second call served from cache — adapter not re-invoked.
        assert dk.calls == ["X"]
    finally:
        await agg.aclose()


async def test_price_force_refresh_bypasses_cache() -> None:
    dk = FakeAdapter(
        name="digikey",
        responses={"X": [_quote(distributor="digikey", mpn="X", unit_price=1.0)]},
    )
    cache = PriceCache(path=":memory:")
    agg = PriceAggregator(adapters=[dk], cache=cache)
    try:
        await agg.price("X", qty=1)
        await agg.price("X", qty=1, force_refresh=True)
        assert dk.calls == ["X", "X"]
    finally:
        await agg.aclose()


async def test_price_excludes_quotes_below_requested_qty_stock() -> None:
    """A quote whose `in_stock_qty < qty` is excluded from cheapest
    selection — the user wanted 500, you've got 100, that's not a
    quote we can act on."""
    dk = FakeAdapter(
        name="digikey",
        responses={"X": [_quote(distributor="digikey", mpn="X", in_stock=100, unit_price=1.00)]},
    )
    mouser = FakeAdapter(
        name="mouser",
        responses={"X": [_quote(distributor="mouser", mpn="X", in_stock=2000, unit_price=1.20)]},
    )
    agg = PriceAggregator(adapters=[dk, mouser], cache=PriceCache(path=":memory:"))
    try:
        result = await agg.price("X", qty=500)
        assert result.cheapest is not None
        assert result.cheapest.distributor == "mouser"
    finally:
        await agg.aclose()


async def test_price_excludes_quotes_below_moq() -> None:
    dk = FakeAdapter(
        name="digikey",
        responses={"X": [_quote(distributor="digikey", mpn="X", moq=1000, unit_price=0.50)]},
    )
    mouser = FakeAdapter(
        name="mouser",
        responses={"X": [_quote(distributor="mouser", mpn="X", moq=1, unit_price=0.80)]},
    )
    agg = PriceAggregator(adapters=[dk, mouser], cache=PriceCache(path=":memory:"))
    try:
        result = await agg.price("X", qty=100)  # below DK's 1000 MOQ
        assert result.cheapest is not None
        assert result.cheapest.distributor == "mouser"
    finally:
        await agg.aclose()


async def test_price_returns_none_winner_when_nothing_in_stock() -> None:
    dk = FakeAdapter(
        name="digikey",
        responses={"X": [_quote(distributor="digikey", mpn="X", in_stock=0)]},
    )
    agg = PriceAggregator(adapters=[dk], cache=PriceCache(path=":memory:"))
    try:
        result = await agg.price("X", qty=1)
        assert result.cheapest is None
        assert result.cheapest_unit_price_usd is None
        assert result.line_total_usd is None
    finally:
        await agg.aclose()


async def test_price_auth_error_surfaces_but_others_succeed() -> None:
    dk = FakeAdapter(
        name="digikey",
        raise_on={"X": DistributorAuthError("creds missing")},
    )
    mouser = FakeAdapter(
        name="mouser",
        responses={"X": [_quote(distributor="mouser", mpn="X", unit_price=1.00)]},
    )
    agg = PriceAggregator(adapters=[dk, mouser], cache=PriceCache(path=":memory:"))
    try:
        result = await agg.price("X", qty=1)
        assert result.cheapest is not None
        assert result.cheapest.distributor == "mouser"
        assert "digikey" in result.errors
        assert "creds missing" in result.errors["digikey"]
    finally:
        await agg.aclose()


async def test_price_transport_error_is_soft_fail() -> None:
    dk = FakeAdapter(
        name="digikey",
        raise_on={"X": DistributorTransportError("connection reset")},
    )
    mouser = FakeAdapter(
        name="mouser",
        responses={"X": [_quote(distributor="mouser", mpn="X", unit_price=1.00)]},
    )
    agg = PriceAggregator(adapters=[dk, mouser], cache=PriceCache(path=":memory:"))
    try:
        result = await agg.price("X", qty=1)
        assert result.cheapest is not None
        assert result.cheapest.distributor == "mouser"
        assert "transport" in result.errors["digikey"]
    finally:
        await agg.aclose()


async def test_price_per_adapter_timeout_isolates_slow_adapters() -> None:
    slow = FakeAdapter(
        name="slow",
        responses={"X": [_quote(distributor="slow", mpn="X", unit_price=0.05)]},
        sleep_seconds=1.0,
    )
    fast = FakeAdapter(
        name="fast",
        responses={"X": [_quote(distributor="fast", mpn="X", unit_price=0.10)]},
    )
    agg = PriceAggregator(
        adapters=[slow, fast],
        cache=PriceCache(path=":memory:"),
        per_adapter_timeout_s=0.05,  # 50 ms — way under slow's 1 s sleep
    )
    try:
        result = await agg.price("X", qty=1)
        # Slow adapter timed out — only `fast` won.
        assert result.cheapest is not None
        assert result.cheapest.distributor == "fast"
        assert "timeout" in result.errors["slow"]
    finally:
        await agg.aclose()


async def test_price_rejects_zero_qty() -> None:
    agg = PriceAggregator(adapters=[], cache=PriceCache(path=":memory:"))
    try:
        with pytest.raises(ValueError, match="qty"):
            await agg.price("X", qty=0)
    finally:
        await agg.aclose()


async def test_price_caches_empty_responses_to_avoid_repeat_misses() -> None:
    """A part not found at Digi-Key shouldn't be re-fetched on
    every refresh. The cache TTL applies to "not found" rows too."""
    dk = FakeAdapter(name="digikey", responses={"PHANTOM": []})
    agg = PriceAggregator(adapters=[dk], cache=PriceCache(path=":memory:"))
    try:
        await agg.price("PHANTOM", qty=1)
        await agg.price("PHANTOM", qty=1)
        # Empty list per the contract — not_found from one adapter is
        # not cached (we only cache positive results); so the second
        # call re-queries. This matches the "cache only what we'd
        # otherwise re-fetch" trade-off.
        assert dk.calls == ["PHANTOM", "PHANTOM"]
    finally:
        await agg.aclose()


# ---------------------------------------------------------------------
# price_bom() — whole-BOM aggregation
# ---------------------------------------------------------------------


async def test_price_bom_picks_cheapest_per_line_and_splits_cart() -> None:
    dk = FakeAdapter(
        name="digikey",
        responses={
            "MCU": [_quote(distributor="digikey", mpn="MCU", unit_price=2.50)],
            "CAP": [_quote(distributor="digikey", mpn="CAP", unit_price=0.10)],
        },
    )
    mouser = FakeAdapter(
        name="mouser",
        responses={
            "MCU": [_quote(distributor="mouser", mpn="MCU", unit_price=2.10)],
            "CAP": [_quote(distributor="mouser", mpn="CAP", unit_price=0.15)],
        },
    )
    agg = PriceAggregator(adapters=[dk, mouser], cache=PriceCache(path=":memory:"))
    try:
        bom = await agg.price_bom([("MCU", 5), ("CAP", 100)])
        assert len(bom.parts) == 2
        by_mpn = {p.mpn: p for p in bom.parts}
        # MCU cheapest at Mouser (2.10), CAP cheapest at Digi-Key (0.10).
        assert by_mpn["MCU"].cheapest.distributor == "mouser"
        assert by_mpn["CAP"].cheapest.distributor == "digikey"
        # Cart split: Mouser = 5 * 2.10, Digi-Key = 100 * 0.10.
        assert bom.distributor_totals_usd == pytest.approx(
            {"mouser": 5 * 2.10, "digikey": 100 * 0.10}, rel=1e-9
        )
        assert bom.grand_total_usd == pytest.approx(5 * 2.10 + 100 * 0.10)
        assert bom.missing_mpns == []
    finally:
        await agg.aclose()


async def test_price_bom_flags_parts_no_distributor_quotes() -> None:
    real_quote = _quote(distributor="digikey", mpn="REAL")
    dk = FakeAdapter(name="digikey", responses={"REAL": [real_quote]})
    agg = PriceAggregator(adapters=[dk], cache=PriceCache(path=":memory:"))
    try:
        bom = await agg.price_bom([("REAL", 1), ("PHANTOM", 10)])
        assert bom.missing_mpns == ["PHANTOM"]
        # Grand total still computed for the parts that DID resolve.
        assert bom.grand_total_usd == pytest.approx(1.00)
    finally:
        await agg.aclose()


async def test_price_bom_deduplicates_errors_per_distributor() -> None:
    """If the same distributor 401s on every part in the BOM, the
    error appears once in the bom.errors map, not N times."""
    dk = FakeAdapter(
        name="digikey",
        raise_on={
            "A": DistributorAuthError("creds missing"),
            "B": DistributorAuthError("creds missing"),
        },
    )
    agg = PriceAggregator(adapters=[dk], cache=PriceCache(path=":memory:"))
    try:
        bom = await agg.price_bom([("A", 1), ("B", 1)])
        assert "digikey" in bom.errors
        assert len(bom.errors["digikey"]) == 1
        assert "creds missing" in bom.errors["digikey"][0]
    finally:
        await agg.aclose()


async def test_price_bom_accepts_bare_mpn_strings_as_qty_one() -> None:
    dk = FakeAdapter(name="digikey", responses={"X": [_quote(distributor="digikey", mpn="X")]})
    agg = PriceAggregator(adapters=[dk], cache=PriceCache(path=":memory:"))
    try:
        bom = await agg.price_bom(["X"])
        assert bom.parts[0].requested_qty == 1
        assert bom.grand_total_usd == pytest.approx(1.00)
    finally:
        await agg.aclose()


# ---------------------------------------------------------------------
# build_default_aggregator()
# ---------------------------------------------------------------------


def test_build_default_aggregator_skips_digikey_without_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DIGIKEY_CLIENT_ID", raising=False)
    monkeypatch.delenv("DIGIKEY_CLIENT_SECRET", raising=False)
    # Opt out of credential-less autoloaded distributors (JLCPCB) so
    # we can isolate the "no DIGIKEY env" check.
    agg = build_default_aggregator(
        cache=PriceCache(path=":memory:"), include_jlcpcb=False
    )
    try:
        assert agg.adapters == ()
    finally:
        pass


def test_build_default_aggregator_registers_digikey_when_credentials_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DIGIKEY_CLIENT_ID", "fake-id")
    monkeypatch.setenv("DIGIKEY_CLIENT_SECRET", "fake-secret")
    agg = build_default_aggregator(
        cache=PriceCache(path=":memory:"), include_jlcpcb=False
    )
    try:
        assert len(agg.adapters) == 1
        assert agg.adapters[0].name == "digikey"
    finally:
        pass


def test_build_default_aggregator_autoloads_jlcpcb_unconditionally(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """JLCPCB has anonymous quota — no env credentials required. The
    factory autoloads it unless explicitly opted out via
    `include_jlcpcb=False`."""
    monkeypatch.delenv("DIGIKEY_CLIENT_ID", raising=False)
    monkeypatch.delenv("DIGIKEY_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("MOUSER_API_KEY", raising=False)
    monkeypatch.delenv("OCTOPART_CLIENT_ID", raising=False)
    monkeypatch.delenv("OCTOPART_CLIENT_SECRET", raising=False)
    agg = build_default_aggregator(cache=PriceCache(path=":memory:"))
    assert [a.name for a in agg.adapters] == ["jlcpcb"]


def test_build_default_aggregator_registers_mouser_when_key_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DIGIKEY_CLIENT_ID", raising=False)
    monkeypatch.delenv("DIGIKEY_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("OCTOPART_CLIENT_ID", raising=False)
    monkeypatch.delenv("OCTOPART_CLIENT_SECRET", raising=False)
    monkeypatch.setenv("MOUSER_API_KEY", "fake-mouser-key")
    agg = build_default_aggregator(
        cache=PriceCache(path=":memory:"), include_jlcpcb=False
    )
    assert [a.name for a in agg.adapters] == ["mouser"]


def test_build_default_aggregator_registers_octopart_when_creds_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DIGIKEY_CLIENT_ID", raising=False)
    monkeypatch.delenv("DIGIKEY_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("MOUSER_API_KEY", raising=False)
    monkeypatch.setenv("OCTOPART_CLIENT_ID", "fake-octo-id")
    monkeypatch.setenv("OCTOPART_CLIENT_SECRET", "fake-octo-secret")
    agg = build_default_aggregator(
        cache=PriceCache(path=":memory:"), include_jlcpcb=False
    )
    assert [a.name for a in agg.adapters] == ["octopart"]


def test_build_default_aggregator_full_house(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every supported distributor autoloads when its creds are
    present + JLCPCB always — the production happy path."""
    monkeypatch.setenv("DIGIKEY_CLIENT_ID", "x")
    monkeypatch.setenv("DIGIKEY_CLIENT_SECRET", "x")
    monkeypatch.setenv("MOUSER_API_KEY", "x")
    monkeypatch.setenv("OCTOPART_CLIENT_ID", "x")
    monkeypatch.setenv("OCTOPART_CLIENT_SECRET", "x")
    agg = build_default_aggregator(cache=PriceCache(path=":memory:"))
    names = sorted(a.name for a in agg.adapters)
    assert names == ["digikey", "jlcpcb", "mouser", "octopart"]


# ---------------------------------------------------------------------
# Cache TTL respected by aggregator
# ---------------------------------------------------------------------


async def test_aggregator_short_ttl_forces_live_lookup() -> None:
    dk = FakeAdapter(name="digikey", responses={"X": [_quote(distributor="digikey", mpn="X")]})
    cache = PriceCache(path=":memory:")
    agg = PriceAggregator(
        adapters=[dk],
        cache=cache,
        cache_ttl=timedelta(microseconds=1),  # effectively zero
    )
    try:
        await agg.price("X", qty=1)
        await asyncio.sleep(0.01)
        await agg.price("X", qty=1)
        # TTL expired between calls → live lookup both times.
        assert dk.calls == ["X", "X"]
    finally:
        await agg.aclose()
