# PLAN-001 — Pre-First-Run Remediation Implementation Plan

> **STATUS: COMPLETE (2026-07-09).** All tasks executed and merged: Task 0
> (housekeeping) + PR-1…PR-7 = GitHub #13–#19, closeout #20; Dependabot
> #8–#10 also merged. Founder-gated residue tracked in `TODO.md` §1
> (`AI_REVIEW_TOKEN`) and §2 (F5). Checkboxes below retained as authored
> (execution followed the steps; see CHANGELOG.md 2026-07-09 entry).
>
> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close every contract-level, operational, and documentation gap found in the 2026-07-09 five-agent pre-first-run review, so the first MVP-1 implementation session builds the *reviewed* design (feedback loop, quarantine, idempotency, tenant wall) instead of the pre-review one.

**Architecture:** Seven small PRs in dependency order: operations reconcile → schema migration 0003 → SDD storage reconciliation → learning-loop/retrieval contracts (code + SDD) → infra/test cleanup → docs cleanup → governance refresh. Each PR is self-contained and green on its own. No conceptual rework: the plane (Postgres spine, 8 ports, two cores, scope ladder) is kept unchanged.

**Tech Stack:** Postgres 16 + pgvector (SQL migrations), Python 3.12 `typing.Protocol` ports, YAML SDD artifacts (BRD→IPLAN), GitHub Actions (aidoc-flow-ci reusables), docker compose.

## Global Constraints

- **Merge convention:** `gh pr merge --squash --delete-branch` after all required checks pass (OPS-0062). Until the `AI_REVIEW_TOKEN` secret lands (founder-gated, Task 1), `call / trust` FAILS on every PR → each merge needs the founder (admin bypass). Announce each merge attempt in-session; escalate at 10 attempts per PR.
- **Multi-agent review before push** (OPS-0065/0067): dispatch review agents on each PR diff before `git push`; ≤300-line diffs → 3-agent parallel + single fold cycle; cap 3 cycles.
- **Governance PR discipline (OPS-0061):** PR-7 touches `CLAUDE.md` + 2 ADR files → governance PR: ≤3 doc surfaces (it has exactly 3), mandatory adversarial self-review before push (dead refs, supersession completeness, status consistency).
- **Verification before completion:** every PR ends with `make lint typecheck test` green locally (`ruff check src`, `mypy src`, `pytest` — all currently pass and must stay passing).
- **Do not touch:** `docs/adr/0001–0005` (conceptual ADRs — no findings), `sdd/01_BRD/**`, `sdd/02_PRD` except the one threshold addition, EARS statements (no requirement text changes — EARS.01.04.c300 already exists; we only *trace* it downstream).
- **Language rules** (global CLAUDE.md): objective language, no subjective qualifiers, no time estimates in any doc content written by this plan.
- Branch names: `fix/plan-001-pr{N}-{slug}`. Commit trailer: `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.

## Review-finding disposition (why each task exists)

| Review finding | Disposition |
|---|---|
| `StoragePort` = relational in SPECs vs blob in code | **Fix in PR-3/PR-7** — SPECs+ADR-02 corrected to object-storage semantics; repository specified over the Postgres driver directly (ADR-01 makes Postgres the one-way spine; a swap-port for the canonical store is YAGNI — cloud migration is a managed-Postgres connection swap) |
| `content_hash`, `kb_sections`, `memories.status`, tenant nullability, no feedback/audit tables | **Fix in PR-2** (migration 0003) |
| IPLAN-02 marks migration DONE while unsatisfying SPEC-02 | **Fix in PR-3** (manifest updated after 0003 exists) |
| No feedback/forget/profile tools; ranking spec one-liner; no token budget | **Fix in PR-4** |
| `MemoryPort.distill` vs SPEC-04 `reflect` naming drift | **Fix in PR-4** — port method renamed `reflect` (SPEC is the contract source; no implementation exists yet, so rename is free) |
| EARS.01.04.c300 (secrets exclusion, P1) has no downstream trace | **Fix in PR-4** (BDD scenario + SPEC-04 rule + TDD-04 case) |
| BDD c060→e500 one-way coverage mismatch | **Fix in PR-4** |
| CI `call / trust` fails on every PR; TODO vs ROADMAP prescribe different fixes | **Fix in PR-1** — reconcile on TODO Option A (`AI_REVIEW_TOKEN` secret). ROADMAP's prescribed fix (pin-bump to a canon version reading a local trust config) is **not executable**: verified 2026-07-09 that aidoc-flow-ci HEAD (> ci/v1.6.0) still checks out private `aidoc-flow-operations@main` in the trust job |
| MinIO healthcheck "can never pass" | **DROPPED — false positive.** Verified empirically 2026-07-09: `docker exec <minio> mc ready local` → "The cluster 'local' is ready", exit 0 (server image bundles `mc` with a default `local` alias). Only image *pinning* remains (PR-5) |
| Unpinned images, Makefile default goal, weak test asserts, CODEOWNERS dead paths | **Fix in PR-5** |
| kb_sections cycle ambiguity, CORES namespace contradiction, dead refs, stale reviews | **Fix in PR-6** |
| CLAUDE.md stale CI state, ADR-00 stale preamble, ADR-02 StoragePort wording | **Fix in PR-7** (governance) |
| Degraded memory-read mode, bi-temporal columns, decay policy, single-active-head constraint | **DEFERRED** (see § Deferred) |

---

### Task 0: Repo housekeeping (no PR)

**Files:** none (git state only)

- [ ] **Step 1: Sync main and prune**

```bash
cd /opt/data/aidoc-flow/engramory
git fetch origin --prune
git checkout main && git pull --ff-only
```

Expected: main advances to squash commit of PR #12 (67b12d2).

- [ ] **Step 2: Delete merged remote branches (verify each first)**

```bash
gh pr list --state merged --limit 20 --json headRefName -q '.[].headRefName'
# For each branch that appears BOTH in that list AND in `git branch -r`:
git push origin --delete <branch>
git branch -d feat/wave3b-plan-003-canon-adoption 2>/dev/null || true
```

Expected: `feat/wave3b-plan-003-canon-adoption`, `chore/plan-002-wave-3-adoption`, `docs/todo-trust-config-gap`, `fix/initial-review-findings`, and other merged `docs/adr-*` branches removed. Do NOT delete any branch not in the merged-PR list.

---

### Task 1 (PR-1): Operations reconcile — trust-gap decision + governance bookkeeping

**Files:**

- Modify: `TODO.md`
- Modify: `roadmap/ROADMAP.md:7,17,25`
- Modify: `HANDOFF.md:8-20`
- Create: `plans/PLAN-001_pre-first-run-remediation.md` (this file — committed here)

**Interfaces:** Produces the decided CI fix (Option A) that the founder-gated action (§ Founder-gated) executes; later PRs' merge flow depends on it.

- [ ] **Step 1: Reconcile TODO.md §1 on Option A and add two tracking items**

In `TODO.md`, replace the `**Recommendation:**` paragraph (lines 57-58) with:

```markdown
**DECIDED (2026-07-09, PLAN-001):** Option A — founder sets the
`AI_REVIEW_TOKEN` secret (PAT, `contents: read` on `aidoc-flow-operations`):
`gh secret set AI_REVIEW_TOKEN --repo vladm3105/aidoc-flow-engramory`.
Option B (public trust config) remains an upstream `aidoc-flow-ci`
follow-up — NOT executable today: verified 2026-07-09 that aidoc-flow-ci
HEAD (> ci/v1.6.0) still checks out private `aidoc-flow-operations@main`
in the trust job; no canon version reads a local consumer config.
Reconciles the contradicting fix prescribed in `roadmap/ROADMAP.md`
"Known issue" (also corrected in PLAN-001 PR-1).
```

Append two new sections at end of file:

```markdown
## 2. 🟡 F5 server-side follow-up — reviewer App install + branch protection

**Status:** open, founder-gated. The unified-CI merge gate is dormant until
the reviewer App is installed and `apply-standards.sh --apply` adds
`call / verify` to branch-protection required contexts (F5 blast-radius
prerequisite per operations CLAUDE.md § Unified CI). Previously tracked only
as a CHANGELOG bullet; promoted here so it has an owner surface.

**Discovered.** 2026-07-08 (Wave 3); promoted 2026-07-09 (PLAN-001 review).

## 3. 🟡 Stranded Dependabot PRs #8 / #9 / #10

**Status:** open, blocked on §1. All three show `call / trust` FAIL +
`call / ai-review` SKIPPED; they accumulate weekly until §1 lands. After
`AI_REVIEW_TOKEN` is set, close-and-reopen (or push an empty commit to) each
PR to re-trigger the gate, then merge per repo convention. Note: PR #8's
group bump overlaps #9/#10 (checkout, setup-python) — merging #8 first may
auto-close the others.

