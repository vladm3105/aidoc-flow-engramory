"""Unit tests for StoragePort, LocalFsStorageAdapter, and PgBlobStorageAdapter (PLAN-023)."""

from __future__ import annotations

import hashlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest

from engramory.adapters.dev.storage_local import LocalFsStorageAdapter
from engramory.adapters.dev.storage_pg import (
    DEFAULT_CHUNK_SIZE,
    MAX_BLOB_BYTES,
    UNLOGGED_THRESHOLD_BYTES,
    PgBlobStorageAdapter,
    _chunk_table_for_tier,
)
from engramory.adapters.factory import dev_adapters
from engramory.ports.storage import (
    BlobIntegrityError,
    BlobNotFoundError,
    BlobTooLargeError,
    BlobTruncatedError,
    KBBlob,
)


class _FakeCopy:
    def __init__(self, store: _FakePgState, table: str) -> None:
        self._store = store
        self._table = table
        self.types: list[str] = []

    def set_types(self, types: list[str]) -> None:
        self.types = list(types)

    async def write_row(self, row: tuple[Any, ...]) -> None:
        tenant_id, blob_sha256, chunk_index, chunk_sha256, byte_size, data = row
        target = (
            self._store.chunks_large
            if self._table == "kb_blob_chunks_large"
            else self._store.chunks_logged
        )
        target[(str(tenant_id), str(blob_sha256), int(chunk_index))] = {
            "tenant_id": str(tenant_id),
            "blob_sha256": str(blob_sha256),
            "chunk_index": int(chunk_index),
            "chunk_sha256": bytes(chunk_sha256),
            "byte_size": int(byte_size),
            "data": bytes(data),
        }


class _FakeCursor:
    def __init__(self, store: _FakePgState) -> None:
        self._store = store

    async def __aenter__(self) -> _FakeCursor:
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        return None

    @asynccontextmanager
    async def copy(self, sql: str) -> AsyncIterator[_FakeCopy]:
        table = "kb_blob_chunks_large" if "kb_blob_chunks_large" in sql else "kb_blob_chunks"
        yield _FakeCopy(self._store, table)


class _FakeResult:
    def __init__(self, rows: list[dict[str, Any]], rowcount: int = 0) -> None:
        self._rows = rows
        self.rowcount = rowcount or len(rows)

    async def fetchone(self) -> dict[str, Any] | None:
        return self._rows[0] if self._rows else None

    async def fetchall(self) -> list[dict[str, Any]]:
        return list(self._rows)


class _FakePgState:
    def __init__(self) -> None:
        self.blobs: dict[tuple[str, str], dict[str, Any]] = {}
        self.chunks_logged: dict[tuple[str, str, int], dict[str, Any]] = {}
        self.chunks_large: dict[tuple[str, str, int], dict[str, Any]] = {}
        self.checkpoints: int = 0


