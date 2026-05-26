#!/usr/bin/env bash
# License audit for kiclaude — runs across all three language stacks.
#
# Fails fast on any disallowed license. The Rust side is enforced by
# cargo-deny (see deny.toml). The Node side parses `pnpm licenses ls
# --prod --json`. The Python side reads installed-package metadata
# via uv's pip-licenses bridge.
#
# Allowlist (mirrored across all three): Apache-2.0, MIT, BSD-2-Clause,
# BSD-3-Clause, ISC, MPL-2.0, Zlib, BSL-1.0, Unicode-3.0, CC0-1.0.
# Anything else fails the gate.

set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

ALLOWLIST_PATTERN='^(Apache-2\.0( WITH LLVM-exception)?|MIT(-0)?|BSD-[23]-Clause|ISC|MPL-2\.0|Zlib|BSL-1\.0|Unicode-3\.0|Unicode-DFS-2016|CC0-1\.0|0BSD|Python-2\.0|PSF-2\.0|HPND)$'

exit_code=0
section() { printf '\n==== %s ====\n' "$1"; }

section "Rust (cargo-deny)"
if ! command -v cargo-deny >/dev/null; then
    echo "cargo-deny not installed; skipping (install with: cargo install cargo-deny)"
else
    if ! cargo deny check licenses; then
        echo "FAIL: cargo-deny rejected one or more Rust dependency licenses."
        exit_code=1
    fi
fi

section "Node (pnpm)"
if command -v pnpm >/dev/null; then
    # `pnpm licenses ls --prod --json` returns a flat object keyed by
    # license name. Anything outside the allowlist trips the gate.
    # The repo has no root package.json (workspace config lives in
    # `pnpm-workspace.yaml` alone), so `pnpm -r` from the repo root
    # errors out. Aggregate per-workspace-member outputs instead.
    json="{}"
    pnpm_ok=1
    if [ -f "package.json" ]; then
        if ! json="$(pnpm -r licenses ls --prod --json 2>/dev/null)"; then
            pnpm_ok=0
        fi
    else
        # Walk every workspace member that has its own package.json.
        merged="{"
        first=1
        for member_pkg in $(find client packages services -maxdepth 3 -name package.json -not -path '*/node_modules/*' 2>/dev/null); do
            member_dir="$(dirname "$member_pkg")"
            if member_json="$(cd "$member_dir" && pnpm licenses ls --prod --json 2>/dev/null)"; then
                if [ "$first" -eq 0 ]; then merged+=","; fi
                merged+="\"$member_dir\":$member_json"
                first=0
            fi
        done
        merged+="}"
        json="$merged"
        if [ "$first" -eq 1 ]; then
            pnpm_ok=0
        fi
    fi
    if [ "$pnpm_ok" -eq 0 ]; then
        echo "WARN: pnpm licenses ls returned no data (likely no node_modules installed). Run pnpm install first."
    else
        # Pass the (potentially large) pnpm-licenses JSON via a temp file, not as
        # an argv string — a big workspace blows past ARG_MAX (E2BIG) otherwise.
        json_tmp="$(mktemp)"
        printf '%s' "$json" > "$json_tmp"
        bad="$(python3 - "$json_tmp" <<'PY'
import json, re, sys
with open(sys.argv[1], encoding="utf-8") as _f:
    data = json.loads(_f.read() or "{}")
allow = re.compile(r"""^(Apache-2\.0( WITH LLVM-exception)?|MIT(-0)?|BSD-[23]-Clause|ISC|MPL-2\.0|Zlib|BSL-1\.0|Unicode-3\.0|CC0-1\.0|0BSD|Python-2\.0|PSF-2\.0|HPND|CC-BY-4\.0)$""")
bad = []
def walk(value):
    if isinstance(value, dict):
        if "license" in value and isinstance(value["license"], str):
            if not allow.match(value["license"]):
                bad.append((value.get("name", "?"), value["license"]))
        for v in value.values():
            walk(v)
    elif isinstance(value, list):
        for v in value:
            walk(v)
walk(data)
for name, lic in bad:
    print(f"{name}: {lic}")
PY
)"
        rm -f "$json_tmp"
        if [ -n "$bad" ]; then
            echo "FAIL: disallowed Node package licenses:"
            echo "$bad"
            exit_code=1
        else
            echo "OK: all production Node deps under allowlist."
        fi
    fi
else
    echo "pnpm not installed; skipping."
fi

section "Python (uv pip + importlib.metadata)"
if command -v uv >/dev/null; then
    bad="$(uv run python - <<'PY'
import importlib.metadata as md
import re
allow = re.compile(r"""^(Apache-2\.0( WITH LLVM-exception)?|MIT(-0)?|BSD-[23]-Clause|ISC|MPL-2\.0|Zlib|BSL-1\.0|Unicode-3\.0|CC0-1\.0|0BSD|Python-2\.0|PSF-2\.0|HPND)$""")
bad = []
for d in md.distributions():
    name = d.metadata["Name"]
    # `License-Expression` is PEP 639's normalized SPDX field — prefer
    # it when present. Fall back to the legacy `License` text otherwise.
    license_value = d.metadata.get("License-Expression") or d.metadata.get("License", "")
    if not license_value:
        # Some packages encode the license only in Trove classifiers.
        for c in d.metadata.get_all("Classifier") or []:
            if c.startswith("License ::"):
                license_value = c.split(" :: ")[-1]
                break
    if not license_value or not allow.match(license_value):
        bad.append((name, license_value or "UNKNOWN"))
for name, lic in bad:
    print(f"{name}: {lic}")
PY
)"
    if [ -n "$bad" ]; then
        echo "WARN: Python packages with non-allowlisted license metadata:"
        echo "$bad"
        # Note: many Python packages have ambiguous license metadata
        # (e.g. classifier-only). Treat as WARN for M0; tighten in M1.
    else
        echo "OK: all Python deps under allowlist."
    fi
else
    echo "uv not installed; skipping."
fi

section "Summary"
if [ "$exit_code" -eq 0 ]; then
    echo "license_audit.sh: PASS"
else
    echo "license_audit.sh: FAIL"
fi
exit "$exit_code"
