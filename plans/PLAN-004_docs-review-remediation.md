# PLAN-004 — Documentation review remediation: restore the agent-facing contract, re-sync the governance layer, close the security-doc gap

**Status:** DRAFT — ready for execution (Pass 2 independent review folded)
**Date:** 2026-07-19
**Owner:** Engramory team
**Origin:** founder request 2026-07-19 — review `engramory/` and especially
`engramory/docs/` for gaps, discrepancies, and improvements against best
practice and current trends (Google's Open Knowledge Format named as an
example). A three-agent review (internal doc consistency · docs-vs-code drift ·
2026 field research) returned 19 findings; Pass 2 added a 20th.
**Numbering note:** this plan is **PLAN-004**, not PLAN-003. `PLAN-003` is
already in use in this repo's prose as an *external* reference — the
aidoc-flow-ci / operations canon-adoption plan — cited at `CLAUDE.md:39`,
`docs/README.md:10`, `DECISIONS.md:14,18,25,33,57`, and `HANDOFF.md:72`.
Reusing the number would make every one of those references ambiguous.
**Decision foundation:** none new. The plan restores docs to the state already
decided by `sdd/05_ADR/ADR-10_agent_facing_packaging.yaml` (Accepted),
`sdd/06_SPEC/SPEC-07_cli_face.yaml`, and PLAN-002's delivery. The one new
decision — the OKF adoption ADR in Phase 6 — is authored as a *proposal* and
accepted separately by the founder.

## Goal

At plan completion: every documented `engramory` invocation runs as written and
conforms to SPEC-07; no agent-facing doc *or normative spec* promises behaviour
the code does not have; the governance layer (CLAUDE.md / ROADMAP / README /
HANDOFF / indexes) describes the post-PLAN-002 repo; the repo has a stated
security boundary and a data-retention/purge policy; and the design debt the
field review surfaced is recorded as tracked work rather than silently absent.

## Non-goals (explicitly out of scope)

Sized deliberately: 20 findings → remediation, no speculative features. The
field research produced 8 recommendations, of which 6 are *engineering* tracks
recorded as tracked debt (`TODO.md` §9) rather than built here:

- **Bi-temporal validity + contradiction detection (D3)**, **hybrid retrieval /
  rerank / query routing (SPEC-03)**, **eval harness + named benchmarks**,
  **injection screen (G1)**, **MCP spec alignment**, **OTel GenAI semconv** —
  each needs its own spec + plan.
- **OKF export implementation** — Phase 6 authors the ADR proposing it only.
- **MCP gateway binding** — unchanged from PLAN-002's non-goals (ADR-10 next
  cycle). This plan only stops docs from implying it is live.
