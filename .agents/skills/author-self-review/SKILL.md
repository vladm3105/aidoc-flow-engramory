---
name: author-self-review
description: >-
  Use before committing code or requesting formal review. Directs the authoring agent to perform forced adversarial bypass payload generation and a 5-lens audit to catch security, boundary, and edge-case defects in the diff before committing.
---

# Author Self-Review

Authors suffer from assumption blindness: the tests an author writes reflect the same mental model that created the implementation. Consequently, an authoring agent can run full test suites, get green passes, and yet fail formal review (`task review`) on basic security boundaries, parameter omissions, or flag bypasses.

This skill establishes the **Layer 1 Author Self-Review Protocol**. Before staging, committing, or requesting review, the authoring agent MUST audit its uncommitted or feature-branch diff against the 5 Rubric Lenses and formulate **forced adversarial bypass payloads**.

---

## 1. Extract the Diff

Extract the exact diff introduced by your changes:

```bash
# If uncommitted:
git diff HEAD

# If already committed on feature branch:
git diff "$(git merge-base dev HEAD)" HEAD
# or:
git diff "$(git merge-base main HEAD)" HEAD
```

Review every added, modified, or deleted line. Do not rely on your memory of what you wrote.

---

## 2. Forced Adversarial Payload Generation (Anti-Blindness)

To defeat author blindness, you MUST formulate and test at least **three concrete adversarial bypass payloads** against the code you touched. You cannot proceed until you verify how your code handles each one:

### Payload Category A: Network & Caller Argument Injections

- **Attached flag variations:** If your code invokes or wraps commands (such as `curl`, `git`, or CLI utilities), test attached arguments: e.g. `-K<file>`, `--config=<file>`, `-u<creds>`, `-L`, `--location`, `-k`, `--insecure`, `-v`, `--verbose`.
- **Multi-step options:** Test chaining flags such as `--next` or `-:` in curl.
- **Verification:** Does the wrapper or validation strictly reject unapproved flags and attached values before execution?

### Payload Category B: Protocol, Scheme, & Host Boundaries

- **Plaintext downgrade:** If credentials, tokens, or session identifiers are passed, test with `http://` endpoints. Does the code strictly prohibit HTTP and enforce HTTPS?
- **Host & userinfo bypasses:** Test hostnames with userinfo (`https://user:pass@host`), queries (`https://host?q=val`), fragments (`https://host#frag`), or subdomain suffix attacks (`https://host.evil.com`).
- **Path traversal:** Test URL or filesystem paths containing dot segments (`/..`, `/.`, `../`) and encoded variations (`%2e%2e`, `%2f`, `%5c`). Does the code canonicalize or strictly reject traversal before attaching credentials or accessing resources?

### Payload Category C: Parsing Discrepancies & File Overwrites

- **Format quote variants:** If modifying YAML, JSON, or regex parsers (e.g. GitHub/Gitea workflow triggers), test unquoted, single-quoted, and double-quoted variants (e.g. `on:`, `'on':`, `"on":`).
- **Parser-discrepant characters:** Test strings containing `$`, `` ` ``, `"`, `'`, `&`, `\`, and newlines. Ensure shell expansions or injection cannot occur.
- **Permissive overwrite vulnerability:** When generating or rewriting environment, token, or secret files, verify that pre-seeding an existing file with permissive mode (`0644` or `0666`) does NOT leave rewritten secrets exposed. Files must be installed or chmod'd to `0600` atomically.

---

## 3. The 5-Lens Adversarial Audit Checklist

Audit your diff against the 5 Rubric Lenses used by the independent reviewer:

### Lens 1 — Correctness & Specification Parity

- [ ] Are all requirements from the task or plan implemented without omissions?
- [ ] Do edge cases (empty inputs, zero counts, negative numbers, missing env vars) fail cleanly with non-zero exit codes?
- [ ] Are error diagnostics unambiguous and sent to `stderr`?

### Lens 2 — Real Verification & Test Airgaps

- [ ] Are there automated tests verifying that all 3 adversarial bypass payloads are rejected?
- [ ] Do negative tests assert non-zero exit codes AND that dangerous operations (network calls, file writes) never executed?
- [ ] Are integration test fixtures strictly airgapped (`GIT_ALLOW_PROTOCOL=file`, mock network services)?
- [ ] Does the test suite avoid modifying shared host paths outside tracked temporary directories?

### Lens 3 — Architecture & Minimal Blast Radius

