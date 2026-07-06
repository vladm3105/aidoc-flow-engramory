"""EventsPort — asynchronous event publish/subscribe (drives reflection/consolidation).

Dev: Redis Streams / NATS. Cloud: Pub/Sub / Azure Service Bus.
"""
from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from typing import Any, Protocol


class EventsPort(Protocol):
    async def publish(self, *, topic: str, event: Mapping[str, Any]) -> None:
        """Publish an event to a topic."""
        ...

    def subscribe(self, *, topic: str) -> AsyncIterator[Mapping[str, Any]]:
        """Yield events from a topic as they arrive (async iterator)."""
        ...