- **CI Postgres services fix** (TODO §4's real gap) — refresh the stale counts,
  leave the item open; changing `ci.yml` is CI work with its own risk surface.
- **`knowledge_search` split** — CORES.md boundary rule 4 already defers it.
- **Widening the SPEC-07 invocation contract** — see Phase 1; deliberately
  demoted to an optional, separately-decided enhancement.

## Findings → phase map

| # | Finding | Sev | Phase |
|---|---|---|---|
| 1 | 5 doc sites teach a `--json` position SPEC-07 does not define; it exits 2 | P1 | 1 |
| 2 | AGENT-QUICKSTART Track B (CI) snippet fails on fresh checkout | P1 | 1 |
| 3 | CLAUDE.md "no adapters or running gateway yet" — false since PLAN-002 | P1 | 3 |
| 4 | CLAUDE.md Unified-CI per-repo state 3 generations stale + cites a resolved blocker | P1 | 3 |
| 5 | ROADMAP "Known issue" prescribes a superseded fix for a resolved problem | P1 | 3 |
| 6 | README "Status: Project initiation" contradicts its own agent section | P2 | 3 |
| 7 | `memory_search` does not span the knowledge core — 4 surfaces say it does | P2 | 2a |
| 8 | "feedback drives confidence updates" — no confidence update exists (3 surfaces) | P2 | 2a |
| 9 | Search ranking is recency-only; agent docs imply relevance ranking | P2 | 2b |
| 10 | `knowledge_ingest` has no CLI binding — an **ADR-10 conformance gap**, not a doc overclaim | P2 | 2b |
| 11 | IPLAN statuses `Draft` while manifests are `DONE, verified: true` | P2 | 4a |
| 12 | docs/README.md index omits INSTALL / AGENT-QUICKSTART / AGENT-INTEGRATION + Skill | P2 | 4a |
| 13 | docs/adr/README.md missing the ADR-10 row | P2 | 6 |
| 14 | TODO §4 cites "30 of 49 tests" (suite is 94); underlying CI gap still real | P2 | 4b |
| 15 | No SECURITY / threat-model doc; no retention-purge policy for secrets/PII | P2 | 5 |
| 16 | HANDOFF ~9 PRs behind; CHANGELOG "Six PRs" vs HANDOFF "Seven" | P3 | 4b |
| 17 | Engine-default drift across 3 sites; MEMORY_DESIGN historical label missing in README | P3 | 4c |
| 18 | Dead/compose-only env vars; AGENTS.md `LITELLM_BASE_URL` claim; inert domain YAML; vendor-SDK carve-out | P3 | 2b |
| 19 | OKF (2026-06-12) and 2026 field alignment absent from strategy docs | P2 | 6 |
| 20 | SPEC-01 `interfaces.exports` signatures drifted from `surface.py` | P3 | 2a |

## Phase 1 — Restore the documented CLI invocation to the SPEC-07 contract (P1)

**Pass 2 corrected the direction of this fix.** `sdd/06_SPEC/SPEC-07_cli_face.yaml:46`
declares the invocation contract **normatively** as
`engramory [--json] [--config PATH] <command> [args]` (claim 1) — global-first.
The code implements exactly that: `--json`/`--config` on the root parser
(claim 2) with subparsers built after (claim 3). **The code is conformant; the
five doc sites are the deviation.** The Pass-1 draft had this backwards — it
called the code "the defect" and proposed silently widening a normative contract
in a PR classed as non-governance. That is the wrong shape regardless of merit.

1. **Correct the five doc sites to the global-first form** (claims 4–8):
   `docs/INSTALL.md:62,71`, `docs/AGENT-QUICKSTART.md:38`,
   `docs/AGENT-INTEGRATION.md:73` (the paste-verbatim vendor snippet — highest
   blast radius), `skills/engramory-memory/SKILL.md:26`. The working form is
   already demonstrated by `scripts/smoke_preprod.sh:22` (claim 9) and
   `tests/integration/test_cli.py`, which is why CI never caught the drift.
2. **Fix Track B** (claims 10, 11): add an `engramory init --dsn "$ENGRAMORY_DSN" …`
   step — `.engramory/` is gitignored by design, so a fresh CI workspace has no
   config and the snippet exits 2 — and correct the guard prose, since
   `|| [ $? -eq 3 ]` does not cover exit 2.
3. **Add a docs-conformance test** so this cannot regress: assert that each
   `engramory …` invocation appearing in the docs parses. Cheap, and it targets
   the actual root cause — the docs were never executable-checked.

**PR 1** (4 doc surfaces + 1 test): not a governance PR; the ≤3 cap does not
apply (no CLAUDE.md / AGENTS.md / ADR / ai-review surfaces).

### Optional enhancement (NOT in this plan — requires its own decision)

Accepting `--json` in *both* positions is defensible ergonomics: five
independent doc sites drifted to the post-subcommand form, which is evidence
that global-first is the unnatural one for agents. It is feasible — verified
empirically 2026-07-19 on a probe mirroring `cli.py`'s real nesting
(root → `memory` → `search`):

| Invocation | no SUPPRESS | SUPPRESS |
| --- | --- | --- |
| `--json status` (smoke + tests use this) | `json=False` — silently broken | `json=True` |
| `status --json` (the 5 doc sites) | `json=True` | `json=True` |
| `--json memory search` | `json=False` — silently broken | `json=True` |
| `memory search --json` | `json=True` | `json=True` |
| `status` (neither) | `json=False` | `json=False` |

Implementation notes if it is ever taken up: the shared parent must be
`argparse.ArgumentParser(add_help=False)` or construction raises
`conflicting option strings: -h, --help`; `default=argparse.SUPPRESS` is
load-bearing (without it a leaf silently overwrites a root-supplied `--json`
with `False`, breaking the smoke script and the whole CLI suite); `parents=`
applies to **leaf** subparsers, so a third position — `engramory memory --json
search` — would still exit 2 unless the intermediates get it too. A
module-level `_add_common(parser)` helper avoids the `parents=`/`add_help`
footgun entirely.

**This is a SPEC-07 contract widening.** It requires amending `SPEC-07:46` +
the `global_flags` block with a decision record — not a silent code change.
Filed as a TODO item, not scheduled here.

## Phase 2 — Stop docs *and normative specs* overstating the implementation (P2)

Pass 2 found the Pass-1 draft edited only the agent-facing docs while leaving
the same claims standing in the normative artifacts. Both sides are fixed here.

### PR 2a — the unified-read, confidence, and signature claims

1. **The unified read is asserted on four surfaces, not two** (claims 12–17,
   21): `docs/CORES.md:24` (the cell), `docs/CORES.md:59` (boundary rule 4,
   which calls it *"deliberately unified … spans both cores"*),
   `sdd/06_SPEC/SPEC-01_access_surface.yaml:56`, and
   `src/engramory/mcp/server.py:4` — whose docstring carries an explicit
   *"keep this docstring in sync with it"* instruction. The code reads
   `FROM memories` only (claim 16); `kb_sections` is written and counted, never
   read back (claim 17).
   **Framing correction:** CORES rule 4 calls this a deliberate MVP-1 design
   choice and SPEC-01 is a signed contract, so downgrading it to "planned" is a
   **spec deviation, not bookkeeping**. Record it as an explicit deviation in
   the PR body with a `DECISIONS.md` entry, and file the `kb_sections`
   retrieval leg as a TODO item. Do not let the amendment land as a silent edit.
2. **The confidence claim also lives in SPEC-01** (claims 18–20, 23):
   `sdd/06_SPEC/SPEC-01_access_surface.yaml:66` says feedback *"drives the
   SPEC-04 confidence-update rule"* in the present tense — the same
   overstatement as `docs/AGENT-QUICKSTART.md:23`. Nothing updates
   `memories.confidence`; the surface itself calls the value the signal *for*
   the future rule (claim 20). Rephrase both to future tense.
3. **SPEC-01 signature drift** (finding 20, claims 24, 25): `SPEC-01:55`
   declares `memory_search(ctx, query, k=8, token_budget=None)` against an
   implementation taking `*, scope, tenant_id, k, token_budget` (claim 24);
   `SPEC-01:60` declares `memory_add(ctx, episode: EpisodeIn)` against keyword
   args, and `EpisodeIn` / `KBEntryIn` / `AuditRecord` do not exist. Sweep
   `interfaces.exports` against `surface.py` in this same pass.

Surfaces: `docs/CORES.md` + `sdd/06_SPEC/SPEC-01_access_surface.yaml` +
`docs/AGENT-QUICKSTART.md` (+ `src/engramory/mcp/server.py`, which is code, not
a doc surface). **Classification correction:** Pass 1 called this governance
because of AGENTS.md — AGENTS.md is in 2b, not here, and none of 2a's surfaces
are on CLAUDE.md's governance list, so **2a is not a governance PR**. Note that
ai-review's `tier=spec` exclusion may still catch it since it edits
`sdd/06_SPEC/`.

### PR 2b — ranking, tool reachability, config honesty (3 surfaces)

4. **Ranking** (claims 26, 27): the module docstring already discloses that
   ranking is recency-based and that the query scopes only the audit trail; no
   agent-facing doc does. Add one interim line to
   `skills/engramory-memory/SKILL.md` and drop its "prefer specific queries"
   advice, which implies relevance ranking that does not exist.
5. **`knowledge_ingest` reachability is an ADR-10 conformance gap** (claims 28,
   29): `ADR-10:71` (**Accepted**) mandates the CLI expose the SPEC-01 tool set
   *including* `knowledge ingest`; the CLI registers `init` / `memory` /
   `profile` / `status` only. So this is **not** a doc overclaim to be papered
   over — the doc qualifier in AGENT-QUICKSTART is an *interim disclosure of an
   ADR non-conformance*, and the missing `engramory knowledge ingest` binding
   is filed as a TODO item citing ADR-10 clause 2. Say so in the PR body.
6. **Config/env honesty** (claims 30–33): verified split —
   `REDIS_URL`, `OLLAMA_HOST`, `EMBEDDING_MODEL`, `EMBEDDING_DIMS`,
   `POSTGRES_HOST/PORT` are read by neither `src/` nor compose (genuinely
   dead/reserved), while `KEYCLOAK_*`, `NEO4J_*`, `S3_*`, `LITELLM_MASTER_KEY`
   are compose-consumed. Label them accordingly rather than as one
   undifferentiated block. Correct `AGENTS.md:15`'s `LITELLM_BASE_URL` claim to
   future tense, note `config/domains/*.yaml` is a forward-looking schema no
   code loads, and add the psycopg-in-core carve-out to `AGENTS.md:13`'s
   vendor-SDK rule so it matches what the tests enforce.

Surfaces: `skills/engramory-memory/SKILL.md` + `AGENTS.md` + `.env.example`.
**Governance PR** (AGENTS.md) — exactly 3, Rule 2 self-review before push.

## Phase 3 — Re-sync the governance layer (P1)

The first files every agent session reads assert a pre-implementation repo.

1. **CLAUDE.md "What this repo is"** (claim 34): replace "no adapters or running
   gateway yet" with the post-PLAN-002 state — SPEC-01 tool set complete over
   AccessSurface, `engramory` CLI dev/CI face live, dev vector adapter wired,
   MCP gateway still a stub.
2. **CLAUDE.md Unified-CI per-repo state** (claim 35): the block pins
   `@ci/v1.4.3`–`v1.6.0`, says "Dormant until the reviewer App is installed",
   and cites the `AI_REVIEW_TOKEN` blocker that `TODO.md:14` marks **RESOLVED
   2026-07-11** (claim 36). Replace the pin enumeration with a pointer to
   `.github/workflows/` plus a current adoption summary — the enumeration is a
   recurring drift source, so removing it is the fix, not re-listing today's
   pins.
3. **ROADMAP "Known issue"** (claim 37): delete the block; it prescribes the
   superseded remedy for the resolved problem. Bump the `Last updated` and
   status-heading dates, which trail the body text.
4. **README Status** (claim 38): replace "Project initiation. Phase 0 (dev
   foundation) scaffolding." with the pre-prod-usable-via-CLI state.
