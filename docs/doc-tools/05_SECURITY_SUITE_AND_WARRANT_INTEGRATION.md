---
id: doc-platform-docs-05-security
title: Unified 6-Pillar Security Suite, Warrant-Gated Runbooks & Rule D14 Integration
kind: specification
domain: platform-docs
namespace: ns:platform
status: active
autonomy_tier: green
owner: security-platform-team
consumers:
  - platform-security
  - aiops-harness
  - aidoc-flow-support-desk
  - aiops-sec
  - platform-paperclip
tags:
  - platform-security
  - cedar-authz
  - x-agent-warrant
  - rule-d14
  - camel-fencing
  - authentik-rbac
endpoints:
  host_loopback: "http://127.0.0.1:8005"
  container_gateway: "http://10.250.0.1:8000"
watched_paths:
  - "docs/doc-tools/05_SECURITY_SUITE_AND_WARRANT_INTEGRATION.md"
last_verified: 2026-11-06
---

# Unified 6-Pillar Security Suite, Warrant-Gated Runbooks & Rule D14 Integration

## 1. Why Documentation & Runbooks Are a Primary Security Boundary

In an autonomous AI engineering ecosystem, operational documentation (`docs/*.md`) and `/llms.txt` manifests are directly ingested into agent context windows and executed as shell commands during incident triage. Two severe threat vectors arise if documentation is not integrated with `platform-security`:

1. **Un-Gated Destructive Runbook Execution:** An autonomous agent reading an infrastructure runbook (e.g., `platform-openbao/docs/OPERATIONS.md` or `platform-databases/docs/FAILOVER.md`) could execute a mutating recovery step or break-glass ceremony without human authorization.
2. **Documentation & RAG Prompt Injection / Secret Leakage:** Untrusted issue descriptions or external Chatwoot feedback ingested into documentation PRs could inject malicious instructions into RAG chunks, or an accidental secret in a Markdown example could be indexed into `aidoc-flow-engramory` and `/llms-full.txt`.

`platform-docs` integrates with all **6 Pillars of `platform-security`** (`10.250.0.1:8095` & `/run/platform-security/gate.sock`) to eliminate both risks deterministically.

---

## 2. 6-Pillar `platform-security` Integration Matrix

| Security Pillar | Module (`platform_security.*`) | Enforcement in `platform-docs` |
| :--- | :--- | :--- |
| **Pillar 1: Edge Gateway** | `platform_security.edge` | Protects the Starlight + Scalar Portal (`https://docs.int.aiops-flow.io`) via Traefik v3.6 OIDC ForwardAuth over the NetBird mesh (`corridor-proxy-stage`); zero open public ports. |
| **Pillar 2: Workforce & Agent Identity** | `platform_security.identity` | Enforces Authentik 2-Axis Hybrid RBAC (`human_roles` + `agent_role`) and strictly disjoint root namespaces (`ns:platform`, `ns:aidoc`, `ns:b-local`) on all portal views, FastMCP queries, and Kùzu slices. |
| **Pillar 3: 3-Layer Cedar AuthZ & Warrants** | `platform_security.authz` | Evaluates in-process AWS Cedar policies (`cedarpy`) + Ed25519 `X-Agent-Warrant` + `RevocationRegistry` before allowing an agent to slice or execute a `RunbookStepNode`. |
| **Pillar 4: Threat, AV & DLP Engine** | `platform_security.threat` | Scans all documentation PR diffs, attachments, and compiled `/llms.txt` files with `inspect_and_redact_text()` (Doc 06 Invariant `S19`: exact secret prefixes, Luhn, SSN, canary tokens). |
| **Pillar 5: CaMeL SecOps & Sandbox Guard** | `platform_security.agents` | Wraps any untrusted defect report, Chatwoot ticket, or failed CI log in a 96-bit CaMeL nonce fence (`fence_untrusted()`) before an agent drafts a `DOC2C` documentation update. |
| **Pillar 6: Centralized Security Gate & Audit** | `platform_security.gate` / `.audit` | Scrubs all outbound OTLP spans, Kùzu nodes, and `/llms-full.txt` exports via `redact_for_d14()` (Rule D14) and emits signed audit records to ClickHouse SIEM. |

---

