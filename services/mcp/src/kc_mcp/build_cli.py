"""`python -m kc_mcp.build_cli <project>` — M2-T-10 entry point.

Runs the full fab pipeline against a KiCad project directory:

1. **validate** — KC001..KC011 + kicad-cli ERC (delegates to
   `kc_mcp.validate_cli.run_validate`).
2. **drc** — `kicad-cli pcb drc` via `kiconnector.drc.run_drc`.
3. **gerbers** — `kicad-cli pcb export gerbers`.
4. **drill** — `kicad-cli pcb export drill`.
5. **pos** — `kicad-cli pcb export pos`.
6. **bom** — `kicad-cli sch export bom` (when a `.kicad_sch` is found).

Exit code is non-zero whenever any stage fails (gate semantics — the
M2-T-10 plan acceptance). `--json` flips the output to a structured
manifest for downstream tooling.

The pipeline runs each stage in-process via the kiconnector wrappers
— there is no HTTP fan-out, no running daemon required. All
subprocess invocations enforce their own timeouts.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from kc_mcp.validate_cli import ValidateReport, run_validate


@dataclass(slots=True)
class StageResult:
    """One pipeline stage's outcome."""

    name: str
    ok: bool
    duration_ms: int = 0
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class BuildReport:
    """Aggregated `kiclaude build` output."""

    ok: bool
    project_path: str
    output_dir: str
    stages: list[StageResult] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "project_path": self.project_path,
            "output_dir": self.output_dir,
            "stages": [asdict(s) for s in self.stages],
        }


def _resolve_project_dir(target: Path) -> Path:
    target = target.expanduser().resolve()
    if target.is_dir():
        return target
    if target.is_file() and target.suffix in {".kicad_pro", ".kicad_sch", ".kicad_pcb"}:
        return target.parent
    raise FileNotFoundError(
        f"target must be a directory or a .kicad_pro/.kicad_sch/.kicad_pcb file: {target}"
    )


def _find_pcb(project_dir: Path) -> Path | None:
    candidates = sorted(project_dir.glob("*.kicad_pcb"))
    return candidates[0] if candidates else None


def _find_sch(project_dir: Path) -> Path | None:
    candidates = sorted(project_dir.glob("*.kicad_sch"))
    if not candidates:
        return None
    # Prefer the one that matches the .kicad_pro stem.
    pro_stem = ""
    pros = sorted(project_dir.glob("*.kicad_pro"))
    if pros:
        pro_stem = pros[0].stem
    for c in candidates:
        if c.stem == pro_stem:
            return c
    return candidates[0]


async def run_build(
    project_path: str,
    *,
    output_dir: str | None = None,
    skip_erc: bool = False,
    skip_drc: bool = False,
    skip_export: bool = False,
) -> BuildReport:
    """Programmatic entry — runs the pipeline and returns the report."""
    project_dir = _resolve_project_dir(Path(project_path))
    out_root = (
        Path(output_dir).expanduser().resolve()
        if output_dir
        else project_dir / "dist"
    )
    try:
        out_root.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        return BuildReport(
            ok=False,
            project_path=str(project_dir),
            output_dir=str(out_root),
            stages=[
                StageResult(
                    name="prepare",
                    ok=False,
                    detail={"error": f"cannot create output dir {out_root}: {e}"},
                )
            ],
        )

    stages: list[StageResult] = []
    pipeline_ok = True

    # 1. validate
    validate_report = await run_validate(str(project_dir), skip_erc=skip_erc)
    stages.append(
        StageResult(
            name="validate",
            ok=validate_report.ok,
            detail=_validate_detail(validate_report),
        )
    )
    if not validate_report.ok:
        pipeline_ok = False

    pcb = _find_pcb(project_dir)
    sch = _find_sch(project_dir)

    # 2. drc
    if not skip_drc and pcb is not None:
        from kiconnector.drc import run_drc

        drc_report = await run_drc(pcb)
        stages.append(
            StageResult(
                name="drc",
                ok=drc_report.ok and not _has_errors(drc_report.issues),
                duration_ms=drc_report.duration_ms,
                detail={
                    "ok": drc_report.ok,
                    "issues": [i.to_dict() for i in drc_report.issues],
                    "error": drc_report.error,
                    "exit_code": drc_report.exit_code,
                },
            )
        )
        if not drc_report.ok or _has_errors(drc_report.issues):
            pipeline_ok = False
    elif pcb is None:
        stages.append(
            StageResult(
                name="drc",
                ok=False,
                detail={"error": "no .kicad_pcb found in project dir"},
            )
        )
        pipeline_ok = False

    # 3-5. exports (gerbers + drill + pos)
    if not skip_export and pcb is not None:
        from kiconnector.export import export_drill, export_gerbers, export_pos

        gerb = await export_gerbers(pcb, out_root)
        stages.append(_stage_from_artifact("gerbers", gerb))
        if not gerb.ok:
            pipeline_ok = False

        drill = await export_drill(pcb, out_root)
        stages.append(_stage_from_artifact("drill", drill))
        if not drill.ok:
            pipeline_ok = False

        pos = await export_pos(pcb, out_root)
        stages.append(_stage_from_artifact("pos", pos))
        if not pos.ok:
            pipeline_ok = False

    # 6. bom (only if a .kicad_sch exists)
    if not skip_export and sch is not None:
        from kiconnector.bom import run_bom

        bom = await run_bom(sch, out_root)
        stages.append(
            StageResult(
                name="bom",
                ok=bom.ok,
                duration_ms=bom.duration_ms,
                detail={
                    "ok": bom.ok,
                    "csv_path": bom.csv_path,
                    "grouped_csv_path": bom.grouped_csv_path,
                    "rows": len(bom.rows),
                    "error": bom.error,
                    "exit_code": bom.exit_code,
                },
            )
        )
        if not bom.ok:
            pipeline_ok = False

    return BuildReport(
        ok=pipeline_ok,
        project_path=str(project_dir),
        output_dir=str(out_root),
        stages=stages,
    )


