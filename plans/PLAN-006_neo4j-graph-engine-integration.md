# PLAN-006: Neo4j Graph Engine Integration & Architecture

> **Status:** Completed  
> **Date:** 2026-11-01  
> **Authors:** Engramory Core Team  
> **Owning Repo:** `aidoc-flow-engramory`  
> **Cross-Repo Impact:** `platform-litellm-laya`  
> **Target Environments:** Local Dev Stack & Pre-Production Staging (`100.98.134.10`)  
> **Canonical Decisions:** [ADR-01 (PostgreSQL Spine)](../docs/sdd/05_ADR/ADR-01_postgres_spine.yaml), [ADR-02 (Ports & Adapters)](../docs/sdd/05_ADR/ADR-02_ports_and_adapters.yaml), [ADR-03 (Graph Engine: Neo4j over AGE)](../docs/sdd/05_ADR/ADR-03_graph_engine.yaml), [ADR-05 (Rebuildable Projections)](../docs/sdd/05_ADR/ADR-05_canonical_memory.yaml)

---

## 1. Executive Summary & Objective

The objective of this plan is to introduce **Neo4j Community Edition (CE)** as the dedicated graph engine for Engramory, fulfilling the roadmap progression outlined in **ADR-03**.

Neo4j will enable high-performance multi-hop relationship traversals, entity network exploration, and hybrid GraphRAG (fusing vector similarity with knowledge graphs) across both Knowledge Base (L0) and Episodic Memory (L1/L2).

### Architectural Invariants

1. **PostgreSQL Remains the Single Canonical Source of Truth ([ADR-01](../docs/sdd/05_ADR/ADR-01_postgres_spine.yaml)):**
   * The graph in Neo4j is strictly a **rebuildable projection** ([ADR-05](../docs/sdd/05_ADR/ADR-05_canonical_memory.yaml)).
   * All canonical entities, relationships, lineage, and document source texts reside in PostgreSQL. If the Neo4j database or volume is lost, a background projection worker or migration script can reconstruct the entire graph from PostgreSQL.
2. **Ports & Adapters Isolation ([ADR-02](../docs/sdd/05_ADR/ADR-02_ports_and_adapters.yaml)):**
   * Business core (`core/`, `mcp/`, `workers/`) only depends on [`GraphPort`](../src/engramory/ports/graph.py).
   * The `neo4j` Python driver is restricted exclusively to `src/engramory/adapters/dev/graph_neo4j.py`, enforced by architecture guards ([`test_no_vendor_imports.py`](../tests/unit/test_no_vendor_imports.py)).
3. **Multi-Cloud Portability:**
   * Dev uses Neo4j Community Edition in Docker.
   * Future cloud production environments can seamlessly target Neo4j Aura (GCP or Azure) or managed self-hosted instances without altering domain logic.

---

## 2. Claim Ledger

| # | Claim | Symbol / Key | Citation |
|---|---|---|---|
| 1 | `GraphPort` protocol defines `upsert_node`, `upsert_edge`, and `neighbors` interfaces | `GraphPort` | `src/engramory/ports/graph.py:12-28` |
| 2 | `AdapterSet` dataclass wires ports but lacks a `graph: GraphPort` slot | `AdapterSet` | `src/engramory/adapters/factory.py:18-24` |
| 3 | `dev_adapters(dsn)` initializes only `vector` and `llm` adapters | `dev_adapters` | `src/engramory/adapters/factory.py:27-28` |
| 4 | `VENDOR_ROOTS` architectural guard includes `"neo4j"` as an adapter-only SDK | `VENDOR_ROOTS` | `tests/unit/test_no_vendor_imports.py:21-35` |
| 5 | `docker-compose.yml` draft contains `neo4j:5-community` dormant under `profiles: ["graph"]` | `neo4j` | `docker-compose.yml:79-85` |
| 6 | ADR-03 establishes Neo4j behind `GraphPort` as a rebuildable projection | `ADR-03` | `sdd/05_ADR/ADR-03_graph_engine.yaml:26-44` |
| 7 | ADR-05 specifies rebuildable projections cannot be sources of truth | `ADR-05` | `sdd/05_ADR/ADR-05_rebuildable_projections.yaml:1-40` |
| 8 | Staging host (`100.98.134.10`) runs services on `platform-mesh` rootless docker | `platform-mesh` | `~/devd/platform-litellm-laya/docker-compose.yml:1-50` |

---

## 3. Implementation Phases

### Phase 1: Local Dev Containerization & Dependency Setup

#### 1.1 Add Async Neo4j Driver

