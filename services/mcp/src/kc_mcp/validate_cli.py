"""`python -m kc_mcp.validate_cli <project>` — M1-T-09 entry point.

Runs the KC001..KC011 structural validators + kicad-cli ERC against a
KiCad project directory or `.kicad_pro` / `.kicad_sch` file and prints
either a human-readable summary (default) or a structured JSON report
(`--json`). Exits non-zero on any error-severity finding.

This is the cross-language bridge for the `kiclaude validate`
subcommand: the TypeScript CLI shells out to this module so the
validator + ERC implementations stay in one place. No network calls;
runs entirely on the local machine.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from kc_mcp.tools.validate import _run_validators


@dataclass(slots=True)
class ValidateReport:
    """The aggregated `validate` output."""

    ok: bool
    project_path: str
    project_name: str | None = None
    findings: list[dict[str, Any]] = field(default_factory=list)
    erc_issues: list[dict[str, Any]] = field(default_factory=list)
    erc_ok: bool = True
    erc_error: str | None = None
    summary: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "project_path": self.project_path,
            "project_name": self.project_name,
            "findings": self.findings,
            "erc_issues": self.erc_issues,
            "erc_ok": self.erc_ok,
            "erc_error": self.erc_error,
            "summary": self.summary,
        }


def _resolve_project_dir(target: Path) -> Path:
    """Accept either a project directory, a `.kicad_pro`, or a
    `.kicad_sch`. Returns the parent directory in the latter two
    cases."""
    target = target.expanduser().resolve()
    if target.is_dir():
        return target
    if target.is_file() and target.suffix in {".kicad_pro", ".kicad_sch", ".kicad_pcb"}:
        return target.parent
    raise FileNotFoundError(
        f"target must be a directory or a .kicad_pro/.kicad_sch/.kicad_pcb file: {target}"
    )


def _load_project(project_dir: Path) -> dict[str, Any]:
    """Open the project via `ki_native`. Raises `RuntimeError` if
    PyO3 isn't installed."""
    try:
        import ki_native  # type: ignore[import-not-found]
    except ImportError as e:
        raise RuntimeError(
            "ki_native is not installed — run `maturin develop --features python` in crates/ki/."
        ) from e
    try:
        return ki_native.open_project(str(project_dir))  # type: ignore[no-any-return]
    except ValueError as e:
        raise RuntimeError(f"failed to open project: {e}") from e


async def _run_erc(
    project_dir: Path, skip_erc: bool
) -> tuple[bool, list[dict[str, Any]], str | None]:
    """Best-effort ERC. Returns `(ok, issues, error)`. When kicad-cli
    is unavailable, returns `(False, [], <reason>)` rather than
    raising — validators still produce a useful report on their own."""
    if skip_erc:
        return True, [], None
    # Import lazily so missing kiconnector doesn't break the import
    # of the validator-only path.
    from kiconnector.erc import run_erc

    report = await run_erc(project_dir)
    issues = [
        {
            "severity": issue.severity,
            "sheet": issue.sheet,
            "position_mm": list(issue.position_mm) if issue.position_mm else None,
            "type": issue.type,
            "description": issue.description,
        }
        for issue in report.issues
    ]
    return report.ok, issues, report.error


def _summarize(findings: list[dict[str, Any]], erc_issues: list[dict[str, Any]]) -> dict[str, int]:
    """Combined error/warning/info counts across KC + ERC."""
    out = {"error": 0, "warning": 0, "info": 0}
    for f in findings:
        sev = str(f.get("severity", "")).lower()
        if sev in out:
            out[sev] += 1
    for issue in erc_issues:
        sev = str(issue.get("severity", "")).lower()
        # KiCad uses `error`, `warning`, `exclusion`, `ignore`.
        if sev == "error":
            out["error"] += 1
        elif sev == "warning":
            out["warning"] += 1
        elif sev in {"exclusion", "ignore", "info"}:
            out["info"] += 1
    return out


