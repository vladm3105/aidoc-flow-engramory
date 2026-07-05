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
  last_updated: "2026-06-22"
---

# ADR-00: Architecture Decision Record Index

Index of Architecture Decision Records for **Engramory**.

**Layer**: 5 (Decision bridge) · **Upstream**: EARS (L3), BDD (L4) · **Downstream**: SPEC (L6)

> These ADRs are the framework-conformant form of the decisions originally
> recorded in `docs/adr/0001–0005`, plus ADR-06 (governed-write / fail-closed)
> surfaced by EARS-01 / BDD-01.

## Document Registry

| ADR ID | Decision | Status | Reversibility | Location |
|--------|----------|--------|---------------|----------|
| ADR-01 | PostgreSQL as the canonical spine | Accepted | One-way (heavy) | [ADR-01_postgres_spine.yaml](ADR-01_postgres_spine.yaml) |
| ADR-02 | Ports & adapters (hexagonal) | Accepted | Two-way | [ADR-02_ports_and_adapters.yaml](ADR-02_ports_and_adapters.yaml) |
| ADR-03 | Neo4j over Apache AGE for the graph engine | Accepted | Two-way (behind GraphPort) | [ADR-03_graph_engine.yaml](ADR-03_graph_engine.yaml) |
| ADR-04 | LiteLLM as the single model gateway | Accepted | Two-way | [ADR-04_model_gateway.yaml](ADR-04_model_gateway.yaml) |
| ADR-05 | Canonical memory in Postgres; projections rebuildable | Accepted | One-way (principle) | [ADR-05_canonical_memory.yaml](ADR-05_canonical_memory.yaml) |
| ADR-06 | Governed-write authority + fail-closed access | Accepted | Two-way | [ADR-06_governed_write_failclosed.yaml](ADR-06_governed_write_failclosed.yaml) |

*Last Updated: 2026-06-22*