**Discovered.** 2026-07-09 (PLAN-001 review).
```

- [ ] **Step 2: Fix ROADMAP known-issue paragraph + date drift**

In `roadmap/ROADMAP.md` replace line 25 (the `**Known issue (CI, operations-owned):**` paragraph) with:

```markdown
**Known issue (CI, operations-owned):** the shared `trust` gate fails on
every PR here — `ai-review.yml@ci/v1.4.3` fetches the trust allowlist from
the **private** `aidoc-flow-operations`, and this **public** repo's default
token cannot read it. PRs currently require admin-merge. **Fix (decided
2026-07-09, see `TODO.md` §1):** founder sets the `AI_REVIEW_TOKEN` repo
secret (Option A). Moving the trust config to a public location is an
upstream `aidoc-flow-ci` follow-up, not yet available in any tagged canon
version.
```

Change line 7 `*Last updated: 2026-07-08*` → `*Last updated: 2026-07-09*` and line 17 heading `## Current status (2026-07-07)` → `## Current status (2026-07-09)`. In the same Current-status section, make the eval harness an explicit exit criterion by appending to the `**Next (MVP-1):**` paragraph (line 23): `The eval harness + feedback loop are MVP-1 exit criteria, not stretch goals.`

- [ ] **Step 3: Update HANDOFF.md current state**

Replace the `## Current state (2026-07-08)` section body with:

```markdown
## Current state (2026-07-09)

**PLAN-003 Wave 3b adoption MERGED** — PR #12 squash-merged to main
2026-07-09 (67b12d2); merged branches pruned. **PLAN-001 pre-first-run
remediation underway** (`plans/PLAN-001_pre-first-run-remediation.md`):
a 2026-07-09 five-agent review found the SDD↔schema↔code contracts
drifted (StoragePort semantics, missing content_hash/kb_sections/status,
untracked learning loop) plus the known `call / trust` CI failure
(`TODO.md` §1, decided: Option A). PR sequence PR-1…PR-7; see the plan's
Task list (Tasks 1-7) for scope and checkbox status.
```

- [ ] **Step 4: Commit, review, push, PR**

```bash
git checkout -b fix/plan-001-pr1-ops-reconcile
git add TODO.md roadmap/ROADMAP.md HANDOFF.md plans/PLAN-001_pre-first-run-remediation.md
git commit -m "ops: reconcile trust-gap fix (Option A), track F5 + dependabot, PLAN-001 kickoff"
# OPS-0065 multi-agent review on the diff BEFORE push, then:
git push -u origin fix/plan-001-pr1-ops-reconcile && gh pr create --fill
```

Expected: `check` (CI) PASS, `call / verify` PASS, `call / trust` FAIL (known, pre-existing) → founder admin-merge.

---

### Task 2 (PR-2): Migration 0003 — contract reconciliation schema

**Files:**

- Create: `db/migrations/0003_reconcile_contracts.sql`
- Modify: `tests/test_migrations.py`

**Interfaces:**

- Produces: tables `memory_retrievals`, `audit_records`, `kb_sections`; columns `episodes.content_hash`, `memories.{status,retrieval_count,last_retrieved_at,source_trust,ts_lex}`; `tenant_id NOT NULL DEFAULT 'default'` on both base tables. PR-3/PR-4 SPEC edits reference these by exact name.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_migrations.py`; also update the ordered-list assertion)

```python
def test_migration_0003_reconciles_contracts() -> None:
    """0003 closes the SPEC-vs-schema gaps found in the 2026-07-09 review."""
    sql = (MIGRATIONS / "0003_reconcile_contracts.sql").read_text()
    for token in (
        "content_hash",        # SPEC-02 Episode idempotency key
        "status",              # SPEC-04 quarantine (EARS.01.03.b800)
        "source_trust",        # ranking trust signal
        "ts_lex",              # SPEC-03 lexical leg
        "memory_retrievals",   # feedback loop
        "audit_records",       # SPEC-01 AuditRecord
        "kb_sections",         # SPEC-02 KBSection / ADR-08 boundary rule 1
    ):
        assert token in sql, f"0003 missing '{token}'"


def test_migration_0003_enforces_tenant_wall() -> None:
    """ADR-07: tenant_id is the hard boundary — schema-enforced from 0003 on."""
    sql = (MIGRATIONS / "0003_reconcile_contracts.sql").read_text()
    assert sql.count("ALTER COLUMN tenant_id SET NOT NULL") == 2  # episodes + memories
    assert "idx_memories_tenant_live" in sql
```

In `test_migrations_exist_and_ordered`, extend the expected list with `"0003_reconcile_contracts.sql"`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_migrations.py -v`
Expected: FAIL — `FileNotFoundError` (0003 does not exist) and ordered-list mismatch.

- [ ] **Step 3: Write the migration** (`db/migrations/0003_reconcile_contracts.sql`)

```sql
-- 0003 — Contract reconciliation (pre-first-run). Closes the spec-vs-schema
-- gaps found in the 2026-07-09 review. Idempotent; additive except the
-- NOT NULL tightenings, which are safe pre-first-run (tables are empty; the
-- guarded UPDATEs make re-runs and non-empty dev DBs safe too).
-- Contracts backed: SPEC-01 (AuditRecord), SPEC-02 (Episode.content_hash,
-- KBSection, Memory.status), SPEC-03 (lexical leg, active-only default),
-- SPEC-04 (quarantine), ADR-07 (tenant wall), ADR-08 (kb_sections boundary).

-- 1. Episode idempotency key (SPEC-02 Episode.content_hash; BDD.01.03.c900).
ALTER TABLE episodes ADD COLUMN IF NOT EXISTS content_hash TEXT;
UPDATE episodes SET content_hash = md5(content_raw) WHERE content_hash IS NULL;
ALTER TABLE episodes ALTER COLUMN content_hash SET NOT NULL;

-- 2. Tenant wall becomes schema-enforced (ADR-07). 'default' = the
--    single-tenant Phase-0 tenant.
ALTER TABLE episodes ALTER COLUMN tenant_id SET DEFAULT 'default';
UPDATE episodes SET tenant_id = 'default' WHERE tenant_id IS NULL;
ALTER TABLE episodes ALTER COLUMN tenant_id SET NOT NULL;
ALTER TABLE memories ALTER COLUMN tenant_id SET DEFAULT 'default';
UPDATE memories SET tenant_id = 'default' WHERE tenant_id IS NULL;
ALTER TABLE memories ALTER COLUMN tenant_id SET NOT NULL;

-- Idempotency is per write scope; coalesce() because project_id is nullable
-- and Postgres unique indexes treat NULLs as distinct.
CREATE UNIQUE INDEX IF NOT EXISTS uq_episodes_idempotency
    ON episodes (tenant_id, agent_id, coalesce(project_id, ''), content_hash);

-- 3. Memory lifecycle status (SPEC-04 quarantine -> EARS.01.03.b800).
--    Default retrieval serves ONLY status = 'active'.
ALTER TABLE memories ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'active';
ALTER TABLE memories DROP CONSTRAINT IF EXISTS chk_memories_status;
ALTER TABLE memories ADD CONSTRAINT chk_memories_status
    CHECK (status IN ('active', 'advisory', 'quarantined', 'superseded'));

-- 4. Feedback-loop usage signals + authorship trust for ranking.
ALTER TABLE memories ADD COLUMN IF NOT EXISTS retrieval_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE memories ADD COLUMN IF NOT EXISTS last_retrieved_at TIMESTAMPTZ;
ALTER TABLE memories ADD COLUMN IF NOT EXISTS source_trust TEXT NOT NULL DEFAULT 'agent';
ALTER TABLE memories DROP CONSTRAINT IF EXISTS chk_memories_source_trust;
ALTER TABLE memories ADD CONSTRAINT chk_memories_source_trust
    CHECK (source_trust IN ('human', 'tool', 'agent'));

-- 5. Lexical leg for hybrid retrieval (SPEC-03 rank fusion).
ALTER TABLE memories ADD COLUMN IF NOT EXISTS ts_lex tsvector
    GENERATED ALWAYS AS (
        to_tsvector('english', coalesce(summary, '') || ' ' || content_raw)
    ) STORED;
CREATE INDEX IF NOT EXISTS idx_memories_lex ON memories USING gin (ts_lex);

-- 6. Tenant-leading indexes. The partial index IS the default read path:
--    live (valid_to IS NULL), active-only.
CREATE INDEX IF NOT EXISTS idx_memories_tenant_live
    ON memories (tenant_id, scope, type)
    WHERE valid_to IS NULL AND status = 'active';
CREATE INDEX IF NOT EXISTS idx_episodes_tenant ON episodes (tenant_id, ts DESC);

-- 7. Retrieval log: every hit handed to an agent is recorded so outcomes
--    can drive the confidence-update rule (SPEC-04; MemoryPort.feedback).
CREATE TABLE IF NOT EXISTS memory_retrievals (
    id           UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    memory_id    UUID NOT NULL REFERENCES memories(id),
    tenant_id    TEXT NOT NULL,
    agent_id     TEXT NOT NULL,
    project_id   TEXT,
    retrieved_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    feedback     TEXT CHECK (feedback IN ('useful', 'not_useful', 'harmful')),
    feedback_at  TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_retrievals_memory
    ON memory_retrievals (memory_id, retrieved_at DESC);
CREATE INDEX IF NOT EXISTS idx_retrievals_tenant
    ON memory_retrievals (tenant_id, agent_id, retrieved_at DESC);

-- 8. Audit sink (SPEC-01 AuditRecord; EARS.01.03.b050).
CREATE TABLE IF NOT EXISTS audit_records (
    id         UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id  TEXT NOT NULL,
    agent_id   TEXT NOT NULL,
    project_id TEXT,
    action     TEXT NOT NULL,
    allowed    BOOLEAN NOT NULL,
    reason     TEXT NOT NULL,
    ts         TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_audit_tenant_ts ON audit_records (tenant_id, ts DESC);

-- 9. Knowledge-core canonical table (SPEC-02 KBSection; ADR-08 boundary
--    rule 1: memory tables never mix with kb_sections). Versioned +
--    permanent (CORES.md lifecycle): new versions append; nothing deleted.
CREATE TABLE IF NOT EXISTS kb_sections (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id       TEXT NOT NULL DEFAULT 'default',
    project_id      TEXT,
    domain_id       TEXT,
    scope           TEXT NOT NULL DEFAULT 'project',
    doc_id          TEXT NOT NULL,
    citation        TEXT NOT NULL,
    text            TEXT NOT NULL,
    version         INTEGER NOT NULL DEFAULT 1,
    embedding       VECTOR,
    embedding_model TEXT,
    embedding_dims  INTEGER,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
DROP TRIGGER IF EXISTS trg_kb_sections_updated_at ON kb_sections;
CREATE TRIGGER trg_kb_sections_updated_at
    BEFORE UPDATE ON kb_sections
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();
CREATE UNIQUE INDEX IF NOT EXISTS uq_kb_sections_version
    ON kb_sections (tenant_id, doc_id, citation, version);
CREATE INDEX IF NOT EXISTS idx_kb_sections_scope
    ON kb_sections (tenant_id, project_id, doc_id);
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_migrations.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Verify the SQL against a real Postgres (idempotency ×2)**

```bash
docker compose up -d postgres && sleep 5
make migrate && make migrate   # second run proves idempotency
docker compose exec -T postgres psql -U engramory -d engramory -c "\d memories" | grep -E "status|ts_lex|source_trust"
docker compose exec -T postgres psql -U engramory -d engramory -c "\dt" | grep -E "memory_retrievals|audit_records|kb_sections"
```

Expected: both `make migrate` runs exit 0; columns and tables listed.

- [ ] **Step 6: Commit, review, push, PR**

```bash
git checkout main && git pull --ff-only && git checkout -b fix/plan-001-pr2-migration-0003
git add db/migrations/0003_reconcile_contracts.sql tests/test_migrations.py
git commit -m "feat(db): migration 0003 — contract reconciliation (content_hash, status, tenant wall, feedback/audit/kb tables)"
# OPS-0065 review, then push + gh pr create --fill
```

---

### Task 3 (PR-3): SDD storage reconciliation (StoragePort semantics + IPLAN truth)

**Files:**

- Modify: `sdd/06_SPEC/SPEC-06_ports_portability.yaml:43-47,107`
- Modify: `sdd/06_SPEC/SPEC-02_memory_store.yaml:32-40,76-89,114`
- Modify: `sdd/08_IPLAN/IPLAN-02_memory_store.yaml`
- Modify: `sdd/08_IPLAN/IPLAN-01_access_surface.yaml:45`

**Interfaces:** Consumes migration 0003 table/column names from PR-2. Produces the corrected contract vocabulary ("Postgres driver, not StoragePort, for relational persistence") that PR-4 and PR-7 reuse verbatim.

- [ ] **Step 1: SPEC-06 — StoragePort means object storage**

Replace the `StoragePort` export entry (lines 43-47):

```yaml
    - name: "StoragePort"
      type: "class"
      signature: "class StoragePort(Protocol): ..."
      description: "Object storage (MinIO | GCS | Azure Blob): put/get/delete/exists for documents and artifacts. NOT the relational store — Postgres is the canonical spine (ADR-01, one-way) and is reached via the Postgres driver directly; cloud migration of the canonical store is a managed-Postgres connection swap, not an adapter swap."
      errors: []
