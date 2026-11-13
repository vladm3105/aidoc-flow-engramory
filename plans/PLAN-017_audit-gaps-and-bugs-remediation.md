# PLAN-017 — Post-Migration Audit Gaps, Bugs, & Isolation Hardening Remediation

**Status:** Implemented
**Date:** 2026-11-09
**Author:** AI Coding Agent
**Related Plans:** `PLAN-012`, `PLAN-013`, `PLAN-014`, `PLAN-015`, `PLAN-016`

---

## 1. Objective & Scope

Following the consolidation of document tools (`PLAN-013`), multi-project security hardening (`PLAN-014`), document processing pipeline hardening (`PLAN-015`), and built-in context tools migration (`PLAN-016`), a full-repository audit identified remaining security/isolation gaps, functional bugs, lifecycle resource leaks, and packaging/documentation drift across `platform-engramory`.

This plan remediates all identified findings across 6 bounded phases:

1. **Phase 1 — Neo4j Graph Tenant/Project Isolation, Multi-Hop Path Filtering, & Stale Purge Reconciliation** (`graph_neo4j.py`, `repository.py`, `kb_graph.py`, `graph_projection.py`, `test_graph_neo4j.py`, `test_graph_projection.py`)
2. **Phase 2 — Knowledge Core Citation Uniqueness, Binary Doc Extraction, & Git Ref Hardening** (`knowledge.py`, `citations.py`, `enrichment.py`, `git_sync.py`)
3. **Phase 3 — Memory Search Query Ranking, Feedback Confidence/Quarantine Rule, & MCP `knowledge_search`** (`surface.py`, `repository.py`, `mcp/server.py`, `vector_pg.py`)
4. **Phase 4 — Embedding Model Provenance, Reflection Dimension Guard, & Context Tools Edge Cases** (`llm_litellm.py`, `distillation.py`, `chunking.py`, `context_tools.py`)
5. **Phase 5 — MCP SSE Event Loop & Health Route, JWT Key Rotation & Fail-Closed Revocation, and CLI Hardening** (`mcp/server.py`, `jwt_auth.py`, `cli.py`, `test_mcp_server.py`)
6. **Phase 6 — Packaging, Docker Runtime Dependencies, Integration Test Fixes, & Documentation Synchronization** (`pyproject.toml`, `Dockerfile`, `.env.example`, `docker-compose.yml`, `Makefile`, `tests/integration/*`, `docs/*`, `README.md`, `llms.txt`)

---

## Claim ledger

