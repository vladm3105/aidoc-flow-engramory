"""PgBlobStorageAdapter — PostgreSQL 18 20 GB+ Chunked Blob CAS (Rule D14a / D14b).

Implements the normative PostgreSQL-First Multimodal & Large-Media Blob Storage Standard
defined in ``platform-technology_stack/05-database-stack.md`` §6.14 and
``docs/MULTIMODAL_AND_POSTGRES_BLOB_STORAGE.md``:
- Manifest table ``kb_blobs`` in ``pg_default``
- Tier 1 ``kb_blob_chunks`` (``LOGGED``, ``<= 50 MiB``) and Tier 2 ``kb_blob_chunks_large``
  (``UNLOGGED``, ``> 50 MiB`` to ``25 GiB``) in ``ts_blob_cold`` with ``STORAGE EXTERNAL``
- Two-pass ``put_file`` (O(1) RAM ``hashlib.file_digest`` + ``< 2 ms`` dedup ``head()`` check +
  ``COPY ... FROM STDIN WITH (FORMAT BINARY)``) and spooled ``put_stream``
- Crash-truncation detection (``BlobTruncatedError``) when ``COUNT(chunk_index) < chunk_count``
- Bounded keyset streaming reads (``LIMIT 4`` chunks per query = ``16 MiB`` window)
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


def _normalize_sha256_key(key: str) -> str:
    if key.startswith("pgblob://"):
        parts = key[len("pgblob://") :].strip("/").split("/")
        candidate = parts[-1].lower()
    else:
        candidate = key.lower()
    if len(candidate) == 64 and all(c in "0123456789abcdef" for c in candidate):
        return candidate
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


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
    async def _conn(self, *, autocommit: bool = False) -> AsyncIterator[Any]:
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
            yield conn
        finally:
            await conn.close()

    async def head(self, *, key: str, tenant_id: str = "default") -> KBBlob | None:
        sha256 = _normalize_sha256_key(key)
        async with self._conn() as conn:
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
                    await conn.execute(
                        cnt_sql,
                        {"tenant_id": tenant_id, "sha256": sha256},
                    )
                ).fetchone()
                actual_chunks = int(cnt_row["actual_chunks"]) if cnt_row else 0
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

    async def exists(self, *, key: str, tenant_id: str = "default") -> bool:
        try:
            blob = await self.head(key=key, tenant_id=tenant_id)
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
        await self.put_stream(
            key=sha256,
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
        """Two-pass O(1)-RAM file ingestion:
        Pass 1 computes (byte_size, sha256) and short-circuits duplicate blobs via head().
        Pass 2 streams 4 MiB chunks into COPY BINARY.
        """
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

        try:
            existing = await self.head(key=expected_sha256, tenant_id=tenant_id)
            if existing is not None and existing.status == "ready":
                return existing
        except BlobTruncatedError:
            logger.info("Re-uploading truncated blob %s for tenant %s", expected_sha256, tenant_id)

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

        # If caller did not provide both expected_sha256 and byte_size, spool in 4 MiB
        # blocks to a confined SpooledTemporaryFile (16 MiB RAM threshold) first so we
        # know the exact sha256 and tier before opening COPY BINARY.
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
                )

        return await self._put_known_stream(
            sha256=expected_sha256.lower(),
            byte_size=byte_size,
            stream=stream,
            content_type=content_type,
            tenant_id=tenant_id,
            project_id=project_id,
            metadata=metadata,
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
    ) -> KBBlob:
        tier = "unlogged" if byte_size > self._unlogged_threshold_bytes else "logged"
        table = _chunk_table_for_tier(tier)
        expected_chunks = (
            0 if byte_size == 0 else (byte_size + self._chunk_size - 1) // self._chunk_size
        )
        meta_json = json.dumps(dict(metadata or {}), sort_keys=True)

        async with self._semaphore:
            async with self._conn() as conn:
                # 1. Upsert manifest in 'uploading' state and clear any partial chunks
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
                delete_chunks_sql = (
                    "DELETE FROM kb_blob_chunks_large "
                    "WHERE tenant_id = %(tenant_id)s AND blob_sha256 = %(sha256)s"
                    if table == "kb_blob_chunks_large"
                    else "DELETE FROM kb_blob_chunks "
                    "WHERE tenant_id = %(tenant_id)s AND blob_sha256 = %(sha256)s"
                )
                await conn.execute(
                    delete_chunks_sql,
                    {"tenant_id": tenant_id, "sha256": sha256},
                )
                await conn.commit()

                whole_hasher = hashlib.sha256()
                buffer = bytearray()
                chunk_index = 0
                total_bytes = 0

                try:
                    if expected_chunks > 0:
                        async with conn.cursor() as cur:
                            copy_sql = (
                                f"COPY {table} (tenant_id, blob_sha256, chunk_index, "
                                f"chunk_sha256, byte_size, data) FROM STDIN WITH (FORMAT BINARY)"
                            )
                            async with cur.copy(copy_sql) as copy:
                                if hasattr(copy, "set_types"):
                                    copy.set_types(
                                        ["text", "text", "int4", "bytea", "int4", "bytea"]
                                    )

                                async def _write_chunk(c_bytes: bytes) -> None:
                                    nonlocal chunk_index
                                    c_sha = hashlib.sha256(c_bytes).digest()
                                    await copy.write_row(
                                        (
                                            tenant_id,
                                            sha256,
                                            chunk_index,
                                            c_sha,
                                            len(c_bytes),
                                            c_bytes,
                                        )
                                    )
                                    chunk_index += 1

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
                    await conn.commit()
                except Exception:
                    await conn.rollback()
                    try:
                        await conn.execute(
                            delete_chunks_sql,
                            {"tenant_id": tenant_id, "sha256": sha256},
                        )
                        await conn.execute(
                            """
                            UPDATE kb_blobs
                            SET status = 'failed', updated_at = now()
                            WHERE tenant_id = %(tenant_id)s AND sha256 = %(sha256)s
                            """,
                            {"tenant_id": tenant_id, "sha256": sha256},
                        )
                        await conn.commit()
                    except Exception as cleanup_exc:  # noqa: BLE001  # reason: preserve primary upload error
                        logger.debug("Cleanup after failed blob upload ignored: %s", cleanup_exc)
                    raise

            # Best-effort post-upload CHECKPOINT for UNLOGGED large blobs
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
            metadata=metadata or {},
        )

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
        if blob.chunk_count == 0:
            return

        table = _chunk_table_for_tier(blob.storage_tier)
        batch_limit = max(1, min(chunk_batch_size, 16))
        next_idx = 0
        yielded = 0

        async with self._conn() as conn:
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

    async def get(self, *, key: str, tenant_id: str = "default") -> bytes:
        out = bytearray()
        async for chunk in self.get_stream(key=key, tenant_id=tenant_id):
            out.extend(chunk)
        return bytes(out)

    async def delete(self, *, key: str, tenant_id: str = "default") -> None:
        sha256 = _normalize_sha256_key(key)
        async with self._conn() as conn:
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

    async def reconcile_truncated_blobs(self, *, tenant_id: str | None = None) -> int:
        """Startup & periodic check marking status='failed' on any 'ready' UNLOGGED blob
        whose chunks were truncated by PostgreSQL after an unclean crash."""
        async with self._conn() as conn:
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

    async def gc_orphan_chunks(self, *, older_than_seconds: int = 7200) -> int:
        """Delete stale 'uploading'/'failed' manifests and any orphaned chunk rows."""
        async with self._conn() as conn:
            c1 = await conn.execute(
                """
                DELETE FROM kb_blobs
                WHERE status IN ('uploading', 'failed')
                  AND updated_at < now() - make_interval(secs => %(secs)s)
                """,
                {"secs": int(older_than_seconds)},
            )
            c2 = await conn.execute(
                """
                DELETE FROM kb_blob_chunks c
                WHERE NOT EXISTS (
                    SELECT 1 FROM kb_blobs b
                    WHERE b.tenant_id = c.tenant_id AND b.sha256 = c.blob_sha256
                )
                """
            )
            c3 = await conn.execute(
                """
                DELETE FROM kb_blob_chunks_large c
                WHERE NOT EXISTS (
                    SELECT 1 FROM kb_blobs b
                    WHERE b.tenant_id = c.tenant_id AND b.sha256 = c.blob_sha256
                )
                """
            )
            total = (c1.rowcount or 0) + (c2.rowcount or 0) + (c3.rowcount or 0)
            await conn.commit()
            return int(total)
