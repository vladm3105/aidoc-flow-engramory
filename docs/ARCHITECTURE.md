---
id: DOC-ENGRAMORY-ARCHITECTURE
title: "Engramory Target Architecture & Portability Matrix"
kind: architecture
domain: knowledge-core
namespace: aidoc
status: active
autonomy_tier: green
watched_paths:
  - "src/engramory/ports/storage.py"
  - "src/engramory/ports/graph.py"
  - "src/engramory/ports/vector.py"
  - "src/engramory/access/surface.py"
  - "db/migrations/0001_init_memory.sql"
last_verified: "2026-11-13"
---

# Engramory — Target Architecture

## Shared Agent Knowledge & Memory Operating System

*Agent-first governance and memory/knowledge operating system with hierarchical access control, native PostgreSQL spine, and swappable hexagonal ports.*

**Naming & positioning.** **Engramory** is the **shared memory + knowledge operating system** — the persistent, distilled "brain" and governed technical repository for multi-agent software engineering ecosystems. Consumer projects (`b-local-privy`, `aidoc-flow`, `aidoc-flow-operations`, `iplanic`, …) plug into Engramory over MCP or the `engramory` CLI; each is a project workspace on the shared infrastructure, not a separate siloed stack.

Engramory is designed **agent-first**:

- **Project Executors (Headless Coding Agents):** Run in isolated repository worktrees (e.g. Claude Code, Codex, Cursor in `b-local-privy`), strictly restricted to their own `project_id` and private memory. They cannot inspect or mutate other projects.
- **Operations Assistant (Supervisor Agent / Virtual CTO):** Orchestrates multi-project workflows, reviews code, and enforces standards with cross-project visibility (`domain` and `space` scopes).
- **Near-Real-Time Supervisory Audit Loop:** Every agent action, authorization check (default-deny, fail-closed), and episode streams into PostgreSQL (`audit_records`, `episodes`), enabling the Operations Assistant to monitor and audit worker agents near real-time.
- **Large-Scale Technical Knowledge Base (`kb_sections`):** Natively stores, indexes, and queries deep SDD technical documentation trees (e.g. 160+ markdown files across 10 layers, 17 MB) using `pgvector` dense embeddings and `ts_lex` full-text search with cumulative `@-tag` citation lineage.

---

## What this design satisfies

1. **One unified infrastructure, isolated project access** — A shared PostgreSQL spine with multi-tenant and hierarchical scope control (`agent → project → domain → space`). A single deployment safely serves the global Operations Assistant alongside multiple project-specific executor agents without data leakage.
2. **Per-agent, distilled, endless memory** — Each agent accumulates its own episodic experience, semantic knowledge, and procedural skills across sessions, consolidated over time via reflection.
3. **Large-scale governed technical knowledge** — Native PostgreSQL indexing of massive technical documentation bases (`kb_sections`) with hybrid vector + lexical search, replacing external RAG tools (no LightRAG needed).
4. **Supervisory audit & governance** — Continuous, fail-closed audit stream (`audit_records`) allowing supervisor agents to verify execution health, policy compliance, and tool invocations online.
5. **Self-hosted for development, migratable to cloud** — Runs on free/OSS containers (Docker Compose) in dev; swaps to GCP or Azure cloud-native services via hexagonal ports and adapters without re-architecting.

---

## Guiding principles

1. **Ports & adapters (hexagonal).** The application core talks to **interfaces** (ports); each infrastructure dependency has swappable **adapters** (self-hosted / GCP / Azure). This single decision is what makes the platform cloud-migratable.
2. **PostgreSQL is the spine.** Relational + vectors (**pgvector**) + lexical full-text search (**ts_lex** GIN) + (optionally) graph live in Postgres, which exists *identically* self-hosted, on GCP (Cloud SQL / AlloyDB), and on Azure (Flexible Server). Your canonical data never leaves Postgres, so migration = `pg_dump` + restore.
3. **Own the canonical store; engines are replaceable processors.** Distilled memory and knowledge live as plain rows (raw text + provenance + regenerable embeddings) in Postgres — never locked inside a managed third-party service or opaque RAG framework.
4. **One model gateway.** All LLM/embedding calls go through a self-hosted **LiteLLM** proxy (OpenAI-compatible). Dev points it at **Ollama** (free, local); cloud points it at **Vertex AI** or **Azure OpenAI** — a config change, not a code change.
5. **Config over code.** Domains and projects are declarative configuration on a domain-agnostic core.
6. **Everything containerized.** Docker Compose in dev → the same images on Cloud Run/GKE or Container Apps/AKS in cloud.

