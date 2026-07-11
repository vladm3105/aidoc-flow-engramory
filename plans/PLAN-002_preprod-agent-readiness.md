# PLAN-002 — Pre-prod agent readiness: an agent-usable memory plane with full install + usage documentation

**Status:** ready — verified 2026-07-11 (4 review passes, final pass clean);
ADR-10 accepted per founder OK (gate G0 met); execution underway
**Date:** 2026-07-11
**Owner:** Engramory team
**Origin:** founder goal 2026-07-11 — "make engramory ready for pre-prod run,
to be used by other agents, including full documentation — how to use
engramory by other AI agents, how to install and other guidance."
**Decision foundation:** `sdd/05_ADR/ADR-10_agent_facing_packaging.yaml`
(Accepted) — layered thin faces over AccessSurface: CLI + reference Skill as
the dev/CI face now; MCP gateway remains the authenticated production face.

## Goal

At plan completion, an external AI agent (Claude Code, Codex, Gemini, or a CI
job) on a dev/pre-prod host can: install engramory, stand up the store, and
use the full SPEC-01 tool set (add / search / feedback / forget / profile /
ingest) through the `engramory` CLI, guided by a reference Skill and
vendor-neutral docs — with every call authorized, audited, and fail-closed
exactly as the existing AccessSurface enforces.

## Non-goals (explicitly out of scope)

- **MCP gateway binding + OIDC** — next cycle per ADR-10 (revisit trigger:
  gateway lands → CLI may become its thin client). The gateway stub and
  SPEC-01 tool contract are untouched except the wording amendment below.
- **Mem0 / MemoryPort engine adoption, SPEC-03 rank fusion, LLMPort adapter**
  — separate MVP-1 tracks (HANDOFF next-work items (a), (d)).
- **Eval harness** — an MVP-1 exit criterion built in parallel; this plan
  only makes it *possible* (scriptable CLI + feedback loop recorded).
- **Multi-tenant / shared pre-prod deployment** — the dev-tier trust
  carve-out (ADR-10) fences the CLI face to single-tenant dev; "pre-prod run"
  here means a single-tenant pilot on a dev-tier host.

## Gate G0 — governance prerequisites

1. **ADR-10 acceptance** (founder): the dev-tier trust carve-out is the
   load-bearing permission for phases 2–4. Until accepted, phases 2–4 do not
   start; phase 1 (SPEC-01 tool completion) is decision-independent and may
   proceed. **Sequencing:** acceptance is the founder's in-session/verbal OK
   on this plan + ADR-10; PR (ii) below then *records* it (flips ADR-10 to
   Accepted; the SPEC-01 amendment and SPEC-07 follow in PRs (iii)/(iv)).
   The gate is the OK, not any PR landing — the recording PRs are
   themselves plan work, so gating on them would be circular.
2. Governance PR discipline (≤3 doc surfaces per PR) forces the governance
   work into **three sequential PRs** — see the Phase-4 sequencing: (ii)
   ADR-10 flip + ADR-00 index (2 surfaces), (iii) SPEC-01 amendment +
   `mcp/server.py` docstring + `AGENTS.md` "exposed over MCP" wording
   (3 surfaces), (iv) SPEC-07 + SPEC-00 index row (2 surfaces).
   Adversarial self-review before every push; OPS-0065 multi-agent review
   on every PR.

## Phase 1 — Complete the SPEC-01 tool set (core, no new trust surface)

AccessSurface today implements only `memory_search`, `memory_add`,
`knowledge_ingest` (claims 1). SPEC-01 also contracts `memory_feedback`,
`memory_forget`, `agent_profile_get` (claims 3–4). The schema is already
there: `memory_retrievals.feedback` with the exact outcome CHECK (claim 5)
and the `agent_profiles` table (claim 6) + `AgentProfile` model (claim 7).

