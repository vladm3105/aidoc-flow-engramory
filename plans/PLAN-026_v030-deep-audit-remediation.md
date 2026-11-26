<!-- plan-gate: v2 -->
# PLAN-026: Engramory v0.3.0 Deep Audit Remediation (Waves P0–P3)

- **Status:** ready
- **Author:** Antigravity Agent + Operator
- **Date:** 2026-11-21
- **Source Audit:** `plans/AUDIT-2026-11-20_deep-review.md` (40 findings re-verified against `HEAD` `035abc4`: `11 Critical`, `16 High`, `8 Medium`, `5 Low`)
- **Supersedes (Remaining Track 3):** `plans/PLAN-025_paperclip-centralized-backlog-and-audit-governance.md`
- **Planned Chained Authorizing CHGs:**
  - Wave P0 (`TASK-1`): `docs/sdd/09_CHG/CHG-010_audit-wave-p0-security-isolation.yaml` (`SDD2C`, Weight `C3`, findings `C1`–`C8`, `H9`)
  - Wave P1 (`TASK-2`): `docs/sdd/09_CHG/CHG-011_audit-wave-p1-data-integrity-runtime.yaml` (`SDD2C`, Weight `C2`, findings `C9`–`C10`, `H1`–`H8`, `H10`–`H12`, `H15`)
  - Wave P2 (`TASK-3`): `docs/sdd/09_CHG/CHG-012_audit-wave-p2-infra-stack-ci.yaml` (`SDD2C`, Weight `C3`, findings `C11`, `H13`–`H14`, `M3`, `M6`, `M7`, `L2`, `L5`)
  - Wave P3 (`TASK-4`): `docs/sdd/09_CHG/CHG-013_audit-wave-p3-verification-perf-sdd.yaml` (`SDD2C`, Weight `C2`, findings `H16`, `M1`–`M2`, `M4`–`M5`, `M8`, `L1`, `L3`–`L4`)
- **Planned Governing IPLANs & Slices:** `docs/sdd/08_IPLAN/IPLAN-10..13_*.swf.yaml` + `docs/sdd/graph/SLICE-10..13_*.graph.yaml` (auto-ingested into Paperclip `v0.5.0` via `aiops-harness chg ingest-paperclip` from `PLAN-053`)
- **Target Branch:** `feature/plan-026-v030-deep-audit-remediation`
- **Selected Independent Review Lenses:** Google (Design Doc & SRE Invariants), Anthropic (Agent/Tool Contracts & Prompt Fencing), AWS (Well-Architected Least Privilege, Two-Way Doors & EaC), Microsoft (STRIDE Threat Modeling & CI Supply-Chain Integrity)

---

## 1. Executive Summary & 40-Finding Remediation Traceability

The `v0.3.0` deep review audit (`plans/AUDIT-2026-11-20_deep-review.md`, re-verified against `HEAD` `035abc4`) rated Engramory **`BLOCKER` for `stage` / multi-tenant production** and **`SHIP-WITH-FIXES` for local single-user dev**, documenting **40 verified defects** (`11 Critical`, `16 High`, `8 Medium`, `5 Low`) across scope/tenant isolation, host command sandboxing, MCP/OIDC transport security, Postgres/Neo4j state integrity, CAS concurrency, CI/stage governance, Windows portability, performance, and SDD graph consistency.

Because Tracks 1A & 1B of `PLAN-025` already shipped Paperclip `v0.5.0` (`48dba5a` in `platform-paperclip`, `6fd88b2` in `platform-stage-ops`) and `aiops-harness` shipped `PLAN-053` (`CHG-31`/`IPLAN-31`, `f827f0c`) providing native `chg ingest-paperclip`, `PLAN-026` replaces the remaining `PLAN-025` Track 3 governance work and drives direct, risk-ordered remediation across **4 chained CHGs (`CHG-010`–`CHG-013`)**:

| Task ID | Authorizing CHG | Wave & Priority | Finding Count | Covered Audit Findings (`40 / 40`) | Key Approval & Dependency Boundary |
|---|---|---|:---:|---|---|
| `TASK-1` | `CHG-010` | **Wave P0** (`blocker`) | **9** | `C1`, `C2`, `C3`, `C4`, `C5`, `C6`, `C7`, `C8`, `H9` | Pure `aidoc-flow-engramory` code/SDD/test/migration `0011` (`0004`–`0006` guard fix) + already-allowlisted `fastapi`/`starlette`/`uvicorn`/`httpx` + `SDD2C` amendment to `SPEC-04:80` / `EARS.01.03.b800` |
| `TASK-2` | `CHG-011` | **Wave P1** (`critical` / `high`) | **14** | `C9`, `C10`, `H1`, `H2`, `H3`, `H4`, `H5`, `H6`, `H7`, `H8`, `H10`, `H11`, `H12`, `H15` | Pure `aidoc-flow-engramory` data-integrity, NATS JetStream, CLI/MCP parity, Neo4j `$props` & migration `0012` fixes + atomic `watched_paths` doc updates |
| `TASK-3` | `CHG-012` | **Wave P2** (`high` / `medium`) | **8** | `C11`, `H13`, `H14`, `M3`, `M6`, `M7`, `L2`, `L5` | **Approval-gated:** requires operator `approval-gate` for protected `.github/` / `.ci-config` / `.gitleaks.toml`, `.aidoc/stack-policy.yaml` (all dev/optional deps incl. `psycopg-pool` & AGPL `pymupdf` replacement), and companion `platform-stage-ops` OpenTofu PR |
| `TASK-4` | `CHG-013` | **Wave P3** (`medium` / `low` + verification) | **9** | `H16`, `M1`, `M2`, `M4`, `M5`, `M8`, `L1`, `L3`, `L4` | Real PG18+Neo4j CI suite, `psycopg_pool` (after Wave P2 stack approval), Windows portability, parsers/FFmpeg, SDD parity (`8` graph violations -> `0`), portal/changelog reconciliation & migration `0013` |

When each `CHG-010..013` + `IPLAN-10..13_*.swf.yaml` is authored on execution branches, `aiops-harness chg ingest-paperclip` (`PLAN-053`) automatically mints its Root Goal and subtask DAG in `platform-paperclip` `v0.5.0` without manual backlog duplication.

---

## 2. 4-Bucket Platform Applicability Audit

| Bucket | Repositories / Canons | Role & Scope in `PLAN-026` |
|---|---|---|
| **Bucket 1 — Core Runtime Dependencies** | `platform-security` (Authentik OIDC `auth.int.aiops-flow.io`, Security Gate `:8095`/UDS revocation, Ed25519 `X-Agent-Warrant`), `platform-openbao` (`/dev/shm` `0600` ephemeral secrets), `platform-nats` (durable JetStream pull consumers + subject-bound `tenant_id`), PostgreSQL 18 + pgvector (`vector(1536)`, `FORCE ROW LEVEL SECURITY`, non-owner app role), Neo4j (scoped graph projection), `platform-litellm` (`1536d` embedding parity), `platform-paperclip` `v0.5.0` (tracked via `PLAN-053` `chg ingest-paperclip`) | Enforces multi-tenant isolation (`C1`–`C5`, `C10`, `H9`), fail-closed OIDC/JWT + MCP transport (`C7`, `H7`), durable JetStream event delivery (`H1`), OpenBao secret custody (`C11`, `H13`), and unified `1536d` embedding contracts (`H9`, `H12`, `H13`). |
| **Bucket 2 — Canons Embedded in `aiops-harness` (`canons.yaml`)** | `platform-docs`, `aidoc-context-tools`, `aidoc-flow-framework`, `aidoc-hybrid-ci`, `unified-test-suite`, `platform-technology-stack` (`.aidoc/stack-policy.yaml` strict allowlist) | Governs `DOC-FM-000`/`DOC-SEC-001`/`DOC-STACK-001` parity (`M6`, `M8`), SDD graph cardinality (`H11`, `H16`), authentic `harness.lock.json` digests (`H14`), strict Python dependency allowlisting (`H13`, `M1`), D23 scanner integrity (`H14`), and `CHG`/`IPLAN`/`SLICE` 3-way parity. |
| **Bucket 3 — Conditional Light Integrations** | Passive OTLP HTTP telemetry (`otel-collector` endpoint consistency & optional `OtlpHttpExporter`), `platform-stage-ops` (companion OpenTofu/`pyinfra` EaC PR replacing imperative `scripts/deploy-stage.sh`) | Aligns OTLP endpoint defaults and instrumentation (`M3`), and migrates stage deployment & OpenBao `/dev/shm` secret rendering out of imperative bash into `platform-stage-ops` (`C11`, `H13`). |
| **Bucket 4 — Excluded Non-Runtime / Stage-Only / Role-Specific Planes** | `platform-chatwoot`, `platform-superset`, `platform-backup-dr` (covered by host `stage-postgres18` pgBackRest), `platform-edge-gateway` public internet ingress | Excluded from runtime surface; Engramory MCP remains private internal mesh / loopback only (`127.0.0.1` / WireGuard mesh) with zero public internet exposure (`C7`, `C11`). |

---

## 3. Tier-1 Tactical Open Questions (4-Pillar Persona-Decomposed Matrix, 1.0–5.0)

### OQ-1: PostgreSQL Multi-Tenant RLS & Blob CAS Project Isolation (`C5`, `C10`, `H9`)

- **Option 1 *(Selected)*: Dual-Layer Defense-in-Depth (`allowed_scopes` + Ownership Predicates + `kb_blob_projects` + Coupled `FORCE ROW LEVEL SECURITY`)**
  - Enforce `allowed_scopes`, `agent_id`, `domain_id`, and `project_id` in repository and `PgVectorAdapter` SQL predicates, introduce `kb_blob_projects(tenant_id, project_id, sha256)` to authorize deduplicated cross-project reads without leaking uploader metadata, and ship migration `0011` (coupled with idempotency guards in `0004`–`0006`) enabling `FORCE ROW LEVEL SECURITY` paired atomically with `_conn(tenant_id=...)` / `_tx` (`SET LOCAL app.tenant_id`), non-owner `engramory_app` role, and dedicated `engramory_worker` role.
  - *Trade-off:* Higher migration rigor (requires non-owner role setup and `tests/conftest.py` non-superuser RLS fixtures), eliminated by paired `db/migrations/down/0011_*.sql` and role grants.
- **Option 2: Application-Level SQL Predicates Only (Retract RLS Docs)**
  - Fix SQL `WHERE` clauses and remove the RLS claim from `docs/MULTIMODAL_AND_POSTGRES_BLOB_STORAGE.md`.
  - *Trade-off:* Leaves a single missed `WHERE` predicate able to cross tenant boundaries at the DB layer.
- **Option 3: Dynamic Schema-per-Tenant DDL & Drop Cross-Project Blob Dedup**
  - Provision separate Postgres schemas per tenant and move `kb_blobs` PK to `(tenant_id, project_id, sha256)`.
  - *Trade-off:* Multiplies migration blast radius and duplicates identical blobs across projects in the same tenant.

| # | Option | 1. Arch & EaC | 2. Security & Blast | 3. User / Agent DX | 4. Platform Owner Ops | **Composite** |
|---|---|:---:|:---:|:---:|:---:|:---:|
| **1** | **(Selected) Dual-Layer SQL Predicates + `kb_blob_projects` + Coupled `FORCE ROW LEVEL SECURITY`** | **4.9** | **5.0** | **4.6** | **4.6** | **4.78** |
| **2** | Application-Level SQL Predicates Only (Retract RLS Docs) | 3.8 | 3.4 | 4.5 | 4.4 | **4.03** |
| **3** | Dynamic Schema-per-Tenant DDL & Drop Blob Dedup | 3.0 | 4.2 | 3.2 | 2.4 | **3.20** |

