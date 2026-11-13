# Ecosystem SDD Integration Architecture

## Overview

This document specifies the end-to-end integration architecture between the **AIDoc Framework** (`vladm3105/aidoc-flow-framework`), **AIDoc Context Tools** (`vladm3105/aidoc-context-tools`), and **Engramory** (`vladm3105/aidoc-flow-engramory`).

It establishes the **Dual-Core CQRS (Command Query Responsibility Segregation) + Docs-as-Code** pattern for autonomous AI coding agents across the organization.

---

## 1. System Topology & Architectural Roles

```mermaid
flowchart TD
    subgraph SOT ["1. Canonical Source of Truth (Write Authority / Docs-as-Code)"]
        AF["aidoc-framework\n• Generates 10-layer SDD templates\n• Governs spec structure (BRD..EVAL)\n• Human review & PR merge"]
        GitDocs["Git Filesystem (Markdown / YAML)\n• docs/sdd/ across repositories\n• Mermaid diagrams & visual specs\n• Git blame, commits & history"]
        AF -->|Authoring / Scaffolding| GitDocs
    end

    subgraph CoreEngine ["2. Central Knowledge & Memory Engine (Read Authority / Fast Query)"]
        Engramory["Engramory Core Engine\n• Ports & Adapters Architecture\n• Multi-tenant & Multi-project"]
        PG[("PostgreSQL Canonical Spine\n• Plain text & chunks\n• pgvector dense embeddings\n• graph_entities & graph_relations")]
        Neo4j[("Neo4j Read-Optimized GraphDB\n• Compound UIDs (tenant:id)\n• Sub-ms ancestor & descendant traversals")]
        MCPGate["Engramory MCP Gateway\n• sdd_context_slice\n• sdd_blast_radius\n• memory_search / memory_add"]

        Engramory --> PG
        PG -->|Reprojection| Neo4j
        MCPGate --> Engramory
    end

    subgraph EdgeClient ["3. Workstation Edge Client & Offline Fallback"]
        ACT["aidoc-context-tools (aidoc-ctx)\n• Dual-Engine Context Slicing\n• Pre-merge Invariant Gating (AST diff)\n• Embedded Kùzu OLAP / Local DFS"]
    end

    subgraph Inference ["4. Model & Inference Gateway"]
        LiteLLM["LiteLLM Gateway\n• Routes LLM & embedding calls\n• Virtual key authentication\n• Token rate limits & budget quotas"]
    end

    subgraph Agents ["5. Autonomous Coding Agents"]
        Agent["Autonomous Agent\n(Antigravity / Cursor / Claude / LangGraph)"]
    end

    %% Flows
    GitDocs -->|CI Ingestion: engramory sdd ingest| Engramory
    ACT -->|Online Mode: MCP Query| MCPGate
    ACT -.->|Offline Fallback: Local Files| GitDocs
    Agent -->|1. Context Slices & Memory Tools| MCPGate
    Agent -->|2. Local Invariant Verification| ACT
    Agent -->|3. LLM Reasoning Prompts| LiteLLM
    Agent -->|4. Author / Edit Specs| GitDocs
```

---

## 2. Component Responsibility Matrix

| Component | Repository | Primary Responsibility | Ground Truth Status | Interfaces Exposed |
|---|---|---|---|---|
| **AIDoc Framework** | `vladm3105/aidoc-flow-framework` | Authoring 10-layer SDD specifications, lifecycle governance (`SEED2C`..`SDD2C`), document templates, review crews. | **Canonical Ground Truth (Write Authority)** for all markdown/YAML files. | File templates, `.aidoc/Taskfile.yml`, `deploy.sh`. |
| **Engramory** | `vladm3105/aidoc-flow-engramory` | Multi-project knowledge base (L0), semantic embeddings (pgvector), topological graph (Neo4j), and agent episodic memory (L1–L3). | **Primary Query & Retrieval Engine (Read Model)**. Rebuildable from Git files. | `engramory` CLI, MCP Gateway (stdio / HTTP), `AccessSurface`. |
| **AIDoc Context Tools** | `vladm3105/aidoc-context-tools` | Edge CLI (`aidoc-ctx`), local context slicing, AST pre-merge invariant gating (`check-invariants`), embedded Kùzu OLAP. | **Edge Client & Linter**. Operates on local worktree files. | `aidoc-ctx` CLI, FastMCP local server. |
| **LiteLLM Proxy** | Platform Gateway | Model virtualization, embedding generation, rate-limiting, and cost allocation. | Stateless gateway. | `/v1/chat/completions`, `/v1/embeddings`. |

---

## 3. The Dual-Core Operational Model (CQRS)

### 3.1 Write Model: Git as Ground Truth

1. **Human & Agent Authoring**:
   - All architectural requirements, decisions, specifications, test plans, and change requests are authored as structured Markdown and YAML files in `docs/sdd/` (`01_BRD` through `10_EVAL`).
2. **Git Verifiability**:
   - Specifications undergo code review, cryptographic commit signing, PR approval gates, and git blame auditing.
3. **Visual Media & Diagrams**:
   - Rich visuals (Mermaid diagrams, sequence charts, architectural schemas, state machines) live natively in Git Markdown. They are not forced into vector chunks; instead, their files are referenced by URI in graph metadata.

### 3.2 Read Model: Engramory as High-Performance Projection

1. **Automated Ingestion**:
   - On PR merge or CLI trigger, `engramory sdd ingest` scans the repository's `docs/sdd/` directory.
   - Extracts metadata, entity IDs, dependencies (`REFERENCES`, `SPECIFIES`, `DERIVED_FROM`), tokenizes targets, and auto-generates stubs for uncommitted references.
   - Bulk-upserts rows into PostgreSQL (`graph_entities`, `graph_relations`, `memories`) inside atomic transactions.
