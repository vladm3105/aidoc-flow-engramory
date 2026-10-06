# Engramory — Build Strategy & Recommendation

*Recorded 2026-07-05. Direction for how to build the memory concept, grounded in
agent-memory best practice, current trends, and production reality. Companion to
[ARCHITECTURE.md](ARCHITECTURE.md) (the concept) and
[research/MEMORY_CONCEPT_REVIEW.md](research/MEMORY_CONCEPT_REVIEW.md) (the critique).*

---

## Recommendation

**Own the spine, run native hybrid search, adopt distillation heuristics, lead with evaluation.**

- **Own the spine** — the Postgres-canonical store (`kb_sections`, `episodes`, `memories`, `audit_records`), `pgvector` dense embeddings, `ts_lex` lexical search, ports, access surface, scope model, and governance. Engramory natively executes storage, indexing, and hybrid retrieval.
- **No external RAG needed** — Engramory does **not** rely on third-party RAG frameworks like LightRAG or external vector databases. Embeddings, full-text indexes, and rank fusion live natively in PostgreSQL.
- **Adopt distillation heuristics (optional)** — for advanced multi-pass background consolidation (merging, generalizing lessons, pruning noise), pluggable processors may be adapted behind `MemoryPort`. For baseline dev and CI, Engramory provides an in-process native reflection worker (`engramory memory distill`) that runs without external dependencies.
- **Lead with evaluation** — a task-success-with-vs-without-memory harness on Engramory's own workloads is a first-class deliverable, not a later observability item.

This resolves the tension between adopting a tool vs. building infrastructure: Engramory builds the platform and owns the native data/retrieval spine, while treating advanced distillation algorithms as swappable processors.

## Rationale

### Best practice

- Own the canonical store; treat the engine as replaceable. Already decided (ADR-05).
- Retrieval, not storage, is the bottleneck — invest in native hybrid search (vector cosine + `ts_lex` GIN + recency + source-trust weighting) rather than layered external services.
- Evaluate continuously: unmeasured memory regresses without a visible signal.

### Trends (established, not speculative)

- Temporal / contradiction handling (bi-temporal validity + contradiction invalidation) is standard for durable memory — adopt the mechanism, not only the concept.
- Background ("sleep-time") consolidation is a validated direction; the reflection + consolidation loop is aligned with it.
- MCP is the durable access standard for cross-agent memory — keep it as the surface.
- Memory safety (poisoning of self-authored memory) is a rising concern — address source trust weighting early.

### Reality (constraints to plan around)

- No engine "solves" agent memory; public benchmark scores do not transfer to a specific domain. Re-tuning against local tasks is required.
- "Human-like / endless memory" overstates the mechanism. The deliverable is compression + retrieval, with a bounded working set (see ARCHITECTURE "What 'endless' means").
- Procedural "skills" are re-injected text, not model weight updates. Set that expectation.
- The graph is optional early: pure Postgres + strong vector retrieval covers most needs; promote to Neo4j only when multi-hop retrieval is required (ADR-03).
- Building a platform is a larger commitment than adopting one; it is justified here by the per-agent memory, hierarchical multi-agent access control, supervisory audit loop, and portability requirements.

## Engine selection (behind `MemoryPort`)

| Option | License | Role | When to choose |
|--------|---------|------|----------------|
| **Native reflection worker** (default) | Apache-2.0 / internal | Native in-process reflection (`engramory memory distill`) | Zero-dependency baseline in dev, CI, and local test suites |
| **Mem0** (adapter) | Apache-2.0 | Pluggable background processor | Pragmatic start for external distillation service |
| **LangMem** (adapter) | MIT | Pluggable SDK | Explicit semantic/episodic/procedural + consolidation control |
| **Cipher** (adapter) | Elastic 2.0 (source-available) | MCP-native | Turnkey coding-agent reflection; license acceptable for internal use |

The native PostgreSQL reflection worker is active out-of-the-box. Any external processor is strictly an adapter behind `MemoryPort`, keeping PostgreSQL canonical.

## Sequence

1. **Vertical slice first** — one agent, one real workload: Postgres schema + one
   `MemoryPort` adapter (Mem0) + `memory_add` / `memory_search` over MCP. Inspect what the
   reflection pass distills before adding breadth.
2. **Evaluation harness in parallel** — task success ± memory; retrieval precision on
   local tasks.
3. **Feedback loop from the start** — log retrieval → outcome, even minimally; this is what
   converts accumulation into learning.
4. **Temporal correctness** — contradiction detection + a confidence-update rule before the
   store scales.
5. **Defer** — graph engine, multi-tenant hardening, and advanced distillation until the
   slice demonstrates value.
6. **Safety early** — trust-weight memory by source; quarantine any memory crossing an
   agent or tenant boundary.

## Mapping to the roadmap

- **MVP-1 (BRD-01)** carries the vertical slice + evaluation harness + feedback loop, using
  an adopted `MemoryPort` engine.
- **Later cycles** (cloud, advanced distillation, domain config, multi-tenancy, observability,
  portability tooling, multi-project ops) remain sketches until the slice proves value; see
  [../roadmap/ROADMAP.md](../roadmap/ROADMAP.md).

## Retain unchanged

Postgres-canonical spine, three memory types, provenance, temporal validity, ports &
adapters, LiteLLM gateway, MCP access, and the scope model (ADR-07). The next design effort
targets the learning half — feedback, confidence dynamics, contradiction handling,
forgetting, safety, and evaluation — per `research/MEMORY_CONCEPT_REVIEW.md`.
