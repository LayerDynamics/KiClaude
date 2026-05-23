"""HTTP clients to the kiserver + kiconnector services.

MCP tools call these instead of using `httpx` directly so tests can
swap a fake transport via [`set_kiserver_url`][set_kiserver_url] /
[`set_kiconnector_url`][set_kiconnector_url] / [`set_client`][set_client]
without monkey-patching every tool module.

Environment overrides:

- `KISERVER_URL` — defaults to `http://127.0.0.1:8083`.
- `KICONNECTOR_URL` — defaults to `http://127.0.0.1:8084`.
"""

from __future__ import annotations

import json as _json
import os
from typing import Any

import httpx

DEFAULT_KISERVER_URL = "http://127.0.0.1:8083"
DEFAULT_KICONNECTOR_URL = "http://127.0.0.1:8084"

# Module-level mutable state. Tools call `get_client()` which returns
# either the test-injected client or a fresh one built from the URLs.
_kiserver_url: str = os.environ.get("KISERVER_URL", DEFAULT_KISERVER_URL)
_kiconnector_url: str = os.environ.get("KICONNECTOR_URL", DEFAULT_KICONNECTOR_URL)
_injected_client: httpx.AsyncClient | None = None


def set_kiserver_url(url: str) -> None:
    """Replace the kiserver base URL — test/integration helper."""
    global _kiserver_url
    _kiserver_url = url


def set_kiconnector_url(url: str) -> None:
    """Replace the kiconnector base URL — test/integration helper."""
    global _kiconnector_url
    _kiconnector_url = url


def set_client(client: httpx.AsyncClient | None) -> None:
    """Override the HTTP transport used by every tool with a
    pre-built `httpx.AsyncClient` (typically wrapping an
    `httpx.MockTransport`). Pass `None` to restore the default."""
    global _injected_client
    _injected_client = client


def kiserver_url() -> str:
    return _kiserver_url


def kiconnector_url() -> str:
    return _kiconnector_url


def get_client() -> httpx.AsyncClient:
    """Return the active HTTP client. Callers MUST NOT close the
    returned client themselves — the helper functions handle the
    lifecycle so the injected client stays alive across calls."""
    if _injected_client is not None:
        return _injected_client
    return httpx.AsyncClient(timeout=60.0)


async def kiserver_get(path: str) -> dict[str, Any]:
    """`GET ${KISERVER_URL}{path}` → parsed JSON body. Raises
    `httpx.HTTPStatusError` on 4xx/5xx after one read of the body."""
    client = get_client()
    own = _injected_client is None
    try:
        resp = await client.get(_kiserver_url + path)
        resp.raise_for_status()
        return _decode_json(resp)
    finally:
        if own:
            await client.aclose()


async def kiserver_post(path: str, json: Any) -> dict[str, Any]:
    client = get_client()
    own = _injected_client is None
    try:
        resp = await client.post(_kiserver_url + path, json=json)
        resp.raise_for_status()
        return _decode_json(resp)
    finally:
        if own:
            await client.aclose()


async def kiconnector_post(path: str, json: Any) -> dict[str, Any]:
    client = get_client()
    own = _injected_client is None
    try:
        resp = await client.post(_kiconnector_url + path, json=json)
        resp.raise_for_status()
        return _decode_json(resp)
    finally:
        if own:
            await client.aclose()


def _decode_json(resp: httpx.Response) -> dict[str, Any]:
    """Tolerant JSON decode — defaults to `{}` rather than blowing up
    when an empty body sneaks through (some upstreams emit 204 with
    no payload)."""
    text = resp.text
    if not text:
        return {}
    return _json.loads(text)  # type: ignore[no-any-return]


__all__ = [
    "DEFAULT_KICONNECTOR_URL",
    "DEFAULT_KISERVER_URL",
    "get_client",
    "kiconnector_post",
    "kiconnector_url",
    "kiserver_get",
    "kiserver_post",
    "kiserver_url",
    "set_client",
    "set_kiconnector_url",
    "set_kiserver_url",
]
