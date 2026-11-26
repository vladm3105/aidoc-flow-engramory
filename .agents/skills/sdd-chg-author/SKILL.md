---
name: sdd-chg-author
description: >-
  Author a C1/C2/C3 CHG that passes chg_lint first time: complete the §3.4 checklist before writing, validate per §3.4.1 after, and never put code steps in the CHG.
---

# SDD CHG Author

> Framework skill — routes to `.aidoc/framework/framework/layers/09_CHG/CHG-TEMPLATE.yaml`,
> `.aidoc/framework/framework/governance/DOC_GOVERNANCE_CORE.md` §3.4/§3.4.1, and
> `.aidoc/framework/sdd_doc_lint/chg_lint.py` (CHG-L001–L021).
> Paths below use `<skills-root>` = `.agents/skills/` (framework root: `.aidoc/framework/framework/`).
> The flow must already be classified (`sdd-chg-flow`); the issue already
> validated (`sdd-chg-triage`).

## 1. Checklist before writing (§3.4, blocking)

Complete `DOC_GOVERNANCE_CORE.md` §3.4 BEFORE writing. For a docs/tooling
change the load-bearing items are: read every referenced file (item 1),
couple or split issues deliberately (7), name automated test specs (8),
declare the SDD-lifecycle position (11–12: new files only means no
archive/rewrite steps), and keep every step in `documentation_sync` /
`iplan_creation` phases with no `code_implementation` anywhere (13–14).

## 2. Write the CHG

Mirror the latest `framework/archive/CHG-*/CHG-*.yaml` DIR2C vehicle:
`metadata` (`change_level`, `change_source`, `semver_impact`,
`version_action`, `entry_gate`), `change_control` (status starts at
`Proposed`, lifecycle Proposed → Approved → In-Progress → Implemented →
Completed), a non-empty `decision:` block, `problem_statement` with an
explicit `scope` file list, and `implementation` carrying only the
`iplan_ref` plus phased milestones. DIR2C states `change_source: direct`,
`C1`, and `sdd_lifecycle: []` — an empty lifecycle with a code manifest and no
`direct` source is a CHG-L013 misfit.

When an upstream Stage 1 design plan (`plans/PLAN-NNN_<slug>.md`, `ready` under
`check_plan.py`) precedes the CHG: cite it in `decision:` and
`change_description:`, include `plans/PLAN-NNN_<slug>.md` in `scope.files_to_touch`
when created/edited in the same PR (maintaining strict 3-way YAML manifest
parity across `CHG-NN` ↔ `docs/sdd/08_IPLAN/IPLAN-NN` ↔ `SLICE-NN`), keep
`implementation.artifacts_modified` limited to `IPLAN-NN` under `DIR2C`
(`CHG-L006`), and never put code steps inside the CHG (`CHG-L003`).

## 3. Validate before committing (§3.4.1, mandatory)

Run `uv run python3 .aidoc/framework/sdd_doc_lint/chg_lint.py <chg-file.yaml>` and clear every
error (exit 0 is the only pass): lifecycle order, gate approval on C3,
no code steps, a referenced IPLAN, SDD-first order, lifecycle attribution
with `author` (+ `chg_ref` for modules), no premature completion, and the
mandatory `decision:` block. Then hand the IPLAN itself to `sdd-iplan`.

## 4. Open Questions & `decision:` Block Resolution

If the agent has open questions when authoring the CHG or its Section 1B `decision:` block:
1. **Generate 3 Recommendations per Open Question:** Provide **3 distinct recommendations/options** for each question with clear rationale and trade-offs.
2. **Review & Score (1 to 5):** Critically review the generated recommendations and **score each from `1` to `5`** based on **best practices** (platform standards, security, SDD/EaC compliance, maintainability) and **practical use** (operational simplicity, execution feasibility, blast radius), where a higher score is better (`5` = best), and highlight the top-scored recommendation.
3. **Use the Highest-Scored Recommendation & Continue:** Record the top-scored recommendation in `decision.choice` (and the remaining 2 options in `decision.alternatives_considered`) and continue without asking an operator.

## 5. CHG-Governed IPLAN Chains (`CHG-L021`)

- When a Pass-2 `SPLIT_TO_CHAIN` produces multiple sub-IPLANs under a single governing CHG, declare optional `implementation.chain_control` (`chg_id`, `split_depth <= max_split_depth`, `shared_contracts`), `implementation.iplan_chain` (`iplan_id`, `link_kind`, `order`, `reconciled`, `lifecycle_status`, `depends_on`, `target_files`/`create_paths`/`modify_paths`, `merged_sha`), and optional `cross_plan_leases`.
- **Scope ceiling & read-only metrics:** `scope.files_to_touch` is the hard `approved_scope_files` ceiling for every chain link (`probe` links must keep mutable file lists empty). Use `compute_chg_chain_health_rollup` read-only metrics to audit progress; `CHG-L021` fails on out-of-scope targets, empty scope with non-empty targets, cycles, `ORDER_INVALID`, `UPSTREAM_UNRECONCILED`, `LEASE_OVERLAP`, or `UNSERIALIZED_FILE_OVERLAP`.

## 6. Centralized Paperclip Execution Sync

- Git `09_CHG` + `08_IPLAN` + `SLICE-*` files remain the authoritative source of truth; `platform-paperclip` acts as the centralized execution ledger and cross-agent backlog.
- After 3-way SDD parity passes (`aiops-harness iplan verify`), ingest the `CHG-NN` + `IPLAN-NN` task tree into Paperclip via `aiops_chg_sync_paperclip` (or `aiops-harness chg ingest-paperclip`) and link follow-up discoveries via `aiops_paperclip_queue_todo`.
