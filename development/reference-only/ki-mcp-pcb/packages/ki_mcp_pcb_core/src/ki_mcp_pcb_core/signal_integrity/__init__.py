"""Signal-integrity primitives (M3).

  - :mod:`ki_mcp_pcb_core.signal_integrity.impedance` — closed-form
    impedance solvers for microstrip / stripline / differential pairs.
  - :mod:`ki_mcp_pcb_core.signal_integrity.length_tuning` — post-route
    length-match analyzer (tuning queue).
"""

from ki_mcp_pcb_core.signal_integrity.impedance import (
    StackupGeometry,
    differential_microstrip_impedance,
    differential_stripline_impedance,
    geometry_for_net,
    grounded_cpwg_impedance,
    microstrip_impedance,
    stripline_impedance,
)
from ki_mcp_pcb_core.signal_integrity.length_tuning import (
    GroupReport,
    Measurement,
    TuningAction,
    TuningReport,
    analyze_tuning,
    parse_measurements,
)

__all__ = [
    "GroupReport",
    "Measurement",
    "StackupGeometry",
    "TuningAction",
    "TuningReport",
    "analyze_tuning",
    "differential_microstrip_impedance",
    "differential_stripline_impedance",
    "geometry_for_net",
    "grounded_cpwg_impedance",
    "microstrip_impedance",
    "parse_measurements",
    "stripline_impedance",
]
