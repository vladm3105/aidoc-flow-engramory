"""Repository — relational persistence for the canonical store (SPEC-02).

Uses the Postgres driver (psycopg) directly: Postgres is the one-way spine
(ADR-01); the relational store is deliberately NOT behind a port (SPEC-06).
``get_memories`` ranks by cosine similarity when ``query_vec`` is given (the
pgvector projection lives in the same canonical table; embeddings are written
via the VectorPort dev adapter) and by recency otherwise.

Failure mode: ``StoreUnavailable`` wraps driver connectivity errors so callers
can fail closed with a retryable error (EARS.01.03.c900).
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import asynccontextmanager
from typing import Any

import psycopg
from psycopg.rows import dict_row

from engramory.core.models import (
    AgentProfile,
    Episode,
    GraphEntity,
    GraphRelation,
    KBSection,
    Memory,
)

# memory_retrievals.feedback CHECK domain (migration 0003) — enforced here so a
# bad outcome fails before it reaches Postgres.
FEEDBACK_OUTCOMES = frozenset({"useful", "not_useful", "harmful"})

# ADR-07 visibility ladder, one clause per level. get_memories() includes ONLY
# the levels the caller was actually granted — an ungranted level (e.g. 'space')
# must never leak into results even inside the right tenant.
_SCOPE_CLAUSES = {
    "agent": "(scope = 'agent' AND agent_id = %(agent_id)s)",
    "project": "(scope = 'project' AND project_id IS NOT NULL AND project_id = %(project_id)s)",
    "domain": "(scope = 'domain' AND domain_id IS NOT NULL AND domain_id = %(domain_id)s)",
    "space": "scope = 'space'",
}


def _visibility_sql(scopes: Sequence[str] | None) -> str:
    """Tenant wall + SPEC-03 active-only default (idx_memories_tenant_live) +
    the granted subset of the ladder."""
    levels = tuple(scopes) if scopes is not None else tuple(_SCOPE_CLAUSES)
    unknown = set(levels) - set(_SCOPE_CLAUSES)
    if not levels or unknown:
        raise ValueError(f"invalid visibility scopes: {sorted(unknown) or 'empty'}")
    ladder = " OR ".join(_SCOPE_CLAUSES[level] for level in levels)
    return f"""
    tenant_id = %(tenant_id)s
    AND valid_to IS NULL
    AND status = 'active'
    AND ({ladder})
