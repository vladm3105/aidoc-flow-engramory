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
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from typing import Any

import psycopg
from psycopg.rows import dict_row

from engramory.core.models import Episode, KBSection, Memory

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

    async def get_memory(self, memory_id: str) -> Memory:
        """Fetch one memory by id regardless of status (audit/supersede chains)."""
        async with self._conn() as conn:
            row = await (
                await conn.execute(
                    f"SELECT {_MEMORY_COLUMNS} FROM memories WHERE id = %(id)s",
                    {"id": memory_id},
                )
            ).fetchone()
            if row is None:
                raise KeyError(memory_id)
            return _hydrate_memory(row)

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

    async def get_audit_records(
        self, *, tenant_id: str, limit: int = 20
    ) -> list[dict[str, Any]]:
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

    async def count_episodes(self, *, tenant_id: str) -> int:
        return await self._count("episodes", tenant_id)

    async def count_kb_sections(self, *, tenant_id: str) -> int:
        return await self._count("kb_sections", tenant_id)

    async def _count(self, table: str, tenant_id: str) -> int:
        # Security guard, not an assert: asserts vanish under `python -O`,
        # and this is the only barrier before f-string SQL interpolation.
        if table not in {"episodes", "kb_sections"}:
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
