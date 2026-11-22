# Session Handoff — `PLAN-025` Paperclip `v0.5.0` & Centralized Backlog / Audit Governance

- **Updated:** 2026-11-21T10:25:00-08:00
- **Coordinated Master Plan:** `aidoc-flow-engramory/plans/PLAN-025_paperclip-centralized-backlog-and-audit-governance.md`
- **Status:** Superseded (2026-11-21) — Tracks 1A & 1B merged on dev; Track 2 superseded by aiops-harness PLAN-053 (f827f0c); Track 3 declined in favor of plans/PLAN-026_v030-deep-audit-remediation.md (CHG-010..CHG-013). Do not execute §4/§5 below.

---

## 1. Repository State & Verified Remote Parity

| Track / Role | Repository | Active Branch | Verified HEAD / Parity | Notes |
|---|---|---|---|---|
| **Master Plan (Merged)** | `aidoc-flow-engramory` | `dev` (`status: clean`) | Local `dev` == `origin/dev` == `gitea/dev` @ `db9b1b2fffada1229562ddbae11c713ebc073b3b` | PR #96 merged (`a484668`), `PLAN-025` landed on `dev`; `feature/plan-025-paperclip-backlog-and-audit-governance` deleted locally & on `origin` |
| **Track 1A (Done & Merged)** | `platform-paperclip` | `dev` (`status: clean`) | Local `dev` == `origin/dev` == `gitea/dev` @ `48dba5a380f77d09b7650658a9b78fb1688b65f2` | PR #2 merged (`99c59cd`), feature branch deleted locally & remotely; Gitea `https://gitea.int.aiops-flow.io/vladm3105/platform-paperclip` (`private`, `default_branch: dev`) |
| **Track 1B (Done & Merged)** | `platform-stage-ops` | `dev` (`status: clean`) | Local `dev` == `origin/dev` == `gitea/dev` @ `6fd88b2263ddf3b5450fbd2e6aeb6bb3a1a4e94e` (`origin/main` == `gitea/main` @ `4d554bad131403c730e929320b0fc1df265fbc4d`) | PR #56 merged (`2256c20`, `PLAT-054`), feature branch deleted locally & remotely; Gitea `https://gitea.int.aiops-flow.io/vladm3105/platform-stage-ops` (`private`, `default_branch: dev`) |
| **Track 2 (Next)** | `aiops-harness` | `dev` | `dev` @ `c1cbcb9` (`[origin/dev]`, pre-existing untracked `.agents/plugins/authentik-auth/`) | Already landed `PLAN-050`/`CHG-28` and `PLAN-051`/`CHG-29`; stale worktree `aiops-harness-plan050` (`feature/plan-050-audit-and-backlog-governance` @ `244b1d2`) exists |

---

## 2. Verification Summary Across Completed Tracks

- **Master Plan (`aidoc-flow-engramory/plans/PLAN-025_paperclip-centralized-backlog-and-audit-governance.md`, `db9b1b2`):**
  - `verified-planning-reviewer`: 3 full + 1 targeted fresh-context multi-lens passes (0 remaining open findings).
  - `check_plan.py`: `verified 71 citation(s), 5 review pass(es)` (exit `0`).
  - Zero `MD010` hard tabs, zero `MD038` code-span spaces, `.ci/bin/policy`: `OK`, `.ci/bin/universal hygiene`: `OK`, `git diff --check`: exit `0`.
- **Track 1A (`platform-paperclip`, pre-merge SHA `99c59cd`, merged as `48dba5a`):**
  - `uv run --extra dev pytest -v`: `21 passed, 5 skipped` (the 5 skipped tests are live Docker `:13100` E2E tests in `tests/test_dev_docker_e2e.py`; includes AST verification `test_server_sql_execute_calls_use_only_static_string_literals` confirming pure static SQL constants across all 18 `cur.execute` sites in `client/server.py`).
  - `check_plan.py` (`plans/PLAN-005_paperclip-v050-centralized-backlog.md`): `verified 43 citation(s), 4 review pass(es)` (exit `0`).
  - `second-opinion-judge` (Pass 5 on `c605595`): `Verdict: pass`, `Confidence: high`.
