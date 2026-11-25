---
name: sdd-iplan
description: >-
  Author the scoped or full IPLAN (or workflow IPLAN) the CHG points at: In Progress status, authorizing CHG, subtype selection, and a file manifest covering every touched deliverable — no execution without it.
---

# SDD IPLAN Author

> Framework skill — routes to `.aidoc/framework/framework/layers/08_IPLAN/IPLAN-TEMPLATE.yaml`
> (or `IPLAN-SWF-TEMPLATE.yaml` + `IPLAN-GRAPH-TEMPLATE.yaml` for `subtype: workflow` agent/state-machine DAGs)
> and the executing example `.aidoc/framework/framework/archive/CHG-*/IPLAN-*.yaml`.
> Paths below use `<skills-root>` = `.agents/skills/` (framework root: `.aidoc/framework/framework/`).
> The authorizing CHG (`sdd-chg-author`) must exist first, except for
> seed-phase drafting before the first BRD — the sole change needing
> neither object.

## 1. The gate (non-negotiable)

No deliverable (code, agent workflow DAG, generated media, contract, docs, or IaC) may be executed without an IPLAN, and the IPLAN must be `In Progress`,
reference its authorizing CHG (`source_chg` / `authorizing_chg`), and list every modified file in
`file_manifest` — one entry per path with `action` (create/modify) and
`status` (PENDING/TODO → DONE), mirrored in `traceability.code_inventory` (the universal deliverable sink). A manifest that misses a file the diff touches fails
the gate the same way a missing IPLAN does.

Keep Stage 1 and Stage 3 distinct: `plans/PLAN-NNN_<slug>.md` is the upstream
human/agent design plan gated by `check_plan.py` and **never substitutes** for
Layer-08 `docs/sdd/08_IPLAN/IPLAN-NN_<slug>.swf.yaml` in SDD-governed repos.
`aiops-harness iplan verify` enforces strict 3-way YAML manifest parity across
`CHG-NN.yaml` ↔ `IPLAN-NN.swf.yaml` ↔ `SLICE-NN.graph.yaml` (list `PLAN-NNN`
inside the 3-way manifest whenever created/edited in the PR). Under the
`DOC_GOVERNANCE_CORE.md` §3.13 bootstrap exemption, author upstream artifacts on
the feature branch in strict order (`PLAN-NNN` [optional] → `CHG-NN` → upstream
SDD layers → `SLICE-NN` → `IPLAN-NN`) before editing deliverables.

## 2. Shape follows the flow

Match the IPLAN shape to the classified flow: DIR2C takes a scoped IPLAN
(manifest + steps + rollback only); CODE2C takes a bugfix-subtype IPLAN
(`parent_iplan` + `source_chg`, repair-scoped manifest, rollback); full
flows take the full form with all code steps and the complete manifest.
Bug fixes ride their IPLAN's authorizing CHG when it is active, otherwise
the bugfix vehicle (C1 CHG + bugfix IPLAN, parent standing).

## 3. Execute and close out

Work the manifest TODO → DONE, keeping `phase_findings` honest (guards
observed red, fixes declined with reasons). Verification commands named in
the IPLAN run per step, not once at the end. When `platform-paperclip` tracks the execution DAG, log step milestones via `aiops_paperclip_post_notice`, attach verified artifacts via `aiops_paperclip_record_artifact`, queue unrelated follow-up TODOs via `aiops_paperclip_queue_todo`, update state/metadata via `aiops_paperclip_update`, and distill task completion into Engramory via `aiops_task_distill_engramory`. Flip status to Implemented
only when the manifest is fully DONE and the suites are green on the head
that carries the work.

## 4. Link Kinds, Link 0 Probe & Pass-2 Auto-Chains

- **Link classification (`link_kind`):** Declare `probe`, `implementation`, or `closeout` consistently across envelope, `document_control`, and `metadata` (verified by `SWF-L010` and `graph_manager.py`).
- **Link 0 Read-Only Probe SWFs (`link_kind: probe`):** Must keep `file_manifest` empty (`[]` or `{"files": []}`), declare zero `usedForCompensation`/`compensatedBy` states, and run only bare allowlisted read-only `runCommand` actions (`pytest`, `ruff`, `mypy`, `git`, `true`, `echo`, `test` — no shell metacharacters, mutating flags, `target_file`/`target_files`, or path escapes).
- **Convergence split routing & closeout parity:** Route Pass-2/3 splits deterministically via `iplan evaluate-split` (`aiops_harness/cli/main.py`) / `aiops_iplan_evaluate_split` (`aiops_harness/interfaces/mcp.py`), preserve `convergence_control.locked_task_hashes`, and reconcile closeout union parity across `link_kind: closeout` before chain completion.