2. **Graph Projection**:
   - Project entities and relations into Neo4j using compound `uid = f"{tenant_id}:{id}"` for sub-millisecond multi-hop graph traversals.
3. **High-Density Context Slicing**:
   - When an autonomous coding agent starts working on an implementation task (e.g. `IPLAN-042`), it requests the upstream context slice:
     $$\text{Context}(T_k) = \text{Ancestors}(T_k) \cap \{\text{SPEC}, \text{BDD}, \text{ADR}, \text{EARS}, \text{BRD}\}$$
   - Engramory traverses the graph and returns a compact, high-density context slice (~1,500 tokens instead of 25k+ tokens), slashing token consumption by 70–85% and eliminating specification amnesia.
4. **Disaster Recovery & Zero-Data-Loss**:
   - If Neo4j or PostgreSQL is wiped, re-running `engramory sdd ingest` completely reconstructs the knowledge graph deterministically from Git files.

---

## 4. Interaction Flows

### 4.1 Knowledge Ingestion Sequence (CI/CD Pipeline)

```mermaid
sequenceDiagram
    autonumber
    participant Dev as Developer / AI Agent
    participant Git as GitHub (Feature Branch -> dev)
    participant CI as GitHub Actions Runner
    participant Engramory as Engramory Core (Staging)
    participant PG as PostgreSQL Spine
    participant Neo4j as Neo4j Graph Projection

    Dev->>Git: Push PR with new ADR-042 & SPEC-042
    Git->>CI: Trigger verify & merge
    CI->>CI: Run task check & task app:test
    Git->>Git: Merge commit to dev (SHA: a1b2c3d)
    CI->>Engramory: engramory sdd ingest --sdd-root docs/sdd --project-id <repo>
    Engramory->>Engramory: extract_sdd_graph() with stub resolution
    Engramory->>PG: bulk_upsert_graph_entities & bulk_upsert_graph_relations
    Engramory->>Neo4j: batch MERGE (n:Entity {uid: $uid})
    Engramory-->>CI: Ingestion complete (N entities, M relations)
```

### 4.2 Agent Execution & Context Slicing Sequence

```mermaid
sequenceDiagram
    autonumber
    participant Agent as Autonomous Coding Agent
    participant ACT as aidoc-context-tools (CLI)
    participant MCP as Engramory MCP Gateway
    participant Neo4j as Neo4j / PG Fallback
    participant LiteLLM as LiteLLM Model Gateway
    participant FS as Local Workspace (Git)

    Agent->>ACT: aidoc-ctx slice --task IPLAN-042
    alt Engramory Online
        ACT->>MCP: Call tool sdd_context_slice(task_id="IPLAN-042")
        MCP->>Neo4j: MATCH (n)-[:DEPENDS_ON*1..6]->(m) WHERE n.uid = $uid
        Neo4j-->>MCP: Ancestor nodes [SPEC-042, ADR-042, BRD-001]
        MCP-->>ACT: Formatted Markdown Slice + File URIs
    else Offline Fallback
        ACT->>ACT: Traverse local embedded Kùzu / DFS graph
        ACT-->>Agent: Local ancestor closure
    end
    ACT-->>Agent: High-density prompt context (~1,500 tokens)
    opt Deep Visual Inspection
        Agent->>FS: Read file:///docs/sdd/05_ADR/ADR-042.md (inspect Mermaid chart)
    end
    Agent->>LiteLLM: POST /v1/chat/completions (Prompt + Context Slice)
    LiteLLM-->>Agent: Generated implementation code
    Agent->>FS: Write code to src/... & tests/...
    Agent->>ACT: aidoc-ctx check-invariants --base dev
    ACT-->>Agent: Invariants satisfied (0 contract erosion violations)
```

---

## 5. Rich Media, Diagrams & Visualizations

Technical specifications often contain diagrams, charts, and tables that are essential for humans and multimodal agents, but difficult or noisy to vectorize into vector databases:

1. **Mermaid & Sequence Diagrams**:
   - Remain authored in Markdown files in Git (`docs/sdd/`).
   - Engramory extracts textual metadata, structural relations (`DEPENDS_ON`, `SPECIFIES`), and summary abstracts.
   - When an agent requests a context slice, Engramory returns the summary and the local file link:

     ```markdown
     ### [ADR-042: Compound Graph UIDs](file:///workspace/docs/sdd/05_ADR/ADR-042.md#L1-L80)
     - **Summary**: Neo4j Community single-property compound uniqueness.
     - **Status**: accepted
     - **Full Document & Diagrams**: See linked file for architectural Mermaid flowcharts.
     ```

2. **Multimodal Agent Fallback**:
   - If an agent needs to reason about the visual flow, it uses its toolchain to read the linked file directly from the filesystem.

---

## 6. Implementation & Adoption Roadmap

1. **Continuous Ingestion in CI/CD**:
   - Add automated `engramory sdd ingest` actions to `aidoc-framework` and consumer repos on merge to `dev` and `main`.
2. **`aidoc-context-tools` Dual-Engine Adapter**:
   - Teach `aidoc-ctx slice` to check for `ENGRAMORY_MCP_URL` or local `engramory` binary, delegating to Engramory when available and falling back to embedded Kùzu when offline.
3. **Standard Agent MCP Configuration**:
   - Ship standardized `.cursor/mcp.json` and Antigravity MCP definitions across all ecosystem repositories.
