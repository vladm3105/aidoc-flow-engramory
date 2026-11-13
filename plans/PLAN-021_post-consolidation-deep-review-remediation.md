# PLAN-021: Remediate Post-Consolidation Deep Review Gaps, Bugs, Tenant Isolation, and SDD Split-Brain

> **Status:** Completed  
> **Date:** 2026-11-10  
> **Repository:** `platform-engramory` (`aidoc-flow-engramory`)  
> **Target Branch:** `fix/plan-021-deep-review-remediation` -> `dev`  
> **Governance Standard:** `aiops-harness` v0.2.0 (`verified-planning`, `DIR2C` / `C3`)  
> **Authorizing CHG:** `docs/sdd/09_CHG/CHG-007_post_consolidation_deep_review_remediation.yaml`

---

## 1. Context & Architectural Motivation

Following the consolidation of `docs_tools` (`PLAN-013` / `PLAN-015` / `PLAN-019`), `context_tools` (`PLAN-016`), `aiops-harness` v0.2.0 (`PLAN-018`), and the `experience` / `events_nats` pipeline (`PLAN-020` / `CHG-006`), a comprehensive 4-subagent deep review identified concrete runtime bugs, security/isolation flaws, SDD split-brain issues, and test/CI blind spots across `platform-engramory`:

1. **Immediate Test & Typecheck Failures on `dev`:**
   - `tests/integration/test_cli.py:343-365` (`test_worker_start_with_env`) fails with `TypeError: fake_start_daemon() got an unexpected keyword argument 'events'` because `_cmd_worker_start` (`src/engramory/cli.py:474`) was updated in `PLAN-020` without updating `fake_start_daemon`.
   - `uv run mypy src` fails at `src/engramory/cli.py:1596` (`json.loads(raw_str)` where `raw_str: str | None`).
2. **Critical Database, Experience & NATS Bugs (`C-01`..`C-04`):**
   - `Repository.get_learning_health_summary` (`src/engramory/core/repository.py:1738-1746`) queries `ORDER BY created_at ASC` on `episodes`, which only has a `ts` column (`db/migrations/0001_init_memory.sql:37`), crashing with `psycopg.errors.UndefinedColumn` on PostgreSQL and ordering oldest-first instead of recent-first.
   - `ExperienceEvent.to_episode()` (`src/engramory/core/experience.py:284-288`) omits `event_id` and `origin.repo` from `content_raw`, causing distinct milestone events across plans/PRs/runs that share the same summary or payload to collide on `Episode.content_hash` (`md5(content_raw)`) and be silently dropped by `Repository.add_episode()` (`src/engramory/core/repository.py:130-131`).
   - `NatsEventsAdapter._drain_topic` (`src/engramory/adapters/dev/events_nats.py:106-120`) iterates `async for msg in sub.messages:` indefinitely when connected to a live NATS broker (blocking `run_worker_loop()` at `src/engramory/workers/runner.py:84` forever and starving `reflect()` + heartbeat updates), lacks `nats-py` in `pyproject.toml`, and lacks `MAX_EXPERIENCE_CONTENT_BYTES = 65536` and P14 `has_dangerous_unicode` checks in `publish()`.
   - `agent_profiles` (`db/migrations/0001_init_memory.sql:9-15`, `src/engramory/core/repository.py:297-338`) has no `tenant_id` column, leaking L3 profiles across tenants when two tenants share an `agent_id`.
3. **Critical Runbook Safety, Multi-Project Graph Isolation & SDD Split-Brain (`C-05`..`C-08`):**
   - `check_green_step_safety` (`src/engramory/core/docs_tools.py:228-307`) only matches `python3 -c` / `bash -c` when `-c` is the first flag, and `parse_markdown_document` (`src/engramory/core/docs_tools.py:809-860`) still emits `KBRunbookStep(tier="green")` when `INV-STEP-02` fails, which `KnowledgeCore`, `GitSyncEngine`, and `cli.py` persist into `kb_runbook_steps`.
   - `ingest_sdd_knowledge_graph` (`src/engramory/core/kb_graph.py:553-589`) never enables `namespace_by_project`, causing cross-project entity collisions when `project_id != "core"`, and `normalize_sdd_id` (`src/engramory/core/kb_graph.py:44-64`) truncates 4-segment EARS/BDD IDs (`EARS.01.03.a700` -> `EARS-01`) and corrupts project prefixes containing underscores.
   - `platform-engramory` has a split-brain SDD tree (`sdd/` with 39 domain artifacts vs `docs/sdd/` with 4 stubs + `09_CHG`), and `context_tools._extract_yaml_deps` (`src/engramory/core/context_tools.py:423-456`) treats `traceability.tags` as self-dependencies (37 false cycles) while ignoring nested `traceability.upstream` dicts and `source_spec`/`architecture_decision`/`brd_reference` keys.
