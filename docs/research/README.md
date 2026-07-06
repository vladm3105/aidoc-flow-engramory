# Research & background

> **Historical input.** The landscape/predecessor reviews below informed Engramory's
> design and are kept for rationale. Where they conflict with `docs/ARCHITECTURE.md` or
> an accepted ADR (`sdd/05_ADR/`), the architecture/ADRs govern — e.g. these reviews
> recommend adopting an existing tool (Obsidian vault, Mem0/Cipher) or consolidating on
> Apache AGE, both superseded by the decision to build Engramory on the Postgres spine
> with Neo4j-on-promotion.

- [MEMORY_LANDSCAPE.md](MEMORY_LANDSCAPE.md) — survey of agent-memory approaches and OSS tools (AGENTS.md/CLAUDE.md, Obsidian/MCP, MemPalace, Mem0, Letta, Zep/Graphiti, Cognee, LangMem, Cipher) with licensing and portability notes.
- [RAC_REVIEW.md](RAC_REVIEW.md) — review of the predecessor RAC project.
- [NEXUS_V3_REVIEW.md](NEXUS_V3_REVIEW.md) — review of the predecessor Nexus v3 design.

Current analysis (governs where it informs open design work):

- [MEMORY_CONCEPT_REVIEW.md](MEMORY_CONCEPT_REVIEW.md) — conceptual review of Engramory's agent-memory approach: what is strong, what is under-specified (the "learning half": feedback, confidence dynamics, conflict, forgetting, safety, evaluation), and prioritized fixes.
