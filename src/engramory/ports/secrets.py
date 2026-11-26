"""SecretsPort — read-only secret resolution interface.

Planned port interface: stage/dev currently consume secrets via OpenBao ``/dev/shm``
ephemeral env injection and environment variables; dedicated OpenBao / Secret Manager
/ Key Vault adapter implementations arrive under ``engramory.adapters``. Resolved
values must never land in logs or the canonical store.
"""

from __future__ import annotations

from typing import Protocol


class SecretsPort(Protocol):
    async def get(self, *, name: str) -> str:
        """Resolve a secret by logical name. Raises if it is undefined."""
        ...