async def run_validate(
    project_path: str,
    *,
    skip_erc: bool = False,
) -> ValidateReport:
    """Programmatic entry point — used by tests and the CLI alike."""
    project_dir = _resolve_project_dir(Path(project_path))
    try:
        project = _load_project(project_dir)
    except RuntimeError as e:
        return ValidateReport(
            ok=False,
            project_path=str(project_dir),
            erc_ok=False,
            erc_error=str(e),
            summary={"error": 1, "warning": 0, "info": 0},
        )
    findings = _run_validators(project)
    erc_ok, erc_issues, erc_error = await _run_erc(project_dir, skip_erc)
    summary = _summarize(findings, erc_issues)
    overall_ok = summary["error"] == 0
    return ValidateReport(
        ok=overall_ok,
        project_path=str(project_dir),
        project_name=project.get("name") if isinstance(project, dict) else None,
        findings=findings,
        erc_issues=erc_issues,
        erc_ok=erc_ok,
        erc_error=erc_error,
        summary=summary,
    )


def _human_report(report: ValidateReport, *, color: bool) -> str:
    """Format a `ValidateReport` for terminal output."""

    def paint(s: str, code: str) -> str:
        if not color:
            return s
        return f"\x1b[{code}m{s}\x1b[0m"

    lines: list[str] = []
    name = report.project_name or Path(report.project_path).name
    header = f"kiclaude validate — {name}"
    lines.append(header)
    lines.append("=" * len(header))
    lines.append(f"path:    {report.project_path}")
    sev_line = (
        f"findings: {paint(str(report.summary.get('error', 0)), '31;1')} error  "
        f"{paint(str(report.summary.get('warning', 0)), '33;1')} warning  "
        f"{paint(str(report.summary.get('info', 0)), '36;1')} info"
    )
    lines.append(sev_line)
    lines.append("")
    if report.findings:
        lines.append("KCIR validators:")
        for f in report.findings:
            sev = str(f.get("severity", "")).upper()
            code_str = str(f.get("code", ""))
            target = f.get("target_uuid") or ""
            line = f"  [{code_str:5}] {sev:7} {f.get('message', '')}"
            if target:
                line += f"  (uuid={target})"
            sev_color = "31" if sev == "ERROR" else "33" if sev == "WARNING" else "36"
            lines.append(paint(line, sev_color))
    else:
        lines.append("KCIR validators: no findings")
    lines.append("")
    if report.erc_error:
        lines.append(paint(f"ERC: skipped — {report.erc_error}", "33"))
    elif report.erc_issues:
        lines.append("ERC:")
        for issue in report.erc_issues:
            sev = str(issue.get("severity", "")).upper()
            ty = str(issue.get("type", ""))
            desc = str(issue.get("description", ""))
            sheet = str(issue.get("sheet", "")) or "/"
            line = f"  {sev:7} {ty:24} {sheet}  {desc}"
            sev_color = "31" if sev == "ERROR" else "33" if sev == "WARNING" else "36"
            lines.append(paint(line, sev_color))
    else:
        lines.append("ERC: no issues")
    lines.append("")
    status = paint("PASS", "32;1") if report.ok else paint("FAIL", "31;1")
    lines.append(f"result: {status}")
    return "\n".join(lines)


def _build_argparser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="kiclaude validate",
        description=(
            "Run KC001..KC011 KCIR validators + kicad-cli ERC against a "
            "KiCad project directory. Exits non-zero on any error-severity "
            "finding."
        ),
    )
    p.add_argument("project", help="Path to project directory or .kicad_pro file")
    p.add_argument(
        "--json",
        action="store_true",
        help="Emit structured JSON instead of the human report.",
    )
    p.add_argument(
        "--no-color",
        action="store_true",
        help="Disable ANSI color codes in the human report.",
    )
    p.add_argument(
        "--skip-erc",
        action="store_true",
        help="Skip the kicad-cli ERC pass and only run KCIR validators.",
    )
    return p


def _color_enabled(args: argparse.Namespace) -> bool:
    if args.no_color:
        return False
    if os.environ.get("NO_COLOR"):
        return False
    return sys.stdout.isatty()


def main(argv: list[str] | None = None) -> int:
    args = _build_argparser().parse_args(argv)
    try:
        report = asyncio.run(run_validate(args.project, skip_erc=args.skip_erc))
    except FileNotFoundError as e:
        print(f"kiclaude validate: {e}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(report.to_dict(), sort_keys=True, indent=2))
    else:
        print(_human_report(report, color=_color_enabled(args)))
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
