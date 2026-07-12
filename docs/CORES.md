# The Two Cores — Memory and Knowledge

Engramory is **one platform with two bounded cores**, not two projects. Decision recorded
in [ADR-08](../sdd/05_ADR/ADR-08_two_bounded_cores.yaml).

- **Memory core** — experiential, agent-authored, distilled (L1–L3).
- **Knowledge core** — curated documents, human/governed, citable (L0).

Both run on the **same spine** and are reached through **one MCP gateway**; they are
separated only on the axes where they genuinely differ.

---

## What each core is

| | **Memory core** | **Knowledge core** |
|---|---|---|
| Layers | L1 short-term (project/session), L2 long-term distilled (cross-project), L3 agent identity | L0 documents/sections |
| Author | AI agents (experience) | Humans / governed processes |
| Trust | Inferred — needs confidence + safety screening | Authoritative, citable |
| Write governance | Reflection + consolidation; injection/quarantine screening | Governed write + evidence reference ([ADR-06](../sdd/05_ADR/ADR-06_governed_write_failclosed.yaml)) |
| Lifecycle | Distilled → consolidated → forgotten | Versioned, permanent |
| Schema | `episodes`, `memories`, `agent_profiles`, `consolidation_runs`, `memory_retrievals` | `kb_sections` (migration 0003, MVP-1) |
| MCP tools | `memory_add`, `memory_search`, `memory_feedback`, `memory_forget`, `agent_profile_get` | `knowledge_ingest` (reads served through `memory_search` for MVP-1 — see boundary rule 4) |
| Code | `src/engramory/core/memory.py` | `src/engramory/core/knowledge.py` |

Memory is **per-agent and cross-project** (L2 recalls lessons distilled in *other*
projects) — not per-project only. Knowledge is **multi-tenant and scoped**, not a single
global pool.

## The shared spine (one platform)

Both cores share, and must not duplicate:

- **Postgres canonical store** + pgvector ([ADR-01](../sdd/05_ADR/ADR-01_postgres_spine.yaml), [ADR-05](../sdd/05_ADR/ADR-05_canonical_memory.yaml)).
- **Scope + isolation model** — `agent → project → domain → space` within a `tenant_id` wall ([ADR-07](../sdd/05_ADR/ADR-07_scope_model.yaml)).
- **One MCP gateway** — a single auth/scope decision per call; two tool namespaces on it.
- **Ports & adapters** ([ADR-02](../sdd/05_ADR/ADR-02_ports_and_adapters.yaml)) — the same StoragePort/VectorPort/GraphPort/… serve both cores.
- **Portability** — canonical text in Postgres; embeddings/graph are rebuildable projections.

```text
              Agents  ──MCP──►  ONE GATEWAY  (auth + scope, per call)
                                   │        │
                     memory_*  ◄───┘        └───►  knowledge_*
                        │                              │
                 ┌──────▼──────┐                ┌──────▼──────┐
                 │ MEMORY core │                │ KNOWLEDGE   │
                 │ L1·L2·L3    │  cross-link     │ core (L0)   │
                 └──────┬──────┘   by IDs        └──────┬──────┘
                        └───────────── SHARED SPINE ────┘
                 Postgres canonical · scope/tenant model · ports/adapters
```

## Boundary rules (keep the seam sharp)

1. **Separate schemas** — Memory tables never mix with `kb_sections`.
2. **Separate write governance** — Knowledge writes are governed (evidence); Memory writes are distilled and safety-screened. Agent-authored memory must never write into governed knowledge.
3. **Separate lifecycle** — Knowledge is versioned/permanent; Memory is distilled, consolidated, and forgotten.
4. **Separate MCP namespaces for writes** — `knowledge_ingest` vs `memory_*`, on one gateway. MVP-1 retrieval is deliberately unified: `memory_search` spans both cores in one scoped, ranked call (SPEC-01); a dedicated `knowledge_search` splits out when the knowledge core grows its own retrieval semantics.
5. **Cross-link by ID, don't duplicate** — a memory may cite a knowledge section; it does not copy it.

## Why one platform (not two projects)

- Splitting would **reverse the RAC/Nexus consolidation** Engramory was formed to achieve.
- The cores **share the entire spine** — two projects means two copies kept in sync forever.
- They are **coupled at runtime** — retrieval blends L0 + L2; distillation reads knowledge/episodes to write memory. A project boundary adds a network hop to the hot path and splits one scope/auth decision into two.

## Related plane: the execution ledger (not a core)

The iplan-runner / iplanic **execution ledger** is a *separate bounded context* — the
execution plane's system-of-record — **not** a third Engramory core and **not** Engramory's
storage backend. Engramory owns its own store; it **ingests** execution-events as L1 episodes
(via `EventsPort`) and cross-links provenance by ID. Decision recorded in
[ADR-09](../sdd/05_ADR/ADR-09_independent_memory_storage.yaml).

## When to revisit (extract a core later)

The ports boundary makes a later split cheap, so it is deferred until a trigger appears:

- Knowledge gains its **own non-agent users/product** at scale.
- The cores need **independent scaling or deployment cadence** one deployable can't serve.
- **Separate ownership/compliance** boundaries force it.

Until then: **one platform, two bounded cores.**
