# Testing profile — {{PROJECT}}

How the unified-test-suite canon is tuned for this project. The QA engineer maintains it (`.agents/agents/qa-engineer.md`, loop step 0); humans approve changes. Everything here is project-specific. The canon protocols (`.unified-test-suite/AGENTS.testing.md`, `.unified-test-suite/agents/JOURNEYS.md`) are not repeated.

## Document Control

| Field | Value |
|---|---|
| Version | 0.1 (draft from `uts adapt`, {{DATE}}) |
| Status | Draft — complete §1–§10, then Approved |
| Author | {{AUTHOR}} |
| Seed read | {{SEED_FILES}} |
| Modules read | {{MODULE_FILES}} |
| SDD snapshot | `docs/testing/SDD_TEST_INVENTORY.md` ({{INVENTORY_TOTALS}}) |

## 1. Why this project exists

From the seed (cite the file and §). Two to five sentences: the problem, who has it, and what must never go wrong for them.

## 2. What we are building

From the modules (cite MODULE ids). One row per module: purpose, the user-facing surfaces it owns (screens, APIs), and its key invariants.

| Module | Purpose | Surfaces | Invariants that tests must protect |
|---|---|---|---|

## 3. Final target of the tests

The definition of done in SDD terms:

- which EARS requirements must be **verified** in `summary.sdd` before a release, and at which gate;
- which BDD scenarios need journey evidence on staging;
- which scenarios need a codified Playwright spec in the PR gate.

State the numeric targets from TDD/EVAL thresholds (for example e2e ≥ 75 % of happy paths), not invented ones.

## 4. State glossary

The shared vocabulary for EARS `WHILE` states and journey `setup.reach` (JOURNEYS.md §2). One line each, observable in the UI.

| State | How a user or agent recognizes it | Reached by |
|---|---|---|

## 5. Oracle conventions

What success, failure and "handled error" look like in this product, so every card asserts the same way. Examples: toast text and role, URL patterns, error-code display, requests that must return 2xx, console noise that is known and allowed.

| Oracle | Convention |
|---|---|

## 6. Environments, identities, providers, journeys

| Environment | APP_URL / BACKEND_URL source | BACKEND_MANAGED | Journeys allowed | Notes |
|---|---|---|---|---|

- **Identity pool:** env var, entries per worker, which slots mean which persona. Never list credentials here.
- **Allowed extra origins** (identity providers, payment pages):
- **Seeds allowed for `setup.via: seed`:**
- **Providers and modes per environment** (mirror `providers` in the config):
- **Issue conventions:** repository, extra labels, milestone, assignee rules, and who triages `environment` issues.

**Journey register** (the QA engineer updates it; promotion after 3 consecutive fresh passes):

| BDD scenario | Card | TDD case(s) | Priority (§8) | Last 3 runs | Promoted spec |
|---|---|---|---|---|---|

## 7. Gates and tracks

| Gate | Layers | sddMode | Runs where | Blocks |
|---|---|---|---|---|
| `pr` | deterministic layers only (no `journeys`) | build → gate-code once clean | local CI on dev PRs | merge to dev |
| `functional` | `journeys` (+ smoke) | gate-code | staging, before deploy | release |

## 8. Risk ranking

What hurts users or the business first (money, access, data, trust). Ranked list with the EARS/BDD IDs. Journeys and specs are prioritized in this order.

## 9. Not browser-testable here

Behaviors verified only by API, unit or integration layers, and why (no UI, needs time travel, provider-only). This keeps agents from spending browser runs on them.

## 10. Open questions

Gaps found while filling this profile or while running journeys (`inconclusive` / `test` findings). Each one becomes a card fix, a CHG proposal or an answer here.
