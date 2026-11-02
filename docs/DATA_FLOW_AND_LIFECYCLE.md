# Data Flow, Lifecycle & Graph Retrieval Architecture

## 1. Architectural Foundation & Topology

Engramory separates persistent state and relational graph traversal according to **ADR-01** (PostgreSQL Spine), **ADR-02** (Ports & Adapters), and **ADR-05** (Rebuildable Projections).

```text
 ┌────────────────────────┐
 │   Agent / Caller       │
 │   (MCP Gateway / CLI)  │
 └───────────┬────────────┘
             │
             ▼
 ┌────────────────────────┐
 │     AccessSurface      │
 │  (Authz, Token Budget) │
 └─────┬────────────┬─────┘
       │            │
       │ (1. Read/  │ (2. Graph Traversal /
       │  Write)    │     Projection)
       ▼            ▼
┌─────────────┐  ┌─────────────┐
│ PostgreSQL  │  │    Neo4j    │
│  (Canonical │  │ (Rebuildable│
│    Spine)   │  │ Projection) │
└─────────────┘  └─────────────┘
```

- **PostgreSQL is the Single Source of Truth:** All entities, relationships, lineage, episodes, and knowledge sections are permanently committed to PostgreSQL.
- **Neo4j is an Unprivileged Projection:** Neo4j stores derived graphs optimized for multi-hop graph traversals. Neo4j is never the independent source of truth. If Neo4j is lost or restarted, running `engramory graph reproject` reconstructs 100% of the graph from PostgreSQL.

---

## 2. Tool & Capability Inventory: Insert, Update, Delete & Retrieve

| Surface / Layer | Insertion / Add | Update / Modify | Deletion / Retire | Retrieval / Query |
|---|---|---|---|---|
| **Agent Face (MCP / SPEC-01)** | `memory_add`<br>`knowledge_ingest` | Append new version with `supersedes`<br>`knowledge_ingest` (upsert) | `memory_forget` (soft retire; sets `valid_to=now()`) | `memory_search`<br>`graph_neighbors`<br>`agent_profile_get` |
| **Access Surface (`AccessSurface`)** | `memory_add(...)`<br>`knowledge_ingest(...)` | `knowledge_ingest(...)`<br>Superseding append | `memory_forget(...)` (soft delete) | `memory_search(..., expand_graph=True)`<br>`graph_neighbors(id, depth)` |
| **Canonical Repo (`Repository`)** | `add_episode`<br>`add_memory`<br>`upsert_kb_section`<br>`upsert_graph_entity`<br>`upsert_graph_relation` | `upsert_kb_section` (`ON CONFLICT DO UPDATE`)<br>`upsert_graph_entity` (`ON CONFLICT DO UPDATE`)<br>`upsert_graph_relation` (`ON CONFLICT DO UPDATE`) | `retire_memory` (soft: `status='superseded'`)<br>`delete_graph_entity` (hard: cascading FKs)<br>`delete_graph_relation` (hard) | `get_memories`<br>`get_kb_sections`<br>`get_graph_entity`<br>`get_graph_entities`<br>`get_graph_relations` |
| **Graph Adapter (`Neo4jGraphAdapter`)** | `upsert_node`<br>`upsert_edge` | `upsert_node` (`MERGE ... SET +=`)<br>`upsert_edge` (`MERGE ... SET +=`) | `delete_node` (`DETACH DELETE n`)<br>`delete_edge` (`MATCH ()-[r]->() DELETE r`) | `neighbors(id, rel, depth)` (1..5 hops) |

---

## 3. Sequential Data Flow Diagrams

### 3.1 Document & Knowledge Insertion Sequence

```mermaid
sequenceDiagram
    autonumber
    participant Agent as "Agent (MCP / CLI)"
    participant Surface as "AccessSurface"
    participant Authz as "Authz Guard"
    participant Repo as "Repository (Postgres)"
    participant Worker as "Projection Worker"
    participant Neo4j as "Neo4jGraphAdapter"

    Agent->>Surface: knowledge_ingest / memory_add
    Surface->>Authz: _decide(ctx, action, target_scope)
    Authz-->>Surface: AuthzDecision(allowed=True)
    Surface->>Repo: upsert_kb_section / upsert_graph_entity
    Note over Repo: INSERT ... ON CONFLICT DO UPDATE<br/>PostgreSQL commits canonical row
    Repo-->>Surface: entity_id / section_id
    
    alt Asynchronous or Direct Graph Projection
        Worker->>Repo: read unprojected / updated entities
        Repo-->>Worker: GraphEntity / GraphRelation records
        Worker->>Neo4j: upsert_node(id, labels, props) / upsert_edge(...)
        Note over Neo4j: MERGE (n:Entity {id: $id})<br/>SET n += $props
        Neo4j-->>Worker: success
    end

    Surface-->>Agent: Confirmation & Resource ID
```

