# Changelog

All notable changes to Engramory are documented here. Format: [Keep a Changelog](https://keepachangelog.com/); versioning: [SemVer](https://semver.org/).

## [Unreleased]
### Added
- Project initiation: README, architecture, memory design, portability, roadmap, ADRs.
- Phase-0 dev scaffolding: docker-compose (Postgres+pgvector, Redis, MinIO, LiteLLM+Ollama, Neo4j, Keycloak), plus a MinIO bucket-init job and service healthchecks.
- Port interfaces (storage, vector, graph, cache, llm, secrets, events, memory) with method signatures; identity is a gateway concern, not a core port.
- Initial memory schema migration (episodes, memories, agent_profiles, consolidation_runs) with an `updated_at` trigger and a supersession index.
- Migration 0002: additive `domain` scope level and `domain_id` columns/indexes.
- ADR-07: scope-model decision (agent/project/domain/space ladder; `tenant_id` isolation).
- `docs/research/MEMORY_CONCEPT_REVIEW.md`: conceptual review of the agent-memory approach.
- `docs/STRATEGY.md`: build strategy & recommendation (build the plane, adopt the engine, lead with evaluation).
- Dev tooling: ruff lint rules, pytest + mypy config, `py.typed`, CI workflow, and a test skeleton.

### Changed
- Scope vocabulary unified across schema/SPEC/docs: isolation column renamed `space_id` → `tenant_id`; the `shared` scope value is retired in favor of `space` (tenant-wide). See ADR-07.
- `make migrate` now applies **all** `db/migrations/*.sql` in order (was hard-coded to 0001) and loads `.env`.
- ADR homes clarified: `sdd/05_ADR/` is canonical/implementing; `docs/adr/` is conceptual/descriptive.
