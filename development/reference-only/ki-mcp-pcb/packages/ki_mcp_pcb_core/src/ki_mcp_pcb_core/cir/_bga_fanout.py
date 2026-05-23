"""BGA fanout template lookup.

Loads ``libs/bga_fanout.yaml`` and exposes a tiny API for the CIR110
validator. Lives next to the CIR validation logic on purpose — fanout
intent is part of design-intent, not signal-integrity math.
"""

from __future__ import annotations

import math
from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import BaseModel


class BGAFanoutTemplate(BaseModel):
    via_diameter_mm: float
    via_drill_mm: float
    escape_trace_width_mm: float
    escape_clearance_mm: float
    requires_hdi: bool = False
    notes: str = ""


_DEFAULT_REGISTRY_PATH = (
    Path(__file__).resolve().parents[5] / "libs" / "bga_fanout.yaml"
)


@lru_cache(maxsize=1)
def _load(path: Path = _DEFAULT_REGISTRY_PATH) -> dict[float, BGAFanoutTemplate]:
    if not path.exists():
        return {}
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return {float(pitch): BGAFanoutTemplate.model_validate(entry)
            for pitch, entry in raw.items()}


def bga_fanout_for_pitch(pitch_mm: float, *, tolerance_mm: float = 0.001) -> BGAFanoutTemplate | None:
    """Return the closest matching template within ``tolerance_mm``."""
    templates = _load()
    if not templates:
        return None
    best = min(templates, key=lambda p: abs(p - pitch_mm))
    if math.isclose(best, pitch_mm, abs_tol=tolerance_mm):
        return templates[best]
    return None
