#!/usr/bin/env python3
"""Populate / verify the bundled KiCad library mirror (SPEC §9.5, §12, D6, FR-040).

kiclaude ships a *pinned, curated subset* of the official KiCad libraries —
exactly the symbol/footprint libraries the bundled `examples/` reference — so a
project resolves its parts fully offline (first principle #8: local-first). This
is deliberately **not** the full multi-GB mirror; it is the slice the examples
need, pinned by git tag and verified by SHA-256.

Two modes:

    python scripts/populate_libs.py            # verify: every file in
                                               # MANIFEST.toml exists and its
                                               # SHA-256 matches. Any missing
                                               # file is fetched from GitLab at
                                               # the pinned tag (D6 self-heal).

    python scripts/populate_libs.py --pin      # (re)fetch every curated entry
                                               # from GitLab at UPSTREAM_TAG,
                                               # write the file under libs/, and
                                               # regenerate MANIFEST.toml with
                                               # fresh SHA-256 pins.

D6 ("bundled pinned mirror; fall through to KiCad GitLab on-demand"): the verify
mode fetches anything absent, so a fresh checkout self-heals from upstream while
the SHA pins keep the content honest.
"""

from __future__ import annotations

import argparse
import hashlib
import sys
import tomllib
import urllib.request
from dataclasses import dataclass
from pathlib import Path

# Pinned upstream tag for every curated library (SPEC §9.5: pinned at install).
UPSTREAM_TAG = "9.0.0"
SYMBOLS_REPO = "https://gitlab.com/kicad/libraries/kicad-symbols"
FOOTPRINTS_REPO = "https://gitlab.com/kicad/libraries/kicad-footprints"
PACKAGES3D_REPO = "https://gitlab.com/kicad/libraries/kicad-packages3D"

_REPO_FOR_KIND = {
    "symbol": SYMBOLS_REPO,
    "footprint": FOOTPRINTS_REPO,
    "model3d": PACKAGES3D_REPO,
}

LIBS_DIR = Path(__file__).resolve().parent.parent / "libs"
MANIFEST_PATH = LIBS_DIR / "MANIFEST.toml"


@dataclass(frozen=True)
class Entry:
    """One curated library file: which upstream repo path it comes from and
    where it lands under `libs/`."""

    kind: str  # "symbol" | "footprint" | "model3d"
    nickname: str  # KiCad library nickname (the lib-table `name`)
    repo_path: str  # path within the upstream repo at UPSTREAM_TAG
    dest: str  # path under libs/ (relative, forward slashes)

    @property
    def url(self) -> str:
        return f"{_REPO_FOR_KIND[self.kind]}/-/raw/{UPSTREAM_TAG}/{self.repo_path}"


# The curated subset: exactly the libraries/footprints the bundled examples
# reference, every one a real KiCad 9.0.0 part. Edit this list to change what
# the mirror ships, then re-run with --pin to refresh MANIFEST.toml.
_CURATED: list[Entry] = [
    # Whole symbol libraries (pinned) — resolve every example schematic symbol.
    Entry("symbol", "Device", "Device.kicad_sym", "symbols/Device.kicad_sym"),
    Entry("symbol", "power", "power.kicad_sym", "symbols/power.kicad_sym"),
    Entry("symbol", "Connector", "Connector.kicad_sym", "symbols/Connector.kicad_sym"),
    Entry(
        "symbol",
        "Regulator_Switching",
        "Regulator_Switching.kicad_sym",
        "symbols/Regulator_Switching.kicad_sym",
    ),
    Entry("symbol", "RF_Module", "RF_Module.kicad_sym", "symbols/RF_Module.kicad_sym"),
    # Footprints — the exact files the examples place (after the lib-id fixups).
    Entry(
        "footprint",
        "Capacitor_SMD",
        "Capacitor_SMD.pretty/C_0402_1005Metric.kicad_mod",
        "footprints/Capacitor_SMD.pretty/C_0402_1005Metric.kicad_mod",
    ),
    Entry(
        "footprint",
        "Capacitor_SMD",
        "Capacitor_SMD.pretty/C_0603_1608Metric.kicad_mod",
        "footprints/Capacitor_SMD.pretty/C_0603_1608Metric.kicad_mod",
    ),
    Entry(
        "footprint",
        "RF_Module",
        "RF_Module.pretty/ESP32-S3-WROOM-1.kicad_mod",
        "footprints/RF_Module.pretty/ESP32-S3-WROOM-1.kicad_mod",
    ),
    Entry(
        "footprint",
        "RF_Module",
        "RF_Module.pretty/ESP32-C6-MINI-1.kicad_mod",
        "footprints/RF_Module.pretty/ESP32-C6-MINI-1.kicad_mod",
    ),
    Entry(
        "footprint",
        "Package_BGA",
        "Package_BGA.pretty/BGA-16_1.92x1.92mm_Layout4x4_P0.5mm.kicad_mod",
        "footprints/Package_BGA.pretty/BGA-16_1.92x1.92mm_Layout4x4_P0.5mm.kicad_mod",
    ),
    Entry(
        "footprint",
        "Connector_Coaxial",
        "Connector_Coaxial.pretty/U.FL_Molex_MCRF_73412-0110_Vertical.kicad_mod",
        "footprints/Connector_Coaxial.pretty/U.FL_Molex_MCRF_73412-0110_Vertical.kicad_mod",
    ),
    # 3D STEP component models (T10 / FR-029 / D6). The .step siblings of the
    # footprints' .wrl model refs — the kiserver model3d resolver swaps .wrl
    # for .step and the kithree viewer tessellates them via occt-import-js.
    # (ESP32-C6-MINI-1 and the U.FL connector ship only .wrl upstream, so they
    # box-fall-back in the viewer — there is no .step to seed.)
    Entry(
        "model3d",
        "RF_Module",
        "RF_Module.3dshapes/ESP32-S3-WROOM-1.step",
        "packages3D/RF_Module.3dshapes/ESP32-S3-WROOM-1.step",
    ),
    Entry(
        "model3d",
        "Package_BGA",
        "Package_BGA.3dshapes/BGA-16_1.92x1.92mm_Layout4x4_P0.5mm.step",
        "packages3D/Package_BGA.3dshapes/BGA-16_1.92x1.92mm_Layout4x4_P0.5mm.step",
    ),
    Entry(
        "model3d",
        "Capacitor_SMD",
        "Capacitor_SMD.3dshapes/C_0402_1005Metric.step",
        "packages3D/Capacitor_SMD.3dshapes/C_0402_1005Metric.step",
    ),
    Entry(
        "model3d",
        "Capacitor_SMD",
        "Capacitor_SMD.3dshapes/C_0603_1608Metric.step",
        "packages3D/Capacitor_SMD.3dshapes/C_0603_1608Metric.step",
    ),
]