- **Track 1B (`platform-stage-ops`, pre-merge SHA `2256c20`, merged as `6fd88b2`):**
  - Offline stdlib suite (`test_stage_ops_paperclip` `11/11`, `test_stage_ops_governance` `2/2`, `test_stage_ops_inventory` `3/3`, filtered offline `test_stage_ops_manifest` `4/4`, filtered `test_stage_ops_sso` `4/4`): `24/24` passed.
  - Mirrored Python runtime `pytest` (`PYTHONPATH=stacks/platform-paperclip`): `21 passed`.
  - `check_plan.py` (`plans/PLAN-005-PAPERCLIP-CENTRALIZED-BACKLOG-AND-GITEA-BRIDGE.md`): `verified 33 citation(s), 4 review pass(es)` (exit `0`, `0` warnings).
  - `.ci/bin/policy`: `policy: OK`; `.ci/bin/universal hygiene`: `hygiene: OK (29 text files)`; `git diff --check`: exit `0`.
  - `second-opinion-judge` (Pass 4 on `e0f4411` prior to CI static-SQL/`MD038` polish): `Verdict: pass`, `Confidence: high`.
  - GitHub Actions on PR #56 (`2256c20`): **`verify` (`pass`, all 15 `task ci-full` checks incl. `sast` & `markdown`), `quick-gates` (`pass`), `audit-trail` (`pass`), `verify-source` (`pass`), `verify-integration-source` (`pass`)**, `mergeStateStatus: CLEAN`.

---

## 3. Stage Operator Preconditions Before Live `tofu apply`

Live Stage host (`platform-stage-ops/tofu/stage-config-management/main.tf`) was **not** applied yet. Before running `task tofu:config-apply` / `tofu apply` on `stage`:

1. **Tier-1 OCI Vault:** Pre-seed `stage-PAPERCLIP_PROXY_AUTH_YAML` (Traefik dynamic middleware YAML setting `X-Paperclip-Proxy-Auth`) and `stage-PAPERCLIP_API_ENV` (strict unquoted Docker `env_file` syntax `PAPERCLIP_API_TOKEN=<token>`, no `export` prefix). Note: `host/bin/platform-secrets-render.py` fails closed on missing Vault entries and exits `1` without overwriting if an existing host RAM file differs from Vault (on rotation, remove the stale RAM file, re-run `platform-secrets-render.py`, and restart consuming stacks).
2. **Tier-2 OpenBao KV Cross-Store Parity (`kv/stage/paperclip/app`):** Pre-seed matching `PAPERCLIP_PROXY_AUTH_SECRET`, matching `PAPERCLIP_API_TOKEN`, and `PAPERCLIP_WEBHOOK_HMAC_SECRET`, and ensure `kv/stage/litellm-laya/proxy` omits any stale `PAPERCLIP_API_TOKEN`.
3. **PG18 Migration Sequence on `stage`:** `tofu/stage-config-management/main.tf` runs `/usr/local/bin/platform-pg-backup.sh diff` and executes `stacks/platform-paperclip/migrations/002_backlog_v050.sql` via `stage_bootstrap` before restarting `platform-paperclip` and downstream consumers (`platform-edge-gateway`, `platform-litellm`, `platform-temporal`, and active tenant fleets).

---

## 4. Remaining Tracks & Exact Next Actions

### Track 2 — `$AIDOC_WORKSPACE/aiops-harness`

