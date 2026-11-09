# PLAN-016 — Built-In Context Tools Migration (`aidoc-context-tools` -> `platform-engramory`)

**Status:** Verified & Ready for Execution
**Date:** 2026-11-09
**Author:** AI Coding Agent
**Related Plans:** `PLAN-013`, `PLAN-014`, `PLAN-015`

---

## 1. Objective & Scope

With `archived/aidoc-context-tools` retired, this plan audits and consolidates the complete `aidoc-context-tools` capability set into `platform-engramory` as a first-class built-in module (`src/engramory/core/context_tools.py`), exposed through the `engramory` CLI (`engramory sdd ...`) and the `McpServer` gateway (`sdd_validate_graph`, `sdd_check_invariants`, `sdd_lint_policy`, plus offline/compacted fallback on `sdd_context_slice` and `sdd_blast_radius`).

### Migration Audit Summary (`archived/aidoc-context-tools` vs `platform-engramory`)

| Capability in `archived/aidoc-context-tools` | Current Status in `platform-engramory` | Action in `PLAN-016` |
|---|---|---|
| Online SDD corpus ingestion (`sdd_ingest`) | **Present** (`extract_sdd_graph` / `ingest_sdd_knowledge_graph` in `kb_graph.py`) | Keep & complement with local DAG scanner |
| Online Neo4j/Postgres `sdd_context_slice` & `sdd_blast_radius` | **Present** (`AccessSurface.context_slice` / `blast_radius` in `surface.py`) | Enhance with offline local DAG fallback & Tier 0/1/2 token compaction (`render_context_digest`) |
| Local/Offline SDD-GRAPH v2.0 DAG Scanner & `@project:layer:tag` Scoped Contract Resolver (`graph.py` + `resolver.py`) | **Missing** | Implement `ArtifactNode`, `ScopedContractResolver`, and `AidocDependencyGraph` in `src/engramory/core/context_tools.py` |
| Tiered Token Compaction (`compact_node_content` Tier 0/1/2 & `ContextSlicer.render_context_digest` in `slicer.py`) | **Missing** | Implement `ContextSlicer`, `compact_node_content`, and `render_context_digest` in `src/engramory/core/context_tools.py` |
| DAG Cycle Detection & SDD-GRAPH v2.0 Semantic Cardinality Validator (`validate-graph` / `GRAPH-CARD-001..003`) | **Missing** | Implement `validate_sdd_graph`, CLI `engramory sdd validate-graph`, and MCP `sdd_validate_graph` |
| Pre-Merge Contract Erosion Invariant Gate (`check-invariants` / `InvariantChecker` in `invariants.py`) | **Missing** | Implement `InvariantChecker` (hardened against git ref option injection), CLI `engramory sdd check-invariants`, and MCP `sdd_check_invariants` |
| Stack Policy Validator (`lint-policy` / `StackPolicyValidator` in `policy.py`) | **Missing** | Implement `StackPolicyValidator`, CLI `engramory sdd lint-policy`, and MCP `sdd_lint_policy` |

---

## Claim ledger

