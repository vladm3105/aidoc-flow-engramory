# AI Agent Memory — Landscape Review & Recommendation

*Prepared June 18, 2026 · For a small team running Codex, Claude Code, Hermes, and custom/CLI agents across software, knowledge, and business work*

*Scope constraint: open-source or free solutions only. Every option below qualifies — see the Cost & licensing section.*

---

## TL;DR

Treat memory as **two separate layers**, not one product:

1. **Substrate (where memory lives):** a single Git-versioned, Obsidian-compatible Markdown vault. Plain files, no lock-in, every agent and every human can read/write it.
2. **Retrieval (how agents find the right memory):** an MCP server on top of the vault so agents can search instead of loading everything.

For your exact stack, the closest off-the-shelf starting point is **`open-second-brain`** (Hermes-first, with Claude Code + Codex adapters and an MCP server, built on an Obsidian vault). Pair it with **`AGENTS.md` + `CLAUDE.md`** files for per-project instructions. Add a heavier semantic memory engine (**MemPalace** or **Mem0**) only if/when flat search stops scaling.

Avoid jumping straight to a hosted vector/graph memory service — it's more power than a small team needs day one, and it reintroduces the lock-in that a Markdown vault avoids.

---

## How to think about it

"Memory for agents" collapses three different problems people keep conflating:

- **Instructions / conventions** — "how we work, our stack, our rules." Mostly static, lives best as files in the repo.
- **Knowledge / notes** — research, decisions, client context, meeting notes. Your "second brain." Grows continuously, needs to be searchable.
- **Episodic recall** — "what did we decide last Tuesday, and is it still true?" Needs timestamps, validity, and de-duplication.

A small team doesn't need three separate systems. It needs **one shared store that handles the first two well and bolts on the third when the pain shows up.** That framing drives the recommendation.

---

## The four approaches in trend right now

### 1. File-based instruction standards — `AGENTS.md` / `CLAUDE.md`

The de facto baseline for cross-agent instructions. `AGENTS.md` originated at OpenAI, is now stewarded by the Linux Foundation's Agentic AI Foundation, and ships in 28+ tools and 60,000+ repos. Codex and most CLI agents read it natively. Claude Code prefers its own `CLAUDE.md`, and added partial `AGENTS.md` support over spring 2026.

- **Strengths:** zero infrastructure, version-controlled, auditable, diff-able in PRs, no vendor lock-in. Solves ~80% of the *instructions* problem.
- **Limits:** it's static context, not searchable memory. Doesn't scale to thousands of notes or "recall what was true last week."
- **Team trick:** write one `AGENTS.md` and symlink `CLAUDE.md` to it (`ln -s AGENTS.md CLAUDE.md`) so both ecosystems read the same source of truth.

### 2. Obsidian / Markdown vault as a shared "second brain"

Obsidian (1.5M+ users, growing ~22% YoY) is popular as an AI substrate for one reason: it's **just Markdown files on disk**. Any agent — Codex, Claude Code, Hermes, a custom CLI — can read and write them, local models work natively, and nothing is uploaded unless you choose. Agents connect through an **MCP server** that lets them read notes, follow wikilinks, and (in the better servers) do hybrid search rather than dumping the whole vault into context.

- **Strengths:** no lock-in, human-and-agent readable, Git-friendly, doubles as your team's actual knowledge base. Perfect fit for "software + knowledge + business" mixed work.
- **Limits:** you assemble the pieces (vault + MCP server + conventions). Plain semantic search over a big vault can get noisy without a good retrieval layer.

#### How many Markdown files can Obsidian handle?

There is **no built-in cap on the number of `.md` files** in a vault. (The only published "100 GB limit" applies to *Obsidian Sync*, the paid sync add-on — not to local vaults.) Real-world ceilings:

| Vault size | Behavior |
|---|---|
| Up to ~few thousand notes | Everything snappy — editing, search, backlinks, graph all fine. |
| ~10,000–50,000 notes | Still very usable; graph view starts to get heavy; some plugins (e.g. Dataview) slow down. |
| **100,000 notes** (benchmarked: ~93M words, 1.87M links) | Editing, search, and backlinks work "surprisingly well." Caveats: the **one-time full index build took ~2 hours**, and the **graph view becomes effectively useless** at this scale. |
| ~280,000 notes | Reported as still handled by a community stress test, near the practical edge. |

