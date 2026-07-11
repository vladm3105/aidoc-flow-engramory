# HANDOFF — engramory

Live cross-session resume point for the engramory repo — the aidoc-flow
workspace's memory plane (durable substrate for AI-agent memory that
other aidoc-flow projects consume as a subscriber). Read at session
start; refresh at milestones and before context compaction.

## Current state (2026-07-11, session wrap — PLAN-002 complete: agent-usable memory plane)

**PLAN-002 executed end-to-end** (record: `plans/PLAN-002_preprod-agent-readiness.md`,
verified-planning: 25 cited claims, 4 independent passes). Engramory is
now **pre-prod usable as memory by external AI agents** via the CLI face.
Six PRs merged 2026-07-11 (#33 core, #34 ADR-10 Accepted, #35 SPEC-01
faces amendment, #36 SPEC-07, #37 CLI, #39 agent docs + Skill):

- **SPEC-01 tool set complete**: memory_feedback / memory_forget /
  agent_profile_get + tenant-scoped get_memory + interim `reflect()`
  (episodes -> retrievable memories; SPEC-04 engine replaces the body).
- **ADR-10 (Accepted)**: CLI + reference Skill = dev/CI face over
  AccessSurface; MCP gateway = authenticated production face; dev-tier
  trust carve-out fenced by `require_dev_profile()` (ENGRAMORY_PROFILE=dev).
- **`engramory` CLI** (SPEC-07): init / memory add/search/feedback/forget/
  distill / profile get / status; exit codes 0/1/2/3; --json; config
  discovered upward from `.engramory/config.toml`.
- **Agent docs**: docs/INSTALL.md, AGENT-QUICKSTART.md, AGENT-INTEGRATION.md
  + skills/engramory-memory/SKILL.md + README agent section.
- **Verified**: 94 tests vs real Postgres; `make smoke` (full agent loop
  incl. fence) passing on the dev host (compose store, POSTGRES_HOST_PORT
  override — host 5432 is foreign-owned).

CI note: ai-review runs are frequently superseded-cancelled and read as
"fail" in the rollup — `gh run rerun --failed` clears them (happened on
every PR this session). Trust-fetch works App-natively since ci/v1.9.1
(TODO §1 resolved).

**Next engineering work (pick one):** (a) LLMPort dev adapter
(LiteLLM/Ollama) -> query embeddings + SPEC-03 rank fusion + reembed tool
(closes IPLAN-06); (b) **eval harness** (MVP-1 exit criterion — the CLI
makes it scriptable now); (c) MCP gateway binding + OIDC ActorContext
(ADR-10 production face; revisit trigger: CLI may become its thin
client); (d) Mem0 adoption behind MemoryPort (replaces interim reflect).
Deferred follow-ups: within-tenant write containment for retire/feedback
(gateway concern, security review note PR #33); agent_profiles is
untenanted (dev-tier assumption, revisit with gateway).

## Open threads

- **Substantive engineering standard evolution** — routed through
  `sdd/` (BRD → IPLAN artifacts + implementing ADRs at `sdd/05_ADR/` +
  conceptual ADRs at `docs/adr/`). Not tracked here; substantive
  engineering evolution is the `sdd/` lifecycle described in
  `AGENTS.md`.
- **PLAN-004 sub-plan 3** — logging-standard OTel-native family
  ratification in progress on `interlog`. Engramory subscribes to
  interlog's log stream once shipped. Track upstream progress on
  `interlog/plans/` + `aidoc-flow-operations/ops/DECISIONS.md`
  OPS-related entries.

## Next-session start-here

1. Read `AGENTS.md` for the engineering conventions (architecture,
   ports & adapters, config, testing) — the substantive delivery
   guide.
2. Read `CLAUDE.md ## Per-repo governance` for the workspace-adoption
   surfaces this file lives among.
3. Check `roadmap/ROADMAP.md` for the phase sequence.

## Recent decisions

- 2026-07-08 — `E-0001` (see `DECISIONS.md`): Wave 3b adoption of
  PLAN-003 canon; 2 governance files created + `## What this repo
  is` section added + dual-ROADMAP consolidation + stray `tmp/TODO.md`
  cleaned.

---

**Maintenance protocol:**

- Update `Current state` on every PR that changes what this repo is
  actively working on.
- Move resolved `Open threads` to `Recent decisions` (with `E-NNNN`
  entry ID if load-bearing) or to git commit history.
- Prune `Recent decisions` — entries older than 4 weeks belong only in
  `DECISIONS.md`.