- **Numbering update verified live:** `aiops-harness` `dev` (`c1cbcb9`) already contains `PLAN-050`/`CHG-28` and `PLAN-051`/`CHG-29`.
- **Action:**
  1. Leave pre-existing untracked `.agents/plugins/` untouched; cleanly remove stale worktree `aiops-harness-plan050` (`git worktree remove` before `git branch -D`) and create a fresh worktree off `dev` (`c1cbcb9`) on `feature/plan-052-audit-and-backlog-governance`.
  2. Author and independently review `PLAN-052` / `CHG-30` / `IPLAN-30` / `SLICE-30` via the full `aiops-harness` `verified-planning` pipeline (`check_plan.py --v2`, up to 3 independent reviewer passes).
  3. Implement Track 2 per `PLAN-025`: reconcile `.agents/rules/audits-and-backlog.md`, skills, `ADR-0004`, and `AGENTS.md` (`<= 200` unmanaged lines); ship `<5ms` OpenBao fast-fail, `PAPERCLIP_API_URL`/`PAPERCLIP_URL`, extended `PaperclipClient` + fail-closed `401`/`403`/`409`/`AIOPS_MULTI_INSTANCE=1`, `docs/audits/` exemptions (`docs_tools.py` + `portal_builder.py`), `audit-disposition` gate + `check_audit.py`, signed MCP/CLI tool parity, and SDD 3-way parity (`aidoc_validate_graph` + `aiops_iplan_seal_evidence` / `aiops_sdd_seal`).
  4. Verify with `task ci`, independent Pre-CI review, push, PR/merge to `dev`, and sync `gitea` + `origin`.

### Track 3 — `$AIDOC_WORKSPACE/aidoc-flow-engramory`

- Executed after Track 2 lands (`PLAN-025` is already merged on `dev` @ `db9b1b2`; branch off `dev` for `CHG-010` / `IPLAN-10`):
  1. Create a feature branch off `dev` (`db9b1b2`).
  2. Update `AGENTS.md` governance table (`TODO / backlog`, `Audits`, stale path fixes), add `docs/audits/` (`kind: audit`) exemptions across `src/engramory/cli.py`, `src/engramory/core/docs_tools.py`, and `src/engramory/core/portal_builder.py` + unit tests.
  3. `git mv` + redact + wave-split `docs/audits/AUDIT-2026-11-20_deep-review.md`, ingest the 1 Root (`$60.00`) + 40 subtasks (`$57.00 <= $60.00`) into Paperclip with `## Disposition ledger` sealed, and run `docs lint --strict`, `audit-disposition`, and `task ci`.

---

## 5. Copy-Paste Prompt for Next Session

```text
Continue executing PLAN-025 (aidoc-flow-engramory/plans/PLAN-025_paperclip-centralized-backlog-and-audit-governance.md). Read aidoc-flow-engramory/plans/HANDOFF-PLAN-025_paperclip-backlog-and-audit-governance.md first for full verified state.

Completed & merged into dev with GitHub + Gitea SHA parity:
- Master Plan (aidoc-flow-engramory): dev @ db9b1b2fffada1229562ddbae11c713ebc073b3b (PR #96 merged, Gitea + origin synced).
- Track 1A (platform-paperclip): dev @ 48dba5a380f77d09b7650658a9b78fb1688b65f2 (PR #2 merged, Gitea + origin synced).
- Track 1B (platform-stage-ops): dev @ 6fd88b2263ddf3b5450fbd2e6aeb6bb3a1a4e94e (PR #56 merged, PLAT-054, Gitea + origin synced).

Now execute Track 2 in $AIDOC_WORKSPACE/aiops-harness:
- Note: aiops-harness dev is at c1cbcb9 (already contains PLAN-050/CHG-28 and PLAN-051/CHG-29; leave untracked .agents/plugins/ untouched).
- Cleanly retire stale worktree aiops-harness-plan050 (244b1d2) and create a fresh worktree off dev (c1cbcb9) on feature/plan-052-audit-and-backlog-governance.
- Author and independently review PLAN-052 / CHG-30 / IPLAN-30 / SLICE-30 via the full aiops-harness verified-planning pipeline, then implement, verify, and ship Track 2 before moving to Track 3 (aidoc-flow-engramory).
```