5. **Disambiguate the external PLAN-003 reference at `CLAUDE.md:39`** (claim 57)
   while in the file — qualify it as `aidoc-flow-ci PLAN-003`. The remaining
   references are handled in 4c.

**PR 3** — governance PR, exactly 3 surfaces: `CLAUDE.md` + `roadmap/ROADMAP.md`
+ `README.md`. Rule 2 adversarial self-review before push, focused on dead refs
and status consistency.

## Phase 4 — Bookkeeping currency (P2/P3) — split three ways

Pass 2 found the Pass-1 single "PR 4" carried ~10 surfaces — 3× the cap — and
was arguably governance-classed via `docs/adr/README.md`. Split, with that file
moved to PR 6, which already owns it.

- **PR 4a — SDD + docs indexes** (claims 39–41): flip doc-level `status: Draft`
  on completed IPLANs and their index rows to match the manifests'
  `DONE, verified: true`; add the missing INSTALL / AGENT-QUICKSTART /
  AGENT-INTEGRATION rows + Skill pointer to `docs/README.md`.
- **PR 4b — live-state currency** (claims 42–46): refresh `TODO.md` §4's
  "30 of 49 tests" to the current 94-test suite (item stays open — the
  no-Postgres-in-CI gap is real, evidenced by `ci.yml:25`'s bare `pytest` with
  no `services:` block); refresh `HANDOFF.md` past the ~9 CI PRs merged since
  2026-07-11 and its superseded CI note; correct `CHANGELOG.md:40`'s "Six PRs"
  to seven.