def _validate_detail(report: ValidateReport) -> dict[str, Any]:
    return {
        "project_name": report.project_name,
        "summary": report.summary,
        "findings": report.findings,
        "erc_ok": report.erc_ok,
        "erc_error": report.erc_error,
        "erc_issues": report.erc_issues,
    }


def _has_errors(issues: list[Any]) -> bool:
    for i in issues:
        sev = (
            i.severity
            if hasattr(i, "severity")
            else (i.get("severity") if isinstance(i, dict) else "")
        )
        if str(sev).lower() == "error":
            return True
    return False


def _stage_from_artifact(name: str, artifact: Any) -> StageResult:
    return StageResult(
        name=name,
        ok=artifact.ok,
        duration_ms=artifact.duration_ms,
        detail={
            "ok": artifact.ok,
            "output_dir": artifact.output_dir,
            "files": list(artifact.files),
            "error": artifact.error,
            "exit_code": artifact.exit_code,
        },
    )


def _human_report(report: BuildReport, *, color: bool) -> str:
    def paint(s: str, code: str) -> str:
        if not color:
            return s
        return f"\x1b[{code}m{s}\x1b[0m"

    lines: list[str] = []
    header = f"kiclaude build — {Path(report.project_path).name}"
    lines.append(header)
    lines.append("=" * len(header))
    lines.append(f"project:    {report.project_path}")
    lines.append(f"output dir: {report.output_dir}")
    lines.append("")
    for stage in report.stages:
        status = paint("PASS", "32;1") if stage.ok else paint("FAIL", "31;1")
        line = f"  [{status}] {stage.name:10} ({stage.duration_ms} ms)"
        err = (stage.detail or {}).get("error")
        if err:
            line += f"  — {err}"
        lines.append(line)
    lines.append("")
    overall = paint("PASS", "32;1") if report.ok else paint("FAIL", "31;1")
    lines.append(f"result: {overall}")
    return "\n".join(lines)


def _build_argparser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="kiclaude build",
        description=(
            "Run the full fab pipeline against a KiCad project: validate → "
            "DRC → gerber + drill + PnP + BOM. Non-zero exit on any gate "
            "failure."
        ),
    )
    p.add_argument("project", help="Path to project directory or .kicad_pro file")
    p.add_argument(
        "--out",
        dest="output_dir",
        default=None,
        help="Output directory for fab artifacts (defaults to <project>/dist).",
    )
    p.add_argument("--json", action="store_true", help="Emit JSON instead of the human report.")
    p.add_argument(
        "--no-color", action="store_true", help="Disable ANSI color in the human report."
    )
    p.add_argument(
        "--skip-erc", action="store_true", help="Skip the ERC pass in the validate stage."
    )
    p.add_argument("--skip-drc", action="store_true", help="Skip the DRC stage.")
    p.add_argument("--skip-export", action="store_true", help="Skip the fab-export stages.")
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_argparser().parse_args(argv)
    try:
        report = asyncio.run(
            run_build(
                args.project,
                output_dir=args.output_dir,
                skip_erc=args.skip_erc,
                skip_drc=args.skip_drc,
                skip_export=args.skip_export,
            )
        )
    except FileNotFoundError as e:
        sys.stderr.write(f"kiclaude build: {e}\n")
        return 2
    if args.json:
        sys.stdout.write(json.dumps(report.to_dict(), indent=2) + "\n")
    else:
        color = sys.stdout.isatty() and not args.no_color and "NO_COLOR" not in os.environ
        sys.stdout.write(_human_report(report, color=color) + "\n")
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["BuildReport", "StageResult", "main", "run_build"]
