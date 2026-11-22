"""Unit tests closing coverage gaps in CLI, AccessSurface, Knowledge, NATS, LLM & MCP."""

from __future__ import annotations

import base64
import hashlib
import json
import struct
import subprocess
from collections.abc import AsyncIterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

import engramory.cli as cli_mod
from engramory.access.authz import ActorContext
from engramory.access.jwt_auth import (
    AuthenticationError,
    TokenClaims,
    check_token_revocation,
    claims_to_actor_context,
    validate_security_gate_url,
)
from engramory.access.surface import (
    AccessSurface,
    AuthzError,
    GovernedWriteRejected,
    MemoryHit,
    _compute_passport_sha256,
)
from engramory.adapters.dev.events_nats import (
    NatsEventsAdapter,
    _decode_nats_msg,
    _topic_matches,
    _validate_header_value,
    _validate_topic,
)
from engramory.adapters.dev.llm_litellm import (
    MAX_AUDIO_BYTES,
    MAX_VISION_IMAGE_BYTES,
    LiteLLMAdapter,
)
from engramory.cli import CliConfig, ConfigError
from engramory.cli import main as cli_main
from engramory.core.experience import compute_learning_health_metrics
from engramory.core.git_sync import GitSyncEngine
from engramory.core.knowledge import KnowledgeCore
from engramory.core.memory import MemoryCore
from engramory.core.models import (
    AgentProfile,
    Episode,
    KBDocument,
    KBSection,
    Memory,
    ValidationError,
)
from engramory.core.parsers.html import HTMLParser
from engramory.core.repository import StoreUnavailable
from engramory.mcp.server import (
    McpServer,
    _coerce_int,
    _format_context_slice_digest,
    format_context_slice_markdown,
)
from engramory.ports.storage import KBBlob

_CTX = ActorContext(
    agent_id="agent-1",
    project_id="proj-1",
    tenant_id="tenant-1",
    domain_id="core",
    scopes=frozenset({"agent", "project", "domain"}),
)


def _mock_cli_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    monkeypatch.setenv("ENGRAMORY_PROFILE", "dev")
    cfg = CliConfig(
        ctx=_CTX,
        dsn="postgresql://u:p@127.0.0.1:5432/db",
        path=tmp_path / ".engramory" / "config.toml",
    )

    storage = MagicMock()

    async def _fake_get_stream(**_kwargs: object) -> AsyncIterator[bytes]:
        yield b"blob-chunk"

    storage.get_stream = _fake_get_stream

    adapters = SimpleNamespace(
        vector=MagicMock(close=AsyncMock()),
        llm=MagicMock(),
        graph=MagicMock(close=AsyncMock()),
        events=MagicMock(close=AsyncMock(), publish=AsyncMock()),
        storage=storage,
    )

    hit = MemoryHit(
        memory_id="mem-1",
        summary="distilled insight",
        score=0.92,
        token_count=12,
        retrieval_id="ret-1",
        provenance={"source": "test"},
    )
    kb_sec = KBSection(
        id="sec-1",
        doc_id="doc-1",
        citation="§1",
        text="knowledge snippet",
        tenant_id="tenant-1",
        project_id="proj-1",
        scope="project",
        agent_id="agent-1",
    )
    prof = AgentProfile(
        agent_id="agent-1",
        display_name="Agent One",
        standing_preferences={"agent_type": "coder", "role": "coder"},
        tenant_id="tenant-1",
    )

    fake_surface = MagicMock()
    fake_surface.memory_add = AsyncMock(return_value="ep-1")
    fake_surface.memory_search = AsyncMock(return_value=[hit])
    fake_surface.memory_feedback = AsyncMock(return_value=None)
    fake_surface.memory_forget = AsyncMock(return_value=None)
    fake_surface.memory_promote = AsyncMock(
        return_value={
            "source_memory_id": "mem-1",
            "promoted_memory_id": "mem-2",
            "target_scope": "project",
        }
    )
    fake_surface.memory_ingest_event = AsyncMock(
        return_value={
            "event_id": "ev-1",
            "episode_id": "ep-2",
            "kind": "execution.completed",
            "tenant_id": "tenant-1",
            "project": "proj-1",
            "producer": "agent-1",
        }
    )
    fake_surface.memory_learning_health = AsyncMock(
        return_value={
            "exec_ready_calibration": {
                "paired_plans": 1,
                "mean_predicted_ready_ratio": 0.9,
                "actual_first_pass_rate": 1.0,
                "calibration_mae": 0.1,
            },
            "repeat_findings": {
                "total_findings": 2,
                "repeat_findings": 0,
                "repeat_findings_rate": 0.0,
            },
        }
    )
    fake_surface.agent_profile_get = AsyncMock(return_value=prof)
    fake_surface.agent_profile_upsert = AsyncMock(return_value=prof)
    fake_surface.agent_roster_list = AsyncMock(
        return_value=[{"agent_id": "agent-1", "active_skill_count": 1}]
    )
    fake_surface.agent_brain_export = AsyncMock(
        return_value={
            "schema_version": "engramory.brain_passport.v1",
            "agent_id": "agent-1",
            "skills_exported": 1,
            "memories": [],
        }
    )
    fake_surface.agent_brain_import = AsyncMock(
        return_value={"tenant_id": "tenant-1", "agent_id": "agent-1", "skills_imported": 1}
    )
    fake_surface.agent_brain_compare = AsyncMock(
        return_value={
            "winner": "v1",
            "variant_a": {"brain_variant": "v1"},
            "variant_b": {"brain_variant": "v2"},
        }
    )
    fake_surface.agent_space_offboard = AsyncMock(
        return_value={
            "tenant_id": "tenant-1",
            "agent_id": "agent-1",
            "retired_memories": 1,
            "purged_episodes": 2,
        }
    )
    fake_surface.knowledge_ingest = AsyncMock(return_value="sec-1")
    fake_surface.knowledge_ingest_batch = AsyncMock(return_value=["sec-1"])
    fake_surface.knowledge_ingest_files = AsyncMock(return_value={"doc.md": ["sec-1"]})
    fake_surface.knowledge_search = AsyncMock(return_value=[kb_sec])
    fake_surface.knowledge_sync_git = AsyncMock(
        return_value={
            "success": True,
            "sections_created": 1,
            "files_added": 1,
            "files_modified": 0,
            "files_skipped": 0,
            "duration_ms": 5,
        }
    )
    fake_surface.knowledge_ingest_media = AsyncMock(
        return_value={
            "path": "pic.png",
            "modality": "image",
            "section_ids": ["sec-1"],
            "blob_uri": "pgblob://tenant-1/abc",
        }
    )
    fake_surface.blob_head = AsyncMock(
        return_value={
            "status": "ready",
            "byte_size": 10,
            "storage_tier": "logged",
            "chunk_count": 1,
        }
    )
    fake_surface.context_slice = AsyncMock(
        return_value=[{"id": "SPEC-01", "properties": {"name": "Access"}}]
    )
    fake_surface.blast_radius = AsyncMock(
        return_value=[{"id": "IPLAN-01", "properties": {"name": "Plan"}}]
    )
    fake_surface.sdd_ingest = AsyncMock(
        return_value={
            "sdd_root": ".",
            "entities_ingested": 2,
            "relations_ingested": 1,
            "project_id": "proj-1",
            "tenant_id": "tenant-1",
        }
    )
    fake_surface.docs_slice_runbook = AsyncMock(
        return_value={
            "step_count": 1,
            "markdown": "# Runbook",
            "l0_steps": [{"step_id": "s1"}],
            "l2_memories": [],
        }
    )
    fake_surface.docs_ingest_runbook = AsyncMock(return_value={"doc_id": "DOC-1", "step_count": 1})
    fake_surface._decide = AsyncMock(return_value=None)

    fake_repo = MagicMock()
    fake_repo.ping = AsyncMock(return_value=True)
    fake_repo.count_episodes = AsyncMock(return_value=3)
    fake_repo.count_kb_sections = AsyncMock(return_value=5)
    fake_repo.count_graph_entities = AsyncMock(return_value=7)
    fake_repo.count_graph_relations = AsyncMock(return_value=9)
    fake_repo.close = AsyncMock(return_value=None)

    monkeypatch.setattr(cli_mod, "_load_config", lambda _p=None: cfg)
    monkeypatch.setattr(cli_mod, "adapters_for_profile", lambda **_kw: adapters)
    monkeypatch.setattr(cli_mod, "Repository", lambda *_a, **_kw: fake_repo)
    monkeypatch.setattr(cli_mod, "AccessSurface", lambda *_a, **_kw: fake_surface)
    monkeypatch.setattr(cli_mod, "reflect", AsyncMock(return_value=2))
    monkeypatch.setattr(cli_mod, "reproject_all", AsyncMock(return_value=(4, 3)))
    monkeypatch.setattr(cli_mod, "ingest_sdd_knowledge_graph", AsyncMock(return_value=(5, 4)))
    return SimpleNamespace(surface=fake_surface, repo=fake_repo, adapters=adapters)


