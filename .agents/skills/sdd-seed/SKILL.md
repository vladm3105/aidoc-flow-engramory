---
name: sdd-seed
description: >-
  Author and validate the Seed (Tier 1): capture the cycle's starting point per version — frozen once published, totally disposed, attributed — never amended to pass an audit.
---

# SDD Seed Author

> Framework skill — routes to `.aidoc/framework/framework/governance/SEED_CONTRACT.md`
> (three rules, AI-attribution GOV-021, seed gap review gate).
> Paths below use `<skills-root>` = `.agents/skills/` (framework root: `.aidoc/framework/framework/`).
> Upstream of everything: modules (`sdd-module`) decompose the seed; the
> BRD absorbs, rejects, or defers each seed claim.

## 1. Freeze the starting point

A published seed version is immutable: once the first BRD of a cycle is
authored against seed vN, vN is historical input. A finding of the form
"the seed says X, the chain does not" resolves in the BRD, never by
amending the published version. When an assumption genuinely changes, ship
a supersede — archive vN, author vN+1 clean, link `supersedes` — authorized
by a CHG with a `seed_scope` supersede decision. New mid-cycle human input
outside the standing seed arrives through `<project>/chg/`, which already
has a gate.

## 2. Attribute every AI touch

Any document an AI agent creates or modifies in a versioned tier must carry
`document_control` + metadata naming the agent, the supervising human, the
authorizing CHG, and the change. Backfill is mandatory when the carrier is
missing. Lifecycle entries carry `author` (and `chg_ref` for modules) —
the machine-checked half (CHG-L015); carrier completeness stays with the
reviewer lens.

## 3. Seal and hand off

Run the seed gap review gate before modules begin. Next: `sdd-module`,
then `sdd-brd`. Seed-phase drafting before the first BRD is the sole
change needing neither CHG nor IPLAN — everything after it rides a vehicle.

## 4. Spoke & Agent Seed Structure and Gate Hygiene (`aiops-harness`)

When authoring `docs/seed/` in an `aiops-harness` spoke or `aiops-*` agent repository:

1. **Three-Directory Seed Layout & Cross-Repo Gap Audit:**
   - Include `docs/seed/00_index.md`, `docs/seed/architecture/00_index.md`, `docs/seed/adr/00_index.md` (locked seed decisions feeding into `docs/sdd/05_ADR/`), and `docs/seed/agent-surface/00_index.md` (MCP tool matrix, CLI sub-runners, and skills).
   - Cross-check against `$AIOPS_HARNESS_ROOT/docs/` and sibling agent repos (`aiops-agent-pm`, `aiops-agent-support`, `aiops-agent-dev`) so core harness planes are covered: (a) Charter, Role & Separation of Duties, (b) Duties & Authority Matrix (`consent_id`, Paperclip Doc 06 S6/S7 SoD gate), (c) Common Tasks & `route_preflight`, (d) Execution Flows, FSM & Two-Stage NATS JetStream Ordering (`EVENTS_PAPERCLIP`, `INBOX_AIOPS_PM`, `pr_opened.v1` before post-merge `completed.v1`), (e) 4-Bucket Platform Matrix, (f) Wire Contracts & Temporal Queue (`ExecutePaperclipTaskWorkflow`, `DeskBridgeWorker`), (g) Worktree (`[I-01]`–`[I-03]`) & `classify_path` Jail (`[I-04]`), (h) Verification Gates & `MonotonicTestGate`, (i) OpenBao `/dev/shm` (`0600`, `[I-05]`), `platform-security` Security Gate (`:8095`/`:18095`/UDS, `X-Agent-Warrant`), Paperclip v0.4.2 lease-epoch fencing (`PaperclipLeaseGuard`, `SpendTripwire`) & Dual-Fence Prompt Translation (`[I-10]`), (j) Package & Ops Guide, (k) LangGraph Cognitive Split & `LiteLLMClient` Routing (`paperclip_spend_hook.py`), (l) In-Process Kùzu Slicing (`[I-07]`, `[I-12]`) & 4-Tier Engramory Memory (`platform-engramory`), (m) Capability Profile, Dynamic Transfer & `aiops_decline_plan`, (n) Split-Forge VCS (`jj` + Gitea/GitHub) & `task -x sync` Conflict Protocol, (o) UALF Trajectory WAL & RFC 6962 Merkle Sealing, and (p) Greenfield (`SEED2C`) / Brownfield (`CODE2S`) Onboarding Playbooks.
2. **Dual-Compatible Frontmatter & Linter Rules (`docs-lint` + `sdd-seed` + `MD025`):**
   - Include top-level `REQUIRED_FRONTMATTER_KEYS` (`id`, `title`, `kind`, `domain`, `namespace`, `status`, `autonomy_tier`, `watched_paths` matching existing repo files, `last_verified`) alongside the nested `document_control:` block.
   - Start Markdown body headings at H2 (`## Overview`) so `markdownlint` `MD025` does not clash with frontmatter `title:`.
   - Keep every heading section `<= 450` words (`MAX_SECTION_WORDS`), avoid banned deictic phrases and retired tool/service names (`pre-commit`, `seaweedfs`, `minio`, `bytebase`, `platform-identity`, `platform-authz`, `aidoc-flow-support-desk`, `aidoc-flow-interlog`, `operations-assistant` — use `lefthook`, `Cloudflare R2 / OCI Object Storage / local bind mounts`, `Atlas CLI`, `platform-security` [`platform_security.authz` / `platform_security.identity`], `aiops-agent-support`, `aiops-agent-pm`), end each shard with `## Seed Claims for SDD Absorption` (natural-language bullets, zero synthetic IDs), and run `git add -N .` before `run_gate("docs-drift")` on untracked files.
3. **Thin-Spoke `Taskfile.yml` Include Hygiene:**
   - After `init-greenfield.sh --thin-spoke`, verify `Taskfile.yml` delegates both `ci` and `uts` includes via `'{{env "AIOPS_HARNESS_ROOT" | default "../aiops-harness"}}/.ci/taskfiles/root.yml'` and `'{{env "AIOPS_HARNESS_ROOT" | default "../aiops-harness"}}/.unified-test-suite/Taskfile.yml'` so `task check` and `task ci` resolve cleanly without local `.ci/` or `.unified-test-suite/` directories.
4. **Autonomous Open-Question Resolution (3 Options + 1–5 Scoring + Auto-Continue):**
   - If the agent has open questions while authoring or decomposing the Seed: (1) generate **3 distinct recommendations/options** for each question with clear rationale and trade-offs; (2) review the generated recommendations and **score each from `1` to `5`** based on **best practices** (platform standards, security, SDD/EaC compliance, maintainability) and **practical use** (operational simplicity, execution feasibility, blast radius), where a higher score is better (`5` = best), highlighting the top-scored recommendation; (3) **use the recommendation with the highest score and continue without asking an operator**.
