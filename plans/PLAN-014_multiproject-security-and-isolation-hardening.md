# PLAN-014: Multi-Project Security & Cross-Repository Isolation Hardening

- **Status:** Ready — Verified-Planning Gate & Independent Review Passed
- **Date:** 2026-11-06
- **Author:** Antigravity Agent
- **Target Repository:** `platform-engramory`
- **Prerequisite:** `PLAN-013_platform-engramory-rebrand-and-doc-tools-consolidation.md`

---

## 1. Objective & Problem Statement

When multiple repositories (`project_id`) share a single `tenant_id` (such as `"default"`), cross-project data leakage, collision, and authorization vectors exist across PostgreSQL (`kb_documents`, `kb_sections`, `graph_entities`, `graph_relations`), Neo4j (`Entity` upsert and traversal), and the MCP server's local-filesystem `docs_*` / `sdd_ingest` tools:

1. **Cross-Project `kb_documents` & `kb_sections` Overwrite/Deletion & Unaudited Collision:**
   - `kb_documents` has `PRIMARY KEY (tenant_id, doc_id)` (`db/migrations/0008_runbooks_and_doc_governance.sql:6`), and `parse_markdown_document` (`src/engramory/core/docs_tools.py:289`) uses `fm_dict.get("id")` verbatim when present.
   - `Repository.upsert_kb_document` (`src/engramory/core/repository.py:1285`) executes `ON CONFLICT (tenant_id, doc_id) DO UPDATE SET project_id = EXCLUDED.project_id` without checking whether a different `project_id` already owns `(tenant_id, doc_id)`. A second project declaring the same frontmatter `id` overwrites the first project's runbook and replaces its `kb_runbook_steps` (`src/engramory/core/repository.py:1228`).
   - Similarly, `Repository.upsert_kb_section` (`src/engramory/core/repository.py:411`) and `Repository.upsert_kb_sections_batch` (`src/engramory/core/repository.py:477`) update `text = EXCLUDED.text` on `(tenant_id, doc_id, citation, version)` conflict without verifying `project_id` ownership, `AccessSurface.docs_ingest_runbook` (`src/engramory/access/surface.py:512`) and `AccessSurface.knowledge_ingest` (`src/engramory/access/surface.py:373`) do not write an `AuditRecord` when a cross-project collision is rejected, and `Repository.delete_kb_document` (`src/engramory/core/repository.py:1416`) — called by `GitSyncEngine._sync_changed_doc_metadata` (`src/engramory/core/git_sync.py:365`) — deletes by `(tenant_id, doc_id)` or `repo_path` without checking `project_id`.
2. **Cross-Project Graph Traversal Leakage, Entity/Relation Overwrite, & TOFU ID Squatting (`graph_entities` / `graph_relations` / Neo4j):**
   - `graph_entities` is keyed on `PRIMARY KEY (tenant_id, id)` (`db/migrations/0007_graph_entities_canonical.sql:5`). SDD node IDs extracted by `extract_sdd_graph` (`src/engramory/core/kb_graph.py:163`) and `normalize_sdd_id` (`src/engramory/core/kb_graph.py:44`) are un-namespaced (`ADR-001`, `SPEC-001`).
   - `Repository.upsert_graph_entity` (`src/engramory/core/repository.py:787`), `Repository.bulk_upsert_graph_entities` (`src/engramory/core/repository.py:847`), `Repository.upsert_graph_relation` (`src/engramory/core/repository.py:777`), `Repository.bulk_upsert_graph_relations` (`src/engramory/core/repository.py:885`), and `Neo4jGraphAdapter.upsert_node` (`src/engramory/adapters/dev/graph_neo4j.py:82`) overwrite existing `(tenant_id, id)` rows/nodes regardless of `project_id`.
   - Graph read paths — `Repository.find_ancestors` (`src/engramory/core/repository.py:1096`), `Repository.find_descendants` (`src/engramory/core/repository.py:1177`), `Repository.list_graph_entities` (`src/engramory/core/repository.py:1019`), `Neo4jGraphAdapter.neighbors` (`src/engramory/adapters/dev/graph_neo4j.py:158`), `Neo4jGraphAdapter.ancestors` (`src/engramory/adapters/dev/graph_neo4j.py:223`), `Neo4jGraphAdapter.descendants` (`src/engramory/adapters/dev/graph_neo4j.py:288`), `AccessSurface.graph_neighbors` (`src/engramory/access/surface.py:207`), `AccessSurface.context_slice` (`src/engramory/access/surface.py:228`), and `AccessSurface.blast_radius` (`src/engramory/access/surface.py:278`) — filter only by `tenant_id`, allowing a `project`-scoped caller to traverse and read another project's graph nodes.
