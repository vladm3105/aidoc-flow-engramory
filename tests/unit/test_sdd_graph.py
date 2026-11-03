"""Unit tests for SDD knowledge graph extraction and normalization (ADR-01/03/05)."""

from __future__ import annotations

from pathlib import Path

from engramory.core.kb_graph import (
    extract_sdd_graph,
    normalize_sdd_id,
    resolve_sdd_project_id,
)


def test_normalize_sdd_id() -> None:
    assert normalize_sdd_id("ADR-01") == "ADR-01"
    assert normalize_sdd_id("BDD.01.03.a100") == "BDD-01"
    assert normalize_sdd_id("SPEC.06.01.c3b8") == "SPEC-06"
    assert normalize_sdd_id("@spec: SPEC-03") == "SPEC-03"
    assert normalize_sdd_id("@adr: ADR-02") == "ADR-02"
    assert normalize_sdd_id("IPLAN-319_offerta_forward_home") == "IPLAN-319"
    assert normalize_sdd_id("CHG-316_offerta_forward_home") == "CHG-316"
    assert normalize_sdd_id("") == ""


def test_extract_sdd_graph_from_local_sdd_corpus() -> None:
    entities, relations = extract_sdd_graph(
        "sdd",
        tenant_id="tenant-local",
        project_id="engramory",
        domain_id="core",
    )

    assert len(entities) >= 30
    assert len(relations) >= 50

    entity_ids = {e.id for e in entities}
    assert "BRD-01" in entity_ids
    assert "PRD-01" in entity_ids
    assert "EARS-01" in entity_ids
    assert "BDD-01" in entity_ids
    assert "ADR-01" in entity_ids
    assert "SPEC-01" in entity_ids
    assert "TDD-01" in entity_ids
    assert "IPLAN-01" in entity_ids

    rel_types = {r.rel_type for r in relations}
    assert "IMPLEMENTS" in rel_types
    assert "CONSTRAINED_BY" in rel_types
    assert "SATISFIES" in rel_types
    assert "DEPENDS_ON" in rel_types


def test_extract_sdd_graph_synthetic_layers(tmp_path: Path) -> None:
    # Set up mock 10-layer SDD structure
    brd_dir = tmp_path / "01_BRD"
    prd_dir = tmp_path / "02_PRD"
    spec_dir = tmp_path / "06_SPEC"
    iplan_dir = tmp_path / "08_IPLAN"

    brd_dir.mkdir()
    prd_dir.mkdir()
    spec_dir.mkdir()
    iplan_dir.mkdir()

    (brd_dir / "BRD-01_core.yaml").write_text(
        "id: BRD-01\ntitle: Core Platform\nstatus: Approved\n",
        encoding="utf-8",
    )
    (prd_dir / "PRD-01_core.yaml").write_text(
        "id: PRD-01\ntitle: Core Features\nbrd_reference: '@brd: BRD-01'\n",
        encoding="utf-8",
    )
    (spec_dir / "SPEC-01_auth.yaml").write_text(
        "id: SPEC-01\ntitle: Auth Interface\narchitecture_decision: '@adr: ADR-02'\n",
        encoding="utf-8",
    )
    yaml_content = (
        "document_control:\n"
        "  document_id: IPLAN-01\n"
        "  source_spec: '@spec: SPEC-01'\n"
        "status: Active\n"
    )
    (iplan_dir / "IPLAN-01_impl.yaml").write_text(yaml_content, encoding="utf-8")

    entities, relations = extract_sdd_graph(
        tmp_path,
        tenant_id="synthetic-tenant",
        project_id="proj-x",
    )

    ent_map = {e.id: e for e in entities}
    assert "BRD-01" in ent_map
    assert ent_map["BRD-01"].entity_type == "BRD"
    assert "PRD-01" in ent_map
    assert ent_map["PRD-01"].entity_type == "PRD"
    assert "SPEC-01" in ent_map
    assert "IPLAN-01" in ent_map

    rel_tuples = {(r.src_id, r.dst_id, r.rel_type) for r in relations}
    assert ("PRD-01", "BRD-01", "FULFILLS") in rel_tuples
    assert ("SPEC-01", "ADR-02", "CONSTRAINED_BY") in rel_tuples
    assert ("IPLAN-01", "SPEC-01", "IMPLEMENTS") in rel_tuples


def test_resolve_sdd_project_id(tmp_path: Path) -> None:
    # 1. Explicit ID wins
    assert resolve_sdd_project_id(tmp_path, explicit_id="explicit-p") == "explicit-p"

    # 2. Configured ID wins if no explicit ID
    assert resolve_sdd_project_id(tmp_path, config_id="cfg-p") == "cfg-p"

    # 3. .aidoc/profile.yaml metadata.project_name
    aidoc_dir = tmp_path / ".aidoc"
    aidoc_dir.mkdir()
    (aidoc_dir / "profile.yaml").write_text("metadata:\n  project_name: aidoc-profile-proj\n")
    assert resolve_sdd_project_id(tmp_path) == "aidoc-profile-proj"

    # 4. pyproject.toml [project] name
    proj_dir = tmp_path / "subproject"
    proj_dir.mkdir()
    (proj_dir / "pyproject.toml").write_text('[project]\nname = "pyproject-proj"\n')
    assert resolve_sdd_project_id(proj_dir) == "pyproject-proj"

    # 5. Directory fallback
    fallback_dir = tmp_path.parent / "isolated_fallback_dir"
    fallback_dir.mkdir(exist_ok=True)
    assert resolve_sdd_project_id(fallback_dir) == "isolated_fallback_dir"