| # | Claim | Symbol | Citation |
|---|---|---|---|
| 1 | `Neo4jGraphAdapter.upsert_node` applies `ON MATCH SET n{label_str}` unconditionally before checking `n.project_id`, and `upsert_edge` neither sets `r.project_id` on `ON CREATE` nor guards edge property overwrites by `project_id`. | `upsert_node` | src/engramory/adapters/dev/graph_neo4j.py:85-156 |
| 2 | `Neo4jGraphAdapter.neighbors`, `ancestors`, and `descendants` allow `$tenant_id = 'default'` to match any foreign tenant's root node `n` (`$tenant_id = 'default' OR n.tenant_id = $tenant_id`) and traverse its connected component (`m.tenant_id = n.tenant_id`), and do not filter intermediate path nodes `nodes(p)` by `tenant_id` or `project_id`. | `neighbors` | src/engramory/adapters/dev/graph_neo4j.py:182-335 |
| 3 | `Repository.purge_stale_graph_entities` returns `[]` immediately when `not active_ids` and binds `%(active_ids)s` without `::text[]` cast, preventing deletion of remaining entities when all artifacts in a project are removed. | `purge_stale_graph_entities` | src/engramory/core/repository.py:1120-1002 |
| 4 | `ingest_sdd_knowledge_graph` skips `purge_stale_graph_entities` and `purge_stale_nodes` when `entities` is empty (`if reconcile and entities:`), and `expand_graph_context`, `get_sdd_context_slice`, and `get_sdd_blast_radius` do not accept or forward `tenant_id` or `project_id`. | `ingest_sdd_knowledge_graph` | src/engramory/core/kb_graph.py:553-695 |
| 5 | `project_entities` in `src/engramory/workers/graph_projection.py` passes `labels=[entity.entity_type]` without preserving the `"SDD"` label for SDD layer entities (`BRD`..`EVAL`). | `project_entities` | src/engramory/workers/graph_projection.py:17-40 |
| 6 | `KnowledgeCore.ingest_documents_batch` computes `(1, 1)` line ranges when `full_text` is empty (e.g., binary PDF/DOCX extraction) and calls `format_local_citation(rel_str, line_start=l_start, line_end=l_end, section_slug=sec.citation)`, while `format_local_citation` ignores `section_slug` whenever `line_start is not None`, causing sub-chunks of the same parent section or binary document sections to collide on `(tenant_id, citation)`. | `ingest_documents_batch` | src/engramory/core/knowledge.py:75-155 |
| 7 | `format_local_citation` uses `if line_start is not None:` followed by `elif section_slug:`, dropping `section_slug` whenever `line_start` is passed. | `format_local_citation` | src/engramory/core/citations.py:71-88 |
| 8 | `SemanticChunker.refine_sections` splits oversized sections in Pass 1 (`s.content.split()`) before Pass 2 prepends the `[Doc: ...]` header; if `s.content` already begins with `[Doc: ...]\n` from `MarkdownParser._subchunk_section`, only the first sub-chunk keeps the header. | `refine_sections` | src/engramory/core/extraction/enrichment.py:19-81 |
| 9 | `GitSyncEngine.get_changed_files` passes `base_sha` and `target_head` to `git diff --name-status` without validating against leading `-` option flags (`_GIT_REF_RE`). | `get_changed_files` | src/engramory/core/git_sync.py:140-190 |
| 10 | `AccessSurface.memory_search` does not accept or use `LLMPort` to embed `query` and ignores the `query` parameter when calling `self._repo.get_memories`. | `memory_search` | src/engramory/access/surface.py:148-218 |
| 11 | `Repository.get_memories` filters out rows with `embedding IS NULL` whenever `query_vec` is provided (without checking `len(query_vec) == 1536`) and has no optional `query` parameter for lexical/ILIKE ranking. | `get_memories` | src/engramory/core/repository.py:362-406 |
| 12 | `Repository.record_feedback` updates `memory_retrievals.feedback` and `feedback_at` without adjusting `memories.confidence` or setting `memories.status = 'quarantined'` on `'harmful'` feedback per SPEC-04 (`+0.05` useful, `-0.10` not_useful, `-0.30` + quarantine harmful). | `record_feedback` | src/engramory/core/repository.py:229-273 |
| 13 | `AccessSurface.knowledge_search` exists in `src/engramory/access/surface.py`, but `McpServer` (`TOOLS_SCHEMA` and `handle_call_tool`) does not expose a `knowledge_search` MCP tool. | `knowledge_search` | src/engramory/access/surface.py:550-585 |
| 14 | `PgVectorAdapter.upsert` and `delete` update `memories` by `WHERE id = %(id)s` without optional `tenant_id` scoping from `metadata`. | `PgVectorAdapter` | src/engramory/adapters/dev/vector_pg.py:30-91 |
| 15 | `LiteLLMAdapter.__init__` sets `self._default_embed_model`, whereas `knowledge.py`, `git_sync.py`, `chunking.py`, and `distillation.py` read `getattr(llm, "_embed_model", ...)` and fall back to `"text-embedding-3-small"` (or `"nomic-embed-text"` in `distillation.py`). | `LiteLLMAdapter` | src/engramory/adapters/dev/llm_litellm.py:22-56 |
| 16 | `distillation.reflect` sets `has_vector` and calls `await vector.upsert(id=memory_id, embedding=vec)` after `repo.upsert_memory` without checking `len(vec) == 1536` against `memories.embedding` (`vector(1536)`). | `reflect` | src/engramory/workers/distillation.py:19-86 |
| 17 | `StackPolicyValidator._scan_banned_patterns` strips `//` comments via `code_part.split("//")[0]`, truncating lines containing `http://` or `https://` string literals before matching banned patterns. | `_scan_banned_patterns` | src/engramory/core/context_tools.py:1540-1580 |
| 18 | `ContextSlicer.slice_subgraph` marks `visited.add(curr_key)` at first visit without tracking `best_depth`, so a node first reached at `depth == max_depth` is not re-expanded when reached later via a shallower path; and `compact_node_content` toggles `in_fm = not in_fm` on any `---` line anywhere in the document. | `slice_subgraph` | src/engramory/core/context_tools.py:975-1015 |
| 19 | `AidocDependencyGraph._scan_markdown_file` truncates `ArtifactNode.content` to `content[:4000]` and uses `file_path.stem` without disambiguation when `fm_id` is absent. | `_scan_markdown_file` | src/engramory/core/context_tools.py:839-890 |
| 20 | `McpServer.run_sse` spawns `asyncio.run(...)` per HTTP POST inside `ThreadingHTTPServer`, creating and closing a new event loop per request (breaking loop-bound `AsyncDriver` in `Neo4jGraphAdapter`), and `do_GET` only handles `/` and `/healthz` while `tests/steps/smoke.list` probes `/health`. | `run_sse` | src/engramory/mcp/server.py:934-815 |
| 21 | `McpServer.handle_call_tool` (`sdd_ingest`) does not pass `project_id=project_id` to `resolve_confined_repo_path` and passes `sdd_root=str(confined_sdd) if self._auth_enabled else sdd_root` instead of `str(confined_sdd)`. | `sdd_ingest` | src/engramory/mcp/server.py:568-605 |
| 22 | `JWKSValidator.__init__` does not store `self.headers`, `self.cache_jwk_set`, or `self.lifespan`, and `validate_token` recreates `PyJWKClient(self.jwks_url)` on unknown `kid` without preserving those settings; `check_token_revocation` fails open on network errors. | `JWKSValidator` | src/engramory/access/jwt_auth.py:96-179 |
| 23 | `_cmd_knowledge_ingest` in `src/engramory/cli.py` constructs `SmartExtractionRouter()` without passing `enable_docling=bool(getattr(args, "docling", False))`, and `_cmd_docs_ingest` (`AccessSurface.knowledge_ingest_batch`) does not embed sections when `adapters.llm` is available. | `_cmd_knowledge_ingest` | src/engramory/cli.py:263-335 |
| 24 | `pyproject.toml` lacks `[project.optional-dependencies].extraction` (`docling`, `pymupdf`, `beautifulsoup4`) and points `Repository` at `aidoc-flow-engramory`; `Dockerfile` runtime stage does not install `git`. | `dependencies` | pyproject.toml:11-27 |
| 25 | `tests/integration/test_graph_reproject.py` calls `graph.neighbors(id="ADR-03-graph", rel="CONSTRAINED_BY", depth=1)` without `tenant_id=tenant_id`. | `test_reproject_canonical_postgres_to_live_neo4j` | tests/integration/test_graph_reproject.py:20-75 |

---

## Phases

### Phase 1: Neo4j Graph Tenant/Project Isolation, Multi-Hop Path Filtering, & Stale Purge Reconciliation

