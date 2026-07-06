"""VectorPort — similarity-search interface over embeddings.

Dev: pgvector in the canonical Postgres. Cloud: the same (AlloyDB / Azure PG) or a
managed index used as a rebuildable projection only — never the source of truth.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Protocol


class VectorPort(Protocol):
    async def upsert(self, *, id: str, embedding: Sequence[float],
                     metadata: Mapping[str, Any] | None = None) -> None:
        """Insert or replace a vector and its metadata under `id`."""
        ...

    async def query(self, *, embedding: Sequence[float], k: int = 8,
                    filter: Mapping[str, Any] | None = None) -> Sequence[tuple[str, float]]:
        """Return up to k (id, distance) pairs nearest to `embedding`, closest first."""
        ...

    async def delete(self, *, id: str) -> None:
        """Remove a vector by id (idempotent)."""
        ...
