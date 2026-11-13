# PLAN-023: Implement Multimodal RAG, GraphRAG (`PostgreSQL 18 + Neo4j`), Knowledge Hybrid RRF, and `20 GB+` PostgreSQL Chunked Blob CAS in `platform-engramory`

> **Status:** Completed (All 7 Phases Implemented & Verified via `task check` and `task ci`)  
> **Date:** 2026-11-12  
> **Owning Repository:** `platform-engramory`  
> **Target Branch:** `feature/plan-023-multimodal-rag-graphrag-roadmap` -> `dev`  
> **Governance Standard:** `aiops-harness` v0.2.0 (`verified-planning`, `SDD2C` — SDD-First & Documentation-First)  
> **Upstream Normative Specifications:** `platform-technology_stack/05-database-stack.md` (§6.13 `Decision D17`, §6.14 `Rule D14a`/`D14b`), `docs/MULTIMODAL_AND_POSTGRES_BLOB_STORAGE.md`, `ADR-01`, `ADR-03`, `ADR-05`, `SPEC-01`, `SPEC-02`, `SPEC-03`, `SPEC-06`, `SPEC-07`

---

## 1. Executive Summary & Architectural Decisions (Scored 1–5)

Following the approval of **PostgreSQL 18 Chunked Blob CAS (`Rule D14a`/`D14b`, up to `20 GB+`)** and **Neo4j (`Adopt` alongside PostgreSQL 18, `Decision D17`)** in `PLAN-022`, this plan defines the end-to-end engineering implementation for **Multimodal Knowledge Ingestion (Text, Documents, Images, Audio, `20 GB+` Video)**, **3-Leg Hybrid Knowledge RAG (Dense `pgvector` HNSW + Lexical `ts_lex` BM25 + Reciprocal Rank Fusion)**, and **Dual-Engine GraphRAG (`PostgreSQL 18` canonical graph + `Neo4j` multi-hop Cypher projection)** in `platform-engramory`.

### 1.1 Scored Architectural Decisions (`3 Options + 1–5 Scoring`)

#### Decision Q1: Multimodal Indexing & Vector Representation Strategy

| Option | Approach | Score (1–5) | Rationale & Trade-offs |
| :--- | :--- | :---: | :--- |
| **Option A** | Separate secondary `vector(512)`/`vector(1024)` CLIP/SigLIP column on `kb_sections` | `2/5` | Doubles HNSW memory footprint in `shared_buffers`, requires dual-space query routing, and degrades cross-modal rank calibration against text-heavy SDD/runbook queries. |
| **Option B** | Inline raw base64 media payloads inside `kb_sections.text` | `1/5` | Violates `Rule D14a` (`256 KiB` hot-row limit), pollutes `ts_lex` GIN indexes with base64 tokens, and causes TOAST buffer thrashing. |
| **Option C (Selected)** | **Derive-to-Text + Time/Region-Coded `kb_sections` + Content-Addressable `pgblob://sha256/<hex>` Pointer in `kb_blobs`** | **`5/5`** | Vision-LLM captioning + OCR (`Docling` / `LiteLLMAdapter.describe_image`) and ASR transcript + keyframe scene extraction (`VideoAudioExtractor`) convert images, audio, and `20 GB+` video into rich, time/region-anchored Markdown sections embedded into the unified `vector(1536)` + `ts_lex` GIN space (`kb_sections`), while linking each section to its immutable raw binary in `kb_blobs` (`PgBlobStorageAdapter`). |

#### Decision Q2: Knowledge Retrieval & GraphRAG Ranking Architecture (`PostgreSQL 18 + Neo4j`)

| Option | Approach | Score (1–5) | Rationale & Trade-offs |
| :--- | :--- | :---: | :--- |
| **Option A** | Vector-only when `query_vec` is present, `ILIKE` fallback only when `query_vec` is `None` (current `search_kb_sections` in `repository.py:744-875`) | `2/5` | Misses exact identifier, CLI flag, and error-code matches whenever embeddings are enabled, and ignores multi-hop document/entity/media graph relationships. |
| **Option B** | Standalone Neo4j-only vector + graph search bypassing PostgreSQL | `3/5` | Weakens `ADR-01`/`ADR-05` single-spine transactional governance and duplicates the `ADR-07` 4-level scope visibility wall (`agent -> project -> domain -> space`). |
| **Option C (Selected)** | **3-Leg Hybrid RRF (`pgvector` HNSW + `ts_lex` GIN BM25 in PostgreSQL 18 + `GraphPort` Multi-Hop GraphRAG Expansion via Neo4j with PG CTE Fallback)** | **`5/5`** | Adds `ts_lex tsvector GENERATED ALWAYS AS (...) STORED` + GIN index to `kb_sections`, fuses top-$N$ dense cosine similarity and top-$N$ lexical `ts_rank_cd` hits via SQL Reciprocal Rank Fusion ($\text{RRF}(d) = \frac{1}{60 + r_{\text{vec}}} + \frac{1}{60 + r_{\text{lex}}}$), and when `expand_graph=True`, expands top seed sections/entities $1\text{–}3$ hops via `Neo4jGraphAdapter` (with automatic fallback to PostgreSQL recursive CTEs `find_neighbors_cte` when Neo4j is unreachable). |

#### Decision Q3: Large-Video (`20 GB+`) Ingestion & Streaming I/O Model

| Option | Approach | Score (1–5) | Rationale & Trade-offs |
| :--- | :--- | :---: | :--- |
| **Option A** | In-memory `file_path.read_bytes()` before storage and extraction (current pattern in `docling.py:74` and `router.py:96`) | `1/5` | Immediately triggers Python OOM kills on multi-gigabyte video files (`20 GB+`). |
| **Option B** | Single-connection SQL `INSERT INTO kb_blob_chunks` over `PgBouncer :6432` | `2/5` | Starves `PgBouncer` transaction pool slots, fails on `CHECKPOINT` in transaction mode, and generates `20 GB+` of WAL. |
| **Option C (Selected)** | **Streaming `4 MiB` Chunked `COPY BINARY` (`PgBlobStorageAdapter.put_stream`) over Bounded Direct `:5432` Pool + Bounded `ffmpeg`/`ffprobe` Subprocess Extraction** | **`5/5`** | Streams files in fixed `4 MiB` (`4,194,304` B) chunks (`<= 16 MiB` peak Python RAM), routes `<= 50 MiB` blobs to `LOGGED` `kb_blob_chunks` and `> 50 MiB` (up to `25 GiB`) to `UNLOGGED` `kb_blob_chunks_large` in `TABLESPACE ts_blob_cold` with `STORAGE EXTERNAL` via `COPY ... FROM STDIN WITH (FORMAT BINARY)` (`16 MiB` `BAS_BULKWRITE` ring buffer), and runs `ffprobe`/`ffmpeg` directly against the file stream/path with strict subprocess timeouts. |

---

## 2. Current Gaps Identified in `platform-engramory`

1. **Missing `StoragePort` Streaming & Concrete Adapters (`src/engramory/ports/storage.py:11-26`, `src/engramory/adapters/factory.py:22-45`):**
   - `StoragePort` only declares in-memory `put`/`get`/`delete`/`exists` (`bytes`) and has **zero** concrete adapter implementations in `src/engramory/adapters/dev/`.
   - `AdapterSet` (`factory.py:22-31`) only wires `vector`, `llm`, `graph`, and `events` — `storage` is absent.
2. **Unbounded `read_bytes()` & Binary Fallback Bug in Extraction Pipeline (`src/engramory/core/extraction/router.py:93-98`, `docling.py:74`):**
   - `SmartExtractionRouter.extract_file()` (`router.py:93-98`) catches `UnicodeDecodeError` and passes `file_path.read_bytes()` directly to `DocumentDetector.parse()`, which falls back to `TextParser` (`detector.py:109`) and decodes arbitrary binary bytes as UTF-8 (`errors="replace"`), indexing binary replacement-character noise into `kb_sections` whenever Docling is disabled/unavailable or when a `.mp4`/`.mov`/`.mp3`/`.png` file is passed.
   - `DoclingExtractor.extract()` (`docling.py:74`) calls `file_bytes = file_path.read_bytes()` into memory before checking size limits, and sets `images=[]` (`docling.py:111`).
3. **Missing Multimodal `LLMPort` Operations (`src/engramory/ports/llm.py:13-28`, `src/engramory/adapters/dev/llm_litellm.py:80-127`):**
   - `LLMPort` only supports text-only `chat()` and `embed()`. It lacks `describe_image()` (OpenAI-compatible vision content array `[{"type": "text", ...}, {"type": "image_url", ...}]` via LiteLLM `/v1/chat/completions`) and `transcribe_audio()` (OpenAI-compatible `/v1/audio/transcriptions` via LiteLLM).
4. **Missing Hybrid Lexical + Vector RRF & Modality Columns on `kb_sections` (`db/migrations/0003_reconcile_contracts.sql:90-113`, `src/engramory/core/repository.py:744-875`):**
   - Unlike `memories` (which has `ts_lex tsvector GENERATED ALWAYS AS (...) STORED` at `0003:44-48`), `kb_sections` has no `ts_lex` column, no GIN index, and no `modality`, `blob_sha256`, `blob_uri`, or `media_metadata` columns.
   - `Repository.search_kb_sections()` (`repository.py:744-875`) executes vector search *or* `ILIKE` substring fallback, never combining vector similarity + lexical BM25 rank fusion.
5. **Incomplete Knowledge Graph & GraphRAG Coverage (`src/engramory/core/kb_graph.py:600-719`, `src/engramory/access/surface.py:682-713`):**
   - `kb_graph.py` extracts SDD YAML (`01_BRD..10_EVAL`) and Markdown runbooks (`RUNBOOK`, `RUNBOOK_STEP`, `ALERT`), and at `kb_graph.py:667` unconditionally labels all projected nodes with `:SDD` even when they come from `extract_docs_graph()`.
   - General `kb_documents`, `kb_sections`, and multimodal `kb_blobs` (`DOCUMENT`, `SECTION`, `MEDIA_BLOB`, `CONCEPT`) are not yet extracted into `graph_entities` / `graph_relations` (`CONTAINS_SECTION`, `DERIVED_FROM_BLOB`, `MENTIONS_ENTITY`, `CITES_DOC`, `NEXT_SEGMENT`) or expanded during `AccessSurface.knowledge_search()`.

### 2.1 Deep Codebase Gap Audit (`GAP-01` through `GAP-09`)

A line-by-line audit of `platform-engramory` against the initial plan design surfaced **9 additional implementation gaps** that must be closed in Phases 1–7:

| Gap ID | Severity | Location | Gap Description | Required Fix in `PLAN-023` |
| :--- | :---: | :--- | :--- | :--- |
| **`GAP-01`** | **Critical** | `src/engramory/core/repository.py:693-743`, `771-826` | `_KB_SECTION_COLUMNS` is defined at line 73, **but `get_kb_section`, `list_kb_sections`, and `search_kb_sections` hardcode `SELECT id, tenant_id, project_id, domain_id, scope, doc_id, citation, text, version`** instead of interpolating `{_KB_SECTION_COLUMNS}`. Updating only `_KB_SECTION_COLUMNS` would cause every read/search to drop `modality`, `blob_sha256`, `blob_uri`, and `media_metadata`. | Replace hardcoded column lists in `get_kb_section` (`L699-700`), `list_kb_sections` (`L730-731`), and `search_kb_sections` (`L771-772`, `L825-826`) with `f"SELECT {_KB_SECTION_COLUMNS} FROM kb_sections ..."`. |
| **`GAP-02`** | **Critical** | `src/engramory/mcp/server.py:563-576`, `src/engramory/cli.py:348-460` | 1) `McpServer.handle_call_tool("knowledge_search")` builds `formatted_secs` with only 8 fields, stripping `modality`, `blob_sha256`, `blob_uri`, and `media_metadata` (`graph_context`). 2) `cli._cmd_knowledge_ingest`, `_cmd_knowledge_sync_git`, and `_cmd_knowledge_search` construct `KnowledgeCore` / `GitSyncEngine` without `storage` or `graph` and strip multimodal/graph fields from CLI output. | Include `modality`, `blob_sha256`, `blob_uri`, and `media_metadata` in MCP `formatted_secs` (when non-default or always as additive keys) and wire `storage=adapters.storage, graph=adapters.graph` across `_cmd_knowledge_ingest`, `_cmd_knowledge_sync_git`, and `_cmd_knowledge_search`. |
| **`GAP-03`** | **Critical** | `src/engramory/adapters/factory.py:33-45` | In staging/production, `ENGRAMORY_DSN` points to `PgBouncer :6432` (transaction pooling), whereas `PgBlobStorageAdapter` (`COPY BINARY` up to `20 GB+` + `autocommit=True` `CHECKPOINT`) **must** bypass `PgBouncer` and connect directly to PostgreSQL `:5432` (`05-database-stack.md` §6.14). | Resolve `blob_dsn = os.getenv("ENGRAMORY_BLOB_DSN") or dsn` in `dev_adapters()` so `PgBlobStorageAdapter` uses a dedicated direct `:5432` DSN in stage/prod while defaulting seamlessly to `dsn` in local dev. |
| **`GAP-04`** | **Major** | `src/engramory/core/extraction/docling.py:108-115` | `DoclingExtractor.extract()` currently hardcodes `images=[]` (`L111`), discarding architecture diagrams, charts, and figures embedded inside PDF, DOCX, and PPTX documents. | Extract embedded figures (`doc.pictures`) in `DoclingExtractor` (when available), store figure bytes in `kb_blobs` (`StoragePort`), caption via `LLMPort.describe_image`, and emit child `modality="image"` `KBSection`s (`<doc_id>#figure-N`) linked via `DERIVED_FROM_BLOB`. |
| **`GAP-05`** | **Major** | `src/engramory/core/git_sync.py:82-93`, `217-284`, `324-355` | 1) `GitSyncEngine.SUPPORTED_EXTENSIONS` omits `.png`, `.jpg`, `.jpeg`, `.webp`, `.gif`, `.mp4`, `.mov`, `.webm`, `.mkv`, `.mp3`, `.wav`, `.m4a`, `.pptx`, `.xlsx`. 2) `extract_sync_sections` drops `modality`, `blob_sha256`, `blob_uri`, `media_metadata`. 3) File deletion in `sync()` leaves orphan `DOCUMENT`/`SECTION` graph nodes and unreferenced `kb_blobs`. | Extend `SUPPORTED_EXTENSIONS`, forward multimodal fields in `extract_sync_sections()`, and purge stale document/section graph nodes + trigger `gc_orphan_chunks()` on file deletion/modification. |
| **`GAP-06`** | **Major** | `src/engramory/workers/runner.py:39-185`, `workers/graph_projection.py:22-48` | 1) `run_worker_loop()` never invokes `storage.reconcile_truncated_blobs()` (required on startup after an unclean OS crash truncates `UNLOGGED` `kb_blob_chunks_large`) or periodic `storage.gc_orphan_chunks()`. 2) `workers/graph_projection.py` and `core/kb_graph.py` duplicate Neo4j label logic and diverge on `:Knowledge` / `:SDD`. | Pass `storage=adapters.storage` into `start_daemon` / `run_worker_loop` (`cli.py:481`), run `reconcile_truncated_blobs()` on worker startup + `gc_orphan_chunks()` every pass, and share a single `entity_labels_for(entity)` helper between `kb_graph.py` and `workers/graph_projection.py`. |
| **`GAP-07`** | **Major** | `src/engramory/access/surface.py:123-133`, `205-217` | 1) `AccessSurface.__init__` does not yet declare `storage: Optional[StoragePort] = None`. 2) `AccessSurface.memory_search(expand_graph=True)` (`L205-217`) only queries `self._graph.neighbors` and silently drops graph expansion when Neo4j is unreachable instead of falling back to `self._repo.find_neighbors()`. | Add `storage: Optional[StoragePort] = None` to `AccessSurface.__init__` (`self._storage = storage`) and route both `memory_search(expand_graph=True)` and `knowledge_search(expand_graph=True)` through a shared `_expand_neighbors_with_fallback()` helper (`Neo4j` primary -> `Repository.find_neighbors` PG CTE fallback). |
| **`GAP-08`** | **Medium** | `src/engramory/core/repository.py:744-875` | When `query_vec is None` (offline mode or embedding endpoint down), `search_kb_sections` only runs `AND text ILIKE %(pattern)s` and lacks `modality` filtering and `ts_lex` BM25 ranking. | Apply `AND (%(modality)s::text IS NULL OR modality = %(modality)s)` across all branches of `search_kb_sections` and upgrade the `query_vec is None` branch to use `(ts_lex @@ websearch_to_tsquery('english', %(query)s) OR text ILIKE %(pattern)s OR citation ILIKE %(pattern)s)` ordered by `ts_rank_cd(...) DESC, version DESC, id ASC`. |
| **`GAP-09`** | **Medium** | `src/engramory/core/context_tools.py:1-120` | Adding `CHG-008` in Phase 1 without complete upstream trace links (`@ears:`, `@bdd:`, `@adr:`, `@spec:`, `@tdd:`) or without updating `IPLAN` will fail `engramory sdd validate-graph --invariants` (`GRAPH-CARD-001..003`). | Explicitly require `docs/sdd/08_IPLAN/IPLAN-008_multimodal_rag_graphrag.yaml` alongside `CHG-008` in Phase 1 and gate Phase 1 on `uv run engramory sdd validate-graph --invariants` and `uv run engramory sdd check-invariants --type SDD2C`. |

---

## 3. Phased Implementation Plan

### Phase 1: SDD-First Contract Updates (`06_SPEC`, `07_TDD`, `08_IPLAN`, `09_CHG`)

Per the `aiops-harness` **SDD-First Mandate (`SDD2C`)**, update the governing SDD specifications before or atomically with code changes:

1. **`docs/sdd/06_SPEC/SPEC-02_memory_store.yaml` (`L43-135`):**
   - Add `KBBlob` (`tenant_id`, `sha256`, `project_id`, `media_type`, `byte_size`, `chunk_size`, `chunk_count`, `storage_tier`, `status`, `metadata`) and extend `KBSection` with optional multimodal provenance fields (`modality: str = "text"`, `blob_sha256: str | None = None`, `blob_uri: str | None = None`, `media_metadata: Mapping[str, Any] = {}`).
   - Document `kb_sections.ts_lex` (`GENERATED ALWAYS AS (to_tsvector('english', coalesce(citation, '') || ' ' || text)) STORED`) and `search_kb_sections` hybrid RRF ranking.
2. **`docs/sdd/06_SPEC/SPEC-03_retrieval_graph.yaml` (`L43-102`):**
   - Add `knowledge_search_graphrag` / `KnowledgeHit` contract combining 3-leg RRF (`pgvector` cosine distance + `ts_lex` BM25 + `GraphPort` Neo4j / PG CTE neighborhood expansion across `DOCUMENT`, `SECTION`, `MEDIA_BLOB`, `CONCEPT`, and `SDD` nodes).
3. **`docs/sdd/06_SPEC/SPEC-06_ports_portability.yaml` (`L43-80`):**
   - Update `StoragePort` (`put`, `get`, `delete`, `exists`, `put_stream`, `get_stream`, `head`) to specify the 3 approved `Rule D14b` adapters: `PgBlobStorageAdapter` (PostgreSQL 18 Chunked Blob CAS in `ts_blob_cold` up to `20 GB+`), `LocalFsStorageAdapter` (`file://` / in-memory for offline tests), and cloud S3/GCS/Azure adapters.
   - Update `LLMPort` to include `describe_image` and `transcribe_audio`.
4. **`docs/sdd/06_SPEC/SPEC-01_access_surface.yaml` & `SPEC-07_cli_face.yaml`:**
   - Register `blob_put`, `blob_get`, `blob_head`, and `knowledge_ingest_media` actions and CLI subcommands under `engramory knowledge` (with `kb` registered as a parser alias in `cli.py:962`: `engramory knowledge ingest-media`, `engramory knowledge blob-get`, `engramory knowledge search --expand-graph --modality`).
5. **`docs/sdd/07_TDD/TDD-02_memory_store.yaml`, `TDD-03_retrieval_graph.yaml`, `TDD-06_ports_portability.yaml`, `docs/sdd/08_IPLAN/IPLAN-008_multimodal_rag_graphrag.yaml` & `docs/sdd/09_CHG/CHG-008_multimodal_rag_graphrag_and_blob_cas.yaml` (`GAP-09`):**
   - Add verification oracles, `IPLAN-008`, and authoring `CHG-008` with complete upstream `@ears:`, `@bdd:`, `@adr:`, `@spec:`, and `@tdd:` trace pointers, and verify with `uv run engramory sdd validate-graph --invariants` and `uv run engramory sdd check-invariants --type SDD2C`.

---

### Phase 2: Database Migration `0010` & `PgBlobStorageAdapter` (`20 GB+` PostgreSQL Chunked CAS)

