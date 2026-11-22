---
name: verified-planning
description: >-
  Use when creating or reviewing an implementation or design plan. Require a cited claim ledger, a passing deterministic gate, and bounded fresh-context independent review before calling the plan ready.
---

# Verified Planning

Plan-review rules get ritualized: the author writes "Pass 1/Pass 2" from memory
and finds nothing, because the author cannot see their own wrong assumption. This
skill replaces ritual with two forcing functions.

## 0. Where the plan lives

A development design plan lives in the owning repository's declared planning surface:

- **SDD-governed repositories** (e.g. `aiops-harness` and its thin spokes): follow the **4-Stage Planning & Change Pipeline** (sole exemption: pre-first-BRD seed drafting per `SEED_CONTRACT R1`):
  1. **Stage 0 — Classify (`sdd-chg-flow`):** Route to `HOTFIX`, `CODE2S`, `CODE2C`, `SEED2C`, `DIR2C` (strictly `C1` non-behavior/non-SDD-cascading only), or `SDD2C` (`GATE-SPEC` + `change_source: spec` for normative `.aidoc/framework/framework/**`).
  2. **Stage 1 — Upstream Design (`plans/PLAN-NNN_<slug>.md`):** Citation-verified design doc (`check_plan.py`), Two-Tier evaluation, and fresh-context multi-lens review (`<= 3` passes). Optional for trivial `C1` docs/typos or single-line bugfixes tracked directly via GitHub Issue + `C1 CHG` + scoped `IPLAN`. In SDD-governed repos, `plans/PLAN-NNN_<slug>.md` is strictly upstream design and **never replaces `CHG` or `IPLAN`**. (`PLAN-001-ONBOARDING.md`, `PLAN-001-FOUNDATION-AND-INCEPTION.md`, and `PLAN-001-ARCHITECTURE-AND-MODERNIZATION-BASELINE.md` remain Phase-1 kickoff aliases alongside `PLAN-NNN_<slug>.md`.)
  3. **Stage 2 — Governance Authorization (`docs/sdd/09_CHG/CHG-NN_<slug>.yaml`):** Formal authorization, `decision:`, and Phase 0 SDD cascade (`01_BRD..07_TDD` for `SEED2C`/`SDD2C`) before IPLAN authoring.
  4. **Stage 3 & 4 — Executable Contract (`docs/sdd/08_IPLAN/IPLAN-NN_<slug>.swf.yaml` + `docs/sdd/graph/SLICE-NN_<slug>.graph.yaml`) & Closeout:** Required `In Progress` before code/deliverable edits (`DOC_GOVERNANCE_CORE.md` §3.13 bootstrap exemption covers authoring `PLAN-NNN` → `CHG` → SDD → `SLICE` → `IPLAN` in order). **3-Way YAML Manifest Parity** (`aiops-harness iplan verify`) validates `CHG-NN.yaml` ↔ `IPLAN-NN.swf.yaml` ↔ `SLICE-NN.graph.yaml`; `plans/PLAN-NNN_<slug>.md` is gated separately by `check_plan.py`, links `CHG`/`IPLAN`/`SLICE` in metadata, and enters the 3-way YAML manifest whenever created/edited in the PR. Authors follow either **Pattern A (Pure Stage-1 Plan First)** (citing only pre-existing repo files in `## Claim ledger` and recording `Planned Authorizing CHG: CHG-NN` in header metadata before minting `CHG`/`IPLAN`/`SLICE`) or **Pattern B (Feature-Branch Phase-0 Co-Bootstrap)** (scaffolding `PLAN-NNN` → `CHG-NN` → `SLICE-NN` → `IPLAN-NN` together on the feature branch before code edits so `check_plan.py` and `iplan verify` run together). Normative files under `.aidoc/framework/framework/**` are governed by `GATE-SPEC` (`change_source: spec`, `framework/VERSION` bump from `0.96.25`), while host `aiops-harness` skills/rules/guides use `DIR2C` (`C1`).
