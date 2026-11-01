"""Integration tests for background distillation runner (PLAN-005 Phase 4)."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from engramory.adapters.dev.vector_pg import PgVectorAdapter
from engramory.core.models import Episode
from engramory.core.repository import Repository
from engramory.ports import LLMPort
from engramory.workers.runner import run_worker_loop

pytestmark = pytest.mark.asyncio


def _vec384(val: float = 0.5) -> list[float]:
    v = [0.0] * 384
    v[0] = val
    return v


async def test_worker_runner_reflects_and_touches_heartbeat(tmp_path: Path, pg_dsn: str) -> None:
    repo = Repository(pg_dsn)
    vec = PgVectorAdapter(pg_dsn)
    heartbeat_file = tmp_path / "worker.heartbeat"

    # Add unreflected episode
    ep = Episode(
        agent_id="agent-runner-test",
        project_id="p-runner",
        tenant_id="t-runner-test",
        content_raw="Heartbeat daemon verification episode",
    )
    await repo.add_episode(ep)

    mock_llm = AsyncMock(spec=LLMPort)
    mock_llm.embed.return_value = [_vec384(0.77)]

    # Start runner task with 0.1s interval
    task = asyncio.create_task(
        run_worker_loop(
            repo,
            llm=mock_llm,
            vector=vec,
            interval_sec=0.1,
            tenant_id="t-runner-test",
            heartbeat_path=heartbeat_file,
        )
    )

    # Allow one loop iteration to complete
    await asyncio.sleep(0.3)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    # Verify heartbeat file exists and has content
    assert heartbeat_file.exists()
    content = heartbeat_file.read_text(encoding="utf-8").strip()
    assert float(content) > 0

    # Verify episode was distilled into memory
    unreflected = await repo.get_unreflected_episodes(
        tenant_id="t-runner-test", agent_id="agent-runner-test"
    )
    assert len(unreflected) == 0

    memories = await repo.get_memories(tenant_id="t-runner-test", agent_id="agent-runner-test")
    assert len(memories) >= 1
    assert "Heartbeat daemon verification" in memories[0].content_raw
