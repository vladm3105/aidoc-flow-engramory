# Changelog

All notable changes to Engramory are documented here. Format: [Keep a Changelog](https://keepachangelog.com/); versioning: [SemVer](https://semver.org/).

## [Unreleased]

### Added — canon markdown-lint workflow (report-only) (2026-07-11)

Added `.github/workflows/markdown-lint.yml`, a thin caller of the aidoc-flow-ci
canon `markdown-lint.yml@ci/v1.9.4` reusable (markdownlint-cli2 via npm, no
blocked third-party action), plus a `.markdownlint.json` config. Deployed
**report-only** (`continue-on-error`): surfaces markdownlint annotations on PRs
without blocking merge; graduates to a blocking gate after a `--fix`
remediation pass.

### Added — canon secret-scan (gitleaks) workflow (2026-07-11)

Adopted the aidoc-flow-ci secret-scan gate (, gitleaks binary).

### Added — PLAN-002: agent-usable memory plane — CLI face, full SPEC-01 tool set, agent docs (2026-07-11)

Engramory is now usable as memory by external AI agents on a dev/pre-prod
host, per ADR-10 (accepted 2026-07-11) and PLAN-002 (verified plan: 25
cited claims, 4 independent review passes). Six PRs, every one
multi-agent-reviewed per OPS-0065; 94 tests green vs real Postgres;
`make smoke` verifies the full loop end-to-end on the compose store:

- **SPEC-01 tool set complete** (#33) — `memory_feedback` (tenant-guarded
  outcome stamping), `memory_forget` (soft retire, audited with caller
  reason), `agent_profile_get` (own profile only; default on fresh store);
  tenant-scoped `Repository.get_memory` (closes the IPLAN-01 follow-up);
  interim `reflect()` projection (episodes -> retrievable memories,
  idempotent by provenance episode_id) so agent-added facts are
  retrievable before the SPEC-04 engine lands.
- **ADR-10 accepted** (#34) — agent-facing packaging: CLI + reference
  Skill as the dev/CI face over AccessSurface; MCP gateway remains the
  authenticated production face; dev-tier trust carve-out (client-built
  ActorContext only under ENGRAMORY_PROFILE=dev).
- **SPEC-01 faces amendment** (#35) + **SPEC-07 CLI-face contract** (#36)
  — normative exit codes 0/1/2/3, --json shapes, config schema, fence.
- **The `engramory` CLI** (#37) — init / memory add/search/feedback/
  forget/distill / profile get / status; `python -m engramory` alias;
  dev-tier fence enforced via `require_dev_profile()`;
  `POSTGRES_HOST_PORT` compose override (dev hosts where 5432 is taken).
- **Agent documentation + reference Skill** (#39) — `docs/INSTALL.md`,
  `docs/AGENT-QUICKSTART.md` (add -> distill -> search -> feedback loop,
  3 tracks), `docs/AGENT-INTEGRATION.md` (per-vendor wiring),
  `skills/engramory-memory/SKILL.md`, README "Use it as an agent".
- **Pre-prod smoke** (#40) — `make smoke` (`scripts/smoke_preprod.sh`)
  drives the full loop through the real CLI and asserts SPEC-07 exit
  codes incl. the fence; verified passing 2026-07-11 on the dev host.

Backward compatibility: `Repository.get_memory` now requires a
`tenant_id` keyword (internal API, pre-1.0).

### Changed — re-pin aidoc-flow-ci callers to @ci/v1.9.1; adopt App-native ai-review trust-fetch (2026-07-11)

Re-pinned the four `vladm3105/aidoc-flow-ci` callers (ai-review, audit-trail,
auto-merge-ai-prs, composition) to `@ci/v1.9.1` via `install.sh --repin`
(version-only; also fixes the stale audit-trail `v1.6.0` pin). v1.9.1's ai-review
mints its trust-config read token from the reviewer App instead of a per-repo
`AI_REVIEW_TOKEN` PAT — fixing the `repository not found` trust-fetch failure with
no secret needed (App installed on `aidoc-flow-operations` with `contents: read`).

### Added — MVP-1 implementation: store, adapters, access surface (2026-07-09)

First working code of the memory plane — three IPLANs, TDD-first across
four test tiers against real Postgres (49 tests; `mypy --strict` + ruff
clean; every PR multi-agent-reviewed per OPS-0065):

- **IPLAN-02 complete** — `src/engramory/core/models.py` (typed
  contracts mirroring migrations 0001–0003; derived content_hash,
  read-only mapping fields) + `src/engramory/core/repository.py`
  (idempotent `add_episode` via `uq_episodes_idempotency`, transactional
  tenant-guarded soft-supersede, ADR-07 visibility-ladder retrieval,
  keyed `upsert_kb_section`, `StoreUnavailable` fail-closed wrapper).
  First runtime dependency: `psycopg[binary]` (spine per ADR-01/SPEC-06).
  (#22, #23)
- **IPLAN-06 6/7** — `PgVectorAdapter` (VectorPort over pgvector;
  embedding as rebuildable projection, cosine `<=>`, filter allowlist),
  adapter factory (`ENGRAMORY_PROFILE`), no-vendor-imports architecture
  guard (psycopg carve-out core-only), and similarity ranking live in
  `get_memories(query_vec)` composed with visibility. Remaining:
  `reembed_and_reproject` (needs LLMPort dev adapter). (#24)
- **IPLAN-01 complete** — `engramory.access`: pure default-deny
  `authorize()`, `AccessSurface` (authorize→audit→execute; fail-closed
  even when the audit store is down; governed `knowledge_ingest`
  requiring evidence; `memory_search` → `MemoryHit{retrieval_id,
  token_count}` with batched `memory_retrievals` logging, PRD
  token-budget default 2000, `k` clamp, scope containment). P1 security
  scenarios (cross-tenant deny+audit, fail-closed, scope containment)
  run as e2e/security tests. (#25)
- Test infrastructure: shared ephemeral-pgvector fixture
  (`tests/conftest.py`; `ENGRAMORY_TEST_DSN` override; CI skips
  container tests by design until ci.yml gains a `services:` postgres).

### Added / Changed — PLAN-001 pre-first-run remediation (2026-07-09)

Closes the contract gaps found by the 2026-07-09 five-agent pre-first-run
review. Plan + claim ledger + review log:
`plans/PLAN-001_pre-first-run-remediation.md` (all 7 PRs #13–#19 merged
2026-07-09; Dependabot #8–#10 merged the same day).

- **`db/migrations/0003_reconcile_contracts.sql`** (NEW) — episode
  idempotency (`content_hash` + unique index), memory lifecycle
  `status` (quarantine surface for EARS.01.03.b800), `tenant_id`
  NOT NULL + tenant-leading partial index (ADR-07), lexical `ts_lex`
  + GIN, feedback signals (`retrieval_count`, `last_retrieved_at`,
  `source_trust`), `memory_retrievals` + `audit_records` tables,
  `kb_sections` knowledge-core table (ADR-08). Verified idempotent
  ×2 on pgvector:pg16. (#14)
- **StoragePort reconciliation** — SPEC-06/SPEC-02/IPLAN-01/02 no
  longer describe the object-storage port as relational persistence;
  repositories are specified over the Postgres driver directly
  (ADR-01 spine). ADR-02 rationale corrected. (#15, #19)
- **Learning-loop contracts** — `MemoryPort.reflect` (renamed from
  `distill`) + `feedback`/`forget`/`get_profile`;
  `search(include_advisory, token_budget)`; MCP tools
  `memory_feedback`/`memory_forget`/`agent_profile_get`; SPEC-03
  concrete RRF ranking spec; SPEC-04 reflection triggers +
  confidence-update rule + secrets screen; PRD threshold
  `context_token_budget` = 2000; EARS.01.04.c300 (P1 secrets
  exclusion) traced to BDD.01.03.g010 + TDD.04.04.c100. (#16)
- **Infra determinism** — all compose images pinned (litellm off
  `main-latest` to `-stable`); `make` defaults to `help`; CODEOWNERS
  dead paths fixed; MinIO healthcheck kept (`mc ready local` verified
  working — review finding was a false positive). (#17)
- **Docs reconcile** — kb_sections cycle attribution (MVP-1/BRD-01),
  CORES↔SPEC-01 namespace rule, retracted "bounded store" claims,
  NEXUS review dead refs, MEMORY_CONCEPT_REVIEW status annotation,
  README doc-table rows. (#18)
- **Ops/governance** — CI trust-gap fix DECIDED (Option A,
  `AI_REVIEW_TOKEN`; TODO §1), F5 + Dependabot tracked (TODO §2/§3),
  CLAUDE.md CI state refreshed + `plans/` surface adopted, ADR-00
  preamble fixed. (#13, #19, #20)

### Changed — Wave 3b adoption of aidoc-flow-ci PLAN-003 governance-file canon (2026-07-08)

engramory adopts the PLAN-003 flexible-canonical (Option B) project-
governance file canon. Design in `aidoc-flow-ci#72` (plan draft);
shipment: `aidoc-flow-ci#73`/`#74`/`#75`/`#76`/`#77`/`#78`/`#79`,
`aidoc-flow-operations#217` = PR-V1/V2/V3/V4 + canon-template fix +
parser §N handling + rubric fix + canonical-source authority
disambiguation — all merged 2026-07-08.

Governance drift check (`bash ../aidoc-flow-ci/install/apply-standards.sh
--check`) — `CLAUDE.md#per-repo-governance` now reports OK (6/6 required
+ 4 additional + 0 errors).

- **`HANDOFF.md`** (NEW at repo root) — cross-session resume; seeded
  with Wave 3b state + active surfaces (substantive engineering via
  `sdd/`; PLAN-004 upstream ratification on interlog).
- **`DECISIONS.md`** (NEW at repo root) — append-only decision log for
  repo-level + workspace-standard-adoption decisions; seeded with
  `E-0001` (this Wave 3b adoption). ID prefix: `E-NNNN`. Distinct from
  substantive ADRs (`sdd/05_ADR/` + `docs/adr/`) which evolve through
  the framework CHG / GATE-SPEC process.
- **`CLAUDE.md`** — new `## What this repo is` section (currently
  absent per PLAN-003 §5.4c audit); `## Per-repo governance` table
  restructured to canonical 6-required-row format + 4 additional rows
  (`AGENTS.md`, `sdd/`, `sdd/05_ADR/`, `docs/adr/`).
- **`docs/ROADMAP.md`** (DELETED) — consolidated into
  `roadmap/ROADMAP.md` per PLAN-003 §5.5 Wave 3 dual-ROADMAP option
  (a). Content preserved: the Phase 0-3 engineering-view table +
  ARCHITECTURE.md pointer migrated into a new §"Engineering Phase
  view (Phase 0-3)" section in `roadmap/ROADMAP.md`; the dead
  `../docs/ROADMAP.md` cross-reference at roadmap/ROADMAP.md:3
  updated to describe the consolidation.
- **`roadmap/ROADMAP.md`** — added new §"Engineering Phase view
  (Phase 0-3)" section containing the migrated Phase table + pointer
  to `../docs/ARCHITECTURE.md`. Updated line-3 preamble from
  "engineering view is in ../docs/ROADMAP.md" to "single canonical
  roadmap — cycle scope + Phase view". Last-updated date bumped to
  2026-07-08.
- **`README.md`** — updated 2 cross-references: dropped the standalone
  `docs/ROADMAP.md` row (Phase view now folded into `roadmap/ROADMAP.md`);
  §Status pointer switched from `docs/ROADMAP.md` to
  `roadmap/ROADMAP.md § "Engineering Phase view"`.
- **`docs/README.md`** — updated the ROADMAP row to describe the
  consolidation (no more standalone ROADMAP.md in docs/; canonical
  roadmap at ../roadmap/ROADMAP.md).
- **`CHANGELOG.md`** — this entry.

Working-tree cleanup (not a diff surface — `tmp/` is gitignored):
- Removed untracked `tmp/TODO.md` + `tmp/SESSION_HANDOFF_2026-07-07.md`
  from disk to enforce the "Never in `tmp/`" memory rule. `tmp/`
  itself is preserved as the workspace scratch directory.

**8 tracked surfaces** (HANDOFF.md + DECISIONS.md + CLAUDE.md +
docs/ROADMAP.md deletion + roadmap/ROADMAP.md migration + README.md +
docs/README.md + this CHANGELOG entry). Above OPS-0061 Rule 1 ≤3
default. Bundle authorized under standing founder OK for Wave 3
rollout (per PLAN-003 §5.5 Wave sequencing + founder direction
2026-07-08 "continuing... Wave 3"). Expanded from initial 5 tracked +
1 untracked-cleanup after 2-agent review surfaced 4 dead refs to the
deleted `docs/ROADMAP.md` (3 pre-existing consumer refs + 1 in the
canonical roadmap itself) — Phase 0-3 content migrated rather than
lost; all dead refs cleared.

Multi-agent self-review per OPS-0065 (code-reviewer + documentation-specialist parallel dispatch): approved after 1 fold cycle addressing 1 CRITICAL (4 dead refs to deleted docs/ROADMAP.md — code-reviewer flagged; all 4 fixed by migrating Phase 0-3 content into roadmap/ROADMAP.md + updating README + docs/README consumer refs) + 3 HIGH (docs/ROADMAP.md supersession framing was wrong — Phase content wasn't duplicate, was complementary; content-migration path chosen; false "AGENTS.md CHG/GATE-SPEC" claim retracted across HANDOFF + DECISIONS + CLAUDE.md; surface count 6→8 corrected honestly; CHANGELOG TBD → filled) + 4 MEDIUM/LOW (charter/build-in-progress → Phase 0 dev foundation per README wording; tmp cleanup reframed as working-tree hygiene not diff surface; tmp/SESSION_HANDOFF also cleaned; CHANGELOG subsection order preserved — Added-then-Changed acceptable within Unreleased)

### Added
- **Wave 3 product-tier adoption of aidoc-flow-ci canon** (2026-07-08) —
  self-adopts the workspace-wide standards canon from
  `aidoc-flow-ci@ci/v1.6.0` per PLAN-002 §5.5 Wave 3 (product-code tier).
  9 file surfaces + this CHANGELOG (atomic canon-adoption bundle per
  PLAN-002 §5.5 explicit exemption to OPS-0061 Rule 1's ≤3-surface cap;
  same precedent as PR-U4 aidoc-flow-ci + PR #13 iplan-standard + PR #69
  iplan-runner):
  - `scripts/pre_push_check.sh` (NEW) — canon self-review script
    byte-identical to canon at `ci/v1.6.0`.
  - `.pre-commit-config.yaml` (NEW) — canon fragment verbatim with
    `# CANON:` marker.
  - `.github/CODEOWNERS` (NEW) — canon shape.
  - `.github/pull_request_template.md` (NEW) — canon PR template.
  - `.github/dependabot.yml` (NEW) — FULL canon (5 ecosystems; Dependabot
    silently skips missing manifests).
  - `.gitignore` (edit) — 8 canon baseline lines appended.
  - `.gitattributes` (NEW) — canon baseline.
  - `.github/workflows/audit-trail.yml` (NEW) — caller of `audit-trail-check.yml`
    reusable at `@ci/v1.6.0`; check-name = `call / verify`.
  - `.github/workflows/standards-drift.yml` (NEW) — weekly cron running
    `sync/check-standards-drift.sh --tier product` (script fetched from
    canon at runtime). Warning-only per canon §3.1b.
  - **Server-side follow-up (F5 blast-radius; not in this PR):** founder
    runs `bash install/apply-standards.sh --apply --repo vladm3105/aidoc-flow-engramory --tier product --ci-tag ci/v1.6.0 --yes` to add
    `call / verify` to branch-protection contexts + apply canon labels +
    repo-settings + actions-permissions + branch-protection-product.
  - **Origin:** `aidoc-flow-ci/plans/PLAN-002_workspace-standards-rollout.md`
    §5.5 Wave 3.
- **aidoc-flow CI standards adoption** (2026-07-06) — engramory joins the
  aidoc-flow workspace CI infrastructure:
  - `CLAUDE.md` populated with the aidoc-flow-standard governance sections
    (Per-repo governance / Unified CI / Governance PR discipline OPS-0061 /
    AI agent auto-merge default OPS-0062 / Multi-agent automated review
    OPS-0065+0067). `AGENTS.md` remains the engineering source-of-truth;
    both files apply.
  - `.github/workflows/ai-review.yml` (NEW) — thin caller for
    `vladm3105/aidoc-flow-ci@ci/v1.4.3`, reviewer=claude, runner=ubuntu-latest.
  - `.github/workflows/composition.yml` (NEW) — thin caller for
    `vladm3105/aidoc-flow-ci@ci/v1.3.0`.
  - `.github/workflows/auto-merge-ai-prs.yml` (NEW) — thin caller for
    `vladm3105/aidoc-flow-ci@ci/v1.5.1` (server-side enforcer for AI-opened
    PRs per OPS-0062).
  - **Dormant** until reviewer App is installed on this repo (F5 blast-radius
    prerequisite per aidoc-flow-operations CLAUDE.md § Unified CI). Follow-up
    PR on aidoc-flow-operations adds `vladm3105/aidoc-flow-engramory` to the
    `auto_merge.repos` allowlist.
  - Follows the sibling wire-up pattern (business PR #37 / iplanic PR #228 /
    iplan-runner PR #61) with engramory's public + ubuntu-latest topology.
- Project initiation: README, architecture, memory design, portability, roadmap, ADRs.
- Phase-0 dev scaffolding: docker-compose (Postgres+pgvector, Redis, MinIO, LiteLLM+Ollama, Neo4j, Keycloak), plus a MinIO bucket-init job and service healthchecks.
- Port interfaces (storage, vector, graph, cache, llm, secrets, events, memory) with method signatures; identity is a gateway concern, not a core port.
- Initial memory schema migration (episodes, memories, agent_profiles, consolidation_runs) with an `updated_at` trigger and a supersession index.
- Migration 0002: additive `domain` scope level and `domain_id` columns/indexes.
- ADR-07: scope-model decision (agent/project/domain/space ladder; `tenant_id` isolation).
- `docs/research/MEMORY_CONCEPT_REVIEW.md`: conceptual review of the agent-memory approach.
- `docs/STRATEGY.md`: build strategy & recommendation (build the plane, adopt the engine, lead with evaluation).
- ADR-08 + `docs/CORES.md`: single platform, two bounded cores (Memory and Knowledge) sharing the spine, with the boundary rules and revisit triggers.
- ADR-09: independent memory storage — Engramory owns its Postgres store; the iplan execution ledger is integrated as an **episode source** (execution-events → L1 episodes via `EventsPort`) with provenance cross-linking, not a storage backend.
- Dev tooling: ruff lint rules, pytest + mypy config, `py.typed`, CI workflow, and a test skeleton.

### Changed
- Scope vocabulary unified across schema/SPEC/docs: isolation column renamed `space_id` → `tenant_id`; the `shared` scope value is retired in favor of `space` (tenant-wide). See ADR-07.
- `make migrate` now applies **all** `db/migrations/*.sql` in order (was hard-coded to 0001) and loads `.env`.
- ADR homes clarified: `sdd/05_ADR/` is canonical/implementing; `docs/adr/` is conceptual/descriptive.
