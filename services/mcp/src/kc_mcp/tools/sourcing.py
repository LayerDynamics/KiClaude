"""`kc_part_search` + `kc_bom_price` — Claude-facing sourcing tools
(M3-P-06).

Both tools drive the real distributor aggregator from
`kc_mcp.distributors`. With Digi-Key credentials in env they hit
live distributor APIs; without them they raise
`DistributorAuthError` for the requested distributor and the
aggregator soft-fails to whatever else is configured.

## Tool surface

- `kc_part_search(mpn, qty?)` — look up a single MPN across every
  registered distributor; return every variation + the cheapest one
  at `qty`.
- `kc_bom_price(parts, force_refresh?)` — fan out across a list of
  `{mpn, qty}` entries; return cart-split totals + the per-line
  cheapest distributor.

Both return structured envelopes the BOM panel + the bom-sourcer
subagent consume. The cheapest-mix selection is the aggregator's
responsibility — tools just shape the response.

## Test seam

`build_default_aggregator()` honours `DIGIKEY_CLIENT_ID/SECRET` env.
For tests that don't want to monkey-patch env, the module exposes
`set_aggregator_factory()` so the test fixture can inject a fake
aggregator that returns canned `PartPricing` rows.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict
from typing import Any

from claude_agent_sdk import tool

from kc_mcp._envelope import envelope, error_envelope
from kc_mcp.distributors import (
    BomPricing,
    PartPricing,
    PartQuote,
    PriceAggregator,
    build_default_aggregator,
)

#: Type of the factory the tool calls to obtain an aggregator. Each
#: tool invocation builds a fresh aggregator and closes it after the
#: call so credentials picked up from env are always current and the
#: shared SQLite cache is flushed via __exit__.
AggregatorFactory = Callable[[], PriceAggregator]


_factory: AggregatorFactory = build_default_aggregator


def set_aggregator_factory(factory: AggregatorFactory | None) -> None:
    """Swap the factory the tools use to build an aggregator. Pass
    `None` to reset to the default (env-driven build)."""
    global _factory
    _factory = factory or build_default_aggregator


def _quote_to_payload(quote: PartQuote) -> dict[str, Any]:
    raw = asdict(quote)
    raw["quoted_at"] = quote.quoted_at.isoformat()
    raw["price_breaks"] = [
        {"min_qty": b.min_qty, "unit_price_usd": b.unit_price_usd}
        for b in quote.price_breaks
    ]
    return raw


def _pricing_to_payload(part: PartPricing) -> dict[str, Any]:
    return {
        "mpn": part.mpn,
        "requested_qty": part.requested_qty,
        "quotes": [_quote_to_payload(q) for q in part.quotes],
        "cheapest": _quote_to_payload(part.cheapest) if part.cheapest else None,
        "cheapest_unit_price_usd": part.cheapest_unit_price_usd,
        "line_total_usd": part.line_total_usd,
        "errors": dict(part.errors),
    }


def _bom_to_payload(bom: BomPricing) -> dict[str, Any]:
    return {
        "parts": [_pricing_to_payload(p) for p in bom.parts],
        "distributor_totals_usd": dict(bom.distributor_totals_usd),
        "grand_total_usd": bom.grand_total_usd,
        "missing_mpns": list(bom.missing_mpns),
        "errors": {k: list(v) for k, v in bom.errors.items()},
    }


@tool(
    "kc_part_search",
    "Look up an MPN across every configured distributor (Digi-Key, "
    "Mouser, Octopart, JLCPCB) and return every available quote + the "
    "cheapest one at the requested quantity. Honours the BOM cache "
    "(6h TTL by default). M3-P-06.",
    {
        "mpn": str,
        "qty": int,
        "force_refresh": bool,
    },
)
async def kc_part_search(args: dict[str, Any]) -> dict[str, Any]:
    mpn = (args.get("mpn") or "").strip()
    if not mpn:
        return error_envelope("`mpn` is required")
    qty_raw = args.get("qty")
    qty = int(qty_raw) if qty_raw is not None else 1
    if qty < 1:
        return error_envelope("`qty` must be >= 1")
    force_refresh = bool(args.get("force_refresh"))
    aggregator = _factory()
    try:
        part = await aggregator.price(mpn, qty=qty, force_refresh=force_refresh)
    finally:
        await aggregator.aclose()
    payload = _pricing_to_payload(part)
    payload["ok"] = True
    return envelope(payload)


@tool(
    "kc_bom_price",
    "Price a full BOM by fanning out across every configured "
    "distributor in parallel. Accepts a list of `{mpn, qty}` entries; "
    "returns the cheapest distributor per line, the cart-split "
    "totals so the user can collapse onto the fewest carts, and the "
    "list of MPNs no distributor returned a live quote for. M3-P-06.",
    {
        "parts": list,
        "force_refresh": bool,
    },
)
async def kc_bom_price(args: dict[str, Any]) -> dict[str, Any]:
    raw_parts = args.get("parts")
    if not isinstance(raw_parts, list) or not raw_parts:
        return error_envelope("`parts` is required and must be a non-empty list")
    normalised: list[tuple[str, int]] = []
    for idx, entry in enumerate(raw_parts):
        if isinstance(entry, str):
            normalised.append((entry.strip(), 1))
            continue
        if not isinstance(entry, dict):
            return error_envelope(
                f"`parts[{idx}]` must be a string MPN or "
                f"`{{mpn, qty}}` object, got {type(entry).__name__}"
            )
        mpn = (entry.get("mpn") or "").strip()
        if not mpn:
            return error_envelope(f"`parts[{idx}].mpn` is required")
        qty_raw = entry.get("qty")
        try:
            qty = int(qty_raw) if qty_raw is not None else 1
        except (TypeError, ValueError):
            return error_envelope(f"`parts[{idx}].qty` must be a positive integer")
        if qty < 1:
            return error_envelope(f"`parts[{idx}].qty` must be >= 1")
        normalised.append((mpn, qty))
    force_refresh = bool(args.get("force_refresh"))
    aggregator = _factory()
    try:
        bom = await aggregator.price_bom(normalised, force_refresh=force_refresh)
    finally:
        await aggregator.aclose()
    payload = _bom_to_payload(bom)
    payload["ok"] = True
    return envelope(payload)


__all__ = ["kc_bom_price", "kc_part_search", "set_aggregator_factory"]
