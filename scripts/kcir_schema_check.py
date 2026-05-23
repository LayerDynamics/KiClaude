#!/usr/bin/env python3
"""M1-Q-02 — KCIR schema-bump CI gate.

Runs against a PR (or any pair of git refs) and refuses changes to
``crates/ki/src/kcir/**`` that don't also:

1. Bump ``KCIR_VERSION`` in ``crates/ki/src/lib.rs`` to a strictly
   greater semver string than the base ref's value.
2. Add at least one new migration module under
   ``crates/ki/src/kcir/migrations/v*.rs`` AND a matching entry in
   ``crates/ki/src/kcir/migrations/mod.rs`` ``MIGRATIONS`` array.

The script is intentionally narrow: it doesn't validate the migration's
correctness — that's the migration's own smoke test. It only enforces
the *policy* that any KCIR-shape change is paired with a version bump
and a migration entry.

Usage
-----
    # Compare HEAD against origin/main (the default CI mode).
    python3 scripts/kcir_schema_check.py

    # Compare an explicit pair of refs.
    python3 scripts/kcir_schema_check.py --base main --head feature-x

    # Local dry-run that prints the diff summary but never exits 1.
    python3 scripts/kcir_schema_check.py --check-only

Exit codes
----------
    0  policy satisfied (no KCIR change OR change + bump + migration).
    1  policy violated.
    2  git or repo state error.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path


def _repo_root() -> Path:
    """Resolve the repo we're gating. Honors `KICLAUDE_REPO_ROOT` so
    tests can point at a temp clone; otherwise falls back to the
    current working directory, then to the script's install
    location.
    """
    env = os.environ.get("KICLAUDE_REPO_ROOT")
    if env:
        return Path(env).resolve()
    cwd = Path.cwd()
    if (cwd / ".git").exists():
        return cwd
    return Path(__file__).resolve().parents[1]


KCIR_DIR = "crates/ki/src/kcir/"
LIB_PATH = "crates/ki/src/lib.rs"
MIGRATIONS_DIR = "crates/ki/src/kcir/migrations/"
MIGRATIONS_MOD = "crates/ki/src/kcir/migrations/mod.rs"
KCIR_VERSION_RE = re.compile(
    r'pub\s+const\s+KCIR_VERSION\s*:\s*&str\s*=\s*"([0-9]+\.[0-9]+\.[0-9]+[^"]*)"'
)
MIGRATION_FILE_RE = re.compile(r"^v[0-9]+_[0-9]+\.rs$")
MIGRATIONS_ENTRY_RE = re.compile(
    r'Migration\s*\{\s*to_version:\s*"([0-9]+\.[0-9]+\.[0-9]+[^"]*)"'
)


def _run_git(args: list[str]) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=_repo_root(),
        check=False,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"git {' '.join(args)} failed: {proc.stderr.strip() or proc.stdout.strip()}"
        )
    return proc.stdout


def _ref_exists(ref: str) -> bool:
    proc = subprocess.run(
        ["git", "rev-parse", "--verify", ref],
        cwd=_repo_root(),
        check=False,
        capture_output=True,
    )
    return proc.returncode == 0


def _resolve_base(explicit: str | None) -> str:
    """Pick a base ref. Prefer an explicit `--base`; otherwise
    `origin/main`; otherwise `main`. Final fallback `HEAD~1`."""
    candidates: list[str] = []
    if explicit:
        candidates.append(explicit)
    candidates.extend(["origin/main", "main", "HEAD~1"])
    for ref in candidates:
        if _ref_exists(ref):
            return ref
    raise RuntimeError(
        f"could not resolve any base ref (tried: {candidates})"
    )


def _changed_files(base: str, head: str) -> list[str]:
    output = _run_git(["diff", "--name-only", f"{base}...{head}"])
    return [line for line in output.splitlines() if line]


def _read_at_ref(ref: str, path: str) -> str | None:
    """Read `path` as it existed at `ref`. Returns None if the file
    didn't exist at that ref."""
    proc = subprocess.run(
        ["git", "show", f"{ref}:{path}"],
        cwd=_repo_root(),
        check=False,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        return None
    return proc.stdout


def _extract_kcir_version(text: str) -> str | None:
    m = KCIR_VERSION_RE.search(text)
    return m.group(1) if m else None


def _extract_migration_versions(text: str) -> set[str]:
    return set(MIGRATIONS_ENTRY_RE.findall(text))


def _list_migration_files_at(ref: str | None) -> set[str]:
    """Migration *.rs files (excluding mod.rs and tests.rs) present at
    `ref`, or in the working tree if `ref is None`."""
    files: set[str] = set()
    if ref is None:
        path = _repo_root() / MIGRATIONS_DIR
        if not path.is_dir():
            return files
        for entry in path.iterdir():
            if entry.is_file() and MIGRATION_FILE_RE.match(entry.name):
                files.add(entry.name)
        return files
    try:
        listing = _run_git(["ls-tree", "-r", "--name-only", ref, MIGRATIONS_DIR])
    except RuntimeError:
        return files
    for line in listing.splitlines():
        name = Path(line).name
        if MIGRATION_FILE_RE.match(name):
            files.add(name)
    return files


def _semver_tuple(s: str) -> tuple[int, int, int, int, str]:
    """Parse a semver string into a comparable tuple.

    Per semver §11.4, a pre-release version is *less than* the
    corresponding release. We encode that by making the 4th element
    `0` for pre-releases and `1` for releases; the 5th element keeps
    the pre-release suffix as a tiebreaker among pre-releases on the
    same release track.
    """
    m = re.match(r"^([0-9]+)\.([0-9]+)\.([0-9]+)(?:[-+](.*))?$", s)
    if not m:
        raise ValueError(f"invalid semver: {s}")
    prerelease = m.group(4) or ""
    release_rank = 0 if prerelease else 1
    return (
        int(m.group(1)),
        int(m.group(2)),
        int(m.group(3)),
        release_rank,
        prerelease,
    )


def _greater(a: str, b: str) -> bool:
    """Return `True` if `a` is strictly greater than `b` (semver)."""
    return _semver_tuple(a) > _semver_tuple(b)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="kcir_schema_check.py",
        description=(
            "Refuse PRs that change crates/ki/src/kcir/** without bumping "
            "KCIR_VERSION and adding a migration."
        ),
    )
    parser.add_argument(
        "--base", help="Base ref. Defaults to origin/main → main → HEAD~1."
    )
    parser.add_argument("--head", default="HEAD", help="Head ref. Defaults to HEAD.")
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="Always exit 0; just print the diagnosis.",
    )
    args = parser.parse_args(argv)

    try:
        base = _resolve_base(args.base)
        head = args.head
        changed = _changed_files(base, head)
    except RuntimeError as e:
        print(f"kcir_schema_check: {e}", file=sys.stderr)
        return 2

    kcir_changed = [p for p in changed if p.startswith(KCIR_DIR)]

    if not kcir_changed:
        print(
            f"kcir_schema_check: no changes under {KCIR_DIR} between {base}..{head}; "
            "policy not triggered."
        )
        return 0

    # Diagnostic data.
    print(
        f"kcir_schema_check: {len(kcir_changed)} file(s) changed under {KCIR_DIR}:"
    )
    for p in kcir_changed:
        print(f"  - {p}")

    base_lib = _read_at_ref(base, LIB_PATH) or ""
    head_lib_path = _repo_root() / LIB_PATH
    head_lib = (
        head_lib_path.read_text()
        if args.head == "HEAD"
        else (_read_at_ref(head, LIB_PATH) or "")
    )
    base_version = _extract_kcir_version(base_lib)
    head_version = _extract_kcir_version(head_lib)
    if not base_version or not head_version:
        print(
            "kcir_schema_check: ERROR — could not extract KCIR_VERSION "
            f"from {LIB_PATH} at base ({base_version!r}) or head ({head_version!r}).",
            file=sys.stderr,
        )
        return 0 if args.check_only else 1

    print(f"  KCIR_VERSION: base={base_version}  head={head_version}")
    if not _greater(head_version, base_version):
        print(
            f"kcir_schema_check: VIOLATION — KCIR_VERSION must be bumped above "
            f"{base_version}; head is {head_version}.",
            file=sys.stderr,
        )
        return 0 if args.check_only else 1

    # Confirm at least one new migration file vs. base.
    base_files = _list_migration_files_at(base)
    head_files = (
        _list_migration_files_at(None)
        if args.head == "HEAD"
        else _list_migration_files_at(head)
    )
    new_files = head_files - base_files
    print(f"  new migration files: {sorted(new_files) or '<none>'}")

    # Confirm mod.rs gained a Migration entry whose to_version >= head_version.
    base_mod = _read_at_ref(base, MIGRATIONS_MOD) or ""
    head_mod = (
        (_repo_root() / MIGRATIONS_MOD).read_text()
        if args.head == "HEAD"
        else (_read_at_ref(head, MIGRATIONS_MOD) or "")
    )
    base_versions = _extract_migration_versions(base_mod)
    head_versions = _extract_migration_versions(head_mod)
    new_versions = head_versions - base_versions
    print(f"  new MIGRATIONS entries: {sorted(new_versions) or '<none>'}")

    has_new_migration = bool(new_files) and bool(new_versions)
    if not has_new_migration:
        print(
            "kcir_schema_check: VIOLATION — changes to "
            f"{KCIR_DIR} must include a new migration file under "
            f"{MIGRATIONS_DIR} AND a matching `Migration {{ to_version: \"...\" }}` "
            "entry in mod.rs's `MIGRATIONS` array.",
            file=sys.stderr,
        )
        return 0 if args.check_only else 1

    # Confirm the new migration's to_version matches the new KCIR_VERSION.
    if head_version not in new_versions:
        print(
            "kcir_schema_check: VIOLATION — new migration's `to_version` "
            f"set does not contain the new KCIR_VERSION={head_version}. "
            f"new_versions={sorted(new_versions)}",
            file=sys.stderr,
        )
        return 0 if args.check_only else 1

    print(
        "kcir_schema_check: OK — KCIR change is paired with a version "
        f"bump ({base_version} → {head_version}) and a migration "
        f"({sorted(new_files)})."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
