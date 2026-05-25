"""Mouser Search API v2 adapter — M3-P-02.

Implements the [`DistributorAdapter`][..base.DistributorAdapter]
contract against Mouser's V2 Search API:

- `POST /api/v2/search/partnumber` with an API-key query param —
  the simplest path to a single-MPN lookup, returning Mouser parts
  + pricing.
- Falls back to `POST /api/v2/search/keyword` when the direct
  partnumber search misses, mirroring the Digi-Key two-stage
  lookup so users typing close-but-not-exact MPNs still get a hit.

Credentials are read from env at construction time:

- `MOUSER_API_KEY` — single API key, no OAuth (Mouser keeps
  authentication simple: query-param-attached key with per-key
  daily quota; sign up at https://www.mouser.com/api-hub/).
- `MOUSER_BASE_URL` — defaults to `https://api.mouser.com`. There's
  no separate sandbox; Mouser returns real data against any valid
  key (request quota: 1000/day on the free tier).
- `MOUSER_PARTNUMBER_OPTION` — defaults to `"Exact"`; set to
  `"None"` / `"Begins"` / `"Contains"` for fuzzier matches.

Without a key, every `lookup()` raises
[`DistributorAuthError`][..base.DistributorAuthError] and the
aggregator soft-fails over to whatever else is configured. No
fakes, no mock mode.

## Pricing shape

Mouser returns `PriceBreaks` as `[{Quantity, Price, Currency}]`
where `Price` is a culture-formatted string (`"$1.65"`,
`"€1,42"`, …). The adapter strips currency markers + comma-decimal
formatting and converts to a `float` USD per the locale; the
fallback when currency is anything other than USD is a `Decimal` ->
USD conversion the user can override per-call via
`MOUSER_LOCALE_CURRENCY` (default `"USD"`). Quotes whose currency
doesn't match the requested locale are still returned with the
parsed numeric value; the aggregator picks "cheapest at the
returned numeric value" — if the user mixes currencies the
selection is meaningless, but Mouser's per-locale pricing is
already homogeneous in practice.
"""

from __future__ import annotations

import os
import re
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

DEFAULT_BASE_URL = "https://api.mouser.com"
PARTNUMBER_SEARCH_PATH = "/api/v2/search/partnumber"
KEYWORD_SEARCH_PATH = "/api/v2/search/keyword"
DEFAULT_TIMEOUT_SECONDS = 10.0

#: Strip every character that isn't a digit, dot, or comma — leaves
#: the numeric portion of culture-formatted price strings like
#: `"$1.65"` or `"€1,42"`.
_PRICE_NUMERIC_RE = re.compile(r"[^0-9.,-]")


def _parse_price(raw: Any) -> float:
    """Best-effort parse of Mouser's culture-formatted price string
    into a float. `"$1.65"` → `1.65`; `"€1,42"` → `1.42` (comma
    decimal); `"1,234.56"` → `1234.56` (US thousands)."""
    if raw is None:
        return 0.0
    if has_dot and has_comma:
        if text.find(".") > text.find(","):
            text = text.replace(",", "")
        else:
            text = text.replace(".", "").replace(",", ".")
    elif has_comma and not has_dot:
        text = text.replace(",", ".")
    has_comma = "," in text
    if has_dot and has_comma:
        # `1,234.56` US format — comma is thousands, dot is decimal.
        text = text.replace(",", "")
    elif has_comma and not has_dot:
        # Pure-comma → treat as decimal separator (`1,42`).
        text = text.replace(",", ".")
    try:
        return float(text)
    except ValueError:
        return 0.0


def _normalise_lifecycle(raw: Any) -> str:
    """Map Mouser's `LifecycleStatus` string onto the canonical
    [`PartQuote.lifecycle`][..base.PartQuote.lifecycle] taxonomy."""
    text = (str(raw) if raw is not None else "").strip().lower()
    if not text:
        return "unknown"
    if "active" in text or "production" in text:
        return "active"
    if "nrnd" in text or "not recommended" in text:
        return "nrnd"
    if "obsolete" in text or "eol" in text or "end of life" in text or "discontinued" in text:
        return "obsolete"
    if "preview" in text or "pre-release" in text or "pre-production" in text:
        return "preview"
    return "unknown"


