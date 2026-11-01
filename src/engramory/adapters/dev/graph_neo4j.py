"""Neo4jGraphAdapter — GraphPort implementation backed by Neo4j.

The graph in Neo4j is a rebuildable projection (ADR-03 / ADR-05):
PostgreSQL remains the canonical source of truth for entities and relations.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping, Sequence
from typing import Any

from neo4j import AsyncDriver, AsyncGraphDatabase

DEFAULT_NEO4J_URI = "bolt://localhost:7687"
DEFAULT_NEO4J_USER = "neo4j"
DEFAULT_NEO4J_PASSWORD = "change_me"

_IDENT_RE = re.compile(r"^[A-Za-z0-9_]+$")


def _sanitize_ident(ident: str, kind: str) -> str:
    """Validate identifier contains only alphanumeric characters or underscores."""
    if not _IDENT_RE.match(ident):
        raise ValueError(f"invalid {kind} identifier: {ident!r}")
    return ident


class Neo4jGraphAdapter:
    """GraphPort implementation using asynchronous Neo4j Bolt driver."""

    def __init__(
        self,
        uri: str | None = None,
        auth: tuple[str, str] | None = None,
        driver: AsyncDriver | None = None,
    ) -> None:
        resolved_uri = uri or os.getenv("NEO4J_URI") or DEFAULT_NEO4J_URI
        self._uri: str = resolved_uri
        user: str = (auth[0] if auth else None) or os.getenv("NEO4J_USER") or DEFAULT_NEO4J_USER
        password: str = (
            (auth[1] if auth else None) or os.getenv("NEO4J_PASSWORD") or DEFAULT_NEO4J_PASSWORD
        )
        self._auth: tuple[str, str] = (user, password)
        self._driver = driver or AsyncGraphDatabase.driver(self._uri, auth=self._auth)

    async def close(self) -> None:
        """Close the underlying Neo4j driver connection pool."""
        await self._driver.close()

    async def __aenter__(self) -> Neo4jGraphAdapter:
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        await self.close()

    async def init_schema(self) -> None:
        """Ensure uniqueness constraints and indexes exist in Neo4j."""
        query = (
            "CREATE CONSTRAINT entity_id_unique IF NOT EXISTS FOR (n:Entity) REQUIRE n.id IS UNIQUE"
        )
        async with self._driver.session() as session:
            await session.run(query)

    async def upsert_node(
        self,
        *,
        id: str,
        labels: Sequence[str],
        props: Mapping[str, Any] | None = None,
    ) -> None:
        """Insert or update a node with given labels and properties."""
        sanitized_labels = [_sanitize_ident(label, "label") for label in labels]
        clean_props = dict(props or {})

        # Build Cypher with sanitized labels appended
        label_str = ":Entity"
        for label in sanitized_labels:
            if label != "Entity":
                label_str += f":`{label}`"

        query = f"""
        MERGE (n:Entity {{id: $id}})
        SET n{label_str}, n += $props
        """
        async with self._driver.session() as session:
            await session.run(query, {"id": id, "props": clean_props})

    async def upsert_edge(
        self,
        *,
        src: str,
        dst: str,
        rel: str,
        props: Mapping[str, Any] | None = None,
    ) -> None:
        """Insert or update a directed relationship src -[rel]-> dst."""
        sanitized_rel = _sanitize_ident(rel, "relationship type")
        clean_props = dict(props or {})

        query = f"""
        MERGE (a:Entity {{id: $src}})
        MERGE (b:Entity {{id: $dst}})
        MERGE (a)-[r:`{sanitized_rel}`]->(b)
        SET r += $props
        """
        async with self._driver.session() as session:
            await session.run(query, {"src": src, "dst": dst, "props": clean_props})

    async def neighbors(
        self,
        *,
        id: str,
        rel: str | None = None,
        depth: int = 1,
    ) -> Sequence[Mapping[str, Any]]:
        """Return nodes reachable from `id` within `depth` hops (bounded traversal)."""
        safe_depth = max(1, min(depth, 5))
        if rel:
            sanitized_rel = _sanitize_ident(rel, "relationship type")
            rel_pattern = f":`{sanitized_rel}`*1..{safe_depth}"
        else:
            rel_pattern = f"*1..{safe_depth}"

        query = f"""
        MATCH (n:Entity {{id: $id}})-[{rel_pattern}]-(m:Entity)
        WHERE m.id <> $id
        RETURN DISTINCT m.id AS id, labels(m) AS labels, properties(m) AS properties
        """
        async with self._driver.session() as session:
            result = await session.run(query, {"id": id})
            records = await result.data()
            return [
                {
                    "id": record["id"],
                    "labels": [lbl for lbl in record["labels"] if lbl != "Entity"],
                    "properties": record["properties"],
                }
                for record in records
            ]
