# PLAN-019: Consolidate `platform-docs` Capabilities into `platform-engramory` as Built-In Modules

> **Status:** Ready for Implementation  
> **Date:** 2026-11-09  
> **Repository:** `platform-engramory`  
> **Target Branch:** `feature/plan-018-harness-v0.2.0-reinit` -> `dev`  
> **Governance Standard:** `aiops-harness` v0.2.0 (`verified-planning`)  
> **Companion Plan on Branch:** `plans/PLAN-018_aiops-harness-v0.2.0-reinitialization.md`  
> **Retiring External Spoke:** `$WORKSPACE_ROOT/platform-docs`

---

## 1. Context & Motivation

This branch (`feature/plan-018-harness-v0.2.0-reinit`) combines two governed deliverables that together complete the `aiops-harness` v0.2.0 reinitialization and `platform-docs` consolidation on `platform-engramory`:

- **`PLAN-018` (`plans/PLAN-018_aiops-harness-v0.2.0-reinitialization.md`):** Reinitializes `platform-engramory` onto `aiops-harness` v0.2.0 (`a669f84`), updating the vendored `.ci/`, `.agents/`, `.unified-test-suite/`, `.aidoc/`, and `.github/workflows/` canons, removing deprecated `CLAUDE.md`, archiving root `CHANGELOG.md` to `docs/archive/CHANGELOG.md`, updating `AGENTS.md`, `Taskfile.yml`, `.ci-config`, and `.gitignore`, and aligning the `docs/sdd/` 10-layer SDD artifacts and `docs/sdd/graph/` property graph.
- **`PLAN-019` (this plan):** Consolidates the remaining `$WORKSPACE_ROOT/platform-docs` capabilities directly into `platform-engramory` as built-in modules so `$WORKSPACE_ROOT/platform-docs` can be retired.

Earlier consolidation work (`PLAN-013`) integrated the core AI-first documentation parser, executable runbook AST validator (`INV-STEP-01`/`INV-STEP-02`), drift detector, `/llms.txt` exporter, and `docs/doc-tools/00_INDEX.md`..`09_ENGRAMORY_CONSOLIDATION_AND_EXTENSION_BLUEPRINT.md` into `platform-engramory`. However, an audit against the standalone `$WORKSPACE_ROOT/platform-docs` spoke identified five capabilities that still exist only in `$WORKSPACE_ROOT/platform-docs` (or have flag/catalog deltas):

1. **Governed Documentation Tool Inventory (`DOCUMENTATION_TOOL_CATALOG` & `tools` / `tool-inventory` CLI/MCP surface):**
   - `$WORKSPACE_ROOT/platform-docs` defines the 12-entry `DOCUMENTATION_TOOL_CATALOG` (8 `Adopt`, 2 `Trial` [`@astrojs/starlight`, `@scalar/api-reference`], 2 `Hold` [`copybara`, `mintlify / readme / mkdocs-material-insiders`]) using the 6-key schema `{"tool", "category", "tier", "gate", "license", "role"}`, and exposes `tools` / `tool-inventory` (`--tier Adopt|Trial|Hold`, `--json`).
   - `src/engramory/core/docs_tools.py` and `src/engramory/cli.py` do not yet include `DOCUMENTATION_TOOL_CATALOG`, `list_documentation_tools()`, `engramory docs tools` (`tool-inventory`), or the MCP `docs_tools_inventory` tool.
2. **Built-In Starlight + Scalar Portal Bundle Stager (`portal_builder.py` & `build-portal` CLI/MCP surface):**
   - `$WORKSPACE_ROOT/platform-docs` provides `stage_portal_bundle()` (`portal_builder.py`) and the `build-portal` CLI subcommand to stage governed `docs/*.md` files into `portal/src/content/docs/reference` with path-confinement validation and Rule D14 secret/Unicode scrubbing.
   - `platform-engramory` lacks `src/engramory/core/portal_builder.py`, `engramory docs build-portal`, and the MCP `docs_build_portal` tool.
3. **Governed Lint Enforcement Flags (`run_governed_lint`, `--require-frontmatter`, `--verify-registry`, `--target`, and subdirectory `smoke-run`/`slice-runbook` discovery):**
   - `lint_repo()` in `src/engramory/core/docs_tools.py:1012-1040` currently auto-detects frontmatter requirement (`req_fm = "doc-tools" in f.parts or f.read_text(encoding="utf-8").startswith("---\n")`) so legacy un-frontmattered files (`docs/ARCHITECTURE.md`) do not fail `DOC-FM-000` by default, whereas `$WORKSPACE_ROOT/platform-docs` exposes `run_governed_lint()` (`require_frontmatter=True` across all inspected Markdown files) plus `--require-frontmatter`, `--verify-registry`, and `--target` CLI flags.
   - In addition, `smoke_run_green_steps()` (`src/engramory/core/docs_tools.py:1107-1113`) returns `raw_files = []` when pointed at a subdirectory that does not itself contain a `docs/` child (such as `docs/doc-tools`), and `slice_local_runbooks()` (`src/engramory/core/docs_tools.py:1631`) uses non-recursive `glob("*.md")` instead of `rglob("*.md")`.
4. **Technical Reference Specification (`10_DOCUMENTATION_TOOLS_TECHNICAL_REFERENCE.md`) & Vale Style Parity:**
   - `docs/doc-tools/` in `platform-engramory` currently contains `00_INDEX.md` through `09_ENGRAMORY_CONSOLIDATION_AND_EXTENSION_BLUEPRINT.md`, missing `10_DOCUMENTATION_TOOLS_TECHNICAL_REFERENCE.md`.
   - `.vale/styles/AIOpsAgent/*.yml` in `platform-engramory` needs its `DeterministicPortFormat.yml` (`https?://localhost:\d+` with `nonword: true`) and `NoAmbiguousSubjectStart.yml` verb alternation (`is|are|was|were|will|should|must|can|triggers|runs|fails`) aligned with `src/engramory/core/docs_tools.py:163-166`.
