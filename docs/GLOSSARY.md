# Glossary

## Platform

- **Engramory** — the shared memory + knowledge operating system; the platform this repo builds.
- **Engram** — a single stored unit of distilled memory (a row in `memories`).
- **Consumer project** — a software project that connects to Engramory via MCP or CLI (e.g. `b-local-privy`, `aidoc-flow`, `aidoc-flow-operations`, `iplanic`).
- **MCP** — Model Context Protocol; the authenticated remote tool interface agents use to reach Engramory.
- **UCX / ucx_kb** — the SDD framework (Unified Context eXcelerator) and its predecessor knowledge base that Engramory's L0 replaces.

## Multi-Agent Roles & Hierarchy

- **Project Executor (Coding Agent)** — an autonomous or pair-programming agent running in an isolated repository worktree (e.g. Claude Code or Codex in `b-local-privy`). Restricted to `agent` and `project` scope; cannot access other projects.
- **Operations Assistant (Supervisor Agent / Virtual CTO)** — a meta-orchestrator and governance agent with fleet-wide visibility (`domain` and `space` scopes). Supervises multi-project workflows, validates architectural compliance, and audits executions.
- **Supervisory Audit Stream (`audit_records`)** — the continuous, fail-closed log of all authorization decisions, tool invocations, and memory operations recorded in PostgreSQL. Monitored near real-time by the supervisor agent.
- **ActorContext** — the validated security context (`agent_id`, `project_id`, `tenant_id`, `scopes`) accompanying every call to `AccessSurface`.

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

- **Distillation (reflection)** — promoting raw episodes into durable long-term memories.
- **Consolidation** — compacting long-term memory (merge, generalize, expire) to keep retrieved working sets dense and bounded.
- **Quarantine** — marking a low-confidence distilled memory advisory-only (excluded from default retrieval) while keeping its provenance.
- **Feedback Loop (`memory_retrievals`)** — logging retrieval outcomes (`useful`, `not_useful`, `harmful`) to tune memory confidence scores dynamically.

## Portability & Architecture

- **Port / Adapter** — a vendor-neutral interface (`engramory.ports`) / its per-environment implementation (self-hosted, GCP, Azure).
- **Canonical store** — PostgreSQL; the sole system-of-record. Embeddings and graphs are rebuildable projections.
- **Rebuildable projection** — a derived index (vector index, Neo4j graph) that can be regenerated from canonical text in PostgreSQL.
- **Provenance** — immutable lineage tracing where a memory originated (source episode IDs, project, agent, timestamp).
