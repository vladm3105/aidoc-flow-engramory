# Engramory — Target Architecture

### Shared Agent Knowledge & Memory Core

*June 22, 2026 · Consolidates RAC + the Nexus v3 design into one platform, adds per-agent distilled memory, runs self-hosted for dev, migrates to GCP or Azure.*

**Naming & positioning.** **Engramory** is the **shared memory + knowledge core** — the persistent, distilled "brain" that all your AI projects build on. Consumer projects (`aidoc-flow`, `iplanic`, `aidoc-flow-operations`, …) plug into Engramory over MCP/API; each is a domain config / client, not a separate stack. The two predecessors — **RAC** (working build) and the **Nexus v3** design — merge *into* Engramory. Throughout this doc, "Nexus v3" refers only to the existing design artifact being absorbed; the go-forward platform is **Engramory**.

---

## What this design has to satisfy

1. **One platform** — merge RAC (working, Neo4j/OpenAI; Trading was just its example domain) and Nexus v3 (domain-agnostic, GCP-native, AGE/Vertex) into a single stack instead of two divergent ones. **Each project becomes a domain config** on the shared core; Trading is one example among many.
2. **Per-agent, distilled, endless memory** — the layer neither project has: each agent accumulates its own knowledge + skills + experience across projects, consolidated over time.
3. **Self-hosted for development (minimize cost), migratable to cloud** — runs on free/OSS containers now; swaps to GCP **or** Azure cloud-native services later without re-architecting.

The third constraint drives everything: **portability is a first-class design goal, not an afterthought.**

---

## Guiding principles

1. **Ports & adapters (hexagonal).** The application core talks to **interfaces** (ports); each infrastructure dependency has swappable **adapters** (self-hosted / GCP / Azure). RAC already does this for storage (local/GCS factory) — generalize it to *every* dependency. This single decision is what makes the platform cloud-migratable.
2. **PostgreSQL is the spine.** Relational + vectors (**pgvector**) + (optionally) graph live in Postgres, which exists *identically* self-hosted, on GCP (Cloud SQL / AlloyDB), and on Azure (Flexible Server). Your canonical data never leaves Postgres, so migration = `pg_dump` + restore.
3. **Own the canonical store; engines are replaceable processors.** Distilled memory lives as plain rows (raw text + provenance + regenerable embeddings) in Postgres — never locked inside a managed memory service. (See [MEMORY_DESIGN.md](MEMORY_DESIGN.md) → Portability.)
4. **One model gateway.** All LLM/embedding calls go through a self-hosted **LiteLLM** proxy (OpenAI-compatible). Dev points it at **Ollama** (free, local); cloud points it at **Vertex AI** or **Azure OpenAI** — a config change, not a code change.
5. **Config over code (from Nexus).** Domains (Trading, Legal, USFS…) are YAML configuration on a domain-agnostic core. RAC becomes the Trading domain config.
6. **Everything containerized.** Docker Compose in dev → the same images on Cloud Run/GKE or Container Apps/AKS in cloud.

---

## Target architecture (logical)

```
┌──────────────────────────────────────────────────────────────────────┐
│  AGENTS / CLIENTS:  Claude Code · Codex · Hermes · custom CLI · Web UI │
└───────────────────────────────┬──────────────────────────────────────┘
                                 │  MCP  (+ REST/WebSocket for UI)
┌────────────────────────────────▼─────────────────────────────────────┐
│  MCP GATEWAY  (unified tool surface — knowledge, memory, ingest, admin)│
└───────────────┬───────────────────────────────────┬──────────────────┘
                │                                   │
┌───────────────▼──────────────┐      ┌──────────────▼───────────────────┐
│  KNOWLEDGE CORE              │      │  MEMORY CORE                      │
│  • ingestion / parsing       │      │  • L1 short-term (project/session)│
│  • semantic + graph search   │      │  • L2 long-term (sem/epi/proc)    │
│  • verification / citations  │      │  • L3 per-agent identity          │
│  • domain config (YAML)      │      │  • distillation + consolidation   │
└───────────────┬──────────────┘      └──────────────┬────────────────────┘
                │                                    │
        ┌───────▼────────────────────────────────────▼────────┐
        │  PORTS (interfaces the core depends on)              │
        │  StoragePort · VectorPort · GraphPort · CachePort    │
        │  LLMPort · SecretsPort · EventsPort · MemoryPort     │
        └───────┬───────────────┬───────────────┬─────────────┘
                │ adapters       │ adapters       │ adapters
        ┌───────▼──────┐ ┌──────▼───────┐ ┌──────▼──────────┐
        │  SELF-HOSTED │ │     GCP      │ │     AZURE        │
        │  (dev)       │ │  (option A)  │ │  (option B)      │
        └──────────────┘ └──────────────┘ └──────────────────┘
```

