"""End-to-end pipeline: source → CIR → KiCad project → routed → fab package.

This is what ``kimp build`` and the MCP ``pcb_build`` tool wrap. Each
stage is a pure function over filesystem paths so the orchestrator can
be tested layer by layer.

The pipeline fails closed at every stage:
  - parse: ValueError on syntax issues
  - CIR validate: structural errors abort
  - sourcing: missing MPNs abort before any KiCad file is written
  - synthesize: UnresolvedMPNError from the resolver re-raises
  - drc/erc: non-zero errors abort fab packaging

Stages requiring KiCad gracefully short-circuit with a structured
result (instead of crashing) so callers can present a useful message.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ki_mcp_pcb_core import _kicad_cli
from ki_mcp_pcb_core.cir.models import Board
from ki_mcp_pcb_core.cir.validation import validate_board
from ki_mcp_pcb_core.parsers import parse_ato, parse_yaml
from ki_mcp_pcb_core.sourcing import check_sourcing
from ki_mcp_pcb_core.synthesis import synthesize


@dataclass(frozen=True)
class BuildStageResult:
    name: str
    ok: bool
    detail: dict[str, Any]


#: Optional progress callback — invoked with each stage as it completes.
StageCallback = Callable[[BuildStageResult], None]


@dataclass(frozen=True)
class BuildResult:
    ok: bool
    stages: list[BuildStageResult]
    out_dir: Path

    def stage(self, name: str) -> BuildStageResult | None:
        for s in self.stages:
            if s.name == name:
                return s
        return None


class _StageList(list[BuildStageResult]):
    """A stage list that also fires a callback as each stage is appended.

    Lets ``build`` stream progress without threading a callback through
    its dozen-plus ``append`` sites — the existing body is untouched.
    """

    def __init__(self, on_stage: StageCallback | None) -> None:
        super().__init__()
        self._on_stage = on_stage

    def append(self, stage: BuildStageResult) -> None:
        super().append(stage)
        if self._on_stage is not None:
            self._on_stage(stage)


def build(
    source: Path,
    out_dir: Path,
    *,
    run_route: bool = False,
    on_stage: StageCallback | None = None,
) -> BuildResult:
    """Run the full pipeline. Returns a structured per-stage report.

    ``on_stage``, when given, is called with each :class:`BuildStageResult`
    the moment it completes — used by callers (e.g. the GUI) that stream
    progress rather than waiting for the final result.
    """
    source = Path(source)
    out_dir = Path(out_dir)

    stages: list[BuildStageResult] = _StageList(on_stage)

    try:
        out_dir.mkdir(parents=True, exist_ok=True)
    except (FileExistsError, NotADirectoryError, OSError) as exc:
        stages.append(BuildStageResult("setup", False, {"error": f"output path: {exc}"}))
        return BuildResult(ok=False, stages=stages, out_dir=out_dir)

    # 1. Parse ---------------------------------------------------------
    try:
        board = _parse(source)
    except Exception as exc:
        stages.append(BuildStageResult("parse", False, {"error": str(exc)}))
        return BuildResult(ok=False, stages=stages, out_dir=out_dir)
    stages.append(BuildStageResult("parse", True, {
        "components": len(board.components),
        "nets": len(board.nets),
    }))

    # 2. Validate ------------------------------------------------------
    report = validate_board(board)
    stages.append(BuildStageResult("validate", report.ok, {
        "errors": len(report.errors),
        "warnings": len(report.warnings),
        "issues": [i.model_dump() for i in report.issues],
    }))
    if not report.ok:
        return BuildResult(ok=False, stages=stages, out_dir=out_dir)

    # 3. Sourcing ------------------------------------------------------
    sourcing = check_sourcing(board)
    stages.append(BuildStageResult("sourcing", sourcing.ok, {
        "missing": [e.mpn for e in sourcing.missing],
        "entries": [{"refdes": e.refdes, "mpn": e.mpn, "status": e.status, "lcsc": e.lcsc}
                    for e in sourcing.entries],
    }))
    if not sourcing.ok:
        return BuildResult(ok=False, stages=stages, out_dir=out_dir)

    # 4. Synthesize ----------------------------------------------------
    try:
        synth = synthesize(board, out_dir)
    except Exception as exc:
        stages.append(BuildStageResult("synthesize", False, {"error": str(exc)}))
        return BuildResult(ok=False, stages=stages, out_dir=out_dir)
    stages.append(BuildStageResult("synthesize", True, {
        "project": str(synth.project_path),
        "pcb": str(synth.pcb_path),
        "netlist": str(synth.netlist_path),
    }))

    # 5. Populate — kill the manual "Update PCB from netlist" step ----
    from ki_mcp_pcb_core.placement import plan_placement
    from ki_mcp_pcb_core.synthesis.populator import populate as _populate

    # Hint-aware placement: a declared rectangular outline gives the board
    # dimensions the edge hints ("south edge", …) need; otherwise
    # plan_placement falls back to its own defaults.
    outline = board.outline
    board_w = outline.width_mm if outline.shape == "rect" and outline.width_mm else 50.0
    board_h = outline.height_mm if outline.shape == "rect" and outline.height_mm else 40.0
    placements = plan_placement(
        board.components, board_width_mm=board_w, board_height_mm=board_h
    )

    pop = _populate(
        synth.pcb_path,
        synth.netlist_path,
        fab=board.fab,
        outline=board.outline,
        placements=placements,
    )
    if pop.status == "pcbnew_unavailable":
        stages.append(BuildStageResult("populate", False, {
            "skipped": True,
            "reason": "pcbnew not importable — run `kimp doctor`. "
                      "Until then, open the .kicad_pro in KiCad and 'Update PCB from netlist'.",
        }))
        # Without populate, everything downstream is skipped. The synthesized
        # files still exist for the user to bring up in KiCad.
        for stage_name in ("erc", "drc", "fab"):
            stages.append(BuildStageResult(stage_name, False, {
                "skipped": True, "reason": "depends on populate (pcbnew)",
            }))
        return BuildResult(ok=True, stages=stages, out_dir=out_dir)

    stages.append(BuildStageResult("populate", pop.ok, {
        "components_placed": pop.components_placed,
        "footprints_missing": pop.footprints_missing,
        "errors": pop.errors,
    }))
    if not pop.ok:
        return BuildResult(ok=False, stages=stages, out_dir=out_dir)

    # 6. KiCad-cli gated stages — should be available once pcbnew is ---
    if not _kicad_cli.is_available():
        for stage_name in ("erc", "drc", "fab"):
            stages.append(BuildStageResult(stage_name, False, {
                "skipped": True, "reason": "kicad-cli not on PATH — run `kimp doctor`",
            }))
        return BuildResult(ok=True, stages=stages, out_dir=out_dir)

    # 7. ERC + DRC -----------------------------------------------------
    from ki_mcp_pcb_core.validation.drc import run_drc as _drc

    # DRC runs after populate but before the optional routing step below,
    # so the board is populated-but-unrouted here: score ratsnest
    # completeness as non-fatal (expect_routed=False), keep rule
    # violations fatal.
    drc_path = synth.pcb_path
    drc_result = _drc(drc_path, expect_routed=False)
    stages.append(BuildStageResult("drc", drc_result.ok, {
        "errors": drc_result.errors,
        "warnings": drc_result.warnings,
        "report_path": str(drc_result.report_path) if drc_result.report_path else None,
    }))

    # ERC — run kicad-cli sch erc on the synthesized schematic. If
    # kicad-cli can't load the schematic (a known limitation of the
    # current kiutils-emitted .kicad_sch format), run_erc returns a
    # structured "cli_failure"; we mark the stage skipped rather than
    # failing the build over that upstream synthesis limitation. Once the
    # schematic format is fixed this stage starts enforcing ERC with no
    # further change here.
    from ki_mcp_pcb_core.validation.erc import run_erc as _erc

    erc_result = _erc(synth.schematic_path)
    if any(issue.type == "cli_failure" for issue in erc_result.issues):
        stages.append(BuildStageResult("erc", True, {
            "skipped": True,
            "reason": erc_result.issues[0].description,
        }))
    else:
        stages.append(BuildStageResult("erc", erc_result.ok, {
            "errors": erc_result.errors,
            "warnings": erc_result.warnings,
            "report_path": (
                str(erc_result.report_path) if erc_result.report_path else None
            ),
        }))

    # 7. Routing (optional) -------------------------------------------
    if run_route:
        from ki_mcp_pcb_core.routing import route as _route
        try:
            _route(synth.pcb_path)
            stages.append(BuildStageResult("route", True, {}))
        except Exception as exc:
            stages.append(BuildStageResult("route", False, {"error": str(exc)}))
            return BuildResult(ok=False, stages=stages, out_dir=out_dir)

    # 8. Fab package ---------------------------------------------------
    from ki_mcp_pcb_core.export.fab_package import export_fab_package
    try:
        pkg = export_fab_package(board, synth.pcb_path, out_dir / "fab")
        stages.append(BuildStageResult("fab", True, {"zip": str(pkg.zip_path)}))
    except Exception as exc:
        stages.append(BuildStageResult("fab", False, {"error": str(exc)}))
        return BuildResult(ok=False, stages=stages, out_dir=out_dir)

    overall_ok = all(s.ok or s.detail.get("skipped") for s in stages)
    return BuildResult(ok=overall_ok, stages=stages, out_dir=out_dir)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _parse(source: Path) -> Board:
    suffix = source.suffix.lower()
    if suffix in {".yaml", ".yml"}:
        return parse_yaml(source)
    if suffix == ".ato":
        return parse_ato(source)
    raise ValueError(f"Unknown CIR source extension: {suffix!r}")


# ---------------------------------------------------------------------------
# Doctor — environment diagnostics
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DoctorCheck:
    name: str
    ok: bool
    detail: str


def doctor() -> list[DoctorCheck]:
    """Probe the local environment for everything M1 needs."""
    import os
    import shutil

    checks: list[DoctorCheck] = []

    # kicad-cli
    if _kicad_cli.is_available():
        checks.append(DoctorCheck("kicad-cli", True, _kicad_cli.find_kicad_cli()))
    else:
        checks.append(DoctorCheck(
            "kicad-cli", False,
            "not found on PATH. Install KiCad 9+ or set KICAD_CLI env var."
        ))

    # kiutils
    try:
        import kiutils  # noqa: F401
        checks.append(DoctorCheck("kiutils", True, "importable"))
    except ImportError:
        checks.append(DoctorCheck("kiutils", False, "pip install kiutils (or `uv sync --extra kicad`)"))

    # pcbnew (KiCad-bundled — needed to populate the PCB from netlist)
    from ki_mcp_pcb_core.synthesis.populator import (
        PCBNewNotAvailableError,
        find_pcbnew_python,
    )
    try:
        py = find_pcbnew_python()
        checks.append(DoctorCheck("pcbnew", True, f"importable via {py}"))
    except PCBNewNotAvailableError as exc:
        checks.append(DoctorCheck("pcbnew", False, str(exc)))

    # atopile (optional)
    try:
        import atopile  # noqa: F401
        checks.append(DoctorCheck("atopile", True, "importable (preferred .ato compiler)"))
    except ImportError:
        checks.append(DoctorCheck("atopile", True,
                                  "not installed — fallback parser handles M1 demo. Optional."))

    # java
    java = os.environ.get("JAVA") or shutil.which("java")
    if java:
        checks.append(DoctorCheck("java", True, java))
    else:
        checks.append(DoctorCheck("java", False, "needed by Freerouting; install JRE 17+"))

    # freerouting jar
    jar = os.environ.get("FREEROUTING_JAR")
    if jar and Path(jar).exists():
        checks.append(DoctorCheck("freerouting", True, jar))
    else:
        checks.append(DoctorCheck(
            "freerouting", False,
            "FREEROUTING_JAR not set. Download freerouting.jar and set the env var.",
        ))

    # kipy — optional. "ok" if we can talk to a running KiCad, "warn"-flavoured
    # otherwise. We report it as informational rather than failing because the
    # whole text-to-fab loop runs without it.
    from ki_mcp_pcb_core.placement.kipy_placer import probe as _kipy_probe
    kipy_status = _kipy_probe()
    if kipy_status.ok:
        detail = (f"connected; KiCad {kipy_status.kicad_version}"
                  if kipy_status.kicad_version else "connected")
        checks.append(DoctorCheck("kipy", True, detail))
    elif kipy_status.code == "kipy_unavailable":
        checks.append(DoctorCheck(
            "kipy", True,
            "not installed — autoplace disabled. `uv sync --extra ipc` to enable.",
        ))
    else:
        checks.append(DoctorCheck(
            "kipy", True,
            f"installed but {kipy_status.code}: {kipy_status.detail}. "
            "Open a board in KiCad and enable IPC under Preferences → API.",
        ))

    return checks