```

Replace the first `constraints` entry (line 107) `"Core imports only ports — no vendor SDKs"` with:

```yaml
  constraints: ["Core imports only ports — no cloud-vendor SDKs; the Postgres driver is spine infrastructure (ADR-01), not a vendor SDK, and repositories use it directly", "Adapter set chosen by ENGRAMORY_PROFILE"]
```

- [ ] **Step 2: SPEC-02 — repository over the Postgres driver; Memory gains status**

Line 35 diagram: `Repository --> StoragePort` → `Repository --> PostgresCanonical`.
Line 39 dependency: replace

```yaml
    - { name: "StoragePort (SPEC-06)", version: "internal", purpose: "Relational persistence" }
```

with

```yaml
    - { name: "Postgres canonical store (ADR-01)", version: "internal", purpose: "Relational persistence via the Postgres driver — not behind a port; see SPEC-06 StoragePort note" }
```

Line 114 patterns: `"Repository pattern over StoragePort/VectorPort"` → `"Repository pattern over the Postgres driver (canonical) + VectorPort (projection)"`.

In the `Memory` data model (after the `confidence` field, line 86), insert:

```yaml
        - { name: "status", type: "str", required: true, description: "active|advisory|quarantined|superseded — non-active excluded from default retrieval (EARS.01.03.b800); schema: migration 0003" }
        - { name: "source_trust", type: "str", required: true, description: "human|tool|agent — authorship trust signal used by SPEC-03 ranking; schema: migration 0003" }
```

- [ ] **Step 3: IPLAN-02 — make the manifest truthful**

- `file_manifest.files`: insert after the 0001 entry (line 21):
  `- { path: "db/migrations/0003_reconcile_contracts.sql", order: 4, status: DONE, session: "plan-001-remediation", verified: true }`
  and renumber the two entries that follow: `src/engramory/core/repository.py` order 4 → **5**, `tests/integration/test_repository.py` order 5 → **6**. 0001 keeps its existing `order: 3`; the list stays strictly ordered. Update `estimated_files: 5` → `6`.
- Line 32 implementation comment → `"# Implement src/engramory/core/models.py then repository.py over the Postgres driver + VectorPort"`.
- Line 44 consumed dependency → `- { name: "VectorPort", from: "@spec: SPEC-06", note: "vector index projection; relational persistence is the Postgres driver directly (SPEC-02)" }`.
- Line 51-53 session note: `partial_work` → `"Schema scaffold (0001) + contract reconciliation (0003) exist; models/repository not implemented."`; `blockers` → `"Depends on IPLAN-06 (VectorPort)."`.
- `code_inventory.files`: add `- { path: "db/migrations/0003_reconcile_contracts.sql", status: created, session: "plan-001-remediation", verified: true }`.
- `document_control.last_updated` → `"2026-07-09T00:00:00"`.

- [ ] **Step 3b: IPLAN-01 — remove the stray StoragePort read/write dependency**

`sdd/08_IPLAN/IPLAN-01_access_surface.yaml:45`: replace the consumed-dependency entry
`- { name: "MemoryPort / StoragePort", from: "@spec: SPEC-06", note: "scoped reads/writes" }`
with
`- { name: "MemoryPort", from: "@spec: SPEC-06", note: "scoped reads/writes; relational persistence is the Postgres driver (SPEC-02) — StoragePort is object storage only" }`

- [ ] **Step 4: Validate + commit**

```bash
python3 - <<'EOF'
import yaml, glob
for f in glob.glob("sdd/**/*.yaml", recursive=True):
    yaml.safe_load(open(f))
print("yaml ok")
EOF
grep -rn "Relational persistence" sdd/06_SPEC/SPEC-06_ports_portability.yaml && echo "FAIL: stale wording" || echo "clean"
git checkout main && git pull --ff-only && git checkout -b fix/plan-001-pr3-sdd-storage
git add sdd/06_SPEC/SPEC-02_memory_store.yaml sdd/06_SPEC/SPEC-06_ports_portability.yaml \
  sdd/08_IPLAN/IPLAN-02_memory_store.yaml sdd/08_IPLAN/IPLAN-01_access_surface.yaml
