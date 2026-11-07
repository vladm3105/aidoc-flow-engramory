# PLAN-015 — Document Processing Pipeline Hardening (Pre-Stage Readiness)

**Status:** Verified & Ready for Execution
**Date:** 2026-11-07
**Author:** AI Coding Agent
**Related Plans:** `PLAN-010`, `PLAN-013`, `PLAN-014`

---

## 1. Objective & Scope

Before running multi-repository document ingestion on `stage`, this plan resolves **6 defects and consistency gaps** identified in the 6-stage document processing pipeline across `MarkdownParser`, `SemanticChunker`, `GitSyncEngine`, `KnowledgeCore`, `chunking.py`, `Repository`, `AccessSurface`, and the `engramory` CLI:

1. **Gap 1 (Stage 3 — Code-Fence Bug & Contextual Header in `MarkdownParser` + `SemanticChunker`):**
   `MarkdownParser.HEADER_PATTERN` (`r"^(#{1,6})\s+(.+?)(?:\s+#*)?$"`) matches `# comments` inside fenced code blocks (` ```bash ... ``` `) as H1 headings when documents are routed through `SmartExtractionRouter` (`GitSyncEngine` and `KnowledgeCore`). Additionally, `MarkdownParser` does not prepend the Anthropic Contextual Retrieval header (`[Doc: ... | Title: ... | Section: ...]`) for frontmatter-governed documents, nor does `SemanticChunker.refine_sections` avoid merging already-prefixed governed sections.
2. **Gap 2 (Stage 4 — Duplicate & Orphan `kb_sections` on `MODIFIED` / Re-Synced Files in `GitSyncEngine`):**
   `GitSyncEngine.extract_sync_sections` embeds line numbers and commit SHAs into `KBSection.citation`, and `GitSyncEngine.sync` only deletes prior `kb_sections` on `RENAMED` or `DELETED` files, not on `MODIFIED` or re-synced `ADDED` files. Consequently, every Git modification or full re-sync inserts duplicate section rows while leaving stale sections from prior commits orphaned in `kb_sections`.
3. **Gap 3 (Stages 2 & 4 — Silent Swallowing of `MemoryPoisoningError`/`ValidationError` & Unchecked `kb_sections`):**
   Both `GitSyncEngine._sync_changed_doc_metadata` and `KnowledgeCore.ingest_documents_batch` catch broad `Exception` and log at `DEBUG` level, silently swallowing `MemoryPoisoningError` (OWASP ASI03) and cross-project `ValidationError`. Furthermore, neither `GitSyncEngine.sync` nor `KnowledgeCore.ingest_documents_batch` calls `inspect_memory_write` on `KBSection.text` before `repo.upsert_kb_sections_batch(unique_sections)`.
4. **Gap 4 (Stages 1 & 4 — `engramory docs ingest` Split-Brain, `--project-id` Override Loss, `domain_id` Drop, & Subdirectory Basename Collision):**
   `_cmd_docs_ingest` writes `kb_documents` and `kb_runbook_steps` via `surface.docs_ingest_runbook`, which overwrites `project_id=ctx.project_id` (ignoring `--project-id`) and does not populate `kb_sections`. `AccessSurface.knowledge_ingest_batch` also omits `domain_id=s.domain_id` when constructing `KBSection`. Meanwhile, `KnowledgeCore.ingest_documents_batch` computes `doc_id = build_doc_id(project_id, p.name)` using only the basename `p.name`, causing files with identical names in different subdirectories (`docs/a/README.md` vs `docs/b/README.md`) to collide when ingesting a directory.
5. **Gap 5 (Stage 5 — Embedding Model Metadata Default & Dimension Configuration):**
   `Repository.set_kb_section_embedding` and `Repository.set_kb_sections_embedding_batch` default `embedding_model` to `"bge-small-en-v1.5"` despite migration `0006` pinning 1536-dim vectors, and `GitSyncEngine.sync` hardcodes `len(vec) == 1536` without a configurable `expected_embedding_dims` parameter.
6. **Gap 6 (Stage 5 — Unchecked Embedding Persistence & Swallowed Exceptions in `chunking.ingest_file`):**
   `chunking.ingest_file` calls `await repo.set_kb_section_embedding(...)` without forwarding `embedding_model` or using `set_kb_sections_embedding_batch`, and swallows all exceptions in `except Exception: pass`.

---

## Claim ledger

