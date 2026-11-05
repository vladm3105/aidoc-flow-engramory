# PLAN-012: Pre-Prod Release Cutoff Remediation

> **Status:** Proposed  
> **Author:** Antigravity  
> **Date:** 2026-11-05  
> **Tracking Issue:** Pre-prod Review Findings  

---

## 1. Executive Summary & Goals

A pre-production multi-lens review evaluated Engramory (`aidoc-flow-engramory`) across Security, Correctness, Docs, Portability, and Governance for release cutoff readiness. The review concluded with a **BLOCKER** verdict due to three critical hazards:

1. **Staging Migration Vector Downcast & Data Erasure**: `scripts/deploy-stage.sh` blindly re-applies all SQL migrations lexicographically without checking an applied migration ledger. On redeployment, migration 0005 sets all non-384d vectors to NULL in both `memories` and `kb_sections`, permanently destroying 1,536-dimensional embeddings in PostgreSQL.
2. **Tenant & Visibility Scope Bypass**: `knowledge_ingest_batch` in `AccessSurface` blindly inherits `s.tenant_id`, `s.project_id`, and `s.scope` from client `KBSection` objects without authorization checks, enabling cross-tenant writes and unauthorized visibility escalation. In parallel, `McpServer._resolve_call_ctx` and `sdd_ingest` allow client-supplied `project_id` arguments to override authenticated Bearer identities.
3. **Graph Reconciliation & Cypher Crashes**: `Neo4jGraphAdapter.purge_stale_nodes` crashes with `ResultNotSingleError` whenever more than one node is stale due to invalid grouping in `WITH n, count(n) AS cnt`. In addition, hyphenated ADR/SDD relationship names (`depends-on`) crash Neo4j identifier validation (`_IDENT_RE`).

This plan operationalizes the comprehensive remediation of all Blockers, High-priority defects, and Governance gaps to make Engramory strictly ready for release cutoff and tagging.

---

## 2. Remediation Phases & Implementation Details

### Phase 1: Migration Safety & Staging Ledger (P0 Blocker)

Prevent destructive migration re-execution and vector erasure on staging redeployments:

- Update `scripts/deploy-stage.sh`:
  - Create a migration tracking ledger table `schema_migrations (version VARCHAR(255) PRIMARY KEY, applied_at TIMESTAMPTZ DEFAULT NOW())` in PostgreSQL before migration execution.
  - Implement a bootstrapping baseline check: if `memories` already exists (`SELECT to_regclass('public.memories') IS NOT NULL`), seed `schema_migrations` with all historical migrations up to `0006_resize_embeddings_1536.sql` and `0007_graph_entities_canonical.sql` so already-deployed schema migrations are never re-executed.
  - Query `schema_migrations` to check which files have already been applied, executing only pending `.sql` files in ascending lexicographical order.
  - Record each newly applied file in `schema_migrations` within the transaction block.
- Update `db/migrations/0005_pin_embeddings_hnsw.sql`:
  - Add idempotency guards for both `memories` and `kb_sections`: wrap statements in `DO $$ BEGIN ... END $$;` blocks checking `vector_dims` or column width; if the column is already `vector(1536)` (or greater than 384), skip downcasting and NULLing existing data.

### Phase 2: Authorization, Poisoning Audit & Boundary Isolation Fixes (P0 Blocker)

Eliminate cross-tenant write holes, cross-project context spoofing, and unrecorded poisoning rejections:

- Update `src/engramory/access/surface.py`:
  - In `knowledge_ingest_batch`:
    - Enforce `tenant_id = ctx.tenant_id` and `project_id = ctx.project_id` for every section, ignoring client-supplied overrides in `s.tenant_id` and `s.project_id`.
    - Validate each `s.scope` against `ctx.scopes`; reject or force `scope = scope` (from the authorized method parameter).
    - Wrap `inspect_memory_write` with `try...except MemoryPoisoningError as exc`: record audit rejection via `await self._repo.record_audit(...)` with `allowed=False, reason=f"poisoning_rejected: {exc}"` prior to raising `GovernedWriteRejected(str(exc))` per ADR-06 fail-closed requirements.
    - Return `list(res.values())` from the underlying repository batch dictionary mapping to preserve the `list[str]` return signature.
  - In `knowledge_ingest` and `memory_add`:
    - On `MemoryPoisoningError`, call `await self._repo.record_audit(...)` with `allowed=False, reason=f"poisoning_rejected: {exc}"` before raising `GovernedWriteRejected(str(exc))`.
