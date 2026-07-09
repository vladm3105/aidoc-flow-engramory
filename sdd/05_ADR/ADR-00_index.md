---
title: "ADR-00: Architecture Decision Record Index"
tags:
  - adr
  - index
  - layer-5-artifact
custom_fields:
  document_type: adr-index
  artifact_type: ADR-INDEX
  layer: 5
  last_updated: "2026-07-09"
---

# ADR-00: Architecture Decision Record Index

Index of Architecture Decision Records for **Engramory**.

**Layer**: 5 (Decision bridge) · **Upstream**: EARS (L3), BDD (L4) · **Downstream**: SPEC (L6)

> **This is the canonical, implementing ADR log** for Engramory. The narrative
> descriptions in `docs/adr/0001–0005` are conceptual/descriptive records of the
> *desired* decisions; these YAML ADRs are the framework-conformant, implementing
> form (and add ADR-06 through ADR-09, which have no `docs/adr/` counterpart).
> When the two differ, this log governs.

## Document Registry

| ADR ID | Decision | Status | Reversibility | Location |
|--------|----------|--------|---------------|----------|
| ADR-01 | PostgreSQL as the canonical spine | Accepted | One-way (heavy) | [ADR-01_postgres_spine.yaml](ADR-01_postgres_spine.yaml) |
| ADR-02 | Ports & adapters (hexagonal) | Accepted | Two-way | [ADR-02_ports_and_adapters.yaml](ADR-02_ports_and_adapters.yaml) |
| ADR-03 | Graph engine: pure-Postgres default, Neo4j on promotion (AGE rejected) | Accepted | Two-way (behind GraphPort) | [ADR-03_graph_engine.yaml](ADR-03_graph_engine.yaml) |
| ADR-04 | LiteLLM as the single model gateway | Accepted | Two-way | [ADR-04_model_gateway.yaml](ADR-04_model_gateway.yaml) |
| ADR-05 | Canonical memory in Postgres; projections rebuildable | Accepted | One-way (principle) | [ADR-05_canonical_memory.yaml](ADR-05_canonical_memory.yaml) |
| ADR-06 | Governed-write authority + fail-closed access | Accepted | Two-way | [ADR-06_governed_write_failclosed.yaml](ADR-06_governed_write_failclosed.yaml) |
| ADR-07 | Scope ladder (agent/project/domain/space) + tenant_id isolation | Accepted | Two-way | [ADR-07_scope_model.yaml](ADR-07_scope_model.yaml) |
| ADR-08 | Single platform, two bounded cores — Memory and Knowledge | Accepted | Two-way | [ADR-08_two_bounded_cores.yaml](ADR-08_two_bounded_cores.yaml) |
| ADR-09 | Independent memory storage; integrate the iplan ledger as an episode source | Accepted | Two-way | [ADR-09_independent_memory_storage.yaml](ADR-09_independent_memory_storage.yaml) |

*Last Updated: 2026-07-09*