---

## Target architecture (logical)

```text
┌────────────────────────────────────────────────────────────────────────────────────────┐
│  MULTI-AGENT FLEET & CLIENTS                                                           │
│  ┌─────────────────────────────────────────┐  ┌──────────────────────────────────────┐ │
│  │ Project Executors (Coding Agents)       │  │ Operations Assistant (Supervisor)    │ │
│  │ • Isolated worktrees (e.g. b-local)     │  │ • Virtual CTO / Fleet Orchestrator   │ │
│  │ • Scope: agent + project                │  │ • Scope: domain + space              │ │
│  └────────────────────┬────────────────────┘  └──────────────────┬───────────────────┘ │
└───────────────────────┼──────────────────────────────────────────┼─────────────────────┘
                        │ MCP / CLI (ActorContext)                 │
┌───────────────────────▼──────────────────────────────────────────▼─────────────────────┐
│  UNIFIED ACCESS SURFACE (AccessSurface: ADR-02, ADR-06, ADR-10, SPEC-01)              │
│  • Default-deny authorization (Scope ladder: agent → project → domain → space)         │
│  • Evidence-backed governed writes for SDD artifacts                                   │
│  • Real-time Audit Sink (audit_records table) for supervisor streaming                 │
└───────────────────────┬──────────────────────────────────────────┬─────────────────────┘
                        │                                          │
        ┌───────────────▼──────────────┐            ┌──────────────▼───────────────────┐
        │  KNOWLEDGE CORE (L0)         │            │  MEMORY CORE (L1–L3)             │
        │  • kb_sections (PostgreSQL)  │            │  • episodes (content hash dedupe)│
        │  • 160+ SDD docs at scale    │            │  • memories (sem / epi / proc)   │
        │  • Dense + Sparse search     │            │  • agent_profiles (L3 identity)  │
        │  • @-tag citation lineage    │            │  • memory_retrievals + feedback  │
        └───────────────┬──────────────┘            └──────────────┬───────────────────┘
                        │                                          │
                ┌───────▼──────────────────────────────────────────▼────────┐
                │  PORTS (Hexagonal Interfaces)                             │
                │  StoragePort · VectorPort · GraphPort · CachePort         │
                │  LLMPort · SecretsPort · EventsPort · MemoryPort          │
                └───────┬───────────────────┬───────────────────┬───────────┘
                        │ adapters          │ adapters          │ adapters
                ┌───────▼──────┐    ┌───────▼───────┐   ┌───────▼──────────┐
                │  SELF-HOSTED │    │     GCP       │   │     AZURE        │
                │  Postgres 18 │    │ Cloud SQL /   │   │ Azure DB Flex    │
                │  + pgvector  │    │ AlloyDB       │   │ (pgvector)       │
                │  PG-CAS/OCI  │    │ GCS / Vertex  │   │ Blob / OpenAI    │
                └──────────────┘    └───────────────┘   └──────────────────┘
```

The unified access surface means **all agents (Project Executors, Operations Assistant, CI runners)** interact through consistent tool contracts. The ports layer means **backends swap per environment** with zero code changes.

The **Knowledge core** and **Memory core** are two *bounded cores of one platform*, not two projects — they share the spine (Postgres, scope/tenant model, gateway, ports) and are separated by schema, write-governance, lifecycle, and tool namespace. See [CORES.md](CORES.md) and [ADR-08](sdd/05_ADR/ADR-08_two_bounded_cores.yaml).

---

## The portability matrix (the heart of this design)

Each capability is accessed through a port; here are the adapters per environment.

