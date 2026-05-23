"""CIR v0.1 — typed electrical model.

This is a deliberately small first pass. Enough fields to round-trip a
simple two-layer board through synthesis, placement, and Gerber export.
Pro-stack fields (impedance, length-match groups, RF stackup helpers)
are stubbed and will fill out across M2–M4.

Any change here must:
1. Bump ``CIR_VERSION``.
2. Add a migration in ``ki_mcp_pcb_core.cir.migrations``.
3. Update the golden examples under ``examples/`` if their shape changes.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

# Schema version. Bump for any breaking change; minor bump for additive.
# 0.2 (M2): added power-plane stackup, partitions, decoupling intent,
#           length-match groups.
# 0.3 (M3): added diff_pair_with, reference_plane on Net (high-speed signal
#           integrity).
# 0.4 (M4): added DDR fly-by topology (Net.topology, fly_by_order),
#           BGA fanout intent (Component.bga_pitch_mm), board-level
#           signoff for co-pilot acknowledgment of high-stakes features.
#           Additive — migrations.py handles 0.1 → 0.2 → 0.3 → 0.4.
CIR_VERSION = "0.4"

# ---------------------------------------------------------------------------
# Primitive types
# ---------------------------------------------------------------------------

Mils = Annotated[float, Field(ge=0, description="Distance in mils (thousandths of an inch).")]
Mm = Annotated[float, Field(ge=0, description="Distance in millimeters.")]
RefDes = Annotated[
    str,
    Field(
        pattern=r"^[A-Z]+[0-9]+$",
        description="Reference designator (e.g. 'R1', 'U2', 'C42').",
    ),
]
NetName = Annotated[str, Field(min_length=1, max_length=128)]


class _Base(BaseModel):
    """Shared model config."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=False,
        str_strip_whitespace=True,
        populate_by_name=True,
    )


# ---------------------------------------------------------------------------
# Stackup + outline
# ---------------------------------------------------------------------------


class Layer(_Base):
    """A single copper or dielectric layer."""

    name: str
    kind: Literal["copper", "dielectric", "soldermask", "silkscreen", "paste"]
    thickness_mm: Mm = 0.035  # 1 oz copper default
    material: str | None = None
    # Used by the impedance solver later (M3+); ignored for now.
    er: float | None = Field(None, description="Relative permittivity for dielectric layers.")


class Stackup(_Base):
    """PCB layer stackup.

    For M1 the default was 2-layer FR-4. M2 introduces 4-layer with
    power planes via ``default_4layer_fr4``. M3+ adds controlled-impedance
    hints for high-speed work.
    """

    layers: list[Layer]
    finished_thickness_mm: Mm = 1.6
    controlled_impedance: bool = False
    # Layers that should be flooded as power/ground planes. Names refer
    # to entries in ``layers`` (e.g. "GND.Cu", "PWR.Cu"). M2 validators
    # use this to enforce decoupling-return-path rules.
    power_plane_layers: list[str] = Field(default_factory=list)

    @classmethod
    def default_2layer_fr4(cls) -> Stackup:
        return cls(
            layers=[
                Layer(name="F.Cu", kind="copper"),
                Layer(name="dielectric", kind="dielectric", thickness_mm=1.53, material="FR-4", er=4.5),
                Layer(name="B.Cu", kind="copper"),
            ]
        )

    @classmethod
    def default_4layer_fr4(cls) -> Stackup:
        """4-layer SIG / GND / PWR / SIG stackup, JLC-compatible."""
        return cls(
            layers=[
                Layer(name="F.Cu", kind="copper"),
                Layer(name="pp1", kind="dielectric", material="prepreg", er=4.3, thickness_mm=0.21),
                Layer(name="In1.Cu", kind="copper"),
                Layer(name="core", kind="dielectric", material="FR-4", er=4.5, thickness_mm=1.18),
                Layer(name="In2.Cu", kind="copper"),
                Layer(name="pp2", kind="dielectric", material="prepreg", er=4.3, thickness_mm=0.21),
                Layer(name="B.Cu", kind="copper"),
            ],
            power_plane_layers=["In1.Cu", "In2.Cu"],
        )


class Outline(_Base):
    """Board outline. ``auto`` lets the placement step infer a rectangle."""

    shape: Literal["auto", "rect", "polygon"] = "auto"
    width_mm: Mm | None = None
    height_mm: Mm | None = None
    polygon_mm: list[tuple[float, float]] | None = None
    corner_radius_mm: Mm = 0.0

    @field_validator("polygon_mm")
    @classmethod
    def _check_polygon(cls, v: list[tuple[float, float]] | None) -> list[tuple[float, float]] | None:
        if v is not None and len(v) < 3:
            raise ValueError("polygon_mm must have at least 3 points")
        return v


