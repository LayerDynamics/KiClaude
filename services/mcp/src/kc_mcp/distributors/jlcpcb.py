"""JLCPCB parts library adapter — M3-P-04.

JLCPCB doesn't publish an official public API for their parts
library, but their assembly-quote endpoint
`https://jlcpcb.com/api/overseas-pcb-order/v1/shoppingCart/smtGood/selectSmtComponentList`
returns the same component database the SMT-assembly order flow
queries. This adapter wraps it.

Identity: JLCPCB requires a logged-in session cookie OR a
preview / public quota that's IP-rate-limited. We treat it as a
**key-optional adapter**:

- `JLCPCB_SESSION_COOKIE` — set to a captured `secretKey` /
  session cookie if you want higher quota + full search. Without
  it the adapter still works but hits the same per-IP quota a
  logged-out user gets.
- `JLCPCB_BASE_URL` — defaults to `https://jlcpcb.com`. Override
  for the staging endpoint if you have one.

Even without a cookie the lookup runs against the real JLCPCB API
and returns real data. There's no "auth missing → raise" path here
the way Digi-Key / Mouser / Octopart have, because JLCPCB explicitly
serves anonymous queries. We DO raise `DistributorAuthError` when
the API itself returns a `code != 200` envelope mentioning auth,
since that's the signal the cookie expired and the next call needs
a fresh one.

## Pricing shape

JLCPCB's `priceList` is `[{startNumber, endNumber, productPrice}]`
with prices in CNY. We convert to USD via the cached daily rate
fetched from the free `https://open.er-api.com/v6/latest/CNY`
endpoint when `JLCPCB_CNY_TO_USD` env is unset; the env override
short-circuits the network call for offline / CI runs.

The "JLC assembly compatible" flag every M3 spec line wants
(`extras.jlc_assembly_basic` / `extras.jlc_assembly_extended`) comes
from the response's `componentLibraryType` ("base" / "expand") plus
`stockCount > 0`.
"""

from __future__ import annotations

import asyncio
import os
import time
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

DEFAULT_BASE_URL = "https://jlcpcb.com"
SEARCH_PATH = "/api/overseas-pcb-order/v1/shoppingCart/smtGood/selectSmtComponentList"
FX_RATE_URL_TEMPLATE = "https://open.er-api.com/v6/latest/{base}"
DEFAULT_TIMEOUT_SECONDS = 10.0

#: JLCPCB pricing is quoted in CNY. We cache the FX rate for the
#: process lifetime — the rate moves <1%/day so re-minting per call
#: would be wasteful.
_FX_CACHE: dict[str, tuple[float, float]] = {}
_FX_LOCK = asyncio.Lock()


def _normalise_lifecycle(_part: dict[str, Any]) -> str:
    """JLCPCB doesn't expose a per-part JEDEC lifecycle. Their UI
    shows "Discontinued" and "Out of Stock" but those collapse to
    `obsolete` / `unknown` from the data we get. Default to
    `active` when stock > 0; otherwise `unknown`."""
    stock = int(_part.get("stockCount") or 0)
    return "active" if stock > 0 else "unknown"


def _parse_quote(*, mpn: str, part: dict[str, Any], cny_to_usd: float) -> PartQuote:
    """Map one JLCPCB `componentInfo` block to a [`PartQuote`]."""
    manufacturer = str(part.get("componentBrandEn") or part.get("componentBrand") or "")
    description = str(part.get("describe") or part.get("description") or "")
    distributor_sku = str(part.get("componentCode") or part.get("lcscPartNo") or "")
    product_url = (
        f"{DEFAULT_BASE_URL}/parts/componentSearch?searchKeyword={mpn}"
        if not part.get("clickUrl")
        else str(part.get("clickUrl"))
    )
    in_stock_qty = int(part.get("stockCount") or 0)
    moq_raw = part.get("minQuantity") or part.get("minPurchase") or 1
    try:
        moq = max(1, int(float(moq_raw)))
    except (TypeError, ValueError):
        moq = 1
    lifecycle = _normalise_lifecycle(part)

    price_list = part.get("componentPriceList") or part.get("priceList") or []
    price_breaks: list[PriceBreakpoint] = []
    for entry in price_list:
        if not isinstance(entry, dict):
            continue
        start_qty_raw = entry.get("startNumber") or entry.get("ladderQuantity") or 1
        try:
            start_qty = max(1, int(start_qty_raw))
        except (TypeError, ValueError):
            start_qty = 1
        cny_price_raw = entry.get("productPrice") or entry.get("price") or 0
        try:
            cny_price = float(cny_price_raw)
        except (TypeError, ValueError):
            cny_price = 0.0
        usd_price = cny_price * cny_to_usd
        price_breaks.append(PriceBreakpoint(min_qty=start_qty, unit_price_usd=usd_price))
    price_breaks.sort(key=lambda b: b.min_qty)

    library_type = str(part.get("componentLibraryType") or "").lower()
    return PartQuote(
        distributor="jlcpcb",
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
            "package": part.get("componentSpecificationEn") or part.get("componentSpecification"),
            "category": part.get("firstSortAccessoryName") or part.get("componentTypeEn"),
            "datasheet_url": part.get("dataManualUrl"),
            "jlc_assembly_basic": library_type == "base",
            "jlc_assembly_extended": library_type == "expand" or library_type == "extended",
            "raw_cny_price_at_qty_1": (
                next(
                    (
                        float(p.get("productPrice") or 0)
                        for p in price_list
                        if isinstance(p, dict) and int(p.get("startNumber") or 0) <= 1
                    ),
                    None,
                )
                if price_list
                else None
            ),
        },
    )