4. **AccessSurface, MCP, CLI & Test Suite Gaps (`M-01`..`M-12`):**
   - `AccessSurface.docs_slice_runbook` (`src/engramory/access/surface.py:790-809`) calls `self.memory_search` without `scope=scope`, defaulting to `scope="agent"` and causing false `AuthzError` audit denials and omitting project/domain/space memories.
   - `Repository.search_kb_sections` (`src/engramory/core/repository.py:767-775`) and `Repository.slice_runbook` (`src/engramory/core/repository.py:1686-1696`) allow cross-domain reads when `scope='domain'` and `domain_id=None`, and `slice_runbook` omits `d.scope` checks for `agent`/`project`.
   - `McpServer` (`src/engramory/mcp/server.py:386`) passes `token_budget=None` by default to `memory_search` (disabling `DEFAULT_TOKEN_BUDGET = 2000`), crashes on non-dict JSON-RPC requests or `"params": null` (`src/engramory/mcp/server.py:689-715`), re-calls `_decide` on `StoreUnavailable` (`src/engramory/mcp/server.py:530, 600`), and lacks MCP tools for `memory_ingest_event`, `memory_learning_health`, and `docs_ingest_runbook`.
   - `cli.py:1155` does not include `MemoryPoisoningError` alongside `(AuthzError, GovernedWriteRejected)`, causing unwrapped CLI commands to exit `2` instead of `1`.
   - `Taskfile.yml:15-24` delegates test execution via `unified-test-suite.config.json:11-20`, which runs `pytest -q -m "integration" tests/` while zero tests in `tests/integration/`, `tests/e2e/`, or `tests/conformance/` carry `@pytest.mark.integration`.

---

## 2. Adopted 4-Part `5/5` Remediation Strategy

1. **Recommendation 1A & 1B (NATS Transport & Dual-Subject Support — `5/5`):**
   - Add `"nats-py>=2.8.0"` to `pyproject.toml:11-17` and `.aidoc/stack-policy.yaml:48-94`, and add `"nats"` to `VENDOR_ROOTS` in `tests/unit/test_no_vendor_imports.py:17-31`.
   - Harden `NatsEventsAdapter` (`src/engramory/adapters/dev/events_nats.py`) with `MAX_EXPERIENCE_CONTENT_BYTES = 65536`, P14 `has_dangerous_unicode` validation, topic whitespace/control-char rejection, bounded `deque(maxlen=1000)`, dual-subject wildcard matching (`engramory.events.>` and `xp.engramory.experience.>`), and bounded batch draining over live NATS (`sub = await client.subscribe(topic)` with bounded `next_msg(timeout=0.1)` up to `max_items=100` and `await sub.unsubscribe()` in `finally:`) so `run_worker_loop()` never hangs.
2. **Recommendation 2 (Canonical `docs/sdd/` Consolidation — `5/5`):**
   - Consolidate all SDD artifacts from `sdd/01_BRD..08_IPLAN` into `docs/sdd/01_BRD..08_IPLAN`, rename the `PLAN-018` stub `docs/sdd/05_ADR/ADR-01_foundation.yaml` to `docs/sdd/05_ADR/ADR-11_foundation.yaml` (`id: ADR-11`), remove duplicate stub index files, remove legacy root `sdd/`, and add a transparent `"sdd" -> "docs/sdd"` path resolution fallback in `kb_graph.py`, `cli.py`, and `mcp/server.py`.
3. **Recommendation 3 (Additive Migration `0009` & Deterministic Experience Deduplication — `5/5`):**
   - Add `db/migrations/0009_tenant_profiles_and_experience_dedup.sql` adding `tenant_id TEXT NOT NULL DEFAULT 'default'` to `agent_profiles` with `PRIMARY KEY (tenant_id, agent_id)`, partial unique index `uq_episodes_event_id` on `(tenant_id, (metadata->>'event_id')) WHERE metadata ? 'event_id'`, and index `idx_episodes_tenant_project_ts` on `episodes (tenant_id, project_id, ts DESC, id DESC)`.
   - Update `ExperienceEvent.to_episode()` (`src/engramory/core/experience.py:284-288`) so `content_raw` deterministically embeds `[kind] event_id=<event_id>` (plus `repo=<origin.repo>` when present) and update `Repository.get_learning_health_summary()` (`src/engramory/core/repository.py:1738-1746`) to select the most recent `limit` episodes via `ORDER BY ts DESC, id DESC LIMIT %(limit)s` re-ordered `ASC`.

---

## 3. Phased Implementation Steps

### Phase 1: Additive Migration `0009`, Repository Fixes & Experience Deduplication (`db/migrations/`, `src/engramory/core/experience.py`, `src/engramory/core/repository.py`, `src/engramory/workers/distillation.py`)

1. **Create `db/migrations/0009_tenant_profiles_and_experience_dedup.sql`:**
   - Add `tenant_id TEXT NOT NULL DEFAULT 'default'` to `agent_profiles`, drop `agent_profiles_pkey`, and recreate `PRIMARY KEY (tenant_id, agent_id)`.
   - Create `UNIQUE INDEX IF NOT EXISTS uq_episodes_event_id ON episodes (tenant_id, (metadata->>'event_id')) WHERE metadata ? 'event_id'`.
   - Create `INDEX IF NOT EXISTS idx_episodes_tenant_project_ts ON episodes (tenant_id, project_id, ts DESC, id DESC)`.
2. **Update `AgentProfile` & `Repository` (`src/engramory/core/models.py:162-173`, `src/engramory/core/repository.py:297-338`):**
   - Add `tenant_id: str = "default"` to `AgentProfile` in `src/engramory/core/models.py:162-173`.
   - Update `Repository.get_agent_profile(self, agent_id: str, *, tenant_id: str = "default")` and `Repository.upsert_agent_profile(self, profile: AgentProfile)` in `src/engramory/core/repository.py:297-338` to filter and conflict-target `(tenant_id, agent_id)`, pass `tenant_id=ctx.tenant_id` (and return `AgentProfile(agent_id=ctx.agent_id, tenant_id=ctx.tenant_id)` when `profile is None`) in `AccessSurface.agent_profile_get` (`src/engramory/access/surface.py:419-426`), and update `_upsert_profile_best_effort(agent_id: str, dsn: str, tenant_id: str = "default")` in `src/engramory/cli.py:135-176` to accept and pass `args.tenant_id`.
   - Add ISO date validation (`datetime.date.fromisoformat(self.last_verified)`) to `KBDocument.__post_init__` (`src/engramory/core/models.py:240-259`).