def test_cli_subcommands_json_and_text_coverage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "run.md").write_text(
        "---\n"
        "id: DOC-RUN-1\n"
        "title: Runbook\n"
        "kind: runbook\n"
        "domain: core\n"
        "namespace: ns:platform\n"
        "status: active\n"
        "autonomy_tier: green\n"
        "watched_paths:\n"
        '  - "docs/**"\n'
        "last_verified: 2026-11-22\n"
        "---\n\n# Runbook\n\n## Procedure\n\nRun the verification step.\n\n"
        '```bash {id="s1" tier="green" mode="read-only" expect_exit=0 timeout_sec=5}\n'
        "echo ok\n"
        "```\n",
        encoding="utf-8",
    )
    passport_file = tmp_path / "passport.json"
    passport_file.write_text(
        json.dumps({"schema_version": "engramory.brain_passport.v1"}), encoding="utf-8"
    )
    ev_file = tmp_path / "event.json"
    ev_file.write_text(
        json.dumps(
            {
                "kind": "execution.completed",
                "occurred_at": "2026-11-22T12:00:00Z",
                "payload_type": "task.summary",
                "payload": {"ok": True},
                "summary": "ok",
            }
        ),
        encoding="utf-8",
    )
    _mock_cli_env(tmp_path, monkeypatch)

    commands = [
        [
            "--json",
            "memory",
            "add",
            "--content",
            "hello",
            "--scope",
            "project",
            "--specialization",
            "s",
        ],
        ["--json", "memory", "search", "--query", "hello", "--expand-graph", "--include-knowledge"],
        ["memory", "feedback", "--retrieval-id", "ret-1", "--outcome", "useful"],
        ["memory", "forget", "--memory-id", "mem-1", "--reason", "stale"],
        [
            "memory",
            "promote",
            "--memory-id",
            "mem-1",
            "--target-scope",
            "project",
            "--evidence-ref",
            "C1",
        ],
        ["memory", "distill", "--brain-variant", "v1", "--rag-params-json", '{"k": 3}'],
        ["--json", "memory", "ingest-event", "--event-file", str(ev_file), "--publish-nats"],
        ["--json", "memory", "learning-health"],
        ["profile", "get"],
        ["--json", "profile", "set", "--display-name", "A", "--pref", "tone=terse"],
        ["profile", "list"],
        ["profile", "export-brain", "--out", str(tmp_path / "out_passport.json")],
        ["profile", "import-brain", "--file", str(passport_file), "--evidence-ref", "CHG-1"],
        ["profile", "compare-brains", "--variant-a", "v1", "--variant-b", "v2"],
        ["profile", "offboard", "--evidence-ref", "CHG-1"],
        [
            "knowledge",
            "ingest",
            "--doc-id",
            "d1",
            "--citation",
            "§1",
            "--text",
            "hi",
            "--evidence-ref",
            "E1",
        ],
        ["knowledge", "ingest", "docs/run.md", "--evidence-ref", "E1"],
        ["knowledge", "search", "snippet", "--expand-graph"],
        ["knowledge", "sync-git", "--repo-path", "."],
        ["knowledge", "ingest-media", "docs/run.md", "--evidence-ref", "E1"],
        ["knowledge", "blob-stat", "a" * 64],
        ["knowledge", "blob-get", "a" * 64, "--out", "out.bin"],
        ["graph", "reproject"],
        ["sdd", "ingest", "--sdd-root", ".", "--evidence-ref", "CHG-1"],
        ["sdd", "slice", "--task", "IPLAN-01", "--markdown"],
        ["sdd", "blast-radius", "--doc", "SPEC-01"],
        ["docs", "lint", "."],
        ["docs", "tools"],
        ["docs", "build-portal", ".", "--dry-run"],
        ["docs", "export-llms", "."],
        ["docs", "slice-runbook", "--alert-id", "ALT-1"],
        ["docs", "smoke-run", ".", "--dry-run"],
        ["docs", "ingest", "docs/run.md"],
        ["status"],
    ]
    for cmd in commands:
        assert cli_main(cmd) == 0, f"CLI command failed: {cmd}"
    _ = capsys.readouterr()


def test_cli_main_error_exit_codes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ENGRAMORY_PROFILE", "dev")

    for exc, expected_rc in (
        (ConfigError("bad cfg"), 2),
        (AuthzError("denied"), 1),
        (GovernedWriteRejected("missing ref"), 1),
        (ValidationError("bad field"), 1),
        (KeyError("missing"), 1),
        (StoreUnavailable("db down"), 3),
    ):
        with patch.object(cli_mod, "_load_config", side_effect=exc):
            assert cli_main(["status"]) == expected_rc


