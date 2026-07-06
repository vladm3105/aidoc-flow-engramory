# CLAUDE.md

Working agreement for AI agents in Engramory. For engineering conventions
(architecture, ports & adapters, config, testing) see **[AGENTS.md](AGENTS.md)** —
this file records only what's specific to Engramory as an **aidoc-flow workspace repo**
(governance, CI, cross-repo standards). Both files apply.

## Per-repo governance — this repo owns its own continuity

The `aidoc-flow` workspace is multi-repo. Each repo governs its own activity
tracking; cross-session continuity is per-repo. The durable surfaces for **this**
repo:

| Surface | Path (in this repo) |
| --- | --- |
| Engineering agreement | `AGENTS.md` (the source of truth for how to build; CLAUDE.md points here) |
| Changelog | `CHANGELOG.md` |
| Roadmap | `roadmap/` |
| Decisions log | `sdd/05_ADR/` (canonical/implementing ADRs) + `docs/adr/` (conceptual) |
| SDD lifecycle | `sdd/` (BRD→IPLAN artifacts) |

**Never put any of these in `tmp/`** — `tmp/` is for transient working files;
nothing in it survives a context-clear or new session.
**Never centralize in the umbrella `aidoc-flow/`** — the umbrella holds no
dev; plans, decisions, and tracking live in the owning submodule.

## GitHub operations

Use the **GitHub CLI (`gh`)** as the default for all GitHub operations — PRs,
issues, reviews, releases, repo queries — not the GitHub MCP servers
(`github-tt`, `github-vl`) or raw API calls. If `gh` is unauthenticated, run
`gh auth login` rather than falling back to MCP/API.

## Unified CI — consume from `aidoc-flow-ci`

This repo's CI workflows consume reusable workflows from the
**`vladm3105/aidoc-flow-ci`** library repo. The library is the source-of-truth
for CI logic shared across the aidoc-flow workspace; it ships independently
semver-tagged (`ci/v1.0.0`, `ci/v1.0.1`, …). Plan + charter live in
**`aidoc-flow-operations`** at
`ops/iplans/IPLAN-0017_unified-ci-flows.md` +
`ops/iplans/IPLAN-0017-CHARTER_aidoc-flow-ci.md`.

**Per-repo state (2026-07-06):** public repo. Adopts unified CI
via `.github/workflows/ai-review.yml`@ci/v1.4.3 + `composition.yml`@ci/v1.3.0
+ `auto-merge-ai-prs.yml`@ci/v1.5.1 (this PR). Runner topology: `ubuntu-latest`
(matches existing `ci.yml`). Reviewer: `claude` (subscription-auth via
`CLAUDE_CODE_OAUTH_TOKEN`). Dormant until reviewer App is installed
(F5 blast-radius prerequisite per operations CLAUDE.md § Unified CI).

### Local overrides shared — the foundational rule

GitHub Actions runs whatever's in this repo's `.github/workflows/*.yml`.
A shared workflow from `aidoc-flow-ci` only runs when this repo explicitly
calls it via `uses:`. So **local always wins** — by GitHub's default, not by
engineering.

Three override modes (preferred order):

| Mode | When | How |
| --- | --- | --- |
| **Parameter override** | Change one knob (runner labels, label colors, human-approval count) | Edit `with:` block in the local workflow; keep the `uses:` call |
| **Full replacement** | Local logic genuinely differs from canonical | Drop the `uses:` call; write the local jobs/steps |
| **Add a custom workflow** | New check the shared CI doesn't have | Create a new `.github/workflows/<custom>.yml`; siblings the shared callers |

There is no merge/inheritance/diamond pattern — GitHub doesn't support one.
"Override" means this repo's workflow file is what runs.

### Drift detection — warning-only, never blocks

The `aidoc-flow-ci/sync/check-drift.sh` script (run as a pre-commit hook or
periodic GitHub Action) compares each workflow file against the canonical
template at the pinned `ci/vX.Y.Z` tag and reports any diff as a warning.
**Never blocks the commit or the PR.**

### When this repo edits a shared workflow

If a change is broadly useful (every consumer would want it): open a PR on
`aidoc-flow-ci`, tag a new `ci/vX.Y.Z`, then bump this repo's `uses:` pin in
a separate PR. If the change is genuinely local: keep it in this repo's
`.github/workflows/` and accept the drift warning.

## Governance PR discipline (mandatory)

A **governance PR** is any PR that touches ADR files, `CLAUDE.md`, `AGENTS.md`,
`.github/ai-review/`, `.github/workflows/ai-review.yml`, or supersedes a locked
decision. Two rules apply — no exceptions without explicit founder OK and an
audit-trail note in the commit message.

### Rule 1 — Small scope (≤3 doc surfaces per PR)

A governance PR touches **at most 3 distinct doc surfaces** in one PR. If
more surfaces need updating, **split into sequential PRs**.