3. **`d.project_id IS NULL` Wildcard, Missing `domain_id` Check, & Cross-Project Executable Runbook Injection in `Repository.slice_runbook`:**
   - `Repository.slice_runbook` (`src/engramory/core/repository.py:1453`) includes `OR d.project_id IS NULL` (`src/engramory/core/repository.py:1363`), omits `d.project_id` and `d.scope` from its `SELECT` list, and ignores `d.scope` and `d.domain_id`, leaking any runbook ingested with `project_id=None` to all projects in the tenant while failing to isolate `scope="project"` executable runbook queries from foreign-project `space` runbooks or foreign `domain_id` runbooks (`Repository._SCOPE_CLAUSES`, `src/engramory/core/repository.py:43`).
4. **Scope-Hierarchy & `domain_id` Mismatch in `search_kb_sections` & `knowledge_search`:**
   - `AccessSurface.knowledge_search` (`src/engramory/access/surface.py:490`) unconditionally passes `project_id=ctx.project_id` to `Repository.search_kb_sections` (`src/engramory/core/repository.py:634`), which appends separate `project_id = %(project_id)s` and `scope = %(scope)s` predicates instead of enforcing the scope visibility hierarchy (`project` sees `project_id` + `scope='space'`; `domain` sees `scope='domain' AND domain_id=%(domain_id)s` + `scope='space'` + `project_id`; `space` sees `scope='space'` across projects + `project_id`).
5. **Shared-Host Cross-Project Filesystem Read, Symlink Escape, & Missing Reference Monitor Check on `sdd_ingest`:**
   - When `McpServer` (`src/engramory/mcp/server.py:694`) runs over a multi-repo workspace (`ENGRAMORY_ALLOWED_DOCS_ROOT=/workspace/repos`), `resolve_confined_repo_path` (`src/engramory/core/docs_tools.py:210`) only checks that the target path is under `allowed_root` and leaks the resolved host path in its `ValidationError` message.
   - Inside a confined repo root, `check_repo_drift` (`src/engramory/core/docs_tools.py:1201`), `export_repo_llms` (`src/engramory/core/docs_tools.py:1353`), `extract_sdd_graph` (`src/engramory/core/kb_graph.py:163`), and `extract_docs_graph` (`src/engramory/core/kb_graph.py:665`) iterate `rglob` candidates without verifying `f.resolve().is_relative_to(root)`, allowing planted symlinks inside a project to read sibling repositories.
   - `"sdd_ingest"` is absent from `KNOWN_ACTIONS` (`src/engramory/access/authz.py:15`), and `McpServer.handle_call_tool` (`src/engramory/mcp/server.py:317`) executes `sdd_ingest` without `self._surface._decide(...)` and dispatches `graph_neighbors` without forwarding `scope`.

---

## 2. Implementation Phases

### Phase 1: Cross-Project Ownership Guard & Audit Trail on `kb_documents` & `kb_sections`

1. **`Repository.upsert_kb_document` (`src/engramory/core/repository.py:1295`):**
   - Preserve existing non-null `project_id` on unscoped updates via `project_id = COALESCE(EXCLUDED.project_id, kb_documents.project_id)` and add an ownership `WHERE` guard to `ON CONFLICT (tenant_id, doc_id) DO UPDATE`:

     ```sql
     WHERE kb_documents.project_id IS NULL
        OR kb_documents.project_id = EXCLUDED.project_id
     RETURNING doc_id
     ```

   - If `fetchone()` is `None`, raise `ValidationError(f"Cross-project doc_id collision: '{doc.doc_id}' is already owned by another project in tenant '{doc.tenant_id}'")`.
2. **`Repository.delete_kb_document` (`src/engramory/core/repository.py:1426`) & `GitSyncEngine._sync_changed_doc_metadata` (`src/engramory/core/git_sync.py:365`):**
   - Add optional `project_id: str | None = None` parameter to `Repository.delete_kb_document` and restrict `DELETE FROM kb_documents` in both branches with `AND (%(project_id)s::text IS NULL OR project_id = %(project_id)s)`.
   - Update all three `repo.delete_kb_document(...)` calls in `GitSyncEngine._sync_changed_doc_metadata` (`src/engramory/core/git_sync.py:387, 392, 455`) to pass `project_id=project_id` so deleting/renaming `docs/RUNBOOK.md` in Project B never deletes Project A's row.
