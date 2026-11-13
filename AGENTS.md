# AGENTS.md — working agreement for AI coding agents on Engramory

This file orients any AI agent (Claude Code, Codex, Hermes, custom) working on this repo.

## What Engramory is

The shared memory + knowledge core for our AI projects (aidoc-flow, iplanic, aidoc-flow-operations, ...).
It provides L0 knowledge base + L1 short-term + L2 long-term distilled + L3 per-agent memory, exposed through thin faces over one access surface (ADR-10): the `engramory` CLI (dev/CI face) and the MCP gateway (authenticated production face).

## Architecture you must respect

- **Ports & adapters.** Import from `engramory.ports`. Never call a vendor SDK or a concrete adapter from core/business code.
- **Postgres is canonical.** Distilled memory = plain rows (`content_raw` + `provenance` + embedding). The graph (Neo4j) and embeddings are **rebuildable projections**, not sources of truth.
- **One model gateway.** All LLM/embedding calls go through the LiteLLM endpoint (`LITELLM_BASE_URL`). Dev = Ollama; cloud = Vertex/Azure by config.
- **Migratable memory.** Always persist the raw text + embedding model + dims so memory can be re-embedded after a model/platform change.

## Conventions

- Language: Python 3.12. Lint/format: ruff. Tests: pytest.
- Config over code: projects/domains are YAML under `config/domains/`.
- Secrets only via `SecretsPort` / `.env` — never hardcode.

## Where things live

- `src/engramory/ports/` — interfaces the core depends on.
- `src/engramory/adapters/` — per-environment implementations (dev/gcp/azure).
- `src/engramory/access/` — `AccessSurface`, default-deny authorization (`authz.py`), OIDC JWT validation (`jwt_auth.py`), and poisoning guard (`poisoning_guard.py`).
- `src/engramory/core/` — knowledge + memory logic, canonical `Repository`, SDD context tools, and doc tools.
- `src/engramory/mcp/` — MCP gateway exposing tools to agents.
- `src/engramory/workers/` — reflection + consolidation (the distillation loop).
- `src/engramory/cli.py` — thin `engramory` CLI transport over `AccessSurface`.
- `db/migrations/` — SQL schema.
- `config/` — per-project / per-domain YAML config on the shared core.
- `docs/sdd/` — SDD lifecycle artifacts (BRD→IPLAN→CHG); `docs/sdd/05_ADR/` = canonical implementing ADRs.
- `plans/` — verified implementation plans (`PLAN-001`..`PLAN-024`).
- `docs/` — architecture, memory design, conceptual ADRs (`docs/adr/`), research.

## Before you finish

Run `task check` and `task ci`. Record non-trivial design choices as an ADR: canonical
implementing decisions go in `docs/sdd/05_ADR/`; conceptual/descriptive records live
in `docs/adr/`.

<!-- agents-canon:begin -->
<!-- managed by aiops-harness (aidoc-flow-agents-config) — edits inside this block are overwritten on deploy -->
## Agent working agreement (aiops-harness / aidoc-flow-agents-config)

This repository follows canonical AI agent rules, skills, and governance deployed from **aiops-harness** (`$AIOPS_HARNESS_ROOT/.agents`, consolidating `aidoc-flow-agents-config`). `AGENTS.md` is the single authoritative agent instruction file (`CLAUDE.md` is deprecated and forbidden).

### Operating rules

- **Instruction discovery:** Read the applicable `AGENTS.md` and `.agents/rules/` before editing. More specific rules take precedence. Reusable workflows live in `.agents/skills/`.
- **Worktree state:** Inspect worktree state before editing (`git status`). Never silently stage or overwrite unrelated changes.
- **Git & branch safety:** Never commit or push directly to the default branch (`main` or `dev`). Always verify with `git branch --show-current` before the first write, before every commit, and before every push.
- **Minimal edits:** Make the smallest defensible change. Read source before changing it. Verify assumptions against authoritative files.
- **Verification:** Run the narrowest relevant check after every edit, then full gate (`task check` / `task ci`) before reporting completion. Never claim success without read-back or state verification.
- **Durable records:** Keep factual claims, test evidence, and architecture decisions in machine-verifiable files (plans, ADRs, test results).
- **Subagent delegation:** Delegate independent, bounded, read-heavy analysis to custom agents (`.agents/agents/`). Read back at least 3 specific changes after any write.
- **External actions:** Use approval gates before deleting, overwriting, force-pushing, or publishing outside the workspace. Never self-approve.
<!-- agents-canon:end -->

<!-- aidoc-local-flow-ci:begin -->
<!-- managed by aidoc-local-flow-ci 1.1.0 — edits inside this block are overwritten by .ci/bin/update -->
## CI protocol (aiops-harness / aidoc-hybrid-ci)