5. **Starlight + Scalar Static Portal Scaffold (`portal/`):**
   - `$WORKSPACE_ROOT/platform-docs/portal/` (`portal/package.json` and `portal/astro.config.mjs`) is not yet present in `platform-engramory/portal/`.

Consolidating these five capabilities directly into `platform-engramory` as first-class built-in modules fulfills ADR-08 (*Two Bounded Cores on One Spine*) and `docs/doc-tools/09_ENGRAMORY_CONSOLIDATION_AND_EXTENSION_BLUEPRINT.md`, allowing `$WORKSPACE_ROOT/platform-docs` to be cleanly retired with zero loss of functionality.

---

## 2. Objectives & Non-Goals

### 2.1 Objectives

1. **Built-In Tool Inventory (`src/engramory/core/docs_tools.py`):** Add `DOCUMENTATION_TOOL_CATALOG` (12 governed tool definitions: 8 `Adopt`, 2 `Trial`, 2 `Hold`, each with keys `tool`, `category`, `tier`, `gate`, `license`, `role`) and `list_documentation_tools(*, tier=None)`.
2. **Governed Lint & Subdirectory Discovery Parity (`src/engramory/core/docs_tools.py`):**
   - Extend `lint_markdown_file()` and `lint_repo()` with `require_frontmatter: bool = False` and `verify_registry: bool = False` (`DOC-REG-001` validating `endpoints.host_loopback`, `endpoints.container_gateway`, and `triggers.cli_commands` / `triggers.mcp_tools` structure), and provide `run_governed_lint(target_path, *, strict=False, verify_registry=False)` that enforces `require_frontmatter=True` across all target Markdown files.
   - Fix `smoke_run_green_steps()` (`src/engramory/core/docs_tools.py:1112`) and `slice_local_runbooks()` (`src/engramory/core/docs_tools.py:1631`) so directory targets without a nested `docs/` child fall back to `sorted(root.rglob("*.md"))` (matching `lint_repo()` at `src/engramory/core/docs_tools.py:1031`).
   - Extend `export_repo_llms()` (`src/engramory/core/docs_tools.py:1400`) to include `portal/README.md` alongside `README.md` and `AGENTS.md`.
3. **Built-In Portal Builder Module (`src/engramory/core/portal_builder.py`):** Implement `stage_portal_bundle(repo_root, *, output_dir=None, dry_run=False, allowed_root=None)` using `resolve_confined_repo_path`, `_is_within_root`, and `_scrub_manifest_text` from `engramory.core.docs_tools`, and re-export `stage_portal_bundle` from `engramory.core.docs_tools`.
4. **CLI & MCP Parity (`src/engramory/cli.py`, `src/engramory/access/authz.py`, `src/engramory/mcp/server.py`):**
   - Add `engramory docs tools` (and alias `engramory docs tool-inventory`) with `--tier {Adopt,Trial,Hold}` and `--json` (`default=argparse.SUPPRESS`).
   - Add `engramory docs build-portal` with positional `path` (`nargs="?"`, `default="."`), `--repo-path` / `--target` (`dest="repo_path"`, `default=None`), `--output-dir`, `--dry-run`, and `--json` (`default=argparse.SUPPRESS`).
   - Enhance `engramory docs lint`, `check-drift`, `export-llms`, `slice-runbook`, and `smoke-run` with `--target` aliases, `--base`/`--head` aliases on `check-drift`, `--require-frontmatter` and `--verify-registry` on `lint`, and `--json` (`default=argparse.SUPPRESS`) on all `docs` subparsers so both `engramory --json docs <cmd>` and `engramory docs <cmd> --json` work without `argparse` default clobbering.
   - Register `docs_tools_inventory` and `docs_build_portal` in `KNOWN_ACTIONS` (`src/engramory/access/authz.py:15-38`), `DOCS_TOOLS_SCHEMA` (`src/engramory/mcp/server.py:1189-1310`, including `"annotations": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True}` and `"scope"` property), and `_handle_docs_tool` (`src/engramory/mcp/server.py:972-1102`), enforcing `enforce_project_boundary=enforce_project` on both `repo_path` and `output_dir` and forcing `dry_run=True` on `docs_build_portal` when `self._auth_enabled` is `True`.
5. **Specification Suite, Vale Rules & Portal Scaffold (`docs/doc-tools/`, `.vale/`, `portal/`):**
   - Add `docs/doc-tools/10_DOCUMENTATION_TOOLS_TECHNICAL_REFERENCE.md` and update `docs/doc-tools/00_INDEX.md` and `docs/doc-tools/09_ENGRAMORY_CONSOLIDATION_AND_EXTENSION_BLUEPRINT.md` to reference Document 10 and record the retirement of standalone `platform-docs` (ensuring zero hardcoded local `/home/.../devd` paths so `.ci-config` `PATHS_DENY` passes).
   - Harmonize `.vale/styles/AIOpsAgent/DeterministicPortFormat.yml` and `.vale/styles/AIOpsAgent/NoAmbiguousSubjectStart.yml` with `src/engramory/core/docs_tools.py:163-166, 874-893`.
   - Add `portal/package.json` (including `@astrojs/starlight`, `@scalar/api-reference`, `@scalar/astro`, `astro`, `mermaid`), `portal/astro.config.mjs`, and `portal/README.md`.
   - Regenerate `llms.txt` and `llms-full.txt` via `engramory docs export-llms . --write --full`.