@pytest.mark.asyncio
async def test_access_surface_branches_and_fallbacks(tmp_path: Path) -> None:
    repo = MagicMock()
    repo.record_audit = AsyncMock(return_value=None)
    repo.find_ancestors = AsyncMock(return_value=[{"id": "ADR-01", "properties": {}}])
    repo.find_descendants = AsyncMock(return_value=[{"id": "IPLAN-01", "properties": {}}])
    repo.upsert_kb_document = AsyncMock(return_value="DOC-1")
    repo.upsert_kb_runbook_steps = AsyncMock(return_value=1)
    repo.get_memory = AsyncMock(
        return_value=Memory(
            content_raw="raw",
            summary="sum",
            type="semantic",
            embedding_model="embed",
            provenance={"source": "t"},
            agent_id="agent-1",
            project_id="proj-1",
            tenant_id="tenant-1",
            scope="agent",
            id="mem-1",
        )
    )
    repo.find_existing_promoted_memory = AsyncMock(return_value=None)
    repo.upsert_memory = AsyncMock(return_value="mem-2")
    repo.get_agent_profile = AsyncMock(
        return_value=AgentProfile(
            agent_id="agent-1",
            tenant_id="tenant-1",
            standing_preferences={"agent_type": "coder"},
        )
    )

    graph = MagicMock()
    graph.ancestors = AsyncMock(side_effect=RuntimeError("neo4j offline"))
    graph.descendants = AsyncMock(side_effect=RuntimeError("neo4j offline"))

    storage = MagicMock()
    blob = KBBlob(
        tenant_id="tenant-1",
        sha256="a" * 64,
        media_type="text/plain",
        byte_size=5,
        chunk_size=65536,
        chunk_count=1,
    )
    storage.put = AsyncMock(return_value=blob)
    storage.head = AsyncMock(return_value=blob)
    storage.get = AsyncMock(return_value=b"hello")

    surface = AccessSurface(repo, graph=graph, storage=storage)

    # 1. Context slice & blast radius Neo4j -> PG fallback
    anc = await surface.context_slice(_CTX, task_id="IPLAN-01")
    assert anc[0]["id"] == "ADR-01"
    desc = await surface.blast_radius(_CTX, doc_id="ADR-01")
    assert desc[0]["id"] == "IPLAN-01"

    # 2. Governed write rejections on missing evidence_ref
    with pytest.raises(GovernedWriteRejected, match="evidence_ref"):
        await surface.memory_promote(
            _CTX, memory_id="mem-1", target_scope="project", evidence_ref=""
        )
    with pytest.raises(GovernedWriteRejected, match="evidence_ref"):
        await surface.agent_brain_import(_CTX, passport={}, evidence_ref="")
    with pytest.raises(GovernedWriteRejected, match="evidence_ref"):
        await surface.agent_space_offboard(_CTX, evidence_ref="")
    with pytest.raises(GovernedWriteRejected, match="evidence_ref"):
        await surface.knowledge_ingest(_CTX, doc_id="d", citation="c", text="t", evidence_ref="")
    with pytest.raises(GovernedWriteRejected, match="evidence_ref"):
        await surface.knowledge_ingest_batch(_CTX, sections=[], evidence_ref="")
    with pytest.raises(GovernedWriteRejected, match="evidence_ref"):
        await surface.knowledge_ingest_files(_CTX, paths=[], evidence_ref="")
    with pytest.raises(GovernedWriteRejected, match="evidence_ref"):
        await surface.sdd_ingest(_CTX, sdd_root=tmp_path, evidence_ref="")
    with pytest.raises(GovernedWriteRejected, match="evidence_ref"):
        await surface.knowledge_ingest_media(_CTX, file_path=tmp_path / "x.png", evidence_ref="")
    kb_doc = KBDocument(doc_id="DOC-1", repo_path="docs/r.md", title="R", kind="runbook")
    with pytest.raises(GovernedWriteRejected, match="evidence_ref"):
        await surface.docs_ingest_runbook(_CTX, doc=kb_doc, steps=(), evidence_ref="")

    # 3. agent_brain_import security checks
    with pytest.raises(GovernedWriteRejected, match="schema_version"):
        await surface.agent_brain_import(
            _CTX, passport={"schema_version": "bad"}, evidence_ref="CHG-1"
        )
    with pytest.raises(GovernedWriteRejected, match="verify_integrity"):
        await surface.agent_brain_import(
            _CTX,
            passport={"schema_version": "engramory.brain_passport.v1"},
            evidence_ref="CHG-1",
            verify_integrity=False,
        )
    with pytest.raises(GovernedWriteRejected, match="integrity"):
        await surface.agent_brain_import(
            _CTX,
            passport={"schema_version": "engramory.brain_passport.v1", "passport_sha256": "dead"},
            evidence_ref="CHG-1",
        )
    from engramory.access.surface import _compute_passport_sha256

    mismatch_passport = {
        "schema_version": "engramory.brain_passport.v1",
        "agent_type": "reviewer",
        "standing_preferences": {"agent_type": "reviewer"},
        "memories": [],
    }
    mismatch_passport["passport_sha256"] = _compute_passport_sha256(mismatch_passport)
    with pytest.raises(GovernedWriteRejected, match="agent_type_mismatch"):
        await surface.agent_brain_import(_CTX, passport=mismatch_passport, evidence_ref="CHG-1")
    with pytest.raises(GovernedWriteRejected, match="space scope"):
        await surface.agent_brain_import(
            _CTX,
            passport=mismatch_passport,
            evidence_ref="CHG-1",
            allow_type_override=True,
        )

    # 4. Storage operations
    put_res = await surface.blob_put(_CTX, key="k1", data=b"hello")
    assert put_res["sha256"] == "a" * 64
    head_res = await surface.blob_head(_CTX, sha256="a" * 64)
    assert head_res is not None
    assert await surface.blob_get(_CTX, sha256="a" * 64) == b"hello"
    doc_out = await surface.docs_ingest_runbook(_CTX, doc=kb_doc, steps=(), evidence_ref="CHG-1")
    assert doc_out["doc_id"] == "DOC-1"