git commit -m "docs(sdd): StoragePort = object storage; repository over Postgres driver; IPLAN-02 manifest reflects 0003"
# OPS-0065 review, push, gh pr create --fill
```

---

### Task 4 (PR-4): Learning-loop + retrieval contracts (code + SDD, atomic)

**Files:**

- Modify: `src/engramory/ports/memory.py`
- Modify: `src/engramory/mcp/server.py`
- Modify: `tests/test_ports.py`
- Modify: `sdd/06_SPEC/SPEC-01_access_surface.yaml:44-64`
- Modify: `sdd/06_SPEC/SPEC-03_retrieval_graph.yaml:44-48,56-64,75-78`
- Modify: `sdd/06_SPEC/SPEC-04_distillation_engine.yaml:44-50,67-70`
- Modify: `sdd/02_PRD/PRD-01_engramory_core.yaml:232-239`
- Modify: `sdd/04_BDD/BDD-01_engramory_core.yaml`
- Modify: `sdd/07_TDD/TDD-04_distillation_engine.yaml`

**Interfaces:**

- Consumes: `memory_retrievals` / `audit_records` / `status` / `source_trust` names from PR-2; "Postgres driver" vocabulary from PR-3.
- Produces: `MemoryPort` methods `reflect`, `feedback`, `forget`, `get_profile`; `search(..., include_advisory: bool = False, token_budget: int | None = None)`; MCP tools `memory_feedback`, `memory_forget`, `agent_profile_get`; threshold `PRD.01.perf.context_token_budget` = 2000 tokens.

- [ ] **Step 1: Write the failing tests** (in `tests/test_ports.py`, replace `test_ports_are_protocols_and_importable` and add one test)

```python
def test_ports_are_protocols() -> None:
    for name in EXPECTED_PORTS:
        port = getattr(ports, name)
        assert inspect.isclass(port), f"{name} should be a class"
        assert getattr(port, "_is_protocol", False), f"{name} should be a typing.Protocol"


def test_memory_port_learning_loop_surface() -> None:
    """The learning loop (SPEC-01/03/04) is part of the port contract."""
    for method in ("add_episode", "search", "reflect", "consolidate",
                   "feedback", "forget", "get_profile"):
        assert hasattr(ports.MemoryPort, method), f"MemoryPort missing '{method}'"
    sig = inspect.signature(ports.MemoryPort.search)
    for param in ("include_advisory", "token_budget"):
        assert param in sig.parameters, f"MemoryPort.search missing '{param}'"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_ports.py -v`
Expected: FAIL — `MemoryPort missing 'reflect'` (and `'feedback'`).

- [ ] **Step 3: Extend `src/engramory/ports/memory.py`**

Rename `distill` → `reflect` (same signature/docstring intent; SPEC-04 is the contract source and no implementation exists yet). Extend `search` and append three methods:

```python
    async def search(self, *, query: str, agent_id: str | None = None,
                     project_id: str | None = None, domain_id: str | None = None,
                     tenant_id: str | None = None, scope: str = "agent",
                     k: int = 8, include_advisory: bool = False,
                     token_budget: int | None = None) -> Sequence[Mapping[str, Any]]:
        """Retrieve top-k distilled memories relevant to `query`, visible at `scope`
        (agent | project | domain | space). Default excludes non-active memories
        (advisory/quarantined/superseded); `include_advisory=True` adds advisory only.
        `token_budget` caps the cumulative summary tokens returned (SPEC-03).
        Each hit carries a `retrieval_id` for `feedback`."""
        ...

    async def reflect(self, *, agent_id: str, project_id: str | None = None) -> int:
        """Reflection pass: promote recent episodes into long-term memories.
        Returns the number of memories written."""
        ...

    async def feedback(self, *, retrieval_id: str, outcome: str) -> None:
        """Record a retrieval outcome ('useful' | 'not_useful' | 'harmful').
        Drives the confidence-update rule (SPEC-04); 'harmful' quarantines."""
        ...

    async def forget(self, *, memory_id: str, reason: str,
                     superseded_by: str | None = None) -> None:
        """Retire a memory: end-date it and mark it superseded (soft — provenance
        retained, never hard-deleted). `superseded_by` links a correcting memory."""
        ...

    async def get_profile(self, *, agent_id: str) -> Mapping[str, Any]:
        """Load the agent's L3 identity profile (standing preferences) —
        step 1 of every task per MEMORY_DESIGN."""
        ...
```

- [ ] **Step 4: Update `src/engramory/mcp/server.py` docstring tool registry**

Replace the `Tools (...)` block with:

```text
Tools (authoritative registry: sdd/06_SPEC/SPEC-01 Access Surface):
    memory_search     — scoped retrieval of knowledge + distilled memory (token-budgeted)
    memory_add        — append an episode in the caller's scope (idempotent by content hash)
    memory_feedback   — record a retrieval outcome (useful | not_useful | harmful)
    memory_forget     — retire a memory (end-date + supersede; soft, audited)
    agent_profile_get — load the caller's L3 identity profile
    knowledge_ingest  — create/update an SDD-artifact KB entry (governed write; needs evidence)
    authorize         — per-call scope authorization (default deny)
```

- [ ] **Step 5: Run tests + static checks**

Run: `pytest && ruff check src && mypy src`
Expected: all PASS.

- [ ] **Step 6: SPEC-01 — add the three tools + token budget**

`memory_search` signature (line 47) → `"async def memory_search(ctx: ActorContext, query: str, k: int = 8, token_budget: int | None = None) -> list[MemoryHit]"` and append to its description: `Token budget defaults to @threshold:PRD.01.perf.context_token_budget. Deliberately does NOT expose include_advisory: advisory/quarantined memories are unreachable through the agent-facing surface (EARS.01.03.b800); include_advisory exists only on the internal MemoryPort/retrieve layer for ops and evaluation.`

After the `memory_add` export, insert:

```yaml
    - name: "memory_feedback"
      type: "async function"
      signature: "async def memory_feedback(ctx: ActorContext, retrieval_id: RetrievalId, outcome: str) -> None"
      description: "Record a retrieval outcome (useful | not_useful | harmful); drives the SPEC-04 confidence-update rule via the memory_retrievals log (migration 0003)."
      errors: ["AuthzError"]
    - name: "memory_forget"
      type: "async function"
      signature: "async def memory_forget(ctx: ActorContext, memory_id: MemoryId, reason: str) -> None"
      description: "Retire a memory in the caller's scope: end-date + mark superseded (soft; provenance retained). Audited."
      errors: ["AuthzError"]
    - name: "agent_profile_get"
      type: "async function"
      signature: "async def agent_profile_get(ctx: ActorContext) -> AgentProfile"
      description: "Load the caller's L3 identity profile (MEMORY_DESIGN task step 1)."
      errors: ["AuthzError"]
```

- [ ] **Step 7: SPEC-03 — real ranking spec + budget + retrieval_id**

`retrieve` signature (line 46) → `"async def retrieve(scope: Scope, query: str, k: int = 8, include_advisory: bool = False, token_budget: int | None = None) -> list[MemoryHit]"`.

`MemoryHit` model: append fields

```yaml
        - { name: "retrieval_id", type: "RetrievalId", required: true, description: "memory_retrievals row id (migration 0003) — pass to memory_feedback" }
        - { name: "token_count", type: "int", required: true, description: "summary token count, for budget accounting" }
```

Replace `patterns: ["Hybrid rank fusion (vector + graph signal)"]` (line 77) with:

```yaml
  patterns:
    - "Rank fusion: weighted Reciprocal Rank Fusion over three legs — vector cosine (w=0.5), lexical ts_rank over memories.ts_lex (w=0.3), recency (w=0.2); score = sum(w_leg / (60 + rank_leg)); graph signal, when GraphPort has a backend, joins as a fourth leg"
    - "Post-fusion multipliers: confidence (0..1) x scope proximity (agent 1.0, project 0.9, domain 0.8, space 0.7) x source_trust (human 1.0, tool 0.9, agent 0.8)"
    - "Default filter: status = 'active' AND valid_to IS NULL (partial index idx_memories_tenant_live, migration 0003); include_advisory=true adds status='advisory', never quarantined/superseded"
    - "Every returned hit writes a memory_retrievals row and carries its retrieval_id; token_budget (default @threshold:PRD.01.perf.context_token_budget) truncates by cumulative summary tokens"
```

- [ ] **Step 8: SPEC-04 — confidence rule, reflection triggers, secrets screen**

Append to `behavior.validation_rules`:

```yaml
    - { rule: "Reflection triggers: >=10 unprocessed episodes OR 30 min elapsed since the first unprocessed episode OR an explicit reflect() call — never 'project end' alone (continuous agents have no project end)", source: "@bdd: BDD.01.03.d400" }
    - { rule: "Confidence dynamics: distiller assigns initial confidence in [0,1]; on each feedback outcome v (useful=1.0, not_useful=0.25, harmful=0.0): c := clamp(c + 0.2*(v - c), 0, 1); status:='advisory' when c < 0.4, restored to 'active' when c >= 0.5 (hysteresis); a 'harmful' outcome sets status:='quarantined' immediately", source: "@ears: EARS.01.03.b800" }
    - { rule: "Secrets screen: before any episode or memory write, content is screened for credential material (key/token/password patterns + high-entropy strings); matches are rejected (or the span redacted) and an audit_records row is written", source: "@ears: EARS.01.04.c300" }