1. **Create `db/migrations/0010_multimodal_rag_and_blob_cas.sql` & Update `tests/test_migrations.py` (`L11-24`):**
   - **Conditional Cold Tablespace Check (`ts_blob_cold` vs. `pg_default` Fallback):**
     - Execute a `DO $$` block checking `SELECT 1 FROM pg_tablespace WHERE spcname = 'ts_blob_cold'`. When `ts_blob_cold` exists (provisioned by host/container init script at `/var/lib/postgresql/blob_ts`), move both the chunk tables (`ALTER TABLE kb_blob_chunks SET TABLESPACE ts_blob_cold`, `ALTER TABLE kb_blob_chunks_large SET TABLESPACE ts_blob_cold`) **and** their primary key indexes (`ALTER INDEX kb_blob_chunks_pkey SET TABLESPACE ts_blob_cold`, `ALTER INDEX kb_blob_chunks_large_pkey SET TABLESPACE ts_blob_cold`) to `ts_blob_cold`; otherwise fall back cleanly to `pg_default` so unprivileged CI and managed Cloud SQL / AlloyDB / Flexible Server instances run the exact same migration without `SUPERUSER` errors.
   - **Blob Manifest Table (`kb_blobs` in `pg_default`):**

     ```sql
     CREATE TABLE IF NOT EXISTS kb_blobs (
         tenant_id      TEXT        NOT NULL DEFAULT 'default',
         sha256         TEXT        NOT NULL CHECK (char_length(sha256) = 64),
         project_id     TEXT,
         media_type     TEXT        NOT NULL,
         byte_size      BIGINT      NOT NULL CHECK (byte_size >= 0 AND byte_size <= 26843545600),
         chunk_size     INTEGER     NOT NULL DEFAULT 4194304 CHECK (chunk_size BETWEEN 65536 AND 16777216),
         chunk_count    INTEGER     NOT NULL CHECK (chunk_count >= 0),
         storage_tier   TEXT        NOT NULL DEFAULT 'logged' CHECK (storage_tier IN ('logged', 'unlogged')),
         status         TEXT        NOT NULL DEFAULT 'uploading' CHECK (status IN ('uploading', 'ready', 'failed')),
         metadata       JSONB       NOT NULL DEFAULT '{}'::jsonb,
         created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
         updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
         PRIMARY KEY (tenant_id, sha256)
     );
     CREATE INDEX IF NOT EXISTS idx_kb_blobs_tenant_project_status
         ON kb_blobs (tenant_id, project_id, status, created_at DESC);
     DROP TRIGGER IF EXISTS trg_kb_blobs_updated_at ON kb_blobs;
     CREATE TRIGGER trg_kb_blobs_updated_at
         BEFORE UPDATE ON kb_blobs
         FOR EACH ROW EXECUTE FUNCTION set_updated_at();
     ```

   - **Tiered `4 MiB` Chunk Tables (`kb_blob_chunks` `LOGGED` for `<= 50 MiB`; `kb_blob_chunks_large` `UNLOGGED` for `> 50 MiB` to `20 GB+`):**

     ```sql
     CREATE TABLE IF NOT EXISTS kb_blob_chunks (
         tenant_id      TEXT        NOT NULL DEFAULT 'default',
         blob_sha256    TEXT        NOT NULL CHECK (char_length(blob_sha256) = 64),
         chunk_index    INTEGER     NOT NULL CHECK (chunk_index >= 0),
         chunk_sha256   BYTEA       NOT NULL CHECK (octet_length(chunk_sha256) = 32),
         byte_size      INTEGER     NOT NULL CHECK (byte_size > 0 AND byte_size <= 16777216),
         data           BYTEA       NOT NULL,
         PRIMARY KEY (tenant_id, blob_sha256, chunk_index)
     ) WITH (
         fillfactor = 100,
         toast.autovacuum_enabled = true,
         toast.autovacuum_vacuum_scale_factor = 0.01,
         autovacuum_vacuum_scale_factor = 0.01
     );
     ALTER TABLE kb_blob_chunks ALTER COLUMN data SET STORAGE EXTERNAL;

     CREATE UNLOGGED TABLE IF NOT EXISTS kb_blob_chunks_large (
         tenant_id      TEXT        NOT NULL DEFAULT 'default',
         blob_sha256    TEXT        NOT NULL CHECK (char_length(blob_sha256) = 64),
         chunk_index    INTEGER     NOT NULL CHECK (chunk_index >= 0),
         chunk_sha256   BYTEA       NOT NULL CHECK (octet_length(chunk_sha256) = 32),
         byte_size      INTEGER     NOT NULL CHECK (byte_size > 0 AND byte_size <= 16777216),
         data           BYTEA       NOT NULL,
         PRIMARY KEY (tenant_id, blob_sha256, chunk_index)
     ) WITH (
         fillfactor = 100,
         toast.autovacuum_enabled = true,
         toast.autovacuum_vacuum_scale_factor = 0.01,
         autovacuum_vacuum_scale_factor = 0.01
     );
     ALTER TABLE kb_blob_chunks_large ALTER COLUMN data SET STORAGE EXTERNAL;
     ```

   - **Extend `kb_sections` for Multimodal Provenance & Hybrid Lexical BM25 (`ts_lex`):**

     ```sql
     ALTER TABLE kb_sections
         ADD COLUMN IF NOT EXISTS modality TEXT NOT NULL DEFAULT 'text'
             CHECK (modality IN ('text', 'markdown', 'pdf', 'document', 'image', 'audio', 'video')),
         ADD COLUMN IF NOT EXISTS blob_sha256 TEXT,
         ADD COLUMN IF NOT EXISTS blob_uri TEXT,
         ADD COLUMN IF NOT EXISTS media_metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
         ADD COLUMN IF NOT EXISTS ts_lex TSVECTOR GENERATED ALWAYS AS (
             to_tsvector('english', coalesce(citation, '') || ' ' || text)
         ) STORED;

     CREATE INDEX IF NOT EXISTS idx_kb_sections_lex
         ON kb_sections USING gin (ts_lex);
     CREATE INDEX IF NOT EXISTS idx_kb_sections_modality
         ON kb_sections (tenant_id, project_id, modality);
     CREATE INDEX IF NOT EXISTS idx_kb_sections_blob
         ON kb_sections (tenant_id, blob_sha256)
         WHERE blob_sha256 IS NOT NULL;
     ```

   - **Update `tests/test_migrations.py` (`L11-24`):**
     - Append `"0010_multimodal_rag_and_blob_cas.sql"` to `test_migrations_exist_and_ordered()` so the hardcoded migration list gate passes, and add a static assertion verifying `0010` defines `kb_blobs`, `kb_blob_chunks`, `kb_blob_chunks_large`, `STORAGE EXTERNAL`, and `ts_lex`.
2. **Extend `src/engramory/ports/storage.py` (`L11-26`), `src/engramory/core/models.py` (`L140-160`), & `src/engramory/core/repository.py` (`L73`, `L501-646`, `L1857-1868`):**
   - **Stdlib-Only Port Boundary Compliance (`tests/unit/test_no_vendor_imports.py:65-69`):**
     - Because `test_ports_are_stdlib_only()` enforces that `src/engramory/ports/*.py` imports **only** Python standard-library modules (`_stdlib_top_levels() | {"__future__"}` — importing `engramory.core.models` in `ports/storage.py` is forbidden), define `KBBlob` (`@dataclass(frozen=True, slots=True)`) and the typed storage exceptions (`StorageError`, `BlobNotFoundError`, `BlobTruncatedError`, `BlobIntegrityError`, `BlobTooLargeError`) **directly inside `src/engramory/ports/storage.py`** using stdlib types (`collections.abc.AsyncIterator`, `Iterable`, `Mapping`, `dataclasses`, `pathlib.Path`, `typing.Any`, `Protocol`), and re-export `KBBlob` from `src/engramory/core/models.py`.
   - **Frozen `KBSection` Multimodal Fields (`src/engramory/core/models.py:140-160`):**
     - Add `modality: str = "text"`, `blob_sha256: str | None = None`, `blob_uri: str | None = None`, and `media_metadata: Mapping[str, Any] = field(default_factory=dict)` to `KBSection`.
     - In `KBSection.__post_init__()`, validate `modality in {"text", "markdown", "pdf", "document", "image", "audio", "video"}` and wrap `media_metadata` via `object.__setattr__(self, "media_metadata", _frozen_mapping(self.media_metadata))` (matching `Memory.provenance` at `models.py:121-125`).
   - **Full `Repository` & `AccessSurface` Column Round-Trip (`src/engramory/core/repository.py` & `src/engramory/access/surface.py`, `GAP-01` & `GAP-07`):**
     - Update `_KB_SECTION_COLUMNS` (`repository.py:73`) to `"id, tenant_id, project_id, domain_id, scope, doc_id, citation, text, version, modality, blob_sha256, blob_uri, media_metadata"`.
     - Replace the hardcoded `SELECT id, tenant_id, project_id, domain_id, scope, doc_id, citation, text, version` clauses in `Repository.get_kb_section()` (`repository.py:699-700`), `Repository.list_kb_sections()` (`repository.py:730-731`), and `Repository.search_kb_sections()` (`repository.py:771-772`, `825-826`) with `f"SELECT {_KB_SECTION_COLUMNS} FROM kb_sections ..."` so every read and search query hydrates all 4 multimodal columns (`GAP-01`).
     - Update `Repository.upsert_kb_section()` (`repository.py:501-540`) and `Repository.upsert_kb_sections_batch()` (`repository.py:567-646`) (`UNNEST` with `%(modalities)s::text[]`, `%(blob_sha256s)s::text[]`, `%(blob_uris)s::text[]`, `%(media_metadatas)s::jsonb[]` and `ON CONFLICT DO UPDATE SET modality = EXCLUDED.modality, blob_sha256 = EXCLUDED.blob_sha256, blob_uri = EXCLUDED.blob_uri, media_metadata = EXCLUDED.media_metadata`).
     - Update `_hydrate_kb_section(row)` (`repository.py:1857-1868`) using backward-compatible `.get()` lookups (`modality=row.get("modality") or "text"`, `blob_sha256=row.get("blob_sha256")`, `blob_uri=row.get("blob_uri")`, `media_metadata=row.get("media_metadata") or {}`) so existing unit tests with stub cursors remain green.
     - Update `AccessSurface.__init__()` (`surface.py:123-133`) to accept `storage: StoragePort | None = None` (`self._storage = storage`, `GAP-07`), and update `AccessSurface.knowledge_ingest()` (`surface.py:559-567`) and `AccessSurface.knowledge_ingest_batch()` (`surface.py:628-638`) to forward `modality`, `blob_sha256`, `blob_uri`, and `media_metadata` and run `inspect_memory_write()` over both `sec.text` and `json.dumps(dict(sec.media_metadata), sort_keys=True)`.
   - **Streaming Protocol Methods on `StoragePort` (`src/engramory/ports/storage.py`):**
     - `put_file(*, file_path: Path, content_type: str, tenant_id: str = "default", project_id: str | None = None, metadata: Mapping[str, Any] | None = None) -> KBBlob`
     - `put_stream(*, key: str, stream: AsyncIterator[bytes] | Iterable[bytes], content_type: str, tenant_id: str = "default", project_id: str | None = None, byte_size: int | None = None, expected_sha256: str | None = None, metadata: Mapping[str, Any] | None = None) -> KBBlob`
     - `get_stream(*, key: str, tenant_id: str = "default", chunk_batch_size: int = 4) -> AsyncIterator[bytes]`
     - `head(*, key: str, tenant_id: str = "default") -> KBBlob | None`
     - `reconcile_truncated_blobs(*, tenant_id: str | None = None) -> int`
     - `gc_orphan_chunks(*, older_than_seconds: int = 7200) -> int`