This repository uses the **aiops-harness** hybrid CI framework (`$AIOPS_HARNESS_ROOT/.ci`, consolidating `aidoc-hybrid-ci`). Commands, output format and
rules are identical in every repository and every language. Follow this protocol exactly.

### Commands

| Command | When |
|---|---|
| `task doctor` | First thing in a new repo or worktree. Fix every `FAIL` before coding. |
| `task --list` | Discover tasks. Service tasks are named `<service>:<task>`. |
| `task fmt` | Auto-fix formatting (code, whitespace, final newlines, Markdown). Run freely. |
| `task check` | Fast gate (format, lint, types, Markdown/YAML/shell/workflow lint). After every meaningful edit. |
| `task <service>:test -- <filter>` | Targeted tests while iterating. |
| `task ci` | Full gate. Must end with `RESULT: pass` before you report work as done. |
| `task review -- --issue <N>` | Self-review: Pass 1 (`task ci`) + Pass 2 (independent reviewer) on your committed change. |
| `task ship` | Human-approved. Request it only after `task ci` (and `task review`, if required) pass. |
| `task autoship` | Only where `.ci-config` lists the base branch in `SHIP_AUTONOMOUS_BRANCHES`: ship + auto-merge. |
| `task sync` | Update your branch with the base branch; resolves conflicts per the conflict protocol below. |
| `task -x pr-status -- --watch` | Poll the forge's view of your PR until it merges (github: re-arms auto-merge; gitea: merges once checks are green). Exit 10 = conflicts → `task sync`; exit 12 = a human owns the PR (conflict:human) → stop. |
| `task cleanup -- <branch>` | After the PR is merged: remove worktree and branches (run from the main checkout). |
| `task promote -- <branch>` | Human-approved promotion to staging/main (requires server `verify` green; never local CI). |

### Reading results

- `.ci-out/summary.json` — machine-readable result of the last `check`/`ci` run:
  `status`, `failed[]`, `warnings[]`, and `checks[]` with `status` (`pass`/`fail`/`timeout`/`warn`/`cached`/`skipped`) and `log`.
  `warn` = a report-only check failed: it does not block, but mention it in your report.
- `.ci-out/logs/<service>__<task>.log` — full output of one check.
- Read these files instead of re-running commands to see output. `cached` means inputs are unchanged since the last pass.
- Exit codes named in this protocol are raw codes: read them with `task -x <task>` (`--exit-code`); plain `task` reports 201 for any failure.

### Work loop

1. Edit → `task check` → fix.
2. Run targeted tests for the area you changed. Add or update tests for every behaviour change.
3. `task ci` → on failure: read `summary.json`, open the failing log, fix the **root cause**, re-run.
4. If the same check still fails after **3 genuine fix attempts**: stop. Report the failing check,
   the relevant log excerpt, your hypotheses and what you tried. Do not keep guessing.
5. Author audit & self-review: run `author-self-review` skill + `task check` before committing.
   When `.ci-config` has `REVIEW=required`: commit, then `task review -- --issue <N>` (or
   `--requirements <file>`). Pass 0 runs static heuristics before Pass 1/2. Reviewer is independent:
   never paste reasoning into requirements. `REVISE` → fix & review; `BLOCK` → stop & report.
   Otherwise, if `AUDIT_TRAIL=required`: pushed commits need `Multi-agent self-review per OPS-0065
   (<agents>): <verdict summary>`. Never write founder-override (`Self-review skipped per founder OK`)
   unless the human explicitly authorized it in this session. Read status: `task review -- --status`.
6. Done = `task ci` passes (and `task review` is PASS when required). Report "ready to ship" with:
   what changed, why, tests added, and any `warnings`. Then request `task ship` (a human approves it),
   or run `task autoship` where autonomous merging is allowed. After the merge: `task cleanup -- <branch>`.

### Merge conflicts ("This branch has conflicts that must be resolved")

You resolve conflicts on your own feature branch without a human, within the limits of
the conflict protocol (canon `docs/CONFLICT_RESOLUTION.md`:
`https://github.com/vladm3105/aidoc-hybrid-ci/blob/main/docs/CONFLICT_RESOLUTION.md` —
this repo vendors only `.ci/`, not the canon docs). Merge-only: never rebase pushed
commits, never force-push.

1. Before `ship`/`autoship`, and whenever `task -x pr-status` exits 10: `task -x sync -- --check`, then `task -x sync`.
2. Exit 0 → merged (class A clean / class B lockfile or changelog handled automatically). Continue at step 5.
3. Exit 20 → class C files await you. `task sync -- --brief` shows what the base side changed and why.
   Edit each file so **both** intents survive, remove every marker, `git add` it, then
   `task sync -- --continue`. Never take a side wholesale (`checkout/restore --ours/--theirs`,
   `merge -X ours/theirs`, `-s ours`). If one side is genuinely superseded, keep the other and pass
   the reason: `task sync -- --continue -m "<why>"`.
