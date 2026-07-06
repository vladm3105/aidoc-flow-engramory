# Layered Agent Memory — Design

*June 22, 2026 · Designing short-term + long-term + per-agent distilled memory on top of AI Knowledge RAC*

> **Historical design input.** This document is superseded by [ARCHITECTURE.md](ARCHITECTURE.md) and the ADRs where they conflict. Note in particular: RAC has **merged into Engramory** — it is not a live separate platform; dev object storage is **MinIO**, not GCS. Scope/tenant vocabulary here predates [ADR-07](../sdd/05_ADR/ADR-07_scope_model.yaml): the current isolation column is `tenant_id`, and the current scope ladder is **agent → project → domain → space** (`space` = tenant-wide; the old `shared` scope value is retired). Read the body below as design rationale, not current spec.

---

## Is it possible? Yes — here's the honest version

All three things you want are achievable with today's open-source tools:

1. **Short-term memory** scoped to the current project + **long-term memory** distilled from all past projects.
2. A **knowledge base** of project docs, plans, notes, memos.
3. **Each agent accumulates its own unique context** (experience, lessons, errors), which is **distilled** and effectively **endless**.

The one expectation to set clearly: **"endless context" = compression + retrieval, not an infinite context window, and not model retraining.** The agent stores experience as text, *distills* it into dense lessons, and at each task retrieves only the relevant slice. The body of memory grows without limit; the amount loaded into the model stays small and bounded. That is precisely how human memory works — you don't reload your whole life, you carry consolidated lessons and recall what's relevant. Done well, this approximates "the agent remembers what it has learned" for the cases that matter — but it is relevant recall, not total recall: the system will sometimes fail to surface a past detail, by design.

---

## The mental model: four layers

Your three requirements map onto four memory layers, which mirror cognitive science:

| Your requirement | Layer | Lifespan | Scope | Maps to (human) |
|---|---|---|---|---|
| Knowledge base (2) | **L0 — Documents** | Permanent, versioned | Project / domain | Reference library / textbooks |
| Short-term memory (1) | **L1 — Working memory** | Current project | Per project | What you're actively holding in mind |
| Long-term memory (1) | **L2 — Long-term store** | Permanent, distilled | Cross-project | Semantic + episodic + procedural memory |
| Per-agent experience (3) | **L3 — Agent identity & distillation** | Permanent, evolving | Per agent (+ shared) | A person's accumulated life experience |

### L0 — Knowledge base (documents)
Project documentation, plans, notes, memos. Authoritative, human-curated, citable. **You already have this: it's RAC** (section-level retrieval, Neo4j + pgvector, citations, multi-tenant). Keep it as-is.

### L1 — Short-term / working memory (per project)
A bounded, project-namespaced store of the *current* project's live state: decisions made, open threads, recent errors, "what we're doing right now." Fast to read/write, scoped so it never mixes projects. At project end it's archived — its durable lessons get promoted to L2, the rest is dropped.

### L2 — Long-term memory (cross-project, distilled)
The permanent store, organized by the three classic memory types:

- **Semantic** — facts and knowledge: "our auth pattern is X," "client Y prefers Z."
- **Episodic** — what happened, when, in which project: "in project A we tried approach B, it failed because C."
- **Procedural** — reusable skills and playbooks distilled from experience: "when you see error pattern X, do Y." This is where *lessons and errors* become *skills*.

### L3 — Per-agent identity & distillation
Each agent gets its **own memory namespace** — its unique accumulated experience — plus access to a **shared/team namespace**. A **distillation (reflection) process** continuously promotes L1 → L2 and compacts L2 so it stays dense. This loop is what makes the context feel endless.

---

## The distillation loop — the "sleep" that makes it endless

This is the heart of requirement 3, and the genuinely hard part. The mechanism:

```
During work:    agent retrieves relevant L0/L2 memory + L1 project state → acts →
                writes new episodes (decisions, errors, outcomes) to L1.

After session:  REFLECTION pass reads recent L1 episodes and asks
                "what is worth keeping?" → writes distilled lessons/skills/facts to L2,
                tagged with agent + project + timestamp.

Periodically:   CONSOLIDATION pass over L2 → merge duplicates, generalize repeated
                lessons into stronger rules, end-date facts that are no longer true,
                prune low-value noise. Keeps L2 dense and bounded.
```

Three properties this gives you:
- **Endless, because bounded.** Raw episodes are compressed into a roughly constant-size, high-signal long-term store. You retrieve from a dense library, not an ever-growing transcript.
- **Self-correcting.** Errors become procedural rules ("don't do X"); superseded facts get invalidated instead of lingering.
- **Per-agent personality.** Because distillation writes to each agent's namespace, agents genuinely diverge based on what they've each lived through — while still sharing team knowledge.

