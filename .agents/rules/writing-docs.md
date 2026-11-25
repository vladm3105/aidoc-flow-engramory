---
paths:
  - "**/*.md"
  - "**/*.rst"
  - "**/*.txt"
---

# Writing documentation

**Objective and factual.** Imperative verbs for procedures; conditionals for
error handling; precise data types; explicit scope limits; measurable impact
criteria where available.

**Never write in *prose docs*:** benefit statements ("this will help you…") ·
efficiency or
speed claims · ease-of-use assertions ("simply…", "just…") · future promises
("you'll be able to…") · superlatives (best, optimal, powerful, easy) · verbose
introductions · time estimates.

Planning artifacts (`PLAN-*`, `IPLAN-*`) are exempt from the time-estimate ban
only — effort estimation is their deliverable. The remaining bans stay
universal.

**Always state:** implementation complexity (1 = minimal config, 5 =
architectural change) · resource requirements (CPU / memory / network /
storage) · failure modes and error conditions · dependencies with versions ·
the validation procedure.

**Density:** one sentence per function description · tables for parameter specs
· bullets for configuration options · abbreviated notation (HTTP status codes,
RFC references).

**Code in docs:** inline is fine under ~50 lines; beyond that put it in a real
file and reference it as `[See Code Example: filename.py - function_name()]`.
Mermaid for complex logic. Pick the format that serves clarity.

**Size:** target ≤50,000 tokens (200KB) per file, hard cap 100,000 (400KB).
Split into sequential files (`doc_001.md`, `doc_002.md`) only past the cap or at
a logical boundary. Keep under 30KB where GitHub Copilot is a consumer, or ship
a companion summary. For Gemini CLI use the file-read tool rather than `@` above
10,000 tokens — no splitting needed.

**Governed `platform-docs` canon (`docs-lint` / `docs-drift`).** In repositories
with governed documentation (`docs/` or `.aidoc/docs/`), every section under an
H2/H3 heading must stay `<= 450` words (`max_section_words`), document body
headings must start at `##` (H2), and `00_INDEX.md` frontmatter `last_verified`
must stay within the 90-day freshness window. Never reference retired tools or
service names (`seaweedfs`, `platform-identity`, `platform-authz`, `aidoc-flow-support-desk`,
`aidoc-flow-interlog`, `operations-assistant`); use `OCI Object Storage / Cloudflare R2`,
`platform-security` (`platform_security.authz` / `platform_security.identity`), `aiops-agent-support`, and `aiops-agent-pm`.
Verify with `uv run aiops-harness gate run docs-lint --target <docs-root> --json`
and `uv run aiops-harness gate run docs-drift --target <docs-root> --json`.

**On-demand documentation protocol.** When requested to generate or maintain documentation (e.g. "Create a full set of documentation for the project as discussed"), execute in 4 steps:
1. *Context & Scope Intake:* Determine whether the request targets Plane-A SDD specifications (`01_BRD`..`09_CHG`), Plane-B operator/developer runbooks (`docs/*.md`), or both.
2. *Tool Selection:* Use `docs_scaffold` / `aiops-harness docs scaffold` for Plane-B scaffolding, `sdd_scaffold` for Plane-A SDD layers, `docs_validate` / `aiops-harness docs lint --strict` for AST/chunk linting, `docs_check_drift` for git drift audits, `docs_export_llms` for LLM representation, and `docs_build_portal` for Starlight+Scalar portal staging.
3. *Frontmatter & Chunk Invariant:* Enforce mandatory 9-key frontmatter (`DOC-FM-000`), <=450 words per H2/H3 section, no deictics, and explicit host:port addresses (`127.0.0.1:<port>` or `10.250.0.1:<port>`).
4. *Autonomous Execution & Verification:* Run `task check` and strict doc linting (`uv run aiops-harness docs lint --strict --json`) before reporting completion.
