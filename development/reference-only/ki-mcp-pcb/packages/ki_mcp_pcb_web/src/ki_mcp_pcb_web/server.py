"""FastAPI server for the ki-mcp-pcb web viewer.

Thin HTTP wrapper around the core library. The HTML viewer in
``static/`` calls these endpoints via ``fetch``; nothing here duplicates
logic from the core package.

Run with ``uv run kimp serve`` or ``uv run ki-mcp-pcb-web``.
"""

from __future__ import annotations

import asyncio
import json
import queue
import uuid
from collections.abc import AsyncIterator
from dataclasses import asdict
from pathlib import Path
from typing import Any, cast

import yaml
from fastapi import FastAPI, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from ki_mcp_pcb_core import __version__ as core_version
from ki_mcp_pcb_core import pipeline
from ki_mcp_pcb_core.cir.models import CIR_VERSION, Board
from ki_mcp_pcb_core.cir.validation import ValidationIssue, validate_board
from ki_mcp_pcb_core.diff import diff_boards
from ki_mcp_pcb_core.export.bom import BOMRow, build_bom_rows
from ki_mcp_pcb_core.parsers.nl import (
    NLParserError,
    NLParserUnavailableError,
    parse_nl,
)
from ki_mcp_pcb_core.parsers.yaml import parse_yaml
from ki_mcp_pcb_core.sourcing import check_sourcing
from pydantic import BaseModel

from ki_mcp_pcb_web import agent, session

_STATIC_DIR = Path(__file__).resolve().parent / "static"

app = FastAPI(
    title="ki-mcp-pcb viewer",
    version="0.0.1",
    description="Browser viewer for the ki-mcp-pcb pipeline.",
)


# ---------------------------------------------------------------------------
# Health + meta
# ---------------------------------------------------------------------------


@app.get("/api/version")
def version_endpoint() -> dict[str, str]:
    return {
        "core_version": core_version,
        "cir_version": CIR_VERSION,
        "viewer_version": "0.0.1",
    }


# ---------------------------------------------------------------------------
# Parse + validate
# ---------------------------------------------------------------------------


def _parse_source(text: str, kind: str) -> Board:
    """Parse YAML or .ato content into a Board."""
    if kind == "ato":
        from ki_mcp_pcb_core.parsers.ato import parse_ato
        return parse_ato(text)
    return parse_yaml(text)


@app.post("/api/validate")
async def validate_endpoint(file: UploadFile) -> dict[str, Any]:
    """Validate an uploaded CIR YAML/.ato. Returns full board summary +
    validation report so the viewer can render everything from one call."""
    raw = (await file.read()).decode("utf-8", errors="replace")
    kind = "ato" if (file.filename or "").lower().endswith(".ato") else "yaml"
    try:
        board = _parse_source(raw, kind)
    except Exception as exc:
        raise HTTPException(400, detail=f"parse error: {exc}") from exc

    report = validate_board(board)
    bom_rows = build_bom_rows(board)
    sourcing = check_sourcing(board)
    return {
        "board": _board_summary(board),
        "validation": report.model_dump(),
        "bom": [r.model_dump() for r in bom_rows],
        "sourcing": [asdict(e) for e in sourcing.entries],
    }


def _board_summary(board: Board) -> dict[str, Any]:
    """A flat-but-rich shape that's friendly to render in HTML."""
    return {
        "cir_version": board.cir_version,
        "name": board.name,
        "description": board.description,
        "fab": board.fab.model_dump(),
        "stackup": {
            "layer_count": sum(1 for layer in board.stackup.layers if layer.kind == "copper"),
            "finished_thickness_mm": board.stackup.finished_thickness_mm,
            "power_plane_layers": board.stackup.power_plane_layers,
        },
        "components": [c.model_dump() for c in board.components],
        "nets": [n.model_dump() for n in board.nets],
        "constraints": [c.model_dump() for c in board.constraints],
        "signoff": board.signoff.model_dump(),
    }


# ---------------------------------------------------------------------------
# Working CIR file — GET/PUT the single working-session board
# ---------------------------------------------------------------------------


