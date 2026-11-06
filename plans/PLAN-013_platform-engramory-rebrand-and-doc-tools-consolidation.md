# PLAN-013: Rebrand `aidoc-flow-engramory` → `platform-engramory` & Consolidate AI-First Documentation Toolchain (`engramory docs`)

> **Status:** Ready  
> **Author:** Antigravity  
> **Date:** 2026-11-06  
> **Tracking Issue:** Ecosystem Documentation Governance & Platform Engramory Consolidation  

---

## 1. Executive Summary & Goals

Engramory serves as the shared **L0 Knowledge Base + L1–L3 Agent Memory Spine** across every repository in the ecosystem (`platform-*`, `aidoc-*`, `aiops-*`, `b-local-*`), deployed on stage in the shared `platform` rootless Docker namespace (`UID 1004` at `/home/platform/platform-engramory` per `scripts/deploy-stage.sh:24`).

In accordance with **ADR-08 (*Two Bounded Cores on One Spine*)** in `docs/CORES.md` and the platform's Multi-Tenant Consolidation Pillar, maintaining a separate `platform-docs` service would split L0 documentation away from `kb_sections`, duplicate PostgreSQL/graph ingestion pipelines, and sever zero-hop L0 Runbook + L2 Episodic Memory fusion.

This plan executes two tightly bound objectives:

1. **Zero-Breakage Rebrand (`aidoc-flow-engramory` $\rightarrow$ `platform-engramory`):**
   - Rename `../aidoc-flow-engramory` to `../platform-engramory` inside `$WORKSPACE_ROOT` and create a backward-compatible symlink `../aidoc-flow-engramory -> ../platform-engramory` (matching the existing `aidoc-observability-monitoring -> ../platform-observability-monitoring` pattern) so all relative paths (`../aidoc-flow-engramory`) across `aiops-harness` and sibling spokes continue resolving without interruption.
   - Preserve the internal Python package name `engramory` (`src/engramory`) and CLI entrypoint `engramory = "engramory.cli:main"` in `pyproject.toml:23` so zero Python imports or CLI invocations break, and run `uv sync` after moving the directory so `.venv` editable paths resolve cleanly to `../platform-engramory`.
   - Consolidate all 10 authored `doc-tools` specifications (`00_INDEX.md` through `09_ENGRAMORY_CONSOLIDATION_AND_EXTENSION_BLUEPRINT.md`) from `../platform-docs/docs/` into `docs/doc-tools/` inside `platform-engramory`, scrub any banned workspace-local paths per `PATHS_DENY` in `.ci-config:72`, align stage port references (`127.0.0.1:${MCP_PORT:-8005}:8000`) with `compose.platform.yaml:21`, and remove the temporary `../platform-docs` directory (whose root `README.md` and `AGENTS.md` are superseded by `platform-engramory/README.md`, `platform-engramory/AGENTS.md`, and `docs/doc-tools/00_INDEX.md`).
2. **Direct Implementation of the AI-First Documentation & Executable Runbook Toolchain in `engramory`:**
   - **Schema Migration (`db/migrations/0008_runbooks_and_doc_governance.sql`):** Add idempotent `kb_documents` (storing deterministic frontmatter, `autonomy_tier`, `watched_paths`, `trigger_alerts`, `last_verified`, `review_ttl_days`) and `kb_runbook_steps` (storing annotated `{id, tier, mode, expect_exit, timeout_sec}` executable code blocks) on the canonical PostgreSQL spine (`tenant_id, doc_id` composite keys), updating `tests/test_migrations.py:11` to include `0008_runbooks_and_doc_governance.sql`.
   - **Core Documentation & Runbook Engine (`src/engramory/core/docs_tools.py`, `.vale.ini`, & `src/engramory/core/kb_graph.py`):** Implement stdlib/PyYAML-backed `DocumentFrontmatter` validation, CommonMark fenced code-block attribute parsing (`{id, tier, mode, expect_exit, timeout_sec}`), `INV-STEP-01`/`02` safety checks (`green` requires `read-only` and bans mutating shell verbs), `smoke_run_green_steps` (`tier="green"` probe verifier), `AIOpsAgent` chunk-independence prose rules + `.vale.ini` / `.vale/styles/AIOpsAgent/*.yml` rule files, git-diff `watched_paths` drift & `last_verified` TTL staleness detection, Rule D14-scrubbed `/llms.txt` + `/llms-full.txt` export, and `extract_docs_graph()` in `src/engramory/core/kb_graph.py` for `RUNBOOK`, `RUNBOOK_STEP`, and `ALERT` entities.
   - **KnowledgeCore, GitSyncEngine, Repository, AuthZ, Surface, CLI & MCP Gateway Integration:** Add `"docs_ingest_runbook"` and `"docs_slice_runbook"` to `KNOWN_ACTIONS` in `src/engramory/access/authz.py:15`, persistence/deletion & query methods in `Repository`, governed methods in `AccessSurface`, automatic `kb_documents`/`kb_runbook_steps` population & deletion in `KnowledgeCore.ingest_documents_batch` (`src/engramory/core/knowledge.py:73`) and `GitSyncEngine.sync` (`src/engramory/core/git_sync.py:269`), the `engramory docs {lint, check-drift, export-llms, slice-runbook, smoke-run, ingest}` CLI subcommand group in `src/engramory/cli.py`, and MCP tools (`docs_slice_runbook`, `docs_validate`, `docs_check_drift`, `docs_export_llms`, `docs_smoke_run`) in `src/engramory/mcp/server.py`.

---

## 2. Implementation Phases

### Phase 1: Specification Consolidation & Repository Rebranding (`platform-engramory`)

