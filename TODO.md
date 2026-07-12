# Engramory — Operational TODO

Per-repo backlog of infrastructure / operational items that don't (yet)
belong in `roadmap/ROADMAP.md` (product roadmap) or `docs/adr/`
(architecture decisions). Add items as they're discovered; strike or
move to a plan when resolved.

Framework spec-level TODOs go to `framework-feedback-log.md` (mirrors
issues upstream to `aidoc-flow-framework`); use this file for the
engramory-side work.

---

## 1. ~~🔴 CI `call / trust` fails on `ai-review.yml`~~ — RESOLVED 2026-07-11

**Status:** RESOLVED — ci/v1.9.1 mints the trust-config read token from the reviewer App (no `AI_REVIEW_TOKEN` PAT needed); `call / trust` passed on every PLAN-002 PR (#33–#39, 2026-07-11). Original record below.

**Status (original):** open. First hit: prior sessions; observed again on PR #6 (Wave 3 canon-adoption, 2026-07-08); merged with `--admin` bypass because `call / verify` (OPS-0069 audit-trail check) passed independently and the failing check is pre-existing infrastructure debt orthogonal to the PR content.

**Symptom (from PR #6 CI log):**

```text
call / trust  ##[error]fatal: repository 'https://github.com/vladm3105/aidoc-flow-operations/' not found
call / trust  The process '/usr/bin/git' failed with exit code 128
```

"not found" is GitHub's response when the token lacks permission —
`aidoc-flow-operations` exists, but engramory's default `GITHUB_TOKEN`
cannot cross the repo boundary to read a separate private repo.

**Root cause.**

- **engramory is a PUBLIC repo.**
- **`aidoc-flow-operations` is a PRIVATE repo** that holds the
  `trust.ai_review` allowlist (per `aidoc-flow-ci/.github/workflows/
  ai-review.yml` docstring — the trust decision reads
  `operations@main` config).
- The default `GITHUB_TOKEN` on engramory is scoped per-repo (public
  scope only), so `git clone https://github.com/vladm3105/
  aidoc-flow-operations.git` fails with the misleading "not found".
- The `ai-review.yml` reusable expects a cross-repo secret named
  `AI_REVIEW_TOKEN` (a scoped PAT with read on private `operations`)
  to be set on the consumer repo. **engramory doesn't have it set.**

**Downstream effect.** Every engramory PR shows `call / trust` FAIL +
`call / ai-review` SKIPPED. Merging requires `--admin` bypass (as
happened for PR #6). Automated OPS-0062 auto-merge cannot arm without
`call / trust` passing.

**Resolution options** (ordered by effort → correctness):

| Option | Effort | Notes |
| --- | --- | --- |
| **A. Set `AI_REVIEW_TOKEN` secret on engramory** | Small | Immediate unblock. Same pattern other consumers use (iplan-runner, iplan-standard likely already do this since they don't see the failure). Requires founder-created PAT with `contents: read` on `aidoc-flow-operations`; store as encrypted repo secret. |
| **B. Mirror `trust.ai_review` allowlist to a public location** (e.g., `aidoc-flow-ci/config/trust.json`) | Medium | Longer-term proper fix. Allowlist data is usernames (already public info); no confidentiality lost. Trust boundary reasoning stays the same. Requires small `ai-review.yml` reusable edit to read from the new public path. |
| **C. Inline the allowlist directly into engramory's `.github/workflows/ai-review.yml`** | Small | Config drift risk (allowlist maintained per-repo instead of centrally). Not recommended. |
| **D. Skip ai-review on engramory** | None | Loses AI review gate. Not recommended. |

**DECIDED (2026-07-09, PLAN-001):** Option A — founder sets the
`AI_REVIEW_TOKEN` secret (PAT, `contents: read` on `aidoc-flow-operations`):
`gh secret set AI_REVIEW_TOKEN --repo vladm3105/aidoc-flow-engramory`.
Option B (public trust config) remains an upstream `aidoc-flow-ci`
follow-up — NOT executable today: verified 2026-07-09 that aidoc-flow-ci
HEAD (> ci/v1.6.0) still checks out private `aidoc-flow-operations@main`
in the trust job; no canon version reads a local consumer config.
Reconciles the contradicting fix prescribed in `roadmap/ROADMAP.md`
"Known issue" (also corrected in PLAN-001 PR-1).

**Blockers / gates.** None; founder can do Option A whenever
convenient. Option B needs a small plan doc on aidoc-flow-ci first.

**References.**

- PR #6 (this repo) — merged with `--admin` bypass
- `aidoc-flow-ci/.github/workflows/ai-review.yml` — reusable docstring
  describing the two-job trust split
- `aidoc-flow-operations/CLAUDE.md` — OPS-0062 auto-merge default
  (blocked when `call / trust` FAIL)

**Discovered.** 2026-07-08 during PLAN-002 Wave 3 rollout.

## 2. 🟡 F5 server-side follow-up — reviewer App install + branch protection

**Status:** open, founder-gated — **partially underway**: the
`APP_REVIEWER_1_ID` / `APP_REVIEWER_1_KEY` secrets landed 2026-07-09; the
remaining half is `apply-standards.sh --apply` adding `call / verify` to
branch-protection required contexts (F5 blast-radius prerequisite per
operations CLAUDE.md § Unified CI). Previously tracked only as a CHANGELOG
bullet; promoted here so it has an owner surface.

**Discovered.** 2026-07-08 (Wave 3); promoted 2026-07-09 (PLAN-001 review).

## 3. ~~🟡 Stranded Dependabot PRs #8 / #9 / #10~~ — RESOLVED 2026-07-09

**Status:** resolved. All three merged 2026-07-09 on founder instruction:
`call / trust` turned out to be a **non-required** check (branch protection
not yet applied — see §2), so `mergeStateStatus` was UNSTABLE, not BLOCKED,
and normal squash-merge succeeded without the §1 secret. #10 needed a
`@dependabot rebase` after #9 touched the same `ci.yml` lines. §1 remains
open on its own merits (trust gate still fails until `AI_REVIEW_TOKEN`
lands); it just doesn't hard-block merges until §2 makes `call / verify`
(and any future required contexts) branch-protection-required.

**Discovered.** 2026-07-09 (PLAN-001 review). **Resolved.** 2026-07-09.

## 4. 🟡 CI does not run the container-backed test tiers (19 tests skipped)

**Status:** open. `ci.yml` runs bare `pytest`; the shared fixture
(`tests/conftest.py`) deliberately skips container-dependent
integration/e2e/conformance/security tests under `CI=` without
`ENGRAMORY_TEST_DSN` (no per-run Docker Hub pull → no rate-limit/outage
flakiness on the merge gate). Net: CI verifies 30 of 49 tests; the P1
security scenarios run only locally.

**Fix:** add a `services:` postgres (pgvector/pgvector:pg16) job container
to `ci.yml` and export `ENGRAMORY_TEST_DSN` — the fixture already prefers
the DSN path and applies migrations itself. Small PR; no test changes.

**Discovered.** 2026-07-09 (IPLAN-02 s2 review, finding folded as CI-skip).

## 5. 🟡 `kb_sections` conflict key permits same-tenant cross-project text overwrite

**Status:** open — needs a schema decision (migration 0004 candidate).
`uq_kb_sections_version` is `(tenant_id, doc_id, citation, version)`;
`upsert_kb_section` `DO UPDATE SET text` lets project A overwrite project
B's section text within one tenant, and a `space`-scoped section keeps its
wider scope while a narrower-scoped caller replaces its text. Mitigated
today by the governed-write gate (evidence + authz) and single-project
Phase 0 use.

**Fix options:** include `coalesce(project_id,'')` in the unique index
(new migration; drop + recreate), or reject cross-project version
collisions in `upsert_kb_section`. Decide before multi-project onboarding
(BRD-08 at the latest).

**Discovered.** 2026-07-09 (IPLAN-01 security review).

## 6. ~~🟡 `Repository.get_memory` has no tenant binding~~ — RESOLVED 2026-07-11

**Status:** RESOLVED in PR #33 (PLAN-002 Phase 1): `get_memory(memory_id, *, tenant_id)` WHERE-guarded; `memory_feedback`/`memory_forget` ride on tenant-guarded `record_feedback`/`retire_memory` (foreign id ≡ missing id). Original record below.

**Status (original):** open. `get_memory(id)` fetches any tenant's row and its
`KeyError` echoes the id (existence probe). Safe today — only tests and
same-tenant supersede flows call it — but `memory_feedback` /
`memory_forget` (next tools, SPEC-01) take caller-supplied ids and MUST
NOT ride on an unscoped fetch.

**Fix:** add a required `tenant_id` parameter (`WHERE id = %(id)s AND
tenant_id = %(t)s`) as the first step of the feedback-tools session; also
recorded in the IPLAN-01 session handoff.

**Discovered.** 2026-07-09 (IPLAN-01 security review).

## 7. ⚪ MVP-1 remaining engineering work (pointer — not tracked here)

Engineering delivery is tracked in the `sdd/` lifecycle, not this file.
Remaining for the MVP-1 vertical slice, per the `next_session_directive`
fields in the IPLAN session handoffs (`sdd/08_IPLAN/IPLAN-{01,02,06}_*.yaml`)
and `roadmap/ROADMAP.md`:

- LLMPort dev adapter (LiteLLM/Ollama) → query embeddings, SPEC-03 rank
  fusion, `reembed_and_reproject` (closes IPLAN-06, 7/7)
- ~~`memory_feedback` / `memory_forget` / `agent_profile_get` tools~~ —
  DONE 2026-07-11 (PR #33, PLAN-002 Phase 1); the SPEC-04 confidence
  *rule* consuming the recorded feedback is still open
- Eval harness + retrieval→outcome feedback loop (MVP-1 **exit criteria**
  per ROADMAP) — now scriptable via the `engramory` CLI (PLAN-002
  delivered the CLI face + docs; outcome *recording* shipped in #33; the
  SPEC-04 confidence rule consuming it is open)
- MCP gateway binding over `AccessSurface` (`engramory.mcp`) + OIDC
  ActorContext construction (ADR-10 production face)
- Deferred (gateway-cycle) security follow-ups from PLAN-002 reviews:
  within-tenant write containment for retire/feedback; `agent_profiles`
  is untenanted (dev-tier assumption)