| # | Claim | Symbol | Citation |
|---|---|---|---|
| 1 | `MarkdownParser.HEADER_PATTERN` is a multiline regex `r"^(#{1,6})\s+(.+?)(?:\s+#*)?$"` applied across `body` without code-fence state tracking. | `HEADER_PATTERN` | src/engramory/core/parsers/markdown.py:26-30 |
| 2 | `MarkdownParser._find_headers` iterates `self.HEADER_PATTERN.finditer(body)` directly without skipping fenced code blocks (` ``` ` or ` ~~~ `). | `_find_headers` | src/engramory/core/parsers/markdown.py:113-128 |
| 3 | `MarkdownParser._subchunk_section` splits section content by word count without prepending Contextual Retrieval metadata headers when frontmatter `id` is present, and `SemanticChunker.refine_sections` merges sections `< 30` words without checking for `[Doc:` prefixes. | `_subchunk_section` | src/engramory/core/parsers/markdown.py:207 |
| 4 | `GitSyncEngine.extract_sync_sections` embeds commit SHA and `#L<start>-L<end>` line numbers into `full_citation` on every extracted `KBSection`. | `extract_sync_sections` | src/engramory/core/git_sync.py:211-265 |
| 5 | `GitSyncEngine.sync` calls `repo.delete_kb_sections_for_doc` only for `RENAMED` (on `old_doc_id`) and `DELETED`, leaving stale sections in place on `MODIFIED` and full re-sync `ADDED`. | `delete_kb_sections_for_doc` | src/engramory/core/git_sync.py:354 |
| 6 | `GitSyncEngine._sync_changed_doc_metadata` catches `Exception` and logs at `logger.debug`, swallowing `MemoryPoisoningError` and cross-project `ValidationError`. | `_sync_changed_doc_metadata` | src/engramory/core/git_sync.py:350 |
| 7 | `KnowledgeCore.ingest_documents_batch` catches `Exception` around `inspect_memory_write` and `upsert_kb_document`, logging at `logger.debug`. | `inspect_memory_write` | src/engramory/core/knowledge.py:145 |
| 8 | `GitSyncEngine.sync` upserts `unique_sections` via `repo.upsert_kb_sections_batch` without calling `inspect_memory_write` on `KBSection.text`. | `upsert_kb_sections_batch` | src/engramory/core/git_sync.py:362 |
| 9 | `KnowledgeCore.ingest_documents_batch` upserts `unique_sections` via `self.repo.upsert_kb_sections_batch` without calling `inspect_memory_write` on `KBSection.text`. | `upsert_kb_sections_batch` | src/engramory/core/knowledge.py:221 |
| 10 | `_cmd_docs_ingest` in `src/engramory/cli.py` only calls `surface.docs_ingest_runbook` and does not ingest `kb_sections`. | `_cmd_docs_ingest` | src/engramory/cli.py:1054-1092 |
| 11 | `AccessSurface.docs_ingest_runbook` sets `project_id=ctx.project_id` on `locked_doc`, and `AccessSurface.knowledge_ingest_batch` omits `domain_id=s.domain_id` when constructing `KBSection`. | `docs_ingest_runbook` | src/engramory/access/surface.py:645-686 |
| 12 | `KnowledgeCore.ingest_documents_batch` builds `doc_id = build_doc_id(project_id, p.name)` using only the file basename `p.name`. | `build_doc_id` | src/engramory/core/knowledge.py:118 |
| 13 | `_cmd_knowledge_ingest` in `src/engramory/cli.py` collects recursive files under `target_path` and passes `files_to_ingest` to `core.ingest_documents_batch` without `repo_root`. | `_cmd_knowledge_ingest` | src/engramory/cli.py:263-320 |
| 14 | `Repository.set_kb_sections_embedding_batch` defaults `embedding_model: str = "bge-small-en-v1.5"`. | `set_kb_sections_embedding_batch` | src/engramory/core/repository.py:559-565 |
| 15 | `GitSyncEngine.sync` hardcodes `if s_id and len(vec) == 1536:` and does not pass `embedding_model` to `set_kb_sections_embedding_batch`. | `set_kb_sections_embedding_batch` | src/engramory/core/git_sync.py:375-350 |
| 16 | `chunking.ingest_file` persists section embeddings via `repo.set_kb_sections_embedding_batch` with `embedding_model` forwarding and warning logging on failure. | `set_kb_sections_embedding_batch` | src/engramory/core/chunking.py:220 |
| 17 | `Repository.set_kb_section_embedding` also defaults `embedding_model: str = "bge-small-en-v1.5"`. | `set_kb_section_embedding` | src/engramory/core/repository.py:452-460 |

---

## Phases

### Phase 1: Fence-Aware `MarkdownParser`, Contextual Retrieval Header, & `SemanticChunker` Guard (Gap 1)

**Files:**

- `src/engramory/core/parsers/markdown.py`
- `src/engramory/core/extraction/enrichment.py`

**Steps:**

1. Update `MarkdownParser._find_headers(self, body: str, base_offset: int)` ([src/engramory/core/parsers/markdown.py:113-128](src/engramory/core/parsers/markdown.py#L113-L128)) to scan `body` line-by-line (preserving exact character offsets via `body.splitlines(keepends=True)`) while tracking fenced code blocks using `_FENCE_OPEN_RE = re.compile(r"^(`{3,}|~{3,})([^\n`~]*)$")` for opening fences (so single-line backtick code spans like ```` ```inline``` ```` do not open a fence) and `stripped.startswith(fence_marker) and stripped == fence_marker[0] * len(stripped)` for closing fences. Ignore any `#` line inside an open code fence.
2. In `MarkdownParser.parse` ([src/engramory/core/parsers/markdown.py:63-86](src/engramory/core/parsers/markdown.py#L63-L86)), route the non-empty `if not raw_headers:` fallback through `self._subchunk_section(sec, start_order=1)` so headerless frontmatter documents also receive word chunking and Contextual Retrieval headers.
3. In `MarkdownParser._subchunk_section` ([src/engramory/core/parsers/markdown.py:182-216](src/engramory/core/parsers/markdown.py#L182-L216)), when `section.metadata` contains a governed frontmatter `"id"` key, construct `ctx_header = f"[Doc: {doc_id} | Title: {title} | Kind: {kind} | Domain: {domain} | Tier: {tier} | Section: {section.name}]\n"`, reserve `len(ctx_header.split())` from `self.max_words_per_chunk` (`eff_max = max(1, self.max_words_per_chunk - len(ctx_header.split()))`) so every prefixed chunk stays `<= self.max_words_per_chunk`, and prepend `ctx_header` in both the single-chunk and multi-chunk branches if not already prefixed. Plain Markdown files without frontmatter `id` remain unchanged so existing `tests/test_parsers.py` assertions stay green.
4. In `SemanticChunker.refine_sections` ([src/engramory/core/extraction/enrichment.py:58-72](src/engramory/core/extraction/enrichment.py#L58-L72)), do not merge undersized sections in Pass 2 when `s.content.startswith("[Doc: ")` or `merged_sections[-1].content.startswith("[Doc: ")`, preventing mid-chunk duplicate Contextual Retrieval headers.

### Phase 2: Fail-Closed Validation, Atomic Stale Section Cleanup, & Embedding Alignment in `GitSyncEngine`, `KnowledgeCore`, `Repository`, & `chunking.py` (Gaps 2, 3, 5, 6)

**Files:**

- `src/engramory/core/git_sync.py`
- `src/engramory/core/knowledge.py`
- `src/engramory/core/repository.py`
- `src/engramory/core/chunking.py`

**Steps:**

1. **Validate before deleting or writing in `GitSyncEngine.sync`:**
   - In `GitSyncEngine.sync` ([src/engramory/core/git_sync.py:291-337](src/engramory/core/git_sync.py#L291-L337)), first extract all candidate sections across `ADDED`, `MODIFIED`, and `RENAMED` files, run `inspect_memory_write(s.text, tenant_id=tenant_id, writer_agent_id=f"git_sync:{project_id}", target=s.doc_id)` on every `KBSection` in `unique_sections`, and run `await self._sync_changed_doc_metadata(...)` (which re-raises `MemoryPoisoningError` and `ValidationError` instead of swallowing them in `except Exception`).
   - Only after all poisoning and ownership checks pass, execute `delete_kb_sections_for_doc` for `DELETED`, `RENAMED` (`old_doc_id` and new `doc_id`), `MODIFIED`, and `ADDED` (so full re-syncs with `base_sha=None` are also idempotent), followed by `await repo.upsert_kb_sections_batch(unique_sections)`.
2. **Fail-closed section inspection before DB writes in `KnowledgeCore.ingest_documents_batch` + subdirectory-aware `doc_id`:**
   - Add `repo_root: Path | None = None` to both `KnowledgeCore.ingest_file` ([src/engramory/core/knowledge.py:46-71](src/engramory/core/knowledge.py#L46-L71)) and `KnowledgeCore.ingest_documents_batch` ([src/engramory/core/knowledge.py:73-102](src/engramory/core/knowledge.py#L73-L102)). When `repo_root` is provided and `p.resolve().is_relative_to(repo_root.resolve())`, compute `rel_key = p.resolve().relative_to(repo_root.resolve()).as_posix()` for `build_doc_id(project_id, rel_key)` and use `repo_root.resolve()` as the root passed to `parse_markdown_document`.
   - In `KnowledgeCore.ingest_documents_batch` ([src/engramory/core/knowledge.py:124-186](src/engramory/core/knowledge.py#L124-L186)), run `inspect_memory_write` on every extracted `KBSection.text` in `file_secs` *before* calling `upsert_kb_document` and `upsert_kb_runbook_steps`, and re-raise `(MemoryPoisoningError, ValidationError)` instead of swallowing them at `logger.debug`.
3. **Embedding model & dimension alignment + `chunking.ingest_file` fix:**
   - Update default `embedding_model` to `"text-embedding-3-small"` on both `Repository.set_kb_section_embedding` ([src/engramory/core/repository.py:452-460](src/engramory/core/repository.py#L452-L460)) and `Repository.set_kb_sections_embedding_batch` ([src/engramory/core/repository.py:559-565](src/engramory/core/repository.py#L559-L565)).
   - Add `expected_embedding_dims: int = 1536` to `GitSyncEngine.sync` ([src/engramory/core/git_sync.py:269-280](src/engramory/core/git_sync.py#L269-L280)) and pass `embedding_model=getattr(llm, "_embed_model", "text-embedding-3-small")` from both `GitSyncEngine.sync` and `KnowledgeCore.ingest_documents_batch`.
   - In `chunking.ingest_file` ([src/engramory/core/chunking.py:215-222](src/engramory/core/chunking.py#L215-L222)), replace `await repo.set_kb_section_embedding(sec_id, embedding=embeddings[0])` with `await repo.set_kb_sections_embedding_batch([(sec_id, embeddings[0])], embedding_model=getattr(adapters.llm, "_embed_model", "text-embedding-3-small"))` and log a warning on failure instead of `except Exception: pass`.

### Phase 3: Unified CLI `engramory docs ingest`, `knowledge ingest`, & `AccessSurface` Domain Preservation (Gap 4)

**Files:**

- `src/engramory/access/surface.py`
- `src/engramory/cli.py`

**Steps:**

1. In `AccessSurface.knowledge_ingest_batch` ([src/engramory/access/surface.py:521-531](src/engramory/access/surface.py#L521-L531)), pass `domain_id=s.domain_id` when constructing `KBSection` so domain-scoped section search works for governed documents ingested through `AccessSurface`.
2. In `_cmd_knowledge_ingest` ([src/engramory/cli.py:295-320](src/engramory/cli.py#L295-L320)), pass `repo_root=target_path if target_path.is_dir() else None` to `core.ingest_documents_batch(...)`.
3. In `_cmd_docs_ingest` ([src/engramory/cli.py:1054-1092](src/engramory/cli.py#L1054-L1092)):
   - Filter discovered `md_files` with `_is_within_root(f, bound_root)`.
   - Honor `--project-id` by constructing `eff_actor = dataclasses.replace(actor, project_id=args.project_id)` when `args.project_id` is specified.
   - Ingest `kb_sections` for each governed document via `chunk_markdown` + `surface.knowledge_ingest_batch` so `engramory docs ingest` populates both `kb_documents`/`kb_runbook_steps` and searchable `kb_sections`, and return `sections_ingested` in the summary payload.

### Phase 4: Regression & Integration Tests

**Files:**

- `tests/test_docs_tools.py`

**Steps:**

1. Add `test_doc_processing_pipeline_hardening_plan015` in `tests/test_docs_tools.py` verifying:
   - `MarkdownParser.parse` ignores `# bash comments` inside ```` ```bash ```` and ```` ~~~bash ```` fences (while not getting stuck on single-line ```` ```inline``` ```` code spans), prepends the Contextual Retrieval header when frontmatter `id` is present (including headerless governed docs), and keeps chunks `<= max_words_per_chunk` without duplicate header merging in `SemanticChunker`.
   - `GitSyncEngine.sync` deletes prior `kb_sections` when a file is `MODIFIED` or re-synced, and blocks `MemoryPoisoningError` / cross-project `ValidationError` *before* deleting existing valid sections in PostgreSQL.
   - `KnowledgeCore.ingest_documents_batch` disambiguates `docs/a/README.md` vs `docs/b/README.md` when `repo_root` is passed and rejects poisoned `kb_sections` before writing `kb_documents`.
   - `chunking.ingest_file` calls `repo.set_kb_sections_embedding_batch` with `embedding_model`.
   - `engramory --json docs ingest` honors `--project-id` and ingests both `kb_documents`/`kb_runbook_steps` and `kb_sections` (with `domain_id` preserved).

---

## 4. Verification & Acceptance Criteria

- `python3 /home/ya/.gemini/config/plugins/aiops-harness/skills/verified-planning/check_plan.py plans/PLAN-015_doc-processing-pipeline-hardening.md` passes.
- `uv run pytest tests/test_docs_tools.py tests/test_parsers.py tests/test_git_sync.py tests/test_knowledge_core.py` passes.
- `task check` and `task app:test` pass with coverage $\ge 80\%$.

---

## Review log

### Pass 1 - 2026-11-07

- **Reviewer:** Authoring Agent (`self`)
- **Findings:**
  1. Verified that `tests/test_parsers.py` tests `MarkdownParser` on documents without frontmatter `id` (e.g., `title: Sample Architecture Guide`) and asserts exact `section.content` equality on plain Markdown (`test_markdown_parser_no_headers`). Scoping Contextual Retrieval header injection in `MarkdownParser._subchunk_section` to documents carrying frontmatter `id` preserves backward compatibility with `tests/test_parsers.py`.
  2. Verified that `tests/test_knowledge_core.py` calls `core.ingest_documents_batch([f1, f2], ...)` without `repo_root` and asserts `f"{project_id}:guide.md" in doc_ids`. Making `repo_root: Path | None = None` opt-in preserves `p.name` for flat file lists while fixing subdirectory collisions when `repo_root` is provided.
- **Result:** All 17 citations verified against live workspace files; awaiting Pass 2 independent fresh-context review.

### Pass 2 - 2026-11-07 - independent

- **Reviewer:** Independent Fresh-Context Subagent (`5b105216-7fd0-4113-8b53-4a4bf3e560a5`, Google / Anthropic / Microsoft lenses)
- **Findings & Resolutions Folded:**
  1. **[Major — Claim 17 & Gap 6 accuracy (`Repository.set_kb_section_embedding`)]:** `Repository.set_kb_section_embedding` exists at `src/engramory/core/repository.py:452-476` with stale default `embedding_model="bge-small-en-v1.5"`. Updated Claim 17, Gap 5, Gap 6, and Phase 2 Step 3 to update both `set_kb_section_embedding` and `set_kb_sections_embedding_batch` defaults to `"text-embedding-3-small"` and switch `chunking.ingest_file` to `set_kb_sections_embedding_batch` with warning logging instead of `except Exception: pass`.
  2. **[Major — Fail-closed ordering in `GitSyncEngine.sync` & `KnowledgeCore.ingest_documents_batch`]:** Reordered Phase 2 Steps 1–2 so `inspect_memory_write` on `KBSection.text` and `_sync_changed_doc_metadata` run *before* `delete_kb_sections_for_doc` and `upsert_kb_document`, preventing destructive deletion of existing valid sections or partial document writes when a poisoning payload is rejected. Added `FileChangeStatus.ADDED` to pre-upsert `delete_kb_sections_for_doc` so full re-syncs (`base_sha=None`) are idempotent.
  3. **[Major — Contextual Retrieval header interaction with `SemanticChunker` & headerless docs]:** Updated Phase 1 Steps 2–4 to reserve `len(ctx_header.split())` from `max_words_per_chunk` in `MarkdownParser._subchunk_section`, route headerless frontmatter docs (`if not raw_headers:`) through `_subchunk_section`, and guard `SemanticChunker.refine_sections` Pass 2 against merging sections that start with `"[Doc: "`.
  4. **[Medium — `AccessSurface.knowledge_ingest_batch` `domain_id` preservation & Claim 10 cleanup]:** Removed non-existent `ingest_docs_knowledge_graph` reference from Claim 10/Gap 4 and added Phase 3 Step 1 to pass `domain_id=s.domain_id` in `AccessSurface.knowledge_ingest_batch`.
  5. **[Medium — `repo_root` forwarding in `KnowledgeCore`]:** Updated Phase 2 Step 2 to accept `repo_root: Path | None = None` on `KnowledgeCore.ingest_file` as well and pass `repo_root.resolve()` into `parse_markdown_document`.
  6. **[Medium — Single-line backtick spans in `MarkdownParser._find_headers`]:** Updated Phase 1 Step 1 to use `r"^(`{3,}|~{3,})([^\n`~]*)$"` so single-line ```` ```inline``` ```` spans do not open unclosed fences.
- **Result:** ready — no further findings.