*Auto-selected **Option 1** (`4.78 / 5.00`).*

### OQ-2: `smoke_run_green_steps` Host Command Sandboxing (`C6`)

- **Option 1 *(Selected)*: Strict Argv + Subcommand/Argument Allowlist (`shell=False`) + Stage gVisor Policy + `INV-STEP-02` Failure Surfacing**
  - Parse green step commands with `shlex.split`, allow only explicit read-only binaries and safe subcommands/flags (`shell=False`, forbidding `-c`, `-m`, `--eval`, `uv run`, redirection, heredocs, pipes, and shell wrappers), bound stdout and timeouts, require `runsc --network=none` in stage/prod, and surface `INV-STEP-02` safety diagnostics as `status="error"` failures in `smoke_run_green_steps`.
  - *Trade-off:* Existing green runbooks/tests using shell chaining or `uv run` (`tests/test_docs_tools.py:223`, `docs/doc-tools/03_LINTING_GATES_AND_DRIFT_ENGINE.md`) must be updated atomically in Wave P0.
- **Option 2: Expanded Regex Denylist over `bash -c`**
  - Keep `bash -c` and add regex patterns for numbered redirections (`1>`, `2>`, `&>`), heredocs, and quotes.
  - *Trade-off:* Shell quoting, parameter expansion, and interpreter gadgets (`python3`, `env`, `awk`) remain trivially bypassable.
- **Option 3: Disable `smoke_run_green_steps` Execution (`dry_run=True` Only)**
  - Refuse all non-dry-run smoke execution permanently.
  - *Trade-off:* Breaks live runbook smoke verification gate across the platform.

| # | Option | 1. Arch & EaC | 2. Security & Blast | 3. User / Agent DX | 4. Platform Owner Ops | **Composite** |
|---|---|:---:|:---:|:---:|:---:|:---:|
| **1** | **(Selected) Strict Argv + Subcommand Allowlist (`shell=False`) + gVisor + `INV-STEP-02` Failures** | **4.8** | **5.0** | **4.4** | **4.7** | **4.73** |
| **2** | Expanded Regex Denylist over `bash -c` | 2.2 | 1.5 | 4.0 | 3.0 | **2.68** |
| **3** | Disable `smoke_run_green_steps` Execution (`dry_run=True` Only) | 3.5 | 4.8 | 2.3 | 3.2 | **3.45** |

*Auto-selected **Option 1** (`4.73 / 5.00`).*

### OQ-3: MCP HTTP/SSE Transport & Approval-Gated Stack Dependencies (`C7`, `H7`, `H13`, `M1`)

- **Option 1 *(Selected)*: Immediate Wave-P0 Migration to Already-Allowlisted `fastapi`/`starlette`/`uvicorn`/`httpx` + Isolate Unapproved Deps Behind Wave-P2 `approval-gate`**
  - Migrate MCP Streamable HTTP / persistent SSE and async cached OIDC revocation immediately in Wave P0 (`CHG-010`) using packages already allowlisted in `.aidoc/stack-policy.yaml` (`fastapi`, `starlette`, `uvicorn`, `httpx`), while gating `.aidoc/stack-policy.yaml` approval for `psycopg-pool`, `docling`/`beautifulsoup4`, dev-group tools, and AGPL-3.0 `pymupdf` replacement behind explicit Wave P2 (`CHG-012`) operator signoff alongside `ci.yml` `uv sync --locked` migration.
  - *Trade-off:* Requires updating `pyproject.toml`, `uv.lock`, and existing `create_http_server`/`check_token_revocation` tests inside Wave P0 and isolating connection pooling to Wave P3 (`CHG-013`).
- **Option 2: Hand-Roll Persistent SSE in `ThreadingHTTPServer` & Add `psycopg-pool`/`pypdf` Without Stack Approval**
  - Patch stdlib `ThreadingHTTPServer` for persistent SSE and add unallowlisted packages immediately.
  - *Trade-off:* Violates `.aidoc/stack-policy.yaml` `strict_allowlist: true` and keeps bespoke HTTP/timeout parsing fragile.
- **Option 3: Block All P0 Security Remediation Until External Stack-Policy PRs Merge**
  - Hold Wave P0 until upstream `platform-technology-stack` and `platform-stage-ops` approvals complete.
  - *Trade-off:* Leaves all 9 P0 security/isolation blockers open unnecessarily.

| # | Option | 1. Arch & EaC | 2. Security & Blast | 3. User / Agent DX | 4. Platform Owner Ops | **Composite** |
|---|---|:---:|:---:|:---:|:---:|:---:|
| **1** | **(Selected) Immediate Allowlisted `fastapi`/`uvicorn`/`httpx` in Wave P0 + Gate Unapproved Deps in Wave P2** | **5.0** | **4.9** | **4.8** | **4.8** | **4.88** |
| **2** | Custom `ThreadingHTTPServer` SSE + Unapproved `psycopg-pool`/`pypdf` | 2.1 | 3.0 | 3.5 | 2.6 | **2.80** |
| **3** | Block Wave P0 Until Upstream Stack-Policy PRs Merge | 4.0 | 2.0 | 2.8 | 3.5 | **3.08** |

*Auto-selected **Option 1** (`4.88 / 5.00`).*

### OQ-4: Chained Wave CHGs (`CHG-010`–`CHG-013`) vs. Monolithic Remediation

- **Option 1 *(Selected)*: 4 Chained Wave CHGs (`CHG-010` P0 -> `CHG-011` P1 -> `CHG-012` P2 -> `CHG-013` P3) with 3-Way SDD Parity & `PLAN-053` Ingestion**
  - Isolate security isolation (`CHG-010`), data integrity/runtime (`CHG-011`), approval-gated infra/CI/protected-paths (`CHG-012`), and test/performance/SDD parity (`CHG-013`) into independently verifiable, reversible waves automatically tracked in Paperclip via `aiops-harness chg ingest-paperclip`.
  - *Trade-off:* Mints 4 `CHG`/`IPLAN`/`SLICE` sets instead of 1, keeping blast radius bounded and approvals unblocked.
- **Option 2: Single Monolithic `CHG-010` Covering All 40 Findings**
  - Fix application, Postgres migrations, Docker, CI workflows, Astro portal, and SDD graph in one massive branch.
  - *Trade-off:* Blocks security patches behind external approvals and makes bisection/rollback hazardous.
- **Option 3: 40 Micro-CHGs (1 CHG per Audit Finding)**
  - Open 40 sequential `CHG`/`IPLAN`/`SLICE` triples across overlapping files (`surface.py`, `repository.py`, `server.py`).
  - *Trade-off:* Heavy ceremony and constant merge conflicts across shared modules.

| # | Option | 1. Arch & EaC | 2. Security & Blast | 3. User / Agent DX | 4. Platform Owner Ops | **Composite** |
|---|---|:---:|:---:|:---:|:---:|:---:|
| **1** | **(Selected) 4 Chained Wave CHGs (`CHG-010`–`CHG-013`) + `PLAN-053` Paperclip Ingestion** | **5.0** | **4.9** | **4.7** | **4.9** | **4.88** |
| **2** | Single Monolithic `CHG-010` Across All 40 Findings | 3.3 | 2.6 | 3.2 | 2.9 | **3.00** |
| **3** | 40 Individual Micro-CHGs (1 per Finding) | 3.8 | 4.2 | 2.1 | 2.4 | **3.13** |

*Auto-selected **Option 1** (`4.88 / 5.00`).*

---

## 4. Tier-2: Plan Architecture Decision Scorecard (100-Point 5-Pillar Well-Architected Model)

> Passing bar: `>= 80 / 100` total score; every individual pillar `>= 50%` of its max weight.

| Pillar | Max Pts | Minimum Floor (`>= 50%`) | Option A: 4-Wave Chained Remediation + Defense-in-Depth RLS & Allowlisted FastAPI MCP *(Selected)* | Option B: Monolithic Patch (App-Only Filters, Custom HTTPServer) | Option C: Governance-First (`PLAN-025` Track 2/3 Before Remediation) |
|---|:---:|:---:|:---:|:---:|:---:|
| **1. Security, Fencing & Least Privilege** | 25 | `>= 12.5` | **24** *(Closes all 9 P0 escalation/injection/auth/RCE/vector isolation gaps immediately + `FORCE ROW LEVEL SECURITY` & fail-closed OIDC)* | **16** *(App-only filters leave single-bug tenant exposure and fragile stdlib HTTP parsing)* | **10** *(FAILS floor: defers all 11 Critical / 16 High findings behind doc moves)* |
| **2. Reliability, Rollback & Blast Radius** | 25 | `>= 12.5` | **23** *(Wave-scoped 2-way door releases, idempotent `0004`–`0006` + reversible `0011`–`0013` down-scripts outside `db/migrations/`, CAS locking, JetStream `PubAck` & DLQ)* | **12** *(FAILS floor: massive 40-finding diff entangles migrations, CI, and app fixes)* | **18** *(Low code risk initially, but production remains broken on `GitSyncEngine`/`CAS`)* |
| **3. Operational Excellence & EaC Convergence** | 20 | `>= 10.0` | **19** *(Respects `.aidoc/stack-policy.yaml`, isolates protected-path/OpenTofu approvals to Wave P2, preserves D23 SAST control, uses `PLAN-053` native CHG ingestion)* | **11** *(Mixes unapproved packages and bypasses approval boundaries)* | **16** *(Maintains governance ceremony while leaving CI/stage broken)* |
| **4. Developer & Agent Experience (DX)** | 15 | `>= 7.5` | **14** *(Clean FastAPI MCP Streamable HTTP/SSE, `AccessSurface`-unified CLI, Windows selector loop & `.as_posix()` parity)* | **9** *(Custom HTTPServer quirks and ongoing Windows/CLI divergence)* | **9** *(Requires manual 40-ticket Paperclip wiring without fixing agent tools)* |
| **5. Performance & Token/Resource Economy** | 15 | `>= 7.5` | **13** *(Unlocks HNSW/GIN/`ts_lex` indexes, eliminates N+1 queries, bounds `blob_get`/FFmpeg RAM, introduces approved `psycopg_pool`)* | **8** *(Keeps unpooled connections and `break`-starved `memory_search`)* | **7** *(FAILS floor: zero query/memory/token fixes delivered)* |
| **TOTAL SCORE** | **100** | `>= 80` | **93 / 100 (READY)** | **56 / 100 (BLOCKER)** | **60 / 100 (BLOCKER)** |

---

## 5. Phased Remediation Blueprint

### Phase 1: Wave P0 — Security, Tenant/Scope Isolation, Sandboxing & Auth Hardening (`TASK-1` / `CHG-010`)

**Scope:** Findings `C1`, `C2`, `C3`, `C4`, `C5`, `C6`, `C7`, `C8`, `H9` (9 findings; zero external repo dependencies; uses only already-allowlisted packages; updates Wave-P0 tests/docs atomically).

1. **Scope Pinning & Provenance Stripping (`C1`):**
   - In `AccessSurface.memory_add` (`src/engramory/access/surface.py:518`), unconditionally assign `merged_meta["scope"] = scope` (removing `if scope != "agent":`) and strip or reject caller-supplied reserved provenance/confidence keys (`effectiveness_score`, `producer`, `origin`, `agent_type`, `calibration_delta`).
   - Reconcile `reflect()` (`src/engramory/workers/distillation.py:126`) and `Repository.offboard_agent_space` so forged episode metadata can never promote to `space`/`domain`/`project` or evade agent offboarding deletion.