3. **Implement `src/engramory/adapters/dev/storage_pg.py` (`PgBlobStorageAdapter`) & `src/engramory/adapters/dev/storage_local.py` (`LocalFsStorageAdapter`):**
   - **`PgBlobStorageAdapter` Deterministic Two-Pass / Pre-Hashed `COPY BINARY` Streaming Protocol:**
     - Because `kb_blob_chunks` and `kb_blob_chunks_large` require `blob_sha256` on every chunk row from chunk `0` and routing between `LOGGED` (`<= 50 MiB`) and `UNLOGGED` (`> 50 MiB`) requires knowing `byte_size` before opening `COPY ... FROM STDIN WITH (FORMAT BINARY)`:
       1. **`put_file(file_path, ...)` (Primary Path for `20 GB+` Local Media):** Pass 1 computes `byte_size = file_path.stat().st_size` (rejecting `> 25 GiB` immediately with `BlobTooLargeError`) and `sha256 = hashlib.file_digest(f, "sha256").hexdigest()` in `4 MiB` buffers (`O(1)` Python RAM). Next, it checks `await self.head(key=sha256, tenant_id=tenant_id)`: if the blob is already `'ready'` with complete chunks, it returns immediately in `< 2 ms` (zero duplicate `20 GB` upload). Otherwise, Pass 2 upserts `kb_blobs(status='uploading', storage_tier=tier, chunk_count=...)` and streams fixed `4 MiB` (`4,194,304` B) chunks into `COPY <chunk_table> (tenant_id, blob_sha256, chunk_index, chunk_sha256, byte_size, data) FROM STDIN WITH (FORMAT BINARY)` with `set_types(["text", "text", "int4", "bytea", "int4", "bytea"])`.
       2. **`put_stream(stream, byte_size=..., expected_sha256=...)`:** When `expected_sha256` and `byte_size` are supplied by the caller, streams directly in a single pass into `COPY BINARY` and verifies `computed_sha256 == expected_sha256` and `total_bytes == byte_size` before committing `status = 'ready'`. When `expected_sha256` or `byte_size` is omitted on an arbitrary `AsyncIterator[bytes]`, spools chunks in `4 MiB` blocks to a confined `tempfile.SpooledTemporaryFile(max_size=16 * 1024 * 1024)` while computing `(sha256, byte_size)` (aborting immediately if `total_bytes > MAX_BLOB_BYTES`), rewinds, and streams into `COPY BINARY`.
     - Uses a bounded direct `:5432` connection semaphore (`max_concurrent_streams = 4`, `autocommit = True` for post-upload `CHECKPOINT` best-effort execution) per `05-database-stack.md` §6.14.
     - On `get_stream()` / `get()` / `exists()`, verifies `kb_blobs.status = 'ready'` AND `SELECT COUNT(*) FROM <chunk_table> WHERE tenant_id = $1 AND blob_sha256 = $2` equals `kb_blobs.chunk_count` (raising `BlobTruncatedError` and marking `status = 'failed'` if an unclean OS crash truncated `kb_blob_chunks_large`). Reads in bounded keyset batches (`WHERE tenant_id = $1 AND blob_sha256 = $2 AND chunk_index >= $3 ORDER BY chunk_index ASC LIMIT 4`) and verifies each chunk's `chunk_sha256`.
   - **`LocalFsStorageAdapter`:**
     - Confined local filesystem / in-memory implementation (`file://` / `memory://`) for hermetic unit tests without PostgreSQL.
   - **Wire `storage` Across `AdapterSet`, `workers/runner.py`, `cli.py`, and `docker-compose.yml` (`GAP-03` & `GAP-06`):**
     - Add `storage: StoragePort | None = None` to `AdapterSet` and `dev_adapters(dsn, ..., blob_dsn: str | None = None)` in `src/engramory/adapters/factory.py:22-45`. Resolve `effective_blob_dsn = blob_dsn or os.getenv("ENGRAMORY_BLOB_DSN") or dsn` (`GAP-03`) so staging/production deployments route `PgBlobStorageAdapter` directly to PostgreSQL `:5432` (bypassing `PgBouncer :6432`) while local dev defaults to `dsn`.
     - Update `run_worker_loop()` and `start_daemon()` in `src/engramory/workers/runner.py:39-185` (and `_cmd_worker_start()` in `cli.py:481`) to accept `storage: StoragePort | None = None`, run `await storage.reconcile_truncated_blobs(tenant_id=tenant_id)` once on worker startup (detecting any `UNLOGGED` `kb_blob_chunks_large` tables truncated by an unclean PostgreSQL crash), and run `await storage.gc_orphan_chunks(older_than_seconds=7200)` during each maintenance pass (`GAP-06`).
     - Update `_close_adapters()` in `src/engramory/cli.py:190-200` to iterate `("graph", "events", "storage")`, and pass `storage=getattr(adapters, "storage", None)` to `AccessSurface(...)` in both `_run_with_surface()` (`cli.py:209-213`) and `_cmd_mcp_serve()` (`cli.py:798-802`).
     - Update `docker-compose.yml:6-24,151-156` to upgrade the dev image from `pgvector/pgvector:pg16` to `pgvector/pgvector:pg18`, add `stop_grace_period: 30s`, and mount `pgblob_ts:/var/lib/postgresql/blob_ts`.

---

### Phase 3: Multimodal `LLMPort` & Image / Audio / `20 GB+` Video Ingestion Pipeline

1. **Extend `LLMPort` (`src/engramory/ports/llm.py:13-28`) & `LiteLLMAdapter` (`src/engramory/adapters/dev/llm_litellm.py:80-127`):**
   - Add `describe_image(*, image_bytes: bytes, mime_type: str = "image/png", prompt: str = ..., model: str = "vision") -> str`:
     - Formats an OpenAI-compatible vision chat completion request (`messages=[{"role": "user", "content": [{"type": "text", "text": prompt}, {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{b64}"}}]}]`) to LiteLLM `/v1/chat/completions` (`DEFAULT_VISION_MODEL = os.getenv("ENGRAMORY_VISION_MODEL", "vision")`).
     - Enforces a strict `MAX_VISION_IMAGE_BYTES = 20 * 1024 * 1024` (`20 MiB`) per-frame payload guard before base64 encoding.
   - Add `transcribe_audio(*, audio_bytes: bytes, filename: str = "audio.wav", mime_type: str = "audio/wav", model: str = "audio-transcribe") -> dict[str, Any]`:
     - Sends multipart/form-data to LiteLLM `/v1/audio/transcriptions` with `response_format="verbose_json"` (returning `text` and timestamped `segments: [{"start": float, "end": float, "text": str}]`).
2. **Extend `FileCategory` (`src/engramory/core/extraction/types.py:12-22`) & `DocType` (`src/engramory/core/parsers/base.py:13-22`):**
   - Add `AUDIO = "audio"` and `VIDEO = "video"` to `FileCategory` and `IMAGE = "image"`, `AUDIO = "audio"`, `VIDEO = "video"` to `DocType`.
   - Update `DocumentDetector` (`src/engramory/core/parsers/detector.py:20-95`) with magic-byte headers (`PNG` `\x89PNG\r\n\x1a\n`, `JPEG` `\xff\xd8\xff`, `WebP` `RIFF....WEBP`, `GIF` `GIF8`, `MP4`/`MOV` `ftyp`, `MKV`/`WebM` `\x1a\x45\xdf\xa3`, `WAV` `RIFF....WAVE`, `MP3` `ID3`/`\xff\xfb`) and extension mappings so binary media is never misrouted to `TextParser`.
3. **Create `src/engramory/core/extraction/multimodal.py` (`ImageExtractor` & `VideoAudioExtractor`):**
   - **`ImageExtractor`:**
     - Parses image dimensions (`width`, `height`), format (`PNG`, `JPEG`, `WebP`, `GIF`), and SHA-256 header metadata in pure Python stdlib without loading multi-GB files.
     - When `LLMPort.describe_image` is available, generates structured Markdown (visual summary, detected text/OCR, architectural diagram components/edges, table transcription); when offline, combines `DoclingExtractor` OCR (if available) with deterministic structural metadata so offline ingestion remains 100% functional.
     - Emits `Section` objects with **anchor-only** `citation="#image"` (or `"#image-1"`), so `format_local_citation(rel_key, sec.citation, ...)` in `knowledge.py:142` produces clean `<rel_key>#image` citations without duplicating the filename, and populates `metadata={"modality": "image", "mime_type": ..., "width": ..., "height": ..., "sha256": ...}`.
   - **`VideoAudioExtractor` (Streaming `20 GB+` Support with Hardened `ffprobe`/`ffmpeg` Execution):**
     - **Never calls `file_path.read_bytes()`!** Computes SHA-256 and streams raw bytes via `StoragePort.put_file(file_path=resolved_path, ...)` (`4 MiB` chunks, `O(1)` RAM).
     - **Subprocess Security Lockdown (`ffprobe` & `ffmpeg`):**
       - Requires `resolved_path` to be validated through `resolve_confined_repo_path()` first (rejecting symlinks and paths outside the workspace/project root) and verifies `not str(resolved_path).startswith("-")`.
       - Invokes `ffprobe` with explicit protocol lockdown and option terminator:
         `["ffprobe", "-v", "quiet", "-protocol_whitelist", "file,pipe", "-print_format", "json", "-show_format", "-show_streams", "--", str(resolved_path)]` (`stdin=subprocess.DEVNULL`, `timeout=30`, `check=False`).
     - Splits video/audio along fixed time windows (default `segment_seconds = 60`):
       - Extracts 1 representative keyframe per window via `["ffmpeg", "-nostdin", "-v", "quiet", "-protocol_whitelist", "file,pipe", "-ss", str(start), "-i", str(resolved_path), "-frames:v", "1", "-vf", "scale='min(1280,iw)':-2", "-fs", "20971520", "-f", "image2pipe", "-vcodec", "png", "pipe:1"]` (`timeout=60`, stdout capped at `20 MiB`) and captions it via `LLMPort.describe_image`.
       - Extracts compressed mono 16 kHz audio per window via `["ffmpeg", "-nostdin", "-v", "quiet", "-protocol_whitelist", "file,pipe", "-ss", str(start), "-t", str(window), "-i", str(resolved_path), "-vn", "-ac", "1", "-ar", "16000", "-fs", "26214400", "-f", "wav", "pipe:1"]` (`timeout=60`, stdout capped at `25 MiB`) and transcribes via `LLMPort.transcribe_audio`.
       - If `ffmpeg`/`ffprobe` or `LLMPort` is absent (offline unit test environment), emits a deterministic manifest section with container metadata, byte size, SHA-256, and `blob_uri` so ingestion succeeds fail-safe with a warning.
     - Emits time-coded `Section`s with **anchor-only** `citation=f"#t={start_hms}-{end_hms}"` (so `format_local_citation` formats `<rel_key>#t=00:00:00-00:01:00` without line-span `:L1-L1` noise) and `metadata={"modality": "video"|"audio", "start_ms": ..., "end_ms": ..., "blob_sha256": ..., "blob_uri": f"pgblob://{tenant_id}/{sha256}"}`.
