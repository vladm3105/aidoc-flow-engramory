"""GraphPort — entity/relationship graph interface (a rebuildable projection).

Dev default: pure Postgres (edge tables + recursive CTEs). Promote to Neo4j when real
multi-hop traversal / graph algorithms are needed (ADR-03). Never the source of truth.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Protocol


class GraphPort(Protocol):
    async def upsert_node(
        self, *, id: str, labels: Sequence[str], props: Mapping[str, Any] | None = None
    ) -> None:
        """Insert or update a node."""
        ...

    async def upsert_edge(
        self, *, src: str, dst: str, rel: str, props: Mapping[str, Any] | None = None
    ) -> None:
        """Insert or update a directed relationship src -[rel]-> dst."""
        ...

    async def neighbors(
        self, *, id: str, rel: str | None = None, depth: int = 1, tenant_id: str = "default"
    ) -> Sequence[Mapping[str, Any]]:
        """Return nodes reachable from `id` within `depth` hops (bounded traversal)."""
        ...

    async def ancestors(
        self, *, id: str, rel: str | None = None, depth: int = 6, tenant_id: str = "default"
    ) -> Sequence[Mapping[str, Any]]:
        """Return upstream ancestor nodes reachable from `id` along directed out-edges."""
        ...

    async def descendants(
        self, *, id: str, rel: str | None = None, depth: int = 6, tenant_id: str = "default"
    ) -> Sequence[Mapping[str, Any]]:
        """Return downstream descendant nodes reachable from `id` along directed in-edges."""
        ...

    async def delete_node(self, *, id: str, tenant_id: str = "default") -> None:
        """Delete a node and its attached relationships."""
        ...

    async def delete_edge(
        self, *, src: str, dst: str, rel: str | None = None, tenant_id: str = "default"
    ) -> None:
        """Delete relationship(s) between src and dst."""
        ...

    async def purge_stale_nodes(
        self,
        *,
        tenant_id: str,
        active_ids: Sequence[str],
        project_id: str | None = None,
    ) -> int:
        """Delete any nodes belonging to tenant_id (and optional project_id)
        whose IDs are not in active_ids.
        """
        ...