6. **Comprehensive Test Coverage (`tests/test_docs_tools.py`):** Add unit, CLI, MCP, and security tests covering `DOCUMENTATION_TOOL_CATALOG` (8 `Adopt`, 2 `Trial`, 2 `Hold`), `list_documentation_tools`, `run_governed_lint`, `lint_repo(..., require_frontmatter=True, verify_registry=True)`, `stage_portal_bundle` (including symlink escape rejection, `output_dir` confinement, and Rule D14 secret scrubbing), `portal/` scaffold validation, and `docs/doc-tools/10_DOCUMENTATION_TOOLS_TECHNICAL_REFERENCE.md`.
7. **Harness v0.2.0 Reinitialization (`PLAN-018` Companion Deliverables on Branch):** Include the `aiops-harness` v0.2.0 canon synchronization (`.ci/`, `.agents/`, `.unified-test-suite/`, `.aidoc/`, `.github/workflows/`, `AGENTS.md`, `Taskfile.yml`, `.ci-config`, `CLAUDE.md` removal, `CHANGELOG.md` relocation to `docs/archive/CHANGELOG.md`, and `docs/sdd/` + `docs/sdd/graph/` alignment) authorized by `plans/PLAN-018_aiops-harness-v0.2.0-reinitialization.md`.

### 2.2 Non-Goals

