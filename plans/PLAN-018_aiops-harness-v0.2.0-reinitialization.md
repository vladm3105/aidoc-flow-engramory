# PLAN-018: Reinitialize `platform-engramory` Based on `aiops-harness` v0.2.0

- **Status:** Completed
- **Author:** Antigravity + Operator
- **Date:** 2026-11-09
- **Repository:** `platform-engramory`
- **Harness Baseline:** `aiops-harness` `v0.2.0` (`a5bbf52`)

---

## 1. Objective & Scope

`aiops-harness` `v0.2.0` (`a5bbf52`) consolidated the 8-Canon Hub-and-Spoke ecosystem control plane, pinning `local_ci` (`aidoc-hybrid-ci`) at `1.1.0`, `agent_canon` (`aidoc-flow-agents-config`) at `1.0.0`, `framework_adaptation` (`aidoc-flow-framework`) at `0.96.25`, `test_suite` (`aidoc-testing-framework`) at `1.0.1`, `tech_stack` at `1.0.0`, `remote_ci` at `1.1.0`, `data_contracts` at `1.0.0`, and `context_tools` (`aidoc-ctx` / `aiops_harness.context`) at `1.0.0`.

Running `aiops_onboard_audit`, `bin/onboard drift`, `bin/onboard check`, and `aidoc-local-flow-ci update --dry-run` against `platform-engramory` surfaced the following version, governance, and policy drift:

1. **8-Canon Version & Lock Drift:**
   - [`.ci/VERSION`](../.ci/VERSION) is at `1.1.0-rc.2` instead of `1.1.0` (`Claim 1`).
   - [`.aidoc/onboarding-version`](../.aidoc/onboarding-version) is at `0.1.0` instead of `0.2.0` (`Claim 2`).
   - [`.aidoc/profile.yaml`](../.aidoc/profile.yaml) still specifies `project_name: "aidoc-flow-engramory"` (`Claim 3`) and `framework_version: "0.96.4"` (`Claim 4`) instead of `project_name: "platform-engramory"` and `framework_version: "0.96.25"`.
   - [`.unified-test-suite/VERSION`](../.unified-test-suite/VERSION) is at `1.0.0` instead of `1.0.1` (`Claim 5`), and [`unified-test-suite.config.json`](../unified-test-suite.config.json) still specifies `"project": "aidoc-flow-engramory"` (`Claim 6`).
   - `.aidoc/harness.lock.json` is absent and must be cryptographically generated and attested against `canons.yaml` (`schema_version: "2.0.0"`) and `.ci/MANIFEST.sha256` (`f7297149e4980207acc8782e2c25a0259fae2c4f400c98ba2334573930689429`).
2. **Stack Policy & Engine Binding Drift:**
   - [`.aidoc/stack-policy.yaml`](../.aidoc/stack-policy.yaml) still carries `tenant_id: "aidoc-flow-engramory"` (`Claim 7`) and its `allowed_packages:` list (`Claim 8`) omits the `v0.2.0` template additions (`pyyaml`, `jsonschema`, `packaging`, `starlette`, `uvicorn`) as well as the approved runtime dependencies declared in [`pyproject.toml`](../pyproject.toml) (`neo4j`, `psycopg`, `pyyaml`, `Claim 9`), causing `aidoc-ctx lint-policy` and [`StackPolicyValidator`](../src/engramory/core/context_tools.py) (`Claim 10`) to fail with 6 disallowed-dependency errors during `bin/onboard check`.
   - [`.aidoc/engine/engine.yaml`](../.aidoc/engine/engine.yaml) binds legacy `engine: "aidoc-flow-langgraph"` (`Claim 11`) instead of `engine: "aiops-harness"` (`version: "0.2.0"`).
   - [`.aidoc/Taskfile.yml`](../.aidoc/Taskfile.yml) (`Claim 12`) and [`.aidoc/bin/sdd-lint`](../.aidoc/bin/sdd-lint) (`Claim 13`) predate the `0.96.25` / `v0.2.0` templates (`AIOPS_HARNESS_ROOT`, `AIDOC_FRAMEWORK_ROOT`, `aiops_harness.context.cli`, `check-invariants`, `lint-policy`, and `BASE` defaulting to `origin/dev`).