```

Append `"@ears: EARS.01.04.c300"` to `traceability.upstream.ears_references`. Add to `exports` a note on `reflect`: append to its description: `Port binding: MemoryPort.reflect (renamed from distill, PLAN-001). Validate this batch-reflect surface against the adopted engine's API (Mem0 write-time ADD/UPDATE/DELETE/NOOP) before building the adapter (IPLAN-04 precondition).`

- [ ] **Step 9: PRD-01 — token-budget threshold**

In `component_decomposition` → `memory-retrieval` → `thresholds` (after the `retrieval_p95` entry, line 239), append:

```yaml
        - key: "context_token_budget"
          full_id: "PRD.01.perf.context_token_budget"
          value: 2000
          unit: "tokens"
          source: "@brd: BRD-01"
```

- [ ] **Step 10: BDD-01 — secrets scenario + c060 two-way fix**

Under `scenarios.error` (after BDD.01.03.a700), insert:

```yaml
      - id: "BDD.01.03.g010"
        name: "Secret material is excluded from stored memory"
        tags: ["@scenario-type:error", "@p1-high", "@scenario-id:BDD.01.03.g010", "@security"]
        given: "an episode submission whose content contains a credential-like string"
        when: "the write is screened and the reflection pass runs"
        then: "THE system SHALL exclude the secret content from any stored episode or memory AND write an audit record for the exclusion"
        covers_ears: ["EARS.01.04.c300"]
        spec_trace: ["5 (Behavior — validation_rules)"]
```

Line 134 (`BDD.01.03.e500` `covers_ears`) → `["EARS.01.03.f600", "EARS.01.03.e020", "EARS.01.03.b050", "EARS.01.03.c060"]` (closes the one-way coverage mismatch; note in the matrix `_note` that c060 is a statistical bound whose full verification is the eval harness, the scenario covers the deny mechanism).

Append `- "@ears: EARS.01.04.c300"` to `traceability.upstream.ears_references` and a matrix item `- ears: "EARS.01.04.c300"` / `scenarios: ["BDD.01.03.g010"]`.

Update the matrix `_note` (line 216) so its coverage claim stays true once a §4 line enters the matrix: `"Bidirectional EARS→scenario coverage. Every EARS line is covered by >=1 scenario."` → `"Bidirectional EARS→scenario coverage. Every EARS §3 functional line is covered by >=1 scenario; §4 quality attributes are covered where scenario-testable (c300 → g010; a100/d400 verify via the eval/load harness, b200 via the e500/e020 authz scenarios). EARS.01.03.c060 is a statistical bound: e500 covers the deny mechanism, the rate bound verifies in the eval harness."`

- [ ] **Step 11: TDD-04 — secrets test case**

Under `test_cases.unit_tests.cases`, append:

```yaml
      - id: "TDD.04.04.c100"
        name: "credential-like content excluded from distilled memory, audited"
        spec_ref: "@spec: SPEC-04"
        target: "reflect"
        test_file: "tests/unit/test_secrets_screen.py"
        test_function: "test_secret_content_excluded"
        inputs: [{ name: "episode", type: "Episode", value: "content containing an API-key-like high-entropy string" }]
        expected_output: { type: "int", value: "0 memories written from the secret span; audit_records row emitted" }
        edge_cases: [{ condition: "secret embedded mid-sentence", expected: "memory stored with the secret span redacted OR write rejected entirely" }]
```

Add matching `test_mapping.scenarios` entry (`bdd_scenario: "@bdd: BDD.01.03.g010"`, unit test above, status pending), a `coverage_table` row `["BDD.01.03.g010", 1, 0, 0, 1]`, and append `"@bdd: BDD.01.03.g010"` / `"@ears: EARS.01.04.c300"` to upstream refs.

- [ ] **Step 12: Validate + commit**

```bash
python3 - <<'EOF'
import yaml, glob
for f in glob.glob("sdd/**/*.yaml", recursive=True):
    yaml.safe_load(open(f))
print("yaml ok")
EOF
pytest && ruff check src && mypy src
git checkout main && git pull --ff-only && git checkout -b fix/plan-001-pr4-learning-loop
git add src/engramory/ports/memory.py src/engramory/mcp/server.py tests/test_ports.py \
  sdd/06_SPEC/SPEC-01_access_surface.yaml sdd/06_SPEC/SPEC-03_retrieval_graph.yaml \
  sdd/06_SPEC/SPEC-04_distillation_engine.yaml sdd/02_PRD/PRD-01_engramory_core.yaml \
  sdd/04_BDD/BDD-01_engramory_core.yaml sdd/07_TDD/TDD-04_distillation_engine.yaml
git commit -m "feat(contracts): learning loop (feedback/forget/profile), ranking spec + token budget, secrets-exclusion trace"
# OPS-0065 review, push, gh pr create --fill
```

---

### Task 5 (PR-5): Infra + ops-file cleanup

**Files:**

- Modify: `docker-compose.yml:28,43,55,65`
- Modify: `Makefile:1-2` (+ new target)
- Modify: `.github/CODEOWNERS:36-37`
- Modify: `tests/test_migrations.py:22`

**Interfaces:** none consumed/produced (leaf task).

- [ ] **Step 1: Pin the four unpinned/moving images**

Verified current tags for minio (2026-07-09, from `--version` inside pulled `latest`): server `RELEASE.2025-09-07T16-13-09Z`. Discover the remaining tags at execution time (they move):

```bash
docker run --rm minio/mc --version | head -1                          # → mc RELEASE tag
curl -s 'https://registry.hub.docker.com/v2/repositories/ollama/ollama/tags?page_size=25' \
  | jq -r '.results[].name' | grep -E '^[0-9]+\.[0-9]+\.[0-9]+$' | head -1   # → latest ollama semver
gh api --paginate repos/BerriAI/litellm/releases --jq '.[].tag_name' \
  | grep -E '^v[0-9.]+-stable$' | head -1
# → newest litellm -stable tag. --paginate is REQUIRED: stable tags lag
# mainline (~10 minor versions); the default 30-release page contains only
# -dev/-rc/plain-semver tags and the grep matches nothing (verified
# 2026-07-09: paginated → v1.83.14-stable; unpaginated → empty).
# Decision rule: pin the newest -stable (reproducibility over freshness —
# the stack is a dormant dev foundation, not a serving path).
# Do NOT use `gh api /users/BerriAI/packages/...` — needs read:packages
# scope, fails HTTP 403 under this repo's gh auth (verified 2026-07-09).
# Confirm the image tag exists before pinning:
#   docker manifest inspect ghcr.io/berriai/litellm:<tag> >/dev/null && echo ok
```

Then in `docker-compose.yml`: `image: minio/minio` → `image: minio/minio:RELEASE.2025-09-07T16-13-09Z`; `image: minio/mc` → `image: minio/mc:<discovered>`; `image: ollama/ollama` → `image: ollama/ollama:<discovered>`; `image: ghcr.io/berriai/litellm:main-latest` → `image: ghcr.io/berriai/litellm:<discovered-stable>`. Add a comment above the minio service: `# Healthcheck note: the server image bundles mc with a default 'local' alias — 'mc ready local' is MinIO's own recommended check (verified working 2026-07-09).` **Do NOT change the healthcheck** (review finding was a false positive).

- [ ] **Step 2: Makefile default goal + help**

At the top (after the `.PHONY` line — add `help` to it):

```make
.DEFAULT_GOAL := help

help:          ## Show available targets
 @grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-12s %s\n", $$1, $$2}'
```

Run: `make` → prints the target list (no longer silently starts the docker stack).

- [ ] **Step 3: CODEOWNERS — protect real files**

Replace lines 36-37 (`ops/DECISIONS.md`, `docs/REPO_STANDARDS.md` — neither exists here) with:

```text
DECISIONS.md                            @vladm3105
AGENTS.md                               @vladm3105
```

Also fix the header comments' dead refs (same defect class): line 1 `# CODEOWNERS — canonical shape per docs/REPO_STANDARDS.md §7.` → `# CODEOWNERS — canonical shape per aidoc-flow-ci REPO_STANDARDS.md §7.` and line 8 `#   - governance + umbrella tiers: CODEOWNERS review is REQUIRED` block's intro line `# Adoption per tier (see REPO_STANDARDS.md §2 branch-protection table):` → `# Adoption per tier (see aidoc-flow-ci REPO_STANDARDS.md §2 branch-protection table):`. Line 18's parenthetical `(CLAUDE.md, DECISIONS.md, REPO_STANDARDS.md)` → `(CLAUDE.md, DECISIONS.md, AGENTS.md)`.

- [ ] **Step 4: Tighten the substring test**

`tests/test_migrations.py:22`: replace `assert "space_id" not in init_sql` with:

```python
    import re
    assert not re.search(r"\bspace_id\b", init_sql)
```

- [ ] **Step 5: Verify + commit**

```bash
docker compose config -q && make && pytest
git checkout main && git pull --ff-only && git checkout -b fix/plan-001-pr5-infra-cleanup
git add docker-compose.yml Makefile .github/CODEOWNERS tests/test_migrations.py
git commit -m "chore(infra): pin all images, make help default goal, fix CODEOWNERS dead paths, tighten migration test"
# OPS-0065 review, push, gh pr create --fill
```

