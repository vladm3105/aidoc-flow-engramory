---
name: saas-stewards-runner
description: >-
  Use to orchestrate, execute, inspect, and verify multi-tenant Synchora & SaaS Stewards scenarios (Autonomous SDLC Engineering #7-#9, Household Estate #1-#2, Laya BYOK Smart Router #6, CNCF Serverless Workflows, and Fleet Health) via Antigravity agy CLI, Python workflows, and FastMCP.
---

# Synchora & SaaS Stewards Scenario Runner & Fleet Orchestrator

This skill guides the Antigravity agent (`agy`) in executing, inspecting, and verifying multi-tenant scenarios across the 9 declarative Blueprints, 3 Infrastructure Bundles, and CNCF Serverless Workflows in **`aiops-synchora`** (formerly `aiops-saas-stewards`).

All executions follow the **Three-Layer One-Way Plane Boundary**:
- **Layer 1 (`aiops-synchora` / Human Operator, formerly `aiops-saas-stewards`)**: Owns multi-tenant & Godfood fleet supervision, tenant lifecycle, NATS subscriptions (`CANONICAL_STEWARD_PIPELINES`), and warranted workflow dispatch.
- **Layer 2 (`aiops-agent-support`, `aiops-agent-pm`, `aiops-agent-dev`, IDE/CLI Agents, `aiops-edge-runner`)**: Owns cognitive reasoning and domain execution (requiring a Layer-1 Steward `X-Agent-Warrant` in `stage`/`prod` via `SecurityGatePlugin.pre_task_execute`, with direct CLI/IDE `warrant-local` calls restricted to `dev`/`test`), consuming Layer 3 via MCP/skills or `HarnessKernel.from_consumer()` SDK.
- **Layer 3 (`aiops-harness`)**: Deterministic Execution, Tooling, SDK & 8-Canon Governance Harness ("The Agent's Harness").

---

## 1. Fast Scenario Execution via CLI

The fastest way to execute a pre-packaged scenario is via the unified scenario runner:

```bash
cd "${AIOPS_SYNCHORA_ROOT:-${AIOPS_SAAS_STEWARDS_ROOT:-../aiops-synchora}}"
PYTHONPATH=src:../aiops-edge-runner/src ../aiops-harness/.venv/bin/python scripts/run_scenario.py --scenario <SCENARIO> [--json]
```

### Available Scenario Targets

| Scenario | Command Flag | Description & Invariants Verified |
| :--- | :--- | :--- |
| **Full SDLC Choreography** | `--scenario sdlc-choreography` | Executes end-to-end SDLC pipeline: Defect intake triage & PII/secret scrubbing (`sdlc-support-v1`) $\rightarrow$ Backlog scoring & DAG slice planning (`sdlc-pm-v1`) $\rightarrow$ Leased task execution under `WorktreeJailGuard` (`sdlc-dev-v1`). |
| **Household Estate & Undo** | `--scenario household-estate` | Provisions `household-estate-v1`, enrolls Vlad's 256-dim voiceprint vector, actuates lighting with owner preferences (35%), and verifies 1-Click Undo rollback via `compensate_from_checkpoint`. |
| **Laya BYOK Smart Router** | `--scenario byok-router` | Evaluates multi-tier routing (fast/standard/frontier/regulated), prompt compaction, counterfactual cloud savings, and 90% turn buffer budget tripwires (`laya-router-byok-v1`). |
| **Tenant Lifecycle Sync** | `--scenario tenant-lifecycle` | Executes CNCF Serverless Workflow (`workflows/tenant_lifecycle_sync.swf.yaml`): `ACTIVE` $\rightarrow$ `SUSPENDED` (warrant fencing) $\rightarrow$ `ACTIVE` (warrant restoration). |
| **Fleet Health Sweep** | `--scenario fleet-sweep` | Inspects circuit breaker states (`CLOSED`, `HALF_OPEN`, `OPEN`), reaps expired task leases, and audits unresolved DLQ envelopes across all active tenants. |
| **Godfood Bootstrap** | `--scenario godfood-bootstrap` | Idempotently provisions internal Godfood tenants (`tenant_platform`, `tenant_aidoc`, `tenant_beelocal`) with declared virtual employee fleets. |
| **All Scenarios Suite** | `--scenario all` | Sequentially executes all 6 scenarios and generates a complete test receipt. |

---

## 2. Dispatching Individual Steward Turns via `fleet-dispatch`

To dispatch a single turn to any of the 9 blueprints on an active or newly provisioned tenant stack:

```bash
cd "${AIOPS_SYNCHORA_ROOT:-${AIOPS_SAAS_STEWARDS_ROOT:-../aiops-synchora}}"
uv run aiops-synchora fleet-dispatch \
  --tenant-id <TENANT_ID> \
  --bundle-id <BUNDLE_ID> \
  --tier <control_room|full_suite> \
  --blueprint-id <BLUEPRINT_ID> \
  --operation <OPERATION_NAME> \
  --payload-json '<JSON_PAYLOAD>'
```
*(Note: `uv run aiops-saas-stewards` remains fully supported as a backward-compatible CLI alias).*

### Supported Blueprint Operations Quick-Reference

1. **`sdlc-support-v1`**:
   - `triage_and_redact_intake`: `{ "raw_message": "...", "reporter_handle": "alice", "target_repo": "..." }`
2. **`sdlc-pm-v1`**:
   - `triage_and_score_backlog`: `{ "intake_id": "...", "title": "...", "severity": "high", "blast_radius": "module" }`
   - `plan_and_delegate_slice`: `{ "task_id": "...", "slice_id": "SLICE-01", "target_repo": "...", "allowed_paths": ["src"], "target_files": ["src/app.py"], "budget_cents": 500 }`
3. **`sdlc-dev-v1`**:
   - `enqueue_self_hosted_dev_task`: `{ "task_id": "...", "slice_id": "SLICE-01", "allowed_paths": ["src"], "target_files": ["src/app.py"], "budget_cents": 500 }`
4. **`steward-home-v1`**:
   - `handle_voice_or_chat_command`: `{ "utterance": "Turn on lights", "sample_voiceprint_256": [...], "proposed_action": "light.turn_on", "entity_id": "light.study", "checkpoint_id": "chk_01" }`
   - `propose_household_memory`: `{ "member_id": "vlad", "memory_id": "mem_01", "content": "Family dinner Saturday", "shared_household_scope": true }`
   - `approve_memory_proposal_card`: `{ "member_id": "vlad", "memory_id": "mem_01", "enforce_sod": true }`
5. **`yard-drip-v1`**:
   - `evaluate_and_actuate_drip_zone`: `{ "assessment": { "zone_id": "zone_lawn", "dryness_index": 0.8, "recommended_drip_minutes": 20 }, "confirmed_via_ui": true }`
6. **`smb-desk-v1`**:
   - `triage_incoming_message`: `{ "conversation_id": "conv_01", "customer_message": "...", "reporter_email": "..." }`
7. **`kep-knowledge-v1`**:
   - `submit_and_extract`: `{ "doc_id": "doc_01", "raw_text": "...", "sensitivity_class": "restricted", "candidate_kus": [...] }`
8. **`trading-advisor-v1`**:
   - `generate_advisory_thesis`: `{ "trader_id": "alex", "symbol": "NVDA", "thesis_summary": "..." }`
   - `distill_post_trade_reflection`: `{ "trader_id": "alex", "episode_id": "ep_01", "distilled_rule": "...", "confirmed_via_ui": true }`
9. **`laya-router-byok-v1`**:
   - `route_and_compact`: `{ "classifier_label": "fast", "messages": [...], "max_tool_chars": 200 }`

---

## 3. Invariant & Safety Guardrails

When executing scenarios via `agy`:
1. **Separation of Duties (SoD)**: The proposer of a shared space memory card or distilled trading rule cannot approve their own item (`home_steward.py:168`, `trading_steward.py:157`).
2. **Worktree Jail Guard**: `sdlc-dev-v1` file mutations must be strictly confined to declared `allowed_paths` within the tenant worktree. Paths containing `..`, `/etc/`, `.ci/`, or `Taskfile.yml` fail closed immediately (`platform_runtime.py:910`).
3. **SSRF Guard**: `trading-advisor-v1` rejects internal IPs (`127.0.0.1`, `0.0.0.0`, `10.x`, `100.64.x`), local DNS names, and URL credentials fail-closed (`trading_steward.py:118`).
4. **DLP & Secret Sanitizer**: Intakes containing API keys (`ghp_`, `gsk_`, `sk-`, `AKIA`) are scrubbed before writing to memory (`SaasMemoryPlane.write_memory`), and emit `[REDACTED_SECRET]` with quarantine warnings (`platform_runtime.py:162`).
