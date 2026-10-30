---
paths:
  - "**/PLAN-*.md"
  - "**/IPLAN-*.md"
  - "**/plans/**"
  - "**/work_plans/**"
---

# Plan status governance

**The status of an IPLAN or a working plan MUST reflect its actual state.** A status is a claim
about reality, not a label set once at authoring time.

**When to change it** — in the same session and the same change as the state change, never deferred
to a later cleanup pass:

| Event | Status |
|-------|--------|
| Authored, not started | `Draft` |
| Execution starts — first task, file or session of the plan is worked | `In Progress` |
| Every declared item is done AND verified | `Completed` |
| Work stopped and will not resume | `ABANDONED` (state the reason) |

**Rules**:
- Update the status at its **source**. Where the plan document is generated, edit the generator's
  authoring data and regenerate — a hand-edit to a generated document is destroyed by the next run.
  Where the status is **derived** from progress fields (files done, sessions done), update those
  fields as work lands rather than only at the end; the derived status then tracks reality on its own.
- The plan document and its registry/index row MUST agree, and both move in the same change.
- Record the transition (`from` → `to`, date, reason) wherever the index keeps status history.
- Do not mark `Completed` on unverified work. "Verified" means the repo's own gates and test
  commands were run and their output read.
- A stale status is a defect of the same class as a wrong count: correct it, and say what it was.

**Where an issue tracks the work, the issue state is the status and there is only one of them.**

| Plan status | Issue |
|---|---|
| `Draft` | open, no `status:in-progress` label |
| `In Progress` | open + `status:in-progress` (or assigned) |
| `Completed` | **closed by the merging PR** (`Closes #N`), never by hand |
| `ABANDONED` | closed as **not planned**, with the reason in a final comment |

- **Do not maintain a status field in a document AND an issue.** Pick the one the
  work is tracked in and let the other link to it. Two records of one status is
  the same defect as two records of one count, and it fails the same way.
- **A generated plan is the exception and keeps its derived status** — hand-editing
  it is destroyed by the next generator run, so the generator's authoring data
  stays the source and the issue links to the plan rather than restating it.

## When a plan is a DOCUMENT rather than an issue

**A `plans/` file is warranted only when the work needs something an issue
cannot hold:** a **gated Claim ledger** with `file:line` citations, or a design
that a review pass has to verify. **Everything else is an issue** — a
`work_plans/` file for a bug fix is a queue entry in the wrong place, and
`NEXT_TASKS.md` is superseded outright by the tracker's open list.

**The test is not size, it is whether a check reads it.** One repo's 22 plans
carry **441 line-citations into code** that a citation gate verifies; that is
why those stay files. A plan with no ledger and no review residual is an issue
that grew a filename.

Naming, where a file *is* warranted: `plans/PLAN-NNN_{slug}.md` for feature and
roadmap work; `work_plans/` for the disposable remainder.

**Paths are per-repo.** Read them from the repo's own governance table (its
`AGENTS.md`) — a global rule caching another workspace's paths bakes in
staleness that nothing here can detect. In aidoc-flow, the umbrella `AGENTS.md`'s
per-surface table declares plans **"Not adopted"** at umbrella level; the
submodule's own declaration is canonical.