| # | Claim | Symbol | Citation |
|---|---|---|---|
| 1 | `KNOWN_ACTIONS` in `src/engramory/access/authz.py` is a closed `frozenset` containing `context_slice`, `blast_radius`, and `sdd_ingest`, and denies unknown actions with `unknown_action`. | `KNOWN_ACTIONS` | src/engramory/access/authz.py:15-35 |
| 2 | `AccessSurface.context_slice` queries `self._graph.ancestors` and falls back to `self._repo.find_ancestors`, returning `Sequence[Mapping[str, Any]]` without local workspace fallback or Tier 1/2 formal compaction. | `context_slice` | src/engramory/access/surface.py:246-304 |
| 3 | `AccessSurface.blast_radius` queries `self._graph.descendants` and falls back to `self._repo.find_descendants` without local workspace fallback. | `blast_radius` | src/engramory/access/surface.py:306-337 |
| 4 | `resolve_confined_repo_path` in `src/engramory/core/docs_tools.py` enforces workspace and multi-project path confinement (`enforce_project_boundary`) and raises `ValidationError` on traversal outside the allowed root. | `resolve_confined_repo_path` | src/engramory/core/docs_tools.py:218-260 |
| 5 | `_GIT_REF_RE` (`r"^[A-Za-z0-9_./~^-]+$"`) in `src/engramory/core/docs_tools.py` validates git refs and rejects leading `-` flags to prevent git option injection. | `_GIT_REF_RE` | src/engramory/core/docs_tools.py:162 |
| 6 | `_cmd_sdd_slice` and `_cmd_sdd_blast_radius` in `src/engramory/cli.py` invoke `_run_with_surface(args, op, ...)` and fail with exit code 2 (`ConfigError`) or 3 (`StoreUnavailable`) if no `.engramory/config.toml` or database is available. | `_cmd_sdd_slice` | src/engramory/cli.py:526-599 |
| 7 | `p_sdd` in `_build_parser()` in `src/engramory/cli.py` registers `ingest`, `slice`, `blast-radius`, `validate-graph`, `check-invariants`, and `lint-policy` subcommands under `engramory sdd`. | `p_sdd` | src/engramory/cli.py:920 |
| 8 | `_cmd_docs_slice_runbook` in `src/engramory/cli.py` supports `--repo-path` for offline local slicing and automatically falls back to local workspace slicing when `(ConfigError, StoreUnavailable)` is raised. | `_cmd_docs_slice_runbook` | src/engramory/cli.py:1140 |
| 9 | `McpServer.handle_call_tool` in `src/engramory/mcp/server.py` dispatches `sdd_context_slice`, `sdd_blast_radius`, `sdd_ingest`, `sdd_validate_graph`, `sdd_check_invariants`, `sdd_lint_policy`, and `name.startswith("docs_")`. | `handle_call_tool` | src/engramory/mcp/server.py:339 |
| 10 | `TOOLS_SCHEMA` in `src/engramory/mcp/server.py` registers `sdd_context_slice`, `sdd_blast_radius`, and `sdd_ingest`, and extends `DOCS_TOOLS_SCHEMA` and `SDD_CONTEXT_TOOLS_SCHEMA` at module load. | `DOCS_TOOLS_SCHEMA` | src/engramory/mcp/server.py:1067 |

---

## Phases

### Phase 1: Built-In Context Tools Core Engine (`src/engramory/core/context_tools.py`)

**Files:**

- `src/engramory/core/context_tools.py` (new)

**Steps:**

1. Implement `ArtifactNode`, `CANONICAL_LAYERS`, `MAX_SCAN_FILE_BYTES = 2 * 1024 * 1024` (2 MiB), and `normalize_layer` for SDD layers (`BRD`, `PRD`, `EARS`, `BDD`, `ADR`, `SPEC`, `TDD`, `TASK`, `IPLAN`, `CHG`, `EVAL`).
2. Implement `ScopedContractResolver`:
   - Confine all local workspace, `.aidoc/contracts`, and `proto/` lookups within the resolved workspace root using `_is_within_root` and `_read_bounded_text(path, max_bytes=MAX_SCAN_FILE_BYTES)` (preventing symlink escape, path traversal via malicious `@project:layer:../../etc/passwd` pointers, and oversized file DoS).
   - Restrict `tag_id`, `project_id`, and `layer` tokens to safe identifier characters (`^[A-Za-z0-9_.-]+$`, rejecting `.` and `..`).
   - Disable both `AIDOC_CONTRACT_PATH` and sibling repository discovery when `allow_sibling_lookup=False` (used when `enforce_project_boundary=True` in authenticated multi-project MCP calls). When `allow_sibling_lookup=True`, verify `_is_within_root(candidate_file, search_root)` on every candidate file inside a sibling/search root and return relativized `file_path` metadata (`f"{project_id}/{rel}"`) so raw host paths are never leaked.
3. Implement `AidocDependencyGraph`:
   - `scan_workspace(root_dir)` scanning all present SDD directories (`sdd/`, `docs/sdd/`, `framework/layers/`, or `root_dir` if none of those subdirectories exist) without double-scanning, using `os.walk(..., followlinks=False)`, enforcing `_is_within_root(file_path, resolved_root)` and `MAX_SCAN_FILE_BYTES` on every file, and skipping excluded directories.
   - Parse SDD-GRAPH v2.0 YAML (`schema_version in ("2.0-graph", "2.0")`, `graph_type == "SDD-GRAPH"`, or `nodes`/`edges` lists), standard single-doc SDD YAML (`id`/`task_id`/`doc_id` or `document_control.{document_id,iplan_id,chg_id,spec_id}`, `depends_on`, `related_decisions.dependencies`, `trace`/`traceability`), and Markdown YAML frontmatter (`id`, `title`, `layer`, `depends_on`, `trace`) + `@`-pointers (`@spec:...` and `@project:layer:tag`).
   - Implement `detect_cycles()` (DFS cycle detection) and `validate_invariants()` enforcing:
     - `GRAPH-CARD-001`: Seed intent (`BRD`/`PRD`) decomposes into $\ge 1$ downstream capability/requirement (`EARS`, `SPEC`, `BDD`, `TASK`, `IPLAN`).
     - `GRAPH-CARD-002`: Every `EARS` requirement traces to upstream intent (`BRD`, `PRD`, `ADR`) when seed nodes exist.
     - `GRAPH-CARD-003` (Oracle Invariant): Every `EARS` requirement has $\ge 1$ verifying `BDD` or `TDD` artifact.
   - Implement `descendants(doc_id, max_depth=6)` on `AidocDependencyGraph` for local/offline blast-radius calculation, normalizing IDs (stripping `@` and matching `normalize_sdd_id` variants) for reverse dependency matching and `visited` tracking.
