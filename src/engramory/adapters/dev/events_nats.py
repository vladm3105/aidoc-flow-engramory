"""NatsEventsAdapter — EventsPort implementation backed by NATS JetStream (PLAN-020 / SPEC-06).

Replaces the legacy aidoc-flow-interlog Phase C GitTransport (git clone/rebase/push)
with asynchronous NATS publish/subscribe over `engramory.events.>` topics, plus a
deterministic in-memory buffer mode for offline unit/conformance testing.
"""

from __future__ import annotations

import fnmatch
import inspect
import json
import logging
import os
from collections import deque
from collections.abc import AsyncIterator, Mapping
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_NATS_URL = "nats://localhost:4222"
DEFAULT_EXPERIENCE_TOPIC_PREFIX = "engramory.events"


def _topic_matches(pattern: str, subject: str) -> bool:
    """Match NATS subject hierarchy wildcards (`*` single token, `>` tail tokens)."""
    if pattern == subject or pattern == ">":
        return True
    if pattern.endswith(".>"):
        prefix = pattern[:-2]
        return subject == prefix or subject.startswith(f"{prefix}.")
    glob_pat = pattern.replace("*", "[^.]*")
    return fnmatch.fnmatchcase(subject, glob_pat)


class NatsEventsAdapter:
    """EventsPort implementation using NATS/JetStream or an in-memory queue buffer."""

    def __init__(
        self,
        url: str | None = None,
        *,
        client: Any | None = None,
        use_memory_buffer: bool = False,
    ) -> None:
        self._url: str = (
            url or os.getenv("ENGRAMORY_NATS_URL") or os.getenv("NATS_URL") or DEFAULT_NATS_URL
        )
        self._client: Any | None = client
        self._use_memory_buffer: bool = use_memory_buffer
        self._buffer: deque[tuple[str, dict[str, Any]]] = deque()

    @property
    def url(self) -> str:
        return self._url

    async def _ensure_client(self) -> Any | None:
        if self._use_memory_buffer:
            return None
        if self._client is not None:
            return self._client
        try:
            import nats  # type: ignore[import-not-found]

            self._client = await nats.connect(self._url)
            return self._client
        except Exception as exc:  # noqa: BLE001  # reason: graceful fallback to local buffer when NATS broker is offline in dev
            logger.debug("NATS connection unavailable (%s); falling back to memory buffer", exc)
            self._use_memory_buffer = True
            return None

    async def publish(self, *, topic: str, event: Mapping[str, Any]) -> None:
        """Publish an event to ``topic`` (serializing Mapping to UTF-8 JSON)."""
        if not topic or not topic.strip():
            raise ValueError("NatsEventsAdapter.publish requires a non-empty topic")
        payload_dict = dict(event)
        raw_bytes = json.dumps(payload_dict, sort_keys=True).encode("utf-8")

        client = await self._ensure_client()
        if client is not None:
            headers: dict[str, str] = {}
            msg_id = payload_dict.get("event_id")
            if isinstance(msg_id, str) and msg_id:
                headers["Nats-Msg-Id"] = msg_id
            trace_id = payload_dict.get("trace_id")
            if isinstance(trace_id, str) and trace_id:
                headers["traceparent"] = f"00-{trace_id}-0000000000000001-01"
            pub_fn = getattr(client, "publish", None)
            if pub_fn is not None:
                try:
                    res = pub_fn(topic, raw_bytes, headers=headers or None)
                except TypeError:
                    res = pub_fn(topic, raw_bytes)
                if inspect.isawaitable(res):
                    await res
                return

        self._buffer.append((topic, payload_dict))

    def subscribe(self, *, topic: str) -> AsyncIterator[Mapping[str, Any]]:
        """Yield currently buffered or incoming events matching ``topic``."""
        if not topic or not topic.strip():
            raise ValueError("NatsEventsAdapter.subscribe requires a non-empty topic")
        return self._drain_topic(topic)

    async def _drain_topic(self, topic: str) -> AsyncIterator[Mapping[str, Any]]:
        client = await self._ensure_client()
        if client is not None and hasattr(client, "subscribe"):
            sub = await client.subscribe(topic)
            messages = getattr(sub, "messages", None)
            if messages is not None:
                async for msg in messages:
                    data = getattr(msg, "data", b"{}")
                    decoded = json.loads(
                        data.decode("utf-8") if isinstance(data, bytes) else str(data)
                    )
                    if isinstance(decoded, Mapping):
                        yield dict(decoded)
                return

        remaining: deque[tuple[str, dict[str, Any]]] = deque()
        matched: list[dict[str, Any]] = []
        while self._buffer:
            subj, item = self._buffer.popleft()
            if _topic_matches(topic, subj):
                matched.append(item)
            else:
                remaining.append((subj, item))
        self._buffer = remaining
        for item in matched:
            yield item

    async def close(self) -> None:
        """Close the underlying NATS client connection if open."""
        if self._client is not None and hasattr(self._client, "close"):
            res = self._client.close()
            if inspect.isawaitable(res):
                await res
        self._client = None

    async def __aenter__(self) -> NatsEventsAdapter:
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        await self.close()
