---
id: doc-platform-docs-03-linting-drift
title: Tier-1/2/3 Linting Gates, Vale AI-Chunking Rules, Drift Engine & Smoke Runner
kind: specification
domain: platform-docs
namespace: ns:platform
status: active
autonomy_tier: green
owner: platform-team
consumers:
  - aiops-harness
  - aidoc-hybrid-ci
  - unified-test-suite
  - aiops-dev
  - aiops-qa
tags:
  - vale
  - markdownlint-cli2
  - lychee
  - drift-detection
  - smoke-testing
  - hybrid-ci
endpoints:
  host_loopback: "http://127.0.0.1:8005"
  container_gateway: "http://10.250.0.1:8000"
watched_paths:
  - "docs/doc-tools/03_LINTING_GATES_AND_DRIFT_ENGINE.md"
last_verified: 2026-11-06
---

# Tier-1/2/3 Linting Gates, `Vale` AI-Chunking Rules, Drift Engine & Smoke Runner

## 1. 4-Tier Gate Placement Matrix

In strict alignment with `25-approved-tooling-inventory.md`, `platform-docs` partitions its verification battery across the platform's 4-tier gate cadence so git hooks (`lefthook`) and `task check` remain 100% offline and finish in `< 1 second`:

| Gate Tier | Execution Context | Budget | Tools & Validators Executed | Output Format |
| :--- | :--- | :--- | :--- | :--- |
| **Tier 1** (`lefthook` / `task check`) | Local workstation, gVisor sandbox (`--network=none`) | **Offline, `< 1s`** | 1. `platform-docs lint` (Frontmatter + Code-Block AST + CLI/Port Registry check)<br/>2. `vale --output=JSON` (AI-First Chunk-Independence & Prose rules)<br/>3. `markdownlint-cli2` (Heading & Fence AST)<br/>4. `yamllint` (Frontmatter & Config YAML)<br/>5. `platform-docs check-drift --local` (`watched_paths` vs. staged diff) | `.ci-out/summary.json` + JSON diagnostics |
| **Tier 2** (Gitea PR CI / `task ci`) | Ephemeral `act_runner` in gVisor | **Network OK, `< 30s`** | All Tier-1 checks +<br/>6. `lychee` (Cross-repo links, anchors, internal URL excludes)<br/>7. `platform-docs export-llms --check` (Verifies `/llms.txt` is up to date)<br/>8. `mermaid-cli` (`mmdc`) syntax verification on ```mermaid blocks | JUnit XML + `.ci-out/summary.json` |
| **Tier 3** (Scheduled Nightly Daemon) | `platform-docs` Stage Sweeper (`:8000`) | **Nightly Cron** | 9. `platform-docs check-staleness` (`last_verified` > `review_ttl_days` scan)<br/>10. `platform-docs smoke-run --tier green` (Executes `tier="green" mode="read-only"` runbook probes against live stage)<br/>11. Full external `lychee` sweep | OTLP Spans + NATS CloudEvents (`xp.platform.docs.>`) |

---

## 2. `Vale` AI-First Prose & Chunk-Independence Linter (MIT License)

`Vale` (`errata-ai/vale`, MIT license, single Go binary provisioned via `mise.toml`) parses Markdown ASTs while ignoring code blocks and inline literals. Unlike human editorial style guides, `platform-docs` ships a dedicated **`AIOpsAgent` Vale Style Pack** (`.vale/styles/AIOpsAgent/`) engineered specifically to keep Markdown chunks unambiguous for LLM context windows and RAG retrievers.

### 2.1 Canonical `.vale.ini` Configuration

```ini
StylesPath = .vale/styles
MinAlertLevel = error
Vocab = PlatformVocab

