---
name: engramory-memory
description: >-
  Use Engramory as persistent episodic, semantic, procedural, and knowledge-graph memory across Antigravity (agy) sessions and projects. Use at session start to recall prior decisions, constraints, and lessons (memory_search), when a durable fact is learned (memory_add -> distill -> memory_promote), after acting on a retrieved hit (memory_feedback), and when a memory proves wrong (memory_forget). Never store transcripts, logs, secrets, credentials, or personal data.
---

# Engramory Memory (`platform-engramory` + `aiops-harness` in `agy`)

Engramory is the workspace's governed 4-layer memory plane (`L0` Knowledge Base, `L1` Working Memory, `L2` Distilled Episodic/Semantic/Procedural Memory, `L3` Agent Identity & Knowledge Graph). In Antigravity (`agy`) sessions, the `aiops-harness` plugin provides three integrated access surfaces:

1. **Automatic `SessionStart` Cognitive Hydration (`aiops-harness session-start`):**
   - At the start of every `agy` conversation, the `SessionStart` lifecycle hook (`antigravity-plugin/hooks.json`) runs `aiops-harness session-start`, which queries scoped memories for the active workspace, screens every retrieved field via `_inspect_text_strict` (`MemoryPoisoningError`), and injects a `[I-10]` CaMeL nonce-fenced `systemMessage` (`---START-DATA-BLOCK-<nonce>--- ... ---END-DATA-BLOCK-<nonce>---`).
2. **Native `engramory` MCP Server Tools (33 Tools via `antigravity-plugin/mcp_config.json`: 23 Core + 7 `docs_*` + 3 `sdd_*`):**
   - **Memory Lifecycle (7):** `memory_search`, `memory_add`, `memory_promote`, `memory_feedback`, `memory_forget`, `memory_ingest_event`, `memory_learning_health`
   - **Agent Profiles, Roster & Brain Passports (7):** `agent_profile_get`, `agent_profile_upsert`, `agent_roster_list`, `agent_brain_export`, `agent_brain_import`, `agent_brain_compare`, `agent_space_offboard`
   - **Knowledge Base, Graph & SDD Context (9):** `knowledge_ingest`, `knowledge_search`, `knowledge_ingest_media`, `blob_head`, `graph_neighbors`, `sdd_context_slice`, `sdd_blast_radius`, `sdd_ingest`, `docs_ingest_runbook`
   - **Docs & SDD Graph Validation Extensions (10):** `docs_slice_runbook`, `docs_validate`, `docs_check_drift`, `docs_export_llms`, `docs_smoke_run`, `docs_tools_inventory`, `docs_build_portal`, `sdd_validate_graph`, `sdd_check_invariants`, `sdd_lint_policy`
3. **`aiops-harness` Virtual Employee & Fleet MCP Tools (`aiops_ve_*`):**
   - `aiops_ve_load_templates`, `aiops_ve_spawn`, `aiops_ve_provision_space`, `aiops_ve_register_fleet`, `aiops_ve_roster`, `aiops_ve_hydrate`, `aiops_ve_export_brain`, `aiops_ve_import_brain`, `aiops_ve_offboard`

## 1. When to Recall (`memory_search` / `knowledge_search`)

- **At task selection or architectural investigation:** Call the `memory_search` MCP tool (or `aiops_ve_hydrate`) with a specific `query` (`scope: "project"` or `"agent"`, `k: 5`, `token_budget: 2000`) before repeating prior analysis.
- **CLI Fallback:** When invoking the CLI directly in a terminal:

  ```bash
  ENGRAMORY_PROFILE=dev uv run --project ${ENGRAMORY_ROOT:-../platform-engramory} engramory --json memory search --query "<topic>"
  ```

  If `.engramory/config.toml` has not yet been created in the workspace root, bootstrap it with `0600` permissions via:

  ```bash
  uv run aiops-harness session-start --bootstrap-config < /dev/null
  ```

## 2. When to Remember (`memory_add` -> `distill` -> `memory_promote`)

Record durable, reusable facts with provenance:

| Situation | Recommended `kind` / `scope` |
| :--- | :--- |
| An architectural or design decision was ratified (and why) | `decision` (`scope: "project"`) |
| A non-obvious bug root cause and fix were verified | `error` (`scope: "project"`) |
| A benchmark, evaluation, or rollout experiment concluded | `outcome` (`scope: "project"`) |
| A reusable workflow constraint or lesson was learned | `note` (`scope: "agent"` or `"project"`) |

- **Two-Stage L1 -> L2 Lifecycle (`memory_add` -> `distill` -> `memory_promote`):**
  1. **Record L1 Episode (`memory_add`):** Call `memory_add` with `content`, `scope` (`"project"` or `"agent"`), and `domain_id`. Note that `memory_add` writes to the `L1` `episodes` table and returns an `episode_id`.
  2. **Distill L1 -> L2 (`engramory memory distill`):** Run `engramory memory distill` to distill pending `L1` episodes into `L2` `memory_items` (each assigned a `memory_id`).
  3. **Promote Across Scopes (`memory_promote`):** Once an item exists in `L2` (`memory_items`), call `memory_promote` with its `L2` `memory_id` and `target_scope` (`agent -> project -> domain -> space`) to widen its visibility.
- **CLI:**

  ```bash
  ENGRAMORY_PROFILE=dev uv run --project ${ENGRAMORY_ROOT:-../platform-engramory} engramory memory add --content "<distilled fact>" --kind <decision|error|outcome|note> --scope project
  ENGRAMORY_PROFILE=dev uv run --project ${ENGRAMORY_ROOT:-../platform-engramory} engramory memory distill
  ```

- **Never store:** raw conversation transcripts, unredacted logs, secrets, tokens, passwords, connection strings with credentials, or personal data (PII).

## 3. Always Close the Feedback Loop (`memory_feedback` & `memory_forget`)

- After acting on a retrieved memory hit (`retrieval_id`), record whether it helped:
  - **MCP:** Call `memory_feedback` with `retrieval_id` and `outcome: "useful" | "not_useful" | "harmful"`.
  - **CLI:** `ENGRAMORY_PROFILE=dev uv run --project ${ENGRAMORY_ROOT:-../platform-engramory} engramory memory feedback --retrieval-id <id> --outcome useful`
- When a memory is superseded or wrong, retire it with an explicit reason and evidence reference:
  - **MCP:** Call `memory_forget` with `memory_id` and `reason`.
  - **CLI:** `ENGRAMORY_PROFILE=dev uv run --project ${ENGRAMORY_ROOT:-../platform-engramory} engramory memory forget --memory-id <id> --reason "<why superseded>"`

## 4. Paperclip <-> Engramory CHG/IPLAN Flywheel

- When closing a Paperclip task executed from a CHG + IPLAN (`aiops_chg_sync_paperclip`), distill the Paperclip execution notices (`dev_notice`, `ci_status`, `review_verdict`), artifacts, and modified docs directly into Engramory via `aiops_task_distill_engramory` (MCP) or `uv run aiops-harness paperclip distill <task_id> --git-sha <sha> --json` (CLI).
- Note that Engramory L0 `knowledge_search` excludes `docs/audits/` (`kind: audit`) so unverified review prose never pollutes runbook/spec retrieval; audit findings are queried cleanly from `platform-paperclip`.
