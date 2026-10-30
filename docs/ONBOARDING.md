# Project Onboarding & Ecosystem Guidance

The canonical onboarding procedures, declarative CNCF workflows, drop-in templates, and architecture guides for this project are maintained in the authoritative platform onboarding canon:

👉 **[`aidoc-project-onboarding`](https://github.com/vladm3105/aidoc-project-onboarding)**

---

## Quick Reference Guides

- **Quick Start Guide:** [`docs/QUICKSTART.md`](https://github.com/vladm3105/aidoc-project-onboarding/blob/dev/docs/QUICKSTART.md) — 3-minute setup for Greenfield and Brownfield projects.
- **Greenfield Onboarding Guide:** [`docs/GREENFIELD_ONBOARDING.md`](https://github.com/vladm3105/aidoc-project-onboarding/blob/dev/docs/GREENFIELD_ONBOARDING.md) — In-depth 8-step pipeline for brand-new repositories.
- **Brownfield Onboarding Guide:** [`docs/BROWNFIELD_ONBOARDING.md`](https://github.com/vladm3105/aidoc-project-onboarding/blob/dev/docs/BROWNFIELD_ONBOARDING.md) — Non-destructive adoption and `CODE2S` baseline reconciliation.
- **Initial Planning Standard:** [`docs/INITIAL_PLANNING_GUIDE.md`](https://github.com/vladm3105/aidoc-project-onboarding/blob/dev/docs/INITIAL_PLANNING_GUIDE.md) — Authoring `PLAN-001` vs engine-internal plans.
- **Governance Surfaces Standard:** [`docs/GOVERNANCE_SURFACES_GUIDE.md`](https://github.com/vladm3105/aidoc-project-onboarding/blob/dev/docs/GOVERNANCE_SURFACES_GUIDE.md) — Managing canonical state in `AGENTS.md` and preventing deprecated files.
- **Upgrade & Drift Management:** [`docs/UPGRADE_AND_DRIFT_GUIDE.md`](https://github.com/vladm3105/aidoc-project-onboarding/blob/dev/docs/UPGRADE_AND_DRIFT_GUIDE.md) — Auditing version drift and running non-destructive upstream reconciliations.
- **LLM Gateway Standard:** [`docs/LLM_GATEWAY_INTEGRATION.md`](https://github.com/vladm3105/aidoc-project-onboarding/blob/dev/docs/LLM_GATEWAY_INTEGRATION.md) — LiteLLM proxy default provider, caching, and D14 token scrubbing.
- **Identity & SSO Standard:** [`docs/SSO_AUTHENTICATION_INTEGRATION.md`](https://github.com/vladm3105/aidoc-project-onboarding/blob/dev/docs/SSO_AUTHENTICATION_INTEGRATION.md) — Authentik universal SSO, OIDC/SAML, and FastAPI JWKS token verification.
- **AI Agent Runbook:** [`docs/INSTALLATION_AGENT_GUIDE.md`](https://github.com/vladm3105/aidoc-project-onboarding/blob/dev/docs/INSTALLATION_AGENT_GUIDE.md) — Operational instructions and prime invariants for autonomous coding agents.

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
