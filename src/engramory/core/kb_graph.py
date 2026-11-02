"""Knowledge Base & SDD Graph Extractor & Hybrid Retrieval Helper (ADR-01/03/05).

Extracts topological relationship graphs from SDD specification layers
(01_BRD through 10_EVAL) and ADRs, stores them canonically in PostgreSQL,
and projects them into Neo4j for sub-millisecond context slicing and blast radius.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

import yaml

from engramory.core.models import GraphEntity, GraphRelation
from engramory.core.repository import Repository
from engramory.ports import GraphPort

logger = logging.getLogger(__name__)

TAG_RE = re.compile(
    r"@(?P<kind>spec|adr|bdd|ears|prd|brd|tdd|chg|eval|depends):\s*(?P<target>[A-Za-z0-9_.-]+)",
    re.IGNORECASE,
)

SDD_PREFIXES = (
    "BRD.",
    "PRD.",
    "EARS.",
    "BDD.",
    "ADR.",
    "SPEC.",
    "TDD.",
    "IPLAN.",
    "CHG.",
    "EVAL.",
)


def normalize_sdd_id(raw_id: str) -> str:
    """Normalize raw SDD identifiers, hashes, or file stems into canonical IDs."""
    cleaned = raw_id.strip()
    if not cleaned:
        return ""
    if cleaned.startswith("@"):
        parts = cleaned.split(":", 1)
        if len(parts) == 2:
            cleaned = parts[1].strip()

    upper_cleaned = cleaned.upper()
    for prefix in SDD_PREFIXES:
        if upper_cleaned.startswith(prefix):
            parts = cleaned.split(".")
            if len(parts) >= 2:
                return f"{parts[0].upper()}-{parts[1]}"

    if "_" in cleaned and not cleaned.startswith("0"):
        cleaned = cleaned.split("_")[0]

    return cleaned


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


def extract_sdd_graph(
    sdd_root: Path | str,
    *,
    tenant_id: str = "default",
    project_id: str = "core",
    domain_id: str = "core",
) -> tuple[list[GraphEntity], list[GraphRelation]]:
    """Parse entire SDD corpus across all 10 layers and extract entities & lineage relations."""
    root_dir = Path(sdd_root)
    entities_map: dict[str, GraphEntity] = {}
    relations_map: dict[tuple[str, str, str], str] = {}  # (src, dst, rel_type) -> rationale

    if not root_dir.exists():
        logger.warning("SDD root directory %s does not exist", root_dir)
        return [], []

    # Find candidate directories: either subdirectories or the root itself
    layer_dirs = [d for d in sorted(root_dir.iterdir()) if d.is_dir()]
    if not layer_dirs:
        layer_dirs = [root_dir]

    for layer_dir in layer_dirs:
        layer_folder = layer_dir.name
        # Deduce layer type from folder name (e.g. 01_BRD -> BRD, 08_IPLAN -> IPLAN, 09-CHG -> CHG)
        raw_type = layer_folder.split("_")[-1].replace("-", "").upper()
        if not raw_type or raw_type.isdigit():
            raw_type = "SDD"

        for file_path in sorted(layer_dir.glob("*.yaml")) + sorted(layer_dir.glob("*.md")):
            if file_path.name.endswith(".TEMPLATE.yaml") or file_path.name.endswith(
                "-TEMPLATE.yaml"
            ):
                continue

            try:
                content = file_path.read_text(encoding="utf-8")
            except Exception as exc:  # noqa: BLE001  # reason: unreadable file should be skipped
                logger.debug("Failed to read %s: %s", file_path, exc)
                continue

            data: dict[str, Any] = {}
            if file_path.suffix in (".yaml", ".yml"):
                try:
                    loaded = yaml.safe_load(content)
                    if isinstance(loaded, dict):
                        data = loaded
                except Exception as exc:  # noqa: BLE001  # reason: yaml parse failure logged and skipped
                    logger.debug("YAML load failed for %s: %s", file_path, exc)

            dc = data.get("document_control") or {}
            doc_id = (
                data.get("id")
                or dc.get("document_id")
                or dc.get("iplan_id")
                or dc.get("chg_id")
                or dc.get("spec_id")
            )
            if not doc_id:
                stem = file_path.stem
                doc_id = stem.split("_")[0] if "_" in stem else stem

            doc_id = normalize_sdd_id(str(doc_id))
            if not doc_id or doc_id in ("README", "HANDOFF", "IMPLEMENTATION"):
                continue

            entity_type = raw_type
            for known in (
                "BRD",
                "PRD",
                "EARS",
                "BDD",
                "ADR",
                "SPEC",
                "TDD",
                "IPLAN",
                "CHG",
                "EVAL",
            ):
                if doc_id.startswith(known):
                    entity_type = known
                    break

            title = data.get("title") or dc.get("title") or file_path.stem
            status = dc.get("status") or data.get("status") or "Active"
            author = dc.get("author") or "Framework"

            entity = GraphEntity(
                id=doc_id,
                entity_type=entity_type,
                name=str(title),
                tenant_id=tenant_id,
                domain_id=domain_id,
                properties={
                    "status": str(status),
                    "author": str(author),
                    "project_id": project_id,
                    "layer_folder": layer_folder,
                    "source_file": file_path.name,
                },
            )
            entities_map[doc_id] = entity

            # 1. Structural schema fields in YAML
            # source_spec in IPLAN
            if "source_spec" in dc or "source_spec" in data:
                spec_val = dc.get("source_spec") or data.get("source_spec")
                if spec_val:
                    dst = normalize_sdd_id(str(spec_val))
                    if dst and dst != doc_id:
                        relations_map[(doc_id, dst, "IMPLEMENTS")] = "source_spec"

            # source_document in BDD / EARS / SPEC
            src_doc = data.get("source_document") or dc.get("source_document")
            if src_doc:
                dst = normalize_sdd_id(str(src_doc))
                if dst and dst != doc_id:
                    rel_type = (
                        "SATISFIES"
                        if doc_id.startswith("SPEC")
                        else "SPECIFIES"
                        if doc_id.startswith("BDD")
                        else "ORIGINATES_FROM"
                    )
                    relations_map[(doc_id, dst, rel_type)] = "source_document"

            # architecture_decision in SPEC
            arch_dec = data.get("architecture_decision") or data.get("component_overview", {}).get(
                "architecture_decision"
            )
            if arch_dec:
                dst = normalize_sdd_id(str(arch_dec))
                if dst and dst != doc_id:
                    relations_map[(doc_id, dst, "CONSTRAINED_BY")] = "architecture_decision"

            # brd_reference in PRD
            brd_ref = data.get("brd_reference")
            if brd_ref:
                dst = normalize_sdd_id(str(brd_ref))
                if dst and dst != doc_id:
                    relations_map[(doc_id, dst, "FULFILLS")] = "brd_reference"

            # authoring_chg in IPLAN / document_control
            auth_chg = dc.get("authoring_chg") or data.get("chg_id")
            if auth_chg:
                dst = normalize_sdd_id(str(auth_chg))
                if dst and dst != doc_id:
                    relations_map[(dst, doc_id, "MODIFIES")] = "authoring_chg"

            # related_decisions.dependencies in ADR
            adr_deps = data.get("related_decisions", {}).get("dependencies", [])
            for dep in adr_deps:
                dst_adr = dep.get("adr")
                if dst_adr:
                    dst = normalize_sdd_id(str(dst_adr))
                    if dst and dst != doc_id:
                        rel_type = dep.get("relationship", "DEPENDS_ON").upper().replace(" ", "_")
                        relations_map[(doc_id, dst, rel_type)] = dep.get("rationale", "")

            # 2. Free-text lineage tag matching (@kind: TARGET)
            for m in TAG_RE.finditer(content):
                kind = m.group("kind").lower()
                target_id = normalize_sdd_id(m.group("target"))
                if not target_id or target_id == doc_id:
                    continue

                if kind == "spec":
                    rel = "IMPLEMENTS" if doc_id.startswith("IPLAN") else "DEPENDS_ON"
                elif kind == "adr":
                    rel = "CONSTRAINED_BY"
                elif kind == "bdd":
                    rel = "VERIFIES" if doc_id.startswith("TDD") else "SATISFIES"
                elif kind == "ears":
                    rel = "SATISFIES" if doc_id.startswith("SPEC") else "SPECIFIES"
                elif kind == "prd":
                    rel = "ORIGINATES_FROM"
                elif kind == "brd":
                    rel = "FULFILLS"
                elif kind == "tdd":
                    rel = "VERIFIES_WITH"
                elif kind == "depends":
                    rel = "DEPENDS_ON"
                elif kind == "chg":
                    rel = "MODIFIED_BY"
                else:
                    rel = "REFERENCES"

                key = (doc_id, target_id, rel)
                if key not in relations_map:
                    relations_map[key] = f"lineage_tag_@{kind}"

    relations: list[GraphRelation] = [
        GraphRelation(
            src_id=src,
            dst_id=dst,
            rel_type=rel,
            tenant_id=tenant_id,
            properties={"rationale": rationale, "project_id": project_id},
        )
        for (src, dst, rel), rationale in sorted(relations_map.items())
    ]

    return list(entities_map.values()), relations


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


async def ingest_sdd_knowledge_graph(
    repo: Repository,
    graph: GraphPort | None = None,
    *,
    sdd_root: Path | str = "sdd",
    tenant_id: str = "default",
    project_id: str = "core",
    domain_id: str = "core",
) -> tuple[int, int]:
    """Ingest complete multi-layer SDD corpus into PostgreSQL and project to Neo4j."""
    entities, relations = extract_sdd_graph(
        sdd_root,
        tenant_id=tenant_id,
        project_id=project_id,
        domain_id=domain_id,
    )

    # 1. Canonical PostgreSQL persistence (ADR-01)
    for entity in entities:
        await repo.upsert_graph_entity(entity)

    for relation in relations:
        await repo.upsert_graph_relation(relation)

    # 2. Rebuildable projection to Neo4j (ADR-05)
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
            labels = ["Entity", "SDD", entity.entity_type]
            await graph.upsert_node(id=entity.id, labels=labels, props=props)

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
        "Ingested %d SDD entities and %d relations for tenant %s (project %s)",
        len(entities),
        len(relations),
        tenant_id,
        project_id,
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
        clean_seed = normalize_sdd_id(seed_id)
        try:
            hits = await graph.neighbors(id=clean_seed, depth=depth)
            for hit in hits:
                nid = hit.get("id")
                if nid and nid not in all_neighbors:
                    all_neighbors[nid] = dict(hit)
        except Exception as exc:  # noqa: BLE001  # reason: graph expansion failure for seed must not abort overall traversal
            logger.debug("Graph neighbor expansion failed for %s: %s", clean_seed, exc)

    return list(all_neighbors.values())


async def get_sdd_context_slice(
    graph: GraphPort,
    task_id: str,
    *,
    depth: int = 6,
) -> list[dict[str, Any]]:
    """Retrieve upstream specification contract closure (ancestors) for an IPLAN task."""
    clean_task = normalize_sdd_id(task_id)
    ancestors = await graph.ancestors(id=clean_task, depth=depth)
    return [dict(a) for a in ancestors]


async def get_sdd_blast_radius(
    graph: GraphPort,
    doc_id: str,
    *,
    depth: int = 6,
) -> list[dict[str, Any]]:
    """Retrieve downstream dependent specifications, tasks, and tests affected by a change."""
    clean_doc = normalize_sdd_id(doc_id)
    descendants = await graph.descendants(id=clean_doc, depth=depth)
    return [dict(d) for d in descendants]