3. **Forbidden Legacy Governance Surfaces & Stale Agent Config:**
   - Root `CHANGELOG.md` was re-created in `PLAN-012` despite already having been archived to [`docs/archive/CHANGELOG.md`](../docs/archive/CHANGELOG.md) (`Claim 14`), causing `aiops_onboard_audit`, `drift-check.sh`, and `bin/onboard check` to fail on a forbidden legacy surface. (Note: legacy root files `DECISIONS.md`, `HANDOFF.md`, and `TODO.md` are not in `canons.yaml` `forbidden_files` and are retained out-of-scope so historical `PLAN-001`..`PLAN-004` citations remain intact.)
   - Root `CLAUDE.md` is a deprecated 218-line pre-onboarding file flagged by `aidoc-local-flow-ci update` (`AGENTS.md` is the single authoritative agent instruction file; `CLAUDE.md` is deprecated and ignored by `.ci/bin/governance-check`).
   - [`AGENTS.md`](../AGENTS.md) (`Claim 15`, `Claim 16`) carries the `1.1.0-rc.2` managed CI block and a `## Per-repo governance — where state lives` table whose `Changelog` cell (`CHANGELOG.md + docs/sdd/09_CHG/archive/`) fails `.ci/bin/governance-check` path validation and omits the `SDD Graph` row.
4. **CI Configuration, Workflows & Onboarding Docs Drift:**
   - [`.ci-config`](../.ci-config) (`Claim 17`, `Claim 18`) sets `BASE_BRANCH=main` and `SHIP_AUTONOMOUS_BRANCHES=` instead of `BASE_BRANCH=dev`, `SHIP_AUTONOMOUS_BRANCHES=dev`, `PROMOTION_AUTONOMOUS=staging`, and `REVIEW_EXCLUDE=.agents/** .unified-test-suite/**`, and lacks the 8 new `aidoc-hybrid-ci` `1.1.0` template keys (`PROMOTION_FORGE`, `PROMOTION_REMOTE`, `ISSUE_FORGE`, `MIRROR_APP_ID`, `MIRROR_GITEA_USER`, `INTEGRATION_SOURCE_CHECK`, `PROMOTION_AUTONOMOUS`, `REVIEW_EXCLUDE`).
   - [`Taskfile.yml`](../Taskfile.yml) has `EXTRA_FAST_CHECKS: ""` (`Claim 19`) instead of `EXTRA_FAST_CHECKS: "sdd:check"`.
   - [`.github/workflows/verify.yml`](../.github/workflows/verify.yml) (`Claim 20`), [`.github/workflows/bot-signoff.yml`](../.github/workflows/bot-signoff.yml) (`Claim 21`), [`.github/workflows/audit-trail.yml`](../.github/workflows/audit-trail.yml) (`Claim 22`), [`.github/workflows/quick-gates.yml`](../.github/workflows/quick-gates.yml) (`Claim 26`), [`.github/workflows/branch-promotion.yml`](../.github/workflows/branch-promotion.yml) (`Claim 27`), `.github/workflows/nightly-audit.yml`, `.github/pull_request_template.md`, `lefthook.yml`, `scripts/pre_push_guard.sh`, and `scripts/secret_scan.py` predate the `aidoc-hybrid-ci` `1.1.0` templates.
   - [`.github/workflows/ci.yml`](../.github/workflows/ci.yml) (`Claim 28`) and [`.github/workflows/standards-drift.yml`](../.github/workflows/standards-drift.yml) (`Claim 29`) use `runs-on: ubuntu-latest`, which fails the new `aidoc-hybrid-ci` `1.1.0` `universal workflows` check (`#49: Self-hosted runners only`).
   - [`docs/ONBOARDING.md`](../docs/ONBOARDING.md) (`Claim 23`) still points to the deprecated `aidoc-project-onboarding` repository instead of `aiops-harness`.

---

## 2. Implementation Phases

### Phase 1: Legacy Governance Surface Archival & `AGENTS.md` Alignment

1. **Consolidate & Archive Root `CHANGELOG.md`:**
   - Merge the `2026-11-05` (`PLAN-009`..`PLAN-012`) release notes from root `CHANGELOG.md` into [`docs/archive/CHANGELOG.md`](../docs/archive/CHANGELOG.md) (`Claim 14`) and preserve `docs/sdd/09_CHG/archive/.gitkeep` for structured CHG records.
   - Remove root `CHANGELOG.md` (`git rm CHANGELOG.md`) so `aiops_onboard_audit` and `bin/onboard check` report zero forbidden legacy governance surfaces.
