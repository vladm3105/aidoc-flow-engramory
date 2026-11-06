---
id: doc-platform-docs-06-agent-workflows
title: Autonomous Agent Workflows, NATS Contracts & Paperclip Self-Healing Loops
kind: specification
domain: platform-docs
namespace: ns:platform
status: active
autonomy_tier: green
owner: platform-team
consumers:
  - aiops-pm
  - platform-paperclip
  - platform-nats
  - aiops-dev
  - aiops-harness
  - aidoc-flow-support-desk
tags:
  - nats-jetstream
  - cloudevents
  - paperclip
  - doc2c
  - code2s
  - autonomous-remediation
endpoints:
  host_loopback: "http://127.0.0.1:8005"
  container_gateway: "http://10.250.0.1:8000"
watched_paths:
  - "docs/doc-tools/06_AUTONOMOUS_AGENT_WORKFLOWS_NATS_AND_PAPERCLIP.md"
last_verified: 2026-11-06
---

# Autonomous Agent Workflows, NATS Contracts & Paperclip Self-Healing Loops

## 1. Autonomous Documentation Maintenance Architecture

Rather than relying on unconstrained third-party bots (`RepoAgent`) that push unverified prose directly to branches, `platform-docs` routes all autonomous documentation maintenance through the platform's canonical **NATS JetStream $\rightarrow$ `aiops-pm` $\rightarrow$ `platform-paperclip` $\rightarrow$ `aiops-dev` $\rightarrow$ `aidoc-hybrid-ci` Gitea PR pipeline**.

Four deterministic triggers initiate an autonomous documentation update (`DOC2C` or `CODE2S` CHG flow):

1. **Code-to-Doc Drift (`xp.platform.docs.drift.v1`):** Source code, Compose files, OpenTofu modules, or CLI definitions matched by a document's `watched_paths` changed without updating the bound `docs/*.md`.
2. **TTL Staleness Expiry (`xp.platform.docs.stale.v1`):** Tier-3 nightly scan detects `Today - last_verified > review_ttl_days` (default 90 days).
3. **Failed Green Runbook Probe (`xp.platform.docs.probe_failed.v1`):** Tier-3 `platform-docs smoke-run --tier green` detects that a diagnostic probe in a runbook exited non-zero or referenced a deprecated flag/port.
4. **Support Desk Documentation Gap (`xp.support.triage.doc_gap.v1`):** `aidoc-flow-support-desk` triages a Chatwoot incident or operator question and classifies the root cause as a missing or ambiguous runbook step.

---

## 2. NATS JetStream Subjects & CloudEvents 1.0 Contracts

All `platform-docs` events are published to the `PLATFORM_DOCS` stream on `platform-nats` (`10.250.0.1:4222`), serialized as RFC 8785 JCS-canonicalized I-JSON CloudEvents v1.0 (`platform-data-contracts`) with deterministic UUIDv5 idempotency keys (`request_id`):

| NATS Subject | Event Type | Producer | Consumer | Purpose |
| :--- | :--- | :--- | :--- | :--- |
| `xp.platform.docs.drift.v1` | `io.aiops.platform.docs.drift.v1` | `platform-docs check-drift` | `aiops-pm` | Requests a `DOC2C` / `CODE2S` Paperclip task to sync drifted documentation with code changes. |
| `xp.platform.docs.stale.v1` | `io.aiops.platform.docs.stale.v1` | `platform-docs check-staleness` | `aiops-pm` | Requests a periodic verification & smoke-test pass on a runbook exceeding `review_ttl_days`. |
| `xp.platform.docs.probe_failed.v1` | `io.aiops.platform.docs.probe_failed.v1` | `platform-docs smoke-run` | `aiops-pm`, `support-desk` | Alerts that a `tier="green"` runbook probe failed on stage; triggers doc or infra triage. |
| `xp.platform.docs.indexed.v1` | `io.aiops.platform.docs.indexed.v1` | `platform-docs sync` | `aidoc-context-tools`, `engramory` | Signals that a repo's Kùzu graph slice, Engramory L0 chunks, and `/llms.txt` bundle were updated. |

### 2.1 Example JCS-Signed CloudEvent Payload (`xp.platform.docs.drift.v1`)

```json
{
  "specversion": "1.0",
  "id": "019a5860-7c4a-7000-8000-000000000101",
  "source": "urn:aiops:platform-docs:stage",
  "type": "io.aiops.platform.docs.drift.v1",
  "time": "2026-11-06T15:20:00Z",
  "datacontenttype": "application/json",
  "subject": "platform-openbao/docs/OPERATIONS.md",
  "data": {
    "request_id": "550e8400-e29b-51d4-a716-446655440000",
    "namespace": "ns:platform",
    "repo": "platform-openbao",
    "doc_id": "runbook-openbao-operations",
    "doc_path": "docs/OPERATIONS.md",
    "drift_kind": "WATCHED_PATH_MODIFIED",
    "trigger_commit": "e4f9a12b8c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f",
    "touched_watched_paths": [
      "infra/tofu/openbao/main.tf",
      "compose.yaml"
    ],
    "recommended_chg_flow": "CODE2S",
    "autonomy_tier": "green",
    "suggested_budget_usd": 1.50
  }
}
```

---

## 3. `platform-paperclip` Task Lifecycle & Budget Governance

When `aiops-pm` consumes a `xp.platform.docs.drift.v1` or `stale.v1` event:

1. **Idempotent Paperclip Task Creation (`INV-PC-07`):**
   `aiops-pm` queries `platform-paperclip` (`10.250.0.1:3100`) by `request_id = UUIDv5(DNS, repo + doc_id + trigger_commit)`. If no open task exists, it creates a task in the target repository's backlog with:
   - **Flow Classification:** `DOC2C` (for runbook/readme updates) or `CODE2S` (when SDD `SPEC`/`ADR` layers also require synchronization).
   - **LLM Spend Ceiling (`max_budget_usd`):** `$1.50` default for single-doc updates, enforced in real time by `platform-litellm-laya` (`tier-standard` / `tier-fast`).
2. **Atomic Checkout & Context Slicing by `aiops-dev`:**
   An `aiops-dev` worker acquires the atomic Paperclip checkout lock, mints a task-scoped Ed25519 `X-Agent-Warrant`, creates an isolated Gitea git worktree (`docs/chg-<seq>-sync-runbook`), and calls `docs_slice_runbook` + `aidoc_slice_context` to load only the affected document and git diff (~1,500 tokens).
3. **Deterministic Verification Before PR (`task check` + `task ci` + `task review`):**
    inside the worktree, `aiops-dev`:
   - Updates the Markdown sections and bumps `last_verified: <today>`.
   - Runs `platform-docs lint` (Frontmatter + Code-Block AST + CLI/Port registry check).
   - Runs `vale` (`AIOpsAgent` chunk-independence rules) and `markdownlint-cli2`.
   - Regenerates `/llms.txt` and `/llms-full.txt` via `platform-docs export-llms`.
   - Executes `task ci` and 2-pass independent `task review`.
4. **Autonomy-Tier Merge Routing:**
   - If the modified document has `autonomy_tier: green` (or only prose/green read-only probes were updated) and `.ci-config` permits autonomous merge on `dev`, `aiops-dev` runs `task autoship`.
   - If `yellow` or `red` executable code blocks in a runbook were modified, `platform-docs check-drift` escalates the Paperclip task to **`tier: yellow`**, pausing for a human `consent_id` in Paperclip or Chatwoot before merging into `dev`.