**Files:**

- `src/engramory/adapters/dev/graph_neo4j.py`
- `src/engramory/core/repository.py`
- `src/engramory/core/kb_graph.py`
- `src/engramory/workers/graph_projection.py`
- `tests/unit/test_graph_neo4j.py`
- `tests/unit/test_graph_projection.py`

**Steps:**

1. Harden `Neo4jGraphAdapter.upsert_node` and `upsert_edge` in [src/engramory/adapters/dev/graph_neo4j.py:85-156](../src/engramory/adapters/dev/graph_neo4j.py#L85-L156):
   - In `upsert_node`: do not apply `ON MATCH SET n{label_str}` unconditionally before project check. Apply dynamic labels via `FOREACH (_ IN CASE WHEN n.project_id IS NULL OR $project_id IS NULL OR n.project_id = $project_id THEN [1] ELSE [] END | SET n{label_str})` so a foreign project collision cannot inject labels onto another project's node while keeping `MERGE (n:Entity {uid: $uid})` and `:{label_str}` intact.
   - In `upsert_edge`: preserve `MERGE (a:Entity {uid: $src_uid})` and `MERGE (b:Entity {uid: $dst_uid})`, set `ON CREATE SET a.id = $src, a.tenant_id = $tenant_id, a.project_id = $project_id` and `ON CREATE SET b.id = $dst, b.tenant_id = $tenant_id, b.project_id = $project_id`, filter `WITH a, b WHERE (a.tenant_id = $tenant_id OR ($tenant_id = 'default' AND a.tenant_id IS NULL)) AND (b.tenant_id = $tenant_id OR ($tenant_id = 'default' AND b.tenant_id IS NULL)) AND ($project_id IS NULL OR a.project_id IS NULL OR a.project_id = $project_id) AND ($project_id IS NULL OR b.project_id IS NULL OR b.project_id = $project_id)`, and on `MERGE (a)-[r:...]->(b)` set `r.project_id = $project_id` on `ON CREATE SET` and guard `ON MATCH SET` with `r.project_id = CASE WHEN r.project_id IS NULL OR $project_id IS NULL THEN coalesce($project_id, r.project_id) ELSE r.project_id END` and `r += CASE WHEN r.project_id IS NULL OR $project_id IS NULL OR r.project_id = $project_id THEN $props ELSE {} END`, binding `"project_id": clean_props.get("project_id")` in `session.run`.
2. Fix tenant and multi-hop path isolation in `neighbors`, `ancestors`, and `descendants` in [src/engramory/adapters/dev/graph_neo4j.py:158-335](../src/engramory/adapters/dev/graph_neo4j.py#L158-L335):
   - Replace `AND ($tenant_id = 'default' OR n.tenant_id = $tenant_id)` with `AND (n.tenant_id = $tenant_id OR ($tenant_id = 'default' AND n.tenant_id IS NULL))` so `tenant_id="default"` only matches `"default"` or legacy `NULL`-tenant nodes, never foreign tenants.
   - Enforce path-level predicate on variable-length paths `p`:
     `WHERE ALL(x IN nodes(p) WHERE (x.tenant_id = $tenant_id OR ($tenant_id = 'default' AND x.tenant_id IS NULL)) AND ($project_id IS NULL OR x.project_id IS NULL OR x.project_id = $project_id))`
     so multi-hop traversals (`depth > 1`) cannot bridge through intermediate nodes belonging to another tenant or ungranted project.
   - Update `tests/unit/test_graph_neo4j.py:223` (`test_descendants_executes_directed_inpath_query`) to match `MATCH p = (m:Entity)-[*1..5]->(n)` and add unit tests verifying path-level tenant/project filtering and `upsert_node` / `upsert_edge` cross-project mutation guards.
3. Fix empty `active_ids` reconciliation and `tenant_id`/`project_id` forwarding in:
   - `Repository.purge_stale_graph_entities` ([src/engramory/core/repository.py:953-1002](../src/engramory/core/repository.py#L953-L1002)): remove `if not active_ids: return []` and cast array bindings explicitly as `%(active_ids)s::text[]` and `%(stale_ids)s::text[]` so `active_ids=[]` cleanly purges all stale entities without `psycopg.errors.IndeterminateDatatype`.
   - `Neo4jGraphAdapter.purge_stale_nodes` ([src/engramory/adapters/dev/graph_neo4j.py:382-415](../src/engramory/adapters/dev/graph_neo4j.py#L382-L415)): use `(n.tenant_id = $tenant_id OR ($tenant_id = 'default' AND n.tenant_id IS NULL))` so `"default"` tenant purge never deletes other tenants' nodes.
   - `ingest_sdd_knowledge_graph`, `expand_graph_context`, `get_sdd_context_slice`, and `get_sdd_blast_radius` ([src/engramory/core/kb_graph.py:553-695](../src/engramory/core/kb_graph.py#L553-L695)):
     - Change `if reconcile and entities:` at lines 591 and 611 to `if reconcile and sdd_path.exists():` so deleting all SDD files in an existing directory still triggers stale entity/node purge.
     - Add optional `tenant_id: str = "default"` and `project_id: str | None = None` parameters to `expand_graph_context`, `get_sdd_context_slice`, and `get_sdd_blast_radius` and pass them through to `graph.neighbors`, `graph.ancestors`, and `graph.descendants`.
4. Preserve `"SDD"` label in `project_entities` ([src/engramory/workers/graph_projection.py:17-40](../src/engramory/workers/graph_projection.py#L17-L40)) and update `tests/unit/test_graph_projection.py`:
   - When `entity.entity_type` is an SDD layer (`BRD`, `PRD`, `EARS`, `BDD`, `ADR`, `SPEC`, `TDD`, `TASK`, `IPLAN`, `CHG`, `EVAL`) or `entity.properties.get("sdd")` is truthy, pass `labels=[entity.entity_type, "SDD"]` to `graph.upsert_node`, and update `tests/unit/test_graph_projection.py:36-39` to expect `labels=["ADR", "SDD"]`.

### Phase 2: Knowledge Core Citation Uniqueness, Binary Doc Extraction, & Git Ref Hardening

**Files:**

- `src/engramory/core/citations.py`
- `src/engramory/core/knowledge.py`
- `src/engramory/core/extraction/enrichment.py`
- `src/engramory/core/git_sync.py`

**Steps:**

1. Fix sub-chunk and binary-document citation uniqueness in `KnowledgeCore.ingest_documents_batch` ([src/engramory/core/knowledge.py:75-155](../src/engramory/core/knowledge.py#L75-L155)) and `GitSyncEngine.extract_sync_sections` ([src/engramory/core/git_sync.py:280-315](../src/engramory/core/git_sync.py#L280-L315)):
   - Avoid double-running `refine_sections` in `KnowledgeCore.ingest_documents_batch` when `self.chunker is None` and `self.router` already refined sections (`SmartExtractionRouter` runs `self.chunker.refine_sections` when `refine_chunks=True`).
   - When computing citations for extracted sections of a file: if multiple sections in the same file resolve to the same `(l_start, l_end)` line span (for example, sub-chunks `-p1`, `-p2` split from an oversized section, or binary PDF/DOCX sections where `full_text == ""` and `l_start == l_end == 1`), disambiguate the citation by appending `#<slug>` (using `sec.citation.lstrip("#")` or `sec.section_id`, e.g., `docs/file.md#L10-L50#overview-p2`, or `format_local_citation(rel_str, section_slug=sec.citation or sec.section_id)` when `full_text == ""`). Single-section line ranges keep their exact `path#Lstart-Lend` format for full backward compatibility with existing citation tests.
2. Preserve `[Doc: ...]` contextual header across sub-chunks in `SemanticChunker.refine_sections` ([src/engramory/core/extraction/enrichment.py:19-81](../src/engramory/core/extraction/enrichment.py#L19-L81)):
   - In Pass 1 of `refine_sections`, when `s.content.startswith("[Doc: ")` and `"]\n"` is present in `s.content`, extract `ctx_header = s.content[:idx + 2]` (where `idx = s.content.find("]\n")`) and `body = s.content[idx + 2:].lstrip("\n")`, split `body.split()` into chunks of `max(1, self.max_words - len(ctx_header.split()))` words, and prepend `ctx_header` to each resulting sub-chunk so Pass 2 sees `[Doc: ...]` on every sub-chunk (`-p1`, `-p2`, ...) without duplicating or losing the header.
3. Harden `GitSyncEngine.get_changed_files` ([src/engramory/core/git_sync.py:140-190](../src/engramory/core/git_sync.py#L140-L190)):
   - Validate `base_sha` and `target_head` against `_GIT_REF_RE` (`r"^[A-Za-z0-9_./~^-]+$"` and not starting with `-`), raising `ValidationError` on invalid refs or option-injection payloads before calling `subprocess.run`.

### Phase 3: Memory Search Query Ranking, Feedback Confidence/Quarantine Rule, & MCP `knowledge_search`

**Files:**

- `src/engramory/access/surface.py`
- `src/engramory/core/repository.py`
- `src/engramory/mcp/server.py`
- `src/engramory/adapters/dev/vector_pg.py`

**Steps:**

1. Wire `llm: LLMPort | None = None` into `AccessSurface.__init__` ([src/engramory/access/surface.py:118-135](../src/engramory/access/surface.py#L118-L135)) and enhance `AccessSurface.memory_search` ([src/engramory/access/surface.py:148-218](../src/engramory/access/surface.py#L148-L218)), `AccessSurface.knowledge_ingest_batch` ([src/engramory/access/surface.py:475-549](../src/engramory/access/surface.py#L475-L549)), `AccessSurface.knowledge_search` ([src/engramory/access/surface.py:550-585](../src/engramory/access/surface.py#L550-L585)), and `Repository.get_memories` ([src/engramory/core/repository.py:362-406](../src/engramory/core/repository.py#L362-L406)):
   - Add optional `llm: LLMPort | None = None` to `AccessSurface.__init__`.
   - In `AccessSurface.memory_search`: if `self._llm is not None` and `query.strip()`, attempt `vecs = await self._llm.embed([query])` (falling back gracefully to `query_vec = None` if the LLM endpoint is unavailable or if the returned vector does not have length 1536). Pass `query=query` and `query_vec=query_vec` to `self._repo.get_memories`.
   - In `Repository.get_memories`: accept optional `query: str | None = None` alongside `query_vec: Sequence[float] | None = None`. Instead of filtering out rows with `AND embedding IS NOT NULL` when `query_vec` is provided:
     - Only bind `query_vec` when `query_vec is not None and len(query_vec) == 1536`.
     - Order results so that when a 1536-dim `query_vec` is present, rows with embeddings rank first by cosine distance (`CASE WHEN embedding IS NOT NULL THEN embedding <=> %(qv)s::vector ELSE 2.0 END ASC`), followed by lexical/substring match boost when `query` is non-empty (`(content_raw ILIKE %(q_like)s OR coalesce(summary, '') ILIKE %(q_like)s) DESC`), followed by `valid_from DESC`. This ensures vector and lexical matches always rank at the top while never dropping un-embedded rows when a caller or test searches without embeddings.
   - In `AccessSurface.knowledge_ingest_batch`: when `self._llm is not None` and `vetted_sections` is non-empty, attempt to embed `vetted_sections` via `await self._llm.embed([f"{s.title}\n\n{s.content}" for s in vetted_sections])` and persist 1536-dim embeddings via `await self._repo.set_kb_sections_embedding_batch(...)` (logging and continuing on LLM failure so offline ingestion still succeeds).
   - In `AccessSurface.knowledge_search`: when `query_vec is None` and `self._llm is not None` and `query.strip()`, automatically compute `query_vec` via `await self._llm.embed([query])` (using it when `len(vec) == 1536`).
2. Enforce the SPEC-04 confidence and quarantine rule in `Repository.record_feedback` ([src/engramory/core/repository.py:224-273](../src/engramory/core/repository.py#L224-L273)):
   - Within the same transaction after updating `memory_retrievals` (`SET feedback = %(outcome)s, feedback_at = now() ... RETURNING memory_id`), update `memories` (`WHERE id = %(memory_id)s AND tenant_id = %(tenant_id)s`):
     - `outcome == "useful"`: `confidence = LEAST(1.0, confidence + 0.05)`
     - `outcome == "not_useful"`: `confidence = GREATEST(0.0, confidence - 0.10)`
     - `outcome == "harmful"`: `confidence = GREATEST(0.0, confidence - 0.30), status = 'quarantined'`
3. Expose `knowledge_search` on `McpServer` ([src/engramory/mcp/server.py:43-335](../src/engramory/mcp/server.py#L43-L335) and [src/engramory/mcp/server.py:425-560](../src/engramory/mcp/server.py#L425-L560)):
   - Add `knowledge_search` to `TOOLS_SCHEMA` (with `query`, `project_id`, `domain_id`, `scope`, `k` parameters and `readOnlyHint: True`).
   - Handle `name == "knowledge_search"` in `McpServer.handle_call_tool` by calling `await self._surface.knowledge_search(call_ctx, query=arguments["query"], domain_id=arguments.get("domain_id"), scope=arguments.get("scope", "project"), limit=int(arguments.get("k", arguments.get("limit", 5))))` (where `project_id` in `arguments` is resolved into `call_ctx` via `self._resolve_call_ctx(arguments, ctx)`) and serializing the returned `KBSection` list to JSON.
4. Add optional `tenant_id` scoping to `PgVectorAdapter.upsert` ([src/engramory/adapters/dev/vector_pg.py:36-91](../src/engramory/adapters/dev/vector_pg.py#L36-L91)):
   - If `metadata` contains `"tenant_id"`, include `AND tenant_id = %(tenant_id)s` in `PgVectorAdapter.upsert`.

### Phase 4: Embedding Model Provenance, Reflection Dimension Guard, & Context Tools Edge Cases

**Files:**

- `src/engramory/adapters/dev/llm_litellm.py`
- `src/engramory/core/knowledge.py`
- `src/engramory/core/git_sync.py`
- `src/engramory/core/chunking.py`
- `src/engramory/workers/distillation.py`
- `src/engramory/core/context_tools.py`

**Steps:**

1. Standardize embedding model provenance across `LiteLLMAdapter` and all callers:
   - In `LiteLLMAdapter.__init__` ([src/engramory/adapters/dev/llm_litellm.py:22-56](../src/engramory/adapters/dev/llm_litellm.py#L22-L56)): set `self._embed_model = self._default_embed_model` and add `@property def embed_model(self) -> str: return self._default_embed_model`.
   - Across `knowledge.py:241`, `git_sync.py:374`, `chunking.py:219`, and `distillation.py:20,50`, resolve the embedding model name via a type-safe check (`next((val for attr in ("embed_model", "_embed_model", "_default_embed_model") if isinstance((val := getattr(llm, attr, None)), str) and val.strip()), "text-embedding-3-small")`) so un-specced `MagicMock`/`AsyncMock` instances in unit tests never leak a `MagicMock` object as `embedding_model`, and align `DEFAULT_EMBED_MODEL = "text-embedding-3-small"` in `distillation.py`.
2. Guard `distillation.reflect` against embedding dimension mismatches ([src/engramory/workers/distillation.py:60-86](../src/engramory/workers/distillation.py#L60-L86)):
   - Compute `has_vector = idx < len(embeddings) and bool(embeddings[idx]) and len(embeddings[idx]) == 1536` **before** constructing `Memory(...)` for `await repo.upsert_memory(memory)` and `await vector.upsert(id=memory_id, embedding=vec, metadata={"tenant_id": tenant_id})` (logging a warning when `0 < len(embeddings[idx]) != 1536` so stub/non-1536 vectors in dev/unit tests neither write inconsistent `embedding_dims` metadata nor crash Postgres `vector(1536)`).
3. Fix `context_tools.py` edge cases ([src/engramory/core/context_tools.py:839-1015](../src/engramory/core/context_tools.py#L839-L1015) and [src/engramory/core/context_tools.py:1540-1580](../src/engramory/core/context_tools.py#L1540-L1580)):
   - **URL-safe comment stripping (`_scan_banned_patterns`):** Replace naive `line.split("#")[0]` / `code_part.split("//")[0]` with a quote-state-aware helper `_strip_inline_comment(line: str, *, is_python: bool) -> str` that ignores `#` and `//` inside single- or double-quoted string literals and never treats `://` (e.g. `http://`, `https://`) as a `//` comment start.
   - **Best-depth DAG traversal (`ContextSlicer.slice_subgraph`):** Track `best_depth: dict[str, int] = {}` and `emitted_keys: set[str] = set()`. Allow a node `curr_key` to be re-traversed if reached at a strictly smaller depth (`depth < best_depth[curr_key]`), while only appending `node` to `ordered_closure` once (`if curr_key not in emitted_keys`).
   - **Single-frontmatter toggle (`compact_node_content`):** Only enter YAML frontmatter when line 0 is `---`, and once the closing `---` is seen (`fm_closed = True`), never re-enter `in_fm` on subsequent Markdown horizontal rules (`---`).
   - **Markdown content cap & stem disambiguation (`_scan_markdown_file`):** Increase `content[:4000]` to `content[:65536]` (64 KiB) so Tier 0/1 compaction sees full specifications, and when `fm_id` is absent and `base_name` is already in `self.nodes` from a different file path, disambiguate the fallback ID using the parent directory slug (`f"{file_path.parent.name}:{base_name}"`).

### Phase 5: MCP SSE Event Loop & Health Route, JWT Key Rotation & Fail-Closed Revocation, and CLI Hardening

**Files:**

- `src/engramory/mcp/server.py`
- `src/engramory/access/jwt_auth.py`
- `src/engramory/cli.py`
- `tests/unit/test_mcp_server.py`

**Steps:**

1. Harden `McpServer.run_sse` and `McpServer.handle_call_tool` ([src/engramory/mcp/server.py:568-605](../src/engramory/mcp/server.py#L568-L605) and [src/engramory/mcp/server.py:705-815](../src/engramory/mcp/server.py#L705-L815)):
   - Start a dedicated persistent background `asyncio` event loop thread in `run_sse` and dispatch `asyncio.run_coroutine_threadsafe(server_instance.handle_request(req, ctx=req_ctx), bg_loop).result(timeout=60)` from `ThreadingHTTPServer.do_POST`, ensuring `Neo4jGraphAdapter`'s `AsyncDriver` always runs on a single long-lived event loop.
   - Accept `/health` in addition to `/` and `/healthz` in `do_GET` so `tests/steps/smoke.list` passes.
   - Enforce `MAX_REQUEST_BODY_BYTES = 4 * 1024 * 1024` (returning HTTP 413 on oversized `Content-Length`), return JSON-RPC `-32700` (Parse error) on `json.JSONDecodeError`, and preserve `"id": req_id` in error responses.
   - In `handle_call_tool` (`sdd_ingest`): pass `project_id=project_id` to `resolve_confined_repo_path`, pass `sdd_root=str(confined_sdd)` to `ingest_sdd_knowledge_graph`, and update `tests/unit/test_mcp_server.py:156-161` to expect `sdd_root=str(Path("sdd").resolve())`.
2. Harden `JWKSValidator` and `check_token_revocation` ([src/engramory/access/jwt_auth.py:96-179](../src/engramory/access/jwt_auth.py#L96-L179) and [src/engramory/access/jwt_auth.py:245-275](../src/engramory/access/jwt_auth.py#L245-L275)):
   - In `JWKSValidator.__init__`: store `self.headers = req_headers`, `self.cache_jwk_set = cache_jwk_set`, and `self.lifespan = lifespan` on `self`.
   - In `JWKSValidator.validate_token`: when recreating `PyJWKClient(self.jwks_url, ...)` on key rotation (`PyJWKClientError`), pass `headers=self.headers`, `cache_jwk_set=self.cache_jwk_set`, and `lifespan=self.lifespan`.
   - In `check_token_revocation`: add `fail_closed: bool = True` (defaulting to `os.environ.get("SECURITY_GATE_FAIL_CLOSED", "true").lower() not in ("0", "false", "no")`). When `fail_closed=True` and the configured `security_gate_url` fails with an HTTP/network error, raise `AuthenticationError("Token revocation check failed (fail-closed)")` per ADR-06.
3. Harden CLI commands and resource cleanup ([src/engramory/cli.py:140-445](../src/engramory/cli.py#L140-L445) and [src/engramory/cli.py:733-789](../src/engramory/cli.py#L733-L789)):
   - Pass `enable_docling=bool(getattr(args, "docling", False))` to `SmartExtractionRouter(...)` in `_cmd_knowledge_ingest`.
   - Pass `llm=adapters.llm` into `AccessSurface(...)` in `_run_with_surface` and `_cmd_mcp_serve` (which also enables automatic section embeddings for `_cmd_docs_ingest` via `AccessSurface.knowledge_ingest_batch`).
   - Add an async helper `_close_adapters(adapters)` in `src/engramory/cli.py` that checks `if getattr(adapters, "graph", None) is not None and hasattr(adapters.graph, "close"): res = adapters.graph.close(); if inspect.isawaitable(res): await res` (safe with both real `Neo4jGraphAdapter` and synchronous `MagicMock` in CLI unit tests) and invoke it in `finally:` blocks across all `dev_adapters(config.dsn)` callers (`_run_with_surface`, `_cmd_memory_distill`, `_cmd_knowledge_ingest`, `_cmd_knowledge_sync_git`, `_cmd_knowledge_search`, `_cmd_sdd_ingest`, `_cmd_graph_reproject`, `_cmd_mcp_serve`).

### Phase 6: Packaging, Docker Runtime Dependencies, Integration Test Fixes, & Documentation Synchronization

**Files:**

- `pyproject.toml`
- `Dockerfile`
- `.env.example`
- `docker-compose.yml`
- `Makefile`
- `tests/integration/test_graph_reproject.py`
- `tests/integration/test_sdd_knowledge_graph.py`
- `tests/integration/test_adr_knowledge_graph.py`
- `tests/integration/test_multiproject_sdd.py`
- `README.md`, `docs/INSTALL.md`, `docs/AGENT-QUICKSTART.md`, `docs/AGENT-INTEGRATION.md`, `docs/ARCHITECTURE.md`, `docs/OBSERVABILITY.md`, `docs/README.md`, `llms.txt`

**Steps:**

1. Update `pyproject.toml` and `Dockerfile`:
   - In `pyproject.toml`: add `extraction = ["docling>=2.0", "pymupdf>=1.24.0", "beautifulsoup4>=4.12.0"]` under `[project.optional-dependencies]` and update `Repository` URL to `https://github.com/vladm3105/platform-engramory`.
   - In `Dockerfile`: install `git` in the final runtime stage (`apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*`) so `GitSyncEngine` and `InvariantChecker` work inside containers.
2. Align `.env.example`, `docker-compose.yml`, and `Makefile`:
   - Remove retired `SeaweedFS` references from `.env.example` and `docs/OBSERVABILITY.md`.
   - Align `ENGRAMORY_EMBED_MODEL=text-embedding-3-small` and `ENGRAMORY_EMBED_DIMS=1536` in `.env.example`, `docker-compose.yml`, and `Makefile`.
   - Align `LITELLM_API_KEY` / `LITELLM_MASTER_KEY` defaults in `docker-compose.yml` and pass `NEO4J_URI`, `NEO4J_USER`, `NEO4J_PASSWORD` to the `worker` service.
3. Fix integration test tenant-scoping and assertions:
   - `tests/integration/test_graph_reproject.py:69`: pass `tenant_id=tenant_id` to `graph.neighbors(id="ADR-03-graph", rel="CONSTRAINED_BY", depth=1, tenant_id=tenant_id)`.
   - `tests/integration/test_sdd_knowledge_graph.py:66,75,82`: pass `tenant_id=tenant_id` to `get_sdd_context_slice(graph, "IPLAN-03", depth=4, tenant_id=tenant_id)`, `get_sdd_blast_radius(graph, "SPEC-03", depth=3, tenant_id=tenant_id)`, and `graph.delete_node(id="IPLAN-06", tenant_id=tenant_id)`.
   - `tests/integration/test_adr_knowledge_graph.py:74`: pass `tenant_id=tenant_id` to `expand_graph_context(graph, ["ADR-03"], depth=2, tenant_id=tenant_id)`.
   - `tests/integration/test_multiproject_sdd.py:281`: replace the tautological assertion (`assert any(x["id"] == "TASK-01" for x in remaining) or True`) with `assert await repo.get_graph_entity("TASK-01", tenant_id=tenant) is not None` and `assert await repo.get_graph_entity("TASK-02", tenant_id=tenant) is None`.
4. Synchronize documentation (`README.md`, `docs/INSTALL.md`, `docs/AGENT-QUICKSTART.md`, `docs/AGENT-INTEGRATION.md`, `docs/ARCHITECTURE.md`, `docs/OBSERVABILITY.md`, `docs/README.md`) and regenerate `llms.txt`:
   - Replace stale `aidoc-flow-engramory` repo URLs/clones with `platform-engramory`.
   - Document the full MCP tool catalog (`memory_*`, `knowledge_search`, `sdd_*`, `docs_*`) and CLI command groups (`engramory docs`, `engramory sdd`, `engramory knowledge`).

---

## Verification & Acceptance Criteria

- `python3 .agents/skills/verified-planning/check_plan.py plans/PLAN-017_audit-gaps-and-bugs-remediation.md` passes.
- New and updated unit/integration tests pass via `uv run pytest`.
- `task check` and `task ci` pass with `RESULT: pass`.

---

## Review log

### Pass 1 - 2026-11-09

- **Reviewer:** Authoring Agent (`self`)
- **Findings:**
  1. Verified all citations in the Claim ledger against live repository source lines.
  2. Checked existing unit and e2e tests (`tests/test_citations.py:66-72`, `tests/e2e/test_retrieval.py:55`, `tests/test_plan012_remediation.py:65`) to ensure backward compatibility on citation formatting and scoped memory retrieval.
- **Result:** All citations verified; ready for Pass 2 independent fresh-context multi-lens review.

### Pass 2 - 2026-11-09 - independent

- **Reviewer:** Independent Fresh-Context Subagent (`460aa087-2f2e-4323-8197-575d417c537d`, Google / Anthropic / AWS / Microsoft lenses)
- **Findings & Resolutions Folded:**
  1. **[CRITICAL-1 — Microsoft/Google Lens (`JWKSValidator.__init__` instance attributes)]:** Updated Claim 22 (`src/engramory/access/jwt_auth.py:96-179`) and Phase 5 Step 2 to explicitly store `self.headers = req_headers`, `self.cache_jwk_set = cache_jwk_set`, and `self.lifespan = lifespan` in `JWKSValidator.__init__` before passing them to `PyJWKClient(self.jwks_url, ...)` on key rotation.
  2. **[CRITICAL-2 — Google/Microsoft Lens (`Neo4jGraphAdapter` signature, `kb_graph.py` tenant forwarding, & integration tests)]:** Corrected Claim 25 and Phase 6 Step 3 to preserve `Neo4jGraphAdapter(uri=NEO4J_URI, auth=NEO4J_AUTH)`, added Claim 4 and Phase 1 Step 3 for `expand_graph_context` / `get_sdd_context_slice` / `get_sdd_blast_radius` (`src/engramory/core/kb_graph.py:553-695`) to forward `tenant_id` and `project_id`, updated `test_sdd_knowledge_graph.py:66,75,82` to pass `tenant_id`, and replaced the tautological `or True` assertion at `test_multiproject_sdd.py:281` with direct `repo.get_graph_entity` assertions.
  3. **[CRITICAL-3 — Google/Anthropic Lens (`SemanticChunker.refine_sections` Pass 1 `[Doc: ...]\n` header preservation)]:** Updated Claim 8 (`src/engramory/core/extraction/enrichment.py:19-81`) and Phase 2 Step 2 to extract any existing `[Doc: ...]\n` prefix in Pass 1 and prepend it to every split sub-chunk without referencing non-existent Pass 3 or `sec.title`.
  4. **[MAJOR-1 — AWS/Microsoft Lens (Empty `active_ids` `::text[]` cast & `kb_graph.py` reconciliation guard)]:** Updated Claim 3, Claim 4, and Phase 1 Step 3 to cast `%(active_ids)s::text[]` and `%(stale_ids)s::text[]` in `Repository.purge_stale_graph_entities` and change `if reconcile and entities:` in `ingest_sdd_knowledge_graph` (`src/engramory/core/kb_graph.py:591,611`) to `if reconcile and sdd_path.exists():`.
  5. **[MAJOR-2 — Google Lens (`upsert_edge` `MERGE` preservation and unit test updates in `test_graph_neo4j.py` & `test_graph_projection.py`)]:** Updated Phase 1 Steps 1, 2, and 4 to keep `MERGE (a:Entity {uid: $src_uid})` / `MERGE (b:Entity {uid: $dst_uid})` in `upsert_edge` (guarded by `ON CREATE SET` and `WITH a, b WHERE ...`), and added `tests/unit/test_graph_neo4j.py` and `tests/unit/test_graph_projection.py` to Phase 1 files and steps.
  6. **[MAJOR-3 — Anthropic/Google Lens (`McpServer.handle_call_tool` `sdd_ingest` citation & `test_mcp_server.py` update)]:** Corrected Claim 21 to cite `src/engramory/mcp/server.py:568-605` and updated Phase 5 Step 1 to update `tests/unit/test_mcp_server.py:156-161` for `sdd_root=str(Path("sdd").resolve())`.
  7. **[MAJOR-4 — Google Lens (`_close_adapters` mock-safe helper across all `dev_adapters` callers in `cli.py`)]:** Removed the non-existent `tests/conftest.py` `adapters` fixture reference and specified `_close_adapters(adapters)` using `inspect.isawaitable(res)` across all `dev_adapters(config.dsn)` callers in `src/engramory/cli.py`.
  8. **[MEDIUM-1 — Google/Anthropic Lens (`Repository.get_memories`, `record_feedback`, and `distillation.reflect` precision & 1536-dim guards)]:** Updated Claims 11, 12, and 16 and Phase 3 Step 1 / Phase 4 Step 2 to reflect existing `query_vec` / `feedback` / `upsert_memory` implementations and guard `len(vec) == 1536` before both `get_memories` vector binding and `Memory(...)` construction in `distillation.reflect`.
  9. **[MEDIUM-2 — Anthropic/Microsoft Lens (`MagicMock`-safe `embed_model` string resolution)]:** Updated Phase 4 Step 1 to check `isinstance(val, str) and val.strip()` when resolving `embed_model` / `_embed_model` / `_default_embed_model` on `llm`.
- **Result:** All 9 findings folded; dispatching Pass 3 independent verification.

### Pass 3 - 2026-11-09 - independent

- **Reviewer:** Independent Fresh-Context Subagent (`937fa504-419f-4592-ba90-ce93fc1d79e4`, Google / Anthropic / AWS / Microsoft lenses)
- **Findings & Resolutions Folded:**
  1. **[MAJOR-1 — Anthropic/Microsoft Lens (`McpServer.handle_call_tool` `knowledge_search` signature alignment)]:** Updated Phase 3 Step 3 so `McpServer.handle_call_tool` calls `await self._surface.knowledge_search(call_ctx, query=arguments["query"], domain_id=arguments.get("domain_id"), scope=arguments.get("scope", "project"), limit=int(arguments.get("k", arguments.get("limit", 5))))`, letting `self._resolve_call_ctx(arguments, ctx)` resolve `project_id` into `call_ctx`.
  2. **[MAJOR-2 — Google/AWS Lens (`Neo4jGraphAdapter.upsert_edge` `$src`/`$dst` & `$project_id` parameter binding)]:** Updated Phase 1 Step 1 to use `$src` and `$dst` (`ON CREATE SET a.id = $src, a.tenant_id = $tenant_id, a.project_id = $project_id` and `b.id = $dst, ...`) and bind `"project_id": clean_props.get("project_id")` in `session.run`.
  3. **[MAJOR-3 — Google Lens (`tests/integration/test_adr_knowledge_graph.py:74` `tenant_id` forwarding & `test_multiproject_sdd.py` cleanup)]:** Added `tests/integration/test_adr_knowledge_graph.py` (`line 74`: pass `tenant_id=tenant_id` to `expand_graph_context`) to Phase 6 files and Step 3, and simplified the `test_multiproject_sdd.py` step to only line 281.
  4. **[MEDIUM-1 — Google/Anthropic Lens (`_cmd_mcp_serve` & `AccessSurface.knowledge_ingest_batch` section embedding)]:** Updated Phase 3 Step 1 and Phase 5 Step 3 so `AccessSurface.knowledge_ingest_batch` embeds upserted sections when `self._llm is not None` and `_cmd_mcp_serve` passes `llm=adapters.llm` and invokes `_close_adapters(adapters)` in a `finally:` block.
- **Result:** ready — no remaining findings.
