"""CIR schema migrations.

When ``CIR_VERSION`` bumps, add a function here that takes the old dict
and returns a dict in the new shape. ``upgrade(data)`` walks the chain.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ki_mcp_pcb_core.cir.models import CIR_VERSION


def _migrate_0_1_to_0_2(data: dict[str, Any]) -> dict[str, Any]:
    """0.1 → 0.2 is purely additive (new fields default to empty/None).

    No data needs rewriting; we just stamp the version forward so the
    document parses under the M2 schema.
    """
    data = dict(data)
    data["cir_version"] = "0.2"
    return data


def _migrate_0_2_to_0_3(data: dict[str, Any]) -> dict[str, Any]:
    """0.2 → 0.3 is additive: ``diff_pair_with`` and ``reference_plane``
    on Net. Stamp the version forward."""
    data = dict(data)
    data["cir_version"] = "0.3"
    return data


def _migrate_0_3_to_0_4(data: dict[str, Any]) -> dict[str, Any]:
    """0.3 → 0.4 is additive: DDR fly-by topology fields on Net, BGA
    pitch on Component, board-level Signoff. Stamp the version forward."""
    data = dict(data)
    data["cir_version"] = "0.4"
    return data


# Map "from_version" -> migration function. Each fn must set cir_version
# to the next version on return.
_MIGRATIONS: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
    "0.1": _migrate_0_1_to_0_2,
    "0.2": _migrate_0_2_to_0_3,
    "0.3": _migrate_0_3_to_0_4,
}


def upgrade(data: dict[str, Any]) -> dict[str, Any]:
    """Walk migrations until ``data`` is at ``CIR_VERSION``."""
    while data.get("cir_version") != CIR_VERSION:
        version = data.get("cir_version", "0.0")
        migration = _MIGRATIONS.get(version)
        if migration is None:
            raise ValueError(
                f"No migration path from CIR {version} to {CIR_VERSION}. "
                "Edit ki_mcp_pcb_core.cir.migrations to add one."
            )
        data = migration(data)
    return data