4. Implement `ContextSlicer`, `estimate_tokens`, and `compact_node_content`:
   - Tier 0 (full content), Tier 1 (frontmatter + headings/bullets/formal `Given`/`When`/`Then`/`SHALL`/`MUST`/`rpc`/`message`/`service`/`enum` lines), Tier 2 (frontmatter + first heading summary).
   - `slice_subgraph(target_task_id, allowed_layers=None, max_depth=6)` and `render_context_digest(target_task_id, max_depth=6, max_tokens=None, allowed_layers=None)`, normalizing IDs (`node.node_id.lstrip("@")` / `normalize_sdd_id`) for `visited` tracking and self-exclusion so `@`-prefixed and unprefixed references never duplicate or include the target task in its own ancestor closure.
5. Implement `InvariantChecker`:
   - Validate `base_ref` with `_validate_git_ref(base_ref)` (rejecting leading `-` or invalid characters via `ValidationError`) before invoking `git rev-parse`, `git merge-base`, or `git diff`, and enforce `timeout=10` on all `subprocess.run` invocations (catching `subprocess.TimeoutExpired` gracefully).
   - Implement `get_changed_files(base_ref="origin/dev")`, `check_contract_erosion(changed_files, chg_type="CODE2C")`, and `check_blast_radius_warnings(changed_files, graph=None)` using the local `AidocDependencyGraph` (or online descendants) to flag modified `ADR`/`SPEC`/`PRD`/`BRD` contracts whose downstream `IPLAN`/`TASK`/`TDD` dependents are unchanged.
6. Implement `StackPolicyValidator`:
   - In `load_policy(policy_path=None)`, resolve `policy_path` relative to `root_dir`, verify `_is_within_root(resolved_policy, resolved_root)` (raising `ValidationError` on path escape), and enforce `MAX_SCAN_FILE_BYTES`.
   - Scan source files (up to `MAX_SCAN_FILE_BYTES`, with `followlinks=False` and `_is_within_root`) for `boundaries.banned_patterns`.
   - Normalize package names per PEP 503 (`re.sub(r"[-_.]+", "-", name).lower()`), parse `pyproject.toml` `[project.dependencies]` via standard-library `tomllib.loads`, validate `requirements.txt` and `uv.lock` against `allowed_packages`, and reject legacy `poetry.lock` whenever `strict_allowlist` is enabled (or whenever `poetry.lock` is present under stack policy validation).
7. Provide top-level convenience functions returning structured dictionaries for CLI and MCP, where node dicts in `slice_local_context` and `local_blast_radius` include both flat fields (`id`, `layer`, `title`, `file_path`) and `AccessSurface`-compatible fields (`labels: [layer]`, `properties: {"name": title, "title": title, "layer": layer, "path": file_path, "summary": ...}`) so CLI human output and `format_context_slice_markdown` work identically online and offline:
   - `slice_local_context(root_dir, *, task_id, depth=6, layers=None, max_tokens=2000, output_format="json", allow_sibling_lookup=True)`
   - `local_blast_radius(root_dir, *, doc_id, depth=6, allow_sibling_lookup=True)`
   - `validate_sdd_graph(root_dir, *, check_invariants=False, allow_sibling_lookup=True)`
   - `check_sdd_invariants(root_dir, *, base_ref="origin/dev", chg_type="CODE2C", include_blast_warnings=True, allow_sibling_lookup=True)`
   - `lint_stack_policy(root_dir, *, policy_path=None)`

### Phase 2: Authorization, CLI (`engramory sdd ...`), & MCP Gateway Integration

**Files:**

- `src/engramory/access/authz.py`
- `src/engramory/cli.py`
- `src/engramory/mcp/server.py`

**Steps:**

