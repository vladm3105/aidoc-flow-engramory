"""StoragePort — object storage interface; implementations in engramory.adapters.{dev,gcp,azure}.

Vendor-neutral: MinIO (dev) / GCS (gcp) / Azure Blob (azure) all satisfy this.
"""
from __future__ import annotations

from typing import Protocol


class StoragePort(Protocol):
    async def put(self, *, key: str, data: bytes, content_type: str | None = None) -> None:
        """Store an object at `key` (overwrites any existing object)."""
        ...

    async def get(self, *, key: str) -> bytes:
        """Fetch the object at `key`. Raises if it does not exist."""
        ...

    async def delete(self, *, key: str) -> None:
        """Remove the object at `key` (idempotent)."""
        ...

    async def exists(self, *, key: str) -> bool:
        """Return True if an object exists at `key`."""
        ...
