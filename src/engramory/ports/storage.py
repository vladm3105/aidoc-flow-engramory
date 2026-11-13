"""StoragePort — content-addressable blob storage interface (Rule D14a / D14b).

Implementations live in ``engramory.adapters.{dev,gcp,azure}``:
- ``PgBlobStorageAdapter``: PostgreSQL 18 4 MiB Chunked Blob CAS up to 20 GB+ (ts_blob_cold)
- ``LocalFsStorageAdapter``: Confined local filesystem / in-memory CAS for offline tests
- Cloud object adapters (GCS / Azure Blob) for BRD-02 cloud profiles

IMPORTANT: ``engramory.ports`` must remain 100% Python stdlib-only
(enforced by ``tests/unit/test_no_vendor_imports.py::test_ports_are_stdlib_only``).
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any, Protocol

VALID_STORAGE_TIERS = frozenset({"logged", "unlogged"})
VALID_BLOB_STATUSES = frozenset({"uploading", "ready", "failed"})


class StorageError(RuntimeError):
    """Base error for StoragePort operations."""


class BlobNotFoundError(StorageError, KeyError):
    """Requested blob SHA-256 or key does not exist in the caller's tenant."""


class BlobTruncatedError(StorageError):
    """Blob manifest exists with status='ready', but underlying chunks were truncated
    (e.g. after an unclean PostgreSQL crash on UNLOGGED kb_blob_chunks_large)."""


class BlobIntegrityError(StorageError):
    """Per-chunk or whole-blob SHA-256 digest mismatch."""


class BlobTooLargeError(StorageError, ValueError):
    """Blob exceeds the configured maximum byte size (25 GiB)."""


@dataclass(frozen=True, slots=True)
class KBBlob:
    """Content-addressable blob manifest record (kb_blobs table, migration 0010)."""

    tenant_id: str
    sha256: str
    media_type: str
    byte_size: int
    chunk_size: int = 4_194_304
    chunk_count: int = 0
    storage_tier: str = "logged"
    status: str = "ready"
    project_id: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.tenant_id:
            raise ValueError("KBBlob.tenant_id is required")
        if len(self.sha256) != 64 or any(c not in "0123456789abcdef" for c in self.sha256.lower()):
            raise ValueError(f"KBBlob.sha256 must be a 64-char hex digest, got {self.sha256!r}")
        object.__setattr__(self, "sha256", self.sha256.lower())
        if not self.media_type:
            raise ValueError("KBBlob.media_type is required")
        if self.byte_size < 0:
            raise ValueError("KBBlob.byte_size must be >= 0")
        if self.chunk_size <= 0:
            raise ValueError("KBBlob.chunk_size must be > 0")
        if self.chunk_count < 0:
            raise ValueError("KBBlob.chunk_count must be >= 0")
        if self.storage_tier not in VALID_STORAGE_TIERS:
            raise ValueError(
                f"KBBlob.storage_tier must be one of {sorted(VALID_STORAGE_TIERS)}, "
                f"got {self.storage_tier!r}"
            )
        if self.status not in VALID_BLOB_STATUSES:
            raise ValueError(
                f"KBBlob.status must be one of {sorted(VALID_BLOB_STATUSES)}, got {self.status!r}"
            )
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))

    @property
    def chunk_size_bytes(self) -> int:
        """Alias for ``chunk_size`` in bytes."""
        return self.chunk_size

    @property
    def blob_uri(self) -> str:
        """Canonical content-addressable URI for this tenant + SHA-256."""
        return f"pgblob://{self.tenant_id}/{self.sha256}"


class StoragePort(Protocol):
    async def put(
        self,
        *,
        key: str,
        data: bytes,
        content_type: str | None = None,
        tenant_id: str = "default",
        project_id: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> None:
        """Store an in-memory object at `key` (or by its SHA-256 digest)."""
        ...

    async def get(self, *, key: str, tenant_id: str = "default") -> bytes:
        """Fetch the full object bytes at `key`. Raises BlobNotFoundError if missing."""
        ...

    async def delete(self, *, key: str, tenant_id: str = "default") -> None:
        """Remove the object and its chunks at `key` within `tenant_id` (idempotent)."""
        ...

    async def exists(self, *, key: str, tenant_id: str = "default") -> bool:
        """Return True if a complete, non-truncated object exists at `key`."""
        ...

    async def head(self, *, key: str, tenant_id: str = "default") -> KBBlob | None:
        """Return the KBBlob manifest (verifying chunk completeness) or None if missing."""
        ...

    async def put_file(
        self,
        *,
        file_path: Path,
        content_type: str = "application/octet-stream",
        tenant_id: str = "default",
        project_id: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> KBBlob:
        """Stream a local file (up to 20 GB+) into content-addressable storage in O(1) RAM."""
        ...

    async def put_stream(
        self,
        *,
        key: str | None = None,
        stream: AsyncIterator[bytes] | Iterable[bytes],
        content_type: str = "application/octet-stream",
        tenant_id: str = "default",
        project_id: str | None = None,
        byte_size: int | None = None,
        expected_sha256: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> KBBlob:
        """Stream fixed 4 MiB chunks into content-addressable storage."""
        ...

    def get_stream(
        self,
        *,
        key: str,
        tenant_id: str = "default",
        chunk_batch_size: int = 4,
    ) -> AsyncIterator[bytes]:
        """Stream chunks for `key` in bounded keyset batches with per-chunk SHA-256 verification."""
        ...

    async def reconcile_truncated_blobs(self, *, tenant_id: str | None = None) -> int:
        """Mark status='failed' on any 'ready' UNLOGGED blob truncated on crash."""
        ...

    async def gc_orphan_chunks(self, *, older_than_seconds: int = 7200) -> int:
        """Delete stale 'uploading'/'failed' manifests and orphan chunk rows."""
        ...