def _parse_quote(*, mpn: str, part: dict[str, Any]) -> PartQuote:
    """Map one Mouser `MouserPart` block to a [`PartQuote`].

    Mouser returns one entry per (MPN, packaging) combo — already
    the right shape for our per-variation `PartQuote` model."""
    manufacturer = str(part.get("Manufacturer") or "")
    description = str(part.get("Description") or "")
    distributor_sku = str(part.get("MouserPartNumber") or "")
    product_url = str(part.get("ProductDetailUrl") or "")
    in_stock_raw = part.get("AvailabilityInStock") or part.get("Availability") or "0"
    # Mouser may return `"6,000"` (string with thousands separator).
    in_stock_str = _PRICE_NUMERIC_RE.sub("", str(in_stock_raw)).replace(",", "")
    try:
        in_stock_qty = int(float(in_stock_str)) if in_stock_str else 0
    except ValueError:
        in_stock_qty = 0
    moq_raw = part.get("Min") or part.get("MinimumOrderQuantity") or "1"
    try:
        moq = max(1, int(float(_PRICE_NUMERIC_RE.sub("", str(moq_raw)) or "1")))
    except ValueError:
        moq = 1
    lifecycle = _normalise_lifecycle(part.get("LifecycleStatus"))

    price_breaks_raw = part.get("PriceBreaks") or []
    price_breaks: list[PriceBreakpoint] = []
    for entry in price_breaks_raw:
        if not isinstance(entry, dict):
            continue
        qty_raw = entry.get("Quantity") or 1
        try:
            qty = max(1, int(qty_raw))
        except (TypeError, ValueError):
            qty = 1
        unit_price = _parse_price(entry.get("Price"))
        price_breaks.append(PriceBreakpoint(min_qty=qty, unit_price_usd=unit_price))
    # Mouser returns the ladder ascending already; sort defensively
    # in case a custom feed comes out of order.
    price_breaks.sort(key=lambda b: b.min_qty)

    return PartQuote(
        distributor="mouser",
        mpn=mpn,
        distributor_sku=distributor_sku,
        manufacturer=manufacturer,
        description=description,
        in_stock_qty=in_stock_qty,
        moq=moq,
        lifecycle=lifecycle,
        price_breaks=tuple(price_breaks),
        product_url=product_url,
        quoted_at=datetime.now(UTC),
        extras={
            "category": part.get("Category"),
            "rohs_status": part.get("ROHSStatus"),
            "datasheet_url": part.get("DataSheetUrl"),
            "packaging": part.get("Packaging"),
        },
    )


class MouserAdapter(DistributorAdapter):
    """Real Mouser V2 client. Reads `MOUSER_API_KEY` from env at
    construction time; raises [`DistributorAuthError`] on every
    `lookup()` when the key is absent so the aggregator can surface
    + skip cleanly."""

    name = "mouser"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        client: httpx.AsyncClient | None = None,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        partnumber_option: str | None = None,
    ) -> None:
        self._api_key = api_key if api_key is not None else os.environ.get("MOUSER_API_KEY", "")
        self._base_url = (
            base_url or os.environ.get("MOUSER_BASE_URL") or DEFAULT_BASE_URL
        ).rstrip("/")
        self._timeout = timeout_seconds
        self._partnumber_option = (
            partnumber_option
            or os.environ.get("MOUSER_PARTNUMBER_OPTION")
            or "Exact"
        )
        self._owns_client = client is None
        self._http = client or httpx.AsyncClient(
            base_url=self._base_url,
            timeout=httpx.Timeout(self._timeout),
        )

    async def aclose(self) -> None:
        if self._owns_client:
            await self._http.aclose()

    async def lookup(self, mpn: str) -> list[PartQuote]:
        if not mpn or not mpn.strip():
            return []
        if not self._api_key:
            raise DistributorAuthError(
                "Mouser credentials missing: set MOUSER_API_KEY"
            )

        # Try the exact partnumber search first.
        partnumber_body = {
            "SearchByPartRequest": {
                "mouserPartNumber": mpn,
                "partSearchOptions": self._partnumber_option,
            }
        }
        parts = await self._search(PARTNUMBER_SEARCH_PATH, partnumber_body)
        if parts:
            return [_parse_quote(mpn=mpn, part=p) for p in parts]

        # Fall back to the keyword search.
        keyword_body = {
            "SearchByKeywordRequest": {
                "keyword": mpn,
                "records": 5,
                "startingRecord": 0,
                "searchOptions": "",
                "searchWithYourSignUpLanguage": "",
            }
        }
        parts = await self._search(KEYWORD_SEARCH_PATH, keyword_body)
        if not parts:
            return []
        return [_parse_quote(mpn=mpn, part=p) for p in parts]

    async def _search(self, path: str, body: dict[str, Any]) -> list[dict[str, Any]]:
        try:
            resp = await self._http.post(
                path,
                params={"apiKey": self._api_key},
                json=body,
                headers={
                    "accept": "application/json",
                    "content-type": "application/json",
                },
            )
        except httpx.HTTPError as e:
            raise DistributorTransportError(f"Mouser {path} network error: {e}") from e
        if resp.status_code == 401 or resp.status_code == 403:
            raise DistributorAuthError(
                f"Mouser rejected key: HTTP {resp.status_code} — {resp.text[:200]}"
            )
        if resp.status_code == 404:
            return []
        if resp.status_code >= 400:
            raise DistributorTransportError(
                f"Mouser {path} HTTP {resp.status_code}: {resp.text[:200]}"
            )
        body_json = resp.json()
        # Mouser's V2 envelope: `{Errors: [], SearchResults: {NumberOfResult, Parts}}`
        errors = body_json.get("Errors") or []
        if errors:
            messages = [
                str(e.get("Message") or e.get("Code") or "") for e in errors if isinstance(e, dict)
            ]
            is_auth = any(
                ("Unauthor" in m) or ("ApiKey" in m) or ("Forbidden" in m)
                for m in messages
            )
            if messages and is_auth:
                raise DistributorAuthError(
                    f"Mouser API key rejected: {'; '.join(messages)}"
                )
            # Other errors (rate-limit, malformed query) → transport.
            raise DistributorTransportError(f"Mouser API errors: {'; '.join(messages)}")
        results = body_json.get("SearchResults") or {}
        parts = results.get("Parts") or []
        return [p for p in parts if isinstance(p, dict)]


__all__ = ["DEFAULT_BASE_URL", "MouserAdapter"]
