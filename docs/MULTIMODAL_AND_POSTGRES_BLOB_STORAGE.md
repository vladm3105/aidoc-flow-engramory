---
id: DOC-ENGRAMORY-MULTIMODAL-BLOB-CAS
title: "Engramory Multimodal Ingestion & PostgreSQL 18 Large-Blob (20 GB+) CAS Architecture"
kind: architecture
domain: knowledge-core
namespace: aidoc
status: active
autonomy_tier: green
watched_paths:
  - "src/engramory/ports/storage.py"
  - "src/engramory/ports/llm.py"
  - "src/engramory/ports/graph.py"
  - "src/engramory/core/parsers/detector.py"
  - "src/engramory/core/extraction/router.py"
  - "db/migrations/0001_init_memory.sql"
last_verified: "2026-11-12"
---

# Engramory Multimodal Ingestion & PostgreSQL 18 Large-Blob (`20 GB+`) CAS Architecture

**Authority:** Platform Technology Stack `05-database-stack.md` (§6.13, §6.14, `Rule D14a`/`D14b`, `Decision D17`), `22-hybrid-s3-object-storage-seaweedfs-and-cloudflare-r2.md`, `23-federated-graph-lake-embedded-kuzu-and-central-graphdb.md`, and Engramory `ADR-01` (*Postgres Spine*), `ADR-03` (*Hexagonal Ports*), and `ADR-05` (*Canonical Store*).

Engramory unifies text, document (`PDF`, `DOCX`, `HTML`), image (`PNG`, `JPEG`, `WebP`, `SVG`), audio (`MP3`, `WAV`, `M4A`), and large video (`MP4`, `MOV`, `WebM`, `MKV` up to `20 GB+`) processing on the shared **PostgreSQL 18** spine while projecting multi-hop entity relationships into **Neo4j** (`Decision D17`) as needed behind `GraphPort`.

---

## 1. Current State vs. Target Multimodal Architecture

### 1.1 Current Text & Document Pipeline (`v0.1`)

Engramory `v0.1` implements a text-and-document ingestion and hybrid retrieval pipeline:

- **MIME & Format Detection (`src/engramory/core/parsers/detector.py`):** Maps `.md`, `.yaml`, `.pdf`, `.docx`, `.html`, `.txt`, and `.json` files into `markdown`, `yaml`, `pdf`, `docx`, `html`, `text`, and `json` categories.
- **Smart Extraction (`src/engramory/core/extraction/router.py`):** Routes PDFs (`Docling` / `PyMuPDF`), DOCX, and HTML (`BeautifulSoup4`) through `SmartExtractionRouter` into structured Markdown (`ExtractedSection`).
- **PostgreSQL 18 Spine (`db/migrations/0001_init_memory.sql`):** Stores `episodes`, `memories`, and `kb_sections` as `text` (`content`, `content_raw`) with `vector(1536)` dense embeddings (`HNSW`) and `tsvector` (`ts_lex` `GIN`) full-text indexes.
- **Hexagonal Ports (`src/engramory/ports/`):** `LLMPort` (`embed(texts: Sequence[str])`), `VectorPort`, `GraphPort` (`Neo4jAdapter`), and `StoragePort` (`put`/`get`/`exists`/`delete`).

### 1.2 Target Multimodal Pipeline (Text, Images, Audio & `20 GB+` Video)

Engramory processes multimodal media using a **Derive-to-Text + Optional Clip/Frame Multimodal Embedding + Content-Addressable Blob Pointer** architecture:

1. **Content-Addressable Raw Blob Offload (`Rule D14a` & `D14b`):** Raw media binaries (`> 256 KiB` up to `20 GB+`) are streamed into `StoragePort` (`PgBlobStorageAdapter` in PostgreSQL 18 `TABLESPACE ts_blob_cold` or `OCI Object Storage`) and referenced by `pgblob://sha256/<hex>` or `oci://...` pointers.
2. **Modality-Specific Derived Text & Temporal Segmentation:**
   - **Images (`PNG`, `JPEG`, `WebP`):** Vision-LLM captioning + OCR (`Docling` / LiteLLM multimodal router on `http://127.0.0.1:4000`) generates structured Markdown descriptions and extracted text stored in `kb_sections.content`.
   - **Video (`MP4`, `MOV`, `WebM`, `MKV` up to `20 GB+`) & Audio:** `ffmpeg` keyframe extraction + Whisper/ASR transcript diarization splits long recordings into time-coded scenes (`start_ms`, `end_ms`), storing each scene as a searchable `kb_sections` row linked to the parent `kb_blobs.sha256` manifest.
