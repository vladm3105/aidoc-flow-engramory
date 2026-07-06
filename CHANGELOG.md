# Changelog

All notable changes to Engramory are documented here. Format: [Keep a Changelog](https://keepachangelog.com/); versioning: [SemVer](https://semver.org/).

## [Unreleased]
### Added
- **aidoc-flow CI standards adoption** (2026-07-06) — engramory joins the
  aidoc-flow workspace CI infrastructure:
  - `CLAUDE.md` populated with the aidoc-flow-standard governance sections
    (Per-repo governance / Unified CI / Governance PR discipline OPS-0061 /
    AI agent auto-merge default OPS-0062 / Multi-agent automated review
    OPS-0065+0067). `AGENTS.md` remains the engineering source-of-truth;
    both files apply.
  - `.github/workflows/ai-review.yml` (NEW) — thin caller for
    `vladm3105/aidoc-flow-ci@ci/v1.4.3`, reviewer=claude, runner=ubuntu-latest.
  - `.github/workflows/composition.yml` (NEW) — thin caller for
    `vladm3105/aidoc-flow-ci@ci/v1.3.0`.
  - `.github/workflows/auto-merge-ai-prs.yml` (NEW) — thin caller for
    `vladm3105/aidoc-flow-ci@ci/v1.5.1` (server-side enforcer for AI-opened
    PRs per OPS-0062).
  - **Dormant** until reviewer App is installed on this repo (F5 blast-radius
    prerequisite per aidoc-flow-operations CLAUDE.md § Unified CI). Follow-up
    PR on aidoc-flow-operations adds `vladm3105/aidoc-flow-engramory` to the
    `auto_merge.repos` allowlist.
  - Follows the sibling wire-up pattern (business PR #37 / iplanic PR #228 /
    iplan-runner PR #61) with engramory's public + ubuntu-latest topology.
- Project initiation: README, architecture, memory design, portability, roadmap, ADRs.
- Phase-0 dev scaffolding: docker-compose (Postgres+pgvector, Redis, MinIO, LiteLLM+Ollama, Neo4j, Keycloak), plus a MinIO bucket-init job and service healthchecks.
- Port interfaces (storage, vector, graph, cache, llm, secrets, events, memory) with method signatures; identity is a gateway concern, not a core port.
- Initial memory schema migration (episodes, memories, agent_profiles, consolidation_runs) with an `updated_at` trigger and a supersession index.
- Migration 0002: additive `domain` scope level and `domain_id` columns/indexes.
- ADR-07: scope-model decision (agent/project/domain/space ladder; `tenant_id` isolation).
- `docs/research/MEMORY_CONCEPT_REVIEW.md`: conceptual review of the agent-memory approach.
- `docs/STRATEGY.md`: build strategy & recommendation (build the plane, adopt the engine, lead with evaluation).
- ADR-08 + `docs/CORES.md`: single platform, two bounded cores (Memory and Knowledge) sharing the spine, with the boundary rules and revisit triggers.
- Dev tooling: ruff lint rules, pytest + mypy config, `py.typed`, CI workflow, and a test skeleton.

### Changed
- Scope vocabulary unified across schema/SPEC/docs: isolation column renamed `space_id` → `tenant_id`; the `shared` scope value is retired in favor of `space` (tenant-wide). See ADR-07.
- `make migrate` now applies **all** `db/migrations/*.sql` in order (was hard-coded to 0001) and loads `.env`.
- ADR homes clarified: `sdd/05_ADR/` is canonical/implementing; `docs/adr/` is conceptual/descriptive.
