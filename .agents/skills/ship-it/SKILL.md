---
name: ship-it
description: >-
  Use after a PR you opened is running CI, but only when the repository explicitly authorizes agent auto-merge. Watch required checks, enforce carve-outs, merge when eligible, and clean up the branch; otherwise stop for explicit user direction. GitHub-only: every mechanism step is `gh`/GitHub.
# Edit/Write and the git merge/commit/add/push verbs are scoped by §§1.1/2.1,
# not by this list: conflict resolution and fixes stay on the PR's branch,
# and anything the PR does not own ends the loop (bail-out).
allowed-tools: >-
  Bash(gh pr view:*) Bash(gh pr checks:*) Bash(gh pr merge:*) Bash(git rev-parse:*) Bash(git worktree:*) Bash(git branch:*) Bash(git fetch:*) Bash(git checkout:*) Bash(git pull:*) Bash(git merge:*) Bash(git commit:*) Bash(git add:*) Bash(git push:*) Read Edit Write
metadata:
  mined-from: netresearch/git-workflow-skill v1.23.0 (MIT AND CC-BY-SA-4.0), 2026-08-12
---

# Ship it

The stretch between "PR is open" and "PR is merged" had no owner: `wrap-session`
starts *after* the merge, and the skill that gets you *to* the PR stops at
opening it. This is that stretch, and nothing else.

## 0. Precedence, and one seam worth knowing

| File | Owns | Beats |
|---|---|---|
| `<repo>/AGENTS.md` | the **authorization and declaration** — whether agents may auto-merge, merge convention, protected branches, who may merge | global defaults |
| global `AGENTS.md` | global safety carve-outs | this skill |
| this skill | the **mechanism** — how to watch, what "green" means, how to merge | — |

If this file and the global `AGENTS.md` disagree, the policy wins and the
disagreement is a defect **here**.

**Forge check first.** If the PR's remote is not GitHub (`gh repo view`
fails, or the remote host mismatches), stop with a message — the
carve-outs below (stale-SHA, bucket-vs-state, re-arm rules) do not
transfer to other forges, and improvising around them voids the safety
case. Requires `gh` and an authenticated GitHub session throughout.

**The seam with `superpowers:finishing-a-development-branch`.** Where that
plugin is installed, its rationalization table states the same rule
(*"Integration is your human partner's decision. Present the menu and wait."*);
the table below governs the same moments with or without it. It is not in
conflict with merge-on-green — it governs a **different moment**:

| Moment | Governed by |
|---|---|
| implementation done; choose an integration route (merge locally / open a PR / leave it) | `finishing-a-development-branch` — present the menu, wait |
| a PR of yours is open and its checks are running | the repo's explicit auto-merge rule, if any — otherwise wait for user direction |

Read them as sequential, not competing. **Merge-on-green does not authorize
merging locally without a PR** — it presupposes an open PR and passing required
checks. Do not use this skill to skip the menu.

## 1. Watch the checks

**Read `mergeStateStatus` first — before any check rollup.** It is the only field
that sees a **missing required review**: `BLOCKED` ends the question regardless
of what the checks say, and no check-rollup field reports it.

```sh
gh pr view <N> --json mergeStateStatus --jq .mergeStateStatus   # CLEAN / BLOCKED / BEHIND / UNKNOWN / DIRTY / UNSTABLE / DRAFT / HAS_HOOKS
```

`BLOCKED` → the PR is not mergeable yet; find out whether it is checks or review
before reading any colour. `DIRTY` → §1.1 — a conflicted PR is not red CI.
`UNSTABLE` → required checks are failing — §2.1 red loop, never merge failing.
`DRAFT` → stop unless the draft state is intended. `HAS_HOOKS` → commit-status
hooks outstanding — wait and re-read like `UNKNOWN`. Then watch:

```sh
gh pr checks --required --watch          # required checks only, blocks until they settle
gh pr checks --json name,state,bucket,workflow --jq '.[] | select(.bucket!="pass")'
```

Three things that make "green" a claim rather than an observation:

- **`--required` is the whole point.** Merge-on-green is worded *"all **required**
  checks pass"*. A repo with advisory or optional checks will show red or pending
  entries that do not gate the merge, and — worse — a repo whose important check
  is *not* marked required will look green when nothing verified anything.
  Check which is which before reading the colour.
- **`bucket`, not `state`.** `gh` normalises `state` into `pass` · `fail` ·
  `pending` · `skipping` · `cancel`. **`skipping` is not `pass`** — a required
  check that was skipped has verified nothing.
- **Exit code 8 means pending**, not failure. Do not read a non-zero exit as red.