class _FakeConn:
    def __init__(self, store: _FakePgState) -> None:
        self._store = store

    def cursor(self) -> _FakeCursor:
        return _FakeCursor(self._store)

    async def commit(self) -> None:
        return None

    async def rollback(self) -> None:
        return None

    async def execute(self, sql: str, params: dict[str, Any] | None = None) -> _FakeResult:
        p = params or {}
        norm = " ".join(sql.split())

        if norm.startswith("CHECKPOINT"):
            self._store.checkpoints += 1
            return _FakeResult([])

        if "INSERT INTO kb_blobs" in norm:
            key = (str(p["tenant_id"]), str(p["sha256"]))
            self._store.blobs[key] = {
                "tenant_id": str(p["tenant_id"]),
                "sha256": str(p["sha256"]),
                "project_id": p.get("project_id"),
                "media_type": str(p["media_type"]),
                "byte_size": int(p["byte_size"]),
                "chunk_size": int(p["chunk_size"]),
                "chunk_count": int(p["chunk_count"]),
                "storage_tier": str(p["tier"]),
                "status": "uploading",
                "metadata": p.get("metadata", "{}"),
            }
            return _FakeResult([self._store.blobs[key]])

        if "UPDATE kb_blobs SET status = 'ready'" in norm:
            key = (str(p["tenant_id"]), str(p["sha256"]))
            if key in self._store.blobs:
                self._store.blobs[key]["status"] = "ready"
            return _FakeResult([], rowcount=1)

        if "UPDATE kb_blobs b SET status = 'failed'" in norm:
            tid_filter = p.get("tenant_id")
            updated = 0
            for (tid, sha), b in list(self._store.blobs.items()):
                if tid_filter is not None and tid != tid_filter:
                    continue
                if b["storage_tier"] == "unlogged" and b["status"] == "ready":
                    cnt = sum(
                        1 for (ct, cs, _) in self._store.chunks_large if ct == tid and cs == sha
                    )
                    if cnt < b["chunk_count"]:
                        b["status"] = "failed"
                        updated += 1
            return _FakeResult([], rowcount=updated)

        if "UPDATE kb_blobs SET status = 'failed'" in norm:
            key = (str(p["tenant_id"]), str(p["sha256"]))
            if key in self._store.blobs:
                self._store.blobs[key]["status"] = "failed"
            return _FakeResult([], rowcount=1)

        if "SELECT tenant_id, sha256, project_id" in norm and "FROM kb_blobs" in norm:
            key = (str(p["tenant_id"]), str(p["sha256"]))
            row = self._store.blobs.get(key)
            return _FakeResult([row] if row else [])

        if "SELECT COUNT(*) AS actual_chunks FROM kb_blob_chunks_large" in norm:
            tid, sha = str(p["tenant_id"]), str(p["sha256"])
            cnt = sum(1 for (ct, cs, _) in self._store.chunks_large if ct == tid and cs == sha)
            return _FakeResult([{"actual_chunks": cnt}])

        if "SELECT COUNT(*) AS actual_chunks FROM kb_blob_chunks" in norm:
            tid, sha = str(p["tenant_id"]), str(p["sha256"])
            cnt = sum(1 for (ct, cs, _) in self._store.chunks_logged if ct == tid and cs == sha)
            return _FakeResult([{"actual_chunks": cnt}])

        if "SELECT chunk_index, chunk_sha256, data" in norm:
            target = (
                self._store.chunks_large
                if "kb_blob_chunks_large" in norm
                else self._store.chunks_logged
            )
            tid, sha = str(p["tenant_id"]), str(p["sha256"])
            cursor_idx = int(p["next_idx"])
            limit = int(p["batch_limit"])
            matching = sorted(
                (
                    r
                    for (ct, cs, cidx), r in target.items()
                    if ct == tid and cs == sha and cidx >= cursor_idx
                ),
                key=lambda x: x["chunk_index"],
            )[:limit]
            return _FakeResult(matching)

        if norm.startswith("DELETE FROM kb_blob_chunks_large WHERE tenant_id ="):
            tid, sha = str(p["tenant_id"]), str(p["sha256"])
            to_del = [k for k in self._store.chunks_large if k[0] == tid and k[1] == sha]
            for k in to_del:
                del self._store.chunks_large[k]
            return _FakeResult([], rowcount=len(to_del))

        if norm.startswith("DELETE FROM kb_blob_chunks WHERE tenant_id ="):
            tid, sha = str(p["tenant_id"]), str(p["sha256"])
            to_del = [k for k in self._store.chunks_logged if k[0] == tid and k[1] == sha]
            for k in to_del:
                del self._store.chunks_logged[k]
            return _FakeResult([], rowcount=len(to_del))

        if norm.startswith("DELETE FROM kb_blobs WHERE tenant_id ="):
            key = (str(p["tenant_id"]), str(p["sha256"]))
            existed = int(key in self._store.blobs)
            self._store.blobs.pop(key, None)
            return _FakeResult([], rowcount=existed)

        return _FakeResult([])


class _FakePgBlobStorageAdapter(PgBlobStorageAdapter):
    def __init__(self, chunk_size: int = DEFAULT_CHUNK_SIZE) -> None:
        super().__init__(dsn="postgresql://localhost:5432/engramory", chunk_size=chunk_size)
        self.state = _FakePgState()

    @asynccontextmanager
    async def _conn(self, *, autocommit: bool = False) -> AsyncIterator[Any]:
        yield _FakeConn(self.state)


