"""LocalFsStorageAdapter — confined local filesystem / in-memory StoragePort adapter.

Provides a hermetic, zero-daemon implementation of the Rule D14b StoragePort contract
for unit tests, CI, and offline local execution, enforcing the same 4 MiB chunking,
25 GiB size cap, 50 MiB LOGGED/UNLOGGED tier classification, SHA-256 verification,
per-project CAS ownership, and tenant isolation rules as PgBlobStorageAdapter.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
import tempfile
import time
from collections.abc import AsyncIterator, Iterable, Mapping
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from engramory.core.docs_tools import has_symlink_component
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
SPOOL_MAX_MEMORY_BYTES = 16 * 1024 * 1024


def _is_sha256_hex(val: str) -> bool:
    return len(val) == 64 and all(c in "0123456789abcdef" for c in val.lower())


def _parse_blob_key(key: str, *, tenant_id: str | None = None) -> tuple[str | None, str | None]:
    """Return ``(sha256_or_none, alias_or_none)`` rejecting cross-tenant pgblob:// URIs."""
    if key.startswith("pgblob://"):
        parts = [p for p in key[len("pgblob://") :].strip("/").split("/") if p]
        if len(parts) != 2:
            return None, None
        uri_tenant, candidate = parts[0], parts[1].lower()
        if tenant_id is not None and uri_tenant != tenant_id:
            return None, None
        return (candidate, None) if _is_sha256_hex(candidate) else (None, None)
    if _is_sha256_hex(key):
        return key.lower(), None
    clean = key.strip()
    return (None, clean) if clean else (None, None)


def _atomic_write_bytes(target: Path, data: bytes) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=".tmp-", dir=str(target.parent))
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_path, target)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise


def _atomic_write_json(target: Path, payload: Mapping[str, Any]) -> None:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    _atomic_write_bytes(target, encoded)


