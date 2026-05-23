"""Subprocess broker — runs external tools (`kicad-cli`, `freerouting`,
`kikit`) with a per-call timeout and never propagates an exception out
to the FastAPI handler.

[`probe_version`][probe_version] is the M0-P-05 surface; richer
subprocess wrapping (DSN export, gerber generation) lands in M2.
"""

from __future__ import annotations

import asyncio
import shutil
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class VersionProbe:
    """Result of probing a tool's `--version`."""

    available: bool
    version: str  # Either the trimmed output or "not installed"


async def probe_version(
    binary: str,
    args: tuple[str, ...] = ("--version",),
    *,
    timeout_s: float = 5.0,
) -> VersionProbe:
    """Run `binary <args>` with a `timeout_s` cap. Returns either the
    trimmed stdout/stderr text, or `VersionProbe(False, "not installed")`
    if the binary isn't on PATH / the call times out / a permission
    error fires.
    """
    resolved = shutil.which(binary)
    if resolved is None:
        return VersionProbe(False, "not installed")
    try:
        proc = await asyncio.create_subprocess_exec(
            resolved,
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
    except (TimeoutError, FileNotFoundError, PermissionError, OSError):
        return VersionProbe(False, "not installed")
    raw = (stdout or b"").decode("utf-8", errors="replace").strip()
    if not raw:
        # Some tools (notably the freerouting CLI) print their banner
        # to stderr, not stdout — fall back to that.
        raw = (stderr or b"").decode("utf-8", errors="replace").strip()
    if not raw:
        return VersionProbe(False, "unknown")
    return VersionProbe(True, raw.splitlines()[0])


async def probe_freerouting_jar(
    jar_path: str | None,
    *,
    timeout_s: float = 5.0,
) -> VersionProbe:
    """Variant for Freerouting, which ships as a `.jar` and is invoked
    via `java -jar`. Returns `not installed` when either `java` or the
    jar path is missing.
    """
    java = shutil.which("java")
    if java is None or jar_path is None or not jar_path:
        return VersionProbe(False, "not installed")
    return await probe_version(
        "java",
        ("-jar", jar_path, "--version"),
        timeout_s=timeout_s,
    )


__all__ = ["VersionProbe", "probe_freerouting_jar", "probe_version"]