- Adding a Node.js runtime service or `node_modules` tree to `Taskfile.yml` (the `portal/` directory is a static Starlight + Scalar build scaffold staged by `engramory docs build-portal`, keeping `platform-engramory`'s local CI gate fast and pure-Python).
- Modifying database schemas under `db/migrations/`.
- Deleting `$WORKSPACE_ROOT/platform-docs` outside the active repository without a separate human-approved workspace operation.

---

## 3. Architectural Design & Invariants

1. **Ports & Adapters Boundary (`AGENTS.md`):** `src/engramory/core/docs_tools.py` and `src/engramory/core/portal_builder.py` remain 100% pure-Python stdlib + PyYAML modules with zero vendor SDK imports (`tests/unit/test_no_vendor_imports.py`).
2. **Fail-Closed Path Confinement (`resolve_confined_repo_path`):** Every file or directory touched by `lint_repo`, `run_governed_lint`, and `stage_portal_bundle` (including both `repo_root` and any custom `output_dir`) is confined within `allowed_root` (`ENGRAMORY_ALLOWED_DOCS_ROOT` or workspace CWD, never `repo_root.resolve().parent`) and checked via `_is_within_root()` (`Path.resolve().is_relative_to(...)`) to block `..` traversal and symlink escape (`src/engramory/core/docs_tools.py:210-260`).
3. **Rule D14 Secret & Unicode Scrubbing (`INV-DOC-09`):** Every Markdown file staged into `portal/src/content/docs/reference` by `stage_portal_bundle()` has any synthesized frontmatter JSON-escaped and the complete staged payload scrubbed through `_scrub_manifest_text()` (`src/engramory/core/docs_tools.py:1379-1386`), stripping P14 dangerous Unicode (`has_dangerous_unicode`) and redacting all D14 secret patterns (`detect_hard_secrets` -> `[REDACTED-BY-D14]`) before writing to disk.
4. **Remote MCP Read-Only Safety & Multi-Project Isolation (`ADR-06`, `SPEC-01`):** Over authenticated remote MCP (`self._auth_enabled == True`), `docs_build_portal` confines both `repo_path` and `output_dir` with `enforce_project_boundary=enforce_project` (`project_id=call_ctx.project_id`) and unconditionally forces `dry_run=True` so remote callers can inspect staged portal manifests without mutating the server filesystem, matching `docs_export_llms` (`allow_write=False` at `src/engramory/mcp/server.py:1070`) and `docs_smoke_run` (`dry_run=True` at `src/engramory/mcp/server.py:1090`).
5. **Default-Deny Authorization (`SPEC-01`):** `docs_tools_inventory` and `docs_build_portal` are added to `KNOWN_ACTIONS` in `src/engramory/access/authz.py:15-38` and gated by `await self._surface._decide(call_ctx, name, TargetScope(tenant_id=call_ctx.tenant_id, scope=scope))` before execution, ensuring every allow/deny decision writes an `AuditRecord`.

---

## 4. Phased Implementation Steps

### Phase 1: Built-In Tool Catalog, Governed Lint & Discovery Fixes (`src/engramory/core/docs_tools.py`)

1. **Add `DOCUMENTATION_TOOL_CATALOG` and `list_documentation_tools()` in `src/engramory/core/docs_tools.py`:**
   - Define `DOCUMENTATION_TOOL_CATALOG: tuple[dict[str, str], ...]` using the 6-key schema `{"tool", "category", "tier", "gate", "license", "role"}` with **8 `Adopt`, 2 `Trial`, and 2 `Hold`** entries matching `docs/doc-tools/10_DOCUMENTATION_TOOLS_TECHNICAL_REFERENCE.md`:
     1. `engramory docs` (`Adopt`, `Core Engine & AST Linter`, `Tier-1 (<1s) + Tier-2 + MCP`, `Proprietary-Internal (MIT deps)`)
     2. `vale` (`Adopt`, `Prose & Chunk-Independence Linter`, `Tier-1 (<1s)`, `MIT`)
     3. `@astrojs/starlight` (`Trial`, `Human Supervision Web Portal`, `Portal Build`, `MIT`)
     4. `@scalar/api-reference` (`Trial`, `Interactive API & MCP Console`, `Portal Build`, `MIT`)
     5. `docling + pymupdf + beautifulsoup4` (`Adopt`, `Multi-Format Ingestion (PDF/DOCX/HTML -> MD)`, `L0 Ingestion`, `MIT / AGPL-optional`)
     6. `mermaid` (`Adopt`, `Architecture & Sequence Diagrams (C4 / DFD)`, `Tier-1 + Portal`, `MIT`)
     7. `markdownlint-cli2` (`Adopt`, `Markdown Syntax & Formatting Gate`, `Tier-1 (<1s)`, `MIT`)
     8. `yamllint` (`Adopt`, `YAML Schema & Frontmatter Syntax Gate`, `Tier-1 (<1s)`, `GPL-3.0 (CLI tool)`)
     9. `lychee` (`Adopt`, `Offline & External Link Integrity Checker`, `Tier-1 / Tier-2`, `Apache-2.0 / MIT`)
     10. `aidoc-framework (sdd_doc_lint) + Embedded Kùzu` (`Adopt`, `10-Layer SDD & Runbook Graph Engine`, `Tier-1 / Tier-2`, `MIT`)
     11. `copybara` (`Hold`, `Cross-Repo Code/Doc Sync`, `Prohibited`, `Apache-2.0`)
     12. `mintlify / readme / mkdocs-material-insiders` (`Hold`, `Proprietary SaaS / Open-Core Paywalled Portals`, `Prohibited`, `Proprietary`)
   - Implement `list_documentation_tools(*, tier: str | None = None) -> dict[str, Any]` that validates optional `tier in {"Adopt", "Trial", "Hold"}` (raising `ValidationError` on invalid tier) and returns `{"status": "ok", "count": len(tools), "tools": tools}`.
2. **Extend `lint_markdown_file()`, `lint_repo()`, and add `run_governed_lint()` in `src/engramory/core/docs_tools.py`:**
   - Add `verify_registry: bool = False` to `lint_markdown_file(file_path, *, repo_root=None, require_frontmatter=True, verify_registry=False)` (`src/engramory/core/docs_tools.py:753-804`). When `verify_registry=True` and `parsed.has_frontmatter` is `True`:
     - Validate `endpoints` in `parsed.frontmatter`: if `endpoints` is present and not a `dict`, or `endpoints.host_loopback` does not match `^https?://127\.0\.0\.1:\d+`, or `endpoints.container_gateway` does not match `^https?://10\.250\.0\.1:\d+`, emit `LintDiagnostic(rule_id="DOC-REG-001", severity="error", ...)`.
     - Validate `triggers` in `parsed.frontmatter`: if `triggers` is present and not a `dict`, or any present `triggers.cli_commands` / `triggers.mcp_tools` / `triggers.alerts` value is not a `list` of non-empty strings, emit `LintDiagnostic(rule_id="DOC-REG-001", severity="error", ...)`.
   - Update `lint_repo(target_path, *, strict=False, require_frontmatter=False, verify_registry=False)` (`src/engramory/core/docs_tools.py:1012-1040`) so `req_fm = require_frontmatter or "doc-tools" in f.parts or f.read_text(encoding="utf-8").startswith("---\n")` and pass `verify_registry=verify_registry` to `lint_markdown_file()`.
   - Add `run_governed_lint(target_path: Path, *, strict: bool = False, verify_registry: bool = False) -> dict[str, Any]` calling `lint_repo(target_path, strict=strict, require_frontmatter=True, verify_registry=verify_registry)`.
3. **Fix Subdirectory Markdown Discovery in `smoke_run_green_steps()`, `slice_local_runbooks()`, and `export_repo_llms()`:**
   - In `smoke_run_green_steps()` (`src/engramory/core/docs_tools.py:1112`) and `slice_local_runbooks()` (`src/engramory/core/docs_tools.py:1631`), change the fallback when `(root / "docs").is_dir()` is `False` to `sorted(root.rglob("*.md"))` so subdirectory targets like `docs/doc-tools` discover all `.md` files.
   - In `export_repo_llms()` (`src/engramory/core/docs_tools.py:1400`), check `("README.md", "AGENTS.md", "portal/README.md")` so `portal/README.md` is included in `/llms.txt` and `/llms-full.txt`.

### Phase 2: Built-In Portal Builder Module (`src/engramory/core/portal_builder.py`) & `portal/` Scaffold

1. **Create `src/engramory/core/portal_builder.py`:**
   - Implement `stage_portal_bundle(repo_root: Path, *, output_dir: Path | None = None, dry_run: bool = False, allowed_root: Path | None = None) -> dict[str, Any]`:
     - Confine `confined_repo = resolve_confined_repo_path(repo_root, allowed_root=allowed_root)` (`src/engramory/core/docs_tools.py:218-260`). Never fall back to `repo_root.resolve().parent`.
     - Confine `target_dir`: if `output_dir is None`, set `target_dir = (confined_repo / "portal" / "src" / "content" / "docs" / "reference").resolve()` and verify `_is_within_root(target_dir, confined_repo)`; otherwise resolve `target_dir = resolve_confined_repo_path(output_dir, allowed_root=allowed_root)` (raising `ValidationError`, which subclasses `ValueError`, if `output_dir` escapes `allowed_root` / `ENGRAMORY_ALLOWED_DOCS_ROOT` / CWD).
     - Discover Markdown files under `docs_dir = confined_repo / "docs"` if `docs_dir.is_dir()` else `confined_repo` via `sorted(docs_dir.rglob("*.md"))`. Skip any file where `md_file.is_symlink()` and `not _is_within_root(md_file, confined_repo)` (raising `ValidationError` if a symlink inside `docs_dir` escapes `confined_repo`), and skip any file inside `target_dir` (`_is_within_root(md_file, target_dir)`) to prevent recursive self-staging.
     - For each Markdown file, parse via `parse_markdown_document(md_file, repo_root=confined_repo)`, skip documents with `status in {"deprecated", "archived"}`, synthesize YAML frontmatter (`title: json.dumps(title)`, `description: json.dumps(...)`) if the source file lacked frontmatter, and scrub the entire staged text (`raw_text`) through `_scrub_manifest_text(raw_text)` (`clean_text`), incrementing `scrubbed_count` whenever `clean_text != raw_text`.
     - Preserve relative path under `docs_dir` (`rel_under_docs = md_file.relative_to(docs_dir)`), compute `dest = target_dir / rel_under_docs`, verify `not dest.is_symlink()` and `_is_within_root(dest, target_dir)`, and write `clean_text` when `dry_run=False`.
     - Return the unified dictionary containing both `platform-docs` and `engramory` keys:

       ```python
       {
           "status": "ok",
           "repo": confined_repo.name,
           "output_dir": str(target_dir),
           "dry_run": dry_run,
           "files_staged": len(staged_files),
           "staged_count": len(staged_files),
           "scrubbed_files": scrubbed_count,
           "staged_paths": staged_files,
           "staged_files": staged_files,
           "portal_framework": "@astrojs/starlight",
           "api_playground": "@scalar/api-reference",
       }
       ```

   - Re-export `stage_portal_bundle` from `src/engramory/core/docs_tools.py`.
2. **Create `portal/` Scaffold in Repository Root:**
   - Create `portal/package.json` (`name: "@platform/engramory-docs-portal"`, `private: true`, scripts `dev`, `build`, `preview`, `stage-docs: "cd .. && uv run engramory docs build-portal --repo-path ."`, dependencies `@astrojs/starlight`, `@scalar/api-reference`, `@scalar/astro`, `astro`, `mermaid`).
   - Create `portal/astro.config.mjs` configuring Astro Starlight (`title: "AIOps Platform & Engramory Documentation Portal"`, sidebar groups `Architecture & Specifications`, `Operational Runbooks`, `API & FastMCP Reference`).
   - Create `portal/README.md` with valid governed YAML frontmatter (`id: doc-engramory-portal-readme`, `watched_paths: ["portal/package.json", "portal/astro.config.mjs", "src/engramory/core/portal_builder.py"]`) documenting how `engramory docs build-portal` stages governed Markdown into `portal/src/content/docs/reference/`.
   - Add `portal/dist/`, `portal/.astro/`, and `portal/src/content/docs/reference/` to `.gitignore` so transient staged output does not dirty the worktree.

### Phase 3: CLI Subcommands (`src/engramory/cli.py`) & MCP Tools (`src/engramory/access/authz.py`, `src/engramory/mcp/server.py`)

1. **Update `src/engramory/cli.py` (`docs` parser and handlers at lines 1148–1315):**
   - Add `--json` (`action="store_true"`, `default=argparse.SUPPRESS`) to every `docs` subparser (`lint`, `check-drift`, `export-llms`, `slice-runbook`, `smoke-run`, `ingest`, `tools`, `build-portal`) so passing `--json` either before `docs` (`engramory --json docs ...`) or after the subcommand (`engramory docs ... --json`) sets `getattr(args, "json", False)` to `True` without clobbering.
   - On `engramory docs lint`: keep positional `path` (`nargs="?"`, `default="."`), add `--target` / `--repo-path` (`dest="repo_path"`, `default=None`), `--require-frontmatter` (`action="store_true"`), and `--verify-registry` (`action="store_true"`), resolving `raw_path = args.repo_path or args.path` and passing `require_frontmatter=args.require_frontmatter` and `verify_registry=args.verify_registry` to `lint_repo()`.
   - On `engramory docs check-drift`, `export-llms`, `slice-runbook`, and `smoke-run`: add `--target` as an alias for `--repo-path`, and on `check-drift` add `--base` / `--head` aliases for `--base-ref` / `--head-ref`.
   - Add `engramory docs tools` (and alias subparser `tool-inventory`): accepts `--tier` (`choices=["Adopt", "Trial", "Hold"]`, `default=None`) and `--json` (`default=argparse.SUPPRESS`), calling `list_documentation_tools(tier=args.tier)`.
   - Add `engramory docs build-portal`: accepts positional `path` (`nargs="?"`, `default="."`), `--repo-path` / `--target` (`dest="repo_path"`, `default=None`), `--output-dir` (`default=None`), `--dry-run` (`action="store_true"`), and `--json` (`default=argparse.SUPPRESS`), confining `raw_path = args.repo_path or args.path` and calling `stage_portal_bundle()`.
2. **Update `src/engramory/access/authz.py` & `src/engramory/mcp/server.py`:**
   - Add `"docs_tools_inventory"` and `"docs_build_portal"` to `KNOWN_ACTIONS` in `src/engramory/access/authz.py:15-38`.
   - Add `docs_tools_inventory` and `docs_build_portal` schemas to `DOCS_TOOLS_SCHEMA` in `src/engramory/mcp/server.py:1189-1310` with `"annotations": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True}` and `"scope": {"type": "string", "default": "project"}`, and add optional `require_frontmatter` (`boolean`) and `verify_registry` (`boolean`) properties to `docs_validate`.
   - Handle `docs_tools_inventory` and `docs_build_portal` in `McpServer._handle_docs_tool()` (`src/engramory/mcp/server.py:972-1102`):
     - Call `await self._surface._decide(call_ctx, name, TargetScope(tenant_id=call_ctx.tenant_id, scope=scope))`.
     - In `docs_validate`, pass `require_frontmatter=bool(arguments.get("require_frontmatter", False))` and `verify_registry=bool(arguments.get("verify_registry", False))` to `lint_repo()`.
     - In `docs_build_portal`, confine `repo_path` via `resolve_confined_repo_path(str(arguments.get("repo_path", ".")), project_id=call_ctx.project_id, enforce_project_boundary=enforce_project)`, confine optional `output_dir` via `resolve_confined_repo_path(str(arguments["output_dir"]), project_id=call_ctx.project_id, enforce_project_boundary=enforce_project)` when provided, and force `dry_run = True if self._auth_enabled else bool(arguments.get("dry_run", True))`.

### Phase 4: Specification Suite Consolidation (`docs/doc-tools/`) & `.vale` Rule Sync

1. **Add `docs/doc-tools/10_DOCUMENTATION_TOOLS_TECHNICAL_REFERENCE.md`:**
   - Port `10_DOCUMENTATION_TOOLS_TECHNICAL_REFERENCE.md` into `docs/doc-tools/10_DOCUMENTATION_TOOLS_TECHNICAL_REFERENCE.md` with zero hardcoded `/home/.../devd` paths.
   - Set `watched_paths` to `["docs/doc-tools/10_DOCUMENTATION_TOOLS_TECHNICAL_REFERENCE.md", "src/engramory/core/docs_tools.py", "src/engramory/core/portal_builder.py", ".vale.ini", "portal/package.json"]`.
   - Update `triggers.cli_commands`, `triggers.mcp_tools`, and the two `tier="green" mode="read-only"` executable blocks to invoke `uv run engramory docs tools` and `uv run engramory docs lint --target docs/doc-tools --require-frontmatter --verify-registry` so both `engramory docs lint docs/doc-tools --require-frontmatter --verify-registry --strict` and live `engramory docs smoke-run` pass with 0 errors and 0 warnings.
2. **Update `docs/doc-tools/00_INDEX.md` and `docs/doc-tools/09_ENGRAMORY_CONSOLIDATION_AND_EXTENSION_BLUEPRINT.md`:**
   - Add Document `10` (`10_DOCUMENTATION_TOOLS_TECHNICAL_REFERENCE.md`) to the catalog table and Mermaid diagram in `docs/doc-tools/00_INDEX.md:50-90`.
   - Update `docs/doc-tools/09_ENGRAMORY_CONSOLIDATION_AND_EXTENSION_BLUEPRINT.md` to record that `platform-docs` is retired and its tool catalog, portal builder, governed lint flags, technical reference (Doc 10), and Starlight + Scalar portal scaffold are now built-in modules of `platform-engramory` (using `$WORKSPACE_ROOT/...` and repo-relative paths only).
3. **Sync `.vale/styles/AIOpsAgent/` Rules:**
   - Update `.vale/styles/AIOpsAgent/DeterministicPortFormat.yml` (`nonword: true`, token `https?://localhost:\d+`) and update the verb alternation in `.vale/styles/AIOpsAgent/NoAmbiguousSubjectStart.yml` (`\n+(?:It|This|They|These|Those)\s+(?:is|are|was|were|will|should|must|can|triggers|runs|fails)\b` with `nonword: true`) so both Vale and pure-Python (`src/engramory/core/docs_tools.py:163-166, 874-893`) enforce identical section-opening subject rules without false-positive mid-paragraph alerts.
4. **Regenerate `/llms.txt` and `/llms-full.txt`:**
   - Run `uv run engramory docs export-llms . --write --full` so the root manifests include `docs/doc-tools/10_DOCUMENTATION_TOOLS_TECHNICAL_REFERENCE.md` and `portal/README.md`.

### Phase 5: Comprehensive Tests (`tests/test_docs_tools.py`) & Full Gate Verification

1. **Extend `tests/test_docs_tools.py`:**
   - Add tests verifying:
     - `DOCUMENTATION_TOOL_CATALOG` (12 tools: 8 `Adopt`, 2 `Trial`, 2 `Hold`, 6-key schema), `list_documentation_tools(tier="Adopt")`, `list_documentation_tools(tier="Trial")`, `list_documentation_tools(tier="Hold")`, invalid tier `ValidationError`, CLI `engramory --json docs tools --tier Adopt` and `engramory docs tool-inventory --tier Hold --json`, and MCP `docs_tools_inventory`.
     - `run_governed_lint()` and `lint_repo(..., require_frontmatter=True, verify_registry=True)` catching `DOC-FM-000` on un-frontmattered files and `DOC-REG-001` on invalid `endpoints` / `triggers`, while passing with 0 errors and 0 warnings on `docs/doc-tools/`.
     - `smoke_run_green_steps()` and `slice_local_runbooks()` on `REPO_ROOT / "docs" / "doc-tools"` (verifying `passed >= 6` and `ALT-DOC-004` runbook slicing on subdirectory targets).
     - `stage_portal_bundle()` dry-run and write modes, unified return dictionary keys (`files_staged`, `staged_count`, `scrubbed_files`, `staged_paths`, `staged_files`, `portal_framework`, `api_playground`), subdirectory path preservation, synthesized YAML frontmatter escaping on colons, Rule D14 secret redaction (`sk-proj-...` -> `[REDACTED-BY-D14]`), symlink escape rejection, `output_dir` confinement rejection (`ValidationError`/`ValueError`), CLI `engramory docs build-portal --dry-run --json`, and MCP `docs_build_portal` (verifying `dry_run=True` is enforced when `auth_enabled=True`).
     - `portal/package.json` (`@astrojs/starlight`, `@scalar/api-reference`, `@scalar/astro`, `astro`, `mermaid`) and `portal/astro.config.mjs` existence and content.
2. **Run Full Verification Suite:**
   - Run `uv run pytest -q tests/test_docs_tools.py tests/unit/test_no_vendor_imports.py`.
   - Run `uv run engramory docs lint docs/doc-tools --require-frontmatter --verify-registry --strict`.
   - Run `uv run engramory docs check-drift --target .`.
   - Run `uv run engramory docs smoke-run --target docs/doc-tools --dry-run`.
   - Run `task doctor`, `task check`, and `task ci` (noting the expected non-blocking `.ci/bin/policy` protected-file warning on `portal/package.json` in the completion report per `AGENTS.md`).

---

## 5. Security & Threat Model (STRIDE / Rule D14 / ADR-06)

| Threat / Risk | Mitigation in Design | Verification |
| :--- | :--- | :--- |
| **Path Traversal / Arbitrary File Write via `build-portal --output-dir`** | Both `repo_root` and `output_dir` are resolved through `resolve_confined_repo_path()` (never `repo_root.resolve().parent`) and verified via `_is_within_root()` against `allowed_root` (`ENGRAMORY_ALLOWED_DOCS_ROOT` or CWD). Symlinks pointing outside `confined_repo` or `target_dir` are rejected with `ValidationError`. | Unit tests in `tests/test_docs_tools.py` for `output_dir="../escape"` and symlinked Markdown files. |
| **Remote Unauthenticated or Mutating File Writes / Cross-Project Escape via MCP `docs_build_portal`** | `docs_build_portal` requires `authorize(call_ctx, "docs_build_portal", TargetScope(...))` via `AccessSurface._decide()` (`KNOWN_ACTIONS`), confines `repo_path` and `output_dir` with `enforce_project_boundary=enforce_project`, and forces `dry_run=True` whenever `self._auth_enabled` is `True`. | Unit test on `McpServer(surface, auth_enabled=True)` calling `docs_build_portal` with `dry_run=False` and asserting no files are written (`dry_run: True`). |
| **Secret Leakage or Invisible Prompt Injection in Staged Portal Bundles** | `stage_portal_bundle()` JSON-escapes synthesized frontmatter scalars and scrubs the entire staged document through `_scrub_manifest_text()` (`has_dangerous_unicode` + `detect_hard_secrets` -> `[REDACTED-BY-D14]`). | Unit test staging a Markdown file containing an OpenAI key (`sk-proj-...`) and verifying `[REDACTED-BY-D14]` in the staged output and `scrubbed_files == 1`. |
| **Supply-Chain / Unapproved Tooling Drift** | `DOCUMENTATION_TOOL_CATALOG` hard-codes `copybara`, `mintlify`, `readme`, and `mkdocs-material-insiders` as `tier="Hold"` and `BANNED_RETIRED_TOOLS` blocks `seaweedfs`, `bytebase`, and `pre-commit` (`DOC-STACK-001`). | Unit test verifying `list_documentation_tools(tier="Hold")` and `DOC-STACK-001`. |

---

## 6. Rollback & Recovery Strategy

All changes are additive Python modules, documentation files, and static portal configuration on a dedicated feature branch (`feature/plan-019-platform-docs-consolidation`) with zero database schema migrations (`db/migrations/` untouched). If any regression is detected, reverting the feature branch commit restores the prior state cleanly with zero data migration or state rollback required.

---

## Claim ledger

| # | Claim | Symbol | Citation |
|---|---|---|---|
| 1 | `docs_tools.py` defines `REQUIRED_FRONTMATTER_KEYS`, `BANNED_DEICTIC_PHRASES`, and `BANNED_RETIRED_TOOLS` for Tier-1 offline documentation linting | `REQUIRED_FRONTMATTER_KEYS` | `src/engramory/core/docs_tools.py:52-78` |
| 2 | `docs_tools.py` defines `_AMBIGUOUS_SUBJECT_RE` and `_BARE_LOCALHOST_RE` for prose subject and port format checks | `_AMBIGUOUS_SUBJECT_RE` | `src/engramory/core/docs_tools.py:309-315` |
| 3 | `_is_within_root` and `resolve_confined_repo_path` enforce path confinement and block symlink escape outside `allowed_root` | `resolve_confined_repo_path` | `src/engramory/core/docs_tools.py:364-410` |
| 4 | `lint_markdown_file` validates frontmatter (`DOC-FM-000`), `watched_paths` globs (`DEAD_WATCHED_PATH_GLOB`), hard secrets (`DOC-SEC-001`), prose chunk rules, and relative links | `lint_markdown_file` | `src/engramory/core/docs_tools.py:899-965` |
| 5 | `lint_repo` discovers Markdown files under `docs/` (or target directory) and runs `lint_markdown_file` plus optional Vale integration | `lint_repo` | `src/engramory/core/docs_tools.py:1256-1335` |
| 6 | `smoke_run_green_steps` and `slice_local_runbooks` discover Markdown files and execute or slice `tier='green'` runbook steps | `smoke_run_green_steps` | `src/engramory/core/docs_tools.py:1370-1395` |
| 7 | `_scrub_manifest_text` strips dangerous Unicode control/bidi characters and redacts D14 secret patterns with `[REDACTED-BY-D14]` | `_scrub_manifest_text` | `src/engramory/core/docs_tools.py:1651-1660` |
| 8 | `export_repo_llms` deterministically generates `/llms.txt` and `/llms-full.txt` from repository Markdown and SDD YAML files | `export_repo_llms` | `src/engramory/core/docs_tools.py:1661-1805` |
| 9 | `cli.py` registers the `engramory docs` subcommands (`lint`, `check-drift`, `export-llms`, `slice-runbook`, `smoke-run`, `ingest`) | `_cmd_docs` | `src/engramory/cli.py:1177-1340` |
| 10 | `authz.py` defines the closed `KNOWN_ACTIONS` frozenset for default-deny per-call authorization | `KNOWN_ACTIONS` | `src/engramory/access/authz.py:15-40` |
| 11 | `mcp/server.py` defines `DOCS_TOOLS_SCHEMA` for the documentation and executable runbook MCP tools | `DOCS_TOOLS_SCHEMA` | `src/engramory/mcp/server.py:1235-1360` |
| 12 | `McpServer._handle_docs_tool` dispatches `docs_slice_runbook`, `docs_validate`, `docs_check_drift`, `docs_export_llms`, and `docs_smoke_run` with fail-closed remote write guards | `_handle_docs_tool` | `src/engramory/mcp/server.py:972-1150` |
| 13 | `docs/doc-tools/00_INDEX.md` defines the Platform Docs Specification Catalog (`01`–`09`) and Ten Non-Negotiable Architectural Invariants (`INV-DOC-01`..`10`) | `doc-platform-docs-00-index` | `docs/doc-tools/00_INDEX.md:1-103` |
| 14 | `docs/doc-tools/09_ENGRAMORY_CONSOLIDATION_AND_EXTENSION_BLUEPRINT.md` specifies consolidating `platform-docs` into `engramory` (`KnowledgeCore` L0) and optionally staging the Starlight + Scalar portal | `doc-platform-docs-09-engramory-consolidation` | `docs/doc-tools/09_ENGRAMORY_CONSOLIDATION_AND_EXTENSION_BLUEPRINT.md:2-104` |
| 15 | `tests/test_docs_tools.py` tests `.vale.ini` and the 4 `AIOpsAgent` rule files, AST parsing, `INV-STEP-01`/`02`, drift detection, and `llms.txt` export | `test_vale_scaffolding_exists` | `tests/test_docs_tools.py:96-107` |

---

## Review log

### Pass 1 - 2026-11-09 (Author Self-Review)

- **Scope & Citations Check:** Verified all claims in `## Claim ledger` against live files and line ranges in `platform-engramory`.
- **Architecture & Security Check:** Confirmed `stage_portal_bundle` reuses `resolve_confined_repo_path`, `_is_within_root`, and `_scrub_manifest_text` from `src/engramory/core/docs_tools.py`, and that `docs_tools_inventory` and `docs_build_portal` are registered in `KNOWN_ACTIONS` (`src/engramory/access/authz.py`) with `dry_run=True` forced when `self._auth_enabled` is `True`.
- **Result:** Proceeding to deterministic `check_plan.py` gate and multi-lens fresh-context subagent review.

### Pass 2 - 2026-11-09 (Independent Multi-Lens Subagent Review 1 — `f91ee79e-8f34-4422-bf27-074c4b090226`)

- **Lenses Applied:** Google (Design Doc invariants, SRE reliability, CI gate interactions), Anthropic (MCP contracts, Rule D14 scrubbing), AWS (least privilege, `KNOWN_ACTIONS`, remote MCP safety), Microsoft (STRIDE path confinement, CLI backward compatibility).
- **Findings Folded (4 Critical, 6 Major, 3 Medium):**
  1. `CRITICAL-01`: Aligned `DOCUMENTATION_TOOL_CATALOG` to the 6-key schema (`tool`, `category`, `tier`, `gate`, `license`, `role`) and exact `8 Adopt / 2 Trial (@astrojs/starlight, @scalar/api-reference) / 2 Hold` distribution matching Doc 10.
  2. `CRITICAL-02`: Removed `repo_root.resolve().parent` fallback in `stage_portal_bundle`, confined both `repo_root` and `output_dir` via `resolve_confined_repo_path(..., allowed_root=allowed_root)`, enforced `enforce_project_boundary=enforce_project` on both `repo_path` and `output_dir` in MCP `docs_build_portal`, and added destination symlink checks.
  3. `CRITICAL-03`: Fixed `smoke_run_green_steps()` (`docs_tools.py:1112`) and `slice_local_runbooks()` (`docs_tools.py:1631`) fallback to `sorted(root.rglob("*.md"))` so subdirectory targets like `docs/doc-tools` do not vacuously scan 0 files.
  4. `CRITICAL-04`: Unified `stage_portal_bundle()` return dictionary to include both `files_staged`/`scrubbed_files`/`staged_paths`/`portal_framework`/`api_playground` and `staged_count`/`staged_files`.
  5. `MAJOR-01`..`MAJOR-06`: Added `default=argparse.SUPPRESS` on `--json` subparsers, separated positional `path` from `--repo-path`/`--target`, added `--base`/`--head` aliases on `check-drift`, prohibited `/home/.../devd` paths (`PATHS_DENY`), updated Doc 10 `watched_paths` and `tier="green"` commands to `engramory docs ...`, preserved `nonword: true` + `\n+...` in `NoAmbiguousSubjectStart.yml`, JSON-escaped synthesized frontmatter before `_scrub_manifest_text()`, and added `annotations` (`readOnlyHint: True`, etc.) to `DOCS_TOOLS_SCHEMA` (`src/engramory/mcp/server.py:1189-1310`).
  6. `MEDIUM-01`..`MEDIUM-03`: Added `portal/README.md` to `export_repo_llms()`, included `@scalar/astro` in `portal/package.json` and noted the expected `.ci/bin/policy` protected-file warning, and extended `verify_registry=True` to validate `triggers` structure.
- **Result:** Proceeding to Review 2 (Pass 3) fresh-context subagent verification.

### Pass 3 - 2026-11-09 (Independent Multi-Lens Subagent Review 2 — `56083f9a-9eb1-43cd-95f0-0fd17edcbf80`)

- **Lenses Applied:** Google, Anthropic, AWS, and Microsoft lenses verifying all 13 folded findings from Review 1 against the live codebase and checking for any residual gaps.
- **Findings:** No new findings (0 Critical, 0 Major, 0 Medium).
- **Result:** ready