# ---------------------------------------------------------------------------
# Components + nets
# ---------------------------------------------------------------------------


Partition = Literal["analog", "digital", "rf", "power", "isolated"]


class Component(_Base):
    """A placed (or to-be-placed) electrical component."""

    refdes: RefDes
    mpn: str = Field(..., description="Manufacturer part number — must resolve at synthesis time.")
    value: str | None = None
    footprint: str | None = Field(
        None,
        description="KiCad footprint identifier 'Library:Name'. If None, "
        "the synthesizer resolves it from MPN.",
    )
    symbol: str | None = Field(
        None,
        description="KiCad symbol identifier 'Library:Name'. If None, resolved from MPN.",
    )
    placement_hint: str | None = Field(
        None,
        description="Declarative natural-language placement hint, e.g. "
        "'south edge, centered'. Never raw coordinates from an LLM.",
    )
    # ── M2 fields ────────────────────────────────────────────────────
    partition: Partition | None = Field(
        None,
        description="Mixed-signal partition this component belongs to. "
        "Validators (CIR050) enforce no signal nets cross partitions "
        "except via declared bridge components.",
    )
    decoupling_pins: list[str] = Field(
        default_factory=list,
        description="Supply pin numbers that require nearby decoupling. "
        "Used by the M2 decoupling-coverage validator (CIR030).",
    )
    is_bridge: bool = Field(
        False,
        description="Marks a component as a partition bridge (ferrite bead, "
        "opto-isolator, capacitor coupling). Bridges may legally connect "
        "different partitions.",
    )
    # ── M4 fields ────────────────────────────────────────────────────
    bga_pitch_mm: Mm | None = Field(
        None,
        description="Ball pitch in mm for BGA / LGA packages (typically "
        "0.4 / 0.5 / 0.65 / 0.8 / 1.0). CIR110 looks up the fanout "
        "template for the declared pitch and warns if the current fab "
        "target can't escape-route it.",
    )
    attrs: dict[str, str] = Field(default_factory=dict)


NetClass = Literal["signal", "power", "ground", "high_speed", "differential", "rf", "analog"]


class Net(_Base):
    """A named electrical net."""

    name: NetName
    members: list[str] = Field(
        default_factory=list,
        description="List of 'REFDES.PIN' strings (e.g. 'U1.3', 'R5.2').",
    )
    net_class: NetClass = "signal"
    # Length-match group; nets in the same group must be matched within tolerance.
    length_match_group: str | None = None
    target_impedance_ohm: float | None = None
    # ── M2 fields ────────────────────────────────────────────────────
    power_rail: str | None = Field(
        None,
        description="Which power rail this net belongs to (e.g. '3V3', 'VBUS'). "
        "Used by the decoupling-coverage validator to match supply pins.",
    )
    partition: Partition | None = Field(
        None,
        description="Mixed-signal partition this net belongs to. CIR050 "
        "enforces signal nets stay within a partition.",
    )
    cross_partition_ok: bool = Field(
        False,
        description="Marks a partition crossing as intentional (e.g. an I2S "
        "or SPI bus that legitimately bridges digital ↔ analog ICs). "
        "Set this only when the crossing has been reviewed.",
    )
    # ── M3 fields ────────────────────────────────────────────────────
    diff_pair_with: str | None = Field(
        None,
        description="Name of the other net in this differential pair. "
        "CIR060 validates that the named net exists, points back at us, "
        "and shares a length-match group. USB D+/D−, Ethernet TX±/RX±.",
    )
    reference_plane: str | None = Field(
        None,
        description="Stackup layer name (e.g. 'In1.Cu') that this net's "
        "return current should travel on. CIR090 checks the plane exists "
        "in the stackup and is contiguous (not split under the trace).",
    )
    trace_width_mm: Mm | None = Field(
        None,
        description="Per-net trace width override. Used by the impedance "
        "solver (CIR070) when set; otherwise a conservative default is "
        "applied. Diff pairs and controlled-impedance nets typically need "
        "this set explicitly.",
    )
    trace_spacing_mm: Mm | None = Field(
        None,
        description="Per-net edge-to-edge spacing for differential pairs. "
        "Only used when ``diff_pair_with`` is also set.",
    )
    cpwg_gap_mm: Mm | None = Field(
        None,
        description="Trace-to-side-ground gap for grounded coplanar "
        "waveguide (CPWG) RF nets. CIR070 calls the CPWG solver when "
        "this is set instead of the microstrip approximation.",
    )
    # ── M4 fields ────────────────────────────────────────────────────
    topology: Literal["point_to_point", "fly_by", "t_branch", "star"] | None = Field(
        None,
        description="Routing topology hint. DDR3/4 address+command nets are "
        "typically 'fly_by' (controller → ram_0 → ram_1 → … → terminator).",
    )
    fly_by_order: list[str] = Field(
        default_factory=list,
        description="Ordered list of refdes for fly-by topology — first entry "
        "is the controller, last entry is the terminator. CIR100 enforces "
        "the order and structure.",
    )

    @field_validator("members")
    @classmethod
    def _check_members(cls, v: list[str]) -> list[str]:
        for m in v:
            if "." not in m:
                raise ValueError(f"net member {m!r} must be 'REFDES.PIN'")
        return v


