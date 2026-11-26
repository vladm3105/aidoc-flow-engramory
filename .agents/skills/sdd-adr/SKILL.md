---
name: sdd-adr
description: >-
  Author ADRs (Layer 05): record architecture decisions with options, trade-offs, and a Selected reason — status Proposed until accepted, never silent.
---

# SDD ADR Author

> Framework skill — routes to `.aidoc/framework/framework/layers/05_ADR/ADR-TEMPLATE.yaml`
> and `ADR_WORKFLOW_STANDARD.md` (entry gate GATE-03).
> Paths below use `<skills-root>` = `.agents/skills/` (framework root: `.aidoc/framework/framework/`).
> Upstream: BDD scenarios bound the behavior the architecture must support.

## 1. Decide on paper, with losers named

Each ADR names the decision, the options considered with trade-offs, and
the selection with a one-line reason. An ADR with one option is a rubber
stamp — find the real alternative or say why none exists. Status starts at
`Proposed`; only acceptance moves it to `Accepted` (later: `Deprecated`
or `Superseded`, never edited into a different past).

Whenever the agent encounters open questions or architectural choices:
1. **Generate 3 Recommendations per Open Question:** Provide **3 distinct recommendations/options** for each question with clear rationale and trade-offs.
2. **Review & Score (1 to 5):** Critically review the generated recommendations and **score each from `1` to `5`** based on **best practices** (platform standards, security, SDD/EaC compliance, maintainability) and **practical use** (operational simplicity, execution feasibility, blast radius), where **a higher score is better (`5` = best)**, and highlight the top-scored recommendation.
3. **Use the Highest-Scored Recommendation & Continue:** Select and apply the recommendation with the highest score and continue without asking an operator.

## 2. Keep the deterministic gates honest

`spec_ready_status` stays a binary topological verdict (PASS), not a score.
Scores rot into arguments about thresholds; PASS/FAIL rot into nothing.

## 3. Seal and hand off

Gate: GATE-03. Fast-path restriction applies (CHG-L019). Next: `sdd-spec`.