The MCP gateway means **all your agents (Codex, Claude Code, Hermes, custom)** use the same knowledge + memory through one interface. The ports layer means **the backends swap per environment** with no change to the cores.

---

## The portability matrix (the heart of this design)

Each capability is accessed through a port; here are the adapters per environment.

| Capability (Port) | Self-hosted dev (free/OSS) | GCP | Azure |
|---|---|---|---|
| **Relational + Vector** (`VectorPort`) | PostgreSQL 16 + **pgvector** (Docker) | Cloud SQL / **AlloyDB** for PostgreSQL (pgvector) | Azure Database for PostgreSQL Flexible Server (pgvector) |
| **Graph** (`GraphPort`) | **pure Postgres** (edge tables + recursive CTE) for simple needs; **Neo4j Community** (Docker) when real graph is needed | **Neo4j Aura** (managed, on GCP) or pure-Postgres | **Neo4j Aura** (managed, on Azure) or managed Apache AGE |
| **Object storage** (`StoragePort`) | **MinIO** (S3-compatible) | Cloud Storage (GCS) | Azure Blob Storage |
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

-- Per-agent identity
agent_profiles(agent_id, display_name, standing_preferences jsonb, created_at)

-- Distillation bookkeeping
consolidation_runs(id, agent_id, started_at, finished_at, stats jsonb)
```

(The block above is the memory core. L0 knowledge-base sections live in a separate
`kb_sections` table introduced with the knowledge/BRD-04 cycle — see `sdd/06_SPEC/SPEC-02`.)

Because `content_raw` and `provenance` are always kept and `embedding` is marked regenerable, **the brain survives any model or platform migration** — you re-embed from `content_raw`, you don't re-architect.

### The distillation loop (domain-agnostic generalization of Nexus's Trading learning loop)
- **Per task:** retrieve top-K from L2 (agent + shared) + L1 project state + L0 docs → act → append new episodes to L1.
- **Reflection (async, post-session):** worker reads recent episodes → writes distilled semantic/episodic/procedural memories to L2 under the agent's namespace.
- **Consolidation (periodic):** worker compacts L2 — merge duplicates, generalize repeated lessons, `valid_to`-expire stale facts, prune noise. Keeps L2 dense and high-signal so the *retrieved working set* stays bounded even as the store grows.

**What "endless" means (and doesn't).** The body of memory grows without a fixed cap; consolidation keeps it dense, and each task retrieves only a small, relevant slice. So the model's *working set* is bounded while the *store* is unbounded. "Endless" here is **relevant recall via compression + retrieval — not total recall, not a constant-size store, and not an infinite context window.** The system will sometimes fail to surface a past detail; that is expected behavior, not a defect.

Nexus's existing Trading `learning:` block (`post_trade_review`, `meta_review_frequency`, bias/accuracy tracking) becomes **one domain's configuration of this general engine**, not a bespoke feature.

### Memory processor (the swappable engine on top)
The cores call a `MemoryPort`. Adapter options, all keeping Postgres canonical:
- **Dev / self-host:** **LangMem** (MIT, explicit sem/epi/proc + consolidation) or **Mem0** (Apache-2.0) running against your Postgres; **Cipher** if you want turnkey reflection.
- **Cloud (optional accelerator):** Vertex AI Memory Bank (GCP) / Azure equivalent — used as cache/index only, **never the source of truth**.

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
| **0 — Dev foundation** | Cheap self-hosted base | Docker Compose: Postgres(pgvector) · Redis · MinIO · LiteLLM+Ollama · Keycloak · MCP gateway · worker. Define all Ports. | Free local platform, portable by construction |
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

Provisioned in the Phase-0 compose **today:** `postgres:16` (pgvector) · `redis` · `minio` · `ghcr.io/berriai/litellm` + `ollama` · `keycloak`. **Planned (not yet in docker-compose):** the application services `mcp-gateway` · `knowledge-core` · `memory-core` · `worker` (reflection/consolidation), and observability `grafana`+`loki`+`tempo` (OTel).

All open-source, all free, all with a documented GCP **and** Azure adapter for later. Start here; the cloud is a swap, not a rebuild.

---

*Companion docs: [MEMORY_DESIGN.md](MEMORY_DESIGN.md), [research/NEXUS_V3_REVIEW.md](research/NEXUS_V3_REVIEW.md), [research/RAC_REVIEW.md](research/RAC_REVIEW.md), [research/MEMORY_LANDSCAPE.md](research/MEMORY_LANDSCAPE.md), [research/MEMORY_CONCEPT_REVIEW.md](research/MEMORY_CONCEPT_REVIEW.md). Cloud-service mappings should be re-verified at migration time as managed offerings change.*