---

## Mapping to open-source tools (minimize custom build)

You do **not** need to build all of this. Split by layer:

| Layer | Build vs. adopt | Recommended OSS |
|---|---|---|
| L0 Documents | **Keep — already built** | **RAC** (yours) |
| L1 Short-term | Thin — a project-scoped namespace | RAC's pgvector + a `project_id` scope, or the memory engine's session scope |
| L2 Long-term (semantic/episodic/procedural) | **Adopt** | **LangMem** (MIT) or **Cipher** (ELv2) |
| L3 Distillation + per-agent | **Adopt the loop, don't reinvent it** | **LangMem Memory Manager** (consolidation) / **Cipher System-2 reflection** / **Letta** if each agent should be a stateful service |

**The decisive piece is the distillation loop (L3).** It's the part most DIY projects get wrong, and it's exactly what these tools already implement:

- **LangMem (MIT)** — explicit **semantic / episodic / procedural** memory + a **Memory Manager that decides what to store/update/delete and consolidates over time**. This is the closest match to your three memory types *and* the distillation requirement, with the cleanest license. Trade-off: it's an SDK (some glue to run as a service behind your MCP).
- **Cipher (ELv2, source-available)** — turnkey for coding agents: **System 1 (knowledge)** + **System 2 (reflection = lessons/reasoning)** + per-agent **workspace memory**, cross-project, MCP-native. Fastest to stand up; license is source-available (fine for internal use).
- **Letta (Apache-2.0)** — if you want each agent to literally *be* a stateful entity whose tiered memory (core = short-term, archival = long-term) self-edits. Strong conceptual fit for "each agent's own endless context," but it's a runtime your custom agents run inside — best for one flagship agent, not as a wrapper over Codex/Claude Code.

### Integration reality: which agents can use which engine

This matters a lot for your mixed fleet (Codex, Claude Code, Hermes, custom). The deciding factor is **how the agent talks to the memory engine** — directly in code (SDK) or over MCP. Closed runtimes you don't write the code for (Codex, Claude Code, Hermes) can only consume memory **over MCP**.

| Engine | Ships an MCP server? | Custom Python agent | Codex / Claude Code / Hermes | Effort to cover all your agents |
|---|---|---|---|---|
| **LangMem** | **No** — SDK only (framework-agnostic core, happiest in LangGraph) | ✅ direct via SDK | ⚠️ only via an **MCP wrapper you write** | Medium — you build one thin MCP adapter |
| **Cipher** | ✅ MCP-native, ships adapters | ✅ | ✅ out of the box | Lowest |
| **Mem0** | ✅ community MCP servers + Python/Node SDK | ✅ | ✅ via MCP server | Low |
| **Letta** | ✅ MCP-native (but it's a runtime) | ✅ (run inside Letta) | ⚠️ they'd have to run inside Letta | High for closed runtimes |

**So, directly answering "can LangMem work with any agent?"**
- **Custom Python agents:** yes — call the SDK in your code, any storage backend (pgvector, Mongo, Postgres).
- **Codex / Claude Code / Hermes:** **not out of the box** — LangMem has no MCP server, so you must wrap it behind one. Codex connects via its TOML MCP config, Claude Code via MCP, Hermes via `hermes mcp add`. Once wrapped, all of them work.

**The good news for you specifically:** you already run an MCP server (RAC, with ~79 tools). Adding a handful of memory tools (`memory_search`, `memory_add`, `memory_consolidate`) that call LangMem underneath is a natural, low-cost extension of infrastructure you already operate — so the "MCP wrapper" cost is much smaller for you than for someone starting from zero.

**If you want zero glue code and instant compatibility with all four agent types, Cipher or Mem0 are the better fit than LangMem** — they're MCP-native today. The trade is: Cipher (ELv2, turnkey, coding-agent-shaped reflection) and Mem0 (Apache-2.0, simplest layer) vs. LangMem (MIT, richest explicit semantic/episodic/procedural + consolidation, but you wire the MCP surface yourself — cheap because you already have RAC's MCP server).

### Recommended split for your stack

```
            ┌──────────────────────────────────────────────┐
            │   Agents: Claude Code · Codex · Hermes · CLI   │
            └───────────────┬───────────────┬──────────────┘
                            │ MCP           │ MCP
            ┌───────────────▼──────┐  ┌─────▼─────────────────────┐
            │  RAC (your platform) │  │  Memory engine            │
            │  L0 Knowledge base   │  │  L1 short-term (project)  │
            │  docs/plans/notes    │  │  L2 long-term (distilled) │
            │  multi-tenant + sec  │  │  L3 per-agent + reflection │
            └──────────────────────┘  └───────────────────────────┘
                     pgvector+Neo4j          LangMem (or Cipher)
```