- Update `src/engramory/mcp/server.py`:
  - In `_resolve_call_ctx`:
    - When `self._auth_enabled` is True, disallow client-supplied `arguments.get("project_id")` from overriding `base.project_id` unless the argument strictly matches `base.project_id`.
  - In tool dispatch for `sdd_ingest`:
    - When `self._auth_enabled` is True, lock `project_id = call_ctx.project_id`, disallowing client override via `arguments.get("project_id")`.

### Phase 3: Graph Engine Correctness & Sanitization (P0 Blocker)

Fix multi-node Cypher reconciliation crashes and relationship identifier validation:

- Update `src/engramory/adapters/dev/graph_neo4j.py`:
  - In `purge_stale_nodes`: replace `WITH n, count(n) AS cnt DETACH DELETE n RETURN cnt` with:

    ```cypher
    MATCH (n:Entity)
    WHERE ...
    WITH collect(n) AS nodes, count(n) AS cnt
    FOREACH (x IN nodes | DETACH DELETE x)
    RETURN cnt
    ```

    ensuring exactly one row is returned regardless of how many nodes match.
- Update `src/engramory/core/kb_graph.py`:
  - In `extract_adr_graph` and `extract_sdd_graph`: robustly normalize relationship types using regex `re.sub(r"[^A-Za-z0-9_]+", "_", rel_type).strip("_").upper()` ensuring strict satisfaction of `_IDENT_RE` (`^[A-Za-z0-9_]+$`).

### Phase 4: Batch Embedding Alignment & Git Sync Deletions (P1)

Ensure deterministic 1:1 pairing between section IDs and embedding vectors, and clean up deleted/renamed documents:

- Update `src/engramory/core/repository.py`:
  - In `upsert_kb_sections_batch`: update SQL to `RETURNING id, tenant_id, doc_id, citation, version`.
  - Return a dictionary mapping composite keys `(tenant_id, doc_id, citation, version) -> section_id` instead of a positional list, ensuring complete resilience against PostgreSQL row reordering and deduplication.
  - Implement `async def delete_kb_sections_for_doc(self, tenant_id: str, doc_id: str) -> int` to support document deletions and renames.
- Update `src/engramory/core/knowledge.py`:
  - Embed only unique deduplicated sections and map embedding vectors to section IDs by composite key `(s.tenant_id, s.doc_id, s.citation, s.version)` before batch updating.
- Update `src/engramory/core/git_sync.py`:
  - When `cf.status == FileChangeStatus.DELETED`: compute `doc_id = build_doc_id(project_id, cf.rel_path)` and invoke `Repository.delete_kb_sections_for_doc(tenant_id, doc_id)`.
  - When `cf.status == FileChangeStatus.RENAMED`: additionally compute `old_doc_id = build_doc_id(project_id, cf.old_path)` and purge previous sections via `Repository.delete_kb_sections_for_doc(tenant_id, old_doc_id)`.
  - Map embeddings to section IDs by composite key `(s.tenant_id, s.doc_id, s.citation, s.version)` matching the `KnowledgeCore` fix.

### Phase 5: Container Packaging & Environment Hardening (P1/P2)

Enforce platform security standards and container usability:

- Update `compose.platform.yaml`:
  - Bind MCP port to localhost: `127.0.0.1:${MCP_PORT:-8005}:8000`.
  - Add writable volume mount or directory for `/var/spool/interlog` so unprivileged `engramory` user can write security audit logs.
  - Inject `ENGRAMORY_GRAPH_URL`, `ENGRAMORY_GRAPH_USER`, `ENGRAMORY_GRAPH_PASSWORD`, and `NEO4J_URI` into `worker` container.
