# aidoc-flow-engramory

## Engramory — the memory & knowledge plane of the aidoc-flow ecosystem

**Shared agent knowledge & memory core.** Engramory is the persistent, distilled "brain" that the aidoc-flow ecosystem and your AI projects build on — it gives agents long-term memory (knowledge, skills, and experience) that survives across sessions, projects, and platforms.

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
2. **PostgreSQL is the spine** — relational + vectors (pgvector) + canonical memory live in Postgres.
3. **Own the canonical store** — distilled memory is plain rows (raw text + provenance + regenerable embeddings); engines are replaceable processors.
4. **One model gateway** — all LLM/embedding calls go through LiteLLM (Ollama in dev → Vertex/Azure in cloud).
5. **Self-hosted for dev, portable to GCP or Azure** — cloud migration is an adapter swap, not a rewrite.
6. **Config over code** — each project/domain is YAML on the shared core.

## Quickstart (dev, self-hosted)

```bash
cp .env.example .env          # fill in secrets; set POSTGRES_HOST_PORT if 5432 is taken
docker compose up -d          # Postgres+pgvector, Redis, MinIO, LiteLLM+Ollama, Neo4j, Keycloak
make migrate                  # apply db/migrations
make pull-models              # pull local Ollama models
# the MCP gateway is not yet shipped — the dev/CI agent face is the CLI (ADR-10):
pip install -e . && engramory --help
```

All dev dependencies are free and open-source.

## Use it as an agent (memory for AI agents)

Any AI agent with a shell uses Engramory as memory through the **`engramory`
CLI** — remember (`memory add` + `memory distill`), retrieve (`memory
search`), and close the learning loop (`memory feedback`). Every call is
authorized (default deny), scoped agent/project/tenant, and audited.

- **Install + first round-trip:** [docs/INSTALL.md](docs/INSTALL.md)
- **Agent workflow (3 tracks: terminal agent, CI, Skill):** [docs/AGENT-QUICKSTART.md](docs/AGENT-QUICKSTART.md)
- **Wire any vendor (Claude / Gemini / Codex / Copilot / Cursor):** [docs/AGENT-INTEGRATION.md](docs/AGENT-INTEGRATION.md)
- **Claude reference Skill:** [skills/engramory-memory/SKILL.md](skills/engramory-memory/SKILL.md)
- **CLI contract (exit codes, JSON shapes, config):** [sdd/06_SPEC/SPEC-07_cli_face.yaml](sdd/06_SPEC/SPEC-07_cli_face.yaml)

The CLI is the ADR-10 **dev/CI face** (single-tenant dev tier); the MCP
gateway is the authenticated production face and arrives in a later cycle.

## Documentation

| Doc | Purpose |
|---|---|
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Target architecture, portability matrix, build roadmap |
| [docs/CORES.md](docs/CORES.md) | The two bounded cores (Memory & Knowledge), their boundary, and the shared spine |
| [docs/STRATEGY.md](docs/STRATEGY.md) | Build strategy & recommendation (build the plane, adopt the engine, lead with evaluation) |
| [docs/MEMORY_DESIGN.md](docs/MEMORY_DESIGN.md) | Layered memory (L0–L3), distillation loop, schema, portability |
| [docs/PORTABILITY.md](docs/PORTABILITY.md) | Self-hosted → GCP/Azure principles and migration |
| [roadmap/ROADMAP.md](roadmap/ROADMAP.md) | Canonical roadmap — MVP cycle plan (MVP-1..8) + engineering Phase view (Phase 0–3) |
| [docs/GLOSSARY.md](docs/GLOSSARY.md) | Terms |
| [docs/HOW_TO_USE_THE_FRAMEWORK.md](docs/HOW_TO_USE_THE_FRAMEWORK.md) | Authoring SDD artifacts the UCX / aidoc-flow way |
| [docs/UCX_INTEGRATION.md](docs/UCX_INTEGRATION.md) | How Engramory fits UCX and replaces `ucx_kb` |
| [docs/INSTALL.md](docs/INSTALL.md) | Install the dev stack + CLI |
| [docs/AGENT-QUICKSTART.md](docs/AGENT-QUICKSTART.md) | An agent's first remembered + retrieved memory |
| [docs/AGENT-INTEGRATION.md](docs/AGENT-INTEGRATION.md) | Per-vendor agent wiring (Claude/Gemini/Codex/Copilot/…) |
| [sdd/](sdd/) | SDD lifecycle artifacts (BRD→IPLAN); `sdd/05_ADR/` = canonical implementing ADRs |
| [docs/adr/](docs/adr/) | Conceptual/descriptive architecture decision records |
| [config/](config/) | Per-project / per-domain YAML config on the shared core |
| [docs/research/](docs/research/) | Background: memory-tool landscape, RAC & Nexus v3 reviews |

## Status

Project initiation. Phase 0 (dev foundation) scaffolding. See [roadmap/ROADMAP.md](roadmap/ROADMAP.md) § "Engineering Phase view".

## License

Apache-2.0 — see [LICENSE](LICENSE).

<!-- composition verification 2026-07-12 -->