3. **Fix `ExperienceEvent.to_episode()` & Provenance Propagation (`src/engramory/core/experience.py:277-346`, `src/engramory/workers/distillation.py:115-133`):**
   - Format `raw_text` in `ExperienceEvent.to_episode()` (`src/engramory/core/experience.py:284-288`) as `f"[{self.kind}] event_id={self.event_id}{repo_part} {summary_part}"` so every distinct `event_id` produces a distinct `Episode.content_hash` while identical `event_id`s deduplicate deterministically.
   - Include `"producer": self.producer` in `Episode.metadata` (`src/engramory/core/experience.py:322-328`) and add `"producer"`, `"occurred_at"`, and `"payload_type"` to `_PROVENANCE_PASSTHROUGH_KEYS` in `src/engramory/workers/distillation.py:115-122`.
4. **Fix `Repository.get_learning_health_summary` & Scope SQL Queries (`src/engramory/core/repository.py`):**
   - In `Repository.get_learning_health_summary` (`src/engramory/core/repository.py:1738-1746`), replace `ORDER BY created_at ASC, id ASC` with:

     ```sql
     SELECT id, agent_id, project_id, domain_id, tenant_id, kind, content_raw, metadata
     FROM (
         SELECT id, agent_id, project_id, domain_id, tenant_id, kind, content_raw, metadata, ts
         FROM episodes
         WHERE tenant_id = %(tenant_id)s
           AND (%(project_id)s::text IS NULL OR project_id = %(project_id)s::text)
         ORDER BY ts DESC, id DESC
         LIMIT %(limit)s
     ) recent_ep
     ORDER BY ts ASC, id ASC
     ```

   - In `Repository.add_episode` (`src/engramory/core/repository.py:121-152`), replace the single-index `ON CONFLICT (tenant_id, agent_id, coalesce(project_id, ''), content_hash) DO NOTHING` with unconstrained `ON CONFLICT DO NOTHING RETURNING id` (so conflicts on either `uq_episodes_idempotency` or the partial unique index `uq_episodes_event_id` are caught without `UniqueViolation`), include `"event_id": episode.metadata.get("event_id") if isinstance(episode.metadata, dict) else None` in `_episode_params(episode)`, and update the fallback `SELECT id FROM episodes` query to match either `(agent_id = %(agent_id)s AND coalesce(project_id, '') = coalesce(%(project_id)s, '') AND content_hash = %(content_hash)s)` or `(%(event_id)s::text IS NOT NULL AND metadata ? 'event_id' AND metadata->>'event_id' = %(event_id)s)`.
   - In `Repository.search_kb_sections` (`src/engramory/core/repository.py:756-855`) and `Repository.slice_runbook` (`src/engramory/core/repository.py:1685-1716`), require `domain_id IS NOT NULL AND domain_id = %(domain_id)s` for `scope = 'domain'` rows (unless `scope = 'space'`), enforce `d.scope IN ('agent', 'project', 'space')` when `scope IN ('agent', 'project')` in `slice_runbook`, and include `scope IN ('domain', 'space')` when `scope = 'space'`.
   - Wrap `Repository.upsert_kb_sections_batch` (`src/engramory/core/repository.py:574-634`) in a single `async with self._conn() as conn:` transaction across chunks, and add optional `project_id: str | None = None` guard to `delete_kb_sections_for_doc` (`src/engramory/core/repository.py:636-644`).

### Phase 2: `NatsEventsAdapter` Bounded Drain, Guards & Dependencies (`pyproject.toml`, `.aidoc/stack-policy.yaml`, `src/engramory/adapters/dev/events_nats.py`, `tests/unit/test_no_vendor_imports.py`)

1. **Declare `nats-py>=2.8.0`:**
   - Add `"nats-py>=2.8.0"` to `pyproject.toml:11-17` and `.aidoc/stack-policy.yaml:48-94`.
   - Add `"nats"` to `VENDOR_ROOTS` in `tests/unit/test_no_vendor_imports.py:17-31`.
   - Export `NatsEventsAdapter` in `src/engramory/adapters/dev/__init__.py:10-14`.
2. **Harden `NatsEventsAdapter` (`src/engramory/adapters/dev/events_nats.py:21-132`):**
   - Support dual subject prefixes (`DEFAULT_EXPERIENCE_TOPIC_PREFIX = "engramory.events"` and `LEGACY_EXPERIENCE_TOPIC_PREFIX = "xp.engramory.experience"`) in `_topic_matches()` so subscribing to `engramory.events.>` or `xp.engramory.experience.>` matches events published under either prefix.
   - Bound `self._buffer: deque[tuple[str, dict[str, Any]]] = deque(maxlen=1000)`.
   - In `publish()` (`src/engramory/adapters/dev/events_nats.py:72-98`): reject topics with whitespace or control characters, enforce `len(raw_bytes) <= 65536`, and reject payloads failing `has_dangerous_unicode()` (`src/engramory/access/poisoning_guard.py:34`).
   - In `_drain_topic()` (`src/engramory/adapters/dev/events_nats.py:106-132`):
     - Check `inspect.iscoroutinefunction(getattr(sub, "next_msg", None))` before calling `await sub.next_msg(timeout=0.05)` (so `MagicMock` subscriptions in `tests/unit/test_adapters_dev_Wave2.py:143-179` that only set `sub.messages = _AsyncIter(...)` do not fail with `TypeError: object MagicMock can't be used in 'await' expression`), and otherwise consume `sub.messages` via `await asyncio.wait_for(anext(it), timeout=0.05)` up to `max_items = 100`, catching `json.JSONDecodeError` per message with a warning log, and **always** calling `await sub.unsubscribe()` in a `finally:` block so `run_worker_loop()` never blocks or leaks NATS subscriptions!