@pytest.mark.asyncio
async def test_knowledge_memory_nats_litellm_jwt_and_mcp_coverage(tmp_path: Path) -> None:
    # 1. KnowledgeCore + MemoryCore
    doc_file = tmp_path / "guide.md"
    doc_file.write_text("# Title\n\nClean technical guide section content.\n", encoding="utf-8")
    png_file = tmp_path / "img.png"
    png_file.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 32)

    repo = MagicMock()
    repo.upsert_kb_sections_batch = AsyncMock(
        return_value={("tenant-1", "proj-1:guide.md", "§1", 1): "sec-1"}
    )
    repo.set_kb_sections_embedding_batch = AsyncMock(return_value=None)
    repo.bulk_upsert_graph_entities = AsyncMock(return_value=1)
    repo.bulk_upsert_graph_relations = AsyncMock(return_value=1)
    repo.search_kb_sections = AsyncMock(return_value=[])
    repo.get_kb_section = AsyncMock(return_value=None)
    repo.list_kb_sections = AsyncMock(return_value=[])
    repo.add_episode = AsyncMock(return_value="ep-1")
    repo.get_memories = AsyncMock(return_value=[])
    repo.log_retrievals = AsyncMock(return_value=[])

    llm = MagicMock()
    llm.embed = AsyncMock(side_effect=lambda texts: [[0.1] * 8 for _ in texts])
    llm.embed_model = "embed-x"
    storage = MagicMock()
    storage.put_file = AsyncMock(
        return_value=KBBlob(
            tenant_id="tenant-1",
            sha256=hashlib.sha256(png_file.read_bytes()).hexdigest(),
            media_type="image/png",
            byte_size=40,
        )
    )

    kcore = KnowledgeCore(repo, llm=llm, storage=storage, expected_embedding_dims=8)
    ids = await kcore.ingest_file(
        doc_file, tenant_id="tenant-1", project_id="proj-1", repo_root=tmp_path
    )
    assert isinstance(ids, list)
    with pytest.raises(ValidationError):
        bad_file = tmp_path / "note.exe"
        bad_file.write_bytes(b"MZ")
        await kcore.ingest_media_file(bad_file, repo_root=tmp_path)
    assert await kcore.search_sections("query", tenant_id="tenant-1") == []
    assert await kcore.get_section("sec-1", tenant_id="tenant-1") is None
    assert await kcore.list_sections(tenant_id="tenant-1") == []

    mcore = MemoryCore(repo, llm=llm, default_tenant_id="tenant-1")
    assert await mcore.add_episode(agent_id="a1", content="safe note") == "ep-1"
    assert await mcore.search(query="safe", agent_id="a1", scope="project") == []

    # 2. NatsEventsAdapter
    assert _topic_matches("engramory.events.*", "xp.engramory.experience.task")
    with pytest.raises(ValueError):
        _validate_topic("", "publish")
    with pytest.raises(ValueError):
        _validate_header_value("X-Bad", "val\r\n")
    assert _decode_nats_msg(SimpleNamespace(data=b"not-json")) is None

    buf_nats = NatsEventsAdapter(use_memory_buffer=True)
    await buf_nats.publish(topic="engramory.events.task", event={"event_id": "e1", "ok": True})
    drained = [ev async for ev in buf_nats.subscribe(topic="engramory.events.>")]
    assert len(drained) == 1 and drained[0]["ok"] is True

    ack_mock = MagicMock()
    ack_mock.ack = AsyncMock(return_value=None)
    ack_mock.data = json.dumps({"event_id": "e2"}).encode()
    ack_mock.subject = "engramory.events.done"
    pull_sub = MagicMock()
    pull_sub.fetch = AsyncMock(return_value=[ack_mock])
    js_mock = MagicMock()
    js_mock.publish = AsyncMock(return_value="puback")
    js_mock.pull_subscribe = AsyncMock(return_value=pull_sub)
    nats_client = MagicMock(is_closed=False, close=AsyncMock())
    nats_client.jetstream = MagicMock(return_value=js_mock)
    live_nats = NatsEventsAdapter(client=nats_client)
    await live_nats.publish(
        topic="engramory.events.done",
        event={"event_id": "e2", "trace_id": "0" * 32},
    )
    js_msgs = [ev async for ev in live_nats.subscribe(topic="engramory.events.done")]
    assert len(js_msgs) == 1
    await live_nats.close()

    # 3. LiteLLMAdapter chat/describe_image/transcribe_audio + size guards
    litellm = LiteLLMAdapter(base_url="http://127.0.0.1:4000")
    resp_ok = MagicMock()
    resp_ok.__enter__ = MagicMock(return_value=resp_ok)
    resp_ok.__exit__ = MagicMock(return_value=False)
    resp_ok.read.return_value = json.dumps(
        {"choices": [{"message": {"content": "described"}}], "text": "hello", "segments": []}
    ).encode()
    with patch("urllib.request.urlopen", return_value=resp_ok):
        assert await litellm.describe_image(image_bytes=b"png") == "described"
        tr = await litellm.transcribe_audio(audio_bytes=b"wav")
        assert tr["text"] == "hello"
    with pytest.raises(ValueError, match="MAX_VISION_IMAGE_BYTES"):
        await litellm.describe_image(image_bytes=b"x" * (MAX_VISION_IMAGE_BYTES + 1))
    with pytest.raises(ValueError, match="MAX_AUDIO_BYTES"):
        await litellm.transcribe_audio(audio_bytes=b"x" * (MAX_AUDIO_BYTES + 1))

    # 4. JWT helper coverage
    assert validate_security_gate_url("unix:///run/gate.sock")[1] == "/run/gate.sock"
    with pytest.raises(ValueError):
        validate_security_gate_url("ftp://gate.local")
    with pytest.raises(ValueError):
        validate_security_gate_url("https://user:pass@gate.example.com")
    claims = TokenClaims(
        sub="s1",
        iss="iss",
        aud="aud",
        exp=9999999999,
        raw_claims={"groups": ["ns:platform", "role:coder", "domain:sec"], "projects": ["p1"]},
    )
    assert claims.agent_role == "coder"
    ctx_mapped = claims_to_actor_context(claims)
    assert ctx_mapped.domain_id == "sec"
    with pytest.raises(AuthenticationError):
        claims_to_actor_context(TokenClaims(sub="", iss="i", aud="a", exp=1, raw_claims={}))
    check_token_revocation(claims, revoked_identifiers=None)

    # 5. MCP tool dispatch + /health & /ready endpoints
    mock_surface = MagicMock(spec=AccessSurface)
    mock_surface._repo = repo
    mock_surface._decide = AsyncMock(return_value=None)
    mock_surface.agent_profile_get = AsyncMock(
        return_value=AgentProfile(agent_id="agent-1", tenant_id="tenant-1")
    )
    mock_surface.memory_feedback = AsyncMock(return_value=None)
    mock_surface.memory_forget = AsyncMock(return_value=None)
    mock_surface.blob_put = AsyncMock(return_value={"sha256": "a" * 64})
    mock_surface.blob_get = AsyncMock(return_value=b"data")
    server = McpServer(mock_surface, _CTX)
    assert not (await server.handle_call_tool("agent_profile_get", {})).get("isError")
    assert not (
        await server.handle_call_tool(
            "memory_feedback", {"retrieval_id": "r1", "outcome": "useful"}
        )
    ).get("isError")
    assert not (
        await server.handle_call_tool("memory_forget", {"memory_id": "m1", "reason": "old"})
    ).get("isError")
    assert not (
        await server.handle_call_tool(
            "blob_put", {"data_base64": base64.b64encode(b"abc").decode()}
        )
    ).get("isError")
    assert not (await server.handle_call_tool("blob_get", {"sha256": "a" * 64})).get("isError")
    assert (await server.handle_call_tool("unknown_tool", {})).get("isError") is True
    assert not (await server.handle_call_tool("docs_tools_inventory", {})).get("isError")
    assert not (await server.handle_call_tool("sdd_lint_policy", {"root": str(tmp_path)})).get(
        "isError"
    )

    app = server.create_http_app()
    client = TestClient(app)
    assert client.get("/").status_code == 200
    assert client.get("/health").status_code == 200


