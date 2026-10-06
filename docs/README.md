# Engramory Documentation

**Engramory** is an **agent-first governance and memory/knowledge operating system** designed for multi-agent software engineering environments. It provides persistent episodic, semantic, and procedural memory (L1–L3) alongside a governed, large-scale technical knowledge base (L0).

Engramory enforces **hierarchical access control** (`agent → project → domain → space` within a hard `tenant_id` boundary):

- **Project Executors (Coding Agents):** Run in isolated repository worktrees, bounded to their own `project_id` and private memory.
- **Operations Assistant (Supervisor Agent / Virtual CTO):** Has fleet-wide visibility across domains and spaces, monitoring executor actions near real-time via Postgres audit streams.
- **Native Storage Spine:** Native PostgreSQL with `pgvector` for dense semantic embeddings, `ts_lex` GIN full-text search, relational metadata, and graph projections (no external RAG dependencies like LightRAG).

## Documentation Index

| Doc | Purpose |
|---|---|
| [ARCHITECTURE.md](ARCHITECTURE.md) | Target architecture, multi-agent operating model, SQL schema, hybrid retrieval, and portability matrix |
| [CORES.md](CORES.md) | The two bounded cores (Memory & Knowledge), access hierarchy, audit loop, and shared spine |
| [STRATEGY.md](STRATEGY.md) | Build strategy & recommendation: native PostgreSQL spine, swappable processors, and evaluation-led delivery |
| [INSTALL.md](INSTALL.md) | Dev-tier environment setup, Docker Compose stack, migrations, and CLI verification |
| [AGENT-QUICKSTART.md](AGENT-QUICKSTART.md) | Step-by-step guide for AI agents to remember, retrieve, and close the feedback loop |
| [AGENT-INTEGRATION.md](AGENT-INTEGRATION.md) | Vendor-neutral integration guide (Claude, Gemini, Codex, Copilot, Cursor) |
| [MEMORY_DESIGN.md](MEMORY_DESIGN.md) | Layered cognitive memory model (L0–L3), distillation loop, and engine portability analysis |
| [PORTABILITY.md](PORTABILITY.md) | Self-hosted → GCP/Azure principles, hexagonal ports, and migration rules |
| [HOW_TO_USE_THE_FRAMEWORK.md](HOW_TO_USE_THE_FRAMEWORK.md) | Authoring SDD artifacts the UCX / aidoc-flow way (8-layer lifecycle) |
| [UCX_INTEGRATION.md](UCX_INTEGRATION.md) | Ecosystem integration: replacing `ucx_kb`, Operations Assistant supervision, and cumulative `@`-tag lineage |
| [GLOSSARY.md](GLOSSARY.md) | Definitive terminology (executors, supervisor, audit loop, hybrid retrieval, scopes) |
| [adr/](adr/) | Conceptual/descriptive architecture decision records |
| [research/](research/) | Historical landscape reviews, benchmark evaluations, and memory-tool critiques |

The canonical SDD lifecycle artifacts (BRD→IPLAN) live in the [`sdd/`](../sdd/) tree; [`sdd/05_ADR/`](../sdd/05_ADR/ADR-00_index.md) holds the canonical implementing ADRs. Canonical roadmap is maintained at [`roadmap/ROADMAP.md`](../roadmap/ROADMAP.md).
