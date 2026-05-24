"""kiserver FastAPI app — port :8083.

Endpoints (M0-P-04):

- `GET /health` → `{ok: true, service: "kiserver", version, native: <bool>}`
  where `native` reflects whether `ki_native` is importable.
- `POST /project/open` body `{path}` → `{ok, project_id, summary}`
  populated via PyO3-loaded `ki_native.open_project`. Path is resolved
  relative to the current working directory of the kiserver process.
- `GET /project/{project_id}` → full KCIR `Project` dict.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import structlog
from fastapi import FastAPI, HTTPException
from kc_mcp.tools.snapshot import (
    get_snapshot_meta,
    get_snapshot_project,
    list_snapshots,
    record_snapshot,
)
from kc_mcp.ui_tools import UI_TOOLS
from pydantic import BaseModel, Field

from kiserver import __version__
from kiserver.project import REGISTRY
from kiserver.telemetry import tracer

log = structlog.get_logger(__name__)


app = FastAPI(
    title="kiclaude-kiserver",
    version=__version__,
    description="FastAPI surface over the PyO3 ki_native crate.",
)


def _ki_native_available() -> bool:
    try:
        import ki_native  # noqa: F401  # type: ignore[import-not-found]

        return True
    except ImportError:
        return False


class OpenRequest(BaseModel):
    path: str = Field(..., min_length=1, max_length=4_096)
    # FR-050 view selection: which slices of the KCIR `Project` the
    # caller wants back. Defaults to the compact summary; the full
    # project remains fetchable via `GET /project/{id}`.
    view: list[str] = Field(default_factory=lambda: ["summary"], max_length=10)


class SaveRequest(BaseModel):
    """Body for `POST /project/{id}/save`.

    `target_dir` may be omitted to save back to the directory the
    project was opened from. When provided, the path is resolved
    relative to the kiserver process and must already exist.
    """

    target_dir: str | None = Field(default=None, max_length=4_096)


class ReplaceRequest(BaseModel):
    """Body for `POST /project/{id}/replace` — swap the stored KCIR
    project dict for a (typically mutated) one."""

    project: dict[str, Any]


class SnapshotCreateRequest(BaseModel):
    """Body for `POST /project/{id}/snapshot/create`. Used by the
    M1-T-08 auto-snapshot path so the journal has a 'before' state
    to revert to."""

    label: str = Field(default="auto", max_length=120)
    snapshot_id: str | None = Field(default=None, max_length=64)


class SnapshotRevertRequest(BaseModel):
    """Body for `POST /project/{id}/snapshot/revert`. Restores the
    KCIR to a previously recorded snapshot."""

    snapshot_id: str = Field(..., min_length=1, max_length=64)


class UiInvokeRequest(BaseModel):
    """Body for `POST /project/{id}/ui/{tool}` — kwargs for a
    UI-only tool. The kiserver dispatches to the matching function
    in [`kc_mcp.ui_tools`][kc_mcp.ui_tools]."""

    args: dict[str, Any] = Field(default_factory=dict)


@app.get("/health")
async def health() -> dict[str, Any]:
    """Liveness probe. The `native` field tells the gateway whether
    PyO3 actually wired up — without it, `/project/open` returns 503."""
    return {
        "ok": True,
        "service": "kiserver",
        "version": __version__,
        "native": _ki_native_available(),
    }


@app.post("/project/open")
async def project_open(req: OpenRequest) -> dict[str, Any]:
    """Open a `KiCad` project directory via `ki_native.open_project`
    and register it under a fresh UUID4 in the in-memory registry.
    """
    if not _ki_native_available():
        raise HTTPException(
            status_code=503,
            detail=(
                "ki_native is not installed — run "
                "`maturin develop --features python` in crates/ki/."
            ),
        )
    import ki_native  # type: ignore[import-not-found]

    target = Path(req.path).expanduser().resolve()
    if not target.exists():
        raise HTTPException(status_code=404, detail=f"path not found: {req.path}")
    if not target.is_dir():
        raise HTTPException(status_code=400, detail=f"not a directory: {req.path}")

    try:
        project: dict[str, Any] = ki_native.open_project(str(target))
    except ValueError as e:
        # ki_native maps `OpenError` to ValueError; surface as 400.
        raise HTTPException(status_code=400, detail=str(e)) from e

    opened = REGISTRY.insert(project, target)
    log.info("project_opened", project_id=opened.project_id, name=opened.summary["name"])
    payload: dict[str, Any] = {
        "ok": True,
        "project_id": opened.project_id,
    }
    if "summary" in req.view:
        payload["summary"] = opened.summary
    if "pcb" in req.view:
        payload["pcb"] = opened.project.get("pcb", {})
    if "schematic" in req.view:
        payload["schematic"] = opened.project.get("schematic", {})
    if "metadata" in req.view:
        payload["metadata"] = opened.project.get("metadata", {})
    if "full" in req.view:
        payload["project"] = opened.project
    return payload


@app.post("/project/{project_id}/ui/{tool_name}")
async def project_ui_tool(
    project_id: str, tool_name: str, req: UiInvokeRequest
) -> dict[str, Any]:
    """Invoke a `ui_*` tool against a stored project (M1-P-05).

    These tools are coordinate-driven and intentionally NOT exposed
    via the MCP server. The gateway proxies `POST /api/ui/<tool>` →
    `POST /project/{id}/ui/<tool>` here so the frontend keeps its
    direct-coordinate surface without leaking into Claude's tool
    registry.
    """
    tool_fn = UI_TOOLS.get(tool_name)
    if tool_fn is None:
        raise HTTPException(
            status_code=404,
            detail=f"unknown ui tool: {tool_name}; "
            f"choose from {sorted(UI_TOOLS.keys())}",
        )
    opened = REGISTRY.get(project_id)
    if opened is None:
        raise HTTPException(status_code=404, detail=f"unknown project_id: {project_id}")
    # Tools accept `position_mm` as a 2-tuple; tolerate list input
    # from JSON callers.
    args = dict(req.args)
    if "position_mm" in args and isinstance(args["position_mm"], list):
        pos = args["position_mm"]
        args["position_mm"] = (
            float(pos[0]) if len(pos) > 0 else 0.0,
            float(pos[1]) if len(pos) > 1 else 0.0,
        )
    project_copy = dict(opened.project)
    try:
        result = tool_fn(project_copy, **args)
    except TypeError as e:
        raise HTTPException(status_code=400, detail=f"bad args for {tool_name}: {e}") from e
    if not isinstance(result, dict):
        raise HTTPException(status_code=500, detail=f"{tool_name} did not return a dict")
    if not result.get("ok", False):
        # The tool ran but reported a domain error — surface as 400
        # so the UI shows the message rather than a generic 200.
        raise HTTPException(status_code=400, detail=result.get("error", "tool error"))
    mutated = result.pop("project", project_copy)
    REGISTRY.replace(project_id, mutated)
    log.info(
        "ui_tool_invoked",
        project_id=project_id,
        tool=tool_name,
    )
    return {"ok": True, **result}


@app.post("/project/{project_id}/snapshot/create")
async def project_snapshot_create(
    project_id: str, req: SnapshotCreateRequest
) -> dict[str, Any]:
    """Record an auto-snapshot of the current KCIR. M1-T-08 uses this
    inside the agent's permission hook so every mutating tool call has
    a recoverable 'before' state without a kc_snapshot_create round-trip
    through Claude."""
    import uuid as _uuid

    opened = REGISTRY.get(project_id)
    if opened is None:
        raise HTTPException(status_code=404, detail=f"unknown project_id: {project_id}")
    snapshot_id = req.snapshot_id or str(_uuid.uuid4())
    ts = record_snapshot(project_id, snapshot_id, req.label, opened.project)
    return {
        "ok": True,
        "project_id": project_id,
        "snapshot_id": snapshot_id,
        "label": req.label,
        "ts": ts,
    }


@app.post("/project/{project_id}/snapshot/revert")
async def project_snapshot_revert(
    project_id: str, req: SnapshotRevertRequest
) -> dict[str, Any]:
    """Restore the project to a previously recorded snapshot.

    The journal's per-row revert button (FR-056) calls
    `POST /api/snapshot/revert/<project_id>` on the gateway which
    forwards here. Returns the snapshot label + timestamp so the UI
    can show "reverted to <label>".
    """
    opened = REGISTRY.get(project_id)
    if opened is None:
        raise HTTPException(status_code=404, detail=f"unknown project_id: {project_id}")
    project = get_snapshot_project(project_id, req.snapshot_id)
    if project is None:
        raise HTTPException(
            status_code=404,
            detail=f"unknown snapshot_id: {req.snapshot_id} for project {project_id}",
        )
    meta = get_snapshot_meta(project_id, req.snapshot_id) or {}
    with tracer.start_as_current_span("kiserver.project.snapshot_revert") as span:
        span.set_attribute("project_id", project_id)
        span.set_attribute("snapshot_id", req.snapshot_id)
        REGISTRY.replace(project_id, project)
        span.set_attribute("reverted_to_label", str(meta.get("label", "")))
    log.info(
        "project_reverted",
        project_id=project_id,
        snapshot_id=req.snapshot_id,
        label=meta.get("label"),
    )
    return {
        "ok": True,
        "project_id": project_id,
        "snapshot_id": req.snapshot_id,
        "reverted_to_label": meta.get("label"),
        "reverted_to_ts": meta.get("ts"),
    }


@app.get("/project/{project_id}/snapshots")
async def project_snapshots(project_id: str) -> dict[str, Any]:
    """List recorded snapshots for the project (without KCIR payloads
    so the response stays lightweight). The journal calls this on
    mount to show pre-existing history when a user reloads the page."""
    if REGISTRY.get(project_id) is None:
        raise HTTPException(status_code=404, detail=f"unknown project_id: {project_id}")
    return {
        "ok": True,
        "project_id": project_id,
        "snapshots": list_snapshots(project_id),
    }


@app.get("/project/{project_id}/dfm/check")
async def project_dfm_check(
    project_id: str, target: str = "generic"
) -> dict[str, Any]:
    """Pre-flight DFM dry-run for the M2-T-09 fab export dialog.

    Runs `dfm.run_dfm_check` against the in-memory project for the
    chosen board-house preset. Returns
    `{ok, target, issues:[...], counts:{error, warning}}`. The
    `ok` flag is `True` iff there are no error-severity findings —
    the export dialog gates the `Export` button on it.
    """
    from kiserver.dfm import known_targets, run_dfm_check

    opened = REGISTRY.get(project_id)
    if opened is None:
        raise HTTPException(
            status_code=404, detail=f"unknown project_id: {project_id}"
        )
    target_lower = (target or "generic").lower()
    if target_lower not in known_targets():
        raise HTTPException(
            status_code=400,
            detail=(
                f"unknown fab target {target!r}; "
                f"choose from {known_targets()}"
            ),
        )
    return run_dfm_check(opened.project, target_lower)


@app.get("/dfm/targets")
async def dfm_targets() -> dict[str, Any]:
    """Enumerate the supported fab targets so the M2-T-09 dialog
    can render the selector without hard-coding the list."""
    from kiserver.dfm import known_targets

    return {"ok": True, "targets": known_targets()}


@app.post("/project/{project_id}/replace")
async def project_replace(project_id: str, req: ReplaceRequest) -> dict[str, Any]:
    """Swap the stored KCIR for a (typically mutated) one.

    Mutating MCP tools call this after applying an edit so the
    in-memory project the gateway / UI reads stays in sync.
    """
    replaced = REGISTRY.replace(project_id, req.project)
    if replaced is None:
        raise HTTPException(status_code=404, detail=f"unknown project_id: {project_id}")
    log.info(
        "project_replaced",
        project_id=replaced.project_id,
        name=replaced.summary["name"],
    )
    return {
        "ok": True,
        "project_id": replaced.project_id,
        "summary": replaced.summary,
    }


@app.post("/project/{project_id}/save")
async def project_save(project_id: str, req: SaveRequest | None = None) -> dict[str, Any]:
    """Write a previously-opened project back to disk.

    Idempotent: writing the same KCIR twice produces byte-identical
    files. Emits a `kiserver.project.save` OpenTelemetry span with
    `project_id`, `target_dir`, and the list of files written
    (FR-001, FR-003).
    """
    opened = REGISTRY.get(project_id)
    if opened is None:
        raise HTTPException(status_code=404, detail=f"unknown project_id: {project_id}")
    if not _ki_native_available():
        raise HTTPException(
            status_code=503,
            detail=(
                "ki_native is not installed — run "
                "`maturin develop --features python` in crates/ki/."
            ),
        )
    import ki_native  # type: ignore[import-not-found]

    target_str = (req.target_dir if req is not None else None) or str(opened.path)
    target = Path(target_str).expanduser().resolve()
    if not target.exists():
        raise HTTPException(status_code=404, detail=f"target_dir not found: {target_str}")
    if not target.is_dir():
        raise HTTPException(status_code=400, detail=f"target_dir is not a directory: {target_str}")

    with tracer.start_as_current_span("kiserver.project.save") as span:
        span.set_attribute("project_id", opened.project_id)
        span.set_attribute("target_dir", str(target))
        try:
            written: list[str] = ki_native.save_project(opened.project, str(target))
        except ValueError as e:
            span.record_exception(e)
            raise HTTPException(status_code=400, detail=str(e)) from e
        except OSError as e:
            span.record_exception(e)
            raise HTTPException(status_code=500, detail=str(e)) from e
        span.set_attribute("files_written", len(written))
    log.info("project_saved", project_id=opened.project_id, files=written)
    return {
        "ok": True,
        "project_id": opened.project_id,
        "target_dir": str(target),
        "written": written,
    }


@app.get("/project/{project_id}")
async def project_get(project_id: str) -> dict[str, Any]:
    """Return the full KCIR `Project` dict for a previously opened
    project_id."""
    opened = REGISTRY.get(project_id)
    if opened is None:
        raise HTTPException(status_code=404, detail=f"unknown project_id: {project_id}")
    return {
        "ok": True,
        "project_id": opened.project_id,
        "path": str(opened.path),
        "project": opened.project,
        "summary": opened.summary,
    }


# ----------------------------------------------------------------
# M3-T-08 — BOM pricing endpoint. The React `BomView` panel hits
# this via the gateway. It walks the project's footprints, groups
# by MPN with summed quantity, and runs the M3-P-05 aggregator
# (Digi-Key today via M3-P-03; Mouser / Octopart / JLCPCB plug
# in via the same `DistributorAdapter` ABC as M3-P-01/02/04 land).
# ----------------------------------------------------------------


def _bom_lines_from_project(project: dict[str, Any]) -> list[tuple[str, int]]:
    """Sum every footprint's `mpn` into a `[(mpn, qty)]` list.
    Footprints without an MPN are skipped (they're un-sourced parts
    the user hasn't filled in yet — the BOM panel still shows them
    elsewhere but they don't contribute to pricing)."""
    pcb = project.get("pcb") or {}
    counts: dict[str, int] = {}
    for fp in pcb.get("footprints") or []:
        mpn_raw = fp.get("mpn")
        if not isinstance(mpn_raw, str):
            continue
        mpn = mpn_raw.strip()
        if not mpn:
            continue
        counts[mpn] = counts.get(mpn, 0) + 1
    return sorted(counts.items(), key=lambda pair: pair[0])


@app.get("/project/{project_id}/bom/price")
async def project_bom_price(
    project_id: str,
    force_refresh: bool = False,
    qty_multiplier: int = 1,
) -> dict[str, Any]:
    """Price every MPN on the project's BOM via the M3-P-05
    `PriceAggregator`. `qty_multiplier` scales each line — set to
    100 to price 100 boards in one shot.

    Returns:
    ```
    {
      ok: True,
      project_id: ...,
      bom_lines: [{mpn, qty, refdes_count}],
      pricing: { parts: [...], distributor_totals_usd, grand_total_usd, missing_mpns, errors },
    }
    ```
    """
    from kc_mcp.distributors import build_default_aggregator

    opened = REGISTRY.get(project_id)
    if opened is None:
        raise HTTPException(status_code=404, detail=f"unknown project_id: {project_id}")
    if qty_multiplier < 1:
        raise HTTPException(status_code=400, detail="qty_multiplier must be >= 1")

    lines = _bom_lines_from_project(opened.project)
    scaled = [(mpn, count * qty_multiplier) for (mpn, count) in lines]

    aggregator = build_default_aggregator()
    try:
        bom_pricing = await aggregator.price_bom(scaled, force_refresh=force_refresh)
    finally:
        await aggregator.aclose()

    def _pricing_payload() -> dict[str, Any]:
        parts: list[dict[str, Any]] = []
        for part in bom_pricing.parts:
            cheapest = part.cheapest
            parts.append(
                {
                    "mpn": part.mpn,
                    "requested_qty": part.requested_qty,
                    "cheapest": (
                        {
                            "distributor": cheapest.distributor,
                            "distributor_sku": cheapest.distributor_sku,
                            "manufacturer": cheapest.manufacturer,
                            "description": cheapest.description,
                            "in_stock_qty": cheapest.in_stock_qty,
                            "lifecycle": cheapest.lifecycle,
                            "product_url": cheapest.product_url,
                            "unit_price_usd": part.cheapest_unit_price_usd,
                        }
                        if cheapest is not None
                        else None
                    ),
                    "line_total_usd": part.line_total_usd,
                    "errors": dict(part.errors),
                    "quote_count": len(part.quotes),
                }
            )
        return {
            "parts": parts,
            "distributor_totals_usd": dict(bom_pricing.distributor_totals_usd),
            "grand_total_usd": bom_pricing.grand_total_usd,
            "missing_mpns": list(bom_pricing.missing_mpns),
            "errors": {k: list(v) for k, v in bom_pricing.errors.items()},
        }

    return {
        "ok": True,
        "project_id": project_id,
        "bom_lines": [
            {"mpn": mpn, "qty": count * qty_multiplier, "refdes_count": count}
            for (mpn, count) in lines
        ],
        "pricing": _pricing_payload(),
    }


__all__ = ["app"]