3. **Unified Hybrid Retrieval:** Text queries search `kb_sections` via `pgvector` HNSW + `ts_lex` BM25 and return exact citations (`doc_id`, `section_anchor`, `blob_uri`, `start_ms`..`end_ms`).

---

## 2. Why Dedicated S3 Daemons Are Not Required for Engramory

### 2.1 Operational Overhead of Self-Hosted S3 Emulators

Per Platform Technology Stack `22-hybrid-s3-object-storage-seaweedfs-and-cloudflare-r2.md`, self-hosted S3 emulator containers (`minio/minio` and `chrislusf/seaweedfs`) are retired across all platform tiers:

- **Zero Dual-Store Backup Skew:** Storing raw blobs in an external S3 daemon creates backup synchronization drift between `pgBackRest` WAL points-in-time and object bucket states.
- **Elimination of Sidecar Daemons:** Self-hosted S3 containers add IAM key rotation, two-container startup races, and path-style vs. virtual-host S3 drift.
- **PostgreSQL 18 Spine Alignment (`ADR-01` & `ADR-05`):** Keeping both structured metadata (`kb_documents`, `kb_sections`, `memories`) and content-addressed raw blobs inside PostgreSQL 18 preserves single-engine governance and transactional orphan prevention.

### 2.2 Approved `StoragePort` Backend Matrix (`Rule D14a` & `D14b`)

| Backend | URI Scheme | Target Workload | Durability & Backup Path |
| :--- | :--- | :--- | :--- |
| **`PgBlobStorageAdapter` (PostgreSQL 18 Chunked CAS)** | `pgblob://sha256/<64-hex>` | Self-contained `dev`/`stage`/`prod` multimodal artifacts, PDFs, images, and `20 GB+` video without external S3 | `pgBackRest` (`LOGGED` `<= 50 MiB`) + daily `pg_dump -Z 0 \| zstd \| age` (`UNLOGGED` `> 50 MiB`) |
| **`OciS3StorageAdapter` (OCI Object Storage / R2)** | `oci://<bucket>/<key>` | Multi-TB cloud-native media distribution and direct pre-signed CDN range delivery | OCI Object Versioning + daily `rclone sync` to `B1`/`B2a`/`B2b` |
| **`LocalFsBackend` / `InMemoryStorage`** | `file://` / `memory://` | Offline unit and integration tests (`pytest -m "not integration"`) | Ephemeral test workspace |

---

## 3. PostgreSQL 18 Large-Blob (`20 GB+`) CAS Schema & Tablespace Isolation

### 3.1 Five Database Protections for Multi-Gigabyte Blobs

Naively storing a `20 GB` video in a single PostgreSQL column fails due to PostgreSQL's `1 GB` `varlena` field limit (`MaxAllocSize = 0x3fffffff`), `shared_buffers` eviction of hot `pgvector` HNSW indexes, and `2x–3x` WAL write amplification. Engramory's approved PostgreSQL 18 CAS (`05-database-stack.md` §6.14) enforces five database-level protections:

1. **Fixed `4 MiB` (`4,194,304` B) Chunking:** Splits arbitrarily large blobs (`20 GB` = `5,120` chunks) well below the `1 GB` `varlena` limit and bounds peak Python worker memory to `<= 16 MiB`.
2. **Physical Tablespace Isolation (`TABLESPACE ts_blob_cold`):** Places chunk tables on a dedicated mount (`/var/lib/postgresql/blob_ts`) separate from `pg_default` (`/var/lib/postgresql/data/pgdata`).
3. **Zero-Recompression TOAST (`STORAGE EXTERNAL`):** Disables `pglz`/`lz4` CPU recompression on pre-compressed `MP4`/`H.264`/`H.265`/`JPEG`/`PNG`/`PDF` bytes and sets `fillfactor = 100`.
4. **Tiered WAL Logging (`LOGGED` vs. `UNLOGGED`):** Routes blobs `<= 50 MiB` to `LOGGED` `kb_blob_chunks` and blobs `> 50 MiB` (up to `20 GB+`) to `UNLOGGED` `kb_blob_chunks_large` (`0 B` WAL generation during bulk video ingest).
5. **Ring-Buffer Bulk I/O & TOAST Clock-Sweep Hygiene:** Uses `COPY BINARY` (`16 MiB` `BAS_BULKWRITE` ring buffer) on write and `4`-chunk (`16 MiB`) keyset pagination on read (TOAST pages enter `shared_buffers` with `usage_count = 1`, never evicting HNSW pages at `usage_count = 5`).