| Capability (Port) | Self-hosted dev (free/OSS) | GCP | Azure |
|---|---|---|---|
| **Relational + Vector** (`VectorPort`) | **PostgreSQL 18** + **pgvector** (Docker) | Cloud SQL / **AlloyDB** for PostgreSQL (pgvector) | Azure Database for PostgreSQL Flexible Server (pgvector) |
| **Graph** (`GraphPort`, `Decision D17`) | **PostgreSQL 18** (canonical entities/edges + `1–2` hop CTEs) + **Neo4j 5.26 Community** (`Adopt`, `stage-neo4j` / Docker) as needed for multi-hop Cypher, lineage & GraphRAG | **Neo4j Aura** (managed, on GCP) or pure-Postgres | **Neo4j Aura** (managed, on Azure) or managed Apache AGE |
| **Object & Multimodal Blob storage** (`StoragePort`, `Rule D14a`/`D14b`) | **PostgreSQL 18 Chunked Blob CAS (`PgBlobStorageAdapter` in `TABLESPACE ts_blob_cold` up to `20 GB+`)** or **OCI Object Storage** (`LocalFsBackend` for offline tests; see [MULTIMODAL_AND_POSTGRES_BLOB_STORAGE.md](MULTIMODAL_AND_POSTGRES_BLOB_STORAGE.md)) | Cloud Storage (GCS) or AlloyDB / Cloud SQL Chunked Blob CAS | Azure Blob Storage or Flexible Server Chunked Blob CAS |
| **Cache / session L1** (`CachePort`) | Redis (Docker) | Memorystore for Redis | Azure Cache for Redis |
| **LLM + embeddings** (`LLMPort`) | **LiteLLM → Ollama** (local models) | LiteLLM → **Vertex AI Model Garden** | LiteLLM → **Azure OpenAI / AI Foundry** |
| **Secrets** (`SecretsPort`) | Docker env / SOPS / Infisical | Secret Manager | Azure Key Vault |
| **Identity / auth** (gateway concern, not a core port) | **Keycloak / Authentik** (OIDC) | Identity Platform | Entra ID (Azure AD B2C) |
| **Events** (`EventsPort`) | Redis Streams / NATS | Pub/Sub | Azure Service Bus / Event Grid |
| **Analytics** (optional) | DuckDB / Postgres | BigQuery | Microsoft Fabric / Synapse |
| **Observability** | OpenTelemetry + Grafana/Loki/Tempo | Cloud Logging/Trace (+ OTel) | Azure Monitor / App Insights (+ OTel) |
| **Compute** | Docker Compose | Cloud Run / GKE | Container Apps / AKS |

### The graph engine — Neo4j is the preferred dedicated option (not AGE)

Verified June 2026: **Apache AGE is a managed extension on Azure Database for PostgreSQL, but NOT on GCP Cloud SQL or AlloyDB** (nor AWS RDS). That inverts the usual intuition for a *cloud-undecided* project:

- **AGE** → seamless on **Azure only**; on **GCP** you must self-manage Postgres+AGE (GKE/VM), losing managed convenience. AGE also has a smaller ecosystem and no graph-algorithms library.
- **Neo4j** → managed **Aura is multi-cloud (both GCP and Azure)**, so it is **more portable across "GCP or Azure" than AGE**. It is also RAC's existing choice, a mature graph engine, and well-suited to the **multi-hop reasoning / GraphRAG / entity-timeline** work the knowledge layer needs (native Cypher, GDS algorithms, built-in vector index).

**Decision:**

1. **Default to pure Postgres** for the graph while needs are shallow (entity/edge tables + recursive CTEs over the same pgvector DB) — cheapest dev option, zero extra infra, 100% portable.
2. **Promote to Neo4j** — not AGE — the moment you need real multi-hop traversal, graph algorithms, or GraphRAG. **Neo4j Community (Docker) in dev → Neo4j Aura (GCP or Azure) in cloud.** This keeps a single graph engine across dev and *both* clouds and reuses RAC's proven Neo4j work.
3. **Keep AGE only as a fallback** if you later commit firmly to Azure and want the graph inside Postgres (one backup, travels with `pg_dump`).

**Portability safeguard — treat the graph as a *rebuildable projection*, not a second source of truth.** The canonical entities/relations are derived from documents + extraction results stored in **Postgres**. Neo4j (or AGE) holds an *index* you can always **re-project by re-running entity extraction** — exactly like embeddings. So even though Neo4j is a separate store, migrating or losing it is a rebuild, not data loss, and the "own your store in Postgres" principle holds. (Aura also has native dump/export and multi-cloud migration if you prefer a direct transfer.)

This resolves the RAC↔Nexus divergence cleanly in favor of your existing investment: **Postgres spine for canonical data + vectors; Neo4j as the graph engine when needed, behind `GraphPort`, portable to either cloud via Aura.**

