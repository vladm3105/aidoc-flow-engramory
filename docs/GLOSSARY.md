# Glossary

- **Engramory** — the shared memory + knowledge core; the platform this repo builds.
- **Consumer project** — an app that uses Engramory over MCP/API (e.g. aidoc-flow, iplanic, aidoc-flow-operations). Configured as a domain.
- **L0 Knowledge base** — documents/plans/notes with section retrieval and citations.
- **L1 Short-term memory** — current session/project working state.
- **L2 Long-term memory** — distilled semantic / episodic / procedural memory across projects.
- **L3 Agent identity** — per-agent namespace plus the distillation loop.
- **Semantic / Episodic / Procedural** — facts / what-happened / skills (how-to).
- **Distillation (reflection)** — promoting raw episodes into durable long-term memories.
- **Consolidation** — compacting long-term memory (merge, generalize, expire) to stay dense → endless.
- **Port / Adapter** — a vendor-neutral interface / its per-environment implementation.
- **Canonical store** — Postgres; the source of truth. Embeddings and graph are rebuildable projections.
- **Provenance** — where a memory came from (source episodes, project, agent, timestamps).