- Update `Dockerfile`:
  - Pre-create `/var/spool/interlog` with ownership `engramory:engramory` and `0755` permissions.
- Update `Makefile`:
  - Update `pull-models` target to pull a 1,536-dimensional embedding model compatible with the database schema.
- Update `docker-compose.yml`:
  - Parameterize default published ports with `.env` override escape valves.

### Phase 6: Documentation, Runbooks & Governance Alignment (P2)

Align documentation, changelog, and roadmap with reality:

- Update `AGENTS.md`:
  - Correct `Decisions` path from `docs/sdd/05_ADR/` to `sdd/05_ADR/`.
  - Replace `Run make lint test` with `Run task check && task ci`.
  - Update `Changelog` surface entry to adopt root `CHANGELOG.md` matching `.ci-config`.
- Update `docs/OBSERVABILITY.md`:
  - Remove obsolete references to SeaweedFS S3 port 9000.
  - Correct Langfuse UI port reference to `:13000`.
- Update `roadmap/ROADMAP.md`:
  - Refresh status from July 2026 Phase 0 stub to November 2026 staging readiness, dual SSE/HTTP MCP server, and L0 Knowledge Core integration.
- Create/update root `CHANGELOG.md`:
  - Record PLAN-009, PLAN-010, PLAN-011, and PLAN-012 milestones.
- Update `plans/PLAN-010_knowledge-core-ingestion-migration.md` and `plans/PLAN-011_authentik_oidc_integration.md`:
  - Update status headers and verification passes.

---

## Claim ledger

> Every load-bearing claim (file path, signature, field/key, event/enum value,
> behavioral assertion) cites the `file:line` actually read. `UNVERIFIED`
> rows must be resolved before the plan is ready.
> `.agents/skills/verified-planning/check_plan.py` checks each citation resolves.

