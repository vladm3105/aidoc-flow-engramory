"""Wave P3 security, performance, portability, extraction & SDD parity tests (CHG-013)."""

from __future__ import annotations

import asyncio
import stat
import subprocess
import sys
import uuid
from datetime import date
from pathlib import Path
from types import MappingProxyType
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import psycopg
import pytest
from psycopg_pool import PoolTimeout

from engramory.access.authz import ActorContext
from engramory.access.poisoning_guard import (
    MemoryPoisoningError as ShimPoisoningError,
)
from engramory.access.poisoning_guard import (
    inspect_memory_write as shim_inspect_memory_write,
)
from engramory.access.surface import MAX_K, MAX_METADATA_BYTES, AccessSurface
from engramory.adapters.dev.graph_neo4j import _IDENT_RE, _sanitize_ident, _validate_tenant_id
from engramory.adapters.dev.llm_litellm import MAX_RESPONSE_BYTES, LiteLLMAdapter, _strip_crlf
from engramory.adapters.dev.storage_pg import PgBlobStorageAdapter
from engramory.adapters.dev.vector_pg import PgVectorAdapter
from engramory.adapters.factory import ACTIVE_PROFILES, PLANNED_PROFILES, adapters_for_profile
from engramory.cli import main as cli_main
from engramory.core.citations import generate_evidence_ref
from engramory.core.context_tools import (
    AidocDependencyGraph,
    ArtifactNode,
    StackPolicyValidator,
    _extract_yaml_deps,
    _host_doc_key,
)
from engramory.core.docs_tools import (
    _ALLOWED_SMOKE_ENV_KEYS,
    _GIT_REF_RE,
    _extract_frontmatter,
    check_repo_drift,
    lint_repo,
    resolve_confined_repo_path,
    smoke_run_green_steps,
    validate_watched_path_glob,
)
from engramory.core.experience import ExperienceEvent, compute_learning_health_metrics
from engramory.core.extraction.enrichment import SemanticChunker
from engramory.core.extraction.multimodal import (
    VideoAudioExtractor,
    _run_bounded_subprocess,
    infer_media_mime_type,
)
from engramory.core.git_sync import GitSyncEngine
from engramory.core.kb_graph import normalize_neo4j_rel
from engramory.core.models import (
    KBDocument,
    KBSection,
    Memory,
    ValidationError,
)
from engramory.core.parsers.base import Section
from engramory.core.parsers.html import HTMLParser
from engramory.core.parsers.markdown import MarkdownParser
from engramory.core.parsers.text import TextParser
from engramory.core.poisoning_guard import MemoryPoisoningError, inspect_memory_write
from engramory.core.repository import Repository, StoreUnavailable, _escape_ilike, _hydrate_memory
from engramory.ports.storage import MAX_CHUNK_SIZE, MIN_CHUNK_SIZE, KBBlob, StorageError

REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS = REPO_ROOT / "db" / "migrations"


# ---------------------------------------------------------------------------
# M1: DB pooling, error translation, ILIKE escape, metadata bounds & compare
# ---------------------------------------------------------------------------


def test_m1_escape_ilike_and_hydrate_null_embedding_model() -> None:
    assert _escape_ilike(r"100%_match\path") == r"100\%\_match\\path"

    hydrated = _hydrate_memory(
        {
            "id": "mem-null-model",
            "agent_id": "agent-1",
            "project_id": "proj-1",
            "domain_id": "core",
            "tenant_id": "t-1",
            "scope": "agent",
            "type": "semantic",
            "content_raw": "raw note",
            "summary": "summary note",
            "confidence": 0.9,
            "source_trust": "agent",
            "status": "active",
            "embedding_model": None,
            "embedding_dims": None,
            "provenance": {"source": "unit-test"},
            "valid_from": None,
            "valid_to": None,
            "supersedes": None,
        }
    )
    assert hydrated.embedding_model == "none"