- **Standalone and platform-tooling repositories** (no `docs/sdd/`): `plans/PLAN-NNN_<slug>.md` alone without `09_CHG`/`08_IPLAN`.

A plan never lives in `docs/superpowers/` and never in an aggregator/umbrella repo that holds no development of its own. This convention takes priority over any generic plan skill's default location.

**Documentation-First Prerequisite (`Seed -> Module -> SDD -> Code`):** In any new or unseeded repository (`docs/seed/00_index.md` absent), `PLAN-001` MUST scope and deliver the Tier-1 Seed documentation (`docs/seed/00_index.md`, `architecture/`, `adr/`, `agent-surface/`) and 4-Bucket Platform Applicability Audit before any application code phases. In existing repositories, plans that introduce or modify features must include phases/tasks to update the governing SDD layers (`01_BRD..09_CHG`) and `watched_paths` documentation prior to or atomically with code changes.

## 1. Draft with a Claim ledger

Every **load-bearing claim** in the plan — a file path, a function/method
signature, a field or key name, an event/enum value, or a behavioral assertion
("validator X ignores unknown keys", "the loop reads only Y") — goes in a
`## Claim ledger` table with the `file:line` you actually opened and read:

| #   | Claim                                 | Symbol                  | Citation                    |
| --- | ------------------------------------- | ----------------------- | --------------------------- |
| 1   | project route regex matches tenant    | `_CHATWOOT_PROJECT_ROUTE`| services/intake/app.py:136  |

The `#` column is the documented shape; a table without one is also accepted
(header + separator + rows, claim first) and reports rows by position.
**Always include the `Symbol` column** containing an exact literal identifier/token that appears within 3 lines (`PROXIMITY = 3`) of the cited `file:line`; a 3-column table (`| # | Claim | Citation |`) fails `check_plan.py` because the parser reads the prose `Claim` cell as `Symbol` when only 2 data columns follow `#`.

A claim you have not verified is written `UNVERIFIED` in the Citation column and
**cannot survive to ready**. Do not guess a line number — open the file.

**A claim that source CANNOT settle is written `PROBE: <command>`.** Some claims
are live facts — a server-side setting, an API behaviour, a runtime observation.
Reading the repository harder will never answer them, and before this state
existed the only options were to guess in prose or to stall the plan forever.
Authors guessed: one plan answered the same unmeasurable question **three
different ways across three review passes**, each fold retracting the last.

A probe is not a free pass. The gate requires the command that would settle it,
**and** that the claim text names what it **blocks** — a phase, not the plan, and
**a phase that exists**. "Exists" has exactly two shapes: a `### Phase <X>`
heading or a bold `**B…**` ID elsewhere in the plan — prose mentions and other
heading levels do not resolve. That is the point: a probe gates the phase that
depends on it and leaves the rest of the plan free to be ready.

Two rules learned from this mechanism's own first use, where both were broken:

- **The command must SETTLE the claim, not merely relate to it.** The first
  probe written under this rule asked *which bypass permits a fast-forward
  push* and gave a command that **applied a protection payload** — it never
  attempted the push, so it could not produce the answer. Write the command that
  ends the argument. If a claim is "X permits Y", the command must attempt Y.
- **Probe on a throwaway, not on the repo you care about.** That same command
  targeted the live canon repo. A probe is an experiment; run it where a wrong
  answer costs nothing.

| Citation | Means | Can reach ready? |
| --- | --- | --- |
| `path:line` | verified against source | yes |
| `UNVERIFIED` | you did not look | **no** |
| `PROBE[experiment]: <cmd>` (or bare `PROBE: <external_cmd>`) | external/throwaway live experiment gating its named `### Phase <N>` (never enters Link-0 SWF) | yes — the phase is blocked |
| `PROBE[link0]: <readonly_cmd>` (or bare `PROBE: <readonly_cmd>`) | hermetic Link-0 read-only local diagnostic (`pytest`, `ruff`, `mypy`, `true`, `echo`, `test`, `git` with zero shell metacharacters; pairs with `Status = PROBE` + `SPLIT_TO_CHAIN (CHG-<N>)` in `v2`) | yes (or `SPLIT_TO_CHAIN` in `v2`) |