## 3. AWS Cedar Policy & `X-Agent-Warrant` Enforcement on Runbook Steps

Every `RunbookStepNode` extracted by `platform-docs` carries `tier` (`green` | `yellow` | `red`), `mode` (`read-only` | `mutating`), and `namespace` (`ns:platform` | `ns:aidoc` | `ns:b-local`).

Before `aiops-harness` or `docs_execute_step` executes any runbook command, it calls `platform_security.authz` with the caller's signed Ed25519 `X-Agent-Warrant`:

```cedar
// policies/docs_runbooks.cedar

// 1. Disjoint Namespace Isolation: Never allow cross-namespace runbook slicing or execution
forbid (
    principal,
    action,
    resource is Docs::RunbookStep
)
when {
    principal.namespace != resource.namespace
};

// 2. Doc 06 Invariant S6 (AGENT_FORBIDDEN): Agents may NEVER execute 'red' (break-glass) runbook steps
forbid (
    principal is Agent::Worker,
    action == Docs::Action::"ExecuteRunbookStep",
    resource is Docs::RunbookStep
)
when {
    resource.tier == "red"
};

// 3. Green Read-Only Probes: Allowed for authorized agents with an active, non-revoked warrant
permit (
    principal is Agent::Worker,
    action == Docs::Action::"ExecuteRunbookStep",
    resource is Docs::RunbookStep
)
when {
    resource.tier == "green" &&
    resource.mode == "read-only" &&
    context.warrant_valid == true &&
    context.warrant_revoked == false
};

// 4. Yellow Mutating Steps: Require active warrant + verified human consent_id from Paperclip/Chatwoot
permit (
    principal is Agent::Worker,
    action == Docs::Action::"ExecuteRunbookStep",
    resource is Docs::RunbookStep
)
when {
    resource.tier == "yellow" &&
    context.warrant_valid == true &&
    context.warrant_revoked == false &&
    context.has_verified_human_consent == true &&
    context.consent_step_uid == resource.step_uid
};
```

---

## 4. Pre-Index & Pre-Export Secret Redaction (Rule D14 + Invariant `S19`)

Documentation frequently contains example curl requests, OpenBao paths, or JWT structures. To prevent real credentials from ever entering `aidoc-flow-engramory`, Kùzu, or `/llms.txt`:

1. **Tier-1 Hard Gate (`gitleaks` + `platform_security.threat.inspect_and_redact_text`):**
   Runs during `platform-docs lint` and `platform-docs export-llms`. Any deterministic secret match (`hard=True`: OpenBao `hvs.*` tokens, OCI private keys, GitHub/Gitea PATs, Ed25519 private keys, Luhn-valid payment cards) immediately fails the build (`DOC-SEC-002`).
2. **Mandatory Dummy Token Convention in Examples:**
   All documentation examples must use RFC-compliant placeholder domains (`example.com`, `*.int.aiops-flow.io`) and explicitly marked dummy tokens (`<OPENBAO_APP_TOKEN>`, `hvs.<EXAMPLE-TOKEN>`).
3. **In-Line Rule D14 Scrubbing (`redact_for_d14`):**
   Every chunk exported to `aidoc-flow-engramory` or written to `/llms.txt` / `/llms-full.txt` passes through `redact_for_d14()` prior to serialization.

---

## 5. CaMeL Nonce Fencing & `MemoryPoisoningGuard`

When `platform-docs` or `aidoc-flow-engramory` ingests documentation updates triggered by external tickets (`aidoc-flow-support-desk` / `platform-chatwoot`):

1. **96-Bit CaMeL Nonce Fencing (`fence_untrusted`):**
   The external issue description or failed user command log is wrapped in an unguessable per-request XML nonce boundary (`<untrusted_input_a9f3e12b8c4d>...</untrusted_input_a9f3e12b8c4d>`) before `aiops-dev` reads it to draft a documentation fix.
2. **Heuristic Signal Escalation (`S19`):**
   If `platform_security.threat` detects prompt-injection heuristics (`hard=False`) inside a proposed Markdown diff or `/llms.txt` file, the PR is automatically escalated from `autonomy_tier: green` to `yellow` in `platform-paperclip`, requiring human security sign-off before merge and before `sync-engramory` indexes the chunk.
