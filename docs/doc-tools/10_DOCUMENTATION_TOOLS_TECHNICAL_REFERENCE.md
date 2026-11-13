---
id: doc-platform-docs-10-tools-technical-reference
title: Documentation Tools Technical Reference — Vale, Starlight, Scalar, Docling, Engramory & Copybara Evaluation
kind: reference
domain: platform-docs
namespace: ns:platform
status: active
autonomy_tier: green
owner: platform-team
consumers:
  - aiops-harness
  - platform-engramory
  - aidoc-context-tools
  - aidoc-flow-support-desk
  - aiops-dev
tags:
  - technical-reference
  - vale
  - starlight
  - scalar
  - docling
  - copybara
  - mermaid
  - markdownlint
  - lychee
  - kuzu
triggers:
  cli_commands:
    - "engramory docs tools"
    - "engramory docs lint"
    - "engramory docs build-portal"
  mcp_tools:
    - "docs_slice_runbook"
    - "docs_validate"
    - "docs_check_drift"
    - "docs_export_llms"
    - "docs_smoke_run"
    - "docs_tools_inventory"
    - "docs_build_portal"
endpoints:
  host_loopback: "http://127.0.0.1:8005"
  container_gateway: "http://10.250.0.1:8000"
watched_paths:
  - "docs/doc-tools/10_DOCUMENTATION_TOOLS_TECHNICAL_REFERENCE.md"
  - "src/engramory/core/docs_tools.py"
  - "src/engramory/core/portal_builder.py"
  - ".vale.ini"
  - "portal/package.json"
last_verified: 2026-11-12
---

# Documentation Tools Technical Reference: Vale, Starlight, Scalar, Docling, Engramory & Copybara Evaluation

## 1. Master Documentation Toolchain Matrix

Every documentation tool in the ecosystem is governed by `25-approved-tooling-inventory.md` (`INV-DOC-01`: 100% open-source, self-hosted licenses; zero SaaS or open-core paywalls). The complete technical catalog consolidated inside `platform-engramory` and `aidoc-context-tools` is summarized in the matrix below:

