---
name: sdd-chg-flow
description: >-
  Classify any artifact change into exactly one CHG flow (HOTFIX, CODE2S, CODE2C, SEED2C, DIR2C, SDD2C) before authoring the CHG, so the vehicle matches the router instead of the guess.
---

# SDD CHG Flow Classifier

> Framework skill — routes to `.aidoc/framework/framework/governance/CHG_REQUEST_FLOWS.md` §1 (normative router).
> Paths below use `<skills-root>` = `.agents/skills/` (framework root: `.aidoc/framework/framework/`).

Every artifact change MUST be classified into exactly one flow — or a governed
non-flow path — before a CHG is authored. Classify in router order; the first
matching row wins.

## 1. The router (first match wins)

| Code | Flow | Traversal | Trigger |
|---|---|---|---|
| HOTFIX | `hotfix` | Code → Prod (post-hoc Doc) | Critical production issue needing a fix before authorization |
| CODE2S | `code_to_sdd` | Code → IPLAN → SDD | Working code preceding its specs (reverse-authored) |
| CODE2C | `code_to_code` | Defect → IPLAN → Code | Defect traceable to a Completed/Verified parent IPLAN |
| SEED2C | `seed_to_code` | Seed → Module → SDD → Code | Product/design change to an implemented product |
| DIR2C | `iplan_to_code` | IPLAN → Code/Docs | Request unrelated to any prior IPLAN, no product-behavior change |
| SDD2C | `sdd_to_code` | BRD → … → Code → EVAL | New product or layer chain, no prior implementation |

Legacy aliases: HOTFIX = Emergency, CODE2S = Type-R, CODE2C = F4, SEED2C = F3, DIR2C = F2, SDD2C = F1.

## 2. Read the row, not just the code

Each router row fixes `change_source`, `change_level`, entry gate, SDD-cascade
duty, and IPLAN shape. DIR2C takes `change_source: direct`, C1, GATE-CODE,
`sdd_lifecycle: []`, and a scoped IPLAN (manifest + steps only) — and nothing
else satisfies CHG-L013. A fast-path flow touching 03_EARS/04_BDD/05_ADR/06_SPEC
trips CHG-L019: that change is SDD2C whether it feels small or not.

## 2.1 Relationship to Upstream Design Plans (`verified-planning`)

In SDD-governed repos, Stage 0 classification feeds the remaining stages of the 4-Stage Pipeline:

- **Stage 1 (`plans/PLAN-NNN_<slug>.md`):** Draft and independently review an upstream design plan (`verified-planning` + `check_plan.py`) before authoring `CHG` + `IPLAN` whenever the change involves architecture, trade-offs, or multi-file design. Skip `plans/` for trivial `C1` docs/typos or single-line fixes tracked directly via GitHub Issue + `C1 CHG` + scoped `IPLAN`. Never let `plans/PLAN-NNN_<slug>.md` substitute for `09_CHG` + `08_IPLAN`.
- **Flow Boundary:** `DIR2C` is strictly `C1` for non-behavior, non-SDD-cascading changes and MUST NEVER bypass `SEED2C`/`SDD2C` when product behavior or `01_BRD..07_TDD` layers change. Normative edits inside `.aidoc/framework/framework/**` route via `GATE-SPEC` (`change_source: spec`, `>= C2`).

## 3. Hand off, don't author here

Classification is the whole job. The CHG itself belongs to `sdd-chg-author`;
issue validation before any of this belongs to `sdd-chg-triage`. State the
verdict as one line — `Flow: DIR2C (F2), C1, direct, GATE-CODE` — and stop.

## 4. Open Questions in CHG Flow Classification & Execution

Whenever the agent has open questions during CHG flow routing or execution (`HOTFIX`, `CODE2S`, `CODE2C`, `SEED2C`, `DIR2C`, `SDD2C`):
1. **Generate 3 Recommendations per Open Question:** Provide **3 distinct recommendations/options** for each question with clear rationale and trade-offs.
2. **Review & Score (1 to 5):** Critically review the generated recommendations and **score each from `1` to `5`** based on **best practices** (platform standards, security, SDD/EaC compliance, maintainability) and **practical use** (operational simplicity, execution feasibility, blast radius), where a higher score is better (`5` = best), and highlight the top-scored recommendation.
3. **Use the Highest-Scored Recommendation & Continue:** Apply the recommendation with the highest score and continue without asking an operator.
