# PLAN-005: Staging Traffic Readiness Remediation

> **Status:** Accepted  
> **Date:** 2026-11-01  
> **Authors:** Engramory & LiteLLM Integration Working Group  
> **Owning Repo:** `aidoc-flow-engramory`  
> **Cross-Repo Impact:** `platform-litellm-laya`  
> **Target Environment:** Pre-Production Staging (`100.98.134.10`)  

---

## 1. Executive Summary & Objective

The objective of this plan is to remediate the **5 critical gaps** preventing `aidoc-flow-engramory` from receiving and serving live staging traffic from `platform-litellm-laya`:

1. **Embedding Adapter & Vector Annular Index:** Connect Engramory to LiteLLM's embedding endpoint (`bge-small-en-v1.5`, 384 dims) and create safe HNSW vector indices in PostgreSQL for both memories and knowledge sections.
2. **Active Distillation Pipeline:** Update the reflection worker to populate real vector embeddings via `LLMPort` and `VectorPort` in non-blocking batches instead of `NULL` placeholders.
3. **Knowledge & Documentation Ingestion Tooling:** Expose `knowledge ingest` in the `engramory` CLI and provide an automated section-chunking crawler to seed institutional memory within ambient token ceilings.
4. **Autonomous Distillation Daemon:** Create a resilient background worker daemon (`src/engramory/workers/runner.py`) with dynamic work discovery and healthcheck monitoring.
5. **Production Containerization:** Provide a production `Dockerfile` running as non-root `UID 1004` and compose service definition for the staging platform namespace.

---

## 2. Claim Ledger

| # | Claim | Symbol / Key | Citation |
|---|---|---|---|
| 1 | `LLMPort` protocol defines `chat` and `embed` interfaces | `LLMPort` | `src/engramory/ports/llm.py:13-28` |
| 2 | `dev_adapters(dsn)` provides only `VectorPort` and lacks `llm` adapter | `dev_adapters` | `src/engramory/adapters/factory.py:27-29` |
| 3 | `distillation.py` assigns placeholder model and leaves `embedding = None` | `INTERIM_EMBEDDING_MODEL` | `src/engramory/workers/distillation.py:18-45` |
| 4 | `memories.embedding` column is unconstrained vector with no index | `embedding` | `db/migrations/0001_init_memory.sql:58-76` |
| 5 | `kb_sections.embedding` is unconstrained vector with no index | `embedding` | `db/migrations/0003_reconcile_contracts.sql:100-102` |
| 6 | `PgVectorAdapter.upsert()` persists vector projection separately from canonical row | `upsert` | `src/engramory/adapters/dev/vector_pg.py:36-54` |
| 7 | `AccessSurface.knowledge_ingest()` requires `evidence_ref` and persists `KBSection` | `knowledge_ingest` | `src/engramory/access/surface.py:216-251` |
| 8 | `cli.py` subparser does not expose `knowledge` subcommand | `_build_parser` | `src/engramory/cli.py:272-319` |
| 9 | Ambient gateway queries `v_ambient_memory_active` view | `v_ambient_memory_active` | `../platform-litellm-laya/ambient_memory_hook.py:214` |

---

## 3. Implementation Phases

### Phase 1: Embedding Adapter & Safe PostgreSQL HNSW Index

#### 1.1 Implement `LiteLLMAdapter`

Create `src/engramory/adapters/dev/llm_litellm.py` implementing `LLMPort` (`chat` and `embed`):

- Connects to LiteLLM Proxy endpoints (`/v1/embeddings` and `/v1/chat/completions`) using standard library `urllib.request`.
- Uses `asyncio.to_thread` for all HTTP I/O so calls are non-blocking to the asyncio event loop.
- Standardizes on model `bge-small-en-v1.5` (384 dimensions) for embeddings and `chat` alias for completions.
- Provides timeout and error handling.

#### 1.2 Wire `llm` into `AdapterSet`

Update `src/engramory/adapters/factory.py`:

- Add `llm: LLMPort` to `AdapterSet`.
- In `dev_adapters(dsn)` wire `llm=LiteLLMAdapter()`.

#### 1.3 Add Migration `0005_pin_embeddings_hnsw.sql`

Create `db/migrations/0005_pin_embeddings_hnsw.sql` with pre-alter dimension sanitization:

```sql
-- 0005 — Pin embedding dimensions to 384 (bge-small-en-v1.5) and add HNSW indices.

-- 1. Sanitize memories with mismatched dimensions before altering column type
UPDATE memories
SET embedding = NULL, embedding_dims = NULL
WHERE embedding IS NOT NULL AND vector_dims(embedding) != 384;

ALTER TABLE memories ALTER COLUMN embedding TYPE vector(384);

CREATE INDEX IF NOT EXISTS idx_memories_embedding_hnsw 
    ON memories USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);

-- 2. Pin and index kb_sections
UPDATE kb_sections
SET embedding = NULL, embedding_dims = NULL
WHERE embedding IS NOT NULL AND vector_dims(embedding) != 384;

ALTER TABLE kb_sections ALTER COLUMN embedding TYPE vector(384);

CREATE INDEX IF NOT EXISTS idx_kb_sections_embedding_hnsw
    ON kb_sections USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);
```