class JlcpcbAdapter(DistributorAdapter):
    """Real JLCPCB parts client. Optional session cookie via
    `JLCPCB_SESSION_COOKIE` for higher quota. Pricing is converted
    CNY → USD via the cached daily FX rate (overridable via
    `JLCPCB_CNY_TO_USD` for deterministic tests)."""

    name = "jlcpcb"

    def __init__(
        self,
        *,
        session_cookie: str | None = None,
        base_url: str | None = None,
        client: httpx.AsyncClient | None = None,
        cny_to_usd: float | None = None,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._session_cookie = (
            session_cookie
            if session_cookie is not None
            else os.environ.get("JLCPCB_SESSION_COOKIE", "")
        )
        self._base_url = (
            base_url or os.environ.get("JLCPCB_BASE_URL") or DEFAULT_BASE_URL
        ).rstrip("/")
        self._timeout = timeout_seconds
        env_rate = os.environ.get("JLCPCB_CNY_TO_USD")
        try:
            self._cny_to_usd_override: float | None = (
                cny_to_usd if cny_to_usd is not None
                else (float(env_rate) if env_rate else None)
            )
        except ValueError:
            self._cny_to_usd_override = None
        self._owns_client = client is None
        self._http = client or httpx.AsyncClient(
            base_url=self._base_url,
            timeout=httpx.Timeout(self._timeout),
        )

    async def aclose(self) -> None:
        if self._owns_client:
            await self._http.aclose()

    # ----------------------------------------------------------------
    # CNY → USD FX rate
    # ----------------------------------------------------------------

    async def _get_cny_to_usd(self) -> float:
        if self._cny_to_usd_override is not None:
            return self._cny_to_usd_override
        # 24-hour cached rate at module scope.
        async with _FX_LOCK:
            cached = _FX_CACHE.get("CNY")
            if cached is not None:
                rate, cached_at = cached
                if (time.time() - cached_at) < 24 * 3600:
                    return rate
            try:
                resp = await self._http.get(
                    FX_RATE_URL_TEMPLATE.format(base="CNY"),
                    headers={"accept": "application/json"},
                )
            except httpx.HTTPError as e:
                raise DistributorTransportError(
                    f"JLCPCB FX rate fetch failed: {e}"
                ) from e
            if resp.status_code >= 400:
                raise DistributorTransportError(
                    f"JLCPCB FX rate HTTP {resp.status_code}: {resp.text[:200]}"
                )
            payload = resp.json()
            rates = payload.get("rates") or {}
            rate_raw = rates.get("USD")
            try:
                rate = float(rate_raw) if rate_raw is not None else 0.0
            except (TypeError, ValueError):
                rate = 0.0
            if rate <= 0:
                raise DistributorTransportError(
                    f"JLCPCB FX rate payload missing USD: {payload!r}"
                )
            _FX_CACHE["CNY"] = (rate, time.time())
            return rate

    # ----------------------------------------------------------------
    # Public lookup
    # ----------------------------------------------------------------

    async def lookup(self, mpn: str) -> list[PartQuote]:
        if not mpn or not mpn.strip():
            return []
        cny_to_usd = await self._get_cny_to_usd()
        headers = {
            "accept": "application/json",
            "content-type": "application/json",
        }
        if self._session_cookie:
            headers["cookie"] = self._session_cookie
        # JLCPCB's smt-component-list expects a multipart-style query
        # in the body. We use keyword search by MPN.
        body = {
            "keyword": mpn,
            "currentPage": 1,
            "pageSize": 5,
            "componentTypeEn": "",
            "componentBrandEn": "",
            "stockFlag": True,
            "sortBy": "",
        }
        try:
            resp = await self._http.post(SEARCH_PATH, json=body, headers=headers)
        except httpx.HTTPError as e:
            raise DistributorTransportError(f"JLCPCB search network error: {e}") from e
        if resp.status_code in (401, 403):
            raise DistributorAuthError(
                f"JLCPCB rejected request: HTTP {resp.status_code} — set "
                f"JLCPCB_SESSION_COOKIE to a fresh authenticated cookie"
            )
        if resp.status_code == 404:
            return []
        if resp.status_code >= 400:
            raise DistributorTransportError(
                f"JLCPCB search HTTP {resp.status_code}: {resp.text[:200]}"
            )
        payload = resp.json()
        # JLCPCB envelope: `{code: 200|<err>, message, data: {componentInfoList: [...]}}`
        code = payload.get("code")
        if code is not None and int(code) != 200:
            message = str(payload.get("message") or "")
            if any(
                tok in message.lower()
                for tok in ("login", "auth", "session", "cookie", "unauthor")
            ):
                raise DistributorAuthError(
                    f"JLCPCB session rejected (code={code}): {message}"
                )
            raise DistributorTransportError(
                f"JLCPCB API error (code={code}): {message}"
            )
        data = payload.get("data") or {}
        components = (
            data.get("componentInfoList")
            or data.get("componentPageInfo", {}).get("list")
            or []
        )
        if not isinstance(components, list):
            return []
        return [
            _parse_quote(mpn=mpn, part=c, cny_to_usd=cny_to_usd)
            for c in components
            if isinstance(c, dict)
        ]


def reset_fx_cache_for_tests() -> None:
    """Wipe the module-level FX cache. Tests call this between
    runs so a deterministic cny_to_usd override isn't polluted by a
    prior fixture's real-fetch."""
    _FX_CACHE.clear()


__all__ = ["DEFAULT_BASE_URL", "JlcpcbAdapter", "reset_fx_cache_for_tests"]
