"""Content-addressed object store (SPEC §6.4.4 + §7.4 + D7).

A small, pluggable blob store used by the cloud-sync (FR-007) and
read-only share-link (FR-080) features. Every object is keyed by the
SHA-256 of its bytes, so writes are idempotent (the same content always
lands at the same key) and a key is a tamper-evident content hash.

Two backends sit behind the [`ObjectStore`][ObjectStore] ABC:

- [`LocalFsObjectStore`][LocalFsObjectStore] — sha256-sharded files
  under a root directory. Pure stdlib; this is the default and keeps the
  install local-first (first principle #8).
- [`S3ObjectStore`][S3ObjectStore] — an S3 bucket via `boto3`, imported
  lazily so the `boto3` dependency is only needed when cloud storage is
  actually selected (`pip install kiclaude-kiserver[s3]`).

[`build_object_store`][build_object_store] picks a backend from the
environment so the rest of kiserver never hard-codes one.
"""

from __future__ import annotations

import hashlib
import os
import tempfile
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

# A SHA-256 hex digest is 64 lowercase hex chars.
_KEY_LEN = 64


def content_key(data: bytes) -> str:
    """SHA-256 hex digest of `data` — the content-addressed object key."""
    return hashlib.sha256(data).hexdigest()


def _is_valid_key(key: str) -> bool:
    return len(key) == _KEY_LEN and all(c in "0123456789abcdef" for c in key)


class ObjectStore(ABC):
    """Content-addressed blob store. Keys are SHA-256 hex digests."""

    @abstractmethod
    def put(self, data: bytes) -> str:
        """Store `data`; return its content key. Idempotent: storing the
        same bytes twice is a no-op that returns the same key."""

    @abstractmethod
    def get(self, key: str) -> bytes | None:
        """Return the bytes stored under `key`, or `None` if absent."""

    @abstractmethod
    def exists(self, key: str) -> bool:
        """Whether an object is stored under `key`."""

    @abstractmethod
    def delete(self, key: str) -> bool:
        """Delete `key`; return whether it existed beforehand."""


class LocalFsObjectStore(ObjectStore):
    """Filesystem backend: objects sharded as `<root>/<ab>/<cd>/<key>`.

    The two-level fan-out keeps any single directory from accumulating
    millions of entries. Writes are atomic (temp file + `os.replace`) so
    a crash mid-write never leaves a half-object at a content key.
    """

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path_for(self, key: str) -> Path:
        return self.root / key[:2] / key[2:4] / key

    def put(self, data: bytes) -> str:
        key = content_key(data)
        dest = self._path_for(key)
        if dest.exists():
            return key  # write-once: identical content already present.
        dest.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(dir=dest.parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
            os.replace(tmp_name, dest)
        except BaseException:
            # Clean up the temp file on any failure so we don't leak it.
            try:
                os.unlink(tmp_name)
            except FileNotFoundError:
                pass
            raise
        return key

    def get(self, key: str) -> bytes | None:
        if not _is_valid_key(key):
            return None
        path = self._path_for(key)
        try:
            return path.read_bytes()
        except FileNotFoundError:
            return None

    def exists(self, key: str) -> bool:
        return _is_valid_key(key) and self._path_for(key).is_file()

    def delete(self, key: str) -> bool:
        if not _is_valid_key(key):
            return False
        path = self._path_for(key)
        try:
            path.unlink()
            return True
        except FileNotFoundError:
            return False


class S3ObjectStore(ObjectStore):
    """S3 backend. Objects live at `<prefix><ab>/<key>` in `bucket`.

    `boto3` is imported lazily in `__init__` so the dependency is only
    required when an S3 store is actually constructed. Pass an explicit
    `client` (e.g. a `moto`-mocked one) to bypass real-AWS credential
    discovery in tests.
    """

    def __init__(
        self, bucket: str, *, prefix: str = "", client: Any | None = None
    ) -> None:
        if not bucket:
            raise ValueError("S3ObjectStore requires a non-empty bucket name")
        self.bucket = bucket
        # Normalise prefix to "" or "dir/".
        self.prefix = (prefix.rstrip("/") + "/") if prefix else ""
        if client is not None:
            self._client = client
        else:
            try:
                import boto3
            except ImportError as exc:  # pragma: no cover - exercised via the s3 extra
                raise RuntimeError(
                    "S3ObjectStore needs boto3; install the cloud extra: "
                    "pip install 'kiclaude-kiserver[s3]'"
                ) from exc
            self._client = boto3.client("s3")

    def _s3_key(self, key: str) -> str:
        return f"{self.prefix}{key[:2]}/{key}"

    def put(self, data: bytes) -> str:
        key = content_key(data)
        s3_key = self._s3_key(key)
        # Idempotent: skip the upload when the object is already present.
        if not self._head(s3_key):
            self._client.put_object(Bucket=self.bucket, Key=s3_key, Body=data)
        return key

    def get(self, key: str) -> bytes | None:
        if not _is_valid_key(key):
            return None
        try:
            resp = self._client.get_object(Bucket=self.bucket, Key=self._s3_key(key))
        except Exception as exc:
            if _is_not_found(exc):
                return None
            raise
        body = resp["Body"].read()
        return bytes(body)

    def exists(self, key: str) -> bool:
        return _is_valid_key(key) and self._head(self._s3_key(key))

    def delete(self, key: str) -> bool:
        if not _is_valid_key(key):
            return False
        s3_key = self._s3_key(key)
        if not self._head(s3_key):
            return False
        self._client.delete_object(Bucket=self.bucket, Key=s3_key)
        return True

    def _head(self, s3_key: str) -> bool:
        try:
            self._client.head_object(Bucket=self.bucket, Key=s3_key)
            return True
        except Exception as exc:
            if _is_not_found(exc):
                return False
            raise


def _is_not_found(exc: Exception) -> bool:
    """True if a boto3 exception represents a missing object/key."""
    response = getattr(exc, "response", None)
    if isinstance(response, dict):
        code = str(response.get("Error", {}).get("Code", ""))
        status = response.get("ResponseMetadata", {}).get("HTTPStatusCode")
        if code in {"NoSuchKey", "NoSuchBucket", "404", "NotFound"} or status == 404:
            return True
    return exc.__class__.__name__ in {"NoSuchKey", "NoSuchBucket"}


def default_object_root() -> Path:
    """Default local object-store root: `$KICLAUDE_OBJECT_ROOT` or
    `~/.cache/kiclaude/objects`."""
    env = os.environ.get("KICLAUDE_OBJECT_ROOT")
    if env:
        return Path(env).expanduser()
    return Path.home() / ".cache" / "kiclaude" / "objects"


def build_object_store() -> ObjectStore:
    """Construct the object store selected by the environment.

    - `KICLAUDE_OBJECT_STORE=s3` → [`S3ObjectStore`] on bucket
      `$KICLAUDE_S3_BUCKET` (prefix `$KICLAUDE_S3_PREFIX`).
    - anything else (default) → [`LocalFsObjectStore`] rooted at
      [`default_object_root`].
    """
    backend = os.environ.get("KICLAUDE_OBJECT_STORE", "local").strip().lower()
    if backend == "s3":
        bucket = os.environ.get("KICLAUDE_S3_BUCKET", "")
        prefix = os.environ.get("KICLAUDE_S3_PREFIX", "")
        return S3ObjectStore(bucket, prefix=prefix)
    return LocalFsObjectStore(default_object_root())


__all__ = [
    "LocalFsObjectStore",
    "ObjectStore",
    "S3ObjectStore",
    "build_object_store",
    "content_key",
    "default_object_root",
]