---

### Task 6 (PR-6): Documentation cleanup (non-governance docs)

**Files:**

- Modify: `docs/ARCHITECTURE.md:145,151-152,211`
- Modify: `docs/CORES.md:23-24,59`
- Modify: `docs/MEMORY_DESIGN.md:64,68,223`
- Modify: `docs/research/NEXUS_V3_REVIEW.md:54,98`
- Modify: `docs/research/MEMORY_CONCEPT_REVIEW.md` (status annotation at top)
- Modify: `README.md` (Documentation table)

**Interfaces:** Consumes migration-0003 names and the PR-4 tool registry.

- [ ] **Step 1: ARCHITECTURE.md**

- Lines 151-152: replace `(The block above is the memory core. L0 knowledge-base sections live in a separate` / `` `kb_sections` table introduced with the knowledge/BRD-04 cycle — see `sdd/06_SPEC/SPEC-02`.) `` with:
  `(The block above is the memory core as of migration 0001/0002. Migration 0003 adds the contract-reconciliation surfaces — episodes.content_hash, memories.status/source_trust/ts_lex, memory_retrievals, audit_records — and the L0 kb_sections table (MVP-1/BRD-01 knowledge core; see sdd/06_SPEC/SPEC-02).)`
- Line 145: `agent_profiles(agent_id, display_name, standing_preferences jsonb, created_at)` → `agent_profiles(agent_id, display_name, standing_preferences jsonb, created_at, updated_at)`.
- Line 211 (verbatim anchor, backticks included): `` `postgres:16` (pgvector) · `redis` · `minio` · `ghcr.io/berriai/litellm` + `ollama` · `keycloak` `` → `` `postgres:16` (pgvector) · `redis` · `minio` · `ghcr.io/berriai/litellm` + `ollama` · `neo4j` · `keycloak` ``.
- Line 167 (memory processor bullet): append `**Default engine: Mem0 — decided in [STRATEGY.md](STRATEGY.md).**`

- [ ] **Step 2: CORES.md**

- Line 23 Schema row: Memory cell → `` `episodes`, `memories`, `agent_profiles`, `consolidation_runs`, `memory_retrievals` ``; Knowledge cell → `` `kb_sections` (migration 0003, MVP-1) ``.
- Line 24 MCP tools row: Memory cell → `` `memory_add`, `memory_search`, `memory_feedback`, `memory_forget`, `agent_profile_get` ``; Knowledge cell → `` `knowledge_ingest` (reads served through `memory_search` for MVP-1 — see boundary rule 4) ``.
- Line 59 boundary rule 4 → `**Separate MCP namespaces for writes** —`knowledge_ingest` vs `memory_*`. MVP-1 retrieval is deliberately unified:`memory_search` spans both cores in one scoped, ranked call (SPEC-01); a dedicated `knowledge_search`splits out when the knowledge core grows its own retrieval semantics.`

- [ ] **Step 3: MEMORY_DESIGN.md (retracted-claim hygiene)**

- Line 64: `prune low-value noise. Keeps L2 dense and bounded.` → `prune low-value noise. Keeps L2 dense and high-signal (the store may grow; the retrieved working set stays bounded — see ARCHITECTURE §"What endless means").`
- Line 68: `**Endless, because bounded.** Raw episodes are compressed into a roughly constant-size, high-signal long-term store. You retrieve from a dense library, not an ever-growing transcript.` → `**Endless, because compressed + retrieved.** Raw episodes are compressed into a dense, high-signal long-term store; the store grows without a fixed cap while each task retrieves only a small relevant slice. You retrieve from a dense library, not an ever-growing transcript.`
- Line 223: prefix the phrase `fold the L1/L2 tables into your own Neo4j+pgvector` with `(historical option — superseded by ADR-01/ADR-05: the canonical store is Postgres)` so the banner's supersession explicitly covers it.

- [ ] **Step 4: NEXUS_V3_REVIEW.md dead refs**

- Line 54: `` `layered-agent-memory-design.md` `` → `` [MEMORY_DESIGN.md](../MEMORY_DESIGN.md) (formerly layered-agent-memory-design.md) ``.
- Line 98 footer: `ai-knowledge-rac-review.md` → `RAC_REVIEW.md`; `layered-agent-memory-design.md` → `../MEMORY_DESIGN.md`; `agent-memory-review.md` → `MEMORY_LANDSCAPE.md`.

- [ ] **Step 5: MEMORY_CONCEPT_REVIEW.md status annotation**

Under the existing banner at the top, add:

```markdown
> **Status (2026-07-09, PLAN-001):** the contract-level fixes for D1
> (feedback loop), D2 (confidence dynamics), D4 partially (status +
> usage columns), and the quarantine/idempotency gaps now live in
> `db/migrations/0003_reconcile_contracts.sql` + SPEC-01/03/04 updates.
> Still open as design work: D3 (contradiction detection), D5 (query-type
> routing), D6 partially (reflection triggers specified in SPEC-04, not
> yet built), G1 (injection screen / self-poisoning gate beyond
> source_trust), G2 (cross-agent promotion/corroboration before `space`
> scope), G4 (context assembly beyond token_budget). This review governs
> those open items.
```

- [ ] **Step 6: README Documentation table**

Locate the Documentation table in `README.md`; add rows for `docs/UCX_INTEGRATION.md` and `docs/HOW_TO_USE_THE_FRAMEWORK.md` (mirroring the purpose text used in `docs/README.md` for those two files).

- [ ] **Step 7: Commit**

```bash
git checkout main && git pull --ff-only && git checkout -b fix/plan-001-pr6-docs-cleanup
git add docs/ARCHITECTURE.md docs/CORES.md docs/MEMORY_DESIGN.md \
  docs/research/NEXUS_V3_REVIEW.md docs/research/MEMORY_CONCEPT_REVIEW.md README.md
git commit -m "docs: reconcile kb_sections cycle, tool registry, retracted claims, dead refs"
# OPS-0065 review, push, gh pr create --fill
```

---

### Task 7 (PR-7): Governance refresh — **governance PR (OPS-0061: ≤3 surfaces, Rule-2 review mandatory)**

**Files (exactly 3 surfaces):**

- Modify: `CLAUDE.md:71-76` (+ governance table Plans row)
- Modify: `sdd/05_ADR/ADR-00_index.md:20-24,40`
- Modify: `sdd/05_ADR/ADR-02_ports_and_adapters.yaml:120`

- [ ] **Step 1: CLAUDE.md per-repo CI state**

Replace the `**Per-repo state (2026-07-06):**` paragraph with:

```markdown
**Per-repo state (2026-07-09):** public repo. Adopts unified CI via
`.github/workflows/ai-review.yml`@ci/v1.4.3 + `composition.yml`@ci/v1.3.0
+ `auto-merge-ai-prs.yml`@ci/v1.5.1 + `audit-trail.yml`@ci/v1.6.0 +
`standards-drift.yml` (fetches canon at ci/v1.6.0). Runner topology:
`ubuntu-latest` (matches existing `ci.yml`). Reviewer: `claude`
(subscription-auth via `CLAUDE_CODE_OAUTH_TOKEN`). Dormant until the
reviewer App is installed (F5 prerequisite — tracked in `TODO.md` §2).
Known: `call / trust` fails pending the `AI_REVIEW_TOKEN` secret
(`TODO.md` §1).
```

In the `## Per-repo governance` table, replace the `Plans` row (`Not adopted — …`) with:

```markdown
| Plans | `plans/` — repo-wide remediation/feature plans (`PLAN-NNN_slug.md`; adopted 2026-07-09 with PLAN-001). Substantive engineering delivery still flows through the `sdd/` lifecycle; a plan here orchestrates cross-cutting work the sdd layers don't own |
```

- [ ] **Step 2: ADR-00 index preamble**

In the blockquote (lines 20-24): `(and add ADR-06 governed-write/fail-closed and ADR-07 scope model, which have no docs/adr/ counterpart)` → `(and add ADR-06 through ADR-09, which have no docs/adr/ counterpart)`. Update `Last Updated` (line 40) and the frontmatter `last_updated` to `2026-07-09`.

- [ ] **Step 3: ADR-02 related-decision wording**

Line 120: `rationale: "StoragePort/VectorPort back onto the Postgres spine."` → `rationale: "The repository layer and VectorPort back onto the Postgres spine; StoragePort is object storage (SPEC-06)."`

- [ ] **Step 4: OPS-0061 Rule-2 adversarial self-review, then commit**

Dispatch a code-reviewer agent on the diff with the three required focus areas (dead refs / supersession completeness / status consistency). Fix load-bearing findings BEFORE push.