@pytest.mark.asyncio
async def test_knowledge_core_multimodal_batch_and_runbook_coverage(tmp_path: Path) -> None:
    sub = tmp_path / "corpus"
    sub.mkdir()
    outside = tmp_path / "outside.md"
    outside.write_text("# Outside\n\nOutside root content.\n", encoding="utf-8")

    png1 = sub / "arch.png"
    png1_bytes = b"\x89PNG\r\n\x1a\n" + struct.pack(">I4sII", 13, b"IHDR", 10, 10) + b"\x00" * 16
    png1.write_bytes(png1_bytes)
    png2 = sub / "fallback.png"
    png2.write_bytes(png1_bytes)
    mp4 = sub / "clip.mp4"
    mp4.write_bytes(b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00isomiso2" + b"\x00" * 32)

    runbook = sub / "ops_runbook.md"
    runbook.write_text(
        "---\n"
        "id: DOC-OPS-1\n"
        "title: Ops Runbook\n"
        "kind: runbook\n"
        "domain: core\n"
        "namespace: ns:platform\n"
        "status: active\n"
        "autonomy_tier: green\n"
        "watched_paths:\n"
        '  - "docs/**"\n'
        "last_verified: 2026-11-22\n"
        "---\n\n# Ops Runbook\n\n## Probe\n\nVerify probe.\n\n"
        '```bash {id="p1" tier="green" mode="read-only" expect_exit=0 timeout_sec=5}\n'
        "echo ok\n"
        "```\n",
        encoding="utf-8",
    )
    symlink_doc = sub / "link.md"
    symlink_doc.symlink_to(outside)

    repo = MagicMock()
    repo.upsert_kb_document = AsyncMock(return_value="DOC-OPS-1")
    repo.upsert_kb_runbook_steps = AsyncMock(return_value=1)

    async def _batch_upsert(secs: list[KBSection]) -> dict[tuple[str, str, str, int], str]:
        return {
            (s.tenant_id, s.doc_id, s.citation, s.version): f"sec-{i}" for i, s in enumerate(secs)
        }

    repo.upsert_kb_sections_batch = AsyncMock(side_effect=_batch_upsert)
    repo.set_kb_sections_embedding_batch = AsyncMock(return_value=None)
    repo.bulk_upsert_graph_entities = AsyncMock(return_value=1)
    repo.bulk_upsert_graph_relations = AsyncMock(return_value=1)
    repo.get_kb_section = AsyncMock(
        return_value=KBSection(
            id="sec-0",
            doc_id="proj-1:arch.png",
            citation="#image",
            text="arch diagram",
            tenant_id="tenant-1",
            project_id="proj-1",
            modality="image",
            blob_sha256="a" * 64,
            blob_uri="pgblob://tenant-1/" + "a" * 64,
        )
    )
    repo.search_kb_sections = AsyncMock(return_value=[])

    call_count = 0

    async def _describe_img(**_kw: object) -> str:
        nonlocal call_count
        call_count += 1
        if call_count == 2:
            raise RuntimeError("vision offline")
        return "architecture diagram"

    llm = MagicMock()
    llm.describe_image = AsyncMock(side_effect=_describe_img)
    llm.transcribe_audio = AsyncMock(return_value={"text": "spoken scene"})
    llm.embed = AsyncMock(side_effect=lambda texts: [[0.1] * 8 for _ in texts])
    llm.embed_model = "embed-x"

    storage = MagicMock()
    storage.put_file = AsyncMock(
        return_value=KBBlob(
            tenant_id="tenant-1",
            sha256="a" * 64,
            media_type="image/png",
            byte_size=len(png1_bytes),
        )
    )
    graph = MagicMock()
    graph.upsert_node = AsyncMock(return_value=None)
    graph.upsert_edge = AsyncMock(return_value=None)

    kcore = KnowledgeCore(repo, llm=llm, storage=storage, graph=graph, expected_embedding_dims=8)

    with pytest.raises(FileNotFoundError):
        await kcore.ingest_file(sub / "missing.md", tenant_id="tenant-1")
    with pytest.raises(FileNotFoundError):
        await kcore.ingest_media_file(sub / "missing.png", tenant_id="tenant-1")

    media_out = await kcore.ingest_media_file(
        png1, tenant_id="tenant-1", project_id="proj-1", repo_root=tmp_path
    )
    assert media_out["modality"] == "image"
    assert media_out["blob_uri"] == "pgblob://tenant-1/" + "a" * 64

    va_ext = kcore.router.video_audio_extractor
    with (
        patch.object(va_ext, "probe_media", return_value={"duration_seconds": 65}),
        patch.object(va_ext, "extract_keyframe_png", return_value=png1_bytes),
        patch.object(va_ext, "extract_audio_window_wav", return_value=b"RIFF\x24\x00\x00\x00WAVE"),
    ):
        batch_res = await kcore.ingest_documents_batch(
            [sub / "absent.md", symlink_doc, png2, mp4, runbook],
            tenant_id="tenant-1",
            project_id="proj-1",
            ingest_runbooks=True,
            project_graph=True,
        )
    assert str(png2) in batch_res
    assert str(mp4) in batch_res
    assert str(runbook) in batch_res
    repo.upsert_kb_document.assert_awaited()
    repo.upsert_kb_runbook_steps.assert_awaited()
    graph.upsert_node.assert_awaited()
    graph.upsert_edge.assert_awaited()

    # Out-of-root skip when repo_root is specified
    out_res = await kcore.ingest_documents_batch([outside], tenant_id="tenant-1", repo_root=sub)
    assert out_res == {}

    # search_sections modality + embed error fallback
    llm.embed = AsyncMock(side_effect=RuntimeError("embed down"))
    assert await kcore.search_sections("arch", tenant_id="tenant-1", modality="image") == []


@pytest.mark.asyncio
async def test_mcp_server_full_tool_dispatch_and_helpers_coverage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "docs").mkdir()
    runbook = tmp_path / "docs" / "run.md"
    runbook.write_text(
        "---\n"
        "id: DOC-RUN-1\n"
        "title: Runbook\n"
        "kind: runbook\n"
        "domain: core\n"
        "namespace: ns:platform\n"
        "status: active\n"
        "autonomy_tier: green\n"
        "watched_paths:\n"
        '  - "docs/**"\n'
        "last_verified: 2026-11-22\n"
        "---\n\n# Runbook\n\n## Check\n\nVerify status.\n\n"
        '```bash {id="s1" tier="green" mode="read-only" expect_exit=0 timeout_sec=5}\n'
        "echo ok\n"
        "```\n",
        encoding="utf-8",
    )

    assert _coerce_int(2.0, 5, name="k") == 2
    with pytest.raises(ValidationError):
        _coerce_int(True, 5, name="k")
    with pytest.raises(ValidationError):
        _coerce_int(2.5, 5, name="k")
    with pytest.raises(ValidationError):
        _coerce_int("not-int", 5, name="k")
    with pytest.raises(ValidationError):
        _coerce_int(["bad"], 5, name="k")

    assert "No upstream" in format_context_slice_markdown("IPLAN-1", [])
    md = format_context_slice_markdown(
        "IPLAN-1",
        [
            {
                "id": "SPEC-01",
                "properties": {
                    "name": "Access",
                    "path": "/tmp/SPEC-01.yaml",
                    "domain_id": "core",
                    "status": "Approved",
                    "summary": "Access surface contract",
                },
            }
        ],
    )
    assert "SPEC-01: Access" in md and "file:///tmp/SPEC-01.yaml" in md
    assert (
        len(
            _format_context_slice_digest(
                "IPLAN-1", [{"id": "S", "properties": {"summary": "x" * 500}}], 20
            )
        )
        <= 76
    )
    assert "END OF DIGEST" in _format_context_slice_digest(
        "IPLAN-1", [{"id": "S", "properties": {}}], 200
    )

    hit = MemoryHit(
        memory_id="mem-1",
        summary="distilled insight",
        score=0.95,
        token_count=10,
        retrieval_id="ret-1",
        provenance={"source": "test"},
    )
    kb_sec = KBSection(
        id="sec-1",
        doc_id="doc-1",
        citation="§1",
        text="knowledge snippet",
        tenant_id="tenant-1",
        project_id="proj-1",
        scope="project",
        agent_id="agent-1",
    )
    prof = AgentProfile(
        agent_id="agent-1",
        display_name="Agent One",
        standing_preferences={"agent_type": "coder", "role": "coder"},
        tenant_id="tenant-1",
    )

    mock_surface = MagicMock(spec=AccessSurface)
    mock_surface._repo = MagicMock(record_audit=AsyncMock(return_value=None))
    mock_surface._decide = AsyncMock(return_value=None)
    mock_surface.memory_search = AsyncMock(return_value=[hit])
    mock_surface.memory_add = AsyncMock(return_value="ep-1")
    mock_surface.memory_promote = AsyncMock(return_value={"promoted_memory_id": "mem-2"})
    mock_surface.memory_ingest_event = AsyncMock(return_value={"episode_id": "ep-2"})
    mock_surface.memory_learning_health = AsyncMock(return_value={"episodes_analyzed": 1})
    mock_surface.agent_profile_upsert = AsyncMock(return_value=prof)
    mock_surface.agent_roster_list = AsyncMock(return_value=[{"agent_id": "agent-1"}])
    mock_surface.agent_brain_export = AsyncMock(
        return_value={"schema_version": "engramory.brain_passport.v1"}
    )
    mock_surface.agent_brain_import = AsyncMock(return_value={"skills_imported": 1})
    mock_surface.agent_brain_compare = AsyncMock(return_value={"winner": "v1"})
    mock_surface.agent_space_offboard = AsyncMock(return_value={"retired_memories": 1})
    mock_surface.knowledge_ingest = AsyncMock(return_value="sec-1")
    mock_surface.knowledge_search = AsyncMock(return_value=[kb_sec])
    mock_surface.knowledge_ingest_media = AsyncMock(return_value={"section_ids": ["sec-1"]})
    mock_surface.blob_head = AsyncMock(return_value={"status": "ready"})
    mock_surface.blob_put = AsyncMock(return_value={"sha256": "a" * 64})
    mock_surface.blob_get = AsyncMock(return_value=b"data")
    mock_surface.graph_neighbors = AsyncMock(return_value=[{"id": "ADR-01"}])
    mock_surface.context_slice = AsyncMock(
        return_value=[{"id": "SPEC-01", "properties": {"name": "Access"}}]
    )
    mock_surface.blast_radius = AsyncMock(
        return_value=[{"id": "IPLAN-01", "properties": {"name": "Plan"}}]
    )
    mock_surface.docs_ingest_runbook = AsyncMock(
        return_value={"doc_id": "DOC-RUN-1", "step_count": 1}
    )
    mock_surface.docs_slice_runbook = AsyncMock(
        return_value={"l0_steps": [], "l2_memories": [], "markdown": "# Slice"}
    )

    server = McpServer(mock_surface, _CTX, ingest_fn=None)

    # JSON-RPC handle_request branches
    bad_req: Any = "bad"
    bad_resp: Any = await server.handle_request(bad_req)
    assert bad_resp is not None and bad_resp["error"]["code"] == -32600
    assert (
        await server.handle_request({"jsonrpc": "2.0", "method": "notifications/initialized"})
        is None
    )
    ping_resp: Any = await server.handle_request({"jsonrpc": "2.0", "id": 1, "method": "ping"})
    assert ping_resp is not None and ping_resp["result"] == {}
    list_resp: Any = await server.handle_request(
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}
    )
    assert list_resp is not None and "tools" in list_resp["result"]
    bad_params_resp: Any = await server.handle_request(
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": []}
    )
    assert bad_params_resp is not None and bad_params_resp["error"]["code"] == -32602
    empty_name_resp: Any = await server.handle_request(
        {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": ""}}
    )
    assert empty_name_resp is not None and empty_name_resp["error"]["code"] == -32602
    bad_args_resp: Any = await server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 5,
            "method": "tools/call",
            "params": {"name": "ping", "arguments": []},
        }
    )
    assert bad_args_resp is not None and bad_args_resp["error"]["code"] == -32602
    missing_resp: Any = await server.handle_request(
        {"jsonrpc": "2.0", "id": 6, "method": "no_such_method"}
    )
    assert missing_resp is not None and missing_resp["error"]["code"] == -32601

    calls: list[tuple[str, dict[str, object]]] = [
        (
            "memory_search",
            {
                "query": "hello",
                "k": 3,
                "include_knowledge": True,
                "expand_graph": True,
                "domain_id": "core",
                "brain_variant": "v1",
                "specialization": "sec",
            },
        ),
        (
            "memory_add",
            {
                "content": "obs",
                "scope": "project",
                "domain_id": "core",
                "specializations": ["sec"],
                "brain_variant": "v1",
            },
        ),
        (
            "memory_promote",
            {
                "memory_id": "mem-1",
                "target_scope": "project",
                "evidence_ref": "CHG-1",
                "specializations": ["sec"],
            },
        ),
        ("memory_ingest_event", {"event": {"kind": "execution.completed"}}),
        ("memory_ingest_event", {"kind": "execution.completed", "scope": "project"}),
        ("memory_learning_health", {"project_id": "proj-1", "limit": 50}),
        (
            "agent_profile_upsert",
            {
                "display_name": "Agent One",
                "agent_type": "coder",
                "role": "coder",
                "domain_id": "core",
                "specializations": ["sec"],
                "brain_config": {"active_brain_variant": "v1"},
                "standing_preferences": {"tone": "concise"},
            },
        ),
        ("agent_roster_list", {"domain_id": "core", "include_stats": True}),
        (
            "agent_brain_export",
            {"brain_variant": "v1", "specialization": "sec", "redact_terms": ["secret"]},
        ),
        (
            "agent_brain_import",
            {
                "passport": {"schema_version": "engramory.brain_passport.v1"},
                "evidence_ref": "CHG-1",
                "brain_variant": "v1",
                "redact_terms": ["secret"],
            },
        ),
        ("agent_brain_compare", {"variant_a": "v1", "variant_b": "v2", "query": "hello"}),
        ("agent_space_offboard", {"evidence_ref": "CHG-1"}),
        (
            "knowledge_ingest",
            {"doc_id": "DOC-1", "citation": "§1", "text": "body", "evidence_ref": "CHG-1"},
        ),
        (
            "knowledge_search",
            {"query": "snippet", "modality": "text", "expand_graph": True, "graph_depth": 2},
        ),
        ("knowledge_ingest_media", {"file_path": "docs/run.md", "evidence_ref": "CHG-1"}),
        ("blob_head", {"sha256": "a" * 64}),
        ("graph_neighbors", {"id": "ADR-01", "depth": 2}),
        ("sdd_context_slice", {"task_id": "IPLAN-01", "format": "markdown"}),
        ("sdd_context_slice", {"task_id": "IPLAN-01", "format": "digest"}),
        ("sdd_context_slice", {"task_id": "IPLAN-01", "offline": True, "workspace_dir": "."}),
        (
            "sdd_context_slice",
            {"task_id": "IPLAN-01", "offline": True, "workspace_dir": ".", "format": "digest"},
        ),
        ("sdd_blast_radius", {"doc_id": "SPEC-01"}),
        ("sdd_blast_radius", {"doc_id": "SPEC-01", "offline": True, "workspace_dir": "."}),
        ("docs_ingest_runbook", {"path": "docs/run.md", "evidence_ref": "CHG-1"}),
        ("docs_slice_runbook", {"doc_id": "DOC-RUN-1", "format": "markdown"}),
        ("docs_slice_runbook", {"doc_id": "DOC-RUN-1", "repo_path": "."}),
        ("docs_validate", {"path": "."}),
        ("docs_export_llms", {"repo_path": ".", "write": False}),
        ("docs_smoke_run", {"repo_path": ".", "dry_run": True}),
        ("docs_build_portal", {"repo_path": ".", "dry_run": True}),
        ("sdd_validate_graph", {"workspace_dir": ".", "invariants": True}),
    ]
    for tool_name, args in calls:
        res = await server.handle_call_tool(tool_name, args)
        assert not res.get("isError"), f"{tool_name} failed with {res}"

    with patch("engramory.core.docs_tools.check_repo_drift", return_value={"status": "ok"}):
        assert not (await server.handle_call_tool("docs_check_drift", {"repo_path": "."})).get(
            "isError"
        )
    with patch(
        "engramory.core.context_tools.check_sdd_invariants",
        return_value={"passed": True, "status": "ok"},
    ):
        assert not (
            await server.handle_call_tool("sdd_check_invariants", {"workspace_dir": "."})
        ).get("isError")

    # Error branches for blob_put, blob_get, sdd_ingest, docs_ingest_runbook
    assert (
        await server.handle_call_tool("blob_put", {"file_path": "a", "data_base64": "YQ=="})
    ).get("isError")
    assert (await server.handle_call_tool("blob_put", {})).get("isError")
    assert (await server.handle_call_tool("blob_put", {"data_base64": 123})).get("isError")
    assert (await server.handle_call_tool("blob_put", {"data_base64": "not*base64!"})).get(
        "isError"
    )
    assert (
        await server.handle_call_tool("blob_put", {"data_base64": "YQ==", "metadata": "bad"})
    ).get("isError")
    assert (await server.handle_call_tool("blob_get", {"sha256": "a" * 64, "max_bytes": 0})).get(
        "isError"
    )
    assert (
        await server.handle_call_tool(
            "docs_ingest_runbook", {"path": "docs/run.md", "evidence_ref": ""}
        )
    ).get("isError")
    assert (await server.handle_call_tool("sdd_ingest", {"evidence_ref": "CHG-1"})).get("isError")

    server_with_ingest = McpServer(mock_surface, _CTX, ingest_fn=AsyncMock(return_value=(3, 2)))
    assert not (
        await server_with_ingest.handle_call_tool(
            "sdd_ingest", {"sdd_root": ".", "evidence_ref": "CHG-1"}
        )
    ).get("isError")