- **PR 4c — engine default, doc labels, PLAN-003 disambiguation** (claims
  47–49, 55): normalize the Phase-2 rows at `docs/ARCHITECTURE.md:201` and
  `roadmap/ROADMAP.md:45` to the Mem0 default decided at
  `docs/ARCHITECTURE.md:176`, with LangMem/Cipher as alternates; add the
  historical qualifier to README's MEMORY_DESIGN row (claim 55); qualify the
  remaining external `PLAN-003` references in `docs/README.md:10`,
  `DECISIONS.md`, and `HANDOFF.md:72`.

**File-collision note (Pass 2 finding):** PR 3 and PR 4c both edit `README.md`
and `roadmap/ROADMAP.md`; PR 4a and PR 6 both touch index files. The phases are
**not** independent on those files — execute strictly in PR order and rebase 4c
on 3, and 6 on 4a.

## Phase 5 — Close the security-doc gap (P2)

1. **`SECURITY.md`** (absence verified; claims 50, 51): one page consolidating
   the trust boundary currently duplicated at `docs/AGENT-QUICKSTART.md:85` and
   `docs/AGENT-INTEGRATION.md:89` plus `TODO.md` §5 — the dev-tier fence
   (`ENGRAMORY_PROFILE=dev`), default-deny authz, tenant wall,
   audit-before-return, fail-closed behaviour, and what the CLI face
   deliberately does *not* defend (single-tenant dev trust).
