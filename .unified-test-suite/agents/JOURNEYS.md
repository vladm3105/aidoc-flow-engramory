# Browser journeys — the SDD protocol for browser-capable agents

`.unified-test-suite/agents/JOURNEYS.md` · canon-owned, normative (CANON.md §12, SPEC-03). Applies in SDD mode.

This is **not** a guide to using a browser. Your runtime already knows how to click and read a page. This protocol defines *what* a browser run must prove, *how its result becomes evidence* the SDD chain can count, and *what never happens*. Project specifics (states, oracles, identities, environments) live in the project's `docs/testing/TESTING_PROFILE.md` and in the journey cards.

## 1. Where browser runs fit

```text
docs/seed  ─┐
docs/modules├─▶ TESTING_PROFILE.md (why, final target, states, oracles, risks) ◀── QA engineer
docs/sdd   ─┘            │
EARS ──▶ BDD scenario ──▶ journey card ──▶ browser subagent ──▶ evidence record
  ▲            ▲              │ tdd:                                  │
  │            │              ▼                                       ▼
  └── summary.sdd roll-up ◀── TDD e2e case ◀── binder ◀── uts journeys import (runner journeys)
                                                                      │
  GitHub issues ◀── uts issues ◀── failures + findings (QA engineer files; nobody on the QA side fixes)
                          coding agent ◀── issues, and 3 consecutive fresh passes → tagged Playwright spec (PR gate)
```

| Evidence | Produced by | Deterministic | Gate |
|---|---|---|---|
| Playwright spec tagged `@BDD…` `@TDD…` | coding agent, run by `task test:<e2e layer>` | yes | `pr`, `nightly`, `all` |
| Journey evidence record | browser subagent, imported by `task test:journeys` | no | functional / staging gates only |

The framework marks BDD scenarios "QA staging only". Journeys are how the scenarios themselves get verified on a real stack. Specs are how the verified behavior stays verified.

## 2. EARS → states

Every UI-relevant EARS requirement is decomposed into a **state model**: the only vocabulary a card and an evidence record use.

| EARS pattern | Keyword | Card field | The subagent… | Evidence |
|---|---|---|---|---|
| state-driven | `WHILE <state>` | `ears[].while` → a `states` entry | reaches and confirms the state before acting | `setup` step observed |
| event-driven | `WHEN <trigger>` | `ears[].when` → an `act` step | performs the trigger once | `act` step observed |
| response | `THE <system> SHALL <response>` | `ears[].then` → an `expect` id | evaluates exactly that oracle | `expect` step observed value |
| bound | `WITHIN <n unit>` | `ears[].within` | measures trigger → observable, ≥ 3 samples when cheap | `timings[]` |
| unwanted | `IF <condition> THEN …` | a negative card (own BDD scenario); elsewhere `covered_by: <BDD-ID>` | provokes the condition via allowed means only | `expect` shows the handled state |
| optional | `WHERE <feature>` | `environments` / `setup.reach` | runs only where the feature is enabled, else `blocked` | reason |
| ubiquitous | `THE … SHALL` | `expect` checked in every journey of that area | checks it opportunistically | `expect` step |

**State names** are snake_case product nouns (`signed_out`, `otp_sent`, `cabinet_ready`, `kyc_pending`). They are defined once per project in the profile's state glossary and reused across cards, so "same state" means the same thing everywhere.

**Timing.** An agent's clock is noisy. `within` bounds are recorded as `timings[]` with samples. They fail the journey only when the card sets `within.enforce: true`. Authoritative p95 numbers come from measured tests, not from a browser agent.

## 3. BDD → journey card

One card per BDD scenario: `tests/journeys/<BDD-ID>.journey.yaml` (`sdd.journeys`). `uts adapt` writes skeletons from the scenario text with `draft: true` (drafts are skipped by plan and import and reported by `uts check`). The QA engineer finishes them: completing a `draft: true` card is QA authoring the test basis. Once a card is finished (no `draft`), it changes only through an issue (`test-gap`) implemented under an IPLAN, never during or after a run to make it pass.