**A zero-length rollup is a case to name, not a silence to fill.** `checks == 0`
means one of three things — no CI is configured (this repo's own declared
decision, verified in its `AGENTS.md`/workflows) · workflows exist but have not
fired yet on a fresh head (a race — wait and re-read) · the wrong PR/head was
queried. Say which one you are in; "nothing to wait for" is a conclusion only
the first case supports.

**The stale-SHA trap.** After a push, `gh pr checks` can still describe the
previous head. Confirm the checks belong to the commit you intend to merge —
read the head from GitHub on both sides of the watch, never from the local
checkout (which may be another branch or worktree):

```sh
gh pr view --json headRefOid --jq .headRefOid   # before the watch
gh pr checks --required --watch
gh pr view --json headRefOid --jq .headRefOid   # after: must match, else re-read
```

Different → the run you are reading is not the run that matters. Wait, or push
and re-watch. Reporting an earlier run's green as this commit's is a result
that no input can falsify.

### 1.1 DIRTY — the conflict branch, not the red branch

`DIRTY` (`mergeable: "CONFLICTING"`) means the base advanced under the PR —
usually a preceding merge landing first. GitHub **disarms auto-merge** on
entry to this state, so re-arming is part of every resolution. A conflicted
PR is not red CI: do not poll its checks (§1) as if a re-run could fix it.
Detect it with the same read:

```sh
gh pr view <N> --json mergeable,mergeStateStatus --jq '"\(.mergeable) \(.mergeStateStatus)"'
```

**Classify first.** Exactly one of:

| Class | Shape | Action |
|---|---|---|
| **Class 1 — deterministic / additive** | appended registry/changelog rows, adjacent independent edits, import-block additions, formatting alignment | resolve autonomously below |
| **Class 2 — semantic / architectural** | incompatible logic, conflicting schema migrations, security-policy collision, delete-vs-modify, anything unfixable in one attempt | `git merge --abort`, post the conflict finding to the PR, escalate to the human |

**Resolve (Class 1 only), on the PR's branch.** Substitute the repo's
integration branch and remote name (`origin` below is the default):

```sh
git fetch origin <integration-branch> && git merge origin/<integration-branch>
git diff --name-only --diff-filter=U   # unmerged paths, when the merge does not complete cleanly
```

Merge, never rebase: a pushed branch cannot be rebased without `--force`,
and force-push is 🔴 per `approval-gate` — the protocol is zero-force-push
by construction. Combine additive rows keeping order and formatting; keep
both valid independent additions. Then stage, commit, verify with the repo's
declared gate (**never `--no-verify`** — a failing hook is a defect to fix,
not a gate to bypass), and push:

```sh
git add <resolved-files> && git commit -m "chore(ci): merge origin/<branch> and resolve deterministic conflicts"
<repo gate>                            # the repo's declared check, e.g. its local gate script
git push origin <pr-branch>
```

Where the repo uses auto-merge, **verify branch protection first, then
re-arm immediately** — GitHub will not do it for you. Arming `--auto` on a
branch whose protection is absent or weaker than you assume (no required
reviews, no strict status checks) merges the resolution push the moment CI
passes — an arm-then-push TOCTOU. Confirm the expected protection
(`gh api repos/{owner}/{repo}/branches/<base> --jq .protected` plus the
required-checks list) matches what the repo declares; if protection is
absent or you cannot verify it, do NOT arm `--auto` — merge explicitly or
stop for direction:

```sh
gh pr merge <N> --auto --squash     # or --merge / --rebase, per the repo (§3); confirm MERGEABLE + auto-merge active after
```

**One-attempt cap.** Conflicts re-emerging after one autonomous resolution
mean the branch is thrashing against a moving base — stop, report, escalate.
Same for any Class 2 file discovered mid-resolution: abort the merge, do not
resolve around it.

## 2. The merge decision — not yours to restate

The active repository must explicitly grant agent auto-merge. If it does not,
stop and ask the user. When it does, apply that declaration plus the global
safety carve-outs as a checklist:

| Carve-out | If it applies |
|---|---|
| the user said wait or hold | stop |
| **CI is red or incomplete** | **report + fix — never merge failing.** This one is a prohibition, not an approval request: no sign-off converts it into a go |
| it is not a PR you opened | stop |
| a repo rule reserves merge for a human | stop |
| you are unsure whether one applies | ask before merging |

`approval-gate` does not tier merging, deliberately — standing authorization is
not self-approval. That reasoning lives in `approval-gate`; do not re-derive it.

### 2.1 When CI is red — the loop the prohibition implies

