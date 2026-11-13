"""Knowledge Base & SDD Graph Extractor & Hybrid Retrieval Helper (ADR-01/03/05).

Extracts topological relationship graphs from SDD specification layers
(01_BRD through 10_EVAL) and ADRs, stores them canonically in PostgreSQL,
and projects them into Neo4j for sub-millisecond context slicing and blast radius.
"""

from __future__ import annotations

import logging
import re
import subprocess  # nosec B404 # reason: read-only git remote origin lookup for project_id auto-detection
import tomllib
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import yaml

from engramory.core.models import GraphEntity, GraphRelation, KBBlob, KBSection
from engramory.core.repository import Repository
from engramory.ports import GraphPort

logger = logging.getLogger(__name__)

_SDD_LAYERS: frozenset[str] = frozenset(
    {"BRD", "PRD", "EARS", "BDD", "ADR", "SPEC", "TDD", "TASK", "IPLAN", "CHG", "EVAL"}
)
_KNOWLEDGE_ENTITY_TYPES: frozenset[str] = frozenset(
    {
        "DOCUMENT",
        "SECTION",
        "MEDIA_BLOB",
        "CONCEPT",
        "RUNBOOK",
        "RUNBOOK_STEP",
        "ALERT",
        "ARCHITECTURE",
        "REFERENCE",
        "GUIDE",
        "SERVICE",
    }
)
_SDD_REF_RE = re.compile(r"\b(?P<id>(?:BRD|PRD|EARS|BDD|ADR|SPEC|TDD|IPLAN|CHG|EVAL)-[0-9]{2,3})\b")
_WIKI_CONCEPT_RE = re.compile(r"\[\[(?P<concept>[^\[\]\n]{2,80})\]\]")


def entity_labels_for(entity: GraphEntity) -> list[str]:
    """Compute canonical Neo4j labels for a GraphEntity (GAP-06)."""
    labels = [entity.entity_type]
    if entity.entity_type in _SDD_LAYERS or entity.properties.get("sdd"):
        labels.append("SDD")
    elif entity.entity_type in _KNOWLEDGE_ENTITY_TYPES:
        labels.append("Knowledge")
    return labels


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


def _resolve_sdd_root(sdd_root: Path | str) -> Path:
    """Resolve SDD root or layer directory, transparently falling back from `sdd` to `docs/sdd`."""
    p = Path(sdd_root)
    if p.exists():
        return p
    if p.name == "sdd":
        alt = p.parent / "docs" / "sdd"
        if alt.exists():
            return alt
    if p.parent.name == "sdd":
        alt = p.parent.parent / "docs" / "sdd" / p.name
        if alt.exists():
            return alt
    return p


def normalize_sdd_id(raw_id: str) -> str:
    """Normalize raw SDD identifiers, hashes, or file stems into canonical IDs."""
    cleaned = raw_id.strip()
    if not cleaned:
        return ""
    if cleaned.startswith("@"):
        parts = cleaned.split(":", 1)
        if len(parts) == 2:
            cleaned = parts[1].strip()

    proj_prefix = ""
    if ":" in cleaned:
        p_head, p_tail = cleaned.split(":", 1)
        if p_head and p_tail:
            proj_prefix = f"{p_head}:"
            cleaned = p_tail.strip()

    upper_cleaned = cleaned.upper()
    for prefix in SDD_PREFIXES:
        if upper_cleaned.startswith(prefix):
            parts = cleaned.split(".")
            if len(parts) >= 2:
                return f"{proj_prefix}{parts[0].upper()}-{parts[1]}"

    if "_" in cleaned and not cleaned.startswith("0"):
        cleaned = cleaned.split("_")[0]

    return f"{proj_prefix}{cleaned}"


def extract_sdd_target_ids(raw_value: Any) -> list[str]:
    """Extract and normalize all distinct SDD target IDs from a field value."""
    if not raw_value:
        return []
    candidates = (
        [str(x) for x in raw_value if x] if isinstance(raw_value, list) else [str(raw_value)]
    )
    targets: list[str] = []
    for cand in candidates:
        matches = re.findall(
            r"\b(BRD|PRD|EARS|BDD|ADR|SPEC|TDD|IPLAN|CHG|EVAL)[-._]([0-9]+[a-zA-Z0-9_\-]*)\b",
            cand,
            re.IGNORECASE,
        )
        if matches:
            for prefix, suffix in matches:
                if suffix.upper() == "NN" or "TEMPLATE" in suffix.upper():
                    continue
                targets.append(normalize_sdd_id(f"{prefix.upper()}-{suffix}"))
        else:
            cleaned = cand.split()[0].rstrip(".,;")
            norm = normalize_sdd_id(cleaned)
            if norm and not norm.endswith("-NN") and "TEMPLATE" not in norm.upper():
                targets.append(norm)
    return list(dict.fromkeys(targets))


