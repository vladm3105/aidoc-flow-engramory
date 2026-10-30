# Review rubric

Project-owned rubric for Pass 2 (independent review) of the aidoc-local-flow-ci self-review flow.
The reviewer sees only: the requirements, this rubric, and the diff. Keep it specific to this
project — generic advice is already in the reviewer instructions. Editing this file invalidates
existing review verdicts (its hash is part of every verdict).

## Lens 1 — Functional correctness and scope

- Every requirement is implemented; nothing outside the requirements changed.
- Error states are handled explicitly; no silent failure, no swallowed errors.
- No regressions in behaviour visible in the diff; edge cases (empty, zero, max, concurrent) handled.

## Lens 2 — Real verification (anti-mock)

- Tests exercise the real code path with real assertions.
- The unit under test is never mocked; mocks only at process/network boundaries.
- No skipped, weakened, or assertion-free tests; every behaviour change has a test.

## Lens 3 — Governance and project invariants

<!-- Replace with this project's rules, for example:
- Architecture layering (which packages may import which).
- Change/plan manifests (CHG/IPLAN) updated, versions and step statuses accurate.
- Public API / schema changes are versioned and documented.
-->

- Project conventions in AGENTS.md are followed.

## Lens 4 — Security and boundaries

- No secrets, tokens or credentials in code, tests, fixtures or logs.
- Input validation at trust boundaries; no injection (SQL, shell, template, prompt).
- Money and ledger values: integer minor units or decimal types, never floats; idempotent writes.
<!-- Add project rules, e.g. key custody / crypto-invisibility rules, PII handling. -->