@pytest.mark.asyncio
async def test_html_parser_and_access_surface_happy_paths_coverage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    html = (
        "<html><head><title>Guide</title><style>body{}</style></head>"
        "<body><script>alert(1)</script>"
        "<h1>Overview</h1><p>Intro paragraph.</p>"
        "<h2>Details</h2><ul><li>Item A</li><li>Item B</li></ul>"
        "<table><thead><tr><th>Col</th></tr></thead><tbody><tr><td>Val</td></tr></tbody></table>"
        "<h3>Code</h3><pre><code>echo ok</code></pre>"
        "</body></html>"
    )
    parser = HTMLParser()
    res = parser.parse(html.encode("utf-8"), filename="guide.html")
    assert res.title == "Guide"
    assert len(res.sections) == 3
    assert parser.parse("<html><body>Plain text only</body></html>").sections[0].name == "Content"
    assert parser.parse("<html><body><script>x</script></body></html>").sections == []
    assert parser._parse_with_stdlib(html, "guide.html").title == "Guide"
    assert parser._parse_with_stdlib("<p>Only text</p>", None).sections[0].name == "Content"
    assert parser._parse_with_stdlib("<script>skip</script>", None).sections == []

    # AccessSurface happy paths
    mem = Memory(
        id="mem-1",
        content_raw="raw skill text",
        summary="distilled skill summary",
        type="semantic",
        embedding_model="embed-x",
        embedding_dims=1536,
        provenance={"brain_variant": "v1", "specializations": ["sec"]},
        confidence=0.9,
        agent_id="agent-1",
        project_id="proj-1",
        domain_id="core",
        tenant_id="tenant-1",
        scope="agent",
    )
    prof = AgentProfile(
        agent_id="agent-1",
        display_name="Agent One",
        standing_preferences={"agent_type": "coder", "role": "coder", "department_focus": "core"},
        tenant_id="tenant-1",
    )

    repo = MagicMock()
    repo.record_audit = AsyncMock(return_value=None)
    repo.get_agent_profile = AsyncMock(return_value=prof)
    repo.upsert_agent_profile = AsyncMock(return_value=None)
    repo.list_agent_profiles = AsyncMock(
        return_value=[{"agent_id": "agent-1", "active_skill_count": 1}]
    )
    repo.get_memories = AsyncMock(return_value=[mem])
    repo.offboard_agent_space = AsyncMock(
        return_value={"retired_memories": 1, "purged_episodes": 2, "deleted_profiles": 1}
    )
    repo.get_memory = AsyncMock(return_value=mem)
    repo.find_existing_promoted_memory = AsyncMock(return_value=None)
    repo.upsert_memory = AsyncMock(return_value="mem-promoted")

    llm = MagicMock()
    llm.embed = AsyncMock(return_value=[[0.1] * 1536])
    llm.embed_model = "embed-x"
    surface = AccessSurface(repo, llm=llm)

    up = await surface.agent_profile_upsert(
        _CTX,
        display_name="Agent One",
        agent_type="coder",
        role="coder",
        domain_id="core",
        specializations=["sec"],
        brain_config={"active_brain_variant": "v1"},
        standing_preferences={"tone": "terse"},
    )
    assert up.agent_id == "agent-1"
    assert len(await surface.agent_roster_list(_CTX, domain_id="core")) == 1

    passport = await surface.agent_brain_export(_CTX, domain_id="core", brain_variant="v1")
    assert passport["skills_exported"] == 1
    assert passport["passport_sha256"] == _compute_passport_sha256(passport)

    off = await surface.agent_space_offboard(_CTX, evidence_ref="CHG-1")
    assert off["retired_memories"] == 1

    prom = await surface.memory_promote(
        _CTX,
        memory_id="mem-1",
        target_scope="project",
        evidence_ref="CHG-1",
        specializations=["ops"],
    )
    assert prom["promoted_memory_id"] == "mem-promoted" and prom["idempotent_replay"] is False

    repo.find_existing_promoted_memory = AsyncMock(
        return_value=Memory(
            id="mem-promoted",
            content_raw="raw",
            summary="sum",
            type="semantic",
            embedding_model="embed-x",
            provenance={"source": "test"},
            agent_id="agent-1",
            project_id="proj-1",
            domain_id="core",
            tenant_id="tenant-1",
            scope="project",
        )
    )
    prom_replay = await surface.memory_promote(
        _CTX, memory_id="mem-1", target_scope="project", evidence_ref="CHG-1"
    )
    assert prom_replay["idempotent_replay"] is True

    # Additional AccessSurface happy paths (knowledge batch, GraphRAG, files/media/git/sdd)
    doc_md = tmp_path / "guide.md"
    doc_md.write_text("# Guide\n\nDetailed runbook procedure.\n", encoding="utf-8")
    png_file = tmp_path / "diagram.png"
    png_bytes = b"\x89PNG\r\n\x1a\n" + struct.pack(">I4sII", 13, b"IHDR", 10, 10) + b"\x00" * 16
    png_file.write_bytes(png_bytes)
    sdd_dir = tmp_path / "sdd"
    (sdd_dir / "05_ADR").mkdir(parents=True)

    kb_sec = KBSection(
        id="sec-1",
        doc_id="DOC-1",
        citation="§1#intro",
        text="Detailed runbook procedure.",
        tenant_id="tenant-1",
        project_id="proj-1",
        domain_id="core",
        scope="project",
        agent_id="agent-1",
        blob_sha256="a" * 64,
    )

    async def _rich_batch_upsert(secs: list[KBSection]) -> dict[tuple[str, str, str, int], str]:
        return {
            (s.tenant_id, s.doc_id, s.citation, s.version): f"sec-{i + 1}"
            for i, s in enumerate(secs)
        }

    repo.upsert_kb_sections_batch = AsyncMock(side_effect=_rich_batch_upsert)
    repo.set_kb_sections_embedding_batch = AsyncMock(return_value=None)
    repo.search_kb_sections = AsyncMock(return_value=[kb_sec])
    repo.find_neighbors = AsyncMock(return_value=[{"id": "ADR-01", "properties": {}}])
    repo.get_kb_section = AsyncMock(return_value=kb_sec)
    repo.bulk_upsert_graph_entities = AsyncMock(return_value=1)
    repo.bulk_upsert_graph_relations = AsyncMock(return_value=1)
    repo.log_retrievals = AsyncMock(return_value=["ret-1"])
    repo.slice_runbook = AsyncMock(
        return_value=[
            {
                "doc_id": "DOC-1",
                "step_id": "s1",
                "tier": "green",
                "mode": "read-only",
                "command_body": "echo ok",
                "heading_path": "H1",
            }
        ]
    )

    storage = MagicMock()
    blob_obj = KBBlob(
        tenant_id="tenant-1",
        sha256="a" * 64,
        media_type="image/png",
        byte_size=len(png_bytes),
        project_id="proj-1",
    )
    storage.put_file = AsyncMock(return_value=blob_obj)
    storage.put = AsyncMock(return_value=blob_obj)
    storage.head = AsyncMock(return_value=blob_obj)
    storage.get = AsyncMock(return_value=png_bytes)

    graph = MagicMock()
    graph.neighbors = AsyncMock(return_value=[{"id": "ADR-01", "properties": {}}])
    graph.upsert_node = AsyncMock(return_value=None)
    graph.upsert_edge = AsyncMock(return_value=None)

    rich_surface = AccessSurface(repo, graph=graph, llm=llm, storage=storage)
    assert await rich_surface.knowledge_ingest_batch(
        _CTX, sections=[kb_sec], evidence_ref="CHG-1"
    ) == ["sec-1"]
    searched = await rich_surface.knowledge_search(
        _CTX, "procedure", domain_id="core", expand_graph=True, graph_depth=2
    )
    assert searched[0].media_metadata.get("graph_context")
    files_res = await rich_surface.knowledge_ingest_files(
        _CTX, paths=["guide.md"], evidence_ref="CHG-1", repo_root=tmp_path
    )
    assert files_res
    media_res = await rich_surface.knowledge_ingest_media(
        _CTX, file_path="diagram.png", evidence_ref="CHG-1", allowed_root=tmp_path
    )
    assert media_res["path"].endswith("diagram.png")
    blob_res = await rich_surface.blob_put(_CTX, file_path="diagram.png", allowed_root=tmp_path)
    assert blob_res["sha256"] == "a" * 64
    sdd_res = await rich_surface.sdd_ingest(
        _CTX, sdd_root="sdd", evidence_ref="CHG-1", ingest_fn=AsyncMock(return_value=(2, 1))
    )
    assert sdd_res["entities_ingested"] == 2
    with patch(
        "engramory.core.git_sync.GitSyncEngine.sync",
        new=AsyncMock(
            return_value=SimpleNamespace(
                success=True,
                base_sha="a",
                head_sha="b",
                files_added=1,
                files_modified=0,
                files_deleted=0,
                files_skipped=0,
                sections_created=1,
                duration_ms=2,
            )
        ),
    ):
        sync_out = await rich_surface.knowledge_sync_git(_CTX, repo_path=".")
        assert sync_out["success"] is True
    sliced = await rich_surface.docs_slice_runbook(_CTX, doc_id="DOC-1", include_memories=True)
    assert sliced["step_count"] == 1

    # agent_brain_import happy path (fresh insert + dedup replay)
    repo.get_memories = AsyncMock(return_value=[])
    imp_res = await rich_surface.agent_brain_import(_CTX, passport=passport, evidence_ref="CHG-1")
    assert imp_res["skills_imported"] == 1
    repo.get_memories = AsyncMock(return_value=[mem])
    imp_dup = await rich_surface.agent_brain_import(_CTX, passport=passport, evidence_ref="CHG-1")
    assert imp_dup["skills_imported"] == 1

    # compute_learning_health_metrics paired calibration & repeat findings
    lh = compute_learning_health_metrics(
        [
            Episode(
                agent_id="agent-1",
                project_id="proj-1",
                tenant_id="tenant-1",
                kind="plan.authored",
                content_raw="p",
                metadata={
                    "origin": {"repo": "org/r", "plan_id": "P1"},
                    "payload": {"exec_ready_score": 80},
                },
            ),
            Episode(
                agent_id="agent-1",
                project_id="proj-1",
                tenant_id="tenant-1",
                kind="execution.completed",
                content_raw="e",
                metadata={
                    "origin": {"repo": "org/r", "plan_id": "P1"},
                    "payload": {"first_pass_success": True},
                },
            ),
            Episode(
                agent_id="agent-1",
                project_id="proj-1",
                tenant_id="tenant-1",
                kind="review.verdict",
                content_raw="r1",
                metadata={
                    "origin": {"repo": "org/r", "plan_id": "P1"},
                    "payload": {"findings": [{"rule_id": "R1"}, "R2"], "rule_id": "R1"},
                },
            ),
        ]
    )
    assert lh["exec_ready_calibration"]["paired_plans"] == 1
    assert lh["repeat_findings"]["repeat_findings"] == 1


