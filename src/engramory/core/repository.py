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

import asyncio
import json
import weakref
from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import asynccontextmanager
from typing import Any, LiteralString, cast

import psycopg
from psycopg import sql
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool, PoolTimeout

from engramory.core.models import (
    AUTONOMY_TIERS,
    AgentProfile,
    Episode,
    GraphEntity,
    GraphRelation,
    KBDocument,
    KBRunbookStep,
    KBSection,
    Memory,
    ValidationError,
    thaw_mapping,
)

# memory_retrievals.feedback CHECK domain (migration 0003) — enforced here so a
# bad outcome fails before it reaches Postgres.
FEEDBACK_OUTCOMES = frozenset({"useful", "not_useful", "harmful"})
_FEEDBACK_TARGETS: dict[str, float] = {
    "useful": 1.0,
    "not_useful": 0.25,
    "harmful": 0.0,
}
EXPECTED_EMBEDDING_DIMS = 1536


def _compose_static_sql(template: LiteralString, **clauses: str) -> str:
    """Compose internal static SQL predicate clauses safely via psycopg.sql."""
    return (
        sql.SQL(template)
        .format(
            **{
                k: sql.SQL(v)  # type: ignore[arg-type] # reason: internal static clause
                for k, v in clauses.items()
            }
        )
        .as_string(None)
    )


def _validate_embedding_dims(embedding: Sequence[float], label: str = "embedding") -> None:
    """Enforce canonical pgvector vector(1536) dimensions before hitting Postgres."""
    if len(embedding) != EXPECTED_EMBEDDING_DIMS:
        raise ValidationError(
            f"{label} dimension mismatch: expected {EXPECTED_EMBEDDING_DIMS}, got {len(embedding)}"
        )


def _scope_flags(
    allowed_scopes: Sequence[str] | None, *, requested_scope: str | None = None
) -> dict[str, Any]:
    """Compute hierarchical visibility flags without granting unheld scopes."""
    if allowed_scopes is None:
        return {
            "inc_agent": requested_scope == "agent" or requested_scope is None,
            "inc_project": True,
            "inc_domain": requested_scope in ("domain", "space"),
            "inc_space": True,
        }
    granted = set(allowed_scopes)
    eff = requested_scope or ("space" if "space" in granted else "project")
    return {
        "inc_agent": "agent" in granted,
        "inc_project": "project" in granted or "domain" in granted or "space" in granted,
        "inc_domain": eff in ("domain", "space") and ("domain" in granted or "space" in granted),
        "inc_space": "space" in granted,
    }


def _kb_scope_where(alias: str = "") -> str:
    """Shared tenant/project/domain/agent/space SQL filter for KB reads (C5)."""
    p = f"{alias}." if alias else ""
    return f"""(
        (
            %(scope)s::text = 'space'
            AND %(inc_space)s
            AND (
                {p}scope IN ('domain', 'space')
                OR (
                    %(project_id)s::text IS NOT NULL
                    AND {p}scope = 'project'
                    AND {p}project_id = %(project_id)s
                )
            )
        )
        OR (
            %(scope)s::text = 'domain'
            AND %(inc_domain)s
            AND (
                (
                    {p}scope = 'domain'
                    AND {p}domain_id IS NOT NULL
                    AND {p}domain_id = %(domain_id)s
                )
                OR (%(inc_space)s AND {p}scope = 'space')
                OR (
                    %(project_id)s::text IS NOT NULL
                    AND {p}scope = 'project'
                    AND {p}project_id = %(project_id)s
                )
            )
        )
        OR (
            coalesce(%(scope)s::text, '') NOT IN ('space', 'domain')
            AND (
                (
                    %(inc_agent)s
                    AND %(agent_id)s::text IS NOT NULL
                    AND {p}scope = 'agent'
                    AND {p}agent_id = %(agent_id)s
                    AND (
                        %(project_id)s::text IS NULL
                        OR {p}project_id IS NULL
                        OR {p}project_id = %(project_id)s
                    )
                )
                OR (
                    %(inc_project)s
                    AND {p}scope = 'project'
                    AND (
                        %(project_id)s::text IS NULL
                        OR {p}project_id = %(project_id)s
                    )
                )
                OR (%(inc_space)s AND {p}scope = 'space')
            )
        )
    )"""


def _kb_conflict_ownership_where(table: str) -> str:
    """Strict ON CONFLICT ownership guard for kb_sections and kb_documents (C4)."""
    return f"""(
        ({table}.scope = 'space' AND EXCLUDED.scope = 'space')
        OR (
            {table}.scope = 'domain'
            AND (
                EXCLUDED.scope = 'space'
                OR (
                    EXCLUDED.scope = 'domain'
                    AND {table}.domain_id IS NOT NULL
                    AND {table}.domain_id = EXCLUDED.domain_id
                )
            )
        )
        OR (
            {table}.scope = 'project'
            AND (
                EXCLUDED.scope = 'space'
                OR (
                    EXCLUDED.scope = 'domain'
                    AND {table}.domain_id IS NOT NULL
                    AND {table}.domain_id = EXCLUDED.domain_id
                )
                OR (
                    EXCLUDED.scope = 'project'
                    AND (
                        ({table}.project_id IS NULL AND EXCLUDED.project_id IS NULL)
                        OR (
                            {table}.project_id IS NOT NULL
                            AND {table}.project_id = EXCLUDED.project_id
                        )
                    )
                )
            )
        )
        OR (
            {table}.scope = 'agent'
            AND EXCLUDED.scope = 'agent'
            AND {table}.agent_id = EXCLUDED.agent_id
            AND (
                ({table}.project_id IS NULL AND EXCLUDED.project_id IS NULL)
                OR {table}.project_id = EXCLUDED.project_id
            )
        )
    )"""


def _graph_entity_conflict_where() -> str:
    """Strict ON CONFLICT ownership guard for graph_entities (C4)."""
    return """(
        COALESCE(graph_entities.properties->>'scope', '') <> 'space'
        OR COALESCE(EXCLUDED.properties->>'scope', '') = 'space'
    )
    AND (
        graph_entities.domain_id = 'default'
        OR COALESCE(EXCLUDED.properties->>'scope', '') = 'space'
        OR graph_entities.domain_id = EXCLUDED.domain_id
        OR (
            graph_entities.entity_type IN ('MEDIA_BLOB', 'CONCEPT')
            AND COALESCE(graph_entities.properties->>'scope', '') = 'shared'
            AND COALESCE(EXCLUDED.properties->>'scope', '') = 'shared'
        )
    )
    AND (
        COALESCE(EXCLUDED.properties->>'scope', '') = 'space'
        OR (
            graph_entities.entity_type IN ('MEDIA_BLOB', 'CONCEPT')
            AND COALESCE(graph_entities.properties->>'scope', '') = 'shared'
            AND COALESCE(EXCLUDED.properties->>'scope', '') = 'shared'
        )
        OR (
            graph_entities.properties->>'project_id' IS NOT NULL
            AND graph_entities.properties->>'project_id' = EXCLUDED.properties->>'project_id'
        )
        OR (
            graph_entities.properties->>'project_id' IS NULL
            AND (
                EXCLUDED.properties->>'project_id' IS NULL
                OR COALESCE((graph_entities.properties->>'stub')::boolean, false)
            )
        )
    )"""


def _graph_relation_conflict_where() -> str:
    """Strict ON CONFLICT ownership guard for graph_relations (C4)."""
    return """(
        COALESCE(graph_relations.properties->>'scope', '') <> 'space'
        OR COALESCE(EXCLUDED.properties->>'scope', '') = 'space'
    )
    AND (
        COALESCE(EXCLUDED.properties->>'scope', '') = 'space'
        OR (
            graph_relations.properties->>'project_id' IS NOT NULL
            AND graph_relations.properties->>'project_id' = EXCLUDED.properties->>'project_id'
        )
        OR (
            graph_relations.properties->>'project_id' IS NULL
            AND EXCLUDED.properties->>'project_id' IS NULL
        )
    )"""


def _graph_node_visibility_where(alias: str) -> str:
    """Strict tenant/project/domain/scope filter for graph nodes at every hop (C3)."""
    return f"""(
        (
            %(project_id)s::text IS NULL
            AND %(domain_id)s::text IS NULL
            AND %(has_scope_filter)s IS FALSE
        )
        OR (%(inc_space)s AND {alias}.properties->>'scope' = 'space')
        OR (
            %(inc_domain)s
            AND %(domain_id)s::text IS NOT NULL
            AND {alias}.domain_id = %(domain_id)s
            AND (
                COALESCE({alias}.properties->>'scope', '') = 'domain'
                OR {alias}.properties->>'project_id' IS NULL
            )
        )
        OR (
            {alias}.entity_type IN ('MEDIA_BLOB', 'CONCEPT')
            AND {alias}.properties->>'scope' = 'shared'
        )
        OR (
            %(project_id)s::text IS NOT NULL
            AND {alias}.properties->>'project_id' = %(project_id)s
        )
    )"""


def _graph_edge_visibility_where(alias: str) -> str:
    """Strict tenant/project/domain/scope filter for graph edges (C3)."""
    return f"""(
        (
            %(project_id)s::text IS NULL
            AND %(domain_id)s::text IS NULL
            AND %(has_scope_filter)s IS FALSE
        )
        OR (%(inc_space)s AND {alias}.properties->>'scope' = 'space')
        OR (
            %(inc_domain)s
            AND %(domain_id)s::text IS NOT NULL
            AND {alias}.properties->>'domain_id' = %(domain_id)s
        )
        OR (
            %(project_id)s::text IS NOT NULL
            AND {alias}.properties->>'project_id' = %(project_id)s
        )
    )"""


def _hydrate_graph_node(row: Mapping[str, Any], tenant_id: str) -> dict[str, Any]:
    """Unpack user properties FIRST so canonical identity fields cannot be spoofed (C3)."""
    raw_props = row.get("properties")
    if isinstance(raw_props, dict):
        props = dict(raw_props)
    elif isinstance(raw_props, str) and raw_props:
        props = json.loads(raw_props)
    else:
        props = {}
    eff_domain = (
        row["domain_id"] if row.get("domain_id") is not None else props.get("domain_id", "default")
    )
    return {
        "id": row["id"],
        "labels": [row["entity_type"]],
        "properties": {
            **props,
            "name": row["name"],
            "tenant_id": tenant_id,
            "domain_id": eff_domain,
        },
    }


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

_KB_SECTION_COLUMNS = (
    "id, tenant_id, project_id, domain_id, scope, agent_id, doc_id, citation, text, version, "
    "modality, blob_sha256, blob_uri, media_metadata"
)


class StoreUnavailable(RuntimeError):
    """The canonical store cannot be reached; callers fail closed (EARS.01.03.c900).

    Usually retryable (connection loss), but psycopg maps auth/config failures
    to OperationalError too — inspect ``__cause__`` before retrying forever.
    """


def vector_literal(embedding: Sequence[float]) -> str:
    """pgvector input literal: '[x1,x2,...]' (cast with ::vector at the call site)."""
    return "[" + ",".join(repr(float(x)) for x in embedding) + "]"


def _escape_ilike(val: str) -> str:
    """Escape SQL ILIKE wildcards (`\\`, `%`, `_`) so user queries match literally."""
    return val.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


