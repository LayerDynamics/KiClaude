"""BOM export wrapper around `kicad-cli sch export bom` (M2-P-03).

The KiCad UI's BOM export ("File → Export → BOM…") and the CLI both
walk the schematic looking for symbol fields (Reference, Value,
Footprint, MPN, Manufacturer, Description, Datasheet) and group rows
by Value + Footprint.

The wrapper here produces TWO artifacts side-by-side:

- `bom.csv` — flat CSV with one row per Reference, columns in a stable
  order that matches the KiCad UI default preset.
- `bom-grouped.csv` — rows pre-grouped by `Value`+`Footprint` with a
  `Quantity` column. This is the artifact JLC's "BOM upload" page
  accepts directly.

IPC-2581 export is not yet stable in `kicad-cli` (it lives in
`pcbnew-cli` on some platforms); the M2-P-03 plan calls for it but
ships the CSV pair first so the M2 demo gate (`/pcb-fab jlcpcb`) can
land. The IPC-2581 path is a TODO documented in the plan, NOT a stub
returned by this module.
"""

from __future__ import annotations

import asyncio
import csv
import io
import shutil
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DEFAULT_TIMEOUT_S = 60.0

# The columns the KiCad UI's default preset emits. We pin them so the
# CSV is stable across kicad-cli upgrades.
DEFAULT_FIELDS: tuple[str, ...] = (
    "Reference",
    "Value",
    "Footprint",
    "MPN",
    "Manufacturer",
    "Description",
    "Datasheet",
    "Quantity",
)


@dataclass(slots=True)
class BomRow:
    """One row from the parsed BOM."""

    reference: str
    value: str
    footprint: str
    mpn: str = ""
    manufacturer: str = ""
    description: str = ""
    datasheet: str = ""
    quantity: int = 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "reference": self.reference,
            "value": self.value,
            "footprint": self.footprint,
            "mpn": self.mpn,
            "manufacturer": self.manufacturer,
            "description": self.description,
            "datasheet": self.datasheet,
            "quantity": self.quantity,
        }


@dataclass(slots=True)
class BomReport:
    """Result of a kicad-cli BOM run.

    `rows` carries the parsed flat rows (one per Reference). `csv_path`
    and `grouped_csv_path` point at the on-disk artifacts the caller
    can stream back.
    """

    ok: bool
    rows: list[BomRow] = field(default_factory=list)
    csv_path: str | None = None
    grouped_csv_path: str | None = None
    error: str | None = None
    duration_ms: int = 0
    exit_code: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "rows": [r.to_dict() for r in self.rows],
            "csv_path": self.csv_path,
            "grouped_csv_path": self.grouped_csv_path,
            "error": self.error,
            "duration_ms": self.duration_ms,
            "exit_code": self.exit_code,
        }