1. Add `"sdd_validate_graph"`, `"sdd_check_invariants"`, and `"sdd_lint_policy"` to `KNOWN_ACTIONS` in [src/engramory/access/authz.py:15-35](../src/engramory/access/authz.py#L15-L35).
2. Enhance `engramory sdd` subcommands in [src/engramory/cli.py:789-850](../src/engramory/cli.py#L789-L850):
   - On `engramory sdd slice`: add `--root` (default `None`), `--offline` (`action="store_true"`), and `--digest` (`action="store_true"`). When `--offline` or `--root` is passed, or when `_run_with_surface` raises `(ConfigError, StoreUnavailable)`, fall back to `slice_local_context(...)`. Support `--digest` to emit the Tier 0/1/2 compacted prompt digest (`render_context_digest`).
   - On `engramory sdd blast-radius`: add `--root` (default `None`) and `--offline` (`action="store_true"`), falling back to `local_blast_radius(...)` when `--offline`/`--root` is specified or `(ConfigError, StoreUnavailable)` occurs.
   - Add `engramory sdd validate-graph [--root .] [--invariants]` (`_cmd_sdd_validate_graph`), `engramory sdd check-invariants [--root .] [--base origin/dev] [--type CODE2C] [--offline]` (`_cmd_sdd_check_invariants`), and `engramory sdd lint-policy [--root .] [--policy .aidoc/stack-policy.yaml]` (`_cmd_sdd_lint_policy`), each emitting structured output via `_emit(args.json, report, summary)` and returning `0` when `report["passed"]` is `True` and `1` when `False`.
3. Extend `McpServer` in [src/engramory/mcp/server.py:317-470](../src/engramory/mcp/server.py#L317-L470) and `TOOLS_SCHEMA`:
   - Add `SDD_CONTEXT_TOOLS_SCHEMA` (`sdd_validate_graph`, `sdd_check_invariants`, `sdd_lint_policy`) with `readOnlyHint: True`, and add optional `workspace_dir` / `offline` / `format="digest"` support to `sdd_context_slice` and `sdd_blast_radius`.
   - Ensure `sdd_context_slice` and `sdd_blast_radius` ALWAYS invoke `await self._surface._decide(call_ctx, "context_slice" / "blast_radius", TargetScope(tenant_id=call_ctx.tenant_id, scope=scope))` even when `offline=True` is passed, preserving the `SPEC-01` / `EARS.01.03.b050` audit invariant.
   - Enforce `self._surface._decide(call_ctx, tool_name, TargetScope(tenant_id=call_ctx.tenant_id, scope=scope))` and `resolve_confined_repo_path(..., project_id=call_ctx.project_id, enforce_project_boundary=enforce_project)` on every workspace-reading tool (`allow_sibling_lookup=not enforce_project`).

### Phase 3: Unit, Integration, & Adversarial Security Tests (`tests/test_context_tools.py`)

**Files:**

- `tests/test_context_tools.py` (new)

**Steps:**

1. Test `AidocDependencyGraph` & `ScopedContractResolver`:
   - SDD-GRAPH v2.0 YAML parsing, single-doc YAML parsing (including `document_control` and `related_decisions`), Markdown frontmatter + `@`-pointer resolution, local `proto/*.proto` resolution, and symlink/traversal rejection (`@proj:SPEC:../../etc/passwd`).
   - `detect_cycles()` detecting circular dependencies and `validate_invariants()` catching `GRAPH-CARD-001`, `GRAPH-CARD-002`, and `GRAPH-CARD-003` violations.
2. Test `ContextSlicer` & tiered compaction:
   - Ancestor closure ordering, `@`-prefix normalization (no self-inclusion or duplicate nodes), layer filtering, `compact_node_content` Tier 1 & Tier 2 compaction, `render_context_digest` under tight `max_tokens` budgets, and local `descendants` blast radius.
3. Test `InvariantChecker` & `StackPolicyValidator`:
   - `check_contract_erosion` blocking `CODE2C`/`DIR2C`/`HOTFIX` edits to `proto/*.proto` or `docs/sdd/06_SPEC/contracts/*` while allowing `SEED2C`/`SDD2C`.
   - `check_blast_radius_warnings` emitting advisories when `ADR-01` is modified without its downstream `IPLAN` dependent.
   - Git ref validation rejecting `--upload-pack=...` option injection with `ValidationError`.
   - `StackPolicyValidator` catching banned patterns, PEP 503 normalized disallowed packages in `pyproject.toml` / `requirements.txt` / `uv.lock`, legacy `poetry.lock`, and rejecting `policy_path` traversal outside `root_dir` with `ValidationError`.
4. Test CLI (`engramory sdd validate-graph`, `check-invariants`, `lint-policy`, `slice --offline --digest`, `blast-radius --offline`) and MCP (`sdd_validate_graph`, `sdd_check_invariants`, `sdd_lint_policy`, `sdd_context_slice` with `offline=True` / `format="digest"`, and cross-project boundary enforcement when `auth_enabled=True`).

---

## 4. Verification & Acceptance Criteria

- `python3 .agents/skills/verified-planning/check_plan.py plans/PLAN-016_builtin-context-tools-migration.md` passes.
- `uv run pytest tests/test_context_tools.py` passes.
- `task check` and `task app:test` pass with coverage $\ge 80\%$.

---

## Review log

### Pass 1 - 2026-11-09

- **Reviewer:** Authoring Agent (`self`)
- **Findings:**
  1. Verified all 10 citations in the Claim ledger against live workspace files (`authz.py`, `surface.py`, `docs_tools.py`, `cli.py`, `mcp/server.py`).
  2. Confirmed that `ScopedContractResolver` in `archived/aidoc-context-tools` did not validate `tag_id` or `project_id` against `..` path traversal and allowed sibling directory reads unconditionally; added path-confinement (`_is_within_root`) and `allow_sibling_lookup=not enforce_project` in Phase 1 Step 2 and Phase 2 Step 3.
  3. Confirmed that `InvariantChecker.get_changed_files` in `archived/aidoc-context-tools` passed `base_ref` directly to `git rev-parse` / `git merge-base`; added `_validate_git_ref(base_ref)` in Phase 1 Step 5.
- **Result:** All citations verified; awaiting Pass 2 independent fresh-context multi-lens review.

### Pass 2 - 2026-11-09 - independent

- **Reviewer:** Independent Fresh-Context Subagent (`4f248020-3410-412e-93be-85f4404fb8b7`, Google / Anthropic / Microsoft lenses)
- **Findings & Resolutions Folded:**
  1. **[Critical — Microsoft Lens (`AIDOC_CONTRACT_PATH`, sibling symlink escape, and `policy_path` confinement)]:** Updated Phase 1 Steps 2 & 6 so `AIDOC_CONTRACT_PATH` and sibling lookups are both disabled when `allow_sibling_lookup=False`; when `allow_sibling_lookup=True`, candidate files inside search roots must pass `_is_within_root(candidate, search_root)` and return relativized `file_path` metadata; and `StackPolicyValidator.load_policy` verifies `_is_within_root(resolved_policy, resolved_root)` (raising `ValidationError` on escape).
  2. **[Major — Anthropic Lens (`AccessSurface` return shape compatibility on offline fallback)]:** Updated Phase 1 Step 7 so `slice_local_context` and `local_blast_radius` include `"labels": [layer]` and `"properties": {"name": title, "title": title, "layer": layer, "path": file_path, "summary": ...}` alongside flat keys (`id`, `layer`, `title`, `file_path`), keeping CLI human output and `format_context_slice_markdown` identical across online and offline modes.
  3. **[Major — Google Lens (`@`-prefix and SDD ID normalization in `ContextSlicer` and `descendants`)]:** Updated Phase 1 Steps 3 & 4 to normalize node IDs (`lstrip("@")` and `normalize_sdd_id`) during `visited` tracking, self-exclusion, and reverse dependency matching.
  4. **[Major — Google Lens (Multi-directory SDD scanning & frontmatter/`document_control` compatibility)]:** Updated Phase 1 Step 3 so `scan_workspace` scans all present SDD directories (`sdd/`, `docs/sdd/`, `framework/layers/`, or `root_dir`) without double-scanning and parses `document_control`, `related_decisions.dependencies`, `trace`/`traceability`, and Markdown YAML frontmatter.
  5. **[Major — Anthropic Lens (`_decide` audit invariant on MCP `offline=True` and CLI exit codes)]:** Updated Phase 2 Steps 2 & 3 so MCP `sdd_context_slice` and `sdd_blast_radius` always invoke `await self._surface._decide(...)` even when `offline=True`, and CLI validation subcommands emit via `_emit(args.json, report, summary)` and exit `0` on pass / `1` on failure.
  6. **[Major — Microsoft Lens (`MAX_SCAN_FILE_BYTES` & `followlinks=False` DoS guards)]:** Added `MAX_SCAN_FILE_BYTES = 2 * 1024 * 1024` and `os.walk(..., followlinks=False)` across Phase 1 Steps 1, 2, 3, and 6.
  7. **[Medium — Microsoft & Google Lenses (`subprocess.run` timeout=10, PEP 503 normalization, `tomllib`)]:** Added `timeout=10` to all `subprocess.run` calls in `InvariantChecker.get_changed_files` (Phase 1 Step 5) and PEP 503 package name normalization + `tomllib.loads` parsing in `StackPolicyValidator` (Phase 1 Step 6).
- **Result:** ready — no further findings.