def _fetch(url: str) -> bytes:
    """Fetch a pinned raw file over HTTPS. Scheme is enforced so the audited
    `urlopen` call only ever talks to the pinned GitLab host."""
    if not url.startswith("https://"):
        raise ValueError(f"refusing non-https url: {url}")
    req = urllib.request.Request(  # noqa: S310 - https scheme enforced above
        url, headers={"User-Agent": "kiclaude-populate-libs"}
    )
    with urllib.request.urlopen(req, timeout=60) as resp:  # noqa: S310 - https enforced above
        return bytes(resp.read())


def _write_manifest(pinned: list[tuple[Entry, str]]) -> None:
    """Regenerate MANIFEST.toml from the freshly fetched entries + SHA-256s."""
    lines = [
        "# Pinned KiCad library mirror — GENERATED by scripts/populate_libs.py --pin.",
        "# Do not hand-edit: change _CURATED in the script and re-run --pin.",
        "",
        "[meta]",
        f'upstream_tag = "{UPSTREAM_TAG}"',
        f'symbols_repo = "{SYMBOLS_REPO}"',
        f'footprints_repo = "{FOOTPRINTS_REPO}"',
        'license = "CC-BY-SA-4.0 with the KiCad Library Exception (see LICENSE.md)"',
        'update_cadence = "monthly"',
        "",
    ]
    for entry, sha in pinned:
        lines += [
            "[[library]]",
            f'kind = "{entry.kind}"',
            f'nickname = "{entry.nickname}"',
            f'file = "{entry.dest}"',
            f'url = "{entry.url}"',
            f'sha256 = "{sha}"',
            "",
        ]
    MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _pin() -> int:
    pinned: list[tuple[Entry, str]] = []
    for entry in _CURATED:
        dest = LIBS_DIR / entry.dest
        dest.parent.mkdir(parents=True, exist_ok=True)
        blob = _fetch(entry.url)
        dest.write_bytes(blob)
        sha = hashlib.sha256(blob).hexdigest()
        pinned.append((entry, sha))
        print(f"pinned {entry.dest}  {sha[:12]}…  ({len(blob)} bytes)")
    _write_manifest(pinned)
    print(f"\nwrote {MANIFEST_PATH} with {len(pinned)} pins @ {UPSTREAM_TAG}")
    return 0


def _verify() -> int:
    if not MANIFEST_PATH.is_file():
        print(f"{MANIFEST_PATH} missing — run with --pin first", file=sys.stderr)
        return 1
    data = tomllib.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    libs: list[dict[str, str]] = data.get("library", [])
    if not libs:
        print(f"{MANIFEST_PATH} lists no [[library]] entries", file=sys.stderr)
        return 1
    bad = 0
    for lib in libs:
        dest = LIBS_DIR / lib["file"]
        if not dest.exists():
            print(f"fetch  {lib['file']}")
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(_fetch(lib["url"]))
        got = hashlib.sha256(dest.read_bytes()).hexdigest()
        if got != lib["sha256"]:
            print(
                f"SHA MISMATCH  {lib['file']}\n"
                f"  manifest {lib['sha256']}\n  on-disk  {got}",
                file=sys.stderr,
            )
            bad += 1
        else:
            print(f"ok     {lib['file']}")
    if bad:
        print(f"\n{bad} file(s) failed verification", file=sys.stderr)
        return 1
    print(f"\n{len(libs)} file(s) verified against {MANIFEST_PATH.name}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Populate/verify the bundled KiCad library mirror."
    )
    ap.add_argument(
        "--pin",
        action="store_true",
        help="(re)fetch all curated libraries from GitLab and regenerate MANIFEST.toml",
    )
    args = ap.parse_args()
    return _pin() if args.pin else _verify()


if __name__ == "__main__":
    raise SystemExit(main())
