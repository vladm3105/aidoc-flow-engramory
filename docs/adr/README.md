# Architecture Decision Records (conceptual)

These are short, narrative records of the **desired** significant decisions — the
conceptual form, written for humans. They are **not** the canonical implementing log.

**Canonical, implementing ADRs live in [`sdd/05_ADR/`](../../sdd/05_ADR/ADR-00_index.md)**
(framework-conformant YAML, with traceability into SPEC/TDD). When a decision here and
its `sdd/05_ADR/` counterpart differ, the `sdd/05_ADR/` version governs. Record **new**
implementing decisions in `sdd/05_ADR/`; keep this file for the conceptual summary.

| # | Decision | Implementing ADR |
|---|----------|------------------|
| 0001 | PostgreSQL as the canonical spine | sdd/05_ADR/ADR-01 |
| 0002 | Ports & adapters (hexagonal) for cloud portability | sdd/05_ADR/ADR-02 |
| 0003 | Graph engine: pure-Postgres default, Neo4j on promotion (AGE rejected) | sdd/05_ADR/ADR-03 |
| 0004 | LiteLLM as the single model gateway | sdd/05_ADR/ADR-04 |
| 0005 | Canonical memory in Postgres; embeddings & graph are rebuildable projections | sdd/05_ADR/ADR-05 |
| — | Governed-write authority + fail-closed access | sdd/05_ADR/ADR-06 |
| — | Scope ladder (agent/project/domain/space) + tenant_id isolation | sdd/05_ADR/ADR-07 |
| — | Single platform, two bounded cores — Memory and Knowledge | sdd/05_ADR/ADR-08 |
| — | Independent memory storage; iplan ledger as an episode source (not a backend) | sdd/05_ADR/ADR-09 |
