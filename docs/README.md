# Engramory Documentation

**Engramory** is an **agent-first governance and memory/knowledge operating system** designed for multi-agent software engineering environments. Engramory provides persistent episodic, semantic, and procedural memory (L1–L3) alongside a governed, multimodal technical knowledge base (L0).

Engramory enforces **hierarchical access control** (`agent → project → domain → space` within a hard `tenant_id` boundary):

- **Virtual Employee Specialist Fleet (`agent_type` $\to$ `agent_id` $\to$ `specializations` & `domain_id`):** Multi-instance specialist fleets (for example, `10 agent-support`, `5 agent-pm`, and `5 agent-dev` instances per tenant) maintain isolated private L2 skills (`scope='agent'`), collaborate in shared project spaces (`scope='project'`) and department spaces (`scope='domain'`), distill parallel multi-model brain variants (`brain_variant`), and export/import sanitized **Brain Passports** (`engramory.brain_passport.v1` with `passport_sha256` integrity and `agent_type` guards).
- **Project Executors (Coding Agents):** Run in isolated repository worktrees, bounded to their assigned `project_id`, `domain_id`, and private `agent_id` memory.
- **Operations Assistant (Supervisor Agent / Virtual CTO):** Holds fleet-wide visibility across domains and spaces, ranks specialists via `agent_roster_list`, compares brain variants via `agent_brain_compare`, and monitors executor actions via PostgreSQL `audit_records`.
- **Native PostgreSQL 18 Spine:** Native PostgreSQL 18 with `pgvector` (`vector(1536)` HNSW), `ts_lex` GIN full-text search, `kb_blobs` / `kb_blob_chunks_large` `20 GB+` chunked blob CAS, and rebuildable Neo4j / PostgreSQL CTE GraphRAG projections.

## Documentation Index

| Doc | Purpose |
|---|---|
| [VIRTUAL_EMPLOYEE_ARCHITECTURE.md](VIRTUAL_EMPLOYEE_ARCHITECTURE.md) | Virtual Employee hierarchy, multi-instance specialist taxonomy, multi-model `brain_variant` benchmarking, `memory_promote`, and portable Brain Passports |
| [MULTIMODAL_AND_POSTGRES_BLOB_STORAGE.md](MULTIMODAL_AND_POSTGRES_BLOB_STORAGE.md) | Multimodal RAG (`PDF`/`DOCX`/Image/Audio/Video), dual-engine GraphRAG, and PostgreSQL 18 `20 GB+` Chunked Blob CAS |
| [DATA_FLOW_AND_LIFECYCLE.md](DATA_FLOW_AND_LIFECYCLE.md) | Data flow, document lifecycle, Virtual Employee & GraphRAG sequences, and CRUD tool audit |
| [NEO4J_MIGRATION_ARCHITECTURE.md](NEO4J_MIGRATION_ARCHITECTURE.md) | Neo4j graph engine architecture, dual-store contract, and rebuildable projection |
| [runbooks/neo4j_ops.md](runbooks/neo4j_ops.md) | Neo4j operations runbook, memory tuning, health verification, and disaster recovery |
| [ARCHITECTURE.md](ARCHITECTURE.md) | Target architecture, multi-agent operating model, SQL schema, hybrid retrieval, and portability matrix |
| [CORES.md](CORES.md) | The two bounded cores (Memory & Knowledge), access hierarchy, audit loop, and shared spine |
| [STRATEGY.md](STRATEGY.md) | Build strategy & recommendation: native PostgreSQL spine, swappable processors, and evaluation-led delivery |
| [INSTALL.md](INSTALL.md) | Dev-tier environment setup, Docker Compose stack, migrations, and CLI verification |
| [AGENT-QUICKSTART.md](AGENT-QUICKSTART.md) | Step-by-step guide for AI agents to remember, retrieve, promote skills, and close the feedback loop |
| [AGENT-INTEGRATION.md](AGENT-INTEGRATION.md) | Vendor-neutral integration guide (Claude, Gemini, Codex, Copilot, Cursor) |
| [OBSERVABILITY.md](OBSERVABILITY.md) | OpenTelemetry tracing, structured JSON logging, and `/health` + `/healthz` probes |
| [SDD_KNOWLEDGE_GRAPH_ARCHITECTURE.md](SDD_KNOWLEDGE_GRAPH_ARCHITECTURE.md) | 10-layer SDD knowledge graph, context slicing, and blast-radius analysis |
| [doc-tools/00_INDEX.md](doc-tools/00_INDEX.md) | Governed documentation & executable runbook toolchain specification |
| [MEMORY_DESIGN.md](MEMORY_DESIGN.md) | Layered cognitive memory model (L0–L3), distillation loop, and engine portability analysis |
| [PORTABILITY.md](PORTABILITY.md) | Self-hosted → GCP/Azure principles, hexagonal ports, and Brain Passport portability rules |
| [HOW_TO_USE_THE_FRAMEWORK.md](HOW_TO_USE_THE_FRAMEWORK.md) | Authoring SDD artifacts the UCX / aidoc-flow way (8-layer lifecycle) |
| [UCX_INTEGRATION.md](UCX_INTEGRATION.md) | Ecosystem integration: replacing `ucx_kb`, Operations Assistant supervision, and cumulative `@`-tag lineage |
| [GLOSSARY.md](GLOSSARY.md) | Definitive terminology (Virtual Employees, Brain Passports, executors, supervisor, hybrid retrieval, scopes) |
| [adr/](adr/) | Conceptual/descriptive architecture decision records |
| [research/](research/) | Historical landscape reviews, benchmark evaluations, and memory-tool critiques |

The canonical SDD lifecycle artifacts (BRD→CHG) live in the [`sdd/`](sdd/) tree; [`sdd/05_ADR/`](sdd/05_ADR/ADR-00_index.md) holds the canonical implementing ADRs. Canonical roadmap is maintained at [`docs/sdd/08_IPLAN/IPLAN-00_index.yaml`](sdd/08_IPLAN/IPLAN-00_index.yaml).