2. **Target Ownership, Write-Once/Reversible Feedback & `SPEC-04` Amendment (`C2`):**
   - Fetch target memory ownership in `memory_forget` / `Repository.retire_memory` (`src/engramory/core/repository.py:263`) and enforce `scope in ctx.scopes` plus `agent_id`, `project_id`, and `domain_id` boundaries before retiring.
   - Enforce `memory_retrievals.agent_id == ctx.agent_id` in `memory_feedback` / `Repository.record_feedback` (`src/engramory/core/repository.py:279`) and make outcomes write-once or delta-reversing to stop `useful`/`not_useful` confidence drift.
   - Author Phase-0 `SDD2C` amendment to `docs/sdd/06_SPEC/SPEC-04_distillation_engine.yaml:80` / `EARS.01.03.b800` / `TDD-04` gating shared-scope (`project`/`domain`/`space`) `harmful` auto-quarantine behind multi-signal thresholds or elevated scopes.
   - Call `self._enforce_domain_boundary` inside `AccessSurface.agent_roster_list` (`src/engramory/access/surface.py:687`) for non-`space` callers.
3. **Graph Traversal Domain/Project Isolation & Safe Property Unpacking (`C3`):**
   - Replace `eff_project_id = None` for `scope="domain"` in `AccessSurface.{graph_neighbors,context_slice,blast_radius,knowledge_search}` (`src/engramory/access/surface.py:397`) with strict propagation of `domain_id` and `allowed_scopes`.
   - Update `GraphPort` (`src/engramory/ports/graph.py:26`), Postgres CTEs (`find_neighbors`, `find_ancestors`, `find_descendants` at `src/engramory/core/repository.py:1859`), and Neo4j queries (`neighbors`, `ancestors`, `descendants` at `src/engramory/adapters/dev/graph_neo4j.py:185`, synced with `tests/unit/test_graph_neo4j.py:14`) to stop matching `project_id IS NULL` for `project`-only callers, filter edges and every intermediate recursive hop node by `project_id`/`domain_id`/`allowed_scopes`, and unpack `**row["properties"]` **before** fixed `"name"`/`"tenant_id"`/`"domain_id"` keys in both `repository.py:1859` and `src/engramory/workers/graph_projection.py:32`.
4. **Postgres Upsert Ownership Guards, Bulk Verification & `kb_runbook_steps` Composite PK (`C4`):**
   - Tighten all 7 `ON CONFLICT DO UPDATE` predicates (`upsert_kb_section`, `upsert_kb_sections_batch`, `upsert_kb_document` at `src/engramory/core/repository.py:2197`, `upsert_graph_entity`, `upsert_graph_relation`, `bulk_upsert_graph_entities` at `src/engramory/core/repository.py:1603`, `bulk_upsert_graph_relations`) so lower-scoped callers cannot claim `project_id IS NULL` rows or overwrite `space`/cross-`domain_id` rows.
   - Add `RETURNING`/rowcount validation on `upsert_graph_entity` and verify actual written counts/conflict mismatches in `bulk_upsert_graph_entities` and `bulk_upsert_graph_relations` (replacing unconditional `return len(entities)` / `len(relations)` over `cur.executemany`).
   - Author reversible migration `db/migrations/0011_wave_p0_isolation_and_rls.sql` migrating `kb_runbook_steps` PK (`db/migrations/0008_runbooks_and_doc_governance.sql:61`) to `(tenant_id, doc_id, step_uid)` atomically alongside updating `ON CONFLICT (tenant_id, doc_id, step_uid)` in `repository.py` (preventing Postgres `42P10`), rejecting `#` inside `doc_id`/`step_id`, and enforcing `step_uid == f"{doc.doc_id}#{step.step_id}"`.