class CirText(BaseModel):
    """Request body for ``PUT /api/cir`` — the raw CIR YAML text."""

    text: str


class ValidationSummary(BaseModel):
    """Flattened validation outcome — the GUI gates on ``ok``."""

    ok: bool
    errors: int
    warnings: int
    issues: list[dict[str, Any]]


class CirState(BaseModel):
    """The working-CIR API payload: raw text plus parsed/validated state.

    ``board``/``bom``/``sourcing`` are passed through as loose JSON (the
    GUI renders them); ``parse_error`` / ``validation`` are what the GUI
    branches on, so they are precisely typed.
    """

    exists: bool
    text: str
    parse_error: str | None
    board: dict[str, Any] | None
    validation: ValidationSummary | None
    bom: list[BOMRow]
    sourcing: list[dict[str, Any]]


def _cir_state(text: str, *, exists: bool) -> CirState:
    """Build the CIR-state payload from raw YAML text.

    When the text parses, includes the board summary, validation report,
    BOM and sourcing; when it doesn't, ``parse_error`` carries the message
    and the parsed fields stay ``None``/empty.
    """
    try:
        board = parse_yaml(text)
    except Exception as exc:  # parser raises ValueError-family on bad input
        return CirState(
            exists=exists, text=text, parse_error=str(exc),
            board=None, validation=None, bom=[], sourcing=[],
        )
    report = validate_board(board)
    return CirState(
        exists=exists,
        text=text,
        parse_error=None,
        board=_board_summary(board),
        # ValidationReport exposes ok/errors/warnings as properties, not
        # model fields — flatten them so the GUI never derives pass/fail.
        validation=ValidationSummary(
            ok=report.ok,
            errors=len(report.errors),
            warnings=len(report.warnings),
            issues=[issue.model_dump() for issue in report.issues],
        ),
        bom=build_bom_rows(board),
        sourcing=[asdict(e) for e in check_sourcing(board).entries],
    )


@app.get("/api/cir")
def get_cir() -> CirState:
    """Return the working CIR file: its text plus parsed/validated state."""
    path = session.cir_path()
    if not path.exists():
        return CirState(
            exists=False, text="", parse_error=None,
            board=None, validation=None, bom=[], sourcing=[],
        )
    return _cir_state(path.read_text(encoding="utf-8"), exists=True)


def _board_to_yaml(board: Board) -> str:
    """Serialise a ``Board`` to canonical YAML.

    The round-trip ``parse_yaml(yaml) -> Board -> _board_to_yaml -> parse_yaml``
    yields an equal ``Board`` for every example CIR (verified before G3),
    so this is the form editor's authoritative writer — the Pydantic model
    stays the single source of truth, the GUI never owns YAML emission.
    """
    return cast(
        str,
        yaml.safe_dump(
            board.model_dump(mode="json"),
            sort_keys=False,
            allow_unicode=True,
        ),
    )


@app.put("/api/cir")
def put_cir(payload: CirText) -> CirState:
    """Replace the working CIR file.

    Rejects (400) text that won't parse — a syntactically broken file is
    never written to disk, so the working copy always stays loadable.
    """
    try:
        parse_yaml(payload.text)
    except Exception as exc:
        raise HTTPException(400, detail=f"parse error: {exc}") from exc
    session.cir_path().write_text(payload.text, encoding="utf-8")
    return _cir_state(payload.text, exists=True)


class SignoffPatch(BaseModel):
    """Partial sign-off update — only the fields the GUI sent get applied.

    SPEC-1 / CLAUDE.md: only a human may flip a ``Board.signoff.*`` flag.
    The GUI dispatches this PATCH directly (no agent path goes through
    here); the agent's only way to touch sign-off is to Write/Edit the CIR
    file, which is already gated by ``can_use_tool`` (G2).
    """

    rf_reviewed: bool | None = None
    ddr_reviewed: bool | None = None
    bga_fanout_reviewed: bool | None = None
    reviewer: str | None = None
    reviewed_at: str | None = None


