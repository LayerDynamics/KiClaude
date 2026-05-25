"""M3-T-08 — `/project/{id}/bom/price` endpoint tests.

Exercises the kiserver BOM-pricing surface against a fake aggregator
(injected via the `kc_mcp.distributors.aggregator.build_default_aggregator`
factory the endpoint calls).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient
from kc_mcp.distributors import (
    BomPricing,
    PartPricing,
    PartQuote,
    PriceAggregator,
    PriceBreakpoint,
    PriceCache,
)
from kiserver.main import _bom_lines_from_project, app
from kiserver.project import REGISTRY


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    REGISTRY.clear()
    # Wire a deterministic fake aggregator via the same import path
    # the endpoint uses: it imports inside the request handler so we
    # have to patch the module-level symbol on `kc_mcp.distributors`.
    import kc_mcp.distributors as dist_mod

    monkeypatch.setattr(
        dist_mod, "build_default_aggregator", _build_fake_aggregator_factory()
    )
    return TestClient(app)


# ---------------------------------------------------------------------
# Fake aggregator — real PriceAggregator subclass with deterministic
# canned `price_bom` so endpoint tests don't touch live distributor
# APIs.
# ---------------------------------------------------------------------


def _quote(mpn: str, unit_price: float = 1.0) -> PartQuote:
    return PartQuote(
        distributor="digikey",
        mpn=mpn,
        distributor_sku=f"DIG-{mpn}",
        manufacturer="Test Mfg",
        description=f"{mpn} test",
        in_stock_qty=1_000,
        moq=1,
        lifecycle="active",
        price_breaks=(PriceBreakpoint(min_qty=1, unit_price_usd=unit_price),),
        product_url=f"https://digikey.com/{mpn}",
        quoted_at=datetime.now(UTC),
    )


class _CannedAggregator(PriceAggregator):
    """Records the call args; returns canned line totals so we can
    assert the endpoint shaped them correctly. Keeps the cache real
    so the close-on-exit path runs."""

    def __init__(self) -> None:
        super().__init__(adapters=[], cache=PriceCache(path=":memory:"))
        self.calls: list[tuple[list[tuple[str, int]], bool]] = []
        self.closed = False

    async def price_bom(self, bom: Any, *, force_refresh: bool = False) -> BomPricing:
        items: list[tuple[str, int]] = []
        for entry in bom:
            if isinstance(entry, tuple):
                items.append((str(entry[0]), int(entry[1])))
            else:
                items.append((str(entry), 1))
        self.calls.append((items, force_refresh))
        parts: list[PartPricing] = []
        totals: dict[str, float] = {}
        grand = 0.0
        for mpn, qty in items:
            q = _quote(mpn, unit_price=0.50)
            line_total = 0.50 * qty
            parts.append(
                PartPricing(
                    mpn=mpn,
                    requested_qty=qty,
                    quotes=[q],
                    errors={},
                    cheapest=q,
                    cheapest_unit_price_usd=0.50,
                )
            )
            totals["digikey"] = totals.get("digikey", 0.0) + line_total
            grand += line_total
        return BomPricing(
            parts=parts,
            distributor_totals_usd=totals,
            grand_total_usd=grand,
            missing_mpns=[],
            errors={},
        )

    async def aclose(self) -> None:
        self.closed = True


_LAST_AGGREGATOR: _CannedAggregator | None = None


def _build_fake_aggregator_factory():
    def factory() -> _CannedAggregator:
        global _LAST_AGGREGATOR
        _LAST_AGGREGATOR = _CannedAggregator()
        return _LAST_AGGREGATOR

    return factory


# ---------------------------------------------------------------------
# Project registration helper — kiserver's REGISTRY keys by project_id.
# ---------------------------------------------------------------------


def _register_project(project: dict[str, Any]) -> str:
    """Stash a synthetic project dict in the REGISTRY so the endpoint
    can resolve it without opening a real on-disk project."""
    from pathlib import Path

    opened = REGISTRY.insert(project, Path("/tmp/nonexistent.kicad_pro"))
    return opened.project_id


def _project_with_mpns(*mpn_pairs: tuple[str, int]) -> dict[str, Any]:
    """Build a project dict whose pcb.footprints reproduces
    `[(mpn, count)]` — each mpn appears `count` times."""
    footprints: list[dict[str, Any]] = []
    counter = 0
    for mpn, count in mpn_pairs:
        for _ in range(count):
            counter += 1
            footprints.append(
                {
                    "refdes": f"U{counter}",
                    "mpn": mpn,
                    "lib_id": "Package_DIP:DIP-8_W7.62mm",
                }
            )
    return {
        "name": "test_bom",
        "pcb": {"footprints": footprints, "nets": [], "layers": []},
    }


# ---------------------------------------------------------------------
# _bom_lines_from_project — pure helper
# ---------------------------------------------------------------------


def test_bom_lines_groups_by_mpn_and_counts_refdes() -> None:
    project = _project_with_mpns(
        ("STM32F103C8T6", 1),
        ("GRM188R71H104KA93D", 12),
        ("ECS-160-CDX-1284-CN-TR", 1),
    )
    lines = _bom_lines_from_project(project)
    # Sorted by mpn alphabetically.
    assert lines == [
        ("ECS-160-CDX-1284-CN-TR", 1),
        ("GRM188R71H104KA93D", 12),
        ("STM32F103C8T6", 1),
    ]


def test_bom_lines_skips_footprints_without_mpn() -> None:
    project = {
        "pcb": {
            "footprints": [
                {"refdes": "U1", "mpn": "STM32F103C8T6"},
                {"refdes": "U2"},  # no mpn key
                {"refdes": "U3", "mpn": ""},  # empty
                {"refdes": "U4", "mpn": "   "},  # whitespace
                {"refdes": "U5", "mpn": None},  # explicit None
            ]
        }
    }
    lines = _bom_lines_from_project(project)
    assert lines == [("STM32F103C8T6", 1)]


def test_bom_lines_empty_project_returns_empty_list() -> None:
    assert _bom_lines_from_project({"pcb": {"footprints": []}}) == []
    assert _bom_lines_from_project({"pcb": {}}) == []
    assert _bom_lines_from_project({}) == []


# ---------------------------------------------------------------------
# /project/{id}/bom/price — full HTTP round-trip
# ---------------------------------------------------------------------


def test_endpoint_returns_grouped_lines_and_pricing(client: TestClient) -> None:
    project_id = _register_project(
        _project_with_mpns(("STM32F103C8T6", 1), ("GRM188R71H104KA93D", 3))
    )
    resp = client.get(f"/project/{project_id}/bom/price")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["project_id"] == project_id
    # bom_lines sorted by mpn.
    assert body["bom_lines"] == [
        {"mpn": "GRM188R71H104KA93D", "qty": 3, "refdes_count": 3},
        {"mpn": "STM32F103C8T6", "qty": 1, "refdes_count": 1},
    ]
    # Pricing: both lines @ $0.50 unit (canned).
    pricing = body["pricing"]
    assert pricing["grand_total_usd"] == pytest.approx(0.50 * 3 + 0.50 * 1)
    assert pricing["distributor_totals_usd"] == {"digikey": pytest.approx(2.0)}
    parts = {p["mpn"]: p for p in pricing["parts"]}
    assert parts["STM32F103C8T6"]["cheapest"]["distributor"] == "digikey"
    assert parts["STM32F103C8T6"]["cheapest"]["unit_price_usd"] == 0.50
    assert parts["STM32F103C8T6"]["line_total_usd"] == pytest.approx(0.50)


def test_endpoint_qty_multiplier_scales_every_line(client: TestClient) -> None:
    project_id = _register_project(_project_with_mpns(("MCU", 1), ("CAP", 4)))
    resp = client.get(f"/project/{project_id}/bom/price?qty_multiplier=100")
    body = resp.json()
    # Original counts: MCU=1, CAP=4. Scaled by 100: MCU=100, CAP=400.
    assert body["bom_lines"] == [
        {"mpn": "CAP", "qty": 400, "refdes_count": 4},
        {"mpn": "MCU", "qty": 100, "refdes_count": 1},
    ]
    pricing = body["pricing"]
    # Grand total: 100 * 0.50 + 400 * 0.50 = 250.
    assert pricing["grand_total_usd"] == pytest.approx(250.0)


def test_endpoint_force_refresh_flag_propagates(client: TestClient) -> None:
    project_id = _register_project(_project_with_mpns(("X", 1)))
    client.get(f"/project/{project_id}/bom/price?force_refresh=true")
    assert _LAST_AGGREGATOR is not None
    assert _LAST_AGGREGATOR.calls[0][1] is True
    # Without the flag → False.
    client.get(f"/project/{project_id}/bom/price")
    assert _LAST_AGGREGATOR is not None
    assert _LAST_AGGREGATOR.calls[-1][1] is False


def test_endpoint_unknown_project_id_returns_404(client: TestClient) -> None:
    resp = client.get("/project/no-such-id/bom/price")
    assert resp.status_code == 404
    assert "no-such-id" in resp.json()["detail"]


def test_endpoint_rejects_zero_qty_multiplier(client: TestClient) -> None:
    project_id = _register_project(_project_with_mpns(("X", 1)))
    resp = client.get(f"/project/{project_id}/bom/price?qty_multiplier=0")
    assert resp.status_code == 400


def test_endpoint_closes_aggregator_even_on_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the aggregator raises mid-fan-out the endpoint must still
    aclose() it (no socket / cache leak)."""
    closed = {"value": False}

    class _BoomAggregator(PriceAggregator):
        def __init__(self) -> None:
            super().__init__(adapters=[], cache=PriceCache(path=":memory:"))

        async def price_bom(self, *args: Any, **kwargs: Any) -> BomPricing:
            raise RuntimeError("boom")

        async def aclose(self) -> None:
            closed["value"] = True

    import kc_mcp.distributors as dist_mod

    monkeypatch.setattr(dist_mod, "build_default_aggregator", lambda: _BoomAggregator())
    REGISTRY.clear()
    project_id = _register_project(_project_with_mpns(("X", 1)))
    # TestClient defaults to `raise_server_exceptions=True` which
    # re-raises through the client; flip it so the response shape
    # mirrors what a real client would see.
    boom_client = TestClient(app, raise_server_exceptions=False)
    resp = boom_client.get(f"/project/{project_id}/bom/price")
    assert resp.status_code == 500
    assert closed["value"] is True
