"""NatsEventsAdapter — EventsPort implementation backed by NATS JetStream (PLAN-020 / SPEC-06).

Replaces the legacy aidoc-flow-interlog Phase C GitTransport (git clone/rebase/push)
with asynchronous NATS publish/subscribe over `engramory.events.>` (and legacy
`xp.engramory.experience.>`) topics, plus a deterministic in-memory buffer mode
for offline unit/conformance testing.
"""

from __future__ import annotations

import asyncio
import fnmatch
import inspect
import json
import logging
import os
from collections import deque
from collections.abc import AsyncIterator, Mapping
from typing import Any

from engramory.access.poisoning_guard import has_dangerous_unicode

logger = logging.getLogger(__name__)

DEFAULT_NATS_URL = "nats://localhost:4222"
DEFAULT_EXPERIENCE_TOPIC_PREFIX = "engramory.events"
LEGACY_EXPERIENCE_TOPIC_PREFIX = "xp.engramory.experience"
MAX_NATS_EVENT_BYTES = 65536
MAX_BUFFER_SIZE = 1000
MAX_DRAIN_ITEMS = 100


def _raw_topic_matches(pattern: str, subject: str) -> bool:
    """Match NATS subject hierarchy wildcards (`*` single token, `>` tail tokens)."""
    if pattern == subject or pattern == ">":
        return True
    if pattern.endswith(".>"):
        prefix = pattern[:-2]
        return subject == prefix or subject.startswith(f"{prefix}.")
    glob_pat = pattern.replace("*", "[^.]*")
    return fnmatch.fnmatchcase(subject, glob_pat)


def _topic_matches(pattern: str, subject: str) -> bool:
    """Match NATS subjects with dual-prefix compatibility."""
    if _raw_topic_matches(pattern, subject):
        return True
    for src_prefix, dst_prefix in (
        (DEFAULT_EXPERIENCE_TOPIC_PREFIX, LEGACY_EXPERIENCE_TOPIC_PREFIX),
        (LEGACY_EXPERIENCE_TOPIC_PREFIX, DEFAULT_EXPERIENCE_TOPIC_PREFIX),
    ):
        if pattern == src_prefix or pattern.startswith(f"{src_prefix}."):
            alt_pattern = dst_prefix + pattern[len(src_prefix) :]
            if _raw_topic_matches(alt_pattern, subject):
                return True
    return False


def _decode_nats_msg(msg: Any) -> dict[str, Any] | None:
    """Safely decode a single NATS message payload without aborting batch drain."""
    data = getattr(msg, "data", b"{}")
    try:
        decoded = json.loads(data.decode("utf-8") if isinstance(data, bytes) else str(data))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        logger.warning("Dropping malformed NATS event payload: %s", exc)
        return None
    if isinstance(decoded, Mapping):
        return dict(decoded)
    return None


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
        self._buffer: deque[tuple[str, dict[str, Any]]] = deque(maxlen=MAX_BUFFER_SIZE)

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
        except Exception as exc:  # noqa: BLE001 — reason: isolate malformed NATS payload without aborting batch drain  # reason: graceful fallback to local buffer when NATS broker is offline in dev
            logger.debug("NATS connection unavailable (%s); falling back to memory buffer", exc)
            self._use_memory_buffer = True
            return None

    async def publish(self, *, topic: str, event: Mapping[str, Any]) -> None:
        """Publish an event to ``topic`` (serializing Mapping to UTF-8 JSON)."""
        if not topic or not topic.strip():
            raise ValueError("NatsEventsAdapter.publish requires a non-empty topic")
        if any(ch.isspace() or ord(ch) < 32 or ord(ch) == 127 for ch in topic):
            raise ValueError(
                "NatsEventsAdapter.publish topic must not contain whitespace or control chars"
            )
        payload_dict = dict(event)
        raw_unicode_str = json.dumps(payload_dict, sort_keys=True, ensure_ascii=False)
        raw_bytes = raw_unicode_str.encode("utf-8")
        if len(raw_bytes) > MAX_NATS_EVENT_BYTES:
            raise ValueError(
                f"NatsEventsAdapter.publish payload exceeds {MAX_NATS_EVENT_BYTES} bytes"
            )
        if has_dangerous_unicode(raw_unicode_str):
            raise ValueError(
                "NatsEventsAdapter.publish rejected payload containing dangerous Unicode characters"
            )

        client = await self._ensure_client()
        if client is not None:
            headers: dict[str, str] = {}
            msg_id = payload_dict.get("event_id")
            if isinstance(msg_id, str) and msg_id:
                headers["Nats-Msg-Id"] = msg_id
            origin = payload_dict.get("origin")
            trace_id = payload_dict.get("trace_id") or (
                origin.get("trace_id") if isinstance(origin, Mapping) else None
            )
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
        if any(ch.isspace() or ord(ch) < 32 or ord(ch) == 127 for ch in topic):
            raise ValueError(
                "NatsEventsAdapter.subscribe topic must not contain whitespace or control chars"
            )
        return self._drain_topic(topic)

    async def _drain_topic(self, topic: str) -> AsyncIterator[Mapping[str, Any]]:
        client = await self._ensure_client()
        if client is not None and hasattr(client, "subscribe"):
            sub = await client.subscribe(topic)
            try:
                next_msg_fn = getattr(sub, "next_msg", None)
                if inspect.iscoroutinefunction(next_msg_fn):
                    for _ in range(MAX_DRAIN_ITEMS):
                        try:
                            msg = await next_msg_fn(timeout=0.05)
                        except (TimeoutError, Exception):
                            break
                        decoded = _decode_nats_msg(msg)
                        if decoded is not None:
                            yield decoded
                    return

                messages = getattr(sub, "messages", None)
                if messages is not None:
                    it = aiter(messages)
                    for _ in range(MAX_DRAIN_ITEMS):
                        try:
                            msg = await asyncio.wait_for(anext(it), timeout=0.05)
                        except (StopAsyncIteration, TimeoutError):
                            break
                        decoded = _decode_nats_msg(msg)
                        if decoded is not None:
                            yield decoded
                    return
            finally:
                unsub_fn = getattr(sub, "unsubscribe", None)
                if unsub_fn is not None:
                    try:
                        res = unsub_fn()
                        if inspect.isawaitable(res):
                            await res
                    except Exception as exc:  # noqa: BLE001 — reason: isolate malformed NATS payload without aborting batch drain
                        logger.debug("NATS unsubscribe warning: %s", exc)

        remaining: deque[tuple[str, dict[str, Any]]] = deque(maxlen=MAX_BUFFER_SIZE)
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
