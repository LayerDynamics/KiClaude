"""ERC (Electrical Rules Check) wrapper around `kicad-cli sch erc`
(M1-P-03).

`kicad-cli sch erc --format json` produces a structured violations
report; this module normalizes it into a list of
[`ErcIssue`][ErcIssue] objects shaped for the M1-T-06 ERC results
panel.

The wrapper enforces a 30 s timeout (per the plan) and returns a
typed [`ErcReport`][ErcReport] that distinguishes "tool failure"
(kicad-cli not installed / crashed) from "ERC ran and found N
issues".
"""

from __future__ import annotations

import asyncio
import json
import shutil
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

# 30s budget mirrors the M1-P-03 plan acceptance.
DEFAULT_TIMEOUT_S = 30.0


@dataclass(frozen=True, slots=True)
class ErcIssue:
    """One ERC violation."""

    severity: str  # "error" | "warning" | "exclusion" | "ignore"
    sheet: str  # sheet uuid (when available) or sheet path
    position_mm: tuple[float, float]  # (x, y); (0, 0) when unknown
    type: str  # KiCad's `code` field (e.g. "no_connect", "label_dangling")
    description: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "severity": self.severity,
            "sheet": self.sheet,
            "position_mm": list(self.position_mm),
            "type": self.type,
            "description": self.description,
        }


@dataclass(slots=True)
class ErcReport:
    """The result of a kicad-cli ERC run.

    `ok` is False whenever the underlying subprocess failed (missing
    binary, timed out, non-zero exit with no JSON). In those cases
    `error` carries the diagnostic and `issues` is empty.
    """

    ok: bool
    issues: list[ErcIssue] = field(default_factory=list)
    error: str | None = None
    duration_ms: int = 0
    exit_code: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "issues": [i.to_dict() for i in self.issues],
            "error": self.error,
            "duration_ms": self.duration_ms,
            "exit_code": self.exit_code,
        }


async def run_erc(
    project_path: str | Path,
    *,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    kicad_cli_binary: str = "kicad-cli",
) -> ErcReport:
    """Run `kicad-cli sch erc` on `project_path` (a directory or a
    `.kicad_sch` file) and return a parsed [`ErcReport`].

    Surfaces every failure mode as an `ErcReport.ok=False` with the
    diagnostic in `error` — never raises.
    """
    started = asyncio.get_event_loop().time()

    target = _resolve_target(project_path)
    if not target.exists():
        return _err(f"target not found: {project_path}", duration_from(started))

    binary = shutil.which(kicad_cli_binary)
    if binary is None:
        return _err(f"{kicad_cli_binary} not on PATH", duration_from(started))

    args = ["sch", "erc", "--format", "json", str(target)]
    try:
        proc = await asyncio.create_subprocess_exec(
            binary,
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=timeout_s
        )
    except TimeoutError:
        return _err(f"kicad-cli timed out after {timeout_s}s", duration_from(started))
    except (FileNotFoundError, PermissionError, OSError) as e:
        return _err(f"kicad-cli failed to launch: {e}", duration_from(started))

    duration_ms = duration_from(started)
    exit_code = proc.returncode if proc.returncode is not None else -1
    stdout_text = (stdout or b"").decode("utf-8", errors="replace").strip()
    stderr_text = (stderr or b"").decode("utf-8", errors="replace").strip()

    # kicad-cli emits the JSON report on stdout. A clean run with no
    # violations still produces a `{ "violations": [], ... }` block.
    if not stdout_text:
        msg = stderr_text or f"kicad-cli exited {exit_code} with no output"
        return _err(msg, duration_ms, exit_code=exit_code)
    try:
        payload = json.loads(stdout_text)
    except json.JSONDecodeError as e:
        return _err(
            f"failed to parse kicad-cli JSON: {e}; stdout was: {stdout_text[:200]}",
            duration_ms,
            exit_code=exit_code,
        )
    issues = _normalize(payload)
    return ErcReport(
        ok=True,
        issues=issues,
        error=stderr_text or None,
        duration_ms=duration_ms,
        exit_code=exit_code,
    )


def parse_erc_report(payload: dict[str, Any]) -> list[ErcIssue]:
    """Public helper — exposed so tests and downstream tools can
    re-parse a captured kicad-cli JSON report without invoking the
    subprocess."""
    return _normalize(payload)


# ---------------------------------------------------------------------
# Internal helpers.
# ---------------------------------------------------------------------


def _resolve_target(project_path: str | Path) -> Path:
    p = Path(project_path).expanduser()
    if not p.is_absolute():
        p = p.resolve()
    return p


def _err(message: str, duration_ms: int, *, exit_code: int | None = None) -> ErcReport:
    return ErcReport(
        ok=False,
        issues=[],
        error=message,
        duration_ms=duration_ms,
        exit_code=exit_code,
    )


def duration_from(started: float) -> int:
    """Wall-clock ms since `started` (an event-loop timestamp)."""
    delta = asyncio.get_event_loop().time() - started
    return max(0, int(delta * 1000.0))


def _normalize(payload: dict[str, Any]) -> list[ErcIssue]:
    """kicad-cli's ERC JSON has the shape:

    ```json
    {
      "$schema": "...",
      "source": "blinky.kicad_sch",
      "sheets": [
        {
          "uuid_path": "/<uuid>",
          "path": "/path/to/sheet",
          "violations": [
            { "severity": "warning", "type": "no_connect",
              "description": "...", "items": [
                { "uuid": "...", "pos_x": 50.0, "pos_y": 60.0 }
              ]
            }
          ]
        }
      ]
    }
    ```

    Older / newer kicad-cli versions sometimes flatten everything
    onto a single top-level `violations` array. We accept both
    shapes.
    """
    issues: list[ErcIssue] = []
    sheets = payload.get("sheets") or []
    if not sheets:
        # Flat-format fallback.
        for v in payload.get("violations", []) or []:
            issues.extend(_to_issues(v, sheet=""))
        return issues
    for sheet in sheets:
        sheet_id = (
            sheet.get("uuid_path")
            or sheet.get("uuid")
            or sheet.get("path")
            or ""
        )
        for v in sheet.get("violations", []) or []:
            issues.extend(_to_issues(v, sheet=sheet_id))
    return issues


def _to_issues(violation: dict[str, Any], *, sheet: str) -> Sequence[ErcIssue]:
    severity = (violation.get("severity") or "warning").lower()
    issue_type = violation.get("type") or violation.get("code") or ""
    description = violation.get("description") or violation.get("message") or ""
    items = violation.get("items") or []
    if not items:
        return [
            ErcIssue(
                severity=severity,
                sheet=sheet,
                position_mm=(0.0, 0.0),
                type=issue_type,
                description=description,
            )
        ]
    out: list[ErcIssue] = []
    for item in items:
        out.append(
            ErcIssue(
                severity=severity,
                sheet=sheet,
                position_mm=(
                    float(item.get("pos_x", 0.0) or 0.0),
                    float(item.get("pos_y", 0.0) or 0.0),
                ),
                type=issue_type,
                description=description,
            )
        )
    return out


# Suppress unused-import lint when callers don't reach `asdict` —
# we expose it for tests that introspect dataclass shape.
_ = asdict


__all__ = [
    "DEFAULT_TIMEOUT_S",
    "ErcIssue",
    "ErcReport",
    "parse_erc_report",
    "run_erc",
]