3. **`Repository.upsert_kb_section` (`src/engramory/core/repository.py:411`), `Repository.upsert_kb_sections_batch` (`src/engramory/core/repository.py:477`), & `AccessSurface` Audit Recording (`src/engramory/access/surface.py:373, 512`):**
   - In both repository methods, update `project_id = COALESCE(EXCLUDED.project_id, kb_sections.project_id)` and add `WHERE kb_sections.project_id IS NULL OR kb_sections.project_id = EXCLUDED.project_id` to `ON CONFLICT (tenant_id, doc_id, citation, version) DO UPDATE`.
   - In `upsert_kb_section`, if `fetchone()` is `None`, raise `ValidationError` on cross-project `(tenant_id, doc_id, citation, version)` collision.
   - In `upsert_kb_sections_batch`, if `len(rows) != len(chunk)`, raise `ValidationError` so batch conflicts also fail closed.
   - In `AccessSurface.knowledge_ingest` (`src/engramory/access/surface.py:373`), `AccessSurface.knowledge_ingest_batch` (`src/engramory/access/surface.py:427`), and `AccessSurface.docs_ingest_runbook` (`src/engramory/access/surface.py:512`), catch `ValidationError` from `upsert_kb_*`, record an explicit security denial via `await self._repo.record_audit(..., allowed=False, reason=f"cross_project_collision: {exc}")`, and raise `GovernedWriteRejected(str(exc))` from `exc`.

### Phase 2: Project-Scoped SDD & Docs Graph Isolation & TOFU Squatting Prevention (PostgreSQL + Neo4j)

1. **PostgreSQL Graph Ownership, TOFU Squatting Prevention, & Traversal (`src/engramory/core/repository.py` & `src/engramory/core/kb_graph.py`):**
   - In `extract_sdd_graph` (`src/engramory/core/kb_graph.py:163`), add optional `namespace_by_project: bool = False`: when `namespace_by_project=True` and `project_id` is non-empty and not in `("default", "core")`, prefix extracted entity and relation IDs as `f"{project_id}:{doc_id}"` while storing `properties["sdd_id"] = doc_id` so multi-project repositories in the same tenant never collide on `ADR-001` or `SPEC-001` (preventing Trust-on-First-Use squatting while keeping default single-project IDs unchanged).
   - In `Repository.upsert_graph_entity` (`src/engramory/core/repository.py:789`) and `Repository.bulk_upsert_graph_entities` (`src/engramory/core/repository.py:851`), guard `ON CONFLICT (tenant_id, id) DO UPDATE` with:

     ```sql
     WHERE graph_entities.properties->>'project_id' IS NULL
        OR graph_entities.properties->>'project_id' = EXCLUDED.properties->>'project_id'
     ```

   - In `Repository.upsert_graph_relation` (`src/engramory/core/repository.py:777`) and `Repository.bulk_upsert_graph_relations` (`src/engramory/core/repository.py:890`), guard `ON CONFLICT (tenant_id, src_id, dst_id, rel_type) DO UPDATE` with:

     ```sql
     WHERE graph_relations.properties->>'project_id' IS NULL
        OR graph_relations.properties->>'project_id' = EXCLUDED.properties->>'project_id'
     ```

     (and in `upsert_graph_relation`, if `fetchone()` is `None`, raise `ValidationError` on cross-project conflict).
   - Add optional `project_id: str | None = None` to `Repository.list_graph_entities` (`src/engramory/core/repository.py:1025`), `Repository.find_ancestors` (`src/engramory/core/repository.py:1102`), and `Repository.find_descendants` (`src/engramory/core/repository.py:1185`). When `project_id` is not `None`, match seed `id` against `id = %(id)s OR id = %(prefixed_id)s` (`f"{project_id}:{id}"`) and enforce `(e.properties->>'project_id' IS NULL OR e.properties->>'project_id' = %(project_id)s)` on both the seed node and all traversed/returned nodes `e` (preserving backward compatibility with unscoped/tenant-wide ADR/test graph nodes where `properties->>'project_id'` is `NULL` while blocking any node carrying a foreign `project_id`).