"Report + fix — never merge failing" is the likeliest branch of the only phase
this skill owns, and it has steps:

1. **Diagnose, then fix on the branch, push, and re-enter §1 from the stale-SHA
   re-check** — the new head means every previous reading is void; confirm
   `headRefOid` before trusting any colour again. Verify concentrically (the
   repo's fast gate before committing the fix, its full gate before pushing),
   and never `--no-verify`: a failing hook is a defect to fix, not a gate to
   bypass.
2. **Bail out when** the same check is red twice after a fix attempt, or the fix
   is non-trivial (it touches things the PR does not own) — stop, report the
   diagnosis and the attempted fix to the human, and leave the PR open.
3. **Context running out mid-loop** → `context-handoff`; its gate matches
   (unfinished thread), and the PR number plus the diagnosis is what the
   successor needs.
4. **Cancelled checks are red here**, not a pass — re-run them before any
   merge decision: `gh run rerun <run-id>` for each cancelled run (find ids
   via the check's URL), then `gh pr checks --required --watch` to re-watch.
   Watching alone never clears a cancelled state.

## 3. Merge and clean up

Use the repo's declared convention — do not assume squash.

```sh
gh pr merge <N> --squash --delete-branch     # or --merge / --rebase, per the repo
gh pr view <N> --json state,mergedAt --jq '"\(.state) \(.mergedAt)"'   # read it back
```

**Read the merge back.** A merge command that printed a URL is not proof the PR
merged — the same read-back discipline `submit-feedback` applies to issue bodies.

If the PR body carried `Closes #N`, the merge closes the issue. **Do not close it
by hand afterwards** — that step is the one that gets forgotten, and doing both
is two records of one act.

**Post-merge cleanup & hygiene (mandatory).** Immediately after a merge, return
the repository to a clean baseline from the main checkout — a standalone
obligation, not a parenthetical. Substitute the repo's integration branch,
remote name, worktree path, and branch name:

1. `git worktree remove <task-worktree> --force` — remove the task worktree
   (skip when no worktree was used).
2. `git fetch --prune origin` — prune deleted remote-tracking references.
3. `git branch -D <feature-branch>` — delete the local branch. `-D`, not
   `-d`: squash merges rewrite commit SHAs, so the ancestry check behind `-d`
   fails on cleanly merged branches (`error: The branch '...' is not fully
   merged`) and agents abort cleanup or leave the branch behind.
4. `git checkout <integration-branch> && git pull --ff-only origin
   <integration-branch>` — fast-forward the integration branch.
5. Verify `git worktree list` shows only the main checkout and `git branch`
   shows no leftover feature branch.

**A PR closed unmerged is a named outcome, not a silent one.** Rejection usually
means the thread is *not* finished: report why it was closed (read the review or
close reason), and wrap only if the thread is actually finished without it.

## 4. Hand off

A merged PR is a wrap point: say so, and run `wrap-session` **in this session** —
the wrap must not wait for a fresh one. `wrap-session` takes it from here.

**This skill never writes a handoff, a changelog entry, or a memory.** If you are
reaching for one of those, you have already left this skill.

## Red flags

| Thought | Reality |
|---|---|
| "CI is green" | Required-green? Optional checks are noise; an unrequired important check is worse than noise. |
| "Checks passed" | On which SHA? Compare `headRefOid` to `HEAD`. |
| "A check was skipped, so it didn't fail" | `skipping` is not `pass`. It verified nothing. |
| "Non-zero exit, so it's red" | Exit 8 is pending. |
| "It's my PR, so I can merge" | Run the four carve-outs. One of them is a prohibition. |
| "The other skill said to wait for the human" | Different moment — see §0. And it does **not** license merging without a PR. |
| "Merged — now I'll close the issue" | `Closes #N` did it. Closing again is a second record. |
| "It printed a URL, so it merged" | Read the state back. |
| "Red — I'll just fix it forever" | Red twice, or a fix outside the PR's scope → stop and report. That is §2.1's bail-out, not persistence. |
| "DIRTY — the checks will settle" | Conflict, not CI. Classify and resolve (§1.1); polling changes nothing. |
| "Rebase, then force-push the fix" | Force-push is 🔴. Merge; the protocol needs zero force. |
| "Hook failed — push with --no-verify" | Never. Fix the hook failure on the branch. |
| "Checks came back empty — green by vacuous truth" | Name the zero-case: no CI (declared?), not fired yet (race), or wrong head. Only the first is green. |
| "The PR was closed, so it's finished" | Closed ≠ merged. Report why; the thread usually is not finished. |
