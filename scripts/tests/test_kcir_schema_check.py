"""M1-Q-02 acceptance tests for the KCIR schema-bump CI gate.

Each test builds a tiny temp git repo with the relevant file layout
(``crates/ki/src/lib.rs``, ``crates/ki/src/kcir/...``,
``crates/ki/src/kcir/migrations/...``), commits a "main" state, branches
to a "head" state with the scenario under test, then runs the gate
script against the pair and asserts the exit code + stderr message.

The gate must:

- exit 0 when no KCIR file changed.
- exit 1 when a KCIR file changed but ``KCIR_VERSION`` did not bump.
- exit 1 when a KCIR file changed + version bumped but no migration
  module / no ``MIGRATIONS`` entry was added.
- exit 1 when the new migration's ``to_version`` doesn't match the
  new ``KCIR_VERSION``.
- exit 0 when everything is in order.
- exit 0 with ``--check-only`` regardless of violations.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "kcir_schema_check.py"


def _run_git(cwd: Path, *args: str, env: dict[str, str] | None = None) -> None:
    proc = subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
    if proc.returncode != 0:
        raise AssertionError(
            f"git {' '.join(args)} failed: {proc.stderr.strip() or proc.stdout.strip()}"
        )


def _git_env() -> dict[str, str]:
    base = dict(os.environ)
    base.update(
        {
            "GIT_AUTHOR_NAME": "kcir-test",
            "GIT_AUTHOR_EMAIL": "test@example.com",
            "GIT_COMMITTER_NAME": "kcir-test",
            "GIT_COMMITTER_EMAIL": "test@example.com",
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_CONFIG_SYSTEM": "/dev/null",
        }
    )
    return base


def _init_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    env = _git_env()
    _run_git(repo, "init", "-q", "-b", "main", env=env)
    _run_git(repo, "config", "user.email", "test@example.com", env=env)
    _run_git(repo, "config", "user.name", "kcir-test", env=env)
    return repo


LIB_TEMPLATE = '''//! kiclaude-ki test stub.\n\npub const KCIR_VERSION: &str = "{version}";\n'''

KCIR_FIELD_TEMPLATE = """//! kcir/project.rs stub.

pub struct Project {{
    pub kcir_version: String,
    pub name: String,
{extra}}}
"""

MIGRATIONS_MOD_TEMPLATE = """//! migrations/mod.rs stub.

pub mod v0_2;
{extra_mod_decls}

pub const MIGRATIONS: &[Migration] = &[
    Migration {{ to_version: "0.2.0", apply: v0_2::migrate }},
{extra_entries}];

