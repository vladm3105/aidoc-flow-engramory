"""PgVectorAdapter — VectorPort over pgvector in the canonical Postgres.

The embedding lives in ``memories.embedding`` as a rebuildable projection
(ADR-05): upsert/delete touch ONLY the projection columns, never the canonical
row. Distance is cosine (``<=>``), matching the SPEC-03 rank-fusion leg.

The ``memories.embedding`` column is dimensionless until a deployment pins
EMBEDDING_DIMS (migration 0001 note) — fine for dev; ANN indexing arrives with
the pin.

Note: NaN/inf embeddings are rejected by pgvector as a DataException (fail-loud
programming error), NOT wrapped into StoreUnavailable — the fail-closed contract
(EARS.01.03.c900) covers connectivity only. Embedding producers (LLMPort) must
not emit non-finite values.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from engramory.core.repository import Repository, vector_literal

# Filter allowlist — a security boundary: filter keys become SQL column names.
_FILTER_COLUMNS = frozenset(
    {"tenant_id", "agent_id", "project_id", "domain_id", "scope", "status", "type"}
)


class PgVectorAdapter:
    """VectorPort implementation backed by the canonical memories table."""

    def __init__(self, dsn: str) -> None:
        self._repo = Repository(dsn)

    async def upsert(
        self,
        *,
        id: str,
        embedding: Sequence[float],
        metadata: Mapping[str, Any] | None = None,
    ) -> None:
        """Write/replace the projection for memory `id`. Optional tenant_id in
        metadata scopes the update to that tenant."""
        tenant_id = (metadata or {}).get("tenant_id")
        async with self._repo._conn() as conn:  # dev adapter shares the spine (SPEC-06)
            if tenant_id is not None:
                await conn.execute(
                    """
                    UPDATE memories
                    SET embedding = %(vec)s::vector, embedding_dims = %(dims)s
                    WHERE id = %(id)s AND tenant_id = %(tenant_id)s
                    """,
                    {
                        "vec": vector_literal(embedding),
                        "dims": len(embedding),
                        "id": id,
                        "tenant_id": str(tenant_id),
                    },
                )
            else:
                await conn.execute(
                    """
                    UPDATE memories
                    SET embedding = %(vec)s::vector, embedding_dims = %(dims)s
                    WHERE id = %(id)s
                    """,
                    {"vec": vector_literal(embedding), "dims": len(embedding), "id": id},
                )

    async def query(
        self,
        *,
        embedding: Sequence[float],
        k: int = 8,
        filter: Mapping[str, Any] | None = None,
    ) -> Sequence[tuple[str, float]]:
        """Up to k (memory_id, cosine_distance) pairs, closest first."""
        params: dict[str, Any] = {
            "vec": vector_literal(embedding),
            "k": k,
            "f_tenant_id": None,
            "f_agent_id": None,
            "f_project_id": None,
            "f_domain_id": None,
            "f_scope": None,
            "f_status": None,
            "f_type": None,
        }
        for key, value in (filter or {}).items():
            if key not in _FILTER_COLUMNS:
                raise ValueError(f"refusing to filter on unknown column: {key!r}")
            params[f"f_{key}"] = value
        async with self._repo._conn() as conn:
            rows = await (
                await conn.execute(
                    """
                    SELECT id, embedding <=> %(vec)s::vector AS distance
                    FROM memories
                    WHERE embedding IS NOT NULL
                      AND (%(f_tenant_id)s::text IS NULL OR tenant_id = %(f_tenant_id)s)
                      AND (%(f_agent_id)s::text IS NULL OR agent_id = %(f_agent_id)s)
                      AND (%(f_project_id)s::text IS NULL OR project_id = %(f_project_id)s)
                      AND (%(f_domain_id)s::text IS NULL OR domain_id = %(f_domain_id)s)
                      AND (%(f_scope)s::text IS NULL OR scope = %(f_scope)s)
                      AND (%(f_status)s::text IS NULL OR status = %(f_status)s)
                      AND (%(f_type)s::text IS NULL OR type = %(f_type)s)
                    ORDER BY distance
                    LIMIT %(k)s
                    """,
                    params,
                )
            ).fetchall()
            return [(str(row["id"]), float(row["distance"])) for row in rows]

    async def delete(self, *, id: str) -> None:
        """Remove the projection (idempotent). The canonical row is untouched."""
        async with self._repo._conn() as conn:
            await conn.execute(
                "UPDATE memories SET embedding = NULL, embedding_dims = NULL WHERE id = %(id)s",
                {"id": id},
            )