# ---------------------------------------------------------------------------
# Constraints
# ---------------------------------------------------------------------------

ConstraintKind = Literal[
    "max_length",
    "min_length",
    "length_match",
    "min_separation",
    "max_stub",
    "decoupling_within_mm",
    "keep_out",
    "controlled_impedance",
]


class Constraint(_Base):
    """A design-intent constraint that the validator will enforce."""

    kind: ConstraintKind
    targets: list[str] = Field(
        default_factory=list,
        description="Net names, net classes, or refdes the constraint applies to.",
    )
    value_mm: float | None = None
    value_ohm: float | None = None
    tolerance_pct: float | None = None
    note: str | None = None


# ---------------------------------------------------------------------------
# Fab + BOM policy
# ---------------------------------------------------------------------------


class FabTarget(_Base):
    """Target fabrication house + its DFM constraints."""

    name: Literal["jlcpcb", "oshpark", "pcbway", "generic"] = "jlcpcb"
    min_trace_mm: Mm = 0.127  # 5 mil
    min_space_mm: Mm = 0.127  # 5 mil
    min_drill_mm: Mm = 0.2
    min_annular_ring_mm: Mm = 0.13
    layer_count: int = Field(2, ge=2, le=16)


Distributor = Literal["digikey", "mouser", "jlc", "lcsc", "octopart"]


def _default_distributors() -> list[Distributor]:
    return ["jlc", "digikey", "mouser"]


class Signoff(_Base):
    """Co-pilot acknowledgment for high-stakes M4 features.

    When set ``True``, suppresses the "needs human review" warnings the
    M4 validators emit. This is the audit trail: an LLM cannot flip these
    flags on its own — a human must commit the change.
    """

    rf_reviewed: bool = Field(False,
                               description="RF traces / antennas have been reviewed by a human EE")
    ddr_reviewed: bool = Field(False,
                               description="DDR fly-by topology + length tuning reviewed")
    bga_fanout_reviewed: bool = Field(False,
                                       description="BGA escape routing reviewed")
    reviewer: str | None = Field(None, description="Name/handle of the human reviewer")
    reviewed_at: str | None = Field(None,
                                     description="ISO 8601 date the review was completed")


class BOMPolicy(_Base):
    distributors: list[Distributor] = Field(default_factory=_default_distributors)
    require_in_stock: bool = True
    max_unit_price_usd: float | None = None
    prefer_basic_parts: bool = Field(True, description="JLC 'basic' parts avoid the assembly fee.")


# ---------------------------------------------------------------------------
# Top-level board
# ---------------------------------------------------------------------------


class Board(_Base):
    """The top-level CIR document — one board per file."""

    cir_version: str = CIR_VERSION
    name: str
    description: str | None = None

    stackup: Stackup = Field(default_factory=Stackup.default_2layer_fr4)
    outline: Outline = Field(default_factory=Outline)

    components: list[Component] = Field(default_factory=list)
    nets: list[Net] = Field(default_factory=list)
    constraints: list[Constraint] = Field(default_factory=list)

    fab: FabTarget = Field(default_factory=FabTarget)
    bom_policy: BOMPolicy = Field(default_factory=BOMPolicy)
    signoff: Signoff = Field(default_factory=Signoff)

    @field_validator("cir_version")
    @classmethod
    def _check_version(cls, v: str) -> str:
        major = v.split(".")[0]
        expected_major = CIR_VERSION.split(".")[0]
        if major != expected_major:
            raise ValueError(
                f"CIR major version mismatch: file is {v}, library is {CIR_VERSION}. "
                "Run a migration."
            )
        return v