@pytest.mark.asyncio
async def test_m1_repo_and_storage_translate_driver_errors() -> None:
    class _FailingPool:
        def __init__(self, exc: Exception) -> None:
            self._exc = exc

        def connection(self, timeout: float = 5.0) -> object:
            raise self._exc

    for exc_obj in (
        psycopg.OperationalError("conn dropped"),
        psycopg.InterfaceError("closed"),
        PoolTimeout("pool exhausted"),
        TimeoutError("timed out"),
    ):
        repo = Repository("postgresql://nobody:nope@127.0.0.1:1/nothing", min_size=0)
        failing = AsyncMock(return_value=_FailingPool(exc_obj))
        repo._get_pool = failing  # type: ignore[method-assign]  # reason: fault stub
        with pytest.raises(StoreUnavailable):
            async with repo._conn():
                pass

    class _BadConnFactory:
        def __init__(self, exc: Exception) -> None:
            self._exc = exc

        async def __aenter__(self) -> object:
            raise self._exc

        async def __aexit__(self, *args: object) -> None:
            return None

    for pg_exc in (
        psycopg.OperationalError("blob store unreachable"),
        psycopg.InterfaceError("broken socket"),
        TimeoutError("blob timeout"),
    ):
        storage = PgBlobStorageAdapter(
            "postgresql://nobody:nope@127.0.0.1:1/nothing",
            conn_factory=lambda e=pg_exc: _BadConnFactory(e),
        )
        with pytest.raises(StorageError, match="blob store unavailable"):
            async with storage._conn():
                pass


