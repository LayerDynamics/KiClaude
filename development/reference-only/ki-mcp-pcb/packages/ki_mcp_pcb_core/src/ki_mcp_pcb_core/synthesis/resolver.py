"""MPN → KiCad library resolver.

Enforces CLAUDE.md rule #6: every MPN must resolve. If a component's
``footprint``/``symbol`` aren't set explicitly and the MPN isn't in the
registry, ``resolve_components`` raises — synthesis fails closed.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

import yaml
from pydantic import BaseModel

from ki_mcp_pcb_core.cir.models import Component

# The default registry ships in the repo under ``libs/footprints.yaml``.
# Tests can override this with a per-call ``registry_path``.
#   resolver.py        parents[0] = synthesis/
#                      parents[1] = ki_mcp_pcb_core/
#                      parents[2] = src/
#                      parents[3] = ki_mcp_pcb_core/ (package)
#                      parents[4] = packages/
#                      parents[5] = repo root
_DEFAULT_REGISTRY_PATH = (
    Path(__file__).resolve().parents[5] / "libs" / "footprints.yaml"
)


class RegistryEntry(BaseModel):
    symbol: str
    footprint: str
    value: str | None = None
    lcsc: str | None = None


class UnresolvedMPNError(ValueError):
    """An MPN couldn't be resolved and no explicit footprint/symbol was set."""


@dataclass(frozen=True)
class ResolvedComponent:
    """A Component with library identifiers filled in."""

    refdes: str
    mpn: str
    symbol: str
    footprint: str
    value: str | None
    lcsc: str | None


def load_registry(path: Path | None = None) -> dict[str, RegistryEntry]:
    """Load the MPN → footprint registry from YAML."""
    path = path or _DEFAULT_REGISTRY_PATH
    if not path.exists():
        return {}
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return {mpn: RegistryEntry.model_validate(data) for mpn, data in raw.items()}


def resolve_component(
    component: Component, registry: dict[str, RegistryEntry]
) -> ResolvedComponent:
    """Resolve a single component. Raises ``UnresolvedMPNError`` on failure."""
    entry = registry.get(component.mpn)
    symbol = component.symbol or (entry.symbol if entry else None)
    footprint = component.footprint or (entry.footprint if entry else None)
    if symbol is None or footprint is None:
        raise UnresolvedMPNError(
            f"Cannot resolve MPN {component.mpn!r} for refdes {component.refdes!r}. "
            "Add it to libs/footprints.yaml or set explicit `symbol` + `footprint` "
            "on the component."
        )
    value = component.value or (entry.value if entry else None)
    lcsc = entry.lcsc if entry else None
    return ResolvedComponent(
        refdes=component.refdes,
        mpn=component.mpn,
        symbol=symbol,
        footprint=footprint,
        value=value,
        lcsc=lcsc,
    )


def resolve_components(
    components: Iterable[Component], *, registry_path: Path | None = None
) -> list[ResolvedComponent]:
    """Resolve a batch of components. Fails closed on first unresolved MPN."""
    registry = load_registry(registry_path)
    return [resolve_component(c, registry) for c in components]
