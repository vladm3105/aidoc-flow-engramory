"""Graph projection worker — projects canonical PostgreSQL entities into Neo4j (ADR-01/03/05).

The graph engine is a rebuildable projection:
PostgreSQL remains the single source of truth for entities and relationships.
"""

from __future__ import annotations

import logging

from engramory.core.repository import Repository
from engramory.ports import GraphPort

logger = logging.getLogger(__name__)


_SDD_LAYERS = frozenset(
    {"BRD", "PRD", "EARS", "BDD", "ADR", "SPEC", "TDD", "TASK", "IPLAN", "CHG", "EVAL"}
)


async def project_entities(
    repo: Repository,
    graph: GraphPort,
    *,
    tenant_id: str = "default",
) -> int:
    """Project canonical graph entities from PostgreSQL into graph nodes."""
    entities = await repo.list_graph_entities(tenant_id=tenant_id)
    count = 0
    for entity in entities:
        props = {
            "name": entity.name,
            "domain_id": entity.domain_id,
            "tenant_id": entity.tenant_id,
            **entity.properties,
        }
        labels = [entity.entity_type]
        if entity.entity_type in _SDD_LAYERS or entity.properties.get("sdd"):
            labels.append("SDD")
        await graph.upsert_node(
            id=entity.id,
            labels=labels,
            props=props,
        )
        count += 1
    return count


async def project_relations(
    repo: Repository,
    graph: GraphPort,
    *,
    tenant_id: str = "default",
) -> int:
    """Project canonical graph relationships from PostgreSQL into graph edges."""
    relations = await repo.list_graph_relations(tenant_id=tenant_id)
    count = 0
    for rel in relations:
        props = {
            "tenant_id": rel.tenant_id,
            **rel.properties,
        }
        await graph.upsert_edge(
            src=rel.src_id,
            dst=rel.dst_id,
            rel=rel.rel_type,
            props=props,
        )
        count += 1
    return count


async def reproject_all(
    repo: Repository,
    graph: GraphPort,
    *,
    tenant_id: str = "default",
) -> tuple[int, int]:
    """Rebuild all graph projections from PostgreSQL for a tenant (disaster recovery / sync)."""
    if hasattr(graph, "init_schema"):
        await graph.init_schema()

    # Reconcile deletions if graph adapter supports purge
    if hasattr(graph, "purge_stale_nodes"):
        entities = await repo.list_graph_entities(tenant_id=tenant_id)
        canonical_ids = [e.id for e in entities]
        purged = await graph.purge_stale_nodes(tenant_id=tenant_id, active_ids=canonical_ids)
        if isinstance(purged, int) and purged > 0:
            logger.info("Purged %d stale graph nodes for tenant %s", purged, tenant_id)

    node_count = await project_entities(repo, graph, tenant_id=tenant_id)
    edge_count = await project_relations(repo, graph, tenant_id=tenant_id)
    logger.info(
        "Reprojected graph from PostgreSQL for tenant %s: %d nodes, %d edges",
        tenant_id,
        node_count,
        edge_count,
    )
    return node_count, edge_count
