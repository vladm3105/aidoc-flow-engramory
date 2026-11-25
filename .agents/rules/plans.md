---
paths:
  - "**/PLAN-*.md"
  - "**/IPLAN-*.md"
  - "**/docs/sdd/08_IPLAN/**"
  - "**/plans/**"
  - "**/work_plans/**"
---

# Plan status governance

**The status of an IPLAN or a working plan MUST reflect its actual state.** A status is a claim
about reality, not a label set once at authoring time.

**When to change it** — in the same session and the same change as the state change, never deferred
to a later cleanup pass:

| Event | Status |
|-------|--------|
| Authored, not started | `Draft` |
| Execution starts — first task, file or session of the plan is worked | `In Progress` |
| Every declared item is done AND verified | `Completed` |
| Work stopped and will not resume | `ABANDONED` (state the reason) |

**Rules**:
- Update the status at its **source**. Where the plan document is generated, edit the generator's
  authoring data and regenerate — a hand-edit to a generated document is destroyed by the next run.
  Where the status is **derived** from progress fields (files done, sessions done), update those
  fields as work lands rather than only at the end; the derived status then tracks reality on its own.
- The plan document and its registry/index row MUST agree, and both move in the same change.
- Record the transition (`from` → `to`, date, reason) wherever the index keeps status history.
- Do not mark `Completed` on unverified work. "Verified" means the repo's own gates and test
  commands were run and their output read.
- A stale status is a defect of the same class as a wrong count: correct it, and say what it was.

**Where an issue tracks the work, the issue state is the status and there is only one of them.**

| Plan status | Issue |
|---|---|
| `Draft` | open, no `status:in-progress` label |
| `In Progress` | open + `status:in-progress` (or assigned) |
| `Completed` | **closed by the merging PR** (`Closes #N`), never by hand |
| `ABANDONED` | closed as **not planned**, with the reason in a final comment |

- **Do not maintain a status field in a document AND an issue.** Pick the one the
  work is tracked in and let the other link to it. Two records of one status is
  the same defect as two records of one count, and it fails the same way.
- **A generated plan is the exception and keeps its derived status** — hand-editing
  it is destroyed by the next generator run, so the generator's authoring data
  stays the source and the issue links to the plan rather than restating it.

## When a plan is a DOCUMENT rather than an issue

Follow the **4-Stage Planning & Change Pipeline** in SDD-governed repos (sole exemption: pre-first-BRD seed drafting per `SEED_CONTRACT R1`):
- **Stage 0 (Classify):** Route via `sdd-chg-flow` (`HOTFIX`, `CODE2S`, `CODE2C`, `SEED2C`, `DIR2C` [strictly `C1`], `SDD2C`, or `GATE-SPEC` for `.aidoc/framework/framework/**`).
- **Stage 1 (Upstream Design — `plans/PLAN-NNN_<slug>.md`):** Warranted when work needs a gated **Claim ledger** (`file:line` + `symbol`), Two-Tier evaluation, or multi-lens independent review (`check_plan.py`). **Trivial `C1` docs/typos or single-line bugfixes skip `plans/`** and track directly via GitHub Issue + `C1 CHG` + scoped `IPLAN`. Never let `plans/PLAN-NNN_<slug>.md` replace `09_CHG` or `08_IPLAN` in SDD-governed repos (`NEXT_TASKS.md` and `work_plans/` bugfix files are superseded by the issue tracker).
- **Stage 2–4 (`09_CHG` + `08_IPLAN` + `graph/` + Paperclip/Engramory Flywheel):** `CHG-NN` authorizes without code steps; `IPLAN-NN` (`In Progress`) + `SLICE-NN` bind execution and verify strict **3-way YAML manifest parity** via `aiops-harness iplan verify` (`CHG-NN.yaml` ↔ `IPLAN-NN.swf.yaml` ↔ `SLICE-NN.graph.yaml`). Ingest `CHG-NN` + `IPLAN-NN` into centralized `platform-paperclip` via `aiops_chg_sync_paperclip`, log notices (`aiops_paperclip_post_notice`), queue follow-up TODOs (`aiops_paperclip_queue_todo`), and distill completion into Engramory (`aiops_task_distill_engramory`). Standalone non-SDD repos without `docs/sdd/` use `plans/PLAN-NNN_<slug>.md` alone.

**The test is not size, it is whether a gate reads it.** `check_plan.py` validates upstream design plans (`plans/PLAN-NNN_<slug>.md`); `chg_lint.py`, `swf_lint.py`, and `iplan verify` validate Layer 09/08 execution contracts.

Naming, where an upstream design file *is* warranted: `plans/PLAN-NNN_<slug>.md` (with `PLAN-001-ONBOARDING.md`, `PLAN-001-FOUNDATION-AND-INCEPTION.md`, and `PLAN-001-ARCHITECTURE-AND-MODERNIZATION-BASELINE.md` as Phase-1 kickoff aliases).

**Paths are per-repo.** Read them from the repo's own governance table (its `AGENTS.md`). In `aiops-harness`-governed spokes, upstream design plans live in `plans/PLAN-NNN_<slug>.md`, paired downstream with Layer-09 change requests in `docs/sdd/09_CHG/CHG-NN_<slug>.yaml`, Layer-08 workflow execution plans in `docs/sdd/08_IPLAN/IPLAN-NN_<slug>.swf.yaml`, and graph slices in `docs/sdd/graph/SLICE-NN_<slug>.graph.yaml`. In `aidoc-flow`, the umbrella `AGENTS.md` declares plans **"Not adopted"** at umbrella level; the submodule's own declaration is canonical.

## Task Convergence & CHG Chain Sync (`plan-gate: v2`)

- Plans opting into `<!-- plan-gate: v2 -->` / `check_plan.py --v2` must keep `## Task convergence` hash-pinned rows (`LOCKED`, `FIX`, `STALE`, `PROBE`, `REDESIGN`) synchronized with execution state across `IPLAN-NN_*.swf.yaml` and `SLICE-NN_*.graph.yaml` (mapping Stage-1 `TASK-1..N` hashes via `check_plan.py --hash-tasks [--fix]` to Stage-3 `TASK.08.NN.a001..a00N` graph nodes).
- Distinguish external Claim-ledger phase gates (`PROBE[experiment]: <cmd>`) from Link-0 read-only local diagnostic probes (`PROBE[link0]: <readonly_cmd>` restricted to `pytest`, `ruff`, `mypy`, `true`, `echo`, `test`, `git` with zero shell metacharacters). Keep `## Review log` within `2..3` `### Pass N` headings (`_TASK_PASS_CAP_V2 = 3`).
- When a `v2` review ends with `**Disposition:** SPLIT_TO_CHAIN (CHG-<N>)`, register every generated Link 0..N sub-IPLAN under the governing CHG's `implementation.chain_control` / `implementation.iplan_chain` within `scope.files_to_touch` and reconcile all upstream links before marking `Completed`.
