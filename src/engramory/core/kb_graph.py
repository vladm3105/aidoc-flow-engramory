"""Knowledge Base Graph Extractor & Hybrid Retrieval Helper (ADR-01/03/05).

Extracts topological relationship graphs from ADRs and specifications,
stores them canonically in PostgreSQL, and projects them into Neo4j.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

from engramory.core.models import GraphEntity, GraphRelation
from engramory.core.repository import Repository
from engramory.ports import GraphPort

logger = logging.getLogger(__name__)


def extract_adr_graph(
    adr_dir: Path | str,
    *,
    tenant_id: str = "default",
    domain_id: str = "core",
) -> tuple[list[GraphEntity], list[GraphRelation]]:
    """Parse ADR YAML files and extract entities and directed dependency relations."""
    directory = Path(adr_dir)
    entities: list[GraphEntity] = []
    relations: list[GraphRelation] = []

    if not directory.exists():
        logger.warning("ADR directory %s does not exist", directory)
        return entities, relations

    for file_path in sorted(directory.glob("ADR-*.yaml")):
        try:
            with open(file_path, encoding="utf-8") as f:
                data = yaml.safe_load(f)
        except Exception as exc:  # noqa: BLE001  # reason: invalid adr yaml should be logged and skipped rather than crash batch
            logger.warning("Failed to parse %s: %s", file_path, exc)
            continue

        adr_id = data.get("id")
        title = data.get("title", file_path.stem)
        if not adr_id:
            continue

        status = data.get("document_control", {}).get("status", "Accepted")
        layer = data.get("metadata", {}).get("layer", 5)

        entity = GraphEntity(
            id=adr_id,
            entity_type="ADR",
            name=title,
            tenant_id=tenant_id,
            domain_id=domain_id,
            properties={
                "status": status,
                "layer": layer,
                "source_file": file_path.name,
            },
        )
        entities.append(entity)

        # Parse dependencies
        deps = data.get("related_decisions", {}).get("dependencies", [])
        for dep in deps:
            dst_adr = dep.get("adr")
            if not dst_adr:
                continue
            rel_type = dep.get("relationship", "DEPENDS_ON").upper().replace(" ", "_")
            rationale = dep.get("rationale", "")
            relations.append(
                GraphRelation(
                    src_id=adr_id,
                    dst_id=dst_adr,
                    rel_type=rel_type,
                    tenant_id=tenant_id,
                    properties={"rationale": rationale},
                )
            )

    return entities, relations


async def ingest_adr_knowledge_graph(
    repo: Repository,
    graph: GraphPort | None = None,
    *,
    adr_dir: Path | str = "sdd/05_ADR",
    tenant_id: str = "default",
    domain_id: str = "core",
) -> tuple[int, int]:
    """Ingest ADR dependency graph into PostgreSQL and project to Neo4j."""
    entities, relations = extract_adr_graph(adr_dir, tenant_id=tenant_id, domain_id=domain_id)

    # 1. Canonical PostgreSQL store
    for entity in entities:
        await repo.upsert_graph_entity(entity)

    for relation in relations:
        await repo.upsert_graph_relation(relation)

    # 2. Rebuildable projection to Neo4j
    if graph is not None:
        if hasattr(graph, "init_schema"):
            await graph.init_schema()

        for entity in entities:
            props = {
                "name": entity.name,
                "tenant_id": entity.tenant_id,
                "domain_id": entity.domain_id,
                **entity.properties,
            }
            await graph.upsert_node(id=entity.id, labels=[entity.entity_type], props=props)

        for relation in relations:
            props = {
                "tenant_id": relation.tenant_id,
                **relation.properties,
            }
            await graph.upsert_edge(
                src=relation.src_id,
                dst=relation.dst_id,
                rel=relation.rel_type,
                props=props,
            )

    logger.info(
        "Ingested %d ADR entities and %d relations for tenant %s",
        len(entities),
        len(relations),
        tenant_id,
    )
    return len(entities), len(relations)


async def expand_graph_context(
    graph: GraphPort,
    seed_ids: list[str],
    *,
    depth: int = 1,
) -> list[dict[str, Any]]:
    """Retrieve connected topological neighborhood for given seed nodes."""
    all_neighbors: dict[str, dict[str, Any]] = {}

    for seed_id in seed_ids:
        # Standardize seed ID (e.g. 'ADR-01' from 'ADR-01_postgres_spine')
        clean_seed = seed_id.split("_")[0] if "_" in seed_id else seed_id
        try:
            hits = await graph.neighbors(id=clean_seed, depth=depth)
            for hit in hits:
                nid = hit.get("id")
                if nid and nid not in all_neighbors:
                    all_neighbors[nid] = dict(hit)
        except Exception as exc:  # noqa: BLE001  # reason: graph expansion failure for seed must not abort overall traversal
            logger.debug("Graph neighbor expansion failed for %s: %s", clean_seed, exc)

    return list(all_neighbors.values())