For complete details on the migration, post-migration graph architecture, and sequential data flows, see [Neo4j Migration Architecture](NEO4J_MIGRATION_ARCHITECTURE.md), [Data Flow & Lifecycle](DATA_FLOW_AND_LIFECYCLE.md), and [PLAN-006](../plans/PLAN-006_neo4j-graph-engine-integration.md).

---

## Memory architecture (the part neither RAC nor Nexus has)

Merge Nexus's scope-based memory (Session/Space/User) with the type-based, per-agent model from [MEMORY_DESIGN.md](MEMORY_DESIGN.md). The result keeps Nexus's tiers **and** adds the two missing dimensions: **memory type** and **agent identity**.

### Scoping dimensions (every memory row carries these)

`agent_id` · `project_id` · `domain_id` · `tenant_id` · `scope ∈ {agent, project, domain, space}`

`tenant_id` is the hard isolation boundary; `scope` sets how widely a memory is shared *within* a
tenant — agent → project → domain → space, where `space` = tenant-wide. See ADR-07 + GLOSSARY.

This is the key upgrade: memory is no longer only per-tenant — **each agent has its own namespace**, with project, domain, and tenant-wide sharing above it.

### Layers

| Layer | Lifespan | Backend (dev → cloud) | Notes |
|---|---|---|---|
| **L0 Knowledge base** | permanent | Postgres + object storage | Documents/sections (RAC ingestion + Nexus spaces) |
| **L1 Short-term** | session / project | Redis (session) + Postgres (project working set) | Distilled into L2 at project end |
| **L2 Long-term distilled** | permanent | **Postgres (canonical)** | Three types below |
| **L3 Agent identity + distillation** | permanent, evolving | Postgres + worker | Per-agent profile + reflection/consolidation jobs |

### Canonical memory schema (engine-neutral, lives in your Postgres)

```sql
-- Raw experience (episodic source material)
episodes(id, agent_id, project_id, tenant_id, domain_id, ts, kind, content_raw, metadata jsonb)

-- Distilled long-term memory (the "brain")
memories(
  id, agent_id, project_id, tenant_id, domain_id, scope,
  type,                 -- 'semantic' | 'episodic' | 'procedural'
  content_raw text,     -- source text  (KEEP — enables re-embedding on migration)
  summary   text,       -- distilled form used in prompts
  embedding vector,     -- regenerable; model recorded below
  embedding_model text, embedding_dims int,
  provenance jsonb,     -- source episode ids, project, agent, timestamps
  confidence real,
  valid_from timestamptz, valid_to timestamptz,  -- temporal validity
  supersedes uuid       -- corrections instead of deletes
)

-- Per-agent L3 Virtual Employee identity (composite PK per migration 0009)
agent_profiles(
  tenant_id text,
  agent_id text,
  display_name text,
  standing_preferences jsonb, -- agent_type, role, department_focus, specializations, brain_config
  created_at timestamptz,
  updated_at timestamptz,
  PRIMARY KEY (tenant_id, agent_id)
)

-- Distillation bookkeeping
consolidation_runs(id, agent_id, started_at, finished_at, stats jsonb)
```

(The block above is the memory core across migrations `0001`–`0010`. Migration `0003` adds `episodes.content_hash`, `memories.status/source_trust/ts_lex`, `memory_retrievals`, `audit_records`, and L0 `kb_sections`; migration `0009` scopes `agent_profiles` by `(tenant_id, agent_id)`; migration `0010` adds `kb_blobs`, `kb_blob_chunks`, `kb_blob_chunks_large`, and multimodal columns on `kb_sections`.)

Because `content_raw` and `provenance` are always kept and `embedding` is marked regenerable, **the brain survives any model or platform migration** — you re-embed from `content_raw`, you don't re-architect.

### The distillation loop (domain-agnostic generalization of Nexus's Trading learning loop)

- **Per task:** retrieve top-K from L2 (agent + shared) + L1 project state + L0 docs → act → append new episodes to L1.
- **Reflection (async, post-session):** worker reads recent episodes → writes distilled semantic/episodic/procedural memories to L2 under the agent's namespace.
- **Consolidation (periodic):** worker compacts L2 — merge duplicates, generalize repeated lessons, `valid_to`-expire stale facts, prune noise. Keeps L2 dense and high-signal so the *retrieved working set* stays bounded even as the store grows.

