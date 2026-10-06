# The Two Cores — Memory and Knowledge

Engramory is **one platform with two bounded cores**, not two projects. Decision recorded
in [ADR-08](../sdd/05_ADR/ADR-08_two_bounded_cores.yaml).

- **Memory core** — experiential, agent-authored, distilled (L1–L3).
- **Knowledge core** — curated documents, governed, citable (L0).

Both run on the **same PostgreSQL spine** and are reached through **one unified access surface** (`AccessSurface`, exposed via the `engramory` CLI and MCP gateway); they are separated only on the axes where they genuinely differ.

---

## What each core is

| | **Memory core** | **Knowledge core** |
|---|---|---|
| Layers | L1 short-term (project/session), L2 long-term distilled (cross-project), L3 agent identity | L0 documents/sections (`kb_sections`) |
| Author | AI agents (experience, outcomes, lessons) | Humans / governed processes (specifications, ADRs) |
| Trust | Inferred — needs confidence + safety screening (`source_trust`: `human`/`tool`/`agent`) | Authoritative, citable, versioned |
| Write governance | Reflection + consolidation; injection/quarantine screening | Governed write + evidence reference ([ADR-06](../sdd/05_ADR/ADR-06_governed_write_failclosed.yaml)) |
| Lifecycle | Distilled → consolidated → forgotten | Versioned, permanent (append-only) |
| Schema | `episodes`, `memories`, `agent_profiles`, `consolidation_runs`, `memory_retrievals` | `kb_sections` (migration 0003, MVP-1) |
| Scale profile | High-frequency append + periodic reflection/compaction | Massive document corpora (e.g. 160+ SDD docs, 17 MB in `b-local-privy`) |
| MCP / CLI tools | `memory add`, `memory search`, `memory feedback`, `memory forget`, `profile get` | `knowledge_ingest` (reads served through `memory search` for MVP-1) |
| Code | `src/engramory/core/memory.py` | `src/engramory/core/knowledge.py` |

Memory is **per-agent and cross-project** (L2 recalls lessons distilled in *other* projects) — not per-project only. Knowledge is **multi-tenant and scoped**, not a single global pool.

---

## Multi-Agent Access Hierarchy across the Cores

Engramory serves a heterogeneous fleet with explicit privilege boundaries:

```text
┌────────────────────────────────────────────────────────────────────────────────────────┐
│ OPERATIONS ASSISTANT (Supervisor Agent / Virtual CTO)                                  │
│ • Scopes: domain, space (tenant-wide)                                                  │
│ • Knowledge Core: Full visibility into all project specs, cross-cutting ADRs, and PRDs │
│ • Memory Core: Queries cross-project patterns, shared playbooks, and fleet-wide errors  │
│ • Audit: Continuous streaming inspection of audit_records and executor episodes        │
└──────────────────────────────────────────┬─────────────────────────────────────────────┘
                                           │
                        Hierarchical Authorization
                                           │
    ┌──────────────────────────────────────┴──────────────────────────────────────┐
    │                                                                             │
┌───▼─────────────────────────────────────┐   ┌───▼─────────────────────────────────────┐
│ PROJECT EXECUTOR A (Repo A worktree)    │   │ PROJECT EXECUTOR B (Repo B worktree)    │
│ • Scopes: agent, project (Repo A only)  │   │ • Scopes: agent, project (Repo B only)  │
│ • Knowledge: Isolated to Repo A docs    │   │ • Knowledge: Isolated to Repo B docs    │
│ • Memory: Private agent + Repo A memory │   │ • Memory: Private agent + Repo B memory │
└─────────────────────────────────────────┘   └─────────────────────────────────────────┘
```

1. **Project Executors (Coding Agents):** Bounded strictly to `scopes = ["agent", "project"]`. They cannot inspect documents or memories belonging to other repositories.
2. **Operations Assistant (Supervisor):** Operates at `domain` or `space` scope. It synthesizes architectural coherence across projects, audits compliance against requirements, and directs workers.
3. **Supervisory Audit Stream:** Every read, write, deny, and memory feedback across both cores is logged to `audit_records` and `memory_retrievals`, allowing the supervisor to detect drift and assist stalled workers near real-time.

