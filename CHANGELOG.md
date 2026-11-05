# Changelog

All notable changes to the Engramory platform will be documented in this file.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased] - 2026-11-05

### Added

- **Release Cutoff Remediation (PLAN-012)**:
  - Added idempotency guards in `db/migrations/0005_pin_embeddings_hnsw.sql` checking column typmod to prevent downcasting or wiping 1536-dim embeddings.
  - Implemented `schema_migrations` tracking and migration baseline bootstrapping in `scripts/deploy-stage.sh`.
  - Added scope boundary validation and audit recording for memory poisoning rejections in `AccessSurface`.
  - Added tenant and project parameter locking in authenticated MCP server mode to prevent client spoofing.
  - Added `delete_kb_sections_for_doc` in `Repository` and deletion/rename reconciliation in Git sync engine.
  - Added composite key `(tenant_id, doc_id, citation, version)` deduplication and mapping for knowledge batch upsert and embedding vectors.
  - Hardened Docker and Compose configurations with localhost port bindings (`127.0.0.1`), configurable spool directory mounts, and worker Neo4j environment parity.

- **Authentik OIDC / JWT Security Integration (PLAN-011)**:
  - Integrated with `platform-security` (Authentik IdP) via JWT token verification, JWKS caching, audience checking, and `ActorContext` propagation.
  - Added security audit event logging adhering to Interlog spool standard.
  - Added platform blueprint `blueprints/25-app-engramory.yaml` and compose platform definition `compose.platform.yaml`.

- **Knowledge Core Ingestion Migration (PLAN-010)**:
  - Router-based document extraction for Markdown, Python, JSON, and YAML specifications.
  - Git repository synchronization with differential commit tracking.
  - Topological SDD lineage graph extraction and Neo4j projection.

- **Canonical Postgres Graph Schema & Resized Vectors (PLAN-009)**:
  - Migrated vector dimensions to 1536 for OpenAI and LiteLLM model compatibility.
  - Added canonical PostgreSQL tables for entities and relations before Neo4j projection.