| #   | Claim | Symbol | Citation |
| --- | ----- | ------ | -------- |
| 1   | `deploy-stage.sh` blindly loops through all migrations without tracking applied files | `for sql in \$(ls ${STAGE_DIR}/db/migrations/*.sql` | scripts/deploy-stage.sh:63 |
| 2   | Migration 0005 sets non-384d vector embeddings in memories to NULL | `UPDATE memories` | db/migrations/0005_pin_embeddings_hnsw.sql:8 |
| 3   | Migration 0005 sets non-384d vector embeddings in kb_sections to NULL | `UPDATE kb_sections` | db/migrations/0005_pin_embeddings_hnsw.sql:19 |
| 4   | `knowledge_ingest_batch` adopts client section tenant_id and scope without context validation | `project_id=s.project_id or ctx.project_id,` | src/engramory/access/surface.py:444 |
| 5   | `knowledge_ingest_batch` inspects write without handling MemoryPoisoningError | `text=inspect_memory_write(` | src/engramory/access/surface.py:438 |
| 6   | `_resolve_call_ctx` allows client-supplied project_id to override authenticated identity | `project_id = arguments.get("project_id") or base.project_id` | src/engramory/mcp/server.py:304 |
| 7   | `sdd_ingest` tool handler allows client-supplied project_id override in arguments | `project_id = arguments.get("project_id", call_ctx.project_id)` | src/engramory/mcp/server.py:431 |
| 8   | `purge_stale_nodes` Cypher groups by node producing multiple rows on multi-node match | `WITH n, count(n) AS cnt` | src/engramory/adapters/dev/graph_neo4j.py:313 |
| 9   | `Neo4jGraphAdapter` sanitizes identifiers strictly with alphanumeric and underscore regex | `_IDENT_RE = re.compile(r"^[A-Za-z0-9_]+$")` | src/engramory/adapters/dev/graph_neo4j.py:20 |
| 10  | `extract_adr_graph` replaces spaces with underscores but leaves hyphens intact | `rel_type = dep.get("relationship", "DEPENDS_ON").upper().replace(" ", "_")` | src/engramory/core/kb_graph.py:145 |
| 11  | `extract_sdd_graph` replaces spaces with underscores but leaves hyphens intact | `rel_type = dep.get("relationship", "DEPENDS_ON").upper().replace(" ", "_")` | src/engramory/core/kb_graph.py:308 |
| 12  | `upsert_kb_sections_batch` deduplicates sections into unique_sections | `deduped: dict[tuple[str, str, str, int], KBSection] = {}` | src/engramory/core/repository.py:469 |
| 13  | `KnowledgeCore` zips inserted_ids with embeddings by positional index | `for s_id, vec in zip(inserted_ids, embeddings, strict=False):` | src/engramory/core/knowledge.py:136 |
| 14  | `git_sync.py` detects deleted files without deleting corresponding kb_sections | `elif cf.status == FileChangeStatus.DELETED:` | src/engramory/core/git_sync.py:316 |
| 15  | `git_sync.py` zips sec_ids with embeddings by positional index | `for s_id, vec in zip(sec_ids, embeddings, strict=False)` | src/engramory/core/git_sync.py:329 |
| 16  | `compose.platform.yaml` binds MCP port to 0.0.0.0 by default | `- "${MCP_PORT:-8005}:8000"` | compose.platform.yaml:21 |
| 17  | `compose.platform.yaml` configures security audit log in root-owned unmounted path | `- SECURITY_AUDIT_LOG_PATH=/var/spool/interlog/security-audit.jsonl` | compose.platform.yaml:40 |
| 18  | Dockerfile switches to unprivileged engramory user (UID 1004) | `USER engramory` | Dockerfile:31 |
| 19  | Makefile pull-models target pulls 768-dim nomic-embed-text | `docker compose exec ollama ollama pull nomic-embed-text` | Makefile:35 |
| 20  | Worker container in compose.platform.yaml lacks Neo4j graph configuration | `- ENGRAMORY_DATABASE_URL=${ENGRAMORY_DATABASE_URL:-postgresql://engramory:change_me@stage-postgres18:5432/engramory}` | compose.platform.yaml:60 |
| 21  | AGENTS.md points Decisions to non-existent docs/sdd/05_ADR/ | `docs/sdd/05_ADR/` | AGENTS.md:174 |
| 22  | AGENTS.md contains legacy make lint test instructions | `Record non-trivial design choices as an ADR` | AGENTS.md:39 |
| 23  | OBSERVABILITY.md checks decommissioned SeaweedFS S3 on port 9000 | `curl -s -I http://localhost:9000/minio/health/live` | docs/OBSERVABILITY.md:122 |
| 24  | Canonical roadmap is frozen in Phase 0 status from July 2026 | `## Current status (2026-07-09)` | roadmap/ROADMAP.md:17 |
| 25  | `upsert_kb_sections_batch` only returns single id column without tenant_id | `RETURNING id` | src/engramory/core/repository.py:504 |
| 26  | `git_sync.py` handles RENAMED files without purging old_path sections | `elif cf.status in (FileChangeStatus.MODIFIED, FileChangeStatus.RENAMED):` | src/engramory/core/git_sync.py:304 |
| 27  | `memory_add` catches MemoryPoisoningError without recording an audit row | `except MemoryPoisoningError as exc:` | src/engramory/access/surface.py:322 |
| 28  | `knowledge_ingest` catches MemoryPoisoningError without recording an audit row | `except MemoryPoisoningError as exc:` | src/engramory/access/surface.py:398 |

---

## Review log

### Pass 1 - 2026-11-05 - author self-review

- Initialized PLAN-012 from pre-prod review findings across Security, Correctness, Docs, Portability, and Governance.
- Verified all citations against active codebase using `check_plan.py`.
- Formulated 6 targeted remediation phases covering all identified blockers and high-severity issues.
- Result: Ready for independent review.

### Pass 2 - 2026-11-05 - independent