### Phase 3: Runbook Safety (`INV-STEP-02`), Multi-Project Graph Isolation & SDD Parser Fixes (`src/engramory/core/docs_tools.py`, `src/engramory/core/kb_graph.py`, `src/engramory/core/context_tools.py`, `src/engramory/core/knowledge.py`, `src/engramory/core/git_sync.py`)

1. **Harden `check_green_step_safety` & Block Unsafe Step Ingestion (`src/engramory/core/docs_tools.py:228-307, 809-860`):**
   - Expand `_GREEN_BANNED_PATTERNS` in `src/engramory/core/docs_tools.py:228-307` to block:
     - Interpreter execution flags in any flag position (`\b(?:bash|sh|zsh|dash|python3?|perl|ruby|node)\b[^\n;|&]*\s-(?:[a-zA-Z]*[ce]|m)\b`) and piping into interpreters (`\|\s*(?:env\s+)?(?:bash|sh|zsh|dash|python3?|perl|ruby|node)\b`).
     - In-place file mutators and process killers (`\bsed\s+-[a-zA-Z]*i\b`, `\bawk\s+-i\s+inplace\b`, `\b(?:pkill|killall|kill)\b`, `\bgit\s+(?:checkout\s+-f|branch\s+-[dD]|tag\s+-d)\b`, `\bfind\b[^\n]*-exec(?:dir)?\b`).
   - Expand `_SENSITIVE_ENV_RE` (`src/engramory/core/docs_tools.py:315-318`) to also scrub `AWS_ACCESS_KEY_ID`, `GOOGLE_APPLICATION_CREDENTIALS`, `SSH_AUTH_SOCK`, and `KUBECONFIG`.
   - In `parse_markdown_document` (`src/engramory/core/docs_tools.py:809-875`), record `rollback_candidates: list[tuple[str, str, int]] = []` (`(step_id, rollback_step, fence_start_line)`) for every annotated step fence before checking `green_violations`, only append `KBRunbookStep` to `steps` when `not green_violations` (`if cmd_body and not (tier == "green" and (mode != "read-only" or green_violations)):`), and validate `MISSING_ROLLBACK_STEP` against `rollback_candidates` and `seen_step_ids` after the loop. This guarantees unsafe `INV-STEP-02` steps are never emitted into `parsed.steps` or persisted into PostgreSQL `kb_runbook_steps`, while preserving `MISSING_ROLLBACK_STEP` diagnostics tested by `tests/test_docs_tools.py:178-187`.
   - In `resolve_confined_repo_path` (`src/engramory/core/docs_tools.py:394-406`), resolve relative paths against `base` when `allowed_root` was explicitly provided (`(base / raw).resolve()` when `allowed_root is not None` and `(Path.cwd().resolve() / raw).resolve()` is not relative to `base`), and in `stage_portal_bundle` (`src/engramory/core/portal_builder.py:40-64`), confine `output_dir` to `allowed_root or confined_repo`.
   - Exclude `docs/sdd/` when globbing `docs/` for governed Markdown runbooks in `lint_repo` (`src/engramory/core/docs_tools.py:1278`), `smoke_run_green_steps` (`docs_tools.py:1385`), `check_repo_drift` (`docs_tools.py:1552`), `slice_local_runbooks`, `extract_docs_graph` (`src/engramory/core/kb_graph.py:735`), `stage_portal_bundle` (`src/engramory/core/portal_builder.py:66`), and `_cmd_docs_ingest` (`src/engramory/cli.py:1512`), and scan both `root / "docs" / "sdd"` and `root / "sdd"` in `export_repo_llms` (`docs_tools.py:1683-1686`).
2. **Fix `normalize_sdd_id` & Multi-Project Graph Isolation (`src/engramory/core/kb_graph.py:44-64, 553-589, 779-854`):**
   - In `normalize_sdd_id` (`src/engramory/core/kb_graph.py:44-64`): split off any `<project_id>:` namespace prefix (`proj, sep, rest = cleaned.partition(":")`) **before** running `.split("_")[0]` (so underscores inside `project_id` like `my_proj:ADR-01_title` are preserved as `my_proj:ADR-01`), while preserving document-level dotted ID normalization (`BDD.01.03.a100 -> BDD-01`, `SPEC.06.01.c3b8 -> SPEC-06`) required by `tests/unit/test_sdd_graph.py:16-17` and `tests/integration/test_sdd_knowledge_graph.py:63`.
   - In `ingest_sdd_knowledge_graph` (`src/engramory/core/kb_graph.py:564-569`), pass `namespace_by_project=(project_id not in ("", "core", "default"))` when callers explicitly ingest a non-default project corpus, and include `"project_id": eff_project` on `CONTAINS_STEP` relations in `extract_docs_graph` (`src/engramory/core/kb_graph.py:818-826`).
   - Guard against `None` values on `document_control`, `component_overview`, and `related_decisions` in `extract_adr_graph` and `extract_sdd_graph` (`(data.get("document_control") or {}).get(...)` at `src/engramory/core/kb_graph.py:117-145, 308-338`), and skip `*-00_index.*` index files in `extract_sdd_graph` and `AidocDependencyGraph` (`src/engramory/core/context_tools.py:782, 839`).
