# Project Onboarding & Ecosystem Guidance

The canonical onboarding procedures, declarative CNCF workflows, drop-in templates, and architecture guides for this project are maintained in the authoritative 8-Canon Hub-and-Spoke control plane:

👉 **[`aiops-harness`](https://github.com/vladm3105/aiops-harness)** (local checkout: `$AIOPS_HARNESS_ROOT`)

---

## Quick Reference Guides

- **Quick Start Guide:** [`docs/QUICKSTART.md`](https://github.com/vladm3105/aiops-harness/blob/dev/docs/QUICKSTART.md) — 3-minute setup for Greenfield and Brownfield projects via `aiops-harness`.
- **Master CLI, MCP & Operational Runbook:** [`docs/CLI_AND_MCP_RUNBOOK.md`](https://github.com/vladm3105/aiops-harness/blob/dev/docs/CLI_AND_MCP_RUNBOOK.md) — Unified CLI (`uv run aiops-harness ...`) and FastMCP server reference.
- **Greenfield Onboarding Guide:** [`docs/GREENFIELD_ONBOARDING.md`](https://github.com/vladm3105/aiops-harness/blob/dev/docs/GREENFIELD_ONBOARDING.md) — In-depth pipeline for brand-new repositories (`scripts/init-greenfield.sh`).
- **Brownfield Onboarding Guide:** [`docs/BROWNFIELD_ONBOARDING.md`](https://github.com/vladm3105/aiops-harness/blob/dev/docs/BROWNFIELD_ONBOARDING.md) — Non-destructive adoption and `CODE2S` baseline reconciliation (`scripts/adapt-brownfield.sh`).
- **Initial Planning Standard:** [`docs/INITIAL_PLANNING_GUIDE.md`](https://github.com/vladm3105/aiops-harness/blob/dev/docs/INITIAL_PLANNING_GUIDE.md) — Authoring `PLAN-001` vs engine-internal plans.
- **Governance Surfaces Standard:** [`docs/GOVERNANCE_SURFACES_GUIDE.md`](https://github.com/vladm3105/aiops-harness/blob/dev/docs/GOVERNANCE_SURFACES_GUIDE.md) — Managing canonical state in `AGENTS.md` and preventing deprecated files.
- **Upgrade & Drift Management:** [`docs/UPGRADE_AND_DRIFT_GUIDE.md`](https://github.com/vladm3105/aiops-harness/blob/dev/docs/UPGRADE_AND_DRIFT_GUIDE.md) — Auditing version drift and running non-destructive upstream reconciliations.
- **LLM Gateway Standard:** [`docs/LLM_GATEWAY_INTEGRATION.md`](https://github.com/vladm3105/aiops-harness/blob/dev/docs/LLM_GATEWAY_INTEGRATION.md) — LiteLLM proxy default provider, caching, and D14 token scrubbing.
- **Identity & SSO Standard:** [`docs/SSO_AUTHENTICATION_INTEGRATION.md`](https://github.com/vladm3105/aiops-harness/blob/dev/docs/SSO_AUTHENTICATION_INTEGRATION.md) — Authentik universal SSO, OIDC/SAML, and FastAPI JWKS token verification.
- **Observability Standard:** [`docs/OBSERVABILITY_INTEGRATION.md`](https://github.com/vladm3105/aiops-harness/blob/dev/docs/OBSERVABILITY_INTEGRATION.md) — OTel Collector (`10.250.0.1:24318`), HyperDX APM/traces/logs, Coroot topology, Langfuse.
- **Master Platform Integration Guide:** [`docs/INTEGRATION_GUIDE.md`](https://github.com/vladm3105/aiops-harness/blob/dev/docs/INTEGRATION_GUIDE.md) — Mandatory staging integration across all core subsystems.
- **Support Desk Standard:** [`docs/SUPPORT_DESK_INTEGRATION.md`](https://github.com/vladm3105/aiops-harness/blob/dev/docs/SUPPORT_DESK_INTEGRATION.md) — Autonomous support desk defect intake, Chatwoot bouncer, and feedback streaming.
- **AI Agent Runbook:** [`docs/INSTALLATION_AGENT_GUIDE.md`](https://github.com/vladm3105/aiops-harness/blob/dev/docs/INSTALLATION_AGENT_GUIDE.md) — Operational instructions and prime invariants for autonomous coding agents.

---

## Core Quality Gates

Every onboarded project satisfies 9 mandatory quality gates:

```bash
task doctor           # Verify local toolchain prerequisites
task uts:check        # Verify Unified Test Suite integrity
task test:fast        # Run unit test battery
task check            # Run full code and configuration linters
task ci               # Run comprehensive local CI pipeline
task sdd:check        # Run SDD gate: profile + structural lint + review verification
task sdd:graph-check  # Run Binary Topological Validation (BTV) over docs/sdd/graph/
task sdd:profile-lint # Validate .aidoc/profile.yaml against schema 1.1.0
task stack:audit      # Verify stack conformance via Syft SBOM + Trivy scan
```