* Add official `neo4j` package via package manager:

  ```bash
  uv add "neo4j>=5.26.0"
  ```

* Ensure version compatibility with Python 3.12 and `asyncio` event loop.

#### 1.2 Update Local Docker Compose (`docker-compose.yml`)

* Pin image to `neo4j:5.26-community` (LTS stability).
* Provide safe heap and pagecache memory constraints (initial 512MB heap, max 1GB heap, 512MB pagecache) to prevent container ballooning.
* Enable APOC core plugin (`NEO4J_PLUGINS='["apoc"]'`).
* Provide non-blocking healthcheck on HTTP port 7474.
* Support default startup without requiring manual `--profile graph` flags, or integrate into primary dev services.

---

### Phase 2: Asynchronous `Neo4jGraphAdapter` Implementation

#### 2.1 Adapter Implementation (`src/engramory/adapters/dev/graph_neo4j.py`)

Implement `Neo4jGraphAdapter` conforming to `GraphPort`:

* Use `neo4j.AsyncGraphDatabase.driver(uri, auth=(user, password))` for non-blocking I/O.
* Support connection pooling, automatic retries with exponential backoff on transient connection failures, and explicit resource cleanup (`close()`).
* Implement required methods:
  * `upsert_node(id, labels, props)`: Parameterized Cypher query merging entity node and setting labels/properties.
  * `upsert_edge(src, dst, rel, props)`: Parameterized Cypher query creating directed relation between source and destination nodes.
  * `neighbors(id, rel, depth)`: Parameterized multi-hop traversal returning reachable subgraph nodes and relation attributes up to `depth`.
* Provide schema bootstrapping helper to initialize uniqueness constraints:

  ```cypher
  CREATE CONSTRAINT entity_id_unique IF NOT EXISTS
  FOR (n:Entity) REQUIRE n.id IS UNIQUE;
  ```

#### 2.2 Factory Integration (`src/engramory/adapters/factory.py`)

* Add `graph: GraphPort` field to `AdapterSet`.
* In `dev_adapters(dsn)`:
  * Read `NEO4J_URI` (default: `bolt://localhost:7687`), `NEO4J_USER` (default: `neo4j`), and `NEO4J_PASSWORD` (default: `change_me`).
  * Wire `graph=Neo4jGraphAdapter(uri, auth=(user, password))` into `AdapterSet`.

---

### Phase 3: Canonical Entity & Lineage Model in PostgreSQL

#### 3.1 PostgreSQL Entities and Relations Schema (`db/migrations/0007_graph_entities_canonical.sql`)

To preserve the invariant that Postgres is the single source of truth:

* Create `graph_entities` table:
  * `id TEXT PRIMARY KEY`
  * `tenant_id TEXT NOT NULL`
  * `domain_id TEXT NOT NULL`
  * `entity_type TEXT NOT NULL`
  * `name TEXT NOT NULL`
  * `properties JSONB NOT NULL DEFAULT '{}'::jsonb`
  * `created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()`
  * `updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()`
* Create `graph_relations` table:
  * `id UUID PRIMARY KEY DEFAULT gen_random_uuid()`
  * `tenant_id TEXT NOT NULL`
  * `src_id TEXT NOT NULL REFERENCES graph_entities(id) ON DELETE CASCADE`
  * `dst_id TEXT NOT NULL REFERENCES graph_entities(id) ON DELETE CASCADE`
  * `rel_type TEXT NOT NULL`
  * `properties JSONB NOT NULL DEFAULT '{}'::jsonb`
  * `created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()`
* Add indices on `(tenant_id, entity_type)`, `src_id`, and `dst_id`.

#### 3.2 Graph Projection & Replay Worker

* Implement projection service (`src/engramory/workers/graph_projection.py`):
  * Listens to / scans new canonical entity and relation rows in PostgreSQL.
  * Projects them to Neo4j via `GraphPort`.
  * Provides a CLI command `engramory graph reproject` to rebuild the entire Neo4j database from PostgreSQL from scratch (idempotent reconciliation).

---

### Phase 4: Knowledge Base & Memory Graph Integration

#### 4.1 Knowledge Base ADR & Document Graphing

* Extract structured dependency graphs from ingested ADRs and specs:
  * Nodes: ADRs (`ADR-01`, `ADR-02`, ...), Specs (`SPEC-01`, ...), Concepts.
  * Edges: `DEPENDS_ON`, `IMPLEMENTS`, `CONSTRAINS`, `SUPERSEDES`.
* Ingest into canonical PostgreSQL tables and project to Neo4j.

#### 4.2 Episodic Memory Graphing (Distillation Pass)