| BDD | Card | Rule |
|---|---|---|
| `Given …` | `setup[]` | each step `reach`es a named state. `via: ui` (default) or `via: seed` with a seed command the profile allows. `identity: <slot>` picks the worker's pool entry, never a literal account |
| `When …` | `act[]` | one `intent` per user action, in product vocabulary. `target` only for the first navigation, written as `{APP_URL}/path` |
| `Then … / And …` | `expect[]` | one oracle per observable. `oracle` ∈ `ui` (visible text or role), `url`, `network` (status of a named request), `console` (no errors), `timing`, `storage`, `a11y`. `assert` is a sentence a reviewer can check against a screenshot |
| scenario tags / data | `data`, `external`, `environments` | data is namespaced by `TEST_RUN_ID`. `external: true` when a provider sandbox is touched |

Never put selectors, XPath or code in a card. If a check needs a selector, it belongs in a Playwright spec.

**Allowed origins.** `allow.origins` defaults to `{APP_URL}` and `{BACKEND_URL}`. The subagent never navigates elsewhere, except an identity provider listed in the profile.

## 4. Binding to TDD

The scenario's e2e TDD case points at the card:

```yaml
- id: TDD.NN.04.xxxx
  type: e2e
  test_file: tests/journeys/BDD.NN.03.xxxx.journey.yaml
  test_function: BDD.NN.03.xxxx
  bdd_ref: "@bdd: BDD.NN.03.xxxx"
```

The card names the case back (`tdd:`), and `uts check` verifies both directions. Once imported, the evidence binds to this case, the case pairs the scenario, and the scenario rolls up to its EARS requirements in `summary.sdd`.

## 5. Work order and evidence

`node .unified-test-suite/bin/uts.mjs journeys plan [--ids BDD.…,…] [--env local|staging]` prints one JSON work order and keeps a copy as `<evidenceDir>/<runId>/_plan.json`. Hand **one item per browser subagent**; each item is self-contained (card, URLs, identity slots, evidence file and a `record` header to copy). At most one subagent per identity slot runs at a time.

Each run writes **one record** to the item's `evidenceFile` (`<resultsDir>/agent-evidence/<runId>/<BDD-ID>.json`) with schema `uts.agent-evidence/1`:

| Field | Content |
|---|---|
| `schema`, `journey`, `journeyFile`, `journeySha256`, `fingerprint`, `runId` | copied verbatim from the work item's `record` header (`journeyFile` = item `file`, `journeySha256` = item `sha256`). They make the record fresh or stale |
| `agent` | `{ runtime, tool, model? }`, e.g. `{ "runtime": "antigravity", "tool": "browser_subagent" }` |
| `verdict` | `passed` · `failed` · `blocked` · `inconclusive` (table below) |
| `classification` | for non-passed runs: `product` (app wrong) · `test` (card wrong or ambiguous) · `environment` (stack or provider down) |
| `steps[]` | one entry per setup / act / expect id: `status`, `observed` (what was seen, verbatim where short), `durationMs` |
| `timings[]` | `{ ears, metric, samples[], unit, bound?, ok? }` |
| `artifacts[]` | paths of failure-only screenshots / DOM snapshots under the evidence run folder |
| `startedAt`, `finishedAt`, `notes` | ISO timestamps; free text for triage (no secrets) |

| Situation | verdict | classification | Gate effect |
|---|---|---|---|
| every setup and act step passed, every expect oracle observed as asserted, no enforced time bound exceeded | `passed` | — | test passes |
| an oracle observed and it contradicts `assert` | `failed` | `product` | failure, `kind: journey` |
| the card is ambiguous, a state cannot be named, an assert is untestable | `inconclusive` | `test` | failure (fix the card or the BDD via CHG) |
| setup state unreachable because the stack, identity provider or sandbox is down | `blocked` | `environment` | skipped; the layer is `infra_error` when nothing failed |
| the step limit or timeout is reached before a verdict | `inconclusive` | `test` or `environment` | failure, or skipped when `environment` |

## 6. Import is the gate

`task test:journeys` (a layer with `runner: "journeys"`) imports the evidence:

- it keeps only schema-valid records whose `journeySha256` matches the card on disk and whose `fingerprint` matches the tree;
- it takes the newest per journey;
- it downgrades a `passed` record to `inconclusive` when a setup or act step did not pass or an expect id has no observation, and to `failed` when an enforced `within` bound was exceeded;
- it requires evidence for **every** finished card allowed in the planned environment (`_plan.json` on this tree; all finished cards when none): a card without fresh evidence is listed under `missing` and keeps the layer unverified, so no journey drops out silently;
- it redacts every text file in place (records, DOM snapshots, logs);
- it writes JUnit and `journeys.json` (records used, cards missing, records ignored with reasons).

