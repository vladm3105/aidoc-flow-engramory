"""CachePort — ephemeral key/value cache interface (L1 session state, transient results).

Planned port interface: no concrete ``CachePort`` adapter is shipped in dev/stage
profiles yet (Valkey/Redis / Memorystore / Azure Cache adapters arrive when needed).
Losing cached entries must never cause data loss.
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
