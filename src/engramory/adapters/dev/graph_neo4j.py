"""Neo4jGraphAdapter — GraphPort implementation backed by Neo4j.

The graph in Neo4j is a rebuildable projection (ADR-03 / ADR-05):
PostgreSQL remains the canonical source of truth for entities and relations.
"""

from __future__ import annotations

import logging
import os
import re
from collections.abc import Mapping, Sequence
from typing import Any

from neo4j import AsyncDriver, AsyncGraphDatabase

logger = logging.getLogger(__name__)

DEFAULT_NEO4J_URI = "bolt://localhost:7687"
DEFAULT_NEO4J_USER = "neo4j"
DEFAULT_NEO4J_PASSWORD = "change_me"  # nosec B105  # reason: local dev default placeholder when NEO4J_PASSWORD is unset

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
        resolved_uri = (
            uri or os.getenv("ENGRAMORY_GRAPH_URL") or os.getenv("NEO4J_URI") or DEFAULT_NEO4J_URI
        )
        self._uri: str = resolved_uri
        user: str = (
            (auth[0] if auth else None)
            or os.getenv("ENGRAMORY_GRAPH_USER")
            or os.getenv("NEO4J_USER")
            or DEFAULT_NEO4J_USER
        )
        password: str = (
            (auth[1] if auth else None)
            or os.getenv("ENGRAMORY_GRAPH_PASSWORD")
            or os.getenv("NEO4J_PASSWORD")
            or DEFAULT_NEO4J_PASSWORD
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
        drop_old = "DROP CONSTRAINT entity_id_unique IF EXISTS"
        query = (
            "CREATE CONSTRAINT entity_uid_unique IF NOT EXISTS "
            "FOR (n:Entity) REQUIRE n.uid IS UNIQUE"
        )
        async with self._driver.session() as session:
            try:
                await session.run(drop_old)
            except Exception as exc:
                logger.debug("Optional legacy constraint drop skipped: %s", exc)
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
        tenant_id = clean_props.get("tenant_id", "default")
        node_uid = f"{tenant_id}:{id}"

        # Build Cypher with sanitized labels appended
        label_str = ":Entity"
        for label in sanitized_labels:
            if label != "Entity":
                label_str += f":`{label}`"

        project_id = clean_props.get("project_id")
        query = f"""
        MERGE (n:Entity {{uid: $uid}})
        ON CREATE SET n.id = $id, n.tenant_id = $tenant_id, n{label_str}, n += $props
        ON MATCH SET n += CASE
            WHEN n.project_id IS NULL OR $project_id IS NULL OR n.project_id = $project_id
            THEN $props
            ELSE {{}}
        END
        FOREACH (_ IN CASE
            WHEN n.project_id IS NULL OR $project_id IS NULL OR n.project_id = $project_id
            THEN [1]
            ELSE []
        END | SET n{label_str})
        """
        async with self._driver.session() as session:
            await session.run(
                query,
                {
                    "uid": node_uid,
                    "id": id,
                    "tenant_id": tenant_id,
                    "project_id": project_id,
                    "props": clean_props,
                },
            )

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
        tenant_id = clean_props.get("tenant_id", "default")
        project_id = clean_props.get("project_id")
        src_uid = f"{tenant_id}:{src}"
        dst_uid = f"{tenant_id}:{dst}"

        query = f"""
        MERGE (a:Entity {{uid: $src_uid}})
        ON CREATE SET a.id = $src, a.tenant_id = $tenant_id, a.project_id = $project_id
        MERGE (b:Entity {{uid: $dst_uid}})
        ON CREATE SET b.id = $dst, b.tenant_id = $tenant_id, b.project_id = $project_id
        WITH a, b
        WHERE (a.tenant_id = $tenant_id OR ($tenant_id = 'default' AND a.tenant_id IS NULL))
          AND (b.tenant_id = $tenant_id OR ($tenant_id = 'default' AND b.tenant_id IS NULL))
          AND ($project_id IS NULL OR a.project_id IS NULL OR a.project_id = $project_id)
          AND ($project_id IS NULL OR b.project_id IS NULL OR b.project_id = $project_id)
        MERGE (a)-[r:`{sanitized_rel}`]->(b)
        ON CREATE SET r.project_id = $project_id, r += $props
        ON MATCH SET
            r.project_id = CASE
                WHEN r.project_id IS NULL OR $project_id IS NULL
                THEN coalesce($project_id, r.project_id)
                ELSE r.project_id
            END,
            r += CASE
                WHEN r.project_id IS NULL OR $project_id IS NULL OR r.project_id = $project_id
                THEN $props
                ELSE {{}}
            END
        """
        async with self._driver.session() as session:
            await session.run(
                query,
                {
                    "src_uid": src_uid,
                    "dst_uid": dst_uid,
                    "src": src,
                    "dst": dst,
                    "tenant_id": tenant_id,
                    "project_id": project_id,
                    "props": clean_props,
                },
            )

    async def neighbors(
        self,
        *,
        id: str,
        rel: str | None = None,
        depth: int = 1,
        tenant_id: str = "default",
        project_id: str | None = None,
    ) -> Sequence[Mapping[str, Any]]:
        """Return nodes reachable from `id` within `depth` hops (bounded traversal)."""
        safe_depth = max(1, min(depth, 5))
        if rel:
            sanitized_rel = _sanitize_ident(rel, "relationship type")
            rel_pattern = f":`{sanitized_rel}`*1..{safe_depth}"
        else:
            rel_pattern = f"*1..{safe_depth}"

        node_uid = f"{tenant_id}:{id}"
        prefixed_id = (
            f"{project_id}:{id}" if project_id and not id.startswith(f"{project_id}:") else id
        )
        prefixed_uid = f"{tenant_id}:{prefixed_id}"
        query = f"""
        MATCH (n:Entity)
        WHERE (
            n.uid = $uid
            OR n.uid = $prefixed_uid
            OR (
                (n.id = $id OR n.id = $prefixed_id)
                AND (n.tenant_id = $tenant_id OR ($tenant_id = 'default' AND n.tenant_id IS NULL))
            )
        )
        MATCH p = (n)-[{rel_pattern}]-(m:Entity)
        WHERE m <> n
          AND (m.tenant_id = $tenant_id OR ($tenant_id = 'default' AND m.tenant_id IS NULL))
          AND ALL(
              x IN nodes(p)
              WHERE (x.tenant_id = $tenant_id OR ($tenant_id = 'default' AND x.tenant_id IS NULL))
                AND ($project_id IS NULL OR x.project_id IS NULL OR x.project_id = $project_id)
          )
        RETURN DISTINCT m.id AS id, labels(m) AS labels, properties(m) AS properties
        """
        async with self._driver.session() as session:
            result = await session.run(
                query,
                {
                    "uid": node_uid,
                    "prefixed_uid": prefixed_uid,
                    "id": id,
                    "prefixed_id": prefixed_id,
                    "tenant_id": tenant_id,
                    "project_id": project_id,
                },
            )
            records = await result.data()
            return [
                {
                    "id": record["id"],
                    "labels": [lbl for lbl in record["labels"] if lbl != "Entity"],
                    "properties": record["properties"],
                }
                for record in records
            ]

    async def ancestors(
        self,
        *,
        id: str,
        rel: str | None = None,
        depth: int = 6,
        tenant_id: str = "default",
        project_id: str | None = None,
    ) -> Sequence[Mapping[str, Any]]:
        """Return upstream ancestor nodes reachable from `id` (directed out-edges)."""
        safe_depth = max(1, min(depth, 10))
        if rel:
            sanitized_rel = _sanitize_ident(rel, "relationship type")
            rel_pattern = f":`{sanitized_rel}`*1..{safe_depth}"
        else:
            rel_pattern = f"*1..{safe_depth}"

        src_uid = f"{tenant_id}:{id}"
        prefixed_id = (
            f"{project_id}:{id}" if project_id and not id.startswith(f"{project_id}:") else id
        )
        prefixed_uid = f"{tenant_id}:{prefixed_id}"
        query = f"""
        MATCH (n:Entity)
        WHERE (
            n.uid = $uid
            OR n.uid = $prefixed_uid
            OR (
                (n.id = $id OR n.id = $prefixed_id)
                AND (n.tenant_id = $tenant_id OR ($tenant_id = 'default' AND n.tenant_id IS NULL))
            )
        )
        MATCH p = (n)-[{rel_pattern}]->(m:Entity)
        WHERE m <> n
          AND (m.tenant_id = $tenant_id OR ($tenant_id = 'default' AND m.tenant_id IS NULL))
          AND ALL(
              x IN nodes(p)
              WHERE (x.tenant_id = $tenant_id OR ($tenant_id = 'default' AND x.tenant_id IS NULL))
                AND ($project_id IS NULL OR x.project_id IS NULL OR x.project_id = $project_id)
          )
        RETURN DISTINCT m.id AS id, labels(m) AS labels, properties(m) AS properties
        """
        async with self._driver.session() as session:
            result = await session.run(
                query,
                {
                    "uid": src_uid,
                    "prefixed_uid": prefixed_uid,
                    "id": id,
                    "prefixed_id": prefixed_id,
                    "tenant_id": tenant_id,
                    "project_id": project_id,
                },
            )
            records = await result.data()
            return [
                {
                    "id": record["id"],
                    "labels": [lbl for lbl in record["labels"] if lbl != "Entity"],
                    "properties": record["properties"],
                }
                for record in records
            ]

    async def descendants(
        self,
        *,
        id: str,
        rel: str | None = None,
        depth: int = 6,
        tenant_id: str = "default",
        project_id: str | None = None,
    ) -> Sequence[Mapping[str, Any]]:
        """Return downstream descendant nodes reachable from `id` (directed in-edges)."""
        safe_depth = max(1, min(depth, 10))
        if rel:
            sanitized_rel = _sanitize_ident(rel, "relationship type")
            rel_pattern = f":`{sanitized_rel}`*1..{safe_depth}"
        else:
            rel_pattern = f"*1..{safe_depth}"

        dst_uid = f"{tenant_id}:{id}"
        prefixed_id = (
            f"{project_id}:{id}" if project_id and not id.startswith(f"{project_id}:") else id
        )
        prefixed_uid = f"{tenant_id}:{prefixed_id}"
        query = f"""
        MATCH (n:Entity)
        WHERE (
            n.uid = $uid
            OR n.uid = $prefixed_uid
            OR (
                (n.id = $id OR n.id = $prefixed_id)
                AND (n.tenant_id = $tenant_id OR ($tenant_id = 'default' AND n.tenant_id IS NULL))
            )
        )
        MATCH p = (m:Entity)-[{rel_pattern}]->(n)
        WHERE m <> n
          AND (m.tenant_id = $tenant_id OR ($tenant_id = 'default' AND m.tenant_id IS NULL))
          AND ALL(
              x IN nodes(p)
              WHERE (x.tenant_id = $tenant_id OR ($tenant_id = 'default' AND x.tenant_id IS NULL))
                AND ($project_id IS NULL OR x.project_id IS NULL OR x.project_id = $project_id)
          )
        RETURN DISTINCT m.id AS id, labels(m) AS labels, properties(m) AS properties
        """
        async with self._driver.session() as session:
            result = await session.run(
                query,
                {
                    "uid": dst_uid,
                    "prefixed_uid": prefixed_uid,
                    "id": id,
                    "prefixed_id": prefixed_id,
                    "tenant_id": tenant_id,
                    "project_id": project_id,
                },
            )
            records = await result.data()
            return [
                {
                    "id": record["id"],
                    "labels": [lbl for lbl in record["labels"] if lbl != "Entity"],
                    "properties": record["properties"],
                }
                for record in records
            ]

    async def delete_node(self, *, id: str, tenant_id: str = "default") -> None:
        """Delete a node and its attached relationships from Neo4j."""
        node_uid = f"{tenant_id}:{id}"
        query = """
        MATCH (n:Entity {uid: $uid})
        DETACH DELETE n
        """
        async with self._driver.session() as session:
            await session.run(query, {"uid": node_uid})

    async def delete_edge(
        self, *, src: str, dst: str, rel: str | None = None, tenant_id: str = "default"
    ) -> None:
        """Delete relationship(s) between src and dst from Neo4j."""
        src_uid = f"{tenant_id}:{src}"
        dst_uid = f"{tenant_id}:{dst}"
        if rel:
            sanitized_rel = _sanitize_ident(rel, "relationship type")
            rel_clause = f"[r:`{sanitized_rel}`]"
        else:
            rel_clause = "[r]"

        query = f"""
        MATCH (a:Entity {{uid: $src_uid}})-{rel_clause}->(b:Entity {{uid: $dst_uid}})
        DELETE r
        """
        async with self._driver.session() as session:
            await session.run(query, {"src_uid": src_uid, "dst_uid": dst_uid})

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
        active_uids = [f"{tenant_id}:{i}" for i in active_ids]
        where_clauses = [
            "(n.tenant_id = $tenant_id OR ($tenant_id = 'default' AND n.tenant_id IS NULL))",
            "NOT n.uid IN $active_uids",
        ]
        params: dict[str, Any] = {"tenant_id": tenant_id, "active_uids": active_uids}
        if project_id:
            where_clauses.append("n.project_id = $project_id")
            params["project_id"] = project_id

        query = f"""
        MATCH (n:Entity)
        WHERE {" AND ".join(where_clauses)}
        WITH collect(n) AS nodes, count(n) AS cnt
        FOREACH (x IN nodes | DETACH DELETE x)
        RETURN cnt
        """
        async with self._driver.session() as session:
            result = await session.run(query, params)
            rec = await result.single()
            return int(rec["cnt"]) if rec and rec["cnt"] is not None else 0
