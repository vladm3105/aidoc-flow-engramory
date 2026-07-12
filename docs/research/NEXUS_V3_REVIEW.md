# Nexus Knowledge Platform v3 — Review (and how it relates to RAC + the memory design)

> Historical input — decisions superseded by [docs/ARCHITECTURE.md](../ARCHITECTURE.md) and [sdd/05_ADR/](../../sdd/05_ADR/) where they conflict.

*Reviewed June 22, 2026 · based on `Knowledge_Platform_Nexus_v3_2/` (Executive Summary, Technical Spec v3.0, Changelog v3)*

---

## What Nexus is

Nexus is a **domain-agnostic, enterprise AI knowledge platform on 100% GCP** — and it reads as the **strategic superset that your RAC project plugs into**. RAC provides the **per-project domain-config layer**; Trading is just one example project (not RAC's identity). The platform equation makes the pattern explicit:

```text
Nexus = Core Platform + Domain Config (one config per project)
  Nexus + Trading config   ← Trading is one example project
  Nexus + USFS config
  Nexus + Legal / Medical / Corporate / <your project> ...
```

It's a serious, more complete design than RAC:

- **Agents:** 8 specialist agents / 34 skills (Coordinator, Knowledge, Verification, Multimodal, Ingestion, Gateway + 2 config agents), A2A protocol for agent-to-agent.
- **Data layer:** Cloud SQL **PostgreSQL 16** with **pgvector + Apache AGE** (graph), Vertex AI Vector Search, GCS, BigQuery, **Memorystore (Redis)**, Firestore, Pub/Sub.
- **Models:** **Vertex AI Model Garden (200+ models)** — configurable at platform/domain/user level. Explicit "no model lock-in."
- **Security:** 4D authorization (Action × Skill × Resource × Zone), 4 trust levels, OWASP ASVS L2 + Top-10-LLM, Bell-LaPadula zones.
- **UX:** unified chat + 16 A2UI components, User Spaces (Claude-Projects pattern) with roles/sharing.
- **Ops:** OpenTelemetry LLM tracing, cost tracking, audit, ensemble verification claiming ≥99% citation accuracy.
- **Memory:** a 3-layer system (detailed below).
- **Status:** "Ready for Implementation" (Jan 2026). This is a spec/design package (3 docs + SVG), not yet code.

**Verdict on the platform itself:** strong, coherent, and more mature than RAC on architecture, security, multimodality, and GCP-native scaling. As with RAC, the weight is on platform/security/UI; the agent-*cognition* side (memory types, learning) is the thin part — and that's exactly what your recent questions are about.

---

## The RAC ↔ Nexus relationship — decide this first

These two projects overlap massively but have **diverged on core technology**, which is a risk you should resolve before building more:

| Dimension | RAC | Nexus v3 |
|---|---|---|
| Position | Trading-focused knowledge platform | Domain-agnostic platform (Trading = one config) |
| Graph engine | **Neo4j** | **Apache AGE** (Postgres extension) |
| LLM / embeddings | **OpenAI** (gpt-4o-mini, text-embedding-3-small) | **Vertex Model Garden** (Gemini default, 200+) |
| Vector store | Postgres/pgvector | pgvector + Vertex Vector Search |
| Secrets | GCP Secret Manager | Secret Manager + Identity Platform |
| Maturity | Working, incomplete (per its TODO) | Design complete, pre-implementation |

**They are two stacks for the same idea, with two different graph databases and two different model providers.** Running both is duplicated effort and a future migration headache. Recommendation: **pick Nexus as the strategic target** (it's the more complete, domain-agnostic vision) and **fold RAC into it as the Trading domain config** — porting RAC's working pieces (its 79 MCP tools, parsers, storage abstraction) onto the Nexus stack. Consolidate on **one graph engine** (AGE-in-Postgres reduces moving parts vs separate Neo4j) and one model layer. If RAC's Neo4j/OpenAI choices are deliberate and better for you, then make *that* the standard and simplify Nexus — but don't keep both.

---

## How Nexus's memory maps to the layered design we built

Nexus already has a 3-layer memory system. Here's the honest mapping to the L0–L3 model from [MEMORY_DESIGN.md](../MEMORY_DESIGN.md) (formerly `layered-agent-memory-design.md`):

| Nexus layer | Storage / TTL | Maps to our design | Verdict |
|---|---|---|---|
| **User Spaces → Knowledge Base** (documents) | Cloud SQL + GCS | **L0 — Knowledge base** | ✅ Covered |
| **L1: Session Memory** | Memorystore / session TTL | Part of **L1 short-term** | ✅ but *session*-scoped, narrower than a project |
| **L2: Space Memory** | Cloud SQL / indefinite | **L1 project working memory** + curated space knowledge | ✅ Space ≈ project |
| **L3: User Memory** | Cloud SQL + **Memory Bank** / indefinite | Part of **L2 long-term** (preferences + facts) | ⚠️ Semantic only |

What this comparison reveals — **two real gaps versus your stated goal** ("each agent keeps knowledge + skills + experience across projects, distilled, endless"):

1. **Memory is scoped per-USER and per-SPACE, not per-AGENT.** Nexus's "L3 User Memory" belongs to the human user. You explicitly want **each agent** to accumulate its *own* life-experience (lessons, errors) across projects. There is no **agent-identity memory namespace** in Nexus (or RAC). This is the central missing concept.
2. **No general cross-project distillation/consolidation engine.** Nexus lists "Self-Learning — continuous improvement from usage patterns" as a capability, and the Trading domain has a real **learning loop** (`post_trade_review`, `meta_review_frequency: 5`, bias-pattern + accuracy tracking). But that is **domain analytics** (trading-prediction accuracy), not the general **semantic/episodic/procedural distillation** that turns experience into reusable skills across *any* project. The "endless context" loop is still unspecified — same gap I found in RAC.

In short: Nexus covers **L0, L1, and the semantic half of L2** well. It is missing **episodic + procedural memory, per-agent identity, and the distillation loop (L3)** — precisely the layers that make memory "human-like."

---

## Portability — an important caution for Nexus specifically

Your biggest concern was migrating the agent's brain across platforms/versions. Nexus has a **split portability profile**:

- ✅ **Model portability:** excellent — Model Garden means you can swap Gemini/Claude/Llama/Mistral freely.
- ⚠️ **Infrastructure portability:** weak — Nexus is "100% GCP" and binds to Vertex, Cloud SQL, Memorystore, Firestore, BigQuery, Identity Platform, and **Vertex AI "Memory Bank."** That's deep GCP lock-in at the platform level.
- ⚠️ **The "Memory Bank" choice for L3 is the portability risk.** If long-term agent memory lives in Google's managed Memory Bank, that's the *least* portable place to keep the very thing you most want to be able to migrate.

Applying the principle from the design doc (*own the canonical store in an open format; treat the engine as a replaceable processor*): **keep the canonical distilled memory in Cloud SQL PostgreSQL** (raw text + provenance + embeddings you can regenerate) and use Vertex Memory Bank, if at all, as an *accelerator/cache* — never the source of truth. Postgres is portable (pg_dump to any Postgres anywhere); Memory Bank is not. This keeps Nexus's scale benefits without trapping the brain.

---

## Recommendation

1. **Consolidate on one platform.** Adopt **Nexus as the strategic base** and migrate RAC into it as the **per-project domain-config layer** (each project = one config; Trading is just one example). Stop maintaining two divergent stacks (resolve Neo4j-vs-AGE and OpenAI-vs-Vertex). This is the highest-leverage decision.
2. **Add the missing experiential layer** — the part neither project has and the part you actually asked for:
   - **Per-agent memory namespace** (agent_id), alongside the existing user/space scoping, so each agent builds its own cross-project experience and you can still share a team namespace.
   - **Episodic + procedural memory** tables (what happened/when/which project; distilled skills and lessons-from-errors), not just preferences/facts.
   - **A general distillation/consolidation loop** (the "sleep" pass) that promotes session/space episodes into per-agent long-term memory and compacts it — generalize the Trading learning loop into a domain-agnostic engine.
3. **Keep the canonical memory in Cloud SQL Postgres**, engine-neutral (raw text + provenance), so model and platform migrations re-embed rather than re-architect. Use LangMem/Cipher/Mem0 *or* Vertex Memory Bank as the processor on top — swappable.
4. **Sequence:** platform consolidation → per-agent memory schema → distillation loop → (optional) managed accelerators. Cognitive layer next, not more platform/security surface.

**Bottom line:** Nexus is the better strategic foundation than RAC, and it already nails L0/L1 and infrastructure. But on your real goal — *per-agent, distilled, endless, portable memory* — Nexus has the **same two gaps as RAC**: memory is per-user not per-agent, and there's no general distillation loop. Those two additions, kept in portable Postgres, are what turn this excellent knowledge *platform* into the agent *memory* system you've been describing.

---

*Companion docs: [RAC_REVIEW.md](RAC_REVIEW.md) (RAC review), [../MEMORY_DESIGN.md](../MEMORY_DESIGN.md) (the L0–L3 memory design + portability), [MEMORY_LANDSCAPE.md](MEMORY_LANDSCAPE.md) (tool landscape). This review reads only the three Nexus documents; I did not see implementation code or the full 1,326-line spec beyond the architecture, memory, data, and learning sections.*
