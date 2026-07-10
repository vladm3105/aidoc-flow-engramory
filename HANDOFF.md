# HANDOFF — engramory

Live cross-session resume point for the engramory repo — the aidoc-flow
workspace's memory plane (durable substrate for AI-agent memory that
other aidoc-flow projects consume as a subscriber). Read at session
start; refresh at milestones and before context compaction.

## Current state (2026-07-09, session wrap — MVP-1 implementation underway)

**PLAN-001 remediation COMPLETE** (#13–#21; record at
`plans/PLAN-001_pre-first-run-remediation.md`) and **MVP-1
implementation is 3 IPLANs deep**, all merged 2026-07-09:

- **IPLAN-02 COMPLETE** (#22 contracts, #23 repository): typed data
  contracts + Postgres repository — idempotent episodes, soft-supersede
  with tenant-guarded writes, ADR-07 visibility-ladder retrieval,
  active-only default. First runtime dep: `psycopg` (spine per SPEC-06).
- **IPLAN-06 6/7** (#24): pgvector VectorPort dev adapter + factory +
  no-vendor-imports architecture guard; `get_memories(query_vec)` does
  real cosine similarity composed with visibility. Remaining:
  `reembed_and_reproject` (needs an LLMPort dev adapter).
- **IPLAN-01 COMPLETE** (#25): default-deny `authorize()`, AccessSurface
  (authorize→audit→execute, fail-closed incl. audit-store-down),
  governed `knowledge_ingest`, `memory_search` with MemoryHit
  {retrieval_id, token_count}, PRD token budget (2000) default, scope
  containment (ungranted ladder levels never leak — review-caught).

49 tests across four tiers vs real Postgres (ephemeral-container
fixture at `tests/conftest.py`; CI skips container tests until a
`services:` postgres + ENGRAMORY_TEST_DSN is added to ci.yml);
`mypy --strict` + ruff clean. Every PR carried a multi-agent OPS-0065
review; 4 security holes were folded pre-push in #25 alone.

**Next engineering work (pick one):** (a) LLMPort dev adapter
(LiteLLM/Ollama) → query embeddings + SPEC-03 rank fusion + reembed
tool (closes IPLAN-06); (b) memory_feedback/memory_forget/
agent_profile_get tools (SPEC-04 confidence rule; scope
`Repository.get_memory` to tenant first — see IPLAN-01 handoff
follow-ups); (c) MCP transport binding over AccessSurface; (d) eval
harness (MVP-1 exit criterion per ROADMAP).

**Still open (founder-gated):** `AI_REVIEW_TOKEN` secret (`TODO.md` §1)
and F5 App/branch-protection (`TODO.md` §2; `APP_REVIEWER_1_*` secrets
already added 2026-07-09).

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