- **Platform claims cite the stack.** A behavioral claim about platform
  surfaces (secrets, cloud, envs, IaC, network, identity) cites the
  stack section (`technology_stack/06-security-rbac.md §9.2`) in
  Citation — an `UNVERIFIED` platform claim cannot reach ready either.
  Plans must **never** propose **insecure options** (plaintext secrets,
  relaxed sandboxing, or `--no-verify`); under **Missing Tools**, halt and
  escalate via `approval-gate` if a required tool is absent from
  `technology_stack`.

The **symbol is authoritative; the line is an advisory hint.** If a later edit shifts a cited line, the gate
still passes (the symbol is found elsewhere) and only **warns** that the line drifted — only a genuinely
**absent** symbol fails. Run `check_plan.py --fix <plan>` to re-point drifted line numbers (unambiguous
symbols) automatically.

## 2. Run the gate (before dispatching any review)

```sh
python3 ${AGENTS_CONFIG_DIR:-~/.agents}/skills/verified-planning/check_plan.py <plan>   # canonical
python3 .agents/skills/verified-planning/check_plan.py <plan>        # repo copy — what CI and the hook run
```

**Prefer the canonical path.** The repo copy exists so the gate workflow and the
pre-commit hook can run inside a checkout; it is a committed *gate*, not a
second home for this skill. A repo must never carry its own `SKILL.md`: the user
skill masks that project copy, leaving a duplicate that can drift while appearing
authoritative.

**Tune the gate in your caller, never in the reusable workflow.** `install.sh`
ships `.github/workflows/plan-gate-reusable.yml` (canon-owned, refreshed on
update) but never your caller — create `.github/workflows/plan-gate.yml` once
from this snippet and tune `runner` / `python-version` there; updates can
neither skip nor clobber it:

```yaml
name: plan-gate
on:
  pull_request:
    paths: ["**/PLAN-*.md", "PLAN-*.md"]
permissions:
  contents: read
jobs:
  gate:
    uses: ./.github/workflows/plan-gate-reusable.yml
    with:
      runner: ubuntu-latest        # self-hosted label here if needed
      python-version: "3.12"
```

`timeout-minutes` stays 10 inside the reusable file — GitHub disallows
expressions there. Migrating from the pre-split workflow: replace the old
file's whole content with this caller; your trigger paths carry over.

The gate supports PR callers only: `base` comes from the pull-request
event, so a push/schedule caller fails with a specific error instead of
gating. On air-gapped runners with system python3, skip the toolchain
download with `setup-python: false` alongside the inputs above. Wire
`runner:` from a literal label only — never from PR event data: a steered
runner could report gate-success without running the gate.