@app.patch("/api/cir/signoff")
def patch_cir_signoff(payload: SignoffPatch) -> CirState:
    """Apply a partial sign-off update to the working CIR (SPEC-1 G4).

    Only fields the client explicitly sets are written; unset fields keep
    their on-disk values. Re-emits the canonical YAML through the same
    helper the form PUT uses so the round-trip stays byte-identical.
    """
    board = _load_working_board()
    patch = payload.model_dump(exclude_unset=True)
    new_signoff = board.signoff.model_copy(update=patch)
    new_board = board.model_copy(update={"signoff": new_signoff})
    text = _board_to_yaml(new_board)
    session.cir_path().write_text(text, encoding="utf-8")
    return _cir_state(text, exists=True)


@app.put("/api/cir/board")
def put_cir_board(board: Board) -> CirState:
    """Replace the working CIR from a structured ``Board`` JSON (SPEC-1 G3).

    The form editor sends a full ``Board`` object; FastAPI validates it via
    Pydantic, the canonical YAML is emitted and persisted, and the same
    ``CirState`` the text path returns is returned. A field-validation
    failure surfaces as 422 (FastAPI's standard validation error shape) so
    the form can highlight the offending fields.
    """
    text = _board_to_yaml(board)
    session.cir_path().write_text(text, encoding="utf-8")
    return _cir_state(text, exists=True)


# ---------------------------------------------------------------------------
# Workspace — persisted working-directory choice (SPEC-1 G4)
# ---------------------------------------------------------------------------


class WorkspaceState(BaseModel):
    """Where the working directory is right now + how it was chosen.

    ``source`` lets the GUI render the right control: a persisted choice
    can be replaced; the env-override case is read-only with a hint to
    unset ``KIMP_GUI_WORKDIR`` to regain GUI control.
    """

    path: str
    source: session.WorkspaceSource


class WorkspaceUpdate(BaseModel):
    """Request body for ``POST /api/workspace`` — the new absolute path."""

    path: str


@app.get("/api/workspace")
def get_workspace() -> WorkspaceState:
    """Return the working directory the backend is currently using."""
    return WorkspaceState(
        path=str(session.working_dir()), source=session.working_dir_source()
    )


@app.post("/api/workspace")
def set_workspace(payload: WorkspaceUpdate) -> WorkspaceState:
    """Persist a new working directory.

    The path must be **absolute** and refer to an existing directory; we
    reject relative paths and non-directory targets with 400 so the GUI
    can show a clear error rather than the launcher silently picking a
    nonsense default on the next start.
    """
    candidate = Path(payload.path)
    if not candidate.is_absolute():
        raise HTTPException(400, detail="workspace path must be absolute")
    resolved = candidate.expanduser().resolve()
    if not resolved.is_dir():
        raise HTTPException(
            400, detail=f"workspace path is not an existing directory: {resolved}"
        )
    session.write_persisted_workdir(resolved)
    return WorkspaceState(
        path=str(session.working_dir()), source=session.working_dir_source()
    )


# ---------------------------------------------------------------------------
# New-project-from-intent (SPEC-1 FR-5)
# ---------------------------------------------------------------------------


class IntentRequest(BaseModel):
    """Request body for ``POST /api/parse_intent``."""

    text: str


class ParseIntentResponse(BaseModel):
    """The intent→CIR draft — what the GUI's IntentDialog previews."""

    board: Board
    draft_yaml: str


@app.post("/api/parse_intent")
def parse_intent_endpoint(payload: IntentRequest) -> ParseIntentResponse:
    """Natural-language description → draft CIR (SPEC-1 FR-5).

    Wraps ``ki_mcp_pcb_core.parsers.nl.parse_nl``. The draft is *not*
    written to the working CIR — the GUI shows it to the user first; they
    accept by PUTting the text through ``/api/cir`` themselves. On a
    missing Anthropic SDK / API key the endpoint surfaces 503 with the
    structured detail so the dialog can show "configure Anthropic to use
    intent-to-CIR" rather than a generic 500.
    """
    text = payload.text.strip()
    if not text:
        raise HTTPException(400, detail="empty prompt")
    try:
        result = parse_nl(text)
    except NLParserUnavailableError as exc:
        raise HTTPException(503, detail=str(exc)) from exc
    except NLParserError as exc:
        raise HTTPException(400, detail=str(exc)) from exc
    return ParseIntentResponse(board=result.board, draft_yaml=result.draft_yaml)