### 3.2 Canonical DDL (`kb_blobs`, `kb_blob_chunks`, `kb_blob_chunks_large`)

```sql
-- Dedicated cold tablespace mounted at /var/lib/postgresql/blob_ts
-- (created by deployment bootstrap outside transaction blocks):
-- CREATE TABLESPACE ts_blob_cold LOCATION '/var/lib/postgresql/blob_ts';
-- ALTER TABLESPACE ts_blob_cold SET (
--     seq_page_cost = 1.0, random_page_cost = 1.1,
--     effective_io_concurrency = 256, maintenance_io_concurrency = 256
-- );

CREATE TABLE IF NOT EXISTS kb_blobs (
    tenant_id        text        NOT NULL,
    sha256           text        NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'),
    project_id       text,
    media_type       text        NOT NULL,
    byte_size        bigint      NOT NULL CHECK (byte_size >= 0),
    chunk_size       integer     NOT NULL DEFAULT 4194304 CHECK (chunk_size BETWEEN 65536 AND 16777216),
    chunk_count      integer     NOT NULL CHECK (chunk_count >= 0),
    storage_tier     text        NOT NULL DEFAULT 'logged' CHECK (storage_tier IN ('logged', 'unlogged')),
    status           text        NOT NULL DEFAULT 'uploading' CHECK (status IN ('uploading', 'ready', 'failed')),
    metadata         jsonb       NOT NULL DEFAULT '{}'::jsonb,
    created_at       timestamptz NOT NULL DEFAULT now(),
    updated_at       timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, sha256)
) TABLESPACE pg_default;

CREATE INDEX IF NOT EXISTS kb_blobs_tenant_project_status_idx
    ON kb_blobs (tenant_id, project_id, status, created_at DESC);

CREATE OR REPLACE FUNCTION set_kb_blobs_updated_at()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    NEW.updated_at := now();
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_kb_blobs_updated_at ON kb_blobs;
CREATE TRIGGER trg_kb_blobs_updated_at
    BEFORE UPDATE ON kb_blobs
    FOR EACH ROW EXECUTE FUNCTION set_kb_blobs_updated_at();

CREATE TABLE IF NOT EXISTS kb_blob_chunks (
    tenant_id      text        NOT NULL,
    blob_sha256    text        NOT NULL CHECK (blob_sha256 ~ '^[0-9a-f]{64}$'),
    chunk_index    integer     NOT NULL CHECK (chunk_index >= 0),
    chunk_sha256   bytea       NOT NULL CHECK (octet_length(chunk_sha256) = 32),
    byte_size      integer     NOT NULL CHECK (byte_size > 0 AND byte_size <= 16777216),
    data           bytea       NOT NULL,
    PRIMARY KEY (tenant_id, blob_sha256, chunk_index)
) WITH (
    fillfactor = 100,
    toast.autovacuum_enabled = true,
    autovacuum_vacuum_scale_factor = 0.01,
    toast.autovacuum_vacuum_scale_factor = 0.01
);

ALTER TABLE kb_blob_chunks ALTER COLUMN data SET STORAGE EXTERNAL;
-- When ts_blob_cold exists:
-- ALTER TABLE kb_blob_chunks SET TABLESPACE ts_blob_cold;
-- ALTER INDEX kb_blob_chunks_pkey SET TABLESPACE ts_blob_cold;

CREATE UNLOGGED TABLE IF NOT EXISTS kb_blob_chunks_large (
    tenant_id      text        NOT NULL,
    blob_sha256    text        NOT NULL CHECK (blob_sha256 ~ '^[0-9a-f]{64}$'),
    chunk_index    integer     NOT NULL CHECK (chunk_index >= 0),
    chunk_sha256   bytea       NOT NULL CHECK (octet_length(chunk_sha256) = 32),
    byte_size      integer     NOT NULL CHECK (byte_size > 0 AND byte_size <= 16777216),
    data           bytea       NOT NULL,
    PRIMARY KEY (tenant_id, blob_sha256, chunk_index)
) WITH (
    fillfactor = 100,
    toast.autovacuum_enabled = true,
    autovacuum_vacuum_scale_factor = 0.01,
    toast.autovacuum_vacuum_scale_factor = 0.01
);

ALTER TABLE kb_blob_chunks_large ALTER COLUMN data SET STORAGE EXTERNAL;
-- When ts_blob_cold exists:
-- ALTER TABLE kb_blob_chunks_large SET TABLESPACE ts_blob_cold;
-- ALTER INDEX kb_blob_chunks_large_pkey SET TABLESPACE ts_blob_cold;
```