1. **`Repository.record_feedback(retrieval_id, outcome, *, tenant_id)`** —
   update `memory_retrievals.feedback` + `feedback_at`; reject outcomes
   outside `useful | not_useful | harmful` at the boundary; tenant-guarded
   WHERE (possible because `memory_retrievals` carries `tenant_id`,
   claim 22). Then **`AccessSurface.memory_feedback`** per the SPEC-01 signature
   (claim 3-adjacent, SPEC-01 lists it in the mcp/server.py registry too),
   with the standard `_decide` authorize→audit gate.
2. **Tenant-scope `Repository.get_memory`** (the recorded IPLAN-01 follow-up,
   claim 9/16): add a `tenant_id` parameter and WHERE-guard before anything
   builds on it. Then **`Repository.retire_memory`** (set `valid_to` +
   `status`, provenance retained — soft, per SPEC-01 "end-date + mark
   superseded") and **`AccessSurface.memory_forget(ctx, memory_id, reason)`**
   (claim 3), audited with the reason.
3. **`Repository.get_agent_profile` / `upsert_agent_profile`** over
   `agent_profiles` (claim 6) hydrating the existing `AgentProfile` model
   (claim 7); **`AccessSurface.agent_profile_get(ctx)`** (claim 4) returns
   the caller's own profile only (`ctx.agent_id`), never another agent's.
   Stated assumption: `agent_profiles` is keyed by `agent_id` alone — no
   `tenant_id` column — so the ADR-07 tenant wall does not apply to this
   path; acceptable only under the single-tenant dev tier and revisited
   with the gateway. Missing-profile semantics: `agent_profile_get` on a
   fresh store returns a default `AgentProfile(agent_id=ctx.agent_id)`
   (no error); `engramory init` (Phase 2) upserts the profile row so the
   smoke path exercises a real read.
   New tool calls pass `authorize()` because `memory_feedback`,
   `memory_forget`, and `agent_profile_get` are already in `KNOWN_ACTIONS`
   (claim 21) — no authz-table change needed.
4. **Minimal reflection pass (review-pass-2 finding: without it, nothing an
   agent adds is ever retrievable).** `memory_add` writes an `Episode`
   (episodes table) while `memory_search` reads only distilled rows in
   `memories` (claim 24) — and the real distillation engine (Mem0 behind
   MemoryPort, SPEC-04) is out of scope. Bridge: `workers/distillation.py`
   (today a docstring-only stub, claim 25) gains an **interim** `reflect()`
   pass — project not-yet-projected episodes into `Memory` rows
   (`content_raw` = episode content, summary = content, scope `agent`,
   `type="episodic"`, an explicit placeholder
   `embedding_model` (e.g. `"none/interim-reflect"` — the Memory
   constructor requires a non-empty value until the LLMPort adapter lands),
   default confidence, `provenance` carrying the `episode_id` for
   idempotency). Explicitly marked interim: the adopted
   SPEC-04 engine replaces its body; its signature and the CLI command that
   invokes it (Phase 2) survive. This is worker-plane code (allowed to use
   Repository directly); it is NOT a SPEC-01 agent tool and adds nothing to
   AccessSurface.
5. **Tests** in the SPEC-01 tdd_contracts locations: extend
   `tests/integration/test_access_surface.py` + unit coverage; all tiers run
   against the ephemeral-Postgres fixture (claim 14). NOTE (dev host): port
   5432 is occupied by an unrelated service — always the fixture, never the
   compose port, for tests.

Exit: `make lint typecheck test` green; all seven SPEC-01 registry tools
callable in-process.

## Phase 2 — The `engramory` CLI + library face (ADR-10, blocked on G0)

0. **Unblock the store on this host (load-bearing, review finding 1):**
   `docker-compose.yml` hardcodes `ports: ["5432:5432"]` (claim 23) and the
   dev host's 5432 is owned by an unrelated service — `make up` cannot bind,
   and a CLI defaulting to `localhost:5432` would silently talk to the
   *foreign* service. Parameterize the published port:
   `"${POSTGRES_HOST_PORT:-5432}:5432"` in compose, `.env` consumption via
   the existing Makefile include, and `engramory init` + `make smoke`
   consume `POSTGRES_HOST_PORT`. No such knob exists today — this task
   creates it; INSTALL.md then documents it (Phase 3.1).
1. **Packaging:** add `[project.scripts] engramory = "engramory.cli:main"`
   and a `src/engramory/__main__.py` alias (`python -m engramory`) — today
   pyproject defines no console entry point (claim 11) and no
   `src/engramory/cli.py` / `__main__.py` exists to collide.
2. **Commands** (thin bindings; no logic beyond ActorContext construction,
   arg parsing, error mapping — ADR-10 separation rule):
   `init` (scaffold `.engramory/config.toml`: agent_id, project_id,
   tenant_id, scopes, dsn — and upsert the caller's agent-profile row, the
   behavior Phases 1.3 and 4.1 rely on) · `memory add` · `memory search` ·
   `memory feedback` · `memory forget` · `memory distill` (ops command —
   runs the interim `reflect()` worker in-process; dev-tier only; not a
   SPEC-01 agent tool) · `profile get` · `kb ingest` · `status` (store
   reachability + counts).
3. **Conventions** (adopted from interlog's CLI-SPEC, claim 18 — note that
   doc is itself DRAFT/non-normative; SPEC-07 pins the codes normatively
   rather than by reference): global `--json`
   (one machine-readable object on stdout, diagnostics to stderr) and exit
   codes **0** success · **1** fail-closed reject (`AuthzError`,
   `GovernedWriteRejected`, validation) · **2** usage/config/environment ·
   **3** retryable (`StoreUnavailable`). Async bridge via `asyncio.run`.
4. **Dev-tier fence** (ADR-10 carve-out, claim 17), implemented at BOTH
   layers ADR-10 names: (a) the **factory** gains the guard ADR-10
   specifies ("the adapter factory refuses the CLI face outside
   profile=dev") — today it only rejects gcp/azure with
   `NotImplementedError` (claim 10); (b) the **CLI** resolves the profile
   the same way (`ENGRAMORY_PROFILE`, default `dev`) and refuses to
   construct ActorContext unless it is `dev` — exit 2 with a message naming
   ADR-10. Both fences asserted in unit tests.
5. **CLI-face spec:** author `sdd/06_SPEC/SPEC-07_cli_face.yaml` (next free
   number; SPEC-01..06 exist, claim 20) — commands, `--json` result shapes,
   exit-code table (pinned normatively), config discovery, the fence — AND
   its registry row in `sdd/06_SPEC/SPEC-00_index.md` (repo convention:
   artifact + index in the same change). SPEC-07 also explicitly classifies
   `memory distill` and `status` as **dev-tier ops commands outside
   ADR-10's agent-tool face contract** (they are not thin bindings over
   AccessSurface — distill runs the worker, status reads counts — so the
   "faces add no logic / never touch Repository" rule applies to the seven
   SPEC-01 agent tools, not to these two). Contract-first: SPEC-07 lands with
   (or before) the code PR.

Exit: `pip install -e . && engramory init && engramory memory add` →
`engramory memory distill` → `engramory memory search` returns the added
fact as a hit, against `make up && make migrate` (claims 12–13) with
`POSTGRES_HOST_PORT` set to a free port (task 2.0) on hosts where 5432 is
taken. (The distill step is required: add writes episodes, search reads
distilled memories — Phase 1.4.)

## Phase 3 — Documentation + reference Skill (the "full documentation" goal)

Modeled on interlog's doc kit (quickstart / integration / skill), adapted:

1. **`docs/INSTALL.md`** — prerequisites (Python ≥3.12, Docker), `make up`
   (compose: pgvector/pg16, claim 13 — document the `POSTGRES_HOST_PORT`
   override **created in task 2.0** for hosts where 5432 is taken),
   `make migrate` (claim 12), `pip install -e .`, `engramory init`, smoke
   check (`engramory status`).
2. **`docs/AGENT-QUICKSTART.md`** — three tracks: (A) agent-in-terminal CLI
   walk to a first remembered + retrieved memory (add → distill → search →
   feedback, matching the Phase-2 exit), (B) CI/scripted usage, (C)
   Skill-over-CLI. States the dev-tier fence and the "what is worth
   remembering" curation rule up front.
3. **`docs/AGENT-INTEGRATION.md`** — the interlog three-part pattern
   (trigger + discipline + pointer to `--help`): per-vendor table (Claude
   Skill / `AGENTS.md` / `GEMINI.md` / copilot-instructions) + a portable
   plain-text snippet equivalent to the Skill.
4. **`skills/engramory-memory/SKILL.md`** — the reference Skill: when to
   `memory add` (durable facts, not transcripts), when to `memory search`
   (session start, before repeating work), always `memory feedback` after
   using a hit (drives SPEC-04 confidence), scope discipline, never store
   secrets/personal data.
5. **`README.md`** — add the "use it as an agent" section linking 1–4.
6. **SPEC-01 wording amendment** (ADR-10 downstream): "single MCP-exposed
   surface" (claim 19) → "single access surface (AccessSurface) with thin
   transport faces; the MCP gateway is the authenticated production face" +
   the dev-tier carve-out note in the trust-model section; keep
   `mcp/server.py` docstring in sync.

Docs follow the global language rules (objective, no promotional claims).

## Phase 4 — Pre-prod run verification + repo currency

1. **Smoke path as a make target** (`make smoke`): compose up (honoring
   `POSTGRES_HOST_PORT`) → migrate → `engramory init` (upserts the agent
   profile) → `profile get` → add → **distill** → search (hit returned with
   `retrieval_id`) → feedback → forget → status — asserting exit codes;
   this is the `superpowers:verification-before-completion` evidence for
   the goal.
2. **CHANGELOG.md** entry; **HANDOFF.md** current-state refresh; **TODO.md**
   items closed/added; **roadmap/ROADMAP.md** MVP-1 note: agent access lands
   CLI-first per ADR-10, `over MCP` target (claim 15) unchanged for the
   production face.
3. PRs sequenced per governance — the governance work is **split into
   sequential ≤3-surface PRs** (CLAUDE.md Rule 1; review-pass-2 finding):
   (i) Phase-1 core PR (code + tests);
   (ii) ADR-10 flip to Accepted + ADR-00 index status (2 surfaces;
   governance tier — never auto-merge);
   (iii) SPEC-01 amendment + `mcp/server.py` docstring + `AGENTS.md`
   wording (3 surfaces; governance tier — never auto-merge);
   (iv) SPEC-07 + SPEC-00 index row (2 surfaces; spec tier — never
   auto-merge);
   (v) CLI code PR; (vi) docs + skill PR; (vii) currency PR.
   OPS-0065 multi-agent review before every push; OPS-0069 literal audit
   phrase in commit bodies.

## Risks

| Risk | Mitigation |
| --- | --- |
| ADR-10 not accepted | Phase 1 still lands (SPEC-01 completion is unconditional); phases 2–4 re-plan |
| CLI face escapes the dev fence | Factory-profile fence + unit test + fence stated in SPEC-07, INSTALL, Skill |
| Host port 5432 conflict breaks `make up`/smoke (dev host: 5432 owned by a foreign service — silent wrong-DB risk) | Task 2.0 parameterizes the published port (`POSTGRES_HOST_PORT`); init/smoke consume it; INSTALL documents it; tests always use the ephemeral fixture |
| Doc drift between CLI and docs | Docs never restate flags (interlog rule): point at `--help` + SPEC-07 |

## Claim ledger

| # | Claim | Symbol | Citation |
| --- | --- | --- | --- |
| 1 | AccessSurface implements only memory_search / memory_add / knowledge_ingest today | `async def memory_search` | src/engramory/access/surface.py:99 |
| 2 | MCP gateway is an unimplemented Phase-0 stub | `Status: stub` | src/engramory/mcp/server.py:15 |
| 3 | SPEC-01 contracts memory_forget(ctx, memory_id, reason) -> None | `async def memory_forget` | sdd/06_SPEC/SPEC-01_access_surface.yaml:62 |
| 4 | SPEC-01 contracts agent_profile_get(ctx) -> AgentProfile | `async def agent_profile_get` | sdd/06_SPEC/SPEC-01_access_surface.yaml:67 |
| 5 | memory_retrievals.feedback CHECK admits exactly useful/not_useful/harmful | `feedback     TEXT CHECK` | db/migrations/0003_reconcile_contracts.sql:66 |
| 6 | agent_profiles table exists in the schema | `CREATE TABLE IF NOT EXISTS agent_profiles` | db/migrations/0001_init_memory.sql:9 |
| 7 | AgentProfile model exists (agent_id, display_name, standing_preferences) | `class AgentProfile` | src/engramory/core/models.py:162 |
| 8 | log_retrievals returns retrieval_ids in memory_ids order for memory_feedback | `async def log_retrievals` | src/engramory/core/repository.py:309 |
| 9 | Repository.get_memory takes memory_id only — no tenant guard yet | `async def get_memory(` | src/engramory/core/repository.py:170 |
| 10 | Adapter profile resolves from ENGRAMORY_PROFILE, defaulting to dev | `def adapters_for_profile` | src/engramory/adapters/factory.py:30 |
| 11 | pyproject defines no [project.scripts] console entry point (sections: project, optional-dependencies, urls, build) | `[project.optional-dependencies]` | pyproject.toml:13 |
| 12 | make migrate applies db/migrations/*.sql in order via compose exec | `migrate:` | Makefile:26 |
| 13 | Dev store is compose pgvector/pgvector:pg16 publishing 5432 | `pgvector/pgvector:pg16` | docker-compose.yml:7 |
| 14 | Tests use ENGRAMORY_TEST_DSN or an ephemeral pgvector container fixture | `def pg_dsn` | tests/conftest.py:59 |
| 15 | ROADMAP MVP-1 targets memory_add/memory_search over MCP; eval harness + feedback loop are exit criteria | `**Next (MVP-1):**` | roadmap/ROADMAP.md:23 |
| 16 | HANDOFF names feedback/forget/profile tools + tenant-scoping get_memory as recorded next work | `memory_feedback/memory_forget/` | HANDOFF.md:36 |
| 17 | ADR-10 permits client-constructed ActorContext only behind the dev-tier fence | `DEV-TIER TRUST CARVE-OUT` | sdd/05_ADR/ADR-10_agent_facing_packaging.yaml:73 |
| 18 | Interlog CLI exit-code contract is 0/1/2/3 (success / reject / usage / retryable) | `| \`0\` | success |` | docs/standards/CLI-SPEC.md:29 (repo: interlog — resolve with `--root /opt/data/aidoc-flow/interlog`) |
| 19 | SPEC-01 currently reads "The single MCP-exposed surface" | `The single MCP-exposed surface` | sdd/06_SPEC/SPEC-01_access_surface.yaml:26 |
| 20 | SPEC numbering occupied through SPEC-06; SPEC-07 is free | `SPEC-06_ports_portability.yaml` | sdd/06_SPEC/SPEC-00_index.md:29 |
| 21 | memory_feedback / memory_forget / agent_profile_get are already in the authorize() action set | `KNOWN_ACTIONS` | src/engramory/access/authz.py:14 |
| 22 | memory_retrievals carries tenant_id (enables tenant-guarded record_feedback) | `tenant_id    TEXT NOT NULL` | db/migrations/0003_reconcile_contracts.sql:62 |
| 23 | Compose publishes the store as hardcoded 5432:5432 — no override knob exists today | `ports: ["5432:5432"]` | docker-compose.yml:12 |
| 24 | memory_add persists an Episode (episodes table), while memory_search reads distilled rows from memories — no projection between them exists | `add_episode` | src/engramory/access/surface.py:182 |
| 25 | The distillation worker is a docstring-only stub (no reflect/consolidate code) | `Distillation worker` | src/engramory/workers/distillation.py:1 |

## Review log

### Pass 1 - 2026-07-11 - independent

Fresh-context subagent (general-purpose) reviewed the draft against source in
engramory + interlog. All 20 ledger rows verified true (substance, not just
line existence). Findings: **1 load-bearing** — the compose published port is
hardcoded `5432:5432` while the dev host's 5432 is foreign-owned, so the
Phase-2 exit and Phase-4 smoke were unexecutable and the "documented
override" referenced a nonexistent knob (fixed: new task 2.0 creates
`POSTGRES_HOST_PORT`; exit criteria, INSTALL, smoke, and risk table updated).
**6 minor** — missing favorable ledger facts (KNOWN_ACTIONS, retrievals
tenant_id → claims 21–22), agent_profiles untenanted + missing-profile
semantics unstated (now stated in Phase 1.3 + smoke), G0/PR-(ii) circularity
(G0 now defined as the verbal OK), SPEC-00 index row unscheduled (added to
Phase 2.5), interlog CLI-SPEC is DRAFT (SPEC-07 pins codes normatively),
CLAUDE.md/AGENTS.md touch unenumerated (AGENTS.md wording added to the
amendment PR scope). All folded.

### Pass 2 - 2026-07-11 - independent

Second fresh-context subagent, post-fold. All 23 (then-current) ledger rows
verified true. Findings: **2 load-bearing** — (1) PR (ii) bundled 6
governance surfaces, violating the repo's own ≤3-surface Rule 1 (fixed:
governance work split into three sequential PRs (ii)/(iii)/(iv); G0 item 2
rewritten); (2) the smoke/exit/quickstart add→search round-trip was
unexecutable — `memory_add` writes episodes, `memory_search` reads only
distilled memories, and no projection existed or was in scope (fixed: new
Phase 1.4 interim `reflect()` pass in the stub worker + `memory distill`
CLI command; exit criteria, quickstart, and smoke now add → distill →
search; ledger claims 24–25 added). **2 minor** — `init`'s profile-upsert
behavior stated where the CLI is defined (folded into Phase 2.2); the
dev-tier fence now implemented at both layers ADR-10 names (factory guard +
CLI check, Phase 2.4). All folded.

### Pass 3 - 2026-07-11 - independent

Third fresh-context subagent. All 25 ledger rows verified line-accurate and
substantively true; confirmed the add→distill→search flow is executable
against `_visibility_sql` (an interim-reflect Memory row is returned by the
recency path); PR surface counts and never-auto-merge labels match CLAUDE.md.
Findings: **1 load-bearing** — G0 item 1 still carried stale pre-split text
("amends SPEC-01" / "SPEC-07 rides in the same PR") contradicting the
(ii)/(iii)/(iv) split (fixed: parentheticals rewritten). **2 minor** —
Phase 1.4's Memory recipe omitted required `type` + non-empty
`embedding_model` constructor fields (fixed: interim values named);
`memory distill`/`status` sat in unstated tension with ADR-10's face rule
(fixed: SPEC-07 task now classifies them as dev-tier ops commands outside
the agent-tool face contract). All folded.

### Pass 4 - 2026-07-11 - independent

Fourth fresh-context subagent. All 25 ledger rows verified; Phase 1.4 recipe
checked against `Memory.__post_init__` + schema CHECKs (`type="episodic"`
passes both; placeholder embedding_model satisfies the constructor; interim
rows are returned by `_visibility_sql` on the recency path — the
add→distill→search exit criterion is executable); PR surface counts (2/3/2),
never-auto-merge labels, G0 text, and review-log arithmetic all consistent.
Findings: **zero load-bearing**; 1 minor — the Phase 1.4 recipe omitted
`content_raw` from the named required constructor fields (folded).

**Result:** ready
