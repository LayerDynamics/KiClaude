"""Auth-availability probing for the kiclaude agent service.

Originally the agent service hard-required `ANTHROPIC_API_KEY` in the
environment. That broke the common case where a user has logged into
Claude Code via `claude login` (subscription OAuth) and the
credential lives in the macOS keychain, not the env. The underlying
`claude_agent_sdk` spawns the `claude` CLI as a subprocess, which
authenticates via any of:

1. Explicit `ANTHROPIC_API_KEY` env (or `ANTHROPIC_AUTH_TOKEN`)
2. `CLAUDE_CODE_OAUTH_TOKEN` env
3. The OS keychain credential `"Claude Code-credentials"` (after
   `claude login` from a regular terminal)
4. Bedrock or Vertex proxy env (`CLAUDE_CODE_USE_BEDROCK=1` or
   `CLAUDE_CODE_USE_VERTEX=1` with the corresponding auth set up)

[`is_available`][is_available] returns `(ok, source)` so the agent
service can both gate `/echo` and tell the caller WHICH path
authenticated. [`describe_unavailable`][describe_unavailable] returns
a human-readable message listing every path that was checked, so the
503 body is actionable instead of pointing at one env var.

The keychain probe runs `claude --print` with a no-op prompt the
first time it's called. The result is cached so repeated calls don't
fork a child process per request. The cache is invalidated on a
SIGHUP-equivalent — see [`reset_cache`][reset_cache] — which the
test suite uses.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from typing import Literal

AuthSource = Literal[
    "env_api_key",
    "env_oauth_token",
    "env_auth_token",
    "bedrock",
    "vertex",
    "claude_cli_keychain",
]


@dataclass(frozen=True)
class AuthResult:
    """Outcome of an auth-availability probe."""

    ok: bool
    """True iff at least one auth path is available."""
    source: AuthSource | None
    """Which path was selected. `None` iff `ok` is False."""
    detail: str
    """Human-readable note about the source — env-var name, keychain hit, etc."""


# Module-level cache so the (expensive) `claude --print` keychain
# probe runs at most once per process. Reset via `reset_cache()`.
_cache: AuthResult | None = None


def reset_cache() -> None:
    """Drop the cached probe result. Used by tests that mutate env
    between assertions."""
    global _cache
    _cache = None


def is_available() -> AuthResult:
    """Return the first auth path that's wired, in priority order.

    Probes are cheap-first: env-var lookups never spawn a subprocess,
    so the costly `claude --print` keychain probe runs only after the
    env-based options have been ruled out.
    """
    global _cache
    if _cache is not None:
        return _cache

    # 1. Explicit Anthropic API key (the historical default).
    if os.environ.get("ANTHROPIC_API_KEY"):
        _cache = AuthResult(True, "env_api_key", "ANTHROPIC_API_KEY env var")
        return _cache

    # 2. Anthropic auth token (newer-style API key surface).
    if os.environ.get("ANTHROPIC_AUTH_TOKEN"):
        _cache = AuthResult(True, "env_auth_token", "ANTHROPIC_AUTH_TOKEN env var")
        return _cache

    # 3. OAuth token captured by `claude login`. The CLI exposes this
    #    as `CLAUDE_CODE_OAUTH_TOKEN` when invoked headless or in CI.
    if os.environ.get("CLAUDE_CODE_OAUTH_TOKEN"):
        _cache = AuthResult(True, "env_oauth_token", "CLAUDE_CODE_OAUTH_TOKEN env var")
        return _cache

    # 4. Bedrock / Vertex proxy mode. The user owns wiring those creds
    #    in their respective AWS/GCP env; we only verify the opt-in
    #    flag because the SDK will fail loudly if the underlying
    #    profile is missing.
    if os.environ.get("CLAUDE_CODE_USE_BEDROCK") == "1":
        _cache = AuthResult(True, "bedrock", "CLAUDE_CODE_USE_BEDROCK=1")
        return _cache
    if os.environ.get("CLAUDE_CODE_USE_VERTEX") == "1":
        _cache = AuthResult(True, "vertex", "CLAUDE_CODE_USE_VERTEX=1")
        return _cache

    # 5. Keychain-backed subscription auth. The only sure way to know
    #    is to invoke the CLI and watch the exit code — `--print` with
    #    a trivial prompt round-trips through the auth machinery and
    #    returns non-zero ("Not logged in · Please run /login") when
    #    no credential is reachable. We pass a one-word prompt and
    #    cap with a short timeout so the probe never hangs a request.
    cli = shutil.which("claude")
    if cli is None:
        _cache = AuthResult(False, None, "no `claude` CLI on PATH and no env credential set")
        return _cache

    try:
        # `--max-turns 1` and a one-token prompt keep the probe cheap
        # (~100ms with warm cache, ~1s cold). `cli` is the absolute
        # path returned by `shutil.which` (trusted PATH lookup); all
        # other args are literals — no shell injection surface.
        proc = subprocess.run(  # noqa: S603 — args are literals + which()-resolved path
            [cli, "--print", "--max-turns", "1", "ok"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        _cache = AuthResult(
            False,
            None,
            f"`claude --print` probe failed: {exc}",
        )
        return _cache

    if proc.returncode == 0:
        _cache = AuthResult(
            True,
            "claude_cli_keychain",
            "claude CLI authenticated via keychain / saved credential",
        )
    else:
        # Surface the CLI's first line — typically the user-facing
        # "Not logged in" hint or an auth-error description.
        msg = (proc.stderr or proc.stdout or "").strip().splitlines()
        first = msg[0] if msg else "no output"
        _cache = AuthResult(
            False,
            None,
            f"`claude --print` returned exit {proc.returncode}: {first}",
        )
    return _cache


def describe_unavailable() -> str:
    """Build the 503 detail body — names every accepted path so the
    caller can pick whichever fits their environment."""
    result = is_available()
    return (
        f"{result.detail}. Accepted auth paths: "
        "ANTHROPIC_API_KEY env, ANTHROPIC_AUTH_TOKEN env, "
        "CLAUDE_CODE_OAUTH_TOKEN env, CLAUDE_CODE_USE_BEDROCK=1, "
        "CLAUDE_CODE_USE_VERTEX=1, or a keychain credential from "
        "`claude login`."
    )


__all__ = [
    "AuthResult",
    "AuthSource",
    "describe_unavailable",
    "is_available",
    "reset_cache",
]