4. **Update `DoclingExtractor` (`src/engramory/core/extraction/docling.py:74-115`), `SmartExtractionRouter` (`src/engramory/core/extraction/router.py:18-140`), `KnowledgeCore` (`src/engramory/core/knowledge.py:28-290`), & `GitSyncEngine` (`src/engramory/core/git_sync.py:82-93,217-355`) (`GAP-04` & `GAP-05`):**
   - **Extract Embedded Figures from PDFs/DOCX/PPTX in `DoclingExtractor` (`docling.py:108-115`, `GAP-04`):**
     - Replace `file_bytes = file_path.read_bytes()` at `docling.py:74` with `hashlib.file_digest(f, "sha256")` streaming hash computation and a configurable `MAX_DOCLING_FILE_BYTES = 100 * 1024 * 1024` (`100 MiB`) guard.
     - Instead of hardcoding `images=[]` at `docling.py:111`, extract embedded figures (`getattr(doc, "pictures", [])`) into `DoclingExtractionResult.images` (`[{"index": i, "caption": ..., "png_bytes": ..., "page_no": ...}]`) so `KnowledgeCore` stores each embedded diagram in `kb_blobs`, captions it via `LLMPort.describe_image`, and emits a child `modality="image"` `KBSection` (`#figure-{i}`).
   - Guard `SmartExtractionRouter.extract_file()` so `.png`, `.jpg`, `.jpeg`, `.webp`, `.gif`, `.mp4`, `.mov`, `.webm`, `.mkv`, `.mp3`, `.wav`, `.m4a` route through `ImageExtractor` / `VideoAudioExtractor`, and guard line `96` (`except UnicodeDecodeError`) so non-UTF8 binary files never fall through to `TextParser`.
   - **Prevent Unbounded `p.read_text()` on Binary / Large Files (`knowledge.py:113-118` & `git_sync.py:238-241`):**
     - In `KnowledgeCore.ingest_documents_batch()` (`knowledge.py:113-118`) and `GitSyncEngine.extract_sync_sections()` (`git_sync.py:238-241`), check file category and `p.stat().st_size` **before** calling `p.read_text(encoding="utf-8")`. Only read `full_text` for text/markdown/HTML files `<= 10 MiB`; for binary/media files (`image`, `audio`, `video`, `pdf`, `office`) or files `> 10 MiB`, set `full_text = ""` and skip `calculate_line_numbers()`.
   - **Skip `SemanticChunker.refine_sections()` for Time/Region-Anchored Media (`knowledge.py:122-125`):**
     - Skip `self.chunker.refine_sections(extracted_sections)` when `sec.metadata.get("modality") in {"image", "audio", "video"}` so time-coded video/audio segment boundaries (`#t=00:00:00-00:01:00`) and per-segment metadata (`start_ms`, `end_ms`) are preserved intact.
   - **Extend `GitSyncEngine` for Multimodal Incremental Sync & Cleanup (`git_sync.py:82-93,217-355`, `GAP-05`):**
     - Add `.png`, `.jpg`, `.jpeg`, `.webp`, `.gif`, `.mp4`, `.mov`, `.webm`, `.mkv`, `.mp3`, `.wav`, `.m4a`, `.pptx`, and `.xlsx` to `GitSyncEngine.SUPPORTED_EXTENSIONS` (`git_sync.py:82-93`).
     - Update `GitSyncEngine.extract_sync_sections()` (`git_sync.py:270-281`) and `sync()` (`git_sync.py:285-360`) to accept `storage: StoragePort | None = None` and `graph: GraphPort | None = None`, populate `modality`, `blob_sha256`, `blob_uri`, and `media_metadata` on each `KBSection`, and when files are deleted or modified (`doc_ids_to_delete`), purge stale `DOCUMENT`/`SECTION` graph nodes and invoke `storage.gc_orphan_chunks()`.
   - Update `KnowledgeCore.__init__()` to accept `storage: StoragePort | None = None` and `graph: GraphPort | None = None`.
   - In `KnowledgeCore.ingest_documents_batch()` / `ingest_media_file()`:
     - For any file `> 256 KiB` (`Rule D14a`) or any binary media (`image`, `audio`, `video`, `pdf`), stream raw bytes into `self.storage.put_file()` (`PgBlobStorageAdapter`) to obtain `KBBlob(sha256=..., ...)` and `blob_uri = f"pgblob://{tenant_id}/{blob.sha256}"`.
     - Populate `KBSection(modality=..., blob_sha256=..., blob_uri=..., media_metadata=...)`, screen derived text and serialized `media_metadata` through `inspect_memory_write()` (`poisoning_guard.py`), batch-embed derived text into `vector(1536)`, and persist into `kb_sections`.

---

### Phase 4: Dual-Engine Knowledge Graph & Multimodal GraphRAG (`PostgreSQL 18 + Neo4j`, `Decision D17`)

1. **Unify `:SDD` & `:Knowledge` Label Hygiene Across `src/engramory/core/kb_graph.py:660-669` and `src/engramory/workers/graph_projection.py:17-47` (`GAP-06`):**
   - Extract a shared helper `entity_labels_for(entity: GraphEntity) -> list[str]` in `core/kb_graph.py` and import it in `workers/graph_projection.py:38-41` so both live ingestion (`ingest_sdd_knowledge_graph`) and full graph rebuilds (`engramory graph reproject`) assign identical Neo4j labels: `["Entity", entity.entity_type]` + `"SDD"` only when `entity.entity_type in _SDD_LAYERS or entity.properties.get("sdd")`, and `"Knowledge"` when `entity.entity_type in {"DOCUMENT", "SECTION", "MEDIA_BLOB", "CONCEPT", "RUNBOOK", "RUNBOOK_STEP", "ALERT"}`.
2. **Add Multimodal & Knowledge Graph Extractor (`extract_knowledge_and_media_graph` in `src/engramory/core/kb_graph.py`):**
   - Extract canonical `GraphEntity` and `GraphRelation` records from ingested `kb_documents`, `kb_sections`, and `kb_blobs`:
     - **Nodes (`graph_entities`):**
       - `DOCUMENT` (`id = doc_id`, properties: `project_id`, `title`, `kind`, `repo_path`, `modality`)
       - `SECTION` (`id = f"{doc_id}#{citation_slug}"`, properties: `project_id`, `doc_id`, `citation`, `modality`, `blob_sha256`, `blob_uri`, `start_ms`, `end_ms`)
       - `MEDIA_BLOB` (`id = f"blob:{sha256[:16]}"`, properties: `project_id`, `sha256`, `media_type`, `byte_size`, `storage_tier`, `blob_uri`)
       - `CONCEPT` / `SDD` targets referenced in section text or multimodal captions (`@spec: SPEC-01`, `@adr: ADR-03`, `[[Entity]]`, or extracted headings/components).
     - **Directed Edges (`graph_relations`):**
       - `(DOCUMENT) -[:CONTAINS_SECTION]-> (SECTION)`
       - `(SECTION) -[:DERIVED_FROM_BLOB {start_ms, end_ms}]-> (MEDIA_BLOB)`
       - `(SECTION_i) -[:NEXT_SEGMENT]-> (SECTION_{i+1})` (temporal continuity for video/audio segments)
       - `(SECTION) -[:MENTIONS_ENTITY]-> (CONCEPT | SDD | SERVICE | ALERT)`
       - `(DOCUMENT) -[:CITES_DOC]-> (DOCUMENT)`
   - Persist canonically in PostgreSQL (`bulk_upsert_graph_entities`, `bulk_upsert_graph_relations`) and project into **Neo4j** (`Neo4jGraphAdapter`) via `project_entities` / `project_relations`.
3. **Add PostgreSQL Recursive-CTE Neighbor Fallback (`Repository.find_neighbors` in `src/engramory/core/repository.py`):**
   - Currently `Repository` has `find_ancestors` (`L1285`) and `find_descendants` (`L1368`), while `AccessSurface.graph_neighbors` (`surface.py:238-262`) returns `[]` if `self._graph` (`Neo4jGraphAdapter`) is `None` or unreachable!
   - Add `Repository.find_neighbors(*, id: str, tenant_id: str = "default", rel: str | None = None, depth: int = 1, project_id: str | None = None) -> Sequence[Mapping[str, Any]]` using a cycle-safe bidirectional recursive CTE (`WHERE NOT ((r.src_id = g.id AND r.dst_id = ANY(g.path)) OR (r.dst_id = g.id AND r.src_id = ANY(g.path)))`) over `graph_relations` + `graph_entities` (`depth` capped at `1..6`), enforcing `(%(rel)s::text IS NULL OR r.rel_type = %(rel)s)` and project-scope isolation on **both** `e.properties->>'project_id'` and `r.properties->>'project_id'` (matching `find_ancestors` at `repository.py:1303-1314`).
   - Update `AccessSurface.graph_neighbors()` (`surface.py:238-262`), `AccessSurface.memory_search(expand_graph=True)` (`surface.py:205-217`, `GAP-07`), and `AccessSurface.knowledge_search(expand_graph=True)` (`surface.py:682-713`) to call a shared `AccessSurface._expand_neighbors_with_fallback()` helper that queries `self._graph.neighbors()` (**Neo4j** primary per `Decision D17`) and automatically falls back to `self._repo.find_neighbors()` (PostgreSQL 18 canonical spine) when Neo4j is offline or disabled.

---

### Phase 5: 3-Leg Hybrid RRF (`pgvector` + `ts_lex` BM25 + GraphRAG Expansion) in `Repository` & `KnowledgeCore`

