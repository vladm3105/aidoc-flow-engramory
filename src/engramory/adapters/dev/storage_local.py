"""LocalFsStorageAdapter — confined local filesystem / in-memory StoragePort adapter.

Provides a hermetic, zero-daemon implementation of the Rule D14b StoragePort contract
for unit tests, CI, and offline local execution, enforcing the exact same 4 MiB chunking,
25 GiB size cap, 50 MiB LOGGED/UNLOGGED tier classification, SHA-256 verification, and
tenant isolation rules as PgBlobStorageAdapter.
"""

from __future__ import annotations

import hashlib
from collections.abc import AsyncIterator, Iterable, Mapping
from pathlib import Path
from typing import Any

from engramory.ports.storage import (
    BlobIntegrityError,
    BlobNotFoundError,
    BlobTooLargeError,
    BlobTruncatedError,
    KBBlob,
)

DEFAULT_CHUNK_SIZE = 4 * 1024 * 1024  # 4 MiB
UNLOGGED_THRESHOLD_BYTES = 50 * 1024 * 1024  # 50 MiB
MAX_BLOB_BYTES = 25 * 1024 * 1024 * 1024  # 25 GiB


def _normalize_key(key: str) -> str:
    """Extract the 64-char hex SHA-256 if key is a pgblob:// URI, else return key."""
    if key.startswith("pgblob://"):
        parts = key[len("pgblob://") :].strip("/").split("/")
        return parts[-1].lower()
    return key.lower() if len(key) == 64 else key


