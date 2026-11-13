<!--
PR template. Copy to .github/pull_request_template.md (the installer does this
for new adoptions). Delete subsections that do not apply; keep the six
section headers in place so reviewers always find them.
-->

## Summary

<!-- 1-3 sentences describing what changed and why. Focus on the "why" —
     reviewers read the diff for the "what". -->

## Files touched

<!-- List each surface. Keep framework changes (ci/, install.sh, templates/)
     separate from consumer surfaces (services, docs). -->

| Surface | Change |
| --- | --- |
| `path/to/file` | brief change note |

## Multi-agent self-review

<!--
This project gates merges on `task review` (see canon: https://github.com/vladm3105/aidoc-hybrid-ci/blob/main/docs/SELF_REVIEW.md). The
audit-trail phrase lives in a COMMIT MESSAGE in the push range, not here:

  Multi-agent self-review per OPS-0065 (<reviewers>): PASS

Copy a phrase from .ci-config AUDIT_TRAIL_PHRASES verbatim — that key is the
source of truth the gate matches. The PR body records the outcome for reviewers.
-->

**Review verdict:** <!-- PASS after N rounds / REVISE folded -->

**Rounds:** <!-- e.g. "round 1 REVISE (3 findings folded), round 2 PASS" -->

## Consumer impact

<!--
This repo is the CI canon — changes under ci/ propagate to every project that
vendors it. Assess downstream impact:
-->

**Affected consumers:** <!-- all / some (name them) / none (canon-internal) -->

**Requires consumer re-vendor:** <!-- yes (MAJOR/MINOR) / no (PATCH or canon-internal) -->

**Breaking changes:** <!-- yes — describe migration path / no -->

**Migration notes:** <!-- Steps consumers take after updating .ci/. Delete if none. -->

## Cross-references

<!-- PLAN-NNN, issues, related PRs. Delete section if none. -->

- #

## Test plan

<!-- Reviewer-facing check list: what CI verifies, what a human verifies. -->

- [ ] `task ci` green on the branch
- [ ] `task review` PASS (or N/A with reason)
- [ ] Scratch-install e2e (for installer or template changes)
- [ ] `<domain-specific verification>`