3. **Fix `context_tools._extract_yaml_deps` (`src/engramory/core/context_tools.py:423-456`):**
   - Under `trace` / `traceability`, extract only upstream keys (`upstream`, `originating_topic`, `brd_reference`, `prd_reference`, `ears_reference`, `bdd_reference`, `adr_reference`, `spec_reference`, `tdd_reference`), explicitly ignoring `tags`, `downstream`, `downstream_expected`, `code_inventory`, `health_score`, and `cross_links`; recursively extract string/list values inside nested `traceability["upstream"]` dicts; ignore `"Constrains"` entries under `related_decisions.dependencies` (and remove downstream `ADR-05` / `ADR-03` entries from `ADR-01_postgres_spine.yaml:130-133`); and extract `source_spec`, `source_document`, `architecture_decision`, `brd_reference`, and `authoring_chg` (from top-level, `document_control`, and `component_overview`) so `validate_sdd_graph(Path("docs/sdd"))` produces **0 cycles** on Engramory's SDD corpus!

### Phase 4: Consolidate SDD Corpus into `docs/sdd/` (`docs/sdd/`, `sdd/`)

1. **Unify `sdd/` into `docs/sdd/`:**
   - Move `sdd/01_BRD`, `sdd/02_PRD`, `sdd/03_EARS`, `sdd/04_BDD`, `sdd/05_ADR`, `sdd/06_SPEC`, `sdd/07_TDD`, and `sdd/08_IPLAN` into `docs/sdd/` (replacing the 4 empty `PLAN-018` stub files in `docs/sdd/03_EARS`, `04_BDD`, `05_ADR` and preserving `ADR-01_foundation.yaml` as `docs/sdd/05_ADR/ADR-11_foundation.yaml` with `id: ADR-11`), and update relative links inside `docs/sdd/*/*-00_index.md`.
   - Remove the legacy root `sdd/` directory.
   - Add a helper `_resolve_sdd_root(sdd_root: Path | str) -> Path` in `src/engramory/core/kb_graph.py` (and `context_tools.py`) that transparently maps `Path("sdd")` to `Path("docs/sdd")` and `Path("sdd/05_ADR")` to `Path("docs/sdd/05_ADR")` when `sdd` does not exist and `docs/sdd` exists, and update `resolve_sdd_project_id` (`kb_graph.py:549-550`) and `ingest_sdd_knowledge_graph` (`kb_graph.py:570`) so `<repo>/docs/sdd` resolves `repo_candidate` to `<repo>` (not `<repo>/docs`), while keeping `McpServer._handle_sdd_ingest`'s confinement check compatible with `tests/unit/test_mcp_server.py:146-163`.
   - Update `docs/sdd/06_SPEC/SPEC-01_access_surface.yaml`, `SPEC-04_distillation_engine.yaml`, `SPEC-06_ports_portability.yaml`, and `SPEC-07_cli_face.yaml` to reflect the consolidated tools and commands.

### Phase 5: `AccessSurface`, `McpServer`, and `CLI` Governance & Parity (`src/engramory/access/`, `src/engramory/mcp/server.py`, `src/engramory/cli.py`)

1. **`AccessSurface` Fixes (`src/engramory/access/authz.py`, `src/engramory/access/surface.py`):**
   - Register `"memory_ingest_event"` and `"memory_learning_health"` in `KNOWN_ACTIONS` (`src/engramory/access/authz.py:15-40`).
   - Add `memory_ingest_event(self, ctx: ActorContext, event_data: Mapping[str, Any] | ExperienceEvent) -> dict[str, Any]` and `memory_learning_health(self, ctx: ActorContext, *, project_id: str | None = None, limit: int = 500) -> dict[str, Any]` to `AccessSurface` (`src/engramory/access/surface.py`).
   - In `AccessSurface.memory_add` (`src/engramory/access/surface.py:370-396`), also screen `json.dumps(dict(metadata), sort_keys=True)` through `inspect_memory_write` when `metadata` is non-empty.
   - In `AccessSurface.docs_slice_runbook` (`src/engramory/access/surface.py:790-809`), pass `scope=scope` to `self.memory_search(ctx, query_str, scope=scope, k=4, ...)`.
2. **`McpServer` Fixes (`src/engramory/mcp/server.py`):**
   - In `handle_call_tool` -> `memory_search` (`src/engramory/mcp/server.py:380-387`), default `token_budget` to `DEFAULT_TOKEN_BUDGET` (`arguments.get("token_budget", 2000)`) instead of `None`.
   - Expose `memory_ingest_event`, `memory_learning_health`, and `docs_ingest_runbook` in `TOOLS_SCHEMA` and `handle_call_tool`.
   - In `sdd_context_slice` (`src/engramory/mcp/server.py:527-535`), `sdd_blast_radius` (`src/engramory/mcp/server.py:597-605`), and `docs_slice_runbook` (`src/engramory/mcp/server.py:999-1022`), do not re-call `await self._surface._decide(...)` inside `except StoreUnavailable:` (use `authorize(call_ctx, ...)` directly when Postgres is unreachable), and preserve `l2_memories` when merging `slice_local_runbooks` fallback results.
   - In `handle_request` (`src/engramory/mcp/server.py:685-725`), validate `isinstance(request, Mapping)` (returning JSON-RPC `-32600 Invalid Request` if not) and normalize `params = request.get("params") or {}` and `tool_args = params.get("arguments") or {}`.
   - In `jwt_auth.py:265-292`, catch `(urllib.error.URLError, json.JSONDecodeError, ValueError)` and raise `AuthenticationError` on non-200 status codes.