```bash
git checkout main && git pull --ff-only && git checkout -b fix/plan-001-pr7-governance
git add CLAUDE.md sdd/05_ADR/ADR-00_index.md sdd/05_ADR/ADR-02_ports_and_adapters.yaml
git commit -m "docs(governance): refresh CI state, adopt plans/ surface, fix ADR index/StoragePort wording"
git push -u origin fix/plan-001-pr7-governance && gh pr create --fill
```

Note: governance-tier PRs are excluded from auto-merge (`tier=spec`) — founder merges this one.

---

## Founder-gated actions (cannot be done by the AI)

1. **Create the PAT + set the secret (unblocks everything else):** fine-grained PAT, `contents: read` on `vladm3105/aidoc-flow-operations`, then `gh secret set AI_REVIEW_TOKEN --repo vladm3105/aidoc-flow-engramory`.
2. **F5:** install the reviewer App + run `apply-standards.sh --apply` (branch-protection contexts) — `TODO.md` §2.
3. **Dependabot PRs #8/#9/#10:** after (1), re-trigger checks and merge (#8 first; it may auto-close #9/#10).
4. **Admin-merge PR-1…PR-6** while (1) is pending (each will show the known `call / trust` FAIL).

## Deferred (recorded, deliberately NOT in this plan)

- **Degraded memory-read mode** (serve-nothing-but-continue when the store is down): contradicts EARS.01.03.c900 + ADR-06 fail-closed (both Accepted). Needs an ADR supersession discussion, not a silent contract edit. Candidate for the MVP-1 retro.
- **Bi-temporal columns, decay/forgetting policy, single-active-head supersession constraint, typed episode events:** pre-scale work, not pre-first-run (per MEMORY_CONCEPT_REVIEW disposition).
- **`knowledge_search` split, contradiction detection (D3), query-type routing (D5):** tracked by the MEMORY_CONCEPT_REVIEW status annotation (PR-6 Step 5).
- **aidoc-flow-ci public trust config (Option B):** upstream repo change; out of engramory scope.

## Claim ledger

