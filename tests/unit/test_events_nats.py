"""Unit tests for NatsEventsAdapter JetStream, wildcard matching, headers, and reconnect (H1)."""

from __future__ import annotations

import contextlib
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from engramory.adapters.dev.events_nats import NatsEventsAdapter, _raw_topic_matches


def test_raw_topic_matches_nats_token_boundaries() -> None:
    assert _raw_topic_matches("engramory.events.*", "engramory.events.ok") is True
    assert _raw_topic_matches("engramory.events.*", "engramory.events.plan.authored") is False
    assert _raw_topic_matches("engramory.events.>", "engramory.events.plan.authored") is True
    assert _raw_topic_matches("engramory.events.>", "engramory.events") is False
    assert _raw_topic_matches("a.*.c", "a.b.c") is True
    assert _raw_topic_matches("a.*.c", "a.b.x.c") is False
    assert _raw_topic_matches("a.[x]", "a.b") is False


@pytest.mark.asyncio
async def test_publish_rejects_crlf_headers_and_strips_spoofed_subject() -> None:
    js = AsyncMock()
    js.publish = AsyncMock(return_value=SimpleNamespace(stream="ENGRAMORY_EVENTS", seq=1))
    client = MagicMock()
    client.jetstream.return_value = js
    client.is_closed = False

    adapter = NatsEventsAdapter(client=client)

    with pytest.raises(ValueError, match="control characters"):
        await adapter.publish(
            topic="engramory.events.plan.authored",
            event={"event_id": "bad\r\nX-Injected: 1", "trace_id": "0" * 32},
        )

    await adapter.publish(
        topic="engramory.events.tenant-a.plan.authored",
        event={
            "event_id": "evt-1",
            "trace_id": "a" * 32,
            "_nats_subject": "engramory.events.victim.plan.authored",
        },
    )
    js.publish.assert_awaited_once()
    sent_payload = json.loads(js.publish.await_args.args[1].decode("utf-8"))
    assert "_nats_subject" not in sent_payload
    assert js.publish.await_args.kwargs["headers"]["Nats-Msg-Id"] == "evt-1"


@pytest.mark.asyncio
async def test_jetstream_pull_subscribe_stamps_subject_and_acks_or_naks() -> None:
    ack_mock = AsyncMock()
    nak_mock = AsyncMock()
    good_msg = SimpleNamespace(
        subject="engramory.events.tenant-a.plan.authored",
        data=json.dumps({"event_id": "ok-1", "_nats_subject": "spoofed"}).encode("utf-8"),
        ack=ack_mock,
        nak=nak_mock,
    )

    sub = MagicMock()
    sub.fetch = AsyncMock(side_effect=[[good_msg], TimeoutError()])
    sub.unsubscribe = AsyncMock()
    js = MagicMock()
    js.pull_subscribe = AsyncMock(return_value=sub)
    js.publish = AsyncMock()

    client = MagicMock()
    client.jetstream.return_value = js
    client.is_closed = False

    adapter = NatsEventsAdapter(client=client, durable_name="worker_durable_test")
    received = [ev async for ev in adapter.subscribe(topic="engramory.events.>")]
    assert len(received) == 1
    assert received[0]["_nats_subject"] == "engramory.events.tenant-a.plan.authored"
    ack_mock.assert_awaited_once()
    nak_mock.assert_not_awaited()
    sub.unsubscribe.assert_not_called()

    # Consumer exception triggers nak() instead of ack()
    ack2 = AsyncMock()
    nak2 = AsyncMock()
    bad_consumer_msg = SimpleNamespace(
        subject="engramory.events.tenant-a.plan.authored",
        data=json.dumps({"event_id": "fail-1"}).encode("utf-8"),
        ack=ack2,
        nak=nak2,
    )
    sub.fetch = AsyncMock(return_value=[bad_consumer_msg])

    with pytest.raises(RuntimeError, match="boom"):
        async with contextlib.aclosing(adapter.subscribe(topic="engramory.events.>")) as stream:
            async for _ev in stream:
                raise RuntimeError("boom")

    nak2.assert_awaited_once()
    ack2.assert_not_awaited()


@pytest.mark.asyncio
async def test_reconnect_backoff_is_not_sticky(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = 0
    fake_client = MagicMock()
    fake_client.is_closed = False
    fake_js = MagicMock()
    fake_js.publish = AsyncMock(return_value=SimpleNamespace(stream="S", seq=1))
    fake_client.jetstream.return_value = fake_js

    async def _fake_connect(_url: str) -> MagicMock:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ConnectionError("nats temporarily down")
        return fake_client

    import sys
    import types

    fake_nats_mod = types.ModuleType("nats")
    fake_nats_mod.connect = _fake_connect  # type: ignore[attr-defined]  # reason: dynamic test module stub
    monkeypatch.setitem(sys.modules, "nats", fake_nats_mod)

    adapter = NatsEventsAdapter(url="nats://127.0.0.1:4222")
    assert await adapter._ensure_client() is None
    assert adapter._use_memory_buffer is False

    # During backoff window, immediate call skips connect
    assert await adapter._ensure_client() is None
    assert calls == 1

    # Once backoff expires, reconnect succeeds without rebuilding adapter
    adapter._retry_after = 0.0
    assert await adapter._ensure_client() is fake_client
    assert calls == 2
