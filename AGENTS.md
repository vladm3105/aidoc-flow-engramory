# AGENTS.md — working agreement for AI coding agents on Engramory

This file orients any AI agent (Claude Code, Codex, Hermes, custom) working on this repo.
`CLAUDE.md` points here so all agents share one source of truth.

## What Engramory is
The shared memory + knowledge core for our AI projects (aidoc-flow, iplanic, aidoc-flow-operations, ...).
It provides L0 knowledge base + L1 short-term + L2 long-term distilled + L3 per-agent memory, exposed over MCP.

## Architecture you must respect
- **Ports & adapters.** Import from `engramory.ports`. Never call a vendor SDK or a concrete adapter from core/business code.
- **Postgres is canonical.** Distilled memory = plain rows (`content_raw` + `provenance` + embedding). The graph (Neo4j) and embeddings are **rebuildable projections**, not sources of truth.
- **One model gateway.** All LLM/embedding calls go through the LiteLLM endpoint (`LITELLM_BASE_URL`). Dev = Ollama; cloud = Vertex/Azure by config.
- **Migratable memory.** Always persist the raw text + embedding model + dims so memory can be re-embedded after a model/platform change.

## Conventions
- Language: Python 3.12. Lint/format: ruff. Tests: pytest.
- Config over code: projects/domains are YAML under `config/domains/`.
- Secrets only via `SecretsPort` / `.env` — never hardcode.

## Where things live
- `src/engramory/ports/` — interfaces the core depends on.
- `src/engramory/adapters/` — per-environment implementations (dev/gcp/azure).
- `src/engramory/core/` — knowledge + memory logic.
- `src/engramory/mcp/` — MCP gateway exposing tools to agents.
- `src/engramory/workers/` — reflection + consolidation (the distillation loop).
- `db/migrations/` — SQL schema.
- `config/` — per-project / per-domain YAML config on the shared core.
- `sdd/` — SDD lifecycle artifacts (BRD→IPLAN); `sdd/05_ADR/` = canonical implementing ADRs.
- `roadmap/` — canonical MVP cycle plan (MVP-1..8).
- `docs/` — architecture, memory design, conceptual ADRs (`docs/adr/`), research.

## Before you finish
Run `make lint test`. Record non-trivial design choices as an ADR: canonical
implementing decisions go in `sdd/05_ADR/`; conceptual/descriptive records live
in `docs/adr/`.