pub struct Migration {{
    pub to_version: &'static str,
    pub apply: fn(),
}}
"""

V0_2_BODY = "//! placeholder migration\npub fn migrate() {}\n"


def _write_repo_baseline(repo: Path, *, version: str = "0.2.0", extra_field: str = "") -> None:
    """Write the baseline: lib.rs with KCIR_VERSION, a kcir/project.rs,
    and migrations/{mod.rs, v0_2.rs}."""
    lib = repo / "crates" / "ki" / "src" / "lib.rs"
    lib.parent.mkdir(parents=True, exist_ok=True)
    lib.write_text(LIB_TEMPLATE.format(version=version))
    kcir_dir = repo / "crates" / "ki" / "src" / "kcir"
    kcir_dir.mkdir(parents=True, exist_ok=True)
    (kcir_dir / "project.rs").write_text(KCIR_FIELD_TEMPLATE.format(extra=extra_field))
    mig_dir = kcir_dir / "migrations"
    mig_dir.mkdir(parents=True, exist_ok=True)
    (mig_dir / "mod.rs").write_text(
        MIGRATIONS_MOD_TEMPLATE.format(extra_mod_decls="", extra_entries="")
    )
    (mig_dir / "v0_2.rs").write_text(V0_2_BODY)


def _commit_all(repo: Path, message: str) -> None:
    env = _git_env()
    _run_git(repo, "add", "-A", env=env)
    _run_git(repo, "commit", "-q", "--allow-empty", "-m", message, env=env)


def _run_gate(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = _git_env()
    env["KICLAUDE_REPO_ROOT"] = str(repo)
    return subprocess.run(
        [sys.executable, str(SCRIPT_PATH), *args],
        cwd=repo,
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )


# ---------------------------------------------------------------------
# Scenarios.
# ---------------------------------------------------------------------


def test_no_kcir_change_passes(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    _write_repo_baseline(repo)
    _commit_all(repo, "baseline")
    # Add a non-KCIR file change.
    (repo / "README.md").write_text("hello")
    _commit_all(repo, "doc-only change")
    result = _run_gate(repo, "--base", "HEAD~1", "--head", "HEAD")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "policy not triggered" in result.stdout


def test_kcir_change_without_version_bump_fails(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    _write_repo_baseline(repo)
    _commit_all(repo, "baseline")
    # Edit kcir/project.rs but leave KCIR_VERSION as 0.2.0.
    (repo / "crates" / "ki" / "src" / "kcir" / "project.rs").write_text(
        KCIR_FIELD_TEMPLATE.format(extra="    pub note: String,\n")
    )
    _commit_all(repo, "add Project.note without version bump")
    result = _run_gate(repo, "--base", "HEAD~1", "--head", "HEAD")
    assert result.returncode == 1, result.stdout + result.stderr
    assert "KCIR_VERSION must be bumped" in result.stderr


def test_kcir_change_with_bump_but_no_migration_fails(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    _write_repo_baseline(repo)
    _commit_all(repo, "baseline")
    (repo / "crates" / "ki" / "src" / "kcir" / "project.rs").write_text(
        KCIR_FIELD_TEMPLATE.format(extra="    pub note: String,\n")
    )
    (repo / "crates" / "ki" / "src" / "lib.rs").write_text(
        LIB_TEMPLATE.format(version="0.3.0")
    )
    _commit_all(repo, "bump version but forget migration")
    result = _run_gate(repo, "--base", "HEAD~1", "--head", "HEAD")
    assert result.returncode == 1, result.stdout + result.stderr
    assert "must include a new migration" in result.stderr


def test_kcir_change_with_bump_and_full_migration_passes(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    _write_repo_baseline(repo)
    _commit_all(repo, "baseline")
    # Edit kcir/project.rs + bump version + add migration file + entry.
    (repo / "crates" / "ki" / "src" / "kcir" / "project.rs").write_text(
        KCIR_FIELD_TEMPLATE.format(extra="    pub note: String,\n")
    )
    (repo / "crates" / "ki" / "src" / "lib.rs").write_text(
        LIB_TEMPLATE.format(version="0.3.0")
    )
    (repo / "crates" / "ki" / "src" / "kcir" / "migrations" / "v0_3.rs").write_text(
        "//! v0_3 migration stub.\npub fn migrate() {}\n"
    )
    (repo / "crates" / "ki" / "src" / "kcir" / "migrations" / "mod.rs").write_text(
        MIGRATIONS_MOD_TEMPLATE.format(
            extra_mod_decls="pub mod v0_3;\n",
            extra_entries='    Migration { to_version: "0.3.0", apply: v0_3::migrate },\n',
        )
    )
    _commit_all(repo, "0.3.0 schema bump with migration")
    result = _run_gate(repo, "--base", "HEAD~1", "--head", "HEAD")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "OK — KCIR change is paired with a version bump" in result.stdout


def test_migration_to_version_mismatch_fails(tmp_path: Path) -> None:
    """A migration entry that doesn't match the new KCIR_VERSION is
    rejected — otherwise schema-bump policy would be trivially
    satisfied by a stale entry."""
    repo = _init_repo(tmp_path)
    _write_repo_baseline(repo)
    _commit_all(repo, "baseline")
    (repo / "crates" / "ki" / "src" / "kcir" / "project.rs").write_text(
        KCIR_FIELD_TEMPLATE.format(extra="    pub note: String,\n")
    )
    (repo / "crates" / "ki" / "src" / "lib.rs").write_text(
        LIB_TEMPLATE.format(version="0.3.0")
    )
    (repo / "crates" / "ki" / "src" / "kcir" / "migrations" / "v0_4.rs").write_text(
        "//! mistaken migration\npub fn migrate() {}\n"
    )
    (repo / "crates" / "ki" / "src" / "kcir" / "migrations" / "mod.rs").write_text(
        MIGRATIONS_MOD_TEMPLATE.format(
            extra_mod_decls="pub mod v0_4;\n",
            extra_entries='    Migration { to_version: "0.4.0", apply: v0_4::migrate },\n',
        )
    )
    _commit_all(repo, "0.3.0 version bump but 0.4.0 migration entry")
    result = _run_gate(repo, "--base", "HEAD~1", "--head", "HEAD")
    assert result.returncode == 1, result.stdout + result.stderr
    assert "to_version` set does not contain" in result.stderr


def test_check_only_never_exits_nonzero(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    _write_repo_baseline(repo)
    _commit_all(repo, "baseline")
    (repo / "crates" / "ki" / "src" / "kcir" / "project.rs").write_text(
        KCIR_FIELD_TEMPLATE.format(extra="    pub note: String,\n")
    )
    _commit_all(repo, "kcir change without bump")
    result = _run_gate(repo, "--base", "HEAD~1", "--head", "HEAD", "--check-only")
    assert result.returncode == 0
    assert "VIOLATION" in result.stderr


def test_missing_base_ref_yields_exit_2(tmp_path: Path) -> None:
    """If every candidate base ref is unreachable, the gate exits 2
    rather than silently passing."""
    # Empty repo — no commits at all, so `main` / `HEAD~1` /
    # `origin/main` are all unreachable. We pass an explicit
    # `--base` for an unknown ref to be sure no fallback works.
    repo = tmp_path / "empty"
    repo.mkdir()
    env = _git_env()
    _run_git(repo, "init", "-q", "-b", "main", env=env)
    result = _run_gate(repo, "--base", "does-not-exist", "--head", "HEAD")
    assert result.returncode == 2, result.stdout + result.stderr
    assert "could not resolve any base ref" in result.stderr


@pytest.mark.parametrize(
    "a,b,expected",
    [
        ("0.2.0", "0.1.99", True),
        ("0.2.0", "0.2.0", False),
        ("0.2.0", "0.2.1", False),
        ("1.0.0", "0.99.99", True),
        ("0.3.0-alpha", "0.3.0", False),  # pre-release sorts after release in our tuple
    ],
)
def test_semver_comparison(a: str, b: str, expected: bool) -> None:
    # Import the helper from the module under test.
    sys.path.insert(0, str(SCRIPT_PATH.parent))
    try:
        import kcir_schema_check  # type: ignore[import-not-found]
    finally:
        sys.path.pop(0)
    assert kcir_schema_check._greater(a, b) is expected