2. **GraphPort & Neo4j Adapter (`src/engramory/ports/graph.py` & `src/engramory/adapters/dev/graph_neo4j.py`):**
   - In `Neo4jGraphAdapter.upsert_node` (`src/engramory/adapters/dev/graph_neo4j.py:82`), guard the `SET` clause so an existing node with a non-null `n.project_id` is only updated when `$props.project_id IS NULL OR n.project_id = $props.project_id`:

     ```cypher
     MERGE (n:Entity {uid: $uid})
     ON CREATE SET n.id = $id, n.tenant_id = $tenant_id, n<labels>, n += $props
     ON MATCH SET n += CASE
         WHEN n.project_id IS NULL OR n.project_id = $project_id THEN $props
         ELSE {}
     END
     ```

   - Add optional `project_id: str | None = None` to `GraphPort.neighbors` (`src/engramory/ports/graph.py:26`), `GraphPort.ancestors` (`src/engramory/ports/graph.py:32`), and `GraphPort.descendants` (`src/engramory/ports/graph.py:38`).
   - Update `Neo4jGraphAdapter.neighbors` (`src/engramory/adapters/dev/graph_neo4j.py:158`), `Neo4jGraphAdapter.ancestors` (`src/engramory/adapters/dev/graph_neo4j.py:223`), and `Neo4jGraphAdapter.descendants` (`src/engramory/adapters/dev/graph_neo4j.py:288`) to bind `$project_id` (matching seed `id` or `f"{project_id}:{id}"` when `project_id` is set) and filter `AND ($project_id IS NULL OR ((n.project_id IS NULL OR n.project_id = $project_id) AND (m.project_id IS NULL OR m.project_id = $project_id)))`.
3. **AccessSurface Graph Scoping (`src/engramory/access/surface.py`):**
   - In `_extract_layer_prefix` (`src/engramory/access/surface.py:100`), strip any `"project_id:"` prefix before splitting on `"-"` (`nid = str(node.get("id", "")).split(":", 1)[-1]`) so `namespace_by_project=True` IDs (`"platform-engramory:SPEC-001"`) extract layer `"SPEC"` accurately.
   - In `AccessSurface.graph_neighbors` (`src/engramory/access/surface.py:207`), `AccessSurface.context_slice` (`src/engramory/access/surface.py:228`), and `AccessSurface.blast_radius` (`src/engramory/access/surface.py:278`), compute `eff_project_id = ctx.project_id if scope in ("agent", "project") else None` and pass `project_id=eff_project_id` to both `self._graph` and `self._repo`.
   - Also pass `tenant_id=ctx.tenant_id, project_id=ctx.project_id` in `AccessSurface.memory_search` (`src/engramory/access/surface.py:181`) when `expand_graph=True`.

### Phase 3: Scope-Hierarchy, `domain_id`, & Provenance Enforcement in `slice_runbook` & `search_kb_sections`

1. **`Repository.slice_runbook` (`src/engramory/core/repository.py:1463`) & `AccessSurface.docs_slice_runbook` (`src/engramory/access/surface.py:636`):**
   - Add `scope: str = "project"` and `domain_id: str | None = None` parameters to `Repository.slice_runbook`, and include `d.project_id` and `d.scope` in the `SELECT` projection so callers and agents have explicit provenance for every returned runbook step.
   - Remove the permissive `OR d.project_id IS NULL` clause (`src/engramory/core/repository.py:1363`) and replace with strict execution-boundary scoping:
     - When `scope in ("agent", "project")`: require `(%(project_id)s::text IS NULL OR d.project_id = %(project_id)s)` (do **not** auto-inject cross-project `d.scope = 'space'` executable shell steps into a project-scoped `alert_id` query).
     - When `scope == "domain"`: require `((d.scope = 'domain' AND (%(domain_id)s::text IS NULL OR d.domain_id = %(domain_id)s)) OR d.scope = 'space' OR (%(project_id)s::text IS NOT NULL AND d.project_id = %(project_id)s))`.
     - When `scope == "space"`: allow `(d.scope = 'space' OR (%(project_id)s::text IS NOT NULL AND d.project_id = %(project_id)s))`.
   - Forward `scope=scope` from `AccessSurface.docs_slice_runbook` (`src/engramory/access/surface.py:654`) to `self._repo.slice_runbook(...)`.
2. **`Repository.search_kb_sections` (`src/engramory/core/repository.py:634`) & `AccessSurface.knowledge_search` (`src/engramory/access/surface.py:490`):**
   - Add optional `domain_id: str | None = None` to `Repository.search_kb_sections` and `AccessSurface.knowledge_search`, and replace the separate `if project_id is not None:` and `if scope is not None:` equality checks (`src/engramory/core/repository.py:627-633`) with unified scope-hierarchy clauses:
     - When `scope == "space"`: require `(scope = 'space' OR (%(project_id)s::text IS NOT NULL AND project_id = %(project_id)s))`.
     - When `scope == "domain"`: require `((scope = 'domain' AND (%(domain_id)s::text IS NULL OR domain_id = %(domain_id)s)) OR scope = 'space' OR (%(project_id)s::text IS NOT NULL AND project_id = %(project_id)s))`.
     - When `scope in (None, "agent", "project")`: if `project_id is not None`, require `(project_id = %(project_id)s OR scope = 'space')` (and if `scope is not None`, `scope IN (%(scope)s, 'space')`).