def extract_adr_graph(
    adr_dir: Path | str,
    *,
    tenant_id: str = "default",
    domain_id: str = "core",
) -> tuple[list[GraphEntity], list[GraphRelation]]:
    """Parse ADR YAML files and extract entities and directed dependency relations."""
    directory = _resolve_sdd_root(adr_dir)
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
        if not isinstance(data, dict):
            continue

        adr_id = data.get("id")
        title = data.get("title", file_path.stem)
        if not adr_id:
            continue

        status = (data.get("document_control") or {}).get("status", "Accepted")
        layer = (data.get("metadata") or {}).get("layer", 5)

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
        deps = (data.get("related_decisions") or {}).get("dependencies") or []
        for dep in deps:
            if not isinstance(dep, dict):
                continue
            dst_adr = dep.get("adr")
            if not dst_adr:
                continue
            raw_rel = dep.get("relationship", "DEPENDS_ON")
            rel_type = (
                re.sub(r"[^A-Za-z0-9_]+", "_", str(raw_rel)).strip("_").upper() or "DEPENDS_ON"
            )
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
    namespace_by_project: bool = False,
) -> tuple[list[GraphEntity], list[GraphRelation]]:
    """Parse entire SDD corpus across all 10 layers and extract entities & lineage relations."""
    root_dir = _resolve_sdd_root(sdd_root)
    resolved_root = root_dir.resolve()
    entities_map: dict[str, GraphEntity] = {}
    relations_map: dict[tuple[str, str, str], str] = {}  # (src, dst, rel_type) -> rationale

    if not root_dir.exists():
        logger.warning("SDD root directory %s does not exist", root_dir)
        return [], []

    use_prefix = bool(
        namespace_by_project
        and project_id
        and project_id.strip()
        and project_id.strip() not in ("default", "core")
    )

    def _qualify_id(raw_sdd_id: str) -> str:
        if use_prefix and not raw_sdd_id.startswith(f"{project_id}:"):
            return f"{project_id}:{raw_sdd_id}"
        return raw_sdd_id

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
            if not file_path.resolve().is_relative_to(resolved_root):
                continue
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
            if (
                not doc_id
                or doc_id in ("README", "HANDOFF", "IMPLEMENTATION")
                or doc_id.endswith("-00")
            ):
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
            qualified_doc_id = _qualify_id(doc_id)

            entity = GraphEntity(
                id=qualified_doc_id,
                entity_type=entity_type,
                name=str(title),
                tenant_id=tenant_id,
                domain_id=domain_id,
                properties={
                    "status": str(status),
                    "author": str(author),
                    "project_id": project_id,
                    "sdd_id": doc_id,
                    "layer_folder": layer_folder,
                    "source_file": file_path.name,
                },
            )
            entities_map[qualified_doc_id] = entity

            # 1. Structural schema fields in YAML
            # source_spec in IPLAN
            spec_val = dc.get("source_spec") or data.get("source_spec")
            for dst in extract_sdd_target_ids(spec_val):
                if dst != doc_id:
                    relations_map[(qualified_doc_id, _qualify_id(dst), "IMPLEMENTS")] = (
                        "source_spec"
                    )

            # source_document in BDD / EARS / SPEC
            src_doc = data.get("source_document") or dc.get("source_document")
            for dst in extract_sdd_target_ids(src_doc):
                if dst != doc_id:
                    rel_type = (
                        "SATISFIES"
                        if doc_id.startswith("SPEC")
                        else "SPECIFIES"
                        if doc_id.startswith("BDD")
                        else "ORIGINATES_FROM"
                    )
                    relations_map[(qualified_doc_id, _qualify_id(dst), rel_type)] = (
                        "source_document"
                    )

            # architecture_decision in SPEC
            arch_dec = data.get("architecture_decision") or (
                data.get("component_overview") or {}
            ).get("architecture_decision")
            for dst in extract_sdd_target_ids(arch_dec):
                if dst != doc_id:
                    relations_map[(qualified_doc_id, _qualify_id(dst), "CONSTRAINED_BY")] = (
                        "architecture_decision"
                    )

            # brd_reference in PRD
            brd_ref = data.get("brd_reference")
            for dst in extract_sdd_target_ids(brd_ref):
                if dst != doc_id:
                    relations_map[(qualified_doc_id, _qualify_id(dst), "FULFILLS")] = (
                        "brd_reference"
                    )

            # authoring_chg in IPLAN / document_control
            auth_chg = dc.get("authoring_chg") or data.get("chg_id")
            for dst in extract_sdd_target_ids(auth_chg):
                if dst != doc_id:
                    relations_map[(_qualify_id(dst), qualified_doc_id, "MODIFIES")] = (
                        "authoring_chg"
                    )

            # related_decisions.dependencies in ADR
            adr_deps = (data.get("related_decisions") or {}).get("dependencies") or []
            for dep in adr_deps:
                if not isinstance(dep, dict):
                    continue
                dst_adr = dep.get("adr")
                for dst in extract_sdd_target_ids(dst_adr):
                    if dst != doc_id:
                        raw_rel = dep.get("relationship", "DEPENDS_ON")
                        rel_type = (
                            re.sub(r"[^A-Za-z0-9_]+", "_", str(raw_rel)).strip("_").upper()
                            or "DEPENDS_ON"
                        )
                        relations_map[(qualified_doc_id, _qualify_id(dst), rel_type)] = dep.get(
                            "rationale", ""
                        )

            # 2. Free-text lineage tag matching (@kind: TARGET)
            for m in TAG_RE.finditer(content):
                kind = m.group("kind").lower()
                for target_id in extract_sdd_target_ids(m.group("target")):
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

                    key = (qualified_doc_id, _qualify_id(target_id), rel)
                    if key not in relations_map:
                        relations_map[key] = f"lineage_tag_@{kind}"

    # 3. Ensure referential integrity by auto-creating stubs for unresolved relation targets
    for _src, dst, _rel in sorted(relations_map.keys()):
        if dst not in entities_map:
            unprefixed_dst = dst.split(":", 1)[-1]
            prefix = unprefixed_dst.split("-")[0].upper() if "-" in unprefixed_dst else "SDD"
            entity_type = (
                prefix
                if prefix
                in (
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
                )
                else "SDD"
            )
            entities_map[dst] = GraphEntity(
                id=dst,
                entity_type=entity_type,
                name=f"Stub: {unprefixed_dst}",
                tenant_id=tenant_id,
                domain_id=domain_id,
                properties={
                    "status": "stub",
                    "stub": True,
                    "project_id": project_id,
                    "sdd_id": unprefixed_dst,
                },
            )

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
    await repo.bulk_upsert_graph_entities(entities)
    await repo.bulk_upsert_graph_relations(relations)

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


