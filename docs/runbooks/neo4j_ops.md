# Neo4j Operations Runbook

## Overview

Engramory uses Neo4j Community Edition as a **read-optimized graph projection** behind `GraphPort`.
Per **ADR-01** and **ADR-05**, PostgreSQL is the canonical source of truth for all entities and relationships (`graph_entities` and `graph_relations` tables). Neo4j holds a rebuildable projection designed for multi-hop traversals, shortest path analysis, and hybrid GraphRAG context expansion.

---

## 1. Architecture & Port Bindings

| Environment | Host / Network | Bolt URI | HTTP Browser | Auth Secret |
|---|---|---|---|---|
| **Local Dev** | `engramory-docker` | `bolt://localhost:7687` | `http://localhost:7474` | `NEO4J_AUTH=neo4j/change_me` |
| **Staging** | `platform-mesh` | `bolt://neo4j:7687` | `http://localhost:7474` (loopback) | `NEO4J_AUTH=neo4j/<stage_secret>` |
| **Production** | Managed / Aura | Managed Bolt URI | Managed Web Console | OpenBao `kv/prod/engramory/neo4j` |

---

## 2. Memory Tuning & JVM Configuration

Neo4j Community Edition runs on the HotSpot JVM. Memory allocation must be strictly bounded to prevent out-of-memory crashes and buffer pool thrashing in constrained environments:

```yaml
environment:
  - NEO4J_server_memory_heap_initial__size=512m
  - NEO4J_server_memory_heap_max__size=1g
  - NEO4J_server_memory_pagecache_size=512m
  - NEO4J_PLUGINS=["apoc"]
```

### Memory Rules

1. **Initial & Max Heap**: Keep initial (`512m`) and max (`1g`) balanced for container predictability.
2. **Pagecache**: Neo4j uses off-heap pagecache to map store files into RAM. For MVP knowledge bases (< 100,000 nodes), `512m` covers all active graph indexes and node/relationship records.
3. **Container Limits**: In Docker Compose or systemd slices, cap container RAM at `2g` (`mem_limit: 2g`, `memswap_limit: 2g`).

---

## 3. Routine Operations

### 3.1 Health Verification

Check container health status and Bolt responsiveness:

```bash
# Container healthcheck (HTTP)
curl -s -f http://localhost:7474 > /dev/null && echo "Neo4j HTTP: OK"

# Bolt connectivity check via cypher-shell
docker exec engramory-neo4j cypher-shell -u neo4j -p change_me "RETURN 1 AS alive;"
```

### 3.2 Ingesting Multi-Layer SDD Knowledge Graph

To extract the structured SDD knowledge graph from markdown/yaml specifications (across BRD, PRD, SYS, SRS, ADR, ARCH, DSG, IMP, TEST, EVAL layers), persist to canonical PostgreSQL, and project into Neo4j:

```bash
# Ingest local or staging repository SDD corpus
uv run engramory sdd ingest \
  --sdd-root sdd \
  --tenant-id default \
  --project-id aidoc-flow-engramory

# Ingest an external or sibling project corpus (e.g. b-local-privy)
uv run engramory sdd ingest \
  --sdd-root ../b-local-privy/sdd \
  --tenant-id default \
  --project-id b-local-privy
```

Key guarantees during ingestion:

1. **Referential Integrity & Stub Generation**: If a document control header references draft or future document targets not yet committed to the corpus (e.g. `EVAL-02`), stub entities (`status="stub"`, `stub=True`) are automatically generated to ensure graph completeness and zero PostgreSQL foreign key violations.
2. **Transactional Bulk Upserts**: All extracted entities and relations are written using bulk `executemany` statements inside a single transaction to maintain atomic consistency.
3. **Compound UID Multi-Tenancy**: Nodes in Neo4j are stored with a unique compound `uid = "{tenant_id}:{id}"` alongside `tenant_id` and `id` properties. This provides complete tenant isolation on Neo4j Community Edition without requiring Enterprise composite node keys.

---

## 4. Disaster Recovery & Projection Rebuild

Because Neo4j is strictly an unprivileged projection, **disaster recovery requires zero graph restore procedures from tape or snapshot**.

If Neo4j becomes corrupt, volume data is lost, or the database is wiped:

### Step 1: Wipe & Restart Neo4j Container

```bash
docker rm -f engramory-neo4j
docker volume rm engramory_neo4jdata
docker compose up -d neo4j
```

### Step 2: Re-project All Entities & Relations from PostgreSQL

Execute the deterministic projection worker command (optionally specifying `--tenant-id`):

```bash
# Reproject all tenants or specific tenant
uv run engramory graph reproject --tenant-id default
```

The reprojection worker:

- Ensures the `entity_uid_unique` constraint (`uid = tenant_id:id`) is initialized.
- Purges stale Neo4j nodes that were deleted from PostgreSQL.
- Batches node merges (`MERGE (n:Entity {uid: $uid}) ...`) and relationship edges.

Output:

```text
Re-projecting entities and relations from Postgres to Neo4j...
Projected 10 entities to Neo4j.
Projected 20 relations to Neo4j.
Graph re-projection complete: 10 entities, 20 relations.
```

### Step 3: Verify Graph Counts

Verify node and relationship counts via CLI:

```bash
uv run engramory status
```

---

## 5. Offline & High-Availability Fallback (PostgreSQL CTE)

Neo4j is not a single point of failure for context slicing or blast radius calculations.
If Neo4j is offline, unreachable, or unconfigured (`_graph is None` or query timeout):

1. `AccessSurface.context_slice` and `AccessSurface.blast_radius` automatically fall back to **canonical PostgreSQL recursive CTE traversal**:
   - Upstream ancestor closure: `Repository.find_ancestors(entity_id, tenant_id=..., max_depth=...)`
   - Downstream blast radius: `Repository.find_descendants(entity_id, tenant_id=..., max_depth=...)`
2. Traversal queries in PostgreSQL execute `WITH RECURSIVE` queries directly on indexed `graph_relations` (`source_id`, `target_id`, `tenant_id`).
3. Results are identical in structure and ordering, allowing CI pipelines and developer workstations to operate without running a Neo4j daemon.

---

## 6. Security & Isolation

1. **Vendor Boundary**: The official `neo4j` Python driver must never be imported in `engramory.core`, `engramory.ports`, or `engramory.workers`. All graph operations transit through `GraphPort` in `src/engramory/ports/graph.py`.
2. **Network Exposure**: In production and staging, the Neo4j Bolt port (`7687`) is bound strictly to `platform-mesh` internal overlay network. It must never be exposed to public interfaces without TLS and gateway authorization.
3. **APOC Constraints**: APOC is loaded for optimized batch operations. Procedures with filesystem or remote URL access (`apoc.load.json`, `apoc.export.*`) are disabled in production deployments via `dbms.security.procedures.unrestricted=apoc.coll.*,apoc.map.*`.