1. **Move & Sanitize `doc-tools` Specifications into `docs/doc-tools/`:**
   - Copy all 10 files (`../platform-docs/docs/00_INDEX.md` through `09_ENGRAMORY_CONSOLIDATION_AND_EXTENSION_BLUEPRINT.md`) into `docs/doc-tools/` inside the repository and update headers/links to reference `platform-engramory` (`compose.platform.yaml:10`) and `engramory docs`.
   - Scrub all workspace-local path literals matching `PATHS_DENY` in `.ci-config:72` (replacing with `$WORKSPACE_ROOT`, `$PLATFORM_ROOT`, or repo-relative `..`) so `task check` (`hygiene`) and `task ci` (`paths`) pass cleanly.
   - Align stage port references in `docs/doc-tools/*.md` (`04_GRAPH_INDEXING_MCP_AND_ENGRAMORY_RAG.md`, `07_PORTAL_API_CONTRACTS_AND_DEPLOYMENT_RUNBOOK.md`, `09_ENGRAMORY_CONSOLIDATION_AND_EXTENSION_BLUEPRINT.md`) to `127.0.0.1:${MCP_PORT:-8005}:8000` matching `compose.platform.yaml:21`.
   - Copy `../platform-docs/llms.txt` and merge with Engramory's root manifest as `/llms.txt`.
   - Remove the temporary `../platform-docs` directory (its root `README.md` and `AGENTS.md` are superseded by `platform-engramory`'s root files and `docs/doc-tools/00_INDEX.md`).
2. **Update Repository Identity Metadata:**
   - Update `README.md:1` title and overview to **`platform-engramory` (`aidoc-flow-engramory`) — Shared Platform Memory, Knowledge & Documentation Operating System**, adding `docs/doc-tools/00_INDEX.md` to the `## Documentation` table (`README.md:74`).
   - Keep `name = "engramory"` and `engramory = "engramory.cli:main"` in `pyproject.toml:2` and `pyproject.toml:23` for 100% backward compatibility.
3. **Directory Move, Compatibility Symlink & `.venv` Refresh:**
   - Move `../aidoc-flow-engramory` $\rightarrow$ `../platform-engramory` and create symlink `../aidoc-flow-engramory -> ../platform-engramory`.
   - Run `uv sync` (or `uv pip install -e .`) inside `../platform-engramory` so `.venv` console-script shebangs and editable `.pth` files point cleanly to `../platform-engramory`.

### Phase 2: Canonical PostgreSQL Schema Migration (`0008_runbooks_and_doc_governance.sql`)

1. **Author Idempotent `db/migrations/0008_runbooks_and_doc_governance.sql`:**
   - Use `CREATE TABLE IF NOT EXISTS`, `DROP TRIGGER IF EXISTS`, and `CREATE INDEX IF NOT EXISTS` throughout so the migration is 100% idempotent under `scripts/deploy-stage.sh:65` and test re-runs.
   - Create `kb_documents` keyed by `PRIMARY KEY (tenant_id, doc_id)` (matching `graph_entities` in `db/migrations/0007_graph_entities_canonical.sql:14` and `uq_kb_sections_version` in `db/migrations/0003_reconcile_contracts.sql:110`):
     - Columns: `tenant_id TEXT NOT NULL DEFAULT 'default'`, `doc_id TEXT NOT NULL`, `project_id TEXT`, `domain_id TEXT`, `scope TEXT NOT NULL DEFAULT 'project'`, `repo_path TEXT NOT NULL`, `title TEXT NOT NULL`, `kind TEXT NOT NULL`, `namespace TEXT NOT NULL DEFAULT 'ns:platform'`, `status TEXT NOT NULL DEFAULT 'active'`, `autonomy_tier TEXT NOT NULL DEFAULT 'green' CHECK (autonomy_tier IN ('green', 'yellow', 'red'))`, `frontmatter JSONB NOT NULL DEFAULT '{}'::jsonb`, `watched_paths TEXT[] NOT NULL DEFAULT '{}'`, `trigger_alerts TEXT[] NOT NULL DEFAULT '{}'`, `last_verified DATE NOT NULL DEFAULT CURRENT_DATE`, `review_ttl_days INTEGER NOT NULL DEFAULT 90`, `git_commit_sha TEXT NOT NULL DEFAULT 'local-head'`, `created_at TIMESTAMPTZ NOT NULL DEFAULT now()`, `updated_at TIMESTAMPTZ NOT NULL DEFAULT now()`.
     - Execute `DROP TRIGGER IF EXISTS trg_kb_documents_updated_at ON kb_documents;` followed by `CREATE TRIGGER trg_kb_documents_updated_at BEFORE UPDATE ON kb_documents FOR EACH ROW EXECUTE FUNCTION set_updated_at();` (matching `trg_kb_sections_updated_at` in `db/migrations/0003_reconcile_contracts.sql:107`) and also update `updated_at = NOW()` on `ON CONFLICT DO UPDATE` in `Repository.upsert_kb_document` (matching `upsert_graph_entity` in `src/engramory/core/repository.py:745`).
     - Indexes: `CREATE INDEX IF NOT EXISTS` GIN on `trigger_alerts`, GIN on `watched_paths`, and B-tree on `(tenant_id, project_id, status)`.
   - Create `kb_runbook_steps` keyed by `PRIMARY KEY (tenant_id, step_uid)` with `FOREIGN KEY (tenant_id, doc_id) REFERENCES kb_documents (tenant_id, doc_id) ON DELETE CASCADE`:
     - Columns: `tenant_id TEXT NOT NULL DEFAULT 'default'`, `step_uid TEXT NOT NULL`, `doc_id TEXT NOT NULL`, `step_order INTEGER NOT NULL`, `step_id TEXT NOT NULL`, `heading_path TEXT NOT NULL`, `tier TEXT NOT NULL CHECK (tier IN ('green', 'yellow', 'red'))`, `mode TEXT NOT NULL CHECK (mode IN ('read-only', 'mutating'))`, `expect_exit INTEGER NOT NULL DEFAULT 0`, `timeout_sec INTEGER NOT NULL DEFAULT 30`, `command_body TEXT NOT NULL`, `prose_context TEXT NOT NULL`, `rollback_step TEXT`, `created_at TIMESTAMPTZ NOT NULL DEFAULT now()`, `CONSTRAINT chk_green_readonly CHECK (tier != 'green' OR mode = 'read-only')`.
2. **Update `src/engramory/core/models.py` & `tests/test_migrations.py`:**
   - Add frozen stdlib dataclasses `KBDocument` and `KBRunbookStep` in `src/engramory/core/models.py` enforcing `AUTONOMY_TIERS = frozenset({"green", "yellow", "red"})`, `RUNBOOK_MODES = frozenset({"read-only", "mutating"})`, and `INV-STEP-01` (`tier == "green"` requires `mode == "read-only"`).
   - Update `test_migrations_exist_and_ordered` in `tests/test_migrations.py:11` to include `"0008_runbooks_and_doc_governance.sql"` and add `test_migration_0008_runbooks_and_doc_governance()` verifying `kb_documents`, `kb_runbook_steps`, `PRIMARY KEY (tenant_id, doc_id)`, and `chk_green_readonly`.

### Phase 3: Documentation Linter, Fence-Aware Contextual Chunking, Runbook AST Parser, Smoke-Run Engine, Vale Rules, Drift Engine, `llms.txt` Exporter & Graph Extractor (`src/engramory/core/docs_tools.py`, `src/engramory/core/chunking.py` & `src/engramory/core/kb_graph.py`)

Create `src/engramory/core/docs_tools.py` using stdlib + `yaml` (`pyproject.toml:16`) and `inspect_memory_write` / `detect_hard_secrets` (`src/engramory/access/poisoning_guard.py:78`), upgrade `src/engramory/core/chunking.py`, scaffold `.vale.ini` + `.vale/styles/AIOpsAgent/*.yml`, and extend `src/engramory/core/kb_graph.py`:

1. **Path Confinement Helper (`resolve_confined_repo_path`):**
   - Validates that any caller-supplied `repo_path` or `file_path` resolves (`Path.resolve()`) within the allowed workspace root (`ENGRAMORY_ALLOWED_DOCS_ROOT` or `Path.cwd().resolve()`), rejecting directory traversal (`..`) outside the boundary with `ValidationError`.
2. **Fence-Aware Markdown Chunking & Anthropic *Contextual Retrieval* Prefixing (`src/engramory/core/chunking.py:44`):**
   - Fix `chunk_markdown` (`src/engramory/core/chunking.py:44`) so heading detection (`src/engramory/core/chunking.py:52`) tracks fenced code blocks (` ``` ` and `~~~`) and never splits on `# comments` inside shell/Python code blocks.
   - Replace the newline-destroying `" ".join(words[...])` sub-chunker (`src/engramory/core/chunking.py:77`) with line-preserving, fence-safe sub-chunking.
   - Prepend an **Anthropic Contextual Retrieval** structural header (`[Doc: {doc_id} | Title: {title} | Kind: {kind} | Domain: {domain} | Tier: {autonomy_tier} | Section: {heading_breadcrumb}]`) to each chunk before dense embedding and PostgreSQL FTS indexing so retrieved chunks retain full semantic provenance.
3. **Frontmatter & Executable Code-Block AST Parser (`parse_markdown_document`):**
   - Parses YAML frontmatter (`id`, `title`, `kind`, `domain`, `namespace`, `status`, `autonomy_tier`, `watched_paths`, `last_verified`, `triggers`, `endpoints`).
   - Extracts fenced code blocks carrying `{id="..." tier="green|yellow|red" mode="read-only|mutating" expect_exit=0 timeout_sec=30 rollback_step="..."}` attributes along with their enclosing heading breadcrumb (`# Title > ## Section`).
   - Enforces `INV-STEP-01` (`tier="green"` must be `mode="read-only"`) and Google SRE defense-in-depth `INV-STEP-02` (in `tier="green"` blocks, bans destructive/mutating shell verbs (`docker restart|rm|stop|kill`, `tofu apply|destroy`, `rm -rf`, `bao operator generate-root`, SQL `DROP|DELETE|UPDATE|ALTER|TRUNCATE`), subshell/command substitution (`$(...)`, backticks `` `...` ``, `eval`, `exec`), file output redirection (`>`, `>>`, `| tee`), and mutating HTTP flags (`curl -X POST|PUT|DELETE|PATCH`, `curl -d|--data`, `wget --post-`)).
4. **AI-First Chunk-Independence, Referential Integrity Linter (`lint_markdown_file` / `lint_repo`) & `.vale.ini` Scaffolding:**
   - Validates frontmatter schema, code-block attributes, heading word ceiling (`MAX_SECTION_WORDS = 450`), banned cross-chunk deictics (`"the command above"`, `"as shown below"`, `"in the previous section"`), banned retired tools (`seaweedfs`, `bytebase`, `pre-commit`), and hard secret patterns (`detect_hard_secrets`).
   - Enforces **Google Docs-as-Code Referential Integrity** (`BROKEN_RELATIVE_LINK` for broken relative `[text](./path.md#anchor)` links, `DEAD_WATCHED_PATH_GLOB` when a `watched_paths` glob matches zero files in the repository, and `MISSING_ROLLBACK_STEP` when a step's `rollback_step` ID does not exist in the same runbook).
   - Per **Anthropic Agent-Computer Interface (ACI)** best practices, every diagnostic includes `{rule_id, severity, file, line, message, remediation_hint}` so autonomous coding agents can self-repair violations in a single turn.
   - Scaffold `.vale.ini` and `.vale/styles/AIOpsAgent/{NoCrossChunkDeictics,NoAmbiguousSubjectStart,BannedRetiredTools,DeterministicPortFormat}.yml` in the repository root so external `vale --output=JSON` works out of the box when `vale` is installed on `$PATH`, merging diagnostics into a deterministic JSON report (`status`, `errors[]`, `warnings[]`).
5. **Hardened Executable `tier="green"` Smoke-Run Verifier (`smoke_run_green_steps`):**
   - Extracts only `tier == "green"` and `mode == "read-only"` steps from a runbook (or all runbooks in `repo_path`), verifies `INV-STEP-01` and hardened `INV-STEP-02` AST safety before execution, and supports `dry_run: bool = False` (which runs `bash -n` syntax validation without executing commands).
   - When `dry_run=False`, scrubs sensitive environment variables (`ENGRAMORY_DATABASE_URL`, `DATABASE_URL`, `OPENBAO_TOKEN`, `VAULT_TOKEN`, `GITHUB_TOKEN`, `AWS_SECRET_ACCESS_KEY`) from the child subprocess environment, executes with `timeout=step.timeout_sec`, caps captured `stdout`/`stderr` at `MAX_SMOKE_OUTPUT_BYTES = 8192`, and asserts `returncode == step.expect_exit`.
6. **Code-to-Doc Drift & TTL Staleness Engine (`check_repo_drift`):**
   - Computes `git diff --name-only <base_ref>...<head_ref>` (or working-tree changed files) and matches modified paths against each document's `watched_paths` globs (`fnmatch`).
   - Checks `date.today() - last_verified > review_ttl_days` (default 90 days).
   - Returns structured drift findings (`WATCHED_PATH_MODIFIED`, `TTL_STALE`, `DEAD_WATCHED_PATH_GLOB`) ready for CI gating or `platform-paperclip` `DOC2C` task creation.
7. **Deterministic, Prompt-Cache Friendly `/llms.txt` & `/llms-full.txt` Exporter (`export_repo_llms`):**
   - Scans `README.md`, `AGENTS.md`, `docs/**/*.md`, and `sdd/**/*.yaml` in a target repository.
   - Filters to `status: active` documents, sorts deterministically by `(kind, doc_id)` to preserve LLM prefix KV-cache stability, scrubs secrets/control characters via `detect_hard_secrets` and `has_dangerous_unicode` (`src/engramory/access/poisoning_guard.py:64`), and generates deterministic `/llms.txt` and `/llms-full.txt` manifests.
8. **Runbook & Alert Knowledge Graph Extractor (`extract_docs_graph` in `src/engramory/core/kb_graph.py`):**
   - Add `extract_docs_graph(repo_path: Path, *, tenant_id: str = "default") -> tuple[list[GraphEntity], list[GraphRelation]]` in `src/engramory/core/kb_graph.py` and invoke it inside `ingest_sdd_knowledge_graph` (`src/engramory/core/kb_graph.py:521`) so `docs/**/*.md` files with valid frontmatter emit `RUNBOOK` / `ADR` / `SPEC` entities, `RUNBOOK_STEP` entities, and `ALERT` entities connected by `MITIGATES_ALERT`, `CONTAINS_STEP`, and `DEPENDS_ON` relations in `graph_entities` and `graph_relations`.

### Phase 4: AuthZ, Repository, KnowledgeCore, GitSyncEngine, AccessSurface, CLI & MCP Server Integration

1. **Extend `src/engramory/access/authz.py` & `src/engramory/core/repository.py`:**
   - Add `"docs_ingest_runbook"` and `"docs_slice_runbook"` to `KNOWN_ACTIONS` in `src/engramory/access/authz.py:15` so `AccessSurface._decide` (`src/engramory/access/surface.py:119`) authorizes and audits both actions without raising `AuthzError("...: unknown_action")`.
   - Add `upsert_kb_document(self, doc: KBDocument) -> str`, `upsert_kb_runbook_steps(self, tenant_id: str, doc_id: str, steps: Sequence[KBRunbookStep]) -> int` (executing atomically inside a transaction that first deletes stale `kb_runbook_steps` for `(tenant_id, doc_id)` not present in `steps` so renamed/removed steps are never orphaned), and `delete_kb_document(self, tenant_id: str, doc_id: str, *, repo_path: str | None = None) -> int` in `src/engramory/core/repository.py` (alongside `delete_kb_sections_for_doc` at `src/engramory/core/repository.py:523`).
   - Add `slice_runbook(self, *, tenant_id: str, project_id: str | None = None, doc_id: str | None = None, alert_id: str | None = None, max_tier: str | None = None) -> list[dict[str, Any]]` joining `kb_documents` and `kb_runbook_steps` ordered deterministically by `(doc_id ASC, step_order ASC)`.
2. **Wire `KnowledgeCore` (`src/engramory/core/knowledge.py`) & `GitSyncEngine` (`src/engramory/core/git_sync.py`):**
   - Extend `KnowledgeCore.ingest_documents_batch` (`src/engramory/core/knowledge.py:73`) and `GitSyncEngine.sync` (`src/engramory/core/git_sync.py:269`) so that when a `.md` file with valid `doc-tools` frontmatter is ingested, `parse_markdown_document` also extracts `KBDocument` and `KBRunbookStep` records and persists them via `repo.upsert_kb_document` and `repo.upsert_kb_runbook_steps`.
   - In `GitSyncEngine.sync` (`src/engramory/core/git_sync.py:322`), call `await repo.delete_kb_document(tenant_id, doc_id, repo_path=...)` alongside `await repo.delete_kb_sections_for_doc(tenant_id, doc_id)` when files are `DELETED` or `RENAMED` (`old_path`), matching by `doc_id` or `repo_path` and cascading deletion to `kb_runbook_steps`.
3. **Extend `src/engramory/access/surface.py`:**
   - Add `docs_ingest_runbook(self, ctx: ActorContext, *, doc: KBDocument, steps: Sequence[KBRunbookStep], evidence_ref: str | None, scope: str = "project") -> dict[str, Any]` enforcing `self._decide(ctx, "docs_ingest_runbook", ...)`, ADR-06 `evidence_ref`, `inspect_memory_write` poisoning screening with `record_audit` on rejection, and locking `tenant_id = ctx.tenant_id` and `project_id = ctx.project_id`.
   - Add `docs_slice_runbook(self, ctx: ActorContext, *, doc_id: str | None = None, alert_id: str | None = None, max_tier: str | None = None, scope: str = "project", include_memories: bool = True, token_budget: int = 2000, format: str = "json") -> dict[str, Any]` enforcing `self._decide(ctx, "docs_slice_runbook", ...)`, placing **static L0 `kb_documents` + `kb_runbook_steps` first** and **dynamic L2 distilled memories (`memory_search`) second** (for Anthropic prefix KV-cache hit stability), enforcing `token_budget` (matching `memory_search` at `src/engramory/mcp/server.py:57` and `sdd_context_slice` at `src/engramory/mcp/server.py:170`), and supporting `"markdown"` or `"json"` output.
4. **Extend `src/engramory/cli.py` (`engramory docs ...`):**
   - Add `p_docs = sub.add_parser("docs", help="AI-first documentation, runbook & drift tools")` in `_build_parser()` (`src/engramory/cli.py:686`) with six subcommands:
     - `engramory docs lint [path] [--strict]`
     - `engramory docs check-drift [--repo-path .] [--base-ref HEAD~1] [--head-ref HEAD]`
     - `engramory docs export-llms [--repo-path .] [--write] [--full]`
     - `engramory docs slice-runbook [--repo-path .] [--doc-id ID] [--alert-id ALT-ID] [--max-tier green|yellow|red] [--token-budget 2000] [--format json|markdown]`
     - `engramory docs smoke-run [--repo-path .] [--doc-id ID] [--dry-run]`
     - `engramory docs ingest [--repo-path .] [--project-id ID]`
   - Because `_load_config` (`src/engramory/cli.py:97`) raises `ConfigError` when neither `.engramory/config.toml` nor `ENGRAMORY_DATABASE_URL` is configured, `docs lint`, `docs check-drift`, `docs export-llms`, and `docs smoke-run` invoke `docs_tools` directly on the local repository without calling `_load_config`. For `docs slice-runbook`: if `--repo-path` is passed (or if `_load_config` / `AccessSurface.docs_slice_runbook` raises `ConfigError` / `StoreUnavailable`), the CLI falls back in `cli.py` to slicing `docs/**/*.md` directly from `--repo-path` via `docs_tools.slice_local_runbooks()` so Tier-1 offline usage never requires a running database. `docs ingest` parses local runbooks and ingests them into PostgreSQL via `AccessSurface.docs_ingest_runbook`.
5. **Extend `src/engramory/mcp/server.py`:**
   - Register `docs_slice_runbook` (with `token_budget` and `format`), `docs_validate`, `docs_check_drift`, `docs_export_llms`, and `docs_smoke_run` (defaulting `dry_run: true` over MCP) in `TOOLS_SCHEMA` (`src/engramory/mcp/server.py:43`) with MCP `annotations` (`readOnlyHint`, `destructiveHint`, `idempotentHint`), and dispatch them in `McpServer.handle_call_tool` (`src/engramory/mcp/server.py:317`), locking `tenant_id` and `project_id` to `call_ctx` when `self._auth_enabled` is True.
   - In `handle_call_tool`: `docs_slice_runbook` dispatches through `self._surface.docs_slice_runbook(call_ctx, ...)` (falling back to `docs_tools.slice_local_runbooks` confined via `resolve_confined_repo_path` if `repo_path` is provided and no DB rows match); `docs_validate`, `docs_check_drift`, `docs_export_llms`, and `docs_smoke_run` validate their path arguments via `resolve_confined_repo_path` before calling `docs_tools`.

### Phase 5: Unit Tests, Golden Retrieval Evals & CI Gate Verification

1. **Update `tests/test_migrations.py` & Create `tests/test_docs_tools.py`:**
   - Update `tests/test_migrations.py:11` for `0008_runbooks_and_doc_governance.sql`.
   - Test frontmatter parsing and validation errors on missing required keys or invalid `autonomy_tier`.
   - Test executable code-block extraction (`{id, tier, mode, expect_exit, timeout_sec, rollback_step}`) and verify `INV-STEP-01` (`green` + `mutating` rejected) and hardened `INV-STEP-02` (`green` + `docker restart`, `rm -rf`, `$(...)` subshells, `>` redirection, and `curl -X POST` rejected).
   - Test `smoke_run_green_steps` executing safe `tier="green"` read-only probes (`dry_run=True` syntax check and live probes with scrubbed secret env vars and output cap) and blocking mutating steps.
   - Test `AIOpsAgent` chunk-independence prose rules + referential integrity rules (`BROKEN_RELATIVE_LINK`, `DEAD_WATCHED_PATH_GLOB`, `MISSING_ROLLBACK_STEP`, `"the command above"`, retired `seaweedfs`/`bytebase` mentions, hard secrets, and `remediation_hint` presence) and verify `.vale.ini` + `.vale/styles/AIOpsAgent/*.yml` exist.
   - Test `check_repo_drift` matching modified files against `watched_paths` and expired `last_verified` dates.
   - Test `export_repo_llms` generating `/llms.txt` and `/llms-full.txt` and excluding `deprecated` docs.
   - Test `extract_docs_graph` extracting `RUNBOOK`, `RUNBOOK_STEP`, and `ALERT` entities and relations.
   - Test `resolve_confined_repo_path` rejecting `..` path traversal outside workspace root.
   - **Golden Evaluation Test (`test_golden_runbook_retrieval_and_chunk_integrity`):** Verify that fenced ` ```bash ` blocks with `# comments` are never split by `chunk_markdown`, Contextual Retrieval headers (`[Doc: ... | Title: ... | Section: ...]`) are prepended cleanly, and `slice_runbook(alert_id=..., max_tier="green", token_budget=...)` achieves 100% precision/recall on `green` steps in exact `step_order` with zero `yellow`/`red` leakage and L0-before-L2 ordering.
   - Test CLI `engramory --json docs {lint, check-drift, export-llms, slice-runbook, smoke-run}` (without `.engramory/config.toml`) and MCP `handle_call_tool` for `docs_slice_runbook`, `docs_validate`, `docs_check_drift`, `docs_export_llms`, and `docs_smoke_run`.
2. **Run Full Quality Gates:**
   - Execute `uv run pytest`, `uv run ruff check .`, `uv run mypy src`, and `task check` / `task ci`.

---

## Claim ledger

> Every load-bearing claim (file path, signature, field/key, event/enum value,
> behavioral assertion) cites the `file:line` actually read. `UNVERIFIED`
> rows must be resolved before the plan is ready.
> `.agents/skills/verified-planning/check_plan.py` checks each citation resolves.

| #   | Claim | Symbol | Citation |
| --- | ----- | ------ | -------- |
| 1   | `pyproject.toml` already names the package `engramory` decoupled from the repo prefix | `name = "engramory"` | pyproject.toml:2 |
| 2   | `pyproject.toml` declares the CLI script entrypoint `engramory.cli:main` | `engramory = "engramory.cli:main"` | pyproject.toml:23 |
| 3   | `pyproject.toml` includes `pyyaml>=6.0.3` as a core runtime dependency | `"pyyaml>=6.0.3",` | pyproject.toml:16 |
| 4   | `deploy-stage.sh` already deploys to `/home/platform/platform-engramory` on the stage host | `STAGE_DIR="${STAGE_DIR:-/home/${PLATFORM_USER}/platform-engramory}"` | scripts/deploy-stage.sh:24 |
| 5   | `deploy-stage.sh` tracks applied SQL migrations in `schema_migrations` | `CREATE TABLE IF NOT EXISTS schema_migrations` | scripts/deploy-stage.sh:65 |
| 6   | `CORES.md` documents ADR-08 keeping Memory and Knowledge cores on one shared PostgreSQL spine | `Engramory is **one platform with two bounded cores**, not two projects.` | docs/CORES.md:3 |
| 7   | Migration 0003 defines `kb_sections` with composite uniqueness on `(tenant_id, doc_id, citation, version)` | `CREATE UNIQUE INDEX IF NOT EXISTS uq_kb_sections_version` | db/migrations/0003_reconcile_contracts.sql:110 |
| 8   | Migration 0007 keys `graph_entities` by composite primary key `PRIMARY KEY (tenant_id, id)` | `PRIMARY KEY (tenant_id, id)` | db/migrations/0007_graph_entities_canonical.sql:14 |
| 9   | `models.py` defines `KBSection` as a frozen slots dataclass with `_require` validation | `class KBSection:` | src/engramory/core/models.py:140 |
| 10  | `models.py` defines `SCOPES` ladder `agent`, `project`, `domain`, `space` per ADR-07 | `SCOPES = frozenset({"agent", "project", "domain", "space"})` | src/engramory/core/models.py:30 |
| 11  | `poisoning_guard.py` provides `has_dangerous_unicode` screening for control and bidi characters | `def has_dangerous_unicode(text: str) -> bool:` | src/engramory/access/poisoning_guard.py:64 |
| 12  | `poisoning_guard.py` provides `detect_hard_secrets` for PATs, private keys, and OpenBao tokens | `def detect_hard_secrets(text: str) -> list[str]:` | src/engramory/access/poisoning_guard.py:78 |
| 13  | `poisoning_guard.py` provides `inspect_memory_write` raising `MemoryPoisoningError` on violations | `def inspect_memory_write(` | src/engramory/access/poisoning_guard.py:87 |
| 14  | `citations.py` provides `build_doc_id` formatting `{project_id}:{rel_path}` | `def build_doc_id(project_id:` | src/engramory/core/citations.py:28 |
| 15  | `citations.py` provides `generate_evidence_ref` returning git SHA or content sha256 | `def generate_evidence_ref(file_path: Path,` | src/engramory/core/citations.py:90 |
| 16  | `chunking.py` splits markdown headings with `MAX_CHUNK_WORDS = 300` | `MAX_CHUNK_WORDS = 300` | src/engramory/core/chunking.py:18 |
| 17  | `git_sync.py` implements `GitSyncEngine` tracking added, modified, deleted, and renamed files | `class GitSyncEngine:` | src/engramory/core/git_sync.py:79 |
| 18  | `surface.py` enforces `knowledge_ingest` with `_decide`, `evidence_ref`, and `inspect_memory_write` | `async def knowledge_ingest(` | src/engramory/access/surface.py:410 |
| 19  | `repository.py` implements `upsert_kb_sections_batch` returning composite key mapping | `async def upsert_kb_sections_batch(` | src/engramory/core/repository.py:477 |
| 20  | `cli.py` builds the `engramory` argument parser in `_build_parser` | `def _build_parser() -> argparse.ArgumentParser:` | src/engramory/cli.py:686 |
| 21  | `cli.py` enforces `require_dev_profile("cli")` before command dispatch in `main` | `require_dev_profile("cli")` | src/engramory/cli.py:878 |
| 22  | `server.py` defines `TOOLS_SCHEMA` for the MCP gateway | `TOOLS_SCHEMA: list[dict[str, Any]] = [` | src/engramory/mcp/server.py:43 |
| 23  | `server.py` locks `tenant_id` and `project_id` to `base` when `self._auth_enabled` is True | `if self._auth_enabled:` | src/engramory/mcp/server.py:302 |
| 24  | `server.py` dispatches MCP tool invocations in `handle_call_tool` | `async def handle_call_tool(` | src/engramory/mcp/server.py:317 |
| 25  | NEW@pass2 `authz.py` enforces a closed `KNOWN_ACTIONS` allowlist rejecting unknown actions | `KNOWN_ACTIONS = frozenset(` | src/engramory/access/authz.py:15 |
| 26  | NEW@pass2 `surface.py` `_decide` calls `authorize` and `record_audit` before executing surface methods | `async def _decide(` | src/engramory/access/surface.py:126 |
| 27  | NEW@pass2 `test_migrations.py` asserts the exact ordered list of SQL migration filenames | `def test_migrations_exist_and_ordered() -> None:` | tests/test_migrations.py:11 |
| 28  | NEW@pass2 `cli.py` `_load_config` raises `ConfigError` when neither `.engramory/config.toml` nor `DATABASE_URL` exists | `def _load_config(explicit:` | src/engramory/cli.py:97 |
| 29  | NEW@pass2 `README.md` contains the root title and `## Documentation` table | `## Documentation` | README.md:74 |
| 30  | NEW@pass2 Migration 0003 attaches `trg_kb_sections_updated_at` calling `set_updated_at()` | `CREATE TRIGGER trg_kb_sections_updated_at` | db/migrations/0003_reconcile_contracts.sql:107 |
| 31  | NEW@pass2 `repository.py` `upsert_graph_entity` updates `updated_at = NOW()` on conflict | `async def upsert_graph_entity(self, entity: GraphEntity) -> None:` | src/engramory/core/repository.py:787 |
| 32  | NEW@pass4 `.ci-config` denies hardcoded developer workspace paths in tracked files | `PATHS_DENY=(` | .ci-config:72 |
| 33  | NEW@pass4 `compose.platform.yaml` already names the stack `platform-engramory` | `name: platform-engramory` | compose.platform.yaml:10 |
| 34  | NEW@pass4 `compose.platform.yaml` binds MCP on `127.0.0.1:${MCP_PORT:-8005}:8000` | `- "127.0.0.1:${MCP_PORT:-8005}:8000"` | compose.platform.yaml:21 |
| 35  | NEW@pass4 `knowledge.py` batch-ingests markdown documents in `ingest_documents_batch` | `async def ingest_documents_batch(` | src/engramory/core/knowledge.py:73 |
| 36  | NEW@pass4 `git_sync.py` synchronizes repository files in `GitSyncEngine.sync` | `async def sync(` | src/engramory/core/git_sync.py:269 |
| 37  | NEW@pass4 `git_sync.py` deletes `kb_sections` when a file is deleted or renamed | `await repo.delete_kb_sections_for_doc(tenant_id, doc_id)` | src/engramory/core/git_sync.py:322 |
| 38  | NEW@pass4 `repository.py` defines `delete_kb_sections_for_doc` for removing stale document chunks | `async def delete_kb_sections_for_doc(self, tenant_id: str, doc_id: str) -> int:` | src/engramory/core/repository.py:549 |
| 39  | NEW@pass4 `kb_graph.py` ingests repository knowledge graphs in `ingest_sdd_knowledge_graph` | `async def ingest_sdd_knowledge_graph(` | src/engramory/core/kb_graph.py:551 |
| 40  | NEW@pass6 `chunking.py` defines `chunk_markdown` for splitting markdown documents | `def chunk_markdown(` | src/engramory/core/chunking.py:44 |
| 41  | NEW@pass6 `chunking.py` matches `#{1,3}` headings outside fenced code blocks | `if re.match(r"^#{1,3}\s+", line):` | src/engramory/core/chunking.py:101 |
| 42  | NEW@pass6 `chunking.py` sub-chunks oversized single lines with `MAX_CHUNK_WORDS` | `parts.append(" ".join(ln_words[i : i + MAX_CHUNK_WORDS]))` | src/engramory/core/chunking.py:166 |
| 43  | NEW@pass6 `server.py` `memory_search` schema defines `token_budget` parameter | `"token_budget": {` | src/engramory/mcp/server.py:57 |
| 44  | NEW@pass6 `server.py` `sdd_context_slice` schema defines `max_tokens` and `format` parameters | `"max_tokens": {` | src/engramory/mcp/server.py:170 |

---

## Review log

### Pass 1 - 2026-11-06 - author self-review

- Authored PLAN-013 covering the rebrand of `aidoc-flow-engramory` to `platform-engramory` (with backward-compatible symlink) and full consolidation of `doc-tools` (`0008_runbooks_and_doc_governance.sql` + `engramory docs` CLI & MCP tools).
- Verified all 24 Claim ledger rows against source files in `../aidoc-flow-engramory`.
- Result: Ready for gate check and independent review.

### Pass 2 - 2026-11-06 - independent

- Evaluated by `verified-planning-reviewer` subagent (`fde23e2f-5a07-4ef6-a1d3-69250778a3c6`).
- Findings identified:
  - F1 (Load-bearing): `AccessSurface._decide` checks `KNOWN_ACTIONS` in `src/engramory/access/authz.py:15`; new surface methods `docs_ingest_runbook` and `docs_slice_runbook` must be added to `KNOWN_ACTIONS`.
  - F2 (Load-bearing): `tests/test_migrations.py:11` hardcodes the list of 7 migrations and will fail when `0008_runbooks_and_doc_governance.sql` is added unless updated.
  - F3 (Minor): Typo `lint_Calculation` in Phase 3.2 function name.
  - F4 (Load-bearing): Missing Claim ledger rows for `README.md:74`, `src/engramory/cli.py:97` (`_load_config` offline behavior), `db/migrations/0003_reconcile_contracts.sql:107` (`set_updated_at` trigger), and `src/engramory/core/repository.py:745`.
  - F5 (Load-bearing): Moving `../aidoc-flow-engramory` $\rightarrow$ `../platform-engramory` requires `uv sync` to refresh `.venv` shebangs/editable paths, and Phase 1.1 should note there are 10 files (`00_INDEX.md`..`09_...md`) in `../platform-docs/docs/`.
  - F6 (Load-bearing): Clarify path confinement (`resolve_confined_repo_path`) for MCP tool arguments (`docs_validate`, `docs_check_drift`, `docs_export_llms`) and handle offline `--repo-path` fallback in `cli.py` before `AccessSurface._decide` attempts `record_audit`.
- Folds applied:
  - Folded F1: Added `"docs_ingest_runbook"` and `"docs_slice_runbook"` to `KNOWN_ACTIONS` in Phase 4.1 and added Claim 25 & Claim 26.
  - Folded F2: Added `tests/test_migrations.py:11` update to Phase 2.2 & Phase 5.1 and added Claim 27.
  - Folded F3: Fixed function name to `lint_repo` in Phase 3.3.
  - Folded F4: Added Claims 28–31 (`NEW@pass2`) and specified `trg_kb_documents_updated_at` in Phase 2.1.
  - Folded F5: Specified 10 files in Phase 1.1 and added `uv sync` step after directory rename in Phase 1.3.
  - Folded F6: Added `resolve_confined_repo_path` in Phase 3.1/4.4 and specified offline `--repo-path` dispatch in `cli.py` in Phase 4.3.
- Result: Folded all 6 findings cleanly; re-running `check_plan.py` and dispatching Pass 3 verification.

### Pass 3 - 2026-11-06 - independent

- Evaluated by `verified-planning-reviewer` subagent (`fde23e2f-5a07-4ef6-a1d3-69250778a3c6`) following Pass 2 folds.
- Verified:
  - All 6 Pass 2 findings (`KNOWN_ACTIONS`, `tests/test_migrations.py`, `lint_repo`, `NEW@pass2` Claims 28–31 + `set_updated_at()`, 10 `doc-tools` files + `uv sync`, and `resolve_confined_repo_path` + offline CLI dispatch) are accurately resolved.
  - All 31 Claim ledger citations and internal references match repository source semantics.
- Findings: None. Zero remaining load-bearing findings.

### Pass 4 - 2026-11-06 - gap analysis & author fold

- Folded all 7 gaps identified during user gap review:
  - Gap 1 (`PATHS_DENY` compliance — `.ci-config:72`): Replaced all hardcoded developer workspace paths in `PLAN-013` with `$WORKSPACE_ROOT` / `..` and added explicit sanitization of `docs/doc-tools/*.md` in Phase 1.1 (Claim 32).
  - Gap 2 (Stage port & service alignment — `compose.platform.yaml:10`, `compose.platform.yaml:21`): Aligned `docs/doc-tools/*.md` stage port references to `127.0.0.1:${MCP_PORT:-8005}:8000` in Phase 1.1 (Claims 33–34).
  - Gap 3 (`KnowledgeCore` & `GitSyncEngine` wiring — `src/engramory/core/knowledge.py:73`, `src/engramory/core/git_sync.py:269`, `src/engramory/core/git_sync.py:322`, `src/engramory/core/repository.py:523`): Wired `kb_documents`/`kb_runbook_steps` upsert and `delete_kb_document` into `KnowledgeCore.ingest_documents_batch` and `GitSyncEngine.sync`, and added `engramory docs ingest` in Phase 4.2 & 4.4 (Claims 35–38).
  - Gap 4 (`kb_graph.py` Runbook & Alert extraction — `src/engramory/core/kb_graph.py:521`): Added `extract_docs_graph()` in `src/engramory/core/kb_graph.py` wired into `ingest_sdd_knowledge_graph` in Phase 3.7 (Claim 39).
  - Gap 5 (`smoke-run` CLI & MCP tool): Added `smoke_run_green_steps()` in `src/engramory/core/docs_tools.py`, `engramory docs smoke-run` in `src/engramory/cli.py`, and `docs_smoke_run` in `src/engramory/mcp/server.py` (Phase 3.4, 4.4, 4.5).
  - Gap 6 (`.vale.ini` & `.vale/styles/AIOpsAgent/*.yml` scaffolding): Added scaffolding of `.vale.ini` and the 4 `AIOpsAgent` rule YAML files in Phase 3.3.
  - Gap 7 (Idempotent SQL migration `0008_runbooks_and_doc_governance.sql`): Specified `CREATE TABLE IF NOT EXISTS`, `DROP TRIGGER IF EXISTS`, and `CREATE INDEX IF NOT EXISTS` in Phase 2.1.
- Result: Dispatched Pass 5 independent verification to `verified-planning-reviewer` subagent (`fde23e2f-5a07-4ef6-a1d3-69250778a3c6`).

### Pass 5 - 2026-11-06 - independent

- Evaluated by `verified-planning-reviewer` subagent (`fde23e2f-5a07-4ef6-a1d3-69250778a3c6`) following Pass 4 gap folds.
- Verified:
  - All 7 gaps (`PATHS_DENY` compliance, stage port/service alignment, `KnowledgeCore`/`GitSyncEngine` wiring with `delete_kb_document` matching `doc_id` or `repo_path`, `extract_docs_graph`, `smoke-run`, `.vale.ini` + `AIOpsAgent` rules, and idempotent SQL migration `0008_runbooks_and_doc_governance.sql`) are resolved.
  - All 39 Claim ledger rows (including `NEW@pass4` Claims 32–39) match source semantics.
- Findings: None. Zero remaining load-bearing findings.

### Pass 6 - 2026-11-06 - Google & Anthropic best-practices review & author fold

- Evaluated `PLAN-013` against Anthropic (*Contextual Retrieval*, *Building Effective Agents / ACI*, *Prompt Caching*) and Google (*SRE Executable Playbooks*, *Docs-as-Code Referential Integrity*, *Subprocess Defense-in-Depth*) best practices and folded all 6 findings:
  - Folded Gap 1 (Anthropic Contextual Retrieval & Fence-Aware Chunking — `src/engramory/core/chunking.py:44,52,77`): Added Phase 3.2 fixing fenced code block tracking (` ``` `/`~~~`), preserving line breaks when sub-chunking, and prepending `[Doc: ... | Title: ... | Kind: ... | Domain: ... | Tier: ... | Section: ...]` Contextual Retrieval headers (Claims 40–42).
  - Folded Gap 2 (Anthropic ACI & Prompt-Cache Prefix Ordering — `src/engramory/mcp/server.py:57,170`): Added `token_budget`, `format` (`"markdown" | "json"`), deterministic L0-before-L2 ordering, MCP tool safety `annotations`, and `remediation_hint` on linter diagnostics in Phase 3.4, 4.3, 4.4, and 4.5 (Claims 43–44).
  - Folded Gap 3 (Google SRE Subprocess Defense-in-Depth): Hardened `INV-STEP-02` against subshells (`$(...)`, backticks), redirection (`>`, `>>`), and mutating `curl`/`wget` flags; scrubbed secret env vars, capped output at `8192` bytes, and defaulted MCP `docs_smoke_run` to `dry_run=True` in Phase 3.3, 3.5, and 4.5.
  - Folded Gap 4 (Google Docs-as-Code Referential Integrity): Added `BROKEN_RELATIVE_LINK`, `DEAD_WATCHED_PATH_GLOB`, and `MISSING_ROLLBACK_STEP` checks to Phase 3.4.
  - Folded Gap 5 (Atomic Orphan Step Cleanup on Re-Ingestion): Updated `Repository.upsert_kb_runbook_steps` in Phase 4.1 to atomically delete stale `kb_runbook_steps` for `(tenant_id, doc_id)` before inserting current steps.
  - Folded Gap 6 (Anthropic Evaluation-Driven Development): Added `test_golden_runbook_retrieval_and_chunk_integrity` in Phase 5.1.
- Result: Dispatched Pass 7 independent verification to `verified-planning-reviewer` subagent (`fde23e2f-5a07-4ef6-a1d3-69250778a3c6`).

### Pass 7 - 2026-11-06 - independent

- Evaluated by `verified-planning-reviewer` subagent (`fde23e2f-5a07-4ef6-a1d3-69250778a3c6`) following Pass 6 Google & Anthropic best-practice folds.
- Verified:
  - All 6 Google & Anthropic best-practice improvements (fence-aware chunking + Contextual Retrieval headers in `chunk_markdown`, ACI `token_budget`/`format`/L0-before-L2 ordering/MCP `annotations`/`remediation_hint`, hardened `INV-STEP-02` + subprocess env scrubbing + `8192`-byte output cap + MCP `dry_run=True` default, Docs-as-Code referential integrity `BROKEN_RELATIVE_LINK`/`DEAD_WATCHED_PATH_GLOB`/`MISSING_ROLLBACK_STEP`, atomic orphan step cleanup in `upsert_kb_runbook_steps`, and golden retrieval eval `test_golden_runbook_retrieval_and_chunk_integrity`) are resolved.
  - All 44 Claim ledger rows (including `NEW@pass6` Claims 40–44) match source semantics.
- Findings: None. Zero remaining load-bearing findings.
- **Result:** ready
