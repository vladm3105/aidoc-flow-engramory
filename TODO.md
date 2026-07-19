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

## 8. 🔴 PLAN-004 — documentation review remediation (20 findings)

**Status:** open — plan READY 2026-07-19
(`plans/PLAN-004_docs-review-remediation.md`): 66 cited claims, 5 review
passes incl. **two** independent adversarial passes that returned 10 and
7 load-bearing findings (all folded, none rejected) plus a
founder-decision fold (§10).
Execute in PR order (1 → 2a → 2b → **7** → 3 → 4a → 4b → 4c → 5 → 6);
rebase 4c on 3 and 6 on 4a — they collide on `README.md` /
`roadmap/ROADMAP.md` / index files. PR 7 (the ADR-10 `knowledge ingest`
binding — the plan's one code deliverable) sits right after 2b so the
doc qualifier does not outlive the gap it describes. Each PR carries its
own doc-currency.

Numbered **004**, not 003: `PLAN-003` already refers in this repo's
prose to the *external* aidoc-flow-ci canon-adoption plan
(`CLAUDE.md:39`, `docs/README.md:10`, `DECISIONS.md:14,18,25,33,57`,
`HANDOFF.md:72`). Disambiguating those references is PR 3 + PR 4c work.

Origin: founder-requested review 2026-07-19 (repo + `docs/` vs best
practice and current trends). Three-agent review — internal doc
consistency, docs-vs-code drift, 2026 field research.

**PR 1 — 🔴 P1: five doc sites teach a CLI invocation that exits 2.**
`sdd/06_SPEC/SPEC-07_cli_face.yaml:46` defines the invocation contract
**normatively** as `engramory [--json] [--config PATH] <command> [args]`
— global-first. The code conforms (`src/engramory/cli.py:256,260`); the
**docs are the deviation**, teaching a post-subcommand `--json` that
argparse rejects: `docs/INSTALL.md:62,71` (the documented smoke check
itself), `docs/AGENT-QUICKSTART.md:38`, `docs/AGENT-INTEGRATION.md:73`
(the paste-verbatim vendor snippet — highest blast radius),
`skills/engramory-memory/SKILL.md:26`. `scripts/smoke_preprod.sh:22` and
`tests/integration/test_cli.py` use the correct form, which is why CI
never caught it.
**Fix:** correct the five doc sites to global-first (NOT a code change —
widening the contract would require a SPEC-07 amendment; see §10), add a
docs-conformance test so doc invocations are parse-checked, and fix
Track B's CI snippet (`docs/AGENT-QUICKSTART.md:50`), which has no
`engramory init` so it exits 2 on a fresh checkout — uncovered by the
documented `|| [ $? -eq 3 ]` guard (`:57`).

**PR 2a — 🟡 P2: docs AND normative specs overstate the code.**
(a) The unified read is asserted on **four** surfaces, not two —
`docs/CORES.md:24`, `docs/CORES.md:59` (boundary rule 4, calling it
"deliberately unified"), `sdd/06_SPEC/SPEC-01_access_surface.yaml:56`,
and `src/engramory/mcp/server.py:4` (whose docstring says "keep this
docstring in sync"). The code reads `FROM memories` only
(`core/repository.py:369`); `kb_sections` is written and counted, never
read back (`:505`). Downgrading this to "planned" is a **spec
deviation**, not bookkeeping — record it in `DECISIONS.md` and file the
retrieval leg (§10). (b) The confidence overstatement also lives in
`SPEC-01:66` ("drives the SPEC-04 confidence-update rule", present
tense) alongside `docs/AGENT-QUICKSTART.md:23` and
`skills/engramory-memory/SKILL.md:54` — nothing updates
`memories.confidence`; the surface calls it the signal *for* the future
rule (`access/surface.py:193`). (c) SPEC-01 signature drift:
`interfaces.exports` declares narrower signatures than `surface.py:104`
implements, and `EpisodeIn`/`KBEntryIn`/`AuditRecord` do not exist.
Not a governance PR (AGENTS.md is in 2b), though ai-review's
`tier=spec` may catch it.

**PR 2b — 🟡 P2/P3: ranking, tool reachability, config honesty.**
(a) Ranking is recency-only (`access/surface.py:16`) — disclosed in code,
in no agent doc, while SKILL.md advises "prefer specific queries".
(b) `knowledge_ingest` has no CLI binding — this is an **ADR-10
conformance gap**, not a doc overclaim: `ADR-10:71` (Accepted) mandates
the CLI expose it. Noted in the PR body only — **no doc qualifier**,
since PR 7 closes the gap one PR later and the qualifier would both
breach the ≤3-surface governance cap and be deleted immediately.
(c) Config/env honesty:
only `ENGRAMORY_PROFILE` is read (`adapters/factory.py:32`);
`REDIS_URL`/`OLLAMA_HOST`/`EMBEDDING_MODEL`/`EMBEDDING_DIMS`/
`POSTGRES_HOST`/`PORT` are read by neither `src/` nor compose (dead),
while `KEYCLOAK_*`/`NEO4J_*`/`S3_*` are compose-consumed — label them
differently. `AGENTS.md:15` presents `LITELLM_BASE_URL` as live,
`config/domains/*.yaml` is inert, `AGENTS.md:13` omits the
psycopg-in-core carve-out the tests enforce. **Governance PR**
(AGENTS.md) — 3 surfaces.

**PR 3 — 🔴 P1: governance layer describes a pre-implementation repo.**
`CLAUDE.md:13` "no adapters or running gateway yet" (false since
PLAN-002); `CLAUDE.md:71` per-repo CI state pins `@ci/v1.4.3`–`v1.6.0`
(actual: `@ci/v1.9.5` across 11 callers) and cites the `AI_REVIEW_TOKEN`
blocker §1 above marks RESOLVED; `roadmap/ROADMAP.md:25` prescribes that
superseded fix for the resolved problem; `README.md:97` "Project
initiation … scaffolding" contradicts README's own agent section.
Governance PR, exactly 3 surfaces, Rule 2 self-review before push.

**PR 4a/4b/4c — 🟡 P2/P3: bookkeeping currency (split to hold the cap).**
The single bundle was ~10 surfaces, 3× the governance cap.
**4a (indexes):** IPLAN doc-level `status: Draft` vs `DONE, verified:
true` manifests (`sdd/08_IPLAN/IPLAN-01_access_surface.yaml:8` vs `:19`)
plus index rows; `docs/README.md` missing INSTALL / AGENT-QUICKSTART /
AGENT-INTEGRATION rows + Skill pointer.
**4b (live state):** §4 above's "30 of 49 tests" → 94 (item stays open —
`ci.yml:25` is a bare `pytest` with no `services:`); HANDOFF ~9 PRs
behind + its superseded CI note; `CHANGELOG.md:40` "Six PRs" → seven.
**4c (labels + disambiguation):** engine-default drift (Mem0 decided at
`docs/ARCHITECTURE.md:176`, LangMem/Cipher at `:201` and
`roadmap/ROADMAP.md:45`); README's MEMORY_DESIGN row missing the
historical qualifier; the remaining external `PLAN-003` references.
`docs/adr/README.md`'s ADR-10 row moves to PR 6, which already owns
that file.

**PR 5 — 🟡 P2: no security or retention doc.** Add `SECURITY.md`
consolidating the trust boundary now scattered across AGENT-QUICKSTART,
AGENT-INTEGRATION and §5 here — dev-tier fence, default-deny, tenant
wall, audit-before-return, fail-closed, and what the CLI face
deliberately does *not* defend. Plus the sharper gap: docs tell agents
never to store secrets/PII, but `memory_forget` is a **soft** retire with
provenance retained and **no documented purge path**. State what
soft-retire guarantees, that the episode body persists, and the operator
purge path (or say explicitly that none exists and file it as debt).
Every security claim traced to its enforcing test or marked "not
defended". The retention policy lands as a section *inside*
`SECURITY.md`, keeping this PR at one surface.

**PR 6 — 🟡 P2: field alignment (2026).** Author a conceptual ADR
proposing L0 ↔ **Open Knowledge Format** projection — Google Cloud's
vendor-neutral knowledge-interchange spec (announced 2026-06-12: bundle =
directory of markdown files with YAML frontmatter, file path = concept
identity, markdown links form the graph, reserved `index.md`/`log.md`).
It is an interchange format, not a memory engine: it maps onto the L0
knowledge core and would give MEMORY_DESIGN's existing "read-only
Markdown export" idea conformance with a published spec, filling the
interchange gap those docs flag as standardless. Does not touch L1–L3.
Proposal only — founder accepts separately. Also carries the ADR-10 row
missing from `docs/adr/README.md:21` and a one-line `docs/STRATEGY.md`
note that managed-memory churn in the field reinforces ADR-05.
**Governance PR** — 3 surfaces.

**Discovered.** 2026-07-19 (founder-requested repo + docs review).

## 9. ⚪ Field-alignment design debt (recorded by PLAN-004 — not built there)

Recorded so the gaps are tracked rather than silently absent. Each needs
its own spec + plan; none is documentation work.

- **Bi-temporal validity + contradiction detection (D3)** — the schema
  carries one time dimension (`valid_from`/`valid_to` + `supersedes`);
  the field standard adds an ingestion/transaction dimension and
  invalidate-not-delete on contradiction. The largest measured
  differentiator in the current benchmark literature.
- **Hybrid retrieval** — Postgres full-text/BM25 alongside pgvector, a
  rerank stage, and query-type routing. Blocked behind the LLMPort dev
  adapter (§7) which supplies query embeddings; vector-only loses on
  temporal and multi-hop questions.
- **Named eval benchmarks + targets** — "lead with evaluation"
  (`docs/STRATEGY.md`) currently has no yardstick. Report accuracy per
  category (knowledge-update and temporal reasoning especially) *and*
  tokens-per-query. Pairs with the §7 eval-harness exit criterion.
- **Injection / poisoning screen (G1)** — beyond the `source_trust`
  column; quarantine on scope-crossing writes is already doctrine.
- **MCP gateway naming + spec target** — when the gateway lands (§7),
  align tool shapes to the de-facto memory surface and target the
  post-2026-07-28 MCP spec (stateless core, Tasks).
- **OTel GenAI semconv on memory-operation spans** — dual-emit opt-in
  while the conventions remain Development status; fits the interlog
  logging-standard direction.

**Discovered.** 2026-07-19 (PLAN-004 field research).

## 10. 🟡 Conformance gaps surfaced by the PLAN-004 review — DECIDED 2026-07-19

Three items where an artifact and its normative contract disagree. All
three were decided by the founder 2026-07-19; see PLAN-004 Review log
Pass 4.

- **`kb_sections` retrieval leg — DEFERRED behind a trigger (ratified).**
  `memory_search` reads `FROM memories` only (`core/repository.py:369`),
  so knowledge is unretrievable through the unified read that
  `docs/CORES.md:59` calls "deliberately unified" and
  `sdd/06_SPEC/SPEC-01_access_surface.yaml:56` contracts. **Decided:
  ratify the deviation** — the blocker is definition, not effort.
  SPEC-03's fusion is undefined for knowledge rows: `:80` names
  `memories.ts_lex` for the lexical leg and `:81`'s multipliers need
  `confidence` + `source_trust`, none of which `kb_sections` has
  (`db/migrations/0003_reconcile_contracts.sql:90-105`). Building it
  against today's recency-only ranking would interleave authoritative
  knowledge with inferred memory, inverting the trust separation at
  `docs/CORES.md:20`. **Unified retrieval lands when all three hold:**
  (1) LLMPort dev adapter supplies query embeddings — alone this closes
  the vector leg, since `kb_sections.embedding` already exists (§7);
  (2) migration 0004 adds `ts_lex` + GIN index to `kb_sections`;
  (3) SPEC-03 is amended to define post-fusion multipliers for knowledge
  rows. Condition (3) — not (2) — owns the trust handling: `confidence`
  and `source_trust` are the *memory* core's markers for inferred
  content, so adding them to `kb_sections` would dissolve the very
  separation this deferral protects (they would be constants anyway).
  **Scope:** the trigger gates *unified* retrieval only —
  `sdd/06_SPEC/SPEC-05_kb_compatibility.yaml:59` already specs a
  knowledge-only `kb_context(scope, query)` route needing just (1).
- **`engramory knowledge ingest` CLI binding — PROMOTED to near-term
  (PLAN-004 Phase 7).** `ADR-10:71` (Accepted) mandates the CLI expose
  the full SPEC-01 tool set *including* knowledge ingest;
  `_build_parser` registers `init`/`memory`/`profile`/`status` only
  (`cli.py:262-305`). **Decided: fix it now** — it is a non-conformance
  with an accepted ADR (not a missing feature), it is small (a subparser
  over the already-authorized `access/surface.py:223`), and it is the
  prerequisite that makes the deferral above coherent: with no writer
  face, `kb_sections` is empty, so the read leg would serve nothing and
  its fusion weights could not be tuned against real data.
- **SPEC-07 invocation widening — DECLINED (revisit only on recurrence).**
  Five doc sites independently drifted to a post-subcommand `--json`,
  which is evidence the normative global-first form (`SPEC-07:46`) is the
  unnatural one for agents. Accepting both positions is feasible —
  verified empirically: a shared parent parser needs `add_help=False`
  (else construction raises on `-h/--help`) and
  `default=argparse.SUPPRESS` (else a leaf silently overwrites a
  root-supplied `--json` with `False`, breaking the smoke script and the
  whole CLI suite). **Decided: do not widen.** The docs-conformance test
  in PLAN-004 PR 1 prevents recurrence at zero risk and zero contract
  churn; widening costs a normative amendment for an ergonomic gain.
  Revisit only if drift recurs *after* that test lands — that would be
  evidence the test cannot hold it.

**Discovered.** 2026-07-19 (PLAN-004 Pass 2 independent review).
**Decided.** 2026-07-19 (founder; PLAN-004 Pass 4).

## 11. 🔴 `call / ai-review` fails — reviewer resolves to `codex`, no `OPENAI_API_KEY`

**Status:** open, founder-gated. First observed 2026-07-19 on PR #50.
Blocks the ai-review gate on **every** PR in this repo until resolved.

**Symptom** (run 29696493943, reviewer step):

```text
ERROR codex_api::endpoint::responses_websocket: failed to connect to
websocket: HTTP error: 401 Unauthorized, url: wss://api.openai.com/v1/responses
ERROR: unexpected status 401 Unauthorized: Missing bearer or basic
authentication in header, url: https://api.openai.com/v1/responses
codex rc=1
```

Job env showed `REVIEWER: codex`, `APP_KEY_PRESENT: 1` — so the reviewer
App credentials are fine; the *engine* auth is not.

**Root cause.** The reviewer engine is **config-driven**: this repo's
`.github/workflows/ai-review.yml` leaves `reviewer:` unset, so the
reusable reads `.reviewer` from the trust-config repo
(`vladm3105/aidoc-flow-operations@main` → `.github/ai-review/config.json`).
That config resolves to `codex`, which requires an `OPENAI_API_KEY` repo
secret. engramory does not have one.

**Doc drift this exposes.** `CLAUDE.md` § Unified CI states "Reviewer:
`claude` (subscription-auth via `CLAUDE_CODE_OAUTH_TOKEN`)" — no longer
true. Folded into PLAN-004 Phase 3, which already rewrites that block.

**Resolution options:**

| Option | Effort | Notes |
| --- | --- | --- |
| **A. Set `OPENAI_API_KEY` on engramory** | Small | Matches the central config as-is. Founder-only (secret). |
| **B. Pin `reviewer: claude` in this repo's `ai-review.yml`** | Small | The workflow documents this override explicitly. Needs `CLAUDE_CODE_OAUTH_TOKEN` or `ANTHROPIC_API_KEY` present. Keeps engramory on the engine CLAUDE.md already claims. |
| **C. Change `.reviewer` centrally in `aidoc-flow-operations`** | Medium | Cross-repo write — 🔴 per the autonomy tiers; goes through `ops/inbox`, not in-session. Affects every consumer, not just this repo. |

**Recommendation:** B, then reconcile CLAUDE.md — it keeps this repo on
the engine its own governance doc already names, and it is a local
override the canon workflow explicitly supports. A is fine if the
founder wants engramory to follow the central codex default instead.

**Note.** `main` is not branch-protected (verified 2026-07-19: `gh api
.../branches/main/protection` → 404), so ai-review is **not a required
check** — `mergeStateStatus` reads UNSTABLE, not BLOCKED. Merges are
therefore possible while this is open, but per the auto-merge carve-out
(red CI) they need explicit founder OK rather than the standing
authorization.

**Discovered.** 2026-07-19 (PR #50 — PLAN-004).