2. **Data-retention / purge policy** — the sharpest gap. Docs instruct agents
   never to store secrets or PII, but `memory forget` is a **soft** retire —
   end-dated with provenance retained (claim 52) — and no file documents a hard
   purge. State what soft-retire guarantees, that the episode body persists, and
   the operator purge path (direct SQL against `episodes` + `memories` +
   re-embedding implications). If no acceptable purge path exists today, say so
   explicitly and file it as debt — silence is the failure mode.
   **Surface decision (Pass 2):** the policy lands as a section *inside*
   `SECURITY.md`, not a separate file, keeping PR 5 at one surface.

**PR 5** — 1 surface (`SECURITY.md`). Every security claim traced to its
enforcing test (`tests/security/test_failclosed.py`,
`tests/unit/test_factory_fence.py`, `tests/e2e/test_scope_isolation.py`) or
explicitly marked "not defended".

## Phase 6 — Field alignment: the OKF proposal (P2)

Author a **conceptual ADR** in `docs/adr/` proposing an L0 ↔ **Open Knowledge
Format** projection, and add the ADR-10 row missing from the conceptual-ADR
index (finding 13, claim 56 — deferred here from 4a so one PR owns that file).

OKF is Google Cloud's vendor-neutral knowledge-interchange spec, announced
2026-06-12 (v0.1): a bundle is a directory of markdown files with YAML
frontmatter (only `type` required), the concept ID is the file path minus
`.md`, markdown links assert relationships, and `index.md` / `log.md` are
reserved and must not be concept documents. It is an **interchange format, not
a memory engine**: it maps onto the **L0 knowledge core** only, leaves L1–L3
distilled memory untouched, and would give MEMORY_DESIGN's existing "read-only
Markdown export / portability snapshot" idea conformance with a published spec
— filling the interchange gap those docs flag as standardless. Proposal only;
the founder accepts or declines separately.

Sources: <https://cloud.google.com/blog/products/data-analytics/how-the-open-knowledge-format-can-improve-data-sharing/>,
<https://github.com/GoogleCloudPlatform/knowledge-catalog/blob/main/okf/SPEC.md>.

**PR 6** — governance PR (ADR file): new ADR + `docs/adr/README.md` +
`docs/STRATEGY.md` one-line note that managed-memory churn in the field
reinforces ADR-05. 3 surfaces.

**Already landed (Pass 2 finding):** the six field-alignment debt items and the
plan's execution tracker are already recorded at `TODO.md` §8–§9 (claims 53,
54); PR 6 does **not** re-file them. It adds only the new items this plan
surfaced: the `kb_sections` retrieval leg (2a), the `engramory knowledge ingest`
ADR-10 binding (2b), and the optional SPEC-07 contract widening (Phase 1).

## Sequencing

PR 1 → 2a → 2b → 3 → 4a → 4b → 4c → 5 → 6. Phase 1 first: it is the only
finding that breaks a working command for every integrated agent. Phases are
**not** fully independent — see the file-collision note in Phase 4; rebase in
order.

## Verification

- **Phase 1:** every `--json` example in the 4 doc surfaces executed as written;
  new docs-conformance test green; `make smoke` unchanged and green.
- **Phase 2:** SPEC-01 `interfaces.exports` diffed against `surface.py` with no
  remaining signature drift; the spec deviation recorded in `DECISIONS.md`.
- **Phase 3–4:** markdown-lint CI green — note it is `fail-on-findings: true`
  as of PR #49 but **not yet a required status check** (a pending founder step);
  `links.yml`'s internal job is blocking while its external job is explicitly
  non-blocking, so a green run is not proof of external-link health.
- **Phase 5:** no security claim without an enforcing test or an explicit "not
  defended" note.
- **All PRs:** OPS-0065 multi-agent review pre-push; governance PRs (2b, 3, 6)
  additionally under the ≤3-surface cap + Rule 2 adversarial self-review.

## Claim ledger