4. Exit 30 → escalated (CI/governance config, migrations, delete/rename/binary/submodule, too
   many files or attempts). The merge was aborted; the PR is labelled `conflict:human`. Stop and report.
5. `task ci`, then `task review -- --issue <N>` again (the diff changed; the reviewer sees the
   conflict record), then `task ship`/`task autoship`. Ship refuses while the branch conflicts.

### Hard rules

- Always follow platform standards based on the approved technology stack (`technology-stack` canon; local checkout at `$TECHNOLOGY_STACK_ROOT`).
  Do not offer or use insecure, deprecated, or unapproved options (such as open public ports without
  NetBird mesh / Cloudflare tunnels, unauthenticated Docker socket mounts without `docker-socket-proxy`,
  un-sandboxed untrusted execution outside gVisor `runsc`, or in-memory mock databases instead of real PostgreSQL).
  All templates and tooling must be verified against that technology stack checkout. If a required tool or
  library is missing from the technology stack, you MUST explicitly halt, inform the operator, and request
  instructions on what tool or software to use before proceeding.
- Never bypass hooks or checks: no `--no-verify`, `git commit -n`, `LEFTHOOK=0`, `SKIP=`,
  `core.hooksPath` changes, `ALLOW_TEST_DELETION`, or changing `CI_BASE_BRANCH` / `GITLEAKS_LOG_OPTS`.
- Never edit `.ci/` (vendored framework, integrity-checked by `task doctor`).
- Never weaken checks: do not edit `Taskfile.yml`, `.ci-config`, `.ci-local/`, `lefthook.yml`, `mise.toml`,
  `.github/`, `AGENTS.md`, linter/type/coverage configs or thresholds unless the task explicitly asks for it.
- Use only tools from the approved technology stack (`$TECHNOLOGY_STACK_ROOT`,
  authoritative list: `09-authoritative-platform-technology-stack.md`). Never offer or use insecure
  options. If a needed tool is missing from the stack, stop and ask the operator what tool or
  software to use — never substitute an unapproved tool on your own. In autonomous sessions,
  always file a GitHub issue on the repo recording the missing tool and the blocked work.
- Suppressions (`noqa`, `type: ignore`, `nolint`, `#[allow]`, `biome-ignore`, `@ts-ignore`, `nosec`, …)
  only as a last resort and always with an inline `reason: <why>` on the same line. Prefer fixing the code.
- Never delete, skip or `.only` tests to get green. A skip needs an inline `reason:`.
- Change dependencies only through the package manager (`uv add`, `cargo add`, `go get`, `pnpm add`)
  and commit the updated lockfile.
- Never commit to the base branch. Never post merge statuses yourself (`signoff` via `gh signoff` or
  `gh api …/statuses`, `review`), `gh pr merge`, `gh pr review --approve`. Never touch `.git/aidoc-ci/`
  (review/sync state), and never run `task review -- --reset` or `task sync -- --reset` (human decisions).
  Carve-out: `task pr-status -- --watch` may re-arm auto-merge (github) or merge on
  observed green (gitea) once the required checks are green — you still never merge by hand.
- Never force-push (`--force`, `--force-with-lease`, `+refspec`) or rewrite pushed history.
- Never write real credentials anywhere in the repository.
- Name branches `feature/<topic>` or `fix/<topic>`: integration PRs are accepted only from `feature/*`,
  `fix/*`, `dependabot/*`, `renovate/*` and the `staging` back-merge (`chore/`, `docs/`, `ci/` are refused;
  `task ship` and `task review` check it before pushing).
- When working in parallel with other agents, use your own git worktree
  (`git worktree add ../<repo>-<task> -b feature/<task>`), then run `task setup` there.
<!-- aidoc-local-flow-ci:end -->

## Per-repo governance — where state lives

| Surface | Path |
|---|---|
| Handoff | Not adopted — GitHub issues + `plans/NAME-PLAN.md` |
| TODO / backlog | Not adopted — GitHub issues |
| Decisions | docs/sdd/05_ADR/ |
| Plans | plans/ |
| Changelog | Not adopted — GitHub Releases + docs/sdd/09_CHG/archive/ |
| Roadmap | Not adopted — GitHub Milestones / Issues + `plans/*-PLAN.md` |
| SDD Graph | `docs/sdd/graph/` (native property-graph slices; validated via `task sdd:graph-check`) |