# ---------------------------------------------------------------------------
# Pipeline — doctor, build, streamed build
# ---------------------------------------------------------------------------


class DoctorCheck(BaseModel):
    """One environment-health probe (kicad-cli, pcbnew, Freerouting, …)."""

    name: str
    ok: bool
    detail: str


class StageResult(BaseModel):
    """One pipeline stage's outcome."""

    name: str
    ok: bool
    detail: dict[str, Any]


class BuildResponse(BaseModel):
    """The full pipeline result — every stage plus the output directory."""

    ok: bool
    stages: list[StageResult]
    out_dir: str


class BuildRequest(BaseModel):
    """Request body for ``POST /api/build``."""

    run_route: bool = False


def _stage_result(stage: pipeline.BuildStageResult) -> StageResult:
    return StageResult(name=stage.name, ok=stage.ok, detail=stage.detail)


def _build_response(result: pipeline.BuildResult) -> BuildResponse:
    return BuildResponse(
        ok=result.ok,
        stages=[_stage_result(s) for s in result.stages],
        out_dir=str(result.out_dir),
    )


def _require_cir() -> Path:
    """Return the working CIR path, or raise 400 when there is none."""
    path = session.cir_path()
    if not path.exists():
        raise HTTPException(
            400, detail="no working CIR — save one via PUT /api/cir first"
        )
    return path


@app.get("/api/doctor")
def get_doctor() -> list[DoctorCheck]:
    """Environment health — which pipeline stages can run on this machine."""
    return [
        DoctorCheck(name=c.name, ok=c.ok, detail=c.detail)
        for c in pipeline.doctor()
    ]


@app.post("/api/build")
def post_build(payload: BuildRequest) -> BuildResponse:
    """Run the full pipeline on the working CIR; return the per-stage result."""
    source = _require_cir()
    result = pipeline.build(
        source, session.build_dir(), run_route=payload.run_route
    )
    return _build_response(result)


@app.get("/api/build/stream")
async def build_stream(run_route: bool = False) -> StreamingResponse:
    """Run the pipeline, streaming one SSE ``stage`` event per stage.

    The synchronous ``pipeline.build`` runs in a worker thread; its
    ``on_stage`` callback feeds a thread-safe queue the async generator
    drains. The stream ends with a ``done`` (or ``error``) event.
    """
    source = _require_cir()
    build_root = session.build_dir()

    async def events() -> AsyncIterator[str]:
        stage_queue: queue.Queue[tuple[str, Any]] = queue.Queue()

        def on_stage(stage: pipeline.BuildStageResult) -> None:
            stage_queue.put(("stage", _stage_result(stage).model_dump()))

        def run() -> None:
            try:
                result = pipeline.build(
                    source, build_root, run_route=run_route, on_stage=on_stage
                )
                stage_queue.put(("done", _build_response(result).model_dump()))
            except Exception as exc:  # surface any failure into the stream
                # Named `build_error`, not `error` — EventSource reserves
                # the bare `error` event for transport failures.
                stage_queue.put(("build_error", {"detail": str(exc)}))

        task = asyncio.create_task(asyncio.to_thread(run))
        try:
            while True:
                kind, payload = await asyncio.to_thread(stage_queue.get)
                yield f"event: {kind}\ndata: {json.dumps(payload)}\n\n"
                if kind in ("done", "build_error"):
                    break
        finally:
            await task

    return StreamingResponse(events(), media_type="text/event-stream")


# ---------------------------------------------------------------------------
# Build artifacts — list + download
# ---------------------------------------------------------------------------


class Artifact(BaseModel):
    """One generated file in the build directory."""

    path: str  # relative to the build directory — also the download id
    name: str  # basename
    size: int  # bytes