"""


_MEMORY_COLUMNS = (
    "id, agent_id, project_id, domain_id, tenant_id, scope, type, content_raw, "
    "summary, embedding_model, embedding_dims, provenance, confidence, status, "
    "source_trust, valid_from, valid_to, supersedes"
)  # explicit list: never drag embedding/ts_lex over the wire on reads


class StoreUnavailable(RuntimeError):
    """The canonical store cannot be reached; callers fail closed (EARS.01.03.c900).

    Usually retryable (connection loss), but psycopg maps auth/config failures
    to OperationalError too — inspect ``__cause__`` before retrying forever.
    """


def vector_literal(embedding: Sequence[float]) -> str:
    """pgvector input literal: '[x1,x2,...]' (cast with ::vector at the call site)."""
    return "[" + ",".join(repr(float(x)) for x in embedding) + "]"


class Repository:
    """Typed data access over the canonical Postgres schema (migrations 0001..0003)."""

    def __init__(self, dsn: str) -> None:
        self._dsn = dsn

    @property
    def dsn(self) -> str:
        return self._dsn

    @asynccontextmanager
    async def _conn(self) -> AsyncIterator[psycopg.AsyncConnection[dict[str, Any]]]:
        try:
            conn = await psycopg.AsyncConnection.connect(self._dsn, row_factory=dict_row)
        except psycopg.OperationalError as exc:
            raise StoreUnavailable(str(exc)) from exc
        try:
            async with conn:
                yield conn
        except psycopg.OperationalError as exc:
            raise StoreUnavailable(str(exc)) from exc
        finally:
            # psycopg's __exit__ skips close() when COMMIT itself raises —
            # without this the connection would leak until GC.
            if not conn.closed:
                await conn.close()

    async def add_episode(self, episode: Episode) -> str:
        """Persist idempotently by content hash within scope (BDD.01.03.c900).

        Returns the episode id — the existing row's id on duplicate submission.
        """
        async with self._conn() as conn:
            row = await (
                await conn.execute(
                    """
                    INSERT INTO episodes
                        (agent_id, project_id, domain_id, tenant_id, kind,
                         content_raw, content_hash, metadata)
                    VALUES (%(agent_id)s, %(project_id)s, %(domain_id)s, %(tenant_id)s,
                            %(kind)s, %(content_raw)s, %(content_hash)s, %(metadata)s)
                    ON CONFLICT (tenant_id, agent_id, coalesce(project_id, ''), content_hash)
                        DO NOTHING
                    RETURNING id
                    """,
                    _episode_params(episode),
                )
            ).fetchone()
            if row is not None:
                return str(row["id"])
            existing = await (
                await conn.execute(
                    """
                    SELECT id FROM episodes
                    WHERE tenant_id = %(tenant_id)s AND agent_id = %(agent_id)s
                      AND coalesce(project_id, '') = coalesce(%(project_id)s, '')
                      AND content_hash = %(content_hash)s
                    """,
                    _episode_params(episode),
                )
            ).fetchone()
            if existing is None:  # unreachable: episodes are never hard-deleted
                raise RuntimeError("episode vanished between conflicting INSERT and SELECT")
            return str(existing["id"])

    async def upsert_memory(self, memory: Memory) -> str:
        """Insert a distilled memory; soft-supersede the predecessor in the same
        transaction when ``memory.supersedes`` is set (never delete)."""
        async with self._conn() as conn:
            row = await (
                await conn.execute(
                    """
                    INSERT INTO memories
                        (agent_id, project_id, domain_id, tenant_id, scope, type,
                         content_raw, summary, embedding_model, embedding_dims,
                         provenance, confidence, status, source_trust, supersedes)
                    VALUES (%(agent_id)s, %(project_id)s, %(domain_id)s, %(tenant_id)s,
                            %(scope)s, %(type)s, %(content_raw)s, %(summary)s,
                            %(embedding_model)s, %(embedding_dims)s, %(provenance)s,
                            %(confidence)s, %(status)s, %(source_trust)s, %(supersedes)s)
                    RETURNING id
                    """,
                    _memory_params(memory),
                )
            ).fetchone()
            assert row is not None
            if memory.supersedes is not None:
                # tenant_id guard: the tenant wall (ADR-07) holds on the write
                # path too — a supersede can never end-date a foreign-tenant row.
                await conn.execute(
                    """
                    UPDATE memories
                    SET valid_to = now(), status = 'superseded'
                    WHERE id = %(id)s AND tenant_id = %(tenant_id)s AND valid_to IS NULL
                    """,
                    {"id": memory.supersedes, "tenant_id": memory.tenant_id},
                )
            return str(row["id"])

    async def get_memory(self, memory_id: str, *, tenant_id: str) -> Memory:
        """Fetch one memory by id regardless of status (audit/supersede chains).

        Tenant-guarded: a foreign tenant's id raises KeyError exactly like a
        missing one — existence is never disclosed across the wall (ADR-07).
        """
        async with self._conn() as conn:
            row = await (
                await conn.execute(
                    f"""
                    SELECT {_MEMORY_COLUMNS} FROM memories
                    WHERE id = %(id)s AND tenant_id = %(tenant_id)s
                    """,
                    {"id": memory_id, "tenant_id": tenant_id},
                )
            ).fetchone()
            if row is None:
                raise KeyError(memory_id)
            return _hydrate_memory(row)

    async def retire_memory(self, memory_id: str, *, tenant_id: str) -> None:
        """Soft-retire (SPEC-01 memory_forget): end-date + mark superseded;
        content and provenance are retained, never deleted. Tenant-guarded;
        KeyError when the id is missing, foreign, or already retired."""
        async with self._conn() as conn:
            cur = await conn.execute(
                """
                UPDATE memories
                SET valid_to = now(), status = 'superseded'
                WHERE id = %(id)s AND tenant_id = %(tenant_id)s AND valid_to IS NULL
                """,
                {"id": memory_id, "tenant_id": tenant_id},
            )
            if cur.rowcount != 1:
                raise KeyError(memory_id)

    async def record_feedback(self, retrieval_id: str, outcome: str, *, tenant_id: str) -> None:
        """Stamp a retrieval outcome (SPEC-01 memory_feedback -> SPEC-04
        confidence rule). Tenant-guarded; KeyError when the retrieval is
        missing or foreign."""
        if outcome not in FEEDBACK_OUTCOMES:
            raise ValueError(
                f"feedback outcome must be one of {sorted(FEEDBACK_OUTCOMES)}: {outcome!r}"
            )
        async with self._conn() as conn:
            cur = await conn.execute(
                """
                UPDATE memory_retrievals
                SET feedback = %(outcome)s, feedback_at = now()
                WHERE id = %(id)s AND tenant_id = %(tenant_id)s
                """,
                {"id": retrieval_id, "outcome": outcome, "tenant_id": tenant_id},
            )
            if cur.rowcount != 1:
                raise KeyError(retrieval_id)

    async def get_retrieval_feedback(self, *, tenant_id: str, memory_id: str) -> list[str | None]:
        """Feedback values for a memory's retrievals, oldest first (tests/eval)."""
        async with self._conn() as conn:
            rows = await (
                await conn.execute(
                    """
                    SELECT feedback FROM memory_retrievals
                    WHERE tenant_id = %(tenant_id)s AND memory_id = %(memory_id)s
                    ORDER BY retrieved_at, id
                    """,
                    {"tenant_id": tenant_id, "memory_id": memory_id},
                )
            ).fetchall()
            return [row["feedback"] for row in rows]

    async def get_agent_profile(self, agent_id: str) -> AgentProfile | None:
        """L3 identity row, or None when the agent has no profile yet.

        NOTE: agent_profiles is keyed by agent_id alone (no tenant column) —
        acceptable only under the single-tenant dev tier; revisit with the
        gateway (PLAN-002 Phase 1.3 stated assumption).
        """
        async with self._conn() as conn:
            row = await (
                await conn.execute(
                    """
                    SELECT agent_id, display_name, standing_preferences
                    FROM agent_profiles WHERE agent_id = %(agent_id)s
                    """,
                    {"agent_id": agent_id},
                )
            ).fetchone()
            if row is None:
                return None
            return AgentProfile(
                agent_id=row["agent_id"],
                display_name=row["display_name"],
                standing_preferences=row["standing_preferences"] or {},
            )

    async def upsert_agent_profile(self, profile: AgentProfile) -> None:
        """Create/update the L3 identity row (updated_at kept by trigger)."""
        async with self._conn() as conn:
            await conn.execute(
                """
                INSERT INTO agent_profiles (agent_id, display_name, standing_preferences)
                VALUES (%(agent_id)s, %(display_name)s, %(standing_preferences)s)
                ON CONFLICT (agent_id) DO UPDATE
                    SET display_name = EXCLUDED.display_name,
                        standing_preferences = EXCLUDED.standing_preferences
                """,
                {
                    "agent_id": profile.agent_id,
                    "display_name": profile.display_name,
                    "standing_preferences": json.dumps(dict(profile.standing_preferences)),
                },
            )

    async def get_unreflected_episodes(
        self, *, tenant_id: str, agent_id: str, limit: int = 500
    ) -> list[Episode]:
        """Episodes not yet projected into memories (interim reflect pass;
        PLAN-002 Phase 1.4). Projection linkage: memories.provenance->>'episode_id'."""
        async with self._conn() as conn:
            rows = await (
                await conn.execute(
                    """
                    SELECT id, agent_id, project_id, domain_id, tenant_id, kind,
                           content_raw, metadata
                    FROM episodes e
                    WHERE tenant_id = %(tenant_id)s AND agent_id = %(agent_id)s
                      AND NOT EXISTS (
                          SELECT 1 FROM memories m
                          WHERE m.tenant_id = e.tenant_id
                            AND m.provenance->>'episode_id' = e.id::text
                      )
                    ORDER BY ts, id
                    LIMIT %(limit)s
                    """,
                    {"tenant_id": tenant_id, "agent_id": agent_id, "limit": limit},
                )
            ).fetchall()
            return [
                Episode(
                    agent_id=row["agent_id"],
                    content_raw=row["content_raw"],
                    project_id=row["project_id"],
                    domain_id=row["domain_id"],
                    tenant_id=row["tenant_id"],
                    kind=row["kind"],
                    metadata=row["metadata"] or {},
                    id=str(row["id"]),
                )
                for row in rows
            ]

    async def get_active_agents_with_unreflected_episodes(
        self, *, tenant_id: str | None = None
    ) -> list[tuple[str, str]]:
        """Distinct (tenant_id, agent_id) pairs carrying unreflected episodes."""
        async with self._conn() as conn:
            rows = await (
                await conn.execute(
                    """
                    SELECT DISTINCT tenant_id, agent_id
                    FROM episodes e
                    WHERE (%(tenant_id)s::text IS NULL OR tenant_id = %(tenant_id)s::text)
                      AND NOT EXISTS (
                          SELECT 1 FROM memories m
                          WHERE m.tenant_id = e.tenant_id
                            AND m.provenance->>'episode_id' = e.id::text
                      )
                    """,
                    {"tenant_id": tenant_id},
                )
            ).fetchall()
            return [(str(row["tenant_id"]), str(row["agent_id"])) for row in rows]

    async def get_memories(
        self,
        *,
        tenant_id: str,
        agent_id: str | None = None,
        project_id: str | None = None,
        domain_id: str | None = None,
        k: int = 8,
        query_vec: Sequence[float] | None = None,
        scopes: Sequence[str] | None = None,
    ) -> list[Memory]:
        """Visible, live, active memories per the ADR-07 ladder.

        ``scopes`` restricts visibility to the granted ladder levels; None
        means the full ladder (repo-level tooling — the access surface always
        passes the caller's granted subset).

        Ranked by cosine similarity to ``query_vec`` when given (rows without
        an embedding projection are excluded — they cannot be ranked), by
        recency otherwise.
        """
        params: dict[str, Any] = {
            "tenant_id": tenant_id,
            "agent_id": agent_id,
            "project_id": project_id,
            "domain_id": domain_id,
            "k": k,
        }
        if query_vec is not None:
            extra = "AND embedding IS NOT NULL"
            order = "embedding <=> %(qv)s::vector"
            params["qv"] = vector_literal(query_vec)
        else:
            extra = ""
            order = "valid_from DESC"
        async with self._conn() as conn:
            rows = await (
                await conn.execute(
                    f"""
                    SELECT {_MEMORY_COLUMNS} FROM memories
                    WHERE {_visibility_sql(scopes)} {extra}
                    ORDER BY {order}
                    LIMIT %(k)s
                    """,
                    params,
                )
            ).fetchall()
            return [_hydrate_memory(row) for row in rows]

    async def upsert_kb_section(self, section: KBSection) -> str:
        """Insert/update a knowledge section keyed by (tenant, doc, citation, version)."""
        async with self._conn() as conn:
            row = await (
                await conn.execute(
                    """
                    INSERT INTO kb_sections
                        (tenant_id, project_id, domain_id, scope, doc_id, citation,
                         text, version)
                    VALUES (%(tenant_id)s, %(project_id)s, %(domain_id)s, %(scope)s,
                            %(doc_id)s, %(citation)s, %(text)s, %(version)s)
                    ON CONFLICT (tenant_id, doc_id, citation, version)
                        DO UPDATE SET text = EXCLUDED.text
                    RETURNING id
                    """,
                    {
                        "tenant_id": section.tenant_id,
                        "project_id": section.project_id,
                        "domain_id": section.domain_id,
                        "scope": section.scope,
                        "doc_id": section.doc_id,
                        "citation": section.citation,
                        "text": section.text,
                        "version": section.version,
                    },
                )
            ).fetchone()
            assert row is not None
            return str(row["id"])

    async def set_kb_section_embedding(
        self,
        section_id: str,
        *,
        embedding: Sequence[float],
        embedding_model: str = "bge-small-en-v1.5",
    ) -> None:
        """Store vector projection for a kb_section (rebuildable projection per ADR-05)."""
        async with self._conn() as conn:
            await conn.execute(
                """
                UPDATE kb_sections
                SET embedding = %(vec)s::vector,
                    embedding_model = %(model)s,
                    embedding_dims = %(dims)s
                WHERE id = %(id)s
                """,
                {
                    "vec": vector_literal(embedding),
                    "model": embedding_model,
                    "dims": len(embedding),
                    "id": section_id,
                },
            )

    async def record_audit(
        self,
        *,
        tenant_id: str,
        agent_id: str,
        project_id: str | None,
        action: str,
        allowed: bool,
        reason: str,
    ) -> None:
        """Write an AuditRecord (SPEC-01; EARS.01.03.b050 — allow AND deny)."""
        async with self._conn() as conn:
            await conn.execute(
                """
                INSERT INTO audit_records
                    (tenant_id, agent_id, project_id, action, allowed, reason)
                VALUES (%(tenant_id)s, %(agent_id)s, %(project_id)s, %(action)s,
                        %(allowed)s, %(reason)s)
                """,
                {
                    "tenant_id": tenant_id,
                    "agent_id": agent_id,
                    "project_id": project_id,
                    "action": action,
                    "allowed": allowed,
                    "reason": reason,
                },
            )

    async def get_audit_records(self, *, tenant_id: str, limit: int = 20) -> list[dict[str, Any]]:
        """Most-recent-first audit rows for a tenant (tests/operators)."""
        async with self._conn() as conn:
            return await (
                await conn.execute(
                    """
                    SELECT agent_id, project_id, action, allowed, reason, ts
                    FROM audit_records
                    WHERE tenant_id = %(t)s
                    ORDER BY ts DESC, id DESC
                    LIMIT %(limit)s
                    """,
                    {"t": tenant_id, "limit": limit},
                )
            ).fetchall()

    async def log_retrievals(
        self,
        *,
        memory_ids: Sequence[str],
        tenant_id: str,
        agent_id: str,
        project_id: str | None,
    ) -> list[str]:
        """Record hits handed to an agent (memory_retrievals) and bump the usage
        signals — ONE connection/transaction for the whole batch (never N+1).
        Returns retrieval_ids in memory_ids order, for memory_feedback."""
        if not memory_ids:
            return []
        retrieval_ids: list[str] = []
        async with self._conn() as conn:
            for memory_id in memory_ids:
                row = await (
                    await conn.execute(
                        """
                        INSERT INTO memory_retrievals
                            (memory_id, tenant_id, agent_id, project_id)
                        VALUES (%(memory_id)s, %(tenant_id)s, %(agent_id)s, %(project_id)s)
                        RETURNING id
                        """,
                        {
                            "memory_id": memory_id,
                            "tenant_id": tenant_id,
                            "agent_id": agent_id,
                            "project_id": project_id,
                        },
                    )
                ).fetchone()
                assert row is not None
                retrieval_ids.append(str(row["id"]))
            # tenant guard: defense-in-depth — this public method must never
            # bump a foreign tenant's usage signals.
            await conn.execute(
                """
                UPDATE memories
                SET retrieval_count = retrieval_count + 1, last_retrieved_at = now()
                WHERE id = ANY(%(ids)s::uuid[]) AND tenant_id = %(tenant_id)s
                """,
                {"ids": list(memory_ids), "tenant_id": tenant_id},
            )
        return retrieval_ids

    async def upsert_graph_entity(self, entity: GraphEntity) -> None:
        """Insert or update a canonical graph entity (ADR-01/03/05)."""
        async with self._conn() as conn:
            await conn.execute(
                """
                INSERT INTO graph_entities
                    (id, tenant_id, domain_id, entity_type, name, properties, updated_at)
                VALUES
                    (%(id)s, %(tenant_id)s, %(domain_id)s, %(entity_type)s,
                     %(name)s, %(properties)s, NOW())
                ON CONFLICT (tenant_id, id)
                    DO UPDATE SET
                        domain_id = EXCLUDED.domain_id,
                        entity_type = EXCLUDED.entity_type,
                        name = EXCLUDED.name,
                        properties = EXCLUDED.properties,
                        updated_at = NOW()
                """,
                {
                    "id": entity.id,
                    "tenant_id": entity.tenant_id,
                    "domain_id": entity.domain_id,
                    "entity_type": entity.entity_type,
                    "name": entity.name,
                    "properties": json.dumps(dict(entity.properties)),
                },
            )

    async def upsert_graph_relation(self, relation: GraphRelation) -> str:
        """Insert or update a canonical graph relationship (ADR-01/03/05)."""
        async with self._conn() as conn:
            row = await (
                await conn.execute(
                    """
                    INSERT INTO graph_relations (tenant_id, src_id, dst_id, rel_type, properties)
                    VALUES (%(tenant_id)s, %(src_id)s, %(dst_id)s, %(rel_type)s, %(properties)s)
                    ON CONFLICT (tenant_id, src_id, dst_id, rel_type)
                        DO UPDATE SET properties = EXCLUDED.properties
                    RETURNING id
                    """,
                    {
                        "tenant_id": relation.tenant_id,
                        "src_id": relation.src_id,
                        "dst_id": relation.dst_id,
                        "rel_type": relation.rel_type,
                        "properties": json.dumps(dict(relation.properties)),
                    },
                )
            ).fetchone()
            assert row is not None
            return str(row["id"])

    async def bulk_upsert_graph_entities(self, entities: Sequence[GraphEntity]) -> int:
        """Bulk insert or update canonical graph entities in a single transaction."""
        if not entities:
            return 0
        async with self._conn() as conn:
            async with conn.transaction():
                async with conn.cursor() as cur:
                    await cur.executemany(
                        """
                        INSERT INTO graph_entities
                            (id, tenant_id, domain_id, entity_type, name, properties, updated_at)
                        VALUES
                            (%(id)s, %(tenant_id)s, %(domain_id)s, %(entity_type)s,
                             %(name)s, %(properties)s, NOW())
                        ON CONFLICT (tenant_id, id)
                            DO UPDATE SET
                                domain_id = EXCLUDED.domain_id,
                                entity_type = EXCLUDED.entity_type,
                                name = EXCLUDED.name,
                                properties = EXCLUDED.properties,
                                updated_at = NOW()
                        """,
                        [
                            {
                                "id": e.id,
                                "tenant_id": e.tenant_id,
                                "domain_id": e.domain_id,
                                "entity_type": e.entity_type,
                                "name": e.name,
                                "properties": json.dumps(dict(e.properties)),
                            }
                            for e in entities
                        ],
                    )
        return len(entities)

    async def bulk_upsert_graph_relations(self, relations: Sequence[GraphRelation]) -> int:
        """Bulk insert or update canonical graph relationships in a single transaction."""
        if not relations:
            return 0
        async with self._conn() as conn:
            async with conn.transaction():
                async with conn.cursor() as cur:
                    await cur.executemany(
                        """
                        INSERT INTO graph_relations (
                            tenant_id, src_id, dst_id, rel_type, properties
                        )
                        VALUES (%(tenant_id)s, %(src_id)s, %(dst_id)s, %(rel_type)s, %(properties)s)
                        ON CONFLICT (tenant_id, src_id, dst_id, rel_type)
                            DO UPDATE SET properties = EXCLUDED.properties
                        """,
                        [
                            {
                                "tenant_id": r.tenant_id,
                                "src_id": r.src_id,
                                "dst_id": r.dst_id,
                                "rel_type": r.rel_type,
                                "properties": json.dumps(dict(r.properties)),
                            }
                            for r in relations
                        ],
                    )
        return len(relations)

    async def delete_graph_entity(self, id: str, *, tenant_id: str = "default") -> None:
        """Delete a canonical graph entity. Foreign keys cascade-delete relations."""
        async with self._conn() as conn:
            cur = await conn.execute(
                """
                DELETE FROM graph_entities
                WHERE tenant_id = %(tenant_id)s AND id = %(id)s
                """,
                {"tenant_id": tenant_id, "id": id},
            )
            if cur.rowcount != 1:
                raise KeyError(id)

    async def delete_graph_relation(
        self, *, src_id: str, dst_id: str, rel_type: str, tenant_id: str = "default"
    ) -> None:
        """Delete a canonical graph relation."""
        async with self._conn() as conn:
            cur = await conn.execute(
                """
                DELETE FROM graph_relations
                WHERE tenant_id = %(tenant_id)s AND src_id = %(src_id)s
                  AND dst_id = %(dst_id)s AND rel_type = %(rel_type)s
                """,
                {
                    "tenant_id": tenant_id,
                    "src_id": src_id,
                    "dst_id": dst_id,
                    "rel_type": rel_type,
                },
            )
            if cur.rowcount != 1:
                raise KeyError(f"{src_id}->{dst_id}:{rel_type}")

    async def get_graph_entity(self, id: str, *, tenant_id: str = "default") -> GraphEntity | None:
        """Retrieve a canonical graph entity by id and tenant_id."""
        async with self._conn() as conn:
            row = await (
                await conn.execute(
                    """
                    SELECT id, tenant_id, domain_id, entity_type, name, properties
                    FROM graph_entities
                    WHERE tenant_id = %(tenant_id)s AND id = %(id)s
                    """,
                    {"tenant_id": tenant_id, "id": id},
                )
            ).fetchone()
            if not row:
                return None
            return GraphEntity(
                id=row["id"],
                tenant_id=row["tenant_id"],
                domain_id=row["domain_id"],
                entity_type=row["entity_type"],
                name=row["name"],
                properties=row["properties"],
            )

    async def list_graph_entities(
        self, *, tenant_id: str = "default", entity_type: str | None = None
    ) -> list[GraphEntity]:
        """List canonical graph entities with optional entity_type filter."""
        clauses = ["tenant_id = %(tenant_id)s"]
        params: dict[str, Any] = {"tenant_id": tenant_id}
        if entity_type:
            clauses.append("entity_type = %(entity_type)s")
            params["entity_type"] = entity_type

        async with self._conn() as conn:
            rows = await (
                await conn.execute(
                    f"""
                    SELECT id, tenant_id, domain_id, entity_type, name, properties
                    FROM graph_entities
                    WHERE {" AND ".join(clauses)}
                    ORDER BY id ASC
                    """,
                    params,
                )
            ).fetchall()
            return [
                GraphEntity(
                    id=row["id"],
                    tenant_id=row["tenant_id"],
                    domain_id=row["domain_id"],
                    entity_type=row["entity_type"],
                    name=row["name"],
                    properties=row["properties"],
                )
                for row in rows
            ]

    async def list_graph_relations(
        self, *, tenant_id: str = "default", entity_id: str | None = None
    ) -> list[GraphRelation]:
        """List canonical graph relations, optionally connected to entity_id."""
        clauses = ["tenant_id = %(tenant_id)s"]
        params: dict[str, Any] = {"tenant_id": tenant_id}
        if entity_id:
            clauses.append("(src_id = %(eid)s OR dst_id = %(eid)s)")
            params["eid"] = entity_id

        async with self._conn() as conn:
            rows = await (
                await conn.execute(
                    f"""
                    SELECT id, tenant_id, src_id, dst_id, rel_type, properties
                    FROM graph_relations
                    WHERE {" AND ".join(clauses)}
                    ORDER BY created_at ASC
                    """,
                    params,
                )
            ).fetchall()
            return [
                GraphRelation(
                    id=str(row["id"]),
                    tenant_id=row["tenant_id"],
                    src_id=row["src_id"],
                    dst_id=row["dst_id"],
                    rel_type=row["rel_type"],
                    properties=row["properties"],
                )
                for row in rows
            ]

    async def find_ancestors(
        self,
        *,
        id: str,
        tenant_id: str = "default",
        depth: int = 6,
    ) -> Sequence[Mapping[str, Any]]:
        """Return upstream ancestor nodes reachable from `id` (directed out-edges src -> dst)."""
        safe_depth = max(1, min(depth, 10))
        async with self._conn() as conn:
            rows = await (
                await conn.execute(
                    """
                    WITH RECURSIVE lineage AS (
                        SELECT dst_id, 1 AS depth, ARRAY[src_id] AS path
                        FROM graph_relations
                        WHERE tenant_id = %(tenant_id)s AND src_id = %(id)s
                        UNION ALL
                        SELECT r.dst_id, l.depth + 1, l.path || r.src_id
                        FROM graph_relations r
                        JOIN lineage l ON r.src_id = l.dst_id
                        WHERE r.tenant_id = %(tenant_id)s
                          AND l.depth < %(max_depth)s
                          AND NOT (r.dst_id = ANY(l.path))
                    )
                    SELECT DISTINCT e.id, e.entity_type, e.name, e.properties
                    FROM lineage l
                    JOIN graph_entities e ON e.tenant_id = %(tenant_id)s AND e.id = l.dst_id
                    WHERE e.id <> %(id)s
                    ORDER BY e.id ASC
                    """,
                    {"tenant_id": tenant_id, "id": id, "max_depth": safe_depth},
                )
            ).fetchall()
            return [
                {
                    "id": row["id"],
                    "labels": [row["entity_type"]],
                    "properties": {
                        "name": row["name"],
                        "tenant_id": tenant_id,
                        **(
                            row["properties"]
                            if isinstance(row["properties"], dict)
                            else json.loads(row["properties"])
                            if row["properties"]
                            else {}
                        ),
                    },
                }
                for row in rows
            ]

    async def find_descendants(
        self,
        *,
        id: str,
        tenant_id: str = "default",
        depth: int = 6,
    ) -> Sequence[Mapping[str, Any]]:
        """Return downstream descendant nodes reachable from `id` (directed in-edges dst <- src)."""
        safe_depth = max(1, min(depth, 10))
        async with self._conn() as conn:
            rows = await (
                await conn.execute(
                    """
                    WITH RECURSIVE blast AS (
                        SELECT src_id, 1 AS depth, ARRAY[dst_id] AS path
                        FROM graph_relations
                        WHERE tenant_id = %(tenant_id)s AND dst_id = %(id)s
                        UNION ALL
                        SELECT r.src_id, b.depth + 1, b.path || r.dst_id
                        FROM graph_relations r
                        JOIN blast b ON r.dst_id = b.src_id
                        WHERE r.tenant_id = %(tenant_id)s
                          AND b.depth < %(max_depth)s
                          AND NOT (r.src_id = ANY(b.path))
                    )
                    SELECT DISTINCT e.id, e.entity_type, e.name, e.properties
                    FROM blast b
                    JOIN graph_entities e ON e.tenant_id = %(tenant_id)s AND e.id = b.src_id
                    WHERE e.id <> %(id)s
                    ORDER BY e.id ASC
                    """,
                    {"tenant_id": tenant_id, "id": id, "max_depth": safe_depth},
                )
            ).fetchall()
            return [
                {
                    "id": row["id"],
                    "labels": [row["entity_type"]],
                    "properties": {
                        "name": row["name"],
                        "tenant_id": tenant_id,
                        **(
                            row["properties"]
                            if isinstance(row["properties"], dict)
                            else json.loads(row["properties"])
                            if row["properties"]
                            else {}
                        ),
                    },
                }
                for row in rows
            ]

    async def count_episodes(self, *, tenant_id: str) -> int:
        return await self._count("episodes", tenant_id)

    async def count_kb_sections(self, *, tenant_id: str) -> int:
        return await self._count("kb_sections", tenant_id)

    async def count_graph_entities(self, *, tenant_id: str = "default") -> int:
        return await self._count("graph_entities", tenant_id)

    async def count_graph_relations(self, *, tenant_id: str = "default") -> int:
        return await self._count("graph_relations", tenant_id)

    async def _count(self, table: str, tenant_id: str) -> int:
        # Security guard, not an assert: asserts vanish under `python -O`,
        # and this is the only barrier before f-string SQL interpolation.
        if table not in {"episodes", "kb_sections", "graph_entities", "graph_relations"}:
            raise ValueError(f"refusing to interpolate table name: {table!r}")
        async with self._conn() as conn:
            row = await (
                await conn.execute(
                    f"SELECT count(*) AS n FROM {table} WHERE tenant_id = %(t)s",
                    {"t": tenant_id},
                )
            ).fetchone()
            assert row is not None
            return int(row["n"])


