<!--
Canonical PR template per docs/REPO_STANDARDS.md §8. Applied via PR-B
apply-standards.sh. Ships as .github/pull_request_template.md in
consumer repos.

Remove commented-out sections that don't apply to your PR; leave the
required sections (Summary, Files touched, Multi-agent review, Test
plan) in place.
-->

## Summary

<!-- 1-3 sentences describing what changed and why. Focus on the "why" —
     reviewers read the diff for the "what". -->

## Files touched (OPS-0061 Rule 1 self-check)

<!--
List each file surface. Rule 1 caps this at ≤3 doc surfaces per
governance PR (touches CLAUDE.md, DECISIONS.md, IPLAN files,
.github/ai-review/, .github/workflows/, .claude/agents/,
.claude/skills/, or governance/supersession content). If >3 surfaces
on a governance PR: SPLIT into follow-up PRs or note a founder OK
below.

Non-governance PRs (feature code, docs-only changes, tests) have no
surface cap.
-->

| Surface | Change |
| --- | --- |
| `path/to/file` | brief change note |

**Governance tier:** <!-- 🟢 non-governance / 🟡 governance / 🔴 spec-tier per OPS-0062 carve-outs -->

## Multi-agent self-review (OPS-0065 / OPS-0069)

<!--
OPS-0069 requires dispatch of diff-class-matched sub-agents BEFORE
`git push`. The audit-trail phrase lives in a COMMIT MESSAGE in the
push range, not this PR body:

  Multi-agent self-review per OPS-0065 (<agents>): <verdict-summary>

OR (rare, founder OK required):

  Self-review skipped per founder OK — <reason>

The PR body below records the outcome for reviewers.
-->

**Agents dispatched:**

- `<agent-name>` — <verdict summary>

**Fold outcome:** <!-- e.g. "cycle 1 APPROVED, 0 findings" or "cycle 1 REVISIONS-NEEDED, N findings folded". Cap at 3 fold cycles per OPS-0066. -->

## Cross-references

<!-- OPS-NNNN, IPLAN-NNNN, PLAN-NNN, related PRs. Delete section if none. -->

- OPS-NNNN

## Test plan

<!-- Reviewer-facing check list. What CI verifies, what human-verifies. -->

<!--
Not every tier requires every check. Delete lines that don't apply
per docs/REPO_STANDARDS.md §2 branch-protection table:
  - product-code / ops-private: full set
  - governance: full set MINUS `Secret scan (gitleaks)`
  - umbrella: submodule-pointer PRs — no CI checks required
  - bootstrap: `Lint / format / security hooks` only; ai-review +
    composition are opt-in per REPO_ONBOARDING.md
-->
- [ ] `call / ai-review` verdict on this PR
- [ ] `call / composition` gate
- [ ] `Lint / format / security hooks` green
- [ ] `Secret scan (gitleaks)` green
- [ ] <domain-specific verification>