3. **`CLI` Fixes (`src/engramory/cli.py`):**
   - Fix `mypy` type narrowing in `_cmd_memory_ingest_event` (`src/engramory/cli.py:1590-1598`: assert `isinstance(raw_str, str)` before `json.loads(raw_str)`).
   - Add `MemoryPoisoningError` to the `(AuthzError, GovernedWriteRejected, MemoryPoisoningError)` catch clause in `main()` (`src/engramory/cli.py:1155`) so poisoning rejections always exit `1`.
   - Close `adapters` cleanly on early returns in `_cmd_knowledge_ingest` (`src/engramory/cli.py:320-336`) and on exit in `_cmd_worker_start` (`src/engramory/cli.py:466-481`) and `scripts/ingest_docs.py:36-65`.

### Phase 6: Test Suite, CI & Documentation Alignment (`tests/`, `Taskfile.yml`, `unified-test-suite.config.json`, `.ci-config`, `README.md`, `AGENTS.md`)

1. **Fix `tests/integration/test_cli.py:343-350` (`test_worker_start_with_env`):**
   - Update `fake_start_daemon` in `tests/integration/test_cli.py:343-350` to accept `events: Any = None` (and `**kwargs: Any`) and assert `called["events"] is not None`.
2. **Align Test Runner Configuration (`Taskfile.yml`, `unified-test-suite.config.json`, `tests/conftest.py`):**
   - In `tests/conftest.py`, automatically apply `pytest.mark.integration` in `pytest_collection_modifyitems` to tests under `tests/integration/`, `tests/e2e/`, and `tests/conformance/` (or tests requesting `pg_dsn` / `neo4j_adapter` fixtures) so `pytest -q -m "integration" tests/` and `pytest -q -m "not integration" tests/` (configured in `unified-test-suite.config.json:11-20` and invoked by `Taskfile.yml:15-24`) partition the suite accurately!
   - Update any test paths referencing `"sdd"` in `tests/` where needed (while keeping the `"sdd" -> "docs/sdd"` fallback tested).
3. **Add Regression Tests (`tests/unit/test_experience_pipeline.py`, `tests/integration/test_repository.py`, `tests/integration/test_cli.py`):**
   - Add PostgreSQL integration test for `Repository.get_learning_health_summary()`, `AgentProfile` multi-tenant isolation (`tenant_id`), and `ExperienceEvent` deduplication across distinct plans with identical summaries/payloads.
   - Add unit tests for `NatsEventsAdapter` bounded drain with mock live `sub.messages` + `sub.unsubscribe()`, `MAX_EXPERIENCE_CONTENT_BYTES` & P14 rejection, `check_green_step_safety` flag-order bypasses & `INV-STEP-02` step exclusion in `parse_markdown_document`, and `validate_sdd_graph(Path("docs/sdd"))` returning 0 cycles.
4. **Clean Up Governance & Config Drift (`.ci-config`, `AGENTS.md`, `README.md`):**
   - Update `.ci-config:158-162` (`CHANGELOG_PATH=docs/archive/CHANGELOG.md`), remove retired `HANDOFF.md`, `DECISIONS.md`, `TODO.md`, and `roadmap/ROADMAP.md` (per `ADR-11_foundation.yaml`), and update `AGENTS.md` and `README.md` to reference `docs/sdd/` and the full CLI/MCP surface.

---

## 4. Security & Threat Model (STRIDE / ADR-06 / ADR-07)

| Threat / Risk | Mitigation in Design | Verification |
| :--- | :--- | :--- |
| **Cross-Tenant `AgentProfile` Leakage (Information Disclosure / Tampering)** | Migration `0009` adds `tenant_id` to `agent_profiles` with `PRIMARY KEY (tenant_id, agent_id)`, and `Repository.get_agent_profile` / `upsert_agent_profile` enforce `WHERE tenant_id = %(tenant_id)s AND agent_id = %(agent_id)s`. | Integration test in `tests/integration/test_repository.py` verifying two tenants with identical `agent_id` have isolated profiles. |
| **Command Injection via `tier='green'` Runbook Steps (Elevation of Privilege)** | `check_green_step_safety` blocks interpreter `-c`/`-e`/`-m` flags in any flag position, pipes into interpreters, `sed -i`, `awk -i inplace`, `find -execdir`, and `pkill`; `parse_markdown_document` refuses to emit `KBRunbookStep`s when `INV-STEP-02` fails. | Unit tests in `tests/test_docs_tools.py` verifying `INV-STEP-02` rejection and `len(parsed.steps) == 0` on unsafe green steps. |
| **Worker Starvation & Unbounded Memory via `NatsEventsAdapter` (DoS)** | `NatsEventsAdapter.publish()` enforces `65536`-byte cap and P14 Unicode check; `_drain_topic()` drains at most `max_items=100` with a `0.1s` per-message timeout and calls `await sub.unsubscribe()` in `finally:`. | Unit tests in `tests/unit/test_experience_pipeline.py` verifying bounded drain termination and `sub.unsubscribe()` call. |
| **Cross-Domain KB/Runbook Leakage (Information Disclosure)** | `Repository.search_kb_sections` and `Repository.slice_runbook` require `domain_id IS NOT NULL AND domain_id = %(domain_id)s` on `scope = 'domain'` rows instead of wildcarding when `domain_id` is `None`. | Unit/integration test verifying `scope="domain"` with `domain_id=None` does not leak domain-scoped rows. |