def resolve_sdd_project_id(
    sdd_root: Path | str,
    explicit_id: str | None = None,
    config_id: str | None = None,
) -> str:
    """Resolve project_id deterministically from flags, .aidoc/profile.yaml, Git, or config."""
    if explicit_id and explicit_id.strip():
        return explicit_id.strip()

    root_path = _resolve_sdd_root(sdd_root).resolve()
    search_dirs: list[Path] = [root_path]
    curr = root_path
    for _ in range(3):
        if curr.parent != curr:
            curr = curr.parent
            search_dirs.append(curr)
    for d in search_dirs:
        # 1. Check .aidoc/profile.yaml
        profile_path = d / ".aidoc" / "profile.yaml"
        if not profile_path.is_file():
            profile_path = d / "profile.yaml"
        if profile_path.is_file():
            try:
                data = yaml.safe_load(profile_path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    meta = data.get("metadata", {})
                    if isinstance(meta, dict) and meta.get("project_name"):
                        return str(meta["project_name"]).strip()
                    if data.get("project_name"):
                        return str(data["project_name"]).strip()
                    if data.get("name"):
                        return str(data["name"]).strip()
            except Exception as exc:
                logger.debug("Failed to parse profile.yaml at %s: %s", profile_path, exc)

        # 2. Check pyproject.toml
        pyproject_path = d / "pyproject.toml"
        if pyproject_path.is_file():
            try:
                data_toml = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
                proj_name = data_toml.get("project", {}).get("name")
                if proj_name:
                    return str(proj_name).strip()
            except Exception as exc:
                logger.debug("Failed to parse pyproject.toml at %s: %s", pyproject_path, exc)

        # 3. Check git remote origin
        if (d / ".git").exists() or (d.parent / ".git").exists():
            try:
                out = subprocess.run(  # nosec B603 B607 # reason: fixed git config read call
                    ["git", "config", "--get", "remote.origin.url"],
                    cwd=str(d),
                    capture_output=True,
                    text=True,
                    check=False,
                )
                if out.returncode == 0 and out.stdout.strip():
                    url = out.stdout.strip().rstrip("/")
                    if url.endswith(".git"):
                        url = url[:-4]
                    name = url.split("/")[-1].split(":")[-1]
                    if name:
                        return name.strip()
            except Exception as exc:
                logger.debug("Failed to read git origin in %s: %s", d, exc)

    if config_id and config_id not in ("p1", "default", "core"):
        return config_id

    # Fallback to directory name
    if root_path.name == "sdd" and root_path.parent.name == "docs":
        return root_path.parent.parent.name
    return root_path.parent.name if root_path.name in ("sdd", "docs") else root_path.name


async def ingest_sdd_knowledge_graph(
    repo: Repository,
    graph: GraphPort | None = None,
    *,
    sdd_root: Path | str = "sdd",
    tenant_id: str = "default",
    project_id: str = "core",
    domain_id: str = "core",
    reconcile: bool = True,
    namespace_by_project: bool = False,
) -> tuple[int, int]:
    """Ingest complete multi-layer SDD corpus into PostgreSQL and project to Neo4j."""
    sdd_path = _resolve_sdd_root(sdd_root)
    entities, relations = extract_sdd_graph(
        sdd_path,
        tenant_id=tenant_id,
        project_id=project_id,
        domain_id=domain_id,
        namespace_by_project=namespace_by_project,
    )
    if sdd_path.name == "sdd" and sdd_path.parent.name == "docs":
        repo_candidate = sdd_path.parent.parent
    elif sdd_path.name == "sdd":
        repo_candidate = sdd_path.parent
    else:
        repo_candidate = sdd_path
    if (repo_candidate / "docs").is_dir():
        doc_entities, doc_relations = extract_docs_graph(
            repo_candidate,
            tenant_id=tenant_id,
            project_id=project_id,
            domain_id=domain_id,
        )
        seen_ent = {e.id for e in entities}
        for de in doc_entities:
            if de.id not in seen_ent:
                entities.append(de)
                seen_ent.add(de.id)
        relations.extend(doc_relations)

    # 1. Canonical PostgreSQL persistence (ADR-01)
    await repo.bulk_upsert_graph_entities(entities)
    await repo.bulk_upsert_graph_relations(relations)

    # 2. Reconcile deleted / renamed files by purging stale database entities
    if reconcile and sdd_path.exists():
        active_ids = [e.id for e in entities]
        purged = await repo.purge_stale_graph_entities(
            tenant_id=tenant_id,
            project_id=project_id,
            active_ids=active_ids,
        )
        if purged:
            logger.info(
                "Reconciled %d stale entities for tenant %s (project %s)",
                len(purged),
                tenant_id,
                project_id,
            )

    # 3. Rebuildable projection to Neo4j (ADR-05)
    if graph is not None:
        if hasattr(graph, "init_schema"):
            await graph.init_schema()

        if reconcile and sdd_path.exists() and hasattr(graph, "purge_stale_nodes"):
            await graph.purge_stale_nodes(
                tenant_id=tenant_id,
                active_ids=[e.id for e in entities],
                project_id=project_id,
            )

        for entity in entities:
            props = {
                "name": entity.name,
                "tenant_id": entity.tenant_id,
                "domain_id": entity.domain_id,
                **entity.properties,
            }
            labels = entity_labels_for(entity)
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
    tenant_id: str = "default",
    project_id: str | None = None,
) -> list[dict[str, Any]]:
    """Retrieve connected topological neighborhood for given seed nodes."""
    all_neighbors: dict[str, dict[str, Any]] = {}

    for seed_id in seed_ids:
        clean_seed = normalize_sdd_id(seed_id)
        try:
            hits = await graph.neighbors(
                id=clean_seed,
                depth=depth,
                tenant_id=tenant_id,
                project_id=project_id,
            )
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
    tenant_id: str = "default",
    project_id: str | None = None,
) -> list[dict[str, Any]]:
    """Retrieve upstream specification contract closure (ancestors) for an IPLAN task."""
    clean_task = normalize_sdd_id(task_id)
    ancestors = await graph.ancestors(
        id=clean_task,
        depth=depth,
        tenant_id=tenant_id,
        project_id=project_id,
    )
    return [dict(a) for a in ancestors]


