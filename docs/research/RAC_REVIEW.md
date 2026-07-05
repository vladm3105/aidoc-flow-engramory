# AI Knowledge RAC — Project Review & Cipher Comparison

*Reviewed June 22, 2026 · based on the documentation in `C:\data\ai-knowledge-rac` (docs/ + rac-system/docs/)*

---

## What your project actually is

RAC ("Retrieval Augmented Context") is an **AI-agent-first knowledge-management platform**, not just a memory store. From the docs, the system is already substantial:

- **Ingestion & retrieval:** multi-format parsers (Markdown, HTML, PDF, templates, plain text) with **section-level** storage, entity/relation extraction, and citations.
- **Storage core:** **Neo4j** (graph: entities, relations, multi-hop reasoning) + **PostgreSQL/pgvector** (vectors, metadata, KV, per-workspace schemas) + **OpenAI** embeddings/generation.
- **Agent interface:** a unified **RAC MCP server (:8020) exposing ~79 tools** across KB CRUD, search/semantic/graph query, user management, workspace ops, storage, credentials. Claude Code is the named client.
- **Multi-tenancy:** multi-workspace isolation, **per-user MCP containers**, trust levels, cost tiers, LLM observability/budget monitoring.
- **Enterprise infra:** multi-cloud storage (GCP primary, AWS/Azure/local), **GCP Secret Manager + CMEK**, Loki/Grafana/Promtail monitoring, A2A agent interface + skill router, Google ADK + CopilotKit UI stack.
- **Lightweight "Workspace Memory":** a per-workspace KV store with types `preference`, `instruction`, `learned`, `custom`.
- **Status (per `TODO.md`):** "fully functional for AI agent use via MCP." Open items are mostly *operational* — REST API wrapper, automated backups, credential rotation, external integrations (Auth0/GitHub OAuth), security hardening, CI/CD, horizontal scaling.

In short: you've built the **hard infrastructure** — KG + vectors + MCP + multi-tenant isolation + security + storage abstraction. That's the expensive 70%.

---

## Is it "similar to Cipher"? Partly — same substrate, different job

You're right that there's real overlap, but they sit at different layers. Here's the honest mapping.

### Where they genuinely overlap
- **MCP as the agent interface** — both expose memory/knowledge to agents over MCP.
- **Same storage trio** — Postgres + Neo4j + vectors (Cipher uses Qdrant/Milvus; you use pgvector).
- **Workspace memory shared across sessions/agents** — both have this concept by name.
- **Goal of a team-shared knowledge brain** for AI coding/work agents.

### Where they differ — and why it matters for your stated goal

| Dimension | Your RAC | Cipher (ByteRover) |
|---|---|---|
| **Core purpose** | Document-centric **knowledge base / RAG platform** the agent *queries* | **Agent-experience memory layer** that *auto-captures* what the agent knows and how it reasoned |
| **How memory is filled** | **Ingest-driven** — you upload/index documents | **Capture-driven** — hooks auto-save knowledge + reasoning from sessions |
| **Memory types** | Semantic (KG+vectors) ✓; "learned" KV ✓ | System 1 = Knowledge (semantic); **System 2 = Reflection (reasoning patterns / skills)**; Workspace memory |
| **Episodic memory** (what happened, when) | Not really — no temporal session memory | Yes (session history, cross-project recall) |
| **Procedural / skill memory** | ✗ (no reasoning-pattern capture) | **Yes — this is its headline feature** |
| **Consolidation / reflection loop** | ✗ | Yes (System 2 reflection) |
| **Breadth of infra** (multi-tenant, multi-cloud, security, observability, domains) | **Far more built-out than Cipher** | Lightweight, focused |
| **Out-of-box agent compatibility** | MCP (any client); tooled around your KB + Claude Code | Ships adapters for Cursor, Claude Code, Codex, Gemini CLI, Kiro, VS Code… |
| **Ownership / license** | **Yours** — full control, no license constraint | Elastic License 2.0 (source-available) |

