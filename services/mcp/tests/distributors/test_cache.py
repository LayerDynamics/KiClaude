"""M3-P-05 — SQLite price cache.

Tests run against in-memory SQLite so they don't touch the user's
real cache file. Covers: round-trip put → get, TTL filter,
multi-SKU per MPN, purge, count, and the upsert behaviour."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from kc_mcp.distributors import PartQuote, PriceBreakpoint, PriceCache


def _quote(
    *,
    distributor: str = "digikey",
    mpn: str = "STM32F103C8T6",
    sku: str = "497-6063-ND",
    when: datetime | None = None,
    unit_price: float = 2.50,
) -> PartQuote:
    return PartQuote(
        distributor=distributor,
        mpn=mpn,
        distributor_sku=sku,
        manufacturer="STMicroelectronics",
        description="ARM MCU 64KB Flash",
        in_stock_qty=420,
        moq=1,
        lifecycle="active",
        price_breaks=(
            PriceBreakpoint(min_qty=1, unit_price_usd=unit_price),
            PriceBreakpoint(min_qty=10, unit_price_usd=unit_price * 0.85),
            PriceBreakpoint(min_qty=100, unit_price_usd=unit_price * 0.65),
        ),
        product_url=f"https://digikey.com/p/{sku}",
        quoted_at=when or datetime.now(UTC),
        extras={"packaging": "Tape & Reel"},
    )


def test_put_then_get_round_trips_every_field() -> None:
    with PriceCache(path=":memory:") as cache:
        q = _quote()
        assert cache.put([q]) == 1
        rows = cache.get(distributor="digikey", mpn="STM32F103C8T6")
        assert rows is not None
        assert len(rows) == 1
        out = rows[0]
        assert out.distributor == q.distributor
        assert out.mpn == q.mpn
        assert out.distributor_sku == q.distributor_sku
        assert out.manufacturer == q.manufacturer
        assert out.description == q.description
        assert out.in_stock_qty == q.in_stock_qty
        assert out.lifecycle == q.lifecycle
        assert out.price_breaks == q.price_breaks
        assert out.extras == q.extras


def test_get_miss_returns_none_not_empty_list() -> None:
    """`None` = "no fresh entry, fetch live"; `[]` = "cached as
    not-found". The cache must distinguish."""
    with PriceCache(path=":memory:") as cache:
        assert cache.get(distributor="digikey", mpn="MISSING-PART") is None


def test_ttl_filter_drops_expired_entries() -> None:
    with PriceCache(path=":memory:") as cache:
        stale = _quote(when=datetime.now(UTC) - timedelta(hours=24))
        cache.put([stale])
        # Default TTL is 6h — 24h-old entry must miss.
        assert cache.get(distributor="digikey", mpn=stale.mpn) is None
        # But within a 48h TTL it hits.
        rows = cache.get(distributor="digikey", mpn=stale.mpn, max_age=timedelta(hours=48))
        assert rows is not None
        assert len(rows) == 1


def test_multiple_skus_under_one_mpn_round_trip() -> None:
    """One MPN can map to several distributor SKUs (cut tape vs
    reel). All must come back on the same get()."""
    with PriceCache(path=":memory:") as cache:
        cache.put(
            [
                _quote(sku="497-6063-1-ND", unit_price=2.50),  # cut tape
                _quote(sku="497-6063-2-ND", unit_price=2.30),  # tape & reel
            ]
        )
        rows = cache.get(distributor="digikey", mpn="STM32F103C8T6")
        assert rows is not None
        skus = {r.distributor_sku for r in rows}
        assert skus == {"497-6063-1-ND", "497-6063-2-ND"}


def test_upsert_replaces_same_triple_not_appends() -> None:
    """A second put() with the same (distributor, mpn, sku) must
    overwrite, not duplicate."""
    with PriceCache(path=":memory:") as cache:
        cache.put([_quote(unit_price=2.50)])
        cache.put([_quote(unit_price=1.95)])  # price refresh
        rows = cache.get(distributor="digikey", mpn="STM32F103C8T6")
        assert rows is not None
        assert len(rows) == 1
        assert rows[0].price_breaks[0].unit_price_usd == 1.95


def test_purge_older_than_drops_stale_keeps_fresh() -> None:
    with PriceCache(path=":memory:") as cache:
        cache.put(
            [
                _quote(sku="stale", when=datetime.now(UTC) - timedelta(days=2)),
                _quote(sku="fresh"),
            ]
        )
        assert cache.count() == 2
        purged = cache.purge_older_than(timedelta(days=1))
        assert purged == 1
        assert cache.count() == 1
        rows = cache.get(distributor="digikey", mpn="STM32F103C8T6")
        assert rows is not None
        assert rows[0].distributor_sku == "fresh"


def test_unit_price_at_qty_walks_the_price_ladder() -> None:
    q = _quote()
    assert q.unit_price_at_qty(1) == 2.50
    assert q.unit_price_at_qty(5) == 2.50
    assert q.unit_price_at_qty(10) == 2.50 * 0.85
    assert q.unit_price_at_qty(99) == 2.50 * 0.85
    assert q.unit_price_at_qty(100) == 2.50 * 0.65
    assert q.unit_price_at_qty(50000) == 2.50 * 0.65


def test_unit_price_at_qty_returns_none_below_moq() -> None:
    q = PartQuote(
        distributor="digikey",
        mpn="X",
        distributor_sku="X-ND",
        manufacturer="",
        description="",
        in_stock_qty=100,
        moq=10,
        lifecycle="active",
        price_breaks=(PriceBreakpoint(min_qty=1, unit_price_usd=1.0),),
        product_url="",
        quoted_at=datetime.now(UTC),
    )
    assert q.unit_price_at_qty(5) is None
    assert q.unit_price_at_qty(10) == 1.0