Update `tests/test_migrations.py` to assert migration 0005 existence and ordering.

---

### Phase 2: Active Distillation Embedding Pipeline

#### 2.1 Update Reflection Worker

In `src/engramory/workers/distillation.py`:

- Accept optional `llm: LLMPort | None = None` and `vector: VectorPort | None = None` in `reflect()`.
- Batch unreflected episodes in chunks of at most 32 items.
- If `llm` is provided, call `llm.embed(texts=summaries)` to obtain 384-dim vector representations.
- Persist canonical memory rows via `repo.upsert_memory()`.
- Persist vector projections via `await vector.upsert(id=memory_id, embedding=emb)`.
- If `llm` call encounters a transient failure, log a warning and skip projection for that batch without corrupting state.

#### 2.2 Verification

Add integration tests asserting:

- Unreflected episodes are distilled into active memories.
- Stored memories have non-null 384-dimensional vector embeddings in `memories.embedding`.
- Ambient view `v_ambient_memory_active` successfully surfaces these memories.

---

### Phase 3: Knowledge & Documentation Ingestion Tooling

#### 3.1 CLI `knowledge ingest` Subcommand

Update `src/engramory/cli.py` to add `knowledge ingest`:

```bash
engramory knowledge ingest \
  --doc-id ADR-010 \
  --citation "§decision" \
  --text "All memory access must route through the unified access surface." \
  --evidence-ref "PR#5" \
  --scope project
```

#### 3.2 Automated Document Chunking Crawler (`scripts/ingest_docs.py`)

Create `scripts/ingest_docs.py`:

- Crawls target directories (`sdd/05_ADR/`, `docs/`).
- Splits documents on `##` headings into semantically bounded sections (200–400 tokens max).
- Automatically derives git commit SHA as `evidence_ref`.
- Calls `surface.knowledge_ingest()` for each section.
- Optionally embeds sections via `llm.embed()` and updates `kb_sections.embedding`.

---

### Phase 4: Autonomous Background Distillation Daemon

#### 4.1 Implement Worker Runner (`src/engramory/workers/runner.py`)

Create a background daemon:

- Periodically executes reflection loop (configurable interval, default 300s).
- Dynamically discovers active agents or accepts configured tenant/agent IDs.
- Touches `/tmp/engramory-worker.heartbeat` upon completion of each reflection pass.
- Handles `SIGTERM` and `SIGINT` cleanly for graceful shutdown.
- Adds CLI entrypoint: `engramory worker start`.

---

### Phase 5: Container Packaging & Staging Deployment

#### 5.1 Author Production `Dockerfile`

Create `Dockerfile` in `aidoc-flow-engramory`:

- Multi-stage build based on `python:3.12-slim-bookworm`.
- Creates dedicated non-root user/group `engramory:engramory` (`UID 1004`, `GID 1004`).
- Installs `psycopg[binary]`.
- Installs the engramory package via `pip install --no-cache-dir .`.
- Configures `HEALTHCHECK` inspecting `/tmp/engramory-worker.heartbeat`.
- Default entrypoint: `["engramory", "worker", "start"]`.

#### 5.2 Compose Service Integration

Add `aidoc-engramory-worker` to `compose.platform.yaml` under `UID 1004`:

```yaml
  engramory-worker:
    image: aidoc-engramory-worker:staging
    build:
      context: ../aidoc-flow-engramory
      dockerfile: Dockerfile
    container_name: aidoc-engramory-worker
    user: "1004:1004"
    restart: unless-stopped
    environment:
      - ENGRAMORY_PROFILE=dev
      - ENGRAMORY_REFLECT_INTERVAL_SEC=300
      - LITELLM_BASE_URL=http://aidoc-litellm-proxy:4000/v1
    volumes:
      - /dev/shm:/dev/shm:ro
```

---

## 4. Verification & Acceptance Criteria

1. **Deterministic Gate:** `task check` and `task test:fast` pass with `RESULT: pass`.
2. **Migration Gate:** Migration `0005` applies cleanly to PostgreSQL with 0 errors.
3. **Embedding Gate:** Calling `reflect()` generates populated vector embeddings matching `bge-small-en-v1.5` dimensions.
4. **Retrieval Gate:** LiteLLM ambient pre-call hook queries `v_ambient_memory_active` on staging and successfully receives populated memories in `<15ms`.
5. **Daemon Gate:** `engramory-worker` starts in Docker as `UID 1004`, performs periodic reflection, touches heartbeat, and handles `SIGTERM` gracefully.