1. **Upgrade `Repository.search_kb_sections()` (`src/engramory/core/repository.py:744-875`, `GAP-01` & `GAP-08`):**
   - Add optional filter parameters: `modality: str | None = None`, `hybrid_rrf: bool = True`, `rrf_k: int = 60`.
   - Enforce `AND (%(modality)s::text IS NULL OR modality = %(modality)s)` across all query branches (`GAP-08`), and project `{_KB_SECTION_COLUMNS}` (`GAP-01`).
   - When both `query_vec` (`vector(1536)`) and `query` (non-empty text) are provided and `hybrid_rrf=True`, execute a single SQL CTE query combining:
     - **`vec_leg`:** Top `limit * 4` scoped sections ordered by `embedding <=> %(qv)s::vector ASC` with `ROW_NUMBER() OVER (ORDER BY embedding <=> %(qv)s::vector ASC) AS r_vec`.
     - **`lex_leg`:** Top `limit * 4` scoped sections matching `(ts_lex @@ websearch_to_tsquery('english', %(query)s) OR text ILIKE %(pattern)s OR citation ILIKE %(pattern)s)` ordered by `ts_rank_cd(ts_lex, websearch_to_tsquery('english', %(query)s)) DESC, (CASE WHEN citation ILIKE %(pattern)s THEN 1 ELSE 0 END) DESC, id ASC` with `ROW_NUMBER() OVER (...) AS r_lex`. Including `citation ILIKE %(pattern)s` alongside `ts_lex` and `text ILIKE` preserves exact symbol/citation matching and ensures existing mock-cursor SQL substring assertions in `tests/` remain 100% compatible.
     - **`rrf_fused`:** `FULL OUTER JOIN` on `id` computing `COALESCE(1.0 / (%(rrf_k)s + r_vec), 0.0) + COALESCE(1.0 / (%(rrf_k)s + r_lex), 0.0) AS rrf_score`, returning the top `limit` hydrated `KBSection` rows with their `modality`, `blob_sha256`, `blob_uri`, and `media_metadata`.
   - When `query_vec is None` (offline mode or embedding endpoint unreachable, `GAP-08`), execute the `lex_leg` query (`(ts_lex @@ websearch_to_tsquery('english', %(query)s) OR text ILIKE %(pattern)s OR citation ILIKE %(pattern)s)` ordered by `ts_rank_cd(...) DESC, version DESC, id ASC`) so offline searches still benefit from `ts_lex` GIN BM25 ranking and `modality` filtering.
2. **Add GraphRAG Context Expansion in `KnowledgeCore.search_sections()` & `AccessSurface.knowledge_search()`:**
   - Extend `AccessSurface.knowledge_search()` (`src/engramory/access/surface.py:682-713`) with default-preserving keyword arguments:
     - `modality: str | None = None`
     - `expand_graph: bool = False`
     - `graph_depth: int = 1`
   - When `expand_graph=True`:
     1. Retrieve top-$k$ `KBSection` hits via 3-leg RRF (`search_kb_sections`).
     2. Collect seed graph IDs (`sec.doc_id`, `f"{sec.doc_id}#{slug}"`, `f"blob:{sec.blob_sha256[:16]}"`, and any `@spec:`/`@adr:` tags in `sec.text`).
     3. Traverse $1\text{..}\text{graph\_depth}$ hops via `self._graph.neighbors()` (**Neo4j** `Neo4jGraphAdapter`) with automatic fallback to `self._repo.find_neighbors()` (PostgreSQL 18 CTE), strictly filtered by `tenant_id` and `project_id`.
     4. Because `KBSection` is `@dataclass(frozen=True, slots=True)` with `_frozen_mapping(media_metadata)`, return a new section copy via `dataclasses.replace(sec, media_metadata={**sec.media_metadata, "graph_context": related_entities})` rather than mutating `sec.media_metadata` in place.

---

### Phase 6: Governed `AccessSurface`, MCP Server Tools & CLI Commands

1. **Update `src/engramory/access/authz.py` (`L15-42`) & `src/engramory/access/surface.py`:**
   - Add `"blob_put"`, `"blob_get"`, `"blob_head"`, and `"knowledge_ingest_media"` to `KNOWN_ACTIONS` in `authz.py`.
   - Enforce strict tenant isolation (`TargetScope(tenant_id=ctx.tenant_id, scope=scope)`): a caller in `tenant_a` can never read, probe (`exists`/`head`), or link a `pgblob://` URI belonging to `tenant_b`, and every allow/deny decision writes an `audit_records` row (`EARS.01.03.b050`).
   - **Confined Path Resolution (`resolve_confined_repo_path`):**
     - In `AccessSurface.blob_put()` and `AccessSurface.knowledge_ingest_media()` (as well as `McpServer.handle_call_tool` at `mcp/server.py:1120-1124`), validate any local `file_path` through `resolve_confined_repo_path(str(file_path), project_id=ctx.project_id, enforce_project_boundary=self._auth_enabled)` before opening or probing the file, rejecting symlinks (`file_path.is_symlink()`) and any path outside the confined workspace/project root.
   - Add governed methods on `AccessSurface`:
     - `blob_put(ctx, *, file_path: Path | None = None, data: bytes | None = None, content_type: str, scope: str = "project", metadata: Mapping[str, Any] | None = None) -> dict[str, Any]`
     - `blob_head(ctx, *, sha256: str, scope: str = "project") -> dict[str, Any] | None`
     - `knowledge_ingest_media(ctx, *, file_path: Path, evidence_ref: str, scope: str = "project", embed: bool = True, project_graph: bool = True) -> dict[str, Any]`
2. **Update `src/engramory/mcp/server.py` (`TOOLS_SCHEMA` at `L43-350` & dispatch handlers at `L555-577`, `GAP-02`):**
   - Extend `knowledge_search` schema (`L134-166`) with optional `modality` (`enum: ["text", "markdown", "pdf", "document", "image", "audio", "video"]`), `expand_graph` (`boolean`, default `False`), and `graph_depth` (`integer`, default `1`).
   - **Backward-Compatible MCP Dispatch & Multimodal Response Serialization (`tests/test_plan017_remediation.py:224-230` & `mcp/server.py:563-576`, `GAP-02`):**
     - In `McpServer.handle_call_tool` (`mcp/server.py:555-575`), pass `modality`, `expand_graph`, and `graph_depth` as keyword arguments to `self._surface.knowledge_search(...)` **only when present in `arguments`** (or non-default), so `test_mcp_knowledge_search_dispatches_to_surface` (`assert_awaited_once_with(ctx, query="access surface", domain_id=None, scope="project", limit=3)`) continues to pass unchanged.
     - Update `formatted_secs` in `mcp/server.py:563-576` to include `"modality": getattr(s, "modality", "text")`, `"blob_sha256": getattr(s, "blob_sha256", None)`, `"blob_uri": getattr(s, "blob_uri", None)`, and `"media_metadata": dict(getattr(s, "media_metadata", {}) or {})` so MCP callers receive the blob pointer and `graph_context` neighborhood.
   - Add `knowledge_ingest_media` MCP tool (governed write accepting a workspace-confined `file_path`, `evidence_ref`, `scope`, and `project_graph=True`) and `blob_head` MCP tool (inspecting `kb_blobs` manifest metadata + chunk completeness status by `sha256`).
3. **Update `src/engramory/cli.py` (`L348-460`, `L960-1025`, `GAP-02`):**
   - Register `aliases=["kb"]` on the `knowledge` subparser (`sub.add_parser("knowledge", aliases=["kb"], ...)` at `cli.py:962`) so both `engramory knowledge ...` and `engramory kb ...` work seamlessly.
   - In `_cmd_knowledge_ingest` (`cli.py:348`), `_cmd_knowledge_sync_git` (`cli.py:388-401`), and `_cmd_knowledge_search` (`cli.py:428-460`), pass `storage=getattr(adapters, "storage", None)` and `graph=adapters.graph` into `KnowledgeCore` / `GitSyncEngine` (or route `_cmd_knowledge_search` through `AccessSurface.knowledge_search`), and include `modality`, `blob_sha256`, `blob_uri`, and `media_metadata` in the returned JSON payload (`cli.py:449-457`).
   - Add `--modality`, `--expand-graph`, and `--graph-depth` flags to `engramory knowledge search`.
   - Add `engramory knowledge ingest-media <path> --evidence-ref <ref>` and `engramory knowledge blob-stat <sha256>` / `engramory knowledge blob-get <sha256> --out <path>` (streaming `4`-chunk keyset download).

---

### Phase 7: Verification Suite (`tests/`) & Documentation Synchronization

