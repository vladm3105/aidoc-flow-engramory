# HANDOFF — engramory

Live cross-session resume point for the engramory repo — the aidoc-flow
workspace's memory plane (durable substrate for AI-agent memory that
other aidoc-flow projects consume as a subscriber). Read at session
start; refresh at milestones and before context compaction.

## Current state (2026-07-09, post-PLAN-001)

**PLAN-001 pre-first-run remediation COMPLETE** — all 7 PRs (#13–#19)
merged to main 2026-07-09, plus the 3 stranded Dependabot PRs (#8–#10).
The 2026-07-09 five-agent review's contract gaps are closed: migration
0003 (content_hash, quarantine status, tenant wall, feedback/audit/
kb_sections), StoragePort semantics reconciled, learning-loop contracts
(feedback/forget/profile, RRF ranking, token budget, secrets-exclusion
trace), infra pinned, docs/governance refreshed. See
`plans/PLAN-001_pre-first-run-remediation.md` for the record. **Still
open (founder-gated):** `AI_REVIEW_TOKEN` secret for `call / trust`
(`TODO.md` §1) and the F5 App/branch-protection follow-up (`TODO.md`
§2). Next engineering work: MVP-1 vertical slice per IPLAN-02/06 and
`roadmap/ROADMAP.md`.

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
