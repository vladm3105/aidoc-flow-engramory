"""PgBlobStorageAdapter — PostgreSQL 18 20 GB+ Chunked Blob CAS (Rule D14a / D14b).

Implements the normative PostgreSQL-First Multimodal & Large-Media Blob Storage Standard
defined in ``platform-technology_stack/05-database-stack.md`` §6.14 and
``docs/MULTIMODAL_AND_POSTGRES_BLOB_STORAGE.md``:
- Manifest table ``kb_blobs`` + RLS project join ``kb_blob_projects`` + ``kb_blob_aliases``
- Tier 1 ``kb_blob_chunks`` (``LOGGED``, ``<= 50 MiB``) and Tier 2 ``kb_blob_chunks_large``
  (``UNLOGGED``, ``> 50 MiB`` to ``25 GiB``) in ``ts_blob_cold`` with ``STORAGE EXTERNAL``
- Session-level advisory locking per ``(tenant_id, sha256)`` protecting concurrent CAS uploads
- Crash-truncation & gap detection (``BlobTruncatedError``) when chunks are missing or
  non-contiguous
- Bounded keyset streaming reads guarded by ``self._semaphore`` and optional ``max_bytes`` caps
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import tempfile
from collections.abc import AsyncIterator, Callable, Iterable, Mapping
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import psycopg
from psycopg.rows import dict_row

from engramory.core.docs_tools import has_symlink_component
from engramory.ports.storage import (
    BlobIntegrityError,
    BlobNotFoundError,
    BlobTooLargeError,
    BlobTruncatedError,
    KBBlob,
    StorageError,
)

logger = logging.getLogger(__name__)

DEFAULT_CHUNK_SIZE = 4 * 1024 * 1024  # 4 MiB (4,194,304 bytes)
UNLOGGED_THRESHOLD_BYTES = 50 * 1024 * 1024  # 50 MiB
MAX_BLOB_BYTES = 25 * 1024 * 1024 * 1024  # 25 GiB
SPOOL_MAX_MEMORY_BYTES = 16 * 1024 * 1024  # 16 MiB before spilling to temp disk
HEARTBEAT_CHUNK_INTERVAL = 16


def _is_sha256_hex(val: str) -> bool:
    return len(val) == 64 and all(c in "0123456789abcdef" for c in val.lower())


def _parse_blob_key(key: str, *, tenant_id: str | None = None) -> tuple[str | None, str | None]:
    """Return ``(sha256_or_none, alias_or_none)`` without hashing arbitrary alias keys.

    Rejects malformed or cross-tenant ``pgblob://<tenant>/<sha256>`` URIs by
    returning ``(None, None)``.
    """
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


def _normalize_sha256_key(key: str, *, tenant_id: str | None = None) -> str:
    """Extract canonical 64-char hex digest from a digest or pgblob:// URI."""
    sha, _ = _parse_blob_key(key, tenant_id=tenant_id)
    if sha is None:
        raise BlobNotFoundError(f"Invalid or unresolved blob key {key!r}")
    return sha


def _cas_lock_key(tenant_id: str, sha256: str) -> int:
    digest = hashlib.sha256(f"{tenant_id}\0{sha256}".encode()).digest()[:8]
    return int.from_bytes(digest, "big", signed=True)


def _chunk_table_for_tier(storage_tier: str) -> str:
    if storage_tier == "unlogged":
        return "kb_blob_chunks_large"
    if storage_tier == "logged":
        return "kb_blob_chunks"
    raise ValueError(f"Invalid storage_tier: {storage_tier!r}")