[*.md]
BasedOnStyles = AIOpsAgent
BlockIgnores = (?s) *({[a-z0-9_="-]+})
```

### 2.2 The 5 Core `AIOpsAgent` Vale Rules

| Rule ID | Rule Type | Severity | What It Detects & Why | Example Violation & Required Fix |
| :--- | :--- | :--- | :--- | :--- |
| **`AIOpsAgent.NoCrossChunkDeictics`** | `existence` | `error` | Bans cross-chunk spatial references that lose meaning when a `##` section is sliced in isolation by Kùzu or Engramory. | **Banned:** `"run the command above"`, `"as shown below"`, `"see the previous section"`, `"use the script above"`<br/>**Fix:** Reference the explicit step ID (`"run step probe-bao-status"`) or `[section link](#1-health-check)`. |
| **`AIOpsAgent.NoAmbiguousSubjectStart`** | `existence` | `error` | Bans sentences immediately following a `##` or `###` heading from starting with an unanchored pronoun (`It`, `This`, `They`, `That`). | **Banned:** `## 2. Failover\nIt triggers when...`<br/>**Fix:** `## 2. Patroni Raft Failover\nPatroni triggers failover on pg-node-1 when...` |
| **`AIOpsAgent.BannedRetiredTools`** | `substitution` | `error` | Flags accidental references to **Hold / Retired** tools from `25-approved-tooling-inventory.md` inside operational steps. | Flags `seaweedfs` ($\rightarrow$ `OCI Object Storage`), `bytebase` ($\rightarrow$ `Superset SQL Lab + Squawk + pgroll`), `pre-commit` ($\rightarrow$ `lefthook`), `:latest` upstream image tags. |
| **`AIOpsAgent.SectionLengthCeiling`** | `occurrence` | `warning` / `error` | Ensures no single `##` or `###` prose block exceeds 450 words without subheadings, preserving optimal RAG chunk density. | Split long sections into atomic `###` sub-procedures. |
| **`AIOpsAgent.DeterministicPortFormat`** | `regex` | `error` | Flags bare `localhost:<port>` references in `platform-*` runbooks that omit the Dual-Endpoint specification (`127.0.0.1` host loopback vs. `10.250.0.1` container gateway). | Replace `http://localhost:8200` with explicit `http://127.0.0.1:18200` (host) or `http://10.250.0.1:8200` (container gateway). |

### 2.3 Example Vale Rule Definition (`.vale/styles/AIOpsAgent/NoCrossChunkDeictics.yml`)

```yaml
extends: existence
message: "Avoid cross-chunk reference '%s'. Name the explicit step ID (e.g., `probe-bao-status`) or Markdown anchor so isolated RAG chunks remain self-contained."
level: error
ignorecase: true
tokens:
  - 'the command above'
  - 'the commands above'
  - 'the command below'
  - 'the script above'
  - 'the snippet above'
  - 'as shown above'
  - 'as shown below'
  - 'in the section above'
  - 'in the previous section'
  - 'when this happens'
```

---

## 3. Live Command, MCP Tool & Stage Port Registry Verifier

Static prose linters cannot tell when a CLI flag or port in a runbook is hallucinated or outdated. `platform-docs lint --verify-registry` performs three deterministic cross-checks in `< 150ms`:

1. **CLI Command Verification (`aiops-harness` & `aidoc-ctx` AST Introspection):**
   Extracts every invocation of `aiops-harness <group> <cmd>`, `aidoc-ctx <cmd>`, `engramory <cmd>`, and `platform-docs <cmd>` inside `bash` code blocks and `triggers.cli_commands`, verifying against the Click/Typer command tree that the subcommand and all `--flags` exist (`DOC-CLI-001`).
2. **FastMCP Tool Name Verification:**
   Cross-checks every `triggers.mcp_tools` entry and inline `aiops_*` / `aidoc_*` / `sec_*` / `docs_*` reference against the canonical MCP tool catalog (`DOC-MCP-001`).
3. **Stage Port & Endpoint Verification (`platform_bridge` Lookup):**
   Cross-checks `endpoints.host_loopback` (`127.0.0.1:<port>`) and `endpoints.container_gateway` (`10.250.0.1:<port>`) in frontmatter and code blocks against `aiops_harness.platform_bridge.STAGE_PORT_MATRIX` (`DOC-PORT-001`).

---

## 4. Deterministic Code-to-Doc Drift & Staleness Engine

### 4.1 `watched_paths` Git Diff Matcher (`platform-docs check-drift`)

Every document's frontmatter declares `watched_paths` (glob patterns relative to the repository root, e.g., `["compose.yaml", "infra/tofu/**", "src/authz/**"]`).

When `platform-docs check-drift --base origin/dev` runs in Tier-1/Tier-2 CI:

1. It computes `git diff --name-only <base>...HEAD`.
2. It evaluates all `docs/*.md` in the repository whose `watched_paths` match the modified code/IaC files.
3. If a matched `docs/*.md` was **not** updated in the same diff AND its `last_verified` date was not bumped to today's date:
   - **In Blocking Mode (Default on `platform-*` & `aiops-harness`):** Fails the check with `DOC-DRIFT-001`, printing the exact `docs/*.md` file and the triggering code path so the authoring agent updates or re-verifies the runbook before committing.
   - **In Async Mode (Cross-repo changes):** Emits a `xp.platform.docs.drift.v1` CloudEvent to `platform-paperclip` (see [`docs/06_AUTONOMOUS_AGENT_WORKFLOWS_NATS_AND_PAPERCLIP.md`](06_AUTONOMOUS_AGENT_WORKFLOWS_NATS_AND_PAPERCLIP.md)).

### 4.2 TTL Staleness Scanner (`platform-docs check-staleness`)

During the nightly Tier-3 sweep, `platform-docs` scans `last_verified` across all 35+ repositories:
$$\text{AgeDays}(D) = \text{Today} - D.\text{last\_verified} > D.\text{review\_ttl\_days}$$
Any document exceeding its `review_ttl_days` (default: `90` days) triggers a `DOC-STALE-001` event, automatically scheduling a `DOC2C` verification task in `platform-paperclip`.

---

## 5. Live `tier="green"` Runbook Smoke Runner (`platform-docs smoke-run`)

To guarantee that diagnostic health checks in `platform-*` operational runbooks never rot:

```bash {id="run-green-smoke-probes" tier="green" mode="read-only" expect_exit=0 timeout_sec=60}
uv run platform-docs smoke-run --target $WORKSPACE_ROOT --tier green --json
```

1. **AST Extraction:** Scans all `status: active` documents with `kind: runbook` or `kind: playbook` and extracts code blocks annotated with `{tier="green" mode="read-only"}`.
2. **Safety Gate Verification:** Confirms via `INV-STEP-02` AST check and `platform_security.authz` that every extracted command is strictly read-only.
3. **Sandboxed Execution:** Executes each probe inside a read-only runner with `timeout_sec` enforced and asserts `exit_code == expect_exit`.
4. **Auto-Verification:** When all `tier="green"` probes in a runbook pass on staging, `platform-docs` records the verification receipt in ClickHouse (`otel_logs`) and `unified-test-suite` (`uts`).
