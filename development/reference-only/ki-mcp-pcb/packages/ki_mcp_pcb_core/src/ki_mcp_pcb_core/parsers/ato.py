"""``.ato`` (atopile) → CIR.

Two parsers, dispatched by what's installed:

  1. **atopile** (preferred, M2+) — wraps the real atopile compiler. Most
     correct, handles imports, types, module composition. Requires
     ``atopile`` on the Python path.
  2. **fallback** (M1) — a small, hand-rolled parser that handles the
     shape of files we ship under ``examples/``. Enough to drive the M1
     demo without atopile installed.

Public API stays stable: ``parse_ato(source) -> Board``.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import Path

from ki_mcp_pcb_core.cir.models import Board, Component, Net


def parse_ato(source: str | Path) -> Board:
    """Parse an ``.ato`` file into a CIR ``Board``.

    Falls back to the hand-rolled parser when ``atopile`` isn't installed.
    The fallback only handles the subset used by ``examples/*.ato``.
    """
    text = _read(source)
    try:
        from atopile import compile_to_cir
    except ImportError:
        return _fallback_parse(text, _source_name(source))
    # Real atopile path — pass through.
    board: Board = compile_to_cir(text)
    return board


# ---------------------------------------------------------------------------
# Fallback parser
# ---------------------------------------------------------------------------

_NAME_PART = re.compile(r"[A-Za-z_][\w]*")
_REFDES_PATTERN = re.compile(r"^[A-Z]+[0-9]+$")
_COMPONENT_RE = re.compile(
    r"\s*([A-Za-z_]\w*)\s*=\s*new\s+([A-Za-z_]\w*)\s*$"
)
_VALUE_DECOUPLE_RE = re.compile(
    r"\s*(?P<value>[\d.]+[a-zA-Z]+)\s*--\s*(?P<a>[\w.]+)\s+to\s+(?P<b>[\w.]+)\s*$"
)
_CONNECTION_RE = re.compile(
    r"\s*(?P<a>[\w.]+)\s*~\s*(?P<b>[\w.]+)\s*$"
)

# Map known atopile "type" names → MPN we use in CIR. Drives the demo file.
_TYPE_TO_MPN: dict[str, tuple[str, str]] = {
    # name in .ato file -> (mpn, refdes_prefix)
    "ESP32_S3_WROOM_1": ("ESP32-S3-WROOM-1", "U"),
    "USB_C_Receptacle": ("USB4105-GF-A", "J"),
    "AMS1117_33": ("AMS1117-3.3", "U"),
    "LED_0603_red": ("LTST-C190KGKT", "D"),
}


def _fallback_parse(text: str, name: str) -> Board:
    """Parse the subset of atopile used by the M1 demo file."""
    refdes_counters: dict[str, int] = {}
    components: dict[str, Component] = {}
    type_by_local: dict[str, str] = {}
    nets: dict[str, Net] = {}
    decoupling_idx = 0

    def next_refdes(prefix: str) -> str:
        refdes_counters[prefix] = refdes_counters.get(prefix, 0) + 1
        return f"{prefix}{refdes_counters[prefix]}"

    def get_or_create_net(name_: str) -> Net:
        if name_ not in nets:
            net_class = "ground" if name_ == "GND" else "power" if name_ in {"VBUS", "VCC"} else "signal"
            nets[name_] = Net(name=name_, net_class=net_class)  # type: ignore[arg-type]
        return nets[name_]

    def resolve_endpoint(endpoint: str) -> tuple[str, str] | tuple[None, str]:
        """Endpoint forms:
          - `local_name.pin`  -> (refdes, pin)
          - `local_name.NET`  -> ("GND" or similar) net by alias on the component side
          - `GND` / power-rail name -> (None, net_name)
        """
        if "." in endpoint:
            local, _, pin = endpoint.partition(".")
            comp = components.get(local)
            if comp is None:
                return None, endpoint  # unknown — caller decides
            # If the right-hand side looks like a net name (uppercase, not a number), treat as alias
            if pin and pin[0].isalpha() and pin.isupper():
                return None, pin
            return comp.refdes, pin
        # bare name → net
        return None, endpoint

    for raw_line in text.splitlines():
        line = raw_line.split("//", 1)[0].strip()
        if not line or line.startswith("module ") or line.endswith(":") or line.startswith("#"):
            continue

        # Component instantiation
        m = _COMPONENT_RE.match(line)
        if m:
            local, type_name = m.group(1), m.group(2)
            mpn, prefix = _TYPE_TO_MPN.get(type_name, (type_name, "U"))
            refdes = next_refdes(prefix)
            components[local] = Component(refdes=refdes, mpn=mpn)
            type_by_local[local] = type_name
            continue

        # Decoupling shorthand: `100nF -- foo.bar to baz.GND`
        m = _VALUE_DECOUPLE_RE.match(line)
        if m:
            value = m.group("value")
            decoupling_idx += 1
            # Add a capacitor with a default 0603 MPN. Real synthesis will pick a real cap.
            cap = Component(
                refdes=next_refdes("C"),
                mpn="GRM188R71C104KA01D" if value.lower().endswith("nf") else "GRM21BR60J106KE19L",
                value=value,
            )
            local_cap_name = f"_cap_{decoupling_idx}"
            components[local_cap_name] = cap
            # Connect pins 1 and 2 to the two endpoints
            for endpoint, pin in [(m.group("a"), "1"), (m.group("b"), "2")]:
                ref, where = resolve_endpoint(endpoint)
                if ref is None:
                    net = get_or_create_net(where)
                    net.members.append(f"{cap.refdes}.{pin}")
                else:
                    # Connect the cap pin to a net implied by the original endpoint
                    # If the endpoint name is bare (no pin), create a net for it.
                    net_name = where if where.isupper() else f"{ref}_{where}"
                    net = get_or_create_net(net_name)
                    net.members.append(f"{ref}.{where}")
                    net.members.append(f"{cap.refdes}.{pin}")
            continue

        # Net connection: `a.x ~ b.y`
        m = _CONNECTION_RE.match(line)
        if m:
            a_ref, a_where = resolve_endpoint(m.group("a"))
            b_ref, b_where = resolve_endpoint(m.group("b"))
            # Figure out a net name
            if a_ref is None:
                conn_net_name = a_where
            elif b_ref is None:
                conn_net_name = b_where
            else:
                conn_net_name = a_where if a_where.isupper() else b_where
            net = get_or_create_net(conn_net_name)
            for ref, where in [(a_ref, a_where), (b_ref, b_where)]:
                if ref is not None:
                    net.members.append(f"{ref}.{where}")
            continue

    components_list = [c for c in components.values()]
    nets_list = [
        Net(name=n.name, net_class=n.net_class, members=_uniq(n.members))
        for n in nets.values()
    ]

    return Board(
        name=name,
        components=components_list,
        nets=nets_list,
    )


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _read(source: str | Path) -> str:
    if isinstance(source, Path):
        return source.read_text(encoding="utf-8")
    if "\n" not in source and Path(source).exists():
        return Path(source).read_text(encoding="utf-8")
    return source


def _source_name(source: str | Path) -> str:
    if isinstance(source, Path):
        return source.stem
    if "\n" not in source and Path(source).exists():
        return Path(source).stem
    return "ato-board"


def _uniq(items: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for x in items:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out
