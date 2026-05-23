"""DRC (Design Rules Check) wrapper around `kicad-cli pcb drc`
(M2-P-01).

`kicad-cli pcb drc --format json` produces a structured violations
report (clearance, courtyard, annular ring, drill-to-copper, etc.);
this module normalizes it into a list of [`DrcIssue`][DrcIssue]
objects shaped for the M2-T-06 DRC overlay.

Per spec D8 (§16.1) **kicad-cli is the source of truth for DRC** —
the Rust kernel in `crates/cad/src/drc/` exists only for live editor
overlays, never for the gating result.

The wrapper enforces a 60 s timeout (per the M2-P-01 plan) and
returns a typed [`DrcReport`][DrcReport] that distinguishes "tool
failure" (kicad-cli missing / crashed) from "DRC ran and found N
issues".
"""

from __future__ import annotations

import asyncio
import json
import shutil
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# 60s budget mirrors the M2-P-01 plan acceptance.
DEFAULT_TIMEOUT_S = 60.0


@dataclass(frozen=True, slots=True)
class DrcIssue:
    """One DRC violation surfaced for the editor overlay.

    `layer` carries KiCad's layer name (e.g. `"F.Cu"`); for issues
    that span multiple layers (drill-to-copper) it is the primary
    layer KiCad reports.
    """

    severity: str  # "error" | "warning" | "exclusion" | "ignore"
    layer: str
    position_mm: tuple[float, float]  # (x, y); (0, 0) when unknown
    type: str  # KiCad's `code` field (e.g. "clearance", "courtyards_overlap")
    description: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "severity": self.severity,
            "layer": self.layer,
            "position_mm": list(self.position_mm),
            "type": self.type,
            "description": self.description,
        }


@dataclass(slots=True)
class DrcReport:
    """The result of a kicad-cli DRC run.

    `ok` is False whenever the underlying subprocess failed (missing
    binary, timed out, non-zero exit with no JSON). In those cases
    `error` carries the diagnostic and `issues` is empty.
    """

    ok: bool
    issues: list[DrcIssue] = field(default_factory=list)
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


async def run_drc(
    pcb_path: str | Path,
    *,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    kicad_cli_binary: str = "kicad-cli",
    severity_min: str = "warning",
) -> DrcReport:
    """Run `kicad-cli pcb drc --format json` on `pcb_path` and return
    a parsed [`DrcReport`].

    `severity_min` filters violations whose severity is at or above
    the given threshold ("error" > "warning" > "exclusion" > "ignore").
    Defaults to `"warning"` so the overlay doesn't drown in info-only
    rows.

    Surfaces every failure mode as a `DrcReport.ok=False` with the
    diagnostic in `error` — never raises.
    """
    started = asyncio.get_event_loop().time()

    target = Path(pcb_path).expanduser()
    if not target.is_absolute():
        target = target.resolve()
    if not target.exists():
        return _err(f"PCB not found: {pcb_path}", _duration(started))
    if target.suffix != ".kicad_pcb":
        return _err(
            f"DRC target must be a .kicad_pcb file, got {target.suffix or 'no extension'}",
            _duration(started),
        )

    binary = shutil.which(kicad_cli_binary)
    if binary is None:
        return _err(f"{kicad_cli_binary} not on PATH", _duration(started))

    args = [
        "pcb",
        "drc",
        "--format",
        "json",
        "--severity-all",
        "--exit-code-violations",
        str(target),
    ]
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
        return _err(f"kicad-cli timed out after {timeout_s}s", _duration(started))
    except (FileNotFoundError, PermissionError, OSError) as e:
        return _err(f"kicad-cli failed to launch: {e}", _duration(started))

    duration_ms = _duration(started)
    exit_code = proc.returncode if proc.returncode is not None else -1
    stdout_text = (stdout or b"").decode("utf-8", errors="replace").strip()
    stderr_text = (stderr or b"").decode("utf-8", errors="replace").strip()

    # Some kicad-cli builds dump the JSON to stdout AND a `--output`
    # file. We only read stdout — that matches the ERC wrapper.
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
    issues = _filter_severity(_normalize(payload), severity_min)
    return DrcReport(
        ok=True,
        issues=issues,
        error=stderr_text or None,
        duration_ms=duration_ms,
        exit_code=exit_code,
    )


def parse_drc_report(payload: dict[str, Any]) -> list[DrcIssue]:
    """Public helper — exposed so tests and the M2-T-06 overlay can
    re-parse a captured kicad-cli JSON report without invoking the
    subprocess."""
    return _normalize(payload)


# ---------------------------------------------------------------------
# Internal helpers.
# ---------------------------------------------------------------------


_SEVERITY_RANK = {"ignore": 0, "exclusion": 1, "warning": 2, "error": 3}


def _filter_severity(issues: Sequence[DrcIssue], minimum: str) -> list[DrcIssue]:
    threshold = _SEVERITY_RANK.get(minimum.lower(), _SEVERITY_RANK["warning"])
    return [i for i in issues if _SEVERITY_RANK.get(i.severity.lower(), 0) >= threshold]


def _err(message: str, duration_ms: int, *, exit_code: int | None = None) -> DrcReport:
    return DrcReport(
        ok=False,
        issues=[],
        error=message,
        duration_ms=duration_ms,
        exit_code=exit_code,
    )


def _duration(started: float) -> int:
    """Wall-clock ms since `started` (an event-loop timestamp)."""
    delta = asyncio.get_event_loop().time() - started
    return max(0, int(delta * 1000.0))


def _normalize(payload: dict[str, Any]) -> list[DrcIssue]:
    """kicad-cli's DRC JSON has the shape:

    ```json
    {
      "$schema": "...",
      "source": "blinky.kicad_pcb",
      "violations": [
        {
          "severity": "error",
          "type": "clearance",
          "description": "Clearance violation",
          "items": [
            { "uuid": "...", "pos_x": 50.0, "pos_y": 60.0, "layer": "F.Cu" }
          ]
        }
      ],
      "unconnected_items": [],
      "schematic_parity": []
    }
    ```

    Each top-level bucket (`violations`, `unconnected_items`,
    `schematic_parity`) is normalized identically — only the
    bucket-derived `type` prefix differs.
    """
    issues: list[DrcIssue] = []
    for bucket in ("violations", "unconnected_items", "schematic_parity"):
        for v in payload.get(bucket) or []:
            issues.extend(_to_issues(v, fallback_type=bucket))
    return issues


def _to_issues(violation: dict[str, Any], *, fallback_type: str) -> Sequence[DrcIssue]:
    severity = (violation.get("severity") or "warning").lower()
    issue_type = (
        violation.get("type") or violation.get("code") or fallback_type
    )
    description = (
        violation.get("description") or violation.get("message") or ""
    )
    items = violation.get("items") or []
    if not items:
        return [
            DrcIssue(
                severity=severity,
                layer="",
                position_mm=(0.0, 0.0),
                type=issue_type,
                description=description,
            )
        ]
    out: list[DrcIssue] = []
    for item in items:
        out.append(
            DrcIssue(
                severity=severity,
                layer=str(item.get("layer") or item.get("layer_name") or ""),
                position_mm=(
                    float(item.get("pos_x", 0.0) or 0.0),
                    float(item.get("pos_y", 0.0) or 0.0),
                ),
                type=issue_type,
                description=description,
            )
        )
    return out


__all__ = [
    "DEFAULT_TIMEOUT_S",
    "DrcIssue",
    "DrcReport",
    "parse_drc_report",
    "run_drc",
]
