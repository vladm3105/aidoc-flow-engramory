# ADR-0003: Neo4j over Apache AGE for the graph engine

- **Status:** Accepted
- **Date:** 2026-06-22

## Context
Graph is needed for multi-hop reasoning/GraphRAG. Managed AGE exists on Azure Postgres but NOT on GCP Cloud SQL/AlloyDB; the target cloud is undecided.

## Decision
Default to pure-Postgres (edge tables + recursive CTE) while needs are shallow; promote to Neo4j (Community → multi-cloud Aura) when real graph features are needed. Keep AGE only as an Azure-committed fallback. Access stays behind GraphPort.

## Consequences
Graph is portable across both clouds. The graph is treated as a rebuildable projection of canonical Postgres data.
