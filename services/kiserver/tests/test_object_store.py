"""Contract tests for the content-addressed object store.

The same suite runs against both backends — `LocalFsObjectStore`
(tmp dir) and `S3ObjectStore` (moto-mocked S3) — so they're held to one
behavioural contract. Backend-specific details (FS sharding, the env
factory) get their own focused tests.
"""

from __future__ import annotations

from collections.abc import Iterator

import boto3
import pytest
from kiserver.object_store import (
    LocalFsObjectStore,
    ObjectStore,
    S3ObjectStore,
    build_object_store,
    content_key,
)
from moto import mock_aws

_BUCKET = "kiclaude-test-bucket"
_DATA = b'{"kcir_version":"0.5.0","name":"esp32_c6_rf"}'


@pytest.fixture(params=["local", "s3"])
def store(request: pytest.FixtureRequest, tmp_path) -> Iterator[ObjectStore]:  # type: ignore[no-untyped-def]
    if request.param == "local":
        yield LocalFsObjectStore(tmp_path)
        return
    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket=_BUCKET)
        yield S3ObjectStore(_BUCKET, prefix="snap", client=client)


# --- shared contract -------------------------------------------------------


def test_put_returns_sha256_key_and_get_round_trips(store: ObjectStore) -> None:
    key = store.put(_DATA)
    assert key == content_key(_DATA)
    assert store.get(key) == _DATA


def test_put_is_idempotent(store: ObjectStore) -> None:
    k1 = store.put(_DATA)
    k2 = store.put(_DATA)
    assert k1 == k2
    assert store.get(k1) == _DATA


def test_distinct_content_distinct_keys(store: ObjectStore) -> None:
    k1 = store.put(b"alpha")
    k2 = store.put(b"beta")
    assert k1 != k2
    assert store.get(k1) == b"alpha"
    assert store.get(k2) == b"beta"


def test_get_missing_returns_none(store: ObjectStore) -> None:
    absent = content_key(b"never stored")
    assert store.get(absent) is None


def test_exists_reflects_put_and_delete(store: ObjectStore) -> None:
    key = store.put(_DATA)
    assert store.exists(key) is True
    assert store.delete(key) is True
    assert store.exists(key) is False
    assert store.get(key) is None
    # Second delete is a no-op returning False.
    assert store.delete(key) is False


def test_empty_blob_round_trips(store: ObjectStore) -> None:
    key = store.put(b"")
    assert store.exists(key)
    assert store.get(key) == b""


@pytest.mark.parametrize("bad", ["", "not-a-key", "abc", "z" * 64, "A" * 64])
def test_invalid_keys_are_rejected_safely(store: ObjectStore, bad: str) -> None:
    assert store.get(bad) is None
    assert store.exists(bad) is False
    assert store.delete(bad) is False


# --- LocalFs specifics -----------------------------------------------------


def test_localfs_shards_by_hash_prefix(tmp_path) -> None:  # type: ignore[no-untyped-def]
    store = LocalFsObjectStore(tmp_path)
    key = store.put(_DATA)
    expected = tmp_path / key[:2] / key[2:4] / key
    assert expected.is_file()
    assert expected.read_bytes() == _DATA


# --- S3 specifics ----------------------------------------------------------


def test_s3_requires_bucket() -> None:
    with pytest.raises(ValueError, match="bucket"):
        S3ObjectStore("", client=object())


def test_s3_object_lands_under_prefix() -> None:
    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket=_BUCKET)
        store = S3ObjectStore(_BUCKET, prefix="snap", client=client)
        key = store.put(_DATA)
        listed = client.list_objects_v2(Bucket=_BUCKET)
        names = [o["Key"] for o in listed.get("Contents", [])]
        assert names == [f"snap/{key[:2]}/{key}"]


# --- env factory -----------------------------------------------------------


def test_build_object_store_defaults_to_local(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.delenv("KICLAUDE_OBJECT_STORE", raising=False)
    monkeypatch.setenv("KICLAUDE_OBJECT_ROOT", str(tmp_path / "objects"))
    store = build_object_store()
    assert isinstance(store, LocalFsObjectStore)
    key = store.put(_DATA)
    assert store.get(key) == _DATA


def test_build_object_store_selects_s3(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KICLAUDE_OBJECT_STORE", "s3")
    monkeypatch.setenv("KICLAUDE_S3_BUCKET", _BUCKET)
    monkeypatch.setenv("KICLAUDE_S3_PREFIX", "proj")
    with mock_aws():
        store = build_object_store()
        assert isinstance(store, S3ObjectStore)
        assert store.bucket == _BUCKET
        assert store.prefix == "proj/"