- [ ] Is this the smallest defensible change? Are there unnecessary refactors or formatting churn?
- [ ] Does the change adhere to the approved technology stack? No unapproved third-party dependencies.
- [ ] Is state declarative and reproducible (no manual out-of-band mutations)?

### Lens 4 — Security & Boundary Invariants

- [ ] Are secrets, tokens, and keys isolated from process argv (using 0600 config files or filesystems)?
- [ ] Are secrets prevented from leaking to `stdout`/`stderr` (no `-v`/verbose or debug tracing in authenticated flows)?
- [ ] Are permissions on secret and environment files strictly enforced to mode `0600`?
- [ ] Are network destinations restricted to HTTPS with strict host and path matching?

### Lens 5 — Hygiene & Polish

- [ ] Are all new suppressions (`noqa`, `ignore`, `nolint`) justified with an inline `reason:`?
- [ ] Are there no leftover debug statements (`console.log`, `print(..., "DEBUG")`, `breakpoint()`)?
- [ ] Does `task check` and `task self-review:static` pass without warnings or errors?

---

## 4. Work Loop Integration

1. **Before Commit:**
   - Execute `task self-review:static` (or `task check`).
   - If heuristic anti-patterns are flagged, resolve the root cause or add inline `# self-review-ignore: reason=<why>` if verified safe.
2. **Commit:**
   - Stage tested files: `git add <files>`.
   - Commit: `git commit -m "feat/fix: <description>"`.
3. **Formal Independent Review:**
   - Run `task review -- --issue <N>` or `task review -- --requirements <plan>`.
   - With author self-review completed, the diff is hardened against common reviewer traps, ensuring single-iteration `PASS`.

---

## 5. Adaptive Multi-Lens Subagent Pre-Review & 2-Pass Fold Loop (PLAN-018)

Before invoking outer `task review` (`tier-frontier`) on multi-module or multi-chunk changes, run the **Adaptive Multi-Lens Subagent Pre-Review & 2-Pass Fold Loop** so all Critical, Major, and Medium defects are identified and folded within the 3-review cap:

1. **Select 2–4 Best-Fit Engineering Lenses & Co-Located Clusters (`aiops sdd select-lenses` / `aiops sdd review-clusters`):**
   - Run `uv run python -m aiops_harness.cli.main sdd review-clusters --json` (or `select-lenses`) to deterministically pick the **2–4 best-fit lenses** matched to the touched files and plan:
     - `google_sre_design` — **Google** (Design Doc invariants, readability, SRE hermeticity & blast-radius)
     - `anthropic_evaluator_boundary` — **Anthropic** (Evaluator-Optimizer `Judge != Generator`, sandbox/tool contracts, untrusted prompt/XML/expression boundary safety, anti-mock verification)
     - `aws_saga_resilience` — **AWS** (Well-Architected 1-way/2-way doors, SAGA compensation completeness, idempotency, atomic lock/CAS convergence)
     - `microsoft_stride_contracts` — **Microsoft** (STRIDE threat modeling, defense-in-depth against path/flag/algorithm injection, schema backward compatibility)
   - Minimum-2 fallback guarantee: if fewer than 2 lenses match heuristics, `google_sre_design` + `microsoft_stride_contracts` are selected automatically.
2. **Co-Locate Source Modules with Their Tests & Include `<cross_chunk_index>`:**
   - Ensure each implementation module (`aiops_harness/**/*.py`) is reviewed alongside its matching unit/integration test file (`tests/**/test_<stem>.py`), and pass `<cross_chunk_index>` when changes span multiple chunks.
3. **Execute the 2-Pass Pre-CI Review & Fold Loop (`Review 1 -> Fold 1 -> Review 2 -> Fold 2`):**
   - **Pass 1 (Fresh-Context Subagent Review):** Dispatch an independent read-only subagent (`reviewer_id != generator_id`) with the selected 2–4 best-fit lenses and co-located clusters to exhaustively identify all Critical, Major, and Medium defects.
   - **Fold 1:** Fix every Critical, Major, and Medium finding in the worktree and add or update a regression test for each fix.
   - **Pass 2 (Fresh-Context Verification & Residual Audit):** Re-verify the folded diff and regression tests with the independent subagent until `blocking_findings_remaining == 0` and `verdict == "PASS"` (verifiable via `verify_two_pass_fold_loop`).
   - Only after Pass 2 returns `PASS` should you commit and run `task review`.