**The key insight:** RAC is the *library the agent reads from*. Cipher is *the agent's own memory of what it did and learned*. They are complementary, not the same product. And critically — the **"human-like memory" you said is the real goal (knowledge + skills + experience + consolidation)** is exactly the axis where RAC is thin: you have **knowledge** (semantic) covered well, but **episodic**, **procedural/skills**, and the **reflection/consolidation loop** are missing. That gap is precisely Cipher's (and LangMem's) specialty.

---

## So what should you do?

You have a working, heavily-invested platform. Don't scrap it. Decide **per memory layer**, not all-or-nothing:

**Knowledge / documents / semantic search + citations + multi-tenant security**
→ **Keep RAC. It already does this well and is more capable here than Cipher.** This is your differentiator and the part that's genuinely hard to replace.

**Skills + experience + episodic + consolidation (your actual goal)**
→ This is the missing layer. Two viable paths:

- **Path A — Extend RAC yourself.** You already own the primitives (Neo4j for a temporal/episodic graph, pgvector for recall, MCP to expose it). Add: (1) an **episodic event log** (append-only, per project/session, timestamped), (2) a **procedural/skill store** (captured reasoning patterns / "how I solved X" playbooks), (3) a **reflection/consolidation job** that promotes repeated episodes into confirmed knowledge/skills. Pros: full ownership, no new license, integrates with your security/multi-tenancy. Cons: the consolidation loop is the genuinely hard part to get right — it's real R&D, not plumbing.

- **Path B — Bolt on a proven memory engine and keep RAC as the knowledge platform.** Let **Cipher** (turnkey, coding-agent-shaped, but ELv2) or **LangMem** (MIT, explicit semantic/episodic/procedural + consolidation, more glue code) own episodic + procedural + reflection, while RAC remains the curated knowledge/document KB and the multi-tenant/security/storage backbone. Pros: matches your "avoid own development" preference; you skip rebuilding the reflection loop. Cons: another moving part, and a second memory store to reconcile with RAC's.

### My recommendation

Given you explicitly want **open-source/off-the-shelf and to avoid custom development where possible**, and given the missing piece (consolidation/reflection) is exactly the hard part to build well:

**Treat RAC as your knowledge + infrastructure platform, and add the experiential memory layer rather than rebuilding it.** Concretely:

1. **Short term — validate the gap cheaply.** Wire **LangMem** (MIT, cleanest licensing, explicit three memory types + Memory Manager consolidation) as a small memory service behind your existing MCP server. Your agents already speak MCP, so this is mostly adapter work, and it tests the "skills/experience" behavior without committing.
2. **Keep RAC authoritative for knowledge.** Documents, domain knowledge (trading/legal), citations, multi-tenant isolation, security — all stay in RAC. Don't duplicate these into the memory engine.
3. **Only build Path A if integration proves too lossy.** If reconciling two stores becomes the bottleneck, fold the episodic/procedural tables into your own Neo4j+pgvector and port just the *consolidation logic* — by then you'll know exactly what you need, and you avoid the blank-page version of the hard part.

Avoid the trap of concluding "RAC ≈ Cipher, so I've already built it." On substrate, yes; on the **experiential memory you actually want, RAC is not there yet** — and that layer is worth borrowing rather than reinventing.

---

## Smaller observations from the docs

- **Naming drift / inconsistency.** ARCHITECTURE.md shows `Knowledge Graph (:8000)` while RAC_SYSTEM_PLAN.md and the index reference **LightRAG** on :8000, and the TODO notes port 8000 is "occupied by LightRAG." Worth pinning down a single name in the docs — it reads like the KG server was renamed mid-project.
- **Doc index references files that may not exist** (e.g. `OPENBAO_VAULT_ARCHITECTURE.md`, `SECRETS_ROTATION.md`) even though OpenBao was deprecated for GCP Secret Manager (Feb 2026). The index appears stale relative to the secrets migration — a quick `DOCUMENTATION_STATUS` reconciliation pass would help.
- **"Workspace Memory" is the seed of what you want.** The `learned` type is conceptually the start of semantic memory consolidation — but as documented it's a manual `set()/get()` KV, not an automatic capture/consolidation loop. That's the honest distance between today and the human-like-memory goal.
- **Strong production posture, light on the learning loop.** The investment is heavily weighted toward ops/security/multi-cloud (excellent for a product) and light on the agent-cognition side (memory types, reflection). If "agent that keeps its skills across projects" is the headline goal, the next increment should be cognitive, not operational.

---

*This review is based solely on the documentation; I did not read the source under `rac-system/rac/`. If you want, I can review the actual memory/workspace and MCP-tool implementation next to confirm how close the "learned" memory is to a real consolidation loop.*
