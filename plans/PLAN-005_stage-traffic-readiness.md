# PLAN-005: Staging Traffic Readiness Remediation

> **Status:** Proposed  
> **Date:** 2026-11-01  
> **Authors:** Engramory & LiteLLM Integration Working Group  
> **Owning Repo:** `aidoc-flow-engramory`  
> **Cross-Repo Impact:** `platform-litellm-laya`  
> **Target Environment:** Pre-Production Staging (`100.98.134.10`)  

---

## 1. Executive Summary & Objective

The objective of this plan is to remediate the **5 critical gaps** preventing `aidoc-flow-engramory` from receiving and serving live staging traffic from `platform-litellm-laya`:

1. **Embedding Adapter & Vector Annular Index:** Connect Engramory to LiteLLM's embedding endpoint (`bge-small-en-v1.5`, 384 dims) and create an HNSW vector index in PostgreSQL.
2. **Active Distillation Pipeline:** Update the reflection worker to populate real vector embeddings instead of `NULL` placeholders.
3. **Knowledge & Documentation Ingestion Tooling:** Expose `knowledge ingest` in the `engramory` CLI and provide a bulk ADR/spec crawler to seed institutional memory.
4. **Autonomous Distillation Daemon:** Create a resilient background worker daemon (`src/engramory/workers/runner.py`) to periodically consolidate raw episodes into L2 memories without manual CLI intervention.
5. **Production Containerization:** Provide a production `Dockerfile` and compose service definition for the staging platform namespace (`UID 1004`).

---

## 2. Claim Ledger

| # | Claim | Symbol / Key | Citation |
|---|---|---|---|
| 1 | `LLMPort` protocol defines `chat` and `embed` interfaces | `LLMPort` | `src/engramory/ports/llm.py:13-28` |
| 2 | `dev_adapters(dsn)` provides only `VectorPort` and lacks `llm` adapter | `dev_adapters` | `src/engramory/adapters/factory.py:27-29` |
| 3 | `distillation.py` assigns placeholder model and leaves `embedding = None` | `INTERIM_EMBEDDING_MODEL` | `src/engramory/workers/distillation.py:18-45` |
| 4 | `memories.embedding` column is unconstrained vector with no index | `embedding` | `db/migrations/0001_init_memory.sql:58-76` |
| 5 | `AccessSurface.knowledge_ingest()` requires `evidence_ref` and persists `KBSection` | `knowledge_ingest` | `src/engramory/access/surface.py:216-251` |
| 6 | `cli.py` subparser does not expose `knowledge` subcommand | `_build_parser` | `src/engramory/cli.py:272-319` |
| 7 | Ambient gateway queries `v_ambient_memory_active` view | `v_ambient_memory_active` | `../platform-litellm-laya/ambient_memory_hook.py:214` |

---

## 3. Implementation Phases

### Phase 1: Embedding Adapter & PostgreSQL HNSW Index

#### 1.1 Implement `LiteLLMEmbeddingAdapter`

Create `src/engramory/adapters/dev/llm_litellm.py` implementing `LLMPort`:

- Connects to LiteLLM Proxy `/v1/embeddings` endpoint using standard HTTP (`urllib.request` or `requests` to remain dependency-light).
- Standardizes on model `bge-small-en-v1.5` (384 dimensions).
- Provides retry handling and error wrapping.

```python
# src/engramory/adapters/dev/llm_litellm.py
import json, os, urllib.request
from typing import Sequence

class LiteLLMEmbeddingAdapter:
    def __init__(self, base_url: str | None = None, api_key: str | None = None) -> None:
        self.base_url = (base_url or os.getenv("LITELLM_BASE_URL", "http://localhost:4000/v1")).rstrip("/")
        self.api_key = api_key or os.getenv("LITELLM_API_KEY", "sk-platform-token")
        self.model = os.getenv("ENGRAMORY_EMBED_MODEL", "bge-small-en-v1.5")

    async def embed(self, *, texts: Sequence[str], model: str | None = None) -> Sequence[Sequence[float]]:
        req_data = json.dumps({"model": model or self.model, "input": list(texts)}).encode("utf-8")
        req = urllib.request.Request(
            f"{self.base_url}/embeddings",
            data=req_data,
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {self.api_key}"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return [item["embedding"] for item in data["data"]]
```

#### 1.2 Wire `llm` into `AdapterSet`

Update `src/engramory/adapters/factory.py` to include `llm: LLMPort`.

#### 1.3 Add Migration `0005_pin_embeddings_hnsw.sql`

Create `db/migrations/0005_pin_embeddings_hnsw.sql`:

```sql
-- 0005 — Pin embedding dimensions to 384 (bge-small-en-v1.5) and add HNSW index.
ALTER TABLE memories ALTER COLUMN embedding TYPE vector(384);

CREATE INDEX IF NOT EXISTS idx_memories_embedding_hnsw 
    ON memories USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);
```

Update `tests/test_migrations.py` to assert migration 0005 existence and ordering.

---

### Phase 2: Active Distillation Embedding Pipeline

#### 2.1 Update Reflection Worker

In `src/engramory/workers/distillation.py`:

- Accept an optional `llm: LLMPort` in `reflect()`.
- If `llm` is provided, generate embeddings for `summary` text in batches.
- Persist real 384-dimensional vector embeddings and record `embedding_model = "bge-small-en-v1.5"`.

#### 2.2 Verification

Add integration tests in `tests/integration/test_distillation_embedding.py` asserting that memories produced by `reflect()` carry non-null vectors that can be queried by `v_ambient_memory_active`.

---

### Phase 3: Knowledge & Documentation Ingestion Tooling

#### 3.1 CLI `knowledge ingest` Subcommand

Update `src/engramory/cli.py` to add:

```bash
engramory knowledge ingest \
  --doc-id ADR-010 \
  --citation "§decision" \
  --text "All memory access must route through the unified access surface." \
  --evidence-ref "PR#5" \
  --scope project
```

#### 3.2 Automated Document Crawler (`scripts/ingest_docs.py`)

Create `scripts/ingest_docs.py` to scan directories (e.g. `sdd/05_ADR/` and `docs/`), extract markdown headers and sections, and call `surface.knowledge_ingest()` with automatic git commit evidence hashes.

---

### Phase 4: Autonomous Background Distillation Daemon

#### 4.1 Implement Worker Runner (`src/engramory/workers/runner.py`)

Create a long-running daemon:

- Periodic reflection loop (e.g. every 300 seconds).
- Graceful shutdown signal handlers (`SIGTERM`, `SIGINT`).
- Structured logging and error resilience (never crash on single database timeout).
- CLI command: `engramory worker start`.

---

### Phase 5: Container Packaging & Staging Deployment

#### 5.1 Author Production `Dockerfile`

Create `Dockerfile` in `aidoc-flow-engramory`:

- Multi-stage build based on `python:3.12-slim-bookworm`.
- Installs `psycopg[binary]`.
- Installs the engramory package via `pip install --no-cache-dir .`.
- Default entrypoint: `["engramory", "worker", "start"]`.

#### 5.2 Compose Service Integration

Add `aidoc-engramory-worker` to `compose.platform.yaml` under the `platform` namespace (`UID 1004`):

```yaml
  engramory-worker:
    image: aidoc-engramory-worker:staging
    build:
      context: ../aidoc-flow-engramory
      dockerfile: Dockerfile
    container_name: aidoc-engramory-worker
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
5. **Daemon Gate:** `engramory-worker` starts in Docker, performs periodic reflection, and handles `SIGTERM` gracefully.