---

## 5. Rollback & Recovery Strategy

1. **Additive SQL Migration (`0009`):** `0009_tenant_profiles_and_experience_dedup.sql` is idempotent (`ADD COLUMN IF NOT EXISTS tenant_id TEXT NOT NULL DEFAULT 'default'`, `CREATE UNIQUE INDEX IF NOT EXISTS`). Existing single-tenant rows default to `tenant_id = 'default'`.
2. **Backward-Compatible SDD Path Resolution:** Moving `sdd/*` into `docs/sdd/*` is paired with `_resolve_sdd_root()` in `kb_graph.py`, so any script, test, or caller passing `sdd_root="sdd"` automatically resolves to `docs/sdd`.
3. **Git Revert:** All changes land on branch `fix/plan-021-deep-review-remediation` and can be reverted atomically via `git revert`.

---

## Claim ledger

| # | Claim | Symbol | Citation |
|---|---|---|---|
| 1 | `0001_init_memory.sql` defines `agent_profiles` keyed only by `agent_id TEXT PRIMARY KEY` and `episodes` with timestamp column `ts` (not `created_at`) | `agent_profiles` | `db/migrations/0001_init_memory.sql:9-41` |
| 2 | `0003_reconcile_contracts.sql` defines `uq_episodes_idempotency` on `(tenant_id, agent_id, coalesce(project_id, ''), content_hash)` | `uq_episodes_idempotency` | `db/migrations/0003_reconcile_contracts.sql:25-26` |
| 3 | `0008_runbooks_and_doc_governance.sql` defines `kb_documents` (with `last_verified DATE NOT NULL`) and `kb_runbook_steps` | `kb_documents` | `db/migrations/0008_runbooks_and_doc_governance.sql:6-66` |
| 4 | `experience.py` defines `ExperienceEvent.to_episode()` which currently sets `raw_text` to `self.summary.strip()` or `f"[{self.kind}] {json.dumps(...)}"` without `event_id` | `to_episode` | `src/engramory/core/experience.py:277-289` |
| 5 | `models.py` defines `AgentProfile` without `tenant_id` and `KBDocument` without ISO date check on `last_verified` | `AgentProfile` | `src/engramory/core/models.py:162-173` |
| 6 | `repository.py` defines `get_learning_health_summary` with `ORDER BY created_at ASC, id ASC` on `episodes` | `get_learning_health_summary` | `src/engramory/core/repository.py:1724-1746` |
| 7 | `repository.py` defines `get_agent_profile` and `upsert_agent_profile` querying `agent_profiles` by `agent_id` without `tenant_id` | `get_agent_profile` | `src/engramory/core/repository.py:297-338` |
| 8 | `repository.py` defines `search_kb_sections` and `slice_runbook` where `%(domain_id)s::text IS NULL OR domain_id = %(domain_id)s` matches all domains when `domain_id` is `None` | `slice_runbook` | `src/engramory/core/repository.py:1638-1701` |
| 9 | `events_nats.py` defines `NatsEventsAdapter` with `_drain_topic` iterating `async for msg in messages:` without timeout or `unsubscribe()` | `_drain_topic` | `src/engramory/adapters/dev/events_nats.py:106-120` |
| 10 | `runner.py` defines `run_worker_loop` which iterates `async for raw_ev in events.subscribe(topic=events_topic):` before `reflect()` | `run_worker_loop` | `src/engramory/workers/runner.py:39-119` |
| 11 | `distillation.py` defines `_PROVENANCE_PASSTHROUGH_KEYS` for propagating episode metadata into `Memory.provenance` | `_PROVENANCE_PASSTHROUGH_KEYS` | `src/engramory/workers/distillation.py:115-133` |
| 12 | `docs_tools.py` defines `_GREEN_BANNED_PATTERNS`, `check_green_step_safety`, and `parse_markdown_document` | `check_green_step_safety` | `src/engramory/core/docs_tools.py:410-424` |
| 13 | `docs_tools.py` appends `KBRunbookStep` in `parse_markdown_document` at `if cmd_body and not (tier == "green" and mode != "read-only"):` even when `INV-STEP-02` diagnostics were recorded | `KBRunbookStep` | `src/engramory/core/docs_tools.py:843-859` |
| 14 | `kb_graph.py` defines `normalize_sdd_id`, `extract_sdd_graph`, `ingest_sdd_knowledge_graph`, and `extract_docs_graph` | `normalize_sdd_id` | `src/engramory/core/kb_graph.py:44-64` |
| 15 | `context_tools.py` defines `_extract_yaml_deps` which iterates all values of `traceability` (including `tags`) | `_extract_yaml_deps` | `src/engramory/core/context_tools.py:423-456` |
| 16 | `surface.py` defines `AccessSurface.docs_slice_runbook` which calls `self.memory_search` without passing `scope=scope` | `docs_slice_runbook` | `src/engramory/access/surface.py:761-800` |
| 17 | `mcp/server.py` defines `McpServer.handle_call_tool` and `handle_request` | `handle_call_tool` | `src/engramory/mcp/server.py:374-400` |
| 18 | `cli.py` defines `_cmd_worker_start`, `main`, and `_cmd_memory_ingest_event` | `_cmd_memory_ingest_event` | `src/engramory/cli.py:1585-1600` |
| 19 | `test_cli.py` defines `test_worker_start_with_env` whose `fake_start_daemon` mock omits `events` | `test_worker_start_with_env` | `tests/integration/test_cli.py:338-365` |
| 20 | `Taskfile.yml` defines `EXTRA_FAST_CHECKS` and includes `.unified-test-suite/Taskfile.yml` | `EXTRA_FAST_CHECKS` | `Taskfile.yml:15-24` |
| 21 | `unified-test-suite.config.json` defines `unit` (`-m "not integration"`) and `integration` (`-m "integration"`) test layers | `integration` | `unified-test-suite.config.json:11-20` |
| 22 | `test_no_vendor_imports.py` defines `VENDOR_ROOTS` and enforces hexagonal import boundaries | `VENDOR_ROOTS` | `tests/unit/test_no_vendor_imports.py:17-31` |

