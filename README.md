# platform-engramory

## Platform Engramory — Shared Memory, Knowledge & Documentation Operating System

**Shared agent knowledge, memory & executable documentation core.** Engramory is the persistent, distilled "brain" and L0 documentation/runbook engine that the platform (`platform-*`), `aiops-*`, and `aidoc-*` ecosystem builds on — giving agents long-term memory (knowledge, skills, and experience) plus verified operational playbooks and drift governance across sessions, projects, and platforms.

> *Engramory* = **engram** (a stored memory trace) + **memory**. One brain, many projects.

---

## What it is

Engramory is the **domain-agnostic memory + knowledge plane** of the **aidoc-flow / UCX ecosystem**, consumed over **MCP** (and REST/WebSocket) by any AI agent — Claude Code, Codex, Hermes, or custom CLI agents. It **replaces `ucx_kb`** as the framework's knowledge base (RAG + Graph) and adds per-agent distilled memory on top.

Where it sits in the ecosystem:

| Plane | Component | Role |
|---|---|---|
| Control plane | `aidoc-flow-framework` (UCX) | SDD lifecycle BRD→IPLAN; Hermes orchestration; lifecycle gates are source of truth |
| Execution plane | `iplan-runner` → `iplanic` | Approved IPLAN → ledger → gate → monitor; system-of-record |
| **Memory + knowledge plane** | **Engramory** (this repo) | Shared KB + per-agent distilled memory; **replaces `ucx_kb`** |
| Consumer projects | `aidoc-flow`, `aidoc-flow-operations`, `AI Operations Intelligence`, … | Build on Engramory as a thin domain config / client |

It consolidates two predecessor designs — **RAC** (a working knowledge platform) and the **Nexus v3** design — and adds the layer neither had: **per-agent, distilled, endless memory**. See [docs/UCX_INTEGRATION.md](docs/UCX_INTEGRATION.md) for the `ucx_kb` replacement gap analysis.

## What it gives agents

| Capability | Description |
|---|---|
| **Knowledge base (L0)** | Project docs, plans, notes, memos — section-level retrieval with citations |
| **Short-term memory (L1)** | Current project / session working state |
| **Long-term memory (L2)** | Distilled **semantic** (facts), **episodic** (what happened), **procedural** (skills) memory across all projects |
| **Per-agent identity (L3)** | Each agent accumulates its *own* experience; plus a shared team namespace |
| **Distillation loop** | A "reflection + consolidation" pass turns raw experience into dense, reusable lessons — so context is effectively endless (compression + retrieval, not an infinite window) |

## Design pillars

1. **Ports & adapters** — the core depends on interfaces; infrastructure swaps per environment.
2. **PostgreSQL is the spine** — relational + vectors (`pgvector` HNSW on `vector(1536)`) + canonical memory live in Postgres.
3. **Own the canonical store** — distilled memory is plain rows (raw text + provenance + regenerable embeddings); engines are replaceable processors.
4. **One model gateway** — all LLM/embedding calls go through LiteLLM (Qwen 8B 1536d / Ollama in dev → OpenRouter / Vertex / Azure in cloud).
5. **Self-hosted for dev, portable to GCP or Azure** — cloud migration is an adapter swap, not a rewrite.
6. **Config over code** — each project/domain is YAML on the shared core.

## Quickstart (dev, self-hosted)

```bash
cp .env.example .env          # fill in secrets; set POSTGRES_HOST_PORT if 5432 is taken
docker compose up -d          # Postgres+pgvector, Redis, Neo4j, MCP gateway, Worker
make migrate                  # apply db/migrations
make pull-models              # pull local Ollama models (when using local-ai profile)
pip install -e . && engramory --help
```

All dev dependencies are free and open-source.

## Use it as an agent (memory, knowledge, SDD & docs for AI agents)

Any AI agent with a shell or MCP client uses Engramory through two thin faces over `AccessSurface` (ADR-10):

- **`engramory` CLI (dev/CI face):** `engramory memory` (`add`, `distill`, `search [--include-knowledge --expand-graph]`, `feedback`, `forget`, `ingest-event`, `learning-health`), `engramory knowledge` (`ingest`, `ingest-media`, `blob-put`, `blob-stat`, `blob-get`, `sync-git`, `search [--modality --expand-graph]`), `engramory sdd` (`ingest`, `slice`, `blast-radius`, `validate-graph`, `check-invariants`, `lint-policy`), `engramory docs` (`lint`, `ingest`, `slice-runbook`, `check-drift`, `export-llms`, `smoke-run`, `tool-inventory`, `build-portal`), and `engramory mcp` (`--transport stdio|sse`).
- **MCP Gateway (`engramory mcp`, authenticated production face):** exposes `memory_*` (including `memory_search` with `include_knowledge` & `expand_graph`), `knowledge_ingest`, `knowledge_ingest_media`, `knowledge_search`, `graph_neighbors`, `sdd_context_slice`, `sdd_blast_radius`, `sdd_ingest`, `sdd_validate_graph`, `sdd_check_invariants`, `sdd_lint_policy`, and `docs_*` tools with OIDC/JWKS authentication and fail-closed authorization.

