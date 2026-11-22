---
paths:
  - "**/docs/audits/**"
  - "**/AUDIT-*.md"
  - "**/docs/sdd/09_CHG/**"
  - "**/docs/sdd/08_IPLAN/**"
---

# Audit Ledger & Centralized Paperclip Backlog Governance

## 1. Canonical Placement & Naming

- Repository audit reports MUST live under `docs/audits/AUDIT-YYYY-MM-DD_<slug>.md` (never at repository root or inside `docs/` top-level runbooks).
- Every audit file MUST include complete 9-key YAML frontmatter (`DOC-FM-000`) with `kind: audit`, `status: active`, and valid `watched_paths`.
- Downstream documentation walkers (`docs smoke-run`, `docs check-drift`, `docs export-llms`, `docs slice-runbook`, `docs build-portal`, `extract_docs_graph`, `EngramoryClient.knowledge_search`) exclude `docs/audits/` (`kind: audit`), whereas `docs lint --strict` continues to enforce `DOC-CHUNK-001` (`<= 450` words per H2/H3/H4 subsection) and secret hygiene (`DOC-SEC-001`).

## 2. Mandatory `## Disposition ledger` & `[I-09]` Waiver Independence

- Every audit report MUST contain exactly one `## Disposition ledger` section. Split large audits across `#### Wave ...` subsections so every chunk stays `<= 450` words.
- Every ledger table MUST declare columns: `Finding ID | Severity | Disposition | Target / Ticket | Rationale`.
- No row across any severity (`BLOCKER`, `CRITICAL`, `HIGH`, `MEDIUM`, `LOW`) may have empty or `UNMAPPED` / `TBD` / `TODO` values in `Disposition` or `Target / Ticket`.
- Allowed dispositions: `paperclip`, `fixed`, `waived`, `duplicate`, `out-of-scope`, `deferred`, `superseded`, `tracked`.
  - **`paperclip`:** `Target / Ticket` MUST reference a real `pc-task-<id>` ticket ID (validated via `aiops-harness audit-check [--verify-paperclip]`). Do NOT fabricate fake `pc-task-*` IDs when Paperclip is degraded or offline.
  - **`tracked`:** `Target / Ticket` MUST reference a canonical artifact (`CHG-NN`, `IPLAN-NN`, `PLAN-NNN`, `GH-N`, `#N`, or `commit:<sha>`).
  - **`waived` (`[I-09]` Evaluator Independence):** `Target / Ticket` MUST match `waiver:<human-id>` (non-agent human principal different from the audit author) AND `Rationale` MUST be `>= 15` characters.

## 3. Centralized Backlog & CHG + IPLAN Execution Flywheel

- **Centralized Backlog (`platform-paperclip`):** Cross-agent execution backlog and discovered `TODO` / follow-up items belong in `platform-paperclip` (via `aiops_paperclip_queue_todo`, `aiops_paperclip_submit`, or `aiops-harness paperclip queue-backlog`), linked to parent CHG tasks, with GitHub Issues retained for external support-desk intake.
- **CHG + IPLAN Execution Ledger:** `docs/sdd/09_CHG/` + `docs/sdd/08_IPLAN/` + `docs/sdd/graph/` remain the Git source of truth. Ingest active work into Paperclip via `aiops_chg_sync_paperclip` (`aiops-harness chg ingest-paperclip`), record execution milestones via `aiops_paperclip_post_notice`, and distill completed resolutions into Engramory via `aiops_task_distill_engramory` (`aiops-harness paperclip distill`).