On other forges than GitHub, install with `install.sh --no-ci` (no
`.github/` tree) and run the same command from the forge's own CI on
`PLAN-*.md` changes — the gate is stdlib-only `python3`, nothing else
(`pytest` is needed only to run the gate's own test-suite, never the gate).

On GitHub, mark `plan-gate` a required check in branch protection once it
is green — an unrequired gate is advisory, and plans can land around it.

To gate commits as well as PRs, paste `precommit-hook.snippet.txt` under
`repos:` in the repo's `.pre-commit-config.yaml` (`pre-commit` needed);
it runs the repo-copy gate on `PLAN-*.md` at commit time.

On success it prints `ok <plan> — verified N citation(s)[, P probe(s)], M review pass(es)`. It
checks *form and citation-resolution*; it cannot check that the review was
genuinely independent or that the ledger is complete — that is step 3's job.

Run it **before** dispatching the independent review, and re-run it after any
fold that edits the plan: a broken citation costs this script milliseconds and
a reviewer a full pass.

- **Scaffold a new plan:** `check_plan.py --init <plan>` appends the
  `## Claim ledger` + `## Review log` sections (idempotent).
- **Cite paths relative to the plan's own repo root** (nearest `.git`
  ancestor; the plan's own directory when there is no checkout).
  For **cross-repo** citations (a sibling repo), add `--root <dir>` — citations
  resolve against the plan's repo first, then each `--root` in order. Point
  `--root` at operator-owned paths only: ledger text is PR-controlled, and a
  root outside operator control turns the gate's pass/fail into a presence
  oracle for files the author cannot otherwise open.

## 3. Mandatory independent review (Multi-Lens Subagent Protocol — 3 Reviews Max)

Once the gate is green, immediately dispatch the **`verified-planning-reviewer` agent** (a
fresh-context subagent) to execute a **Multi-Lens Review (up to 3 reviews max)** (`Review 1 -> Fold 1 -> Review 2 -> Fold 2 [-> Review 3 (max) -> Fold 3]`).
Your own re-read does NOT count as the subagent review. The independence
contract this rests on — judge ≠ generator, the reviewer never sees your
reasoning, never re-author the prompt ad hoc — is owned by the `second-opinion`
skill; this step applies it to plans. If the agent type is unavailable, fall
back to the `Agent` tool (`invoke_subagent`) with an explicit `model` rather than reviewing it
yourself. The gate has already
proven every citation resolves to a real symbol — the reviewer must **not**
re-verify existence. Its job is the three things a script cannot do:

1. **Semantic truth** — does each cited symbol actually mean what its claim
   asserts, read in context;
2. **Completeness** — load-bearing claims missing from the ledger;
3. **Wrong assumptions & Critical/Major/Medium gaps** — the adversarial hunt for what the author cannot see.

### 3.0 Adaptive Multi-Lens Selection (Google, Anthropic, AWS, Microsoft) & 3-Review Max Cap

Because the review loop is capped at **3 subagent reviews max** (`Review -> Fold` per round, minimum 2 passes required by `check_plan.py`, capped at **3 reviews max**), review subagents must exhaustively identify **all Critical, Major, and Medium gaps, issues, conformance traps, and bugs** by selecting the **2–4 most suitable industry lenses/approaches** for the specific plan (do NOT apply all simultaneously; pick the lenses that fit the plan's domain):

- **Google (Design Doc & SRE Rigor):** System invariants, boundary/edge-case verification, SLIs/SLOs, blast-radius containment, rollback safety, hermetic testing, and maintainability/simplicity.
- **Anthropic (Agentic Systems & Evaluator-Optimizer):** Evaluator-Optimizer independence (`Judge != Generator`), constitutional invariant enforcement, subagent orchestration & context isolation, tool/schema contract precision, untrusted input nonce fencing, and minimal review surface.
- **AWS (Well-Architected & Operational Excellence):** One-way vs. two-way door decisions, IAM least privilege & zero-trust secret custody, idempotency, distributed SAGA/partial-failure compensation, and Everything-as-Code (OpenTofu / declarative config) convergence.
- **Microsoft (SDL & STRIDE Threat Modeling):** STRIDE security threat modeling (Spoofing, Tampering, Repudiation, Information Disclosure, DoS, Elevation of Privilege), defense-in-depth, backward-compatible schema/contract evolution, and supply-chain/CI gate integrity.

#### 3.0.1 Industry Best Practices Mandate (Never Invent Custom Wheels)

Review subagents must benchmark the plan against established, authoritative industry standards and best practices directly relevant to the plan's domain tasks, rejecting attempts to invent custom or idiosyncratic patterns:

- **Security & Authorization:** NIST SP 800-63 / 800-207 (Zero Trust), OWASP Top 10 & API Security Top 10, Microsoft STRIDE threat taxonomy, principle of least privilege, ephemeral secret delivery (`/dev/shm` mode `0600`).
- **Protocols & Interfaces:** Model Context Protocol (MCP specification), CloudEvents v1.0.2, CNCF Serverless Workflow v0.8, OpenAPI 3.1 / JSON-Schema Draft 2020-12.
- **Reliability & SRE:** Google SRE invariants (error budgets, circuit breakers, graceful degradation, health probes), AWS Well-Architected Framework (two-way door reversible decisions, idempotent handlers, distributed SAGA compensation).
- **Architecture & Infrastructure-as-Code:** Clean Architecture / Hexagonal Ports & Adapters, Twelve-Factor App methodology, OpenTofu / declarative state convergence, Semantic Versioning 2.0.0.
- **Agentic Systems & LLMs:** Anthropic Evaluator-Optimizer pattern (`Judge != Generator`), constitutional invariant enforcement, CaMeL prompt injection fencing, token economy and bounded sliding windows.

Review findings must cite deviations from these established industry standards and require standard alignment before clearing a plan pass.

**Up-to-3-Pass Review Log Workflow (`Pass 1..3`, `_TASK_PASS_CAP_V2 = 3`):**

1. **Pass 1 (`### Pass 1 - <date>` [or `- independent`]) $\to$ Fold 1:** Initial draft and `check_plan.py` gate check (or first fresh-context subagent multi-lens audit). Dispatch fresh-context subagent(s) applying the 2–4 selected lenses to surface all `Critical`, `Major`, and `Medium` gaps with `file:line` evidence. Fold all findings into the plan and `## Claim ledger` (tagging new claims `NEW@pass2`) and re-run `check_plan.py`.
2. **Pass 2 (`### Pass 2 - <date> - independent`) $\to$ Fold 2:** Fresh-context subagent review verifying every prior fix against live source files, confirming zero broken references, and surfacing any remaining `Critical`, `Major`, or `Medium` gaps. If Pass 2 returns zero findings (and `100% LOCKED` tasks in `v2`), mark the plan `ready`.
3. **Pass 3 (`### Pass 3 - <date> - independent` — Final Convergence Pass, 3 Passes Max) $\to$ Fold 3:** If Pass 2 required folding, dispatch the final fresh-context subagent review to verify Fold 2, re-run `check_plan.py --hash-tasks --fix`, and close with either `**Result:** ready — no further findings.`, `**Disposition:** SPLIT_TO_CHAIN (CHG-<N>)`, or `**Disposition:** ESCALATE`.

**Define the unit once: a *pass* is a logged review** recorded as
`### Pass N - <date>`. At least one pass MUST be dispatched — the
`verified-planning-reviewer` agent, or the logged fallback (Agent tool + explicit
`model`) when the agent type was unavailable. Your own re-read counts as a
pass, but never as the dispatched one.

Mark a dispatched pass `### Pass N - <date> - independent` (`- fallback`
when the logged fallback was used); a self-review pass carries no marker.
Fold in findings,
then re-dispatch until a pass returns **zero load-bearing
findings** — capped at **3 reviews max** (a circuit-breaker). If
review 3 still has load-bearing findings after folding, STOP and surface the open items to the
human instead of dispatching a fourth.

### 3.1 Fold discipline — the loop only converges if the fold is bounded

**Measured, across 23 plans in one workspace: the median plan reached ready at
4.5 passes, and only 5 of 12 made it inside the cap of 3.** The cap was firing on
the *median* case, which turns a real escalation signal into noise. The cause is
not reviewer zeal — it is that **a fold writes new prose, and new prose is new
review surface**. Two consecutive passes on one plan each reported that *most* of
their findings were introduced by the previous fold: ~10 retired, ~9 created, net
progress ≈1 per pass.

Contrast the same loop over **code**: it converged in 3, because a code fix is
checked by a test the same session. The gate here checks that citations
*resolve*; nothing checks that the prose around them is *true*. So:

1. **Subtractive by default.** Deleting a claim, narrowing it, or striking a
   phase is free. **Adding** one costs: it needs its own citation and a
   `NEW@passN` marker, so the next reviewer knows exactly where the unreviewed
   surface is.
2. **Growth is a defect signal, not progress.** If the ledger or the plan grew
   between passes, the fold over-corrected. The gate prints the citation count
   every run — compare it to last pass. **If pass N+1 raises more load-bearing
   findings than pass N, stop folding and CUT SCOPE.** Split the plan, or drop a
   phase to its own plan. Do not fold a fourth time.
3. **Retract without over-swinging.** The commonest fold defect is replacing a
   wrong certainty with a *new, unmeasured* certainty. When a reviewer refutes a
   claim, the safe fold deletes it or marks it `PROBE` — it does not assert the
   opposite.
4. **Fix the ledger, not just the prose.** A retraction in the body while the
   ledger row still asserts the old claim leaves the plan's own instrument of
   record carrying the thing you just retracted. Both move together.
5. **A scope cut breaks REFERENCES, not facts — re-check them by hand.**
   Measured: after a cut, an independent pass found **no** surviving semantic
   falsehood in the source-cited body — the place every previous pass had found
   them — and instead found the plan's internal wiring in pieces. Renumbering
   collided ledger IDs and left nine prose references resolving to the wrong
   row; a phase lost its entire body when the section it pointed at was deleted;
   two decisions were orphaned. **Subtractive folding is safer on facts and more
   dangerous to references**, which is the opposite of what you would predict.

   After any cut, verify: ledger IDs are unique; every `Claim N` in prose
   resolves to the row you mean; every `§N` pointer exists; every phase still
   owns its deliverable. The gate checks none of this — it verifies each row's
   own citation and is blind to the plan's internal consistency.
6. **A cut can break things OUTSIDE the plan.** Shipped documents delegate to
   plans by name. A cut that deleted a section and a phase left merged canon
   pointing at `PLAN-NNN §3` and `PLAN-NNN A4`, neither of which still existed.
   Grep the repo for references to the plan before cutting, and move them in the
   same change.

### 3.2 What must converge

**Zero findings over the whole artifact is the wrong target** for a document that
grows. Converge the **decision set**: the claims that change what gets built.
A finding of the form "the plan should also mention X" is not load-bearing unless
X changes an action. Say so in the fold and move on — folding it grows the
surface for no decision.

### 3.3 Autonomous Open-Question Resolution (Tier-1 4-Pillar Persona Matrix + Auto-Continue)

Whenever the authoring or reviewing agent encounters open questions during conversation turns or plan draft rounds:

1. **Generate 3 Recommendations per Open Question:** Provide **3 distinct recommendations/options** for each question with clear rationale and trade-offs.
2. **Review & Score (1.0 to 5.0) via 4-Pillar Persona-Decomposed Matrix:** Critically review the options across four distinct persona perspectives:
   - **Pillar 1: Architectural & EaC Alignment** (Canon Architect perspective: standards, SDD/EaC compliance, 3-layer plane boundary, schema integrity)
   - **Pillar 2: Security & Blast Radius** (Security/SRE perspective: least privilege, STRIDE threat containment, secret isolation, rollback feasibility)
   - **Pillar 3: End-User / Agent DX** (Developer & CLI User perspective: naming ergonomics, TUI readability, prompt context/token efficiency)
   - **Pillar 4: Platform Owner Day-2 Ops** (Platform Engineer perspective: operational simplicity, upgrade stability, drift risk, fleet maintenance)
3. **Compute Composite Score & Auto-Select:** Compute the unweighted composite average ($\sum \text{Pillars} / 4$). Adopt the highest-scoring recommendation directly into the decision record and **continue execution without asking an operator**.

### 3.4 Plan Architecture Decision Scorecard (Tier-2 100-Point Well-Architected Model)

For strategic architectural decisions in `plans/PLAN-*.md`, evaluate competing implementation options using the **100-Point 5-Pillar Well-Architected Scorecard**:

| Pillar | Max Weight | Evaluation Criteria & Minimum Standard |
|---|:---:|---|
| **1. Security, Fencing & Least Privilege** | **25 pts** | STRIDE threat mitigation, cryptographic token/warrant fencing, secret delivery in `/dev/shm`, zero hardcoded credentials, jail containment. |
| **2. Reliability, Rollback & Blast Radius** | **25 pts** | Failure domain containment, two-way door reversible migrations, 1-click compensation/undo, automated rollback pathways. |
| **3. Operational Excellence & EaC Convergence** | **20 pts** | Clean 3-layer plane boundary, OpenTofu/declarative config compliance, SDD artifact graph parity, zero drift. |
| **4. Developer & Agent Experience (DX)** | **15 pts** | Intuitive naming and ergonomics, clear TUI visualization, automated discovery, diagnostic error reporting. |
| **5. Performance & Token/Resource Economy** | **15 pts** | Prompt token footprint, context efficiency via lazy loading, sub-second latency, bounded memory usage. |
| **TOTAL SCORE** | **100 pts** | **Passing Bar: $\ge 80 / 100$ total score; no pillar may score $< 50\%$ of its allocated weight.** |

- **$\ge 80 / 100$ Total**: Approved for implementation (`READY`).
- **$70 - 79 / 100$ Total**: Conditional approval (`SHIP-WITH-FIXES` — must resolve specific pillar deficits prior to merge).
- **$< 70 / 100$ Total or any pillar $< 50\%$ weight**: Rejected (`BLOCKER` / Redesign proposal).

## 4. Plan Gate v2 (Opt-In Task Convergence & Split Cascade)

- **Opt-in activation:** Declare a standalone HTML comment `<!-- plan-gate: v2 -->` outside fenced code blocks/backticks (or pass `--v2` to `check_plan.py`; use `--init --v2` to scaffold without altering default `v1 --init`). Existing `v1` plans remain unaffected.
- **`## Task convergence` table & Stage-1 `--hash-tasks`:** Record `| Task ID | SHA-256 | P1 | P2 | P3 | P4 | Total | Status |` with valid statuses `LOCKED`, `FIX`, `STALE`, `PROBE`, or `REDESIGN`. At Stage 1 (before Stage-3 `SLICE-NN.graph.yaml` exists), run `check_plan.py --hash-tasks [--fix] <plan>` to compute/pin deterministic section-excluded SHA-256 hashes over each `### Phase ... (<Task ID>)` block (mapping Stage-1 `TASK-1..N` to Stage-3 `TASK.08.NN.a001..a00N` graph nodes hashed by `compute_task_canonical_hash` in `aiops-harness iplan evaluate-split`). `LOCKED` requires a non-zero 64-hex lowercase canonical `SHA-256` matching the body task block, `Total >= 16.0`, every pillar `>= 2.5`, and an `independent` / `fallback` pass. `STALE` contributes `0.0%` credit to `plan_health_pct`.
- **Automated `v2` Scorecard, Open-Question, Reference & `PROBE` Gates:** In `v2`, `check_plan.py` also validates Tier-2 100-Point Scorecard tables (`>= 80/100`, every pillar `>= 50%` floor), Tier-1 4-Pillar Open-Question tables (`>= 3` options, composite average, highest composite selected), unique ledger `#` IDs (including alphanumeric `3b`/`5b`), prose `Claim <N>` resolution, `NEW@pass<N>` bounds, and `PROBE[link0]:` vs `PROBE[experiment]:` sandbox coherence (never cite mutable counts like `total_changes: 28` as a `Symbol`; cite stable YAML keys `total_changes:` / `total_plans:`).
- **Hard 3-pass circuit breaker & split cascade:** In `v2`, `Pass 4+` always fails. Terminal convergence requires either (a) `100% LOCKED` tasks + zero findings (`**Result:** ready — no further findings.`), or (b) `**Disposition:** SPLIT_TO_CHAIN (CHG-<N>)` to split unresolved/probe tasks via `iplan evaluate-split`. `**Disposition:** ESCALATE` always halts exit `1` for human intervention.

## What "ready" means

Claim ledger has zero `UNVERIFIED` rows (a `PROBE` row is fine — it blocks its
named phase, not the plan) and the gate passes; the Review log has
**≥2 passes**, at least one of them dispatched (per the definition above),
and the final pass states zero findings — end it with
an explicit `**Result:** ready` line (or "no new/further/load-bearing findings").
Where the `superpowers` plugin is installed, this layers on top of
`superpowers:writing-plans` — it does not replace or fork it. Without the
plugin, this skill stands alone.