@pytest.mark.asyncio
async def test_m1_surface_metadata_bounds_cap_and_nonmutating_compare() -> None:
    class _SurfaceRepoStub:
        def __init__(self) -> None:
            self.recorded_writes: list[str] = []
            self.get_memories_calls = 0

        async def record_audit(self, **kwargs: object) -> None:
            return None

        async def add_episode(self, ep: object) -> str:
            self.recorded_writes.append("add_episode")
            return "ep-1"

        async def upsert_agent_profile(self, *args: object, **kwargs: object) -> None:
            self.recorded_writes.append("upsert_agent_profile")

        async def get_brain_variant_metrics(self, **kwargs: object) -> dict[str, object]:
            return {
                "skill_count": 2,
                "memory_count": 2,
                "mean_confidence": 0.8,
                "retrieval_count": 5,
                "useful_count": 4,
                "useful_rate": 0.8,
            }

        async def get_memories(self, **kwargs: object) -> list[Memory]:
            self.get_memories_calls += 1
            return []

        async def search_kb_sections(self, query: str = "", **kwargs: object) -> list[KBSection]:
            _ = query
            self.recorded_writes.append(f"limit:{kwargs.get('limit')}")
            return []

    repo: Any = _SurfaceRepoStub()
    llm = MagicMock()
    llm.embed = AsyncMock(return_value=[[0.1] * 8])
    surface = AccessSurface(repo, llm=llm, storage=MagicMock())
    ctx = ActorContext(
        agent_id="a1",
        project_id="p1",
        tenant_id="t1",
        scopes=frozenset({"agent", "project", "domain"}),
    )

    oversized = {"blob": "x" * (MAX_METADATA_BYTES + 256)}
    with pytest.raises(ValidationError, match="exceeds"):
        await surface.memory_add(ctx, content="ok", kind="note", metadata=oversized)
    with pytest.raises(ValidationError, match="exceeds"):
        await surface.agent_profile_upsert(ctx, standing_preferences=oversized)
    with pytest.raises(ValidationError, match="exceeds"):
        await surface.agent_profile_upsert(ctx, brain_config=oversized)
    with pytest.raises(ValidationError, match="exceeds"):
        await surface.knowledge_ingest(
            ctx,
            doc_id="d1",
            citation="c1",
            text="safe",
            evidence_ref="CHG-013",
            media_metadata=oversized,
        )
    with pytest.raises(ValidationError, match="exceeds"):
        await surface.knowledge_ingest_batch(
            ctx,
            sections=[
                KBSection(
                    doc_id="d1",
                    citation="c1",
                    text="safe",
                    tenant_id="t1",
                    project_id="p1",
                    media_metadata=oversized,
                )
            ],
            evidence_ref="CHG-013",
        )
    with pytest.raises(ValidationError, match="exceeds"):
        await surface.blob_put(ctx, key="k1", data=b"hi", metadata=oversized)

    cmp_res = await surface.agent_brain_compare(
        ctx, variant_a="v1", variant_b="v2", query="how does pool work", k=3
    )
    assert repo.get_memories_calls == 2
    assert repo.recorded_writes == []
    assert "winner" in cmp_res

    await surface.knowledge_search(ctx, query="how", limit=9999)
    assert repo.recorded_writes[-1] == f"limit:{MAX_K}"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_m1_live_pg18_migration_0013_up_down_indexes_fk_trigger_and_guc_isolation(
    pg_dsn: str, pg_app_dsn: str
) -> None:
    up_sql = (MIGRATIONS / "0013_wave_p3_indexes_and_hygiene.sql").read_text(encoding="utf-8")
    down_sql = (MIGRATIONS / "down" / "0013_wave_p3_indexes_and_hygiene.sql").read_text(
        encoding="utf-8"
    )
    tenant_a = f"t-p3-a-{uuid.uuid4().hex[:8]}"
    tenant_b = f"t-p3-b-{uuid.uuid4().hex[:8]}"
    sha = "a" * 64

    app_pool_repo = Repository(pg_app_dsn, min_size=1, max_size=1)
    super_repo = Repository(pg_dsn, min_size=1, max_size=2)
    try:
        # 1. Verify all 6 Wave P3 indexes + fk + trigger exist
        with psycopg.connect(pg_dsn, autocommit=True) as conn:
            idx_rows = conn.execute(
                """
                SELECT indexname FROM pg_indexes
                WHERE schemaname = 'public' AND indexname = ANY(%s)
                """,
                (
                    [
                        "idx_memories_tenant_domain_live",
                        "idx_memories_tenant_variant_live",
                        "idx_kb_sections_tenant_doc_ver",
                        "idx_memory_retrievals_tenant_mem",
                        "idx_audit_records_tenant_ts",
                        "idx_kb_blobs_status_updated",
                    ],
                ),
            ).fetchall()
            assert len(idx_rows) == 6

            # Nullable embedding_model row + hydration as 'none'
            mem_id = str(uuid.uuid4())
            conn.execute(
                """
                INSERT INTO memories (
                    id, tenant_id, agent_id, scope, type, content_raw, summary,
                    embedding_model, provenance
                ) VALUES (%s, %s, 'agent-1', 'agent', 'semantic', 'raw text', 'sum text',
                          NULL, '{"src":"test"}'::jsonb)
                """,
                (mem_id, tenant_a),
            )

            # Blob + section linking, trg_kb_blobs_updated_at & ON DELETE SET NULL FK
            conn.execute(
                """
                INSERT INTO kb_blobs (tenant_id, sha256, project_id, media_type, byte_size,
                                      chunk_size, chunk_count, storage_tier, status)
                VALUES (%s, %s, 'p1', 'image/png', 10, 65536, 1, 'logged', 'uploading')
                """,
                (tenant_a, sha),
            )
            before_ts = conn.execute(
                "SELECT updated_at FROM kb_blobs WHERE tenant_id = %s AND sha256 = %s",
                (tenant_a, sha),
            ).fetchone()
            assert before_ts is not None
            conn.execute(
                "UPDATE kb_blobs SET status = 'ready' WHERE tenant_id = %s AND sha256 = %s",
                (tenant_a, sha),
            )
            after_ts = conn.execute(
                "SELECT updated_at FROM kb_blobs WHERE tenant_id = %s AND sha256 = %s",
                (tenant_a, sha),
            ).fetchone()
            assert after_ts is not None and after_ts[0] >= before_ts[0]

            sec_id = str(uuid.uuid4())
            conn.execute(
                """
                INSERT INTO kb_sections (id, doc_id, citation, text, tenant_id, scope,
                                         agent_id, version, modality, blob_sha256)
                VALUES (%s, 'doc-fk', 'c1', 'image caption', %s, 'project', 'agent-1', 1,
                        'image', %s)
                """,
                (sec_id, tenant_a, sha),
            )
            conn.execute(
                "DELETE FROM kb_blobs WHERE tenant_id = %s AND sha256 = %s",
                (tenant_a, sha),
            )
            fk_cleared = conn.execute(
                "SELECT blob_sha256 FROM kb_sections WHERE id = %s",
                (sec_id,),
            ).fetchone()
            assert fk_cleared is not None and fk_cleared[0] is None

            # Retrieval feedback summary aggregation
            conn.execute(
                """
                INSERT INTO memories (
                    id, tenant_id, agent_id, scope, type, content_raw, summary,
                    embedding_model, provenance
                ) VALUES (%s, %s, 'agent-2', 'agent', 'semantic', 'v2', 'v2',
                          'embed', '{"brain_variant":"variant-z"}'::jsonb)
                """,
                (str(uuid.uuid4()), tenant_a),
            )
            ret_id_1 = str(uuid.uuid4())
            ret_id_2 = str(uuid.uuid4())
            conn.execute(
                """
                INSERT INTO memory_retrievals (id, memory_id, tenant_id, agent_id, feedback)
                VALUES (%s, %s, %s, 'agent-1', 'useful'),
                       (%s, %s, %s, 'agent-1', 'not_useful')
                """,
                (ret_id_1, mem_id, tenant_a, ret_id_2, mem_id, tenant_a),
            )

        # Hydrate NULL embedding_model and verify feedback summary query
        mems = await super_repo.get_memories(
            tenant_id=tenant_a,
            agent_id="agent-1",
            scopes=("agent",),
        )
        assert any(m.id == mem_id and m.embedding_model == "none" for m in mems)

        fb = await super_repo.get_retrieval_feedback_summary(
            tenant_id=tenant_a,
            memory_ids=[mem_id],
        )
        assert fb == {"total": 2, "useful": 1, "not_useful": 1, "harmful": 0}

        # 2. Verify max_size=1 pool transaction-local GUC reset prevents cross-tenant leakage
        guc_sql = "SELECT current_setting('app.tenant_id', true)"
        async with app_pool_repo._conn(tenant_id=tenant_a) as c1:
            g1 = await (await c1.execute(guc_sql)).fetchone()
            assert g1 is not None and next(iter(g1.values())) == tenant_a
        async with app_pool_repo._conn(tenant_id=tenant_b) as c2:
            g2 = await (await c2.execute(guc_sql)).fetchone()
            assert g2 is not None and next(iter(g2.values())) == tenant_b
        async with app_pool_repo._conn() as c3:
            g3 = await (await c3.execute(guc_sql)).fetchone()
            assert g3 is not None and next(iter(g3.values())) == ""

        # 3. Verify 0013 down migration cleanly reverts, and re-apply 0013 up for shared DB state
        with psycopg.connect(pg_dsn, autocommit=True) as conn:
            conn.execute(down_sql)  # type: ignore[arg-type]  # reason: raw DDL
            down_fk = conn.execute(
                "SELECT 1 FROM pg_constraint WHERE conname = 'fk_kb_sections_blob'"
            ).fetchone()
            assert down_fk is None
            conn.execute(up_sql)  # type: ignore[arg-type]  # reason: raw DDL
            up_fk = conn.execute(
                "SELECT 1 FROM pg_constraint WHERE conname = 'fk_kb_sections_blob'"
            ).fetchone()
            assert up_fk is not None
    finally:
        await app_pool_repo.close()
        await super_repo.close()