* During the `reflect()` distillation pass in `src/engramory/workers/distillation.py`:
  * When distilling episodes, optionally extract key entity triplets (`Subject -[PREDICATE]-> Object`).
  * Persist to PostgreSQL `graph_entities` / `graph_relations` and project to Neo4j.

#### 4.3 Hybrid GraphRAG Retrieval in `AccessSurface`

* Enhance `AccessSurface.memory_search`:
  * Step 1: Semantic vector search finds top-k relevant anchor sections / memories.
  * Step 2: Neo4j graph traversal (`GraphPort.neighbors(anchor_id, depth=2)`) discovers connected concepts, dependencies, and upstream constraints.
  * Step 3: Fused results are deduplicated and formatted within the token budget.

---

### Phase 5: Pre-Production Staging Deployment (`100.98.134.10`)

#### 5.1 OpenBao Credential Provisioning

* Store staging credentials in OpenBao:
  * Path: `kv/stage/engramory/neo4j`
  * Keys: `NEO4J_USER`, `NEO4J_PASSWORD`.

#### 5.2 Staging Docker Compose Deployment

* Add `stage-neo4j` service to staging stack running under platform user UID 1004.
* Connect to `platform-mesh` network.
* Persist data to named volume `stage_neo4jdata`.
* Configure resource limits (CPU: 2 cores, RAM: 2GB).

#### 5.3 Staging Verification

* Verify Bolt endpoint accessibility from `stage-postgres18` and `engramory-worker`.
* Ingest ADR knowledge graph on stage and execute end-to-end traversal tests.

---

### Phase 6: Documentation & Runbooks

Deliver complete documentation covering:

1. **Migration & Post-Migration Architecture Guide (`docs/NEO4J_MIGRATION_ARCHITECTURE.md`):**
   * Architectural topology and data flow diagram.
   * Dual-store contract: Postgres canonical vs Neo4j rebuildable projection.
   * Failure recovery: full re-projection procedure from Postgres.
   * Graph schema standards (node label conventions, relationship casing, property schemas).
2. **Architecture Documentation Update (`docs/ARCHITECTURE.md`):**
   * Update Graph section from "planned / pure Postgres default" to "implemented via Neo4j CE adapter".
3. **ADR-03 Status & Lifecycle Update (`sdd/05_ADR/ADR-03_graph_engine.yaml`):**
   * Update status and implementation records reflecting the completion of the Neo4j adapter.
4. **Operations Runbook (`docs/runbooks/neo4j_ops.md`):**
   * Backup/restore procedures, memory tuning, APOC configuration, and health monitoring.

---

## 4. Verification & Testing Matrix

| Level | Test Scope | Verification Method | Pass Criteria |
|---|---|---|---|
| **Unit** | `Neo4jGraphAdapter` mocked Cypher execution | `pytest tests/unit/test_graph_neo4j.py` | 100% pass, no vendor leaks into core |
| **Architecture** | Architectural boundaries & vendor imports | `pytest tests/unit/test_no_vendor_imports.py` | `neo4j` SDK strictly isolated to `adapters/dev/` |
| **Integration** | Local Neo4j container CRUD & traversal | `pytest tests/integration/test_graph_integration.py` | Node/edge upsert, multi-hop `neighbors()` accuracy |
| **Rebuildability** | Full re-projection from PostgreSQL | Execute `engramory graph reproject` on wipe | 100% graph nodes and edges restored |
| **Hybrid Retrieval**| GraphRAG search in `AccessSurface` | Semantic query expanding connected ADRs | Retrieved context includes 1-hop & 2-hop graph relations |
| **Staging Gate** | Live Bolt connectivity & query execution | Staging smoke script against `100.98.134.10:7687` | P95 query latency < 15ms |

---

## 5. Risk Assessment & Reversibility

| Risk | Impact | Likelihood | Mitigation |
|---|---|---|---|
| **Dual-write inconsistency** between Postgres & Neo4j | High | Low | Never write directly to Neo4j from business code; all mutations commit to PostgreSQL first, followed by projection write (outbox / projection worker). |
| **Neo4j memory exhaustion** in dev/staging | Medium | Low | Explicitly pin heap (`512m` initial, `1g` max) and pagecache (`512m`) in compose. |
| **Data loss on Neo4j volume crash** | Low | Low | Zero data loss risk: graph is a rebuildable projection. Running `reproject` rebuilds the graph from PostgreSQL. |
| **Vendor lock-in** | Low | Low | Core code only sees `GraphPort`. Can swap back to PostgreSQL CTEs or migrate to Neo4j Aura without touching core business logic. |