Both chunk tables intentionally omit foreign keys back to `kb_blobs`: PostgreSQL prohibits foreign keys between `LOGGED` and `UNLOGGED` tables (`SQLSTATE 42P16`) and per-chunk FK checks would acquire `5,120` `FOR KEY SHARE` locks during a `20 GB` upload. Referential cleanup is enforced by `PgBlobStorageAdapter.delete()` and scheduled orphan garbage collection.

---

## 4. Streaming Protocol, `UNLOGGED` Crash Recovery & `postgresql.conf` Tuning

### 4.1 Direct `:5432` Streaming Pool & `COPY BINARY` Protocol

`PgBlobStorageAdapter` connects via a dedicated, bounded direct PostgreSQL pool (`127.0.0.1:5432` on dev / `10.250.0.1:5432` on stage, `max_size = 4`, `autocommit = True`), bypassing `PgBouncer` transaction-mode pooling (`127.0.0.1:6432`) so multi-minute `COPY BINARY` streams never hold transactional pool slots:

```python
# Write path (PgBlobStorageAdapter._stream_chunks_into_pg):
# 1. UPSERT kb_blobs with status='uploading', storage_tier=('unlogged' if size > 52428800 else 'logged')
# 2. Stream 4 MiB chunks via psycopg v3 COPY BINARY (activates 16 MiB BAS_BULKWRITE ring buffer):
async with conn.cursor() as cur:
    async with cur.copy(
        f"COPY {target_table} (tenant_id, blob_sha256, chunk_index, chunk_sha256, byte_size, data) "
        "FROM STDIN WITH (FORMAT BINARY)"
    ) as copy:
        copy.set_types(["text", "text", "int4", "bytea", "int4", "bytea"])
        for idx, chunk_bytes, chunk_digest in chunk_iterator:
            await copy.write_row((tenant_id, blob_sha256, idx, chunk_digest, len(chunk_bytes), chunk_bytes))
# 3. Verify COUNT(*) == chunk_count, flip kb_blobs.status = 'ready', and issue CHECKPOINT if unlogged.
```

For streaming reads (`get_stream`), `PgBlobStorageAdapter` first verifies `SELECT COUNT(*) FROM <chunk_table> WHERE tenant_id = $1 AND blob_sha256 = $2` equals `kb_blobs.chunk_count`, then fetches `4` chunks (`16 MiB`) per round-trip via keyset pagination (`WHERE tenant_id = $1 AND blob_sha256 = $2 AND chunk_index >= $3 ORDER BY chunk_index ASC LIMIT 4`).

### 4.2 `UNLOGGED` Crash-Truncation Recovery & Backup Contract

In PostgreSQL 18, `CHECKPOINT` flushes dirty `UNLOGGED` pages to `ts_blob_cold` for clean container restarts (`docker stop` with `stop_grace_period: 30s`). However, an unclean OS crash (`SIGKILL`, OOM, power loss) causes PostgreSQL recovery to truncate all `UNLOGGED` tables (`kb_blob_chunks_large`) to zero rows via their `_init` fork while `LOGGED` `kb_blobs` retains `status = 'ready'`. Three controls prevent silent data loss:

1. **Daily Non-Recompressing Logical Dump (`28-backup-and-disaster-recovery-strategy.md`):**

   ```bash
   pg_dump -h 127.0.0.1 -p 5432 -U engramory -d engramory \
     --format=custom -Z 0 -t public.kb_blob_chunks_large \
     | zstd -1 -T0 | age -r "$AGE_RECIPIENT" \
     > /home/platform/backups/postgres/engramory-blob-chunks-large-$STAMP.dump.zst.age
   ```

2. **Post-Crash Reconciliation:** Restore `kb_blob_chunks_large` via `pg_restore` and reconcile any un-backed-up `storage_tier = 'unlogged'` rows whose chunk count is incomplete to `status = 'failed'`.
3. **Read-Time Completeness Guard:** `PgBlobStorageAdapter.get_stream()` raises a typed `BlobTruncatedError` (`StorageError` subclass) if `COUNT(chunk_index) != kb_blobs.chunk_count`.