**The key reframe for an agent second brain:** Obsidian's file ceiling is almost never the real constraint, because **your agents never load the whole vault** — they search via the MCP layer and pull a handful of notes into context. So the practical limit is set by **retrieval quality and the LLM context window, not by Obsidian**. Two implications:
- The editor is interchangeable/optional — agents read the Markdown directly. Obsidian is just a convenient human front-end.
- Past ~tens of thousands of notes, invest in the **retrieval layer** (hybrid-search MCP server, or a memory engine like MemPalace/Mem0) and consider **splitting into domain vaults** (e.g. `software/`, `knowledge/`, `business/`) rather than worrying about Obsidian's limits.

Practical takeaway: for a small team, you will not hit Obsidian's scaling wall for years — retrieval design will matter long before file count does.

#### Adapting Obsidian for several developers

Obsidian is local-first and single-user by default — there's no native real-time multiplayer. But the vault is just Markdown in a folder, so making it a team store is a *sync + conventions* problem, and it adapts well. Three models:

| Model | How it works | Best for | Cost |
|---|---|---|---|
| **Git (recommended for devs)** | Vault is a Git repo; commit/push/pull, branches, PR review, full history. Use the **Obsidian Git** plugin for auto-commit/pull, or plain CLI/agents. Map sub-folders to separate repos to keep personal notes private. | A dev team that already lives in Git and wants agent writes reviewed like code | Free |
| **Relay plugin (CRDT multiplayer)** | Real-time co-editing with live cursors; offline edits merge without conflicts via CRDTs; share specific folders. **EVC Team Relay** is a self-hostable, open-source variant. | Humans co-editing notes live, Google-Docs style | Free / paid tiers |
| **Obsidian Sync for teams** | Official shared-vault sync; auto-merges Markdown with diff-match-patch. Max **20 collaborators**; no live cursors. | Non-technical teams wanting zero-setup sync | Paid add-on |

**For your case (small dev team + multiple agents), use Git as the backbone.** It's free, auditable, and turns every agent write into something you can review and roll back. The thing Git *doesn't* solve on its own is **concurrent writes from multiple agents**, so add these conventions:

- **Separate the agent-owned area from human notes** (e.g. `AI Wiki/` for agents, `notes/` for people) so they rarely touch the same files. This is exactly what `open-second-brain` does.
- **Append-only logs:** agents append below a `## Raw events` marker in daily notes instead of rewriting — appends almost never conflict.
- **One file per entity** (per project / person / decision) so two agents writing at once hit different files.
- **Stamp agent identity** in each entry (`@codex`, `@claude`, `@hermes-host1`) for auditability.
- **Commit small and often**, auto-pull on a short cadence; `.gitignore` the `.obsidian/workspace.json` (it changes constantly and is the #1 source of pointless conflicts).
- Optional: tools like **agentic-git-sync** route git/merge failures through an AI agent that resolves conflicts semantically.

**One honest caveat about concurrency at team scale:** Git is *asynchronous* (pull/push), so several agents writing every few seconds across machines is not its sweet spot. If your agents do high-frequency concurrent writes, a **shared memory server handles multi-writer concurrency better than file-sync** — which is the other reason the **Mem0 server (backed by Qdrant/Neo4j)** belongs in the picture. The clean division of labor for a team:

- **Git-synced vault** → curated, human-readable, durable knowledge (decisions, project docs, business context). Reviewed via PR.
- **Mem0 / MemPalace server** → live, high-frequency agent recall shared across all developers and agents over MCP.

That gives every developer and every agent one shared brain, with version control where you want review and a concurrent database where you want speed.

### 3. Cross-tool memory plugins — MemPalace, Memorix, and friends

The 2026 trend is purpose-built memory that spans every agent via MCP. **MemPalace** is the breakout (launched April 2026, 43k+ GitHub stars in a week): a local SQLite-backed "memory palace" with a temporal knowledge graph, ~33 MCP tools, auto-save hooks for Claude Code / Codex / Cursor, structured retrieval (people→wings, topics→rooms), and a benchmark-topping ~96% search accuracy at ~170 tokens of startup overhead. **Memorix** is a lighter cross-agent MCP layer covering Claude Code, Codex, Cursor, Gemini CLI, Copilot, and more.

- **Strengths:** real semantic recall, temporal validity (stale facts get end-dated, not silently kept), works across all your agents through one MCP endpoint, mostly local/offline.
- **Limits:** memory becomes a database, not plain notes — less human-browsable than a vault, and a younger codebase to depend on. Best added *on top of* a vault, not instead of one.

### 4. Dedicated memory frameworks — Mem0, Letta, Zep, Cognee

The "serious infrastructure" tier, aimed more at products than small teams:

- **Mem0** — a memory layer you bolt onto an agent in a few lines; cleanest SDK, strong MCP support, tuned for "recall this user's stable preferences + recent facts" (91.6 on the LoCoMo benchmark).
- **Letta (MemGPT)** — the agent *is* its memory; stateful agents addressed as services. Powerful, heavier, overkill unless memory is your core architecture.
- **Zep** — temporal knowledge graph; best at "what was true at this point in time."
- **Cognee** — a knowledge-graph builder for when your problem is really knowledge management (Neo4j/Kuzu/etc.).

- **Strengths:** production-grade, scalable, benchmarked.
- **Limits:** more setup, more lock-in, often hosted/API-centric — reintroduces exactly the vendor coupling a Markdown vault avoids. Right answer later, wrong answer for day one.

---

## Side-by-side

| Approach | Cross-agent (Codex/Claude/Hermes/custom) | Setup effort | Lock-in | Searchable recall | Human-browsable | Best for |
|---|---|---|---|---|---|---|
| `AGENTS.md` / `CLAUDE.md` | High (native) | Minimal | None | No | Yes | Per-project rules & conventions |
| Obsidian vault + MCP | High (via MCP) | Low–Medium | None | Medium (hybrid search) | Yes | Shared team knowledge base |
| MemPalace / Memorix | High (one MCP endpoint) | Medium | Low | High (graph + temporal) | Low | Semantic recall across sessions |
| Mem0 / Letta / Zep / Cognee | Medium (SDK/REST, some MCP) | High | Medium–High | High | Low | Productized / large-scale memory |

---

## Cost & licensing (open-source / free check)

Everything recommended here is free to run and self-hostable. The only thing to watch is that "free to use" and "open source" aren't the same — Obsidian is the one exception.

| Tool | License | Cost to run | Notes |
|---|---|---|---|
| `AGENTS.md` / `CLAUDE.md` | Open standard (no code) | Free | Just files in your repo. |
| **open-second-brain** | MIT (open source) | Free | Self-hosted, local-first. |
| **MemPalace** | MIT (open source) | Free | Runs locally, zero API cost. |
| **Memorix** | Open source | Free | Cross-agent MCP layer. |
| Mem0 | Apache-2.0 core (open source) | Free self-hosted | Hosted cloud is paid ($19–249/mo) — skip it, self-host. |
| Letta (MemGPT) | Apache-2.0 (open source) | Free self-hosted | Optional paid cloud. |
| Zep | Community edition open source | Free self-hosted | Cloud tiers paid; use the OSS edition. |
| Cognee | Open source | Free self-hosted | Bring-your-own graph/vector backend. |
| **Obsidian (editor)** | ⚠️ Proprietary *freeware* | Free (incl. teams/commercial) | Free to use even commercially since 2025, but **not** open source. Sync/Publish are optional paid add-ons you don't need. |

**If you want a 100% open-source editor instead of Obsidian:** the vault is plain Markdown, so the editor is interchangeable. Swap in **Logseq** (AGPL) or **Foam** (MIT, runs inside VS Code) and every agent still reads the same files. For Mem0/Letta/Zep, just choose the self-hosted open-source edition and ignore the hosted tiers — those are the only paid paths.

---

## Recommendation for your team

**Adopt a layered, file-first setup. Add complexity only when you feel the pain.**

**Layer 1 — Instructions (do this week).**
Put an `AGENTS.md` at the root of each project for stack, conventions, and rules. Symlink `CLAUDE.md → AGENTS.md` so Claude Code and Codex share one source. Version it in Git. This alone removes most "the agent doesn't know how we work" friction.

**Layer 2 — Shared knowledge vault (do this month).**
Stand up one Obsidian-compatible Markdown vault as the team's second brain — research, decisions, client/business context, meeting notes. Keep it in Git (or a synced folder). Because it's plain Markdown, Hermes, Codex, Claude Code, and your custom agents all read and write it directly.

The fastest concrete starting point given your stack is **`open-second-brain`**: it's Hermes-first, ships Claude Code and Codex adapter manifests plus an optional MCP server, bootstraps an `AI Wiki/` area and append-only daily logs inside the vault, and never touches your hand-written notes. Be aware it's an early/niche project (low star count, fast-moving) — treat it as a strong reference architecture you can adopt or fork, not a finished product. If you'd rather build it yourself, the pattern is simple: vault + a maintained Obsidian MCP server + a conventions file.

**Layer 3 — Semantic recall (add when search gets noisy).**
Once the vault is large enough that "just search the Markdown" returns junk, put **MemPalace** (or Memorix) on top via MCP for graph-based, temporally-aware recall across all agents. It complements the vault rather than replacing it.

**Defer:** Mem0/Letta/Zep/Cognee until you're building a product feature around memory or outgrow local tooling. They're excellent but they're the heavyweight tier — and the hosted ones give back the lock-in you avoided.

### Why this fits you specifically
- **Many agents (Codex, Claude Code, Hermes, custom):** plain Markdown + MCP is the one substrate every one of them speaks. No agent is privileged; no agent is locked out.
- **Small team:** files in Git give you sharing, history, and review with tools you already use — no new SaaS, no per-seat cost.
- **Software + knowledge + business mix:** `AGENTS.md` covers the code side, the vault covers notes/business context, and they live side by side.

---

## Long-term, human-like memory: knowledge · skills · experience across projects

This is the real goal: an agent that *keeps what it learns* — facts, how-to skills, and lived experience — and carries it from one project to the next, the way a person does. In memory terms that's three distinct stores, plus a process that turns repeated experience into durable knowledge:

| Human concept | Memory type | What it stores | What does it well (OSS) |
|---|---|---|---|
| **Knowledge** | Semantic | Facts, codebase truths, business rules, definitions | Mem0, Cognee, Cipher (System 1), LangMem |
| **Experience** | Episodic | What happened, when, in which project; past sessions | Zep/Graphiti, LangMem, Cipher |
| **Skills** | Procedural | Reusable how-to: reasoning patterns, solved-problem playbooks, policies | LangMem (procedural), Cipher (System 2 reflection) |
| **"Sleep" / learning** | Consolidation | Promoting repeated experience into confirmed knowledge/skills | LangMem Memory Manager, Cipher reflection, open-second-brain "dream passes" |

**Important expectation-setting:** none of today's off-the-shelf systems make the *model* learn (no weight updates). "Skill" and "experience" memory means the agent **stores what worked as text and re-injects it** next time — playbooks, reasoning traces, corrected preferences. That's the realistic, and genuinely useful, version of human-like memory in 2026. True parametric skill acquisition is still research (e.g. ProcMEM, "Remember Me, Refine Me"), not a product you can drop in.

### The two best off-the-shelf fits for you

**1. Cipher (ByteRover) — purpose-built for exactly this.** It's an open-source memory layer designed specifically for *coding agents across projects, tools, and teams*. Its **dual memory** maps almost one-to-one onto your ask: **System 1 (Knowledge Memory)** holds codebase knowledge and business logic; **System 2 (Reflection Memory)** captures the agent's reasoning steps and problem-solving patterns — i.e. its skills and experience. **Workspace Memory** shares that across IDEs, sessions, and teammates. It's **MCP-native**, so it plugs into Claude Code, Codex (via its TOML MCP config), Gemini CLI, Cursor, and any other MCP client with near-zero code — which is the "avoid building my own" outcome you want. Self-hosts on the exact stack you already named: **PostgreSQL + Neo4j (knowledge graph) + Qdrant/Milvus (vectors)**.
- **License caveat (be aware):** Cipher is **Elastic License 2.0** — *source-available*, not OSI-"open source." In practice that's free to self-host, modify, and use internally; the only real restriction is you can't resell it as a managed service. For an internal team tool, that's a non-issue.
- **Fit:** ★★★★★ for a coding team that wants turnkey cross-project memory with no custom RAG.

**2. LangMem (LangChain) — the most "textbook" human memory model, fully OSI open source.** MIT-licensed SDK that explicitly exposes **semantic, episodic, and procedural** memory through one API, with a **Memory Manager that decides what to store/update/delete and consolidates knowledge over time** — the closest thing to a built-in "learning/consolidation" loop. Backends are flexible: **Postgres/pgvector**, vector DBs, key-value.
- **Trade-off:** it's an SDK you wire into agent code, and it's most seamless inside the **LangGraph** ecosystem. Your Codex/Claude Code/Hermes aren't LangGraph, so you'd run LangMem as a **memory service/MCP endpoint they call** — more integration effort than Cipher, but maximally clean licensing and the richest explicit memory-type model.
- **Fit:** ★★★★☆ if you want pure MIT + the canonical three-type model and don't mind a little glue code.

**3. Letta (MemGPT) — if one flagship agent should *be* its memory.** Apache-2.0, human-inspired tiered memory (core/recall/archival) with self-editing and self-improvement over time. But it's a *runtime your agent runs inside*, so it fits a single greenfield agent you build, not a shared brain for Codex/Claude Code you don't control. Keep it as an option for one custom agent, not the team layer.

### Recommendation for "agent that keeps its knowledge across projects"

**Start with Cipher, self-hosted (Postgres + Neo4j + Qdrant), wired into every agent over MCP.** It is the single closest off-the-shelf answer to your sentence — knowledge + skills + experience, cross-project, cross-tool, team-shareable, no custom development — and it self-hosts on the stack you already had in mind. Accept the Elastic-License-2.0 "source-available" status; for internal use it behaves like open source.

Choose **LangMem instead** if OSI-pure licensing or the explicit semantic/episodic/procedural model matters more to you than turnkey setup, and you're willing to run it as a small memory service your agents call.

Either way, this engine is your **long-term memory brain (the "Layer 3" framework)**; it complements — doesn't replace — the Git-synced Markdown vault, which stays the human-readable, reviewable record. Agents read/write durable knowledge to the memory engine over MCP; humans curate the vault.

---

## Framework layer deep-dive: Mem0 vs Letta vs Zep vs Cognee

The file vault + MCP layer above is *no-code* memory — great for the off-the-shelf agents (Codex, Claude Code) and shared team notes. But for your **custom agents** you want a real **memory framework you call from code**. That's what these four are. All four have a free, self-hostable open-source core; they differ in *what they are*.

**The single most important distinction for you:** is it a **layer you bolt onto your existing agents**, or a **runtime your agents run inside**?

- Your agents are heterogeneous — Codex and Claude Code are their own runtimes, Hermes is its own runtime, plus custom ones. A memory **layer** (call `add()`/`search()` from any of them, or reach it over MCP) fits this. A memory **runtime** means rebuilding agents inside it — invasive, and impossible for Codex/Claude Code which you don't control.

### Mem0 — memory *layer* (the pragmatic pick)
A library you drop into any agent: `memory.add()` / `memory.search()`, two calls. Apache-2.0, ~53k stars, v2.0 (April 2026). Python **and** Node SDK, or run it as a self-hosted server with a dashboard and per-agent isolation. Goes fully offline — swap OpenAI for **Ollama**, back it with **Qdrant** (vectors) + optionally **Neo4j / Apache AGE** (graph). Community MCP servers already wire self-hosted Mem0 into Claude Code and Codex. Tuned for "recall this user's/project's stable facts + recent context" (91.6 LoCoMo).
- **Fit:** ★★★★★ — speaks to every agent you run, smallest integration cost, free self-hosted, mixed-content friendly.

### Letta (MemGPT) — memory *runtime* (powerful, but a commitment)
The agent *is* its memory: an "LLM-as-OS" runtime managing three tiers (core in-context / recall / archival) and the whole agent loop. Open source, `pip install letta` or `docker run letta/letta`, SDKs in Python/TS/Go, MCP-native. Ships **Letta Code**, a memory-first coding agent that tops the OSS Terminal-Bench leaderboard.
- **Fit:** ★★★☆☆ — excellent *if you build a brand-new flagship custom agent from scratch* and want memory to be the architecture. Wrong tool for adding memory to Codex/Claude Code, which you can't move inside Letta. Consider it for *one* greenfield agent, not as the shared layer.

### Zep / Graphiti — temporal *knowledge graph* (add-on for "what was true when")
Graphiti is the open-source engine (Apache-2.0, Neo4j / FalkorDB / Kuzu backend); it excels at multi-hop temporal queries — "what changed between Q2 and Q3," facts with validity windows so retracted info doesn't resurface. **Caveat: the self-hostable Zep Community Edition was deprecated** — to self-host you now run **Graphiti directly** and manage the graph DB yourself (Zep proper is becoming cloud-centric).
- **Fit:** ★★★☆☆ — reach for it specifically when *time-varying business/client facts* become a real problem. It's a complement to Mem0, not a replacement, and the CE deprecation means a bit more DIY.

### Cognee — *knowledge pipeline* (add-on for reasoning over your docs)
An ETL/"cognify" pipeline that ingests PDFs, Slack, Notion, images, audio and builds a hybrid graph+vector store (Neo4j / Kuzu / NetworkX). Best when the problem is *knowledge management over an existing corpus* rather than chat recall.
- **Fit:** ★★★☆☆ — relevant to your knowledge + business notes if you want agents to reason over a big document pile. Overlaps with what the Markdown vault already gives you, so only add it if vault search proves too shallow.

### Framework comparison

| | Mem0 | Letta | Zep / Graphiti | Cognee |
|---|---|---|---|---|
| What it is | Memory **layer** | Agent **runtime** | Temporal **KG** engine | Knowledge **pipeline** |
| License | Apache-2.0 | Apache-2.0 (OSS) | Graphiti Apache-2.0 (Zep CE deprecated) | Open source |
| Self-host free | ✅ library or server | ✅ pip / docker | ✅ run Graphiti + own graph DB | ✅ own graph/vector backend |
| SDKs | Python, Node | Python, TS, Go | Python | Python |
| MCP | Community servers (Claude Code/Codex) | MCP-native | Via wrappers | Early wrappers |
| Fully local (Ollama) | ✅ | ✅ | ✅ (LLM-agnostic) | ✅ |
| Integration cost | Lowest | High (rebuild in runtime) | Medium | Medium–High |
| Best at | Stable facts + recent recall | Stateful self-managing agents | "What was true when" | Reasoning over a doc corpus |
| Fit for your stack | ★★★★★ | ★★★☆☆ | ★★★☆☆ | ★★★☆☆ |

### Framework recommendation

**Default to Mem0, self-hosted, as the memory framework for your custom and CLI agents.** It's the only one of the four whose model — a thin layer any agent calls — matches a multi-runtime, small-team setup, and it's free and fully local (Qdrant + Ollama, add Neo4j when you want graph relations). It also slots cleanly under the two-layer plan: the Markdown vault stays your human-readable source of truth; Mem0 becomes the programmatic recall engine your code talks to, exposed to Codex/Claude Code via a community MCP server.

Then add **one** specialist later, only if a concrete need appears:
- time-varying facts your agents keep getting wrong → bolt on **Graphiti**;
- agents need to reason across a large document corpus → bolt on **Cognee**.

Keep **Letta** in your back pocket for a *single greenfield flagship agent* where you want memory-as-architecture — not as the shared layer across tools you don't own.

**Minimal Mem0 stack to stand up:** Mem0 server (Docker) + Qdrant + Ollama for fully-offline embeddings/LLM, optionally Neo4j for graph memory, plus a community `mem0-mcp-selfhosted` server so Claude Code and Codex share the same store. Everything Apache-2.0 / free.

---

## Suggested starter structure

```
your-knowledge-vault/                 # Obsidian-compatible, in Git
├── AI Wiki/                          # agent-owned, machine-maintained
│   ├── _INDEX.md                     # generated page index
│   ├── decisions/                    # ADRs, "why we chose X"
│   ├── projects/                     # per-project context & status
│   ├── people/                       # clients, team, stakeholders
│   └── daily/                        # append-only agent event logs
├── notes/                            # your hand-written human notes
└── .mcp.json                         # MCP server config for retrieval

per-project-repo/
├── AGENTS.md                         # stack, conventions, rules (source of truth)
└── CLAUDE.md -> AGENTS.md            # symlink so Claude Code reads the same file
```

**First three moves:** (1) add `AGENTS.md` + the `CLAUDE.md` symlink to your main repo; (2) create the vault and wire one MCP server so agents can search it; (3) write a short "memory protocol" note telling agents to *search before answering* and *log decisions after each session*.

---

## Sources

- [CLAUDE.md vs AGENTS.md in 2026 — which one does Claude Code actually read?](https://bestagent.dev/claude-md-vs-agents-md-2026/)
- [Agent Instruction Files: AGENTS.md, CLAUDE.md, and Cross-Tool Portability with Codex CLI](https://codex.danielvaughan.com/2026/05/27/agent-instruction-files-agents-md-claude-md-cross-tool-portability-codex-cli/)
- [Cross-Tool Agent Memory: MemPalace, Built-In Memory, and the Portability Problem](https://codex.danielvaughan.com/2026/04/17/cross-tool-agent-memory-mempalace-portability/)
- [AGENTS.md vs CLAUDE.md: The AI Developer's Guide to Context Standards](https://hivetrail.com/blog/agents-md-vs-claude-md-cross-tool-standard)
- [Obsidian AI Second Brain: Complete Guide (2026) — NxCode](https://www.nxcode.io/resources/news/obsidian-ai-second-brain-complete-guide-2026)
- [How AI Agents Use Your Obsidian Vault in 2026 (MCP + Markdown)](https://www.savemarkdown.co/blog/obsidian-ai-agent-mcp-markdown-workflow/)
- [open-second-brain — local-first memory for Hermes in an Obsidian vault (GitHub)](https://github.com/itechmeat/open-second-brain)
- [MemPalace — best-benchmarked open-source AI memory system (GitHub)](https://github.com/mempalace/mempalace)
- [Memorix — cross-agent memory layer via MCP (GitHub)](https://github.com/AVIDS2/memorix)
- [Best AI Agent Memory 2026: Mem0 vs Letta vs Zep vs Cognee](https://mcp.directory/blog/mem0-vs-letta-vs-zep-vs-cognee-2026)
- [State of AI Agent Memory 2026: Benchmarks, Architectures & Production Gaps](https://mem0.ai/blog/state-of-ai-agent-memory-2026)
- [The Agent Memory Race of 2026: 5 Repos, 4 Architectures, 1 Unsolved Problem](https://ossinsight.io/blog/agent-memory-race-2026)
- [Obsidian commercial license (free for teams as of 2025)](https://obsidian.md/help/teams/license)
- [MemPalace LICENSE (MIT)](https://github.com/MemPalace/mempalace/blob/main/LICENSE)
- [Mem0 open source overview & self-hosting](https://docs.mem0.ai/open-source/overview)
- [Self-hosted Mem0 MCP server for Claude Code (Qdrant + Neo4j + Ollama)](https://github.com/elvismdev/mem0-mcp-selfhosted)
- [Letta — platform for stateful agents (GitHub)](https://github.com/letta-ai/letta)
- [Mem0 vs Letta (MemGPT): AI Agent Memory Compared (2026)](https://vectorize.io/articles/mem0-vs-letta)
- [Graphiti — real-time knowledge graphs for AI agents (GitHub)](https://github.com/getzep/graphiti)
- [Zep (Graphiti) vs Cognee: AI Agent Memory Compared (2026)](https://vectorize.io/articles/zep-vs-cognee)
- [Interlude: Obsidian vs. 100,000 — large-vault benchmark (Gödel's)](https://www.goedel.io/p/interlude-obsidian-vs-100000)
- [Terabyte size, million notes vaults? How scalable is Obsidian? (Obsidian Forum)](https://forum.obsidian.md/t/terabyte-size-million-notes-vaults-how-scalable-is-obsidian/66674)
- [Obsidian plans and storage limits (Sync 100 GB cap)](https://obsidian.md/help/Plans+and+storage+limits)
- [Relay — team collaboration / multiplayer for Obsidian (CRDT)](https://relay.md/)
- [Obsidian Help — syncing & collaboration for teams (20-collaborator limit)](https://obsidian.md/help/teams/sync)
- [agentic-git-sync — AI-resolved two-way Git sync for Obsidian vaults](https://github.com/leweii/agentic-git-sync)
- [Sync Obsidian Vault with Git for AI Collaboration (BSWEN, 2026)](https://docs.bswen.com/blog/2026-03-23-sync-obsidian-vault-git-ai-collaboration/)
- [Cipher (ByteRover) — open-source memory layer for coding agents (GitHub)](https://github.com/campfirein/cipher)
- [Cipher overview — System 1 / System 2 / Workspace memory (Byterover docs)](https://docs.byterover.dev/cipher/overview)
- [LangMem SDK for agent long-term memory (DigitalOcean tutorial)](https://www.digitalocean.com/community/tutorials/langmem-sdk-agent-long-term-memory)
- [LangMem docs (LangChain)](https://langchain-ai.github.io/langmem/)
- [Types of AI Agent Memory: Episodic, Semantic, Procedural (Atlan)](https://atlan.com/know/types-of-ai-agent-memory/)
- [Remember Me, Refine Me: Dynamic Procedural Memory for Agent Evolution (arXiv)](https://arxiv.org/pdf/2512.10696)