### Phase 4: Per-Project Filesystem Confinement, Symlink Escape Guard, & MCP `sdd_ingest` Authorization

1. **`resolve_confined_repo_path` & Symlink Escape Guard (`src/engramory/core/docs_tools.py:210, 980, 1201, 1353` & `src/engramory/core/kb_graph.py:163, 665`):**
   - Add optional `project_id: str | None = None` and `enforce_project_boundary: bool = False` to `resolve_confined_repo_path`.
   - Redact resolved host paths (`candidate` and `base`) from `ValidationError` messages in `resolve_confined_repo_path` so path-validation failures never act as a host filesystem oracle (`f"Path '{path}' is outside allowed boundary"`).
   - When `enforce_project_boundary=True`:
     - If `not project_id or not project_id.strip()` or `"/" in project_id` or `"\\" in project_id` or `project_id.strip() in (".", "..")`: raise `ValidationError("Valid project_id is required when enforce_project_boundary=True")`.
     - Compute `effective_base = base if base.name == project_id else (base / project_id).resolve()`, and verify `effective_base.is_relative_to(base)`.
     - Resolve relative `path` inputs against `effective_base` (unless `raw` already starts with `project_id` relative to `base`), and verify both `candidate.is_relative_to(base)` and `candidate.is_relative_to(effective_base)`.
   - Add a helper `_is_within_root(file_path: Path, root: Path) -> bool` (`file_path.resolve().is_relative_to(root.resolve())`) and filter every discovered Markdown/YAML file in `lint_repo` (`src/engramory/core/docs_tools.py:1012`), `smoke_run_green_steps`, `slice_local_runbooks`, `check_repo_drift` (`src/engramory/core/docs_tools.py:1201`), `export_repo_llms` (`src/engramory/core/docs_tools.py:1353`), `extract_sdd_graph` (`src/engramory/core/kb_graph.py:163`), and `extract_docs_graph` (`src/engramory/core/kb_graph.py:665`) so planted symlinks inside a repository cannot escape to read sibling projects.
2. **MCP Reference Monitor (`sdd_ingest` & `graph_neighbors`) & Path Confinement (`src/engramory/access/authz.py:15` & `src/engramory/mcp/server.py:317, 694`):**
   - Add `"sdd_ingest"` to `KNOWN_ACTIONS` (`src/engramory/access/authz.py:15`).
   - In `McpServer.handle_call_tool` (`src/engramory/mcp/server.py:388`), forward `scope=arguments.get("scope", "project")` in `graph_neighbors`.
   - In `McpServer.handle_call_tool` for `sdd_ingest` (`src/engramory/mcp/server.py:427`), enforce `await self._surface._decide(call_ctx, "sdd_ingest", TargetScope(tenant_id=call_ctx.tenant_id, scope=str(arguments.get("scope", "project"))))` before checking `self._ingest_fn`, and validate `sdd_root` through `resolve_confined_repo_path(str(sdd_root), project_id=call_ctx.project_id, enforce_project_boundary=enforce_project)`.
   - In `McpServer._handle_docs_tool` (`src/engramory/mcp/server.py:694`), compute `enforce_project = self._auth_enabled and ("space" not in call_ctx.scopes)` and pass `project_id=call_ctx.project_id, enforce_project_boundary=enforce_project` into every `resolve_confined_repo_path(...)` call.

### Phase 5: Cross-Project Security & Leakage Regression Tests

1. Add `test_multiproject_isolation_and_leakage_guards` in `tests/test_docs_tools.py`:
   - Verify `Repository.upsert_kb_document`, `upsert_kb_section`, and `upsert_kb_sections_batch` reject cross-project `doc_id` hijacks (including `project_id=None` clobber attempts) with `ValidationError`, and verify `AccessSurface.docs_ingest_runbook` / `knowledge_ingest` record a `cross_project_collision` `AuditRecord` and raise `GovernedWriteRejected`.
   - Verify `GitSyncEngine._sync_changed_doc_metadata` passes `project_id` to `delete_kb_document` so deleting a file in `project-B` never deletes `project-A`'s runbook.
   - Verify `Repository.slice_runbook` (`scope="project"`, `"domain"`, `"space"`) and `Repository.search_kb_sections` enforce strict `project_id` and `domain_id` boundaries and include `project_id`/`scope` provenance on returned steps.
   - Verify `extract_sdd_graph(..., namespace_by_project=True)` prevents TOFU ID collisions across projects, and verify symlinked files pointing outside `repo_root` are skipped by `lint_repo`, `export_repo_llms`, `extract_sdd_graph`, and `extract_docs_graph`.
   - Verify `McpServer` authorizes `sdd_ingest` via `_decide`, forwards `scope` in `graph_neighbors`, redacts host paths from `resolve_confined_repo_path` errors, and rejects cross-project `repo_path` and `sdd_root` access when `auth_enabled=True` and `"space"` is not in `call_ctx.scopes`.