| # | Claim | Symbol | Citation |
| --- | --- | --- | --- |
| 1 | SPEC-07 normatively defines global-first invocation | `invocation: "engramory [--json] [--config PATH]` | sdd/06_SPEC/SPEC-07_cli_face.yaml:46 |
| 2 | `--json` is registered on the root parser only | `parser.add_argument("--json"` | src/engramory/cli.py:256 |
| 3 | subparsers are created after the root flags | `sub = parser.add_subparsers` | src/engramory/cli.py:260 |
| 4 | INSTALL teaches the failing post-subcommand form (smoke check) | `engramory status --json` | docs/INSTALL.md:62 |
| 5 | INSTALL teaches it again for search | `engramory memory search --query "fact" --json` | docs/INSTALL.md:71 |
| 6 | AGENT-QUICKSTART teaches the failing form | `engramory memory search --query "postgres version" --json` | docs/AGENT-QUICKSTART.md:38 |
| 7 | AGENT-INTEGRATION teaches it in the paste-verbatim vendor snippet | `**Session start:** ` | docs/AGENT-INTEGRATION.md:73 |
| 8 | the shipped Skill teaches the failing form | `--json` before repeating work | skills/engramory-memory/SKILL.md:26 |
| 9 | the smoke script uses the working global-first form | `engramory --json init` | scripts/smoke_preprod.sh:22 |
| 10 | Track B's CI snippet runs `memory add` with no `init` step | `- name: Record run outcome into engramory` | docs/AGENT-QUICKSTART.md:50 |
| 11 | Track B's documented guard covers exit 3 only | `Exit `3` is retryable` | docs/AGENT-QUICKSTART.md:57 |
| 12 | CORES claims knowledge reads are served through `memory_search` | `reads served through `memory_search` for MVP-1` | docs/CORES.md:24 |
| 13 | CORES boundary rule 4 calls the unified read deliberate | `MVP-1 retrieval is deliberately unified` | docs/CORES.md:59 |
| 14 | SPEC-01 contracts search over knowledge + memory | `Scoped retrieval of knowledge + distilled memory` | sdd/06_SPEC/SPEC-01_access_surface.yaml:56 |
| 15 | the MCP server docstring repeats the unified-read claim | `scoped retrieval of knowledge + distilled memory` | src/engramory/mcp/server.py:4 |
| 16 | `get_memories` selects from the `memories` table only | `FROM memories` | src/engramory/core/repository.py:369 |
| 17 | `kb_sections` is written/counted but never read back | `async def count_kb_sections` | src/engramory/core/repository.py:505 |
| 18 | QUICKSTART states feedback drives confidence updates | `this drives confidence updates` | docs/AGENT-QUICKSTART.md:23 |
| 19 | the Skill states feedback tunes memory confidence | `the learning signal that tunes memory confidence` | skills/engramory-memory/SKILL.md:54 |
| 20 | the code records feedback as a *future* rule's signal | `signal for the SPEC-04 confidence-update rule` | src/engramory/access/surface.py:193 |
| 21 | the surface's search calls `get_memories` with no query vector | `memories = await self._repo.get_memories(` | src/engramory/access/surface.py:123 |
| 22 | the MCP server is a stub, not a live gateway | `MCP gateway — the single tool surface` | src/engramory/mcp/server.py:1 |
| 23 | SPEC-01 states feedback drives the confidence rule, present tense | `drives the SPEC-04 confidence-update rule` | sdd/06_SPEC/SPEC-01_access_surface.yaml:66 |
| 24 | the implemented search signature carries scope/tenant_id | `async def memory_search(` | src/engramory/access/surface.py:104 |
| 25 | SPEC-01 declares a narrower search signature | `memory_search` | sdd/06_SPEC/SPEC-01_access_surface.yaml:55 |
| 26 | ranking is recency-based; the query scopes the audit trail only | `ranks by recency` | src/engramory/access/surface.py:16 |
| 27 | the Skill advises specific queries, implying relevance ranking | `Prefer specific queries` | skills/engramory-memory/SKILL.md:28 |
| 28 | ADR-10 mandates the CLI expose knowledge ingest | `knowledge ingest` | sdd/05_ADR/ADR-10_agent_facing_packaging.yaml:71 |
| 29 | QUICKSTART points agents at "all six agent tools" | `The tool contract (all six agent tools)` | docs/AGENT-QUICKSTART.md:100 |
| 30 | only `ENGRAMORY_PROFILE` is read by code | `os.environ.get("ENGRAMORY_PROFILE"` | src/engramory/adapters/factory.py:32 |
| 31 | AGENTS.md presents `LITELLM_BASE_URL` as live | `LITELLM_BASE_URL` | AGENTS.md:15 |
| 32 | the AGENTS.md vendor-SDK rule omits the psycopg-in-core carve-out | `Never call a vendor SDK` | AGENTS.md:13 |
| 33 | `.env.example` declares an unread LiteLLM base URL | `LITELLM_BASE_URL=http://localhost:4000` | .env.example:29 |
| 34 | CLAUDE.md says no adapters or running gateway exist | `no adapters or` | CLAUDE.md:13 |
| 35 | CLAUDE.md's per-repo CI state pins superseded canon versions | `Per-repo state (2026-07-09)` | CLAUDE.md:71 |
| 36 | TODO §1 records the trust-gate blocker RESOLVED 2026-07-11 | `RESOLVED 2026-07-11` | TODO.md:14 |
| 37 | ROADMAP presents the resolved trust-gate issue as live | `Known issue (CI, operations-owned)` | roadmap/ROADMAP.md:25 |
| 38 | README's Status line says project initiation / scaffolding | `Project initiation` | README.md:97 |
| 39 | IPLAN-01 carries doc-level `status: Draft` | `status: Draft` | sdd/08_IPLAN/IPLAN-01_access_surface.yaml:8 |
| 40 | IPLAN-01's manifest items are DONE and verified | `status: DONE, session: "s1-2026-07-09", verified: true` | sdd/08_IPLAN/IPLAN-01_access_surface.yaml:19 |
| 41 | the docs index has no INSTALL/QUICKSTART/INTEGRATION rows | `Historical design input` | docs/README.md:8 |
| 42 | TODO §4 cites a stale test count | `CI verifies 30 of 49 tests` | TODO.md:112 |
| 43 | CI runs bare pytest with no postgres service | `run: pytest` | .github/workflows/ci.yml:25 |
| 44 | the suite is 94 tests as of PLAN-002 | `94 tests vs real Postgres` | HANDOFF.md:28 |
| 45 | CHANGELOG says six PRs for a seven-PR delivery | `Six PRs` | CHANGELOG.md:40 |
| 46 | HANDOFF says seven PRs merged for PLAN-002 | `Seven PRs merged 2026-07-11` | HANDOFF.md:13 |
| 47 | ARCHITECTURE names Mem0 the decided default engine | `Default engine: Mem0` | docs/ARCHITECTURE.md:176 |
| 48 | ARCHITECTURE's Phase-2 row still says wire LangMem/Cipher | `wire LangMem/Cipher via `MemoryPort`` | docs/ARCHITECTURE.md:201 |
| 49 | ROADMAP's Phase-2 row names all three engines | `wire a MemoryPort adapter (LangMem/Cipher/Mem0)` | roadmap/ROADMAP.md:45 |
| 50 | trust-boundary prose lives scattered in agent docs | `## Trust boundary (dev tier)` | docs/AGENT-QUICKSTART.md:85 |
| 51 | the same boundary is restated in the integration doc | `## Trust boundary` | docs/AGENT-INTEGRATION.md:89 |
| 52 | `memory forget` is soft — end-dated, provenance retained, no purge | `provenance retained` | docs/AGENT-QUICKSTART.md:83 |
| 53 | the plan's execution tracker is already filed in TODO | `documentation review remediation` | TODO.md:176 |
| 54 | the six field-alignment debt items are already filed in TODO | `Field-alignment design debt` | TODO.md:303 |
| 55 | README's MEMORY_DESIGN row lacks the historical qualifier | `Layered memory (L0–L3), distillation loop, schema, portability` | README.md:81 |
| 56 | the conceptual ADR index ends at ADR-09 | `Independent memory storage` | docs/adr/README.md:21 |
| 57 | this repo's prose already cites an external PLAN-003 | `PLAN-003 §4.2` | CLAUDE.md:39 |