| # | Claim | Symbol | Citation |
| --- | --- | --- | --- |
| 1 | Code defines StoragePort as object storage (put/get/delete/exists) | `class StoragePort(Protocol)` | src/engramory/ports/storage.py:10 |
| 2 | SPEC-06 mislabels StoragePort as relational | `Relational persistence (canonical)` | sdd/06_SPEC/SPEC-06_ports_portability.yaml:46 |
| 3 | SPEC-06 constraint bans vendor SDKs in core | `Core imports only ports — no vendor SDKs` | sdd/06_SPEC/SPEC-06_ports_portability.yaml:107 |
| 4 | SPEC-02 depends on StoragePort for relational persistence | `"Relational persistence"` | sdd/06_SPEC/SPEC-02_memory_store.yaml:39 |
| 5 | SPEC-02 pattern builds Repository over StoragePort/VectorPort | `Repository pattern over StoragePort/VectorPort` | sdd/06_SPEC/SPEC-02_memory_store.yaml:114 |
| 6 | SPEC-02 diagram routes Repository → StoragePort | `Repository --> StoragePort` | sdd/06_SPEC/SPEC-02_memory_store.yaml:35 |
| 7 | SPEC-02 requires Episode.content_hash as idempotency key | `content_hash` | sdd/06_SPEC/SPEC-02_memory_store.yaml:75 |
| 8 | episodes table has no content_hash; columns end at metadata | `metadata     JSONB DEFAULT '{}'` | db/migrations/0001_init_memory.sql:40 |
| 9 | tenant_id is nullable on episodes | `tenant_id    TEXT,` | db/migrations/0001_init_memory.sql:36 |
| 10 | tenant_id is nullable on memories | `tenant_id        TEXT,` | db/migrations/0001_init_memory.sql:52 |
| 11 | memories has confidence but no status column | `confidence       REAL DEFAULT 0.5,` | db/migrations/0001_init_memory.sql:62 |
| 12 | idx_memories_scope leads with agent_id (no tenant leading index) | `idx_memories_scope` | db/migrations/0001_init_memory.sql:68 |
| 13 | set_updated_at() trigger function exists for reuse by kb_sections | `CREATE OR REPLACE FUNCTION set_updated_at()` | db/migrations/0001_init_memory.sql:18 |
| 14 | 0002 is additive domain_id only (no kb_sections anywhere in migrations) | `ADD COLUMN IF NOT EXISTS domain_id` | db/migrations/0002_add_domain_scope.sql:6 |
| 15 | Quarantine requirement: advisory-only below confidence floor | `EARS.01.03.b800` | sdd/03_EARS/EARS-01_engramory_core.yaml:134 |
| 16 | Secrets-exclusion P1 requirement exists in EARS §4 | `EARS.01.04.c300` | sdd/03_EARS/EARS-01_engramory_core.yaml:215 |
| 17 | BDD e500 covers_ears lacks c060 (one-way coverage) | `covers_ears: ["EARS.01.03.f600", "EARS.01.03.e020", "EARS.01.03.b050"]` | sdd/04_BDD/BDD-01_engramory_core.yaml:134 |
| 18 | BDD matrix maps c060 → e500 | `ears: "EARS.01.03.c060"` | sdd/04_BDD/BDD-01_engramory_core.yaml:246 |
| 19 | SPEC-01 exports exactly 4 tools (memory_search/memory_add/knowledge_ingest/authorize) | `- name: "authorize"` | sdd/06_SPEC/SPEC-01_access_surface.yaml:60 |
| 20 | SPEC-01 memory_search signature has no token budget | `async def memory_search(ctx: ActorContext, query: str, k: int = 8) -> list[MemoryHit]` | sdd/06_SPEC/SPEC-01_access_surface.yaml:47 |
| 21 | SPEC-01 defines AuditRecord with no backing table anywhere | `- name: "AuditRecord"` | sdd/06_SPEC/SPEC-01_access_surface.yaml:80 |
| 22 | SPEC-03 ranking spec is a single line | `Hybrid rank fusion (vector + graph signal)` | sdd/06_SPEC/SPEC-03_retrieval_graph.yaml:77 |
| 23 | SPEC-03 MemoryHit has 4 fields, no retrieval_id | `- name: "MemoryHit"` | sdd/06_SPEC/SPEC-03_retrieval_graph.yaml:58 |
| 24 | SPEC-04 exports `reflect` (not distill) | `async def reflect(agent_id: str, project_id: str \| None) -> int` | sdd/06_SPEC/SPEC-04_distillation_engine.yaml:48 |
| 25 | Code port method is named `distill` | `async def distill(self, *, agent_id: str, project_id: str \| None = None) -> int:` | src/engramory/ports/memory.py:31 |
| 26 | MemoryPort.search current signature (no include_advisory/token_budget) | `k: int = 8) -> Sequence[Mapping[str, Any]]` | src/engramory/ports/memory.py:26 |
| 27 | MCP docstring lists 4 tools, names SPEC-01 source of truth | `knowledge_ingest — create/update an SDD-artifact KB entry` | src/engramory/mcp/server.py:6 |
| 28 | PRD thresholds live under component_decomposition per component | `full_id: "PRD.01.perf.retrieval_p95"` | sdd/02_PRD/PRD-01_engramory_core.yaml:236 |
| 29 | TDD-04 unit-case shape (id/name/spec_ref/target/test_file/...) | `id: "TDD.04.04.a100"` | sdd/07_TDD/TDD-04_distillation_engine.yaml:25 |
| 30 | IPLAN-02 marks 0001 migration DONE | `status: DONE, session: "scaffold"` | sdd/08_IPLAN/IPLAN-02_memory_store.yaml:21 |
| 31 | IPLAN-02 directs repository over StoragePort/VectorPort | `repository.py over StoragePort/VectorPort` | sdd/08_IPLAN/IPLAN-02_memory_store.yaml:32 |
| 32 | IPLAN-02 blockers cite StoragePort | `Depends on IPLAN-06 (StoragePort/VectorPort).` | sdd/08_IPLAN/IPLAN-02_memory_store.yaml:52 |
| 33 | StoragePort refs outside SPEC-06 (independent-review grep 2026-07-09): SPEC-02×3, ADR-02×2, IPLAN-02×3 (lines 32/44/52), IPLAN-01×1 — IPLAN-03 has none; all misleading sites edited by PR-3/PR-7 (ADR-02:37 is untouched by design: it legitimately lists StoragePort among the port interfaces) | `MemoryPort / StoragePort` | sdd/08_IPLAN/IPLAN-01_access_surface.yaml:45 |
| 34 | ADR-02 claims StoragePort backs onto the Postgres spine | `StoragePort/VectorPort back onto the Postgres spine.` | sdd/05_ADR/ADR-02_ports_and_adapters.yaml:120 |
| 35 | ADR-00 preamble omits ADR-08/09 from no-counterpart note | `and add ADR-06 governed-write/fail-closed and ADR-07 scope model` | sdd/05_ADR/ADR-00_index.md:23 |
| 36 | minio/minio/ollama images unpinned; litellm on main-latest | `image: ghcr.io/berriai/litellm:main-latest` | docker-compose.yml:65 |
| 37 | minio healthcheck is `mc ready local` | `mc ready local` | docker-compose.yml:36 |
| 38 | `mc ready local` WORKS in the server image — empirical: `docker exec` printed "The cluster 'local' is ready", exit 0 (2026-07-09) | `mc ready local` | docker-compose.yml:36 |
| 39 | minio:latest server version RELEASE.2025-09-07T16-13-09Z (empirical `minio --version`, 2026-07-09) | `image: minio/minio` | docker-compose.yml:28 |
| 40 | Makefile has no help target; first target is `up` (bare `make` starts the stack) | `up:            ## Start the dev stack` | Makefile:13 |
| 41 | CODEOWNERS protects two paths that do not exist in this repo | `ops/DECISIONS.md` | .github/CODEOWNERS:36 |
| 42 | Weak substring assertion on space_id | `assert "space_id" not in init_sql` | tests/test_migrations.py:22 |
| 43 | Weak protocol test (isclass only) | `assert inspect.isclass(port), f"{name} should be a class"` | tests/test_ports.py:31 |
| 44 | TODO recommends Option A (AI_REVIEW_TOKEN secret) | `**Recommendation:** Option A as immediate unblock` | TODO.md:57 |
| 45 | ROADMAP prescribes a different fix (pin-bump to local-config canon) | `bump the ai-review pin to the aidoc-flow-ci version that reads a local` | roadmap/ROADMAP.md:25 |
| 46 | Canon trust job at HEAD still checks out private operations@main (so ROADMAP's fix is not executable) | `repository: vladm3105/aidoc-flow-operations` | aidoc-flow-ci/.github/workflows/ai-review.yml:107 *(cross-repo; resolve with `--root /opt/data/aidoc-flow`)* |
| 47 | ROADMAP date drift (7-08 header vs 7-07 status) | `## Current status (2026-07-07)` | roadmap/ROADMAP.md:17 |
| 48 | ROADMAP already scopes MVP-1 to feedback loop + eval harness + Mem0 | `Mem0` | roadmap/ROADMAP.md:23 |
| 49 | CLAUDE.md CI-state paragraph is dated 2026-07-06 with dead "(this PR)" deixis and omits audit-trail/standards-drift | `auto-merge-ai-prs.yml`@ci/v1.5.1 (this PR) | CLAUDE.md:73 |
| 50 | audit-trail.yml pins canon at ci/v1.6.0 | `audit-trail-check.yml@ci/v1.6.0` | .github/workflows/audit-trail.yml:12 |
| 51 | HANDOFF current state predates the PR #12 merge | `## Current state (2026-07-08)` | HANDOFF.md:8 |
| 52 | CLAUDE.md governance table says Plans "Not adopted" | `Plans` | CLAUDE.md:31 *(row in the Per-repo governance table; line hint approximate — symbol authoritative)* |
| 53 | ARCHITECTURE attributes kb_sections to the knowledge/BRD-04 cycle | `kb_sections` table introduced with the knowledge/BRD-04 cycle | docs/ARCHITECTURE.md:152 |
| 54 | ARCHITECTURE "today" compose list omits neo4j | `postgres:16` (pgvector) · `redis` · `minio` | docs/ARCHITECTURE.md:211 |
| 55 | ARCHITECTURE agent_profiles sketch omits updated_at | `agent_profiles(agent_id, display_name, standing_preferences jsonb, created_at)` | docs/ARCHITECTURE.md:145 |
| 56 | ARCHITECTURE engine list has no default (STRATEGY decides Mem0) | `**Dev / self-host:** **LangMem**` | docs/ARCHITECTURE.md:167 |
| 57 | CORES lists knowledge_search among tools (doesn't exist in SPEC-01) | `knowledge_ingest`, `knowledge_search` | docs/CORES.md:24 |
| 58 | CORES boundary rule 4 mandates separate namespaces incl. reads | `Separate MCP namespaces` | docs/CORES.md:59 |
| 59 | CORES schema row omits consolidation_runs; kb_sections "arrives with the knowledge cycle" (unnamed) | `arrives with the knowledge cycle` | docs/CORES.md:23 |
| 60 | MEMORY_DESIGN retains retracted "bounded"/"constant-size" claims | `**Endless, because bounded.**` | docs/MEMORY_DESIGN.md:68 |
| 61 | MEMORY_DESIGN closing advice suggests Neo4j+pgvector fold-in | `fold the L1/L2 tables into your own Neo4j+pgvector` | docs/MEMORY_DESIGN.md:223 |
| 62 | NEXUS review cites renamed file (dead ref) | `layered-agent-memory-design.md` | docs/research/NEXUS_V3_REVIEW.md:54 |
| 63 | NEXUS footer cites three renamed files (dead refs) | `ai-knowledge-rac-review.md` | docs/research/NEXUS_V3_REVIEW.md:98 |
| 64 | Neither `plans/` dir nor PLAN files existed before this plan (ls verified 2026-07-09: "no plans dir") | `plans/` | CLAUDE.md:31 *(governance-table row; dir created by this plan)* |
| 65 | Behavioral: pytest 6 passed, ruff clean, mypy clean, wheel builds (verified 2026-07-09 pre-plan baseline) | `pytest` | Makefile:43 |
| 66 | PR #12 (this branch) already squash-merged to main 2026-07-09; branch diff vs origin/main empty; 3 Dependabot PRs (#8/#9/#10) open with `call / trust` FAIL (gh verified 2026-07-09) | `migrate:` | Makefile:22 *(behavioral/GitHub-state claim; nearest stable repo anchor)* |

## Review log

### Pass 1 - 2026-07-09 - self-review (author)

Checked spec coverage against the five-agent review's finding list (every CRITICAL/MAJOR finding maps to a task or an explicit DROPPED/DEFERRED row in the disposition table); placeholder scan clean (no TBD/TODO-later; all code steps carry full code); type/name consistency pass (reflect/feedback/forget/get_profile identical across ports code, test, SPEC-01/04, MCP docstring; migration table/column names identical across 0003 SQL, tests, SPEC edits, docs edits). Two review findings were REVERSED during claim verification: MinIO healthcheck (false positive — verified working) and ROADMAP's trust fix (not executable — canon has no local-config version). **Result:** pending independent pass.

### Pass 2 - 2026-07-09 - independent

Fresh-context adversarial subagent verified every Claim-ledger row against source (60+ rows clean, both empirical reversals confirmed), the 0003 SQL against PG16+pgvector semantics, the distill→reflect rename safety (sole reference at ports/memory.py:31), edit-anchor uniqueness, ID-scheme fit (g010/TDD.04.04.c100), and PR-7's 3-surface bound. It returned 4 load-bearing findings — (1) unaccounted StoragePort ref at IPLAN-01:45 + wrong claim-33 counts, (2) self-contradictory IPLAN-02 order renumbering, (3) litellm tag-discovery command fails with HTTP 403 (verified) and would silently produce an empty tag, (4) G1/G2 finding-ID swap in the MEMORY_CONCEPT_REVIEW annotation — and 6 minor (dead "PR table" self-ref in HANDOFF text, non-verbatim ARCHITECTURE:211 anchor, BDD matrix `_note` scope, CODEOWNERS header-comment dead refs, undocumented include_advisory asymmetry at the MCP layer, claim-64 line hint). ALL 10 folded into the plan: Task 3 gains Step 3b (IPLAN-01) + explicit renumbering (0003→4, repository.py→5, test_repository.py→6); Task 5's discovery command replaced with `gh api repos/BerriAI/litellm/releases` + manifest-inspect guard and CODEOWNERS header-comment fixes added; Task 6's annotation now names G1 and G2 correctly; Task 1's HANDOFF text references the Task list; Task 4 documents the deliberate include_advisory asymmetry in SPEC-01 and updates the BDD matrix `_note`; ledger rows 33/64 corrected.

### Pass 3 - 2026-07-09 - independent (fold verification)

A second fresh-context subagent verified all 10 Pass-2 folds against source (anchors verbatim, IPLAN-02 orders 1-5 confirmed so the renumbering is consistent, G1/G2 titles correct, ARCHITECTURE:211 anchor character-exact, CODEOWNERS comment anchors verbatim, ledger rows 33/64 resolving) and executed the folded commands. It returned 1 LOAD-BEARING finding — the litellm discovery still yielded an empty tag because `-stable` releases sit beyond the default 30-item API page (newest at position 81) — plus 2 minor (overstated "all sites edited" clause in ledger row 33; the prior Pass-3 draft's evidence line for the litellm re-check was inaccurate). All folded: the discovery command now uses `--paginate` (author re-ran it: returns `v1.83.14-stable`), row 33 carves out ADR-02:37 explicitly, and this log entry replaces the inaccurate draft.

### Pass 4 - 2026-07-09 - independent (final)

Same fresh-context reviewer (context intact, independent of authorship) re-verified the three Pass-3 folds: the paginated command's output, the row-33 clause against its own grep counts, and this log's accuracy. Zero load-bearing findings. **Result:** ready