**Reconciliation with the existing doc-currency rule:** Rule 1 does NOT
supersede doc-currency; it scopes how the rule applies. Each split PR is a
self-contained smaller change with its own affected docs fully updated
within that PR. Doc-currency applies per-PR-scope, not per-overall-change.

### Rule 2 — Mandatory adversarial self-review before every push

Before `git push` on any governance PR, dispatch a code-reviewer agent on
the diff. Required focus areas:

- **Dead refs** — for every quoted path/file in the diff, grep and verify
  the target exists (or qualify as a forward-reference)
- **Supersession completeness** — when "supersedes X" appears, read X
  end-to-end and name ALL parts superseded vs ALL parts carried forward
- **Internal consistency** — every DECIDED / open / Status status matches
  across files in the diff

Fix every load-bearing finding **BEFORE push**. Skip only with explicit
founder OK + commit-message audit line (`Self-review skipped per founder
OK <reason>`).

**Origin:** operations 2026-06-23 (OPS-0061). Full reasoning + formal
record in `aidoc-flow-operations` `CLAUDE.md` § "Governance PR discipline",
plus `ops/DECISIONS.md` OPS-0061.

## AI agent auto-merge default (OPS-0062)

**Applies to ALL AI agents (Claude, Codex, Gemini, GitHub Copilot, etc.) —
not just one model.** For PRs the AI agent opens itself in this repository:

1. **Auto-watch + auto-merge when green.** After opening a PR, the AI watches
   the check rollup until all checks complete. If `mergeStateStatus = CLEAN`
   AND all required checks are SUCCESS, the AI attempts `gh pr merge --squash
   --delete-branch` without asking the human for explicit per-PR
   authorization.
2. **Escalate to human at 10 attempts.** If the PR still hasn't merged after
   10 distinct merge-or-recovery actions, the AI STOPS and requests human
   confirmation.

**One attempt =** each distinct merge-or-recovery action: each `gh pr merge`
invocation, each `skip-ai-review` label-cycle, each `gh run rerun`, each
`gh workflow run` retrigger. Polling does not count. **Counter is per-PR
cumulative, not per-session.**

**Visibility:** AI announces each merge attempt in-session.

**Exceptions (AI never auto-merges these; always asks):**

- 🟡 / 🔴 actions per the autonomy tiers (canonical table in operations
  `CLAUDE.md`).
- Spec / governance tier PRs (excluded from auto-merge by `ai-review.yml`
  `tier=spec`).
- Cross-repo coordinated changes.
- PRs touching this repo's governance PR list (per the "Governance PR
  discipline" section above): CLAUDE.md, AGENTS.md, ADR files,
  `.github/ai-review/`, `.github/workflows/ai-review.yml`.

**Human-opened PRs are unaffected.**

**Origin:** OPS-0062 (2026-06-27). Full reasoning + scope clauses +
reconciliation with the `auto_merge.repos` allowlist in
`aidoc-flow-operations` `ops/DECISIONS.md` OPS-0062.

## Multi-agent automated review (aidoc-flow standard — OPS-0065 + OPS-0067)

This repo follows the **aidoc-flow standard** for author-side multi-agent
review BEFORE push/commit. The canonical rules + diff-class → agents table +
parameterized prompt templates live in `aidoc-flow-operations`:

- **Rules:** `aidoc-flow-operations/CLAUDE.md` → "Multi-agent automated review
  (OPS-0065 — generalizes the CI ai-reviewer pattern to ALL internal flow)"
  section.
- **Prompt templates:** `aidoc-flow-operations/.claude/agents/review-prompts/`
  — diff-class skeletons (`workflow-yaml.md` / `governance-docs.md` /
  `docs.md` / `scripts.md` / `cross-repo.md` / `adversarial-judge.md` +
  `INDEX.md`).
- **Empirical default (OPS-0067):** 3-agent parallel dispatch + single fold
  cycle for ≤300-line diffs. Re-dispatch only on NEW load-bearing surfaces
  or structural pivots. Cap at 3 cycles per OPS-0066 circuit-breaker.
- **Standard scope:** all aidoc-flow workspace repos — this one included.

The CI `ai-review.yml` gate (merge-side) is unchanged; multi-agent review
strengthens the author-side review pattern.

**Skip discipline:** Stop using `SKIP_LOCAL_AI_REVIEW=1` indiscriminately
per OPS-0065. Acceptable cases: (a) mechanical content (pin bumps with no
logic edits); (b) AI-side review already done via dispatched agent
(commit-message audit-trail line names the agents + verdict); (c) explicit
founder OK per governance PR-discipline Rule 2.

**Origin:** OPS-0065/0067 in `aidoc-flow-operations` `ops/DECISIONS.md`;
cross-repo rollout runbook at
`aidoc-flow-operations` `ops/inbox/2026-06-30_cto-platform_ops-0067-multi-agent-review-rollout.md`.
