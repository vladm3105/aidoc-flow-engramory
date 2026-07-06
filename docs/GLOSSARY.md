# Glossary

## Platform

- **Engramory** — the shared memory + knowledge core; the platform this repo builds.
- **Engram** — a single stored unit of distilled memory (a row in `memories`).
- **Consumer project** — an app that uses Engramory over MCP/API (e.g. aidoc-flow, iplanic, aidoc-flow-operations). Runs as a project under a domain config.
- **RAC** — "AI Knowledge RAC", the predecessor knowledge-base build (Neo4j + pgvector, section retrieval, citations). Folds into Engramory as a domain-config layer; not a live separate platform.
- **Nexus v3** — a predecessor domain-agnostic design artifact being absorbed into Engramory; not a running system.
- **MCP** — Model Context Protocol; the tool interface agents use to reach Engramory.
- **Hermes** — one of the agent runtimes in the target fleet (alongside Claude Code, Codex, custom CLI).
- **UCX / ucx_kb** — the SDD framework (Unified Context eXcelerator) and its knowledge base that Engramory's L0 replaces for this ecosystem.

## Memory layers

- **L0 Knowledge base** — documents/plans/notes with section retrieval and citations.
- **L1 Short-term memory** — current session/project working state.
- **L2 Long-term memory** — distilled semantic / episodic / procedural memory across projects.
- **L3 Agent identity** — per-agent namespace plus the distillation loop.
- **Semantic / Episodic / Procedural** — facts / what-happened / skills (how-to).

## Scope and isolation

The scope ladder controls how widely a memory is shared **within a tenant**. From narrowest to
widest: **agent → project → domain → space**. See ADR-07.

- **agent** (scope) — visible only to the owning agent (`agent_id`).
- **project** (scope) — shared by all agents working in one project (`project_id`).
- **domain** (scope) — shared across all projects grouped under one domain (`domain_id`). A domain is a grouping of related projects (e.g. "Trading").
- **space** (scope) — shared tenant-wide: every project and agent in the tenant's common space. This is the top of the ladder (it replaces the earlier `shared` value).
- **tenant** (`tenant_id`) — the hard multi-tenant **isolation boundary**. Nothing crosses tenants; it is a column, not a scope value. (Earlier drafts named this column `space_id`.)

## Processing

- **Distillation (reflection)** — promoting raw episodes into durable long-term memories.
- **Consolidation** — compacting long-term memory (merge, generalize, expire) to stay dense → endless.
- **Quarantine** — marking a low-confidence distilled memory advisory-only (excluded from default retrieval) while keeping its provenance.
- **Governed write** — an SDD-artifact KB write that requires a valid approved-evidence reference (ADR-06); rejected + audited otherwise.

## Portability

- **Port / Adapter** — a vendor-neutral interface / its per-environment implementation.
- **Canonical store** — Postgres; the source of truth. Embeddings and graph are rebuildable projections.
- **Rebuildable projection** — a derived store (vector index, graph) regenerated from canonical text.
- **Provenance** — where a memory came from (source episodes, project, agent, timestamps).