| Tool ID | Package / Binary | License | Ring | Role in Ecosystem | Primary Repository |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **`engramory-docs-engine`** | `engramory.core.docs_tools` (`engramory docs` CLI) | Apache-2.0 / Internal | **Adopt** | Frontmatter AST (`DocumentFrontmatter`), `{id, tier, mode}` executable runbooks, `INV-STEP-01`/`02` safety gates, drift detection, `/llms.txt` export, and MCP `docs_*` server. | `platform-engramory` |
| **`vale`** | `errata-ai/vale` (Go binary + `.vale/styles/AIOpsAgent/`) | MIT | **Adopt** | Prose linter for AI-agent chunk independence (`NoCrossChunkDeictics`, `NoAmbiguousSubjectStart`, `BannedRetiredTools`, `DeterministicPortFormat`). | `platform-engramory` |
| **`astro-starlight`** | `@astrojs/starlight` (`v0.32+`) | MIT | **Trial** | Zero-JS static multi-repo developer portal rendering `docs/*.md`, Mermaid v11 diagrams, runbook tier badges, and `/llms.txt` routes. | `platform-engramory` (`portal/`) |
| **`scalar`** | `@scalar/api-reference` & `@scalar/astro` | MIT | **Trial** | Interactive OpenAPI 3.1, JSONSchema, FastMCP (`DOCS_TOOLS_SCHEMA`), and Protobuf HTTP playground embedded at `/contracts/<service>`. | `platform-engramory` (`portal/`) |
| **`docling`** | `docling>=2.0` (`PyMuPDF` + `BeautifulSoup4`) | MIT | **Adopt** | Layout-aware document extraction (`SmartExtractionRouter`) converting complex PDFs, DOCX tables, and HTML into structured Markdown for L0 RAG. | `platform-engramory` |
| **`mermaid`** | `mermaid` (`v11+` / `@mermaid-js/mermaid-cli`) | MIT | **Adopt** | Declarative Diagrams-as-Code (`flowchart`, `sequenceDiagram`, `stateDiagram-v2`, `erDiagram`) inside Markdown and SDD artifacts. | All 35+ repositories |
| **`markdownlint-cli2`** | `markdownlint-cli2` | MIT | **Adopt** | Fast Markdown AST structure, heading hierarchy, and code-fence syntax validation in Tier-1 `lefthook` gates. | `aiops-harness` & `platform-engramory` |
| **`yamllint`** | `yamllint` | GPL-3.0 (CLI tool) | **Adopt** | Fast YAML schema, SDD layer, ADR/CHG manifest, and frontmatter syntax validation in Tier-1 `lefthook` gates. | `aiops-harness` & `platform-engramory` |
| **`lychee`** | `lycheeverse/lychee` (Rust binary) | Apache-2.0 / MIT | **Adopt** | High-speed asynchronous relative and cross-repository link and `#anchor` verifier in Tier-2/3 CI gates. | `aiops-harness` & `platform-engramory` |
| **`aidoc-sdd-kuzu`** | `sdd_doc_lint` + `kuzu` (`aidoc-ctx`) | MIT | **Adopt** | 10-layer SDD YAML schema validator, Binary Topological Validation, and `<1ms` openCypher graph context slicer. | `aidoc-framework` & `aidoc-context-tools` |
| **`copybara`** | `google/copybara` | Apache-2.0 | **Hold** | Evaluated and placed on **Hold**; superseded by `GitSyncEngine.sync`, `engramory docs build-portal`, and the Gitea-to-GitHub Split-Forge mirror. | Not deployed (`Hold`) |
| **`mintlify` / `mkdocs-insiders`** | Commercial / Sponsorware portals | Proprietary | **Hold** | Rejected under `INV-DOC-01`; replaced by 100% MIT-licensed `@astrojs/starlight` + `@scalar/api-reference`. | Prohibited (`Hold`) |

---

## 2. `engramory.core.docs_tools` & `engramory docs` CLI Engine

### 2.1 Core Capabilities & Module Architecture

`engramory.core.docs_tools` (`src/engramory/core/docs_tools.py` and `src/engramory/core/portal_builder.py`, exposed via `engramory docs` in `src/engramory/cli.py`) is the deterministic Python engine governing all Markdown specifications and executable runbooks across the platform:

1. **Strict Frontmatter AST Validation (`DocumentFrontmatter`):**
   Parses YAML frontmatter and enforces `DOC-FM-000` through `DOC-FM-007` plus `DOC-REG-001` (`id`, `title`, `kind`, `domain`, `namespace`, `status`, `autonomy_tier`, non-empty `watched_paths` matching live repository files, `endpoints`, `triggers`, and `last_verified` ISO date within `review_ttl_days`).