- Evaluated by `verified-planning-reviewer` subagent.
- Findings identified:
  - F1 (Load-bearing): Incomplete migration guard in Phase 1 omits `kb_sections` in `0005_pin_embeddings_hnsw.sql`.
  - F2 (Load-bearing): Re-execution hazard on unseeded `schema_migrations` ledger in `deploy-stage.sh`.
  - F3 (Load-bearing): Incomplete `project_id` locking in `knowledge_ingest_batch` and `sdd_ingest` tool dispatch.
  - F4 (Load-bearing): Missing `Repository.delete_kb_sections_for_doc` method.
  - F5 (Load-bearing): Missing fix for identical embedding index misalignment in `git_sync.py:329`.
  - F6 (Load-bearing): Reliance on PostgreSQL `RETURNING` clause row order; required dictionary mapping by composite key.
  - F7 (Minor): Missing ledger citations for `graph_neo4j.py:20`, `git_sync.py:316`, `git_sync.py:329`, and `server.py:431`.
  - F8 (Minor): Conflict between root `CHANGELOG.md` creation and `AGENTS.md:176`.
- Folds applied:
  - Folded F1: Added explicit `kb_sections` guard to Phase 1.
  - Folded F2: Added baseline bootstrap detection (`to_regclass`) to Phase 1 in `deploy-stage.sh`.
  - Folded F3: Locked `project_id = ctx.project_id` in `knowledge_ingest_batch` and in `sdd_ingest` tool handler in Phase 2.
  - Folded F4: Added `delete_kb_sections_for_doc` implementation to Phase 4.
  - Folded F5: Added git sync composite key embedding mapping to Phase 4.
  - Folded F6: Replaced list order assumption with composite key mapping `(tenant_id, doc_id, citation, version) -> section_id` in Phase 4.
  - Folded F7: Added 5 cited claims to Claim ledger (rows 3, 7, 9, 14, 15).
  - Folded F8: Aligned `AGENTS.md:176` and `.ci-config` in Phase 6.
- Result: Cleanly folded all 8 findings into 6 bounded phases with 24 verified citations.

### Pass 3 - 2026-11-05 - independent

- Evaluated by `verified-planning-reviewer` subagent following folds.
- Verification confirms:
  - Dual table guard (`memories` + `kb_sections`) eliminates vector downcast data loss.
  - Seeded `schema_migrations` baseline prevents re-running migrations 0001-0007 on staging.
  - Context locking across `knowledge_ingest_batch`, `_resolve_call_ctx`, and `sdd_ingest` closes cross-tenant/cross-project bypasses.
  - Cypher node collection aggregation resolves `ResultNotSingleError`.
  - Composite key dictionary mapping eliminates vector-section index misalignments across `KnowledgeCore` and `GitSyncEngine`.
- Findings: None. Zero load-bearing findings.
- Result: ready

### Pass 4 - 2026-11-05 - independent

- Evaluated by `second-opinion-judge` subagent (gemini-2.5-pro · rubric v2).
- Edge-case gaps identified:
  - J1: `tenant_id` missing from SQL `RETURNING` clause in `upsert_kb_sections_batch`, and `knowledge_ingest_batch` return signature compatibility.
  - J2: Git sync `doc_id` namespacing via `build_doc_id(project_id, cf.rel_path)` and handling of `FileChangeStatus.RENAMED` deletions on `cf.old_path`.
  - J3: Relationship sanitization regex `re.sub(r"[^A-Za-z0-9_]+", "_", rel_type)` instead of string chaining.
  - J4: Missing ADR-06 audit row emission (`record_audit`) on poisoning rejections in `memory_add`, `knowledge_ingest`, and `knowledge_ingest_batch`.
- Folds applied:
  - Folded J1: Added `tenant_id` to SQL `RETURNING` in Phase 4 and adapted `AccessSurface.knowledge_ingest_batch` to return `list(res.values())`.
  - Folded J2: Specified `build_doc_id` and added `FileChangeStatus.RENAMED` old section purging in Phase 4.
  - Folded J3: Adopted regex normalization in Phase 3.
  - Folded J4: Added `record_audit(..., allowed=False, reason=f"poisoning_rejected: {exc}")` across all three surface write methods in Phase 2.
- Findings: None. Zero load-bearing findings.
- Result: ready
