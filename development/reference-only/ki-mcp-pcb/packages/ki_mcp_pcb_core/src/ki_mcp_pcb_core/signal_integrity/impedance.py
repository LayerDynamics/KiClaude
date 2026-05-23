"""Closed-form impedance solvers.

The formulas below are the Hammerstad / IPC-2141 standard forms. They
are good to roughly 5–10% over the parameter ranges typical for
JLC-class manufacturing — close enough for design intent. For
fabrication-grade impedance control the fab does their own field-solver
run against the actual finished stackup.

References:
  - IPC-2141A "Controlled Impedance Circuit Boards and High Speed Logic"
  - Hammerstad & Jensen, "Accurate Models for Microstrip Computer-Aided
    Design", IEEE MTT-S Digest, 1980.

All dimensions are in **mm**. Permittivity εr is dimensionless. Returned
impedance is in **Ω**.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from ki_mcp_pcb_core.cir.models import Board, Layer, Net


@dataclass(frozen=True)
class StackupGeometry:
    """The geometry an impedance calculation needs.

    For a microstrip trace:
      * ``trace_width_mm``     — w
      * ``trace_thickness_mm`` — t (copper weight)
      * ``dielectric_height_mm`` — h, distance from trace to reference plane
      * ``er``                 — dielectric relative permittivity

    For a stripline trace the relevant ``h`` is the plane-to-plane spacing
    and the trace sits at its midpoint.

    For differential pairs, ``trace_spacing_mm`` is the edge-to-edge gap.

    For CPWG (grounded coplanar waveguide), ``cpwg_gap_mm`` is the
    trace-to-side-ground gap and the bottom reference plane lives at
    ``dielectric_height_mm`` below.
    """

    trace_width_mm: float
    trace_thickness_mm: float
    dielectric_height_mm: float
    er: float
    trace_spacing_mm: float | None = None  # only set for diff pairs
    cpwg_gap_mm: float | None = None  # only set for CPWG


# ---------------------------------------------------------------------------
# Single-ended impedance — IPC-2141 / Hammerstad
# ---------------------------------------------------------------------------


def microstrip_impedance(geo: StackupGeometry) -> float:
    """Characteristic impedance of a surface (microstrip) trace.

    Hammerstad approximation, IPC-2141A eq. 4-2:

        Zo = 87 / sqrt(εr + 1.41) * ln( 5.98·h / (0.8·w + t) )

    Valid for 0.1 ≤ w/h ≤ 2.0 and εr ≤ 15. Returns Ω.
    """
    if geo.dielectric_height_mm <= 0 or geo.trace_width_mm <= 0:
        raise ValueError("dielectric_height_mm and trace_width_mm must be > 0")
    h = geo.dielectric_height_mm
    w = geo.trace_width_mm
    t = geo.trace_thickness_mm
    er = geo.er
    return (87.0 / math.sqrt(er + 1.41)) * math.log(5.98 * h / (0.8 * w + t))


def stripline_impedance(geo: StackupGeometry) -> float:
    """Characteristic impedance of an embedded (stripline) trace.

    IPC-2141A eq. 4-9:

        Zo = (60 / sqrt(εr)) * ln( 4·b / (0.67·π·(0.8·w + t)) )

    where b is the plane-to-plane spacing. We use ``dielectric_height_mm``
    as that spacing for a centered stripline.
    """
    if geo.dielectric_height_mm <= 0 or geo.trace_width_mm <= 0:
        raise ValueError("dielectric_height_mm and trace_width_mm must be > 0")
    b = geo.dielectric_height_mm
    w = geo.trace_width_mm
    t = geo.trace_thickness_mm
    er = geo.er
    return (60.0 / math.sqrt(er)) * math.log((4.0 * b) / (0.67 * math.pi * (0.8 * w + t)))


# ---------------------------------------------------------------------------
# Differential impedance — approximations
# ---------------------------------------------------------------------------


def differential_microstrip_impedance(geo: StackupGeometry) -> float:
    """Differential impedance for an edge-coupled microstrip pair.

    Common engineering approximation (IPC-2141 / Polar):

        Zdiff = 2·Zo·(1 - 0.48·exp(-0.96·s/h))

    where s is the edge-to-edge spacing. ``Zo`` is the single-ended
    microstrip impedance for one trace alone.
    """
    if geo.trace_spacing_mm is None:
        raise ValueError("differential calculation requires trace_spacing_mm")
    zo = microstrip_impedance(geo)
    s = geo.trace_spacing_mm
    h = geo.dielectric_height_mm
    return 2.0 * zo * (1.0 - 0.48 * math.exp(-0.96 * s / h))


def differential_stripline_impedance(geo: StackupGeometry) -> float:
    """Differential impedance for an edge-coupled stripline pair.

        Zdiff = 2·Zo·(1 - 0.347·exp(-2.9·s/b))

    where b is the plane-to-plane spacing and s the edge-to-edge gap.
    """
    if geo.trace_spacing_mm is None:
        raise ValueError("differential calculation requires trace_spacing_mm")
    zo = stripline_impedance(geo)
    s = geo.trace_spacing_mm
    b = geo.dielectric_height_mm
    return 2.0 * zo * (1.0 - 0.347 * math.exp(-2.9 * s / b))


# ---------------------------------------------------------------------------
# Grounded CPWG (M4) — RF traces with side-ground pours + bottom plane
# ---------------------------------------------------------------------------


def _ellipk_over_ellipk_complement(k: float) -> float:
    """Ratio K(k) / K(k') of complete elliptic integrals of the first kind.

    Hilberg (1969) piecewise closed-form, accurate to ~3 ppm:

        For 0 ≤ k ≤ 1/√2:
            K(k') / K(k) = (1/π) · ln( 2·(1+√k') / (1-√k') )
          → K(k) / K(k') = π / ln( 2·(1+√k') / (1-√k') )

        For 1/√2 ≤ k ≤ 1:
            K(k) / K(k') = (1/π) · ln( 2·(1+√k) / (1-√k) )

    where k' = sqrt(1-k²).
    """
    if not 0 <= k <= 1:
        raise ValueError("k must be in [0, 1]")
    k_prime = math.sqrt(max(0.0, 1.0 - k * k))
    if k <= 1.0 / math.sqrt(2.0):
        sqrt_kp = math.sqrt(k_prime)
        # ln(2(1+√k')/(1-√k')) is K(k')/K(k) · π; invert for K(k)/K(k')
        return math.pi / math.log(2.0 * (1.0 + sqrt_kp) / max(1e-12, 1.0 - sqrt_kp))
    sqrt_k = math.sqrt(k)
    return (1.0 / math.pi) * math.log(2.0 * (1.0 + sqrt_k) / max(1e-12, 1.0 - sqrt_k))


def grounded_cpwg_impedance(geo: StackupGeometry) -> float:
    """Characteristic impedance of a grounded coplanar waveguide (CPWG).

    Wadell / Wen closed-form approximation:

        Z₀ = (60·π / sqrt(εeff)) · 1 / ( K(k)/K(k') + K(k₁)/K(k₁') )

        k   = w / (w + 2g)
        k₁  = tanh(π·w / 4h) / tanh(π·(w + 2g) / 4h)
        εeff = (εr · K(k')/K(k) + K(k₁')/K(k₁)) /
               (K(k')/K(k) + K(k₁')/K(k₁))

    where w is trace width, g is trace-to-ground gap, h is dielectric
    height to the bottom plane. Good to ~5% for typical RF geometries.
    """
    if geo.cpwg_gap_mm is None:
        raise ValueError("CPWG calculation requires cpwg_gap_mm")
    if geo.trace_width_mm <= 0 or geo.dielectric_height_mm <= 0 or geo.cpwg_gap_mm <= 0:
        raise ValueError("trace_width_mm, dielectric_height_mm, cpwg_gap_mm must be > 0")

    w = geo.trace_width_mm
    g = geo.cpwg_gap_mm
    h = geo.dielectric_height_mm
    er = geo.er

    k = w / (w + 2 * g)
    k1 = math.tanh(math.pi * w / (4 * h)) / math.tanh(math.pi * (w + 2 * g) / (4 * h))

    k_prime = math.sqrt(max(0.0, 1.0 - k * k))
    k1_prime = math.sqrt(max(0.0, 1.0 - k1 * k1))

    # Note: _ellipk_over_ellipk_complement(k) returns K(k)/K(k').
    # We need K(k')/K(k) for εeff and K(k)/K(k') for the Z₀ denominator.
    ratio_k = _ellipk_over_ellipk_complement(k)        # K(k) / K(k')
    ratio_k1 = _ellipk_over_ellipk_complement(k1)      # K(k₁) / K(k₁')
    ratio_kp = 1.0 / ratio_k                            # K(k') / K(k)
    ratio_k1p = 1.0 / ratio_k1                          # K(k₁') / K(k₁)
    # Suppress unused-variable lint — k_prime / k1_prime are documented above
    # for reviewer reference but not used directly (the ratios already
    # encapsulate the complementary moduli).
    _ = (k_prime, k1_prime)

    eps_eff = (er * ratio_kp + ratio_k1p) / (ratio_kp + ratio_k1p)
    z0 = (60.0 * math.pi / math.sqrt(eps_eff)) / (ratio_k + ratio_k1)
    return z0


# ---------------------------------------------------------------------------
# CIR ↔ geometry resolver
# ---------------------------------------------------------------------------


# Conservative default trace geometry for JLCPCB 2-layer/4-layer at 5 mil
# minimum. Real designs override these per net.
_DEFAULT_TRACE_WIDTH_MM = 0.150  # ~6 mil
_DEFAULT_TRACE_THICKNESS_MM = 0.035  # 1 oz copper
_DEFAULT_DIFF_SPACING_MM = 0.150  # 6 mil edge-to-edge


def geometry_for_net(board: Board, net: Net) -> StackupGeometry | None:
    """Infer the geometry to use for ``net`` from the board's stackup.

    Strategy:
      * If the net declares a ``reference_plane``, find the dielectric
        adjacent to that plane and use its thickness + εr.
      * Otherwise, use the first dielectric in the stackup.

    Returns ``None`` if no dielectric is available (e.g. a stackup with
    no dielectric layers — unusual).
    """
    layers = board.stackup.layers
    dielectric = _adjacent_dielectric(layers, net.reference_plane) if net.reference_plane else None
    if dielectric is None:
        dielectric = next((layer for layer in layers if layer.kind == "dielectric"), None)
    if dielectric is None or dielectric.er is None:
        return None
    return StackupGeometry(
        trace_width_mm=net.trace_width_mm or _DEFAULT_TRACE_WIDTH_MM,
        trace_thickness_mm=_DEFAULT_TRACE_THICKNESS_MM,
        dielectric_height_mm=dielectric.thickness_mm or 0.21,
        er=dielectric.er,
        trace_spacing_mm=(
            net.trace_spacing_mm or _DEFAULT_DIFF_SPACING_MM
        ) if net.diff_pair_with else None,
        cpwg_gap_mm=net.cpwg_gap_mm,
    )


def _adjacent_dielectric(layers: list[Layer], plane_name: str) -> Layer | None:
    for i, layer in enumerate(layers):
        if layer.name == plane_name and layer.kind == "copper":
            for nb_idx in (i - 1, i + 1):
                if 0 <= nb_idx < len(layers) and layers[nb_idx].kind == "dielectric":
                    return layers[nb_idx]
    return None