2. **Retire Deprecated `CLAUDE.md` & Clean Up References:**
   - Remove deprecated root `CLAUDE.md` (`git rm CLAUDE.md`) per `aidoc-hybrid-ci` `1.1.0` (`AGENTS.md` is the single authoritative agent file).
   - Remove the `CLAUDE.md` line in `.github/CODEOWNERS` and the `CLAUDE.md` sentence at the top of [`AGENTS.md`](../AGENTS.md) (`Claim 15`).
3. **Update `AGENTS.md` Governance Table:**
   - Update `## Per-repo governance — where state lives` in [`AGENTS.md`](../AGENTS.md) (`Claim 16`) to match `templates/governance/WHERE_STATE_LIVES.snippet.md` while preserving `sdd/05_ADR/` visibility:
     - `Decisions`: `docs/sdd/05_ADR/`
     - `Plans`: `plans/`
     - `Changelog`: `Not adopted — GitHub Releases + docs/sdd/09_CHG/archive/`
     - `Roadmap`: `Not adopted — GitHub Milestones / Issues + plans/*-PLAN.md`
     - `SDD Graph`: `` `docs/sdd/graph/` (native property-graph slices; validated via `task sdd:graph-check`) ``
   - Verify non-managed line count in `AGENTS.md` remains `<= 200` lines and `.ci/bin/governance-check` exits `0` (`governance: OK — surfaces verified in AGENTS.md`).

### Phase 2: Pre-Update `.ci-config` Alignment, 8-Canon `v0.2.0` Sync & Lock Attestation

1. **Align `.ci-config` & `Taskfile.yml` Before Running `update-canons.sh`:**
   - Because `scripts/update-canons.sh` reads `BASE_BRANCH=` directly from `.ci-config` (and does not accept a `--base` CLI flag), update [`.ci-config`](../.ci-config) (`Claim 17`, `Claim 18`) first:
     - Set `BASE_BRANCH=dev`, `SHIP_AUTONOMOUS_BRANCHES=dev`, `PROMOTION_AUTONOMOUS=staging`, `REVIEW_EXCLUDE=.agents/** .unified-test-suite/**`.
     - Add the `1.1.0` template keys (`PROMOTION_FORGE=`, `PROMOTION_REMOTE=github`, `ISSUE_FORGE=`, `MIRROR_APP_ID=`, `MIRROR_GITEA_USER=`, `INTEGRATION_SOURCE_CHECK=1`) while preserving `LINT_EXCLUDE=.agents/** .unified-test-suite/**`, `POLICY_EXCLUDE=.agents/** .unified-test-suite/**`, `REVIEW=required`, and `REVIEW_MODEL=tier-frontier`.
   - Update [`Taskfile.yml`](../Taskfile.yml) (`Claim 19`): set `EXTRA_FAST_CHECKS: "sdd:check"` so `task check` runs the SDD gate automatically.
2. **Execute Upstream 8-Canon Update (`v0.2.0`):**
   - Run `bash "$AIOPS_HARNESS_ROOT/scripts/update-canons.sh" --target .` to:
     - Upgrade `.ci/` from `1.1.0-rc.2` to `1.1.0` (`Claim 1`) and refresh the `<!-- aidoc-local-flow-ci:begin -->` block in [`AGENTS.md`](../AGENTS.md).
     - Refresh `.agents/` (`1.0.0`) and the `<!-- agents-canon:begin -->` block in [`AGENTS.md`](../AGENTS.md).
     - Upgrade `.unified-test-suite/` from `1.0.0` to `1.0.1` (`Claim 5`) and `unified-test-suite.lock.json`.
     - Stamp [`.aidoc/onboarding-version`](../.aidoc/onboarding-version) to `0.2.0` (`Claim 2`) and scaffold stage-first `.aidoc/*.env.example` templates (`database.env.example`, `litellm.env.example`, `nats.env.example`, `observability.env.example`, `openbao.env.example`, `security.env.example`, `temporal.env.example`).
   - Verify `git -C "$AIOPS_HARNESS_ROOT" status --porcelain` is empty so the upstream `aiops-harness` repository remains untouched.