class LocalFsStorageAdapter:
    """In-memory / optional local-directory chunked CAS implementation of StoragePort."""

    def __init__(
        self,
        root_dir: Path | None = None,
        *,
        base_dir: Path | None = None,
        chunk_size: int = DEFAULT_CHUNK_SIZE,
        chunk_size_bytes: int | None = None,
        max_blob_bytes: int = MAX_BLOB_BYTES,
    ) -> None:
        eff_root = root_dir if root_dir is not None else base_dir
        self._root_dir = eff_root.resolve() if eff_root is not None else None
        if self._root_dir is not None:
            self._root_dir.mkdir(parents=True, exist_ok=True)
        self._chunk_size = chunk_size_bytes if chunk_size_bytes is not None else chunk_size
        self._max_blob_bytes = max_blob_bytes
        # (tenant_id, key) -> KBBlob
        self._manifests: dict[tuple[str, str], KBBlob] = {}
        # (tenant_id, sha256) -> list[tuple[bytes, bytes]]  # (chunk_sha256_bytes, data)
        self._chunks: dict[tuple[str, str], list[tuple[bytes, bytes]]] = {}
        # Optional alias mapping (tenant_id, arbitrary_key) -> sha256
        self._aliases: dict[tuple[str, str], str] = {}

    async def close(self) -> None:
        """No-op close for adapter lifecycle parity."""

    def _resolve_sha256(self, tenant_id: str, key: str) -> str:
        norm = _normalize_key(key)
        if (tenant_id, norm) in self._manifests:
            return norm
        alias = self._aliases.get((tenant_id, key)) or self._aliases.get((tenant_id, norm))
        if alias is not None:
            return alias
        return norm

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
        blob = await self.put_stream(
            key=key,
            stream=[data],
            content_type=content_type or "application/octet-stream",
            tenant_id=tenant_id,
            project_id=project_id,
            byte_size=len(data),
            metadata=metadata,
        )
        self._aliases[(tenant_id, key)] = blob.sha256

    async def put_file(
        self,
        *,
        file_path: Path,
        content_type: str = "application/octet-stream",
        tenant_id: str = "default",
        project_id: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> KBBlob:
        if not file_path.exists():
            raise FileNotFoundError(f"Blob source file not found: {file_path}")
        if file_path.is_symlink():
            raise PermissionError(f"Refusing to ingest symlinked blob path: {file_path}")
        st_size = file_path.stat().st_size
        if st_size > self._max_blob_bytes:
            raise BlobTooLargeError(
                f"File size {st_size} exceeds maximum {self._max_blob_bytes} bytes"
            )
        with file_path.open("rb") as f:
            expected_sha256 = hashlib.file_digest(f, "sha256").hexdigest()

        existing = await self.head(key=expected_sha256, tenant_id=tenant_id)
        if existing is not None and existing.status == "ready":
            return existing

        def _iter_chunks() -> Iterable[bytes]:
            with file_path.open("rb") as fh:
                while chunk := fh.read(self._chunk_size):
                    yield chunk

        return await self.put_stream(
            key=expected_sha256,
            stream=_iter_chunks(),
            content_type=content_type,
            tenant_id=tenant_id,
            project_id=project_id,
            byte_size=st_size,
            expected_sha256=expected_sha256,
            metadata=metadata,
        )

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
        if byte_size is not None and byte_size > self._max_blob_bytes:
            raise BlobTooLargeError(
                f"Blob size {byte_size} exceeds maximum {self._max_blob_bytes} bytes"
            )

        whole_hasher = hashlib.sha256()
        buffer = bytearray()
        stored_chunks: list[tuple[bytes, bytes]] = []
        total_bytes = 0

        async def _consume() -> None:
            nonlocal total_bytes
            if isinstance(stream, AsyncIterator):
                async for piece in stream:
                    if not piece:
                        continue
                    total_bytes += len(piece)
                    if total_bytes > self._max_blob_bytes:
                        raise BlobTooLargeError(
                            f"Stream exceeded maximum {self._max_blob_bytes} bytes"
                        )
                    whole_hasher.update(piece)
                    buffer.extend(piece)
                    while len(buffer) >= self._chunk_size:
                        chunk_bytes = bytes(buffer[: self._chunk_size])
                        del buffer[: self._chunk_size]
                        c_digest = hashlib.sha256(chunk_bytes).digest()
                        stored_chunks.append((c_digest, chunk_bytes))
            else:
                for piece in stream:
                    if not piece:
                        continue
                    total_bytes += len(piece)
                    if total_bytes > self._max_blob_bytes:
                        raise BlobTooLargeError(
                            f"Stream exceeded maximum {self._max_blob_bytes} bytes"
                        )
                    whole_hasher.update(piece)
                    buffer.extend(piece)
                    while len(buffer) >= self._chunk_size:
                        chunk_bytes = bytes(buffer[: self._chunk_size])
                        del buffer[: self._chunk_size]
                        c_digest = hashlib.sha256(chunk_bytes).digest()
                        stored_chunks.append((c_digest, chunk_bytes))

        await _consume()
        if buffer:
            chunk_bytes = bytes(buffer)
            c_digest = hashlib.sha256(chunk_bytes).digest()
            stored_chunks.append((c_digest, chunk_bytes))

        computed_sha256 = whole_hasher.hexdigest()
        if expected_sha256 is not None and computed_sha256 != expected_sha256.lower():
            raise BlobIntegrityError(
                f"Blob SHA-256 mismatch: expected {expected_sha256.lower()}, "
                f"computed {computed_sha256}"
            )
        if byte_size is not None and total_bytes != byte_size:
            raise BlobIntegrityError(
                f"Blob byte_size mismatch: expected {byte_size}, got {total_bytes}"
            )

        tier = "unlogged" if total_bytes > UNLOGGED_THRESHOLD_BYTES else "logged"
        blob = KBBlob(
            tenant_id=tenant_id,
            sha256=computed_sha256,
            media_type=content_type or "application/octet-stream",
            byte_size=total_bytes,
            chunk_size=self._chunk_size,
            chunk_count=len(stored_chunks),
            storage_tier=tier,
            status="ready",
            project_id=project_id,
            metadata=metadata or {},
        )
        self._manifests[(tenant_id, computed_sha256)] = blob
        self._chunks[(tenant_id, computed_sha256)] = stored_chunks
        if key:
            self._aliases[(tenant_id, key)] = computed_sha256
        return blob

    async def head(self, *, key: str, tenant_id: str = "default") -> KBBlob | None:
        sha256 = self._resolve_sha256(tenant_id, key)
        blob = self._manifests.get((tenant_id, sha256))
        if blob is None:
            return None
        chunks = self._chunks.get((tenant_id, sha256), [])
        if blob.status == "ready" and len(chunks) != blob.chunk_count:
            failed = KBBlob(
                tenant_id=blob.tenant_id,
                sha256=blob.sha256,
                media_type=blob.media_type,
                byte_size=blob.byte_size,
                chunk_size=blob.chunk_size,
                chunk_count=blob.chunk_count,
                storage_tier=blob.storage_tier,
                status="failed",
                project_id=blob.project_id,
                metadata=blob.metadata,
            )
            self._manifests[(tenant_id, sha256)] = failed
            raise BlobTruncatedError(
                f"Blob {sha256} in tenant {tenant_id!r} truncated: expected "
                f"{blob.chunk_count} chunks, found {len(chunks)}"
            )
        return blob

    async def exists(self, *, key: str, tenant_id: str = "default") -> bool:
        try:
            blob = await self.head(key=key, tenant_id=tenant_id)
            return blob is not None and blob.status == "ready"
        except BlobTruncatedError:
            return False

    async def get_stream(
        self,
        *,
        key: str,
        tenant_id: str = "default",
        chunk_batch_size: int = 4,
    ) -> AsyncIterator[bytes]:
        blob = await self.head(key=key, tenant_id=tenant_id)
        if blob is None or blob.status != "ready":
            raise BlobNotFoundError(f"Blob {key!r} not found in tenant {tenant_id!r}")
        chunks = self._chunks.get((tenant_id, blob.sha256), [])
        batch = max(1, chunk_batch_size)
        for start in range(0, len(chunks), batch):
            for idx, (expected_digest, data) in enumerate(
                chunks[start : start + batch], start=start
            ):
                actual_digest = hashlib.sha256(data).digest()
                if actual_digest != expected_digest:
                    raise BlobIntegrityError(
                        f"Corrupt chunk {idx} for blob {blob.sha256} in tenant {tenant_id!r}"
                    )
                yield data

    async def get(self, *, key: str, tenant_id: str = "default") -> bytes:
        out = bytearray()
        async for chunk in self.get_stream(key=key, tenant_id=tenant_id):
            out.extend(chunk)
        return bytes(out)

    async def delete(self, *, key: str, tenant_id: str = "default") -> None:
        sha256 = self._resolve_sha256(tenant_id, key)
        self._manifests.pop((tenant_id, sha256), None)
        self._chunks.pop((tenant_id, sha256), None)
        self._aliases.pop((tenant_id, key), None)

    async def reconcile_truncated_blobs(self, *, tenant_id: str | None = None) -> int:
        reconciled = 0
        for (tid, sha), blob in list(self._manifests.items()):
            if tenant_id is not None and tid != tenant_id:
                continue
            if blob.status == "ready":
                chunks = self._chunks.get((tid, sha), [])
                if len(chunks) < blob.chunk_count:
                    self._manifests[(tid, sha)] = KBBlob(
                        tenant_id=blob.tenant_id,
                        sha256=blob.sha256,
                        media_type=blob.media_type,
                        byte_size=blob.byte_size,
                        chunk_size=blob.chunk_size,
                        chunk_count=blob.chunk_count,
                        storage_tier=blob.storage_tier,
                        status="failed",
                        project_id=blob.project_id,
                        metadata=blob.metadata,
                    )
                    reconciled += 1
        return reconciled

    async def gc_orphan_chunks(self, *, older_than_seconds: int = 7200) -> int:
        removed = 0
        for (tid, sha), blob in list(self._manifests.items()):
            if blob.status in ("uploading", "failed"):
                self._manifests.pop((tid, sha), None)
                self._chunks.pop((tid, sha), None)
                removed += 1
        for key_pair in list(self._chunks.keys()):
            if key_pair not in self._manifests:
                self._chunks.pop(key_pair, None)
                removed += 1
        return removed