class PgBlobStorageAdapter:
    """PostgreSQL 18 Chunked Content-Addressable Blob Storage Adapter."""

    def __init__(
        self,
        dsn: str,
        *,
        chunk_size: int = DEFAULT_CHUNK_SIZE,
        unlogged_threshold_bytes: int = UNLOGGED_THRESHOLD_BYTES,
        max_blob_bytes: int = MAX_BLOB_BYTES,
        max_concurrent_streams: int = 4,
        conn_factory: Callable[[], Any] | None = None,
    ) -> None:
        self._dsn = dsn
        self._chunk_size = chunk_size
        self._unlogged_threshold_bytes = unlogged_threshold_bytes
        self._max_blob_bytes = max_blob_bytes
        self._semaphore = asyncio.Semaphore(max(1, max_concurrent_streams))
        self._conn_factory = conn_factory

    @property
    def dsn(self) -> str:
        """Direct PostgreSQL connection string used for streaming blob I/O."""
        return self._dsn

    async def close(self) -> None:
        """Close any held resources (connections are scoped per stream operation)."""

    @asynccontextmanager
    async def _conn(
        self,
        *,
        tenant_id: str | None = None,
        autocommit: bool = False,
        worker_sweep: bool = False,
    ) -> AsyncIterator[Any]:
        if self._conn_factory is not None:
            async with self._conn_factory() as c:
                yield c
            return
        try:
            conn = await psycopg.AsyncConnection.connect(
                self._dsn,
                row_factory=dict_row,
                autocommit=autocommit,
            )
        except psycopg.Error as exc:
            raise StorageError(f"PostgreSQL blob store unavailable: {exc}") from exc
        try:
            if not autocommit:
                if tenant_id:
                    await conn.execute(
                        "SELECT set_config('app.tenant_id', %(tid)s, true)",
                        {"tid": tenant_id},
                    )
                if worker_sweep:
                    await conn.execute("SELECT set_config('app.worker_sweep', 'on', true)")
            yield conn
        finally:
            await conn.close()

    async def _reapply_rls(self, conn: Any, tenant_id: str) -> None:
        await conn.execute(
            "SELECT set_config('app.tenant_id', %(tid)s, true)",
            {"tid": tenant_id},
        )

    async def _resolve_sha256_for_read(
        self,
        conn: Any,
        *,
        key: str,
        tenant_id: str,
        project_id: str | None,
    ) -> str | None:
        sha, alias = _parse_blob_key(key, tenant_id=tenant_id)
        if sha is not None:
            return sha
        if alias is None:
            return None
        row = await (
            await conn.execute(
                """
                SELECT sha256
                FROM kb_blob_aliases
                WHERE tenant_id = %(tenant_id)s
                  AND alias_key = %(alias_key)s
                  AND (%(project_id)s::text IS NULL OR project_id = %(project_id)s)
                ORDER BY updated_at DESC
                LIMIT 1
                """,
                {
                    "tenant_id": tenant_id,
                    "alias_key": alias,
                    "project_id": project_id,
                },
            )
        ).fetchone()
        return str(row["sha256"]) if row is not None else None

    async def _upsert_project_and_alias(
        self,
        conn: Any,
        *,
        tenant_id: str,
        project_id: str | None,
        sha256: str,
        meta_json: str,
        alias_key: str | None,
    ) -> None:
        if project_id is not None:
            await conn.execute(
                """
                INSERT INTO kb_blob_projects (tenant_id, project_id, sha256, metadata)
                VALUES (%(tenant_id)s, %(project_id)s, %(sha256)s, %(metadata)s::jsonb)
                ON CONFLICT (tenant_id, project_id, sha256) DO UPDATE SET
                    metadata = EXCLUDED.metadata,
                    updated_at = now()
                """,
                {
                    "tenant_id": tenant_id,
                    "project_id": project_id,
                    "sha256": sha256,
                    "metadata": meta_json,
                },
            )
        if alias_key:
            await conn.execute(
                """
                INSERT INTO kb_blob_aliases (tenant_id, project_id, alias_key, sha256)
                VALUES (%(tenant_id)s, %(project_id)s, %(alias_key)s, %(sha256)s)
                ON CONFLICT (tenant_id, project_id, alias_key) DO UPDATE SET
                    sha256 = EXCLUDED.sha256,
                    updated_at = now()
                """,
                {
                    "tenant_id": tenant_id,
                    "project_id": project_id or "",
                    "alias_key": alias_key,
                    "sha256": sha256,
                },
            )

    async def _count_chunks(self, conn: Any, *, table: str, tenant_id: str, sha256: str) -> int:
        cnt_sql = (
            """
            SELECT COUNT(*) AS actual_chunks
            FROM kb_blob_chunks_large
            WHERE tenant_id = %(tenant_id)s AND blob_sha256 = %(sha256)s
            """
            if table == "kb_blob_chunks_large"
            else """
            SELECT COUNT(*) AS actual_chunks
            FROM kb_blob_chunks
            WHERE tenant_id = %(tenant_id)s AND blob_sha256 = %(sha256)s
            """
        )
        cnt_row = await (
            await conn.execute(cnt_sql, {"tenant_id": tenant_id, "sha256": sha256})
        ).fetchone()
        return int(cnt_row["actual_chunks"]) if cnt_row else 0

    async def head(
        self,
        *,
        key: str,
        tenant_id: str = "default",
        project_id: str | None = None,
    ) -> KBBlob | None:
        async with self._conn(tenant_id=tenant_id) as conn:
            sha256 = await self._resolve_sha256_for_read(
                conn, key=key, tenant_id=tenant_id, project_id=project_id
            )
            if sha256 is None:
                return None

            if project_id is not None:
                row = await (
                    await conn.execute(
                        """
                        SELECT b.tenant_id, b.sha256, bp.project_id, b.media_type, b.byte_size,
                               b.chunk_size, b.chunk_count, b.storage_tier, b.status, bp.metadata
                        FROM kb_blobs b
                        JOIN kb_blob_projects bp
                          ON bp.tenant_id = b.tenant_id
                         AND bp.sha256 = b.sha256
                         AND bp.project_id = %(project_id)s
                        WHERE b.tenant_id = %(tenant_id)s AND b.sha256 = %(sha256)s
                        """,
                        {
                            "tenant_id": tenant_id,
                            "sha256": sha256,
                            "project_id": project_id,
                        },
                    )
                ).fetchone()
            else:
                row = await (
                    await conn.execute(
                        """
                        SELECT tenant_id, sha256, project_id, media_type, byte_size,
                               chunk_size, chunk_count, storage_tier, status, metadata
                        FROM kb_blobs
                        WHERE tenant_id = %(tenant_id)s AND sha256 = %(sha256)s
                        """,
                        {"tenant_id": tenant_id, "sha256": sha256},
                    )
                ).fetchone()
            if row is None:
                return None

            tier = str(row["storage_tier"])
            table = _chunk_table_for_tier(tier)
            expected_chunks = int(row["chunk_count"])
            status = str(row["status"])

            if status == "ready" and expected_chunks > 0:
                actual_chunks = await self._count_chunks(
                    conn, table=table, tenant_id=tenant_id, sha256=sha256
                )
                if actual_chunks < expected_chunks:
                    await conn.execute(
                        """
                        UPDATE kb_blobs
                        SET status = 'failed', updated_at = now()
                        WHERE tenant_id = %(tenant_id)s AND sha256 = %(sha256)s
                        """,
                        {"tenant_id": tenant_id, "sha256": sha256},
                    )
                    await conn.commit()
                    raise BlobTruncatedError(
                        f"Blob {sha256} in tenant {tenant_id!r} truncated "
                        f"(expected {expected_chunks} chunks in {table}, found {actual_chunks})"
                    )

            raw_meta = row.get("metadata")
            meta_dict: Mapping[str, Any] = (
                raw_meta
                if isinstance(raw_meta, Mapping)
                else json.loads(raw_meta)
                if isinstance(raw_meta, str) and raw_meta
                else {}
            )
            return KBBlob(
                tenant_id=str(row["tenant_id"]),
                sha256=str(row["sha256"]),
                project_id=row.get("project_id"),
                media_type=str(row["media_type"]),
                byte_size=int(row["byte_size"]),
                chunk_size=int(row["chunk_size"]),
                chunk_count=expected_chunks,
                storage_tier=tier,
                status=status,
                metadata=meta_dict,
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
        """Two-pass O(1)-RAM file ingestion with locked CAS dedup and project registration."""
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
        with file_path.open("rb") as f:
            expected_sha256 = hashlib.file_digest(f, "sha256").hexdigest()

        def _file_chunks() -> Iterable[bytes]:
            with file_path.open("rb") as fh:
                while chunk := fh.read(self._chunk_size):
                    yield chunk

        return await self.put_stream(
            key=expected_sha256,
            stream=_file_chunks(),
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
            _, parsed_alias = _parse_blob_key(key, tenant_id=tenant_id)
            alias_key = parsed_alias

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
        table = _chunk_table_for_tier(tier)
        expected_chunks = (
            0 if byte_size == 0 else (byte_size + self._chunk_size - 1) // self._chunk_size
        )
        caller_meta = dict(metadata or {})
        meta_json = json.dumps(caller_meta, sort_keys=True)
        lock_key = _cas_lock_key(tenant_id, sha256)

        async with self._semaphore:
            async with self._conn(tenant_id=tenant_id) as conn:
                await conn.execute("SELECT pg_advisory_lock(%(lock_key)s)", {"lock_key": lock_key})
                try:
                    existing_row = await (
                        await conn.execute(
                            """
                            SELECT status, byte_size, chunk_size, chunk_count,
                                   storage_tier, media_type
                            FROM kb_blobs
                            WHERE tenant_id = %(tenant_id)s AND sha256 = %(sha256)s
                            FOR UPDATE
                            """,
                            {"tenant_id": tenant_id, "sha256": sha256},
                        )
                    ).fetchone()
                    if (
                        existing_row is not None
                        and str(existing_row["status"]) == "ready"
                        and int(existing_row["byte_size"]) == byte_size
                        and int(existing_row["chunk_size"]) == self._chunk_size
                        and int(existing_row["chunk_count"]) == expected_chunks
                    ):
                        existing_tier = str(existing_row["storage_tier"])
                        existing_table = _chunk_table_for_tier(existing_tier)
                        actual_chunks = (
                            0
                            if expected_chunks == 0
                            else await self._count_chunks(
                                conn, table=existing_table, tenant_id=tenant_id, sha256=sha256
                            )
                        )
                        if actual_chunks == expected_chunks:
                            await self._upsert_project_and_alias(
                                conn,
                                tenant_id=tenant_id,
                                project_id=project_id,
                                sha256=sha256,
                                meta_json=meta_json,
                                alias_key=alias_key,
                            )
                            await conn.commit()
                            return KBBlob(
                                tenant_id=tenant_id,
                                sha256=sha256,
                                project_id=project_id,
                                media_type=str(existing_row["media_type"]),
                                byte_size=byte_size,
                                chunk_size=self._chunk_size,
                                chunk_count=expected_chunks,
                                storage_tier=existing_tier,
                                status="ready",
                                metadata=caller_meta,
                            )

                    await conn.execute(
                        """
                        INSERT INTO kb_blobs
                            (tenant_id, sha256, project_id, media_type, byte_size,
                             chunk_size, chunk_count, storage_tier, status, metadata)
                        VALUES (%(tenant_id)s, %(sha256)s, %(project_id)s, %(media_type)s,
                                %(byte_size)s, %(chunk_size)s, %(chunk_count)s, %(tier)s,
                                'uploading', %(metadata)s::jsonb)
                        ON CONFLICT (tenant_id, sha256) DO UPDATE SET
                            status = 'uploading',
                            project_id = COALESCE(kb_blobs.project_id, EXCLUDED.project_id),
                            byte_size = EXCLUDED.byte_size,
                            chunk_size = EXCLUDED.chunk_size,
                            chunk_count = EXCLUDED.chunk_count,
                            storage_tier = EXCLUDED.storage_tier,
                            metadata = EXCLUDED.metadata,
                            updated_at = now()
                        """,
                        {
                            "tenant_id": tenant_id,
                            "sha256": sha256,
                            "project_id": project_id,
                            "media_type": content_type or "application/octet-stream",
                            "byte_size": byte_size,
                            "chunk_size": self._chunk_size,
                            "chunk_count": expected_chunks,
                            "tier": tier,
                            "metadata": meta_json,
                        },
                    )
                    await conn.execute(
                        "DELETE FROM kb_blob_chunks "
                        "WHERE tenant_id = %(tenant_id)s AND blob_sha256 = %(sha256)s",
                        {"tenant_id": tenant_id, "sha256": sha256},
                    )
                    await conn.execute(
                        "DELETE FROM kb_blob_chunks_large "
                        "WHERE tenant_id = %(tenant_id)s AND blob_sha256 = %(sha256)s",
                        {"tenant_id": tenant_id, "sha256": sha256},
                    )

                    whole_hasher = hashlib.sha256()
                    buffer = bytearray()
                    chunk_index = 0
                    total_bytes = 0

                    if expected_chunks > 0:
                        insert_chunk_sql = (
                            """
                            INSERT INTO kb_blob_chunks_large (
                                tenant_id, blob_sha256, chunk_index, chunk_sha256, byte_size, data
                            )
                            VALUES (
                                %(tenant_id)s, %(sha256)s, %(chunk_index)s,
                                %(chunk_sha256)s, %(byte_size)s, %(data)s
                            )
                            ON CONFLICT (tenant_id, blob_sha256, chunk_index) DO UPDATE SET
                                chunk_sha256 = EXCLUDED.chunk_sha256,
                                byte_size = EXCLUDED.byte_size,
                                data = EXCLUDED.data
                            """
                            if table == "kb_blob_chunks_large"
                            else """
                            INSERT INTO kb_blob_chunks (
                                tenant_id, blob_sha256, chunk_index, chunk_sha256, byte_size, data
                            )
                            VALUES (
                                %(tenant_id)s, %(sha256)s, %(chunk_index)s,
                                %(chunk_sha256)s, %(byte_size)s, %(data)s
                            )
                            ON CONFLICT (tenant_id, blob_sha256, chunk_index) DO UPDATE SET
                                chunk_sha256 = EXCLUDED.chunk_sha256,
                                byte_size = EXCLUDED.byte_size,
                                data = EXCLUDED.data
                            """
                        )

                        async def _write_chunk(c_bytes: bytes) -> None:
                            nonlocal chunk_index
                            c_sha = hashlib.sha256(c_bytes).digest()
                            await conn.execute(
                                insert_chunk_sql,
                                {
                                    "tenant_id": tenant_id,
                                    "sha256": sha256,
                                    "chunk_index": chunk_index,
                                    "chunk_sha256": c_sha,
                                    "byte_size": len(c_bytes),
                                    "data": c_bytes,
                                },
                            )
                            chunk_index += 1
                            if chunk_index % HEARTBEAT_CHUNK_INTERVAL == 0:
                                await conn.execute(
                                    """
                                    UPDATE kb_blobs
                                    SET updated_at = now()
                                    WHERE tenant_id = %(tenant_id)s
                                      AND sha256 = %(sha256)s
                                      AND status = 'uploading'
                                    """,
                                    {"tenant_id": tenant_id, "sha256": sha256},
                                )

                        if isinstance(stream, AsyncIterator):
                            async for piece in stream:
                                if not piece:
                                    continue
                                total_bytes += len(piece)
                                if total_bytes > self._max_blob_bytes:
                                    max_b = self._max_blob_bytes
                                    raise BlobTooLargeError(
                                        f"Stream exceeded maximum {max_b} bytes"
                                    )
                                whole_hasher.update(piece)
                                buffer.extend(piece)
                                while len(buffer) >= self._chunk_size:
                                    blk = bytes(buffer[: self._chunk_size])
                                    del buffer[: self._chunk_size]
                                    await _write_chunk(blk)
                        else:
                            for piece in stream:
                                if not piece:
                                    continue
                                total_bytes += len(piece)
                                if total_bytes > self._max_blob_bytes:
                                    max_b = self._max_blob_bytes
                                    raise BlobTooLargeError(
                                        f"Stream exceeded maximum {max_b} bytes"
                                    )
                                whole_hasher.update(piece)
                                buffer.extend(piece)
                                while len(buffer) >= self._chunk_size:
                                    blk = bytes(buffer[: self._chunk_size])
                                    del buffer[: self._chunk_size]
                                    await _write_chunk(blk)

                        if buffer:
                            await _write_chunk(bytes(buffer))
                            buffer.clear()

                    computed_sha256 = whole_hasher.hexdigest()
                    if computed_sha256 != sha256:
                        raise BlobIntegrityError(
                            f"Whole-blob SHA-256 mismatch: expected {sha256}, "
                            f"computed {computed_sha256}"
                        )
                    if total_bytes != byte_size or chunk_index != expected_chunks:
                        raise BlobIntegrityError(
                            f"Chunk count/size mismatch: expected ({byte_size} B, "
                            f"{expected_chunks} chunks), wrote "
                            f"({total_bytes} B, {chunk_index} chunks)"
                        )

                    await conn.execute(
                        """
                        UPDATE kb_blobs
                        SET status = 'ready', updated_at = now()
                        WHERE tenant_id = %(tenant_id)s AND sha256 = %(sha256)s
                        """,
                        {"tenant_id": tenant_id, "sha256": sha256},
                    )
                    await self._upsert_project_and_alias(
                        conn,
                        tenant_id=tenant_id,
                        project_id=project_id,
                        sha256=sha256,
                        meta_json=meta_json,
                        alias_key=alias_key,
                    )
                    await conn.commit()
                    await self._reapply_rls(conn, tenant_id)
                except Exception:
                    await conn.rollback()
                    raise
                finally:
                    try:
                        await conn.execute(
                            "SELECT pg_advisory_unlock(%(lock_key)s)",
                            {"lock_key": lock_key},
                        )
                    except Exception as unlock_exc:  # noqa: BLE001  # reason: best-effort session unlock before close
                        logger.debug("Advisory unlock skipped: %s", unlock_exc)

            if tier == "unlogged":
                try:
                    async with self._conn(autocommit=True) as ck_conn:
                        await ck_conn.execute("CHECKPOINT")
                except Exception as ck_exc:  # noqa: BLE001  # reason: non-superuser roles may lack CHECKPOINT privilege
                    logger.debug("Optional post-upload CHECKPOINT skipped: %s", ck_exc)

        return KBBlob(
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

    async def get_stream(
        self,
        *,
        key: str,
        tenant_id: str = "default",
        project_id: str | None = None,
        chunk_batch_size: int = 4,
    ) -> AsyncIterator[bytes]:
        async with self._semaphore:
            blob = await self.head(key=key, tenant_id=tenant_id, project_id=project_id)
            if blob is None or blob.status != "ready":
                raise BlobNotFoundError(f"Blob {key!r} not found in tenant {tenant_id!r}")
            if blob.chunk_count == 0:
                return

            table = _chunk_table_for_tier(blob.storage_tier)
            batch_limit = max(1, min(chunk_batch_size, 16))
            next_idx = 0
            yielded = 0

            async with self._conn(tenant_id=tenant_id) as conn:
                fetch_sql = (
                    """
                    SELECT chunk_index, chunk_sha256, data
                    FROM kb_blob_chunks_large
                    WHERE tenant_id = %(tenant_id)s
                      AND blob_sha256 = %(sha256)s
                      AND chunk_index >= %(next_idx)s
                    ORDER BY chunk_index ASC
                    LIMIT %(batch_limit)s
                    """
                    if table == "kb_blob_chunks_large"
                    else """
                    SELECT chunk_index, chunk_sha256, data
                    FROM kb_blob_chunks
                    WHERE tenant_id = %(tenant_id)s
                      AND blob_sha256 = %(sha256)s
                      AND chunk_index >= %(next_idx)s
                    ORDER BY chunk_index ASC
                    LIMIT %(batch_limit)s
                    """
                )
                while next_idx < blob.chunk_count:
                    rows = await (
                        await conn.execute(
                            fetch_sql,
                            {
                                "tenant_id": tenant_id,
                                "sha256": blob.sha256,
                                "next_idx": next_idx,
                                "batch_limit": batch_limit,
                            },
                        )
                    ).fetchall()
                    if not rows:
                        raise BlobTruncatedError(
                            f"Blob {blob.sha256} truncated at chunk_index {next_idx} "
                            f"(expected {blob.chunk_count} chunks)"
                        )
                    for row in rows:
                        c_idx = int(row["chunk_index"])
                        if c_idx != next_idx:
                            raise BlobTruncatedError(
                                f"Blob {blob.sha256} missing chunk_index {next_idx} (found {c_idx})"
                            )
                        data = bytes(row["data"])
                        expected_digest = bytes(row["chunk_sha256"])
                        actual_digest = hashlib.sha256(data).digest()
                        if actual_digest != expected_digest:
                            raise BlobIntegrityError(
                                f"SHA-256 mismatch on chunk {c_idx} of blob {blob.sha256}"
                            )
                        yield data
                        yielded += 1
                        next_idx = c_idx + 1

            if yielded != blob.chunk_count:
                raise BlobTruncatedError(
                    f"Blob {blob.sha256} yielded {yielded} chunks, expected {blob.chunk_count}"
                )

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
        async with self._conn(tenant_id=tenant_id) as conn:
            sha256 = await self._resolve_sha256_for_read(
                conn, key=key, tenant_id=tenant_id, project_id=project_id
            )
            if sha256 is None:
                return
            _, alias = _parse_blob_key(key, tenant_id=tenant_id)
            lock_key = _cas_lock_key(tenant_id, sha256)
            await conn.execute("SELECT pg_advisory_lock(%(lock_key)s)", {"lock_key": lock_key})
            try:
                if project_id is not None:
                    if alias:
                        await conn.execute(
                            "DELETE FROM kb_blob_aliases "
                            "WHERE tenant_id = %(tenant_id)s "
                            "  AND project_id = %(project_id)s "
                            "  AND alias_key = %(alias_key)s",
                            {
                                "tenant_id": tenant_id,
                                "project_id": project_id,
                                "alias_key": alias,
                            },
                        )
                    cur = await conn.execute(
                        "DELETE FROM kb_blob_projects "
                        "WHERE tenant_id = %(tenant_id)s "
                        "  AND project_id = %(project_id)s "
                        "  AND sha256 = %(sha256)s",
                        {
                            "tenant_id": tenant_id,
                            "project_id": project_id,
                            "sha256": sha256,
                        },
                    )
                    if (cur.rowcount or 0) == 0:
                        await conn.commit()
                        return
                    rem_row = await (
                        await conn.execute(
                            "SELECT COUNT(*) AS n FROM kb_blob_projects "
                            "WHERE tenant_id = %(tenant_id)s AND sha256 = %(sha256)s",
                            {"tenant_id": tenant_id, "sha256": sha256},
                        )
                    ).fetchone()
                    remaining = int(rem_row["n"]) if rem_row else 0
                    blob_row = await (
                        await conn.execute(
                            "SELECT project_id FROM kb_blobs "
                            "WHERE tenant_id = %(tenant_id)s AND sha256 = %(sha256)s",
                            {"tenant_id": tenant_id, "sha256": sha256},
                        )
                    ).fetchone()
                    is_shared = blob_row is not None and blob_row.get("project_id") is None
                    if remaining > 0 or is_shared:
                        await conn.commit()
                        return

                await conn.execute(
                    "DELETE FROM kb_blob_aliases "
                    "WHERE tenant_id = %(tenant_id)s AND sha256 = %(sha256)s",
                    {"tenant_id": tenant_id, "sha256": sha256},
                )
                await conn.execute(
                    "DELETE FROM kb_blob_projects "
                    "WHERE tenant_id = %(tenant_id)s AND sha256 = %(sha256)s",
                    {"tenant_id": tenant_id, "sha256": sha256},
                )
                await conn.execute(
                    "DELETE FROM kb_blob_chunks "
                    "WHERE tenant_id = %(tenant_id)s AND blob_sha256 = %(sha256)s",
                    {"tenant_id": tenant_id, "sha256": sha256},
                )
                await conn.execute(
                    "DELETE FROM kb_blob_chunks_large "
                    "WHERE tenant_id = %(tenant_id)s AND blob_sha256 = %(sha256)s",
                    {"tenant_id": tenant_id, "sha256": sha256},
                )
                await conn.execute(
                    "DELETE FROM kb_blobs WHERE tenant_id = %(tenant_id)s AND sha256 = %(sha256)s",
                    {"tenant_id": tenant_id, "sha256": sha256},
                )
                await conn.commit()
            except Exception:
                await conn.rollback()
                raise
            finally:
                try:
                    await conn.execute(
                        "SELECT pg_advisory_unlock(%(lock_key)s)",
                        {"lock_key": lock_key},
                    )
                except Exception as unlock_exc:  # noqa: BLE001  # reason: best-effort session unlock before close
                    logger.debug("Advisory unlock skipped: %s", unlock_exc)

    async def reconcile_truncated_blobs(self, *, tenant_id: str | None = None) -> int:
        """Startup & periodic check marking status='failed' on any 'ready' UNLOGGED blob
        whose chunks were truncated by PostgreSQL after an unclean crash."""
        async with self._conn(tenant_id=tenant_id, worker_sweep=(tenant_id is None)) as conn:
            cur = await conn.execute(
                """
                UPDATE kb_blobs b
                SET status = 'failed', updated_at = now()
                WHERE b.status = 'ready'
                  AND b.storage_tier = 'unlogged'
                  AND b.chunk_count > 0
                  AND (%(tenant_id)s::text IS NULL OR b.tenant_id = %(tenant_id)s)
                  AND (
                      SELECT COUNT(*)
                      FROM kb_blob_chunks_large c
                      WHERE c.tenant_id = b.tenant_id AND c.blob_sha256 = b.sha256
                  ) < b.chunk_count
                """,
                {"tenant_id": tenant_id},
            )
            reconciled = cur.rowcount or 0
            await conn.commit()
            return int(reconciled)

    async def gc_orphan_chunks(
        self,
        *,
        older_than_seconds: int = 7200,
        tenant_id: str | None = None,
    ) -> int:
        """Delete stale 'uploading'/'failed' manifests and any orphaned chunk rows."""
        clean_tid = tenant_id.strip() if isinstance(tenant_id, str) and tenant_id.strip() else None
        async with self._conn(tenant_id=clean_tid, worker_sweep=(clean_tid is None)) as conn:
            c1 = await conn.execute(
                """
                DELETE FROM kb_blobs
                WHERE (tenant_id, sha256) IN (
                    SELECT tenant_id, sha256
                    FROM kb_blobs
                    WHERE status IN ('uploading', 'failed')
                      AND (%(tenant_id)s::text IS NULL OR tenant_id = %(tenant_id)s)
                      AND updated_at < now() - make_interval(secs => %(secs)s)
                    FOR UPDATE SKIP LOCKED
                )
                """,
                {"secs": int(older_than_seconds), "tenant_id": clean_tid},
            )
            c2 = await conn.execute(
                """
                DELETE FROM kb_blob_chunks c
                WHERE (%(tenant_id)s::text IS NULL OR c.tenant_id = %(tenant_id)s)
                  AND NOT EXISTS (
                      SELECT 1 FROM kb_blobs b
                      WHERE b.tenant_id = c.tenant_id AND b.sha256 = c.blob_sha256
                  )
                """,
                {"tenant_id": clean_tid},
            )
            c3 = await conn.execute(
                """
                DELETE FROM kb_blob_chunks_large c
                WHERE (%(tenant_id)s::text IS NULL OR c.tenant_id = %(tenant_id)s)
                  AND NOT EXISTS (
                      SELECT 1 FROM kb_blobs b
                      WHERE b.tenant_id = c.tenant_id AND b.sha256 = c.blob_sha256
                  )
                """,
                {"tenant_id": clean_tid},
            )
            total = (c1.rowcount or 0) + (c2.rowcount or 0) + (c3.rowcount or 0)
            await conn.commit()
            return int(total)