@app.get("/api/artifacts")
def list_artifacts() -> list[Artifact]:
    """List every generated file under the working build directory."""
    root = session.build_dir()
    artifacts: list[Artifact] = []
    for entry in sorted(root.rglob("*")):
        if entry.is_file():
            artifacts.append(Artifact(
                path=str(entry.relative_to(root)),
                name=entry.name,
                size=entry.stat().st_size,
            ))
    return artifacts


@app.get("/api/artifacts/{artifact_path:path}")
def get_artifact(artifact_path: str) -> FileResponse:
    """Download one build artifact.

    The resolved target must stay inside the build directory — a path that
    escapes it (``..`` traversal) is rejected with 404.
    """
    root = session.build_dir().resolve()
    target = (root / artifact_path).resolve()
    if not target.is_relative_to(root) or not target.is_file():
        raise HTTPException(404, detail="artifact not found")
    return FileResponse(target, filename=target.name)


# ---------------------------------------------------------------------------
# Claude co-pilot — agent chat WebSocket (SPEC-1 §6.5)
# ---------------------------------------------------------------------------
#
# Protocol (JSON messages over WS /api/agent):
#
#   client -> server
#     {"type": "prompt",   "text": <str>}                  — a user turn
#     {"type": "approval", "id": <str>, "decision": <str>}  — G2-T3 reply to
#                                                             an approval_request
#   server -> client
#     {"type": "text" | "thinking", "text": <str>}
#     {"type": "tool_use", "id", "name", "input"}
#     {"type": "tool_result", "tool_use_id", "content", "is_error"}
#     {"type": "done", "is_error", "result", "cost_usd"}
#     {"type": "approval_request", ...}    — G2-T3
#     {"type": "cir_changed"}              — G2-T6
#     {"type": "agent_unavailable" | "error", "detail": <str>}
#
# A turn and an approval never overlap: approvals are awaited *inside* a turn
# (the G2-T3 can_use_tool callback reads the socket directly), prompts only
# between turns. So this single receive loop is the whole bridge.


def _make_approval_gate(websocket: WebSocket) -> agent.ToolPermissionCallback:
    """Build the ``can_use_tool`` approval gate for one chat connection.

    Irreversible / outward-facing tool calls (SPEC-1 FR-16 — fab export, CIR
    file writes) are held: an ``approval_request`` is pushed to the GUI and
    the call blocks until the user replies with a matching ``approval``
    message. Everything else is auto-allowed. The gate runs backend-side, so
    the GUI cannot bypass it.
    """
    from claude_agent_sdk import PermissionResultAllow, PermissionResultDeny

    cir_filename = session.cir_path().name

    async def can_use_tool(
        tool_name: str, tool_input: dict[str, Any], context: Any
    ) -> Any:
        reason = agent.approval_reason(
            tool_name, tool_input, cir_filename=cir_filename
        )
        if reason is None:
            return PermissionResultAllow()

        request_id = uuid.uuid4().hex
        await websocket.send_json({
            "type": "approval_request",
            "id": request_id,
            "tool": tool_name,
            "input": tool_input,
            "reason": reason,
        })
        # The approval is awaited mid-turn; the only expected reply is the
        # matching `approval`. A stray message (e.g. an early prompt) gets an
        # error and the gate keeps waiting — a turn and an approval can't race.
        while True:
            message = await websocket.receive_json()
            if (
                message.get("type") == "approval"
                and message.get("id") == request_id
            ):
                if message.get("decision") == "allow":
                    return PermissionResultAllow()
                return PermissionResultDeny(
                    message="the user rejected this action in the GUI"
                )
            await websocket.send_json({
                "type": "error",
                "detail": "resolve the pending approval before sending more",
            })

    return can_use_tool


