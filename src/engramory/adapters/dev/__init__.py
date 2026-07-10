"""Dev-profile adapters: free/OSS backends from docker-compose.yml.

Implemented so far: PgVectorAdapter (VectorPort over pgvector in the canonical
Postgres). The remaining ports gain dev adapters as their consumers arrive.
"""
from __future__ import annotations

from engramory.adapters.dev.vector_pg import PgVectorAdapter

__all__ = ["PgVectorAdapter"]