async def get_sdd_blast_radius(
    graph: GraphPort,
    doc_id: str,
    *,
    depth: int = 6,
    tenant_id: str = "default",
    project_id: str | None = None,
) -> list[dict[str, Any]]:
    """Retrieve downstream dependent specifications, tasks, and tests affected by a change."""
    clean_doc = normalize_sdd_id(doc_id)
    descendants = await graph.descendants(
        id=clean_doc,
        depth=depth,
        tenant_id=tenant_id,
        project_id=project_id,
    )
    return [dict(d) for d in descendants]


def extract_docs_graph(
    repo_path: Path | str,
    *,
    tenant_id: str = "default",
    project_id: str | None = None,
    domain_id: str = "default",
) -> tuple[list[GraphEntity], list[GraphRelation]]:
    """Extract RUNBOOK, RUNBOOK_STEP, and ALERT entities and relations from docs/**/*.md."""
    from engramory.core.docs_tools import _is_sdd_doc_path, parse_markdown_document

    root = Path(repo_path).resolve()
    bound_root = root if root.is_dir() else root.parent
    eff_project = project_id or root.name
    docs_dir = root / "docs"
    raw_files = (
        [root]
        if root.is_file()
        else sorted(docs_dir.rglob("*.md"))
        if docs_dir.is_dir()
        else sorted(root.glob("*.md"))
    )
    md_files = [
        f
        for f in raw_files
        if f.resolve().is_relative_to(bound_root.resolve())
        and (root.is_file() or not _is_sdd_doc_path(f, bound_root))
    ]

    entities: dict[str, GraphEntity] = {}
    relations: list[GraphRelation] = []

    for f in md_files:
        parsed = parse_markdown_document(
            f,
            repo_root=root if root.is_dir() else root.parent,
            tenant_id=tenant_id,
            project_id=eff_project,
            domain_id=domain_id,
        )
        if not parsed.has_frontmatter or parsed.kb_document is None:
            continue
        doc = parsed.kb_document
        if doc.status in {"deprecated", "archived"}:
            continue

        ent_type = (
            "RUNBOOK"
            if doc.kind == "runbook" or parsed.steps
            else re.sub(r"[^A-Za-z0-9_]", "_", doc.kind.upper())
        )
        entities[doc.doc_id] = GraphEntity(
            id=doc.doc_id,
            entity_type=ent_type,
            name=doc.title,
            tenant_id=tenant_id,
            domain_id=doc.domain_id or domain_id,
            properties={
                "project_id": eff_project,
                "repo_path": doc.repo_path,
                "kind": doc.kind,
                "autonomy_tier": doc.autonomy_tier,
                "namespace": doc.namespace,
                "status": doc.status,
            },
        )

        # Extract ALERT entities and MITIGATES_ALERT relations
        for alert_id in doc.trigger_alerts:
            if alert_id not in entities:
                entities[alert_id] = GraphEntity(
                    id=alert_id,
                    entity_type="ALERT",
                    name=alert_id,
                    tenant_id=tenant_id,
                    domain_id=doc.domain_id or domain_id,
                    properties={"project_id": eff_project},
                )
            relations.append(
                GraphRelation(
                    src_id=doc.doc_id,
                    dst_id=alert_id,
                    rel_type="MITIGATES_ALERT",
                    tenant_id=tenant_id,
                    properties={"project_id": eff_project},
                )
            )

        # Extract RUNBOOK_STEP entities and CONTAINS_STEP relations
        for st in parsed.steps:
            entities[st.step_uid] = GraphEntity(
                id=st.step_uid,
                entity_type="RUNBOOK_STEP",
                name=f"{st.step_id} ({st.tier}/{st.mode})",
                tenant_id=tenant_id,
                domain_id=doc.domain_id or domain_id,
                properties={
                    "project_id": eff_project,
                    "doc_id": doc.doc_id,
                    "step_id": st.step_id,
                    "step_order": st.step_order,
                    "tier": st.tier,
                    "mode": st.mode,
                    "heading_path": st.heading_path,
                },
            )
            relations.append(
                GraphRelation(
                    src_id=doc.doc_id,
                    dst_id=st.step_uid,
                    rel_type="CONTAINS_STEP",
                    tenant_id=tenant_id,
                    properties={"project_id": eff_project, "step_order": st.step_order},
                )
            )

        # Extract DEPENDS_ON relations from frontmatter consumers / dependencies
        for dep_key in ("dependencies", "consumers"):
            dep_list = parsed.frontmatter.get(dep_key)
            if isinstance(dep_list, list):
                for dep in dep_list:
                    dep_id = str(dep).strip()
                    if dep_id:
                        if dep_id not in entities:
                            entities[dep_id] = GraphEntity(
                                id=dep_id,
                                entity_type="SERVICE",
                                name=dep_id,
                                tenant_id=tenant_id,
                                domain_id=doc.domain_id or domain_id,
                                properties={"project_id": eff_project},
                            )
                        rel_t = "DEPENDS_ON" if dep_key == "dependencies" else "CONSUMED_BY"
                        relations.append(
                            GraphRelation(
                                src_id=doc.doc_id,
                                dst_id=dep_id,
                                rel_type=rel_t,
                                tenant_id=tenant_id,
                                properties={"project_id": eff_project},
                            )
                        )

    return list(entities.values()), relations