Outcome: any `failed`/`inconclusive` → the layer fails; otherwise any blocked run or missing card → `infra_error`; otherwise passed. `evidenceDir` must lie under `resultsDir` (or outside the repo), or evidence would change the tree fingerprint.

Code or card changes after the plan make evidence stale by construction: re-plan and re-run. `test:clean` keeps `agent-evidence/`, so a gate can import what the agents wrote before it started.

## 7. Promotion to Playwright

After **three consecutive fresh passes** (the framework's "no flaky tests" bar), the QA engineer files a `codify-journey` issue for the coding agent:

- a spec in the project's e2e layer, tagged `{ tag: ['@BDD.NN.03.xxxx', '@TDD.NN.04.yyyy'] }`, using `identity(test.info())`;
- a second e2e TDD case `TDD.NN.04.yyyy` naming the spec file and test title;
- the card stays. It keeps verifying the scenario on staging, and the spec guards it in PR gates.

## 8. Feeding the SDD chain

Agents never edit SDD documents. An `inconclusive` verdict with `classification: test` names the gap in `notes`: a missing state, an untestable `SHALL`, a BDD step without an observable. The QA engineer files it as a `test-gap` issue: a card fix, or an EARS/BDD change through a CHG. This is how browser runs make the requirements sharper.

## 9. Defects become issues, never fixes

`node .unified-test-suite/bin/uts.mjs issues` writes one draft per fresh failure (tests, journeys, doc lint) and per `summary.sdd` finding to `<resultsDir>/issues/`, with EARS/BDD/TDD IDs, evidence or reproduce command, labels (`bug`, `test-gap`, `environment`, `sdd-drift`) and a stable `uts-key`. The QA engineer searches for the key and creates, comments on or reopens the GitHub issue. The canon never calls GitHub and never edits anything it tests.

## 10. Hard rules

1. One card per run; do only what the card says. No exploratory side quests inside a journey run. Report extra observations in `notes`.
2. Identities only from the canon pool slot (`E2E_USER_POOL`-style env, `identity` field). Never type, log or store a credential or OTP in evidence.
3. Write data only inside the `TEST_RUN_ID` namespace. Never touch production. `BACKEND_MANAGED=0` means a shared stack, so no resets.
4. Screenshots and DOM snapshots only on failure. Text artifacts are redacted on import; screenshots are images and cannot be, so never capture a screen that shows a credential or OTP.
5. Never "fix" the app, the test data or a finished card, during a run or after it (finishing a `draft: true` card is authoring, §3). The QA side discovers and files (`uts issues` → GitHub issues); the coding agent fixes.
6. Never mark `passed` without a passed step for every setup and act id and an observed value for every `expect` id.

## 11. Annotated card

```yaml
schema: uts.journey/1
journey: BDD.02.03.4f1a                  # file: tests/journeys/BDD.02.03.4f1a.journey.yaml
title: "Scenario: Returning user signs in with OTP and lands in the cabinet"
tdd: TDD.07.04.9c20                      # e2e case whose test_file is this card
ears:
  - id: EARS.02.03.a71c
    pattern: event
    while: otp_sent
    when: submit_valid_otp
    then: cabinet_visible
    within: { value: 3, unit: s, enforce: false }
  - id: EARS.02.03.c0d2
    pattern: unwanted
    then: otp_error_shown
    covered_by: BDD.02.03.77e0           # the negative scenario's own card holds this oracle
states:
  signed_out: "Landing page, no session cookie"
  otp_sent: "OTP entry screen shows the masked email"
  cabinet_ready: "Cabinet dashboard with the user's display name"
setup:
  - { id: s1, reach: signed_out }
  - { id: s2, reach: otp_sent, identity: 0 }
act:
  - { id: a1, intent: "Enter the identity's OTP and confirm", target: "{APP_URL}/login" }
expect:
  - { id: cabinet_visible, oracle: ui, assert: "The cabinet dashboard greets the identity's display name", ears: EARS.02.03.a71c }
  - { id: no_console_errors, oracle: console, assert: "No console errors during sign-in" }
allow: { origins: ["{APP_URL}", "{BACKEND_URL}", "https://auth.privy.io"] }
evidence: { capture: [console, network-errors, timings], on_failure: [screenshot, dom-snapshot] }
limits: { max_steps: 25, timeout_s: 120 }
environments: [local, staging]
external: false
```
