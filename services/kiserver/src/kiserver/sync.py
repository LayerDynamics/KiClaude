"""Cloud project sync (FR-007) — content-addressed, off by default.

Pushes a KiCad project's on-disk files to a content-addressed
[`ObjectStore`][kiserver.object_store.ObjectStore] and pulls them back.
We sync the **actual KiCad files** (not KCIR) so first principle #1
holds — the canonical artifact crossing the wire is the same
`.kicad_pro` / `.kicad_sch` / `.kicad_pcb` a `kicad-cli` user reads (D7:
KCIR is never the persisted form).

Each push writes one immutable [`SyncManifest`] object whose content key
is the version id. A team's "latest" pointer (Postgres metadata in the
full FR-007 design) is the caller's concern — `push` simply returns the
manifest key for the caller to record. The store backend (local FS vs
S3) is selected by the environment, so sync stays local-first unless a
team opts into cloud storage (FP#8).
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from .object_store import ObjectStore

# The project files that make up a portable KiCad project. The `.prl` is
# per-user local prefs but we sync it so a pulled project is a faithful
# copy; lib tables resolve the project's libraries.
_SYNCED_SUFFIXES = (".kicad_pro", ".kicad_sch", ".kicad_pcb", ".kicad_prl")
_SYNCED_NAMES = ("fp-lib-table", "sym-lib-table")

_MANIFEST_KIND = "kiclaude.sync.manifest/1"


@dataclass(frozen=True)
class SyncManifest:
    """The set of files (by content key) that make up one synced
    project version."""

    kind: str
    project_id: str
    project_name: str
    created_at: str
    files: dict[str, str]  # project-relative path → content key


def _is_synced_file(path: Path) -> bool:
    return path.is_file() and (
        path.suffix in _SYNCED_SUFFIXES or path.name in _SYNCED_NAMES
    )


class CloudSync:
    """Push/pull a project directory to/from an object store."""

    def __init__(self, store: ObjectStore) -> None:
        self.store = store

    def push_dir(
        self, *, project_id: str, project_name: str, project_dir: Path
    ) -> tuple[str, SyncManifest]:
        """Store every KiCad file under `project_dir`, then store a
        manifest. Returns `(manifest_key, manifest)`.

        Raises `NotADirectoryError` if `project_dir` is not a directory.
        """
        project_dir = Path(project_dir)
        if not project_dir.is_dir():
            raise NotADirectoryError(f"not a directory: {project_dir}")

        files: dict[str, str] = {}
        for path in sorted(project_dir.rglob("*")):
            if not _is_synced_file(path):
                continue
            rel = path.relative_to(project_dir).as_posix()
            files[rel] = self.store.put(path.read_bytes())

        manifest = SyncManifest(
            kind=_MANIFEST_KIND,
            project_id=project_id,
            project_name=project_name,
            created_at=datetime.now(UTC).isoformat(),
            files=files,
        )
        manifest_key = self.store.put(_encode_manifest(manifest))
        return manifest_key, manifest

    def load_manifest(self, manifest_key: str) -> SyncManifest | None:
        """Fetch and decode a manifest, or `None` if absent/invalid."""
        blob = self.store.get(manifest_key)
        if blob is None:
            return None
        return _decode_manifest(blob)

    def pull_dir(self, *, manifest_key: str, dest_dir: Path) -> list[str]:
        """Restore the files named by `manifest_key` into `dest_dir`.

        Returns the project-relative paths written, sorted. Raises
        `KeyError` if the manifest is missing and `FileNotFoundError` if
        a referenced blob is missing from the store (integrity failure).
        """
        manifest = self.load_manifest(manifest_key)
        if manifest is None:
            raise KeyError(f"unknown manifest: {manifest_key}")
        dest_dir = Path(dest_dir)
        dest_dir.mkdir(parents=True, exist_ok=True)

        # Pre-check that all blobs exist to prevent partial/corrupted restores
        for rel, content_key in manifest.files.items():
            if not self.store.exists(content_key):
                raise FileNotFoundError(
                    f"blob {content_key} for {rel} missing from object store"
                )

        written: list[str] = []
        for rel, content_key in sorted(manifest.files.items()):
            blob = self.store.get(content_key)
            if blob is None:
                raise FileNotFoundError(
                    f"blob {content_key} for {rel} missing from object store"
                )
            out = dest_dir / rel
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(blob)
            written.append(rel)
        return written


def _encode_manifest(manifest: SyncManifest) -> bytes:
    return json.dumps(asdict(manifest), sort_keys=True).encode("utf-8")


def _decode_manifest(blob: bytes) -> SyncManifest | None:
    try:
        doc = json.loads(blob)
    except (ValueError, UnicodeDecodeError):
        return None
    if not isinstance(doc, dict) or doc.get("kind") != _MANIFEST_KIND:
        return None
    files = doc.get("files")
    if not isinstance(files, dict):
        return None
    return SyncManifest(
        kind=_MANIFEST_KIND,
        project_id=str(doc.get("project_id", "")),
        project_name=str(doc.get("project_name", "")),
        created_at=str(doc.get("created_at", "")),
        files={str(k): str(v) for k, v in files.items()},
    )


__all__ = ["CloudSync", "SyncManifest"]