@app.websocket("/api/agent")
async def agent_ws(websocket: WebSocket) -> None:
    """Bidirectional Claude co-pilot chat.

    One ``AgentSession`` per connection: client ``prompt`` messages in, the
    agent's streamed events out. When the Claude Agent SDK or its
    credentials are absent, a single ``agent_unavailable`` event is sent and
    the socket closes — the pipeline GUI keeps working without the co-pilot.
    """
    await websocket.accept()
    if not agent.agent_available():
        await websocket.send_json({
            "type": "agent_unavailable",
            "detail": "the Claude Agent SDK is not installed — install "
            "ki-mcp-pcb-web[agent] to use the co-pilot",
        })
        await websocket.close()
        return

    can_use_tool = _make_approval_gate(websocket)

    # Constructing the session builds ClaudeAgentOptions eagerly and
    # connecting spawns the agent CLI — either can fail (bad env, missing
    # credentials). Both degrade to a structured agent_unavailable event.
    try:
        agent_session = agent.AgentSession(
            session.working_dir(), can_use_tool=can_use_tool
        )
        await agent_session.connect()
    except Exception as exc:
        await websocket.send_json({"type": "agent_unavailable", "detail": str(exc)})
        await websocket.close()
        return

    cir_filename = session.cir_path().name
    try:
        while True:
            message = await websocket.receive_json()
            if message.get("type") != "prompt":
                continue
            prompt = str(message.get("text", ""))
            # Tool-use ids of CIR writes seen this turn — when their result
            # lands clean, the GUI is told to reload the editor (SPEC-1 FR-17).
            cir_write_ids: set[str] = set()
            try:
                async for event in agent_session.send(prompt):
                    await websocket.send_json(event)
                    if event["type"] == "tool_use" and agent.is_cir_write(
                        event["name"], event["input"], cir_filename=cir_filename
                    ):
                        cir_write_ids.add(event["id"])
                    elif (
                        event["type"] == "tool_result"
                        and not event["is_error"]
                        and event["tool_use_id"] in cir_write_ids
                    ):
                        cir_write_ids.discard(event["tool_use_id"])
                        await websocket.send_json({"type": "cir_changed"})
            except Exception as exc:  # one turn failed — keep the session open
                await websocket.send_json({"type": "error", "detail": str(exc)})
    except WebSocketDisconnect:
        pass
    finally:
        await agent_session.aclose()


# ---------------------------------------------------------------------------
# Diff
# ---------------------------------------------------------------------------


class ComponentChangeRow(BaseModel):
    """One per-component field-level change row in a structured diff."""

    refdes: str
    field: str
    left: str | None
    right: str | None


class NetChangeRow(BaseModel):
    """One per-net field-level change row in a structured diff."""

    name: str
    field: str
    left: str
    right: str


class DiffResponse(BaseModel):
    """Structured CIR diff — shared by ``/api/diff`` and ``/api/diff/working``."""

    identical: bool
    summary: str
    name_changed: tuple[str, str] | None
    components_added: list[str]
    components_removed: list[str]
    component_changes: list[ComponentChangeRow]
    nets_added: list[str]
    nets_removed: list[str]
    net_changes: list[NetChangeRow]


class HighSpeedNet(BaseModel):
    """A net whose return path the CIR090 check applies to."""

    net: str
    net_class: str
    reference_plane: str | None


class DecouplingCheckResponse(BaseModel):
    """``/api/decoupling_check`` payload — CIR030 issues + GUI context."""

    ok: bool
    issues: list[ValidationIssue]
    ics_with_decoupling_declared: list[str]


class ReturnPathCheckResponse(BaseModel):
    """``/api/return_path_check`` payload — CIR090 issues + GUI context."""

    ok: bool
    issues: list[ValidationIssue]
    high_speed_nets: list[HighSpeedNet]


def _diff_response(d: Any) -> DiffResponse:
    """Serialise a ``BoardDiff`` to the typed shape both diff endpoints use."""
    return DiffResponse(
        identical=d.identical,
        summary=d.summary(),
        name_changed=d.name_changed,
        components_added=d.components_added,
        components_removed=d.components_removed,
        component_changes=[ComponentChangeRow(**asdict(c)) for c in d.component_changes],
        nets_added=d.nets_added,
        nets_removed=d.nets_removed,
        net_changes=[NetChangeRow(**asdict(c)) for c in d.net_changes],
    )


