# Engramory in the UCX / aidoc-flow ecosystem — and replacing `ucx_kb`

*June 22, 2026 · How Engramory fits the aidoc-flow-framework (UCX) ecosystem and what it must add to fully replace `ucx_kb`.*

---

## Ecosystem map

Engramory is the **shared memory + knowledge plane** for a four-plane multi-agent ecosystem:

| Plane | Repo / component | Role |
|---|---|---|
| **Control plane** | `aidoc-flow-framework` (UCX) | AI-First SDD: BRD→PRD→EARS→BDD→ADR→SPEC→TDD→IPLAN. Lifecycle gates and consistency checks are source of truth. |
| **Supervisory plane** | `aidoc-flow-operations` (Operations Assistant) | Meta-orchestrator, Virtual CTO, and governance reviewer. Fleet-wide scope (`domain`, `space`); inspects real-time `audit_records` and supervises executor agents. |
| **Execution plane** | `iplan-runner` (Executors) | Turns an approved IPLAN into auditable execution in project worktrees (e.g. `b-local-privy`): **Ledger → Gate → Monitor**. Isolated `agent` + `project` scope. |
| **System-of-record** | `iplanic` | Online lifecycle manager iplan-runner syncs to: dispatch, immutable versioning, completion gate, audit-grade evidence. |
| **Memory + knowledge plane** | **Engramory** (this repo) | Shared governed KB (`kb_sections`) + per-agent distilled memory (L1–L3) + audit sink (`audit_records`). **Replaces `ucx_kb`.** Serves both supervisors and executors. |

Engramory is the common memory and specification backbone the other planes read from and write to:

- **Project Executors** (coding agents in repo worktrees) access their project-scoped documentation and record episodic decisions.
- **Operations Assistant** (the global supervisor) inspects fleet-wide audit streams, validates cross-project architectural conformance, and directs work across repositories.

---

## What `ucx_kb` does today (the bar Engramory must clear)

From the framework, `ucx_kb` is the **Knowledge Base package (RAG + Graph)** with a governed policy:

1. **KB representation of every SDD artifact** — BRD/PRD/EARS/BDD/ADR/SPEC/TDD/IPLAN plus decisions are represented in the KB (RAG vectors + knowledge graph).
2. **Canonical entry structure** — `KB_ENTRY_TEMPLATE.md` defines the shape of a KB entry; `KB_GENERAL_RULES.md` defines mandatory coverage rules (which artifacts must be represented, and how).
3. **Retrieval enrichment** — the `ucx-kb-context` Hermes skill pulls relevant KB context *before* create / review / remediate.
4. **Governed maintenance** — the `ucx-kb-maintenance` Hermes skill updates the KB only through a governed workflow, *after* approved IPLAN evidence.
5. **Authority model** — KB **augments** decisions; the **UCX MCP lifecycle gates remain the source of truth**. The KB is advisory, never authoritative for SDD artifacts.
6. **Traceability** — KB entries align to UCX ID-naming and cumulative `@`-tag lineage (BRD→…→IPLAN, `@depends: BRD-01`).

> Note: the points above are drawn from the framework README's description of `ucx_kb` and the KB policy baseline. The exact `KB_ENTRY_TEMPLATE.md` and `KB_GENERAL_RULES.md` schemas are read from the `aidoc-flow-framework` repo (canonical templates under `framework/layers/NN_*/`) when finalizing artifacts.

---

## Gap analysis — what Engramory must ADD to replace `ucx_kb`

Engramory's current design already covers the *substrate* (RAG + graph, Postgres spine, MCP) and *exceeds* `ucx_kb` with L1–L3 per-agent distilled memory. To be a drop-in replacement, it must add the **SDD-specific KB duties**:

| # | Missing in Engramory | What to add | Maps to |
|---|---|---|---|
| **G1** | **SDD artifact types as first-class KB entries** | Model BRD/PRD/EARS/BDD/ADR/SPEC/TDD/IPLAN + decisions as typed L0 entries using `ucx_kb`'s canonical entry schema (port `KB_ENTRY_TEMPLATE`). | L0 knowledge base |
| **G2** | **KB coverage rules / enforcement** | Implement `KB_GENERAL_RULES` mandatory-coverage checks: every approved artifact must have a KB entry; flag gaps. | Governance / validation |
| **G3** | **UCX traceability graph** | First-class graph schema for cumulative `@`-tag lineage (artifact nodes; layer edges BRD→…→IPLAN; `@depends` edges) to answer lineage/impact queries. | GraphPort |
| **G4** | **`ucx-kb-context` parity** | An MCP tool/skill providing pre-create/review/remediate retrieval enrichment with the same contract Hermes expects. | MCP gateway |
| **G5** | **`ucx-kb-maintenance` parity (governed writes)** | A governed write workflow: KB/memory updates for SDD artifacts occur **only on approved IPLAN evidence**, with provenance. | Governance + MemoryPort |
| **G6** | **Authority alignment** | Enforce "lifecycle gates = source of truth; KB augments." Engramory is canonical for *agent memory*, advisory for *SDD artifacts*. | Policy |
| **G7** | **UCX ID-naming conformance** | Adopt `ID_NAMING_STANDARDS` for entry IDs so KB entries interoperate with the SDD layer registry. | Schema |
| **G8** | **Hermes skill drop-in** | Ship replacements for `ucx-kb-context` and `ucx-kb-maintenance` (same skill names/interfaces or adapters) so Hermes keeps working unchanged. | Integration |
| **G9** | **Migration: `ucx_kb` → Engramory** | Ingest existing `ucx_kb` entries (vectors + graph) into Engramory's schema, preserving IDs and traceability; cut Hermes skills over; pass `ucx_kb`'s coverage rules as conformance. | Migration |

What Engramory keeps from its own design and `ucx_kb` does **not** have: **L1–L3 per-agent distilled memory + the distillation loop** (lessons/skills/experience across projects). Replacing `ucx_kb` therefore means *KB duties (G1–G9) + the memory superset we already designed*.

---

## Consequence for the BRD

The Engramory BRD (Full depth — BRD-01 is authored at full/8-layer depth per the roadmap and BRD-00) scopes, at minimum:

- **Replace `ucx_kb`** as a first-class goal, with G1–G9 as requirements and a migration/cutover plan.
- **Authority boundary**: Engramory is advisory for SDD artifacts (gates win), canonical for agent memory.
- **Hermes skill compatibility**: `ucx-kb-context` and `ucx-kb-maintenance` must keep working.
- **Conformance**: Engramory must satisfy `ucx_kb`'s KB coverage rules and UCX ID-naming/traceability standards.
- Plus the experiential memory goals (L1–L3 + distillation) already designed.

---

## Conforming to the framework specs

BRD-01 is authored (Full depth). Conforming artifacts (correct template fields, ID format, traceability tags, KB entry schema) are produced against the `aidoc-flow-framework` repo connected as a local folder, reading:

- `framework/layers/01_BRD/BRD-TEMPLATE.yaml` (plus the layer registry, `ID_NAMING_STANDARDS.md`, and the generated `TRACEABILITY.md`)
- the `ucx-kb-maintenance` skill's `KB_ENTRY_TEMPLATE.md` and `KB_GENERAL_RULES.md`
- the `ucx_kb/` package structure

These lock G1–G9 to the actual KB schema. See [HOW_TO_USE_THE_FRAMEWORK.md](HOW_TO_USE_THE_FRAMEWORK.md) for the canonical template locations (`framework/layers/NN_*/`, not `ucx_flow_v3/`).
