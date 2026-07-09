# DECISIONS — engramory

Durable, ISO-stamped, **append-only** record of load-bearing decisions
for the engramory repo (the aidoc-flow workspace's memory plane).

**ID prefix:** `E-NNNN`. Never reuse a retired ID.

**Scope:** repo-level operational + workspace-standard-adoption
decisions. **NOT for** substantive engineering-standard evolution —
that lives in `sdd/` (BRD → IPLAN artifacts) + implementing ADRs at
`sdd/05_ADR/` + conceptual ADRs at `docs/adr/`, per the `sdd/`
lifecycle described in `AGENTS.md`.

## E-0001: PLAN-003 Wave 3b canon adoption (2026-07-08)

**Context**

`aidoc-flow-ci@ci/v1.6.0` shipped the PLAN-003 project-governance file
canon (see `aidoc-flow-ci#72` plan; `aidoc-flow-ci#73`/`#74`/`#75`/`#76`/`#77`/`#78`/`#79`,
`aidoc-flow-operations#217` = PR-V1..V4 + canon-template fix + parser
extensions + rubric fix + canonical-source authority disambiguation).
Workspace audit had this repo missing HANDOFF + DECISIONS files +
`## What this repo is` section; dual ROADMAP (`docs/ROADMAP.md`
1.4KB older + `roadmap/ROADMAP.md` 5.6KB newer); stray `tmp/TODO.md`
violating memory rule "Never in tmp/". Per PLAN-003 §5.4c engramory
row + §5.5 Wave 3: adopt in Wave 3b.

**Decision**

Create `HANDOFF.md` + `DECISIONS.md` at repo root; add `## What this
repo is` section to `CLAUDE.md`; consolidate dual ROADMAP by deleting
`docs/ROADMAP.md` and keeping `roadmap/ROADMAP.md` as canonical (per
PLAN-003 §5.5 Wave 3 recommendation option (a)); clean stray
`tmp/TODO.md`. Restructure `## Per-repo governance` table to canonical
6-required-row format + move `AGENTS.md` + `sdd/` + `sdd/05_ADR/` +
`docs/adr/` to additional-row surfaces. 8-tracked-surface bundle (post-fold; expanded from initial 5-surface plan after 4 dead refs to deleted `docs/ROADMAP.md` surfaced during pre-push review — Phase 0-3 content migrated into `roadmap/ROADMAP.md` + 2 README refs + 1 docs/README ref fixed) above
OPS-0061 Rule 1 ≤3 default authorized under standing founder OK for
Wave 3 rollout.

**Consequences**

- Governance drift on `CLAUDE.md#per-repo-governance` closes; `bash
  ../aidoc-flow-ci/install/apply-standards.sh --check` reports exit 0.
- This DECISIONS log seeds with this entry (`E-0001`); future entries
  append here + never rewrite history.
- Substantive engineering-standard evolution remains routed through
  the `sdd/` lifecycle (BRD → IPLAN + ADRs) described in `AGENTS.md`,
  landing in `sdd/*.md` (implementing at `sdd/05_ADR/` + conceptual
  at `docs/adr/` — the ADR split preserved).
- `docs/ROADMAP.md` deletion is intentional; `roadmap/ROADMAP.md`
  becomes single canonical roadmap.
- `tmp/TODO.md` deletion enforces the memory rule "Never in tmp/".

**Origin**

Founder direction 2026-07-08: continuing Wave 3 (iplan-runner +
engramory) per PLAN-003 §5.5 rollout. PLAN-003 §5.4c engramory row
scope + §5.5 Wave 3 dual-ROADMAP option (a).

---

<!-- Append new entries above this line; append-only. Never rewrite
history; if a decision is reversed, add a NEW entry citing the reversal
and update the superseded entry's "Consequences" section to reference
the reversal ID. -->
