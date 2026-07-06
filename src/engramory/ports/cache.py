"""CachePort — ephemeral key/value cache (L1 session state, transient results).

Dev: Redis. Cloud: Memorystore / Azure Cache for Redis. Losing it is never data loss.
"""
from __future__ import annotations

from typing import Protocol


class CachePort(Protocol):
    async def get(self, *, key: str) -> bytes | None:
        """Return the cached value, or None if absent/expired."""
        ...

    async def set(self, *, key: str, value: bytes, ttl_seconds: int | None = None) -> None:
        """Cache a value with an optional TTL (seconds)."""
        ...

    async def delete(self, *, key: str) -> None:
        """Evict a key (idempotent)."""
        ...
