# Glossary

## Platform

- **Engramory** — the shared memory + knowledge operating system; the platform this repo builds.
- **Engram** — a single stored unit of distilled memory (a row in `memories`).
- **Consumer project** — a software project that connects to Engramory via MCP or CLI (e.g. `b-local-privy`, `aidoc-flow`, `aidoc-flow-operations`, `iplanic`).
- **MCP** — Model Context Protocol; the authenticated remote tool interface agents use to reach Engramory.
- **UCX / ucx_kb** — the SDD framework (Unified Context eXcelerator) and its predecessor knowledge base that Engramory's L0 replaces.

## Multi-Agent Roles & Hierarchy

- **Virtual Employee** — a named, stateful agent instance (`agent_id`, e.g. `agent-support-01`) scoped by `(tenant_id, agent_id)` with its own L3 `AgentProfile`, private L2 `scope='agent'` skill store, department focus (`domain_id`), and technical `specializations` (see [VIRTUAL_EMPLOYEE_ARCHITECTURE.md](VIRTUAL_EMPLOYEE_ARCHITECTURE.md)).
- **Agent Archetype (`agent_type`) vs. Instance (`agent_id`)** — `agent_type` identifies the agent class (`agent-support`, `agent-pm`, `agent-dev`), whereas `agent_id` identifies a specific deployed instance (`agent-support-01..10`) within that archetype.
- **Specializations (`specializations`)** — normalized domain/skill tags (e.g. `["stripe", "chargebacks", "oidc"]`) stored on L3 `agent_profiles` and L2 `memories.provenance` to enable specialist routing (`agent_roster_list`) and narrow retrieval (`memory_search(specialization=...)`).
- **Project Executor (Coding Agent)** — an autonomous or pair-programming agent running in an isolated repository worktree (e.g. Claude Code or Codex in `b-local-privy`). Restricted to `agent` and `project` (or assigned `domain`) scope; cannot access other projects.
- **Operations Assistant (Supervisor Agent / Virtual CTO)** — a meta-orchestrator and governance agent with fleet-wide visibility (`domain` and `space` scopes). Supervises multi-project workflows, validates architectural compliance, and audits executions.
- **Supervisory Audit Stream (`audit_records`)** — the continuous, fail-closed log of all authorization decisions, tool invocations, and memory operations recorded in PostgreSQL. Monitored near real-time by the supervisor agent.
- **ActorContext** — the validated security context (`agent_id`, `project_id`, `tenant_id`, `scopes`, and optional `domain_id`) accompanying every call to `AccessSurface`.

## Cores

Engramory is one platform with two **bounded cores** (see [CORES.md](CORES.md), ADR-08):

- **Memory core** — experiential, agent-authored, distilled: L1 short-term, L2 long-term (cross-project), L3 agent identity.
- **Knowledge core** — curated documents/sections (L0); governed, citable, versioned (`kb_sections`).
- Both share the spine (Postgres canonical, scope/tenant model, MCP gateway, ports); separated by schema, write-governance, lifecycle, and tool namespace.

## Memory Layers

- **L0 Knowledge base** — governed documentation, specifications, and architecture decision records (`kb_sections`) with section-level citations.
- **L1 Short-term memory** — current session/project working state (`episodes`).
- **L2 Long-term memory** — distilled semantic / episodic / procedural memory across projects (`memories`).
- **L3 Agent identity** — per-agent profile (`agent_profiles`) plus reflection/distillation configuration.
- **Semantic / Episodic / Procedural** — facts / what happened and when / reusable skills and playbooks.

## Scope and Isolation

The scope ladder controls how widely a memory or document is shared **within a tenant**. From narrowest to widest: **agent → project → domain → space**. See ADR-07.

- **agent** (scope) — visible only to the owning agent (`agent_id`).
- **project** (scope) — shared by all agents working in one project (`project_id`).
- **domain** (scope) — shared across all projects grouped under one domain (`domain_id`).
- **space** (scope) — shared tenant-wide: every project and agent in the tenant's common space. Top of the ladder.
- **tenant** (`tenant_id`) — the hard multi-tenant **isolation boundary**. Nothing crosses tenants; it is a column, not a scope value.

## Knowledge & Retrieval

- **KB Section (`kb_sections`)** — canonical relational table storing chunked/versioned technical documentation with dense vector embeddings (`pgvector`) and citation anchors.
- **Hybrid Retrieval (Rank Fusion)** — weighted Reciprocal Rank Fusion combining dense vector cosine similarity, sparse lexical search (`ts_lex` GIN index), recency, and source-trust multipliers (SPEC-03).
- **Traceability Graph** — graph of cumulative `@-tags` (`@brd`, `@prd`, `@ears`, `@bdd`, `@adr`, `@spec`, `@tdd`, `@ip-lan`, `@depends`) modeling upstream/downstream lineage across SDD layers.
- **Governed Write** — an SDD-artifact KB write requiring an approved evidence reference (ADR-06); rejected and audited otherwise.

## Processing & Memory Lifecycle

- **Distillation (reflection)** — promoting raw episodes into durable long-term memories (`reflect()`), optionally across multiple parallel `brain_variant` model configurations.
- **Brain Variant (`brain_variant`) & `brain_metadata`** — named model/RAG configuration (`distiller_model`, `extraction_model`, `embedding_model`, `rag_params`) stamped on L2 `memories.provenance` and benchmarked side-by-side via `agent_brain_compare`.
- **Selective Promotion (`memory_promote`)** — governed (`evidence_ref`) elevation of a validated memory up the `ADR-07` scope ladder (`agent → project | domain | space`) with cross-instance deduplication.
- **Brain Passport (`engramory.brain_passport.v1`)** — cryptographically sealed (`passport_sha256`), PII-scrubbed JSON bundle exported by `agent_brain_export` and imported via `agent_brain_import` (with `agent_type` compatibility check and `1536`-dim vector re-embedding).
- **Tenant Offboarding (`agent_space_offboard`)** — transactional cleanup that retires private `scope='agent'` memories, purges private `scope='agent'` episodes, and deletes the L3 `agent_profiles` row on a source tenant while preserving shared project/domain artifacts.
- **Consolidation** — compacting long-term memory (merge, generalize, expire) to keep retrieved working sets dense and bounded.
- **Quarantine** — marking a low-confidence distilled memory advisory-only (excluded from default retrieval) while keeping its provenance.
- **Feedback Loop (`memory_retrievals`)** — logging retrieval outcomes (`useful`, `not_useful`, `harmful`) to tune memory confidence scores dynamically.

## Portability & Architecture

- **Port / Adapter** — a vendor-neutral interface (`engramory.ports`) / its per-environment implementation (self-hosted, GCP, Azure).
- **Canonical store** — PostgreSQL 18; the sole system-of-record. Embeddings and graphs are rebuildable projections.
- **PostgreSQL 18 Chunked Blob CAS (`kb_blobs`)** — content-addressable `4 MiB` chunked storage (`kb_blob_chunks` `LOGGED` `<= 50 MiB` and `kb_blob_chunks_large` `UNLOGGED` `> 50 MiB` up to `20 GB+`) backing `PostgresBlobStorageAdapter`.
- **Rebuildable projection** — a derived index (vector index, Neo4j graph) that can be regenerated from canonical text in PostgreSQL.
- **Provenance** — immutable lineage tracing where a memory originated (source episode IDs, project, agent, `brain_variant`, `specializations`, timestamp).
