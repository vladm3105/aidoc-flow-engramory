"""NatsEventsAdapter — EventsPort implementation backed by NATS JetStream (PLAN-020 / SPEC-06).

Replaces the legacy aidoc-flow-interlog Phase C GitTransport (git clone/rebase/push)
with asynchronous JetStream publish/pull-subscribe over ``engramory.events.>`` (and
legacy ``xp.engramory.experience.>``) topics, plus a deterministic in-memory buffer mode
for offline unit/conformance testing.
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import logging
import os
import time
from collections import deque
from collections.abc import AsyncIterator, Mapping
from typing import Any

from engramory.core.poisoning_guard import has_dangerous_unicode

logger = logging.getLogger(__name__)

DEFAULT_NATS_URL = "nats://localhost:4222"
DEFAULT_EXPERIENCE_TOPIC_PREFIX = "engramory.events"
LEGACY_EXPERIENCE_TOPIC_PREFIX = "xp.engramory.experience"
DEFAULT_STREAM_NAME = "ENGRAMORY_EVENTS"
MAX_NATS_EVENT_BYTES = 65536
MAX_BUFFER_SIZE = 1000
MAX_DRAIN_ITEMS = 100
MAX_RECONNECT_BACKOFF_SEC = 30.0


def _raw_topic_matches(pattern: str, subject: str) -> bool:
    """Match NATS subject hierarchy wildcards using exact token boundaries.

    - ``*`` matches exactly one non-empty dot-delimited token.
    - ``>`` matches one or more trailing tokens and is only valid as the final token.
    """
    if not pattern or not subject:
        return False
    pat_tokens = pattern.split(".")
    subj_tokens = subject.split(".")
    if any(not tok for tok in pat_tokens) or any(not tok for tok in subj_tokens):
        return False
    for idx, tok in enumerate(pat_tokens):
        if tok == ">":
            return idx == len(pat_tokens) - 1 and idx < len(subj_tokens)
        if idx >= len(subj_tokens):
            return False
        if tok != "*" and tok != subj_tokens[idx]:
            return False
    return len(pat_tokens) == len(subj_tokens)


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


def _validate_topic(topic: str, op: str) -> None:
    if not topic or not topic.strip():
        raise ValueError(f"NatsEventsAdapter.{op} requires a non-empty topic")
    if any(ch.isspace() or ord(ch) < 32 or ord(ch) == 127 for ch in topic):
        raise ValueError(
            f"NatsEventsAdapter.{op} topic must not contain whitespace or control chars"
        )


def _validate_header_value(name: str, value: str) -> str:
    for field_label, raw in (("header name", name), (f"header {name!r} value", value)):
        if any(ord(ch) < 32 or ord(ch) == 127 or ch in ("\r", "\n", "\x00") for ch in raw):
            raise ValueError(
                f"NatsEventsAdapter {field_label} contains prohibited control characters"
            )
    return value


def _durable_name_for_topic(topic: str) -> str:
    digest = hashlib.sha256(topic.encode()).hexdigest()[:16]
    return f"engramory_worker_{digest}"


def _decode_nats_msg(msg: Any, fallback_subject: str | None = None) -> dict[str, Any] | None:
    """Safely decode a single NATS message payload and stamp broker-verified subject."""
    data = getattr(msg, "data", b"{}")
    try:
        decoded = json.loads(data.decode("utf-8") if isinstance(data, bytes) else str(data))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        logger.warning("Dropping malformed NATS event payload: %s", exc)
        return None
    if not isinstance(decoded, Mapping):
        return None
    out = dict(decoded)
    broker_subject = getattr(msg, "subject", None)
    if isinstance(broker_subject, str) and broker_subject.strip():
        out["_nats_subject"] = broker_subject.strip()
    elif fallback_subject:
        out["_nats_subject"] = fallback_subject
    else:
        out.pop("_nats_subject", None)
    return out


class NatsEventsAdapter:
    """EventsPort implementation using NATS JetStream or an in-memory queue buffer."""

    def __init__(
        self,
        url: str | None = None,
        *,
        client: Any | None = None,
        use_memory_buffer: bool = False,
        durable_name: str | None = None,
        stream_name: str = DEFAULT_STREAM_NAME,
    ) -> None:
        self._url: str = (
            url or os.getenv("ENGRAMORY_NATS_URL") or os.getenv("NATS_URL") or DEFAULT_NATS_URL
        )
        self._client: Any | None = client
        self._explicit_memory_buffer: bool = use_memory_buffer
        self._use_memory_buffer: bool = use_memory_buffer
        self._durable_name: str | None = durable_name
        self._stream_name: str = stream_name
        self._buffer: deque[tuple[str, dict[str, Any]]] = deque(maxlen=MAX_BUFFER_SIZE)
        self._pull_subs: dict[str, Any] = {}
        self._reconnect_failures: int = 0
        self._retry_after: float = 0.0

    @property
    def url(self) -> str:
        return self._url

    def _record_disconnect(self) -> None:
        self._client = None
        self._pull_subs.clear()
        self._reconnect_failures += 1
        backoff = min(
            MAX_RECONNECT_BACKOFF_SEC,
            0.25 * (2 ** min(self._reconnect_failures - 1, 7)),
        )
        self._retry_after = time.monotonic() + backoff

    async def _ensure_client(self) -> Any | None:
        if self._explicit_memory_buffer:
            return None
        if self._client is not None:
            if getattr(self._client, "is_closed", False) is True:
                self._record_disconnect()
            else:
                return self._client
        if time.monotonic() < self._retry_after:
            return None
        try:
            import nats  # type: ignore[import-not-found]

            self._client = await nats.connect(self._url)
            self._reconnect_failures = 0
            self._retry_after = 0.0
            self._use_memory_buffer = False
            return self._client
        except Exception as exc:  # noqa: BLE001  # reason: bounded backoff retry when broker is transiently unavailable
            self._record_disconnect()
            logger.debug("NATS connection unavailable (%s); backing off", exc)
            return None

    def _get_jetstream(self, client: Any) -> Any | None:
        js_fn = getattr(client, "jetstream", None)
        if not callable(js_fn):
            return None
        try:
            js = js_fn()
        except Exception as exc:  # noqa: BLE001  # reason: fall back to Core NATS mock if JetStream creation fails
            logger.debug("JetStream initialization skipped (%s)", exc)
            return None
        if js is None:
            return None
        if hasattr(js, "publish") or hasattr(js, "pull_subscribe"):
            return js
        return None

    async def publish(self, *, topic: str, event: Mapping[str, Any]) -> None:
        """Publish an event to ``topic`` via JetStream PubAck or local buffer."""
        _validate_topic(topic, "publish")
        payload_dict = dict(event)
        payload_dict.pop("_nats_subject", None)
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

        headers: dict[str, str] = {}
        msg_id = payload_dict.get("event_id")
        if isinstance(msg_id, str) and msg_id:
            headers["Nats-Msg-Id"] = _validate_header_value("Nats-Msg-Id", msg_id)
        origin = payload_dict.get("origin")
        trace_id = payload_dict.get("trace_id") or (
            origin.get("trace_id") if isinstance(origin, Mapping) else None
        )
        if isinstance(trace_id, str) and trace_id:
            headers["traceparent"] = _validate_header_value(
                "traceparent", f"00-{trace_id}-0000000000000001-01"
            )

        client = await self._ensure_client()
        if client is not None:
            js = self._get_jetstream(client)
            target = js if (js is not None and hasattr(js, "publish")) else client
            pub_fn = getattr(target, "publish", None)
            if pub_fn is not None:
                try:
                    try:
                        res = pub_fn(topic, raw_bytes, headers=headers or None)
                    except TypeError:
                        res = pub_fn(topic, raw_bytes)
                    if inspect.isawaitable(res):
                        ack = await res
                        if js is not None and ack is None:
                            raise RuntimeError("JetStream publish did not return PubAck")
                    return
                except (ValueError, TypeError):
                    raise
                except Exception as exc:  # noqa: BLE001  # reason: reconnect backoff on transient NATS publish error
                    logger.warning("NATS publish failed (%s); buffering locally", exc)
                    self._record_disconnect()

        self._buffer.append((topic, payload_dict))

    def subscribe(self, *, topic: str) -> AsyncIterator[Mapping[str, Any]]:
        """Yield events via durable JetStream pull consumer or local memory buffer."""
        _validate_topic(topic, "subscribe")
        return self._drain_topic(topic)

    async def _drain_topic(self, topic: str) -> AsyncIterator[Mapping[str, Any]]:
        client = await self._ensure_client()
        if client is not None:
            js = self._get_jetstream(client)
            if js is not None and hasattr(js, "pull_subscribe"):
                durable = self._durable_name or _durable_name_for_topic(topic)
                try:
                    sub = self._pull_subs.get(topic)
                    if sub is None:
                        sub_res = js.pull_subscribe(topic, durable=durable)
                        sub = await sub_res if inspect.isawaitable(sub_res) else sub_res
                        self._pull_subs[topic] = sub
                    fetch_fn = getattr(sub, "fetch", None)
                    if fetch_fn is not None:
                        try:
                            msgs = fetch_fn(batch=MAX_DRAIN_ITEMS, timeout=0.05)
                            if inspect.isawaitable(msgs):
                                msgs = await msgs
                        except TimeoutError:
                            msgs = []
                        except Exception as fetch_exc:
                            # nats.errors.TimeoutError subclasses Exception on older clients
                            if "timeout" in type(fetch_exc).__name__.lower():
                                msgs = []
                            else:
                                raise
                        for msg in msgs or ():
                            decoded = _decode_nats_msg(msg)
                            if decoded is None:
                                ack_fn = getattr(msg, "ack", None)
                                if ack_fn is not None:
                                    ack_res = ack_fn()
                                    if inspect.isawaitable(ack_res):
                                        await ack_res
                                continue
                            try:
                                yield decoded
                            except BaseException:
                                nak_fn = getattr(msg, "nak", None)
                                if nak_fn is not None:
                                    try:
                                        nak_res = nak_fn()
                                        if inspect.isawaitable(nak_res):
                                            await nak_res
                                    except Exception as nak_exc:  # noqa: BLE001  # reason: preserve consumer exception over best-effort nak
                                        logger.debug("JetStream nak warning: %s", nak_exc)
                                raise
                            else:
                                ack_fn = getattr(msg, "ack", None)
                                if ack_fn is not None:
                                    ack_res = ack_fn()
                                    if inspect.isawaitable(ack_res):
                                        await ack_res
                        return
                except Exception:
                    self._pull_subs.pop(topic, None)
                    raise

            if hasattr(client, "subscribe"):
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
                        except Exception as exc:  # noqa: BLE001  # reason: cleanup best effort on transient sub
                            logger.debug("NATS unsubscribe warning: %s", exc)

        remaining: deque[tuple[str, dict[str, Any]]] = deque(maxlen=MAX_BUFFER_SIZE)
        matched: list[tuple[str, dict[str, Any]]] = []
        while self._buffer:
            subj, item = self._buffer.popleft()
            if _topic_matches(topic, subj):
                matched.append((subj, item))
            else:
                remaining.append((subj, item))
        self._buffer = remaining
        for subj, item in matched:
            out = dict(item)
            out["_nats_subject"] = subj
            yield out

    async def close(self) -> None:
        """Close the underlying NATS client connection if open."""
        self._pull_subs.clear()
        if self._client is not None and hasattr(self._client, "close"):
            res = self._client.close()
            if inspect.isawaitable(res):
                await res
        self._client = None

    async def __aenter__(self) -> NatsEventsAdapter:
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        await self.close()
