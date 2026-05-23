"""Tests for the M3 impedance solver.

We verify the math against the IPC-2141 sanity ranges + a few known
reference values. Tolerances are loose (±10% on values) because the
closed-form approximations have known error bands.
"""

from __future__ import annotations

import math

import pytest
from ki_mcp_pcb_core.signal_integrity import (
    StackupGeometry,
    differential_microstrip_impedance,
    differential_stripline_impedance,
    microstrip_impedance,
    stripline_impedance,
)


def test_microstrip_50ohm_reference() -> None:
    """A wide microstrip on thin prepreg should land near 50 Ω.

    w=0.50mm, h=0.21mm, εr=4.3, t=0.035mm → about 41 Ω
    (Hammerstad approximation; real solvers give 39–43 Ω range.)
    """
    g = StackupGeometry(trace_width_mm=0.50, trace_thickness_mm=0.035,
                        dielectric_height_mm=0.21, er=4.3)
    z = microstrip_impedance(g)
    assert 35 < z < 50, z


def test_microstrip_monotonic_in_width() -> None:
    """Wider trace → lower impedance for fixed dielectric."""
    base = StackupGeometry(trace_width_mm=0.15, trace_thickness_mm=0.035,
                           dielectric_height_mm=0.21, er=4.3)
    wider = StackupGeometry(trace_width_mm=0.30, trace_thickness_mm=0.035,
                            dielectric_height_mm=0.21, er=4.3)
    assert microstrip_impedance(wider) < microstrip_impedance(base)


def test_microstrip_monotonic_in_dielectric() -> None:
    """Taller dielectric → higher impedance for fixed trace."""
    thin = StackupGeometry(trace_width_mm=0.20, trace_thickness_mm=0.035,
                           dielectric_height_mm=0.13, er=4.3)
    thick = StackupGeometry(trace_width_mm=0.20, trace_thickness_mm=0.035,
                            dielectric_height_mm=0.30, er=4.3)
    assert microstrip_impedance(thick) > microstrip_impedance(thin)


def test_stripline_lower_than_microstrip_for_same_geometry() -> None:
    """Stripline impedance is typically lower than microstrip for the same w/h
    because the trace is fully enclosed in dielectric (effective εr is higher)."""
    g = StackupGeometry(trace_width_mm=0.20, trace_thickness_mm=0.035,
                        dielectric_height_mm=0.40, er=4.3)
    assert stripline_impedance(g) < microstrip_impedance(g)


def test_diff_microstrip_higher_than_single_ended() -> None:
    """Z_diff is typically ~1.7-1.9× Z_se, never below Z_se."""
    g = StackupGeometry(trace_width_mm=0.25, trace_thickness_mm=0.035,
                        dielectric_height_mm=0.21, er=4.3, trace_spacing_mm=0.20)
    z_se = microstrip_impedance(g)
    z_diff = differential_microstrip_impedance(g)
    assert z_diff > z_se


def test_diff_microstrip_requires_spacing() -> None:
    g = StackupGeometry(trace_width_mm=0.20, trace_thickness_mm=0.035,
                        dielectric_height_mm=0.21, er=4.3)
    with pytest.raises(ValueError):
        differential_microstrip_impedance(g)


def test_diff_stripline_requires_spacing() -> None:
    g = StackupGeometry(trace_width_mm=0.20, trace_thickness_mm=0.035,
                        dielectric_height_mm=0.50, er=4.5)
    with pytest.raises(ValueError):
        differential_stripline_impedance(g)


def test_zero_width_or_height_raises() -> None:
    with pytest.raises(ValueError):
        microstrip_impedance(StackupGeometry(
            trace_width_mm=0.0, trace_thickness_mm=0.035,
            dielectric_height_mm=0.21, er=4.3,
        ))
    with pytest.raises(ValueError):
        stripline_impedance(StackupGeometry(
            trace_width_mm=0.20, trace_thickness_mm=0.035,
            dielectric_height_mm=0.0, er=4.5,
        ))


def test_known_90ohm_usb_target_geometry() -> None:
    """The geometry shipped in examples/usb_eth_phy.yaml must hit ≈90 Ω."""
    g = StackupGeometry(trace_width_mm=0.350, trace_thickness_mm=0.035,
                        dielectric_height_mm=0.21, er=4.3, trace_spacing_mm=0.330)
    z = differential_microstrip_impedance(g)
    assert math.isclose(z, 90.0, abs_tol=2.0), z


def test_known_100ohm_ethernet_target_geometry() -> None:
    """The geometry shipped for Ethernet must hit ≈100 Ω."""
    g = StackupGeometry(trace_width_mm=0.285, trace_thickness_mm=0.035,
                        dielectric_height_mm=0.21, er=4.3, trace_spacing_mm=0.300)
    z = differential_microstrip_impedance(g)
    assert math.isclose(z, 100.0, abs_tol=2.0), z
