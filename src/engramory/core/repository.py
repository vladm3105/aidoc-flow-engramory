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

_KB_SECTION_COLUMNS = "id, tenant_id, project_id, domain_id, scope, doc_id, citation, text, version"


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
        """Persist idempotently by content hash or event_id within scope (BDD.01.03.c900).

        Returns the episode id — the existing row's id on duplicate submission.
        """
        params = _episode_params(episode)
        async with self._conn() as conn:
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
                            AND metadata ? 'event_id'
                            AND metadata->>'event_id' = %(event_id)s
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
            if row is None:
                raise RuntimeError("INSERT INTO memories did not return id")
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
            async with conn.transaction():
                row = await (
                    await conn.execute(
                        """
                        UPDATE memory_retrievals
                        SET feedback = %(outcome)s, feedback_at = now()
                        WHERE id = %(id)s AND tenant_id = %(tenant_id)s
                        RETURNING memory_id
                        """,
                        {"id": retrieval_id, "outcome": outcome, "tenant_id": tenant_id},
                    )
                ).fetchone()
                if row is None:
                    raise KeyError(retrieval_id)
                memory_id = row["memory_id"]
                if outcome == "useful":
                    await conn.execute(
                        """
                        UPDATE memories
                        SET confidence = LEAST(1.0, confidence + 0.05)
                        WHERE id = %(memory_id)s AND tenant_id = %(tenant_id)s
                        """,
                        {"memory_id": memory_id, "tenant_id": tenant_id},
                    )
                elif outcome == "not_useful":
                    await conn.execute(
                        """
                        UPDATE memories
                        SET confidence = GREATEST(0.0, confidence - 0.10)
                        WHERE id = %(memory_id)s AND tenant_id = %(tenant_id)s
                        """,
                        {"memory_id": memory_id, "tenant_id": tenant_id},
                    )
                elif outcome == "harmful":
                    await conn.execute(
                        """
                        UPDATE memories
                        SET confidence = GREATEST(0.0, confidence - 0.30),
                            status = 'quarantined'
                        WHERE id = %(memory_id)s AND tenant_id = %(tenant_id)s
                        """,
                        {"memory_id": memory_id, "tenant_id": tenant_id},
                    )

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

    async def get_agent_profile(
        self, agent_id: str, *, tenant_id: str = "default"
    ) -> AgentProfile | None:
        """L3 identity row scoped by (tenant_id, agent_id), or None when absent."""
        async with self._conn() as conn:
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
        async with self._conn() as conn:
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
        query: str | None = None,
        query_vec: Sequence[float] | None = None,
        scopes: Sequence[str] | None = None,
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
        has_q = bool(query and query.strip())
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
            "q_like": f"%{query.strip()}%" if has_q and query is not None else None,
        }
        async with self._conn() as conn:
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
                      AND status = 'active'
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
                      CASE
                        WHEN %(qv)s::text IS NOT NULL AND embedding IS NOT NULL
                          THEN embedding <=> %(qv)s::vector
                        ELSE 2.0
                      END ASC,
                      CASE
                        WHEN %(q_like)s::text IS NOT NULL
                             AND (
                                 content_raw ILIKE %(q_like)s
                                 OR coalesce(summary, '') ILIKE %(q_like)s
                             )
                          THEN 1
                        ELSE 0
                      END DESC,
                      valid_from DESC
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
                        DO UPDATE SET
                            project_id = COALESCE(EXCLUDED.project_id, kb_sections.project_id),
                            domain_id = COALESCE(EXCLUDED.domain_id, kb_sections.domain_id),
                            scope = EXCLUDED.scope,
                            text = EXCLUDED.text
                        WHERE kb_sections.project_id IS NULL
                           OR kb_sections.project_id = EXCLUDED.project_id
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
        embedding: Sequence[float],
        embedding_model: str = "text-embedding-3-small",
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

    async def upsert_kb_sections_batch(
        self, sections: Sequence[KBSection]
    ) -> dict[tuple[str, str, str, int], str]:
        """Insert or update a batch of knowledge sections in a single statement.
        Keyed by (tenant_id, doc_id, citation, version).
        Returns a mapping of (tenant_id, doc_id, citation, version) -> section_id.
        """
        if not sections:
            return {}

        # Deduplicate within batch by (tenant_id, doc_id, citation, version) keeping latest
        deduped: dict[tuple[str, str, str, int], KBSection] = {}
        for s in sections:
            deduped[(s.tenant_id, s.doc_id, s.citation, s.version)] = s
        unique_sections = list(deduped.values())

        chunk_size = 100
        section_id_map: dict[tuple[str, str, str, int], str] = {}

        async with self._conn() as conn:
            for i in range(0, len(unique_sections), chunk_size):
                chunk = unique_sections[i : i + chunk_size]
                params: dict[str, Any] = {
                    "tenants": [sec.tenant_id for sec in chunk],
                    "projects": [sec.project_id for sec in chunk],
                    "domains": [sec.domain_id for sec in chunk],
                    "scopes": [sec.scope for sec in chunk],
                    "doc_ids": [sec.doc_id for sec in chunk],
                    "citations": [sec.citation for sec in chunk],
                    "texts": [sec.text for sec in chunk],
                    "versions": [sec.version for sec in chunk],
                }
                rows = await (
                    await conn.execute(
                        """
                        INSERT INTO kb_sections
                            (tenant_id, project_id, domain_id, scope, doc_id, citation,
                             text, version)
                        SELECT * FROM UNNEST(
                            %(tenants)s::text[],
                            %(projects)s::text[],
                            %(domains)s::text[],
                            %(scopes)s::text[],
                            %(doc_ids)s::text[],
                            %(citations)s::text[],
                            %(texts)s::text[],
                            %(versions)s::int[]
                        )
                        ON CONFLICT (tenant_id, doc_id, citation, version)
                            DO UPDATE SET
                                project_id = COALESCE(
                                    EXCLUDED.project_id, kb_sections.project_id
                                ),
                                domain_id = COALESCE(
                                    EXCLUDED.domain_id, kb_sections.domain_id
                                ),
                                scope = EXCLUDED.scope,
                                text = EXCLUDED.text
                            WHERE kb_sections.project_id IS NULL
                               OR kb_sections.project_id = EXCLUDED.project_id
                        RETURNING id, tenant_id, doc_id, citation, version
                        """,
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

    async def delete_kb_sections_for_doc(
        self, tenant_id: str, doc_id: str, *, project_id: str | None = None
    ) -> int:
        """Delete all kb_sections for a document within a tenant (and optional project_id)."""
        async with self._conn() as conn:
            res = await conn.execute(
                """
                DELETE FROM kb_sections
                WHERE tenant_id = %(t)s
                  AND doc_id = %(d)s
                  AND (%(p)s::text IS NULL OR project_id = %(p)s)
                RETURNING id
                """,
                {"t": tenant_id, "d": doc_id, "p": project_id},
            )
            rows = await res.fetchall()
            return len(rows)

    async def set_kb_sections_embedding_batch(
        self,
        updates: Sequence[tuple[str, Sequence[float]]],
        *,
        embedding_model: str = "text-embedding-3-small",
    ) -> None:
        """Store vector projections for multiple kb_sections."""
        if not updates:
            return
        async with self._conn() as conn:
            for section_id, embedding in updates:
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

    async def get_kb_section(self, section_id: str, *, tenant_id: str) -> KBSection | None:
        """Fetch a knowledge section by ID; None when missing or cross-tenant."""
        async with self._conn() as conn:
            row = await (
                await conn.execute(
                    """
                    SELECT id, tenant_id, project_id, domain_id, scope, doc_id,
                           citation, text, version
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
        async with self._conn() as conn:
            rows = await (
                await conn.execute(
                    """
                    SELECT id, tenant_id, project_id, domain_id, scope, doc_id,
                           citation, text, version
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
        query_vec: Sequence[float] | None = None,
        scope: str | None = None,
        limit: int = 5,
    ) -> list[KBSection]:
        """Search knowledge sections by vector similarity (if query_vec is provided)
        or by text matching (ILIKE fallback). Guarded by tenant and scope hierarchy."""
        params: dict[str, Any] = {
            "tenant_id": tenant_id,
            "project_id": project_id,
            "domain_id": domain_id,
            "scope": scope,
            "limit": limit,
            "qv": vector_literal(query_vec) if query_vec is not None else None,
            "pattern": f"%{query}%",
        }
        async with self._conn() as conn:
            if query_vec is not None:
                rows = await (
                    await conn.execute(
                        """
                        SELECT id, tenant_id, project_id, domain_id, scope, doc_id,
                               citation, text, version
                        FROM kb_sections
                        WHERE tenant_id = %(tenant_id)s
                          AND (
                              (
                                  %(scope)s::text = 'space'
                                  AND (
                                      scope IN ('domain', 'space')
                                      OR (
                                          %(project_id)s::text IS NOT NULL
                                          AND project_id = %(project_id)s
                                      )
                                  )
                              )
                              OR (
                                  %(scope)s::text = 'domain'
                                  AND (
                                      (
                                          scope = 'domain'
                                          AND domain_id IS NOT NULL
                                          AND domain_id = %(domain_id)s
                                      )
                                      OR scope = 'space'
                                      OR (
                                          %(project_id)s::text IS NOT NULL
                                          AND project_id = %(project_id)s
                                      )
                                  )
                              )
                              OR (
                                  coalesce(%(scope)s::text, '') NOT IN ('space', 'domain')
                                  AND (
                                      %(project_id)s::text IS NULL
                                      OR project_id = %(project_id)s
                                      OR scope = 'space'
                                  )
                                  AND (
                                      %(scope)s::text IS NULL
                                      OR scope IN (%(scope)s, 'space')
                                  )
                              )
                          )
                          AND embedding IS NOT NULL
                        ORDER BY embedding <=> %(qv)s::vector
                        LIMIT %(limit)s
                        """,
                        params,
                    )
                ).fetchall()
            else:
                rows = await (
                    await conn.execute(
                        """
                        SELECT id, tenant_id, project_id, domain_id, scope, doc_id,
                               citation, text, version
                        FROM kb_sections
                        WHERE tenant_id = %(tenant_id)s
                          AND (
                              (
                                  %(scope)s::text = 'space'
                                  AND (
                                      scope IN ('domain', 'space')
                                      OR (
                                          %(project_id)s::text IS NOT NULL
                                          AND project_id = %(project_id)s
                                      )
                                  )
                              )
                              OR (
                                  %(scope)s::text = 'domain'
                                  AND (
                                      (
                                          scope = 'domain'
                                          AND domain_id IS NOT NULL
                                          AND domain_id = %(domain_id)s
                                      )
                                      OR scope = 'space'
                                      OR (
                                          %(project_id)s::text IS NOT NULL
                                          AND project_id = %(project_id)s
                                      )
                                  )
                              )
                              OR (
                                  coalesce(%(scope)s::text, '') NOT IN ('space', 'domain')
                                  AND (
                                      %(project_id)s::text IS NULL
                                      OR project_id = %(project_id)s
                                      OR scope = 'space'
                                  )
                                  AND (
                                      %(scope)s::text IS NULL
                                      OR scope IN (%(scope)s, 'space')
                                  )
                              )
                          )
                          AND text ILIKE %(pattern)s
                        ORDER BY version DESC, id
                        LIMIT %(limit)s
                        """,
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
                    WHERE graph_entities.properties->>'project_id' IS NULL
                       OR graph_entities.properties->>'project_id' =
                          EXCLUDED.properties->>'project_id'
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
                        WHERE graph_relations.properties->>'project_id' IS NULL
                           OR graph_relations.properties->>'project_id' =
                              EXCLUDED.properties->>'project_id'
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
            if row is None:
                raise ValidationError(
                    f"Cross-project graph_relation collision: '{relation.src_id}'->"
                    f"'{relation.dst_id}' ({relation.rel_type}) is owned by another project"
                )
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
                            WHERE graph_entities.properties->>'project_id' IS NULL
                               OR graph_entities.properties->>'project_id' =
                                  EXCLUDED.properties->>'project_id'
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
                            WHERE graph_relations.properties->>'project_id' IS NULL
                               OR graph_relations.properties->>'project_id' =
                                  EXCLUDED.properties->>'project_id'
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
        async with self._conn() as conn:
            async with conn.transaction():
                query = """
                SELECT id FROM graph_entities
                WHERE tenant_id = %(tenant_id)s
                  AND properties->>'project_id' = %(project_id)s
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

        async with self._conn() as conn:
            rows = await (
                await conn.execute(
                    """
                    SELECT id, tenant_id, domain_id, entity_type, name, properties
                    FROM graph_entities
                    WHERE tenant_id = %(tenant_id)s
                      AND (%(entity_type)s::text IS NULL OR entity_type = %(entity_type)s)
                      AND (
                          %(project_id)s::text IS NULL
                          OR properties->>'project_id' IS NULL
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

        async with self._conn() as conn:
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

    async def find_ancestors(
        self,
        *,
        id: str,
        tenant_id: str = "default",
        depth: int = 6,
        project_id: str | None = None,
    ) -> Sequence[Mapping[str, Any]]:
        """Return upstream ancestor nodes reachable from `id` (directed out-edges src -> dst)."""
        safe_depth = max(1, min(depth, 10))
        prefixed_id = (
            f"{project_id}:{id}" if project_id and not id.startswith(f"{project_id}:") else id
        )
        async with self._conn() as conn:
            rows = await (
                await conn.execute(
                    """
                    WITH RECURSIVE lineage AS (
                        SELECT r.dst_id, 1 AS depth, ARRAY[r.src_id] AS path
                        FROM graph_relations r
                        JOIN graph_entities src_e
                          ON src_e.tenant_id = r.tenant_id AND src_e.id = r.src_id
                        WHERE r.tenant_id = %(tenant_id)s
                          AND (r.src_id = %(id)s OR r.src_id = %(prefixed_id)s)
                          AND (
                              %(project_id)s::text IS NULL
                              OR src_e.properties->>'project_id' IS NULL
                              OR src_e.properties->>'project_id' = %(project_id)s
                          )
                        UNION ALL
                        SELECT r.dst_id, l.depth + 1, l.path || r.src_id
                        FROM graph_relations r
                        JOIN lineage l ON r.src_id = l.dst_id
                        JOIN graph_entities mid_e
                          ON mid_e.tenant_id = r.tenant_id AND mid_e.id = r.src_id
                        WHERE r.tenant_id = %(tenant_id)s
                          AND l.depth < %(max_depth)s
                          AND NOT (r.dst_id = ANY(l.path))
                          AND (
                              %(project_id)s::text IS NULL
                              OR mid_e.properties->>'project_id' IS NULL
                              OR mid_e.properties->>'project_id' = %(project_id)s
                          )
                    )
                    SELECT DISTINCT e.id, e.entity_type, e.name, e.properties
                    FROM lineage l
                    JOIN graph_entities e ON e.tenant_id = %(tenant_id)s AND e.id = l.dst_id
                    WHERE e.id <> %(id)s AND e.id <> %(prefixed_id)s
                      AND (
                          %(project_id)s::text IS NULL
                          OR e.properties->>'project_id' IS NULL
                          OR e.properties->>'project_id' = %(project_id)s
                      )
                    ORDER BY e.id ASC
                    """,
                    {
                        "tenant_id": tenant_id,
                        "id": id,
                        "prefixed_id": prefixed_id,
                        "project_id": project_id,
                        "max_depth": safe_depth,
                    },
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
        project_id: str | None = None,
    ) -> Sequence[Mapping[str, Any]]:
        """Return downstream descendant nodes reachable from `id` (directed in-edges dst <- src)."""
        safe_depth = max(1, min(depth, 10))
        prefixed_id = (
            f"{project_id}:{id}" if project_id and not id.startswith(f"{project_id}:") else id
        )
        async with self._conn() as conn:
            rows = await (
                await conn.execute(
                    """
                    WITH RECURSIVE blast AS (
                        SELECT r.src_id, 1 AS depth, ARRAY[r.dst_id] AS path
                        FROM graph_relations r
                        JOIN graph_entities dst_e
                          ON dst_e.tenant_id = r.tenant_id AND dst_e.id = r.dst_id
                        WHERE r.tenant_id = %(tenant_id)s
                          AND (r.dst_id = %(id)s OR r.dst_id = %(prefixed_id)s)
                          AND (
                              %(project_id)s::text IS NULL
                              OR dst_e.properties->>'project_id' IS NULL
                              OR dst_e.properties->>'project_id' = %(project_id)s
                          )
                        UNION ALL
                        SELECT r.src_id, b.depth + 1, b.path || r.dst_id
                        FROM graph_relations r
                        JOIN blast b ON r.dst_id = b.src_id
                        JOIN graph_entities mid_e
                          ON mid_e.tenant_id = r.tenant_id AND mid_e.id = r.dst_id
                        WHERE r.tenant_id = %(tenant_id)s
                          AND b.depth < %(max_depth)s
                          AND NOT (r.src_id = ANY(b.path))
                          AND (
                              %(project_id)s::text IS NULL
                              OR mid_e.properties->>'project_id' IS NULL
                              OR mid_e.properties->>'project_id' = %(project_id)s
                          )
                    )
                    SELECT DISTINCT e.id, e.entity_type, e.name, e.properties
                    FROM blast b
                    JOIN graph_entities e ON e.tenant_id = %(tenant_id)s AND e.id = b.src_id
                    WHERE e.id <> %(id)s AND e.id <> %(prefixed_id)s
                      AND (
                          %(project_id)s::text IS NULL
                          OR e.properties->>'project_id' IS NULL
                          OR e.properties->>'project_id' = %(project_id)s
                      )
                    ORDER BY e.id ASC
                    """,
                    {
                        "tenant_id": tenant_id,
                        "id": id,
                        "prefixed_id": prefixed_id,
                        "project_id": project_id,
                        "max_depth": safe_depth,
                    },
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
        async with self._conn() as conn:
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
        async with self._conn() as conn:
            row = await (
                await conn.execute(
                    """
                    INSERT INTO kb_documents (
                        tenant_id, doc_id, project_id, domain_id, scope, repo_path,
                        title, kind, namespace, status, autonomy_tier, frontmatter,
                        watched_paths, trigger_alerts, last_verified, review_ttl_days,
                        git_commit_sha, updated_at
                    )
                    VALUES (
                        %(tenant_id)s, %(doc_id)s, %(project_id)s, %(domain_id)s, %(scope)s,
                        %(repo_path)s, %(title)s, %(kind)s, %(namespace)s, %(status)s,
                        %(autonomy_tier)s, %(frontmatter)s, %(watched_paths)s,
                        %(trigger_alerts)s, %(last_verified)s, %(review_ttl_days)s,
                        %(git_commit_sha)s, NOW()
                    )
                    ON CONFLICT (tenant_id, doc_id) DO UPDATE SET
                        project_id = COALESCE(EXCLUDED.project_id, kb_documents.project_id),
                        domain_id = COALESCE(EXCLUDED.domain_id, kb_documents.domain_id),
                        scope = EXCLUDED.scope,
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
                    WHERE kb_documents.project_id IS NULL
                       OR kb_documents.project_id = EXCLUDED.project_id
                    RETURNING doc_id
                    """,
                    {
                        "tenant_id": doc.tenant_id,
                        "doc_id": doc.doc_id,
                        "project_id": doc.project_id,
                        "domain_id": doc.domain_id,
                        "scope": doc.scope,
                        "repo_path": doc.repo_path,
                        "title": doc.title,
                        "kind": doc.kind,
                        "namespace": doc.namespace,
                        "status": doc.status,
                        "autonomy_tier": doc.autonomy_tier,
                        "frontmatter": json.dumps(dict(doc.frontmatter)),
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
        async with self._conn() as conn:
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
                        ON CONFLICT (tenant_id, step_uid) DO UPDATE SET
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
        async with self._conn() as conn:
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
        scope: str = "project",
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

        params: dict[str, Any] = {
            "tenant_id": tenant_id,
            "tiers": allowed_tiers,
            "project_id": project_id,
            "domain_id": domain_id,
            "scope": scope,
            "doc_id": doc_id,
            "alert_id": alert_id,
        }
        async with self._conn() as conn:
            cur = await conn.execute(
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
                  AND (
                      (
                          %(scope)s IN ('agent', 'project')
                          AND d.scope IN ('agent', 'project', 'space')
                          AND (
                              %(project_id)s::text IS NULL
                              OR d.project_id = %(project_id)s
                              OR d.scope = 'space'
                          )
                      )
                      OR (
                          %(scope)s = 'domain'
                          AND (
                              (
                                  d.scope = 'domain'
                                  AND d.domain_id IS NOT NULL
                                  AND d.domain_id = %(domain_id)s
                              )
                              OR d.scope = 'space'
                              OR (
                                  %(project_id)s::text IS NOT NULL
                                  AND d.project_id = %(project_id)s
                              )
                          )
                      )
                      OR (
                          %(scope)s = 'space'
                          AND (
                              d.scope IN ('domain', 'space')
                              OR (
                                  %(project_id)s::text IS NOT NULL
                                  AND d.project_id = %(project_id)s
                              )
                          )
                      )
                  )
                  AND (%(doc_id)s::text IS NULL OR d.doc_id = %(doc_id)s)
                  AND (%(alert_id)s::text IS NULL OR %(alert_id)s = ANY(d.trigger_alerts))
                ORDER BY d.doc_id ASC, s.step_order ASC
                """,
                params,
            )
            rows = await cur.fetchall()
            return [dict(r) for r in rows]

    async def get_learning_health_summary(
        self,
        *,
        tenant_id: str,
        project_id: str | None = None,
        limit: int = 500,
    ) -> dict[str, Any]:
        """Compute closed-loop learning health metrics over recent episodes for tenant_id."""
        from engramory.core.experience import compute_learning_health_metrics

        safe_limit = max(1, min(int(limit), 5000))
        async with self._conn() as conn:
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
                        ORDER BY ts DESC, id DESC
                        LIMIT %(limit)s
                    ) recent_ep
                    ORDER BY ts ASC, id ASC
                    """,
                    {
                        "tenant_id": tenant_id,
                        "project_id": project_id,
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


def _episode_params(episode: Episode) -> dict[str, Any]:
    event_id_val = (
        episode.metadata.get("event_id") if isinstance(episode.metadata, Mapping) else None
    )
    return {
        "agent_id": episode.agent_id,
        "project_id": episode.project_id,
        "domain_id": episode.domain_id,
        "tenant_id": episode.tenant_id,
        "kind": episode.kind,
        "content_raw": episode.content_raw,
        "content_hash": episode.content_hash,
        "metadata": json.dumps(dict(episode.metadata)),
        "event_id": str(event_id_val) if event_id_val else None,
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


def _hydrate_kb_section(row: dict[str, Any]) -> KBSection:
    return KBSection(
        id=str(row["id"]),
        tenant_id=row["tenant_id"],
        project_id=row["project_id"],
        domain_id=row["domain_id"],
        scope=row["scope"],
        doc_id=row["doc_id"],
        citation=row["citation"],
        text=row["text"],
        version=int(row["version"]),
    )