---

## Claim ledger

| # | Claim | Symbol | Citation |
| - | ----- | ------ | -------- |
| 1 | `kb_documents` primary key is `(tenant_id, doc_id)` without `project_id` | `kb_documents` | db/migrations/0008_runbooks_and_doc_governance.sql:6 |
| 2 | `graph_entities` primary key is `(tenant_id, id)` without `project_id` | `graph_entities` | db/migrations/0007_graph_entities_canonical.sql:5 |
| 3 | `parse_markdown_document` uses `fm_dict.get("id")` verbatim before `build_doc_id` | `parse_markdown_document` | src/engramory/core/docs_tools.py:289 |
| 4 | `resolve_confined_repo_path` checks only `candidate.is_relative_to(base)` and leaks resolved host paths in `ValidationError` | `resolve_confined_repo_path` | src/engramory/core/docs_tools.py:210 |
| 5 | `upsert_kb_document` overwrites `project_id = EXCLUDED.project_id` on `(tenant_id, doc_id)` conflict | `upsert_kb_document` | src/engramory/core/repository.py:1295 |
| 6 | `delete_kb_document` deletes by `tenant_id` and `doc_id` without a `project_id` guard | `delete_kb_document` | src/engramory/core/repository.py:1426 |
| 7 | `upsert_kb_section` updates `text` on `(tenant_id, doc_id, citation, version)` conflict without checking `project_id` | `upsert_kb_section` | src/engramory/core/repository.py:411 |
| 8 | `upsert_kb_sections_batch` updates `text` on conflict without checking `project_id` | `upsert_kb_sections_batch` | src/engramory/core/repository.py:477 |
| 9 | `slice_runbook` includes `OR d.project_id IS NULL` and does not filter by `d.scope` or `d.domain_id` | `slice_runbook` | src/engramory/core/repository.py:1463 |
| 10 | `search_kb_sections` filters by exact `project_id` and `scope` equality rather than scope hierarchy | `search_kb_sections` | src/engramory/core/repository.py:634 |
| 11 | `upsert_graph_entity` overwrites `(tenant_id, id)` without checking `properties->>'project_id'` | `upsert_graph_entity` | src/engramory/core/repository.py:789 |
| 12 | `bulk_upsert_graph_entities` overwrites `(tenant_id, id)` without checking `properties->>'project_id'` | `bulk_upsert_graph_entities` | src/engramory/core/repository.py:851 |
| 13 | `list_graph_entities` filters only by `tenant_id` and optional `entity_type` | `list_graph_entities` | src/engramory/core/repository.py:1025 |
| 14 | `find_ancestors` filters recursive lineage only by `tenant_id` | `find_ancestors` | src/engramory/core/repository.py:1102 |
| 15 | `find_descendants` filters recursive blast radius only by `tenant_id` | `find_descendants` | src/engramory/core/repository.py:1185 |
| 16 | `GraphPort` protocol methods `neighbors`, `ancestors`, and `descendants` lack a `project_id` parameter | `GraphPort` | src/engramory/ports/graph.py:13 |
| 17 | `Neo4jGraphAdapter.neighbors` filters only by `tenant_id` | `neighbors` | src/engramory/adapters/dev/graph_neo4j.py:158 |
| 18 | `Neo4jGraphAdapter.ancestors` filters only by `tenant_id` | `ancestors` | src/engramory/adapters/dev/graph_neo4j.py:223 |
| 19 | `Neo4jGraphAdapter.descendants` filters only by `tenant_id` | `descendants` | src/engramory/adapters/dev/graph_neo4j.py:288 |
| 20 | `AccessSurface.graph_neighbors` passes only `tenant_id=ctx.tenant_id` to `self._graph.neighbors` | `graph_neighbors` | src/engramory/access/surface.py:207 |
| 21 | `AccessSurface.context_slice` passes only `tenant_id=ctx.tenant_id` to `ancestors` and `find_ancestors` | `context_slice` | src/engramory/access/surface.py:228 |
| 22 | `AccessSurface.blast_radius` passes only `tenant_id=ctx.tenant_id` to `descendants` and `find_descendants` | `blast_radius` | src/engramory/access/surface.py:278 |
| 23 | `AccessSurface.docs_slice_runbook` does not forward `scope` to `self._repo.slice_runbook` | `docs_slice_runbook` | src/engramory/access/surface.py:636 |
| 24 | `AccessSurface.knowledge_search` unconditionally passes `project_id=ctx.project_id` to `search_kb_sections` | `knowledge_search` | src/engramory/access/surface.py:490 |
| 25 | `McpServer._handle_docs_tool` calls `resolve_confined_repo_path` without per-project subdirectory confinement | `_handle_docs_tool` | src/engramory/mcp/server.py:694 |
| 26 | `McpServer.handle_call_tool` dispatches `sdd_ingest` without `_decide` or `resolve_confined_repo_path` and `graph_neighbors` without `scope` | `handle_call_tool` | src/engramory/mcp/server.py:317 |
| 27 | `NEW@pass2` `GitSyncEngine._sync_changed_doc_metadata` calls `repo.delete_kb_document` without `project_id` | `_sync_changed_doc_metadata` | src/engramory/core/git_sync.py:365 |
| 28 | `NEW@pass2` `upsert_graph_relation` overwrites `(tenant_id, src_id, dst_id, rel_type)` without checking `properties->>'project_id'` | `upsert_graph_relation` | src/engramory/core/repository.py:777 |
| 29 | `NEW@pass2` `bulk_upsert_graph_relations` overwrites `(tenant_id, src_id, dst_id, rel_type)` without checking `properties->>'project_id'` | `bulk_upsert_graph_relations` | src/engramory/core/repository.py:890 |
| 30 | `NEW@pass2` `Neo4jGraphAdapter.upsert_node` merges on `uid = f"{tenant_id}:{id}"` and overwrites `n += $props` across projects | `upsert_node` | src/engramory/adapters/dev/graph_neo4j.py:82 |
| 31 | `NEW@pass3` `_SCOPE_CLAUSES` requires `domain_id = %(domain_id)s` for `"domain"` scope | `_SCOPE_CLAUSES` | src/engramory/core/repository.py:43 |
| 32 | `NEW@pass3` `KNOWN_ACTIONS` omits `"sdd_ingest"` | `KNOWN_ACTIONS` | src/engramory/access/authz.py:15 |
| 33 | `NEW@pass3` `AccessSurface.knowledge_ingest` does not audit cross-project `ValidationError` collisions | `knowledge_ingest` | src/engramory/access/surface.py:373 |
| 34 | `NEW@pass3` `AccessSurface.docs_ingest_runbook` does not audit cross-project `ValidationError` collisions | `docs_ingest_runbook` | src/engramory/access/surface.py:512 |
| 35 | `NEW@pass3` `extract_sdd_graph` emits un-namespaced SDD entity IDs and traverses `layer_dir.glob` without symlink root confinement | `extract_sdd_graph` | src/engramory/core/kb_graph.py:163 |
| 36 | `NEW@pass3` `extract_docs_graph` iterates `rglob("*.md")` without symlink root confinement | `extract_docs_graph` | src/engramory/core/kb_graph.py:665 |
| 37 | `NEW@pass3` `check_repo_drift` iterates `rglob("*.md")` without symlink root confinement | `check_repo_drift` | src/engramory/core/docs_tools.py:1201 |
| 38 | `NEW@pass3` `export_repo_llms` iterates `rglob("*.md")` and `rglob("*.yaml")` without symlink root confinement | `export_repo_llms` | src/engramory/core/docs_tools.py:1353 |
| 39 | `NEW@pass4` `_extract_layer_prefix` splits `node["id"]` on `"-"` without stripping a `"project_id:"` prefix | `_extract_layer_prefix` | src/engramory/access/surface.py:100 |
| 40 | `NEW@pass4` `lint_repo` iterates `rglob("*.md")` without symlink root confinement | `lint_repo` | src/engramory/core/docs_tools.py:1012 |

