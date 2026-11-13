---
name: issue-triage
description: >-
  Triage GitHub issues on a working repo — validate claims live against the tree, assess priority, label, and post a triage note; or pre-validate an issue before implementation. Use when asked to triage, review, or scope issues, or before starting issue work. Set REPO and BASE before use.
---

# Issue Triage

Triage = assess + label + comment. Triage NEVER implements, closes, or rewrites history.

Two modes share one verification core (§§0–2). Mode A (§§3–4) answers "what is this issue worth" — priority, labels, next step. Mode B (§4B) answers "may I implement now" — go/no-go. The mode is recorded in the note header; a Mode B note is never a duplicate of a Mode A note.

## 0. Preconditions

- `gh` authenticated with write access to `$REPO`; local clone on `$BASE`, clean tree.
- Set `REPO` (`OWNER/NAME`) and `BASE` (the integration branch) before use.
- Read the repo's project appendix (§5) and session memory first; they override anything stale below. If absent (fresh machine), proceed without them.

## 1. Fetch the issue live

```sh
gh issue view <N> --repo $REPO --json state,title,labels,body,comments
```

Record: state, current labels, existing comments, every factual claim (file paths, line numbers, counts, error text). Filed line numbers rot fast — treat them as hints, never facts.

If the issue is already CLOSED, stop and report that with evidence — never triage a corpse, never re-triage into a closed thread.

### Review the full comment thread

The body is only the opening claim — read every comment, oldest first:

- Attribute each follow-up claim (correction, reproduction, "+1", proposed fix) to its author and date; verify load-bearing ones under §2 exactly like body claims.
- Corrections inside the thread supersede the body — triage the corrected claim, and name what it supersedes in the note.
- A prior triage note changes the job: post a delta per §4, never a duplicate full note. If an earlier verdict's evidence rotted (file moved, rule renumbered, version advanced), say so with the fresh check.
- Stale or resolved sub-threads get one line in the note, never an in-thread argument with the commenter.

## 2. Verify each claim against the tree (the core discipline)

For every claim, run the check; never trust the issue text:

- File/line exists? `grep -n`, `sed -n`, `git log --oneline -3 -- <file>`
- "X never held / never ran"? Full-history enumeration (`git log`, `git show <sha>:<file>`) or `gh run list`
- Counts? Reproduce the measurement; note when your recount corrects the filing
- CI behavior? `gh pr checks <n>` / `gh run view --log-failed`

Mode B adds one more: `git log` on every cited file since the issue's filing date — claims rot, and the filing-date diff is the rot detector.

## 3. Assess (Mode A)

**Priority** by blast radius, never by adjectives in the issue text. The repo appendix owns the exact rubric; the default shape is P0 (CI red / security / data loss — say so prominently in the note and ask whether to fix now; triage alone underserves it), P1 (contract falsehoods that misroute agents or consumers), P2 (real gaps with workarounds), P3 (stale prose, nits).

**Labels**: list first (`gh label list --repo $REPO`), reuse — create only for a genuinely new class. Compose priority + area + class; remove a clearly wrong label rather than stacking around it, otherwise leave it and note the question in the comment.

**Cluster**: link related issues with the order to read or fix them in.

**Ownership — transfer when another repo owns it**: the test is ownership, not severity. If the defect lives in another repo (a sibling, a submodule, an upstream spec), the issue belongs there. Record the ownership verdict in the triage note; execution per §7.

## 4. Post the triage note + label (Mode A)

Owner-voice: factual, concise, no chitchat, no raw tool dumps — evidence, not transcripts. Publish with the safe mechanics (`--body-file -`, never `--body -`; read the write back) — a URL or exit code alone never proves a write.

If a triage note already exists, post a delta (`## Re-triage note (YYYY-MM-DD)` — what changed, per-claim verdict), never a duplicate full note and never an edit of the old one. Otherwise post one comment in this shape:

```md
## Triage note (YYYY-MM-DD)
- Priority: P1 · Area: <area>
- Assessment: <what's actually wrong, with verification evidence>
- Cluster: <related issues + order>
- Next step: <smallest safe fix direction, incl. what NOT to touch>
- Suggested labels: `<priority>`, `<area>`
```

Then apply labels directly — including priority. Triage owns the labels; suggesting without applying just moves the work. Do not close, do not implement.

## 4B. Mode B — pre-implementation validation

Run the shared §§0–2 core verbatim first, then branch here instead of §§3–4. Mode B NEVER implements either: it authorizes a vehicle (§5 appendix names the repo's), it does not write the fix.

- **Per-claim verdicts.** Every body + thread claim gets one: ALIVE (still reproduces on current `$BASE` — cite the fresh check), DEAD (contradicted by the tree — cite the evidence), or PARTIAL (name exactly what survives).
- **Supersede scan.** Search the repo's later change records, decisions, merged PRs, and open branches/attempts for anything that already fixed, scoped, or declined the claim. Name links or record "none found" — never assume.
- **Implementation vehicle.** Classify per the repo's change procedure and re-check blast radius against the current tree (the filing-time radius may have shifted).
- **Terminal verdict.** Exactly one: GO (all load-bearing claims ALIVE, no superseder), SCOPED-GO (a PARTIAL subset survives — name the surviving scope; the rest is not authorized), STALE (claims no longer reproduce, no superseder), SUPERSEDED (link the superseder), or ALREADY-FIXED (cite the merged evidence). On STALE / SUPERSEDED / ALREADY-FIXED, report with evidence and stop — do not open a vehicle around the verdict.
- **Note shape:**

```md
## Pre-implementation verdict (YYYY-MM-DD, <base> @ <sha>)
- Verdict: GO | SCOPED-GO | STALE | SUPERSEDED | ALREADY-FIXED
- Claims: <id>: ALIVE | DEAD | PARTIAL — <evidence>
- Supersede scan: <links or "none found">
- Vehicle: <repo procedure classification> · Blast radius re-check: <result>
```

- **Memory.** §6 applies with one addition: record Mode B verdicts with the `$BASE` SHA they were verified against, so a later session can tell the verdict rotted instead of re-reading or re-trusting it.
- **Independence.** When the verdict is GO on a high-stakes issue or a contested claim, prefer a fresh-context second opinion (judge ≠ implementer) before opening the vehicle. Low-stakes GO verdicts do not owe one.

## 5. Project appendix (per repo — never hardcoded globally)

Each repo fills in: label taxonomy, priority rubric, known defect classes (each with Why and How-to-apply), standing rules (close keywords, commit phrases, pre-push suites, required-checks invariant), memory paths, and the change procedure §4B classifies vehicles against. The appendix lives with the repo (an `issue-triage-local` skill per `GOVERNANCE.md`, or repo docs this skill points at) — never in this file.

## 6. Write back to memory

Triage results must outlive the session: record ordering decisions (what goes first and why), any new defect class with its Why/How-to-apply, and corrected counts. The next session starts from memory, not from re-reading the backlog.

## 7. Transfer to another repo (forwarding)

Only after §§1–4 are done on the local issue — the triage note carrying the ownership verdict posts here first, so the trail survives the move.

1. Cite the ownership evidence in the note (which repo owns the file/workflow, with `file:line` or workflow path).
2. Run the `approval-gate` check — transfer is an external action; never self-approve.
3. Transfer: `gh issue transfer <N> <OWNER/REPO> --repo $REPO`.
4. Read the transfer back — a printed URL is not proof it landed.
5. Record the move in memory (§6) with the reason. The transfer itself closes out the local item — never close the local issue as completed, and never re-triage into the moved thread.

## What this skill is not

Capture belongs to `submit-feedback`, continuity to `wrap-session` / `context-handoff`, pre-work re-validation mandates to the repo's own validate-before-work rule (`AGENTS.md` § Repository state and task tracking). Triage sits between capture and implementation; it replaces none of them.