**What "endless" means (and doesn't).** The body of memory grows without a fixed cap; consolidation keeps it dense, and each task retrieves only a small, relevant slice. So the model's *working set* is bounded while the *store* is unbounded. "Endless" here is **relevant recall via compression + retrieval — not total recall, not a constant-size store, and not an infinite context window.** The system will sometimes fail to surface a past detail; that is expected behavior, not a defect.

Nexus's existing Trading `learning:` block (`post_trade_review`, `meta_review_frequency`, bias/accuracy tracking) becomes **one domain's configuration of this general engine**, not a bespoke feature.

### Memory processor & native PostgreSQL spine

Engramory's primary storage and retrieval spine is implemented **natively in PostgreSQL** (migration 0003):

- **Native storage & indexing:** `memories` table with `VECTOR` embeddings, generated `ts_lex` full-text search GIN index, and partial indexes for active valid memories.
- **Native hybrid retrieval:** `kb_sections` (`Repository.search_kb_sections`) executes 3-leg Reciprocal Rank Fusion combining dense vector cosine similarity, sparse full-text `ts_lex` lexical ranking, and substring matching, while `Repository.get_memories` ranks scoped `memories` by cosine distance, substring boost, and recency (`valid_from DESC`) (SPEC-03).
- **Native distillation:** Built-in in-process reflection worker (`engramory memory distill`) projecting raw episodes into dense memories.

The cores define an optional `MemoryPort` for advanced distillation algorithms:

- **Dev / self-host:** The native PostgreSQL reflection worker serves as the default baseline. External processors (**Mem0**, **LangMem**, **Cipher**) can be plugged behind `MemoryPort` for advanced multi-pass compaction without altering canonical data.
- **Cloud accelerator (optional):** Managed services (Vertex AI Memory Bank, Azure equivalents) may act as cache accelerators, **never the source of truth**.

---

## Multi-Agent Operating Model: Executors, Operations Assistant, and Supervisory Audit

Engramory is designed specifically for multi-agent software engineering workflows with strict role and scope separation:

```text
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                        GLOBAL OPERATIONS ASSISTANT                                     │
│                  (Virtual CTO / Meta-Orchestrator / Auditor)                           │
│  • Scopes: domain, space (tenant-wide)                                                 │
│  • Supervises cross-project planning, architecture standards, and code reviews         │
│  • Inspects real-time audit_records stream and cross-project episodes                  │
└──────────────────────────────────────────┬─────────────────────────────────────────────┘
                                           │
                    Fleet Governance & Policy Verification
                                           │
    ┌──────────────────────────────────────┴──────────────────────────────────────┐
    │                                                                             │
┌───▼─────────────────────────────────────┐   ┌───▼─────────────────────────────────────┐
│ PROJECT EXECUTOR A                      │   │ PROJECT EXECUTOR B                      │
│ (Coding Agent in Worktree: Repo A)      │   │ (Coding Agent in Worktree: Repo B)      │
│ • Scopes: agent, project (Repo A only)  │   │ • Scopes: agent, project (Repo B only)  │
│ • Isolated: cannot access Repo B        │   │ • Isolated: cannot access Repo A        │
│ • Appends episodes & reads local KB     │   │ • Appends episodes & reads local KB     │
└───────────────────┬─────────────────────┘   └───────────────────┬─────────────────────┘
                    │                                             │
                    └──────────────────────┬──────────────────────┘
                                           │
                             AccessSurface (Default Deny)
                                           │
                         ┌─────────────────▼─────────────────┐
                         │      CENTRAL POSTGRESQL SPINE     │
                         │ • audit_records (Fail-Closed Log) │
                         │ • episodes (Idempotent Stream)    │
                         │ • kb_sections (Governed SDD Docs) │
                         │ • memories (L1–L3 Brain)          │
                         └───────────────────────────────────┘
```

### 1. Project Executors (Coding Agents in Worktrees)

- **Role:** Autonomous or pair-programming coding agents (e.g. Claude Code, Codex, Cursor) running inside a specific repository worktree (e.g., `/home/ya/dev/b-local/b-local-privy` or `aidoc-flow-operations`).
- **Isolation:** Configured with `scopes = ["agent", "project"]` and their specific `project_id`. The AccessSurface enforces default-deny isolation: a coding agent working on Project A cannot search, read, or overwrite memories or episodes belonging to Project B.
- **Workflow:** At session start, the executor searches project knowledge and lessons (`memory search`); during work, it records durable facts and decisions (`memory add`); upon completing tasks, it closes the loop with feedback (`memory feedback`).

### 2. Operations Assistant (Global Supervisor Agent / Virtual CTO)

- **Role:** Fleet-wide orchestrator, specification reviewer, and virtual CTO.
- **Oversight Scope:** Granted higher scope privileges (`domain` or `space`). The Operations Assistant can inspect across all projects in a domain or tenant, allowing it to:
  - Coordinate multi-repository refactorings and cross-project standards.
  - Review implementation plans (IPLANs) against business and architecture requirements (BRD→ADR).
  - Verify that coding agents adhere to governance guidelines before merge.

### 3. Near-Real-Time Supervisory Audit Loop

- **Streaming Audit Trail:** Every single tool call, authorization attempt, and memory manipulation passes through `AccessSurface.authorize()` and immediately logs an `audit_records` row:
  - `(tenant_id, agent_id, project_id, action, allowed, reason, ts)`
- **Immediate Anomaly Detection:** If an executor agent attempts an out-of-scope query, invalid governed write, or repeats failed tool patterns, the Operations Assistant can observe this in `audit_records` near real-time without needing to poll individual git branches.
- **Continuous Learning:** The `memory_retrievals` table logs which memories were served and subsequent feedback (`useful`, `not_useful`, `harmful`), giving the supervisor immediate visibility into retrieval efficacy.

### 4. Virtual Employee Fleet, Multi-Model Brain Variants, and Portable Brain Passports (`PLAN-024` / `CHG-009`)

Engramory models every deployed agent instance as a **Virtual Employee** (see [VIRTUAL_EMPLOYEE_ARCHITECTURE.md](VIRTUAL_EMPLOYEE_ARCHITECTURE.md)):

- **3-Level Specialist Taxonomy:** Separates `agent_type` (archetype such as `agent-support`, `agent-pm`, `agent-dev`), `agent_id` (named instance such as `agent-support-01..10`), and `specializations` + `domain_id` (`department_focus` such as `billing` with `["stripe", "chargebacks"]`).
- **Experience-Ranked Roster Discovery (`agent_roster_list`):** Supervisors and Layer-1 Stewards filter `agent_profiles` by `domain_id`, `agent_type`, and `specialization` and rank instances by live `mean_confidence DESC, active_skill_count DESC`.
- **Parallel Multi-Model Distillation & A/B Comparison (`brain_variant`, `agent_brain_compare`):** `reflect()` projects the same L1 episodes into 2+ parallel L2 `brain_variant` copies (for example, `claude-sonnet-4-6` vs. `gemini-2.5-pro`) with full `brain_metadata`, evaluated side-by-side via `agent_brain_compare`.
- **Selective Skill Promotion (`memory_promote`):** Elevates validated `scope='agent'` skills to `scope='domain'` (`domain_id`), `scope='project'` (`project_id`), or `scope='space'` under `evidence_ref` governance (`ADR-06`) with cross-instance deduplication.
- **Portable Brain Passports & Tenant Offboarding (`agent_brain_export`, `agent_brain_import`, `agent_space_offboard`):** Exports sanitized L3 profiles and L2 `scope='agent'` skills (`sanitize_portable_value`), seals the payload with `passport_sha256`, enforces `agent_type` compatibility and `1536`-dim vector re-embedding on import, and transactionally retires private memories and private episodes on the source tenant.

---

## Large-Scale Technical Knowledge Base (L0 & `kb_sections`)

Engramory's Knowledge Core is engineered to handle massive, multi-layered technical documentation bases common in AI-First Specification-Driven Development (SDD):

### 1. Document Scale & Multi-Layer SDD

In production repositories like `b-local-privy`, documentation comprises 160+ markdown files spanning 17 MB across 10 SDD layers (`01_BRD` through `08_IPLAN` plus `DECISIONS.md`, `CHANGELOG.md`, `ROADMAP.md`). Traditional RAG tools fail on this scale due to flat chunking, lack of schema context, and loss of document lineage.

### 2. Native PostgreSQL `kb_sections` Schema

Engramory represents technical documentation in the `kb_sections` table:

```sql
kb_sections(
  id UUID PRIMARY KEY,
  tenant_id TEXT NOT NULL,
  project_id TEXT,
  domain_id TEXT,
  scope TEXT NOT NULL,          -- 'project' | 'domain' | 'space'
  doc_id TEXT NOT NULL,         -- e.g. 'BRD-01', 'SPEC-02', 'ADR-07'
  citation TEXT NOT NULL,       -- Section anchor or heading path
  text TEXT NOT NULL,           -- Authoritative section content
  version INTEGER NOT NULL,     -- Immutable versioning (new versions append)
  embedding VECTOR(1536),       -- Regenerable dense embedding (pgvector HNSW)
  embedding_model TEXT,         -- Default: 'text-embedding-3-small'
  embedding_dims INTEGER        -- Default: 1536
)
```

### Standard Vector Dimensions & Default Embedding Model (Migration 0006)

- **Standard Dimensionality (`vector(1536)`):** Engramory standardizes its `pgvector` HNSW indexes on **1,536 dimensions**. This provides near-lossless semantic fidelity while fitting within PostgreSQL HNSW limits (< 2,000 dimensions) and keeping RAM consumption predictable (~6.1 GB for 1M vectors).
- **Default Embedding Model:** `text-embedding-3-small` (`dimensions: 1536`), with optional `openrouter/qwen/qwen3-embedding-8b` (Matryoshka 1536 dims) via LiteLLM proxy configuration.
- **Portability Guarantee:** Standardizing on 1,536 dimensions matches industry standards (OpenAI `text-embedding-3-small`, `text-embedding-3-large`, Voyage AI), enabling seamless model switching in the LiteLLM gateway without requiring SQL schema migrations.

### 3. Hybrid Retrieval & Cumulative `@-tag` Lineage

- **Hybrid Dense + Sparse Search:** Queries against `kb_sections` leverage `pgvector` cosine similarity combined with full-text search over structured headers and section text.
- **Traceability Graph:** SDD documents carry cumulative traceability tags (`@brd`, `@prd`, `@ears`, `@bdd`, `@adr`, `@spec`, `@tdd`, `@iplan`, `@depends`). Engramory models these links to enable lineage and impact queries (e.g., "Find all SPECs and code units impacted by changes to ADR-07").
- **Evidence-Backed Governed Writes (ADR-06 / SPEC-05):** Unlike conversational memories, L0 SDD documentation cannot be modified arbitrarily by coding agents. Writes require an approved evidence reference (`evidence_ref`, such as an approved IPLAN completion record); unauthorized writes fail closed and trigger an audit entry.

---

## Native Storage Spine vs. External RAG (Why LightRAG Is Not Needed)

A common point of confusion is whether Engramory requires external RAG frameworks (such as **LightRAG**, **Zep/Graphiti**, or third-party vector databases). The answer is **no**:

1. **Native PostgreSQL Spine (`pgvector` + `ts_lex`):**
   Engramory implements vector storage, cosine distance matching, lexical GIN indexes, and relational integrity natively inside PostgreSQL. External vector stores or RAG wrappers introduce unnecessary network hops, data duplication, and synchronization failures.
2. **Native Hybrid Rank Fusion:**
   Engramory's `Repository` (SPEC-03) performs 3-leg Reciprocal Rank Fusion (RRF) over `kb_sections` (`embedding` cosine similarity + `ts_lex` BM25 + substring matching) and cosine + substring + recency ranking over `memories`, fused with post-retrieval GraphRAG expansion (`expand_graph=True`).
3. **Graph Projections over Relational Data:**
   Rather than making a graph database the primary source of truth, Engramory stores canonical entities and lineage in PostgreSQL and projects them into Neo4j (via `GraphPort`) only when multi-hop graph algorithms or deep topological traversal are required. The graph is completely rebuildable from PostgreSQL.
4. **Conclusion:**
   Engramory is self-contained. You do not need LightRAG, external vector databases, or hosted RAG services. PostgreSQL provides the complete canonical spine for both knowledge and memory.

---

## Consolidating RAC + Nexus — concrete decisions

| Topic | RAC | Nexus | **Unified decision** |
|---|---|---|---|
| Strategic base | One working build (Trading used as example) | Domain-agnostic | **Engramory core (built on the Nexus v3 design); RAC → the per-project / domain config layer (Trading is just one example project among many)** |
| Graph | Neo4j | Apache AGE | **`GraphPort`; pure-Postgres default → Neo4j (Community→Aura, multi-cloud) when graph needed; AGE only if Azure-committed** |
| LLM/embeddings | OpenAI | Vertex | **`LLMPort` via LiteLLM; Ollama dev → Vertex/Azure cloud** |
| Vector | pgvector | pgvector + Vertex | **pgvector canonical; managed vector search optional** |
| Multi-tenancy | workspaces, per-user MCP | Spaces, 4D auth | **Nexus Spaces + 4D auth; add `agent_id` scope** |
| Secrets | GCP Secret Manager | Secret Manager + Identity Platform | **`SecretsPort`; SOPS/Infisical dev → Secret Manager/Key Vault** |
| Memory | Workspace KV (`learned`) | L1/L2/L3 (per-user) | **Unified L0–L3 + per-agent + distillation (above)** |

---

## Build roadmap

| Phase | Goal | Key work | Outcome |
|---|---|---|---|
| **0 — Dev foundation** | Cheap self-hosted base | Docker Compose: PostgreSQL 18 (`pgvector` + `ts_blob_cold` Chunked Blob CAS) · Valkey/Redis · LiteLLM+Ollama · Neo4j 5.26 (`GraphPort`) · Authentik · MCP gateway · worker. Define all Ports. | Free local platform, portable by construction |
| **1 — Consolidate** | One platform | Port RAC's MCP tools/parsers onto the unified core; RAC becomes the **per-project domain-config layer** (each project = one config; Trading is just one example); everything behind Ports | RAC + Nexus merged, no stack duplication |
| **2 — Cognition** | Per-agent cognition | `agent_id` scoping; L1/L2/L3 schema; reflection + consolidation workers; wire LangMem/Cipher via `MemoryPort` | Per-agent distilled, endless memory |
| **3 — Cloud migration** | GCP or Azure | Swap adapters (managed Postgres, Redis, object store, model endpoint, secrets, identity); IaC; `pg_dump`+object copy; re-embed if model changes | Same app, cloud-native, data intact |

Phases 0–2 are entirely free/self-hosted. Phase 3 is a **configuration + adapter swap**, not a rewrite — that's the payoff of the ports design.

---

## Key risks & mitigations

- **Graph portability (AGE not on GCP managed):** mitigated by pure-Postgres-first + `GraphPort`; pick engine at migration.
- **Embedding model change on migration:** mitigated by keeping `content_raw`; re-embed deterministically.
- **Two stores drifting (knowledge vs memory):** rule — *documents → Knowledge core; distilled experience → Memory core*; cross-link by IDs, don't duplicate.
- **Distillation quality (the hard part):** adopt LangMem/Cipher's consolidation before hand-rolling; tune with provenance so bad lessons are traceable.
- **Cloud lock-in creep:** only managed services that are *adapters behind a port* are allowed; canonical data stays in Postgres/object storage.

---

## Recommended dev stack (Phase 0 docker-compose)

Provisioned in `docker-compose.yml` **today:** `postgres` (PostgreSQL 18 + `pgvector` + `ts_blob_cold` Chunked Blob CAS) · `redis` · `ghcr.io/berriai/litellm` + `ollama` (`--profile local-ai`) · `neo4j` (`neo4j:5.26-community`, `Decision D17`) · `mcp` (MCP gateway, authenticating against Stage Authentik or `--profile legacy-idp` `keycloak`) · `worker` (reflection/consolidation). Optional observability connects via OTel (`docs/OBSERVABILITY.md`).

All open-source, all free, all with a documented GCP **and** Azure adapter for later. Start here; the cloud is a swap, not a rebuild.

---

*Companion docs: [MULTIMODAL_AND_POSTGRES_BLOB_STORAGE.md](MULTIMODAL_AND_POSTGRES_BLOB_STORAGE.md), [MEMORY_DESIGN.md](MEMORY_DESIGN.md), [research/NEXUS_V3_REVIEW.md](research/NEXUS_V3_REVIEW.md), [research/RAC_REVIEW.md](research/RAC_REVIEW.md), [research/MEMORY_LANDSCAPE.md](research/MEMORY_LANDSCAPE.md), [research/MEMORY_CONCEPT_REVIEW.md](research/MEMORY_CONCEPT_REVIEW.md). Cloud-service mappings should be re-verified at migration time as managed offerings change.*