def _load_working_board() -> Board:
    """Return the working CIR parsed as a ``Board``, or raise 400.

    Shared by the result-pane endpoints (decoupling / return-path / diff
    against working): if there is no working CIR yet, fail closed with a
    helpful detail rather than a 500 from a stray FileNotFoundError.
    """
    path = session.cir_path()
    if not path.exists():
        raise HTTPException(
            400, detail="no working CIR — save one via PUT /api/cir first"
        )
    try:
        return parse_yaml(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise HTTPException(400, detail=f"parse error: {exc}") from exc


@app.post("/api/diff")
async def diff_endpoint(left: UploadFile, right: UploadFile) -> DiffResponse:
    left_raw = (await left.read()).decode("utf-8", errors="replace")
    right_raw = (await right.read()).decode("utf-8", errors="replace")
    left_kind = "ato" if (left.filename or "").lower().endswith(".ato") else "yaml"
    right_kind = "ato" if (right.filename or "").lower().endswith(".ato") else "yaml"
    try:
        left_board = _parse_source(left_raw, left_kind)
        right_board = _parse_source(right_raw, right_kind)
    except Exception as exc:
        raise HTTPException(400, detail=f"parse error: {exc}") from exc

    return _diff_response(diff_boards(left_board, right_board))


@app.post("/api/diff/working")
async def diff_working_endpoint(baseline: UploadFile) -> DiffResponse:
    """Diff an uploaded baseline against the working CIR (SPEC-1 G3-T2).

    The GUI's diff view always reads the right-hand side from the on-disk
    working CIR, so it only needs to upload the baseline. The baseline is
    treated as the *left* board (the "before"), the working CIR as the
    *right* (the "after").
    """
    baseline_raw = (await baseline.read()).decode("utf-8", errors="replace")
    kind = "ato" if (baseline.filename or "").lower().endswith(".ato") else "yaml"
    try:
        left_board = _parse_source(baseline_raw, kind)
    except Exception as exc:
        raise HTTPException(400, detail=f"parse error: {exc}") from exc

    right_board = _load_working_board()
    return _diff_response(diff_boards(left_board, right_board))


# ---------------------------------------------------------------------------
# Design-intent checks — surfaced for the G3 result panes
# ---------------------------------------------------------------------------


@app.get("/api/decoupling_check")
def decoupling_check_endpoint() -> DecouplingCheckResponse:
    """Decoupling-coverage check (CIR030) over the working CIR.

    Mirrors the MCP ``tool_decoupling_check`` shape: ``ok`` is False when
    any CIR030 issue is an error, plus the list of components that *did*
    declare ``decoupling_pins`` for the GUI to render alongside the issues.
    """
    board = _load_working_board()
    report = validate_board(board)
    issues = [i for i in report.issues if i.code == "CIR030"]
    return DecouplingCheckResponse(
        ok=not any(i.severity == "error" for i in issues),
        issues=issues,
        ics_with_decoupling_declared=[
            c.refdes for c in board.components if c.decoupling_pins
        ],
    )


@app.get("/api/return_path_check")
def return_path_check_endpoint() -> ReturnPathCheckResponse:
    """Return-path check (CIR090) over the working CIR.

    Mirrors the MCP ``tool_return_path_check`` shape and adds the list of
    nets whose return path the check applies to (high-speed / differential
    / RF, or any net that names a reference plane).
    """
    board = _load_working_board()
    report = validate_board(board)
    issues = [i for i in report.issues if i.code == "CIR090"]
    return ReturnPathCheckResponse(
        ok=not any(i.severity == "error" for i in issues),
        issues=issues,
        high_speed_nets=[
            HighSpeedNet(
                net=n.name,
                net_class=n.net_class,
                reference_plane=n.reference_plane,
            )
            for n in board.nets
            if n.net_class in {"high_speed", "differential", "rf"}
            or n.reference_plane is not None
        ],
    )


# ---------------------------------------------------------------------------
# Impedance + length tuning
# ---------------------------------------------------------------------------


class ImpedanceRow(BaseModel):
    """One net's target vs achieved impedance, plus the trace geometry."""

    net: str
    target_ohm: float
    achieved_ohm: float | None
    trace_width_mm: float | None
    trace_spacing_mm: float | None
    cpwg_gap_mm: float | None
    diff_pair_with: str | None


class ImpedanceResponse(BaseModel):
    """``/api/impedance`` / ``/api/impedance/working`` payload."""

    rows: list[ImpedanceRow]


def _impedance_rows(board: Board) -> list[ImpedanceRow]:
    """Compute per-net achievable Zo using the closed-form SI solvers."""
    from ki_mcp_pcb_core.signal_integrity import (
        differential_microstrip_impedance,
        geometry_for_net,
        grounded_cpwg_impedance,
        microstrip_impedance,
    )

    rows: list[ImpedanceRow] = []
    for net in board.nets:
        if net.target_impedance_ohm is None:
            continue
        geo = geometry_for_net(board, net)
        achieved: float | None = None
        if geo is not None:
            try:
                if net.cpwg_gap_mm is not None:
                    achieved = grounded_cpwg_impedance(geo)
                elif net.diff_pair_with:
                    achieved = differential_microstrip_impedance(geo)
                else:
                    achieved = microstrip_impedance(geo)
            except ValueError:
                achieved = None
        rows.append(
            ImpedanceRow(
                net=net.name,
                target_ohm=net.target_impedance_ohm,
                achieved_ohm=round(achieved, 2) if achieved is not None else None,
                trace_width_mm=net.trace_width_mm,
                trace_spacing_mm=net.trace_spacing_mm,
                cpwg_gap_mm=net.cpwg_gap_mm,
                diff_pair_with=net.diff_pair_with,
            )
        )
    return rows


@app.post("/api/impedance")
async def impedance_endpoint(file: UploadFile) -> ImpedanceResponse:
    """Per-net achievable Zo for every net with a target_impedance_ohm
    (file-upload form — the GUI uses ``/api/impedance/working`` instead)."""
    raw = (await file.read()).decode("utf-8", errors="replace")
    kind = "ato" if (file.filename or "").lower().endswith(".ato") else "yaml"
    try:
        board = _parse_source(raw, kind)
    except Exception as exc:
        raise HTTPException(400, detail=f"parse error: {exc}") from exc
    return ImpedanceResponse(rows=_impedance_rows(board))


@app.get("/api/impedance/working")
def impedance_working_endpoint() -> ImpedanceResponse:
    """Per-net achievable Zo for the working CIR (SPEC-1 G3 result pane)."""
    board = _load_working_board()
    return ImpedanceResponse(rows=_impedance_rows(board))


# ---------------------------------------------------------------------------
# Front-end
# ---------------------------------------------------------------------------


def _mount_frontend() -> None:
    """Mount the front-end — run last so ``/api/*`` and ``/static`` win.

    Serves the built GUI single-page app at ``/`` when it exists
    (``kimp serve`` → the real GUI). The legacy ``/static`` viewer stays
    mounted as a fallback front-end and for its own assets.
    """
    if _STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

    gui_dist = session.gui_dist_dir()
    if gui_dist is not None:
        app.mount(
            "/", StaticFiles(directory=str(gui_dist), html=True), name="gui"
        )
        return

    @app.get("/")
    def index() -> Any:
        index_html = _STATIC_DIR / "index.html"
        if not index_html.exists():
            return JSONResponse(
                {
                    "error": "front-end assets missing — build the GUI "
                    "(npm run build) or check the legacy viewer",
                },
                status_code=500,
            )
        return FileResponse(index_html)


_mount_frontend()


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------


def run(host: str = "127.0.0.1", port: int = 8765, reload: bool = False) -> None:
    """Boot the server (used by ``kimp serve`` and the package console script)."""
    import uvicorn  # local — only needed at runtime

    uvicorn.run(
        "ki_mcp_pcb_web.server:app",
        host=host,
        port=port,
        reload=reload,
        log_level="info",
    )


if __name__ == "__main__":  # pragma: no cover
    run()


# Re-export for convenience
__all__ = ["app", "run"]


def _unused_warn() -> None:
    # silence linter complaint about `json` import — it's used in the
    # static index template for tests
    _ = json
