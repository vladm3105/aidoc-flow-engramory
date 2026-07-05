# Changelog

All notable changes to Engramory are documented here. Format: [Keep a Changelog](https://keepachangelog.com/); versioning: [SemVer](https://semver.org/).

## [Unreleased]
### Added
- Project initiation: README, architecture, memory design, portability, roadmap, ADRs.
- Phase-0 dev scaffolding: docker-compose (Postgres+pgvector, Redis, MinIO, LiteLLM+Ollama, Neo4j, Keycloak).
- Port interfaces (storage, vector, graph, cache, llm, secrets, events, memory) and adapter pattern.
- Initial memory schema migration (episodes, memories, agent_profiles, consolidation_runs).
