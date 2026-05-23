"""kimp — ki-mcp-pcb command-line interface.

Verbs mirror the MCP tool surface 1:1 (see SPEC.md §5.3). When you add
a CLI command, add the matching MCP tool in the same change.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Any

import typer
from ki_mcp_pcb_core import __version__ as core_version
from ki_mcp_pcb_core.cir.models import CIR_VERSION
from ki_mcp_pcb_core.cir.validation import validate_board
from ki_mcp_pcb_core.parsers.yaml import parse_yaml
from ki_mcp_pcb_core.pipeline import build as run_build
from ki_mcp_pcb_core.pipeline import doctor as run_doctor
from rich.console import Console
from rich.table import Table

app = typer.Typer(
    name="kimp",
    help="ki-mcp-pcb — plain text to manufacturable KiCad PCB.",
    no_args_is_help=True,
    add_completion=False,
)
console = Console()


@app.command()
def version() -> None:
    """Print version + CIR schema info."""
    console.print(f"kimp (ki-mcp-pcb-cli) — core v{core_version}, CIR v{CIR_VERSION}")


@app.command()
def validate(
    source: Annotated[Path, typer.Argument(help="Path to a CIR YAML or .ato file.")],
    json_out: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    """Run CIR-level structural validation on a board spec."""
    if source.suffix.lower() in {".yaml", ".yml"}:
        board = parse_yaml(source)
    elif source.suffix.lower() == ".ato":
        raise typer.Exit(_error("`.ato` parsing is M1 work. Use a YAML CIR for now."))
    else:
        raise typer.Exit(_error(f"Unknown source type: {source.suffix}"))

    report = validate_board(board)

    if json_out:
        console.print_json(json.dumps(report.model_dump()))
    else:
        _render_report(report)

    raise typer.Exit(code=0 if report.ok else 1)


@app.command()
def build(
    source: Annotated[Path, typer.Argument(help="CIR YAML or .ato file.")],
    out: Annotated[Path, typer.Option("--out", help="Output directory.")] = Path("build"),
    route: Annotated[bool, typer.Option("--route/--no-route",
                                          help="Run Freerouting after synthesis")] = False,
    json_out: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON")] = False,
) -> None:
    """Full pipeline: parse → validate → sourcing → synthesize → DRC → fab."""
    result = run_build(source, out, run_route=route)

    if json_out:
        payload = {
            "ok": result.ok,
            "out_dir": str(result.out_dir),
            "stages": [{"name": s.name, "ok": s.ok, "detail": s.detail} for s in result.stages],
        }
        console.print_json(json.dumps(payload))
    else:
        _render_build_report(result)

    raise typer.Exit(code=0 if result.ok else 1)


@app.command()
def route(
    pcb: Annotated[Path, typer.Argument(help="Placed .kicad_pcb file.")],
    router: Annotated[str, typer.Option(help="freerouting | kicad_native | manual")] = "freerouting",
) -> None:
    """Route a placed board via Freerouting (default)."""
    from ki_mcp_pcb_core.routing import RouterError
    from ki_mcp_pcb_core.routing import route as _route
    try:
        result = _route(pcb, router=router)  # type: ignore[arg-type]
    except RouterError as exc:
        raise typer.Exit(_error(str(exc))) from None
    except FileNotFoundError as exc:
        raise typer.Exit(_error(f"file not found: {exc}")) from None
    console.print(f"[green]routed[/green] {result.pcb_path}")


@app.command()
def fab(
    pcb: Annotated[Path, typer.Argument(help="Routed .kicad_pcb file.")],
    cir: Annotated[Path | None, typer.Option("--cir", help="Source CIR YAML/.ato for the board.")] = None,
    target: Annotated[str, typer.Option(help="jlcpcb | oshpark | pcbway | generic")] = "jlcpcb",
    out: Annotated[Path, typer.Option("--out", help="Output directory.")] = Path("fab"),
) -> None:
    """Produce a fab-house-specific zip of gerbers + drill + BOM + P&P."""
    if cir is None:
        raise typer.Exit(_error("`--cir <source.yaml|.ato>` is required for BOM generation"))
    from ki_mcp_pcb_core.export.fab_package import export_fab_package

    suffix = Path(cir).suffix.lower()
    if suffix in {".yaml", ".yml"}:
        board = parse_yaml(cir)
    else:
        from ki_mcp_pcb_core.parsers import parse_ato
        board = parse_ato(cir)

    pkg = export_fab_package(board, pcb, out, fab_target=target)
    console.print(f"[green]wrote[/green] {pkg.zip_path}")


@app.command()
def diff(
    left: Annotated[Path, typer.Argument(help="Left source (CIR YAML, .ato, or .kicad_pro).")],
    right: Annotated[Path, typer.Argument(help="Right source (CIR YAML, .ato, or .kicad_pro).")],
    json_out: Annotated[bool, typer.Option("--json", help="Emit JSON.")] = False,
) -> None:
    """Diff two CIR sources structurally."""
    from dataclasses import asdict

    from ki_mcp_pcb_core.diff import diff_boards

    left_board = _load_board_any(left)
    right_board = _load_board_any(right)
    result = diff_boards(left_board, right_board)

    if json_out:
        console.print_json(json.dumps(_diff_to_dict(result, asdict)))
    else:
        _render_diff(result)

    raise typer.Exit(code=0 if result.identical else 1)


def _load_board_any(path: Path) -> Any:
    """Load a CIR Board from YAML, .ato, or a KiCad project."""
    suffix = path.suffix.lower()
    if suffix in {".yaml", ".yml"}:
        return parse_yaml(path)
    if suffix == ".ato":
        from ki_mcp_pcb_core.parsers.ato import parse_ato
        return parse_ato(path)
    if suffix == ".kicad_pro":
        from ki_mcp_pcb_core.backends.kicad import KiCadBackend
        return KiCadBackend().read_project(path)
    raise typer.BadParameter(
        f"Unknown source type: {path.suffix!r}. Expected .yaml/.ato/.kicad_pro."
    )


def _diff_to_dict(diff: Any, asdict_fn: Any) -> dict[str, Any]:
    return {
        "identical": diff.identical,
        "summary": diff.summary(),
        "name_changed": diff.name_changed,
        "components_added": diff.components_added,
        "components_removed": diff.components_removed,
        "component_changes": [asdict_fn(c) for c in diff.component_changes],
        "nets_added": diff.nets_added,
        "nets_removed": diff.nets_removed,
        "net_changes": [asdict_fn(c) for c in diff.net_changes],
    }


def _render_diff(diff: Any) -> None:
    if diff.identical:
        console.print("[green]identical[/green]")
        return
    console.print(f"[bold]Summary:[/bold] {diff.summary()}")
    if diff.name_changed:
        console.print(f"  board name: {diff.name_changed[0]!r} → {diff.name_changed[1]!r}")
    for ref in diff.components_added:
        console.print(f"  [green]+[/green] component {ref}")
    for ref in diff.components_removed:
        console.print(f"  [red]-[/red] component {ref}")
    for c in diff.component_changes:
        console.print(f"  ~ {c.refdes}.{c.field}: {c.left!r} → {c.right!r}")
    for n in diff.nets_added:
        console.print(f"  [green]+[/green] net {n}")
    for n in diff.nets_removed:
        console.print(f"  [red]-[/red] net {n}")
    for c in diff.net_changes:
        console.print(f"  ~ net {c.name}.{c.field}: {c.left!r} → {c.right!r}")


@app.command()
def ask(
    prompt: Annotated[list[str], typer.Argument(help="Natural-language board description.")],
    draft: Annotated[Path, typer.Option("--draft", help="Where to write the YAML draft.")] = Path("draft.yaml"),
) -> None:
    """Convert a natural-language PCB description into a CIR YAML draft.

    Requires ``ANTHROPIC_API_KEY`` in your environment.
    """
    from ki_mcp_pcb_core.parsers.nl import (
        NLParserError,
        NLParserUnavailableError,
        parse_nl,
    )

    text = " ".join(prompt)
    try:
        result = parse_nl(text, draft_path=draft)
    except NLParserUnavailableError as exc:
        raise typer.Exit(_error(str(exc))) from None
    except NLParserError as exc:
        raise typer.Exit(_error(f"NL parser failed: {exc}")) from None

    console.print(f"[green]drafted[/green] {draft}")
    console.print(
        f"  → {len(result.board.components)} components, "
        f"{len(result.board.nets)} nets. Review the draft before running "
        "`kimp build`."
    )


@app.command()
def autoplace(
    source: Annotated[Path, typer.Argument(help="CIR YAML or .ato file to plan from.")],
    board_width_mm: Annotated[float, typer.Option(help="Target board width in mm.")] = 50.0,
    board_height_mm: Annotated[float, typer.Option(help="Target board height in mm.")] = 40.0,
    spacing_mm: Annotated[float, typer.Option(help="Grid spacing for non-hinted parts.")] = 15.0,
    json_out: Annotated[bool, typer.Option("--json", help="Emit JSON")] = False,
) -> None:
    """Push planned placements to the currently-open KiCad PCB via IPC.

    Requires the ``ipc`` extra (``uv sync --extra ipc``) and a running
    KiCad 9+ with the IPC API enabled and a PCB open. Per CLAUDE.md rule
    5, placement is hint-driven: ``Component.placement_hint`` carries the
    intent ("south edge", "centered", "within 2 mm of U1"), never raw
    coordinates.
    """
    from ki_mcp_pcb_core.placement.kipy_placer import autoplace_board

    if source.suffix.lower() in {".yaml", ".yml"}:
        board = parse_yaml(source)
    elif source.suffix.lower() == ".ato":
        from ki_mcp_pcb_core.parsers import parse_ato
        board = parse_ato(source)
    else:
        raise typer.Exit(_error(f"Unknown source type: {source.suffix}"))

    status = autoplace_board(
        board,
        board_width_mm=board_width_mm,
        board_height_mm=board_height_mm,
        spacing_mm=spacing_mm,
    )

    if json_out:
        console.print_json(json.dumps({
            "code": status.code,
            "detail": status.detail,
            "kicad_version": status.kicad_version,
            "moved": status.moved,
            "skipped": status.skipped,
        }))
        raise typer.Exit(code=0 if status.ok else 1)

    if status.ok:
        console.print(f"[green]autoplace[/green] moved {len(status.moved)} "
                      f"footprint(s); skipped {len(status.skipped)}.")
        if status.skipped:
            console.print(f"  skipped: {', '.join(status.skipped)}")
    else:
        console.print(f"[red]{status.code}[/red] {status.detail}")
    raise typer.Exit(code=0 if status.ok else 1)


@app.command()
def serve(
    host: Annotated[str, typer.Option(help="Bind host.")] = "127.0.0.1",
    port: Annotated[int, typer.Option(help="Bind port.")] = 8765,
    reload: Annotated[bool, typer.Option("--reload", help="Auto-reload on edit.")] = False,
) -> None:
    """Boot the web viewer (FastAPI + KiCanvas).

    Requires the ``web`` extra: ``uv sync --extra web``.
    """
    try:
        from ki_mcp_pcb_web.server import run as _serve
    except ImportError as exc:
        raise typer.Exit(_error(
            "ki-mcp-pcb-web isn't installed. Run `uv sync --extra web`."
        )) from exc
    console.print(f"[green]serving[/green] http://{host}:{port}/")
    _serve(host=host, port=port, reload=reload)


@app.command()
def doctor(
    json_out: Annotated[bool, typer.Option("--json", help="Emit JSON")] = False,
) -> None:
    """Diagnose the local environment for missing tools."""
    checks = run_doctor()
    if json_out:
        console.print_json(json.dumps([{"name": c.name, "ok": c.ok, "detail": c.detail}
                                       for c in checks]))
        ok = all(c.ok for c in checks)
        raise typer.Exit(code=0 if ok else 1)

    table = Table(title="kimp doctor", show_lines=False)
    table.add_column("tool")
    table.add_column("status")
    table.add_column("detail")
    for c in checks:
        marker = "[green]✓[/green]" if c.ok else "[red]✗[/red]"
        table.add_row(c.name, marker, c.detail)
    console.print(table)
    raise typer.Exit(code=0 if all(c.ok for c in checks) else 1)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _error(msg: str) -> int:
    console.print(f"[bold red]error:[/bold red] {msg}")
    return 1


def _render_build_report(result: object) -> None:
    """Render a pipeline.BuildResult as a Rich table."""
    table = Table(title="kimp build", show_lines=False)
    table.add_column("stage")
    table.add_column("status")
    table.add_column("detail")
    for stage in getattr(result, "stages", []):
        if stage.detail.get("skipped"):
            marker = "[yellow]skip[/yellow]"
            detail = stage.detail.get("reason", "")
        elif stage.ok:
            marker = "[green]ok[/green]"
            detail = ", ".join(
                f"{k}={v}" for k, v in stage.detail.items()
                if k in {"components", "nets", "errors", "warnings", "zip"}
            )
        else:
            marker = "[red]fail[/red]"
            detail = stage.detail.get("error") or ", ".join(
                f"{k}={v}" for k, v in stage.detail.items() if k != "issues"
            )
        table.add_row(stage.name, marker, detail)
    console.print(table)
    console.print(f"output: {getattr(result, 'out_dir', '?')}")


def _render_report(report: object) -> None:
    table = Table(title="CIR validation", show_lines=False)
    table.add_column("severity")
    table.add_column("code")
    table.add_column("where")
    table.add_column("message")
    for issue in getattr(report, "issues", []):
        sev = issue.severity
        color = {"error": "red", "warning": "yellow", "info": "cyan"}.get(sev, "white")
        table.add_row(
            f"[{color}]{sev}[/{color}]",
            issue.code,
            issue.where or "",
            issue.message,
        )
    if not getattr(report, "issues", []):
        console.print("[green]ok[/green] — no issues")
    else:
        console.print(table)


if __name__ == "__main__":  # pragma: no cover
    app()