1. **Unit & Property Tests (Offline, `<2s`, no external daemon required):**
   - `tests/test_migrations.py`:
     - Update `test_migrations_exist_and_ordered()` (`L11-24`) to include `"0010_multimodal_rag_and_blob_cas.sql"` and verify `0010` DDL invariants (`kb_blobs`, `kb_blob_chunks`, `kb_blob_chunks_large`, `STORAGE EXTERNAL`, `ts_lex`).
   - `tests/test_storage_pg_blob_cas.py`:
     - Tests `LocalFsStorageAdapter` and `PgBlobStorageAdapter` (using mock/fake async cursor for two-pass `put_file`, `SpooledTemporaryFile` `put_stream`, `COPY BINARY`, and keyset pagination) across `0 B`, `256 KiB` (`Rule D14a`), `4 MiB` exact chunk boundary, `50 MiB` (`LOGGED` -> `UNLOGGED` tier transition), and simulated multi-chunk streaming.
     - Verifies `BlobTruncatedError` when `COUNT(chunk_index) < kb_blobs.chunk_count` (simulating `UNLOGGED` crash truncation of `kb_blob_chunks_large`), `BlobIntegrityError` on corrupt `chunk_sha256`, `BlobTooLargeError` above `25 GiB`, and cross-tenant isolation (`tenant_a` cannot read `tenant_b`'s `sha256`).
   - `tests/test_multimodal_extraction_and_graphrag.py`:
     - Tests `DocumentDetector` magic-byte detection (`PNG`, `JPEG`, `WebP`, `MP4`, `MKV`, `WAV`, `MP3`) and verifies binary files never fall through to `TextParser` in `SmartExtractionRouter.extract_file()` or `p.read_text()` in `KnowledgeCore` / `GitSyncEngine`.
     - Tests `ImageExtractor` (with mocked `LLMPort.describe_image` and offline fallback) and `VideoAudioExtractor` (verifying `4 MiB` streaming chunk iteration without `file_path.read_bytes()`, `-protocol_whitelist file,pipe` and `-fs` caps on `ffprobe`/`ffmpeg`, and anchor-only citations `#t=00:00:00-00:01:00`).
     - Tests `extract_knowledge_and_media_graph()` (`DOCUMENT`, `SECTION`, `MEDIA_BLOB`, `CONTAINS_SECTION`, `DERIVED_FROM_BLOB`, `NEXT_SEGMENT`) and verifies `:SDD` label hygiene in `kb_graph.py`.
     - Tests 3-leg Hybrid RRF (`search_kb_sections`) + `expand_graph=True` (`Neo4jGraphAdapter` primary with `Repository.find_neighbors` PostgreSQL CTE fallback using `dataclasses.replace()`).
2. **Documentation & Gate Verification:**
   - Synchronize the SQL DDL snippets in `docs/MULTIMODAL_AND_POSTGRES_BLOB_STORAGE.md` (`L84-166`) so they reflect the composite `(tenant_id, sha256)` / `(tenant_id, blob_sha256, chunk_index)` primary keys, `trg_kb_blobs_updated_at` trigger, `toast.autovacuum_vacuum_scale_factor = 0.01`, and `kb_blob_chunks_pkey` / `kb_blob_chunks_large_pkey` index tablespace moves in `0010_multimodal_rag_and_blob_cas.sql`.
   - Run `uv run engramory docs lint --strict --json`, `task check`, and `task ci`.

---

## Claim ledger

| # | Claim | Symbol | Citation |
|---|---|---|---|
| 1 | `StoragePort` currently defines only in-memory `put`, `get`, `delete`, and `exists` without streaming methods | `class StoragePort(Protocol):` | `src/engramory/ports/storage.py:11-26` |
| 2 | `LLMPort` currently defines only `chat` and `embed` methods | `class LLMPort(Protocol):` | `src/engramory/ports/llm.py:13-28` |
| 3 | `GraphPort` defines `upsert_node`, `upsert_edge`, `neighbors`, `ancestors`, `descendants`, `delete_node`, `delete_edge`, and `purge_stale_nodes` | `class GraphPort(Protocol):` | `src/engramory/ports/graph.py:13-82` |
| 4 | `Neo4jGraphAdapter` implements `GraphPort` over `AsyncGraphDatabase` (`neo4j>=5.26.0`) | `class Neo4jGraphAdapter:` | `src/engramory/adapters/dev/graph_neo4j.py:33-60` |
| 5 | `LiteLLMAdapter` implements `chat` (`/v1/chat/completions`) and `embed` (`/v1/embeddings`) over `urllib.request` | `class LiteLLMAdapter:` | `src/engramory/adapters/dev/llm_litellm.py:22-127` |
| 6 | `AdapterSet` and `dev_adapters` in `factory.py` currently wire `vector`, `llm`, `graph`, and `events`, but omit `storage` | `class AdapterSet:` | `src/engramory/adapters/factory.py:22-45` |
| 7 | `FileCategory` enum in `extraction/types.py` defines `DOCUMENT`, `SPREADSHEET`, `PRESENTATION`, `PDF`, `TEXT`, `IMAGE`, `UNKNOWN` (lacking `AUDIO` and `VIDEO`) | `class FileCategory(StrEnum):` | `src/engramory/core/extraction/types.py:12-22` |
| 8 | `DoclingExtractor.extract` reads the entire file into memory via `file_path.read_bytes()` at line 74 and sets `images=[]` at line 111 | `file_bytes = file_path.read_bytes()` | `src/engramory/core/extraction/docling.py:74-115` |
| 9 | `SmartExtractionRouter.extract_file` falls back to `file_path.read_bytes()` on `UnicodeDecodeError` and passes binary bytes to `self.detector.parse` | `content = file_path.read_bytes()` | `src/engramory/core/extraction/router.py:93-99` |
| 10 | `DocType` enum in `parsers/base.py` defines `MARKDOWN`, `HTML`, `PDF`, `TEMPLATE`, `TEXT`, `UNKNOWN` | `class DocType(StrEnum):` | `src/engramory/core/parsers/base.py:13-22` |
| 11 | `DocumentDetector` in `parsers/detector.py` maps `.md`, `.html`, `.pdf`, `.txt`, `.template` and defaults unknown binary content to `DocType.TEXT` | `class DocumentDetector:` | `src/engramory/core/parsers/detector.py:17-105` |
| 12 | `KnowledgeCore` in `core/knowledge.py` orchestrates `ingest_file`, `ingest_documents_batch`, and `search_sections` | `class KnowledgeCore:` | `src/engramory/core/knowledge.py:28-125` |
| 13 | `KBSection` frozen dataclass in `core/models.py` currently carries `doc_id`, `citation`, `text`, `project_id`, `domain_id`, `tenant_id`, `scope`, `version`, `id` | `class KBSection:` | `src/engramory/core/models.py:140-160` |
| 14 | `GraphEntity` and `GraphRelation` frozen dataclasses in `core/models.py` mirror `graph_entities` and `graph_relations` | `class GraphEntity:` | `src/engramory/core/models.py:178-213` |
| 15 | `KBDocument` frozen dataclass in `core/models.py` models L0 governed documentation metadata | `class KBDocument:` | `src/engramory/core/models.py:221-250` |
| 16 | `Repository.search_kb_sections` in `core/repository.py` runs vector search when `query_vec` is provided or `text ILIKE` fallback when `query_vec` is `None` | `async def search_kb_sections(` | `src/engramory/core/repository.py:744-875` |
| 17 | `Repository.find_ancestors` and `Repository.find_descendants` implement PostgreSQL recursive CTE graph traversals, while `find_neighbors` is not yet defined | `async def find_ancestors(` | `src/engramory/core/repository.py:1285-1375` |
| 18 | `ingest_sdd_knowledge_graph` in `core/kb_graph.py` unconditionally sets `labels = ["Entity", "SDD", entity.entity_type]` even for `extract_docs_graph` entities | `labels = ["Entity", "SDD", entity.entity_type]` | `src/engramory/core/kb_graph.py:665-669` |
| 19 | `project_entities` in `workers/graph_projection.py` conditionally appends `"SDD"` only when `entity.entity_type in _SDD_LAYERS or entity.properties.get("sdd")` | `_SDD_LAYERS = frozenset(` | `src/engramory/workers/graph_projection.py:17-41` |
| 20 | `KNOWN_ACTIONS` in `access/authz.py` defines the closed set of authorizable actions enforced by `authorize()` | `KNOWN_ACTIONS = frozenset(` | `src/engramory/access/authz.py:15-42` |
| 21 | `AccessSurface.graph_neighbors` in `access/surface.py` queries `self._graph.neighbors` and returns `[]` without a PostgreSQL CTE fallback if `self._graph` fails | `async def graph_neighbors(` | `src/engramory/access/surface.py:238-263` |
| 22 | `AccessSurface.knowledge_search` in `access/surface.py` embeds the query via `self._llm.embed` and calls `self._repo.search_kb_sections` | `async def knowledge_search(` | `src/engramory/access/surface.py:682-713` |
| 23 | `TOOLS_SCHEMA` in `mcp/server.py` defines the `knowledge_search` and `graph_neighbors` MCP tool schemas | `"name": "knowledge_search"` | `src/engramory/mcp/server.py:134-180` |
| 24 | `0003_reconcile_contracts.sql` defines `memories.ts_lex` GIN index (`L44-48`) and `kb_sections` table (`L90-113`) without `ts_lex` or multimodal columns | `CREATE TABLE IF NOT EXISTS kb_sections (` | `db/migrations/0003_reconcile_contracts.sql:90-113` |
| 25 | `0006_resize_embeddings_1536.sql` pins `memories.embedding` and `kb_sections.embedding` to `vector(1536)` with HNSW cosine indexes | `idx_kb_sections_embedding_hnsw` | `db/migrations/0006_resize_embeddings_1536.sql:25-32` |
| 26 | `0007_graph_entities_canonical.sql` defines `graph_entities` and `graph_relations` tables in PostgreSQL | `CREATE TABLE IF NOT EXISTS graph_entities (` | `db/migrations/0007_graph_entities_canonical.sql:5-35` |
| 27 | `0008_runbooks_and_doc_governance.sql` defines `kb_documents` and `kb_runbook_steps` tables | `CREATE TABLE IF NOT EXISTS kb_documents (` | `db/migrations/0008_runbooks_and_doc_governance.sql:6-30` |
| 28 | `0009_tenant_profiles_and_experience_dedup.sql` is the latest SQL migration (`0009`), making `0010` the next sequential migration | `0009_tenant_profiles_and_experience_dedup.sql` | `db/migrations/0009_tenant_profiles_and_experience_dedup.sql:1-5` |
| 29 | `docker-compose.yml` currently uses `pgvector/pgvector:pg16` (`L7`) and `neo4j:5.26-community` (`L58`) | `image: pgvector/pgvector:pg16` | `docker-compose.yml:6-19` |
| 30 | `SPEC-02_memory_store.yaml` defines the Layer 06 data contracts for `MemoryStore` (`Episode`, `Memory`, `KBSection`) | `id: SPEC-02` | `docs/sdd/06_SPEC/SPEC-02_memory_store.yaml:12-65` |
| 31 | `SPEC-03_retrieval_graph.yaml` defines the Layer 06 contracts for `RetrievalService` (`retrieve`, `traverse`, `MemoryHit`) | `id: SPEC-03` | `docs/sdd/06_SPEC/SPEC-03_retrieval_graph.yaml:12-68` |
| 32 | `SPEC-06_ports_portability.yaml` defines the Layer 06 contracts for `StoragePort`, `VectorPort`, `GraphPort`, and `LLMPort` | `id: SPEC-06` | `docs/sdd/06_SPEC/SPEC-06_ports_portability.yaml:12-70` |
| 33 | `docs/MULTIMODAL_AND_POSTGRES_BLOB_STORAGE.md` specifies the approved `kb_blobs`, `kb_blob_chunks`, `kb_blob_chunks_large` DDL, `COPY BINARY` protocol, and `Decision D17` Neo4j architecture | `id: DOC-ENGRAMORY-MULTIMODAL-BLOB-CAS` | `docs/MULTIMODAL_AND_POSTGRES_BLOB_STORAGE.md:1-30` |
| 34 | `platform-technology_stack/05-database-stack.md` §6.13 (`Decision D17`) and §6.14 (`Rule D14a`/`D14b`) define the normative Dual-Engine Graph Standard and PostgreSQL 18 `20 GB+` Chunked Blob CAS | `CREATE TABLESPACE ts_blob_cold` | `platform-technology_stack/05-database-stack.md:715` |
| 35 | `_KB_SECTION_COLUMNS` in `core/repository.py` explicitly lists projected `kb_sections` columns and must include `modality`, `blob_sha256`, `blob_uri`, and `media_metadata` | `_KB_SECTION_COLUMNS =` | `src/engramory/core/repository.py:73` |
| 36 | `Repository.upsert_kb_section` inserts and updates a single `KBSection` row keyed by `(tenant_id, doc_id, citation, version)` | `async def upsert_kb_section(` | `src/engramory/core/repository.py:501-540` |
| 37 | `Repository.upsert_kb_sections_batch` bulk-upserts `KBSection` rows via `UNNEST` arrays | `async def upsert_kb_sections_batch(` | `src/engramory/core/repository.py:567-646` |
| 38 | `_hydrate_kb_section` in `core/repository.py` constructs `KBSection` instances from database rows | `def _hydrate_kb_section(` | `src/engramory/core/repository.py:1857-1868` |
| 39 | `KnowledgeCore.ingest_documents_batch` reads `full_text = p.read_text(encoding="utf-8")` at line 114 and calls `self.chunker.refine_sections` at line 125 | `full_text = p.read_text(encoding="utf-8")` | `src/engramory/core/knowledge.py:113-126` |
| 40 | `_close_adapters` and `_run_with_surface` in `cli.py` wire and close adapters on `AccessSurface` | `async def _close_adapters(` | `src/engramory/cli.py:190-214` |
| 41 | `test_migrations_exist_and_ordered` in `tests/test_migrations.py` asserts the exact list of SQL migration filenames in `db/migrations/` | `def test_migrations_exist_and_ordered() -> None:` | `tests/test_migrations.py:11-24` |
| 42 | `test_ports_are_stdlib_only` in `tests/unit/test_no_vendor_imports.py` forbids `src/engramory/ports/*.py` from importing `engramory.core` or any third-party package | `def test_ports_are_stdlib_only() -> None:` | `tests/unit/test_no_vendor_imports.py:65-69` |
| 43 | `test_mcp_knowledge_search_dispatches_to_surface` in `tests/test_plan017_remediation.py` asserts exact keyword arguments passed to `mock_surface.knowledge_search` | `mock_surface.knowledge_search.assert_awaited_once_with(` | `tests/test_plan017_remediation.py:224-231` |
| 44 | `Repository.get_kb_section` and `Repository.list_kb_sections` hardcode `SELECT id, tenant_id, project_id, domain_id, scope, doc_id, citation, text, version` instead of interpolating `_KB_SECTION_COLUMNS` | `async def get_kb_section(` | `src/engramory/core/repository.py:693-743` |
| 45 | `McpServer.handle_call_tool` (`knowledge_search`) builds `formatted_secs` with only 8 fields, omitting `modality`, `blob_sha256`, `blob_uri`, and `media_metadata` | `formatted_secs = [` | `src/engramory/mcp/server.py:563-576` |
| 46 | `_cmd_knowledge_search` in `cli.py` constructs `KnowledgeCore(repo, llm=adapters.llm)` without `storage` or `graph` and omits multimodal fields from JSON output | `def _cmd_knowledge_search(args: argparse.Namespace) -> int:` | `src/engramory/cli.py:428-460` |
| 47 | `GitSyncEngine.SUPPORTED_EXTENSIONS` and `extract_sync_sections` in `core/git_sync.py` omit audio/video/image extensions and construct `KBSection` without multimodal fields | `def extract_sync_sections(` | `src/engramory/core/git_sync.py:217-284` |
| 48 | `run_worker_loop` in `workers/runner.py` runs episode reflection without `storage.reconcile_truncated_blobs()` on startup or periodic `storage.gc_orphan_chunks()` | `async def run_worker_loop(` | `src/engramory/workers/runner.py:39-151` |
| 49 | `AccessSurface.__init__` in `access/surface.py` accepts `repository`, `graph`, and `llm`, and needs an optional `storage` parameter | `def __init__(` | `src/engramory/access/surface.py:123-133` |
| 50 | `AccessSurface.memory_search` in `access/surface.py` queries `self._graph.neighbors` when `expand_graph=True` without falling back to `self._repo.find_neighbors` | `if expand_graph and self._graph is not None:` | `src/engramory/access/surface.py:205-217` |

---

## Review log

### Pass 1 - 2026-11-12 (Author Self-Review)

- **Scope & Citations Check:** Verified all 34 load-bearing claims in `## Claim ledger` against live source files in `platform-engramory` and `platform-technology_stack`.
- **Architecture & Governance Check:** Included 3-option scored recommendations (`1–5`) for the 3 core architectural questions (`Q1` Multimodal Representation, `Q2` 3-Leg Hybrid RRF + GraphRAG, `Q3` `20 GB+` Streaming Blob CAS), SDD-First Layer 06/07/09 updates (Phase 1), and full PostgreSQL 18 + Neo4j implementation steps (Phases 2–7).
- **Result:** Ready for `check_plan.py` deterministic gate and fresh-context Multi-Lens Subagent Review 1 (Pass 2).

### Pass 2 - 2026-11-12 - independent (Multi-Lens Subagent Review 1 — Subagent `f52b2a11-a640-4c1e-8973-56a39c50caca`)

- **Lenses Applied:** Google (Design Doc invariants & SRE `20 GB+` streaming / `UNLOGGED` crash blast radius), Anthropic (`Judge != Generator`, MCP/CLI tool contracts, `resolve_confined_repo_path` & `ffprobe`/`ffmpeg` subprocess fencing), AWS (Well-Architected `COPY BINARY` two-pass streaming & `ts_blob_cold` fallback), Microsoft (STRIDE threat modeling & CI test-gate compatibility).
- **Findings Identified & Folded:**
  - **`[C1]` Stdlib-Only Port Boundary & Migration List Gate:** Specified that `KBBlob` and storage exceptions are defined directly in `src/engramory/ports/storage.py` using stdlib only (re-exported in `core/models.py`) to satisfy `test_ports_are_stdlib_only()` (`tests/unit/test_no_vendor_imports.py:65-69`), and added `tests/test_migrations.py:11-24` update for `0010_multimodal_rag_and_blob_cas.sql`.
  - **`[C2]` `Repository` & `AccessSurface` Multimodal Column Round-Trip:** Added explicit updates for `_KB_SECTION_COLUMNS` (`repository.py:73`), `upsert_kb_section()` (`repository.py:501-540`), `upsert_kb_sections_batch()` (`repository.py:567-646`), `_hydrate_kb_section()` (`repository.py:1857-1868`), and `AccessSurface.knowledge_ingest` / `knowledge_ingest_batch` (`surface.py:559-638`).
  - **`[C3]` Deterministic Two-Pass / Pre-Hashed `COPY BINARY` Streaming:** Specified `PgBlobStorageAdapter.put_file()` (Pass 1 `hashlib.file_digest` + `stat().st_size` + `<2 ms` dedup `head()` check, Pass 2 `COPY BINARY`) and `put_stream()` (`SpooledTemporaryFile` when `expected_sha256`/`byte_size` are omitted).
  - **`[C4]` `resolve_confined_repo_path` & `ffprobe`/`ffmpeg` Subprocess Lockdown:** Mandated `resolve_confined_repo_path()` + symlink rejection in `AccessSurface` and `McpServer`, and added `-protocol_whitelist file,pipe`, `--` argument terminator, and `-fs` output size caps to `ffprobe`/`ffmpeg`.
  - **`[M1]`–`[M4]` & `[Med1]`–`[Med3]`:** Added `_frozen_mapping(media_metadata)` + `dataclasses.replace()` in `knowledge_search(expand_graph=True)`, `r.properties->>'project_id'` filtering in `Repository.find_neighbors()`, backward-compatible kwargs forwarding in `McpServer.handle_call_tool` (`tests/test_plan017_remediation.py:224-230`), `citation ILIKE` in `lex_leg`, `trg_kb_blobs_updated_at` trigger + PK index tablespace move + `toast.autovacuum_vacuum_scale_factor = 0.01`, `p.read_text()` size/binary guards in `KnowledgeCore` and `GitSyncEngine`, `engramory knowledge` (`aliases=["kb"]`) + `storage` wiring in `cli.py`, anchor-only citations (`#image`, `#t=...`) + skipping `refine_sections` for media, and 9 new Claim Ledger rows (`35`–`43`).
- **Result:** All 11 findings (`[C1]`–`[C4]`, `[M1]`–`[M4]`, `[Med1]`–`[Med3]`) folded. Proceeding to `check_plan.py` and Pass 3 verification.

### Pass 3 - 2026-11-12 - independent (Multi-Lens Subagent Review 2 — Subagent `43cb5e2b-6e5b-41b5-8321-39c526872964`)

- **Verification Check:** Independent fresh-context subagent verified all 11 folded remediations (`[C1]`–`[C4]`, `[M1]`–`[M4]`, `[Med1]`–`[Med3]`) across Phases 1–7 and spot-checked all 43 `## Claim ledger` rows against live files in `platform-engramory` and `platform-technology_stack`.
- **Result:** Verified Pass 2 remediations; followed by deep codebase gap audit (`GAP-01`–`GAP-09`) in Pass 4.

### Pass 4 - 2026-11-12 - independent (Deep Codebase Gap Audit — `GAP-01` through `GAP-09`)

- **Gaps Identified & Folded:**
  - **`GAP-01` (Critical):** Replaced hardcoded `SELECT` column lists in `get_kb_section`, `list_kb_sections`, and `search_kb_sections` (`repository.py:693-826`) with `{_KB_SECTION_COLUMNS}`.
  - **`GAP-02` (Critical):** Updated `McpServer.handle_call_tool("knowledge_search")` (`mcp/server.py:563-576`) and `cli._cmd_knowledge_ingest` / `_cmd_knowledge_sync_git` / `_cmd_knowledge_search` (`cli.py:348-460`) to wire `storage` and `graph` and preserve `modality`, `blob_sha256`, `blob_uri`, and `media_metadata` in JSON outputs.
  - **`GAP-03` (Critical):** Added `ENGRAMORY_BLOB_DSN` resolution in `dev_adapters()` (`factory.py:33-45`) so `PgBlobStorageAdapter` connects directly to PostgreSQL `:5432` in stage/prod while bypassing `PgBouncer :6432`.
  - **`GAP-04` (Major):** Updated `DoclingExtractor.extract()` (`docling.py:74-115`) to stream-hash files and extract embedded figures (`doc.pictures`) from PDFs/DOCX/PPTX into `kb_blobs` + child `modality="image"` sections.
  - **`GAP-05` (Major):** Extended `GitSyncEngine.SUPPORTED_EXTENSIONS` (`git_sync.py:82-93`) and `extract_sync_sections()` / `sync()` (`git_sync.py:217-355`) to preserve multimodal fields and clean up stale graph nodes / orphan blob chunks on file deletion.
  - **`GAP-06` (Major):** Wired `storage.reconcile_truncated_blobs()` (startup) and `storage.gc_orphan_chunks()` (periodic) into `run_worker_loop()` (`workers/runner.py:39-151`), and unified Neo4j `:SDD` / `:Knowledge` node labeling via a shared `entity_labels_for()` helper across `kb_graph.py` and `workers/graph_projection.py`.
  - **`GAP-07` (Major):** Added `storage: StoragePort | None = None` to `AccessSurface.__init__()` (`surface.py:123-133`) and unified Neo4j-primary + PG-CTE-fallback neighbor expansion across both `memory_search(expand_graph=True)` (`surface.py:205-217`) and `knowledge_search(expand_graph=True)`.
  - **`GAP-08` (Medium):** Added `modality` filtering and `ts_lex` BM25 ranking to the offline (`query_vec is None`) branch of `Repository.search_kb_sections()`.
  - **`GAP-09` (Medium):** Added `IPLAN-008` alongside `CHG-008` and gated Phase 1 on `engramory sdd validate-graph --invariants` and `engramory sdd check-invariants --type SDD2C`.
- **Result:** ready — no new findings.
