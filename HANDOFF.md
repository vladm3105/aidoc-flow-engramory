# HANDOFF — engramory

Live cross-session resume point for the engramory repo — the aidoc-flow
workspace's memory plane (durable substrate for AI-agent memory that
other aidoc-flow projects consume as a subscriber). Read at session
start; refresh at milestones and before context compaction.

## Current state (2026-07-08)

**PLAN-003 Wave 3b adoption** — first substantive workspace-governance
adoption on this repo. Prior state: `CHANGELOG.md` + a small
`CLAUDE.md` + `sdd/`-based engineering lifecycle; no `HANDOFF.md` /
`DECISIONS.md` at repo root. Wave 3b creates both + adds `## What this
repo is` section + consolidates dual ROADMAP (delete older
`docs/ROADMAP.md`; keep `roadmap/ROADMAP.md` as canonical) + cleans
stray `tmp/TODO.md` per memory rule. 8-tracked-surface bundle
(expanded from initial 5 after pre-push review surfaced 4 dead refs
to the deleted `docs/ROADMAP.md`; Phase 0-3 content migrated into
`roadmap/ROADMAP.md` rather than lost) above OPS-0061 Rule 1 ≤3
default, authorized under standing founder OK for Wave 3 rollout.

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