class Repository:
    """Typed data access over the canonical Postgres schema (migrations 0001..0013)."""

    def __init__(
        self,
        dsn: str,
        *,
        min_size: int = 1,
        max_size: int = 10,
        open_timeout: float = 5.0,
    ) -> None:
        self._dsn = dsn
        self._min_size = max(0, int(min_size))
        self._max_size = max(1, int(max_size))
        self._open_timeout = max(0.05, float(open_timeout))
        self._pools: weakref.WeakKeyDictionary[
            asyncio.AbstractEventLoop,
            AsyncConnectionPool[psycopg.AsyncConnection[dict[str, Any]]],
        ] = weakref.WeakKeyDictionary()
        self._pool_locks: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Lock] = (
            weakref.WeakKeyDictionary()
        )

    @property
    def dsn(self) -> str:
        return self._dsn

    async def _get_pool(self) -> AsyncConnectionPool[psycopg.AsyncConnection[dict[str, Any]]]:
        """Lazily initialize and return the AsyncConnectionPool bound to the running loop."""
        loop = asyncio.get_running_loop()
        pool = self._pools.get(loop)
        if pool is not None:
            return pool
        lock = self._pool_locks.get(loop)
        if lock is None:
            lock = asyncio.Lock()
            self._pool_locks[loop] = lock
        async with lock:
            pool = self._pools.get(loop)
            if pool is not None:
                return pool
            candidate = cast(
                AsyncConnectionPool[psycopg.AsyncConnection[dict[str, Any]]],
                AsyncConnectionPool(
                    conninfo=self._dsn,
                    min_size=self._min_size,
                    max_size=self._max_size,
                    open=False,
                    kwargs={"row_factory": dict_row},
                ),
            )
            try:
                await candidate.open(wait=False)
            except Exception:
                await candidate.close()
                raise
            self._pools[loop] = candidate
            return candidate

    async def close(self) -> None:
        """Close the AsyncConnectionPool bound to the current running event loop, if any."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        pool = self._pools.pop(loop, None)
        if pool is not None:
            await pool.close()

    @asynccontextmanager
    async def _conn(
        self,
        *,
        tenant_id: str | None = None,
        worker_sweep: bool = False,
    ) -> AsyncIterator[psycopg.AsyncConnection[dict[str, Any]]]:
        pool = await self._get_pool()
        try:
            async with pool.connection(timeout=self._open_timeout) as conn:
                async with conn.transaction():
                    await conn.execute(
                        """
                        SELECT set_config('app.tenant_id', %(tid)s, true),
                               set_config('app.worker_sweep', %(sweep)s, true)
                        """,
                        {
                            "tid": tenant_id or "",
                            "sweep": "on" if worker_sweep else "",
                        },
                    )
                    yield conn
        except (psycopg.OperationalError, psycopg.InterfaceError, TimeoutError, PoolTimeout) as exc:
            raise StoreUnavailable(str(exc)) from exc

    async def ping(self, *, timeout_sec: float = 2.0) -> bool:
        """Return True iff PostgreSQL executes ``SELECT 1`` within ``timeout_sec``."""

        async def _probe() -> bool:
            async with self._conn() as conn:
                cur = await conn.execute("SELECT 1")
                row = await cur.fetchone()
                return row is not None

        try:
            return bool(await asyncio.wait_for(_probe(), timeout=timeout_sec))
        except Exception:  # noqa: BLE001  # reason: readiness probe must degrade cleanly to unhealthy
            return False

    async def add_episode(self, episode: Episode) -> str:
        """Persist idempotently by content hash or event_id within scope (BDD.01.03.c900).

        Returns the episode id — the existing row's id on duplicate submission.
        """
        params = _episode_params(episode)
        async with self._conn(tenant_id=episode.tenant_id) as conn:
            row = await (
                await conn.execute(
                    """
                    INSERT INTO episodes
                        (agent_id, project_id, domain_id, tenant_id, kind,
                         content_raw, content_hash, metadata)
                    VALUES (%(agent_id)s, %(project_id)s, %(domain_id)s, %(tenant_id)s,
                            %(kind)s, %(content_raw)s, %(content_hash)s, %(metadata)s)
                    ON CONFLICT DO NOTHING
                    RETURNING id
                    """,
                    params,
                )
            ).fetchone()
            if row is not None:
                return str(row["id"])
            existing = await (
                await conn.execute(
                    """
                    SELECT id FROM episodes
                    WHERE tenant_id = %(tenant_id)s
                      AND (
                        (
                            agent_id = %(agent_id)s
                            AND coalesce(project_id, '') = coalesce(%(project_id)s, '')
                            AND content_hash = %(content_hash)s
                        )
                        OR (
                            %(event_id)s::text IS NOT NULL
                            AND agent_id = %(agent_id)s
                            AND coalesce(project_id, '') = coalesce(%(project_id)s, '')
                            AND NULLIF(metadata->>'event_id', '') = %(event_id)s
                        )
                      )
                    LIMIT 1
                    """,
                    params,
                )
            ).fetchone()
            if existing is None:  # unreachable: episodes are never hard-deleted
                raise RuntimeError("episode vanished between conflicting INSERT and SELECT")
            return str(existing["id"])

    async def upsert_memory(
        self,
        memory: Memory,
        *,
        embedding: Sequence[float] | None = None,
        copy_embedding_from_id: str | None = None,
    ) -> str:
        """Insert a distilled memory; soft-supersede the predecessor in the same
        transaction when ``memory.supersedes`` is set (never delete)."""
        params = _memory_params(memory)
        params["embedding"] = (
            vector_literal(embedding) if embedding is not None and len(embedding) == 1536 else None
        )
        async with self._conn(tenant_id=memory.tenant_id) as conn:
            row = await (
                await conn.execute(
                    """
                    INSERT INTO memories
                        (agent_id, project_id, domain_id, tenant_id, scope, type,
                         content_raw, summary, embedding_model, embedding_dims,
                         embedding, provenance, confidence, status, source_trust, supersedes)
                    VALUES (%(agent_id)s, %(project_id)s, %(domain_id)s, %(tenant_id)s,
                            %(scope)s, %(type)s, %(content_raw)s, %(summary)s,
                            %(embedding_model)s, %(embedding_dims)s,
                            CASE
                                WHEN %(embedding)s::text IS NOT NULL THEN %(embedding)s::vector
                                ELSE NULL
                            END,
                            %(provenance)s, %(confidence)s, %(status)s,
                            %(source_trust)s, %(supersedes)s)
                    RETURNING id
                    """,
                    params,
                )
            ).fetchone()
            if row is None:
                raise RuntimeError("INSERT INTO memories did not return id")
            mem_id = str(row["id"])
            if params["embedding"] is None and copy_embedding_from_id is not None:
                await conn.execute(
                    """
                    UPDATE memories dst
                    SET embedding = src.embedding,
                        embedding_model = src.embedding_model,
                        embedding_dims = src.embedding_dims
                    FROM memories src
                    WHERE dst.id = %(dst_id)s
                      AND dst.tenant_id = %(tenant_id)s
                      AND src.id = %(src_id)s
                      AND src.tenant_id = %(tenant_id)s
                      AND src.embedding IS NOT NULL
                    """,
                    {
                        "dst_id": mem_id,
                        "src_id": copy_embedding_from_id,
                        "tenant_id": memory.tenant_id,
                    },
                )
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
            return mem_id

    async def get_memory(self, memory_id: str, *, tenant_id: str) -> Memory:
        """Fetch one memory by id regardless of status (audit/supersede chains).

        Tenant-guarded: a foreign tenant's id raises KeyError exactly like a
        missing one — existence is never disclosed across the wall (ADR-07).
        """
        async with self._conn(tenant_id=tenant_id) as conn:
            row = await (
                await conn.execute(
                    """
                    SELECT id, agent_id, project_id, domain_id, tenant_id, scope, type,
                           content_raw, summary, embedding_model, embedding_dims,
                           provenance, confidence, status, source_trust,
                           valid_from, valid_to, supersedes
                    FROM memories
                    WHERE id = %(id)s AND tenant_id = %(tenant_id)s
                    """,
                    {"id": memory_id, "tenant_id": tenant_id},
                )
            ).fetchone()
            if row is None:
                raise KeyError(memory_id)
            return _hydrate_memory(row)

    async def get_retrieval(self, retrieval_id: str, *, tenant_id: str) -> dict[str, Any]:
        """Fetch retrieval ownership metadata within tenant_id (C2)."""
        async with self._conn(tenant_id=tenant_id) as conn:
            row = await (
                await conn.execute(
                    """
                    SELECT id, memory_id, tenant_id, agent_id, project_id, feedback, applied_delta
                    FROM memory_retrievals
                    WHERE id = %(id)s AND tenant_id = %(tenant_id)s
                    """,
                    {"id": retrieval_id, "tenant_id": tenant_id},
                )
            ).fetchone()
            if row is None:
                raise KeyError(retrieval_id)
            return dict(row)

    async def retire_memory(
        self,
        memory_id: str,
        *,
        tenant_id: str,
        caller_agent_id: str | None = None,
        caller_project_id: str | None = None,
        caller_domain_id: str | None = None,
        allowed_scopes: Sequence[str] | None = None,
    ) -> None:
        """Soft-retire (SPEC-01 memory_forget): end-date + mark superseded.

        Content and provenance are retained, never deleted. Tenant & ownership guarded;
        KeyError when id is missing/foreign/retired; PermissionError on scope mismatch.
        """
        async with self._conn(tenant_id=tenant_id) as conn:
            async with conn.transaction():
                if allowed_scopes is not None:
                    row = await (
                        await conn.execute(
                            """
                            SELECT tenant_id, agent_id, project_id, domain_id, scope, valid_to
                            FROM memories
                            WHERE id = %(id)s AND tenant_id = %(tenant_id)s
                            FOR UPDATE
                            """,
                            {"id": memory_id, "tenant_id": tenant_id},
                        )
                    ).fetchone()
                    if row is None or row["valid_to"] is not None:
                        raise KeyError(memory_id)
                    mem_scope = str(row["scope"])
                    granted = set(allowed_scopes)
                    if mem_scope not in granted:
                        raise PermissionError(
                            f"retire_memory denied: scope {mem_scope!r} not in caller scopes"
                        )
                    if (
                        mem_scope == "agent"
                        and caller_agent_id
                        and row["agent_id"] != caller_agent_id
                    ):
                        raise PermissionError(
                            "retire_memory denied: private agent memory belongs to another agent"
                        )
                    if (
                        mem_scope == "project"
                        and caller_project_id
                        and "space" not in granted
                        and "domain" not in granted
                        and row["project_id"] != caller_project_id
                    ):
                        raise PermissionError(
                            "retire_memory denied: memory belongs to another project"
                        )
                    if (
                        mem_scope == "domain"
                        and caller_domain_id is not None
                        and "space" not in granted
                        and row["domain_id"] != caller_domain_id
                    ):
                        raise PermissionError(
                            "retire_memory denied: memory belongs to another domain"
                        )
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

    async def record_feedback(
        self,
        retrieval_id: str,
        outcome: str,
        *,
        tenant_id: str,
        caller_agent_id: str | None = None,
        allow_space_override: bool = False,
    ) -> None:
        """Stamp a retrieval outcome (SPEC-01 memory_feedback -> SPEC-04
        confidence rule). Enforces ownership, reverses prior applied_delta on
        revision, and requires >=2 distinct agent_id harmful votes on shared scopes."""
        if outcome not in FEEDBACK_OUTCOMES:
            raise ValueError(
                f"feedback outcome must be one of {sorted(FEEDBACK_OUTCOMES)}: {outcome!r}"
            )
        if retrieval_id.startswith("kb-ret:"):
            raise ValidationError(
                "record_feedback requires an L2 memory retrieval_id, not a kb_section hit"
            )
        async with self._conn(tenant_id=tenant_id) as conn:
            async with conn.transaction():
                prev = await (
                    await conn.execute(
                        """
                        SELECT memory_id, agent_id, feedback, applied_delta
                        FROM memory_retrievals
                        WHERE id = %(id)s AND tenant_id = %(tenant_id)s
                        FOR UPDATE
                        """,
                        {"id": retrieval_id, "tenant_id": tenant_id},
                    )
                ).fetchone()
                if prev is None:
                    raise KeyError(retrieval_id)
                if (
                    caller_agent_id is not None
                    and not allow_space_override
                    and str(prev["agent_id"]) != caller_agent_id
                ):
                    raise PermissionError(
                        f"record_feedback denied: retrieval {retrieval_id} belongs to another agent"
                    )
                if prev["feedback"] == outcome:
                    return
                memory_id = prev["memory_id"]
                mem = await (
                    await conn.execute(
                        """
                        SELECT confidence, status, scope
                        FROM memories
                        WHERE id = %(memory_id)s AND tenant_id = %(tenant_id)s
                        FOR UPDATE
                        """,
                        {"memory_id": memory_id, "tenant_id": tenant_id},
                    )
                ).fetchone()
                if mem is None:
                    raise KeyError(retrieval_id)

                c_curr = float(mem["confidence"] or 0.0)
                prev_delta = float(prev.get("applied_delta") or 0.0)
                c_base = max(0.0, min(1.0, c_curr - prev_delta))
                target_v = _FEEDBACK_TARGETS[outcome]
                c_new = max(0.0, min(1.0, c_base + 0.2 * (target_v - c_base)))
                new_delta = c_new - c_base

                await conn.execute(
                    """
                    UPDATE memory_retrievals
                    SET feedback = %(outcome)s,
                        applied_delta = %(new_delta)s,
                        feedback_at = now()
                    WHERE id = %(id)s AND tenant_id = %(tenant_id)s
                    """,
                    {
                        "id": retrieval_id,
                        "outcome": outcome,
                        "new_delta": new_delta,
                        "tenant_id": tenant_id,
                    },
                )

                cur_status = str(mem["status"])
                mem_scope = str(mem["scope"])
                if cur_status == "superseded":
                    new_status = "superseded"
                elif outcome == "harmful":
                    if mem_scope == "agent":
                        new_status = "quarantined"
                    else:
                        harm_row = await (
                            await conn.execute(
                                """
                                SELECT COUNT(DISTINCT agent_id) AS n
                                FROM memory_retrievals
                                WHERE tenant_id = %(tenant_id)s
                                  AND memory_id = %(memory_id)s
                                  AND feedback = 'harmful'
                                """,
                                {"tenant_id": tenant_id, "memory_id": memory_id},
                            )
                        ).fetchone()
                        harm_agents = int(harm_row["n"] or 0) if harm_row else 0
                        if harm_agents >= 2:
                            new_status = "quarantined"
                        elif c_new < 0.4:
                            new_status = "advisory"
                        elif c_new >= 0.5:
                            new_status = "active"
                        else:
                            new_status = cur_status
                else:
                    if cur_status == "quarantined" and mem_scope != "agent":
                        harm_row = await (
                            await conn.execute(
                                """
                                SELECT COUNT(DISTINCT agent_id) AS n
                                FROM memory_retrievals
                                WHERE tenant_id = %(tenant_id)s
                                  AND memory_id = %(memory_id)s
                                  AND feedback = 'harmful'
                                """,
                                {"tenant_id": tenant_id, "memory_id": memory_id},
                            )
                        ).fetchone()
                        if (int(harm_row["n"] or 0) if harm_row else 0) >= 2:
                            new_status = "quarantined"
                        elif c_new < 0.4:
                            new_status = "advisory"
                        else:
                            new_status = "active"
                    elif c_new < 0.4:
                        new_status = "advisory"
                    elif c_new >= 0.5:
                        new_status = "active"
                    elif cur_status == "quarantined":
                        new_status = "active"
                    else:
                        new_status = cur_status

                await conn.execute(
                    """
                    UPDATE memories
                    SET confidence = %(confidence)s,
                        status = %(status)s
                    WHERE id = %(memory_id)s AND tenant_id = %(tenant_id)s
                    """,
                    {
                        "confidence": c_new,
                        "status": new_status,
                        "memory_id": memory_id,
                        "tenant_id": tenant_id,
                    },
                )

    async def get_retrieval_feedback(self, *, tenant_id: str, memory_id: str) -> list[str | None]:
        """Feedback values for a memory's retrievals, oldest first (tests/eval)."""
        async with self._conn(tenant_id=tenant_id) as conn:
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

    async def get_retrieval_feedback_summary(
        self, *, tenant_id: str, memory_ids: Sequence[str]
    ) -> dict[str, int]:
        """Aggregate retrieval feedback counts across ``memory_ids`` in a single query."""
        empty = {"total": 0, "useful": 0, "not_useful": 0, "harmful": 0}
        clean_ids = [str(mid) for mid in memory_ids if mid]
        if not clean_ids:
            return empty
        async with self._conn(tenant_id=tenant_id) as conn:
            row = await (
                await conn.execute(
                    """
                    SELECT
                        COUNT(*)::integer AS total,
                        COUNT(*) FILTER (WHERE feedback = 'useful')::integer AS useful,
                        COUNT(*) FILTER (WHERE feedback = 'not_useful')::integer AS not_useful,
                        COUNT(*) FILTER (WHERE feedback = 'harmful')::integer AS harmful
                    FROM memory_retrievals
                    WHERE tenant_id = %(tenant_id)s
                      AND memory_id = ANY(%(memory_ids)s::uuid[])
                    """,
                    {"tenant_id": tenant_id, "memory_ids": clean_ids},
                )
            ).fetchone()
        if row is None:
            return empty
        return {
            "total": int(row["total"] or 0),
            "useful": int(row["useful"] or 0),
            "not_useful": int(row["not_useful"] or 0),
            "harmful": int(row["harmful"] or 0),
        }

    async def get_agent_profile(
        self, agent_id: str, *, tenant_id: str = "default"
    ) -> AgentProfile | None:
        """L3 identity row scoped by (tenant_id, agent_id), or None when absent."""
        async with self._conn(tenant_id=tenant_id) as conn:
            row = await (
                await conn.execute(
                    """
                    SELECT tenant_id, agent_id, display_name, standing_preferences
                    FROM agent_profiles
                    WHERE tenant_id = %(tenant_id)s AND agent_id = %(agent_id)s
                    """,
                    {"tenant_id": tenant_id, "agent_id": agent_id},
                )
            ).fetchone()
            if row is None:
                return None
            return AgentProfile(
                agent_id=row["agent_id"],
                display_name=row["display_name"],
                standing_preferences=row["standing_preferences"] or {},
                tenant_id=row["tenant_id"],
            )

    async def upsert_agent_profile(self, profile: AgentProfile) -> None:
        """Create/update the L3 identity row scoped by (tenant_id, agent_id)."""
        async with self._conn(tenant_id=profile.tenant_id) as conn:
            await conn.execute(
                """
                INSERT INTO agent_profiles
                    (tenant_id, agent_id, display_name, standing_preferences)
                VALUES (%(tenant_id)s, %(agent_id)s, %(display_name)s, %(standing_preferences)s)
                ON CONFLICT (tenant_id, agent_id) DO UPDATE
                    SET display_name = EXCLUDED.display_name,
                        standing_preferences = EXCLUDED.standing_preferences
                """,
                {
                    "tenant_id": profile.tenant_id,
                    "agent_id": profile.agent_id,
                    "display_name": profile.display_name,
                    "standing_preferences": json.dumps(thaw_mapping(profile.standing_preferences)),
                },
            )

    async def list_agent_profiles(
        self,
        *,
        tenant_id: str,
        domain_id: str | None = None,
        agent_type: str | None = None,
        role: str | None = None,
        specialization: str | None = None,
        include_stats: bool = True,
    ) -> list[dict[str, Any]]:
        """List Virtual Employee profiles in ``tenant_id`` with filters and live skill stats."""
        async with self._conn(tenant_id=tenant_id) as conn:
            rows = await (
                await conn.execute(
                    """
                    SELECT
                        p.tenant_id,
                        p.agent_id,
                        p.display_name,
                        p.standing_preferences,
                        COALESCE(m.active_skill_count, 0) AS active_skill_count,
                        COALESCE(m.mean_confidence, 0.0) AS mean_confidence,
                        COALESCE(m.brain_variants, '[]'::jsonb) AS brain_variants
                    FROM agent_profiles p
                    LEFT JOIN (
                        SELECT
                            tenant_id,
                            agent_id,
                            COUNT(*)::int AS active_skill_count,
                            AVG(confidence)::float AS mean_confidence,
                            jsonb_agg(
                                DISTINCT coalesce(provenance->>'brain_variant', 'default')
                            ) AS brain_variants
                        FROM memories
                        WHERE tenant_id = %(tenant_id)s
                          AND scope = 'agent'
                          AND status = 'active'
                          AND valid_to IS NULL
                        GROUP BY tenant_id, agent_id
                    ) m ON m.tenant_id = p.tenant_id AND m.agent_id = p.agent_id
                    WHERE p.tenant_id = %(tenant_id)s
                      AND (
                          %(domain_id)s::text IS NULL
                          OR p.standing_preferences->>'department_focus' = %(domain_id)s
                      )
                      AND (
                          %(agent_type)s::text IS NULL
                          OR coalesce(
                              p.standing_preferences->>'agent_type',
                              p.standing_preferences->>'role'
                          ) = %(agent_type)s
                      )
                      AND (
                          %(role)s::text IS NULL
                          OR coalesce(
                              p.standing_preferences->>'role',
                              p.standing_preferences->>'agent_type'
                          ) = %(role)s
                      )
                      AND (
                          %(specialization)s::text IS NULL
                          OR coalesce(
                              p.standing_preferences->'specializations', '[]'::jsonb
                          ) ? %(specialization)s
                      )
                    ORDER BY
                        CASE
                            WHEN %(include_stats)s THEN COALESCE(m.mean_confidence, 0.0)
                            ELSE 0.0
                        END DESC,
                        CASE
                            WHEN %(include_stats)s THEN COALESCE(m.active_skill_count, 0)
                            ELSE 0
                        END DESC,
                        p.agent_id ASC
                    """,
                    {
                        "tenant_id": tenant_id,
                        "domain_id": domain_id,
                        "agent_type": agent_type,
                        "role": role,
                        "specialization": specialization,
                        "include_stats": include_stats,
                    },
                )
            ).fetchall()
            out: list[dict[str, Any]] = []
            for row in rows:
                prefs = dict(row["standing_preferences"] or {})
                raw_specs = prefs.get("specializations")
                specs = [str(s) for s in raw_specs] if isinstance(raw_specs, list) else []
                bvars = row["brain_variants"]
                variants = [str(v) for v in bvars] if isinstance(bvars, list) else []
                entry: dict[str, Any] = {
                    "tenant_id": str(row["tenant_id"]),
                    "agent_id": str(row["agent_id"]),
                    "display_name": str(row["display_name"] or ""),
                    "agent_type": prefs.get("agent_type") or prefs.get("role"),
                    "role": prefs.get("role") or prefs.get("agent_type"),
                    "domain_id": prefs.get("department_focus"),
                    "specializations": specs,
                    "brain_config": dict(prefs.get("brain_config") or {}),
                    "standing_preferences": prefs,
                }
                if include_stats:
                    entry["active_skill_count"] = int(row["active_skill_count"] or 0)
                    entry["mean_confidence"] = round(float(row["mean_confidence"] or 0.0), 4)
                    entry["brain_variants"] = variants
                out.append(entry)
            return out

    async def offboard_agent_space(
        self,
        *,
        tenant_id: str,
        agent_id: str,
        reason: str,
        purge_episodes: bool = True,
        retire_memories: bool = True,
    ) -> dict[str, int]:
        """Transactionally retire private scope='agent' memories, purge private agent
        episodes, and delete the L3 agent_profiles row on ``tenant_id`` while leaving
        shared project/domain/space artifacts untouched."""
        _ = reason
        retired_memories = 0
        purged_episodes = 0
        deleted_profiles = 0
        async with self._conn(tenant_id=tenant_id) as conn:
            async with conn.transaction():
                if retire_memories:
                    cur_mem = await conn.execute(
                        """
                        UPDATE memories
                        SET valid_to = now(), status = 'superseded'
                        WHERE tenant_id = %(tenant_id)s
                          AND agent_id = %(agent_id)s
                          AND scope = 'agent'
                          AND valid_to IS NULL
                        """,
                        {"tenant_id": tenant_id, "agent_id": agent_id},
                    )
                    retired_memories = int(cur_mem.rowcount or 0)
                if purge_episodes:
                    cur_ep = await conn.execute(
                        """
                        DELETE FROM episodes
                        WHERE tenant_id = %(tenant_id)s
                          AND agent_id = %(agent_id)s
                          AND coalesce(NULLIF(metadata->>'scope', ''), 'agent') = 'agent'
                        """,
                        {"tenant_id": tenant_id, "agent_id": agent_id},
                    )
                    purged_episodes = int(cur_ep.rowcount or 0)
                cur_prof = await conn.execute(
                    """
                    DELETE FROM agent_profiles
                    WHERE tenant_id = %(tenant_id)s AND agent_id = %(agent_id)s
                    """,
                    {"tenant_id": tenant_id, "agent_id": agent_id},
                )
                deleted_profiles = int(cur_prof.rowcount or 0)
        return {
            "retired_memories": retired_memories,
            "purged_episodes": purged_episodes,
            "deleted_profiles": deleted_profiles,
        }

    async def get_unreflected_episodes(
        self,
        *,
        tenant_id: str,
        agent_id: str,
        limit: int = 500,
        brain_variant: str | None = "default",
    ) -> list[Episode]:
        """Episodes not yet projected into memories for ``brain_variant``.

        When ``brain_variant`` is ``None`` or ``'default'``, matches against
        ``COALESCE(NULLIF(btrim(e.metadata->>'brain_variant'), ''), 'default')`` so
        variant-tagged episodes coalesce cleanly instead of silently matching default.
        Explicit non-default variants match exactly.
        """
        eff_variant = (brain_variant or "default").strip() or "default"
        use_coalesce = eff_variant == "default"
        async with self._conn(tenant_id=tenant_id) as conn:
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
                            AND m.agent_id = e.agent_id
                            AND m.provenance->>'episode_id' = e.id::text
                            AND coalesce(m.provenance->>'brain_variant', 'default') = CASE
                                WHEN %(use_coalesce)s THEN coalesce(
                                    NULLIF(btrim(e.metadata->>'brain_variant'), ''),
                                    %(brain_variant)s
                                )
                                ELSE %(brain_variant)s
                            END
                      )
                    ORDER BY ts, id
                    LIMIT %(limit)s
                    """,
                    {
                        "tenant_id": tenant_id,
                        "agent_id": agent_id,
                        "limit": limit,
                        "brain_variant": eff_variant,
                        "use_coalesce": use_coalesce,
                    },
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
        self, *, tenant_id: str | None = None, brain_variant: str = "default"
    ) -> list[tuple[str, str]]:
        """Distinct (tenant_id, agent_id) pairs carrying unreflected episodes for variant."""
        async with self._conn(tenant_id=tenant_id, worker_sweep=(tenant_id is None)) as conn:
            rows = await (
                await conn.execute(
                    """
                    SELECT DISTINCT e.tenant_id, e.agent_id
                    FROM episodes e
                    LEFT JOIN agent_profiles p
                      ON p.tenant_id = e.tenant_id AND p.agent_id = e.agent_id
                    WHERE (%(tenant_id)s::text IS NULL OR e.tenant_id = %(tenant_id)s::text)
                      AND NOT EXISTS (
                          SELECT 1 FROM memories m
                          WHERE m.tenant_id = e.tenant_id
                            AND m.agent_id = e.agent_id
                            AND m.provenance->>'episode_id' = e.id::text
                            AND coalesce(m.provenance->>'brain_variant', 'default') = coalesce(
                                NULLIF(
                                    btrim(
                                        p.standing_preferences->'brain_config'
                                        ->>'active_brain_variant'
                                    ),
                                    ''
                                ),
                                NULLIF(btrim(e.metadata->>'brain_variant'), ''),
                                %(brain_variant)s
                            )
                      )
                    """,
                    {"tenant_id": tenant_id, "brain_variant": brain_variant},
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
        query: str | None = None,
        query_vec: Sequence[float] | None = None,
        scopes: Sequence[str] | None = None,
        brain_variant: str | None = None,
        specialization: str | None = None,
        include_quarantined: bool = False,
        min_confidence: float | None = None,
    ) -> list[Memory]:
        """Visible, live, active memories per the ADR-07 ladder.

        ``scopes`` restricts visibility to the granted ladder levels; None
        means the full ladder (repo-level tooling — the access surface always
        passes the caller's granted subset).

        Ranked by cosine similarity to ``query_vec`` when given (with fallback
        lexical/substring boost for ``query``), by recency otherwise.
        """
        _visibility_sql(scopes)
        levels = tuple(scopes) if scopes is not None else tuple(_SCOPE_CLAUSES)
        has_qv = query_vec is not None and len(query_vec) == 1536
        clean_q = query.strip() if query and query.strip() else None
        has_q = clean_q is not None
        params: dict[str, Any] = {
            "tenant_id": tenant_id,
            "agent_id": agent_id,
            "project_id": project_id,
            "domain_id": domain_id,
            "k": k,
            "inc_agent": "agent" in levels,
            "inc_project": "project" in levels,
            "inc_domain": "domain" in levels,
            "inc_space": "space" in levels,
            "qv": vector_literal(query_vec) if has_qv and query_vec is not None else None,
            "has_qv": has_qv,
            "has_q": has_q,
            "q_text": clean_q,
            "q_like": f"%{_escape_ilike(clean_q)}%" if clean_q is not None else None,
            "brain_variant": brain_variant,
            "specialization": specialization,
            "include_quarantined": include_quarantined,
            "min_confidence": min_confidence,
        }
        async with self._conn(tenant_id=tenant_id) as conn:
            rows = await (
                await conn.execute(
                    """
                    SELECT id, agent_id, project_id, domain_id, tenant_id, scope, type,
                           content_raw, summary, embedding_model, embedding_dims,
                           provenance, confidence, status, source_trust,
                           valid_from, valid_to, supersedes
                    FROM memories
                    WHERE tenant_id = %(tenant_id)s
                      AND valid_to IS NULL
                      AND (
                          status = 'active'
                          OR (%(include_quarantined)s AND status = 'quarantined')
                      )
                      AND (
                          %(min_confidence)s::double precision IS NULL
                          OR confidence >= %(min_confidence)s
                      )
                      AND (
                          %(brain_variant)s::text IS NULL
                          OR coalesce(provenance->>'brain_variant', 'default') = %(brain_variant)s
                      )
                      AND (
                          %(specialization)s::text IS NULL
                          OR coalesce(
                              provenance->'specializations', '[]'::jsonb
                          ) ? %(specialization)s
                      )
                      AND (
                          %(domain_id)s::text IS NULL
                          OR scope = 'space'
                          OR domain_id = %(domain_id)s
                      )
                      AND (
                          NOT %(has_q)s
                          OR %(has_qv)s
                          OR ts_lex @@ websearch_to_tsquery('english', %(q_text)s)
                          OR content_raw ILIKE %(q_like)s ESCAPE '\\'
                          OR coalesce(summary, '') ILIKE %(q_like)s ESCAPE '\\'
                      )
                      AND (
                          (%(inc_agent)s AND scope = 'agent' AND agent_id = %(agent_id)s)
                          OR (
                              %(inc_project)s
                              AND scope = 'project'
                              AND project_id IS NOT NULL
                              AND project_id = %(project_id)s
                          )
                          OR (
                              %(inc_domain)s
                              AND scope = 'domain'
                              AND domain_id IS NOT NULL
                              AND domain_id = %(domain_id)s
                          )
                          OR (%(inc_space)s AND scope = 'space')
                      )
                    ORDER BY
                      (
                        CASE
                          WHEN %(qv)s::text IS NOT NULL AND embedding IS NOT NULL
                            THEN (1.0 / (1.0 + GREATEST(0.0, (embedding <=> %(qv)s::vector))))
                          ELSE 0.0
                        END
                        + CASE
                            WHEN %(q_text)s::text IS NOT NULL THEN (
                              LEAST(
                                  1.0,
                                  ts_rank_cd(
                                      COALESCE(ts_lex, to_tsvector('english', '')),
                                      websearch_to_tsquery('english', %(q_text)s)
                                  )
                              )
                              + CASE
                                  WHEN (
                                      content_raw ILIKE %(q_like)s ESCAPE '\\'
                                      OR coalesce(summary, '') ILIKE %(q_like)s ESCAPE '\\'
                                  )
                                    THEN 0.25
                                  ELSE 0.0
                                END
                            )
                            ELSE 0.0
                          END
                      ) DESC,
                      valid_from DESC
                    LIMIT %(k)s
                    """,
                    params,
                )
            ).fetchall()
            return [_hydrate_memory(row) for row in rows]

    async def find_existing_promoted_memory(
        self,
        *,
        tenant_id: str,
        target_scope: str,
        source_memory_id: str,
        summary: str,
        project_id: str | None = None,
        domain_id: str | None = None,
        brain_variant: str = "default",
    ) -> Memory | None:
        """Find an existing active promoted memory in ``target_scope`` matching
        ``source_memory_id`` or ``summary`` without MAX_K truncation."""
        async with self._conn(tenant_id=tenant_id) as conn:
            row = await (
                await conn.execute(
                    """
                    SELECT id, agent_id, project_id, domain_id, tenant_id, scope, type,
                           content_raw, summary, embedding_model, embedding_dims,
                           provenance, confidence, status, source_trust,
                           valid_from, valid_to, supersedes
                    FROM memories
                    WHERE tenant_id = %(tenant_id)s
                      AND scope = %(target_scope)s
                      AND status = 'active'
                      AND valid_to IS NULL
                      AND coalesce(provenance->>'brain_variant', 'default') = %(brain_variant)s
                      AND (
                          %(target_scope)s != 'project'
                          OR project_id = %(project_id)s
                      )
                      AND (
                          %(target_scope)s != 'domain'
                          OR domain_id = %(domain_id)s
                      )
                      AND (
                          provenance->>'promoted_from_memory_id' = %(source_memory_id)s
                          OR summary = %(summary)s
                      )
                    ORDER BY valid_from ASC
                    LIMIT 1
                    """,
                    {
                        "tenant_id": tenant_id,
                        "target_scope": target_scope,
                        "source_memory_id": source_memory_id,
                        "summary": summary,
                        "project_id": project_id,
                        "domain_id": domain_id,
                        "brain_variant": brain_variant,
                    },
                )
            ).fetchone()
            if row is None:
                return None
            return _hydrate_memory(row)

    async def get_brain_variant_metrics(
        self,
        *,
        tenant_id: str,
        variant: str,
        scope: str = "agent",
        agent_id: str | None = None,
        project_id: str | None = None,
        domain_id: str | None = None,
        specialization: str | None = None,
    ) -> dict[str, Any]:
        """Compute skill and retrieval feedback metrics for ``variant``."""
        mems = await self.get_memories(
            tenant_id=tenant_id,
            agent_id=agent_id,
            project_id=project_id,
            domain_id=domain_id,
            k=1000,
            scopes=(scope,),
            brain_variant=variant,
            specialization=specialization,
            include_quarantined=True,
        )
        active_mems = [m for m in mems if m.status == "active"]
        skill_count = len(active_mems)
        mean_confidence = (
            round(sum(m.confidence for m in active_mems) / skill_count, 4)
            if skill_count > 0
            else 0.0
        )
        mean_token_count = (
            round(
                sum(max(1, len((m.summary or m.content_raw).split())) for m in active_mems)
                / skill_count,
                2,
            )
            if skill_count > 0
            else 0.0
        )
        distiller_models: set[str] = set()
        embedding_models: set[str] = set()
        rag_params_seen: dict[str, Any] = {}
        total_retrievals = 0
        useful_count = 0
        not_useful_count = 0
        harmful_count = 0
        for m in mems:
            embedding_models.add(m.embedding_model)
            bm = m.provenance.get("brain_metadata")
            if isinstance(bm, Mapping):
                dm = bm.get("distiller_model")
                if dm:
                    distiller_models.add(str(dm))
                rp = bm.get("rag_params")
                if isinstance(rp, Mapping) and not rag_params_seen:
                    rag_params_seen = thaw_mapping(rp)
            elif m.provenance.get("distiller"):
                distiller_models.add(str(m.provenance["distiller"]))
        mem_ids = [m.id for m in mems if m.id]
        if mem_ids:
            if type(self).get_retrieval_feedback is not Repository.get_retrieval_feedback:
                for mid in mem_ids:
                    fbs = await self.get_retrieval_feedback(tenant_id=tenant_id, memory_id=mid)
                    total_retrievals += len(fbs)
                    for fb in fbs:
                        if fb == "useful":
                            useful_count += 1
                        elif fb == "not_useful":
                            not_useful_count += 1
                        elif fb == "harmful":
                            harmful_count += 1
            else:
                fb_summary = await self.get_retrieval_feedback_summary(
                    tenant_id=tenant_id, memory_ids=mem_ids
                )
                total_retrievals = fb_summary["total"]
                useful_count = fb_summary["useful"]
                not_useful_count = fb_summary["not_useful"]
                harmful_count = fb_summary["harmful"]
        rated = useful_count + not_useful_count + harmful_count
        useful_rate = round(useful_count / rated, 4) if rated > 0 else 0.0
        return {
            "brain_variant": variant,
            "skill_count": skill_count,
            "mean_confidence": mean_confidence,
            "mean_token_count": mean_token_count,
            "distiller_models": sorted(distiller_models),
            "embedding_models": sorted(embedding_models),
            "rag_params": rag_params_seen,
            "total_retrievals": total_retrievals,
            "useful_count": useful_count,
            "not_useful_count": not_useful_count,
            "harmful_count": harmful_count,
            "useful_rate": useful_rate,
        }

    async def upsert_kb_section(self, section: KBSection) -> str:
        """Insert/update a knowledge section keyed by (tenant, doc, citation, version)."""
        ownership_where = _kb_conflict_ownership_where("kb_sections")
        async with self._conn(tenant_id=section.tenant_id) as conn:
            row = await (
                await conn.execute(
                    _compose_static_sql(
                        """
                        INSERT INTO kb_sections
                            (tenant_id, project_id, domain_id, scope, agent_id, doc_id, citation,
                             text, version, modality, blob_sha256, blob_uri, media_metadata)
                        VALUES (%(tenant_id)s, %(project_id)s, %(domain_id)s, %(scope)s,
                                %(agent_id)s, %(doc_id)s, %(citation)s, %(text)s, %(version)s,
                                %(modality)s, %(blob_sha256)s, %(blob_uri)s,
                                %(media_metadata)s::jsonb)
                        ON CONFLICT (tenant_id, doc_id, citation, version)
                            DO UPDATE SET
                                project_id = EXCLUDED.project_id,
                                domain_id = COALESCE(EXCLUDED.domain_id, kb_sections.domain_id),
                                scope = EXCLUDED.scope,
                                agent_id = EXCLUDED.agent_id,
                                text = EXCLUDED.text,
                                embedding = CASE
                                    WHEN kb_sections.text IS DISTINCT FROM EXCLUDED.text THEN NULL
                                    ELSE kb_sections.embedding
                                END,
                                embedding_model = CASE
                                    WHEN kb_sections.text IS DISTINCT FROM EXCLUDED.text THEN NULL
                                    ELSE kb_sections.embedding_model
                                END,
                                embedding_dims = CASE
                                    WHEN kb_sections.text IS DISTINCT FROM EXCLUDED.text THEN NULL
                                    ELSE kb_sections.embedding_dims
                                END,
                                modality = EXCLUDED.modality,
                                blob_sha256 = COALESCE(
                                    EXCLUDED.blob_sha256, kb_sections.blob_sha256
                                ),
                                blob_uri = COALESCE(EXCLUDED.blob_uri, kb_sections.blob_uri),
                                media_metadata = EXCLUDED.media_metadata
                            WHERE {ownership_where}
                        RETURNING id
                        """,
                        ownership_where=ownership_where,
                    ),
                    {
                        "tenant_id": section.tenant_id,
                        "project_id": section.project_id,
                        "domain_id": section.domain_id,
                        "scope": section.scope,
                        "agent_id": section.agent_id,
                        "doc_id": section.doc_id,
                        "citation": section.citation,
                        "text": section.text,
                        "version": section.version,
                        "modality": section.modality,
                        "blob_sha256": section.blob_sha256,
                        "blob_uri": section.blob_uri,
                        "media_metadata": json.dumps(thaw_mapping(section.media_metadata)),
                    },
                )
            ).fetchone()
            if row is None:
                raise ValidationError(
                    f"Cross-project kb_section collision: '{section.doc_id}' "
                    f"({section.citation} v{section.version}) is already owned by another "
                    f"project in tenant '{section.tenant_id}'"
                )
            return str(row["id"])

    async def set_kb_section_embedding(
        self,
        section_id: str,
        *,
        tenant_id: str,
        embedding: Sequence[float],
        embedding_model: str = "text-embedding-3-small",
    ) -> None:
        """Store 1536-dim vector projection for a kb_section within tenant_id (ADR-05)."""
        clean_tid = str(tenant_id or "").strip()
        if not clean_tid:
            raise ValidationError("set_kb_section_embedding requires non-empty tenant_id")
        _validate_embedding_dims(embedding, "kb_section embedding")
        async with self._conn(tenant_id=clean_tid) as conn:
            await conn.execute(
                """
                UPDATE kb_sections
                SET embedding = %(vec)s::vector,
                    embedding_model = %(model)s,
                    embedding_dims = %(dims)s
                WHERE id = %(id)s AND tenant_id = %(tenant_id)s
                """,
                {
                    "vec": vector_literal(embedding),
                    "model": embedding_model,
                    "dims": len(embedding),
                    "id": section_id,
                    "tenant_id": clean_tid,
                },
            )

    async def upsert_kb_sections_batch(
        self, sections: Sequence[KBSection]
    ) -> dict[tuple[str, str, str, int], str]:
        """Insert or update a batch of knowledge sections grouped by tenant_id.
        Keyed by (tenant_id, doc_id, citation, version).
        Returns a mapping of (tenant_id, doc_id, citation, version) -> section_id.
        """
        if not sections:
            return {}

        # Deduplicate within batch by (tenant_id, doc_id, citation, version) keeping latest
        deduped: dict[tuple[str, str, str, int], KBSection] = {}
        for s in sections:
            deduped[(s.tenant_id, s.doc_id, s.citation, s.version)] = s
        by_tenant: dict[str, list[KBSection]] = {}
        for sec in deduped.values():
            by_tenant.setdefault(sec.tenant_id, []).append(sec)

        chunk_size = 100
        section_id_map: dict[tuple[str, str, str, int], str] = {}
        ownership_where = _kb_conflict_ownership_where("kb_sections")

        for tid, tenant_sections in by_tenant.items():
            async with self._conn(tenant_id=tid) as conn:
                for i in range(0, len(tenant_sections), chunk_size):
                    chunk = tenant_sections[i : i + chunk_size]
                    params: dict[str, Any] = {
                        "tenants": [sec.tenant_id for sec in chunk],
                        "projects": [sec.project_id for sec in chunk],
                        "domains": [sec.domain_id for sec in chunk],
                        "scopes": [sec.scope for sec in chunk],
                        "agents": [sec.agent_id for sec in chunk],
                        "doc_ids": [sec.doc_id for sec in chunk],
                        "citations": [sec.citation for sec in chunk],
                        "texts": [sec.text for sec in chunk],
                        "versions": [sec.version for sec in chunk],
                        "modalities": [sec.modality for sec in chunk],
                        "blob_sha256s": [sec.blob_sha256 for sec in chunk],
                        "blob_uris": [sec.blob_uri for sec in chunk],
                        "media_metadatas": [
                            json.dumps(thaw_mapping(sec.media_metadata)) for sec in chunk
                        ],
                    }
                    rows = await (
                        await conn.execute(
                            _compose_static_sql(
                                """
                                INSERT INTO kb_sections
                                    (tenant_id, project_id, domain_id, scope, agent_id, doc_id,
                                     citation, text, version, modality, blob_sha256, blob_uri,
                                     media_metadata)
                                SELECT * FROM UNNEST(
                                    %(tenants)s::text[],
                                    %(projects)s::text[],
                                    %(domains)s::text[],
                                    %(scopes)s::text[],
                                    %(agents)s::text[],
                                    %(doc_ids)s::text[],
                                    %(citations)s::text[],
                                    %(texts)s::text[],
                                    %(versions)s::int[],
                                    %(modalities)s::text[],
                                    %(blob_sha256s)s::text[],
                                    %(blob_uris)s::text[],
                                    %(media_metadatas)s::jsonb[]
                                )
                                ON CONFLICT (tenant_id, doc_id, citation, version)
                                    DO UPDATE SET
                                        project_id = EXCLUDED.project_id,
                                        domain_id = COALESCE(
                                            EXCLUDED.domain_id, kb_sections.domain_id
                                        ),
                                        scope = EXCLUDED.scope,
                                        agent_id = EXCLUDED.agent_id,
                                        text = EXCLUDED.text,
                                        embedding = CASE
                                            WHEN kb_sections.text IS DISTINCT FROM EXCLUDED.text
                                                THEN NULL
                                            ELSE kb_sections.embedding
                                        END,
                                        embedding_model = CASE
                                            WHEN kb_sections.text IS DISTINCT FROM EXCLUDED.text
                                                THEN NULL
                                            ELSE kb_sections.embedding_model
                                        END,
                                        embedding_dims = CASE
                                            WHEN kb_sections.text IS DISTINCT FROM EXCLUDED.text
                                                THEN NULL
                                            ELSE kb_sections.embedding_dims
                                        END,
                                        modality = EXCLUDED.modality,
                                        blob_sha256 = COALESCE(
                                            EXCLUDED.blob_sha256, kb_sections.blob_sha256
                                        ),
                                        blob_uri = COALESCE(
                                            EXCLUDED.blob_uri, kb_sections.blob_uri
                                        ),
                                        media_metadata = EXCLUDED.media_metadata
                                    WHERE {ownership_where}
                                RETURNING id, tenant_id, doc_id, citation, version
                                """,
                                ownership_where=ownership_where,
                            ),
                            params,
                        )
                    ).fetchall()
                    if len(rows) != len(chunk):
                        raise ValidationError(
                            "Cross-project kb_section batch collision: one or more sections "
                            "are already owned by another project in this tenant"
                        )
                    for row in rows:
                        key = (
                            str(row["tenant_id"]),
                            str(row["doc_id"]),
                            str(row["citation"]),
                            int(row["version"]),
                        )
                        section_id_map[key] = str(row["id"])

        return section_id_map

    async def get_max_kb_section_versions(
        self, tenant_id: str, doc_ids: Sequence[str], *, project_id: str | None = None
    ) -> dict[str, int]:
        """Return highest persisted section version per doc_id within tenant/project."""
        clean_docs = [d for d in dict.fromkeys(doc_ids) if d]
        if not clean_docs:
            return {}
        async with self._conn(tenant_id=tenant_id) as conn:
            rows = await (
                await conn.execute(
                    """
                    SELECT doc_id, MAX(version)::integer AS max_ver
                    FROM kb_sections
                    WHERE tenant_id = %(t)s
                      AND doc_id = ANY(%(docs)s::text[])
                      AND (%(p)s::text IS NULL OR project_id = %(p)s)
                    GROUP BY doc_id
                    """,
                    {"t": tenant_id, "docs": clean_docs, "p": project_id},
                )
            ).fetchall()
        return {str(row["doc_id"]): int(row["max_ver"] or 0) for row in rows}

    async def delete_kb_sections_for_doc(
        self,
        tenant_id: str,
        doc_id: str,
        *,
        project_id: str | None = None,
        exclude_version: int | None = None,
    ) -> int:
        """Delete kb_sections for a document within a tenant (optionally excluding a version)."""
        async with self._conn(tenant_id=tenant_id) as conn:
            res = await conn.execute(
                """
                DELETE FROM kb_sections
                WHERE tenant_id = %(t)s
                  AND doc_id = %(d)s
                  AND (%(p)s::text IS NULL OR project_id = %(p)s)
                  AND (%(exclude_v)s::integer IS NULL OR version <> %(exclude_v)s)
                RETURNING id
                """,
                {"t": tenant_id, "d": doc_id, "p": project_id, "exclude_v": exclude_version},
            )
            rows = await res.fetchall()
            return len(rows)

    async def set_kb_sections_embedding_batch(
        self,
        updates: Sequence[tuple[str, Sequence[float]]],
        *,
        tenant_id: str,
        embedding_model: str = "text-embedding-3-small",
    ) -> None:
        """Store 1536-dim vector projections for multiple kb_sections within tenant_id."""
        if not updates:
            return
        clean_tid = str(tenant_id or "").strip()
        if not clean_tid:
            raise ValidationError("set_kb_sections_embedding_batch requires non-empty tenant_id")
        for _sid, emb in updates:
            _validate_embedding_dims(emb, "kb_section embedding")
        async with self._conn(tenant_id=clean_tid) as conn:
            for section_id, embedding in updates:
                await conn.execute(
                    """
                    UPDATE kb_sections
                    SET embedding = %(vec)s::vector,
                        embedding_model = %(model)s,
                        embedding_dims = %(dims)s
                    WHERE id = %(id)s AND tenant_id = %(tenant_id)s
                    """,
                    {
                        "vec": vector_literal(embedding),
                        "model": embedding_model,
                        "dims": len(embedding),
                        "id": section_id,
                        "tenant_id": clean_tid,
                    },
                )

    async def get_kb_section(self, section_id: str, *, tenant_id: str) -> KBSection | None:
        """Fetch a knowledge section by ID; None when missing or cross-tenant."""
        async with self._conn(tenant_id=tenant_id) as conn:
            row = await (
                await conn.execute(
                    """
                    SELECT id, tenant_id, project_id, domain_id, scope, agent_id, doc_id, citation,
                           text, version, modality, blob_sha256, blob_uri, media_metadata
                    FROM kb_sections
                    WHERE id = %(id)s AND tenant_id = %(tenant_id)s
                    """,
                    {"id": section_id, "tenant_id": tenant_id},
                )
            ).fetchone()
            if row is None:
                return None
            return _hydrate_kb_section(row)

    async def list_kb_sections(
        self,
        *,
        tenant_id: str,
        project_id: str | None = None,
        doc_id: str | None = None,
        limit: int = 50,
    ) -> list[KBSection]:
        """List knowledge sections matching tenant and optional project/doc_id."""
        params: dict[str, Any] = {
            "tenant_id": tenant_id,
            "project_id": project_id,
            "doc_id": doc_id,
            "limit": limit,
        }
        async with self._conn(tenant_id=tenant_id) as conn:
            rows = await (
                await conn.execute(
                    """
                    SELECT id, tenant_id, project_id, domain_id, scope, agent_id, doc_id, citation,
                           text, version, modality, blob_sha256, blob_uri, media_metadata
                    FROM kb_sections
                    WHERE tenant_id = %(tenant_id)s
                      AND (%(project_id)s::text IS NULL OR project_id = %(project_id)s)
                      AND (%(doc_id)s::text IS NULL OR doc_id = %(doc_id)s)
                    ORDER BY doc_id, version DESC, id
                    LIMIT %(limit)s
                    """,
                    params,
                )
            ).fetchall()
            return [_hydrate_kb_section(row) for row in rows]

    async def search_kb_sections(
        self,
        query: str,
        *,
        tenant_id: str,
        project_id: str | None = None,
        domain_id: str | None = None,
        agent_id: str | None = None,
        query_vec: Sequence[float] | None = None,
        scope: str | None = None,
        allowed_scopes: Sequence[str] | None = None,
        modality: str | None = None,
        hybrid_rrf: bool = True,
        rrf_k: int = 60,
        limit: int = 5,
    ) -> list[KBSection]:
        """Search knowledge sections via 3-leg hybrid RRF (vector + ts_lex BM25 + citation)
        or lexical fallback. Guarded by tenant, scope hierarchy, and optional modality."""
        if query_vec is not None:
            _validate_embedding_dims(query_vec, "query_vec")
        candidate_pool = max(limit * 4, 20)
        scope_flags = _scope_flags(allowed_scopes, requested_scope=scope)
        kb_where = _kb_scope_where()
        scoped_kb_where = _kb_scope_where()
        params: dict[str, Any] = {
            "tenant_id": tenant_id,
            "project_id": project_id,
            "domain_id": domain_id,
            "agent_id": agent_id,
            "scope": scope,
            "modality": modality,
            "limit": limit,
            "candidate_pool": candidate_pool,
            "rrf_k": max(1, int(rrf_k)),
            "query": query,
            "qv": vector_literal(query_vec) if query_vec is not None else None,
            "pattern": f"%{_escape_ilike(query)}%",
            **scope_flags,
        }
        async with self._conn(tenant_id=tenant_id) as conn:
            if query_vec is not None and hybrid_rrf and query.strip():
                rows = await (
                    await conn.execute(
                        _compose_static_sql(
                            """
                            WITH scoped_kb AS (
                                SELECT id, tenant_id, project_id, domain_id, scope, agent_id,
                                       doc_id, citation, text, version, modality, blob_sha256,
                                       blob_uri, media_metadata, embedding, ts_lex
                                FROM kb_sections
                                WHERE tenant_id = %(tenant_id)s
                                  AND (%(modality)s::text IS NULL OR modality = %(modality)s)
                                  AND {scoped_kb_where}
                            ),
                            vec_leg AS (
                                SELECT id,
                                       ROW_NUMBER() OVER (
                                           ORDER BY embedding <=> %(qv)s::vector ASC, id ASC
                                       ) AS r_vec
                                FROM scoped_kb
                                WHERE embedding IS NOT NULL
                                ORDER BY embedding <=> %(qv)s::vector ASC, id ASC
                                LIMIT %(candidate_pool)s
                            ),
                            lex_leg AS (
                                SELECT id,
                                       ROW_NUMBER() OVER (
                                           ORDER BY
                                               ts_rank_cd(
                                                   COALESCE(ts_lex, to_tsvector('english', '')),
                                                   websearch_to_tsquery('english', %(query)s)
                                               ) DESC,
                                               (
                                                   CASE
                                                       WHEN citation ILIKE %(pattern)s
                                                            ESCAPE '\\'
                                                       THEN 1
                                                       ELSE 0
                                                   END
                                               ) DESC,
                                               version DESC,
                                               id ASC
                                       ) AS r_lex
                                FROM scoped_kb
                                WHERE (
                                    ts_lex @@ websearch_to_tsquery('english', %(query)s)
                                    OR text ILIKE %(pattern)s ESCAPE '\\'
                                    OR citation ILIKE %(pattern)s ESCAPE '\\'
                                )
                                LIMIT %(candidate_pool)s
                            ),
                            rrf_fused AS (
                                SELECT COALESCE(v.id, l.id) AS sec_id,
                                       COALESCE(1.0 / (%(rrf_k)s + v.r_vec), 0.0)
                                       + COALESCE(1.0 / (%(rrf_k)s + l.r_lex), 0.0) AS rrf_score
                                FROM vec_leg v
                                FULL OUTER JOIN lex_leg l ON v.id = l.id
                            )
                            SELECT s.id, s.tenant_id, s.project_id, s.domain_id, s.scope,
                                   s.agent_id, s.doc_id, s.citation, s.text, s.version,
                                   s.modality, s.blob_sha256, s.blob_uri, s.media_metadata
                            FROM rrf_fused f
                            JOIN scoped_kb s ON s.id = f.sec_id
                            ORDER BY f.rrf_score DESC, s.version DESC, s.id ASC
                            LIMIT %(limit)s
                            """,
                            scoped_kb_where=scoped_kb_where,
                        ),
                        params,
                    )
                ).fetchall()
            elif query_vec is not None:
                rows = await (
                    await conn.execute(
                        _compose_static_sql(
                            """
                            SELECT id, tenant_id, project_id, domain_id, scope, agent_id, doc_id,
                                   citation, text, version, modality, blob_sha256, blob_uri,
                                   media_metadata
                            FROM kb_sections
                            WHERE tenant_id = %(tenant_id)s
                              AND (%(modality)s::text IS NULL OR modality = %(modality)s)
                              AND {kb_where}
                              AND embedding IS NOT NULL
                            ORDER BY embedding <=> %(qv)s::vector
                            LIMIT %(limit)s
                            """,
                            kb_where=kb_where,
                        ),
                        params,
                    )
                ).fetchall()
            else:
                rows = await (
                    await conn.execute(
                        _compose_static_sql(
                            """
                            SELECT id, tenant_id, project_id, domain_id, scope, agent_id, doc_id,
                                   citation, text, version, modality, blob_sha256, blob_uri,
                                   media_metadata
                            FROM kb_sections
                            WHERE tenant_id = %(tenant_id)s
                              AND (%(modality)s::text IS NULL OR modality = %(modality)s)
                              AND {kb_where}
                              AND (
                                  ts_lex @@ websearch_to_tsquery('english', %(query)s)
                                  OR text ILIKE %(pattern)s ESCAPE '\\'
                                  OR citation ILIKE %(pattern)s ESCAPE '\\'
                              )
                            ORDER BY
                                ts_rank_cd(
                                    COALESCE(ts_lex, to_tsvector('english', '')),
                                    websearch_to_tsquery('english', %(query)s)
                                ) DESC,
                                (
                                    CASE
                                        WHEN citation ILIKE %(pattern)s ESCAPE '\\' THEN 1
                                        ELSE 0
                                    END
                                ) DESC,
                                version DESC,
                                id ASC
                            LIMIT %(limit)s
                            """,
                            kb_where=kb_where,
                        ),
                        params,
                    )
                ).fetchall()
            return [_hydrate_kb_section(row) for row in rows]

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
        async with self._conn(tenant_id=tenant_id) as conn:
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
        async with self._conn(tenant_id=tenant_id) as conn:
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
        async with self._conn(tenant_id=tenant_id) as conn:
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
                if row is None:
                    raise RuntimeError("INSERT INTO memory_retrievals did not return id")
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
        ownership_where = _graph_entity_conflict_where()
        async with self._conn(tenant_id=entity.tenant_id) as conn:
            row = await (
                await conn.execute(
                    _compose_static_sql(
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
                            WHERE {ownership_where}
                        RETURNING id
                        """,
                        ownership_where=ownership_where,
                    ),
                    {
                        "id": entity.id,
                        "tenant_id": entity.tenant_id,
                        "domain_id": entity.domain_id,
                        "entity_type": entity.entity_type,
                        "name": entity.name,
                        "properties": json.dumps(thaw_mapping(entity.properties)),
                    },
                )
            ).fetchone()
            if row is None:
                raise ValidationError(
                    f"Cross-project graph_entity collision: '{entity.id}' is owned by another "
                    f"scope, domain, or project in tenant '{entity.tenant_id}'"
                )

    async def upsert_graph_relation(self, relation: GraphRelation) -> str:
        """Insert or update a canonical graph relationship (ADR-01/03/05)."""
        ownership_where = _graph_relation_conflict_where()
        async with self._conn(tenant_id=relation.tenant_id) as conn:
            row = await (
                await conn.execute(
                    _compose_static_sql(
                        """
                        INSERT INTO graph_relations (
                            tenant_id, src_id, dst_id, rel_type, properties
                        )
                        VALUES (%(tenant_id)s, %(src_id)s, %(dst_id)s, %(rel_type)s, %(properties)s)
                        ON CONFLICT (tenant_id, src_id, dst_id, rel_type)
                            DO UPDATE SET properties = EXCLUDED.properties
                            WHERE {ownership_where}
                        RETURNING id
                        """,
                        ownership_where=ownership_where,
                    ),
                    {
                        "tenant_id": relation.tenant_id,
                        "src_id": relation.src_id,
                        "dst_id": relation.dst_id,
                        "rel_type": relation.rel_type,
                        "properties": json.dumps(thaw_mapping(relation.properties)),
                    },
                )
            ).fetchone()
            if row is None:
                raise ValidationError(
                    f"Cross-project graph_relation collision: '{relation.src_id}'->"
                    f"'{relation.dst_id}' ({relation.rel_type}) is owned by another "
                    "scope or project"
                )
            return str(row["id"])

    async def bulk_upsert_graph_entities(self, entities: Sequence[GraphEntity]) -> int:
        """Bulk upsert canonical graph entities grouped by tenant_id with collision checks."""
        if not entities:
            return 0
        deduped: dict[tuple[str, str], GraphEntity] = {}
        for e in entities:
            deduped[(e.tenant_id, e.id)] = e
        by_tenant: dict[str, list[GraphEntity]] = {}
        for e in deduped.values():
            by_tenant.setdefault(e.tenant_id, []).append(e)

        ownership_where = _graph_entity_conflict_where()
        chunk_size = 200
        total_written = 0
        for tid, tenant_entities in by_tenant.items():
            async with self._conn(tenant_id=tid) as conn:
                async with conn.transaction():
                    for i in range(0, len(tenant_entities), chunk_size):
                        chunk = tenant_entities[i : i + chunk_size]
                        rows = await (
                            await conn.execute(
                                _compose_static_sql(
                                    """
                                    INSERT INTO graph_entities
                                        (id, tenant_id, domain_id, entity_type, name,
                                         properties, updated_at)
                                    SELECT *, NOW() FROM UNNEST(
                                        %(ids)s::text[],
                                        %(tenants)s::text[],
                                        %(domains)s::text[],
                                        %(types)s::text[],
                                        %(names)s::text[],
                                        %(props)s::jsonb[]
                                    )
                                    ON CONFLICT (tenant_id, id)
                                        DO UPDATE SET
                                            domain_id = CASE
                                                WHEN COALESCE(
                                                    (EXCLUDED.properties->>'stub')::boolean, false
                                                ) AND NOT COALESCE(
                                                    (graph_entities.properties->>'stub')::boolean,
                                                    false
                                                )
                                                THEN graph_entities.domain_id
                                                ELSE EXCLUDED.domain_id
                                            END,
                                            entity_type = CASE
                                                WHEN COALESCE(
                                                    (EXCLUDED.properties->>'stub')::boolean, false
                                                ) AND NOT COALESCE(
                                                    (graph_entities.properties->>'stub')::boolean,
                                                    false
                                                )
                                                THEN graph_entities.entity_type
                                                ELSE EXCLUDED.entity_type
                                            END,
                                            name = CASE
                                                WHEN COALESCE(
                                                    (EXCLUDED.properties->>'stub')::boolean, false
                                                ) AND NOT COALESCE(
                                                    (graph_entities.properties->>'stub')::boolean,
                                                    false
                                                )
                                                THEN graph_entities.name
                                                ELSE EXCLUDED.name
                                            END,
                                            properties = CASE
                                                WHEN COALESCE(
                                                    (EXCLUDED.properties->>'stub')::boolean, false
                                                ) AND NOT COALESCE(
                                                    (graph_entities.properties->>'stub')::boolean,
                                                    false
                                                )
                                                THEN graph_entities.properties
                                                ELSE EXCLUDED.properties
                                            END,
                                            updated_at = NOW()
                                        WHERE {ownership_where}
                                    RETURNING id
                                    """,
                                    ownership_where=ownership_where,
                                ),
                                {
                                    "ids": [e.id for e in chunk],
                                    "tenants": [e.tenant_id for e in chunk],
                                    "domains": [e.domain_id for e in chunk],
                                    "types": [e.entity_type for e in chunk],
                                    "names": [e.name for e in chunk],
                                    "props": [
                                        json.dumps(thaw_mapping(e.properties)) for e in chunk
                                    ],
                                },
                            )
                        ).fetchall()
                        if len(rows) != len(chunk):
                            raise ValidationError(
                                "Cross-project graph_entity batch collision: one or more entities "
                                f"are owned by another scope, domain, or project in tenant '{tid}'"
                            )
                        total_written += len(rows)
        return total_written

    async def bulk_upsert_graph_relations(self, relations: Sequence[GraphRelation]) -> int:
        """Bulk insert or update canonical graph relationships grouped by tenant_id."""
        if not relations:
            return 0
        deduped: dict[tuple[str, str, str, str], GraphRelation] = {}
        for r in relations:
            deduped[(r.tenant_id, r.src_id, r.dst_id, r.rel_type)] = r
        by_tenant: dict[str, list[GraphRelation]] = {}
        for r in deduped.values():
            by_tenant.setdefault(r.tenant_id, []).append(r)

        ownership_where = _graph_relation_conflict_where()
        chunk_size = 200
        total_written = 0
        for tid, tenant_rels in by_tenant.items():
            async with self._conn(tenant_id=tid) as conn:
                async with conn.transaction():
                    for i in range(0, len(tenant_rels), chunk_size):
                        chunk = tenant_rels[i : i + chunk_size]
                        rows = await (
                            await conn.execute(
                                _compose_static_sql(
                                    """
                                    INSERT INTO graph_relations (
                                        tenant_id, src_id, dst_id, rel_type, properties
                                    )
                                    SELECT * FROM UNNEST(
                                        %(tenants)s::text[],
                                        %(srcs)s::text[],
                                        %(dsts)s::text[],
                                        %(rels)s::text[],
                                        %(props)s::jsonb[]
                                    )
                                    ON CONFLICT (tenant_id, src_id, dst_id, rel_type)
                                        DO UPDATE SET properties = EXCLUDED.properties
                                        WHERE {ownership_where}
                                    RETURNING id
                                    """,
                                    ownership_where=ownership_where,
                                ),
                                {
                                    "tenants": [r.tenant_id for r in chunk],
                                    "srcs": [r.src_id for r in chunk],
                                    "dsts": [r.dst_id for r in chunk],
                                    "rels": [r.rel_type for r in chunk],
                                    "props": [
                                        json.dumps(thaw_mapping(r.properties)) for r in chunk
                                    ],
                                },
                            )
                        ).fetchall()
                        if len(rows) != len(chunk):
                            raise ValidationError(
                                "Cross-project graph_relation batch collision: one or more edges "
                                f"are owned by another scope or project in tenant '{tid}'"
                            )
                        total_written += len(rows)
        return total_written

    async def delete_graph_entity(self, id: str, *, tenant_id: str = "default") -> None:
        """Delete a canonical graph entity. Foreign keys cascade-delete relations."""
        async with self._conn(tenant_id=tenant_id) as conn:
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
        async with self._conn(tenant_id=tenant_id) as conn:
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

    async def purge_stale_graph_entities(
        self,
        *,
        tenant_id: str = "default",
        project_id: str = "default",
        active_ids: Sequence[str],
    ) -> list[str]:
        """Purge canonical graph entities for a tenant and project no longer in active_ids.

        Foreign keys automatically cascade-delete associated relations.
        Returns the list of deleted entity IDs.
        """
        async with self._conn(tenant_id=tenant_id) as conn:
            async with conn.transaction():
                query = """
                SELECT id FROM graph_entities
                WHERE tenant_id = %(tenant_id)s
                  AND properties->>'project_id' = %(project_id)s
                  AND entity_type NOT IN ('DOCUMENT', 'SECTION', 'MEDIA_BLOB', 'CONCEPT')
                  AND NOT id = ANY(%(active_ids)s::text[])
                """
                cur = await conn.execute(
                    query,
                    {
                        "tenant_id": tenant_id,
                        "project_id": project_id,
                        "active_ids": list(active_ids),
                    },
                )
                rows = await cur.fetchall()
                stale_ids = [r["id"] for r in rows]
                if not stale_ids:
                    return []

                await conn.execute(
                    """
                    DELETE FROM graph_entities
                    WHERE tenant_id = %(tenant_id)s
                      AND id = ANY(%(stale_ids)s::text[])
                    """,
                    {"tenant_id": tenant_id, "stale_ids": stale_ids},
                )
                return stale_ids

    async def get_graph_entity(self, id: str, *, tenant_id: str = "default") -> GraphEntity | None:
        """Retrieve a canonical graph entity by id and tenant_id."""
        async with self._conn(tenant_id=tenant_id) as conn:
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
        self,
        *,
        tenant_id: str = "default",
        entity_type: str | None = None,
        project_id: str | None = None,
    ) -> list[GraphEntity]:
        """List canonical graph entities with optional entity_type and project_id filter."""
        params: dict[str, Any] = {
            "tenant_id": tenant_id,
            "entity_type": entity_type or None,
            "project_id": project_id,
        }

        async with self._conn(tenant_id=tenant_id) as conn:
            rows = await (
                await conn.execute(
                    """
                    SELECT id, tenant_id, domain_id, entity_type, name, properties
                    FROM graph_entities
                    WHERE tenant_id = %(tenant_id)s
                      AND (%(entity_type)s::text IS NULL OR entity_type = %(entity_type)s)
                      AND (
                          %(project_id)s::text IS NULL
                          OR properties->>'project_id' = %(project_id)s
                      )
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
        params: dict[str, Any] = {
            "tenant_id": tenant_id,
            "eid": entity_id or None,
        }

        async with self._conn(tenant_id=tenant_id) as conn:
            rows = await (
                await conn.execute(
                    """
                    SELECT id, tenant_id, src_id, dst_id, rel_type, properties
                    FROM graph_relations
                    WHERE tenant_id = %(tenant_id)s
                      AND (
                          %(eid)s::text IS NULL
                          OR src_id = %(eid)s
                          OR dst_id = %(eid)s
                      )
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

    async def find_neighbors(
        self,
        *,
        id: str,
        tenant_id: str = "default",
        rel: str | None = None,
        depth: int = 1,
        project_id: str | None = None,
        domain_id: str | None = None,
        allowed_scopes: Sequence[str] | None = None,
    ) -> Sequence[Mapping[str, Any]]:
        """Return bidirectional neighbor nodes reachable from `id` within `depth` hops."""
        safe_depth = max(1, min(depth, 6))
        prefixed_id = (
            f"{project_id}:{id}" if project_id and not id.startswith(f"{project_id}:") else id
        )
        scope_flags = _scope_flags(
            allowed_scopes, requested_scope=("domain" if domain_id else "project")
        )
        src_vis = _graph_node_visibility_where("src_e")
        dst_vis = _graph_node_visibility_where("dst_e")
        mid_vis = _graph_node_visibility_where("mid_e")
        nxt_vis = _graph_node_visibility_where("nxt_e")
        end_vis = _graph_node_visibility_where("e")
        edge_vis = _graph_edge_visibility_where("r")
        async with self._conn(tenant_id=tenant_id) as conn:
            rows = await (
                await conn.execute(
                    _compose_static_sql(
                        """
                        WITH RECURSIVE walk AS (
                            SELECT
                                CASE
                                    WHEN r.src_id IN (%(id)s, %(prefixed_id)s) THEN r.dst_id
                                    ELSE r.src_id
                                END AS neighbor_id,
                                1 AS depth,
                                ARRAY[r.src_id, r.dst_id] AS path
                            FROM graph_relations r
                            JOIN graph_entities src_e
                              ON src_e.tenant_id = r.tenant_id AND src_e.id = r.src_id
                            JOIN graph_entities dst_e
                              ON dst_e.tenant_id = r.tenant_id AND dst_e.id = r.dst_id
                            WHERE r.tenant_id = %(tenant_id)s
                              AND (
                                  r.src_id IN (%(id)s, %(prefixed_id)s)
                                  OR r.dst_id IN (%(id)s, %(prefixed_id)s)
                              )
                              AND (%(rel)s::text IS NULL OR r.rel_type = %(rel)s)
                              AND {src_vis}
                              AND {dst_vis}
                              AND {edge_vis}
                            UNION ALL
                            SELECT
                                CASE
                                    WHEN r.src_id = w.neighbor_id THEN r.dst_id
                                    ELSE r.src_id
                                END AS neighbor_id,
                                w.depth + 1 AS depth,
                                w.path || (
                                    CASE
                                        WHEN r.src_id = w.neighbor_id THEN r.dst_id
                                        ELSE r.src_id
                                    END
                                ) AS path
                            FROM graph_relations r
                            JOIN walk w
                              ON (r.src_id = w.neighbor_id OR r.dst_id = w.neighbor_id)
                            JOIN graph_entities mid_e
                              ON mid_e.tenant_id = r.tenant_id AND mid_e.id = w.neighbor_id
                            JOIN graph_entities nxt_e
                              ON nxt_e.tenant_id = r.tenant_id
                             AND nxt_e.id = (
                                 CASE
                                     WHEN r.src_id = w.neighbor_id THEN r.dst_id
                                     ELSE r.src_id
                                 END
                             )
                            WHERE r.tenant_id = %(tenant_id)s
                              AND w.depth < %(max_depth)s
                              AND (%(rel)s::text IS NULL OR r.rel_type = %(rel)s)
                              AND NOT (nxt_e.id = ANY(w.path))
                              AND {mid_vis}
                              AND {nxt_vis}
                              AND {edge_vis}
                        )
                        SELECT DISTINCT e.id, e.domain_id, e.entity_type, e.name, e.properties
                        FROM walk w
                        JOIN graph_entities e
                          ON e.tenant_id = %(tenant_id)s AND e.id = w.neighbor_id
                        WHERE e.id <> %(id)s AND e.id <> %(prefixed_id)s
                          AND {end_vis}
                        ORDER BY e.id ASC
                        """,
                        src_vis=src_vis,
                        dst_vis=dst_vis,
                        edge_vis=edge_vis,
                        mid_vis=mid_vis,
                        nxt_vis=nxt_vis,
                        end_vis=end_vis,
                    ),
                    {
                        "tenant_id": tenant_id,
                        "id": id,
                        "prefixed_id": prefixed_id,
                        "rel": rel,
                        "project_id": project_id,
                        "domain_id": domain_id,
                        "has_scope_filter": allowed_scopes is not None,
                        **scope_flags,
                        "max_depth": safe_depth,
                    },
                )
            ).fetchall()
            return [_hydrate_graph_node(row, tenant_id) for row in rows]

    async def find_ancestors(
        self,
        *,
        id: str,
        tenant_id: str = "default",
        depth: int = 6,
        project_id: str | None = None,
        domain_id: str | None = None,
        allowed_scopes: Sequence[str] | None = None,
    ) -> Sequence[Mapping[str, Any]]:
        """Return upstream ancestor nodes reachable from `id` (directed out-edges src -> dst)."""
        safe_depth = max(1, min(depth, 10))
        prefixed_id = (
            f"{project_id}:{id}" if project_id and not id.startswith(f"{project_id}:") else id
        )
        scope_flags = _scope_flags(
            allowed_scopes, requested_scope=("domain" if domain_id else "project")
        )
        src_vis = _graph_node_visibility_where("src_e")
        dst_vis = _graph_node_visibility_where("dst_e")
        mid_vis = _graph_node_visibility_where("mid_e")
        nxt_vis = _graph_node_visibility_where("nxt_e")
        end_vis = _graph_node_visibility_where("e")
        edge_vis = _graph_edge_visibility_where("r")
        async with self._conn(tenant_id=tenant_id) as conn:
            rows = await (
                await conn.execute(
                    _compose_static_sql(
                        """
                        WITH RECURSIVE lineage AS (
                            SELECT r.dst_id, 1 AS depth, ARRAY[r.src_id] AS path
                            FROM graph_relations r
                            JOIN graph_entities src_e
                              ON src_e.tenant_id = r.tenant_id AND src_e.id = r.src_id
                            JOIN graph_entities dst_e
                              ON dst_e.tenant_id = r.tenant_id AND dst_e.id = r.dst_id
                            WHERE r.tenant_id = %(tenant_id)s
                              AND (r.src_id = %(id)s OR r.src_id = %(prefixed_id)s)
                              AND {src_vis}
                              AND {dst_vis}
                              AND {edge_vis}
                            UNION ALL
                            SELECT r.dst_id, l.depth + 1, l.path || r.src_id
                            FROM graph_relations r
                            JOIN lineage l ON r.src_id = l.dst_id
                            JOIN graph_entities mid_e
                              ON mid_e.tenant_id = r.tenant_id AND mid_e.id = r.src_id
                            JOIN graph_entities nxt_e
                              ON nxt_e.tenant_id = r.tenant_id AND nxt_e.id = r.dst_id
                            WHERE r.tenant_id = %(tenant_id)s
                              AND l.depth < %(max_depth)s
                              AND NOT (r.dst_id = ANY(l.path))
                              AND {mid_vis}
                              AND {nxt_vis}
                              AND {edge_vis}
                        )
                        SELECT DISTINCT e.id, e.domain_id, e.entity_type, e.name, e.properties
                        FROM lineage l
                        JOIN graph_entities e ON e.tenant_id = %(tenant_id)s AND e.id = l.dst_id
                        WHERE e.id <> %(id)s AND e.id <> %(prefixed_id)s
                          AND {end_vis}
                        ORDER BY e.id ASC
                        """,
                        src_vis=src_vis,
                        dst_vis=dst_vis,
                        edge_vis=edge_vis,
                        mid_vis=mid_vis,
                        nxt_vis=nxt_vis,
                        end_vis=end_vis,
                    ),
                    {
                        "tenant_id": tenant_id,
                        "id": id,
                        "prefixed_id": prefixed_id,
                        "project_id": project_id,
                        "domain_id": domain_id,
                        "has_scope_filter": allowed_scopes is not None,
                        **scope_flags,
                        "max_depth": safe_depth,
                    },
                )
            ).fetchall()
            return [_hydrate_graph_node(row, tenant_id) for row in rows]

    async def find_descendants(
        self,
        *,
        id: str,
        tenant_id: str = "default",
        depth: int = 6,
        project_id: str | None = None,
        domain_id: str | None = None,
        allowed_scopes: Sequence[str] | None = None,
    ) -> Sequence[Mapping[str, Any]]:
        """Return downstream descendant nodes reachable from `id` (directed in-edges dst <- src)."""
        safe_depth = max(1, min(depth, 10))
        prefixed_id = (
            f"{project_id}:{id}" if project_id and not id.startswith(f"{project_id}:") else id
        )
        scope_flags = _scope_flags(
            allowed_scopes, requested_scope=("domain" if domain_id else "project")
        )
        src_vis = _graph_node_visibility_where("src_e")
        dst_vis = _graph_node_visibility_where("dst_e")
        mid_vis = _graph_node_visibility_where("mid_e")
        nxt_vis = _graph_node_visibility_where("nxt_e")
        end_vis = _graph_node_visibility_where("e")
        edge_vis = _graph_edge_visibility_where("r")
        async with self._conn(tenant_id=tenant_id) as conn:
            rows = await (
                await conn.execute(
                    _compose_static_sql(
                        """
                        WITH RECURSIVE blast AS (
                            SELECT r.src_id, 1 AS depth, ARRAY[r.dst_id] AS path
                            FROM graph_relations r
                            JOIN graph_entities src_e
                              ON src_e.tenant_id = r.tenant_id AND src_e.id = r.src_id
                            JOIN graph_entities dst_e
                              ON dst_e.tenant_id = r.tenant_id AND dst_e.id = r.dst_id
                            WHERE r.tenant_id = %(tenant_id)s
                              AND (r.dst_id = %(id)s OR r.dst_id = %(prefixed_id)s)
                              AND {src_vis}
                              AND {dst_vis}
                              AND {edge_vis}
                            UNION ALL
                            SELECT r.src_id, b.depth + 1, b.path || r.dst_id
                            FROM graph_relations r
                            JOIN blast b ON r.dst_id = b.src_id
                            JOIN graph_entities mid_e
                              ON mid_e.tenant_id = r.tenant_id AND mid_e.id = r.dst_id
                            JOIN graph_entities nxt_e
                              ON nxt_e.tenant_id = r.tenant_id AND nxt_e.id = r.src_id
                            WHERE r.tenant_id = %(tenant_id)s
                              AND b.depth < %(max_depth)s
                              AND NOT (r.src_id = ANY(b.path))
                              AND {mid_vis}
                              AND {nxt_vis}
                              AND {edge_vis}
                        )
                        SELECT DISTINCT e.id, e.domain_id, e.entity_type, e.name, e.properties
                        FROM blast b
                        JOIN graph_entities e ON e.tenant_id = %(tenant_id)s AND e.id = b.src_id
                        WHERE e.id <> %(id)s AND e.id <> %(prefixed_id)s
                          AND {end_vis}
                        ORDER BY e.id ASC
                        """,
                        src_vis=src_vis,
                        dst_vis=dst_vis,
                        edge_vis=edge_vis,
                        mid_vis=mid_vis,
                        nxt_vis=nxt_vis,
                        end_vis=end_vis,
                    ),
                    {
                        "tenant_id": tenant_id,
                        "id": id,
                        "prefixed_id": prefixed_id,
                        "project_id": project_id,
                        "domain_id": domain_id,
                        "has_scope_filter": allowed_scopes is not None,
                        **scope_flags,
                        "max_depth": safe_depth,
                    },
                )
            ).fetchall()
            return [_hydrate_graph_node(row, tenant_id) for row in rows]

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
        # and this is the only barrier before table lookup.
        count_queries = {
            "episodes": "SELECT count(*) AS n FROM episodes WHERE tenant_id = %(t)s",
            "kb_sections": "SELECT count(*) AS n FROM kb_sections WHERE tenant_id = %(t)s",
            "graph_entities": "SELECT count(*) AS n FROM graph_entities WHERE tenant_id = %(t)s",
            "graph_relations": "SELECT count(*) AS n FROM graph_relations WHERE tenant_id = %(t)s",
        }
        query_sql = count_queries.get(table)
        if query_sql is None:
            raise ValueError(f"refusing to interpolate table name: {table!r}")
        async with self._conn(tenant_id=tenant_id) as conn:
            row = await (
                await conn.execute(
                    query_sql,
                    {"t": tenant_id},
                )
            ).fetchone()
            if row is None:
                raise RuntimeError("SELECT count(*) returned no row")
            return int(row["n"])

    async def upsert_kb_document(self, doc: KBDocument) -> str:
        """Insert or update a canonical L0 documentation/runbook record in kb_documents."""
        ownership_where = _kb_conflict_ownership_where("kb_documents")
        async with self._conn(tenant_id=doc.tenant_id) as conn:
            row = await (
                await conn.execute(
                    _compose_static_sql(
                        """
                        INSERT INTO kb_documents (
                            tenant_id, doc_id, project_id, domain_id, scope, agent_id, repo_path,
                            title, kind, namespace, status, autonomy_tier, frontmatter,
                            watched_paths, trigger_alerts, last_verified, review_ttl_days,
                            git_commit_sha, updated_at
                        )
                        VALUES (
                            %(tenant_id)s, %(doc_id)s, %(project_id)s, %(domain_id)s, %(scope)s,
                            %(agent_id)s, %(repo_path)s, %(title)s, %(kind)s, %(namespace)s,
                            %(status)s, %(autonomy_tier)s, %(frontmatter)s, %(watched_paths)s,
                            %(trigger_alerts)s, %(last_verified)s, %(review_ttl_days)s,
                            %(git_commit_sha)s, NOW()
                        )
                        ON CONFLICT (tenant_id, doc_id) DO UPDATE SET
                            project_id = EXCLUDED.project_id,
                            domain_id = COALESCE(EXCLUDED.domain_id, kb_documents.domain_id),
                            scope = EXCLUDED.scope,
                            agent_id = EXCLUDED.agent_id,
                            repo_path = EXCLUDED.repo_path,
                            title = EXCLUDED.title,
                            kind = EXCLUDED.kind,
                            namespace = EXCLUDED.namespace,
                            status = EXCLUDED.status,
                            autonomy_tier = EXCLUDED.autonomy_tier,
                            frontmatter = EXCLUDED.frontmatter,
                            watched_paths = EXCLUDED.watched_paths,
                            trigger_alerts = EXCLUDED.trigger_alerts,
                            last_verified = EXCLUDED.last_verified,
                            review_ttl_days = EXCLUDED.review_ttl_days,
                            git_commit_sha = EXCLUDED.git_commit_sha,
                            updated_at = NOW()
                        WHERE {ownership_where}
                        RETURNING doc_id
                        """,
                        ownership_where=ownership_where,
                    ),
                    {
                        "tenant_id": doc.tenant_id,
                        "doc_id": doc.doc_id,
                        "project_id": doc.project_id,
                        "domain_id": doc.domain_id,
                        "scope": doc.scope,
                        "agent_id": doc.agent_id,
                        "repo_path": doc.repo_path,
                        "title": doc.title,
                        "kind": doc.kind,
                        "namespace": doc.namespace,
                        "status": doc.status,
                        "autonomy_tier": doc.autonomy_tier,
                        "frontmatter": json.dumps(thaw_mapping(doc.frontmatter)),
                        "watched_paths": list(doc.watched_paths),
                        "trigger_alerts": list(doc.trigger_alerts),
                        "last_verified": doc.last_verified,
                        "review_ttl_days": doc.review_ttl_days,
                        "git_commit_sha": doc.git_commit_sha,
                    },
                )
            ).fetchone()
            if row is None:
                raise ValidationError(
                    f"Cross-project doc_id collision: '{doc.doc_id}' is already owned "
                    f"by another project in tenant '{doc.tenant_id}'"
                )
        return doc.doc_id

    async def upsert_kb_runbook_steps(
        self,
        tenant_id: str,
        doc_id: str,
        steps: Sequence[KBRunbookStep],
    ) -> int:
        """Atomically replace stale steps for (tenant_id, doc_id) and upsert current steps."""
        async with self._conn(tenant_id=tenant_id) as conn:
            async with conn.transaction():
                await conn.execute(
                    "DELETE FROM kb_runbook_steps "
                    "WHERE tenant_id = %(tenant_id)s AND doc_id = %(doc_id)s",
                    {"tenant_id": tenant_id, "doc_id": doc_id},
                )
                if not steps:
                    return 0
                async with conn.cursor() as cur:
                    await cur.executemany(
                        """
                        INSERT INTO kb_runbook_steps (
                            tenant_id, step_uid, doc_id, step_order, step_id,
                            heading_path, tier, mode, expect_exit, timeout_sec,
                            command_body, prose_context, rollback_step
                        )
                        VALUES (
                            %(tenant_id)s, %(step_uid)s, %(doc_id)s, %(step_order)s,
                            %(step_id)s, %(heading_path)s, %(tier)s, %(mode)s,
                            %(expect_exit)s, %(timeout_sec)s, %(command_body)s,
                            %(prose_context)s, %(rollback_step)s
                        )
                        ON CONFLICT (tenant_id, doc_id, step_uid) DO UPDATE SET
                            step_order = EXCLUDED.step_order,
                            step_id = EXCLUDED.step_id,
                            heading_path = EXCLUDED.heading_path,
                            tier = EXCLUDED.tier,
                            mode = EXCLUDED.mode,
                            expect_exit = EXCLUDED.expect_exit,
                            timeout_sec = EXCLUDED.timeout_sec,
                            command_body = EXCLUDED.command_body,
                            prose_context = EXCLUDED.prose_context,
                            rollback_step = EXCLUDED.rollback_step
                        """,
                        [
                            {
                                "tenant_id": tenant_id,
                                "step_uid": s.step_uid,
                                "doc_id": doc_id,
                                "step_order": s.step_order,
                                "step_id": s.step_id,
                                "heading_path": s.heading_path,
                                "tier": s.tier,
                                "mode": s.mode,
                                "expect_exit": s.expect_exit,
                                "timeout_sec": s.timeout_sec,
                                "command_body": s.command_body,
                                "prose_context": s.prose_context,
                                "rollback_step": s.rollback_step,
                            }
                            for s in steps
                        ],
                    )
        return len(steps)

    async def delete_kb_document(
        self,
        tenant_id: str,
        doc_id: str,
        *,
        repo_path: str | None = None,
        project_id: str | None = None,
    ) -> int:
        """Delete a kb_documents record by doc_id or repo_path (cascades to kb_runbook_steps)."""
        async with self._conn(tenant_id=tenant_id) as conn:
            if repo_path:
                cur = await conn.execute(
                    """
                    DELETE FROM kb_documents
                    WHERE tenant_id = %(tenant_id)s
                      AND (%(project_id)s::text IS NULL OR project_id = %(project_id)s)
                      AND (doc_id = %(doc_id)s OR repo_path = %(repo_path)s)
                    """,
                    {
                        "tenant_id": tenant_id,
                        "doc_id": doc_id,
                        "repo_path": repo_path,
                        "project_id": project_id,
                    },
                )
            else:
                cur = await conn.execute(
                    """
                    DELETE FROM kb_documents
                    WHERE tenant_id = %(tenant_id)s
                      AND (%(project_id)s::text IS NULL OR project_id = %(project_id)s)
                      AND doc_id = %(doc_id)s
                    """,
                    {"tenant_id": tenant_id, "doc_id": doc_id, "project_id": project_id},
                )
            return int(cur.rowcount or 0)

    async def slice_runbook(
        self,
        *,
        tenant_id: str,
        project_id: str | None = None,
        domain_id: str | None = None,
        agent_id: str | None = None,
        scope: str = "project",
        allowed_scopes: Sequence[str] | None = None,
        doc_id: str | None = None,
        alert_id: str | None = None,
        max_tier: str | None = None,
    ) -> list[dict[str, Any]]:
        """Query ordered L0 runbook steps joined with kb_documents metadata."""
        if max_tier is not None and max_tier not in AUTONOMY_TIERS:
            raise ValidationError(
                f"Invalid max_tier '{max_tier}'; must be one of {sorted(AUTONOMY_TIERS)}"
            )
        allowed_tiers: list[str]
        if max_tier == "green":
            allowed_tiers = ["green"]
        elif max_tier == "yellow":
            allowed_tiers = ["green", "yellow"]
        else:
            allowed_tiers = ["green", "yellow", "red"]

        scope_flags = _scope_flags(allowed_scopes, requested_scope=scope)
        doc_scope_where = _kb_scope_where("d")
        params: dict[str, Any] = {
            "tenant_id": tenant_id,
            "tiers": allowed_tiers,
            "project_id": project_id,
            "domain_id": domain_id,
            "agent_id": agent_id,
            "scope": scope,
            "doc_id": doc_id,
            "alert_id": alert_id,
            **scope_flags,
        }
        async with self._conn(tenant_id=tenant_id) as conn:
            cur = await conn.execute(
                _compose_static_sql(
                    """
                    SELECT
                        s.step_uid, s.doc_id, d.project_id, d.scope, d.title, d.repo_path,
                        d.trigger_alerts, s.step_order, s.step_id, s.heading_path, s.tier,
                        s.mode, s.expect_exit, s.timeout_sec, s.command_body, s.prose_context,
                        s.rollback_step
                    FROM kb_runbook_steps s
                    JOIN kb_documents d
                      ON d.tenant_id = s.tenant_id AND d.doc_id = s.doc_id
                    WHERE d.tenant_id = %(tenant_id)s
                      AND d.status = 'active'
                      AND s.tier = ANY(%(tiers)s)
                      AND {doc_scope_where}
                      AND (%(doc_id)s::text IS NULL OR d.doc_id = %(doc_id)s)
                      AND (%(alert_id)s::text IS NULL OR %(alert_id)s = ANY(d.trigger_alerts))
                    ORDER BY d.doc_id ASC, s.step_order ASC
                    """,
                    doc_scope_where=doc_scope_where,
                ),
                params,
            )
            rows = await cur.fetchall()
            return [dict(r) for r in rows]

    async def get_learning_health_summary(
        self,
        *,
        tenant_id: str,
        project_id: str | None = None,
        agent_id: str | None = None,
        limit: int = 500,
    ) -> dict[str, Any]:
        """Compute closed-loop learning health metrics over recent episodes for tenant_id."""
        from engramory.core.experience import compute_learning_health_metrics

        safe_limit = max(1, min(int(limit), 5000))
        async with self._conn(tenant_id=tenant_id) as conn:
            rows = await (
                await conn.execute(
                    """
                    SELECT id, agent_id, project_id, domain_id, tenant_id, kind,
                           content_raw, metadata
                    FROM (
                        SELECT id, agent_id, project_id, domain_id, tenant_id, kind,
                               content_raw, metadata, ts
                        FROM episodes
                        WHERE tenant_id = %(tenant_id)s
                          AND (%(project_id)s::text IS NULL OR project_id = %(project_id)s::text)
                          AND (%(agent_id)s::text IS NULL OR agent_id = %(agent_id)s::text)
                        ORDER BY ts DESC, id DESC
                        LIMIT %(limit)s
                    ) recent_ep
                    ORDER BY ts ASC, id ASC
                    """,
                    {
                        "tenant_id": tenant_id,
                        "project_id": project_id,
                        "agent_id": agent_id,
                        "limit": safe_limit,
                    },
                )
            ).fetchall()
            episodes = [
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
            return compute_learning_health_metrics(episodes)

    async def log_consolidation_run(
        self,
        *,
        tenant_id: str,
        agent_id: str | None = None,
        stats: Mapping[str, Any] | None = None,
    ) -> str:
        """Persist a completed consolidation_runs audit entry scoped by tenant_id."""
        async with self._conn(tenant_id=tenant_id) as conn:
            row = await (
                await conn.execute(
                    """
                    INSERT INTO consolidation_runs (
                        tenant_id, agent_id, started_at, finished_at, stats
                    )
                    VALUES (%(tenant_id)s, %(agent_id)s, now(), now(), %(stats)s::jsonb)
                    RETURNING id
                    """,
                    {
                        "tenant_id": tenant_id,
                        "agent_id": agent_id,
                        "stats": json.dumps(thaw_mapping(stats)),
                    },
                )
            ).fetchone()
            if row is None:
                raise RuntimeError("INSERT INTO consolidation_runs did not return id")
            return str(row["id"])

    async def consolidate_memories(
        self,
        *,
        tenant_id: str,
        agent_id: str | None = None,
        project_id: str | None = None,
        domain_id: str | None = None,
        scope: str | None = None,
        expire_below_confidence: float = 0.2,
        min_age_days: int = 30,
    ) -> dict[str, Any]:
        """Soft-merge duplicate memories and expire stale low-confidence rows in one transaction."""
        async with self._conn(tenant_id=tenant_id) as conn:
            async with conn.transaction():
                cur_merge = await conn.execute(
                    """
                    WITH ranked AS (
                        SELECT id,
                               FIRST_VALUE(id) OVER (
                                   PARTITION BY scope, coalesce(agent_id, ''),
                                                coalesce(project_id, ''), coalesce(domain_id, ''),
                                                lower(btrim(coalesce(summary, content_raw)))
                                   ORDER BY confidence DESC, valid_from DESC, id DESC
                               ) AS winner_id,
                               ROW_NUMBER() OVER (
                                   PARTITION BY scope, coalesce(agent_id, ''),
                                                coalesce(project_id, ''), coalesce(domain_id, ''),
                                                lower(btrim(coalesce(summary, content_raw)))
                                   ORDER BY confidence DESC, valid_from DESC, id DESC
                               ) AS rn
                        FROM memories
                        WHERE tenant_id = %(tenant_id)s
                          AND valid_to IS NULL
                          AND status = 'active'
                          AND (%(agent_id)s::text IS NULL OR agent_id = %(agent_id)s)
                          AND (%(project_id)s::text IS NULL OR project_id = %(project_id)s)
                          AND (%(domain_id)s::text IS NULL OR domain_id = %(domain_id)s)
                          AND (%(scope)s::text IS NULL OR scope = %(scope)s)
                    )
                    UPDATE memories m
                    SET valid_to = now(),
                        status = 'superseded',
                        supersedes = COALESCE(m.supersedes, r.winner_id)
                    FROM ranked r
                    WHERE m.id = r.id
                      AND m.tenant_id = %(tenant_id)s
                      AND r.rn > 1
                      AND m.valid_to IS NULL
                    """,
                    {
                        "tenant_id": tenant_id,
                        "agent_id": agent_id,
                        "project_id": project_id,
                        "domain_id": domain_id,
                        "scope": scope,
                    },
                )
                merged = int(cur_merge.rowcount or 0)

                cur_exp = await conn.execute(
                    """
                    UPDATE memories
                    SET valid_to = now(),
                        status = 'superseded'
                    WHERE tenant_id = %(tenant_id)s
                      AND valid_to IS NULL
                      AND (status = 'advisory' OR confidence < %(min_conf)s)
                      AND valid_from < now() - make_interval(days => %(min_age_days)s)
                      AND (%(agent_id)s::text IS NULL OR agent_id = %(agent_id)s)
                      AND (%(project_id)s::text IS NULL OR project_id = %(project_id)s)
                      AND (%(domain_id)s::text IS NULL OR domain_id = %(domain_id)s)
                      AND (%(scope)s::text IS NULL OR scope = %(scope)s)
                    """,
                    {
                        "tenant_id": tenant_id,
                        "agent_id": agent_id,
                        "project_id": project_id,
                        "domain_id": domain_id,
                        "scope": scope,
                        "min_conf": float(expire_below_confidence),
                        "min_age_days": max(0, int(min_age_days)),
                    },
                )
                expired = int(cur_exp.rowcount or 0)

                stats_payload = {
                    "merged": merged,
                    "expired": expired,
                    "scope": scope,
                }
                run_row = await (
                    await conn.execute(
                        """
                        INSERT INTO consolidation_runs
                            (tenant_id, agent_id, started_at, finished_at, stats)
                        VALUES (%(tenant_id)s, %(agent_id)s, now(), now(), %(stats)s::jsonb)
                        RETURNING id
                        """,
                        {
                            "tenant_id": tenant_id,
                            "agent_id": agent_id,
                            "stats": json.dumps(stats_payload),
                        },
                    )
                ).fetchone()
                run_id = str(run_row["id"]) if run_row else None
                return {"merged": merged, "expired": expired, "run_id": run_id}

    async def import_agent_brain(
        self,
        *,
        tenant_id: str,
        profile: AgentProfile | None = None,
        memories: Sequence[tuple[Memory, Sequence[float] | None]] = (),
    ) -> list[str]:
        """Atomically persist agent profile and imported brain memories in a single transaction."""
        created_ids: list[str] = []
        async with self._conn(tenant_id=tenant_id) as conn:
            async with conn.transaction():
                if profile is not None:
                    await conn.execute(
                        """
                        INSERT INTO agent_profiles
                            (tenant_id, agent_id, display_name, standing_preferences)
                        VALUES (%(tenant_id)s, %(agent_id)s, %(display_name)s,
                                %(standing_preferences)s)
                        ON CONFLICT (tenant_id, agent_id) DO UPDATE
                            SET display_name = EXCLUDED.display_name,
                                standing_preferences = EXCLUDED.standing_preferences
                        """,
                        {
                            "tenant_id": profile.tenant_id,
                            "agent_id": profile.agent_id,
                            "display_name": profile.display_name,
                            "standing_preferences": json.dumps(
                                thaw_mapping(profile.standing_preferences)
                            ),
                        },
                    )
                for memory, embedding in memories:
                    params = _memory_params(memory)
                    params["embedding"] = (
                        vector_literal(embedding)
                        if embedding is not None and len(embedding) == 1536
                        else None
                    )
                    row = await (
                        await conn.execute(
                            """
                            INSERT INTO memories
                                (agent_id, project_id, domain_id, tenant_id, scope, type,
                                 content_raw, summary, embedding_model, embedding_dims,
                                 embedding, provenance, confidence, status, source_trust,
                                 supersedes)
                            VALUES (%(agent_id)s, %(project_id)s, %(domain_id)s, %(tenant_id)s,
                                    %(scope)s, %(type)s, %(content_raw)s, %(summary)s,
                                    %(embedding_model)s, %(embedding_dims)s,
                                    CASE
                                        WHEN %(embedding)s::text IS NOT NULL
                                            THEN %(embedding)s::vector
                                        ELSE NULL
                                    END,
                                    %(provenance)s, %(confidence)s, %(status)s,
                                    %(source_trust)s, %(supersedes)s)
                            RETURNING id
                            """,
                            params,
                        )
                    ).fetchone()
                    if row is None:
                        raise RuntimeError("INSERT INTO memories did not return id")
                    mem_id = str(row["id"])
                    if memory.supersedes is not None:
                        await conn.execute(
                            """
                            UPDATE memories
                            SET valid_to = now(), status = 'superseded'
                            WHERE id = %(id)s AND tenant_id = %(tenant_id)s AND valid_to IS NULL
                            """,
                            {"id": memory.supersedes, "tenant_id": memory.tenant_id},
                        )
                    created_ids.append(mem_id)
        return created_ids


def _episode_params(episode: Episode) -> dict[str, Any]:
    raw_eid = episode.metadata.get("event_id") if isinstance(episode.metadata, Mapping) else None
    if raw_eid is None:
        event_id_val: str | None = None
    elif isinstance(raw_eid, bool):
        event_id_val = "true" if raw_eid else "false"
    else:
        cleaned_eid = str(raw_eid).strip()
        event_id_val = cleaned_eid if cleaned_eid else None
    return {
        "agent_id": episode.agent_id,
        "project_id": episode.project_id,
        "domain_id": episode.domain_id,
        "tenant_id": episode.tenant_id,
        "kind": episode.kind,
        "content_raw": episode.content_raw,
        "content_hash": episode.content_hash,
        "metadata": json.dumps(thaw_mapping(episode.metadata)),
        "event_id": event_id_val,
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
        "provenance": json.dumps(thaw_mapping(memory.provenance)),
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
        embedding_model=row["embedding_model"] or "none",
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


def _hydrate_kb_section(row: dict[str, Any]) -> KBSection:
    raw_meta = row.get("media_metadata")
    if isinstance(raw_meta, str):
        try:
            parsed_meta: dict[str, Any] = json.loads(raw_meta)
        except Exception:
            parsed_meta = {}
    elif isinstance(raw_meta, Mapping):
        parsed_meta = dict(raw_meta)
    else:
        parsed_meta = {}

    return KBSection(
        id=str(row["id"]),
        tenant_id=row["tenant_id"],
        project_id=row["project_id"],
        domain_id=row["domain_id"],
        scope=row["scope"],
        agent_id=str(row.get("agent_id") or "system"),
        doc_id=row["doc_id"],
        citation=row["citation"],
        text=row["text"],
        version=int(row["version"]),
        modality=str(row.get("modality") or "text"),
        blob_sha256=row.get("blob_sha256"),
        blob_uri=row.get("blob_uri"),
        media_metadata=parsed_meta,
    )