3. **Harmonize `.aidoc/` Profile, Engine, Taskfile, Shim & Stack Policy Contract:**
   - Update [`.aidoc/profile.yaml`](../.aidoc/profile.yaml) (`Claim 3`, `Claim 4`): set `project_name: "platform-engramory"`, `framework_version: "0.96.25"`, and `last_updated: "2026-11-09"`.
   - Update [`unified-test-suite.config.json`](../unified-test-suite.config.json) (`Claim 6`): set `"project": "platform-engramory"`.
   - Ensure all `.aidoc/*.env.example` files use `platform-engramory` as the project/service identifier.
   - Update [`.aidoc/engine/engine.yaml`](../.aidoc/engine/engine.yaml) (`Claim 11`) from `templates/aidoc/engine/engine.template.yaml`: set `engine: "aiops-harness"` (`version: "0.2.0"`, `mode: "verify-only"`).
   - Update [`.aidoc/Taskfile.yml`](../.aidoc/Taskfile.yml) (`Claim 12`) and [`.aidoc/bin/sdd-lint`](../.aidoc/bin/sdd-lint) (`Claim 13`) to the `v0.2.0` (`framework 0.96.25`) templates with `HARNESS_ROOT: '{{default "../aiops-harness" (env "AIOPS_HARNESS_ROOT")}}'`, `FRAMEWORK_ROOT: '{{default (printf "%s/.aidoc/framework" .HARNESS_ROOT) (env "AIDOC_FRAMEWORK_ROOT")}}'`, and `BASE` defaulting to `origin/dev`.
   - Update [`.aidoc/stack-policy.yaml`](../.aidoc/stack-policy.yaml) (`Claim 7`, `Claim 8`): set `tenant_id: "platform-engramory"` and include the `v0.2.0` template packages (`fastapi`, `pydantic`, `protobuf`, `platform-data-contracts`, `aidoc-context-tools`, `pyjwt`, `cryptography`, `httpx`, `pyyaml`, `jsonschema`, `packaging`, `starlette`, `uvicorn`) plus the approved runtime packages from [`pyproject.toml`](../pyproject.toml) (`neo4j>=5.26.0`, `psycopg>=3.2`, `Claim 9`) so both `aidoc-ctx lint-policy` and [`StackPolicyValidator`](../src/engramory/core/context_tools.py) (`Claim 10`) pass with 0 violations.
4. **Generate & Attest `.aidoc/harness.lock.json`:**
   - Generate `.aidoc/harness.lock.json` using `write_harness_lock` from `aiops_harness.onboarding.engine` and merge top-level `"ci_manifest_sha256": hashlib.sha256(Path(".ci/MANIFEST.sha256").read_bytes()).hexdigest()` (`f7297149e4980207acc8782e2c25a0259fae2c4f400c98ba2334573930689429`, matching `canons.yaml` `local_ci.manifest_sha256`) so both `verify_harness_lock` (`aiops_onboard_audit`) and `.ci/bin/doctor` (`OK harness.lock.json attested`) pass.

### Phase 3: Workflow & Helper Script Reconciliation (`aidoc-hybrid-ci` `1.1.0`)

1. **Synchronize `aidoc-hybrid-ci` `1.1.0` Workflows & Helper Scripts:**
   - Refresh [`.github/workflows/verify.yml`](../.github/workflows/verify.yml) (`Claim 20`, with `branches: [dev]` and `trusted_helper` from `origin/$BASE_REF:.ci/bin/verify-reuse`), [`.github/workflows/bot-signoff.yml`](../.github/workflows/bot-signoff.yml) (`Claim 21`, hardened bot login check), [`.github/workflows/audit-trail.yml`](../.github/workflows/audit-trail.yml) (`Claim 22`), [`.github/workflows/branch-promotion.yml`](../.github/workflows/branch-promotion.yml) (`Claim 27`, preserving `PROMOTION_TARGETS: none` to match empty `PROMOTION_BRANCHES=` in `.ci-config`), `.github/workflows/nightly-audit.yml` (`jdx/mise-action` `v4.3.0`), `.github/pull_request_template.md`, `lefthook.yml`, `scripts/pre_push_guard.sh`, and `scripts/secret_scan.py` from `$AIOPS_HARNESS_ROOT/.aidoc/hybrid-ci/templates/`.
   - Refresh [`.github/workflows/quick-gates.yml`](../.github/workflows/quick-gates.yml) (`Claim 26`) from `$AIOPS_HARNESS_ROOT/.aidoc/hybrid-ci/templates/github/workflows/quick-gates.yml` and append `#.agents/**` and `#.unified-test-suite/**` under `globs:` (matching `.ci-config` `LINT_EXCLUDE`) so server `quick-gates` `markdownlint` matches local `task check`.