def _episode_params(episode: Episode) -> dict[str, Any]:
    return {
        "agent_id": episode.agent_id,
        "project_id": episode.project_id,
        "domain_id": episode.domain_id,
        "tenant_id": episode.tenant_id,
        "kind": episode.kind,
        "content_raw": episode.content_raw,
        "content_hash": episode.content_hash,
        "metadata": json.dumps(dict(episode.metadata)),
    }


def _memory_params(memory: Memory) -> dict[str, Any]:
    return {
        "agent_id": memory.agent_id,
        "project_id": memory.project_id,
        "domain_id": memory.domain_id,
        "tenant_id": memory.tenant_id,
        "scope": memory.scope,
        "type": memory.type,
        "content_raw": memory.content_raw,
        "summary": memory.summary,
        "embedding_model": memory.embedding_model,
        "embedding_dims": memory.embedding_dims,
        "provenance": json.dumps(dict(memory.provenance)),
        "confidence": memory.confidence,
        "status": memory.status,
        "source_trust": memory.source_trust,
        "supersedes": memory.supersedes,
    }


def _hydrate_memory(row: dict[str, Any]) -> Memory:
    return Memory(
        content_raw=row["content_raw"],
        summary=row["summary"],
        type=row["type"],
        embedding_model=row["embedding_model"],
        provenance=row["provenance"],
        agent_id=row["agent_id"],
        project_id=row["project_id"],
        domain_id=row["domain_id"],
        tenant_id=row["tenant_id"],
        scope=row["scope"],
        confidence=row["confidence"],
        status=row["status"],
        source_trust=row["source_trust"],
        embedding_dims=row["embedding_dims"],
        valid_from=row["valid_from"],
        valid_to=row["valid_to"],
        supersedes=str(row["supersedes"]) if row["supersedes"] is not None else None,
        id=str(row["id"]),
    )