---

## Review log

### Pass 1 - 2026-11-06

- Self-review pass verifying all 26 citations in the Claim Ledger against the live codebase (`db/migrations/`, `src/engramory/core/`, `src/engramory/ports/`, `src/engramory/adapters/dev/`, `src/engramory/access/`, `src/engramory/mcp/`).

### Pass 2 - 2026-11-06 - independent

- Independent `verified-planning` review (`c70789cb-8857-4677-8338-e98bfbaaa4a6`) verified all 26 citations and identified 6 load-bearing findings:
  1. Folded `GitSyncEngine._sync_changed_doc_metadata` (`src/engramory/core/git_sync.py:365`, Claim 27) into Phase 1.2 so `project_id=project_id` is passed to `repo.delete_kb_document`.
  2. Removed `OR EXCLUDED.project_id IS NULL` from `ON CONFLICT ... DO UPDATE` guards in Phases 1.1, 1.3, and 2.1, used `COALESCE(EXCLUDED.project_id, <table>.project_id)`, and added row-count verification in `upsert_kb_sections_batch`.
  3. Updated Phase 2.1 and Phase 2.2 graph traversal predicates to allow unscoped/tenant-wide entities (`project_id IS NULL OR project_id = ...`) on both seed and target nodes so existing `test_access_surface.py` and `test_adr_knowledge_graph.py` suites remain green while foreign `project_id` nodes are blocked.
  4. Added `upsert_graph_relation` (Claim 28), `bulk_upsert_graph_relations` (Claim 29), and `Neo4jGraphAdapter.upsert_node` (Claim 30) cross-project overwrite guards to Phase 2.1 and Phase 2.2.
  5. Unified the `project_id` and `scope` SQL clauses in `Repository.search_kb_sections` (Phase 3.2) so `scope="project"` does not contradict `OR scope = 'space'`.
  6. Hardened `resolve_confined_repo_path` in Phase 4.1 to fail closed when `enforce_project_boundary=True` and `project_id` is empty/invalid, and to resolve relative paths against `effective_base`.