---

## Large-Scale Technical Knowledge (`kb_sections`)

Real-world software engineering repositories (such as `b-local-privy` or `aidoc-flow-operations`) contain extensive documentation trees:

- **Corpus Scale:** 160+ markdown files spanning 10 SDD layers (`01_BRD` to `08_IPLAN`), plus ADRs, runbooks, and changelogs.
- **Structured Anchors:** Stored in `kb_sections` with `doc_id`, `citation` (header path), `version`, and `text`.
- **Hybrid Retrieval:** Dense embeddings (`pgvector`) fused with PostgreSQL lexical search (`ts_lex`) enables exact specification retrieval (e.g., specific error codes, interface types) alongside conceptual semantic matching.
- **Traceability:** Models cumulative `@-tag` lineage (`@brd`, `@prd`, `@ears`, `@bdd`, `@adr`, `@spec`, `@tdd`, `@ip-lan`, `@depends`) directly within the database.

---

## The shared spine (one platform)

Both cores share, and must not duplicate:

- **Postgres canonical store** + pgvector ([ADR-01](../sdd/05_ADR/ADR-01_postgres_spine.yaml), [ADR-05](../sdd/05_ADR/ADR-05_canonical_memory.yaml)).
- **Scope + isolation model** — `agent → project → domain → space` within a `tenant_id` wall ([ADR-07](../sdd/05_ADR/ADR-07_scope_model.yaml)).
- **One unified access surface** — a single auth/scope decision and fail-closed audit log per call.
- **Hexagonal ports & adapters** ([ADR-02](../sdd/05_ADR/ADR-02_ports_and_adapters.yaml)) — identical StoragePort/VectorPort/GraphPort/MemoryPort serve both cores.
- **Portability** — canonical text in Postgres; embeddings and graphs are rebuildable projections.

---

## Boundary rules (keep the seam sharp)

1. **Separate schemas** — Memory tables never mix with `kb_sections`.
2. **Separate write governance** — Knowledge writes are governed (evidence); Memory writes are distilled and safety-screened. Agent-authored memory must never write into governed knowledge.
3. **Separate lifecycle** — Knowledge is versioned/permanent; Memory is distilled, consolidated, and forgotten.
4. **Separate MCP namespaces for writes** — `knowledge_ingest` vs `memory_*`, on one gateway. MVP-1 retrieval is deliberately unified: `memory_search` spans both cores in one scoped, ranked call (SPEC-01); a dedicated `knowledge_search` splits out when the knowledge core grows its own retrieval semantics.
5. **Cross-link by ID, don't duplicate** — a memory may cite a knowledge section; it does not copy it.

---

## Why one platform (not two projects)

- Splitting would reverse the platform consolidation Engramory was formed to achieve.
- The cores **share the entire spine** — two projects means two copies of Postgres, auth, and ports kept in sync forever.
- They are **coupled at runtime** — retrieval blends L0 + L2; distillation reads knowledge/episodes to write memory. A project boundary adds a network hop to the hot path and splits one scope/auth decision into two.

---

## Related plane: the execution ledger (not a core)

The iplan-runner / iplanic **execution ledger** is a *separate bounded context* — the
execution plane's system-of-record — **not** a third Engramory core and **not** Engramory's
storage backend. Engramory owns its own store; it **ingests** execution-events as L1 episodes
(via `EventsPort`) and cross-links provenance by ID. Decision recorded in
[ADR-09](../sdd/05_ADR/ADR-09_independent_memory_storage.yaml).

---

## When to revisit (extract a core later)

The ports boundary makes a later split cheap, so it is deferred until a trigger appears:

- Knowledge gains its **own non-agent users/product** at scale.
- The cores need **independent scaling or deployment cadence** one deployable can't serve.
- **Separate ownership/compliance** boundaries force it.

Until then: **one platform, two bounded cores.**