class LocalFsStorageAdapter:
    """In-memory / persistent local-directory chunked CAS implementation of StoragePort."""

    def __init__(
        self,
        root_dir: Path | None = None,
        *,
        base_dir: Path | None = None,
        chunk_size: int = DEFAULT_CHUNK_SIZE,
        chunk_size_bytes: int | None = None,
        unlogged_threshold_bytes: int = UNLOGGED_THRESHOLD_BYTES,
        max_blob_bytes: int = MAX_BLOB_BYTES,
    ) -> None:
        raw_root = root_dir if root_dir is not None else base_dir
        if raw_root is not None and has_symlink_component(raw_root):
            raise PermissionError(f"Refusing symlinked storage root: {raw_root}")
        self._root_dir: Path | None = raw_root.resolve() if raw_root is not None else None
        if self._root_dir is not None:
            self._root_dir.mkdir(parents=True, exist_ok=True)
        self._chunk_size = chunk_size_bytes if chunk_size_bytes is not None else chunk_size
        self._unlogged_threshold_bytes = unlogged_threshold_bytes
        self._max_blob_bytes = max_blob_bytes

        # In-memory mirrors (primary backing store when root_dir is None)
        self._manifests: dict[tuple[str, str], KBBlob] = {}
        self._chunks: dict[tuple[str, str], list[bytes]] = {}
        self._chunk_digests: dict[tuple[str, str], list[bytes]] = {}
        self._projects: dict[tuple[str, str], dict[str, dict[str, Any]]] = {}
        self._aliases: dict[tuple[str, str, str], tuple[str, float]] = {}
        self._updated_at: dict[tuple[str, str], float] = {}
        self._locks: dict[tuple[str, str], asyncio.Lock] = {}

    async def close(self) -> None:
        """Close any held resources."""

    @asynccontextmanager
    async def _blob_lock(self, tenant_id: str, sha256: str) -> AsyncIterator[None]:
        key = (tenant_id, sha256)
        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            yield

    def _tenant_dir(self, tenant_id: str) -> Path:
        if self._root_dir is None:
            raise RuntimeError("LocalFsStorageAdapter root_dir not configured")
        tid_hash = hashlib.sha256(tenant_id.encode()).hexdigest()
        return self._root_dir / "tenants" / tid_hash

    def _blob_dir(self, tenant_id: str, sha256: str) -> Path:
        return self._tenant_dir(tenant_id) / "blobs" / sha256[:2] / sha256

    def _manifest_path(self, tenant_id: str, sha256: str) -> Path:
        return self._blob_dir(tenant_id, sha256) / "manifest.json"

    def _chunk_path(self, tenant_id: str, sha256: str, idx: int) -> Path:
        return self._blob_dir(tenant_id, sha256) / "chunks" / f"{idx:08d}.chunk"

    def _aliases_dir(self, tenant_id: str) -> Path:
        return self._tenant_dir(tenant_id) / "aliases"

    def _alias_path(self, tenant_id: str, project_id: str | None, alias_key: str) -> Path:
        digest = hashlib.sha256(f"{project_id or ''}\0{alias_key}".encode()).hexdigest()
        return self._aliases_dir(tenant_id) / f"{digest}.json"

    def _check_confined(self, path: Path) -> None:
        if self._root_dir is not None and has_symlink_component(path, self._root_dir):
            raise PermissionError(f"Refusing symlinked storage artifact: {path}")

    def _read_disk_manifest(self, tenant_id: str, sha256: str) -> dict[str, Any] | None:
        mpath = self._manifest_path(tenant_id, sha256)
        self._check_confined(mpath)
        if not mpath.is_file():
            return None
        try:
            data = json.loads(mpath.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(data, dict) or str(data.get("tenant_id")) != tenant_id:
            return None
        return data

    def _write_disk_manifest(self, tenant_id: str, sha256: str, payload: dict[str, Any]) -> None:
        mpath = self._manifest_path(tenant_id, sha256)
        self._check_confined(mpath)
        _atomic_write_json(mpath, payload)

    def _resolve_sha256(self, *, tenant_id: str, key: str, project_id: str | None) -> str | None:
        sha, alias = _parse_blob_key(key, tenant_id=tenant_id)
        if sha is not None:
            return sha
        if alias is None:
            return None
        if self._root_dir is None:
            if project_id is not None:
                entry = self._aliases.get((tenant_id, project_id, alias))
                return entry[0] if entry is not None else None
            best_sha: str | None = None
            best_ts = -1.0
            for (tid, _, akey), (cand_sha, ts) in self._aliases.items():
                if tid == tenant_id and akey == alias and ts >= best_ts:
                    best_sha = cand_sha
                    best_ts = ts
            return best_sha

        if project_id is not None:
            apath = self._alias_path(tenant_id, project_id, alias)
            self._check_confined(apath)
            if not apath.is_file():
                return None
            try:
                rec = json.loads(apath.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                return None
            if rec.get("tenant_id") == tenant_id and rec.get("alias_key") == alias:
                return str(rec["sha256"])
            return None

        adir = self._aliases_dir(tenant_id)
        if not adir.is_dir():
            return None
        best_sha = None
        best_ts = -1.0
        for apath in adir.glob("*.json"):
            self._check_confined(apath)
            try:
                rec = json.loads(apath.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if rec.get("tenant_id") != tenant_id or rec.get("alias_key") != alias:
                continue
            ts = float(rec.get("updated_at", 0.0))
            if ts >= best_ts:
                best_ts = ts
                best_sha = str(rec["sha256"])
        return best_sha

    def _record_alias(
        self, *, tenant_id: str, project_id: str | None, alias_key: str | None, sha256: str
    ) -> None:
        if not alias_key:
            return
        now = time.time()
        self._aliases[(tenant_id, project_id or "", alias_key)] = (sha256, now)
        if self._root_dir is not None:
            apath = self._alias_path(tenant_id, project_id, alias_key)
            self._check_confined(apath)
            _atomic_write_json(
                apath,
                {
                    "tenant_id": tenant_id,
                    "project_id": project_id or "",
                    "alias_key": alias_key,
                    "sha256": sha256,
                    "updated_at": now,
                },
            )

    def _drop_aliases_for_sha(
        self, *, tenant_id: str, sha256: str, project_id: str | None = None
    ) -> None:
        for k in list(self._aliases):
            kt, kp, _ = k
            if kt == tenant_id and self._aliases[k][0] == sha256:
                if project_id is None or kp == project_id:
                    del self._aliases[k]
        if self._root_dir is not None:
            adir = self._aliases_dir(tenant_id)
            if adir.is_dir():
                for apath in adir.glob("*.json"):
                    self._check_confined(apath)
                    try:
                        rec = json.loads(apath.read_text(encoding="utf-8"))
                    except (OSError, ValueError):
                        continue
                    if rec.get("sha256") != sha256:
                        continue
                    if project_id is None or rec.get("project_id") == project_id:
                        apath.unlink(missing_ok=True)

    def _actual_chunk_count(self, tenant_id: str, sha256: str, expected_chunks: int) -> int:
        if self._root_dir is None:
            return len(self._chunks.get((tenant_id, sha256), []))
        cdir = self._blob_dir(tenant_id, sha256) / "chunks"
        if not cdir.is_dir():
            return 0
        count = 0
        for idx in range(expected_chunks):
            cpath = cdir / f"{idx:08d}.chunk"
            self._check_confined(cpath)
            if cpath.is_file():
                count += 1
        return count

    async def head(
        self,
        *,
        key: str,
        tenant_id: str = "default",
        project_id: str | None = None,
    ) -> KBBlob | None:
        sha256 = self._resolve_sha256(tenant_id=tenant_id, key=key, project_id=project_id)
        if sha256 is None:
            return None

        if self._root_dir is None:
            blob = self._manifests.get((tenant_id, sha256))
            if blob is None:
                return None
            projects = self._projects.get((tenant_id, sha256), {})
            eff_project = blob.project_id
            eff_meta: Mapping[str, Any] = blob.metadata
            if project_id is not None:
                if project_id not in projects:
                    return None
                eff_project = project_id
                eff_meta = projects[project_id]
            if blob.status == "ready" and blob.chunk_count > 0:
                actual = len(self._chunks.get((tenant_id, sha256), []))
                if actual < blob.chunk_count:
                    self._manifests[(tenant_id, sha256)] = KBBlob(
                        tenant_id=blob.tenant_id,
                        sha256=blob.sha256,
                        project_id=blob.project_id,
                        media_type=blob.media_type,
                        byte_size=blob.byte_size,
                        chunk_size=blob.chunk_size,
                        chunk_count=blob.chunk_count,
                        storage_tier=blob.storage_tier,
                        status="failed",
                        metadata=blob.metadata,
                    )
                    self._updated_at[(tenant_id, sha256)] = time.time()
                    raise BlobTruncatedError(
                        f"Blob {sha256} truncated in tenant {tenant_id!r} "
                        f"(expected {blob.chunk_count} chunks, found {actual})"
                    )
            return KBBlob(
                tenant_id=blob.tenant_id,
                sha256=blob.sha256,
                project_id=eff_project,
                media_type=blob.media_type,
                byte_size=blob.byte_size,
                chunk_size=blob.chunk_size,
                chunk_count=blob.chunk_count,
                storage_tier=blob.storage_tier,
                status=blob.status,
                metadata=eff_meta,
            )

        rec = self._read_disk_manifest(tenant_id, sha256)
        if rec is None:
            return None
        projects_raw = rec.get("projects")
        projects_map: dict[str, dict[str, Any]] = (
            projects_raw if isinstance(projects_raw, dict) else {}
        )
        eff_project = rec.get("project_id")
        raw_meta = rec.get("metadata")
        eff_meta = raw_meta if isinstance(raw_meta, Mapping) else {}
        if project_id is not None:
            if project_id not in projects_map:
                return None
            eff_project = project_id
            eff_meta = projects_map[project_id]

        expected_chunks = int(rec["chunk_count"])
        status = str(rec["status"])
        if status == "ready" and expected_chunks > 0:
            actual = self._actual_chunk_count(tenant_id, sha256, expected_chunks)
            if actual < expected_chunks:
                rec["status"] = "failed"
                self._write_disk_manifest(tenant_id, sha256, rec)
                raise BlobTruncatedError(
                    f"Blob {sha256} truncated in tenant {tenant_id!r} "
                    f"(expected {expected_chunks} chunks, found {actual})"
                )
        return KBBlob(
            tenant_id=tenant_id,
            sha256=sha256,
            project_id=eff_project,
            media_type=str(rec["media_type"]),
            byte_size=int(rec["byte_size"]),
            chunk_size=int(rec["chunk_size"]),
            chunk_count=expected_chunks,
            storage_tier=str(rec["storage_tier"]),
            status=status,
            metadata=eff_meta,
        )

    async def exists(
        self,
        *,
        key: str,
        tenant_id: str = "default",
        project_id: str | None = None,
    ) -> bool:
        try:
            blob = await self.head(key=key, tenant_id=tenant_id, project_id=project_id)
            return blob is not None and blob.status == "ready"
        except BlobTruncatedError:
            return False

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
        sha256 = hashlib.sha256(data).hexdigest()
        _, alias = _parse_blob_key(key, tenant_id=tenant_id)
        await self.put_stream(
            key=alias or sha256,
            stream=[data],
            content_type=content_type or "application/octet-stream",
            tenant_id=tenant_id,
            project_id=project_id,
            byte_size=len(data),
            expected_sha256=sha256,
            metadata=metadata,
        )

    async def put_file(
        self,
        *,
        file_path: Path,
        content_type: str = "application/octet-stream",
        tenant_id: str = "default",
        project_id: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> KBBlob:
        if has_symlink_component(file_path):
            raise PermissionError(f"Refusing to ingest symlinked blob path: {file_path}")
        if not file_path.exists():
            raise FileNotFoundError(f"Blob source file not found: {file_path}")
        if not file_path.is_file():
            raise PermissionError(f"Refusing to ingest non-regular blob path: {file_path}")
        st_size = file_path.stat().st_size
        if st_size > self._max_blob_bytes:
            raise BlobTooLargeError(
                f"File size {st_size} exceeds maximum {self._max_blob_bytes} bytes"
            )
        with file_path.open("rb") as fh:
            expected_sha256 = hashlib.file_digest(fh, "sha256").hexdigest()

        def _iter_chunks() -> Iterable[bytes]:
            with file_path.open("rb") as fh:
                while blk := fh.read(self._chunk_size):
                    yield blk

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
        alias_key: str | None = None
        if key:
            _, alias_key = _parse_blob_key(key, tenant_id=tenant_id)

        if expected_sha256 is None or byte_size is None:
            with tempfile.SpooledTemporaryFile(
                max_size=SPOOL_MAX_MEMORY_BYTES, mode="w+b"
            ) as spooled:
                pre_hasher = hashlib.sha256()
                computed_size = 0
                if isinstance(stream, AsyncIterator):
                    async for piece in stream:
                        if not piece:
                            continue
                        computed_size += len(piece)
                        if computed_size > self._max_blob_bytes:
                            raise BlobTooLargeError(
                                f"Stream exceeded maximum {self._max_blob_bytes} bytes"
                            )
                        pre_hasher.update(piece)
                        spooled.write(piece)
                else:
                    for piece in stream:
                        if not piece:
                            continue
                        computed_size += len(piece)
                        if computed_size > self._max_blob_bytes:
                            raise BlobTooLargeError(
                                f"Stream exceeded maximum {self._max_blob_bytes} bytes"
                            )
                        pre_hasher.update(piece)
                        spooled.write(piece)

                computed_hex = pre_hasher.hexdigest()
                if expected_sha256 is not None and computed_hex != expected_sha256.lower():
                    raise BlobIntegrityError(
                        f"Blob SHA-256 mismatch: expected {expected_sha256.lower()}, "
                        f"computed {computed_hex}"
                    )
                if byte_size is not None and computed_size != byte_size:
                    raise BlobIntegrityError(
                        f"Blob byte_size mismatch: expected {byte_size}, got {computed_size}"
                    )
                spooled.seek(0)

                def _spooled_iter() -> Iterable[bytes]:
                    while blk := spooled.read(self._chunk_size):
                        yield blk

                return await self._put_known_stream(
                    sha256=computed_hex,
                    byte_size=computed_size,
                    stream=_spooled_iter(),
                    content_type=content_type,
                    tenant_id=tenant_id,
                    project_id=project_id,
                    metadata=metadata,
                    alias_key=alias_key,
                )

        return await self._put_known_stream(
            sha256=expected_sha256.lower(),
            byte_size=byte_size,
            stream=stream,
            content_type=content_type,
            tenant_id=tenant_id,
            project_id=project_id,
            metadata=metadata,
            alias_key=alias_key,
        )

    async def _put_known_stream(
        self,
        *,
        sha256: str,
        byte_size: int,
        stream: AsyncIterator[bytes] | Iterable[bytes],
        content_type: str,
        tenant_id: str,
        project_id: str | None,
        metadata: Mapping[str, Any] | None,
        alias_key: str | None = None,
    ) -> KBBlob:
        tier = "unlogged" if byte_size > self._unlogged_threshold_bytes else "logged"
        expected_chunks = (
            0 if byte_size == 0 else (byte_size + self._chunk_size - 1) // self._chunk_size
        )
        caller_meta = dict(metadata or {})

        async with self._blob_lock(tenant_id, sha256):
            # Dedup fast-path when blob already exists completely in 'ready' state
            existing_ready = False
            existing_media_type = content_type or "application/octet-stream"
            existing_tier = tier
            prev_rec: dict[str, Any] | None = None
            if self._root_dir is None:
                prev = self._manifests.get((tenant_id, sha256))
                if (
                    prev is not None
                    and prev.status == "ready"
                    and prev.byte_size == byte_size
                    and prev.chunk_size == self._chunk_size
                    and prev.chunk_count == expected_chunks
                    and len(self._chunks.get((tenant_id, sha256), [])) == expected_chunks
                ):
                    existing_ready = True
                    existing_media_type = prev.media_type
                    existing_tier = prev.storage_tier
            else:
                prev_rec = self._read_disk_manifest(tenant_id, sha256)
                if (
                    prev_rec is not None
                    and str(prev_rec.get("status")) == "ready"
                    and int(prev_rec.get("byte_size", -1)) == byte_size
                    and int(prev_rec.get("chunk_size", -1)) == self._chunk_size
                    and int(prev_rec.get("chunk_count", -1)) == expected_chunks
                    and self._actual_chunk_count(tenant_id, sha256, expected_chunks)
                    == expected_chunks
                ):
                    existing_ready = True
                    existing_media_type = str(prev_rec["media_type"])
                    existing_tier = str(prev_rec["storage_tier"])

            if existing_ready:
                if project_id is not None:
                    self._projects.setdefault((tenant_id, sha256), {})[project_id] = dict(
                        caller_meta
                    )
                    if self._root_dir is not None and prev_rec is not None:
                        proj_map = dict(prev_rec.get("projects") or {})
                        proj_map[project_id] = dict(caller_meta)
                        prev_rec["projects"] = proj_map
                        self._write_disk_manifest(tenant_id, sha256, prev_rec)
                self._record_alias(
                    tenant_id=tenant_id,
                    project_id=project_id,
                    alias_key=alias_key,
                    sha256=sha256,
                )
                return KBBlob(
                    tenant_id=tenant_id,
                    sha256=sha256,
                    project_id=project_id,
                    media_type=existing_media_type,
                    byte_size=byte_size,
                    chunk_size=self._chunk_size,
                    chunk_count=expected_chunks,
                    storage_tier=existing_tier,
                    status="ready",
                    metadata=caller_meta,
                )

            whole_hasher = hashlib.sha256()
            chunk_list: list[bytes] = []
            chunk_digests: list[bytes] = []
            buffer = bytearray()
            total_bytes = 0

            async def _flush_chunk(blk: bytes, idx: int) -> None:
                c_sha = hashlib.sha256(blk).digest()
                if self._root_dir is None:
                    chunk_list.append(blk)
                    chunk_digests.append(c_sha)
                else:
                    cpath = self._chunk_path(tenant_id, sha256, idx)
                    self._check_confined(cpath)
                    _atomic_write_bytes(cpath, blk)
                    chunk_digests.append(c_sha)

            # Initialize uploading manifest on disk if persisting
            prev_disk = self._read_disk_manifest(tenant_id, sha256) if self._root_dir else None
            prior_projects: dict[str, dict[str, Any]] = (
                dict(prev_disk.get("projects") or {})
                if isinstance(prev_disk, dict)
                else dict(self._projects.get((tenant_id, sha256), {}))
            )
            eff_root_project = (
                prev_disk.get("project_id")
                if isinstance(prev_disk, dict) and prev_disk.get("project_id") is not None
                else project_id
            )
            if self._root_dir is not None:
                cdir = self._blob_dir(tenant_id, sha256) / "chunks"
                if cdir.is_dir():
                    shutil.rmtree(cdir)
                self._write_disk_manifest(
                    tenant_id,
                    sha256,
                    {
                        "tenant_id": tenant_id,
                        "sha256": sha256,
                        "project_id": eff_root_project,
                        "media_type": content_type or "application/octet-stream",
                        "byte_size": byte_size,
                        "chunk_size": self._chunk_size,
                        "chunk_count": expected_chunks,
                        "storage_tier": tier,
                        "status": "uploading",
                        "metadata": caller_meta,
                        "projects": prior_projects,
                        "chunk_hashes": [],
                    },
                )

            try:
                chunk_idx = 0
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
                            blk = bytes(buffer[: self._chunk_size])
                            del buffer[: self._chunk_size]
                            await _flush_chunk(blk, chunk_idx)
                            chunk_idx += 1
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
                            blk = bytes(buffer[: self._chunk_size])
                            del buffer[: self._chunk_size]
                            await _flush_chunk(blk, chunk_idx)
                            chunk_idx += 1

                if buffer:
                    await _flush_chunk(bytes(buffer), chunk_idx)
                    chunk_idx += 1

                computed_sha256 = whole_hasher.hexdigest()
                if computed_sha256 != sha256:
                    raise BlobIntegrityError(
                        f"Whole-blob SHA-256 mismatch: expected {sha256}, "
                        f"computed {computed_sha256}"
                    )
                if total_bytes != byte_size or chunk_idx != expected_chunks:
                    raise BlobIntegrityError(
                        f"Chunk count/size mismatch: expected ({byte_size} B, "
                        f"{expected_chunks} chunks), wrote ({total_bytes} B, {chunk_idx} chunks)"
                    )
            except Exception:
                if self._root_dir is not None:
                    bdir = self._blob_dir(tenant_id, sha256)
                    if (bdir / "chunks").is_dir():
                        shutil.rmtree(bdir / "chunks", ignore_errors=True)
                    self._write_disk_manifest(
                        tenant_id,
                        sha256,
                        {
                            "tenant_id": tenant_id,
                            "sha256": sha256,
                            "project_id": eff_root_project,
                            "media_type": content_type or "application/octet-stream",
                            "byte_size": byte_size,
                            "chunk_size": self._chunk_size,
                            "chunk_count": expected_chunks,
                            "storage_tier": tier,
                            "status": "failed",
                            "metadata": caller_meta,
                            "projects": prior_projects,
                            "chunk_hashes": [],
                        },
                    )
                raise

            if project_id is not None:
                prior_projects[project_id] = dict(caller_meta)
            blob = KBBlob(
                tenant_id=tenant_id,
                sha256=sha256,
                project_id=project_id,
                media_type=content_type or "application/octet-stream",
                byte_size=byte_size,
                chunk_size=self._chunk_size,
                chunk_count=expected_chunks,
                storage_tier=tier,
                status="ready",
                metadata=caller_meta,
            )
            if self._root_dir is None:
                self._manifests[(tenant_id, sha256)] = KBBlob(
                    tenant_id=tenant_id,
                    sha256=sha256,
                    project_id=eff_root_project,
                    media_type=blob.media_type,
                    byte_size=byte_size,
                    chunk_size=self._chunk_size,
                    chunk_count=expected_chunks,
                    storage_tier=tier,
                    status="ready",
                    metadata=caller_meta,
                )
                self._chunks[(tenant_id, sha256)] = chunk_list
                self._chunk_digests[(tenant_id, sha256)] = chunk_digests
                if project_id is not None:
                    self._projects[(tenant_id, sha256)] = prior_projects
                self._updated_at[(tenant_id, sha256)] = time.time()
            else:
                self._write_disk_manifest(
                    tenant_id,
                    sha256,
                    {
                        "tenant_id": tenant_id,
                        "sha256": sha256,
                        "project_id": eff_root_project,
                        "media_type": blob.media_type,
                        "byte_size": byte_size,
                        "chunk_size": self._chunk_size,
                        "chunk_count": expected_chunks,
                        "storage_tier": tier,
                        "status": "ready",
                        "metadata": caller_meta,
                        "projects": prior_projects,
                        "chunk_hashes": [d.hex() for d in chunk_digests],
                    },
                )
            self._record_alias(
                tenant_id=tenant_id,
                project_id=project_id,
                alias_key=alias_key,
                sha256=sha256,
            )
            return blob

    async def get_stream(
        self,
        *,
        key: str,
        tenant_id: str = "default",
        project_id: str | None = None,
        chunk_batch_size: int = 4,
    ) -> AsyncIterator[bytes]:
        del chunk_batch_size
        blob = await self.head(key=key, tenant_id=tenant_id, project_id=project_id)
        if blob is None or blob.status != "ready":
            raise BlobNotFoundError(f"Blob {key!r} not found in tenant {tenant_id!r}")
        if blob.chunk_count == 0:
            return

        if self._root_dir is None:
            chunks = self._chunks.get((tenant_id, blob.sha256), [])
            digests = self._chunk_digests.get((tenant_id, blob.sha256), [])
            if len(chunks) != blob.chunk_count:
                raise BlobTruncatedError(
                    f"Blob {blob.sha256} expected {blob.chunk_count} chunks, found {len(chunks)}"
                )
            for idx, blk in enumerate(chunks):
                if idx < len(digests) and hashlib.sha256(blk).digest() != digests[idx]:
                    raise BlobIntegrityError(
                        f"SHA-256 mismatch on chunk {idx} of blob {blob.sha256}"
                    )
                yield blk
            return

        rec = self._read_disk_manifest(tenant_id, blob.sha256)
        expected_hashes: list[str] = (
            list(rec.get("chunk_hashes") or []) if isinstance(rec, dict) else []
        )
        for idx in range(blob.chunk_count):
            cpath = self._chunk_path(tenant_id, blob.sha256, idx)
            self._check_confined(cpath)
            if not cpath.is_file():
                raise BlobTruncatedError(f"Blob {blob.sha256} missing chunk_index {idx} on disk")
            data = cpath.read_bytes()
            if idx < len(expected_hashes):
                if hashlib.sha256(data).hexdigest() != expected_hashes[idx]:
                    raise BlobIntegrityError(
                        f"SHA-256 mismatch on chunk {idx} of blob {blob.sha256}"
                    )
            yield data

    async def get(
        self,
        *,
        key: str,
        tenant_id: str = "default",
        project_id: str | None = None,
        max_bytes: int | None = None,
    ) -> bytes:
        if max_bytes is not None:
            blob = await self.head(key=key, tenant_id=tenant_id, project_id=project_id)
            if blob is None or blob.status != "ready":
                raise BlobNotFoundError(f"Blob {key!r} not found in tenant {tenant_id!r}")
            if blob.byte_size > max_bytes:
                raise BlobTooLargeError(
                    f"Blob {blob.sha256} size {blob.byte_size} exceeds get limit {max_bytes} bytes"
                )
        out = bytearray()
        async for chunk in self.get_stream(key=key, tenant_id=tenant_id, project_id=project_id):
            if max_bytes is not None and len(out) + len(chunk) > max_bytes:
                raise BlobTooLargeError(f"Blob stream exceeds get limit {max_bytes} bytes")
            out.extend(chunk)
        return bytes(out)

    async def delete(
        self,
        *,
        key: str,
        tenant_id: str = "default",
        project_id: str | None = None,
    ) -> None:
        sha256 = self._resolve_sha256(tenant_id=tenant_id, key=key, project_id=project_id)
        if sha256 is None:
            return
        async with self._blob_lock(tenant_id, sha256):
            if self._root_dir is None:
                manifest = self._manifests.get((tenant_id, sha256))
                if project_id is not None:
                    self._drop_aliases_for_sha(
                        tenant_id=tenant_id, sha256=sha256, project_id=project_id
                    )
                    proj_map = self._projects.get((tenant_id, sha256), {})
                    if project_id not in proj_map:
                        return
                    del proj_map[project_id]
                    if proj_map or (manifest is not None and manifest.project_id is None):
                        return
                self._drop_aliases_for_sha(tenant_id=tenant_id, sha256=sha256)
                self._projects.pop((tenant_id, sha256), None)
                self._chunks.pop((tenant_id, sha256), None)
                self._chunk_digests.pop((tenant_id, sha256), None)
                self._manifests.pop((tenant_id, sha256), None)
                self._updated_at.pop((tenant_id, sha256), None)
                return

            rec = self._read_disk_manifest(tenant_id, sha256)
            if rec is None:
                return
            if project_id is not None:
                self._drop_aliases_for_sha(
                    tenant_id=tenant_id, sha256=sha256, project_id=project_id
                )
                proj_map = dict(rec.get("projects") or {})
                if project_id not in proj_map:
                    return
                del proj_map[project_id]
                if proj_map or rec.get("project_id") is None:
                    rec["projects"] = proj_map
                    self._write_disk_manifest(tenant_id, sha256, rec)
                    return

            self._drop_aliases_for_sha(tenant_id=tenant_id, sha256=sha256)
            bdir = self._blob_dir(tenant_id, sha256)
            if bdir.exists():
                shutil.rmtree(bdir)

    async def reconcile_truncated_blobs(self, *, tenant_id: str | None = None) -> int:
        reconciled = 0
        if self._root_dir is None:
            for (tid, sha), blob in list(self._manifests.items()):
                if tenant_id is not None and tid != tenant_id:
                    continue
                if blob.status == "ready" and blob.chunk_count > 0:
                    if len(self._chunks.get((tid, sha), [])) < blob.chunk_count:
                        self._manifests[(tid, sha)] = KBBlob(
                            tenant_id=blob.tenant_id,
                            sha256=blob.sha256,
                            project_id=blob.project_id,
                            media_type=blob.media_type,
                            byte_size=blob.byte_size,
                            chunk_size=blob.chunk_size,
                            chunk_count=blob.chunk_count,
                            storage_tier=blob.storage_tier,
                            status="failed",
                            metadata=blob.metadata,
                        )
                        self._updated_at[(tid, sha)] = time.time()
                        reconciled += 1
            return reconciled

        tenants_root = self._root_dir / "tenants"
        if not tenants_root.is_dir():
            return 0
        for mpath in tenants_root.glob("*/blobs/*/*/manifest.json"):
            self._check_confined(mpath)
            try:
                rec = json.loads(mpath.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            tid = str(rec.get("tenant_id", ""))
            sha = str(rec.get("sha256", ""))
            if tenant_id is not None and tid != tenant_id:
                continue
            expected_chunks = int(rec.get("chunk_count", 0))
            if rec.get("status") == "ready" and expected_chunks > 0:
                if self._actual_chunk_count(tid, sha, expected_chunks) < expected_chunks:
                    rec["status"] = "failed"
                    self._write_disk_manifest(tid, sha, rec)
                    reconciled += 1
        return reconciled

    async def gc_orphan_chunks(
        self, *, older_than_seconds: int = 7200, tenant_id: str | None = None
    ) -> int:
        now = time.time()
        clean_tid = tenant_id.strip() if isinstance(tenant_id, str) and tenant_id.strip() else None
        deleted = 0

        if self._root_dir is None:
            for (tid, sha), blob in list(self._manifests.items()):
                if clean_tid is not None and tid != clean_tid:
                    continue
                if blob.status in ("uploading", "failed"):
                    updated = self._updated_at.get((tid, sha), 0.0)
                    age = now - updated
                    if (older_than_seconds <= 0) or (age > older_than_seconds):
                        self._drop_aliases_for_sha(tenant_id=tid, sha256=sha)
                        self._projects.pop((tid, sha), None)
                        deleted += len(self._chunks.pop((tid, sha), []))
                        self._chunk_digests.pop((tid, sha), None)
                        self._manifests.pop((tid, sha), None)
                        self._updated_at.pop((tid, sha), None)
                        deleted += 1
            for tid, sha in list(self._chunks):
                if clean_tid is not None and tid != clean_tid:
                    continue
                if (tid, sha) not in self._manifests:
                    updated = self._updated_at.get((tid, sha), 0.0)
                    if (older_than_seconds <= 0) or ((now - updated) > older_than_seconds):
                        deleted += len(self._chunks.pop((tid, sha)))
                        self._chunk_digests.pop((tid, sha), None)
            return deleted

        tenants_root = self._root_dir / "tenants"
        if not tenants_root.is_dir():
            return 0
        target_dirs = (
            [self._tenant_dir(clean_tid)]
            if clean_tid is not None
            else [p for p in tenants_root.iterdir() if p.is_dir()]
        )
        for tdir in target_dirs:
            blobs_root = tdir / "blobs"
            if not blobs_root.is_dir():
                continue
            for mpath in list(blobs_root.glob("*/*/manifest.json")):
                self._check_confined(mpath)
                try:
                    rec = json.loads(mpath.read_text(encoding="utf-8"))
                    mtime = mpath.stat().st_mtime
                except (OSError, ValueError):
                    continue
                tid = str(rec.get("tenant_id", ""))
                sha = str(rec.get("sha256", ""))
                if clean_tid is not None and tid != clean_tid:
                    continue
                if rec.get("status") in ("uploading", "failed"):
                    if (older_than_seconds <= 0) or ((now - mtime) > older_than_seconds):
                        self._drop_aliases_for_sha(tenant_id=tid, sha256=sha)
                        bdir = mpath.parent
                        cdir = bdir / "chunks"
                        if cdir.is_dir():
                            deleted += sum(1 for _ in cdir.glob("*.chunk"))
                        shutil.rmtree(bdir, ignore_errors=True)
                        deleted += 1
            # Sweep orphan chunk files without a manifest
            for bdir in list(blobs_root.glob("*/*")):
                if not bdir.is_dir() or (bdir / "manifest.json").is_file():
                    continue
                cdir = bdir / "chunks"
                if cdir.is_dir():
                    for cfile in list(cdir.glob("*.chunk")):
                        self._check_confined(cfile)
                        try:
                            cmtime = cfile.stat().st_mtime
                        except OSError:
                            continue
                        if (older_than_seconds <= 0) or ((now - cmtime) > older_than_seconds):
                            cfile.unlink(missing_ok=True)
                            deleted += 1
        return deleted