2. **Remediate `runs-on: ubuntu-latest` in Legacy Workflows (`universal workflows` `#49`):**
   - Update [`.github/workflows/ci.yml`](../.github/workflows/ci.yml) (`Claim 28`, setting `branches: [dev, main]` and `runs-on: [self-hosted, ci, ephemeral]`) and [`.github/workflows/standards-drift.yml`](../.github/workflows/standards-drift.yml) (`Claim 29`, setting `runs-on: [self-hosted, ci, ephemeral]`) so `.ci/bin/universal` (`workflows` check `#49: Self-hosted runners only`) passes in `task check` and `task ci`.
   - Verify `bash "$AIOPS_HARNESS_ROOT/.aidoc/hybrid-ci/install.sh" --update --dry-run --target . --base dev` reports zero workflow, script, `CLAUDE.md`, or `.ci-config` key drift (aside from the intentional `LINT_EXCLUDE` globs in `quick-gates.yml` and project-tuned linter configs).

### Phase 4: SDD `v0.2.0` Structure, Onboarding Guide & `llms.txt` Refresh

1. **Scaffold SDD `v0.2.0` Directories & Refresh `docs/ONBOARDING.md`:**
   - Ensure `docs/sdd/SEED/.gitkeep`, `docs/sdd/MODULE/.gitkeep`, `docs/sdd/05_ADR`, `docs/sdd/09_CHG/archive`, and `docs/sdd/graph` exist per `scripts/adapt-brownfield.sh`.
   - Replace [`docs/ONBOARDING.md`](../docs/ONBOARDING.md) (`Claim 23`) with `$AIOPS_HARNESS_ROOT/templates/docs/ONBOARDING.template.md` referencing `aiops-harness` (`docs/CLI_AND_MCP_RUNBOOK.md`).
2. **Regenerate `llms.txt` Documentation Index:**
   - Run `uv run engramory docs export-llms --repo-path . --write` ([`_cmd_docs_export_llms`](../src/engramory/cli.py), `Claim 24`) and remove untracked `llms-full.txt` (`rm -f llms-full.txt`) so [`llms.txt`](../llms.txt) (`Claim 25`) reflects the updated `AGENTS.md`, `docs/ONBOARDING.md`, and archived `docs/sdd/09_CHG/archive/` files, keeping `tests/test_docs_tools.py::test_llms_txt_matches_repo` green.

### Phase 5: 9-Gate Verification Battery & Audit Attestation

1. **Run `aiops-harness` `v0.2.0` Audits:**
   - Run MCP `aiops_onboard_audit` (`verify_harness_lock` / `check_drift`) -> verify `status: "PASS"`, `drifted: false`.
   - Run `"$AIOPS_HARNESS_ROOT/bin/onboard" drift --target .` -> verify `AUDIT CLEAN: Target repository has zero drift from canons.yaml`.
   - Run `"$AIOPS_HARNESS_ROOT/bin/onboard" check --target .` -> verify `AUDIT SUCCESS: Target repository fully conforms to the 8-canon standards`.
2. **Execute All 9 Mandatory Project Quality Gates & Re-Verify Plan Ledger:**
   - `task doctor` (`doctor: OK`, `.ci integrity 1.1.0`, `harness.lock.json attested`, `governance surfaces verified (AGENTS.md)`)
   - `task uts:check` (`OK`)
   - `task sdd:profile-lint` (`PASS`)
   - `task sdd:check` (`PASS`)
   - `task sdd:graph-check` (`PASS`)
   - `task sdd:lint-policy`, `uv run engramory sdd lint-policy --root .`, and `uv run engramory docs lint .` (`PASS`)
   - `task stack:audit` (`PASS`)
   - `python3 .agents/skills/verified-planning/check_plan.py --fix plans/PLAN-018_aiops-harness-v0.2.0-reinitialization.md` (`ok`)
   - `task check` (`RESULT: pass`)
   - `task ci` (`RESULT: pass` across all universal and service checks)

---

## Claim ledger

