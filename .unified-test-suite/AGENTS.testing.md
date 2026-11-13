# Testing in this repo — agent instructions (unified-test-suite canon)

This repo runs every test through the unified-test-suite canon. Follow this loop and no other.
Reference it from the project's `AGENTS.md` / `CLAUDE.md`:

```text
Testing: follow .unified-test-suite/AGENTS.testing.md
```

1. **Discover** — `task --list` (or `task test:layers`). Never guess `make`, `npm`, `pytest` or `playwright` spellings.
2. **Preflight once per session** — `task test:doctor`. If a layer is not runnable here (no docker, no CGO, no browser…), do not run it; report what is missing.
3. **Run the smallest scope**
   - `task test:changed`: layers mapped from your diff.
   - `task test:<layer>` or `task test:<group>`: a specific scope.
   - `task test:pr`: before proposing a PR.
4. **Read, don't re-run** — open `test-results/summary.json`:
   - `verdict.status`: `pass` | `fail` | `error` (error = the summarizer crashed; treat as fail).
   - Per layer `status`:
     - `failed` → fix the code; each `failures[i]` has `file`, `line`, `message`, `reproduce`.
     - `infra_error` → the environment is broken (`reason` says how). Fix or report it; **never edit tests to get past it**.
     - `stale` → results predate your last edit; re-run that layer.
     - `not_run` → run it if it is in scope.
   - `summary.md` is the human view. Open Playwright traces (`artifacts`) only for browser failures.
5. **Reproduce one failure at a time** with `failures[i].reproduce`. Never re-run a whole gate to triage one failure. Confirm the fix with `task test:<layer>`, then the gate.
6. **Flaky tests** (`summary.json.flaky`): at most 2 single-test reproductions, then label/file it. Never delete, skip or `.fixme` a test silently, and never raise retries to get green.
7. **Never:**
   - edit anything under `.unified-test-suite/` (changes go upstream; `uts:check` fails)
   - add tests to suites listed under `freeze` in `unified-test-suite.config.json`
   - use a browser for API-only checks
   - print secrets
   - change config or Taskfile per environment (use env vars: `BACKEND_URL=… task test:pr`)
   - add `dotenv:` to a Taskfile
   - enable `TASK_X_ENV_PRECEDENCE`

New E2E scenarios are Playwright specs. Use the API project unless a DOM is needed, tag them statically with `{ tag: '@BDD.xx.yy.zzzz' }` when the project uses BDD IDs, and get test accounts via `identity(test.info())` from `.unified-test-suite/playwright/identity`.

## SDD projects (config has an `sdd` block)

Coding agents, in addition to the loop above:

1. **No code without an `In Progress` IPLAN.** The IPLAN's `file_manifest` names the files you may touch and the TDD cases (`tdd_ref`) your work must satisfy.
2. **TDD first, names from TDD.** Write or adjust the test named by the TDD case (`test_file` + `test_function`) and see it fail (red) before the code. To rename a test, rename the TDD case first (via its CHG); `summary.sdd.findings` reports `UTS-SDD-001` for every mismatch.
3. **Read `summary.sdd`, not only the verdict.** Your change is done when the IPLAN's TDD cases are `passed` and their BDD scenarios and EARS requirements are `verified`. `at_risk` means a paired case is missing or did not run.
4. **Browser behavior is specified by journey cards** (`tests/journeys/<BDD-ID>.journey.yaml`, protocol `.unified-test-suite/agents/JOURNEYS.md`). The QA engineer (`.agents/agents/qa-engineer.md`) and its browser subagent run them; you do not.
5. **Defects arrive as GitHub issues** filed by the QA engineer (labels `bug`, `test-gap`, `environment`, `sdd-drift`, `codify-journey`, each with a `uts-key`). Fix them under an IPLAN; the QA engineer re-verifies and closes the loop — QA never fixes, you never self-certify.
6. **Codify stable journeys.** When the QA engineer files a `codify-journey` issue for a journey with three consecutive fresh passes:
   - write a Playwright spec in the e2e layer, tagged `{ tag: ['@BDD.NN.03.xxxx', '@TDD.NN.04.yyyy'] }`, with identities from `identity(test.info())`;
   - add the e2e TDD case `TDD.NN.04.yyyy` naming the spec file and test title (through the IPLAN/CHG);
   - keep the card.
7. **Never** edit SDD documents to match the code, raise thresholds, or weaken a card's `assert` to get green. Propose a CHG instead.