### 4.3 Recommended `postgresql.conf` Parameters for `stage-postgres18`

```ini
io_method = 'io_uring'                   # PG 18 async I/O (fallback: 'worker')
io_combine_limit = '128kB'               # Coalesce adjacent 8 KiB TOAST page reads/writes
effective_io_concurrency = 256           # NVMe queue depth for parallel read-ahead
maintenance_io_concurrency = 256
shared_buffers = '8GB'                   # Dedicated to pg_default OLTP + pgvector HNSW
wal_compression = 'zstd'                 # Compress WAL for LOGGED <= 50 MiB chunks
max_wal_size = '16GB'
checkpoint_timeout = '15min'
checkpoint_completion_target = 0.9
```

---

## 5. Approved Dual-Engine Graph Standard: PostgreSQL 18 + Neo4j (`Decision D17`)

### 5.1 When to Use PostgreSQL 18 vs. Neo4j Behind `GraphPort`

Per Platform Technology Stack `05-database-stack.md` (§6.13, `Decision D17`), `23-federated-graph-lake-embedded-kuzu-and-central-graphdb.md`, and `25-approved-tooling-inventory.md`, **Neo4j (`neo4j:5.26-community` / `stage-neo4j` on Stage and Neo4j Aura / CE in Production, Python driver `neo4j>=5.26.0`, Ring: `Adopt`)** is approved as a first-class graph engine alongside PostgreSQL 18:

- **PostgreSQL 18 (`pg_default`) — System of Record & Shallow (`1–2` Hop) Joins:** Owns canonical entities, citations (`kb_documents`, `kb_sections`, `memories`), and transactional outbox state.
- **Neo4j (`Neo4jAdapter` in `src/engramory/ports/graph.py`, Bolt `127.0.0.1:7687` / `10.250.0.1:7687`) — Multi-Hop Property Graph & GraphRAG:** Used as needed for variable-length Cypher traversals (`>= 2–3` hops), cross-document entity-timeline reasoning, SDD requirement lineage (`trace_to`), blast-radius analysis, and hybrid vector + graph retrieval.
- **Embedded Kùzu (`aidoc-ctx`) — Zero-Daemon Local/CI Edge Slices:** Used in local CLI/CI gates with `0 MB` idle RAM.

### 5.2 Rebuildable Projection Invariant (`ADR-05`)

Neo4j always acts as a **deterministic, rebuildable projection** of canonical PostgreSQL 18 rows (`kb_documents`, `kb_sections`, `memories`). Every Neo4j node carries `(tenant_id, project_id, id)` uniqueness constraints and `source_sha256` provenance so the entire property graph can be re-projected from PostgreSQL 18 at any time without data loss.

---

## 6. Future Engramory Implementation Roadmap (Deferred to Implementation Session)

This specification defines the approved target architecture. Implementation in `platform-engramory` will proceed in a dedicated coding session across five work packages:

1. **Migration `db/migrations/0010_multimodal_blobs.sql`:** Provision `kb_blobs`, `kb_blob_chunks` (`LOGGED`), `kb_blob_chunks_large` (`UNLOGGED`), and optional `kb_media_segments` (`blob_sha256`, `modality`, `start_ms`, `end_ms`, `section_id`).
2. **`StoragePort` Streaming Extension & `PgBlobStorageAdapter` (`src/engramory/ports/storage.py`):** Add `put_stream()` / `get_stream()` protocols, `COPY BINARY` chunked ingestion, `4`-chunk keyset streaming, `BlobTruncatedError` completeness verification, and orphan chunk GC.
3. **Multimodal `LLMPort` & Ingestion Router (`src/engramory/ports/llm.py`, `src/engramory/core/parsers/detector.py`, `src/engramory/core/extraction/router.py`):** Extend `detector.py` and `router.py` for image (`PNG`, `JPEG`, `WebP`), audio (`MP3`, `WAV`), and video (`MP4`, `MOV`, `WebM`) extraction into `kb_sections` + `kb_blobs`.
4. **Neo4j Multimodal Lineage Projection (`src/engramory/ports/graph.py`):** Project `(:BlobNode)<-[:DERIVED_FROM]-(:SectionNode)-[:MENTIONS]->(:EntityNode)` into Neo4j via `Neo4jAdapter`.
5. **Verification Suite (`tests/`):** Unit, property, and integration tests covering `4 MiB` chunk boundaries, `UNLOGGED` crash-truncation detection, and Neo4j projection parity.