| # | Claim | Symbol | Citation |
| --- | --- | --- | --- |
| 1 | Vendored local CI engine version file in `.ci/VERSION` | `1.1.0` | .ci/VERSION:1 |
| 2 | Onboarding release marker file in `.aidoc/onboarding-version` | `0.` | .aidoc/onboarding-version:1 |
| 3 | Adaptation profile declares `project_name` metadata field | `project_name:` | .aidoc/profile.yaml:21 |
| 4 | Adaptation profile declares `framework_version` metadata field | `framework_version:` | .aidoc/profile.yaml:22 |
| 5 | Unified Test Suite version file in `.unified-test-suite/VERSION` | `1.0.` | .unified-test-suite/VERSION:1 |
| 6 | Unified Test Suite config declares `"project"` identifier | `"project":` | unified-test-suite.config.json:3 |
| 7 | Stack policy contract declares `tenant_id` | `tenant_id:` | .aidoc/stack-policy.yaml:17 |
| 8 | Stack policy contract declares `allowed_packages:` under `dependencies` | `allowed_packages:` | .aidoc/stack-policy.yaml:48 |
| 9 | Project dependencies list in `pyproject.toml` includes `neo4j`, `psycopg`, and `pyyaml` | `dependencies = [` | pyproject.toml:11 |
| 10 | `StackPolicyValidator` validates codebase and lockfiles against `.aidoc/stack-policy.yaml` | `class StackPolicyValidator:` | src/engramory/core/context_tools.py:1360 |
| 11 | Technical engine binding config declares `engine:` in `.aidoc/engine/engine.yaml` | `engine:` | .aidoc/engine/engine.yaml:12 |
| 12 | Canonical SDD Taskfile governs SDD lifecycle execution and linting | `Governs: SDD lifecycle execution` | .aidoc/Taskfile.yml:5 |
| 13 | SDD linter shim `.aidoc/bin/sdd-lint` invokes `sdd_doc_lint` | `sdd_doc_lint` | .aidoc/bin/sdd-lint:10 |
| 14 | Archived changelog in `docs/archive/CHANGELOG.md` | `# Changelog` | docs/archive/CHANGELOG.md:1 |
| 15 | `AGENTS.md` defines the working agreement for AI coding agents on Engramory | `# AGENTS.md` | AGENTS.md:1 |
| 16 | `AGENTS.md` defines the repository architecture and governance conventions | `## What Engramory is` | AGENTS.md:6 |
| 17 | `.ci-config` declares the integration `BASE_BRANCH` setting | `BASE_BRANCH=` | .ci-config:12 |
| 18 | `.ci-config` header declares consumer-owned `KEY=VALUE` configuration | `# aidoc-` | .ci-config:1 |
| 19 | Root `Taskfile.yml` declares `EXTRA_FAST_CHECKS` under `ci` vars | `EXTRA_FAST_CHECKS:` | Taskfile.yml:17 |
| 20 | `.github/workflows/verify.yml` defines the server-side `verify` workflow | `name: verify` | .github/workflows/verify.yml:16 |
| 21 | `.github/workflows/bot-signoff.yml` defines the `Bot signoff` workflow | `name: Bot signoff` | .github/workflows/bot-signoff.yml:10 |
| 22 | `.github/workflows/audit-trail.yml` defines the `audit-trail` workflow | `audit-trail` | .github/workflows/audit-trail.yml:1 |
| 23 | `docs/ONBOARDING.md` provides project onboarding and ecosystem guidance | `# Project Onboarding & Ecosystem Guidance` | docs/ONBOARDING.md:1 |
| 24 | CLI command `_cmd_docs_export_llms` exports `llms.txt` | `def _cmd_docs_export_llms(` | src/engramory/cli.py:1235 |
| 25 | `llms.txt` indexes repository documentation for AI agents | `# platform-engramory` | llms.txt:1 |
| 26 | `NEW@pass2` `.github/workflows/quick-gates.yml` runs the `markdownlint` step | `- name: markdownlint` | .github/workflows/quick-gates.yml:136 |
| 27 | `NEW@pass2` `.github/workflows/branch-promotion.yml` declares `PROMOTION_TARGETS` | `PROMOTION_TARGETS:` | .github/workflows/branch-promotion.yml:15 |
| 28 | `NEW@pass2` `.github/workflows/ci.yml` defines the `CI` workflow `runs-on` runner | `runs-on:` | .github/workflows/ci.yml:10 |
| 29 | `NEW@pass2` `.github/workflows/standards-drift.yml` defines the `standards-drift` workflow `runs-on` runner | `runs-on:` | .github/workflows/standards-drift.yml:15 |

