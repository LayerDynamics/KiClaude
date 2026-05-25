"""Tests for the FR-007 content-addressed cloud-sync service.

Uses the local-FS object store so the round-trip is exercised end to
end without cloud infra; the store is pluggable, so S3 behaves
identically (see test_object_store.py's shared contract).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from kiserver.object_store import LocalFsObjectStore, content_key
from kiserver.sync import CloudSync, SyncManifest

_REPO_ROOT = Path(__file__).resolve().parents[3]


def _make_project(root: Path) -> Path:
    proj = root / "proj"
    proj.mkdir()
    (proj / "p.kicad_pro").write_text('{"meta":{"filename":"p.kicad_pro"}}')
    (proj / "p.kicad_pcb").write_bytes(b"(kicad_pcb (version 20240108) (generator kiclaude))\n")
    (proj / "p.kicad_sch").write_text("(kicad_sch)\n")
    (proj / "fp-lib-table").write_text("(fp_lib_table\n  (version 7)\n)\n")
    (proj / "sym-lib-table").write_text("(sym_lib_table\n  (version 7)\n)\n")
    (proj / "notes.txt").write_text("not a kicad file — must not sync")
    return proj


def test_push_pull_round_trips_every_kicad_file(tmp_path: Path) -> None:
    src = _make_project(tmp_path)
    sync = CloudSync(LocalFsObjectStore(tmp_path / "store"))

    manifest_key, manifest = sync.push_dir(
        project_id="p1", project_name="p", project_dir=src
    )

    # The non-KiCad file is excluded; everything else is captured.
    assert set(manifest.files) == {
        "p.kicad_pro",
        "p.kicad_pcb",
        "p.kicad_sch",
        "fp-lib-table",
        "sym-lib-table",
    }
    # The manifest key is the sha256 of the manifest bytes (content-addressed).
    assert len(manifest_key) == 64

    dest = tmp_path / "restored"
    written = sync.pull_dir(manifest_key=manifest_key, dest_dir=dest)
    assert sorted(written) == sorted(manifest.files)
    for rel in manifest.files:
        assert (dest / rel).read_bytes() == (src / rel).read_bytes(), rel


def test_file_blobs_are_content_addressed_and_deduped(tmp_path: Path) -> None:
    src = _make_project(tmp_path)
    sync = CloudSync(LocalFsObjectStore(tmp_path / "store"))

    _k1, m1 = sync.push_dir(project_id="p1", project_name="p", project_dir=src)
    _k2, m2 = sync.push_dir(project_id="p1", project_name="p", project_dir=src)

    # Unchanged file content → identical blob keys across pushes (dedup).
    assert m1.files == m2.files
    # Each file's key is the sha256 of its bytes.
    assert m1.files["p.kicad_pcb"] == content_key((src / "p.kicad_pcb").read_bytes())


def test_push_pull_real_esp32_c6_rf_example(tmp_path: Path) -> None:
    example = _REPO_ROOT / "examples" / "esp32_c6_rf"
    assert example.is_dir(), f"example missing: {example}"
    sync = CloudSync(LocalFsObjectStore(tmp_path / "store"))

    manifest_key, manifest = sync.push_dir(
        project_id="m5", project_name="esp32_c6_rf", project_dir=example
    )
    assert "esp32_c6_rf.kicad_pcb" in manifest.files
    assert "esp32_c6_rf.kicad_sch" in manifest.files

    dest = tmp_path / "restored"
    sync.pull_dir(manifest_key=manifest_key, dest_dir=dest)
    assert (
        (dest / "esp32_c6_rf.kicad_pcb").read_bytes()
        == (example / "esp32_c6_rf.kicad_pcb").read_bytes()
    )


def test_pull_unknown_manifest_raises_keyerror(tmp_path: Path) -> None:
    sync = CloudSync(LocalFsObjectStore(tmp_path / "store"))
    with pytest.raises(KeyError):
        sync.pull_dir(manifest_key=content_key(b"nope"), dest_dir=tmp_path / "out")


def test_pull_with_missing_blob_raises_integrity_error(tmp_path: Path) -> None:
    src = _make_project(tmp_path)
    store = LocalFsObjectStore(tmp_path / "store")
    sync = CloudSync(store)
    manifest_key, manifest = sync.push_dir(
        project_id="p1", project_name="p", project_dir=src
    )
    # Corrupt the store: drop one referenced blob.
    store.delete(manifest.files["p.kicad_pcb"])
    with pytest.raises(FileNotFoundError):
        sync.pull_dir(manifest_key=manifest_key, dest_dir=tmp_path / "out")


def test_load_manifest_rejects_non_manifest_blob(tmp_path: Path) -> None:
    store = LocalFsObjectStore(tmp_path / "store")
    sync = CloudSync(store)
    key = store.put(b"just some bytes, not a manifest")
    assert sync.load_manifest(key) is None


def test_push_rejects_non_directory(tmp_path: Path) -> None:
    sync = CloudSync(LocalFsObjectStore(tmp_path / "store"))
    f = tmp_path / "afile"
    f.write_text("x")
    with pytest.raises(NotADirectoryError):
        sync.push_dir(project_id="p", project_name="p", project_dir=f)


def test_manifest_dataclass_shape() -> None:
    m = SyncManifest(
        kind="kiclaude.sync.manifest/1",
        project_id="p",
        project_name="proj",
        created_at="2026-05-25T00:00:00+00:00",
        files={"a.kicad_pcb": "f" * 64},
    )
    assert m.files["a.kicad_pcb"] == "f" * 64
