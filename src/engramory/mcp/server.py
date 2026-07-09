"""MCP gateway — the single tool surface Engramory exposes to agents.

Tools (authoritative registry: sdd/06_SPEC/SPEC-01 Access Surface):
    memory_search     — scoped retrieval of knowledge + distilled memory (token-budgeted)
    memory_add        — append an episode in the caller's scope (idempotent by content hash)
    memory_feedback   — record a retrieval outcome (useful | not_useful | harmful)
    memory_forget     — retire a memory (end-date + supersede; soft, audited)
    agent_profile_get — load the caller's L3 identity profile
    knowledge_ingest  — create/update an SDD-artifact KB entry (governed write; needs evidence)
    authorize         — per-call scope authorization (default deny)

Any MCP client (Claude Code, Codex, Hermes, custom) connects here. Every call is
authorized and scoped by agent/project/tenant, audited, and fails closed on uncertainty.

Status: stub — the gateway is not yet implemented (Phase 0). SPEC-01 is the source of
truth for tool names and signatures; keep this docstring in sync with it.
"""