### Pass 3 - 2026-11-06

- Self-review pass against Google Cloud BeyondCorp/Zanzibar and Anthropic MCP security best practices, folding 6 additional architectural hardening items (`NEW@pass3` Claims 31–38):
  1. Added `domain_id` matching to `scope == "domain"` queries in `Repository.slice_runbook` and `Repository.search_kb_sections` (Claim 31).
  2. Added `namespace_by_project` support in `extract_sdd_graph` and prefixed seed matching in PostgreSQL/Neo4j traversals to prevent TOFU namespace squatting on `ADR-001` / `SPEC-001` across projects (Claim 35).
  3. Added `"sdd_ingest"` to `KNOWN_ACTIONS` (Claim 32), enforced `self._surface._decide(...)` before `sdd_ingest`, and forwarded `scope` in `McpServer.handle_call_tool` `graph_neighbors` (Claim 26).
  4. Restricted `slice_runbook` under `scope in ("agent", "project")` strictly to `d.project_id = %(project_id)s` (no auto-injection of foreign `space` executable runbooks on `alert_id` match) and added `d.project_id, d.scope` provenance columns to `slice_runbook`.
  5. Added `cross_project_collision` `AuditRecord` logging in `AccessSurface.knowledge_ingest`, `knowledge_ingest_batch`, and `docs_ingest_runbook` (Claims 33–34), and redacted host-path oracle strings from `resolve_confined_repo_path` errors.
  6. Added `_is_within_root` symlink confinement across `lint_repo`, `smoke_run_green_steps`, `slice_local_runbooks`, `check_repo_drift` (Claim 37), `export_repo_llms` (Claim 38), `extract_sdd_graph` (Claim 35), and `extract_docs_graph` (Claim 36).

### Pass 4 - 2026-11-06 - independent

- Independent `verified-planning` review (`c70789cb-8857-4677-8338-e98bfbaaa4a6`) verified `NEW@pass3` Claims 31–38 and identified 3 findings, all folded here:
  1. Preserved `"outside allowed boundary"` substring in `resolve_confined_repo_path` error messages (`f"Path '{path}' is outside allowed boundary"`) so `test_resolve_confined_repo_path_blocks_traversal` (`tests/test_docs_tools.py:299`) remains green while redacting host paths.
  2. Replaced prose reference `_iter_markdown_files` with the actual functions `lint_repo` (`src/engramory/core/docs_tools.py:980`, Claim 40), `smoke_run_green_steps`, and `slice_local_runbooks`.
  3. Updated `_extract_layer_prefix` (`src/engramory/access/surface.py:100`, Claim 39) in Phase 2.3 to strip any `"project_id:"` prefix before splitting on `"-"`.

### Pass 5 - 2026-11-06 - independent

- Independent `verified-planning` review (`c70789cb-8857-4677-8338-e98bfbaaa4a6`) re-verified Claims 1–40 (`NEW@pass4` on Claims 39–40) and confirmed all Pass 4 findings are resolved with zero load-bearing findings.
- **Result:** ready
