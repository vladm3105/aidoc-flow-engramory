# Engramory in the UCX / aidoc-flow ecosystem — and replacing `ucx_kb`

*June 22, 2026 · How Engramory fits the aidoc-flow-framework (UCX) ecosystem and what it must add to fully replace `ucx_kb`.*

---

## Ecosystem map

Engramory is the **shared memory + knowledge plane** for a three-plane ecosystem:

| Plane | Repo / component | Role |
|---|---|---|
| **Control plane** | `aidoc-flow-framework` (UCX) | AI-First SDD: BRD→PRD→EARS→BDD→ADR→SPEC→TDD→IPLAN. Hermes orchestrates; UCX MCP lifecycle gates are source of truth. |
| **Execution plane** | `iplan-runner` | Turns an approved IPLAN into auditable execution: **Ledger → Gate → Monitor**. Engine-agnostic; `hermes`/`claude` runtimes. Local-first, optional sync. |
| **System-of-record** | `iplanic` | Online lifecycle manager iplan-runner syncs to: dispatch, immutable versioning, completion gate, audit-grade evidence. |
| **Memory + knowledge plane** | **Engramory** (this repo) | Shared KB + per-agent distilled memory. **Replaces `ucx_kb`.** Consumed by Hermes, the runners, and product projects (`aidoc-flow`, `aidoc-flow-operations`, …). |

Engramory is the brain the other planes read from and write to. It must serve the SDD KB duties `ucx_kb` performs today **and** add the per-agent experiential memory the ecosystem lacks.

---

## What `ucx_kb` does today (the bar Engramory must clear)

From the framework, `ucx_kb` is the **Knowledge Base package (RAG + Graph)** with a governed policy:

1. **KB representation of every SDD artifact** — BRD/PRD/EARS/BDD/ADR/SPEC/TDD/IPLAN plus decisions are represented in the KB (RAG vectors + knowledge graph).
2. **Canonical entry structure** — `KB_ENTRY_TEMPLATE.md` defines the shape of a KB entry; `KB_GENERAL_RULES.md` defines mandatory coverage rules (which artifacts must be represented, and how).
3. **Retrieval enrichment** — the `ucx-kb-context` Hermes skill pulls relevant KB context *before* create / review / remediate.
4. **Governed maintenance** — the `ucx-kb-maintenance` Hermes skill updates the KB only through a governed workflow, *after* approved IPLAN evidence.
5. **Authority model** — KB **augments** decisions; the **UCX MCP lifecycle gates remain the source of truth**. The KB is advisory, never authoritative for SDD artifacts.
6. **Traceability** — KB entries align to UCX ID-naming and cumulative `@`-tag lineage (BRD→…→IPLAN, `@depends: BRD-01`).

> Note: exact `KB_ENTRY_TEMPLATE.md` and `KB_GENERAL_RULES.md` contents could not be retrieved by web fetch (GitHub JS-rendered pages returned empty). The points above are from the framework README's description of `ucx_kb` and the KB policy baseline. **Confirm the exact entry schema and coverage rules before finalizing the BRD** — easiest by connecting the `aidoc-flow-framework` repo as a local folder.

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

The Engramory BRD (Standard depth) must scope, at minimum:

- **Replace `ucx_kb`** as a first-class goal, with G1–G9 as requirements and a migration/cutover plan.
- **Authority boundary**: Engramory is advisory for SDD artifacts (gates win), canonical for agent memory.
- **Hermes skill compatibility**: `ucx-kb-context` and `ucx-kb-maintenance` must keep working.
- **Conformance**: Engramory must satisfy `ucx_kb`'s KB coverage rules and UCX ID-naming/traceability standards.
- Plus the experiential memory goals (L1–L3 + distillation) already designed.

---

## To produce conforming artifacts, I need the exact specs

Web fetch could not pull the framework's internal files. To draft a BRD that truly conforms (correct template fields, ID format, traceability tags, KB entry schema), the cleanest path is to **connect the `aidoc-flow-framework` repo (and `ucx_kb`) as a local folder** so I can read:

- `ucx_flow_v3/01_BRD/BRD-TEMPLATE.yaml` (+ `LAYER_REGISTRY.yaml`, `ID_NAMING_STANDARDS.md`, `TRACEABILITY.md`)
- `ucx_hermes/skills/hermes/ucx-kb-maintenance/KB_ENTRY_TEMPLATE.md` and `KB_GENERAL_RULES.md`
- the `ucx_kb/` package structure

With those, I can generate the Engramory BRD on the real template and lock G1–G9 to the actual KB schema.