**Absence claims** (no citation is possible for a file that does not exist;
verification method recorded instead): `SECURITY.md` exists at neither the repo
root nor `docs/` — verified 2026-07-19 via `ls SECURITY.md docs/SECURITY.md`
(both "No such file or directory"). No file in the repo documents a hard-purge
path for secrets or PII that reach `episodes`. The CLI registers no `knowledge`
subcommand — verified via `_build_parser` (`src/engramory/cli.py:262-305`
registers `init` / `memory` / `profile` / `status` only).

## Review log

### Pass 1 — 2026-07-19 — author self-check

Drafted from a three-agent review (doc consistency · docs-vs-code drift · field
research). Every ledger citation re-opened by the author before entry; the
agents' line numbers were not trusted as given (the `--json` root-parser line
was reported as 255 and is 256). Scope trimmed against the minimal-plan rule:
6 of 8 field-research recommendations recorded as debt rather than built.

Self-identified risks carried into Pass 2: (a) the Phase-1 code change could
regress the global-first form the smoke script depends on; (b) claim 41
asserted an absence, which a citation cannot express.

### Pass 2 — 2026-07-19 — independent (fresh-context adversarial subagent)

Returned **10 load-bearing + 7 minor findings**. All verified by the author
against source before folding; none rejected. Material corrections:

1. **Phase 1 was inverted.** `SPEC-07:46` defines global-first *normatively*, so
   the code is conformant and the five docs are the deviation. The draft called
   the code "the defect" and proposed widening a normative contract inside a PR
   classed non-governance. **Folded:** doc-correction is now the primary fix; the
   widening is demoted to an optional, separately-decided enhancement with its
   SPEC-07 amendment requirement stated.
2. **The proposed parser fix would not have compiled** — a `parents=` parent
   without `add_help=False` raises `conflicting option strings: -h, --help`. The
   author's own probe had used `add_help=False` without the plan saying so.
   **Folded** into the optional-enhancement notes, with a simpler
   `_add_common()` alternative.
3. **`knowledge_ingest` is an ADR-10 conformance gap, not a doc overclaim** —
   `ADR-10:71` (Accepted) mandates the CLI expose it. **Folded:** the doc edit is
   reframed as interim disclosure of a non-conformance + a TODO binding item.
4. **Two more surfaces assert the unified read** (`CORES.md:59`,
   `mcp/server.py:4`) and the confidence overstatement also lives in
   `SPEC-01:66`. **Folded** into PR 2a/2b, with the SPEC-01 downgrade recorded as
   an explicit deviation + `DECISIONS.md` entry rather than a silent edit.
5. **PR 4 was ~10 surfaces** (3× the governance cap) and arguably governance via
   `docs/adr/README.md`. **Folded:** split into 4a/4b/4c with that file moved to
   PR 6. PR 2a's governance classification corrected (it is not one); PR 5's
   second surface named (the policy lands inside `SECURITY.md`).
6. **File collisions** across PR 3/4c and 4a/6 broke the "phases are
   independent" claim. **Folded:** explicit rebase order.
7. **Ledger corrections:** claim 27 line drift (`TODO.md:13` → `:14`); claim 37
   cited `CHANGELOG.md:41` ("94 tests") for a finding about "Six PRs" at `:40`.
   Eight uncited load-bearing facts added (`SPEC-07:46`, `ADR-10:71`,
   `mcp/server.py`, `ci.yml:25`, `CHANGELOG.md:40`, `CORES.md:59`,
   `ROADMAP.md:45`, the env-var/compose split). Ledger grew 43 → 57 rows.
8. **Identifier collision:** `PLAN-003` is already used in this repo's prose for
   the external aidoc-flow-ci canon-adoption plan (6 references). **Folded:**
   plan renumbered to **PLAN-004**; disambiguating the existing references added
   to PR 3 and PR 4c.
9. Verification section's CI claim corrected — PR #49 graduated markdown-lint
   only, `links.yml`'s external job is non-blocking, and neither is yet a
   required status check.
10. New finding 20 added (SPEC-01 signature drift), which the Pass-1 drift review
    had missed.

Confirmed sound by Pass 2: findings→phase map complete (no omissions); OKF
description accurate against the Google Cloud blog and `okf/SPEC.md`; the
SUPPRESS reasoning correct modulo `add_help`; non-goals well-sized; 39 of 41
original ledger rows exact.

### Pass 3 — 2026-07-19 — author fold verification

Re-verified each Pass-2 load-bearing finding against source before folding:
`SPEC-07:46` global-first contract ✓; `ADR-10:71` knowledge-ingest mandate ✓;
`CHANGELOG.md:40` "Six PRs" ✓; the six external `PLAN-003` references ✓;
`mcp/server.py:4`, `SPEC-01:56`/`:66`, `CORES.md:59`, `ci.yml:25`,
`ROADMAP.md:45` ✓; the env-var/compose split re-checked by grep (`REDIS_URL`,
`OLLAMA_HOST`, `EMBEDDING_MODEL` absent from compose; `KEYCLOAK_*` / `NEO4J_*`
present) ✓. All 10 load-bearing findings folded, none rejected. Plan renumbered,
ledger expanded to 57 rows, PR structure re-cut from 6 to 9 PRs to hold the
governance cap.

**Result:** ready — no further load-bearing findings.