async def run_bom(
    sch_path: str | Path,
    output_dir: str | Path,
    *,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    kicad_cli_binary: str = "kicad-cli",
) -> BomReport:
    """Run `kicad-cli sch export bom` on `sch_path` and write
    `bom.csv` + `bom-grouped.csv` into `output_dir`.

    Returns a [`BomReport`] with the parsed flat rows.
    """
    started = asyncio.get_event_loop().time()

    target = Path(sch_path).expanduser()
    if not target.is_absolute():
        target = target.resolve()
    if not target.exists():
        return _err(f"schematic not found: {sch_path}", _duration(started))
    if target.suffix != ".kicad_sch":
        return _err(
            f"BOM target must be a .kicad_sch file, got {target.suffix or 'no extension'}",
            _duration(started),
        )

    out_dir = Path(output_dir).expanduser()
    if not out_dir.is_absolute():
        out_dir = out_dir.resolve()
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        return _err(f"cannot create output dir {out_dir}: {e}", _duration(started))

    binary = shutil.which(kicad_cli_binary)
    if binary is None:
        return _err(f"{kicad_cli_binary} not on PATH", _duration(started))

    csv_path = out_dir / "bom.csv"
    args = [
        "sch",
        "export",
        "bom",
        "--output",
        str(csv_path),
        "--preset",
        "Grouped By Value",
        "--fields",
        ",".join(DEFAULT_FIELDS),
        "--labels",
        ",".join(DEFAULT_FIELDS),
        "--group-by",
        "Value,Footprint",
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
    if exit_code != 0:
        stderr_text = (stderr or b"").decode("utf-8", errors="replace").strip()
        stdout_text = (stdout or b"").decode("utf-8", errors="replace").strip()
        msg = stderr_text or stdout_text or f"kicad-cli exited {exit_code}"
        return _err(msg, duration_ms, exit_code=exit_code)
    if not csv_path.exists():
        return _err(
            f"kicad-cli reported success but {csv_path} was not produced",
            duration_ms,
            exit_code=exit_code,
        )
    try:
        rows = parse_bom_csv(csv_path.read_text(encoding="utf-8"))
    except OSError as e:
        return _err(f"cannot read {csv_path}: {e}", duration_ms, exit_code=exit_code)
    grouped_path = out_dir / "bom-grouped.csv"
    grouped_path.write_text(
        format_grouped_csv(group_rows(rows)),
        encoding="utf-8",
    )
    return BomReport(
        ok=True,
        rows=rows,
        csv_path=str(csv_path),
        grouped_csv_path=str(grouped_path),
        error=None,
        duration_ms=duration_ms,
        exit_code=exit_code,
    )


def parse_bom_csv(text: str) -> list[BomRow]:
    """Parse a kicad-cli BOM CSV string into [`BomRow`] objects.

    Tolerates the two common shapes:

    - Flat: one row per Reference, no Quantity column.
    - Grouped (the preset above): one row per Value+Footprint pair with
      `Reference` holding a comma-separated list of refdes and a
      `Quantity` integer.

    Returns the rows un-fanned-out — i.e. grouped rows stay grouped,
    flat rows stay flat. The caller decides whether to re-fan-out by
    splitting References for per-component handling.
    """
    out: list[BomRow] = []
    reader = csv.DictReader(io.StringIO(text))
    for row in reader:
        ref = (row.get("Reference") or "").strip()
        if not ref:
            continue
        try:
            qty = int(row.get("Quantity") or 0)
        except ValueError:
            qty = 0
        if qty <= 0:
            # Flat rows: count refdes by splitting on comma. Pinheaders
            # and grouped rows alike land here when the preset omits
            # Quantity.
            qty = max(1, len([r for r in ref.split(",") if r.strip()]))
        out.append(
            BomRow(
                reference=ref,
                value=(row.get("Value") or "").strip(),
                footprint=(row.get("Footprint") or "").strip(),
                mpn=(row.get("MPN") or "").strip(),
                manufacturer=(row.get("Manufacturer") or "").strip(),
                description=(row.get("Description") or "").strip(),
                datasheet=(row.get("Datasheet") or "").strip(),
                quantity=qty,
            )
        )
    return out


def group_rows(rows: Sequence[BomRow]) -> list[BomRow]:
    """Collapse rows by `(value, footprint, mpn)` into grouped rows with
    comma-separated `Reference` and summed `Quantity`."""
    buckets: dict[tuple[str, str, str], BomRow] = {}
    for r in rows:
        key = (r.value, r.footprint, r.mpn)
        existing = buckets.get(key)
        if existing is None:
            # Clone so we can mutate freely.
            buckets[key] = BomRow(
                reference=r.reference,
                value=r.value,
                footprint=r.footprint,
                mpn=r.mpn,
                manufacturer=r.manufacturer,
                description=r.description,
                datasheet=r.datasheet,
                quantity=r.quantity,
            )
            continue
        existing.reference = ",".join(
            sorted(
                set(
                    s.strip()
                    for s in (existing.reference + "," + r.reference).split(",")
                    if s.strip()
                )
            )
        )
        existing.quantity += r.quantity
    return sorted(
        buckets.values(),
        key=lambda r: (r.value, r.footprint, r.reference),
    )


def format_grouped_csv(rows: Sequence[BomRow]) -> str:
    """Serialize grouped rows in JLC's accepted column order."""
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(DEFAULT_FIELDS)
    for r in rows:
        writer.writerow(
            [
                r.reference,
                r.value,
                r.footprint,
                r.mpn,
                r.manufacturer,
                r.description,
                r.datasheet,
                r.quantity,
            ]
        )
    return buf.getvalue()


# ---------------------------------------------------------------------
# Internal helpers.
# ---------------------------------------------------------------------


def _err(message: str, duration_ms: int, *, exit_code: int | None = None) -> BomReport:
    return BomReport(
        ok=False,
        rows=[],
        csv_path=None,
        grouped_csv_path=None,
        error=message,
        duration_ms=duration_ms,
        exit_code=exit_code,
    )


def _duration(started: float) -> int:
    delta = asyncio.get_event_loop().time() - started
    return max(0, int(delta * 1000.0))


__all__ = [
    "DEFAULT_FIELDS",
    "DEFAULT_TIMEOUT_S",
    "BomReport",
    "BomRow",
    "format_grouped_csv",
    "group_rows",
    "parse_bom_csv",
    "run_bom",
]