@pytest.mark.asyncio
async def test_local_fs_storage_adapter_roundtrip_and_tenant_wall(tmp_path: Path) -> None:
    adapter = LocalFsStorageAdapter(base_dir=tmp_path / "cas", chunk_size_bytes=65536)

    # 1. Empty 0 B blob
    empty_file = tmp_path / "empty.bin"
    empty_file.write_bytes(b"")
    b0 = await adapter.put_file(
        file_path=empty_file,
        content_type="application/octet-stream",
        tenant_id="tenant-a",
        project_id="proj-1",
    )
    assert isinstance(b0, KBBlob)
    assert b0.byte_size == 0
    assert b0.chunk_count == 0
    assert await adapter.get(key=b0.sha256, tenant_id="tenant-a") == b""

    # 2. Multi-chunk 256 KiB blob (4 x 64 KiB chunks)
    payload = b"ABCD" * 65536  # 262,144 B (256 KiB)
    f256 = tmp_path / "sample.bin"
    f256.write_bytes(payload)
    blob = await adapter.put_file(
        file_path=f256,
        content_type="video/mp4",
        tenant_id="tenant-a",
        project_id="proj-1",
        metadata={"title": "demo"},
    )
    assert blob.byte_size == len(payload)
    assert blob.chunk_count == 4
    assert blob.status == "ready"

    # Cross-tenant isolation: tenant-b cannot see or read tenant-a's blob
    assert await adapter.exists(key=blob.sha256, tenant_id="tenant-b") is False
    assert await adapter.head(key=blob.sha256, tenant_id="tenant-b") is None
    with pytest.raises(BlobNotFoundError):
        await adapter.get(key=blob.sha256, tenant_id="tenant-b")

    # Streaming read in tenant-a
    streamed = bytearray()
    async for chunk in adapter.get_stream(key=blob.sha256, tenant_id="tenant-a"):
        streamed.extend(chunk)
    assert bytes(streamed) == payload

    # Integrity check on wrong expected_sha256
    with pytest.raises(BlobIntegrityError):
        await adapter.put_stream(
            key="bad",
            stream=[b"hello"],
            content_type="text/plain",
            tenant_id="tenant-a",
            expected_sha256="0" * 64,
        )


@pytest.mark.asyncio
async def test_pg_blob_storage_adapter_logged_unlogged_and_crash_truncation(
    tmp_path: Path,
) -> None:
    adapter = _FakePgBlobStorageAdapter(chunk_size=65536)

    # 1. Put file in LOGGED tier (150 KiB -> 3 chunks of 64 KiB)
    raw = b"X" * 153600
    expected_sha = hashlib.sha256(raw).hexdigest()
    f_small = tmp_path / "clip.mp4"
    f_small.write_bytes(raw)

    b1 = await adapter.put_file(
        file_path=f_small,
        content_type="video/mp4",
        tenant_id="t1",
        project_id="p1",
    )
    assert b1.sha256 == expected_sha
    assert b1.storage_tier == "logged"
    assert b1.chunk_count == 3
    assert len(adapter.state.chunks_logged) == 3

    # Dedup fast-path: second put_file returns existing ready blob without re-uploading
    b1_dup = await adapter.put_file(
        file_path=f_small,
        content_type="video/mp4",
        tenant_id="t1",
        project_id="p1",
    )
    assert b1_dup.sha256 == expected_sha
    assert await adapter.get(key=expected_sha, tenant_id="t1") == raw

    # 2. Simulate UNLOGGED tier (> 50 MiB) table routing & crash truncation
    assert _chunk_table_for_tier("unlogged") == "kb_blob_chunks_large"
    assert _chunk_table_for_tier("logged") == "kb_blob_chunks"

    unlogged_sha = "a" * 64
    chunk0 = b"frame0"
    adapter.state.blobs[("t1", unlogged_sha)] = {
        "tenant_id": "t1",
        "sha256": unlogged_sha,
        "project_id": "p1",
        "media_type": "video/mp4",
        "byte_size": UNLOGGED_THRESHOLD_BYTES + 1024,
        "chunk_size": 65536,
        "chunk_count": 2,
        "storage_tier": "unlogged",
        "status": "ready",
        "metadata": "{}",
    }
    adapter.state.chunks_large[("t1", unlogged_sha, 0)] = {
        "tenant_id": "t1",
        "blob_sha256": unlogged_sha,
        "chunk_index": 0,
        "chunk_sha256": hashlib.sha256(chunk0).digest(),
        "byte_size": len(chunk0),
        "data": chunk0,
    }
    # Chunk 1 is missing (simulating UNLOGGED table truncation after OS crash)
    with pytest.raises(BlobTruncatedError):
        await adapter.get(key=unlogged_sha, tenant_id="t1")
    assert adapter.state.blobs[("t1", unlogged_sha)]["status"] == "failed"

    # 3. Startup reconciliation marks truncated UNLOGGED blobs as failed
    adapter.state.blobs[("t1", unlogged_sha)]["status"] = "ready"
    reconciled = await adapter.reconcile_truncated_blobs(tenant_id="t1")
    assert reconciled == 1
    assert adapter.state.blobs[("t1", unlogged_sha)]["status"] == "failed"

    # 4. Corrupt chunk_sha256 raises BlobIntegrityError
    first_key = ("t1", expected_sha, 0)
    adapter.state.chunks_logged[first_key]["data"] = b"CORRUPTED"
    with pytest.raises(BlobIntegrityError):
        await adapter.get(key=expected_sha, tenant_id="t1")

    # 5. Size above MAX_BLOB_BYTES raises BlobTooLargeError
    with pytest.raises(BlobTooLargeError):
        await adapter.put_stream(
            key="huge",
            stream=[b"x"],
            content_type="video/mp4",
            byte_size=MAX_BLOB_BYTES + 1,
        )