---

## Review log

### Pass 1 - 2026-11-09

Author self-review of `PLAN-018_aiops-harness-v0.2.0-reinitialization.md` against `aiops-harness` `v0.2.0` (`a5bbf52`) and `platform-engramory` (`7a2aa0e`). Verified all 25 initial Claim ledger citations against live files in the `platform-engramory` repository root and cross-checked drift against `aiops_onboard_audit`, `bin/onboard drift`, `bin/onboard check`, and `aidoc-local-flow-ci update --dry-run`. Dispatched fresh-context multi-lens review subagent for Pass 2.

### Pass 2 - 2026-11-09 - independent

Dispatched fresh-context `research` subagent (`0d26b23a-a852-4cd2-995f-7a7703f40d79`) applying Google (Design Doc Invariants & SRE Reliability), AWS (Well-Architected Idempotency/SAGA & EaC Convergence), and Microsoft (Defense-in-Depth, Schema Compatibility & CI Gates) lenses. Folded all 9 findings (2 Critical, 4 Major, 3 Medium) and added `NEW@pass2` Claim ledger rows 26–29:

1. **[Critical — Microsoft CI Gates]** Added Phase 3 Step 2 remediation for `runs-on: ubuntu-latest` in `.github/workflows/ci.yml:10` (`Claim 28`) and `.github/workflows/standards-drift.yml:15` (`Claim 29`) -> `runs-on: [self-hosted, ci, ephemeral]` so `aidoc-hybrid-ci` `1.1.0` `universal workflows` (`#49: Self-hosted runners only`) passes in `task check` and `task ci`.
2. **[Critical — AWS/Microsoft Lock Attestation]** Corrected `.aidoc/harness.lock.json` top-level `ci_manifest_sha256` in Phase 2 Step 4 to compute `hashlib.sha256(Path(".ci/MANIFEST.sha256").read_bytes()).hexdigest()` (`f7297149e4980207acc8782e2c25a0259fae2c4f400c98ba2334573930689429`, matching `canons.yaml` `local_ci.manifest_sha256` for `1.1.0`) so `task doctor` attests the lockfile cleanly.
3. **[Major — AWS Script Contract]** Moved `.ci-config` (`BASE_BRANCH=dev`) and `Taskfile.yml` updates to Phase 2 Step 1 **before** invoking `scripts/update-canons.sh`, and removed the unsupported `--base dev` flag from `bash "$AIOPS_HARNESS_ROOT/scripts/update-canons.sh" --target .`.
4. **[Major — Google Claim Ledger Stability]** Anchored Claims 15, 16, 18, and 22 to invariant top-of-file symbols and added `check_plan.py --fix` in Phase 5 Step 2.
5. **[Major — Microsoft Workflow Parity]** Specified appending `#.agents/**` and `#.unified-test-suite/**` under `globs:` in `.github/workflows/quick-gates.yml` (`Claim 26`) and preserving `PROMOTION_TARGETS: none` in `.github/workflows/branch-promotion.yml` (`Claim 27`).
6. **[Major — Google/Microsoft CLI Contract]** Updated Phase 4 Step 2 to `uv run engramory docs export-llms --repo-path . --write` (plus `rm -f llms-full.txt`) and Phase 5 Step 2 to `uv run engramory sdd lint-policy --root .`.
7. **[Medium — Google/AWS/Microsoft Hygiene]** Clarified `sdd/` vs `docs/sdd/` handling, added `uv run engramory docs lint .` to Phase 5 Step 2, and documented out-of-scope retention of root `DECISIONS.md`, `HANDOFF.md`, and `TODO.md`.

### Pass 3 - 2026-11-09 - independent

Dispatched fresh-context `research` subagent (`d44cbc76-f057-41da-84fe-a6e28465b930`) across Google, Anthropic, AWS, and Microsoft review lenses. Verified all 29 Claim ledger citations, confirmed all 9 Pass 2 findings were completely resolved, and verified `.ci/bin/universal workflows` (`1.1.0`) against `.github/workflows/*.yml`. Zero remaining findings (0 Critical, 0 Major, 0 Medium, 0 Low); no new findings.

**Result:** ready
