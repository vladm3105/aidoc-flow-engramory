---
paths:
  - "**/CHANGELOG.md"
  - "**/CHANGELOG_*.md"
  - "**/ROADMAP.md"
  - "**/changelog/**"
  - "**/roadmap/**"
---

# Changelog and roadmap

**Scope rule**: Each repository maintains its own changelog and roadmap. Never mix cross-repo changes.

**Layout: a single growing `CHANGELOG.md` with an `## [Unreleased]` section**,
newest entry at the **top** of it (Keep a Changelog). A repo may declare a
different layout in its own `AGENTS.md`; absent that declaration, this is it.

**The changelog is append-only, and you never read the whole file to append to
it.** It is the opposite artifact from the handoff: the
handoff is a small, rewritten snapshot of *now*; the changelog is a permanent,
growing record that is **never rewritten, reordered, or pruned**. Past entries
are history — correct a wrong one with a new dated entry saying so, never by
editing it.

- **"Append" means add without disturbing existing entries** — it does not mean
  bottom-of-file. The newest entry goes at the *top*.
- **Read only the head** — roughly the first 30 lines, enough to find the
  insertion point and the current heading. **Never read the whole file** to
  write to it; a mature changelog is thousands of lines and reading it is pure
  waste. You do not need prior entries to write a new one.
- **Some repos deliberately have no changelog — read the repo's own declaration
  before assuming one is missing.** A repo's `AGENTS.md` governance table is
  authoritative about its own surfaces: "not adopted" there is a decision, not a
  gap (`aidoc-flow-business` declines both `CHANGELOG.md` and `ROADMAP.md` —
  git commits plus `DECISIONS.md` are its changelog). Write to the substitute
  the repo names, and **never create the file it chose not to have.** Same for
  handoffs, TODO files and decision logs.

- **One entry per session or PR**, dated, saying what changed and why it
  mattered — not a commit-message dump. Detail belongs in the decisions log.
- **Split when it gets big.** Past ~1500 lines, move the older half to a dated
  sibling (`CHANGELOG_2026-06.md`) and leave a pointer in both directions.
  Keep the archive at the **same directory depth** as the live file, or the
  relative links inside the moved entries silently stop resolving. Starting a
  new changelog file is a normal, expected act — not a decision needing
  approval.

**Roadmap**:
- Location: `roadmap/ROADMAP.md` (single file, updated in place)
- Update when planning releases, completing milestones, or shifting priorities
- Format: Version table, timeline diagram, planned/completed releases with status

**When to update**:
- Changelog: every session or PR
- Roadmap: When starting a new plan, completing a milestone, or re-prioritizing

**Per-repo locations are declared by the repo, not here.** Read the repo's own
governance table (its `AGENTS.md`); where it has none, create at the
conventional `changelog/` + `roadmap/` paths when first needed. In the
aidoc-flow workspace the umbrella `AGENTS.md`'s per-surface table declares all
three **"Not adopted"** at umbrella level — per-submodule paths are canonical
there, so follow the submodule's own declaration.