def test_dev_adapters_honors_engramory_blob_dsn(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENGRAMORY_BLOB_DSN", "postgresql://127.0.0.1:5432/direct_blob")
    adapters = dev_adapters("postgresql://127.0.0.1:6432/pooled")
    assert adapters.storage is not None
    assert isinstance(adapters.storage, PgBlobStorageAdapter)
    assert adapters.storage.dsn == "postgresql://127.0.0.1:5432/direct_blob"


@pytest.mark.asyncio
async def test_local_and_pg_storage_lifecycle_and_async_streams() -> None:
    local = LocalFsStorageAdapter(chunk_size_bytes=16)

    # 1. put() alias resolution, exists(), delete(), and close()
    await local.put(key="alias-key", data=b"0123456789abcdefABCDEF", tenant_id="t1")
    assert await local.exists(key="alias-key", tenant_id="t1") is True
    assert await local.get(key="alias-key", tenant_id="t1") == b"0123456789abcdefABCDEF"

    # AsyncIterator stream ingestion on LocalFsStorageAdapter
    async def _gen_local() -> AsyncIterator[bytes]:
        yield b"part-one-"
        yield b""
        yield b"part-two-0123456789"

    b_async = await local.put_stream(stream=_gen_local(), tenant_id="t1")
    assert await local.exists(key=f"pgblob://t1/{b_async.sha256}", tenant_id="t1") is True

    # Simulate truncated chunk in LocalFsStorageAdapter
    local._chunks[("t1", b_async.sha256)].pop()
    assert await local.exists(key=b_async.sha256, tenant_id="t1") is False

    # Re-mark ready and test reconcile_truncated_blobs + gc_orphan_chunks
    local._manifests[("t1", b_async.sha256)] = KBBlob(
        tenant_id="t1",
        sha256=b_async.sha256,
        media_type="application/octet-stream",
        byte_size=b_async.byte_size,
        chunk_size=16,
        chunk_count=2,
        storage_tier="logged",
        status="ready",
    )
    assert await local.reconcile_truncated_blobs(tenant_id="t1") == 1
    assert await local.gc_orphan_chunks() >= 1
    await local.delete(key="alias-key", tenant_id="t1")
    await local.close()

    # 2. PgBlobStorageAdapter put(), spooled put_stream() without expected_sha256,
    # AsyncIterator stream, exists(), delete(), gc_orphan_chunks(), close()
    pg = _FakePgBlobStorageAdapter(chunk_size=16)
    await pg.put(key="pg-direct", data=b"hello-pg-cas-world-12345", tenant_id="t1")
    sha = hashlib.sha256(b"hello-pg-cas-world-12345").hexdigest()
    assert await pg.exists(key=f"pgblob://t1/{sha}", tenant_id="t1") is True

    async def _gen_pg() -> AsyncIterator[bytes]:
        yield b"alpha-"
        yield b""
        yield b"beta-gamma-delta"

    b_spooled = await pg.put_stream(stream=_gen_pg(), tenant_id="t1")
    assert await pg.get(key=b_spooled.sha256, tenant_id="t1") == b"alpha-beta-gamma-delta"

    # Spooled sync iterable without expected_sha256
    b_spooled_sync = await pg.put_stream(stream=[b"sync-1-", b"sync-2"], tenant_id="t1")
    assert await pg.get(key=b_spooled_sync.sha256, tenant_id="t1") == b"sync-1-sync-2"

    await pg.delete(key=b_spooled.sha256, tenant_id="t1")
    assert await pg.exists(key=b_spooled.sha256, tenant_id="t1") is False
    await pg.gc_orphan_chunks(older_than_seconds=60)
    await pg.close()