@pytest.mark.asyncio
async def test_git_sync_end_to_end_unit_coverage(tmp_path: Path) -> None:
    repo_dir = tmp_path / "sync_repo"
    repo_dir.mkdir()

    def _git(*args: str) -> str:
        out = subprocess.run(
            ["git", *args],
            cwd=repo_dir,
            check=True,
            capture_output=True,
            text=True,
        )
        return out.stdout.strip()

    _git("init")
    _git("config", "user.email", "test@example.com")
    _git("config", "user.name", "Test User")
    _git("remote", "add", "origin", "https://github.com/org/demo.git")

    doc_a = repo_dir / "doc_a.md"
    doc_a.write_text(
        "---\n"
        "id: DOC-A\n"
        "title: Doc A\n"
        "kind: runbook\n"
        "domain: core\n"
        "namespace: ns:platform\n"
        "status: active\n"
        "autonomy_tier: green\n"
        "watched_paths:\n"
        '  - "docs/**"\n'
        "last_verified: 2026-11-22\n"
        "---\n\n# Doc A\n\n## Probe\n\nCheck A.\n\n"
        '```bash {id="a1" tier="green" mode="read-only" expect_exit=0 timeout_sec=5}\n'
        "echo ok\n"
        "```\n",
        encoding="utf-8",
    )
    doc_old = repo_dir / "old.md"
    doc_old.write_text("# Old\n\nLegacy content.\n", encoding="utf-8")
    _git("add", ".")
    _git("commit", "-m", "initial")
    c1 = _git("rev-parse", "HEAD")

    doc_a.write_text(
        "---\n"
        "id: DOC-A\n"
        "title: Doc A v2\n"
        "kind: runbook\n"
        "domain: core\n"
        "namespace: ns:platform\n"
        "status: active\n"
        "autonomy_tier: green\n"
        "watched_paths:\n"
        '  - "docs/**"\n'
        "last_verified: 2026-11-22\n"
        "---\n\n# Doc A v2\n\n## Probe\n\nCheck A updated.\n\n"
        '```bash {id="a1" tier="green" mode="read-only" expect_exit=0 timeout_sec=5}\n'
        "echo ok\n"
        "```\n",
        encoding="utf-8",
    )
    doc_old.unlink()
    png = repo_dir / "diagram.png"
    png.write_bytes(b"\x89PNG\r\n\x1a\n" + struct.pack(">I4sII", 13, b"IHDR", 8, 8) + b"\x00" * 16)
    _git("add", "-A")
    _git("commit", "-m", "update")
    c2 = _git("rev-parse", "HEAD")

    repo = MagicMock()
    repo.get_max_kb_section_versions = AsyncMock(return_value={"proj-1:doc_a.md": 2})

    async def _upsert_batch(secs: list[KBSection]) -> dict[tuple[str, str, str, int], str]:
        return {
            (s.tenant_id, s.doc_id, s.citation, s.version): f"sec-{i}" for i, s in enumerate(secs)
        }

    repo.upsert_kb_sections_batch = AsyncMock(side_effect=_upsert_batch)
    repo.set_kb_sections_embedding_batch = AsyncMock(return_value=None)
    repo.bulk_upsert_graph_entities = AsyncMock(return_value=1)
    repo.bulk_upsert_graph_relations = AsyncMock(return_value=1)
    repo.delete_kb_sections_for_doc = AsyncMock(return_value=1)
    repo.delete_graph_entity = AsyncMock(return_value=None)
    repo.upsert_kb_document = AsyncMock(return_value="DOC-A")
    repo.upsert_kb_runbook_steps = AsyncMock(return_value=1)
    repo.delete_kb_document = AsyncMock(return_value=1)

    llm = MagicMock()
    llm.embed = AsyncMock(side_effect=lambda texts: [[0.1] * 1536 for _ in texts])
    llm.embed_model = "embed-x"
    storage = MagicMock()
    storage.put_file = AsyncMock(
        return_value=KBBlob(
            tenant_id="tenant-1", sha256="b" * 64, media_type="image/png", byte_size=40
        )
    )
    storage.gc_orphan_chunks = AsyncMock(return_value=0)
    graph = MagicMock()
    graph.upsert_node = AsyncMock(return_value=None)
    graph.upsert_edge = AsyncMock(return_value=None)
    graph.delete_node = AsyncMock(return_value=None)

    engine = GitSyncEngine()
    res = await engine.sync(
        repo,
        repo_path=repo_dir,
        base_sha=c1,
        head_sha=c2,
        tenant_id="tenant-1",
        project_id="proj-1",
        llm=llm,
        storage=storage,
        graph=graph,
    )
    assert res.success is True
    assert res.files_added >= 1
    assert res.files_modified >= 1
    assert res.files_deleted >= 1