# ---------------------------------------------------------------------------
# M2 & L3: Profile routing, close() lifecycle & CLI init permissions
# ---------------------------------------------------------------------------


def test_m2_l3_profile_routing_close_lifecycle_and_cli_init(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert ACTIVE_PROFILES == ("dev", "stage")
    assert PLANNED_PROFILES == ("gcp", "azure")

    stage_bundle = adapters_for_profile("stage", dsn="postgresql://u:p@127.0.0.1:5432/db")
    assert stage_bundle.profile == "stage"
    for planned in PLANNED_PROFILES:
        with pytest.raises(NotImplementedError):
            adapters_for_profile(planned, dsn="postgresql://u:p@127.0.0.1:5432/db")
    with pytest.raises(ValueError, match="unknown ENGRAMORY_PROFILE"):
        adapters_for_profile("bad-profile", dsn="postgresql://u:p@127.0.0.1:5432/db")

    async def _check_close_idempotency() -> None:
        vec = PgVectorAdapter("postgresql://nobody:nope@127.0.0.1:1/nothing")
        await vec.close()
        await vec.close()
        repo = Repository("postgresql://nobody:nope@127.0.0.1:1/nothing", min_size=0)
        await repo.close()
        await repo.close()

    asyncio.run(_check_close_idempotency())

    # _cmd_init creates 0700/0600 files and uses line-exact .gitignore check
    work = tmp_path / "cli_init_ws"
    work.mkdir()
    gitignore = work / ".gitignore"
    gitignore.write_text("# .engramory/\n.engramory-backup\n", encoding="utf-8")
    monkeypatch.chdir(work)
    monkeypatch.setenv("ENGRAMORY_PROFILE", "dev")
    rc = cli_main(
        [
            "init",
            "--agent-id",
            "agent-p3",
            "--project-id",
            "proj-p3",
            "--tenant-id",
            "tenant-p3",
            "--dsn",
            "postgresql://u:p@127.0.0.1:1/db",
        ]
    )
    assert rc == 0
    cfg_dir = work / ".engramory"
    cfg_file = cfg_dir / "config.toml"
    assert stat.S_IMODE(cfg_dir.stat().st_mode) == 0o700
    assert stat.S_IMODE(cfg_file.stat().st_mode) == 0o600
    gi_lines = [ln.strip() for ln in gitignore.read_text(encoding="utf-8").splitlines()]
    assert ".engramory/" in gi_lines

    # Second init skips append since '.engramory/' line now exists exactly
    rc2 = cli_main(
        [
            "init",
            "--force",
            "--agent-id",
            "agent-p3",
            "--project-id",
            "proj-p3",
            "--tenant-id",
            "tenant-p3",
            "--dsn",
            "postgresql://u:p@127.0.0.1:1/db",
        ]
    )
    assert rc2 == 0
    assert gitignore.read_text(encoding="utf-8").splitlines().count(".engramory/") == 1


# ---------------------------------------------------------------------------
# M4: Media, parsers, enrichment, LiteLLM, citations, experience & GitSync
# ---------------------------------------------------------------------------


def test_m4_multimodal_parsers_enrichment_litellm_citations_experience(tmp_path: Path) -> None:
    # 1. AVIF & HEIC magic detection
    assert infer_media_mime_type(tmp_path / "pic.bin", b"\x00\x00\x00\x18ftypavif") == (
        "image",
        "image/avif",
    )
    assert infer_media_mime_type(tmp_path / "pic.bin", b"\x00\x00\x00\x18ftypheic") == (
        "image",
        "image/heic",
    )

    # 2. Bounded subprocess caps oversized stdout
    with pytest.raises(ValueError, match="exceeded"):
        _run_bounded_subprocess(
            [sys.executable, "-c", "print('A' * 5000)"],
            timeout=5,
            max_bytes=512,
        )

    # 3. VideoAudioExtractor math.ceil + truncation warning
    vid = tmp_path / "clip.mp4"
    vid.write_bytes(b"\x00\x00\x00\x18ftypisom" + b"\x00" * 32)
    ext = VideoAudioExtractor(segment_seconds=60, max_segments=2)
    with patch.object(ext, "probe_media", return_value={"duration_seconds": 125.0}):
        res = ext.extract(vid)
    assert len(res.sections) == 2
    assert any("truncated" in w.lower() for w in res.warnings)

    # 4. HTML stdlib nested ignore tags + offsets
    html_res = HTMLParser()._parse_with_stdlib(
        "<div><h1>Title</h1><script>var a='<script>';evil();</script><p>Visible body</p></div>",
        "sample.html",
    )
    joined = " ".join(s.content for s in html_res.sections)
    assert "Visible body" in joined
    assert "evil()" not in joined
    assert all(s.char_start >= 0 and s.char_end >= s.char_start for s in html_res.sections)

    # 5. TextParser exact offsets on repeated words & mixed whitespace
    sample_txt = "alpha   beta\talpha   gamma"
    chunks = TextParser(strategy="chunk", chunk_words=2).parse(sample_txt).sections
    assert len(chunks) == 2
    assert chunks[0].char_start == 0
    assert sample_txt[chunks[0].char_start : chunks[0].char_end] == "alpha   beta"
    assert sample_txt[chunks[1].char_start : chunks[1].char_end] == "alpha   gamma"

    # 6. MarkdownParser handles whitespace-only _subchunk_section safely
    md = MarkdownParser(max_words_per_chunk=50)
    empty_sec = Section(
        section_id="sec_1", name="Blank", order=1, level=1, content="   \n\t ", metadata={"id": "D"}
    )
    assert md._subchunk_section(empty_sec, start_order=1) == []

    # 7. SemanticChunker clamping & non-mutating Pass 2
    ch = SemanticChunker(min_words=-5, max_words=2)
    assert ch.min_words == 0
    assert ch.max_words == 2
    s1 = Section(section_id="s1", name="One", order=1, level=1, content="tiny")
    s2 = Section(section_id="s2", name="Two", order=2, level=1, content="part")
    ch2 = SemanticChunker(min_words=10, max_words=50)
    merged = ch2.refine_sections([s1, s2])
    assert len(merged) == 1
    assert s1.content == "tiny"

    # 8. LiteLLMAdapter CRLF stripping, MAX_RESPONSE_BYTES, unconditional dimensions
    assert _strip_crlf('bad\r\n"name') == "badname"
    adapter = LiteLLMAdapter(
        base_url="http://127.0.0.1:4000", default_embed_model="embed-x", default_embed_dims=1536
    )
    oversized_resp = MagicMock()
    oversized_resp.__enter__ = MagicMock(return_value=oversized_resp)
    oversized_resp.__exit__ = MagicMock(return_value=False)
    oversized_resp.read.return_value = b"x" * (MAX_RESPONSE_BYTES + 1)
    with patch("urllib.request.urlopen", return_value=oversized_resp):
        with pytest.raises(RuntimeError, match="MAX_RESPONSE_BYTES"):
            adapter._sync_request("embeddings", {"input": ["hi"]})

    seen_payloads: list[dict[str, object]] = []

    def _capture_sync(ep: str, payload: dict[str, object]) -> dict[str, object]:
        seen_payloads.append(payload)
        return {"data": [{"embedding": [0.1] * 1536}]}

    with patch.object(adapter, "_sync_request", side_effect=_capture_sync):
        vecs = asyncio.run(adapter.embed(texts=["hello"]))
    assert len(vecs) == 1 and seen_payloads[0]["dimensions"] == 1536

    # 9. Citations streaming digest + Experience int vs float exec_ready_score
    blob_file = tmp_path / "ev.txt"
    blob_file.write_bytes(b"evidence bytes")
    ref = generate_evidence_ref(blob_file, git_sha="local-uncommitted")
    assert ref.startswith("sha256:") and len(ref) == 71

    from engramory.core.models import Episode

    ep_auth = Episode(
        agent_id="a1",
        tenant_id="t1",
        project_id="p1",
        kind="plan.authored",
        content_raw="plan",
        metadata={
            "origin": {"repo": "org/r", "plan_id": "PLAN-1"},
            "payload": {"exec_ready_score": 1},
        },
    )
    ep_done = Episode(
        agent_id="a1",
        tenant_id="t1",
        project_id="p1",
        kind="execution.completed",
        content_raw="done",
        metadata={
            "origin": {"repo": "org/r", "plan_id": "PLAN-1"},
            "payload": {"first_pass_success": False},
        },
    )
    health = compute_learning_health_metrics([ep_auth, ep_done])
    assert health["exec_ready_calibration"]["mean_predicted_ready_ratio"] == 0.01


def test_m4_gitsync_origin_parsing_failclosed_and_upsert_before_delete(tmp_path: Path) -> None:
    repo = tmp_path / "gitea_repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "dev"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@e.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
    (repo / "doc.md").write_text("# Doc\nClean original text.\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True)

    engine = GitSyncEngine()
    for url, expected in (
        ("ssh://git@gitea.example.com:2222/org/platform-engramory.git", "org/platform-engramory"),
        ("git@github.com:org/platform-engramory.git", "org/platform-engramory"),
        ("https://gitea.example.com/org/platform-engramory.git", "org/platform-engramory"),
    ):
        subprocess.run(
            ["git", "remote", "remove", "origin"], cwd=repo, check=False, capture_output=True
        )
        subprocess.run(["git", "remote", "add", "origin", url], cwd=repo, check=True)
        assert engine.get_git_origin_repo(repo) == expected

    # Fail closed on non-zero git exit
    with pytest.raises(ValidationError, match="failed"):
        engine.get_changed_files(repo, base_sha="deadbeefdeadbeef", head_sha="HEAD")

    # Upsert-before-delete: failing batch upsert must never call delete_kb_sections_for_doc
    deleted_calls: list[str] = []

    class _FailingUpsertRepo:
        async def delete_kb_sections_for_doc(
            self, tenant_id: str, doc_id: str, **kw: object
        ) -> int:
            deleted_calls.append(doc_id)
            return 1

        async def upsert_kb_sections_batch(self, sections: object) -> dict[object, str]:
            raise RuntimeError("transient db error")

        async def upsert_kb_document(self, doc: object) -> str:
            return "d"

        async def upsert_kb_runbook_steps(self, *a: object, **k: object) -> int:
            return 0

    failing_repo: Any = _FailingUpsertRepo()
    with pytest.raises(RuntimeError, match="transient db error"):
        asyncio.run(engine.sync(failing_repo, repo))
    assert deleted_calls == []


# ---------------------------------------------------------------------------
# M5: Cross-platform portability, CRLF, builtins, unsafe globs & root-first
# ---------------------------------------------------------------------------


def test_m5_portability_crlf_builtins_globs_and_allowed_root_first(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for win_key in (
        "SYSTEMROOT",
        "COMSPEC",
        "PATHEXT",
        "TEMP",
        "TMP",
        "USERPROFILE",
        "APPDATA",
        "LOCALAPPDATA",
    ):
        assert win_key in _ALLOWED_SMOKE_ENV_KEYS

    # allowed_root-first even when cwd is a subdir inside allowed_root
    repo = tmp_path / "repo"
    sub = repo / "sub"
    sub.mkdir(parents=True)
    monkeypatch.chdir(sub)
    resolved = resolve_confined_repo_path("docs/guide.md", allowed_root=repo)
    assert resolved == (repo / "docs" / "guide.md").resolve()

    # CRLF frontmatter extraction & lint_repo
    crlf_md = (
        "---\r\n"
        "id: DOC-CRLF\r\n"
        "title: CRLF Doc\r\n"
        "kind: runbook\r\n"
        "domain: core\r\n"
        "namespace: ns:platform\r\n"
        "status: active\r\n"
        "autonomy_tier: green\r\n"
        "watched_paths:\r\n"
        '  - "docs/**"\r\n'
        "last_verified: 2026-11-22\r\n"
        "---\r\n\r\n"
        "# Heading\r\n\r\nBody line.\r\n"
    )
    fm, body, _, err = _extract_frontmatter(crlf_md)
    assert err is None and fm is not None and fm["id"] == "DOC-CRLF"
    assert "\r" not in body

    doc_dir = tmp_path / "crlf_repo"
    (doc_dir / "docs").mkdir(parents=True)
    (doc_dir / "docs" / "run.md").write_bytes(crlf_md.encode("utf-8"))
    lint_out = lint_repo(doc_dir)
    assert lint_out["files_checked"] == 1

    # Pure-Python builtin smoke command fallback on FileNotFoundError
    rb_body = (
        "# Smoke\n"
        '```bash {id="s1" tier="green" mode="read-only" expect_exit=0 timeout_sec=5}\n'
        "echo hello world\n"
        "```\n"
    )
    smoke_repo = tmp_path / "smoke_builtins"
    (smoke_repo / "docs").mkdir(parents=True)
    (smoke_repo / "docs" / "smoke.md").write_text(
        crlf_md.replace("\r\n", "\n").split("# Heading")[0] + rb_body,
        encoding="utf-8",
    )
    with patch(
        "engramory.core.docs_tools.subprocess.run", side_effect=FileNotFoundError("no echo")
    ):
        s_res = smoke_run_green_steps(smoke_repo, dry_run=False)
    assert s_res["status"] == "ok" and s_res["passed"] == 1
    assert s_res["steps"][0]["stdout"] == "hello world\n"

    # Unsafe watched_paths globs rejected identically in lint_document and check_repo_drift
    for bad_glob in ("/etc/passwd", r"C:\Windows\System32\*", "../../secret/**"):
        norm, gerr = validate_watched_path_glob(bad_glob)
        assert norm is None and gerr is not None
    assert validate_watched_path_glob(r"src\engramory\**.py")[0] == "src/engramory/**.py"

    bad_doc_repo = tmp_path / "bad_glob_repo"
    (bad_doc_repo / "docs").mkdir(parents=True)
    (bad_doc_repo / "docs" / "bad.md").write_text(
        crlf_md.replace("\r\n", "\n").replace('"docs/**"', '"../outside/**"'),
        encoding="utf-8",
    )
    bad_lint = lint_repo(bad_doc_repo)
    assert any(d["rule_id"] == "INVALID_WATCHED_PATH_GLOB" for d in bad_lint["errors"])
    bad_drift = check_repo_drift(
        bad_doc_repo, changed_files=["docs/other.md"], today=date(2026, 11, 22)
    )
    assert any(f["kind"] == "INVALID_WATCHED_PATH_GLOB" for f in bad_drift["findings"])


# ---------------------------------------------------------------------------
# M8 & L1 & L4: SDD invariants, StackPolicyValidator, anchoring & immutability
# ---------------------------------------------------------------------------


def test_m8_sdd_invariants_sketches_host_tags_and_stack_policy(tmp_path: Path) -> None:
    assert _host_doc_key("BRD-01-01-a1b2") == "BRD-01"
    assert _host_doc_key("@proj:PRD.02.03.abcd") == "PRD-02"
    assert _host_doc_key("BRD-01") is None

    # Sketches skipped & 4-segment host doc tag satisfies cardinality
    g = AidocDependencyGraph()
    g.add_node(ArtifactNode("BRD-01", "BRD", "Core seed", dependencies=[]))
    g.add_node(ArtifactNode("BRD-02", "BRD", "Draft sketch", dependencies=[], is_sketch=True))
    g.add_node(ArtifactNode("EARS-01", "EARS", "Req", dependencies=["BRD-01-01-a1b2"]))
    g.add_node(ArtifactNode("BDD-01", "BDD", "Scenario", dependencies=["EARS-01"]))
    passed, violations = g.validate_invariants()
    assert passed is True and violations == []

    # _extract_yaml_deps parses *_reference and trace.cross_links.depends
    deps = _extract_yaml_deps(
        {
            "document_control": {"brd_reference": "BRD-01", "spec_reference": "SPEC-02"},
            "trace": {"cross_links": {"depends": ["ADR-01", "EARS-01"]}},
        }
    )
    assert {"BRD-01", "SPEC-02", "ADR-01", "EARS-01"}.issubset(set(deps))

    # StackPolicyValidator fails closed on empty allowed_packages & scans optional/dep-groups
    (tmp_path / ".aidoc").mkdir()
    (tmp_path / ".aidoc" / "stack-policy.yaml").write_text(
        "boundaries:\n  strict_allowlist: true\ndependencies:\n  allowed_packages: []\n",
        encoding="utf-8",
    )
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname="x"\ndependencies=[]\n'
        '[project.optional-dependencies]\next=["bannedopt>=1.0"]\n'
        '[dependency-groups]\ndev=["bannedgrp>=2.0"]\n',
        encoding="utf-8",
    )
    ok_empty, v_empty = StackPolicyValidator(tmp_path).validate()
    assert not ok_empty and any("empty" in v.lower() for v in v_empty)

    (tmp_path / ".aidoc" / "stack-policy.yaml").write_text(
        "boundaries:\n  strict_allowlist: true\n"
        "dependencies:\n  allowed_packages:\n    - name: pyyaml\n",
        encoding="utf-8",
    )
    ok_opt, v_opt = StackPolicyValidator(tmp_path).validate()
    assert not ok_opt
    joined = "\n".join(v_opt)
    assert "bannedopt" in joined and "bannedgrp" in joined


def test_l1_l4_immutability_anchoring_poisoning_shim_and_repo_hygiene() -> None:
    # Deep immutability across domain dataclasses
    mem = Memory(
        content_raw="raw",
        summary="sum",
        type="semantic",
        embedding_model="embed",
        provenance={"nested": {"k": [1, 2]}},
        agent_id="a1",
        tenant_id="t1",
    )
    assert isinstance(mem.provenance, MappingProxyType)
    with pytest.raises(TypeError):
        mem.provenance["nested"]["k"] = 3
    assert isinstance(mem.provenance["nested"]["k"], tuple)

    ev = ExperienceEvent(
        project="p1",
        producer="a1",
        kind="execution.completed",
        occurred_at="2026-11-22T00:00:00Z",
        payload_type="task",
        payload={"nested": {"flag": True}, "items": ["x"]},
    )
    with pytest.raises(TypeError):
        ev.payload["nested"]["flag"] = False
    assert isinstance(ev.payload["items"], tuple)

    blob = KBBlob(
        tenant_id="t1",
        sha256="b" * 64,
        media_type="image/png",
        byte_size=128,
        chunk_size=MIN_CHUNK_SIZE,
        metadata={"nested": ["a"]},
    )
    with pytest.raises(TypeError):
        blob.metadata["nested"] = []  # type: ignore[index]  # reason: immutable
    with pytest.raises(ValueError, match="chunk_size"):
        KBBlob(
            tenant_id="t1",
            sha256="b" * 64,
            media_type="image/png",
            byte_size=128,
            chunk_size=MIN_CHUNK_SIZE - 1,
        )
    with pytest.raises(ValueError, match="chunk_size"):
        KBBlob(
            tenant_id="t1",
            sha256="b" * 64,
            media_type="image/png",
            byte_size=128,
            chunk_size=MAX_CHUNK_SIZE + 1,
        )

    node = ArtifactNode("N-1", "SPEC", "text", dependencies=["A-1"])
    assert isinstance(node.dependencies, tuple)

    # Dynamic last_verified default
    doc = KBDocument(doc_id="D-1", repo_path="docs/a.md", title="Title", kind="guide")
    assert doc.last_verified == date.today().isoformat()

    # Anchored regexes reject embedded/trailing newlines
    assert _IDENT_RE.fullmatch("VALID_LABEL\n") is None
    with pytest.raises(ValueError, match="invalid label identifier"):
        _sanitize_ident("VALID_LABEL\n", "label")
    with pytest.raises(ValidationError):
        _validate_tenant_id("t1\n")
    assert _GIT_REF_RE.fullmatch("HEAD\n") is None

    # Core poisoning guard + access shim parity
    assert inspect_memory_write is shim_inspect_memory_write
    assert MemoryPoisoningError is shim_poisoning_class()

    # Repo hygiene: executable bit on deploy-stage.sh & removed example-domain.yaml
    assert (REPO_ROOT / "scripts" / "deploy-stage.sh").stat().st_mode & stat.S_IXUSR
    assert not (REPO_ROOT / "config" / "domains" / "example-domain.yaml").exists()
    assert normalize_neo4j_rel("depends-on!") == "DEPENDS_ON"


def shim_poisoning_class() -> type[Exception]:
    return ShimPoisoningError
