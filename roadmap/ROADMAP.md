# Engramory — Project Roadmap

*This file is **canonical for cycle scope and sequencing** (MVP-1..8); the engineering Phase 0–3 view is in [`../docs/ROADMAP.md`](../docs/ROADMAP.md).*

*Cycles follow the framework cadence: **MVP → PROD → new-feature MVP → new PROD**. Each cycle is anchored by a **BRD set** — a platform BRD plus its feature BRDs, linked by `@depends:` (see [HOW_TO_USE_THE_FRAMEWORK](../docs/HOW_TO_USE_THE_FRAMEWORK.md) §4). The current cycle's BRD set is authored at full depth (8 layers); future cycles are **draft BRD sketches** (business scope only) until their cycle begins.*

*Last updated: 2026-06-22*

---

## Vision

Engramory is the aidoc-flow ecosystem's shared memory & knowledge plane — per-agent, distilled, portable — that all projects build on and that replaces `ucx_kb`.

**Guiding strategy** ([../docs/STRATEGY.md](../docs/STRATEGY.md)): build the plane (Postgres spine, ports, gateway, scope, governance); **adopt** a proven memory engine behind `MemoryPort` rather than hand-writing distillation; and lead with an evaluation + feedback loop. MVP-1 carries a vertical slice (one agent, one real workload) plus that eval harness; later cycles stay sketches until the slice proves value.

## Cycle cadence

```mermaid
flowchart LR
  MVP1[MVP-1 BRD-01<br/>Memory & Knowledge Core] --> PROD1[PROD v1.0]
  PROD1 --> MVP2[MVP-2 BRD-02<br/>Cloud migration] --> PROD2[PROD v2.0]
  PROD2 --> NEXT[MVP-3 … MVP-8]
```

Each BRD set = one iteration cycle. New scope = a new BRD set; cross-cycle traceability via `@depends: BRD-01`.

## Cycle plan

| Cycle | BRD | Status | Goal | Depth | Depends |
|---|---|---|---|---|---|
| **MVP-1** | BRD-01 | **Full (8 layers)** | Memory & Knowledge Core: L0–L3, distillation, shared access, ucx_kb replacement, portability foundation | All layers authored | — |
| MVP-2 | BRD-02 | Draft sketch | Cloud migration & managed operations (GCP/Azure adapters, managed Postgres/graph/models, IaC) | Scope only | BRD-01 |
| MVP-3 | BRD-03 | Draft sketch | Advanced distillation & learning (confidence scoring, drift, smarter reflection/consolidation) | Scope only | BRD-01 |
| MVP-4 | BRD-04 | Draft sketch | Domain & project configuration: two-tier config + inheritance, `domain` scope, domain-shared vs project-scoped memory | Scope only | BRD-01 |
| MVP-5 | BRD-05 | Draft sketch | Multi-tenancy, security & compliance hardening (SSO/RBAC, residency/CMEK, audit, SOC 2-ready) | Scope only | BRD-01 |
| MVP-6 | BRD-06 | Draft sketch | Observability & operations (dashboards, SLOs, alerting, provenance/audit browser, runbooks) | Scope only | BRD-01 |
| MVP-7 | BRD-07 | Draft sketch | Brain portability & migration tooling (export/import, cross-engine migration, re-embed automation) | Scope only | BRD-01 |
| MVP-8 | BRD-08 | Draft sketch | Multi-project operations & tuning: onboarding runbooks, isolation/quotas, instance tuning, shared-vs-dedicated topology | Scope only | BRD-01, BRD-04 |

## Sequencing notes

- **MVP-1 → PROD** before MVP-2: prove the shared per-agent memory plane + ucx_kb parity on the self-hosted stack first.
- **MVP-2 (cloud)** is sequenced early because the portability promise (BRD-01) is only realized once a managed cloud adapter set exists; pull it earlier/later per business need.
- MVP-3 (advanced distillation) and MVP-4 (domain & project configuration) deliver the differentiated value; order by which consumer project needs it first.
- MVP-5/6 (security/compliance, observability) harden for enterprise/regulated consumers (e.g., BeeLocal).
- MVP-8 (multi-project operations & tuning) depends on MVP-4 and on multi-project demand maturing; can move earlier if a consumer's multi-project topology requires it.

## Rules

- Only the current cycle's BRD is authored at full depth. A sketch graduates to a full BRD (PRD→IPLAN) when its cycle starts.
- A sketch carries **business scope only** — no PRD/EARS/BDD/ADR/SPEC/TDD/IPLAN content (that would be over-authoring).
- Re-prioritize by editing this roadmap + the `BRD-00_index.md` Planned-BRDs table; keep `@depends:` links intact.