5. **KB/Ambient Isolation, Migration `0004`–`0006` Idempotency & Coupled PostgreSQL RLS (`C5`):**
   - Guard `db/migrations/0004_ambient_gateway_view.sql:6`, `db/migrations/0005_pin_embeddings_hnsw.sql:50`, and `db/migrations/0006_resize_embeddings_1536.sql:5` with `pg_attribute` (`format_type(atttypid, atttypmod) != 'vector(1536)'`) and `pg_views` existence checks (using direct DDL or `$v$` dollar-quoting inside `0004`'s `DO $$` guard so `tests/test_migrations.py` `"status = 'active'"` check stays intact) so re-running all migrations alphabetically never drops `0011`'s view or triggers Postgres `42P16`.
   - Pass `allowed_scopes=ctx.scopes` into `Repository.search_kb_sections` (`src/engramory/core/repository.py:1184`) and `slice_runbook`, require `AND scope = 'project'` on `project_id` branches, add `agent_id` to both `kb_sections` and `kb_documents` (`db/migrations/0008_runbooks_and_doc_governance.sql:6`) in migration `0011`, and stop `knowledge_ingest` / `docs_ingest_runbook` from stamping `ctx.project_id` onto `domain`/`space` writes.
   - In `0011`, add `tenant_id` (+ RLS) to `consolidation_runs` (`db/migrations/0001_init_memory.sql:79`), and run `DROP VIEW IF EXISTS v_ambient_memory_active; CREATE VIEW v_ambient_memory_active WITH (security_invoker = true) AS ...` (`db/migrations/0006_resize_embeddings_1536.sql:34`) exposing `agent_id`, `domain_id`, and `provenance` so `platform-litellm-laya` and `FORCE ROW LEVEL SECURITY` isolate private rows while keeping active reflection queries (`tests/integration/test_distillation_embedding.py:67`) working.
   - Enable `FORCE ROW LEVEL SECURITY` with `NULLIF(current_setting('app.tenant_id', true), '')` policies across all tenant tables in `0011`, owned by a migrator role (`stage_bootstrap`/`engramory_migrator`), with `GRANT SELECT, INSERT, UPDATE, DELETE` + sequence `USAGE, SELECT` + `ALTER DEFAULT PRIVILEGES` for non-owner `engramory_app` (never `BYPASSRLS`), dedicated `engramory_worker` policy/role for multi-tenant `runner.py` / GC sweeps, and `tenant_id` propagation into `Repository._conn(tenant_id=...)` (`src/engramory/core/repository.py:103`) / `PgBlobStorageAdapter._conn(tenant_id=...)` (`src/engramory/adapters/dev/storage_pg.py:95`) + explicit `SET LOCAL app.tenant_id` re-application after **both** `commit()` (`storage_pg.py:415`) and `rollback()` (`storage_pg.py:510`) in `_put_known_stream` (plus `tests/integration/test_distillation_embedding.py:62` and `tests/integration/test_doc_ingest.py:65`), keeping dev DSN compatibility until Wave P2 role split.
6. **Graph Projection, `VectorPort` / `PgVectorAdapter` Isolation & `vector(1536)` Validation (`H9`):**
   - Parameterize `exclude_kb_labels: bool = True` on `Neo4jGraphAdapter.purge_stale_nodes` (`src/engramory/adapters/dev/graph_neo4j.py:406`, passing `False` for `reproject_all` and `True` for SDD-only reconcile), reconcile removed edges and child `SECTION`/`MEDIA_BLOB`/`CONCEPT` nodes on delete, share-scope `blob:`/`concept:` entities, and sanitize dotted SDD entity types (`V1.2` -> `V1_2`).
   - Update `VectorPort.delete` (`src/engramory/ports/vector.py:26`) and `PgVectorAdapter.delete` (`src/engramory/adapters/dev/vector_pg.py:116`) to require `tenant_id`, update `PgVectorAdapter.upsert` (`vector_pg.py:45-69`) to require and propagate `tenant_id` into `_conn(tenant_id=...)` (removing the unscoped `else:` branch), reject `tenant_id=None` wildcard bypasses and add `valid_to IS NULL` + `status='active'` filters in `PgVectorAdapter.query()`, and enforce `len(vec) == 1536` + `tenant_id` on `search_kb_sections`, `set_kb_section_embedding`, `set_kb_sections_embedding_batch` (`src/engramory/core/chunking.py:231`, `surface.py:1910`), and `chunking.ingest_file`.
7. **Read-Only Sandboxing for `smoke_run_green_steps` (`C6`):**
   - Replace `bash -c` + `_GREEN_BANNED_PATTERNS` (`src/engramory/core/docs_tools.py:228`) in `smoke_run_green_steps` (`src/engramory/core/docs_tools.py:1402`) with strict `shlex.split` **subcommand + argument-pattern allowlisting** (`shell=False`, explicitly rejecting `-c`, `-m`, `--eval`, `uv run`, redirection, heredocs, pipes, and shell wrappers) for dev/CI + gVisor (`runsc --network=none`) container policy in stage/prod.
   - Surface `INV-STEP-02` diagnostics from `parse_markdown_document` as hard failures (`status="error"`) in `smoke_run_green_steps`, and update `tests/test_docs_tools.py:223` + `docs/doc-tools/03_LINTING_GATES_AND_DRIFT_ENGINE.md` atomically in Wave P0.
8. **Compliant FastAPI MCP Transport, Fail-Closed OIDC & Audit Enforcement (`C7`):**
   - Add allowlisted `fastapi`, `starlette`, `uvicorn`, and `httpx` (`.aidoc/stack-policy.yaml:49`) via `uv add` + `uv.lock`; replace custom `ThreadingHTTPServer` `create_http_server` / `do_GET` / `do_POST` (`src/engramory/mcp/server.py:1404`) with compliant MCP Streamable HTTP / persistent SSE, pre-body `Authorization` validation, `/messages?session_id=...` routing, and strict rejection of `?token=` query params.
   - Refuse `mcp --auth` with `stdio` (`src/engramory/mcp/server.py:1594`) and `--host 0.0.0.0` without `--auth`; fail closed on `StoreUnavailable` in `sdd_context_slice` / `sdd_blast_radius`; require `evidence_ref` on `sdd_ingest`; move `docs_ingest_runbook` disk parsing after authz.
   - Harden `claims_to_actor_context` (`src/engramory/access/jwt_auth.py:193`) and `check_token_revocation` (`src/engramory/access/jwt_auth.py:248`): reject missing `tenant_id`/`project_id`, remove unconditional `or domain_id is not None` auto-granting `"domain"` scope (`src/engramory/access/jwt_auth.py:222`), enforce `is_superuser is True`, require `isinstance(memory_scopes, list)`, validate list types for `groups`/`namespaces`/`projects`, accept case-insensitive `Bearer`, switch revocation fetch to bounded async cached `httpx` with `isinstance(revoked_list, list)` guard, and enforce HTTPS on external revocation URLs while allowing loopback/UDS/internal mesh endpoints.
   - Update affected Wave-P0 HTTP/JWT/lifecycle tests atomically (`tests/unit/test_mcp_server.py:171`, `tests/unit/test_jwt_auth.py`, `tests/test_plan017_remediation.py:346`, `tests/test_virtual_employee_lifecycle.py:453`).
9. **Unicode, PEM Redaction, `llms-full.txt` & Symlink Containment (`C8`):**
   - Expand `_CTRL_AND_BIDI_RE` (`src/engramory/access/poisoning_guard.py:17`) with `U+061C`, `U+00AD`, `U+2060`, `U+034F`, and Variation Selectors (`U+FE00..U+FE0F`, `U+E0100..U+E01EF`); normalize NFKC + strip invisible codepoints before `detect_prompt_injection`, `detect_hard_secrets`, `_scrub_manifest_text`, and `sanitize_portable_text`.
   - Redact full multiline PEM key bodies, scrub `doc_id`/`title` headings in `export_repo_llms`, reject symlinks across every ancestor + leaf path before `.resolve()` (including `GitSyncEngine._sync_changed_doc_metadata` at `src/engramory/core/git_sync.py:610`), confine `docs/` export/portal scans strictly to `docs/`, and check `is_symlink()` on output files before writing.
10. **Phase 1 Verification, Atomic Docs & Rollback:** Add real-DB regression tests for `C1`–`C8` and `H9`, configure separate non-superuser `engramory_app` (tenant-scoped) and `engramory_worker` (multi-tenant sweep) test fixtures in `tests/conftest.py:90`, update affected DB/graph tests (`tests/conformance/test_adapters.py:74`, `tests/integration/test_repository.py:119`, `tests/integration/test_doc_ingest.py:65`, `tests/integration/test_access_surface.py:125`, `tests/integration/test_adr_knowledge_graph.py:68`), update `tests/test_migrations.py:11` for `0011`, update `watched_paths` docs (`docs/ARCHITECTURE.md`, `docs/MULTIMODAL_AND_POSTGRES_BLOB_STORAGE.md`, `docs/VIRTUAL_EMPLOYEE_ARCHITECTURE.md:9`, `docs/doc-tools/10_DOCUMENTATION_TOOLS_TECHNICAL_REFERENCE.md:43`, `docs/doc-tools/00_INDEX.md:27`) + `last_verified` atomically, keep down-SQL in `db/migrations/down/0011_wave_p0_isolation_and_rls.sql` (handling duplicate `(tenant_id, step_uid)` conflicts), and require `ruff`, `mypy`, `bandit`, `docs-drift`, and `pytest` green.

### Phase 2: Wave P1 — Data Integrity, Blob CAS Concurrency, JetStream & Runtime Safety (`TASK-2` / `CHG-011`)

**Scope:** Findings `C9`, `C10`, `H1`, `H2`, `H3`, `H4`, `H5`, `H6`, `H7`, `H8`, `H10`, `H11`, `H12`, `H15` (14 findings).

1. **Idempotent `GitSyncEngine.sync()` & Post-Validation Blob Storage (`C9`):**
   - Queue deletions only for `MODIFIED`/`DELETED` files in `GitSyncEngine.sync` (`src/engramory/core/git_sync.py:345`), handle `KeyError` idempotently around `Repository.delete_graph_entity` (`src/engramory/core/repository.py:1675`), pass `project_id` to `delete_kb_sections_for_doc`, and defer `storage.put_file()` until after `inspect_memory_write()` succeeds in both `GitSyncEngine` and `KnowledgeCore`.
2. **PostgreSQL Blob CAS Locking, Backfilled Project Isolation & Streaming (`C10`):**
   - Acquire a per-`(tenant_id, sha256)` advisory/row lock inside `PgBlobStorageAdapter._put_known_stream` (`src/engramory/adapters/dev/storage_pg.py:354`), short-circuit `status='ready'` without wiping live chunks, and re-apply `SET LOCAL app.tenant_id` across every transaction boundary (`storage_pg.py:415`).
   - Author reversible migration `db/migrations/0012_wave_p1_cas_projects_and_dedup.sql` adding `kb_blob_projects(tenant_id, project_id, sha256)` (`db/migrations/0010_multimodal_rag_and_blob_cas.sql:20`), **backfilling** rows from existing `kb_blobs WHERE project_id IS NOT NULL`, enabling `FORCE ROW LEVEL SECURITY` + `engramory_app`/`engramory_worker` policies/grants on `kb_blob_projects`, authorizing deduplicated cross-project reads in `blob_head`/`blob_get` without returning another project's metadata, aligning `put(key)`/`get(key)` semantics, bounding `blob_get` RAM buffers, validating contiguous `c_idx` + `self._semaphore` in `get_stream`, and refreshing `updated_at` during long uploads (`gc_orphan_chunks` at `src/engramory/adapters/dev/storage_pg.py:674`).
3. **Durable NATS JetStream, Variant Distillation & Worker Fault Isolation (`H1`, `H2`):**
   - Replace ephemeral 50ms Core NATS polling (`_drain_topic` at `src/engramory/adapters/dev/events_nats.py:162`) with durable JetStream pull consumers (`js.pull_subscribe`) + `js.publish()` `PubAck`, bind expected `tenant_id` to verified NATS subject namespaces in `run_worker_loop` (`src/engramory/workers/runner.py:39`), replace sticky `_use_memory_buffer = True` with backoff reconnect, fix `*` wildcard matching, and reject CRLF in NATS headers.
   - Coalesce `e.metadata->>'brain_variant'` in `Repository.get_unreflected_episodes` (`src/engramory/core/repository.py:572`), wire `llm.chat()` distillation and `consolidate()` (`core/memory.py`), retry transient embedding errors before marking episodes reflected, and isolate per-agent/episode failures into a worker DLQ.
4. **Brain Passport Integrity, `ExperienceEvent` & Domain Boundary Hardening (`H3`, `H4`, `H5`):**
   - Hard-enforce `verify_integrity=True`, `allow_type_override=False`, and required `schema_version` on MCP `agent_brain_import` (`src/engramory/access/surface.py:1065`), redact `agent_id`/`domain_id`/`brain_variant`, `Mapping` subclasses, DSNs/JWTs/IPv6 with word-bounded matching + fixed `< 3`-char handling (`poisoning_guard.py`) + NFKC/invisible-char stripping, screen `skills[].specializations`, exclude `mem.domain_id is None` when filtering domain exports in `agent_brain_export`, replace `MAX_K=100` Python filtering with SQL filtering + intra-passport dedup + single-transaction commit, and preserve `prefs["role"]`.
   - Enforce `ev.producer == ctx.agent_id` (unless `"space" in ctx.scopes`) across all scopes, propagate `"scope"` inside `ExperienceEvent.to_episode` (`src/engramory/core/experience.py:277`), validate ISO-8601 `occurred_at` and `math.isfinite(calibration_delta)`, fix falsy `event_id` checks in `add_episode` AND align `Repository.add_episode`'s fallback `SELECT` (`src/engramory/core/repository.py:153`) with `AND agent_id = %(agent_id)s AND coalesce(project_id, '') = coalesce(%(project_id)s, '')`, and rescope `uq_episodes_event_id` (`db/migrations/0009_tenant_profiles_and_experience_dedup.sql:35`) in migration `0012` to `(tenant_id, coalesce(project_id, ''), agent_id, (metadata->>'event_id')) WHERE NULLIF(metadata->>'event_id', '') IS NOT NULL`.
   - Enforce `_enforce_domain_boundary` on `department_focus` and `docs_ingest_runbook`, screen runbook metadata/frontmatter fields, fix `KnowledgeCore.ingest_documents_batch` (stop trusting frontmatter `domain`, prevent bypassing `docs_ingest_runbook` authz, and preserve distinct `doc_id` when `repo_root=None`), restrict `knowledge_ingest_media` (`src/engramory/access/surface.py:2124`) to binary/media extensions, and enforce ancestor + leaf symlink checks before `.resolve()`.
5. **CLI `AccessSurface` Routing, Async MCP Offload & Extraction/Parser Fixes (`H6`, `H7`, `H8`):**
   - Route all 6 bypassing CLI subcommands (`_cmd_knowledge_ingest` at `src/engramory/cli.py:397`, `_cmd_knowledge_sync_git`, `_cmd_knowledge_search`, `_cmd_sdd_ingest`, `_cmd_memory_ingest_event`, `_cmd_memory_learning_health`) through `AccessSurface` (registering `knowledge_sync_git` in `authz.KNOWN_ACTIONS` at `src/engramory/access/authz.py:15` and `SPEC-01`), pass `scope=doc.scope` in `_cmd_docs_ingest`, forward `domain_id` consistently across `surface.py`, and map storage/OS `PermissionError` cleanly in `cli.main()`.
   - Offload blocking disk/FFmpeg work with `asyncio.to_thread()`, lift `run_stdio` 64 KiB cap + support Windows stdio, handle MCP `ping`/notifications and `int()` coercions, forward `domain_id` in `McpServer` `docs_slice_runbook` and `McpServer._resolve_call_ctx` (`src/engramory/mcp/server.py:775`), stop `sdd_context_slice(format="digest")` from bypassing Postgres/Neo4j, exclude `status="draft"` in local runbook fallback, fix `readOnlyHint: False` on mutating tools, and register `blob_put`/`blob_get` cleanly.
   - Fix 64 KiB PDF fallback truncation in `SmartExtractionRouter.extract_file` (`src/engramory/core/extraction/router.py:58`), check file extensions before `@section:` sniffing in `DocumentDetector.detect` (`src/engramory/core/parsers/detector.py:165`), auto-stub both `src` and `dst` in `extract_sdd_graph`/`extract_adr_graph`, isolate/atomically write Docling caches per tenant with ZIP-bomb bounds, bound/close `PDFParser`, and skip empty sections cleanly.
6. **Neo4j `$props` Protection, Fail-Closed SDD, Search & Storage Fixes (`H10`, `H11`, `H12`, `H15`):**
   - Strip reserved keys (`uid`, `id`, `tenant_id`, `project_id`) before Neo4j `$props` writes and enforce `tenant_id`/`project_id` on Cypher match/delete; reject `:` in `tenant_id`.
   - Fail closed on git errors in `InvariantChecker` / `check_repo_drift` with `-c core.quotepath=false`, extend `protected_prefixes` in `check_contract_erosion` (`src/engramory/core/context_tools.py:1280`) to SDD specs/ADRs/EARS/BDD and SQL migrations, fix `GRAPH-CARD-001` PRD checks and `GRAPH-CARD-002` empty `seed_nodes`, and enforce hard `max_tokens` truncation in `render_context_digest`.
   - Filter `memory_learning_health` by `ctx.agent_id` for `scope="agent"`, wire `ts_lex` + `domain_id` into `Repository.get_memories` (`src/engramory/core/repository.py:660`), switch `memory_search` budget skip to `continue`, and clear stale KB embeddings on `text` updates in `upsert_kb_section{,s_batch}` (`set_kb_sections_embedding_batch` hardened in Wave P0).
   - Default `ScopedContractResolver(allow_sibling_lookup=False)` and persist `LocalFsStorageAdapter` (`src/engramory/adapters/dev/storage_local.py:37`) chunks to `self._root_dir` with mtime-based `older_than_seconds` GC.
7. **Phase 2 Verification, Atomic Docs & Rollback:** Run unit + real PG18/Neo4j/NATS regression tests covering all 14 Wave P1 findings, update `tests/test_plan012_remediation.py:164` (`delete_kb_sections_for_doc` with `project_id`), update `tests/test_migrations.py:11` for `0012`, update `watched_paths` docs (`docs/VIRTUAL_EMPLOYEE_ARCHITECTURE.md:9`, `docs/MULTIMODAL_AND_POSTGRES_BLOB_STORAGE.md`, `docs/doc-tools/10_DOCUMENTATION_TOOLS_TECHNICAL_REFERENCE.md:43`, `docs/doc-tools/00_INDEX.md:27`) + `last_verified` atomically, keep down-SQL in `db/migrations/down/0012_wave_p1_cas_projects_and_dedup.sql`, and require `task ci` green.

### Phase 3: Wave P2 — Approval-Gated Infra, Stack Policy, Portal & CI Governance (`TASK-3` / `CHG-012`)

**Scope:** Findings `C11`, `H13`, `H14`, `M3`, `M6`, `M7`, `L2`, `L5` (8 findings). **Precondition (`approval-gate`):** Obtain explicit operator signoff for (a) protected `.github/` / `.ci-config` / `.gitleaks.toml` edits (or upstream `aidoc-hybrid-ci` canon PRs), (b) `.aidoc/stack-policy.yaml` reconciliation across all optional/dev dependencies (`psycopg-pool`, `docling`, `beautifulsoup4`, AGPL-3.0 `pymupdf` replacement `pypdf`, and dev tooling), and (c) companion `platform-stage-ops` OpenTofu/`pyinfra` EaC PR.

1. **Docker/Stage EaC, Secret Custody & Container Hardening (`C11`, `H13`, `M3`):**
   - Fix `docker-compose.yml:18` PG18 volume mount (`pgdata:/var/lib/postgresql`), add non-owner `engramory_app`/`engramory_worker` role bootstrap, bind local ports to `127.0.0.1`, and replace unused Redis/Keycloak with Valkey/NATS.
   - Remove plaintext default secrets in `compose.platform.yaml` in favor of OpenBao `/dev/shm` `0600` tmpfs delivery + fail-closed `${VAR:?}`; set `ENGRAMORY_ALLOWED_DOCS_ROOT=/workspace`, canonical `auth.int.aiops-flow.io` (including `src/engramory/cli.py:1233`), and valid `ENGRAMORY_PROFILE` (`compose.platform.yaml:32`).
   - Retire imperative `scripts/deploy-stage.sh:25` via companion `platform-stage-ops` OpenTofu/`pyinfra` PR; unify 4-way `1536d` embedding config across `Makefile`, `config/litellm.config.yaml`, and compose files; lock `Dockerfile` to `uv.lock` + `ffmpeg` + approved PDF deps; unify `[dependency-groups].dev` and reconcile `.aidoc/stack-policy.yaml` + eliminate AGPL-3.0 `pymupdf` (`pyproject.toml:22`, keeping `COV_MIN: "80"` until Wave P3 enables real PG18+Neo4j CI services); fix `scripts/secret_scan.py` anchoring and token patterns.
   - Add container hardening (`cap_drop: [ALL]`, `no-new-privileges:true`, `read_only: true`, resource limits), remove legacy `/var/spool/interlog` from `Dockerfile:28` and `compose.platform.yaml`, wire minimal `OtlpHttpExporter` telemetry toggle (or document explicit `ADR`/`SPEC` deferral) + align OTLP collector env defaults, and fix worker heartbeat / MCP healthcheck timeouts and dependency probes.
2. **Governance Lockfile, Portal Build & SDD/UTS Gate Parity (`H14`, `M6`, `L5`):**
   - Regenerate authentic SHA-256 digests in `.aidoc/harness.lock.json:8` via `aiops-harness`.
   - Scaffold Astro 5 `portal/` (`src/content.config.ts`, `index.mdx`, `runbooks/`, `contracts/`, `@scalar/astro`, lockfile), fix `portal_builder.py` pruning/title synthesis, and move both `portal/astro.config.mjs:10` and `portal/package.json:9` (`--port 8005` in `dev`/`preview`) off `8005` to `4321`.
   - Update `.aidoc/Taskfile.yml` `sdd:check` (`HARNESS_ROOT` at `.aidoc/Taskfile.yml:20`): use `.venv` Python when `../aiops-harness` is present, and in standalone CI checkouts lacking `../aiops-harness` execute native `uv run engramory --json sdd validate-graph --invariants` + `uv run engramory --json docs lint --strict --verify-registry docs/doc-tools`, failing closed on non-zero exit (never printing `"skipping"` and exiting `0`); fix `unified-test-suite.config.json` (`ENGRAMORY_DATABASE_URL`, `smoke`, keeping `COV_MIN` alignment to `85` for Wave P3).
   - Preserve D23 inside `.github/actions/sast-scan/action.yml:221` (never weaken `.semgrepignore` deletion guard; configure any valid exclusions via upstream `aidoc-hybrid-ci` / Semgrep ruleset config and remove dead `.semgrepignore`).
   - Remove `bytebase` (and align `seaweedfs`) from `BANNED_RETIRED_TOOLS` (`src/engramory/core/docs_tools.py:74`) and `.vale/styles/AIOpsAgent/BannedRetiredTools.yml` per Platform Technology Stack, update `docs_tools.py:128` (`"docling + pymupdf + beautifulsoup4"`) when dropping AGPL-3.0 `pymupdf`, update `tests/test_docs_tools.py:193` (replacing `"deploy seaweedfs with bytebase"` with a genuinely retired tool), update `.aidoc/security.env.example` to `auth.int.aiops-flow.io` and sanitize placeholder token in `.aidoc/litellm.env.example`; fix `skills/engramory-memory/SKILL.md:20` SDD path and `.gitleaks.toml:26` 20-char AWS example regex (`L5`).
3. **CI Workflows & Promotion Gates (`C11`, `M7`, `L2`):**
   - Migrate `.github/workflows/ci.yml:19` (`pip install -e ".[dev]"`) to `uv sync --locked` (`.ci/taskfiles/python.yml`) and add `permissions:`, `concurrency:`, and `timeout-minutes:`.
   - Pin SHA-256 verification on `.github/workflows/standards-drift.yml:24`; delete **both** `if: hashFiles('.pre-commit-config.yaml') != ''` (`.github/workflows/quick-gates.yml:125`) and the `PRECOMMIT` skipped carve-out (`quick-gates.yml:212-215`), replace `./.github/actions/pre-commit` at `quick-gates.yml:127` with commit-SHA-pinned `jdx/mise-action` (`D5`) + full-tree `task check` (not `lefthook.yml:8` `--staged`, which scans empty diffs on PR CI), remove `.github/actions/pre-commit/` + `.pre-commit-config.yaml`, and gate retiring legacy v2 callers (`pre-commit.yml`, `markdown-lint.yml`, `links.yml`) on verifying branch protection requires `quick-gates`; fix `PROMOTION_TARGETS` in `.github/workflows/branch-promotion.yml:15` and `PROMOTION_BRANCHES="staging main"` in `.ci-config:16`; fix boolean precedence in `.github/workflows/bot-signoff.yml:25`.
   - Pin reusable workflows to immutable commit SHAs instead of `@ci/v1.9.5`, move `ubuntu-latest` / reusable callers to self-hosted runners and `dev` triggers, resolve `verify.yml` `TODO` stubs, fix `dependabot.yml` (`uv`/`/portal`), align `mise.toml` to Python `3.12`, skip checks on branch deletion in `pre_push_guard.sh`, fix `scripts/ingest_docs.py` `--scope` + non-zero failure exit, and fix `pyproject.toml:28` repository URL + `TRUFFLEHOG_VERIFY` typo in `nightly-audit.yml` (`L2`).
4. **Phase 3 Verification & Rollback:** Validate `docker compose config`, Astro portal production build, `secret_scan.py` positive/negative test cases, update `watched_paths` docs (`docs/doc-tools/07_PORTAL_API_CONTRACTS_AND_DEPLOYMENT_RUNBOOK.md:44`, `docs/doc-tools/10_DOCUMENTATION_TOOLS_TECHNICAL_REFERENCE.md:43`, `docs/doc-tools/00_INDEX.md:27`) + `last_verified` atomically, run `docs lint --strict`, and validate workflows (`actionlint`).

### Phase 4: Wave P3 — Real-DB CI Verification, DB Performance, Portability & SDD Reconciliation (`TASK-4` / `CHG-013`)

**Scope:** Findings `H16`, `M1`, `M2`, `M4`, `M5`, `M8`, `L1`, `L3`, `L4` (9 findings).

1. **Real-DB CI Suite, Coverage Floor & Replacing Theatrical Security Tests (`H16`):**
   - Add real PostgreSQL 18 + pgvector and Neo4j CI services to `.github/workflows/ci.yml`, remove the `GITHUB_ACTIONS` skip guard in `tests/conftest.py:68`, update local Docker fallback `pg16` -> `pg18`, and raise `Taskfile.yml:23` `COV_MIN: "80"` -> `"85"` + UTS coverage alignment (`85`) atomically once real PG18+Neo4j CI services run.
   - Replace remaining theatrical/divergent tests (`test_mcp_http_server_authenticated_endpoints`, `test_neo4j_rel_normalization`, `test_local_fs_storage_adapter_roundtrip_and_tenant_wall`, `InMemoryVirtualEmployeeRepo`, `test_failclosed.py`, `test_jwt_auth.py` edge cases) and extend `test_no_vendor_imports.py` so `ports/` enforces strict stdlib-only (`sys.stdlib_module_names`) while `access/` enforces zero vendor/adapter/DB imports (`psycopg`, `neo4j`, `nats`, `engramory.adapters`).
2. **DB Pooling, Indexable HNSW Queries & Migration `0013` (`M1`):**
   - Adopt Wave-P2-approved `psycopg_pool` in `Repository._conn` and widen `StoreUnavailable` to catch `InterfaceError`/`TimeoutError`; eliminate N+1 queries in `get_brain_variant_metrics` and remove `retrieval_count` side effects in `agent_brain_compare`; rewrite HNSW `ORDER BY` queries to remain indexable and escape `%`/`_` in `ILIKE`.
   - Author reversible migration `db/migrations/0013_wave_p3_indexes_and_hygiene.sql` adding the 5 missing hot-path indexes (`provenance->>'episode_id'`, `memories.provenance` GIN, `agent_profiles.standing_preferences` GIN, `graph_entities`/`graph_relations` `project_id` expression indexes), clearing stale `embedding_model` where `embedding IS NULL`, reconciling/cleaning mismatched `tenant_id` rows on `memory_retrievals` / `memories.supersedes` before adding `ALTER TABLE memories ADD CONSTRAINT uq_memories_tenant_id UNIQUE (tenant_id, id)` and tenant-scoped composite FKs, purging orphan chunks and adding `FOREIGN KEY (tenant_id, blob_sha256) REFERENCES kb_blobs(tenant_id, sha256) ON DELETE CASCADE` on `kb_blob_chunks` (`LOGGED`, <= 50 MiB) + an `AFTER DELETE ON kb_blobs` cleanup trigger for `kb_blob_chunks_large` (`UNLOGGED`, up to 25 GiB, avoiding per-chunk `FOR KEY SHARE` locks during `COPY BINARY` and updating `docs/MULTIMODAL_AND_POSTGRES_BLOB_STORAGE.md:166`), and enforcing `lock_timeout`; prevent dangling `pgblob://` URIs when `put_file()` fails.
3. **Ports, Profiles, Windows Portability & Core/Parser/Media Polish (`M2`, `M4`, `M5`):**
   - Route all 11 `cli.py` call sites through `adapters_for_profile()`, fix cross-loop adapter cleanup in `_cmd_mcp_serve`/`_cmd_worker_start`, and formally document/implement or retire `CachePort`/`SecretsPort`/`MemoryPort` and `gcp`/`azure` profile stubs.
   - Set `WindowsSelectorEventLoopPolicy` on Windows in `cli.py`, apply `.as_posix()` across `docs_tools.py`, `portal_builder.py`, and `git_sync.py`, accept CRLF frontmatter in `lint_repo`, and make `smoke_run_green_steps` cross-platform without WSL `bash.exe`.
   - Fix all media/FFmpeg, parser (`HTMLParser`, `TextParser`, `MarkdownParser`, `SemanticChunker`), `GitSyncEngine` (Gitea URLs, quotepath, dynamic `version`, upsert-before-delete), `watched_paths` glob confinement, `resolve_confined_repo_path`, `StackPolicyValidator`, `LiteLLMAdapter`, `knowledge_search` `k` upper bound, streaming `generate_evidence_ref`, metadata size caps, `exec_ready_score == 1`, `get_git_commit_hash` CWD, and `memory_promote` `embedding_model` defects.
4. **Dogfood SDD Graph Repair, Doc Drift & Remaining Low Polish (`M8`, `L1`, `L3`, `L4`):**
   - Resolve all 8 live `sdd validate-graph --invariants` cardinality violations (`EARS-01`, `BRD-02..BRD-08`) and reconcile `docs/sdd/08_IPLAN/IPLAN-00_index.yaml:8`, `IPLAN-03..08`, `docs/sdd/05_ADR/ADR-00_index.md:39` (`ADR-11`), `SPEC-01`, `docs/sdd/06_SPEC/SPEC-03_retrieval_graph.yaml:22`, `SPEC-04`, `SPEC-07`, `docs/sdd/graph/`, `docs/MULTIMODAL_AND_POSTGRES_BLOB_STORAGE.md`, `docs/archive/CHANGELOG.md`, and `AGENTS.md:177`.
   - Fix `\Z`/`.strip()` regex anchors (`L1`), `0o600` `.engramory/config.toml` permissions and exact-line `.gitignore` checks in `engramory init` (`L3`), and upward `engramory.access` imports + deep freezing + `KBBlob.chunk_size` + `models.py`/`smoke_preprod.sh` (`L4`).
5. **Phase 4 Verification & Rollback:** Update `tests/test_migrations.py:11` for `0013`, keep down-SQL in `db/migrations/down/0013_wave_p3_indexes_and_hygiene.sql`, and require `engramory --json sdd validate-graph --invariants` (`0` violations), `engramory --json docs lint --strict --verify-registry docs/doc-tools`, `uv run --extra dev pytest` (including real PG18 + Neo4j integration), and `task ci` green.

---

## 6. Rollback Strategy & Approval Gates

- **Two-Way Door Code/Schema Waves (`CHG-010`, `CHG-011`, `CHG-013`):** Each wave ships on an isolated feature branch off `gitea/dev` with independent reversible down-scripts in `db/migrations/down/0011..0013_*.sql` (never inside `db/migrations/*.sql`) and `pgBackRest` pre-flight snapshot on stage prior to apply.
- **Approval-Gate Boundary (`CHG-012`):** Execution of Wave P2 stops at explicit `approval-gate` prompts before modifying protected `.github/` / `.ci-config` / `.gitleaks.toml` files, updating `.aidoc/stack-policy.yaml` dependencies (`psycopg-pool`, `docling`, `beautifulsoup4`, `pypdf` replacing AGPL `pymupdf`, dev tooling), or opening companion `platform-stage-ops` OpenTofu PRs.

---

## Task convergence

> Validated by `check_plan.py --v2`: every task carries a canonical 64-hex `SHA-256`, 4-pillar scores (`0.0`–`5.0`), and status in `LOCKED | FIX | STALE | PROBE | REDESIGN`. `LOCKED` requires `Total >= 16.0`, every pillar `>= 2.5`, non-zero 64-hex hash, and an independent review pass.

| Task ID | SHA-256 | P1 | P2 | P3 | P4 | Total | Status |
| --- | --- | ---: | ---: | ---: | ---: | ---: | --- |
| TASK-1 | `58922921126290e68d96814fa9cad0d306cf8c04ca1152477a4753126511e7f2` | 4.9 | 5.0 | 4.6 | 4.8 | 19.3 | LOCKED |
| TASK-2 | `daf583e591c74a100de47d590ffcf4e15d0c0c75c29e92e0c72eadf20d2a441f` | 4.8 | 4.8 | 4.7 | 4.8 | 19.1 | LOCKED |
| TASK-3 | `0209057b8ea01b4827d1db79f187dda7a70f0c6b9182fd4f01b463fdcb4a231c` | 4.9 | 4.8 | 4.6 | 4.9 | 19.2 | LOCKED |
| TASK-4 | `ea5b2cebe42adbb2715ac6607d2ab857e84f3fae0e464fd9d66046cdbe154d1d` | 4.9 | 4.7 | 4.8 | 4.8 | 19.2 | LOCKED |

---

## Claim ledger

| # | Claim | Symbol | Citation |
|---|---|---|---|
| 1 | `AccessSurface.memory_add` overwrites `merged_meta["scope"]` only when `scope != "agent"`, enabling `agent -> space` metadata escalation (`C1`) | `if scope != "agent":` | `src/engramory/access/surface.py:518` |
| 2 | `reflect()` promotes distilled `Memory` directly using unverified `episode.metadata["scope"]` (`C1`) | `raw_scope = str((episode.metadata or {}).get("scope") or "agent")` | `src/engramory/workers/distillation.py:126` |
| 3 | `Repository.retire_memory` updates `memories` scoped only by `id` and `tenant_id`, omitting `agent_id`/`project_id`/`domain_id`/`scope` (`C2`) | `async def retire_memory(` | `src/engramory/core/repository.py:263` |
| 4 | `Repository.record_feedback` omits `memory_retrievals.agent_id` check and reverses no prior delta on outcome toggles (`C2`) | `async def record_feedback(` | `src/engramory/core/repository.py:279` |
| 5 | `SPEC-04` explicitly mandates immediate `status:='quarantined'` on any `harmful` feedback outcome regardless of scope (`C2`) | `a 'harmful' outcome sets status:='quarantined' immediately` | `docs/sdd/06_SPEC/SPEC-04_distillation_engine.yaml:80` |
| 6 | `AccessSurface.agent_roster_list` authorizes `project`/`agent` without calling `_enforce_domain_boundary` (`C2`) | `async def agent_roster_list(` | `src/engramory/access/surface.py:687` |
| 7 | `AccessSurface` graph methods set `eff_project_id = None` for `scope="domain"` while omitting `domain_id` filtering (`C3`) | `eff_project_id = ctx.project_id if scope in ("agent", "project") else None` | `src/engramory/access/surface.py:397` |
| 8 | `Repository.find_neighbors` matches `project_id IS NULL` nodes, omits target hop node filtering, and unpacks `**row["properties"]` last (`C3`) | `async def find_neighbors(` | `src/engramory/core/repository.py:1859` |
| 9 | `Neo4jGraphAdapter.neighbors` matches `project_id IS NULL` nodes and omits edge `r.project_id` boundary predicates (`C3`) | `async def neighbors(` | `src/engramory/adapters/dev/graph_neo4j.py:185` |
| 10 | `kb_runbook_steps` declares `PRIMARY KEY (tenant_id, step_uid)` without `doc_id` (`C4`) | `PRIMARY KEY (tenant_id, step_uid)` | `db/migrations/0008_runbooks_and_doc_governance.sql:61` |
| 11 | `Repository.upsert_kb_document` permits `ON CONFLICT DO UPDATE` on `project_id IS NULL` without checking `scope`/`domain_id` (`C4`) | `async def upsert_kb_document(` | `src/engramory/core/repository.py:2197` |
| 12 | `Repository.search_kb_sections` unconditionally includes `scope = 'space'` rows and omits `AND scope = 'project'` on `project_id` matches (`C5`) | `async def search_kb_sections(` | `src/engramory/core/repository.py:1184` |
| 13 | `v_ambient_memory_active` exposes all active memories across all scopes without `agent_id`, `domain_id`, or `provenance` (`C5`) | `CREATE OR REPLACE VIEW v_ambient_memory_active AS` | `db/migrations/0006_resize_embeddings_1536.sql:34` |
| 14 | `_GREEN_BANNED_PATTERNS` uses a regex denylist whose redirection lookbehind allows `1>`, `2>`, `&>`, heredocs, and quotes (`C6`) | `_GREEN_BANNED_PATTERNS` | `src/engramory/core/docs_tools.py:228` |
| 15 | `smoke_run_green_steps` runs `bash -c` directly on the host while violating steps dropped by `parse_markdown_document` leave `safety_issues` unreachable (`C6`) | `def smoke_run_green_steps(` | `src/engramory/core/docs_tools.py:1402` |
| 16 | `MCPRequestHandler.do_GET` closes the `/sse` stream immediately and accepts `?token=` query-string credentials (`C7`) | `def do_GET(self)` | `src/engramory/mcp/server.py:1404` |
| 17 | `McpServer.run_stdio` never enforces `_auth_enabled` and reads with default 64 KiB `StreamReader` limit (`C7`, `H7`) | `async def run_stdio(self)` | `src/engramory/mcp/server.py:1594` |
| 18 | `claims_to_actor_context` defaults missing `tenant_id`/`project_id` and treats non-empty string `"is_superuser"` values as truthy (`C7`) | `def claims_to_actor_context(` | `src/engramory/access/jwt_auth.py:193` |
| 19 | `check_token_revocation` runs uncached blocking `urlopen` and raises uncaught `TypeError` on non-list `"revocations"` (`C7`) | `def check_token_revocation(` | `src/engramory/access/jwt_auth.py:248` |
| 20 | `_CTRL_AND_BIDI_RE` omits `U+061C`, `U+00AD`, `U+2060`, `U+034F`, and Variation Selectors (`C8`) | `_CTRL_AND_BIDI_RE = re.compile(` | `src/engramory/access/poisoning_guard.py:17` |
| 21 | `GitSyncEngine.sync` queues `ADDED` files into `doc_ids_to_delete` and persists CAS blobs prior to `inspect_memory_write` (`C9`) | `async def sync(` | `src/engramory/core/git_sync.py:345` |
| 22 | `Repository.delete_graph_entity` raises `KeyError` whenever `cur.rowcount != 1`, crashing initial `GitSyncEngine.sync` (`C9`) | `async def delete_graph_entity(` | `src/engramory/core/repository.py:1675` |
| 23 | `PgBlobStorageAdapter._put_known_stream` resets live rows to `'uploading'` and deletes chunks without row/advisory locks (`C10`) | `async def _put_known_stream(` | `src/engramory/adapters/dev/storage_pg.py:354` |
| 24 | `PgBlobStorageAdapter.gc_orphan_chunks` sweeps active uploads across all tenants when `updated_at` is unrefreshed (`C10`) | `async def gc_orphan_chunks(` | `src/engramory/adapters/dev/storage_pg.py:674` |
| 25 | `kb_blobs` uses tenant-wide `PRIMARY KEY (tenant_id, sha256)` with scalar `project_id` (`C10`) | `PRIMARY KEY (tenant_id, sha256)` | `db/migrations/0010_multimodal_rag_and_blob_cas.sql:20` |
| 26 | `NatsEventsAdapter._drain_topic` polls Core NATS for `50ms` and unsubscribes rather than using durable JetStream pull consumers (`H1`) | `async def _drain_topic(` | `src/engramory/adapters/dev/events_nats.py:162` |
| 27 | `run_worker_loop` disables tenant enforcement when `tenant_id=None` and aborts all tenants on single-agent errors (`H1`, `H2`) | `async def run_worker_loop(` | `src/engramory/workers/runner.py:39` |
| 28 | `Repository.get_unreflected_episodes` filters `m.provenance->>'brain_variant'` without coalescing `e.metadata->>'brain_variant'` (`H2`) | `async def get_unreflected_episodes(` | `src/engramory/core/repository.py:572` |
| 29 | `AccessSurface.agent_brain_import` accepts null `schema_version`, skips `specializations` inspection, and leaves `MAX_K=100` dedup defects (`H3`, `H5`) | `async def agent_brain_import(` | `src/engramory/access/surface.py:1065` |
| 30 | `ExperienceEvent.to_episode` omits `"scope"` in metadata while `memory_ingest_event` defaults to `scope="project"` (`H4`) | `def to_episode(` | `src/engramory/core/experience.py:277` |
| 31 | `uq_episodes_event_id` keys only on `(tenant_id, metadata->>'event_id')` without `project_id`/`agent_id` (`H4`) | `CREATE UNIQUE INDEX IF NOT EXISTS uq_episodes_event_id` | `db/migrations/0009_tenant_profiles_and_experience_dedup.sql:35` |
| 32 | `AccessSurface.knowledge_ingest_media` does not restrict file extensions to media and omits `domain_id` propagation (`H5`, `H6`) | `async def knowledge_ingest_media(` | `src/engramory/access/surface.py:2124` |
| 33 | CLI ingestion/SDD/event subcommands call core engines directly and bypass `AccessSurface` authz/audit (`H6`) | `def _cmd_knowledge_ingest(` | `src/engramory/cli.py:397` |
| 34 | `SmartExtractionRouter.extract_file` truncates non-UTF-8 PDF fallbacks at `65536` bytes before parsing (`H8`) | `def extract_file(` | `src/engramory/core/extraction/router.py:58` |
| 35 | `DocumentDetector.detect` checks `b"@section:"` content patterns before filename extension mapping (`H8`) | `def detect(` | `src/engramory/core/parsers/detector.py:165` |
| 36 | `Neo4jGraphAdapter.purge_stale_nodes` hardcodes `NOT (n:DOCUMENT OR n:SECTION OR n:MEDIA_BLOB OR n:CONCEPT)` (`H9`) | `async def purge_stale_nodes(` | `src/engramory/adapters/dev/graph_neo4j.py:406` |
| 37 | `PgVectorAdapter.delete` updates `memories` by `id` with no `tenant_id` predicate (`H9`) | `async def delete(` | `src/engramory/adapters/dev/vector_pg.py:116` |
| 38 | `InvariantChecker.check_contract_erosion` omits active SDD `03_EARS`/`04_BDD`/`05_ADR`/`06_SPEC` and `db/migrations/` paths (`H11`) | `def check_contract_erosion(` | `src/engramory/core/context_tools.py:1280` |
| 39 | `Repository.get_memories` never queries `ts_lex` and omits `domain_id` filtering on `agent`/`project` scopes (`H12`) | `async def get_memories(` | `src/engramory/core/repository.py:660` |
| 40 | `LocalFsStorageAdapter` stores chunks solely in RAM dictionaries and ignores `older_than_seconds` in GC (`H15`) | `class LocalFsStorageAdapter` | `src/engramory/adapters/dev/storage_local.py:37` |
| 41 | `docker-compose.yml` mounts PG18 volume at `/var/lib/postgresql/data` instead of `/var/lib/postgresql` (`C11`) | `pgdata:/var/lib/postgresql/data` | `docker-compose.yml:18` |
| 42 | `compose.platform.yaml` defaults `ENGRAMORY_PROFILE` to invalid `stage` and lacks `/workspace` docs root (`H13`) | `ENGRAMORY_PROFILE=${ENGRAMORY_PROFILE:-stage}` | `compose.platform.yaml:32` |
| 43 | `scripts/deploy-stage.sh` imperatively runs remote `docker compose`/`psql` instead of OpenTofu/`pyinfra` EaC (`C11`) | `DOCKER_HOST` | `scripts/deploy-stage.sh:25` |
| 44 | `standards-drift.yml` downloads and executes `check-standards-drift.sh` from a mutable tag without SHA-256 verification (`C11`) | `check-standards-drift.sh` | `.github/workflows/standards-drift.yml:24` |
| 45 | `branch-promotion.yml` sets `PROMOTION_TARGETS: none`, disabling `verify-source` while mis-gating `dev -> staging` (`C11`) | `PROMOTION_TARGETS: none` | `.github/workflows/branch-promotion.yml:15` |
| 46 | `bot-signoff.yml` places `[bot]` login check outside branch-prefix parens, auto-stamping `[bot]` PRs to `main` (`C11`) | `endsWith(github.event.pull_request.user.login, '[bot]')` | `.github/workflows/bot-signoff.yml:25` |
| 47 | `.ci-config` leaves `PROMOTION_BRANCHES=` empty, leaving `staging`/`main` unprotected by `pre_push_guard.sh` (`C11`) | `PROMOTION_BRANCHES=` | `.ci-config:16` |
| 48 | `.aidoc/stack-policy.yaml` enforces `strict_allowlist: true` and prohibits `AGPL-3.0` (`H13`) | `strict_allowlist: true` | `.aidoc/stack-policy.yaml:28` |
| 49 | `.aidoc/stack-policy.yaml` already allowlists `fastapi` (alongside `httpx`, `starlette`, `uvicorn`) for Wave P0 MCP migration (`OQ-3`, `C7`) | `name: "fastapi"` | `.aidoc/stack-policy.yaml:49` |
| 50 | `pyproject.toml` includes AGPL-3.0 `pymupdf>=1.24.0` under `[project.optional-dependencies].extraction` (`H13`) | `pymupdf>=1.24.0` | `pyproject.toml:22` |
| 51 | `.aidoc/harness.lock.json` contains synthetic `manifest_sha256` values built from `pinned_commit` prefixes (`H14`) | `manifest_sha256` | `.aidoc/harness.lock.json:8` |
| 52 | `portal/astro.config.mjs` binds `127.0.0.1:8005`, colliding with stage MCP host port `8005` (`H14`) | `port: 8005` | `portal/astro.config.mjs:10` |
| 53 | `BANNED_RETIRED_TOOLS` in `docs_tools.py` flags approved DDL linter `bytebase` as a banned tool (`M6`) | `BANNED_RETIRED_TOOLS` | `src/engramory/core/docs_tools.py:74` |
| 54 | `tests/conftest.py` skips all PostgreSQL integration tests under `GITHUB_ACTIONS` and defaults local fallback to `pg16` (`H16`) | `if os.environ.get("GITHUB_ACTIONS"):` | `tests/conftest.py:68` |
| 55 | `IPLAN-00_index.yaml` marks all plans `Completed` while `IPLAN-07`/`SPEC-08` do not exist and `IPLAN-08` is `In Progress` (`M8`) | `total_plans:` | `docs/sdd/08_IPLAN/IPLAN-00_index.yaml:8` |
| 56 | `ADR-00_index.md` terminates at `ADR-10` and omits `ADR-11_foundation.yaml` (`M8`) | `ADR-10` | `docs/sdd/05_ADR/ADR-00_index.md:39` |
| 57 | `SPEC-03_retrieval_graph.yaml` specifies `RetrievalService` 3-leg RRF over `ts_lex` absent from `Repository.get_memories` (`M8`) | `RetrievalService` | `docs/sdd/06_SPEC/SPEC-03_retrieval_graph.yaml:22` |
| 58 | `AGENTS.md` governance table declares `docs/sdd/graph/` for `SLICE-NN` files (`M8`) | `## Per-repo governance` | `AGENTS.md:177` |
| 59 | `0006_resize_embeddings_1536.sql` drops `v_ambient_memory_active` and HNSW unconditionally before `0011` runs (`NEW@pass2`) | `DROP VIEW IF EXISTS v_ambient_memory_active;` | `db/migrations/0006_resize_embeddings_1536.sql:5` |
| 60 | `0004` and `0005` run unconditional `CREATE OR REPLACE VIEW v_ambient_memory_active` that fails `42P16` once `0011` adds view columns (`NEW@pass2`) | `CREATE OR REPLACE VIEW v_ambient_memory_active AS` | `db/migrations/0004_ambient_gateway_view.sql:6` |
| 61 | `test_migrations.py` hardcodes exact `0001`..`0010` SQL file list and requires updates per wave (`NEW@pass2`) | `def test_migrations_exist_and_ordered(` | `tests/test_migrations.py:11` |
| 62 | `consolidation_runs` omits `tenant_id`, requiring addition in `0011` before `FORCE ROW LEVEL SECURITY` (`NEW@pass2`) | `CREATE TABLE IF NOT EXISTS consolidation_runs (` | `db/migrations/0001_init_memory.sql:79` |
| 63 | `Repository._conn` and `PgBlobStorageAdapter._conn` accept no `tenant_id` and `_put_known_stream` calls intermediate `commit()` (`NEW@pass2`) | `async def _conn(` | `src/engramory/core/repository.py:103` |
| 64 | `conftest.py` launches integration Postgres as superuser `it`, bypassing `FORCE ROW LEVEL SECURITY` (`NEW@pass2`) | `POSTGRES_USER=it` | `tests/conftest.py:90` |
| 65 | `sast-scan/action.yml` removes `.semgrepignore` via `d23_scan` as a canon D23 security control (`NEW@pass2`) | `d23_scan` | `.github/actions/sast-scan/action.yml:221` |
| 66 | `ci.yml` runs `pip install -e ".[dev]"` which breaks when `[dependency-groups].dev` unifies dev tools in Wave P2 (`NEW@pass2`) | `pip install -e ".[dev]"` | `.github/workflows/ci.yml:19` |
| 67 | `tests/unit/test_mcp_server.py` and `test_docs_tools.py` couple to `create_http_server` and shell `&&` chains changed in Wave P0 (`NEW@pass2`) | `create_http_server` | `tests/unit/test_mcp_server.py:171` |
| 68 | `bulk_upsert_graph_entities` and `bulk_upsert_graph_relations` execute `cur.executemany` and return input counts ignoring conflict skips (`NEW@pass2`) | `await cur.executemany(` | `src/engramory/core/repository.py:1603` |
| 69 | `claims_to_actor_context` unconditionally grants `"domain"` scope whenever `domain_id is not None` (`NEW@pass2`) | `domain_id is not None` | `src/engramory/access/jwt_auth.py:222` |
| 70 | `kb_documents` lacks `agent_id` despite permitting `scope='agent'` (`NEW@pass2`) | `CREATE TABLE IF NOT EXISTS kb_documents (` | `db/migrations/0008_runbooks_and_doc_governance.sql:6` |
| 71 | `Taskfile.yml` sets `COV_MIN: "80"` while `.aidoc/stack-policy.yaml` sets `line_coverage_threshold: 85` (`NEW@pass2`) | `COV_MIN` | `Taskfile.yml:23` |
| 72 | `portal/package.json` hardcodes `--port 8005` in `dev` and `preview` scripts (`NEW@pass2`) | `8005` | `portal/package.json:9` |
| 73 | `authz.KNOWN_ACTIONS` requires `"knowledge_sync_git"` registration before CLI routes through `AccessSurface` (`NEW@pass2`) | `KNOWN_ACTIONS` | `src/engramory/access/authz.py:15` |
| 74 | `test_distillation_embedding.py` queries `v_ambient_memory_active` after `scope='agent'` reflection (`NEW@pass2`) | `FROM v_ambient_memory_active` | `tests/integration/test_distillation_embedding.py:67` |
| 75 | `chunking.ingest_file` calls `set_kb_sections_embedding_batch` without `tenant_id` (`NEW@pass3`) | `set_kb_sections_embedding_batch` | `src/engramory/core/chunking.py:231` |
| 76 | `VectorPort.delete` omits `tenant_id`, requiring protocol update alongside `PgVectorAdapter.delete` (`NEW@pass3`) | `async def delete(` | `src/engramory/ports/vector.py:26` |
| 77 | `GraphPort` signatures are asserted equal to `Neo4jGraphAdapter` in `test_graph_neo4j.py` (`NEW@pass3`) | `async def neighbors(` | `src/engramory/ports/graph.py:26` |
| 78 | `workers/graph_projection.py` unpacks `**entity.properties` after identity keys (`NEW@pass3`) | `**entity.properties` | `src/engramory/workers/graph_projection.py:32` |
| 79 | `test_virtual_employee_lifecycle.py` asserts `domain` auto-grant on `domain:billing` group (`NEW@pass3`) | `assert "domain" in ctx.scopes` | `tests/test_virtual_employee_lifecycle.py:453` |
| 80 | `test_adapters.py` calls `vec.delete` without `tenant_id` (`NEW@pass3`) | `vec.delete(` | `tests/conformance/test_adapters.py:74` |
| 81 | `test_repository.py` calls `vec.upsert` without `tenant_id` (`NEW@pass3`) | `vec.upsert(` | `tests/integration/test_repository.py:119` |
| 82 | `test_doc_ingest.py` opens `repo._conn()` directly without `tenant_id` (`NEW@pass3`) | `repo._conn()` | `tests/integration/test_doc_ingest.py:65` |
| 83 | `test_plan012_remediation.py` asserts `delete_kb_sections_for_doc` without `project_id` (`NEW@pass3`) | `delete_kb_sections_for_doc` | `tests/test_plan012_remediation.py:164` |
| 84 | `test_docs_tools.py` tests `DOC-STACK-001` with `seaweedfs`/`bytebase` (`NEW@pass3`) | `deploy seaweedfs with bytebase` | `tests/test_docs_tools.py:193` |
| 85 | `lefthook.yml` runs `--staged`, unsuitable as a PR CI gate without full-tree `task check` (`NEW@pass3`) | `--staged` | `lefthook.yml:8` |
| 86 | `.aidoc/Taskfile.yml` delegates SDD tools to `../aiops-harness`, unavailable in standalone CI checkouts (`NEW@pass3`) | `HARNESS_ROOT` | `.aidoc/Taskfile.yml:20` |
| 87 | `docs/doc-tools/10_DOCUMENTATION_TOOLS_TECHNICAL_REFERENCE.md` watches `docs_tools.py`/`portal/` across Waves P0–P2 (`NEW@pass3`) | `watched_paths` | `docs/doc-tools/10_DOCUMENTATION_TOOLS_TECHNICAL_REFERENCE.md:43` |
| 88 | `docs/MULTIMODAL_AND_POSTGRES_BLOB_STORAGE.md` documents `FOR KEY SHARE` lock trade-off on `UNLOGGED` `kb_blob_chunks_large` (`NEW@pass3`) | `FOR KEY SHARE` | `docs/MULTIMODAL_AND_POSTGRES_BLOB_STORAGE.md:166` |

---

## Review log

### Pass 1 - 2026-11-21

- **Author:** Antigravity Execution Agent (Step 3 draft & deterministic gate verification)
- **Checks Performed:** Verified all 40 audit findings (`C1`–`C11`, `H1`–`H16`, `M1`–`M8`, `L1`–`L5`) map 1:1 into chained Waves P0–P3 (`TASK-1`–`TASK-4` / `CHG-010`–`CHG-013`), confirmed all 58 initial `## Claim ledger` citations resolve locally in `platform-engramory`, and verified zero literal secret tokens or unapproved Wave-P0 stack additions.
- **Next:** Dispatch independent fresh-context multi-lens review (`Pass 2`) before locking tasks or setting `Status: ready`.

### Pass 2 - 2026-11-21 - independent

- **Reviewer:** Fresh-context subagent `247a9c4f-80bb-4399-93d8-16162c7038fd` (`Independent Multi-Lens Plan Reviewer`)
- **Selected Lenses:** Google (Design Doc & SRE Invariants), Anthropic (Agent/Tool Contracts & Prompt Fencing), AWS (Well-Architected Least Privilege, Two-Way Doors & EaC), Microsoft (STRIDE Threat Modeling & CI Supply-Chain Integrity)
- **Findings Raised & Folded (3 Critical, 6 Major, 4 Medium — all resolved in Fold 2):**
  1. `CRIT-01`: Moved `0004`–`0006` view/resize idempotency guards directly into `0004`/`0005`/`0006` in Wave P0 (`0013` cannot protect earlier scripts); switched `0011` `v_ambient_memory_active` update to `DROP VIEW ...; CREATE VIEW ... WITH (security_invoker = true)` exposing `agent_id`/`domain_id`/`provenance`.
  2. `CRIT-02`: Moved `H9` (`PgVectorAdapter` + `set_kb_section_embedding` + Neo4j `purge_stale_nodes`) into Wave P0 (`TASK-1` / `CHG-010`), added `_conn(tenant_id=...)` + post-`commit()` `SET LOCAL` re-application, specified migrator owner vs non-owner `engramory_app` / `engramory_worker` role grants, and added non-superuser RLS fixtures in `tests/conftest.py`.
  3. `CRIT-03`: Relocated migration down-scripts to `db/migrations/down/0011..0013_*.sql`, scheduled `tests/test_migrations.py` updates per wave, moved `consolidation_runs.tenant_id` into `0011`, and added `uq_memories_tenant_id UNIQUE (tenant_id, id)` + orphan-chunk cleanup prerequisites to `0013`.
  4. `MAJ-01`: Preserved D23 `.semgrepignore` guard in `.github/actions/sast-scan/action.yml` instead of disabling it.
  5. `MAJ-02`: Replaced missing `.github/actions/lefthook` reference with `jdx/mise-action` + `lefthook run pre-commit` (retiring legacy v2 callers) and moved `ci.yml` `uv sync --locked` + `permissions:`/`concurrency:`/`timeout-minutes:` to Wave P2.
  6. `MAJ-03`: Moved Wave-P0 HTTP/JWT/smoke test and doc updates (`test_mcp_server.py`, `test_jwt_auth.py`, `test_plan017_remediation.py`, `test_docs_tools.py`, `03_LINTING_GATES_AND_DRIFT_ENGINE.md`) into Wave P0 and tightened `C6` to subcommand + argument-pattern allowlisting.
  7. `MAJ-04`: Moved protected CI/gitleaks findings (`L2`, `L5`) into Wave P2 (`TASK-3` / `CHG-012`) and enforced atomic `watched_paths` doc updates at Waves P0 and P1.
  8. `MAJ-05`: Added full `.aidoc/stack-policy.yaml` reconciliation (`docling`, `beautifulsoup4`, `pypdf`, `psycopg-pool`, dev tooling) and `Taskfile.yml` `COV_MIN: "85"` alignment in Wave P2.
  9. `MAJ-06`: Folded `jwt_auth.py:222` (`domain_id` scope auto-grant removal), `cli.py:1233` Authentik default fix, `bulk_upsert_graph_{entities,relations}` conflict count checks, `kb_documents.agent_id` + `surface.py` project stamping fix, `GitSyncEngine._sync_changed_doc_metadata` confinement, `kb_blob_projects` backfill + RLS in `0012`, and `Repository.add_episode` fallback `SELECT` parity.
  10. `MED-01`–`MED-04`: Folded `H3` (`allow_type_override`, `< 3`-char/word-boundary redaction, `domain_id is None` leak), `H5`/`H6`/`H7` (`KnowledgeCore.ingest_documents_batch`, `knowledge_sync_git` in `KNOWN_ACTIONS`, MCP `domain_id` forwarding), `H16` (`ports/` stdlib vs `access/` zero-vendor rule), `M3` OTLP toggle/deferral + `Dockerfile:28` interlog removal, and `portal/package.json:9,11` port `4321` alignment.
- **Next:** Run deterministic gate + `--hash-tasks --fix`, then dispatch final Pass 3 (`independent`) convergence reviewer to verify Fold 2 before locking `TASK-1`–`TASK-4`.

### Pass 3 - 2026-11-21 - independent

- **Reviewer:** Fresh-context subagent `cd11c9c3-c792-4939-ab05-57779c114c15` (`Independent Final Convergence Reviewer`)
- **Selected Lenses:** Google (Design Doc & SRE Invariants), Anthropic (Agent/Tool Contracts & Prompt Fencing), AWS (Well-Architected Least Privilege, Two-Way Doors & EaC), Microsoft (STRIDE Threat Modeling & CI Supply-Chain Integrity)
- **Audit Scope & Verification:** Verified Fold 2 resolution across all 13 Pass 2 items (`0 Critical` remaining), confirmed disjoint 40/40 audit accounting (`TASK-1`: 9, `TASK-2`: 14, `TASK-3`: 8, `TASK-4`: 9), and surfaced 3 Major + 1 Medium remaining deterministic gaps (+ `0004`/`0013` schema details), all resolved in **Fold 3** under hard 3-pass cap (`_TASK_PASS_CAP_V2 = 3`):
  1. `MAJ-P3-01` (Folded): Moved `Repository.set_kb_sections_embedding_batch` hardening into Wave P0 (`chunking.py:231`, `surface.py:1910`), added `VectorPort.delete` (`ports/vector.py:26`) + `PgVectorAdapter.upsert` `tenant_id` propagation, added post-`rollback()` `SET LOCAL app.tenant_id` re-application (`storage_pg.py:510`), used `NULLIF(current_setting('app.tenant_id', true), '')` in `0011` RLS + `$v$`/direct DDL in `0004` (`tests/test_migrations.py:79`), and added non-superuser `engramory_app` / `engramory_worker` fixtures + `test_adapters.py:74`, `test_repository.py:119`, `test_doc_ingest.py:65` to Wave P0.
  2. `MAJ-P3-02` (Folded): Synced `GraphPort` (`ports/graph.py:26`), `test_graph_neo4j.py:14`, and `workers/graph_projection.py:32` in Wave P0; added `test_virtual_employee_lifecycle.py:453`, `test_access_surface.py:125`, and `test_adr_knowledge_graph.py:68` to Wave P0; added `test_plan012_remediation.py:164` to Wave P1; added `test_docs_tools.py:193` and `docs_tools.py:128` (`pymupdf` catalog entry) to Wave P2.
  3. `MAJ-P3-03` (Folded): Deleted both `quick-gates.yml:125` (`hashFiles('.pre-commit-config.yaml')`) and `:212-215` (`PRECOMMIT` skip carve-out) alongside replacing `pre-commit` with SHA-pinned `jdx/mise-action` + full-tree `task check` (avoiding `lefthook.yml:8` `--staged` on PR CI) and gating legacy caller removal on branch-protection parity; defined standalone CI fallback inside `.aidoc/Taskfile.yml:20` (`sdd:check`) using native `engramory sdd validate-graph` / `docs lint`; moved raising `Taskfile.yml:23` `COV_MIN: "85"` + UTS `85` alignment from Wave P2 to Wave P3 atomically with PG18+Neo4j CI services.
  4. `MED-P3-01` + `0013` Precision (Folded): Synchronized `watched_paths` docs (`VIRTUAL_EMPLOYEE_ARCHITECTURE.md:9`, `10_DOCUMENTATION_TOOLS_TECHNICAL_REFERENCE.md:43`, `07_PORTAL_API_CONTRACTS_AND_DEPLOYMENT_RUNBOOK.md:44`, `00_INDEX.md:27`) across Waves P0–P2; scoped `0013` to clean mismatched `tenant_id` FKs before `uq_memories_tenant_id`, use `ON DELETE CASCADE` FK on `LOGGED` `kb_blob_chunks` + `AFTER DELETE` cleanup trigger on `UNLOGGED` `kb_blob_chunks_large` (avoiding per-chunk `FOR KEY SHARE` locks during `COPY BINARY`), and update `MULTIMODAL_AND_POSTGRES_BLOB_STORAGE.md:166`.
- **Gate & Convergence Verification:** Re-ran `check_plan.py --v2 --hash-tasks --fix`, `check_plan.py --v2`, `check_plan.py` (v1), and `secret_scan.py` over all 88 citations and 4 `LOCKED` task SHA-256 digests (`TASK-1`–`TASK-4`).

**Result:** ready — no further findings.