---

## Review log

### Pass 1 - 2026-11-10 (Author Self-Review)

- **Scope & Citations Check:** Verified all 22 claims in `## Claim ledger` against exact file paths and line numbers in `platform-engramory`.
- **Architecture & Security Check:** Confirmed that the four 5/5 user-approved recommendations (`nats-py>=2.8.0` bounded drain, dual-subject matching, canonical `docs/sdd/` consolidation with `"sdd"` fallback, and additive migration `0009`) resolve all Critical, Major, and Medium audit findings cleanly.
- **Result:** Proceeding to deterministic `check_plan.py` gate and independent Multi-Lens Subagent Review 1 (Pass 2).

### Pass 2 - 2026-11-10 - independent (Multi-Lens Review 1 — Google / AWS / Microsoft Lenses, Subagent `3c83a139`)

- **Findings Surfaced & Folded (3 Critical, 3 Major, 2 Medium):**
  1. **`[CRITICAL-01]` (`docs/sdd/` exclusion from governed runbook scanners):** Folded into Phase 3.1 & Phase 4.1 — exclude `docs/sdd/` when globbing `docs/` for governed Markdown runbooks in `lint_repo`, `smoke_run_green_steps`, `check_repo_drift`, `slice_local_runbooks`, `extract_docs_graph`, `stage_portal_bundle`, and `_cmd_docs_ingest`; scan both `docs/sdd` and `sdd` in `export_repo_llms`; and update relative links inside `docs/sdd/*/*-00_index.md`.
  2. **`[CRITICAL-02]` (`MISSING_ROLLBACK_STEP` diagnostic preservation on `INV-STEP-02` steps):** Folded into Phase 3.1 — record `rollback_candidates` for all annotated step fences before skipping `steps.append(...)` on `green_violations`, preserving `tests/test_docs_tools.py:178-187`.
  3. **`[CRITICAL-03]` (`normalize_sdd_id` backward compatibility for `BDD.01.03.a100 -> BDD-01`):** Folded into Phase 3.2 — split `<project_id>:` before `.split("_")[0]` while keeping document-level dotted ID normalization (`BDD.01.03.a100 -> BDD-01`) in `kb_graph.py` (`context_tools.py` already uses `_normalize_sdd_key_lossless`).
  4. **`[MAJOR-01]` (`validate_sdd_graph` cycle & index-file exclusions):** Folded into Phase 3.2 & 3.3 — restrict `traceability` extraction in `_extract_yaml_deps` to upstream keys (ignoring `downstream`, `tags`, `code_inventory`, `cross_links`), ignore `"Constrains"` in `related_decisions.dependencies`, remove downstream `ADR-05`/`ADR-03` from `ADR-01_postgres_spine.yaml`, and skip `*-00_index.*` files in `AidocDependencyGraph` and `extract_sdd_graph`.
  5. **`[MAJOR-02]` (`_resolve_sdd_root` subpath & `repo_candidate` parent resolution):** Folded into Phase 4.1 — support `sdd/05_ADR -> docs/sdd/05_ADR` in `_resolve_sdd_root` and resolve `repo_candidate` to `sdd_path.parent.parent` when `sdd_path` is `<repo>/docs/sdd`.
  6. **`[MAJOR-03]` (`Repository.add_episode` unconstrained `ON CONFLICT DO NOTHING`):** Folded into Phase 1.4 — use unconstrained `ON CONFLICT DO NOTHING RETURNING id` and dual-key fallback `SELECT` so conflicts on `uq_episodes_event_id` never raise `UniqueViolation`.
  7. **`[MEDIUM-01]` (`_upsert_profile_best_effort` `tenant_id` propagation):** Folded into Phase 1.2 — pass `args.tenant_id` in `_cmd_init` -> `_upsert_profile_best_effort`.
  8. **`[MEDIUM-02]` (`NatsEventsAdapter._drain_topic` `MagicMock` safety):** Folded into Phase 2.2 — check `inspect.iscoroutinefunction(getattr(sub, "next_msg", None))` before awaiting `sub.next_msg`.
- **Result:** All 8 findings folded into `PLAN-021`. Proceeding to independent Multi-Lens Subagent Review 2 (Pass 3).

### Pass 3 - 2026-11-10 - independent (Multi-Lens Review 2 — Google / Anthropic / AWS / Microsoft Lenses, Subagent `1ecdcc05`)

- **Verification:** Confirmed all 8 folded findings from Pass 2 (`[CRITICAL-01]`..`[MEDIUM-02]`) and all 22 Claim ledger citations against live source files in `platform-engramory`.
- **Result:** ready — no new findings.