- **Install + first round-trip:** [docs/INSTALL.md](docs/INSTALL.md)
- **Agent workflow (3 tracks: terminal agent, CI, Skill):** [docs/AGENT-QUICKSTART.md](docs/AGENT-QUICKSTART.md)
- **Wire any vendor (Claude / Gemini / Codex / Copilot / Cursor):** [docs/AGENT-INTEGRATION.md](docs/AGENT-INTEGRATION.md)
- **Claude reference Skill:** [skills/engramory-memory/SKILL.md](skills/engramory-memory/SKILL.md)
- **CLI contract (exit codes, JSON shapes, config):** [docs/sdd/06_SPEC/SPEC-07_cli_face.yaml](docs/sdd/06_SPEC/SPEC-07_cli_face.yaml)

The CLI is the ADR-10 **dev/CI face** (single-tenant dev tier); the MCP
gateway is the authenticated production face and arrives in a later cycle.

## Documentation

| Doc | Purpose |
|---|---|
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Target architecture, portability matrix, build roadmap |
| [docs/MULTIMODAL_AND_POSTGRES_BLOB_STORAGE.md](docs/MULTIMODAL_AND_POSTGRES_BLOB_STORAGE.md) | Multimodal ingestion (text, images, audio, `20 GB+` video), PostgreSQL 18 Chunked Blob CAS (`ts_blob_cold`), and Neo4j dual-engine graph architecture (`Rule D14a`/`D14b`, `Decision D17`) |
| [docs/doc-tools/00_INDEX.md](docs/doc-tools/00_INDEX.md) | AI-first documentation, executable runbooks, drift & `llms.txt` toolchain (`engramory docs`) |
| [docs/CORES.md](docs/CORES.md) | The two bounded cores (Memory & Knowledge), their boundary, and the shared spine |
| [docs/STRATEGY.md](docs/STRATEGY.md) | Build strategy & recommendation (build the plane, adopt the engine, lead with evaluation) |
| [docs/MEMORY_DESIGN.md](docs/MEMORY_DESIGN.md) | Layered memory (L0–L3), distillation loop, schema, portability |
| [docs/PORTABILITY.md](docs/PORTABILITY.md) | Self-hosted → GCP/Azure principles and migration |
| [docs/sdd/08_IPLAN/IPLAN-00_index.yaml](docs/sdd/08_IPLAN/IPLAN-00_index.yaml) | Canonical SDD implementation plan index (IPLAN-01..08) and `plans/PLAN-*.md` |
| [docs/GLOSSARY.md](docs/GLOSSARY.md) | Terms |
| [docs/HOW_TO_USE_THE_FRAMEWORK.md](docs/HOW_TO_USE_THE_FRAMEWORK.md) | Authoring SDD artifacts the UCX / aidoc-flow way |
| [docs/UCX_INTEGRATION.md](docs/UCX_INTEGRATION.md) | How Engramory fits UCX and replaces `ucx_kb` |
| [docs/INSTALL.md](docs/INSTALL.md) | Install the dev stack + CLI |
| [docs/AGENT-QUICKSTART.md](docs/AGENT-QUICKSTART.md) | An agent's first remembered + retrieved memory |
| [docs/AGENT-INTEGRATION.md](docs/AGENT-INTEGRATION.md) | Per-vendor agent wiring (Claude/Gemini/Codex/Copilot/…) |
| [docs/sdd/](docs/sdd/) | SDD lifecycle artifacts (BRD→IPLAN→CHG); `docs/sdd/05_ADR/` = canonical implementing ADRs |
| [docs/adr/](docs/adr/) | Conceptual/descriptive architecture decision records |
| [config/](config/) | Per-project / per-domain YAML config on the shared core |
| [docs/research/](docs/research/) | Background: memory-tool landscape, RAC & Nexus v3 reviews |

## Status

Active platform core (`platform-engramory`). See [docs/sdd/08_IPLAN/IPLAN-00_index.yaml](docs/sdd/08_IPLAN/IPLAN-00_index.yaml) and [plans/](plans/).

## License

Apache-2.0 — see [LICENSE](LICENSE).

<!-- composition verification 2026-07-12 -->