**RAC stays your knowledge base and infrastructure backbone** (it's stronger than the memory engines at documents, multi-tenancy, security). **The memory engine owns the experiential layers** (short/long-term + per-agent distillation) — the part RAC doesn't have yet. Both are reachable by every agent over MCP.

---

## Decision record: Obsidian's role & human access pattern

*Captured from the design discussion so the rationale is documented.*

**Context.** The knowledge base is **mostly agent-written and agent-read**. Humans don't browse or hand-edit it directly — **a human extracts knowledge by working through an AI agent** (asking the agent, which queries the store and answers), not by opening a vault.

**Decision: Obsidian is NOT used in this system.** Neither as a storage layer behind RAC, nor as the L0 authoring front-end, nor as short-term memory. Everything lives in infrastructure you already own.

| Layer | Where it lives | Obsidian? |
|---|---|---|
| **L0 — Knowledge base** (docs, plans, notes, memos) | **RAC** (sections in Postgres, graph in Neo4j, files in GCS) | ❌ No |
| **L1 — Short-term memory** (current project) | **RAC's Postgres**, a project-scoped namespace (`project_id`), archived/distilled into L2 at project end | ❌ No |
| **L2 — Long-term distilled memory** | **Your Postgres** (canonical: raw text + provenance), processed by the memory engine | ❌ No |
| **L3 — Per-agent identity + distillation** | Memory engine over your Postgres | ❌ No |
| **Human audit view** (optional) | A **generated, read-only Markdown export** — a *projection* of the Postgres store, built only if a human ever needs to browse provenance | ◻️ Optional, generated — not a source of truth |

**Rationale.**
- Obsidian's entire value is the **human** layer — friendly editor, graph view, wikilinks, Git PR-review. With an agent-written/agent-read KB and **human access mediated through an agent**, none of that value is realized.
- A Markdown vault behind RAC would only add a **third surface to keep in sync** (vault ↔ Postgres ↔ Neo4j) for storage/retrieval RAC already provides.
- **Short-term memory** is high-frequency, concurrent, and project-scoped — it needs **structured, queryable** recall (a Postgres query), not a file scan, and Markdown files hit the Git-async/write-contention problem under concurrent agent writes.
- The **human "extract knowledge via an agent"** pattern means the right human interface is **agent queries over RAC + the memory engine**, not a separate editor. This keeps one canonical store and avoids drift.

**Consequence / guidance.**
- Build human knowledge-extraction as an **agent capability** (the agent reads L0–L2 and answers), not as a vault to browse.
- If human-readable output is ever needed (audit, sharing, provenance review), generate it **on demand as a read-only Markdown/report export** from Postgres — a downstream artifact, never the primary store. This also doubles as a portability snapshot (plain Markdown).
- Revisit Obsidian only if the usage pattern changes — i.e. if humans start **hand-authoring and curating** the KB directly. As long as it's agent-written/agent-read with agent-mediated human access, it stays out.

---

## Portability & migration — moving the agent's "brain"

This is the right thing to optimize for, because it's where lock-in actually hurts. Honest state of play in 2026:

### Do the engines have export/import? Mostly yes — but at different completeness, and there is **no universal cross-tool standard yet**.

| Engine | Export / import | Notes |
|---|---|---|
| **Letta** | **Agent File (`.af`)** — an *open* format for serializing a stateful agent (system prompt, memory blocks, tools, LLM config). Import/export via ADE, REST, SDK. | The closest thing to an open **agent-portability standard**. ⚠️ But today it does **not** include "Passages" (archival long-term memory) or conversation history — so it moves the agent's *config + core memory*, not the full distilled long-term store yet. |
| **Mem0** | **First-class export** — `create_memory_export()` with a custom schema, `get_all()` bulk dump; migration scripts (OSS→platform). | Exports are async and expire after 7 days (download locally). Plus it's Apache-2.0 self-hosted, so you also own the Postgres/Qdrant/Neo4j underneath. Strong portability. |
| **Cipher** | No dedicated interchange format documented | Portability = **you own the stores** (Postgres + Neo4j + Qdrant/Milvus). Migrate via standard DB dumps. |
| **LangMem** | No special format | Data lives in **your** store (pgvector/Postgres/Mongo). Portability = your own DB dumps — no lock-in, but no turnkey "export button" either. |
| **Markdown vault / MemPalace** | Inherently portable | Plain Markdown files / local SQLite. The most portable of all — copy the folder. |

Emerging but not yet standard: open interchange formats like **`.vmig.jsonl`** (connectors for pgvector/Qdrant/Chroma/Weaviate/Pinecone) and "memory passport" projects. MCP has become the standard for *access*, not for *storage/portability*.

### The deeper truth: a vendor export button is not what saves you

Two things matter more than any tool's export feature:

1. **Own the canonical store in an open format.** If your durable memory lives in *your* Postgres (or Markdown), you can always `pg_dump` / copy it, regardless of what the engine does. Self-hosting on standard databases — which all your candidates support — is a stronger portability guarantee than a proprietary export API.

2. **Semantic portability is the genuinely hard, unsolved part.** Moving the *bytes* is easy; preserving *meaning* is not. Two issues:
   - **Schema/representation differences** — each engine stores distilled memory in its own shape, so a straight dump rarely loads cleanly into a different engine without a translation step.
   - **Embeddings are model-specific** — vectors generated by one embedding model are meaningless to another. On any model/version migration you must **re-embed from the source text**. So you must keep the **raw text of every memory**, not just its vector.

### The design principle that protects you (and why it fits your stack)

**Keep the canonical distilled memory in your own open store; treat the memory engine as a replaceable processor over it.** Concretely:

- Persist L2 (long-term distilled memory) as **plain structured records in your own Postgres** — raw text + metadata + **provenance** (which project/episode/agent it came from + timestamps) — not locked inside a vendor's opaque blob.
- Let the engine (LangMem/Cipher/Mem0) do the *processing* — retrieval, consolidation — but the source of truth is your schema.
- Migration to another engine or version then becomes: point the new processor at your store → re-embed from the kept raw text → re-index. The "brain" survives the platform change.

This is a strong argument for the **LangMem-behind-RAC** option *specifically for you*: the canonical memory sits in **your** Postgres (which you already own, dump, and migrate), embeddings can be regenerated from kept text, and the engine on top is swappable. You get maximum portability precisely because you're not trusting someone else's export button.

If you'd rather have a ready-made portable artifact to hand around, **Letta's `.af`** is the best open format today for the *agent definition*, and **Mem0's export API** is the most mature for the *memory contents* — but for the full long-term "brain," owning the store beats both.

---

## What a single task looks like end-to-end

1. **Load identity:** small per-agent profile (who I am, my standing preferences) from L3.
2. **Retrieve:** top-K relevant lessons/skills from L2 (agent + shared) + current project state from L1 + relevant docs from L0 (RAC). All scoped, all small.
3. **Act:** the agent does the work with that focused context.
4. **Record:** write new decisions/errors/outcomes to L1 (project-scoped).
5. **Reflect (async):** distill worthwhile L1 episodes into L2 lessons/skills under the agent's namespace.
6. **Consolidate (periodic):** compact L2 so it stays dense — merge, generalize, invalidate, prune.

The model never sees more than a focused working set, yet behaves as if it carries everything it has ever learned.

---

## Honest hard parts

- **Distillation quality is the whole game.** Deciding *what's worth keeping* and *how to generalize a lesson* is where value is won or lost. Use an off-the-shelf consolidation loop (LangMem/Cipher) before writing your own — then tune.
- **Retrieval scoping.** Short-term must not leak across projects; long-term must surface the *right* lesson at the right time. Namespacing (project_id, agent_id, shared) + good ranking matters more than raw storage.
- **Two stores to reconcile.** RAC (L0) and the memory engine (L1–L3) overlap on semantics. Keep a clear rule: **documents → RAC; distilled experience → memory engine.** Cross-link by IDs, don't duplicate.
- **No free lunch on "truth."** Consolidation can entrench a wrong lesson. Keep provenance (which project/episode a lesson came from) so bad rules can be traced and retired.

---

## Recommended next step

Pilot the experiential layer cheaply before committing: stand up **LangMem as a memory service behind your existing MCP**, wire L1 (project scope) + L2 (semantic/episodic/procedural) + the Memory Manager for L3 distillation, and keep RAC as L0. Run it on one real project for a week and inspect what the reflection pass distills. If the distilled lessons are good, scale it; if integration friction with RAC dominates, fold the L1/L2 tables into your own Neo4j+pgvector and port only the consolidation logic — by then you'll know exactly what you need.

**Verdict: yes, it's possible — and you're already most of the way there.** You have L0 and the infrastructure. What's missing is the experiential memory engine (L1–L2) and the distillation loop (L3), and those are the parts worth adopting rather than building from scratch.