def extract_knowledge_and_media_graph(
    sections: Sequence[KBSection],
    *,
    blobs: Sequence[KBBlob] = (),
    doc_title: str | None = None,
    repo_path: str | None = None,
    tenant_id: str = "default",
    project_id: str | None = None,
    domain_id: str = "default",
) -> tuple[list[GraphEntity], list[GraphRelation]]:
    """Extract DOCUMENT, SECTION, MEDIA_BLOB, and CONCEPT graph nodes and relations (PLAN-023)."""
    eff_project = project_id or "default"
    entities: dict[str, GraphEntity] = {}
    relations: list[GraphRelation] = []
    seen_rels: set[tuple[str, str, str]] = set()

    def _add_rel(
        src_id: str,
        dst_id: str,
        rel_type: str,
        props: dict[str, Any] | None = None,
    ) -> None:
        key = (src_id, dst_id, rel_type)
        if key in seen_rels:
            return
        seen_rels.add(key)
        relations.append(
            GraphRelation(
                src_id=src_id,
                dst_id=dst_id,
                rel_type=rel_type,
                tenant_id=tenant_id,
                properties={"project_id": eff_project, **(props or {})},
            )
        )

    blob_by_sha: dict[str, KBBlob] = {b.sha256: b for b in blobs}
    for b in blobs:
        blob_node_id = f"blob:{b.sha256[:16]}"
        entities[blob_node_id] = GraphEntity(
            id=blob_node_id,
            entity_type="MEDIA_BLOB",
            name=blob_node_id,
            tenant_id=tenant_id,
            domain_id=domain_id,
            properties={
                "project_id": eff_project,
                "sha256": b.sha256,
                "media_type": b.media_type,
                "byte_size": b.byte_size,
                "storage_tier": b.storage_tier,
                "blob_uri": f"pgblob://{tenant_id}/{b.sha256}",
            },
        )

    sections_by_doc: dict[str, list[KBSection]] = {}
    for sec in sections:
        sections_by_doc.setdefault(sec.doc_id, []).append(sec)

    for doc_id, doc_secs in sections_by_doc.items():
        first_sec = doc_secs[0]
        eff_domain = first_sec.domain_id or domain_id
        doc_modality = first_sec.modality
        entities[doc_id] = GraphEntity(
            id=doc_id,
            entity_type="DOCUMENT",
            name=doc_title or doc_id,
            tenant_id=tenant_id,
            domain_id=eff_domain,
            properties={
                "project_id": first_sec.project_id or eff_project,
                "repo_path": repo_path or doc_id,
                "modality": doc_modality,
            },
        )

        prev_sec_node_id: str | None = None
        for idx, sec in enumerate(doc_secs, 1):
            anchor = sec.citation.split("#", 1)[1] if "#" in sec.citation else f"sec-{idx}"
            sec_node_id = f"{doc_id}#{anchor}"
            sec_props: dict[str, Any] = {
                "project_id": sec.project_id or eff_project,
                "doc_id": doc_id,
                "citation": sec.citation,
                "modality": sec.modality,
            }
            if sec.blob_sha256:
                sec_props["blob_sha256"] = sec.blob_sha256
            if sec.blob_uri:
                sec_props["blob_uri"] = sec.blob_uri
            if sec.media_metadata.get("start_ms") is not None:
                sec_props["start_ms"] = sec.media_metadata.get("start_ms")
            if sec.media_metadata.get("end_ms") is not None:
                sec_props["end_ms"] = sec.media_metadata.get("end_ms")

            entities[sec_node_id] = GraphEntity(
                id=sec_node_id,
                entity_type="SECTION",
                name=sec.citation,
                tenant_id=tenant_id,
                domain_id=sec.domain_id or eff_domain,
                properties=sec_props,
            )
            _add_rel(doc_id, sec_node_id, "CONTAINS_SECTION", {"order": idx})

            if sec.blob_sha256:
                blob_node_id = f"blob:{sec.blob_sha256[:16]}"
                if blob_node_id not in entities:
                    matched_blob = blob_by_sha.get(sec.blob_sha256)
                    entities[blob_node_id] = GraphEntity(
                        id=blob_node_id,
                        entity_type="MEDIA_BLOB",
                        name=blob_node_id,
                        tenant_id=tenant_id,
                        domain_id=sec.domain_id or eff_domain,
                        properties={
                            "project_id": sec.project_id or eff_project,
                            "sha256": sec.blob_sha256,
                            "media_type": (
                                matched_blob.media_type
                                if matched_blob
                                else str(
                                    sec.media_metadata.get("mime_type")
                                    or "application/octet-stream"
                                )
                            ),
                            "byte_size": (
                                matched_blob.byte_size
                                if matched_blob
                                else int(sec.media_metadata.get("byte_size") or 0)
                            ),
                            "storage_tier": (
                                matched_blob.storage_tier if matched_blob else "logged"
                            ),
                            "blob_uri": sec.blob_uri or f"pgblob://{tenant_id}/{sec.blob_sha256}",
                        },
                    )
                edge_props: dict[str, Any] = {}
                if sec.media_metadata.get("start_ms") is not None:
                    edge_props["start_ms"] = sec.media_metadata.get("start_ms")
                if sec.media_metadata.get("end_ms") is not None:
                    edge_props["end_ms"] = sec.media_metadata.get("end_ms")
                _add_rel(sec_node_id, blob_node_id, "DERIVED_FROM_BLOB", edge_props)

            if prev_sec_node_id is not None and sec.modality in {"audio", "video"}:
                _add_rel(prev_sec_node_id, sec_node_id, "NEXT_SEGMENT")
            prev_sec_node_id = sec_node_id

            # Extract SDD specification mentions (@spec:, SPEC-01, ADR-03, etc.)
            for match in TAG_RE.finditer(sec.text):
                target = normalize_sdd_id(match.group("target"))
                if target:
                    _add_rel(sec_node_id, target, "MENTIONS_ENTITY")
            for match in _SDD_REF_RE.finditer(sec.text):
                target = normalize_sdd_id(match.group("id"))
                if target:
                    _add_rel(sec_node_id, target, "MENTIONS_ENTITY")

            # Extract [[Concept]] wiki-style links
            for match in _WIKI_CONCEPT_RE.finditer(sec.text):
                raw_concept = match.group("concept").strip()
                if not raw_concept:
                    continue
                slug = re.sub(r"[^a-z0-9_]+", "-", raw_concept.lower()).strip("-")
                if not slug:
                    continue
                concept_id = f"concept:{slug}"
                if concept_id not in entities:
                    entities[concept_id] = GraphEntity(
                        id=concept_id,
                        entity_type="CONCEPT",
                        name=raw_concept,
                        tenant_id=tenant_id,
                        domain_id=sec.domain_id or eff_domain,
                        properties={"project_id": sec.project_id or eff_project},
                    )
                _add_rel(sec_node_id, concept_id, "MENTIONS_ENTITY")

    return list(entities.values()), relations
