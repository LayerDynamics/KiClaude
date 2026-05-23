"""kiconnector FastAPI app — port :8084.

Endpoints:

M0-P-05:
- `GET /health` → `{ok: true, service: "kiconnector", version}`.
- `GET /tools/versions` → `{kicad_cli, freerouting, kikit}` per-tool
  `--version` output (or `"not installed"`). 5 s per-tool timeout.

M1-P-03:
- `POST /tools/erc` — schematic ERC via `kicad-cli sch erc`.

M2-P-01:
- `POST /tools/drc` — PCB DRC via `kicad-cli pcb drc`.

M2-P-02:
- `POST /tools/gerbers` — `kicad-cli pcb export gerbers`.
- `POST /tools/drill` — `kicad-cli pcb export drill`.
- `POST /tools/pos` — `kicad-cli pcb export pos`.

M2-P-03:
- `POST /tools/bom` — `kicad-cli sch export bom`.

M3-P-09:
- `POST /tools/step` — `kicad-cli pcb export step` → `<board>.step`.

Every fab endpoint returns a structured envelope with `ok` distinguishing
"ran clean" from "kicad-cli unavailable or timed out".
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

import structlog
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from kiconnector import __version__
from kiconnector.bom import DEFAULT_TIMEOUT_S as BOM_TIMEOUT_S
from kiconnector.bom import run_bom
from kiconnector.drc import DEFAULT_TIMEOUT_S as DRC_TIMEOUT_S
from kiconnector.drc import run_drc
from kiconnector.erc import DEFAULT_TIMEOUT_S, run_erc
from kiconnector.export import (
    DEFAULT_GERBER_LAYERS,
    export_drill,
    export_step,
    export_gerbers,
    export_pos,
)
from kiconnector.export import (
    DEFAULT_TIMEOUT_S as EXPORT_TIMEOUT_S,
)
from kiconnector.freerouting import DEFAULT_TIMEOUT_S as FREEROUTING_TIMEOUT_S
from kiconnector.freerouting import run_freerouting
from kiconnector.kikit import DEFAULT_TIMEOUT_S as KIKIT_TIMEOUT_S
from kiconnector.kikit import run_panelize
from kiconnector.subprocess import probe_freerouting_jar, probe_version

log = structlog.get_logger(__name__)

app = FastAPI(
    title="kiclaude-kiconnector",
    version=__version__,
    description="Subprocess broker for kicad-cli / freerouting / kikit.",
)


class ErcRequest(BaseModel):
    """Body for `POST /tools/erc`."""

    project_path: str = Field(..., min_length=1, max_length=4_096)
    timeout_s: float = Field(default=DEFAULT_TIMEOUT_S, ge=1.0, le=300.0)


class DrcRequest(BaseModel):
    """Body for `POST /tools/drc`."""

    pcb_path: str = Field(..., min_length=1, max_length=4_096)
    timeout_s: float = Field(default=DRC_TIMEOUT_S, ge=1.0, le=600.0)
    severity_min: str = Field(default="warning", pattern="^(error|warning|exclusion|ignore)$")


class GerbersRequest(BaseModel):
    """Body for `POST /tools/gerbers`."""

    pcb_path: str = Field(..., min_length=1, max_length=4_096)
    output_dir: str = Field(..., min_length=1, max_length=4_096)
    layers: list[str] | None = Field(default=None)
    timeout_s: float = Field(default=EXPORT_TIMEOUT_S, ge=1.0, le=900.0)


class DrillRequest(BaseModel):
    """Body for `POST /tools/drill`."""

    pcb_path: str = Field(..., min_length=1, max_length=4_096)
    output_dir: str = Field(..., min_length=1, max_length=4_096)
    timeout_s: float = Field(default=EXPORT_TIMEOUT_S, ge=1.0, le=900.0)


class PosRequest(BaseModel):
    """Body for `POST /tools/pos`."""

    pcb_path: str = Field(..., min_length=1, max_length=4_096)
    output_dir: str = Field(..., min_length=1, max_length=4_096)
    side: str = Field(default="both", pattern="^(front|back|both)$")
    timeout_s: float = Field(default=EXPORT_TIMEOUT_S, ge=1.0, le=900.0)


class BomRequest(BaseModel):
    """Body for `POST /tools/bom`."""

    sch_path: str = Field(..., min_length=1, max_length=4_096)
    output_dir: str = Field(..., min_length=1, max_length=4_096)
    timeout_s: float = Field(default=BOM_TIMEOUT_S, ge=1.0, le=600.0)


class StepRequest(BaseModel):
    """Body for `POST /tools/step` (M3-P-09)."""

    pcb_path: str = Field(..., min_length=1, max_length=4_096)
    output_dir: str = Field(..., min_length=1, max_length=4_096)
    timeout_s: float = Field(default=EXPORT_TIMEOUT_S, ge=1.0, le=900.0)
    no_dnp: bool = True
    no_unspecified: bool = False
    subst_models: bool = True
    board_only: bool = False


class FreeroutingRequest(BaseModel):
    """Body for `POST /tools/freerouting`."""

    pcb_path: str = Field(..., min_length=1, max_length=4_096)
    freerouting_jar: str | None = Field(default=None, max_length=4_096)
    passes: int = Field(default=1, ge=1, le=200)
    timeout_s: float = Field(default=FREEROUTING_TIMEOUT_S, ge=10.0, le=3_600.0)


class PanelizeRequest(BaseModel):
    """Body for `POST /tools/panelize`."""

    pcb_path: str = Field(..., min_length=1, max_length=4_096)
    output_path: str = Field(..., min_length=1, max_length=4_096)
    config: dict[str, Any] | None = Field(default=None)
    preset_path: str | None = Field(default=None, max_length=4_096)
    timeout_s: float = Field(default=KIKIT_TIMEOUT_S, ge=1.0, le=1_800.0)


@app.get("/health")
async def health() -> dict[str, Any]:
    """Liveness probe."""
    return {"ok": True, "service": "kiconnector", "version": __version__}


@app.get("/tools/versions")
async def tools_versions() -> dict[str, str]:
    """Concurrent version probe for the three external tools kiclaude
    drives. All run in parallel under a 5 s per-tool timeout.
    """
    freerouting_jar = os.environ.get("KICLAUDE_FREEROUTING_JAR", "")
    kicad_cli, freerouting, kikit = await asyncio.gather(
        probe_version("kicad-cli"),
        probe_freerouting_jar(freerouting_jar or None),
        probe_version("kikit"),
    )
    payload = {
        "kicad_cli": kicad_cli.version,
        "freerouting": freerouting.version,
        "kikit": kikit.version,
    }
    log.info("tools_versions", **payload)
    return payload


@app.post("/tools/erc")
async def tools_erc(req: ErcRequest) -> dict[str, Any]:
    """Run `kicad-cli sch erc --format json` on `req.project_path`
    and return the parsed violations list (FR-014).

    Always returns a JSON envelope — even on tool failure — with `ok`
    distinguishing the "ran clean" path from "kicad-cli unavailable
    or timed out".
    """
    report = await run_erc(req.project_path, timeout_s=req.timeout_s)
    log.info(
        "erc_run",
        project_path=req.project_path,
        ok=report.ok,
        issues=len(report.issues),
        exit_code=report.exit_code,
        duration_ms=report.duration_ms,
    )
    if not report.ok and report.error and "not on PATH" in report.error:
        # Surface "kicad-cli not installed" as 503 so the gateway can
        # report the dependency gap without forcing every UI to
        # special-case the body.
        raise HTTPException(
            status_code=503,
            detail=report.error,
        )
    return report.to_dict()


@app.post("/tools/drc")
async def tools_drc(req: DrcRequest) -> dict[str, Any]:
    """Run `kicad-cli pcb drc --format json` and return the parsed
    violations (FR-025, spec D8 — kicad-cli is source of truth)."""
    report = await run_drc(
        req.pcb_path,
        timeout_s=req.timeout_s,
        severity_min=req.severity_min,
    )
    log.info(
        "drc_run",
        pcb_path=req.pcb_path,
        ok=report.ok,
        issues=len(report.issues),
        exit_code=report.exit_code,
        duration_ms=report.duration_ms,
    )
    if not report.ok and report.error and "not on PATH" in report.error:
        raise HTTPException(status_code=503, detail=report.error)
    return report.to_dict()


@app.post("/tools/gerbers")
async def tools_gerbers(req: GerbersRequest) -> dict[str, Any]:
    """Run `kicad-cli pcb export gerbers` and return the produced
    files (FR-030)."""
    layers = tuple(req.layers) if req.layers else DEFAULT_GERBER_LAYERS
    artifact = await export_gerbers(
        req.pcb_path,
        req.output_dir,
        layers=layers,
        timeout_s=req.timeout_s,
    )
    log.info(
        "gerbers_export",
        pcb_path=req.pcb_path,
        output_dir=req.output_dir,
        ok=artifact.ok,
        files=len(artifact.files),
        exit_code=artifact.exit_code,
        duration_ms=artifact.duration_ms,
    )
    if not artifact.ok and artifact.error and "not on PATH" in artifact.error:
        raise HTTPException(status_code=503, detail=artifact.error)
    return artifact.to_dict()


@app.post("/tools/drill")
async def tools_drill(req: DrillRequest) -> dict[str, Any]:
    """Run `kicad-cli pcb export drill` (Excellon mm) (FR-030)."""
    artifact = await export_drill(
        req.pcb_path,
        req.output_dir,
        timeout_s=req.timeout_s,
    )
    log.info(
        "drill_export",
        pcb_path=req.pcb_path,
        output_dir=req.output_dir,
        ok=artifact.ok,
        files=len(artifact.files),
        exit_code=artifact.exit_code,
        duration_ms=artifact.duration_ms,
    )
    if not artifact.ok and artifact.error and "not on PATH" in artifact.error:
        raise HTTPException(status_code=503, detail=artifact.error)
    return artifact.to_dict()


@app.post("/tools/pos")
async def tools_pos(req: PosRequest) -> dict[str, Any]:
    """Run `kicad-cli pcb export pos` and return the CSV positions
    (FR-032)."""
    artifact = await export_pos(
        req.pcb_path,
        req.output_dir,
        side=req.side,
        timeout_s=req.timeout_s,
    )
    log.info(
        "pos_export",
        pcb_path=req.pcb_path,
        output_dir=req.output_dir,
        side=req.side,
        ok=artifact.ok,
        files=len(artifact.files),
        exit_code=artifact.exit_code,
        duration_ms=artifact.duration_ms,
    )
    if not artifact.ok and artifact.error and "not on PATH" in artifact.error:
        raise HTTPException(status_code=503, detail=artifact.error)
    return artifact.to_dict()


@app.post("/tools/step")
async def tools_step(req: StepRequest) -> dict[str, Any]:
    """Run `kicad-cli pcb export step` and return the produced
    `.step` file (M3-P-09). Drives the M3-T-06 `kithree` 3D viewer
    + the M3-R-06 STEP-placement scene builder."""
    artifact = await export_step(
        req.pcb_path,
        req.output_dir,
        timeout_s=req.timeout_s,
        no_dnp=req.no_dnp,
        no_unspecified=req.no_unspecified,
        subst_models=req.subst_models,
        board_only=req.board_only,
    )
    log.info(
        "step_export",
        pcb_path=req.pcb_path,
        output_dir=req.output_dir,
        no_dnp=req.no_dnp,
        board_only=req.board_only,
        ok=artifact.ok,
        files=len(artifact.files),
        exit_code=artifact.exit_code,
        duration_ms=artifact.duration_ms,
    )
    if not artifact.ok and artifact.error and "not on PATH" in artifact.error:
        raise HTTPException(status_code=503, detail=artifact.error)
    return artifact.to_dict()


@app.post("/tools/freerouting")
async def tools_freerouting(req: FreeroutingRequest) -> dict[str, Any]:
    """Run the DSN → Freerouting → SES round-trip and import the
    routed result back into the PCB (FR-027, NFR-009)."""
    result = await run_freerouting(
        req.pcb_path,
        freerouting_jar=req.freerouting_jar,
        passes=req.passes,
        timeout_s=req.timeout_s,
    )
    log.info(
        "freerouting_run",
        pcb_path=req.pcb_path,
        passes=req.passes,
        ok=result.ok,
        exit_code=result.exit_code,
        duration_ms=result.duration_ms,
    )
    if not result.ok and result.error and "not on PATH" in result.error:
        raise HTTPException(status_code=503, detail=result.error)
    return result.to_dict()


@app.post("/tools/panelize")
async def tools_panelize(req: PanelizeRequest) -> dict[str, Any]:
    """Run `kikit panelize` against a preset / inline config and emit
    the panel `.kicad_pcb` (FR-035)."""
    result = await run_panelize(
        req.pcb_path,
        req.output_path,
        config=req.config,
        preset_path=req.preset_path,
        timeout_s=req.timeout_s,
    )
    log.info(
        "panelize_run",
        pcb_path=req.pcb_path,
        output_path=req.output_path,
        ok=result.ok,
        exit_code=result.exit_code,
        duration_ms=result.duration_ms,
    )
    if not result.ok and result.error and "not on PATH" in result.error:
        raise HTTPException(status_code=503, detail=result.error)
    return result.to_dict()


@app.post("/tools/bom")
async def tools_bom(req: BomRequest) -> dict[str, Any]:
    """Run `kicad-cli sch export bom` and return both flat + grouped
    CSV paths plus parsed rows (FR-031)."""
    report = await run_bom(
        req.sch_path,
        req.output_dir,
        timeout_s=req.timeout_s,
    )
    log.info(
        "bom_export",
        sch_path=req.sch_path,
        output_dir=req.output_dir,
        ok=report.ok,
        rows=len(report.rows),
        exit_code=report.exit_code,
        duration_ms=report.duration_ms,
    )
    if not report.ok and report.error and "not on PATH" in report.error:
        raise HTTPException(status_code=503, detail=report.error)
    return report.to_dict()


__all__ = ["app"]