2. **Executable Runbook Step Extraction (`KBRunbookStep`):**
   Parses annotated code fences (` ```bash {id="..." tier="green|yellow|red" mode="read-only|mutating" expect_exit=0 timeout_sec=30} `) into structured AST records stored in `kb_runbook_steps`.
3. **Read-Only Safety Gate (`check_green_step_safety`, `INV-STEP-01`/`02`):**
   Verifies at lint time and pre-execution time that every `tier="green"` step has `mode="read-only"` and contains zero mutating commands (`rm`, `mv`, `dd`, `docker restart`, `kubectl delete`, `bao write`), zero shell redirections (`>`, `>>`, `| tee`), zero subshell expansions (`$(...)`, backticks, `bash -c`, `python -c`), and zero mutating HTTP flags (`POST`, `PUT`, `DELETE`, `-d`).
4. **Code-to-Doc Drift & Staleness Engine (`check_repo_drift`):**
   Matches `git diff` file paths against every document's `watched_paths` globs (`WATCHED_PATH_MODIFIED`) and flags documents whose `last_verified` date exceeds `review_ttl_days` (`TTL_STALE`).
5. **Deterministic `/llms.txt` & `/llms-full.txt` Compiler (`export_repo_llms`):**
   Generates structured agent indexes sorted by priority (`runbook`, `playbook`, `architecture`, `specification`) with Rule D14 secret redaction.

---

## 3. `Vale` (`errata-ai/vale`, MIT License) — Prose-as-Code Linter

### 3.1 Why `Vale` Is Used for AI-Agent Documentation

Standard Markdown linters only validate syntax (such as list indentation or heading levels). When documents are chunked into 150–400 word sections (`kb_sections`) for hybrid vector + BM25 retrieval in `platform-engramory`, ambiguous prose causes **chunk context loss** and **agent hallucination**.

`Vale` (`errata-ai/vale`, a fast single-binary Go prose linter licensed under MIT) enforces the custom `AIOpsAgent` style defined in `.vale.ini` and `.vale/styles/AIOpsAgent/*.yml`, with 100% deterministic parity built into `engramory.core.docs_tools.lint_markdown_file` for offline environments where the Go binary is not yet installed:

| Vale Rule File | Rule ID | Severity | Technical Mechanism & Purpose |
| :--- | :--- | :--- | :--- |
| **`NoCrossChunkDeictics.yml`** | `DOC-PROSE-001` | `error` | `extends: existence` matching `"the command above"`, `"the script above"`, `"as shown below"`, `"in the previous section"`, `"see above"`, `"see below"`, `"as mentioned earlier"`. Forces authors to cite explicit step IDs (`step_id="probe-bao-status"`) or section anchors so retrieved chunks are self-contained. |
| **`NoAmbiguousSubjectStart.yml`** | `DOC-PROSE-002` | `warning` | Regex rule matching the first sentence after a `##` or `###` heading starting with `It`, `This`, `They`, `These`, or `Those` followed by a verb. Forces explicit subject naming at the start of every chunk. |
| **`BannedRetiredTools.yml`** | `DOC-STACK-001` | `error` | `extends: substitution` blocking unquoted prose references to retired stack tools (`` `seaweedfs` ``, `` `bytebase` ``, `` `pre-commit` ``) so outdated architectures cannot re-enter active runbooks. |
| **`DeterministicPortFormat.yml`** | `DOC-PORT-001` | `warning` | Regex rule flagging bare `http://localhost:<port>` in prose and requiring explicit `127.0.0.1:<port>` (host loopback) or `10.250.0.1:<port>` (container gateway) per `INV-NET-LOCALHOST`. |

---

## 4. `Astro Starlight` (`@astrojs/starlight`, MIT License) — Multi-Repo Developer Portal

### 4.1 Technical Architecture & Build Pipeline

`Astro Starlight` (`@astrojs/starlight`, MIT License) is the static documentation portal framework configured in `portal/package.json` and `portal/astro.config.mjs`.

1. **Multi-Repo Content Staging (`engramory docs build-portal`):**
   `src/engramory/core/portal_builder.py` (`stage_portal_bundle`) scans approved ecosystem repositories (`platform-*`, `aidoc-*`, `aiops-*`), validates path confinement (`resolve_confined_repo_path` + `_is_within_root`), scrubs every Markdown file through Rule D14 (`_scrub_manifest_text`), and stages clean `.md` files into `portal/src/content/docs/reference/`.
2. **Zero-JavaScript Static Output & Raw Markdown Routes:**
   Starlight compiles `portal/src/content/docs/` into static HTML assets with built-in Pagefind full-text search, native Mermaid v11 diagram rendering, and alongside raw `.md` and `/llms.txt` endpoints for LLM agents.
3. **Why Starlight Over Alternatives:**
   Unlike `MkDocs Material Insiders` (sponsorware/commercial features) or `Mintlify` (proprietary SaaS), `@astrojs/starlight` is 100% MIT-licensed, builds on the already-approved Node.js 24 LTS + `pnpm` toolchain (`25-approved-tooling-inventory.md`), and natively embeds Astro components such as `@scalar/astro`.

---

## 5. `Scalar` (`@scalar/api-reference`, MIT License) — Interactive Contract & MCP Console

### 5.1 Technical Architecture & Supported Specifications

`Scalar` (`@scalar/api-reference` and `@scalar/astro`, MIT License) provides the interactive API and contract reference console embedded inside the Starlight portal at `/contracts/<service>`:

1. **OpenAPI 3.1 & FastAPI Service Specs:**
   Renders interactive REST API documentation for `platform-engramory` (`127.0.0.1:8000`), `platform-paperclip` (`127.0.0.1:3100`), `aidoc-flow-support-desk` (`127.0.0.1:8090`), and `platform-security` (`127.0.0.1:8095`).
2. **FastMCP Tool Schema Catalog (`DOCS_TOOLS_SCHEMA`):**
   Renders the JSON Schema input/output contracts for all 7 documentation MCP tools (`docs_slice_runbook`, `docs_validate`, `docs_check_drift`, `docs_export_llms`, `docs_smoke_run`, `docs_tools_inventory`, `docs_build_portal`) and Engramory's SDD/Memory MCP tools (`memory_search`, `sdd_context_slice`, `sdd_blast_radius`).
3. **CloudEvents & Protobuf HTTP Transcoding Specs:**
   Displays the NATS JetStream CloudEvents 1.0 payload schemas (`xp.platform.docs.drift.v1`, `xp.platform.docs.stale.v1`) compiled from `platform-data-contracts`.

---

## 6. `Docling` (`docling>=2.0`, MIT) + `PyMuPDF` + `BeautifulSoup4` — Smart Document Extraction

### 6.1 `SmartExtractionRouter` in `platform-engramory`

While `docs/*.md` files are authored directly in Markdown, the ecosystem also ingests external PDFs, architectural RFCs, vendor datasheets, `.docx` files, and HTML snapshots into the `platform-engramory` L0 Knowledge Core (`src/engramory/core/extraction.py`):

1. **Fast Path (`PyMuPDF` / `fitz` + `BeautifulSoup4`):**
   Digital-native text PDFs and clean HTML documents are extracted in milliseconds using `PyMuPDF` (`pymupdf>=1.24.0`) and `BeautifulSoup4` (`beautifulsoup4>=4.12.0`).
2. **Deep Layout & Table Path (`Docling`, `docling>=2.0.0`, MIT License):**
   When `SmartExtractionRouter` detects complex multi-column layouts, embedded tables, or scanned PDF/DOCX artifacts (`[extraction,ocr]` extras in `pyproject.toml`), it routes the document through **IBM Docling** (`DocumentConverter`). Docling reconstructs tables into GitHub-Flavored Markdown tables and preserves heading hierarchies before passing the output to `chunk_markdown()`.

---

## 7. `Mermaid` (`v11+`), `markdownlint-cli2`, `yamllint` & `lychee` — Syntax & Link Gates

### 7.1 Complementary Static Verifiers

1. **Mermaid (`v11+` / `@mermaid-js/mermaid-cli`, MIT License):**
   Enforces Diagrams-as-Code across all architecture specs and SDD modules. Only deterministic diagram types (`flowchart`, `sequenceDiagram`, `stateDiagram-v2`, `classDiagram`, `erDiagram`, `xychart-beta`) are permitted, with quoted node labels (`id["Label (Detail)"]`) to prevent parser failures.
2. **`markdownlint-cli2` (MIT License) & `yamllint` (GPL-3.0):**
   Validate Markdown heading increments, fenced code-block language tags, and YAML frontmatter indentation in `< 200ms` during Tier-1 `lefthook` hooks.
3. **`lychee` (`lycheeverse/lychee`, Apache-2.0 / MIT License):**
   Rust-based link checker that verifies internal relative links, `#heading-anchor` targets, and cross-repository references in Tier-2 and Tier-3 CI gates, complementing the pure-Python `BROKEN_RELATIVE_LINK` check in `engramory.core.docs_tools`.

---

## 8. `aidoc-framework` (`sdd_doc_lint`) & Embedded `Kùzu` (MIT) — SDD & Graph Slicing

### 8.1 Two-Plane Documentation Governance (Plane A SDD + Plane B Operational Docs)

1. **`aidoc-framework` (`sdd_doc_lint`):**
   Validates Plane A 10-layer Specification-Driven Development (`01_BRD` through `10_EVAL`) YAML artifacts, enforcing strict `trace_to` DAG integrity, EARS syntax (`WHEN ... THE SYSTEM SHALL ...`), and BDD Given/When/Then completeness.
2. **Embedded `Kùzu` (`kuzu`, MIT License):**
   In-process C++/Python property graph database shared between `aidoc-context-tools` and `platform-engramory`. Stores both SDD `ArtifactNode` entities and Plane B `DocumentNode`, `RunbookStepNode`, and `AlertNode` entities so agents can execute `< 1ms` openCypher context slices (`aidoc_slice_context` and `docs_slice_runbook`).

---

## 9. Architectural Evaluation: Why Google `Copybara` Is Placed on `Hold`

### 9.1 What Google `Copybara` Does and Why We Evaluated It

Google's **`Copybara`** (`google/copybara`, Apache-2.0) is a Java/Bazel tool (`copy.bara.sky` Starlark scripts) designed to transform, filter, and synchronize code or documentation between disjoint repositories (for example, syncing an internal Google Piper monorepo to a public GitHub mirror while scrubbing internal paths).

### 9.2 Three Reasons `Copybara` Is Placed on `Hold` in Our Ecosystem

1. **Heavy JVM + Bazel Runtime Overhead Violates Single-Host Consolidation:**
   `Copybara` requires a JVM runtime and Bazel build toolchain that does not exist anywhere else in the approved polyglot stack (`09-authoritative-platform-technology-stack.md` standardizes strictly on Python 3.12+, Go 1.26, Rust 1.85+, and Node.js 24 LTS).
2. **Native Incremental Git Sync Already Exists in `platform-engramory` (`GitSyncEngine`):**
   `src/engramory/core/git_sync.py` (`GitSyncEngine.sync`) already performs commit-differential (`git diff --name-status`) synchronization of `.md`, `.html`, `.pdf`, and `.docx` files across all 35+ repositories directly into PostgreSQL 18 (`kb_documents`, `kb_sections`, `kb_runbook_steps`) without duplicating Markdown files across git repositories.
3. **Portal Staging & Split-Forge Mirroring Are Handled Natively with Rule D14 Scrubbing:**
   - For multi-repo portal assembly, `engramory docs build-portal` (`src/engramory/core/portal_builder.py`) stages Markdown files from spoke repositories into `portal/src/content/docs/reference/` while enforcing path-traversal confinement (`resolve_confined_repo_path`) and deterministic Rule D14 secret/PII scrubbing (`_scrub_manifest_text`).
   - For repository mirroring between internal Gitea (`10.250.0.1:3001`) and GitHub backup remotes, the **Split-Forge** pipeline in `aiops-harness` (`25-approved-tooling-inventory.md` §3.1) runs `gitleaks` + `trivy` gates prior to push without needing Starlark `copy.bara.sky` scripts.

---

## 10. Verification & Inspection Commands

Inspect the live documentation tool inventory and validate all governed documentation specifications in `platform-engramory` using the read-only `green` steps below:

```bash {id="inspect-doc-tools-catalog" tier="green" mode="read-only" expect_exit=0 timeout_sec=10 service="platform-engramory"}
uv run engramory docs tools
```

Execute the Tier-1 offline frontmatter, runbook safety (`INV-STEP-01`/`02`), and prose linter across `docs/doc-tools/`:

```bash {id="validate-all-platform-docs-specs" tier="green" mode="read-only" expect_exit=0 timeout_sec=15 service="platform-engramory"}
uv run engramory docs lint --target docs/doc-tools --require-frontmatter --verify-registry
```
