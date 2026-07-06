"""GraphPort — entity/relationship graph interface (a rebuildable projection).

Dev default: pure Postgres (edge tables + recursive CTEs). Promote to Neo4j when real
multi-hop traversal / graph algorithms are needed (ADR-03). Never the source of truth.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Protocol


class GraphPort(Protocol):
    async def upsert_node(self, *, id: str, labels: Sequence[str],
                          props: Mapping[str, Any] | None = None) -> None:
        """Insert or update a node."""
        ...

    async def upsert_edge(self, *, src: str, dst: str, rel: str,
                          props: Mapping[str, Any] | None = None) -> None:
        """Insert or update a directed relationship src -[rel]-> dst."""
        ...

    async def neighbors(self, *, id: str, rel: str | None = None,
                        depth: int = 1) -> Sequence[Mapping[str, Any]]:
        """Return nodes reachable from `id` within `depth` hops (bounded traversal)."""
        ...
