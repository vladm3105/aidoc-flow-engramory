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

### 3.2 Ingesting Knowledge Base ADR Graph

To extract the structured ADR knowledge graph from markdown/yaml specifications, persist to canonical PostgreSQL, and project into Neo4j:

```bash
# Run via python / worker entrypoint
python -c "
import asyncio
from engramory.adapters.factory import dev_adapters
from engramory.core.kb_graph import sync_adr_knowledge_graph

async def main():
    adapters = await dev_adapters()
    summary = await sync_adr_knowledge_graph('sdd/05_ADR', adapters.repository, adapters.graph)
    print('ADR Graph Sync Summary:', summary)

asyncio.run(main())
"
```

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

Execute the deterministic projection worker command:

```bash
uv run engramory graph reproject
```

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

## 5. Security & Isolation

1. **Vendor Boundary**: The official `neo4j` Python driver must never be imported in `engramory.core`, `engramory.ports`, or `engramory.workers`. All graph operations transit through `GraphPort` in `src/engramory/ports/graph.py`.
2. **Network Exposure**: In production and staging, the Neo4j Bolt port (`7687`) is bound strictly to `platform-mesh` internal overlay network. It must never be exposed to public interfaces without TLS and gateway authorization.
3. **APOC Constraints**: APOC is loaded for optimized batch operations. Procedures with filesystem or remote URL access (`apoc.load.json`, `apoc.export.*`) are disabled in production deployments via `dbms.security.procedures.unrestricted=apoc.coll.*,apoc.map.*`.