---

### 3.2 Document & Memory Update Sequence (Immutability & Upsert)

```mermaid
sequenceDiagram
    autonumber
    participant Agent as "Agent"
    participant Surface as "AccessSurface"
    participant Repo as "Repository (Postgres)"
    participant Neo4j as "Neo4j (Projection)"

    alt Knowledge Base Section / Entity Update
        Agent->>Surface: knowledge_ingest (updated content/props)
        Surface->>Repo: upsert_graph_entity(id, props)
        Note over Repo: ON CONFLICT (tenant_id, id)<br/>DO UPDATE SET name=EXCLUDED.name, properties=EXCLUDED.properties, updated_at=NOW()
        Surface->>Neo4j: upsert_node(id, labels, updated_props)
        Note over Neo4j: MERGE (n:Entity {id: $id}) SET n += $props
        Surface-->>Agent: Update confirmed
    else Distilled Memory Update (Append-Only Superseding)
        Agent->>Surface: memory_add (with supersedes=old_id)
        Surface->>Repo: add_memory(new_memory)
        Note over Repo: INSERT new memory row<br/>UPDATE old memory SET valid_to=NOW(), status='superseded'
        Surface-->>Agent: New memory created, old superseded
    end
```

---

### 3.3 Deletion & Invalidation Sequence

```mermaid
sequenceDiagram
    autonumber
    participant Agent as "Agent"
    participant Surface as "AccessSurface"
    participant Repo as "Repository (Postgres)"
    participant Neo4j as "Neo4j (Projection)"

    alt Soft Invalidation (Memories: memory_forget)
        Agent->>Surface: memory_forget(memory_id)
        Surface->>Repo: retire_memory(memory_id)
        Note over Repo: UPDATE memories<br/>SET valid_to = now(), status = 'superseded'<br/>(Audit trail and provenance preserved)
        Repo-->>Surface: ok
        Surface-->>Agent: Memory retired
    else Canonical Graph Entity Deletion
        Agent->>Surface: delete_graph_entity(id)
        Surface->>Repo: delete_graph_entity(id)
        Note over Repo: DELETE FROM graph_entities WHERE id = $id<br/>(PostgreSQL Foreign Key cascades to graph_relations)
        Repo-->>Surface: ok
        Surface->>Neo4j: delete_node(id)
        Note over Neo4j: MATCH (n:Entity {id: $id})<br/>DETACH DELETE n
        Neo4j-->>Surface: ok
        Surface-->>Agent: Entity and attached edges deleted
    end
```

---

### 3.4 Agent Graph Retrieval & Hybrid Retrieval Sequence

```mermaid
sequenceDiagram
    autonumber
    participant Agent as "Agent"
    participant Surface as "AccessSurface"
    participant Repo as "Repository (Postgres)"
    participant Neo4j as "Neo4j (GraphPort)"

    alt Pattern 1: Direct Topological Traversal (graph_neighbors)
        Agent->>Surface: graph_neighbors(id='ADR-03', depth=2)
        Surface->>Surface: Check ActorContext scopes
        Surface->>Neo4j: neighbors(id='ADR-03', depth=2)
        Note over Neo4j: MATCH (n:Entity {id: $id})-[*1..2]-(m:Entity)<br/>RETURN DISTINCT m.id, labels(m), properties(m)
        Neo4j-->>Surface: list of connected neighbor records
        Surface-->>Agent: Neighbor entities with metadata & properties
    else Pattern 2: Hybrid GraphRAG (memory_search with expand_graph=True)
        Agent->>Surface: memory_search(query='storage architecture', expand_graph=True)
        Surface->>Repo: get_memories (Vector / text similarity)
        Repo-->>Surface: Top-K anchor memories
        loop For each anchor memory
            Surface->>Neo4j: neighbors(id=memory.id, depth=1)
            Neo4j-->>Surface: Connected concept & entity IDs
            Note over Surface: Inject into MemoryHit.provenance["related_entities"]
        end
        Surface-->>Agent: Fused memories + topological graph context
    end
```

---

## 4. Disaster Recovery & Projection Rebuild

Because Neo4j is strictly an unprivileged projection, disaster recovery requires zero tape or external database snapshots:

```bash
# 1. Wipe or redeploy Neo4j container
docker rm -f engramory-neo4j
docker compose up -d neo4j

# 2. Replay all entities and relationships from canonical PostgreSQL
uv run engramory graph reproject
```

Output:

```text
Re-projecting entities and relations from Postgres to Neo4j...
Projected 10 entities to Neo4j.
Projected 20 relations to Neo4j.
Graph re-projection complete: 10 entities, 20 relations.
```
